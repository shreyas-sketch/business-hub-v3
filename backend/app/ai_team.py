"""
AI team (v2.4, Start → AI team), the Weekly growth line and Quick answers. Everything here is counted by code from the
owner's saved records; no AI is asked anything and no AI token is used.

1. The AI team page — one card per AI role (growth_core.ROLES) whose feature is on for this owner:
   counts for today and this month (India time) read straight from the records the roles leave behind:
       prospector        prospect_searches.found (summed) by `at`; prospects by `kept_at`
       outreach_writer   approvals kind "outreach" by created_at; of those status "sent" via "own" by decided_at;
                         prospects by `replied_at`
       researcher        leads (owner_id) with brief.status "done" by brief.at
       pipeline_keeper   deal_updates by `at`; state "applied" by applied_at
       call_coach        gym_sessions kind "call", source "call_coach" by scored_at, and their average score.overall
       followup          approvals agent "followup" by created_at; of those sent by a person (not by the agent on its own)
       payment_reminder  approvals kind "reminder" by created_at; dues status "paid" by paid_at (paise → ₹)
   its state (each one true, never decoration): Working (a growth_core.working block is open right now), Switched off,
   Waiting for you (with how many), Needs setup (with what is missing), or Idle; its last finished action from the activity
   log with a link to the record; and the feed of the latest 30 actions. "Hours saved" is an estimate from MINUTES_PER below.

2. The Weekly growth line (agent "growth_weekly", feature growth_weekly): every Monday 09:00 India time, one line to the
   owner about last Monday–Sunday — on the owner's WhatsApp from the hub's own number (the same path as the morning
   digest), in the hub's notices, and on the AI team page and the Dashboard. Only when at least one number is above zero.

3. Quick answers (feature quick_answers): six fixed questions with answers computed from the records (answer()).

Records written here: growth_weekly_sent {_id: "<ws>:<YYYY-MM-DD of the Monday the week started>", ws, line, numbers,
from, to, whatsapp, at} — only what the weekly agent sent, so the page can say so. Nothing another feature reads.
"""
import asyncio
import statistics
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

from . import crm, growth_core, plans
from .agents import catalog, jobs
from .agents.catalog import Skip
from .agents.growth import MINUTES
from .agents.runners import inr, plural
from .config import settings
from .db import IST, db, now
from .messaging import whatsapp_template
from .services import notify

FEED_LINES = 30
REFRESH_SECONDS = 15
MONEY_PAGE = "/money"

# Minutes a person would have spent on each action, for "Hours saved" (an ESTIMATE, shown with this table on the page).
# Assumptions, not measurements. Existing values from agents/growth.MINUTES are reused where the action is the same.
MINUTES_PER = [
    # key, what the owner sees, minutes, role
    ("search", "Finding and checking businesses for one search", 20, "prospector"),
    ("outreach_draft", "Writing a first message or follow-up to a business", MINUTES["followup"], "outreach_writer"),
    ("brief", "Looking up a new inquiry and writing a brief", 10, "researcher"),
    ("filed", "Filing one conversation into a deal", 5, "pipeline_keeper"),
    ("call", "Listening to a call and scoring it", 15, "call_coach"),
    ("followup", "Writing a follow-up to an inquiry", MINUTES["followup"], "followup"),
    ("reminder", "Writing a payment reminder", MINUTES["message"], "payment_reminder"),
]

STATE_LABEL = {"working": "Working", "waiting": "Waiting for you", "setup": "Needs setup", "off": "Switched off", "idle": "Idle"}
# where each role's work lives in the hub
ROLE_PAGE = {"prospector": "/prospects", "outreach_writer": "/prospects", "researcher": "/inquiries", "pipeline_keeper": "/pipeline",
             "call_coach": "/call-coach", "followup": "/approvals", "payment_reminder": MONEY_PAGE}
# one line on what to do next, shown when a role has done nothing this month
NEXT_STEP = {
    "prospector": ("Nothing found yet this month. Run a search on Find new customers, or upload your own list.", "/prospects"),
    "outreach_writer": ("No messages written yet this month. Keep a few businesses on Find new customers and ask for first messages.",
                        "/prospects"),
    "researcher": ("No inquiry researched yet this month. A brief is written when a new inquiry comes in.", "/inquiries"),
    "pipeline_keeper": ("Nothing filed yet this month. Paste a chat or speak a voice note on a deal in your pipeline.", "/pipeline"),
    "call_coach": ("No calls scored yet this month. Paste a call or upload a recording on Call coach.", "/call-coach"),
    "followup": ("No follow-ups written yet this month. They are written after each new inquiry.", "/inquiries"),
    "payment_reminder": ("No reminders yet this month. Add money owed with a due date and the customer's phone number.", MONEY_PAGE),
}
WAITING_TEXT = {
    "prospector": ("business kept, waiting for a first message", "businesses kept, waiting for a first message", "/prospects"),
    "outreach_writer": ("message waiting for you to send", "messages waiting for you to send", "/approvals"),
    "pipeline_keeper": ("suggested change waiting for your tap", "suggested changes waiting for your tap", "/pipeline"),
    "followup": ("follow-up in your Send list", "follow-ups in your Send list", "/approvals"),
    "payment_reminder": ("reminder in your Send list", "reminders in your Send list", "/approvals"),
}
SETUP_TEXT = {
    "prospector": "Search needs a server setting. Your own list still works.",
    "call_coach": "Recordings need a server setting. Pasted calls work.",
}
WEEKLY_KEY = "growth_weekly"


