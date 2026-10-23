"""
Lead Response (v2.2, Growth Mentorship): every new inquiry gets its first reply within seconds, day or night, from the owner's
own WhatsApp number — with a call or a visit to book. Prices, quotes and negotiation always go to a person.

What it does, once the owner switches it on (off by default):
- A new website, lead-magnet or email inquiry (an email's number only when its sender is verified) gets ONE first reply at once,
  from the owner's own number, through the approved template `tpl_lead_response` ({{1}} first name, {{2}} business, {{3}} the
  booking line). It replaces the plain instant reply for that inquiry. Same rules as the instant reply: once a day per customer
  number, at most growth.INSTANT_PER_DAY a day. If it can't go (number not connected, no template…) the reason is logged and the
  owner told — nothing else is sent, and never from the hub's own number (agents/growth.py lead_response_reply).
- The booking line: "Pick a time for {what} here: {link}" (the owner's own calendar page), or "Would {slot1} or {slot2} suit you
  for {what}? Just reply with the time." — the next two times inside the owner's hours, India time, never on a Sunday. The hub
  doesn't see the owner's calendar: the customer's answer arrives in WhatsApp chats and is handed to a person to confirm.
- With "prices to a person" on (the default), the WhatsApp AI never shares a price, quote or discount and never negotiates:
  it says the team will share it and hands the chat over (chat_ai.reply → ai/chat_tasks.reply booking_only).
- Instagram and Facebook DMs need the owner's Employz.ai inbox connected — a setup step, not set up here yet.

lead_response_settings {_id: ws, on, booking link|times, booking_link, what, hours "10-19", prices_to_person, updated_at, by}
lead_response_log      {_id, ws, lead_id, at, seconds, sent, via, source, booking, to, test}

GET  /api/lead-response            settings, connection, preview, last 30 days measured from the log, channels (manager)
PUT  /api/lead-response/settings   (owner)
POST /api/lead-response/test       the first reply to the owner's own number, through the same template (owner)
"""
import hashlib
import re
import statistics
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import integrations, plans
from ..agents import catalog, jobs
from ..agents.approvals import owner_whatsapp
from ..context import Ctx, feature
from ..db import IST, db, now
from ..services import new_id, notify, track
from ..text import greeting_name

router = APIRouter(prefix="/api")

SOURCES = ("website", "guide", "lead_magnet", "email")
WHAT = ("a quick call", "a site visit", "a showroom visit")
DEFAULTS = {"on": False, "booking": "times", "booking_link": "", "what": "a quick call", "hours": "10-19", "prices_to_person": True}
TESTS_PER_DAY = 5
# Meta refuses a template that ends (or starts) with a variable, so the wording closes with a fixed line
TEMPLATE_CLOSE = "We look forward to speaking with you."
TEMPLATE_HINT = "Hello {{1}}, thank you for contacting {{2}}. {{3}} " + TEMPLATE_CLOSE

catalog.register("lead_response", {
    "name": "Lead Response", "feature": "lead_response", "trigger": "lead", "customer_facing": True, "info_only": True, "group": "own_page",
    "what": "Every new inquiry gets a first reply within 2 minutes, day or night, from your own WhatsApp number — with a call or visit "
            "to book. Prices and quotes come to you.",
    "when": "The moment a website, lead magnet or email inquiry comes in; set up on the Lead Response page"})


# ───────────────────────── settings ─────────────────────────
async def settings_of(ws: str) -> dict:
    doc = await db().lead_response_settings.find_one({"_id": ws}) or {}
    return {**DEFAULTS, **{k: v for k, v in doc.items() if k in DEFAULTS and v is not None}}


async def active(owner: dict) -> dict | None:
    """The settings when Lead Response is in the plan and switched on, else None."""
    if not plans.has(owner, "lead_response"):
        return None
    st = await settings_of(owner["_id"])
    return st if st["on"] else None


def hours_of(st: dict) -> tuple[int, int]:
    m = re.fullmatch(r"(\d{1,2})-(\d{1,2})", str(st.get("hours") or ""))
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if 0 <= a < b <= 24:
            return a, b
    return 10, 19


