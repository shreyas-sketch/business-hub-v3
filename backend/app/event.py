"""
Live event pack — the 3-day AI Advantage Membership event.

An attendee opens the event link (Admin → Live event), types their website and GST number, answers three quick taps (GST
monthly or quarterly, how many people work with them, and — only when the GST number can't tell — the exact business type),
and sees what the hub builds for them, before any login:

    rules, in seconds   compliance calendar (from the GST number), website check, brand colours and logo, Knowledge Hub started
    AI, about a minute  Knowledge Hub filled from the website, 3 brand messages, a website draft, ways to get more leads
    drawn on demand     a WhatsApp Status poster and a social post, in their colours

Then email + WhatsApp number → the hub's own WhatsApp login code → the account is created (or logged into) and everything
above is put inside it. Nothing that is already in an account is overwritten: a field, a website or a brand kit that exists
is kept, and the attendee is told which ones.

Safety:
- the link only works while the admin has the event open, and each event has a cap on packs and on packs that get AI
  (no account pays for pre-login AI; the event does, and the cost is recorded on each run);
- the GST number is only checked (format, check digit) and read for its state and business type — no GST portal is called;
- the website is read with the same safe reader as onboarding (public addresses only, size and time limits);
- a pack is only put into an account by the person holding its link who also proves a WhatsApp number with the code;
- the queue lives in the database, so several server processes share it and closing the tab loses nothing.

event_runs {_id, token, cohort, status queued|rules|ai_queued|ai|done|failed, input, gst, identity, ai_allowed, steps {key: {status, note}},
            site, compliance, check, design, profile, profile_source, brand {options, pick}, website, ideas, business_type,
            ai_calls, ai_cost_inr, user_id, saved_at, applied, timings}
event_files {_id "{run}:{key}", run_id, data, ctype}       logo, and the drawn designs (cached per brand-message pick)
event_counters {_id cohort, packs, ai}
"""
import asyncio
import hashlib
import json
import logging
import math
import random
import re
import secrets
import time
from datetime import date, datetime, timedelta

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from . import brand_kit as bk, compliance_rules as rules_mod, designs, fetch, knowledge, plans
from . import score as scoring
from .ai import onboard_tasks, tasks
from .ai.provider import AIError, cost_inr
from .db import IST, db, now
from .services import new_id, note_ai_failure, notify, track

log = logging.getLogger("event")
MAX_ATTEMPTS = 3
STALE_RULES_SECONDS = 180    # phase 1 writes as it goes; this long without a write means the process died
SAME_PACK_LIMIT = 2          # packs per GST number or website per event (a back-button resubmit, a partner's phone)
LOGO_MAX_PIXELS = 4_000_000
DEFAULT_RULES_SECONDS = 5
DEFAULT_AI_SECONDS = 45

STEP_KEYS = ("site", "compliance", "check", "brand", "knowledge", "brand_message", "lead_ideas", "website")
AI_STEPS = ("brand_message", "lead_ideas", "website")
CONSENT_TEXT = ("I run this business. The hub may read its public website and use the GST number to work out its filing dates. "
                "Everything it writes is a draft for me to check.")


class EventError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


# ───────────────────────── the GST number ─────────────────────────
CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
STATE_CODES = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh", "05": "Uttarakhand", "06": "Haryana",
    "07": "Delhi", "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh", "13": "Nagaland",
    "14": "Manipur", "15": "Mizoram", "16": "Tripura", "17": "Meghalaya", "18": "Assam", "19": "West Bengal", "20": "Jharkhand",
    "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat", "25": "Dadra and Nagar Haveli and Daman and Diu",
    "26": "Dadra and Nagar Haveli and Daman and Diu", "27": "Maharashtra", "28": "Andhra Pradesh", "29": "Karnataka", "30": "Goa",
    "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry", "35": "Andaman and Nicobar Islands", "36": "Telangana",
    "37": "Andhra Pradesh", "38": "Ladakh",
}
# the 4th letter of the PAN inside the GST number says what kind of taxpayer it is
PAN_KINDS = {
    "P": ("Individual", ["proprietor"]),
    "F": ("Firm or LLP", ["partnership", "llp"]),
    "C": ("Company", ["pvt_ltd", "opc", "other"]),
}
OTHER_KINDS = ("other",)
ENTITY_LABELS = dict(knowledge.ENTITIES)


def gstin_check_char(first14: str) -> str:
    total = 0
    for i, ch in enumerate(first14):
        p = CHARSET.index(ch) * (2 if i % 2 else 1)
        total += p // 36 + p % 36
    return CHARSET[(36 - total % 36) % 36]


def parse_gstin(raw: str) -> dict:
    """What the GST number itself says: valid or not, the state, and which business types fit. No portal is called."""
    g = re.sub(r"\s+", "", (raw or "")).upper()
    if not g:
        return {"gstin": "", "valid": False, "error": "Type the 15-character GST number, or tick “Not registered for GST”."}
    if len(g) != 15 or not GSTIN_RE.match(g):
        return {"gstin": g, "valid": False, "error": "A GST number has 15 characters, like 27ABCDE1234F1Z5."}
    if gstin_check_char(g[:14]) != g[14]:
        return {"gstin": g, "valid": False, "error": "This GST number doesn't add up — check each character again."}
    kind, entities = PAN_KINDS.get(g[5], ("Trust, HUF, society or other", list(OTHER_KINDS)))
    state = STATE_CODES.get(g[:2])
    return {"gstin": g, "valid": True, "state": state, "state_code": g[:2], "pan_kind": kind,
            "entities": [{"value": e, "label": ENTITY_LABELS[e]} for e in entities], "ask_entity": len(entities) > 1}


# ───────────────────────── the event (a cohort with an `event` section) ─────────────────────────
DEFAULT_EVENT = {"open": False, "ai": True, "pack_cap": 400, "ai_cap": 300, "trial_until": None}


def event_of(cohort: dict | None) -> dict:
    return {**DEFAULT_EVENT, **((cohort or {}).get("event") or {})}


async def cohort(code: str) -> dict | None:
    code = (code or "").strip().upper()[:40]
    return await db().cohorts.find_one({"code": code}) if code else None


async def _take(code: str, field: str, cap: int) -> bool:
    try:
        await db().event_counters.update_one({"_id": code}, {"$setOnInsert": {"packs": 0, "ai": 0}}, upsert=True)
    except DuplicateKeyError:
        pass
    return bool(await db().event_counters.find_one_and_update({"_id": code, field: {"$lt": cap}}, {"$inc": {field: 1}}))


