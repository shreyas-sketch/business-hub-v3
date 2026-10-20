"""
Day-1 report — the ₹99 webinar tool (see app/day1.py for the report and app/day1_runs.py for the queue).

Public, no login:
  GET  /api/day1/form?c=CODE              what the form asks (the Score's 10 questions + 6 numbers) and the consent line
  POST /api/day1/runs                     one run per number per webinar → {token, eta_seconds}
  GET  /api/day1/runs/<token>             building (with "about N seconds") or ready: first name, score, gap line, finding labels
  POST /api/day1/runs/<token>/code        the hub's own WhatsApp login code, to the number on the run
  POST /api/day1/runs/<token>/verify      checks it, creates the Free account (or logs in) and attaches the run
  GET  /r/<token>                         the share page in the WhatsApp message: first name only + "Save with WhatsApp code"
  GET  /r/<token>/preview.png             its link-preview image (first name and the score)
  GET  /r/m/<media_key>.png|.pdf          the report files (AiSensy fetches the image from here)
  POST /api/hooks/day1-status/<secret>    sent / delivered / read from AiSensy (only when DAY1_STATUS_WEBHOOK_SECRET is set)
Logged in:
  GET  /api/day1/mine, /api/day1/runs/<token>/full
Admin (Admin → Day-1 reports):
  library and constants, preview, per-cohort numbers, runs, send / send all pending / resend, retry, export CSV, logs
"""
import base64
import hmac
import json
import re
from datetime import timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .. import day1, day1_render, day1_runs, fetch
from .. import score as scoring
from ..config import settings
from ..db import IST, db, now
from ..security import client_ip, current_user, normalise_phone, rate_limit, require_admin
from ..services import track
from .auth import VerifyIn, login_with_code, send_login_code

router = APIRouter()
admin = APIRouter(prefix="/api/admin/day1", dependencies=[Depends(require_admin)])
BOTS = re.compile(r"bot|crawl|spider|preview|headless|whatsapp|facebookexternalhit|slack|telegram|discord|twitter|linkedin|skype", re.I)


async def _cohort(code: str) -> dict | None:
    code = (code or "").strip().upper()[:40]
    return await db().cohorts.find_one({"code": code}) if code else None


def _open(cohort: dict) -> bool:
    return (cohort.get("day1") or {}).get("open", True)


async def _run(token: str) -> dict:
    run = await db().day1_runs.find_one({"token": (token or "")[:40]})
    if not run:
        raise HTTPException(404, "We couldn't find this report.")
    return run


# ───────────────────────── public: the form ─────────────────────────
@router.get("/api/day1/form")
async def form(c: str = ""):
    cohort = await _cohort(c)
    cfg = await day1.load_config()
    return {"valid": bool(cohort), "open": bool(cohort) and _open(cohort), "cohort": cohort["code"] if cohort else None,
            "name": (cohort or {}).get("name") or "", "program": cfg["templates"]["program"], "consent": day1.CONSENT_TEXT,
            "questions": [{"key": k, "area": a, "q": q} for k, a, q, *_ in scoring.QUESTIONS],
            "answers": [{"value": v, "label": v.capitalize()} for v in scoring.ANSWER_POINTS],
            "numbers": [{**{k: v for k, v in n.items() if k != "choices"}, **({"choices": [{"value": a, "label": b} for a, b in n["choices"]]}
                                                                              if n["kind"] == "choice" else {})} for n in day1.NUMBERS],
            "website_checks": len(scoring.WEBSITE_CHECKS)}


class RunIn(BaseModel):
    cohort: str = Field(min_length=1, max_length=60)
    phone: str = Field(min_length=1, max_length=24)
    name: str = Field(min_length=1, max_length=80)
    city: str | None = Field(default=None, max_length=80)
    website: str | None = Field(default=None, max_length=500)
    answers: dict[str, str] = Field(default_factory=dict)
    numbers: dict[str, int | float | str | None] = Field(default_factory=dict)
    consent: bool = False