# ───────────────────────── periods (India time) ─────────────────────────
def _utc(local: datetime) -> datetime:
    return local.astimezone(timezone.utc)


def day_bounds(at: datetime) -> tuple[datetime, datetime]:
    start = at.astimezone(IST).replace(hour=0, minute=0, second=0, microsecond=0)
    return _utc(start), _utc(start + timedelta(days=1))


def month_bounds(at: datetime) -> tuple[datetime, datetime]:
    start = at.astimezone(IST).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return _utc(start), _utc(end)


def last_week(at: datetime) -> tuple[datetime, datetime]:
    """Last Monday 00:00 to this Monday 00:00, India time (the full week before the one `at` is in)."""
    local = at.astimezone(IST)
    monday = (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return _utc(monday - timedelta(days=7)), _utc(monday)


def _rng(start: datetime, end: datetime) -> dict:
    return {"$gte": start, "$lt": end}


def _aware(dt):
    return dt if not isinstance(dt, datetime) or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt) -> str | None:
    return _aware(dt).isoformat() if isinstance(dt, datetime) else None


def _ist_day(dt: datetime) -> date:
    return _aware(dt).astimezone(IST).date()


def rupees(value: float | int | None) -> str:
    """Rupees (not paise) the owner typed → '₹85,000'."""
    return inr(round(float(value or 0) * 100))


# ───────────────────────── small queries ─────────────────────────
async def _count(coll: str, q: dict) -> int:
    return await db()[coll].count_documents(q)


async def _sum(coll: str, q: dict, field: str) -> float:
    async for g in db()[coll].aggregate([{"$match": q}, {"$group": {"_id": None, "n": {"$sum": f"${field}"}}}]):
        return g.get("n") or 0
    return 0


async def _avg(coll: str, q: dict, field: str) -> float | None:
    async for g in db()[coll].aggregate([{"$match": {**q, field: {"$type": "number"}}}, {"$group": {"_id": None, "n": {"$avg": f"${field}"}}}]):
        return None if g.get("n") is None else round(float(g["n"]), 1)
    return None


# ───────────────────────── what each role counts ─────────────────────────
# role → [(key, label, kind, query builder)]. kind: n (a count), sum:<field> (a sum of a number field), money (a sum of
# paise shown in ₹), avg:<field> (an average). Each builder gets (ws, range) and returns (collection, query).
def _q_found(ws, r): return "prospect_searches", {"ws": ws, "at": r}                                       # noqa: E704
def _q_kept(ws, r): return "prospects", {"ws": ws, "kept_at": r}                                           # noqa: E704
def _q_drafts(ws, r): return "approvals", {"ws": ws, "kind": "outreach", "created_at": r}                  # noqa: E704
def _q_sent(ws, r): return "approvals", {"ws": ws, "kind": "outreach", "status": "sent", "via": "own", "decided_at": r}  # noqa: E704
def _q_replies(ws, r): return "prospects", {"ws": ws, "replied_at": r}                                     # noqa: E704
def _q_briefs(ws, r): return "leads", {"owner_id": ws, "brief.status": "done", "brief.at": r}              # noqa: E704
def _q_filed(ws, r): return "deal_updates", {"ws": ws, "at": r}                                             # noqa: E704
def _q_applied(ws, r): return "deal_updates", {"ws": ws, "state": "applied", "applied_at": r, "undone_at": None}  # noqa: E704 — an undone update no longer counts
def _q_scored(ws, r): return "gym_sessions", {"ws": ws, "kind": "call", "source": "call_coach", "scored_at": r}  # noqa: E704
def _q_fu_drafts(ws, r): return "approvals", {"ws": ws, "agent": "followup", "created_at": r}              # noqa: E704
# "sent by you": a person tapped Send (on WhatsApp or from the connected number) — not sent by the agent on its own
def _q_fu_sent(ws, r): return "approvals", {"ws": ws, "agent": "followup", "status": "sent", "decided_by": {"$nin": ["agent", "auto-approved"]},  # noqa: E704
                                            "decided_at": r}
def _q_prepared(ws, r): return "approvals", {"ws": ws, "kind": "reminder", "created_at": r}                # noqa: E704
def _q_paid(ws, r): return "dues", {"ws": ws, "status": "paid", "paid_at": r}                               # noqa: E704
def _q_searches(ws, r): return "prospect_searches", {"ws": ws, "at": r}                                    # noqa: E704
def _q_new_deals(ws, r): return "leads", {"owner_id": ws, "source": "prospecting", "created_at": r}        # noqa: E704