async def _give_back(code: str, field: str) -> None:
    await db().event_counters.update_one({"_id": code}, {"$inc": {field: -1}})


async def counters(code: str) -> dict:
    c = await db().event_counters.find_one({"_id": code}) or {}
    return {"packs": c.get("packs", 0), "ai": c.get("ai", 0)}


# ───────────────────────── a new pack ─────────────────────────
async def create(code: str, inp: dict, identity: dict, gst: dict | None) -> dict:
    c = await cohort(code)
    ev = event_of(c)
    if not c:
        raise EventError(404, "This event link isn't valid. Please use the link shown in the hall.")
    if not ev["open"]:
        raise EventError(403, "This event link is closed right now.")
    same = [{"gst.gstin": gst["gstin"]}] if gst else []
    if inp.get("website_url"):
        same.append({"input.website_url": inp["website_url"]})
    if same and await db().event_runs.count_documents({"cohort": c["code"], "$or": same}) >= SAME_PACK_LIMIT:
        raise EventError(409, "A pack for this GST number or website was already built at this event. Open it on the phone you used, "
                              "or ask the team at the desk.")
    if not await _take(c["code"], "packs", int(ev["pack_cap"])):
        await track("event_pack_cap", None, cohort=c["code"])
        raise EventError(429, "We've built as many packs as this event allows. Please ask the team at the desk.")
    ai_allowed = bool(ev["ai"]) and await _take(c["code"], "ai", int(ev["ai_cap"]))
    at = now()
    steps = {k: {"status": "waiting"} for k in STEP_KEYS}
    if not inp.get("website_url"):
        steps["site"] = {"status": "skipped", "note": "No website given."}
        steps["check"] = {"status": "skipped", "note": "No website to check."}
    if not ai_allowed:
        for k in ("brand_message", "lead_ideas", "website"):
            steps[k] = {"status": "after_login", "note": "Made in your hub after you log in (1 AI run)."}
    run = {"_id": new_id(), "token": secrets.token_urlsafe(16), "cohort": c["code"], "status": "queued", "attempts": 0, "queued_at": at,
           "created_at": at, "input": inp, "gst": gst, "identity": identity, "ai_allowed": ai_allowed, "steps": steps,
           "consent": {"text": CONSENT_TEXT, "at": at}, "user_id": None, "ai_calls": 0, "ai_cost_inr": 0.0, "timings": {}}
    try:
        await db().event_runs.insert_one(run)
    except Exception:
        await _give_back(c["code"], "packs")
        if ai_allowed:
            await _give_back(c["code"], "ai")
        raise
    await track("event_pack", None, cohort=c["code"], ai=ai_allowed)
    return run


# ───────────────────────── pieces made by rules ─────────────────────────
def site_summary(facts) -> dict:
    return {"url": facts.url, "host": re.sub(r"^www\.", "", (re.sub(r"^https?://", "", facts.url).split("/")[0])), "read": True,
            "title": facts.title, "description": facts.description, "logo": facts.logo, "colour": facts.colour, "checks": facts.checks,
            "phones": facts.phones[:3], "emails": facts.emails[:2], "whatsapp": facts.whatsapp}


def website_check(site: dict | None) -> dict | None:
    if not site or not site.get("read"):
        return None
    checks = site.get("checks") or {}
    items = [{"key": k, "label": label, "ok": bool(checks.get(k)), "fix": fix} for k, label, fix in scoring.WEBSITE_CHECKS]
    return {"passed": sum(1 for i in items if i["ok"]), "total": len(items), "items": items,
            "fixes": [i["fix"] for i in items if not i["ok"]][:3]}


async def compliance_preview(identity: dict, start: date | None = None) -> dict:
    """The filings this business has in the next 12 months, from the same rules table the calendar uses."""
    from .routers.compliance import review_stamp, rules
    start = start or now().astimezone(IST).date()
    end = start + timedelta(days=365)
    items, needs_date = [], []
    for r in await rules():
        if not rules_mod.applies(r, identity):
            continue
        if (r.get("due") or {}).get("type") == "owner_date":
            needs_date.append(r["title"])
            continue
        for label, when in rules_mod.due_dates(r, start, end):
            items.append({"title": r["title"], "period": label, "due_date": when.isoformat(), "category": r.get("category", "tax"),
                          "authority": r.get("authority", "")})
    items.sort(key=lambda x: (x["due_date"], x["title"]))
    month = start.strftime("%Y-%m")
    return {"count": len(items), "this_month": sum(1 for i in items if i["due_date"][:7] == month), "next": items[:8],
            "kinds": sorted({i["title"] for i in items}), "needs_date": needs_date, "review": (await review_stamp())["line"]}


def profile_from_site(facts) -> dict:
    """The Knowledge Hub's basics read straight off the website (no AI): name, what they do, city, offers, contact."""
    return onboard_tasks.mock_from_site(facts)


def profile_from_words(name: str, about: str, city: str = "") -> dict:
    return {"name": name[:80], "city": city[:80] or onboard_tasks._city(about), "industry": about.strip().split("\n")[0][:200], "offers": [],
            "ideal_customer": "", "why_us": "", "proof": "", "whatsapp": "", "email": "", "language": "English"}


async def brand_starter(run_id: str, site: dict | None, name: str) -> dict:
    """Colours (from the logo when it can be read, else the website's theme colour) and the text logo."""
    colour = (site or {}).get("colour") or None
    design = bk.default_design(name, colour)
    source = "website" if colour else "default"
    if site and site.get("logo"):
        try:
            data, ctype = await asyncio.wait_for(fetch.fetch_image(site["logo"]), 8)
            w, h = await asyncio.to_thread(_image_size, data)
            if w * h > LOGO_MAX_PIXELS:
                raise ValueError("logo too large")
            picked = await asyncio.to_thread(bk.from_image, data)
            await db().event_files.replace_one({"_id": f"{run_id}:logo"}, {"_id": f"{run_id}:logo", "run_id": run_id, "data": data, "ctype": ctype},
                                               upsert=True)
            design["colors"].update(primary=picked["primary"].upper(), secondary=picked["secondary"].upper(), accent=picked["accent"].upper())
            design["logo"] = True
            source = "logo"
        except Exception:  # noqa: BLE001  an unreadable logo just means a text logo
            pass
    design["source"] = source
    return design


def _image_size(data: bytes) -> tuple[int, int]:
    """Width and height from the header only (nothing decoded yet)."""
    import io

    from PIL import Image
    with Image.open(io.BytesIO(data), formats=("PNG", "JPEG")) as im:
        return im.size


