"""
Day-1 runs: saving a public run, the queue that builds each report, attaching runs when the number logs in, sending the
report on WhatsApp, and the cohort numbers and export the Performance Hub imports.

A run is stored against the WhatsApp number typed on the first screen, with two random keys: `token` (the share page
/r/<token>, first name only) and `media_key` (the PNG/PDF addresses AiSensy fetches). No account is made until the number
proves itself with the hub's own login code.

The queue lives in MongoDB, so a report finishes even if the attendee closes the tab, and several server processes can
share the work (each run is claimed by exactly one). The public run makes no AI call at all: the website check, the
score, the findings and the arithmetic are all code.
"""
import asyncio
import csv
import io
import json
import logging
import math
import secrets
import time
from datetime import timedelta

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from . import day1, day1_render, fetch
from .config import settings
from .db import IST, db, now
from .messaging import whatsapp_template
from .services import new_id, track

log = logging.getLogger("day1")
MAX_ATTEMPTS = 3
STALE_SECONDS = 180          # a report "building" this long was dropped by a restart; it goes back in the queue
DEFAULT_SECONDS = 6          # until real timings exist: a website check plus drawing the PNG and PDF
EXPORT_COLUMNS = ["phone", "email", "name", "signed_up_at", "tool_kind", "completed", "number_label", "number_value", "extra_json",
                  "report_sent_at", "report_opened_at"]