@router.post("/api/day1/runs")
async def create_run(body: RunIn, request: Request):
    if not body.consent:
        raise HTTPException(400, "Please tick the box to say you understand this is an estimate.")
    cohort = await _cohort(body.cohort)
    if not cohort:
        raise HTTPException(404, "This webinar link isn't valid. Please use the link shared in the webinar.")
    if not _open(cohort):
        raise HTTPException(403, "The report for this webinar is closed.")
    phone = normalise_phone(body.phone)
    if not phone.startswith("+91"):
        raise HTTPException(400, "Please enter a 10-digit Indian WhatsApp number.")
    first = day1.first_name(body.name)
    if not first:
        raise HTTPException(400, "Please type your first name in English letters.")
    website = None
    if (body.website or "").strip():
        try:
            website = fetch.normalise_url(body.website)
        except fetch.FetchError as e:
            raise HTTPException(400, str(e))
    keys = [k for k, *_ in scoring.QUESTIONS]
    answers = {k: body.answers.get(k) for k in keys}
    if any(v not in scoring.ANSWER_POINTS for v in answers.values()):
        raise HTTPException(400, "Answer every question: yes, partly or no.")
    try:
        numbers = day1.clean_numbers(body.numbers)
    except day1.InputError as e:
        raise HTTPException(400, str(e))
    await rate_limit(f"day1-ip:{client_ip(request)}", settings.day1_runs_per_ip, 600,
                     "Too many reports from this network just now. Please try again in a few minutes.")
    try:
        run = await day1_runs.create(phone=phone, first_name=first, city=day1.clean_name(body.city or "", 40), website_url=website,
                                     answers=answers, numbers=numbers, cohort=cohort["code"])
    except day1_runs.Day1Error as e:
        raise HTTPException(e.status, e.message)
    return {"token": run["token"], "status": run["status"], "eta_seconds": await day1_runs.eta(run)}


def _teaser(run: dict) -> dict:
    """What anyone holding the link may see: first name, the number, the gap line and the finding labels."""
    out = {"status": run["status"], "first_name": run["first_name"], "saved": bool(run.get("user_id"))}
    if run["status"] == "done":
        r = run["report"]
        out.update(eyebrow=r["eyebrow"], title=r["title"], score=run["result"]["score"], band=run["result"]["band"],
                   number_label=r["tiles"][0]["label"], gap_line=day1.strip_markup(r["missing"]["line"]),
                   findings=[f["label"] for f in r["findings"]])
    return out


@router.get("/api/day1/runs/{token}")
async def run_status(token: str):
    run = await _run(token)
    return {**_teaser(run), "eta_seconds": await day1_runs.eta(run),
            "error": "We couldn't build this report. Our team has been told." if run["status"] == "failed" else None}


@router.post("/api/day1/runs/{token}/code")
async def run_code(token: str, request: Request):
    run = await _run(token)
    out = await send_login_code(run["phone"], request)
    return {**out, "to": "your WhatsApp number"}


class SaveIn(BaseModel):
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


@router.post("/api/day1/runs/{token}/verify")
async def run_verify(token: str, body: SaveIn, request: Request, response: Response):
    run = await _run(token)
    user, created = await login_with_code(run["phone"], VerifyIn(phone=run["phone"], code=body.code, name=run["first_name"], cohort=run["cohort"],
                                                                 src="webinar"), request, response)
    await day1_runs.save_with_code(run, user)
    return {"ok": True, "new": created}


def _full(run: dict) -> dict:
    files = {"png_url": day1_runs.media_url(run, "png"), "pdf_url": day1_runs.media_url(run, "pdf")} if run["status"] == "done" else {}
    return {"token": run["token"], "status": run["status"], "cohort": run["cohort"], "first_name": run["first_name"],
            "created_at": run["created_at"].isoformat(), "report": run.get("report"), "score": (run.get("result") or {}).get("score"),
            "share_url": day1_runs.share_url(run), **files}


@router.get("/api/day1/runs/{token}/full")
async def run_full(token: str, user: dict = Depends(current_user)):
    run = await _run(token)
    if run.get("user_id") != user["_id"]:
        raise HTTPException(403, "Log in with the WhatsApp number this report was made for.")
    return _full(run)


@router.get("/api/day1/mine")
async def mine(user: dict = Depends(current_user)):
    return [_full(r) async for r in db().day1_runs.find({"user_id": user["_id"]}).sort("created_at", -1).limit(10)]