COUNTS = {
    "prospector": [("found", "Businesses found", "sum:found", _q_found), ("kept", "Kept", "n", _q_kept)],
    "outreach_writer": [("drafts", "Drafts written", "n", _q_drafts), ("sent", "Messages you sent", "n", _q_sent),
                        ("replies", "Replies", "n", _q_replies)],
    "researcher": [("researched", "Leads researched", "n", _q_briefs)],
    "pipeline_keeper": [("filed", "Conversations filed", "n", _q_filed), ("applied", "Updates applied", "n", _q_applied)],
    "call_coach": [("scored", "Calls scored", "n", _q_scored), ("average", "Average score (out of 10)", "avg:score.overall", _q_scored)],
    "followup": [("drafts", "Drafts written", "n", _q_fu_drafts), ("sent", "Sent by you", "n", _q_fu_sent)],
    "payment_reminder": [("prepared", "Reminders prepared", "n", _q_prepared), ("received", "Money marked paid", "money", _q_paid)],
}
# the count behind each "hours saved" row
MINUTES_COUNT = {"search": _q_searches, "outreach_draft": _q_drafts, "brief": _q_briefs, "filed": _q_filed, "call": _q_scored,
                 "followup": _q_fu_drafts, "reminder": _q_prepared}


async def measure(kind: str, coll: str, q: dict) -> float | int | None:
    if kind == "n":
        return await _count(coll, q)
    if kind.startswith("sum:"):
        return int(await _sum(coll, q, kind[4:]))
    if kind == "money":
        return round(int(await _sum(coll, q, "amount")) / 100, 2)
    if kind.startswith("avg:"):
        return await _avg(coll, q, kind[4:])
    raise ValueError(kind)


async def role_counts(ws: str, role: str, at: datetime | None = None) -> list[dict]:
    """[{key, label, kind, today, month}] for one role, India-time today and this month around `at`."""
    at = at or now()
    today, month = _rng(*day_bounds(at)), _rng(*month_bounds(at))
    out = []
    for key, label, kind, build in COUNTS[role]:
        t, m = await asyncio.gather(measure(kind, *build(ws, today)), measure(kind, *build(ws, month)))
        out.append({"key": key, "label": label, "kind": "n" if kind.startswith("sum:") else kind.split(":")[0], "today": t, "month": m})
    return out


async def waiting(ws: str, role: str) -> int:
    """How many drafts or suggestions this role has that the owner hasn't acted on yet."""
    q = {"prospector": ("prospects", {"ws": ws, "status": "kept"}),
         "outreach_writer": ("approvals", {"ws": ws, "kind": "outreach", "status": "pending"}),
         "pipeline_keeper": ("deal_updates", {"ws": ws, "state": "pending"}),
         "followup": ("approvals", {"ws": ws, "agent": "followup", "status": "pending"}),
         "payment_reminder": ("approvals", {"ws": ws, "kind": "reminder", "status": "pending"})}.get(role)
    return await _count(*q) if q else 0


def needs_setup(role: str) -> str | None:
    """What is missing on the server for this role (read live, so a key added later clears it)."""
    from . import competitor_watch, transcribe
    if role == "prospector" and not competitor_watch.places_ready():
        return SETUP_TEXT["prospector"]
    if role == "call_coach" and not transcribe.available():
        return SETUP_TEXT["call_coach"]
    return None


def visible_roles(owner: dict) -> list[str]:
    return [k for k, (_, _, feature) in growth_core.ROLES.items() if plans.has(owner, feature)]


def link_for(ref: str | None, role: str | None = None) -> str:
    """A feed line's record: prospect:<id> → the prospect; lead:<id> → the deal; call:<id> → the scored call; search:<id> →
    Find new customers; update:<id> → the pipeline; an approval (or any other reference) → the Send list. A line without a
    reference opens the role's own page."""
    if not ref:
        return ROLE_PAGE.get(role or "", "/approvals")
    kind, _, rid = str(ref).partition(":")
    rid = quote(rid, safe="")
    if kind == "prospect" and rid:
        return f"/prospects/{rid}"
    if kind == "lead" and rid:
        return f"/pipeline?deal={rid}"
    if kind == "call" and rid:
        return f"/call-coach?call={rid}"
    if kind == "search":
        return "/prospects"
    if kind == "update":
        return "/pipeline"
    return "/approvals"


def _line(e: dict, role_title: str | None = None) -> dict:
    return {"id": e["_id"], "at": _iso(e.get("at")), "role": e.get("agent"), "role_title": role_title or e.get("agent"),
            "text": e.get("text", ""), "status": e.get("status", "done"), "link": link_for(e.get("ref"), e.get("agent"))}


async def role_view(owner: dict, role: str, settings_doc: dict, busy: set, at: datetime | None = None) -> dict:
    ws = owner["_id"]
    title, job, feature = growth_core.ROLES[role]
    counts, n_waiting, last = await asyncio.gather(
        role_counts(ws, role, at), waiting(ws, role),
        db().agent_log.find_one({"ws": ws, "agent": role, "status": "done"}, sort=[("at", -1)]))
    on = jobs.on_for(owner, role, settings_doc)
    setup = needs_setup(role)
    state = ("working" if role in busy else "off" if not on else "waiting" if n_waiting else "setup" if setup else "idle")
    w = WAITING_TEXT.get(role)
    zero = all(not c["month"] for c in counts if c["kind"] in ("n", "money"))
    nxt = NEXT_STEP[role] if zero else None
    return {"key": role, "title": title, "job": job, "feature": feature, "state": state, "state_label": STATE_LABEL[state], "on": on,
            "waiting": n_waiting,
            "waiting_text": (f"{n_waiting} {w[0] if n_waiting == 1 else w[1]}" if w and n_waiting else None),
            "waiting_link": w[2] if w else None, "setup": setup, "counts": counts,
            "last": _line(last, title) if last else None, "page": ROLE_PAGE.get(role),
            "next": {"text": nxt[0], "path": nxt[1]} if nxt else None}