# ───────────────────────── the booking line ─────────────────────────
def next_slots(at: datetime, hours: tuple[int, int], n: int = 2, gap_hours: int = 2) -> list[datetime]:
    """The next `n` whole-hour times (India time) at least an hour from `at`, inside the owner's hours (a time must start
    before closing), never on a Sunday, `gap_hours` apart so the customer gets a real choice."""
    start, end = hours
    t = at.astimezone(IST).replace(minute=0, second=0, microsecond=0) + timedelta(hours=2)   # ≥ 1 hour away, on the hour
    out: list[datetime] = []
    for _ in range(24 * 14):
        if len(out) >= n:
            break
        if t.weekday() == 6 or t.hour >= end:
            t = (t + timedelta(days=1)).replace(hour=start)
            continue
        if t.hour < start:
            t = t.replace(hour=start)
            continue
        out.append(t)
        t += timedelta(hours=gap_hours)
    return out


def _clock(t: datetime) -> str:
    if t.hour == 12:
        return "12 noon"
    return f"{t.hour % 12 or 12} {'am' if t.hour < 12 else 'pm'}"


def slot_text(t: datetime, at: datetime) -> str:
    today = at.astimezone(IST).date()
    d = t.date()
    when = ("today" if d == today else "tomorrow" if d == today + timedelta(days=1) else f"{t.strftime('%A')} {d.day} {t.strftime('%b')}")
    return f"{when} at {_clock(t)}"


def booking_line(st: dict, at: datetime) -> str:
    what = st.get("what") or DEFAULTS["what"]
    if st.get("booking") == "link" and st.get("booking_link"):
        return f"Pick a time for {what} here: {st['booking_link']}"
    s1, s2 = next_slots(at, hours_of(st))
    return f"Would {slot_text(s1, at)} or {slot_text(s2, at)} suit you for {what}? Just reply with the time."


def wording(st: dict) -> str:
    """The booking line as a pattern: the link, or the two-times wording with the owner's hours (the times themselves change
    with each inquiry). What the owner reads in the preview and approves when switching on."""
    what = st.get("what") or DEFAULTS["what"]
    if st.get("booking") == "link" and st.get("booking_link"):
        return f"Pick a time for {what} here: {st['booking_link']}"
    a, b = hours_of(st)
    return f"Would [time] or [another time] suit you for {what}? Just reply with the time. (Times between {a}:00 and {b}:00, never on a Sunday.)"


def sample_key(st: dict, business_name: str) -> str:
    return hashlib.sha1(f"{wording(st)}\n{business_name}".encode()).hexdigest()[:16]


async def booking_rule(owner: dict) -> str | None:
    """For the WhatsApp AI: the booking line when Lead Response is on with prices going to a person, else None (as before)."""
    st = await active(owner)
    if st is None or not st.get("prices_to_person", True):
        return None
    return booking_line(st, now())


# ───────────────────────── the log ─────────────────────────
def seconds_since(created, at: datetime) -> float | None:
    if not isinstance(created, datetime):
        return None
    created = created if created.tzinfo else created.replace(tzinfo=timezone.utc)
    return round(max((at - created).total_seconds(), 0.0), 1)


async def log(ws: str, lead: dict | None, res: dict, st: dict, test: bool = False, to: str = "") -> None:
    await db().lead_response_log.insert_one({
        "_id": new_id(), "ws": ws, "lead_id": (lead or {}).get("_id"), "at": res.get("at") or now(), "seconds": res.get("seconds"),
        "sent": bool(res.get("sent")), "via": res.get("via"), "source": (lead or {}).get("source"), "booking": st.get("booking"),
        "to": to or integrations.digits((lead or {}).get("phone", "")), "test": test})


async def tell_owner(ws: str, text: str) -> None:
    """In the hub, at most once a day — the owner fixes it once, not once per inquiry."""
    if await jobs.claim(f"lead-response-notice:{ws}:{now().astimezone(IST).strftime('%Y-%m-%d')}"):
        await notify(ws, text[:400])