# ───────────────────────── the queue ─────────────────────────
async def ensure_indexes() -> None:
    d = db()
    await d.event_runs.create_index("token", unique=True)
    await d.event_runs.create_index([("status", 1), ("queued_at", 1)])
    await d.event_runs.create_index([("cohort", 1), ("created_at", -1)])
    await d.event_runs.create_index("user_id")
    await d.event_files.create_index("run_id")


async def _claim(from_status: str, to_status: str) -> dict | None:
    """Takes the oldest waiting pack. Each claim gets its own key: every write a build makes carries it, so if a pack is ever
    handed out twice (a process that looked dead wasn't), only the current build's writes land."""
    return await db().event_runs.find_one_and_update(
        {"status": from_status, "queued_at": {"$lte": now()}},
        {"$set": {"status": to_status, "started_at": now(), "claim": secrets.token_hex(6)}, "$inc": {"attempts": 1}},
        sort=[("queued_at", 1)], return_document=ReturnDocument.AFTER)


def _stale_seconds(phase: str) -> int:
    # a build writes after every step (that write is its heartbeat); the longest step is one AI call with its wait in line
    return STALE_RULES_SECONDS if phase == "rules" else _ai_seconds() + 120


async def requeue_stale() -> None:
    for working, waiting in (("rules", "queued"), ("ai", "ai_queued")):
        cutoff = now() - timedelta(seconds=_stale_seconds(working))
        await db().event_runs.update_many({"status": working, "started_at": {"$lt": cutoff}, "attempts": {"$lt": MAX_ATTEMPTS}},
                                          {"$set": {"status": waiting, "queued_at": now(), "error": "Stopped part-way; tried again.", "claim": None}})
        async for r in db().event_runs.find({"status": working, "started_at": {"$lt": cutoff}, "attempts": {"$gte": MAX_ATTEMPTS}}, {"_id": 1}):
            if working == "rules":
                await db().event_runs.update_one({"_id": r["_id"], "status": "rules"}, {"$set": {"status": "failed", "finished_at": now(),
                                                                                                "error": "Stopped part-way three times.",
                                                                                                "apply_pending": False}})
            else:
                await _finish(r["_id"], "ai", "Stopped part-way three times.")


async def _finish(run_id: str, from_status: str, error: str) -> None:
    """Ends a pack whose AI phase couldn't complete: what was built stays, and each piece not written becomes one tap in the hub."""
    run = await db().event_runs.find_one({"_id": run_id}) or {}
    steps = {f"steps.{k}": {"status": "after_login", "note": "Couldn't be written just now — one tap in your hub after you log in."}
             for k, v in (run.get("steps") or {}).items() if (v or {}).get("status") in ("waiting", "working") and k in AI_STEPS + ("knowledge",)}
    r = await db().event_runs.update_one({"_id": run_id, "status": from_status},
                                         {"$set": {"status": "done", "finished_at": now(), "error": error, "claim": None, **steps}, "$unset": {"_facts": ""}})
    if r.modified_count:
        await late_apply(run_id)


async def _set(run: dict, fields: dict) -> None:
    """A build's write: also its heartbeat, and only while this build still holds the pack."""
    await db().event_runs.update_one({"_id": run["_id"], "claim": run.get("claim")}, {"$set": {**fields, "started_at": now()}})


async def _step(run: dict, key: str, status: str, note: str = "") -> None:
    await _set(run, {f"steps.{key}": {"status": status, **({"note": note} if note else {})}})


async def build_rules(run: dict) -> bool:
    """Phase 1, for everyone in seconds: read the website, then everything made by rules."""
    started = time.monotonic()
    rid, inp = run["_id"], run["input"]
    try:
        facts = None
        site = None
        if inp.get("website_url"):
            await _step(run, "site", "working")
            try:
                facts = await asyncio.wait_for(fetch.read_site(inp["website_url"]), _site_seconds())
                site = site_summary(facts)
                await _set(run, {"site": site, "steps.site": {"status": "done"}})
            except (fetch.FetchError, asyncio.TimeoutError) as e:
                msg = str(e) if isinstance(e, fetch.FetchError) else "The website took too long to open."
                host = re.sub(r"^www\.", "", re.sub(r"^https?://", "", inp["website_url"]).split("/")[0])
                site = {"url": inp["website_url"], "host": host, "read": False, "error": msg[:200]}
                await _set(run, {"site": site, "steps.site": {"status": "failed", "note": msg[:200]},
                                 "steps.check": {"status": "failed", "note": "The website couldn't be read, so it couldn't be checked."}})
        comp = await compliance_preview(run["identity"])
        await _set(run, {"compliance": comp, "steps.compliance": {"status": "done"}})
        if site and site.get("read"):
            await _set(run, {"check": website_check(site), "steps.check": {"status": "done"}})
        profile = profile_from_site(facts) if facts else profile_from_words(inp.get("business_name") or "", inp.get("about") or "", inp.get("city") or "")
        if not profile.get("name"):
            profile["name"] = (inp.get("business_name") or (site or {}).get("host") or "Your business")[:80]
        design = await brand_starter(rid, site, profile["name"])
        await _set(run, {"design": design, "steps.brand": {"status": "done"}, "profile": profile, "profile_source": "site" if facts else "words",
                         "steps.knowledge": {"status": "working" if run.get("ai_allowed") else "done"}})
        took = round((time.monotonic() - started) * 1000)
        nxt = {"status": "ai_queued", "queued_at": now(), "attempts": 0} if run.get("ai_allowed") else {"status": "done", "finished_at": now()}
        if facts and run.get("ai_allowed"):   # the AI phase reads the website's text; kept on the run only until then
            nxt["_facts"] = {"url": facts.url, "title": facts.title, "description": facts.description, "headings": facts.headings[:25],
                             "text": facts.text[:16000], "phones": facts.phones, "emails": facts.emails, "whatsapp": facts.whatsapp,
                             "logo": facts.logo, "colour": facts.colour, "checks": facts.checks, "links": {}}
        r = await db().event_runs.update_one({"_id": rid, "status": "rules", "claim": run.get("claim")},
                                             {"$set": {**nxt, "timings.rules_ms": took, "error": None}})
        if r.modified_count and nxt["status"] == "done":
            await late_apply(rid)
        return True
    except Exception as e:  # noqa: BLE001
        log.exception("event pack %s (rules) failed", rid)
        last = run.get("attempts", 1) >= MAX_ATTEMPTS
        await db().event_runs.update_one({"_id": rid, "status": "rules", "claim": run.get("claim")}, {"$set": {
            "status": "failed" if last else "queued", "error": f"{type(e).__name__}: {str(e)[:160]}",
            "queued_at": now() + timedelta(seconds=3 * run.get("attempts", 1)), **({"finished_at": now(), "apply_pending": False} if last else {})}})
        return False