async def hours_saved(ws: str, roles: list[str], at: datetime | None = None) -> dict:
    """This month's estimate: each action the roles finished × the minutes a person would have spent on it."""
    month = _rng(*month_bounds(at or now()))
    rows = []
    for key, what, minutes, role in MINUTES_PER:
        if role not in roles:
            continue
        coll, q = MINUTES_COUNT[key](ws, month)
        n = await _count(coll, q)
        rows.append({"key": key, "what": what, "minutes": minutes, "n": n, "role": role})
    total = sum(r["n"] * r["minutes"] for r in rows)
    return {"estimated": True, "hours": round(total / 60, 1), "minutes": total, "rows": rows}


async def strip(ws: str, at: datetime | None = None) -> dict:
    """The top of the page, this month: businesses found · messages you sent · replies · deals from new customers · value."""
    return await numbers_for(ws, *month_bounds(at or now()))


async def numbers_for(ws: str, start: datetime, end: datetime) -> dict:
    r = _rng(start, end)
    found, sent, replies, deals, value = await asyncio.gather(
        measure("sum:found", *_q_found(ws, r)), measure("n", *_q_sent(ws, r)), measure("n", *_q_replies(ws, r)),
        measure("n", *_q_new_deals(ws, r)), _sum(*_q_new_deals(ws, r), "value"))
    return {"found": found, "sent": sent, "replies": replies, "deals": deals, "value": round(float(value or 0), 2)}


async def page(owner: dict, at: datetime | None = None) -> dict:
    """Everything the AI team page shows."""
    ws = owner["_id"]
    at = at or now()
    roles = visible_roles(owner)
    sets = await jobs.all_settings(ws)
    busy = await growth_core.working_now(ws)
    views = await asyncio.gather(*(role_view(owner, r, sets.get(r, jobs.DEFAULTS), busy, at) for r in roles))
    titles = {k: v[0] for k, v in growth_core.ROLES.items()}
    feed = [_line(e, titles.get(e.get("agent"))) async for e in
            db().agent_log.find({"ws": ws, "agent": {"$in": roles}, "$nor": [{"status": "skipped", "ref": None}]})
            .sort("at", -1).limit(FEED_LINES)] if roles else []      # a scheduled run with nothing to do is not an action
    top = None
    if "prospector" in roles:
        top = await strip(ws, at)
        if not any(top[k] for k in ("found", "sent", "replies", "deals")):
            top["next"] = {"text": "Nothing yet this month. Start on Find new customers: search for businesses near you, or upload your "
                                   "own list.", "path": "/prospects"}
    return {"roles": list(views), "strip": top, "hours": await hours_saved(ws, roles, at) if roles else None, "feed": feed,
            "weekly": await weekly_view(owner, at), "refresh_seconds": REFRESH_SECONDS, "as_of": at.isoformat()}


# ───────────────────────── the weekly growth line ─────────────────────────
def week_line(n: dict) -> str:
    """'Last week: 40 businesses found, 12 messages sent by you, 3 replies, 1 new deal worth ₹85,000.'"""
    deals = f"{plural(n['deals'], 'new deal')}" + (f" worth {rupees(n['value'])}" if n["deals"] and n["value"] else "")
    return (f"Last week: {plural(n['found'], 'business', 'businesses')} found, {plural(n['sent'], 'message')} sent by you, "
            f"{plural(n['replies'], 'reply', 'replies')}, {deals}.")


def worth_saying(n: dict) -> bool:
    return any(n[k] > 0 for k in ("found", "sent", "replies", "deals"))


async def weekly_view(owner: dict, at: datetime | None = None) -> dict:
    """Last week's line for the page (counted now, from the records), whether its switch is on, and what was sent."""
    ws = owner["_id"]
    start, end = last_week(at or now())
    available = plans.has(owner, "growth_weekly")
    out = {"available": available, "on": False, "line": None, "from": _ist_day(start).isoformat(),
           "to": (_ist_day(end) - timedelta(days=1)).isoformat(), "sent": None}
    if not available:
        return out
    out["on"] = await jobs.is_on_for(owner, WEEKLY_KEY)
    n = await numbers_for(ws, start, end)
    out["numbers"] = n
    out["line"] = week_line(n) if worth_saying(n) else None
    sent = await db().growth_weekly_sent.find_one({"_id": f"{ws}:{_ist_day(start).isoformat()}"})
    if sent:
        out["sent"] = {"at": _iso(sent.get("at")), "whatsapp": bool(sent.get("whatsapp"))}
    return out