# ───────────────────────── public: the share page and the files ─────────────────────────
@router.get("/r/m/{name}")
async def report_file(name: str):
    m = re.fullmatch(r"([A-Za-z0-9_-]{10,40})\.(png|pdf)", name or "")
    run = await db().day1_runs.find_one({"media_key": m.group(1)}) if m else None
    files = await db().day1_files.find_one({"_id": run["_id"]}) if run else None
    if not files:
        raise HTTPException(404, "Not found")
    ext = m.group(2)
    name = f'{run["report"]["eyebrow"].split(" · ")[0]} report - {run["first_name"]}.{ext}'
    plain = re.sub(r"[^A-Za-z0-9 ._-]", "", name).strip() or f"report.{ext}"   # headers are Latin-1: an ASCII name, plus the real one encoded
    return Response(bytes(files[ext]), media_type="application/pdf" if ext == "pdf" else "image/png",
                    headers={"Cache-Control": "private, max-age=3600", "X-Robots-Tag": "noindex",
                             "Content-Disposition": f"inline; filename=\"{plain}\"; filename*=UTF-8''{quote(name)}"})


@router.get("/r/{token}/preview.png")
async def report_preview(token: str):
    run = await _run(token)
    files = await db().day1_files.find_one({"_id": run["_id"]}, {"preview": 1})
    if not files:
        raise HTTPException(404, "Not ready yet")
    return Response(bytes(files["preview"]), media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})


@router.get("/r/{token}", response_class=HTMLResponse)
async def share_page(token: str, request: Request):
    from .public import not_found, templates
    run = await db().day1_runs.find_one({"token": (token or "")[:40]})
    if not run:
        return not_found()
    if run["status"] == "done" and not BOTS.search(request.headers.get("user-agent", "")):   # a link preview fetched by WhatsApp is not an open
        await day1_runs.opened(run)
    t = _teaser(run)
    gap_parts = [(part, bool(day1_render.MONEY.fullmatch(part))) for part in re.split(f"({day1_render.MONEY.pattern})", t.get("gap_line", "")) if part]
    html = templates.get_template("day1_share.html").render(
        t=t, gap_parts=gap_parts, url=day1_runs.share_url(run), image=f"{settings.app_url}/r/{run['token']}/preview.png",
        save=f"{settings.app_url}/report/save/{run['token']}", program=settings.program_name, speakers=settings.speaker_line)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache", "X-Robots-Tag": "noindex"})


@router.post("/api/hooks/day1-status/{secret}")
async def status_hook(secret: str, request: Request):
    if not settings.day1_status_secret or not hmac.compare_digest(secret.encode(), settings.day1_status_secret.encode()):
        raise HTTPException(404, "Not found")
    try:
        payload = json.loads(await request.body() or b"{}")
    except ValueError:
        raise HTTPException(400, "Not JSON")
    matched = 0
    for phone, status, message_id in day1_runs.statuses_in(payload)[:50]:
        matched += await day1_runs.record_status(phone, status, message_id)
    return {"ok": True, "matched": matched}


# ───────────────────────── Admin → Day-1 reports ─────────────────────────
@admin.get("/config")
async def get_config():
    cfg, base = await day1.load_config(), day1.defaults()
    rules = {k: rule for k, rule, *_ in day1.FINDINGS}
    return {"templates": [{"key": k, "label": label, "value": cfg["templates"][k], "default": base["templates"][k]}
                          for k, (label, _) in day1.TEMPLATES.items()],
            "findings": sorted([{"key": k, "rule": rules[k], **v, "default": base["findings"][k]} for k, v in cfg["findings"].items()],
                               key=lambda f: (f["priority"], day1.FINDING_KEYS.index(f["key"]))),
            "imagine_defaults": cfg["imagine_defaults"], "constants": cfg["constants"], "constants_default": base["constants"],
            "placeholders": day1.PLACEHOLDERS, "updated_at": cfg["updated_at"].isoformat() if cfg.get("updated_at") else None}


class FindingIn(BaseModel):
    on: bool = True
    priority: int = Field(ge=1, le=999)
    label: str = Field(min_length=2, max_length=80)
    text: str = Field(min_length=10, max_length=400)
    imagine: list[str] = Field(min_length=2, max_length=2)