def _site_seconds() -> int:
    from .config import settings
    return settings.event_site_seconds


def _ai_seconds() -> int:
    from .config import settings
    return settings.event_ai_seconds


_event_gate: dict = {"sem": None, "loop": None}


def _gate() -> asyncio.Semaphore:
    """Packs share the AI engine with everyone already in their hub: at most EVENT_AI_CONCURRENCY pack calls at once per
    process, so a backlog of packs never starves the live demos (the engine's own gate, AI_CONCURRENCY, is above this)."""
    from .config import settings
    loop = asyncio.get_running_loop()
    if _event_gate["loop"] is not loop:
        _event_gate.update(sem=asyncio.Semaphore(max(1, settings.event_ai_concurrency)), loop=loop)
    return _event_gate["sem"]


async def _ai(rid: str, key: str, make_job, check=None):
    """One AI step, with its own time limit (EVENT_AI_SECONDS, waiting in line included) and its cost recorded on the run.
    When the AI account's limit is reached ("too many requests"), the step waits and asks again until that time is used up —
    a pack in a busy hall arrives later rather than with pieces missing. None when it still didn't work."""
    from .ai.provider import Busy

    async def attempts():
        while True:
            try:
                async with _gate():
                    return await make_job()
            except Busy as e:       # the engine's own retries are used up: wait our turn again, outside the gate
                await asyncio.sleep((e.wait if e.wait is not None else 3.0) + random.uniform(0, 2))
    try:
        result, usage = await asyncio.wait_for(attempts(), _ai_seconds())
        if check:
            result = check(result)
        await db().event_runs.update_one({"_id": rid}, {"$inc": {"ai_calls": 1, "ai_cost_inr": cost_inr(usage) if usage else 0.0}})
        return result
    except (AIError, asyncio.TimeoutError, ValueError, TypeError, KeyError) as e:
        log.warning("event %s: AI step %s didn't work: %s", rid, key, str(e)[:200])
        if isinstance(e, AIError):
            await note_ai_failure(f"event pack · {key}", e)
        return None
    except Exception:  # noqa: BLE001
        log.exception("event %s: AI step %s failed", rid, key)
        return None


class _Facts:
    """The website facts kept from phase 1, in the shape the onboarding reader expects."""
    def __init__(self, d: dict):
        self.__dict__.update({"headings": [], "phones": [], "emails": [], "whatsapp": "", "logo": "", "colour": "", "checks": {}, "links": {},
                              "title": "", "description": "", "text": "", "url": ""}, **d)


async def build_ai(run: dict) -> bool:
    """Phase 2: the Knowledge Hub from the website, then brand messages and lead ideas side by side, then the website draft."""
    from .routers.hub import _site_from_ai
    from .routers.leadideas import _ideas_job, _shape, fitting
    from .routers.onboard import _draft
    from .ai import kit_tasks
    started = time.monotonic()
    rid = run["_id"]
    try:
        profile = dict(run.get("profile") or {})
        facts = run.get("_facts")
        inp = run["input"]
        if facts:
            drafted = await _ai(rid, "knowledge", lambda: onboard_tasks.brain_from_site(_Facts(facts)), _draft)
        elif (inp.get("about") or "").strip():
            text = f"Business name: {inp.get('business_name', '')}. City: {inp.get('city', '')}. {inp.get('about', '')}"
            drafted = await _ai(rid, "knowledge", lambda: onboard_tasks.brain_from_words(text), _draft)
        else:
            drafted = None
        if drafted:
            # the AI's reading wins where it found something; the rules' reading fills what it left empty
            profile = {**profile, **{k: v for k, v in drafted.items() if v not in ("", [], None)}}
            if inp.get("business_name") and not facts:
                profile["name"] = inp["business_name"][:80]
            await _set(run, {"profile": profile, "profile_source": "ai", "steps.knowledge": {"status": "done"}})
        else:
            await _set(run, {"steps.knowledge": {"status": "done", "note": "Read from your website without AI — check it after you log in."}})
        if not profile.get("industry"):
            for k in ("brand_message", "lead_ideas", "website"):
                await _step(run, k, "after_login", "Add what your business does in the Knowledge Hub first.")
        else:
            await _step(run, "brand_message", "working")
            await _step(run, "lead_ideas", "working")
            kind = kit_tasks.business_type(profile)
            allowed = fitting(kind)

            def brand_shape(r):
                opts = [{"message": str(o.get("message", ""))[:160], "pitch": str(o.get("pitch", ""))[:700]}
                        for o in (r.get("options") or []) if isinstance(o, dict) and str(o.get("message", "")).strip()][:3]
                if not opts:
                    raise ValueError("no brand message")
                return opts
            options, ideas = await asyncio.gather(_ai(rid, "brand_message", lambda: tasks.brand_message(profile), brand_shape),
                                                  _ai(rid, "lead_ideas", lambda: _ideas_job(profile, kind, allowed, {}), lambda r: _shape(r, allowed)))
            if options:
                await _set(run, {"brand": {"options": options, "pick": 0}, "steps.brand_message": {"status": "done"}})
            else:
                await _step(run, "brand_message", "after_login", "Couldn't be written just now — one tap in your hub after you log in.")
            if ideas:
                await _set(run, {"ideas": [dict(x, tried=False, tried_at=None) for x in ideas], "business_type": kind, "steps.lead_ideas": {"status": "done"}})
            else:
                await _step(run, "lead_ideas", "after_login", "Couldn't be written just now — one tap in your hub after you log in.")
            await _step(run, "website", "working")
            brand = options[0] if options else None
            content = await _ai(rid, "website", lambda: tasks.site_content(profile, brand), _site_from_ai)
            if content:
                await _set(run, {"website": content, "steps.website": {"status": "done"}})
            else:
                await _step(run, "website", "after_login", "Couldn't be written just now — one tap in your hub after you log in.")
        took = round((time.monotonic() - started) * 1000)
        r = await db().event_runs.update_one({"_id": rid, "status": "ai", "claim": run.get("claim")},
                                             {"$set": {"status": "done", "finished_at": now(), "timings.ai_ms": took, "error": None}, "$unset": {"_facts": ""}})
        if r.modified_count:
            await late_apply(rid)
        return True
    except Exception as e:  # noqa: BLE001
        log.exception("event pack %s (AI) failed", rid)
        if run.get("attempts", 1) >= MAX_ATTEMPTS:
            await _finish(rid, "ai", f"{type(e).__name__}: {str(e)[:160]}")
        else:
            await db().event_runs.update_one({"_id": rid, "status": "ai", "claim": run.get("claim")}, {"$set": {
                "status": "ai_queued", "error": f"{type(e).__name__}: {str(e)[:160]}", "queued_at": now() + timedelta(seconds=3 * run.get("attempts", 1))}})
        return False