async def dashboard_line(owner: dict) -> str | None:
    """The same line on the Dashboard (inside the Quick answers tile) while it's on and has something to say."""
    if not plans.has(owner, "growth_weekly") or not await jobs.is_on_for(owner, WEEKLY_KEY):
        return None
    start, end = last_week(now())
    n = await numbers_for(owner["_id"], start, end)
    return week_line(n) if worth_saying(n) else None


async def weekly_runner(owner: dict, run: dict):
    """Monday 09:00 India time: last week's line to the owner — WhatsApp from the hub's own number (as the morning digest
    sends) and a notice in the hub. Nothing is sent when every number is zero."""
    ws = owner["_id"]
    start, end = last_week(run["at"])
    n = await numbers_for(ws, start, end)
    if not worth_saying(n):
        raise Skip("Nothing to report for last week, so no message was sent.")
    line = week_line(n)
    b = await db().businesses.find_one({"owner_id": ws}, {"name": 1}) or {}
    # the owner-alert template ({{1}} business, {{2}} the message) fits a full line; the digest's template is the fallback
    campaign = settings.aisensy_owner_alert_campaign or settings.aisensy_digest_campaign
    sent = await whatsapp_template(owner["phone"], campaign, owner.get("name", ""), [b.get("name") or "your business", line], WEEKLY_KEY)
    await notify(ws, line)
    week = _ist_day(start).isoformat()
    await db().growth_weekly_sent.update_one({"_id": f"{ws}:{week}"}, {"$set": {
        "ws": ws, "line": line, "numbers": n, "from": week, "to": (_ist_day(end) - timedelta(days=1)).isoformat(),
        "whatsapp": bool(sent.get("sent")), "at": now()}}, upsert=True)
    return line + ("" if sent.get("sent") else " (Shown in the hub; WhatsApp isn't set up.)")


async def _weekly_ready(owners: list[dict]) -> set:
    """Owners who have used Find new customers at all — the others never get a "nothing to report" line every Monday."""
    ids = [o["_id"] for o in owners]
    out = set(await db().prospect_searches.distinct("ws", {"ws": {"$in": ids}}))
    out |= set(await db().prospects.distinct("ws", {"ws": {"$in": ids}}))
    out |= set(await db().leads.distinct("owner_id", {"owner_id": {"$in": ids}, "source": "prospecting"}))
    return out


catalog.register(WEEKLY_KEY, {
    "name": "Weekly growth line", "feature": "growth_weekly", "trigger": "schedule", "group": "alerts",
    "what": "Every Monday, one WhatsApp line to you about last week: businesses found, messages you sent, replies and new deals. "
            "Only when there is something to report.",
    "when": "Every Monday at 9am",
    "schedule": {"every": "week", "weekday": 0, "at": "09:00", "grace_hours": 8}, "ready": _weekly_ready}, weekly_runner)


# ───────────────────────── quick answers ─────────────────────────
STUCK_DAYS = 10        # an open deal with nothing recorded for this many days is "stuck"
SOURCE_DAYS = 90       # won deals by source over this many days
REPLY_DAYS = 30        # first replies to inquiries over this many days
REPLY_MIN = 3          # fewer replies than this is "not enough data" for a median
DUE_DAYS = 7           # "this week" = today and the next 7 days
TOP = 5
ROWS_MAX = 10
QUESTIONS = [
    ("stuck", "Which deals are stuck?"),
    ("owes", "Who owes me the most?"),
    ("sources", "Which source brings the most deals?"),
    ("quiet", "Who has gone quiet?"),
    ("reply", "How fast do we reply to new inquiries?"),
    ("due", "What is due this week?"),
]
QUESTION = dict(QUESTIONS)
# category names only (never a company's name); anything else uses the CRM's own label
SOURCE_NAMES = {"marketplace": "Online marketplace", "social": "Social media", "prospecting": "Find new customers",
                "guide": "Lead magnet", "lead_magnet": "Lead magnet"}
NOT_INQUIRIES = ("manual", "import", "prospecting")   # leads the owner added himself have no "first reply" to time


def source_label(source: str | None) -> str:
    s = source or "website"
    return SOURCE_NAMES.get(s) or crm.SOURCE_LABEL.get(s) or s.replace("_", " ").capitalize()


def _answer(key: str, rule: str, line: str, rows: list[dict], numbers: dict, enough: bool = True, empty: bool = False,
            nxt: tuple[str, str | None] | None = None) -> dict:
    return {"key": key, "question": QUESTION[key], "rule": rule, "line": line, "rows": rows, "numbers": numbers, "enough": enough,
            "empty": empty, "next": {"text": nxt[0], "path": nxt[1]} if nxt else None, "tokens": 0, "as_of": now().isoformat()}


def _path_if(owner: dict, feature: str, path: str) -> str | None:
    return path if plans.has(owner, feature) else None


