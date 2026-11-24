"""
v2.4 growth features: what they share.

1. Limits. Each growth feature has a monthly (or daily) limit per plan (plans.GROWTH_LIMITS, admin-editable). `take()` counts one
   use atomically and refuses with a plain message when the limit is reached; `give_back()` returns it when the work failed;
   `left()` is what a page shows ("7 of 10 searches left this month").

2. The AI roles. Every growth feature is one AI role on the AI team page. A role:
   - records every finished action in the activity log with `log_action(ws, role, text, ref)` — `ref` is "<kind>:<id>" so the
     AI team page can link to the record (prospect:<id>, lead:<id>, update:<id>, call:<id>, approval:<id>);
   - marks itself as working while a job runs, with `async with working(ws, role): ...` — the AI team page shows "Working" only
     while such a block is open (each mark expires after WORKING_MAX_SECONDS in case a process dies);
   - can be switched off by the owner (agents/jobs settings, key = the role). `role_on()` reads the switch.

The AI team page counts everything from the saved records (prospects, approvals, leads.brief, deal_updates, gym_sessions,
dues), never from counters that can drift. The activity log is only for the feed of recent actions.
"""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import HTTPException

from . import plans
from .agents import jobs
from .db import IST, db, now

# The roles, in the order the AI team page shows them. key → (title, job, feature that must be on for the role to show)
ROLES = {
    "prospector":      ("Prospector", "Finds businesses near you that fit", "prospects"),
    "outreach_writer": ("Outreach writer", "Writes first messages and follow-ups for you to send", "prospects"),
    "researcher":      ("Researcher", "Writes a short brief on every new inquiry", "lead_brief"),
    "pipeline_keeper": ("Pipeline keeper", "Files conversations into your deals", "deal_autofill"),
    "call_coach":      ("Call coach", "Scores calls and gives 3 fixes", "call_coach"),
    "followup":        ("Follow-up writer", "Day 1, 3 and 7 follow-ups after every inquiry", "follow_up_agent"),
    "payment_reminder": ("Collections", "Payment reminders for money that's due", "reminder_agent"),
}
WORKING_MAX_SECONDS = 300

# limit key → (what the owner sees, period)
LIMITS = {
    "prospect_searches": ("business searches", "month"),
    "prospects_kept": ("businesses kept", "month"),
    "outreach_per_day": ("first messages", "day"),
    "briefs": ("automatic lead briefs", "month"),
    "calls_scored": ("calls scored", "month"),
}


def _period(period: str, at: datetime | None = None) -> str:
    local = (at or now()).astimezone(IST)
    return local.strftime("%Y-%m-%d") if period == "day" else local.strftime("%Y-%m")


def _usage_id(ws: str, key: str, at: datetime | None = None) -> str:
    return f"{ws}:{key}:{_period(LIMITS[key][1], at)}"


async def used(ws: str, key: str, at: datetime | None = None) -> int:
    doc = await db().growth_usage.find_one({"_id": _usage_id(ws, key, at)})
    return int((doc or {}).get("n", 0))


async def left(owner: dict, key: str) -> dict:
    """{"limit", "used", "left", "what", "period"} for a page to show."""
    limit = plans.growth_limit(owner, key)
    n = await used(owner["_id"], key)
    what, period = LIMITS[key]
    return {"limit": limit, "used": n, "left": max(0, limit - n), "what": what, "period": period}


async def take(owner: dict, key: str, n: int = 1) -> None:
    """Counts n uses, or refuses (429) when that would go over the plan's limit. Atomic: two requests can't both take the last one."""
    limit = plans.growth_limit(owner, key)
    what, period = LIMITS[key]
    when = "today" if period == "day" else "this month"
    if n <= 0:
        return
    if limit <= 0:
        raise HTTPException(403, f"Your plan doesn't include {what}.")
    uid = _usage_id(owner["_id"], key)
    await db().growth_usage.update_one({"_id": uid}, {"$setOnInsert": {"n": 0, "ws": owner["_id"], "key": key, "at": now()}}, upsert=True)
    got = await db().growth_usage.find_one_and_update({"_id": uid, "n": {"$lte": limit - n}}, {"$inc": {"n": n}})
    if not got:
        raise HTTPException(429, f"You've used this {period}'s {limit} {what}. More open {'tomorrow' if period == 'day' else 'on the 1st'}."
                            if limit else f"No {what} left {when}.")


async def give_back(owner: dict, key: str, n: int = 1) -> None:
    """Returns uses taken for work that then failed (never below zero)."""
    if n > 0:
        await db().growth_usage.update_one({"_id": _usage_id(owner["_id"], key), "n": {"$gte": n}}, {"$inc": {"n": -n}})


# ───────────────────────── the AI roles ─────────────────────────
async def role_on(ws: str, role: str) -> bool:
    return await jobs.is_on(ws, role)


async def log_action(ws: str, role: str, text: str, ref: str | None = None, status: str = "done") -> None:
    """One line in the AI team feed: a finished action with a link to its record."""
    await jobs.record(ws, role, status, text, ref=ref)


@asynccontextmanager
async def working(ws: str, role: str):
    """Marks the role as working for this owner while the block runs (the AI team page's "Working" state)."""
    key = f"{ws}:{role}"
    started = now()
    await db().growth_working.update_one({"_id": key}, {"$set": {"ws": ws, "role": role, "since": started,
                                                                   "until": started + timedelta(seconds=WORKING_MAX_SECONDS)},
                                                          "$inc": {"n": 1}}, upsert=True)
    try:
        yield
    finally:
        doc = await db().growth_working.find_one_and_update({"_id": key}, {"$inc": {"n": -1}}, return_document=True)
        if doc is not None and doc.get("n", 0) <= 0:
            await db().growth_working.delete_one({"_id": key, "n": {"$lte": 0}})


async def working_now(ws: str) -> set[str]:
    """The roles working for this owner right now (marks past their expiry don't count)."""
    out = set()
    async for d in db().growth_working.find({"ws": ws, "until": {"$gt": now()}, "n": {"$gt": 0}}):
        out.add(d["role"])
    return out


async def ensure_indexes() -> None:
    await db().growth_usage.create_index([("ws", 1), ("key", 1)])
    await db().growth_working.create_index([("ws", 1)])