PHASES = {"rules": ("queued", "rules", build_rules), "ai": ("ai_queued", "ai", build_ai)}


async def drain(rules_workers: int | None = None, ai_workers: int | None = None) -> int:
    """Builds everything waiting now — phase 1 for all, then phase 2 — and returns how many steps finished. (Tests use it;
    the server runs `loop`.)"""
    from .config import settings
    built = 0

    async def worker(phase: str):
        nonlocal built
        waiting, working, build = PHASES[phase]
        while True:
            run = await _claim(waiting, working)
            if not run:
                return
            ok = await build(run)   # not "built += await …": that reads `built` before the await and loses counts
            built += ok

    for phase, n in (("rules", rules_workers), ("ai", ai_workers)):
        await asyncio.gather(*(worker(phase) for _ in range(max(1, n or settings.event_workers))))
    return built


async def _phase_loop(phase: str, capacity: int) -> None:
    """One dispatcher per phase per server process: claims a waiting pack as soon as there's room for it (up to `capacity`
    at once), and asks the database only twice a second when there's nothing waiting."""
    waiting, working, build = PHASES[phase]
    room = asyncio.Semaphore(max(1, capacity))
    running: set = set()
    while True:
        try:
            if phase == "rules":
                await requeue_stale()
            while True:
                await room.acquire()
                try:
                    run = await _claim(waiting, working)
                except BaseException:
                    room.release()      # a database error must never use up a slot for good
                    raise
                if not run:
                    room.release()
                    break
                task = asyncio.create_task(build(run))
                running.add(task)
                task.add_done_callback(lambda t: (running.discard(t), room.release()))
        except asyncio.CancelledError:
            for t in running:
                t.cancel()
            raise
        except Exception:  # noqa: BLE001
            log.exception("event queue (%s)", phase)
        await asyncio.sleep(0.5)


async def loop() -> None:
    """Runs in every server process (main.py): phase 1 (rules) and phase 2 (AI) side by side, so the first things show for
    everyone while earlier packs are still being written."""
    from .config import settings
    await asyncio.gather(_phase_loop("rules", settings.event_workers), _phase_loop("ai", settings.event_ai_workers))


async def retry(run_id: str) -> bool:
    r = await db().event_runs.update_one({"_id": run_id, "status": "failed"}, {"$set": {"status": "queued", "attempts": 0, "queued_at": now(), "error": None}})
    return bool(r.modified_count)


async def add_about(run: dict, name: str, about: str, city: str) -> dict:
    """The website couldn't be read: the business in a line, then the AI phase runs again from it (once per pack)."""
    if run.get("user_id"):
        raise EventError(409, "This pack is saved — add this in your Knowledge Hub.")
    if run["status"] != "done" or run["input"].get("about") or (run.get("profile") or {}).get("industry"):
        raise EventError(409, "This pack is still being built, or already has this.")
    profile = {**(run.get("profile") or {}), **{k: v for k, v in profile_from_words(name, about, city).items() if v}}
    upd = {"input.business_name": name, "input.about": about, "input.city": city, "profile": profile, "profile_source": "words"}
    if run.get("ai_allowed"):
        upd.update({"status": "ai_queued", "queued_at": now(), "attempts": 0, "finished_at": None, "steps.knowledge": {"status": "working"},
                    **{f"steps.{k}": {"status": "waiting"} for k in ("brand_message", "lead_ideas", "website")}})
    else:
        upd["steps.knowledge"] = {"status": "done"}
    r = await db().event_runs.find_one_and_update({"_id": run["_id"], "status": "done", "user_id": None, "input.about": {"$in": [None, ""]}},
                                                  {"$set": upd}, return_document=ReturnDocument.AFTER)
    if not r:
        raise EventError(409, "This pack changed just now. Refresh the page.")
    return r


# ───────────────────────── "about N seconds" ─────────────────────────
_avg = {"at": 0.0, "rules": float(DEFAULT_RULES_SECONDS), "ai": float(DEFAULT_AI_SECONDS)}


async def averages() -> dict:
    if time.monotonic() - _avg["at"] < 10:
        return _avg
    recent = [r.get("timings") or {} async for r in db().event_runs.find({"status": "done"}, {"timings": 1}).sort("finished_at", -1).limit(50)]
    rules_ms = [t["rules_ms"] for t in recent if t.get("rules_ms")]
    ai_ms = [t["ai_ms"] for t in recent if t.get("ai_ms")]
    _avg.update(at=time.monotonic(), rules=(sum(rules_ms) / len(rules_ms) / 1000) if rules_ms else DEFAULT_RULES_SECONDS,
                ai=(sum(ai_ms) / len(ai_ms) / 1000) if ai_ms else DEFAULT_AI_SECONDS)
    return _avg


async def eta(run: dict) -> dict:
    from .config import settings
    a = await averages()
    s = run.get("status")
    out = {"first": 0, "all": 0}
    if s in ("done", "failed"):
        return out
    workers = max(1, settings.event_workers)
    if s in ("queued", "rules"):
        ahead = await db().event_runs.count_documents({"status": "queued", "queued_at": {"$lt": run["queued_at"]}}) if s == "queued" else 0
        out["first"] = int(min(max(round(math.ceil((ahead + 1) / workers) * a["rules"]), 3), 900))
    if run.get("ai_allowed"):
        waiting = await db().event_runs.count_documents({"status": {"$in": ["queued", "ai_queued"]}, "queued_at": {"$lt": run["queued_at"]}})
        out["all"] = int(min(max(round(out["first"] + math.ceil((waiting + 1) / max(1, settings.event_ai_workers)) * a["ai"]), 5), 1800))
    else:
        out["all"] = out["first"]
    return out


# ───────────────────────── what the attendee sees ─────────────────────────
def preview_completeness(run: dict) -> dict:
    """How full their Knowledge Hub will be once they save (their WhatsApp number and email come with the save)."""
    p = run.get("profile") or {}
    b = {**{k: p.get(k) for k in knowledge.BASICS}, "whatsapp": "x", "email": "x", "identity": run.get("identity") or {}}
    opts = (run.get("brand") or {}).get("options") or []
    if opts:
        b["brand"] = {"message": opts[min((run.get("brand") or {}).get("pick", 0), len(opts) - 1)]["message"]}
    return knowledge.completeness(b, {}, 0)