def _pct(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile of the measured values."""
    if not values:
        return None
    v = sorted(values)
    k = max(1, -(-len(v) * p // 100))
    return round(v[int(k) - 1], 1)


async def stats(ws: str) -> dict:
    since = now() - timedelta(days=30)
    inquiries = await db().leads.count_documents({"owner_id": ws, "source": {"$in": list(SOURCES)}, "created_at": {"$gte": since}})
    logs = [x async for x in db().lead_response_log.find({"ws": ws, "at": {"$gte": since}, "test": {"$ne": True}, "lead_id": {"$ne": None}})
            .limit(20_000)]
    sent = [x for x in logs if x.get("sent")]
    secs = [float(x["seconds"]) for x in sent if isinstance(x.get("seconds"), (int, float))]
    not_sent: dict[str, int] = {}
    for x in logs:
        if not x.get("sent"):
            not_sent[x.get("via") or "?"] = not_sent.get(x.get("via") or "?", 0) + 1
    replied = 0
    if sent:
        first = {}
        for x in sent:
            if x.get("to") and (x["to"] not in first or x["at"] < first[x["to"]]):
                first[x["to"]] = x["at"]
        async for t in db().wa_threads.find({"ws": ws, "phone": {"$in": list(first)}}, {"phone": 1, "last_in_at": 1}):
            li = t.get("last_in_at")
            if isinstance(li, datetime):
                a = first[t["phone"]]
                li, a = (li if li.tzinfo else li.replace(tzinfo=timezone.utc)), (a if a.tzinfo else a.replace(tzinfo=timezone.utc))
                replied += li > a
    return {"days": 30, "inquiries": inquiries, "first_replies": len(sent), "bookings_asked": len(sent),
            "median_seconds": round(statistics.median(secs), 1) if secs else None, "p90_seconds": _pct(secs, 90),
            "not_sent": not_sent, "replied": replied}


# ───────────────────────── the page ─────────────────────────
async def _connection(ctx: Ctx) -> dict:
    conn = await owner_whatsapp(ctx.ws, ctx.owner)
    return {"number": bool(conn), "template": bool(conn and conn.get("tpl_lead_response")),
            "ready": bool(conn and conn.get("tpl_lead_response")), "template_name": (conn or {}).get("tpl_lead_response") or ""}


def _channels(chat_on: bool, number: bool) -> list[dict]:
    return [
        {"key": "website", "label": "Website form", "ok": True, "note": "Replied to at once"},
        {"key": "lead_magnet", "label": "Lead magnet downloads", "ok": True, "note": "Replied to at once"},
        {"key": "email", "label": "Email inquiries", "ok": True, "note": "When the email has a mobile number and the sender is verified"},
        {"key": "whatsapp", "label": "WhatsApp", "ok": chat_on and number,
         "note": ("The AI chat answers here" if chat_on and number else
                  "Needs your WhatsApp number connected" if not number else "The AI chat is switched off on the WhatsApp chats page")},
        {"key": "instagram_facebook", "label": "Instagram / Facebook DMs", "ok": False,
         "note": "Needs your Employz.ai inbox connected; not set up yet"},
    ]


@router.get("/lead-response")
async def overview(ctx: Ctx = Depends(feature("lead_response", "manager"))):
    st = await settings_of(ctx.ws)
    b = await db().businesses.find_one({"owner_id": ctx.ws}, {"name": 1}) or {}
    conn = await _connection(ctx)
    line = booking_line(st, now())
    biz = b.get("name") or "us"
    chat_on = jobs.on_for(ctx.owner, "wa_sales", await jobs.get_settings(ctx.ws, "wa_sales"))
    doc = await db().lead_response_settings.find_one({"_id": ctx.ws}, {"note": 1}) or {}
    return {"settings": st, "can_edit": ctx.role == "owner", "connected": conn["ready"], "connection": conn, "note": doc.get("note") or "",
            "preview": {"line": line, "params": ["(the customer's first name)", biz, line], "template_hint": TEMPLATE_HINT,
                        "text": f"Hello (first name), thank you for contacting {biz}. {line} {TEMPLATE_CLOSE}", "wording": wording(st),
                        "key": sample_key(st, b.get("name") or "")},
            "stats": await stats(ctx.ws), "channels": _channels(chat_on, conn["number"]), "what_options": list(WHAT),
            "instant_reply_on": await jobs.is_on_for(ctx.owner, "instant_reply")}


class SettingsIn(BaseModel):
    on: bool | None = None
    booking: Literal["link", "times"] | None = None
    booking_link: str | None = Field(default=None, max_length=300)
    what: str | None = Field(default=None, max_length=40)
    hours: str | None = Field(default=None, max_length=5)
    prices_to_person: bool | None = None
    sample_key: str | None = Field(default=None, max_length=40)


def _link_ok(link: str) -> bool:
    if not link or re.search(r"\s", link):
        return False
    try:
        u = urlsplit(link)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return u.scheme == "https" and "." in host and not u.username and not u.password and host.replace(".", "").replace("-", "").isalnum()


@router.put("/lead-response/settings")
async def save_settings(body: SettingsIn, ctx: Ctx = Depends(feature("lead_response", "owner"))):
    st = await settings_of(ctx.ws)
    patch = body.model_dump(exclude_none=True, exclude={"sample_key"})
    if "booking_link" in patch:
        patch["booking_link"] = patch["booking_link"].strip()
        if patch["booking_link"] and not _link_ok(patch["booking_link"]):
            raise HTTPException(400, "The booking link must be your calendar page's full address, starting with https://")
    if "what" in patch:
        what = " ".join(patch["what"].split())
        if len(what) < 3 or re.search(r"[{}<>\n]|https?://", what):
            raise HTTPException(400, "Say what they book in a few plain words, like “a site visit”.")
        patch["what"] = what
    if "hours" in patch:
        m = re.fullmatch(r"(\d{1,2})-(\d{1,2})", patch["hours"].strip())
        if not m or not (0 <= int(m.group(1)) < int(m.group(2)) <= 24):
            raise HTTPException(400, "Hours look like 10-19 (10am to 7pm, India time).")
        patch["hours"] = f"{int(m.group(1))}-{int(m.group(2))}"
    new = {**st, **patch}
    if new["on"] and new["booking"] == "link" and not new["booking_link"]:
        raise HTTPException(400, "Add your booking page link first (it starts with https://), or offer two times instead.")
    b = await db().businesses.find_one({"owner_id": ctx.ws}, {"name": 1}) or {}
    key = sample_key(new, b.get("name") or "")
    if patch.get("on") and not st["on"]:
        # switching on: the owner has the exact wording on screen (the preview's key for these settings)
        if body.sample_key != key:
            raise HTTPException(409, "Read the first reply below first — it changed. Then switch Lead Response on.")
        # and it can actually send: on without a number and template would stop the plain instant reply for nothing
        if not (await _connection(ctx))["ready"]:
            raise HTTPException(400, "Connect your WhatsApp number and add the approved first-reply template in Connections first.")
        patch["note"] = ""
    elif st["on"] and new["on"] and key != sample_key(st, b.get("name") or ""):
        # the booking wording changed while on: off until the owner has read the new wording
        patch.update(on=False, note="Lead Response was switched off because the booking message changed. Read the new first reply and "
                                    "switch it back on.")
    patch.update(updated_at=now(), by=ctx.actor)
    await db().lead_response_settings.update_one({"_id": ctx.ws}, {"$set": patch}, upsert=True)
    if "on" in patch and patch["on"] != st["on"]:
        await track("lead_response_on" if patch["on"] else "lead_response_off", ctx.ws)
    return await overview(ctx)


@router.post("/lead-response/test")
async def send_test(ctx: Ctx = Depends(feature("lead_response", "owner"))):
    """The first reply exactly as a customer would get it, sent to the owner's own number through the same template."""
    conn = await owner_whatsapp(ctx.ws, ctx.owner)
    if not conn:
        raise HTTPException(400, "Connect your WhatsApp number first (Connections).")
    if not conn.get("tpl_lead_response"):
        raise HTTPException(400, "Add the “first reply with a booking” template name in Connections first.")
    day_start = now().astimezone(IST).replace(hour=0, minute=0, second=0, microsecond=0)
    if await db().lead_response_log.count_documents({"ws": ctx.ws, "test": True, "at": {"$gte": day_start}}) >= TESTS_PER_DAY:
        raise HTTPException(429, f"That's {TESTS_PER_DAY} test messages today. Try again tomorrow.")
    st = await settings_of(ctx.ws)
    b = await db().businesses.find_one({"owner_id": ctx.ws}, {"name": 1}) or {}
    line = booking_line(st, now())
    to = ctx.owner.get("phone", "")
    res = await integrations.wa_template(ctx.ws, conn, to, conn["tpl_lead_response"],
                                         [greeting_name(ctx.owner.get("name", "")) or "there", b.get("name") or "us", line],
                                         ctx.owner.get("name", ""), "lead_response_test")
    await log(ctx.ws, None, {"sent": bool(res.get("sent")), "via": res.get("via"), "at": now()}, st, test=True, to=integrations.digits(to))
    if not res.get("sent"):
        raise HTTPException(502, res.get("error") or "WhatsApp didn't accept the message.")
    return {"ok": True, "line": line}


async def ensure_indexes() -> None:
    await db().lead_response_log.create_index([("ws", 1), ("at", -1)])
    await db().lead_response_log.create_index("lead_id")