async def stuck_deals(owner: dict, at: datetime) -> dict:
    ws = owner["_id"]
    cutoff = at - timedelta(days=STUCK_DAYS)
    fields = {"name": 1, "value": 1, "stage": 1, "pstage": 1, "created_at": 1, "stage_at": 1, "updated_at": 1, "last_followup_at": 1,
              "last_call_at": 1}
    leads = [x async for x in db().leads.find({"owner_id": ws, "status": {"$in": list(crm.OPEN)}}, fields).limit(5000)]
    rule = f"Stuck = an open deal with no note, call, stage move or change for {STUCK_DAYS} days or more."
    if not leads:
        return _answer("stuck", rule, "You have no open deals.", [], {"open": 0, "stuck": 0, "value": 0}, empty=True,
                       nxt=("Add a deal on your pipeline.", _path_if(owner, "pipeline", "/pipeline")))
    last_note: dict = {}
    async for g in db().crm_activities.aggregate([{"$match": {"ws": ws, "lead_id": {"$in": [x["_id"] for x in leads]}}},
                                                   {"$group": {"_id": "$lead_id", "at": {"$max": "$at"}}}]):
        last_note[g["_id"]] = g["at"]
    stages = await crm.stages_of(ws)
    names = {s["id"]: s["name"] for s in stages}
    stuck = []
    for x in leads:
        times = [_aware(t) for t in (x.get("created_at"), x.get("stage_at"), x.get("updated_at"), x.get("last_followup_at"),
                                      x.get("last_call_at"), last_note.get(x["_id"])) if isinstance(t, datetime)]
        last = max(times) if times else None
        if last is None or last <= cutoff:
            idle = (at - last).days if last else None
            stuck.append((x, idle))
    stuck.sort(key=lambda p: (-float(p[0].get("value") or 0), -(p[1] or 0)))
    value = round(sum(float(x.get("value") or 0) for x, _ in stuck), 2)
    rows = [{"label": x.get("name") or "Deal", "value": rupees(x["value"]) if x.get("value") else "No value",
             "sub": f"{names.get(crm.pstage_of(x, stages), '')} · nothing for {plural(idle, 'day')}" if idle is not None else names.get(crm.pstage_of(x, stages), ""),
             "link": f"/pipeline?deal={quote(x['_id'], safe='')}", "id": x["_id"], "days": idle, "amount": float(x.get("value") or 0)}
            for x, idle in stuck[:ROWS_MAX]]
    numbers = {"open": len(leads), "stuck": len(stuck), "value": value}
    if not stuck:
        return _answer("stuck", rule, f"None. All {plural(len(leads), 'open deal')} had something happen in the last {STUCK_DAYS} days.",
                       [], numbers, empty=True)
    line = (f"{plural(len(stuck), 'open deal')} had nothing happen for {STUCK_DAYS} days or more"
            + (f" — worth {rupees(value)} (value you entered)" if value else "") + ".")
    return _answer("stuck", rule, line, rows, numbers)


async def who_owes(owner: dict, at: datetime) -> dict:
    from .salesbook import customer_key
    ws = owner["_id"]
    today = at.astimezone(IST).date().isoformat()
    groups: dict[str, dict] = {}
    async for d in db().dues.find({"ws": ws, "status": "due"}, {"customer": 1, "phone": 1, "amount": 1, "due_date": 1}).limit(20000):
        k = customer_key(d.get("customer", ""), d.get("phone", ""))
        g = groups.setdefault(k, {"name": d.get("customer") or "Customer", "paise": 0, "n": 0, "oldest": None, "late": 0})
        g["paise"] += int(d.get("amount") or 0)
        g["n"] += 1
        day = d.get("due_date") or ""
        if day and (g["oldest"] is None or day < g["oldest"]):
            g["oldest"] = day
        if day and day < today:
            g["late"] += int(d.get("amount") or 0)
    page_name = plans.label_of("money_owed")
    rule = f"Money owed = every amount in {page_name} not yet marked paid, added up per customer."
    total = sum(g["paise"] for g in groups.values())
    late = sum(g["late"] for g in groups.values())
    if not groups:
        return _answer("owes", rule, f"Nobody owes you money in {page_name}.", [], {"customers": 0, "total": 0, "overdue": 0, "top": []},
                       empty=True, nxt=("Add the money customers owe you, with a due date.", _path_if(owner, "money_owed", MONEY_PAGE)))
    top = sorted(groups.values(), key=lambda g: (-g["paise"], g["name"].lower()))[:TOP]
    t = date.fromisoformat(today)
    rows = []
    for g in top:
        if g["oldest"] and g["oldest"] < today:
            when = f"oldest due {crm.day_text(g['oldest'])} ({plural((t - date.fromisoformat(g['oldest'])).days, 'day')} late)"
        elif g["oldest"]:
            when = f"next due {crm.day_text(g['oldest'])}"
        else:
            when = "no due date"
        rows.append({"label": g["name"], "value": inr(g["paise"]), "sub": f"{plural(g['n'], 'payment')} · {when}",
                     "link": _path_if(owner, "money_owed", MONEY_PAGE), "amount": round(g["paise"] / 100, 2)})
    line = f"{inr(total)} is owed to you by {plural(len(groups), 'customer')}." + (f" {inr(late)} of it is past its due date." if late else "")
    return _answer("owes", rule, line, rows, {"customers": len(groups), "total": round(total / 100, 2), "overdue": round(late / 100, 2),
                                              "top": [r["amount"] for r in rows]})