def view(run: dict) -> dict:
    """Everything the link holder may see. It's built from their own public website and GST number; no phone or email is kept
    on a pack before it's saved."""
    p = run.get("profile") or {}
    site = run.get("site") or {}
    design = run.get("design") or {}
    brand = run.get("brand") or {}
    website = run.get("website") or None
    out = {
        "status": run["status"], "saved": bool(run.get("user_id")), "ai": bool(run.get("ai_allowed")),
        "steps": run.get("steps") or {}, "cohort": run["cohort"],
        "business": {"name": p.get("name", ""), "industry": p.get("industry", ""), "city": p.get("city", ""),
                     "offers": [o.get("name") for o in p.get("offers") or [] if o.get("name")][:8],
                     "ideal_customer": p.get("ideal_customer", ""), "why_us": p.get("why_us", ""), "source": run.get("profile_source")},
        "identity": run.get("identity"), "gst": {k: (run.get("gst") or {}).get(k) for k in ("gstin", "state", "pan_kind")} if run.get("gst") else None,
        "site": {"host": site.get("host"), "read": site.get("read"), "error": site.get("error"), "title": site.get("title")} if site else None,
        "check": run.get("check"), "compliance": run.get("compliance"),
        "design": {"colors": design.get("colors"), "source": design.get("source"), "logo": bool(design.get("logo")),
                   "fonts": design.get("fonts")} if design else None,
        "brand": {"options": brand.get("options") or [], "pick": brand.get("pick", 0)},
        "ideas": [{k: x.get(k) for k in ("channel", "title", "why", "first_step")} for x in run.get("ideas") or []],
        "website": {k: website.get(k) for k in ("headline", "subheadline", "cta", "about", "offers", "why")} if website else None,
        "completeness": preview_completeness(run) if p else None,
        # the website couldn't be read: one line about the business lets the AI write the rest
        "pending": bool(run.get("apply_pending")),
        "needs_about": run["status"] == "done" and not run.get("user_id") and not p.get("industry") and not run["input"].get("about"),
    }
    return out


_drawing: dict = {}


async def design_png(run: dict, key: str) -> bytes:
    """Draws once per look, even when the same picture is asked for several times at once."""
    fid, draw = _design_job(run, key)
    cached = await db().event_files.find_one({"_id": fid})
    if cached:
        return bytes(cached["data"])
    if fid not in _drawing:
        _drawing[fid] = asyncio.ensure_future(draw())
        _drawing[fid].add_done_callback(lambda _f, k=fid: _drawing.pop(k, None))
    return await asyncio.shield(_drawing[fid])


def _design_job(run: dict, key: str):
    """The WhatsApp Status poster or the social post, in their colours, with the brand message they picked. Drawn once per pick."""
    brand = run.get("brand") or {}
    pick = brand.get("pick", 0)
    p = run.get("profile") or {}
    opts = brand.get("options") or []
    chosen = opts[min(pick, len(opts) - 1)] if opts else {}
    headline = chosen.get("message") or tasks._short(p.get("industry") or p.get("name") or "", 10)
    pitch = (chosen.get("pitch") or p.get("industry") or "").split(". ")[0].rstrip(".")
    fields = {"status_poster": {"headline": headline, "sub": pitch + ("." if pitch else ""), "cta": "Message us on WhatsApp"},
              "social_square": {"headline": headline, "body": pitch + ("." if pitch else ""), "cta": "Message us"}}[key]
    design = dict(run.get("design") or bk.default_design(p.get("name", "")))
    # drawn once per look: the same words, name and colours give the same picture (the brand messages arrive after the colours)
    look = hashlib.sha1(json.dumps([fields, p.get("name", ""), design.get("colors")], sort_keys=True).encode()).hexdigest()[:12]
    fid = f"{run['_id']}:{key}:{look}"
    return fid, lambda: _draw(run, key, fid, fields, design, p)


async def _draw(run: dict, key: str, fid: str, fields: dict, design: dict, p: dict) -> bytes:
    logo = None
    if design.get("logo"):
        f = await db().event_files.find_one({"_id": f"{run['_id']}:logo"})
        if f:
            try:
                logo = await asyncio.to_thread(lambda: bk.open_image(bytes(f["data"])).convert("RGBA"))
            except Exception:  # noqa: BLE001
                logo = None
    business = {"name": p.get("name", ""), "whatsapp": ""}
    img = await asyncio.to_thread(designs.render, key, fields, design, business, logo)
    small = img.copy()
    small.thumbnail((540, 960))
    data = await asyncio.to_thread(designs.png, small)
    try:
        await db().event_files.replace_one({"_id": fid}, {"_id": fid, "run_id": run["_id"], "data": data, "ctype": "image/png"}, upsert=True)
    except DuplicateKeyError:
        pass
    return data


# ───────────────────────── saving it into the account ─────────────────────────
async def adopt(run: dict, user: dict) -> bool:
    """Ties the pack to this account, once. False when another account saved it first. Saved while still being written: the
    rest is added to the account when it's ready (late_apply)."""
    r = await db().event_runs.find_one_and_update({"_id": run["_id"], "$or": [{"user_id": None}, {"user_id": user["_id"]}]},
                                                  {"$set": {"user_id": user["_id"], "saved_at": now()}}, return_document=ReturnDocument.AFTER)
    if r and r["status"] not in ("done", "failed") and not r.get("applied") and not user.get("team_of"):
        await db().event_runs.update_one({"_id": r["_id"]}, {"$set": {"apply_pending": True}})
    return bool(r)


async def late_apply(run_id: str) -> dict | None:
    """The pack finished after it was saved: what's new goes into the account (still never over what's there). Runs once —
    whoever claims it first, the queue or the save — and only after the save's own apply is done."""
    run = await db().event_runs.find_one_and_update(
        {"_id": run_id, "status": "done", "apply_pending": True, "user_id": {"$ne": None}, "applied": {"$exists": True}},
        {"$set": {"apply_pending": False}}, return_document=ReturnDocument.AFTER)
    if not run:
        return None
    user = await db().users.find_one({"_id": run["user_id"]})
    if not user:
        return None
    try:
        return await apply(run, user, "", "", late=True)
    except Exception:  # noqa: BLE001  the account still has the first part; the owner can make the rest in the hub
        log.exception("event pack %s: adding the late part failed", run_id)
        return None