class ConfigIn(BaseModel):
    templates: dict[str, str] = Field(default_factory=dict)
    findings: dict[str, FindingIn] = Field(default_factory=dict)
    imagine_defaults: list[str] | None = Field(default=None, max_length=5)
    constants: dict[str, float] = Field(default_factory=dict)


def _checked(body: ConfigIn) -> dict:
    """The admin's edits, checked: known keys only, known placeholders only, sensible lengths and a cold rate of 1–90%."""
    out: dict = {"templates": {}, "findings": {}, "constants": {}}
    for k, v in body.templates.items():
        if k not in day1.TEMPLATES:
            raise HTTPException(400, f"Unknown text: {k}")
        v = v.strip()
        if not v or len(v) > 600:
            raise HTTPException(400, f"{day1.TEMPLATES[k][0]}: write 1 to 600 characters.")
        if bad := day1.unknown_placeholders(v):
            raise HTTPException(400, f"{day1.TEMPLATES[k][0]}: {{{bad[0]}}} isn't a placeholder the report knows.")
        if k in day1.PUBLIC_TEMPLATES and (bad := day1.private_placeholders(v)):
            raise HTTPException(400, f"{day1.TEMPLATES[k][0]} shows on the share link, so it can't use {{{bad[0]}}}.")
        out["templates"][k] = v
    for k, f in body.findings.items():
        if k not in day1.FINDING_KEYS:
            raise HTTPException(400, f"Unknown finding: {k}")
        lines = [x.strip() for x in f.imagine]
        if any(not x or len(x) > 200 for x in lines):
            raise HTTPException(400, f"{f.label}: each imagine line needs 1 to 200 characters.")
        for text in (f.label, f.text, *lines):
            if bad := day1.unknown_placeholders(text):
                raise HTTPException(400, f"{f.label}: {{{bad[0]}}} isn't a placeholder the report knows.")
        if bad := day1.private_placeholders(f.label):
            raise HTTPException(400, f"Finding labels show on the share link, so “{f.label}” can't use {{{bad[0]}}}.")
        out["findings"][k] = {"on": f.on, "priority": f.priority, "label": f.label.strip(), "text": f.text.strip(), "imagine": lines}
    if body.imagine_defaults is not None:
        lines = [x.strip() for x in body.imagine_defaults if x.strip()]
        if any(len(x) > 200 for x in lines) or any(day1.unknown_placeholders(x) for x in lines):
            raise HTTPException(400, "Spare imagine lines: up to 200 characters each, known placeholders only.")
        out["imagine_defaults"] = lines
    for k, v in body.constants.items():
        if k != "cold_rate_pct":
            raise HTTPException(400, f"Unknown constant: {k}")
        if not (1 <= v <= 90) or int(v) != v:
            raise HTTPException(400, "The cold rate is a whole percentage from 1 to 90.")
        out["constants"][k] = int(v)
    return out


def _overrides(edits: dict) -> dict:
    """Only what differs from the defaults is kept, so improved default wording still reaches everything not edited."""
    base = day1.defaults()
    spare = edits.get("imagine_defaults")
    return {"templates": {k: v for k, v in edits["templates"].items() if v != base["templates"][k]},
            "findings": {k: v for k, v in edits["findings"].items() if v != base["findings"][k]},
            "imagine_defaults": spare if spare is not None and spare != base["imagine_defaults"] else None,
            "constants": {k: v for k, v in edits["constants"].items() if v != base["constants"][k]}}


def _apply(cfg: dict, overrides: dict) -> dict:
    cfg["templates"].update(overrides["templates"])
    for k, v in overrides["findings"].items():
        cfg["findings"][k].update(v)
    if overrides["imagine_defaults"] is not None:
        cfg["imagine_defaults"] = overrides["imagine_defaults"]
    cfg["constants"].update(overrides["constants"])
    return cfg


@admin.put("/config")
async def save_config(body: ConfigIn, user: dict = Depends(require_admin)):
    """The whole library as the admin wants it (the admin screen always sends all of it); replaces what was saved."""
    edits = _checked(body)
    before = await db().day1_config.find_one({"_id": day1.CONFIG_ID}) or {}
    doc = {"_id": day1.CONFIG_ID, **_overrides(edits), "updated_at": now(), "updated_by": user["_id"]}
    await db().day1_config.replace_one({"_id": day1.CONFIG_ID}, doc, upsert=True)
    await db().day1_config_log.insert_one({"at": now(), "by": user["_id"], "before": {k: v for k, v in before.items() if k != "_id"}, "edits": edits})
    await track("day1_config", user["_id"])
    return await get_config()