async def best_sources(owner: dict, at: datetime) -> dict:
    ws = owner["_id"]
    since = at - timedelta(days=SOURCE_DAYS)
    by: dict[str, dict] = {}
    q = {"owner_id": ws, "$or": [{"status": "won"}, {"created_at": {"$gte": since}}]}
    async for x in db().leads.find(q, {"source": 1, "status": 1, "value": 1, "won_at": 1, "updated_at": 1, "created_at": 1}).limit(20000):
        label = source_label(x.get("source"))
        s = by.setdefault(label, {"source": label, "won": 0, "value": 0.0, "came_in": 0})
        created = _aware(x.get("created_at"))
        if isinstance(created, datetime) and created >= since:
            s["came_in"] += 1
        won_at = _aware(x.get("won_at") or x.get("updated_at") or x.get("created_at"))
        if x.get("status") == "won" and isinstance(won_at, datetime) and won_at >= since:
            s["won"] += 1
            s["value"] += float(x.get("value") or 0)
    rows_all = sorted((s for s in by.values() if s["won"] or s["came_in"]), key=lambda s: (-s["won"], -s["value"], -s["came_in"], s["source"]))
    won = sum(s["won"] for s in rows_all)
    value = round(sum(s["value"] for s in rows_all), 2)
    came = sum(s["came_in"] for s in rows_all)
    rule = f"Deals marked won in the last {SOURCE_DAYS} days, by where each deal came from. Value is what you entered on each deal."
    rows = [{"label": s["source"], "value": f"{s['won']} won", "sub": (f"{rupees(s['value'])} · " if s["value"] else "")
             + f"{s['came_in']} came in", "link": _path_if(owner, "pipeline", "/pipeline"), "won": s["won"], "amount": round(s["value"], 2),
             "came_in": s["came_in"]} for s in rows_all[:ROWS_MAX]]
    numbers = {"won": won, "value": value, "came_in": came,
               "by_source": {s["source"]: {"won": s["won"], "value": round(s["value"], 2), "came_in": s["came_in"]} for s in rows_all}}
    if not won:
        line = f"No deal was marked won in the last {SOURCE_DAYS} days."
        if came:
            busiest = max(rows_all, key=lambda s: (s["came_in"], s["source"]))
            line += f" {plural(came, 'deal')} came in — most from {busiest['source']} ({busiest['came_in']})."
        return _answer("sources", rule, line, rows, numbers, empty=not came,
                       nxt=("Mark deals won on your pipeline when they buy.", _path_if(owner, "pipeline", "/pipeline")))
    best = rows_all[0]
    line = (f"{best['source']} brought the most won deals in the last {SOURCE_DAYS} days: {plural(best['won'], 'deal')}"
            + (f" worth {rupees(best['value'])}" if best["value"] else "") + ".")
    return _answer("sources", rule, line, rows, numbers)


async def gone_quiet(owner: dict, at: datetime) -> dict:
    """The Customer risk page's own rule (routers/moneyinsights.customer_risk), read from the sales book."""
    from .context import Ctx
    from .routers import moneyinsights
    r = await moneyinsights.customer_risk(Ctx(owner, owner, "owner"))
    rule = ("A regular customer (3 or more purchases) who hasn't bought for more than twice their usual gap between orders, and for "
            "over 45 days. Biggest buyers first. The yearly value is an estimate from their past buying.")
    if not r.get("enough_data"):
        return _answer("quiet", rule, "Not enough sales history yet to tell. It needs at least 10 sales from 3 customers — from your "
                       "invoices, a sales sheet or your Customers list.", [], {"quiet": 0, "sales_counted": r.get("sales_counted", 0)},
                       enough=False)
    quiet = r.get("quiet") or []
    rows = [{"label": q["name"], "value": f"about {rupees(q['yearly_value'])} a year", "sub": f"Last bought {crm.day_text(q['last_purchase'])}"
             f" · usually every {plural(q['usual_gap_days'], 'day')}", "link": None, "wa": q.get("whatsapp_link"), "amount": q["yearly_value"],
             "days": q["days_since"]} for q in quiet[:TOP]]
    numbers = {"quiet": r.get("quiet_count", 0), "at_risk_yearly": r.get("at_risk_yearly", 0), "names": [q["name"] for q in quiet[:TOP]]}
    return _answer("quiet", rule, r.get("quiet_line") or "None of your regular customers has gone quiet.", rows, numbers, empty=not quiet)


def _duration(minutes: float) -> str:
    m = int(round(minutes))
    if m < 60:
        return plural(m, "minute")
    if m < 48 * 60:
        h, rest = divmod(m, 60)
        return f"{plural(h, 'hour')}" + (f" {rest} min" if rest else "")
    return plural(round(m / 1440), "day")