class Day1Error(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


async def ensure_indexes() -> None:
    d = db()
    await d.day1_runs.create_index([("phone", 1), ("cohort", 1)], unique=True)   # one first run per number per webinar
    await d.day1_runs.create_index("token", unique=True)
    await d.day1_runs.create_index("media_key", unique=True)
    await d.day1_runs.create_index([("status", 1), ("queued_at", 1)])
    await d.day1_runs.create_index([("status", 1), ("finished_at", -1)])
    await d.day1_runs.create_index([("cohort", 1), ("created_at", 1)])
    await d.day1_runs.create_index([("phone", 1), ("user_id", 1)])


def iso(at) -> str:
    return at.astimezone(IST).isoformat(timespec="seconds") if at else ""


def share_url(run: dict) -> str:
    return f"{settings.app_url}/r/{run['token']}"


def media_url(run: dict, ext: str) -> str:
    return f"{settings.app_url}/r/m/{run['media_key']}.{ext}"


# ───────────────────────── a new public run ─────────────────────────
async def _take_daily_slot() -> bool:
    day = now().astimezone(IST).strftime("%Y-%m-%d")
    try:
        await db().day1_counters.update_one({"_id": day}, {"$setOnInsert": {"n": 0}}, upsert=True)
    except DuplicateKeyError:
        pass  # created by a parallel request a moment ago
    took = await db().day1_counters.find_one_and_update({"_id": day, "n": {"$lt": settings.day1_daily_cap}}, {"$inc": {"n": 1}})
    return bool(took)


async def _give_back_slot() -> None:
    await db().day1_counters.update_one({"_id": now().astimezone(IST).strftime("%Y-%m-%d")}, {"$inc": {"n": -1}})


async def create(*, phone: str, first_name: str, city: str, website_url: str | None, answers: dict, numbers: dict, cohort: str) -> dict:
    taken = Day1Error(409, "This number already has a report from this webinar. It comes to you on WhatsApp.")
    if await db().day1_runs.find_one({"phone": phone, "cohort": cohort}, {"_id": 1}):
        raise taken
    if not await _take_daily_slot():
        await track("day1_daily_cap", None, cohort=cohort)
        raise Day1Error(429, "We've made as many reports as we can today. Please try again tomorrow.")
    at = now()
    run = {"_id": new_id(), "token": secrets.token_urlsafe(12), "media_key": secrets.token_urlsafe(18), "phone": phone,
           "first_name": first_name, "city": city, "website_url": website_url, "cohort": cohort, "answers": answers, "numbers": numbers,
           "consent": {"text": day1.CONSENT_TEXT, "at": at}, "status": "queued", "attempts": 0, "queued_at": at, "created_at": at,
           "user_id": None, "ai_cost_inr": 0.0, "send_count": 0, "sent_at": None, "opened_at": None}
    try:
        await db().day1_runs.insert_one(run)
    except DuplicateKeyError:          # the same number pressed submit twice at once
        await _give_back_slot()
        raise taken
    await track("day1_run", None, cohort=cohort)
    return run


_average = {"at": 0.0, "value": float(DEFAULT_SECONDS)}


async def average_seconds() -> float:
    """The last 50 build times, worked out at most every 10 seconds (every waiting page asks every 2 seconds)."""
    if time.monotonic() - _average["at"] < 10:
        return _average["value"]
    recent = [r.get("duration_ms") or 0 async for r in db().day1_runs.find({"status": "done", "duration_ms": {"$gt": 0}}, {"duration_ms": 1})
              .sort("finished_at", -1).limit(50)]
    _average.update(at=time.monotonic(), value=(sum(recent) / len(recent) / 1000) if recent else DEFAULT_SECONDS)
    return _average["value"]


async def eta(run: dict) -> int:
    """'Building your report… about N seconds': the runs ahead in the queue, shared across the workers."""
    if run.get("status") in ("done", "failed"):
        return 0
    ahead = await db().day1_runs.count_documents({"status": "queued", "queued_at": {"$lt": run["queued_at"]}})
    seconds = math.ceil((ahead + 1) / max(1, settings.day1_workers)) * await average_seconds()
    return int(min(max(round(seconds), 3), 900))


# ───────────────────────── the queue ─────────────────────────
async def claim() -> dict | None:
    return await db().day1_runs.find_one_and_update(
        {"status": "queued", "queued_at": {"$lte": now()}}, {"$set": {"status": "building", "started_at": now()}, "$inc": {"attempts": 1}},
        sort=[("queued_at", 1)], return_document=ReturnDocument.AFTER)


async def requeue_stale() -> None:
    cutoff = now() - timedelta(seconds=STALE_SECONDS)
    await db().day1_runs.update_many({"status": "building", "started_at": {"$lt": cutoff}, "attempts": {"$lt": MAX_ATTEMPTS}},
                                     {"$set": {"status": "queued", "queued_at": now(), "error": "Stopped part-way; tried again."}})
    await db().day1_runs.update_many({"status": "building", "started_at": {"$lt": cutoff}, "attempts": {"$gte": MAX_ATTEMPTS}},
                                     {"$set": {"status": "failed", "error": "Stopped part-way three times.", "finished_at": now()}})


async def _read_website(url: str) -> dict:
    """The Score's 8 website checks. A site that can't be opened is recorded as such — never guessed."""
    site = {"host": day1.site_host(url), "read": False, "checks": {}}
    try:
        facts = await asyncio.wait_for(fetch.read_site(url), settings.day1_site_seconds)
        site.update(read=True, checks=facts.checks, host=day1.site_host(facts.url) or site["host"])
    except fetch.FetchError as e:
        site["error"] = str(e)[:200]
    except asyncio.TimeoutError:
        site["error"] = "The website took too long to open."
    except Exception:  # noqa: BLE001  a strange website must not fail the report
        log.exception("day1 website read failed")
        site["error"] = "The website couldn't be read."
    return site


async def build(run: dict) -> bool:
    """Builds one claimed run: website check → score → report → PNG, PDF and preview image. Returns True when done."""
    started = time.monotonic()
    try:
        site = await _read_website(run["website_url"]) if run.get("website_url") else None
        run["site"] = site
        cfg = await day1.load_config()
        result = day1.score_for(run)
        report = day1.build(run, result, cfg)
        title = f"{report['eyebrow']} · {run['first_name']}"
        files = await asyncio.to_thread(day1_render.all_files, report, title)
        preview = await asyncio.to_thread(day1_render.preview, run["first_name"], result["score"], report["eyebrow"], report["tiles"][0]["label"])
        await db().day1_files.replace_one({"_id": run["_id"]}, {"_id": run["_id"], "png": files["png"], "pdf": files["pdf"], "preview": preview,
                                                                "at": now()}, upsert=True)
        took = round((time.monotonic() - started) * 1000)
        done = await db().day1_runs.find_one_and_update(
            {"_id": run["_id"], "status": "building"},
            {"$set": {"status": "done", "site": site, "result": result, "report": {k: v for k, v in report.items() if k != "gap"},
                      "gap": report["gap"], "finding_keys": report["finding_keys"], "finished_at": now(), "duration_ms": took, "error": None,
                      "png_bytes": len(files["png"])}}, return_document=ReturnDocument.AFTER)
        if done and done.get("saved_with_code"):   # saved with the code while it was still building
            user = await db().users.find_one({"_id": done["user_id"]})
            if user:
                await _first_score(user, done)
        return True
    except Exception as e:  # noqa: BLE001
        log.exception("day1 run %s failed", run["_id"])
        last = run.get("attempts", 1) >= MAX_ATTEMPTS
        await db().day1_runs.update_one({"_id": run["_id"], "status": "building"}, {"$set": {
            "status": "failed" if last else "queued", "error": f"{type(e).__name__}: {str(e)[:160]}",
            "queued_at": now() + timedelta(seconds=5 * run.get("attempts", 1)), **({"finished_at": now()} if last else {})}})
        return False


async def drain(workers: int | None = None) -> int:
    """Builds everything waiting in the queue, `workers` at a time. Returns how many it built."""
    built = 0

    async def worker():
        nonlocal built
        while True:
            run = await claim()
            if not run:
                return
            ok = await build(run)   # not "built += await …": that reads `built` before the await and loses counts
            built += ok

    await asyncio.gather(*(worker() for _ in range(max(1, workers or settings.day1_workers))))
    return built


async def loop() -> None:
    """Runs in every server process (main.py): checks the queue twice a second."""
    while True:
        try:
            await requeue_stale()
            if await drain():
                continue
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("day1 queue")
        await asyncio.sleep(0.5)


async def retry(run_id: str) -> bool:
    r = await db().day1_runs.update_one({"_id": run_id, "status": "failed"},
                                        {"$set": {"status": "queued", "attempts": 0, "queued_at": now(), "error": None}})
    return bool(r.modified_count)


# ───────────────────────── saved with the WhatsApp code ─────────────────────────
async def _first_score(user: dict, run: dict) -> None:
    """The webinar run becomes their first Business AI Score in the hub — only when they saved this report themselves
    (anyone can type a number, so a run is never turned into someone's Score just because they logged in), and only if
    they haven't taken the Score in the hub. Their website check for future Scores is left to the hub's own reader."""
    if user.get("team_of") or run.get("status") != "done" or await db().scores.find_one({"ws": user["_id"]}, {"_id": 1}):
        return
    site = run.get("site") or {}
    website = {"url": run["website_url"], "checks": site["checks"]} if site.get("read") else None
    at = run.get("finished_at") or now()
    doc = {"_id": secrets.token_hex(8), "ws": user["_id"], "at": at, "month": at.strftime("%Y-%m"), "answers": run["answers"],
           "website": website, **run["result"], "change": None, "token": secrets.token_urlsafe(9), "business": "", "city": run.get("city") or "",
           "numbers": run["numbers"], "source": "day1", "day1_run": run["_id"]}
    try:
        await db().scores.insert_one(doc)
    except DuplicateKeyError:
        return


async def attach_runs(user: dict) -> int:
    """Called on every successful login: reports made with this number can be opened from this account."""
    runs = [r["_id"] async for r in db().day1_runs.find({"phone": user["phone"], "user_id": None}, {"_id": 1})]
    if not runs:
        return 0
    await db().day1_runs.update_many({"_id": {"$in": runs}, "user_id": None}, {"$set": {"user_id": user["_id"], "attached_at": now()}})
    await track("day1_attached", user["_id"], runs=len(runs))
    return len(runs)


async def save_with_code(run: dict, user: dict) -> None:
    """The attendee pressed "Save with your WhatsApp code" on this report and proved the number: it's theirs."""
    await db().day1_runs.update_one({"_id": run["_id"], "user_id": user["_id"]}, {"$set": {"saved_with_code": True, "saved_at": now()}})
    fresh = await db().day1_runs.find_one({"_id": run["_id"]})
    if fresh and fresh.get("user_id") == user["_id"] and fresh.get("status") == "done":
        await _first_score(user, fresh)
    await track("day1_saved", user["_id"], run=run["_id"])


# ───────────────────────── WhatsApp ─────────────────────────
async def send(run: dict, only_if_unsent: bool = False) -> dict:
    """The report and its "See the full report" link, to the number typed — the only message a run ever sends.
    Each send first claims the run, so two clicks, two admins or two servers never send it twice at once."""
    if run.get("status") != "done":
        return {"sent": False, "via": "not-ready"}
    claim_q = {"_id": run["_id"], "status": "done", "$or": [{"sending_at": None}, {"sending_at": {"$lt": now() - timedelta(minutes=2)}}]}
    if only_if_unsent:
        claim_q["sent_at"] = None
    if not await db().day1_runs.find_one_and_update(claim_q, {"$set": {"sending_at": now()}}):
        return {"sent": False, "via": "already-sending" if not only_if_unsent else "already-sent"}
    try:
        return await _send(run)
    finally:
        await db().day1_runs.update_one({"_id": run["_id"]}, {"$set": {"sending_at": None}})


async def _send(run: dict) -> dict:
    pdf = settings.aisensy_report_media == "pdf"
    ext = "pdf" if pdf else "png"
    media = {"url": media_url(run, ext), "filename": f"{run['report']['eyebrow'].split(' · ')[0]} report - {run['first_name']}.{ext}"}
    params = [run["first_name"], run["report"]["whatsapp_line"]]
    buttons = None
    if settings.aisensy_report_link == "text":
        params.append(share_url(run))
    else:
        buttons = [{"type": "button", "sub_type": "url", "index": 0, "parameters": [{"type": "text", "text": run["token"]}]}]
    result = await whatsapp_template(run["phone"], settings.aisensy_report_campaign, run["first_name"], params, "day1_report",
                                     media=media, buttons=buttons)
    at = now()
    await db().day1_runs.update_one({"_id": run["_id"]}, {"$set": {"last_send": {"at": at, "ok": result["sent"], "via": result["via"]},
                                                                   **({"wa_message_id": result["id"]} if result.get("id") else {})},
                                                          **({"$inc": {"send_count": 1}} if result["sent"] else {})})
    if result["sent"]:
        await db().day1_runs.update_one({"_id": run["_id"], "sent_at": None}, {"$set": {"sent_at": at}})
    return result


async def send_many(ids: list[str], at_once: int = 5) -> dict:
    """"Send all pending": each report at most once, even if the button is pressed again while this runs."""
    sem = asyncio.Semaphore(at_once)
    counts = {"sent": 0, "failed": 0, "skipped": 0}

    async def one(run_id: str):
        async with sem:
            run = await db().day1_runs.find_one({"_id": run_id})
            if not run:
                return
            result = await send(run, only_if_unsent=True)
            counts["sent" if result["sent"] else "skipped" if result["via"] == "already-sent" else "failed"] += 1

    await asyncio.gather(*(one(i) for i in ids))
    return counts


_background: set = set()


def in_background(coro) -> None:
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


async def settle() -> None:
    """Waits for background sends (tests, and a clean shutdown)."""
    while _background:
        await asyncio.gather(*list(_background), return_exceptions=True)


def _phone_from(raw) -> str | None:
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if len(digits) == 10:
        digits = "91" + digits
    return "+" + digits if 11 <= len(digits) <= 15 else None


def statuses_in(payload) -> list[tuple[str, str, str | None]]:
    """(phone, status) pairs from a WhatsApp status webhook: Meta's shape (entry → changes → value → statuses) or a flat
    one such as AiSensy's ({"phone"/"destination"/…, "status"/"event"}), also under "data" or "payload"."""
    out = []
    if not isinstance(payload, dict):
        return out
    for entry in payload.get("entry") or []:
        for change in (entry or {}).get("changes") or []:
            for st in ((change or {}).get("value") or {}).get("statuses") or []:
                if isinstance(st, dict):
                    out.append((_phone_from(st.get("recipient_id")), str(st.get("status") or "").lower(), st.get("id")))
    for node in (payload, payload.get("data"), payload.get("payload")):
        if not isinstance(node, dict):
            continue
        phone = next((node.get(k) for k in ("phone", "phone_number", "destination", "mobile", "to", "waId", "wa_id", "recipient_id") if node.get(k)), None)
        status = next((node.get(k) for k in ("status", "event", "message_status", "type") if node.get(k)), None)
        mid = next((node.get(k) for k in ("messageId", "message_id", "wamid", "id") if isinstance(node.get(k), str)), None)
        if phone and status:
            out.append((_phone_from(phone), str(status).lower().split(".")[-1], mid))
    return [(p, s, m) for p, s, m in out if p and s in ("sent", "delivered", "read", "failed")]


async def record_status(phone: str, status: str, message_id: str | None = None) -> bool:
    """Puts delivered/read on the report this status is about: by WhatsApp message id when AiSensy gave us one when sending,
    otherwise the most recent report successfully sent to this number in the last 14 days (approximate: a login-code
    message to the same number in that time could be counted)."""
    run = await db().day1_runs.find_one({"wa_message_id": message_id}) if message_id else None
    if not run:
        run = await db().day1_runs.find_one({"phone": phone, "sent_at": {"$gte": now() - timedelta(days=14)}, "last_send.ok": True},
                                            sort=[("sent_at", -1)])
    if not run or run["phone"] != phone:
        return False
    at = now()
    if status == "failed":
        await db().day1_runs.update_one({"_id": run["_id"]}, {"$set": {"delivery_failed_at": at}})
        return True
    fields = {"sent": [], "delivered": ["delivered_at"], "read": ["delivered_at", "read_at"]}[status]
    for field in fields:   # the first time only
        await db().day1_runs.update_one({"_id": run["_id"], field: {"$exists": False}}, {"$set": {field: at}})
    return True


async def opened(run: dict) -> None:
    await db().day1_runs.update_one({"_id": run["_id"]}, {"$inc": {"open_count": 1}})
    await db().day1_runs.update_one({"_id": run["_id"], "opened_at": None}, {"$set": {"opened_at": now()}})


# ───────────────────────── cohorts and the export ─────────────────────────
async def cohort_stats(code: str) -> dict:
    q = {"cohort": code}
    c = db().day1_runs.count_documents
    return {"runs": await c(q), "completed": await c({**q, "status": "done"}), "building": await c({**q, "status": {"$in": ["queued", "building"]}}),
            "failed": await c({**q, "status": "failed"}), "sent": await c({**q, "sent_at": {"$ne": None}}),
            "pending": await c({**q, "status": "done", "sent_at": None}), "delivered": await c({**q, "delivered_at": {"$exists": True}}),
            "read": await c({**q, "read_at": {"$exists": True}}), "opened": await c({**q, "opened_at": {"$ne": None}}),
            "saved": await c({**q, "user_id": {"$ne": None}})}


def _safe_cell(value: str) -> str:
    """Spreadsheet apps run cells that start with = + - @ as formulas; free text never may."""
    return "'" + value if value[:1] in ("=", "+", "-", "@") else value


async def export_rows(code: str) -> list[dict]:
    runs = [r async for r in db().day1_runs.find({"cohort": code}).sort("created_at", 1)]
    ids = [r["user_id"] for r in runs if r.get("user_id")]
    users = {u["_id"]: u async for u in db().users.find({"_id": {"$in": ids}}, {"created_at": 1})}
    emails = {b["owner_id"]: b.get("email", "") async for b in db().businesses.find({"owner_id": {"$in": ids}}, {"owner_id": 1, "email": 1})}
    rows = []
    for r in runs:
        u = users.get(r.get("user_id")) or {}
        done = r.get("status") == "done"
        n, site = r.get("numbers") or {}, r.get("site") or {}
        extra = {"findings": r.get("finding_keys") or [],
                 "inputs": {"enquiries": n.get("enquiries"), "close_rate": int(n["close_rate"]) if n.get("close_rate") else None, "aov": n.get("aov"),
                            "revenue_band": n.get("revenue_band"), "team_size": n.get("team_size"), "reviews_12m": n.get("reviews_12m"),
                            "city": r.get("city") or None, "website": site.get("host") or None, "website_read": bool(site.get("read"))},
                 "answers": r.get("answers") or {},
                 "gap_year": (r.get("gap") or {}).get("gap_year"), "cold_rate_pct": (r.get("gap") or {}).get("cold_rate_pct"),
                 "score_parts": (r.get("result") or {}).get("parts"), "status": r.get("status"), "saved": bool(r.get("user_id")),
                 "delivered_at": iso(r.get("delivered_at")) or None, "read_at": iso(r.get("read_at")) or None, "run_at": iso(r.get("created_at"))}
        rows.append({"phone": r["phone"], "email": _safe_cell(emails.get(r.get("user_id"), "") or ""), "name": _safe_cell(r.get("first_name", "")),
                     "signed_up_at": iso(u.get("created_at")), "tool_kind": day1.TOOL_KIND, "completed": "true" if done else "false",
                     "number_label": day1.NUMBER_LABEL, "number_value": str(r["result"]["score"]) if done else "",
                     "extra_json": json.dumps(extra, ensure_ascii=False, separators=(",", ":")),
                     "report_sent_at": iso(r.get("sent_at")), "report_opened_at": iso(r.get("opened_at"))})
    return rows


def to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=EXPORT_COLUMNS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()