def _empty(v) -> bool:
    return v in (None, "", [], {})


async def apply(run: dict, user: dict, email: str, name: str, late: bool = False) -> dict:
    """Puts the pack into the account. Anything the account already has is kept, and reported as kept. `late`: the second,
    smaller pass for a pack saved while it was still being written."""
    from .routers.compliance import generate as compliance_generate
    from .routers.hub import ACCENTS, _slugify, _unique_slug
    ws = user["_id"]
    out: dict[str, str] = {}
    if user.get("team_of"):      # a team member's login: their pack never goes into the team owner's hub
        await db().event_runs.update_one({"_id": run["_id"]}, {"$set": {"applied": {"all": "team"}, "apply_pending": False}})
        return {"all": "team"}
    b = await db().businesses.find_one({"owner_id": ws}) or {}
    p = run.get("profile") or {}
    patch: dict = {}
    for k in ("name", "city", "industry", "offers", "ideal_customer", "why_us", "proof", "language"):
        if _empty(b.get(k)) and not _empty(p.get(k)):
            patch[k] = p[k]
    phone10 = re.sub(r"\D", "", user.get("phone", ""))[-10:]
    if _empty(b.get("whatsapp")):
        patch["whatsapp"] = phone10
    if _empty(b.get("email")) and email:
        patch["email"] = email
    if not b.get("identity") and run.get("identity"):
        patch["identity"] = run["identity"]
        out["compliance"] = "added"
    elif run.get("identity"):
        out["compliance"] = "kept"
    opts = (run.get("brand") or {}).get("options") or []
    if opts:
        pick = opts[min((run.get("brand") or {}).get("pick", 0), len(opts) - 1)]
        if _empty((b.get("brand") or {}).get("message")):
            patch["brand"] = {"message": pick["message"], "pitch": pick.get("pitch", ""), "chosen_at": now()}
            patch["brand_options"] = opts
            out["brand_message"] = "added"
        else:
            out["brand_message"] = "kept"
    out["knowledge"] = "added" if any(k in patch for k in ("name", "industry", "offers", "city")) else ("kept" if b.get("name") else "added")
    if not _empty(patch) or not b:
        await db().businesses.update_one({"owner_id": ws}, {"$set": {**patch, "updated_at": now()},
                                                             "$setOnInsert": {"_id": new_id(), "owner_id": ws, "created_at": now()}}, upsert=True)
    b = await db().businesses.find_one({"owner_id": ws}) or {}

    site = run.get("site") or {}
    if site.get("read") and not await db().site_scans.find_one({"_id": ws}, {"_id": 1}):
        await db().site_scans.replace_one({"_id": ws}, {"_id": ws, "url": site["url"], "checks": site.get("checks") or {}, "logo": site.get("logo", ""),
                                                       "colour": site.get("colour", ""), "title": site.get("title", ""), "at": now(), "source": "event"},
                                          upsert=True)
    design = dict(run.get("design") or {})
    kit = await db().brand_kits.find_one({"_id": ws}, {"design": 1}) or {}
    if design and not kit.get("design"):
        stored = {k: v for k, v in design.items() if k != "logo"}
        stored["text_logo"] = (b.get("name") or "")[:40]
        stored["logo_file_id"] = None
        if design.get("logo"):
            f = await db().event_files.find_one({"_id": f"{run['_id']}:logo"})
            if f:
                from . import files
                put = await files.put(ws, bytes(f["data"]), "logo", f.get("ctype") or "image/png", "logo")
                stored["logo_file_id"] = put["_id"]
        await db().brand_kits.update_one({"_id": ws}, {"$set": {"design": stored, "updated_at": now()}}, upsert=True)
        await db().businesses.update_one({"owner_id": ws}, {"$set": {"brand_colors": stored["colors"]}})
        out["brand"] = "added"
    elif design:
        out["brand"] = "kept"

    content = run.get("website")
    if content:
        if await db().sites.find_one({"owner_id": ws}, {"_id": 1}):
            out["website"] = "kept"
        else:
            accent = ((await db().brand_kits.find_one({"_id": ws}, {"design": 1}) or {}).get("design") or {}).get("colors", {}).get("primary") or ACCENTS[0]
            for attempt in range(5):
                slug = await _unique_slug(_slugify(b.get("name") or "business") + (f"-{new_id()[:4]}" if attempt else ""), ws)
                try:
                    await db().sites.insert_one({"_id": new_id(), "owner_id": ws, "slug": slug, "status": "draft", "content": content,
                                                 "generated_content": content, "accent": accent, "brand_accent": accent not in ACCENTS,
                                                 "showcase": False, "views": 0, "created_at": now(), "updated_at": now(), "source": "event"})
                    out["website"] = "added"
                    break
                except DuplicateKeyError:
                    if await db().sites.find_one({"owner_id": ws}, {"_id": 1}):
                        out["website"] = "kept"
                        break
    ideas = run.get("ideas")
    if ideas:
        month = now().astimezone(IST).strftime("%Y-%m")
        try:
            await db().lead_ideas.insert_one({"_id": f"{ws}:{month}", "owner_id": ws, "month": month, "business_type": run.get("business_type", "services"),
                                              "ideas": ideas, "created_at": now(), "by": ws, "source": "event"})
            out["lead_ideas"] = "added"
        except DuplicateKeyError:
            out["lead_ideas"] = "kept"
    if out.get("compliance") == "added":
        await compliance_generate(ws)

    if late:   # what the first pass added stays "added"; only pieces new since then are reported
        first = run.get("applied") or {}
        merged = {**out, **{k: v for k, v in first.items() if v == "added" or k not in out}}
        await db().event_runs.update_one({"_id": run["_id"]}, {"$set": {"applied": merged, "late_applied_at": now()}})
        return merged
    user_patch: dict = {}
    # email login keeps each email on one account: one typed here that another account has stays on the business only
    if email and not user.get("email") and not await db().users.find_one({"email": email, "_id": {"$ne": ws}}, {"_id": 1}):
        user_patch["email"] = email
    if name and not user.get("name"):
        user_patch["name"] = name[:80]
    if not user.get("cohort"):
        user_patch["cohort"] = run["cohort"]
    ev = event_of(await cohort(run["cohort"]))
    until = ev.get("trial_until")
    if isinstance(until, datetime) and until > now():
        # Membership features until the event's last day — unless they already have Membership or more that they keep anyway
        # (paid, or set by the admin). A shorter trial is stretched to the event's end; a longer one is never cut.
        sources = plans.plan_sources(user)
        kept = max((plans.rank(t) for k, t in sources.items() if k != "trial"), default=0)
        trial = user.get("trial") or {}
        valid = isinstance(trial.get("until"), datetime) and trial["until"] > now()
        better = valid and (plans.rank(trial.get("plan", "free")) > plans.rank("lite")      # a higher plan's trial is never swapped down
                            or (plans.rank(trial.get("plan", "free")) == plans.rank("lite") and trial["until"] >= until))
        if kept < plans.rank("lite") and not better:
            user_patch["trial"] = {"plan": "lite", "until": until, "reason": "event", "cohort": run["cohort"]}
            out["trial"] = until.isoformat()
    if user_patch:
        try:
            await db().users.update_one({"_id": ws}, {"$set": user_patch})
        except DuplicateKeyError:  # the email was taken between the check and the save
            user_patch.pop("email", None)
            await db().users.update_one({"_id": ws}, {"$set": user_patch})
    # Day 2 of the event shows the follow-ups the room's own inquiries got, so an event account starts with them switched on
    # (unless the owner has already chosen). They are drafts in the Send list; nothing goes out without the owner's tap.
    from .agents import jobs as agent_jobs
    if not await db().agent_settings.find_one({"_id": f"{ws}:followup"}, {"_id": 1}):
        await agent_jobs.save_settings(ws, "followup", {"on": True, "opted_in": True, "opted_in_by": "event"})
    await db().event_runs.update_one({"_id": run["_id"]}, {"$set": {"applied": out, "applied_at": now()}})
    await track("event_saved", ws, cohort=run["cohort"])
    if out.get("trial"):
        await notify(ws, f"Your event pack is in your hub. Membership features are open until {until.astimezone(IST).strftime('%d %b, %I:%M %p')}.")
    return out