async def reply_speed(owner: dict, at: datetime) -> dict:
    ws = owner["_id"]
    since = at - timedelta(days=REPLY_DAYS)
    q = {"owner_id": ws, "created_at": {"$gte": since, "$lte": at}, "created_by": {"$exists": False}, "source": {"$nin": list(NOT_INQUIRIES)}}
    minutes, waiting_n, total = [], 0, 0
    async for x in db().leads.find(q, {"created_at": 1, "first_action_at": 1}).limit(20000):
        total += 1
        first, created = _aware(x.get("first_action_at")), _aware(x.get("created_at"))
        if isinstance(first, datetime) and isinstance(created, datetime):
            minutes.append(max((first - created).total_seconds(), 0) / 60)
        else:
            waiting_n += 1
    rule = (f"From when an inquiry came in to the first reply, call or status change recorded in the hub, over the last {REPLY_DAYS} "
            "days. Deals you added yourself are left out.")
    numbers = {"inquiries": total, "replied": len(minutes), "waiting": waiting_n,
               "median_minutes": round(statistics.median(minutes), 1) if len(minutes) >= REPLY_MIN else None}
    path = _path_if(owner, "inquiry_desk", "/inquiries")
    if not total:
        return _answer("reply", rule, f"No new inquiries in the last {REPLY_DAYS} days.", [], numbers, enough=False, empty=True)
    if len(minutes) < REPLY_MIN:
        return _answer("reply", rule, f"Not enough data: {len(minutes)} of the {plural(total, 'inquiry', 'inquiries')} in the last "
                       f"{REPLY_DAYS} days {'has' if len(minutes) == 1 else 'have'} a reply recorded in the hub.",
                       [{"label": "No reply recorded yet", "value": str(waiting_n), "link": path, "n": waiting_n}] if waiting_n else [],
                       numbers, enough=False)
    hour = sum(1 for m in minutes if m <= 60)
    day_ = sum(1 for m in minutes if 60 < m <= 1440)
    rows = [{"label": "Within 1 hour", "value": str(hour), "n": hour}, {"label": "Within a day", "value": str(day_), "n": day_},
            {"label": "Took longer than a day", "value": str(len(minutes) - hour - day_), "n": len(minutes) - hour - day_},
            {"label": "No reply recorded yet", "value": str(waiting_n), "link": path, "n": waiting_n}]
    numbers.update(within_hour=hour, within_day=day_)
    line = f"Half of your inquiries get a first reply within {_duration(numbers['median_minutes'])} (the median, over {plural(len(minutes), 'reply', 'replies')})."
    return _answer("reply", rule, line, rows, numbers)


async def due_this_week(owner: dict, at: datetime) -> dict:
    ws = owner["_id"]
    t = at.astimezone(IST).date()
    today, until = t.isoformat(), (t + timedelta(days=DUE_DAYS)).isoformat()
    f = await crm.due_followups(ws, days=DUE_DAYS)
    coming = f["today"] + f["upcoming"]
    due_rows, due_paise, late_paise, late_n = [], 0, 0, 0
    async for d in db().dues.find({"ws": ws, "status": "due", "due_date": {"$ne": None, "$lte": until}},
                                  {"customer": 1, "amount": 1, "due_date": 1}).sort("due_date", 1).limit(5000):
        amt = int(d.get("amount") or 0)
        if not d.get("due_date"):
            continue
        if d["due_date"] < today:
            late_paise += amt
            late_n += 1
            continue
        due_paise += amt
        due_rows.append({"group": "Money due", "label": d.get("customer") or "Customer", "value": inr(amt),
                         "sub": "due today" if d["due_date"] == today else f"due {crm.day_text(d['due_date'])}",
                         "link": _path_if(owner, "money_owed", MONEY_PAGE), "amount": round(amt / 100, 2)})

    def fu_row(i: dict) -> dict:
        link = f"/pipeline?deal={quote(i['id'], safe='')}" if i["kind"] == "deal" else f"/contacts/{quote(i['id'], safe='')}"
        return {"group": "Follow-ups", "label": i.get("name") or ("Deal" if i["kind"] == "deal" else "Contact"),
                "value": "today" if i["on"] == today else crm.day_text(i["on"]), "sub": i.get("note") or ("deal" if i["kind"] == "deal" else "contact"),
                "link": link}
    rows = [fu_row(i) for i in coming[:ROWS_MAX]] + due_rows[:ROWS_MAX]
    numbers = {"followups": len(coming), "followups_late": len(f["overdue"]), "money_due": round(due_paise / 100, 2), "money_due_n": len(due_rows),
               "money_late": round(late_paise / 100, 2), "money_late_n": late_n}
    rule = f"Follow-up dates on your contacts and open deals, and money owed with a due date, from today to {crm.day_text(until)}."
    late = ([plural(len(f["overdue"]), "follow-up")] if f["overdue"] else []) + ([f"{inr(late_paise)} owed"] if late_paise else [])
    late_text = f" Already late: {' and '.join(late)}." if late else ""
    if not coming and not due_rows:
        return _answer("due", rule, f"Nothing is due in the next {DUE_DAYS} days.{late_text}", [], numbers, empty=not late)
    line = (f"In the next {DUE_DAYS} days: {plural(len(coming), 'follow-up')} and {inr(due_paise)} due"
            + (f" from {plural(len(due_rows), 'payment')}" if due_rows else "") + f".{late_text}")
    return _answer("due", rule, line, rows, numbers)


ANSWERS = {"stuck": stuck_deals, "owes": who_owes, "sources": best_sources, "quiet": gone_quiet, "reply": reply_speed,
           "due": due_this_week}


async def answer(owner: dict, key: str, at: datetime | None = None) -> dict:
    return await ANSWERS[key](owner, at or now())