@admin.post("/config/reset")
async def reset_config(user: dict = Depends(require_admin)):
    before = await db().day1_config.find_one({"_id": day1.CONFIG_ID}) or {}
    await db().day1_config.delete_one({"_id": day1.CONFIG_ID})
    await db().day1_config_log.insert_one({"at": now(), "by": user["_id"], "before": {k: v for k, v in before.items() if k != "_id"}, "edits": "reset"})
    return await get_config()


class PreviewIn(BaseModel):
    config: ConfigIn | None = None             # the unsaved library (as Save would store it)
    sample: dict | None = None                 # {first_name, city, site: {host, read, checks}, answers, numbers}


@admin.post("/preview")
async def preview(body: PreviewIn):
    """A report from a made-up attendee: with `config`, the admin's unsaved library (same shape as Save); else what's saved."""
    cfg = _apply(day1.defaults(), _overrides(_checked(body.config))) if body.config else await day1.load_config()
    run = day1.sample_run()
    if body.sample:
        s = body.sample
        try:
            run.update(first_name=day1.first_name(s.get("first_name") or "Riya") or "Riya", city=day1.clean_name(s.get("city") or "", 40),
                       numbers=day1.clean_numbers({**run["numbers"], **(s.get("numbers") or {})}))
        except day1.InputError as e:
            raise HTTPException(400, str(e))
        if isinstance(s.get("answers"), dict):
            run["answers"].update({k: v for k, v in s["answers"].items() if k in run["answers"] and v in scoring.ANSWER_POINTS})
        if "site" in s:
            site = s["site"]
            run["site"] = None if not site else {"host": day1.site_host(site.get("host") or "example.in") or "example.in", "read": bool(site.get("read", True)),
                                                 "checks": {k: bool((site.get("checks") or {}).get(k)) for k, *_ in scoring.WEBSITE_CHECKS}}
    report = day1.build(run, day1.score_for(run), cfg)
    png = day1_render.png(report)
    return {"report": report, "png": "data:image/png;base64," + base64.b64encode(png).decode(), "png_bytes": len(png)}


@admin.get("/cohorts")
async def cohorts():
    out = []
    async for c in db().cohorts.find().sort("created_at", -1).limit(100):
        out.append({"code": c["code"], "name": c.get("name", ""), "date": c.get("workshop_date"), "open": _open(c),
                    "link": f"{settings.app_url}/report?c={c['code']}", **await day1_runs.cohort_stats(c["code"])})
    return out


class CohortDay1In(BaseModel):
    open: bool


@admin.patch("/cohorts/{code}")
async def set_cohort(code: str, body: CohortDay1In):
    r = await db().cohorts.update_one({"code": code.strip().upper()[:40]}, {"$set": {"day1.open": body.open}})
    if not r.matched_count:
        raise HTTPException(404, "Cohort not found")
    return {"ok": True}


def _row(r: dict) -> dict:
    return {"id": r["_id"], "phone": r["phone"], "first_name": r["first_name"], "city": r.get("city") or "", "status": r["status"],
            "score": (r.get("result") or {}).get("score"), "gap_year": (r.get("gap") or {}).get("gap_year"),
            "website": (r.get("site") or {}).get("host") or (day1.site_host(r.get("website_url")) or None),
            "website_read": (r.get("site") or {}).get("read"), "findings": r.get("finding_keys") or [],
            "created_at": r["created_at"].isoformat(), "duration_ms": r.get("duration_ms"), "attempts": r.get("attempts", 0),
            "error": r.get("error"), "sent_at": r["sent_at"].isoformat() if r.get("sent_at") else None, "send_count": r.get("send_count", 0),
            "last_send": {"ok": r["last_send"]["ok"], "via": r["last_send"]["via"], "at": r["last_send"]["at"].isoformat()} if r.get("last_send") else None,
            "delivered": bool(r.get("delivered_at")), "read": bool(r.get("read_at")), "opened_at": r["opened_at"].isoformat() if r.get("opened_at") else None,
            "saved": bool(r.get("user_id")), "share_url": day1_runs.share_url(r), "png_url": day1_runs.media_url(r, "png") if r["status"] == "done" else None,
            "pdf_url": day1_runs.media_url(r, "pdf") if r["status"] == "done" else None, "ai_cost_inr": r.get("ai_cost_inr", 0.0)}