# ───────────────────────── the speaker's live numbers ─────────────────────────
async def live(code: str) -> dict:
    """What the room has done, for the speaker's screen: packs, accounts, and each demo by area."""
    from .routers.dashboard import LANDING_AT
    d = db()
    q = {"cohort": code}
    c = d.event_runs.count_documents
    runs = {"started": await c(q), "waiting": await c({**q, "status": {"$in": ["queued", "rules"]}}),
            "writing": await c({**q, "status": {"$in": ["ai_queued", "ai"]}}), "ready": await c({**q, "status": "done"}),
            "failed": await c({**q, "status": "failed"}), "saved": await c({**q, "user_id": {"$ne": None}})}
    recent = [r async for r in d.event_runs.find({**q, "status": "done"}, {"timings": 1, "ai_cost_inr": 1, "created_at": 1, "finished_at": 1})
              .sort("finished_at", -1).limit(300)]
    secs = sorted((r["finished_at"] - r["created_at"]).total_seconds() for r in recent if r.get("finished_at") and r.get("created_at"))
    first = sorted((r.get("timings") or {}).get("rules_ms", 0) / 1000 for r in recent if (r.get("timings") or {}).get("rules_ms"))
    ai_cost = 0.0
    async for r in d.event_runs.find(q, {"ai_cost_inr": 1}):
        ai_cost += float(r.get("ai_cost_inr") or 0)
    # the room: everyone who saved a pack at this event, plus anyone who joined with this cohort's link
    saved_ids = await d.event_runs.distinct("user_id", {"cohort": code, "user_id": {"$ne": None}})
    users = [u async for u in d.users.find({"$or": [{"cohort": code}, {"_id": {"$in": saved_ids}}]}, {"_id": 1, "sub": 1, "trial": 1})]
    ids = [u["_id"] for u in users]
    since = (await d.cohorts.find_one({"code": code}, {"created_at": 1}) or {}).get("created_at") or (now() - timedelta(days=30))

    async def owners(coll: str, query: dict, key: str = "owner_id") -> int:
        return len(await d[coll].distinct(key, {key: {"$in": ids}, **query})) if ids else 0

    pct60 = 0
    async for b in d.businesses.find({"owner_id": {"$in": ids}}):
        if (await knowledge.completeness_for(b["owner_id"], b))["percent"] >= LANDING_AT:
            pct60 += 1
    scores: dict[str, int] = {}
    if ids:
        async for s in d.scores.find({"ws": {"$in": ids}}, {"ws": 1}):
            scores[s["ws"]] = scores.get(s["ws"], 0) + 1
    paid = sum(1 for u in users if (u.get("sub") or {}).get("status") == "active")
    demos = {
        "lead": {"Website live": await owners("sites", {"status": "live"}),
                 "Lead ideas ready": await owners("lead_ideas", {})},
        "sales": {"Quotations made": await owners("quotes", {"kind": "quote", "created_at": {"$gte": since}}, "ws"),
                  "Payment dues added": await owners("dues", {}, "ws")},
        "brand": {"Brand kit written": await owners("brand_kits", {"messages": {"$exists": True}}, "_id")},
        "operations": {"Compliance calendars": await owners("compliance_items", {}),
                       "SOPs written": await owners("sop_maps", {"processes.status": "written"}, "_id")},
        "conversion": {"Sent themselves an inquiry": await owners("leads", {"source": "whatsapp"}),
                       "Got a website inquiry": await owners("leads", {"source": "website"}),
                       "Quotation from an inquiry": await owners("quotes", {"lead_id": {"$ne": None}}, "ws"),
                       "Found their biggest leak": await owners("leaks", {"result.biggest_leak": {"$ne": None}}, "_id"),
                       "Follow-ups ready": await owners("approvals", {"kind": "followup"}, "ws"),
                       "Follow-ups sent": await owners("approvals", {"kind": "followup", "status": "sent"}, "ws")},
        "data": {"Cash flow ready": await owners("businesses", {"money.bank_balance": {"$exists": True}}),
                 "Target set": await owners("businesses", {"targets.monthly_revenue": {"$gt": 0}}),
                 "Took the score": sum(1 for n in scores.values() if n >= 1), "Re-took the score": sum(1 for n in scores.values() if n >= 2),
                 f"Dashboard open ({LANDING_AT}%+)": pct60},
    }
    return {"code": code, "runs": runs, "accounts": len(ids), "members_paid": paid, "ai_cost_inr": round(ai_cost, 2),
            "seconds": {"first_things": round(first[len(first) // 2], 1) if first else None,
                        "everything_median": round(secs[len(secs) // 2], 1) if secs else None,
                        "everything_slowest": round(secs[-1], 1) if secs else None},
            "counters": await counters(code), "demos": demos}