@admin.get("/cohorts/{code}/runs")
async def cohort_runs(code: str, status: str = ""):
    q = {"cohort": code.strip().upper()[:40]}
    if status in ("queued", "building", "done", "failed"):
        q["status"] = status
    elif status == "pending":
        q.update(status="done", sent_at=None)
    return [_row(r) async for r in db().day1_runs.find(q, {"report": 0}).sort("created_at", -1).limit(2000)]


@admin.post("/cohorts/{code}/send-pending")
async def send_pending(code: str):
    ids = [r["_id"] async for r in db().day1_runs.find({"cohort": code.strip().upper()[:40], "status": "done", "sent_at": None}, {"_id": 1})]
    if ids:
        day1_runs.in_background(day1_runs.send_many(ids))
    return {"queued": len(ids)}


@admin.post("/runs/{run_id}/send")
async def send_one(run_id: str):
    run = await db().day1_runs.find_one({"_id": run_id})
    if not run:
        raise HTTPException(404, "Run not found")
    if run["status"] != "done":
        raise HTTPException(400, "This report isn't built yet.")
    result = await day1_runs.send(run)
    if result["via"] == "already-sending":
        raise HTTPException(409, "This report is being sent right now.")
    if not result["sent"]:
        raise HTTPException(502, "WhatsApp didn't accept the message. Check the AiSensy campaign, then try again.")
    return result


@admin.post("/runs/{run_id}/retry")
async def retry_one(run_id: str):
    if not await day1_runs.retry(run_id):
        raise HTTPException(400, "Only a failed report can be tried again.")
    return {"ok": True}


@admin.get("/cohorts/{code}/export.csv")
async def export_csv(code: str):
    code = code.strip().upper()[:40]
    body = day1_runs.to_csv(await day1_runs.export_rows(code))
    stamp = now().astimezone(IST).strftime("%Y-%m-%d")
    return PlainTextResponse(body, media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="day1-{code}-{stamp}.csv"', "Cache-Control": "no-store"})


@admin.get("/logs")
async def logs():
    c = db().day1_runs.count_documents
    since = now() - timedelta(days=1)
    recent = [r async for r in db().day1_runs.find({"status": "done", "finished_at": {"$gte": since}}, {"duration_ms": 1, "ai_cost_inr": 1, "png_bytes": 1})]
    failed = [_row(r) async for r in db().day1_runs.find({"status": "failed"}, {"report": 0}).sort("finished_at", -1).limit(50)]
    send_failed = [_row(r) async for r in db().day1_runs.find({"last_send.ok": False}, {"report": 0}).sort("last_send.at", -1).limit(50)]
    durations = sorted(r.get("duration_ms") or 0 for r in recent)
    return {"queue": {"queued": await c({"status": "queued"}), "building": await c({"status": "building"}), "failed": await c({"status": "failed"}),
                      "done_24h": len(recent)},
            "seconds": {"average": round(sum(durations) / len(durations) / 1000, 1) if durations else None,
                        "slowest": round(durations[-1] / 1000, 1) if durations else None},
            "ai": {"cost_per_run_inr": round(sum(r.get("ai_cost_inr") or 0 for r in recent) / len(recent), 2) if recent else 0.0,
                   "note": "The Day-1 run uses no AI: the website check, score, findings and arithmetic are all code."},
            "png_kb_max": round(max((r.get("png_bytes") or 0) for r in recent) / 1024) if recent else None,
            "workers": settings.day1_workers, "daily_cap": settings.day1_daily_cap,
            "today": ((await db().day1_counters.find_one({"_id": now().astimezone(IST).strftime("%Y-%m-%d")})) or {}).get("n", 0),
            "failures": failed, "send_failures": send_failed,
            "whatsapp": {"campaign": bool(settings.aisensy_report_campaign), "media": settings.aisensy_report_media, "link": settings.aisensy_report_link,
                         "status_webhook": bool(settings.day1_status_secret)}}


async def ensure_indexes() -> None:
    await day1_runs.ensure_indexes()
