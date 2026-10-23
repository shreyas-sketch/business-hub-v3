"""
The agents' bookkeeping: per-owner settings, the activity log, run claims and scheduled jobs.

Collections
- agent_settings {_id: "<ws>:<key>", ws, key, on, opted_in (the owner switched it on themselves), autonomy "ask"|"act",
                  last_run_at, last_status, last_note}
- agent_jobs     {_id, ws, agent, run_at, payload, status scheduled|done|skipped|failed|cancelled, note, attempts, created_at, finished_at}
- agent_log      {_id, ws, agent, text, status, at, ref}
- job_claims     {_id, at} — a run claims itself by inserting a deterministic _id; a duplicate means another worker has it.
"""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from .. import plans
from ..db import IST, db, now
from ..services import new_id, notify
from .catalog import AGENTS, Skip, Spec

log = logging.getLogger("agents")
DEFAULTS = {"on": True, "autonomy": "ask"}
RETRY_MINUTES = 30
MAX_ATTEMPTS = 3


# ───────────────────────── claims ─────────────────────────
async def claim(key: str, at: datetime | None = None) -> bool:
    """True if this worker now owns `key`; False if someone already claimed it."""
    try:
        await db().job_claims.insert_one({"_id": key, "at": at or now()})
        return True
    except DuplicateKeyError:
        return False


async def release(key: str) -> None:
    await db().job_claims.delete_one({"_id": key})


# ───────────────────────── settings ─────────────────────────
async def get_settings(ws: str, key: str) -> dict:
    doc = await db().agent_settings.find_one({"_id": f"{ws}:{key}"}) or {}
    return {**DEFAULTS, **{k: v for k, v in doc.items() if v is not None}}


async def all_settings(ws: str) -> dict[str, dict]:
    out = {k: dict(DEFAULTS) for k in AGENTS}
    async for doc in db().agent_settings.find({"ws": ws}):
        out[doc.get("key")] = {**DEFAULTS, **{k: v for k, v in doc.items() if v is not None}}
    return out


async def is_on(ws: str, key: str) -> bool:
    return bool((await get_settings(ws, key)).get("on", True))


# Agents that are on by default from their usual plan up, but wait for the owner's own switch on a lower plan that only
# has them because the admin moved them there (v2.2: follow-ups in the ₹1,999 Membership). Each run is an AI run, so a
# Membership owner chooses it. agent key -> feature (its tier in the code is the "usual plan").
# The WhatsApp number's two agents too: moved below Growth Mentorship, nothing replies to a customer on the owner's
# number until the owner switches it on.
OPT_IN = {"followup": "follow_up_agent", "instant_reply": "whatsapp_ai", "wa_sales": "whatsapp_ai"}


def needs_opt_in(owner: dict, key: str) -> bool:
    f = OPT_IN.get(key)
    return bool(f) and plans.rank(plans.effective_plan(owner)) < plans.rank(plans.DEFAULT_FEATURES[f]["tier"])


def on_for(owner: dict, key: str, st: dict) -> bool:
    """Whether this agent runs for this owner: switched on, and — where the owner must opt in — switched on by the owner."""
    if not st.get("on", True):
        return False
    return bool(st.get("opted_in")) if needs_opt_in(owner, key) else True


async def is_on_for(owner: dict, key: str) -> bool:
    return on_for(owner, key, await get_settings(owner["_id"], key))


async def save_settings(ws: str, key: str, patch: dict) -> None:
    fields = {"ws": ws, "key": key, **patch, "updated_at": now()}
    insert_only = {k: v for k, v in DEFAULTS.items() if k not in patch}
    try:
        await db().agent_settings.update_one({"_id": f"{ws}:{key}"}, {"$set": fields, **({"$setOnInsert": insert_only} if insert_only else {})},
                                             upsert=True)
    except DuplicateKeyError:  # created by a parallel request a moment ago
        await db().agent_settings.update_one({"_id": f"{ws}:{key}"}, {"$set": fields})


def may_act(owner: dict, agent_settings: dict) -> bool:
    """A customer-facing agent sends on its own only with autonomy "act" AND the LegacyWorkforce switch."""
    return agent_settings.get("autonomy") == "act" and plans.has(owner, "agents_act")


# ───────────────────────── activity ─────────────────────────
async def record(ws: str, agent: str, status: str, text: str, at: datetime | None = None, ref: str | None = None) -> None:
    """Every run leaves one line in the activity log and updates the agent's last run."""
    at = at or now()
    await db().agent_log.insert_one({"_id": new_id(), "ws": ws, "agent": agent, "text": text[:400], "status": status, "at": at, "ref": ref})
    await save_settings(ws, agent, {"last_run_at": at, "last_status": status, "last_note": text[:300]})


async def notify_paused(owner: dict, at: datetime) -> None:
    """Out of AI runs: tell the owner, at most once a day whichever agents were paused."""
    if await claim(f"paused:{owner['_id']}:{at.astimezone(IST).strftime('%Y-%m-%d')}", at):
        await notify(owner["_id"], "Your agents are paused: this month's AI runs are used up. Add runs or upgrade on Plans & billing, "
                                   "or invite an owner for bonus runs.")


async def execute(owner: dict, spec: Spec, run: dict) -> tuple[str, str]:
    """Runs one agent once for one owner, and records what happened. Returns (status, note).
    status: done | skipped | paused (no AI runs left) | failed | retry (the AI was busy; a job may try again)."""
    at = run["at"]
    ref = None
    try:
        out = await spec.runner(owner, run)
        note, ref = out if isinstance(out, tuple) else (str(out or "Done"), None)
        status = "done"
    except Skip as s:
        status, note = "skipped", str(s) or "Nothing to do"
    except HTTPException as e:
        if e.status_code == 402:
            status, note = "paused", "paused: no AI runs left"
            await notify_paused(owner, at)
        elif e.status_code == 502:
            status, note = "retry", "The AI was busy, so this run was not used."
        else:
            status, note = "failed", e.detail if isinstance(e.detail, str) else "Could not finish"
    except Exception:
        log.exception("agent %s failed for %s", spec.key, owner.get("_id"))
        status, note = "failed", "Something went wrong. We'll look into it."
    await record(owner["_id"], spec.key, "failed" if status == "retry" else status, note, at, ref)
    return status, note


# ───────────────────────── jobs ─────────────────────────
def working_hours(t: datetime) -> datetime:
    """Moves a time into 10:00–19:00 India time: earlier → 10:00 that day; 19:00 or later → 10:00 the next day."""
    local = t.astimezone(IST)
    if local.hour < 10:
        local = local.replace(hour=10, minute=0, second=0, microsecond=0)
    elif local.hour >= 19:
        local = (local + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    return local.astimezone(timezone.utc)


async def schedule(ws: str, agent: str, run_at: datetime, payload: dict, job_id: str | None = None) -> dict | None:
    """Adds a job. With a fixed `job_id`, a cancelled job of that id is scheduled again; a live one is left alone."""
    job = {"_id": job_id or new_id(), "ws": ws, "agent": agent, "run_at": run_at, "payload": payload, "status": "scheduled",
           "note": "", "attempts": 0, "created_at": now(), "finished_at": None}
    try:
        await db().agent_jobs.insert_one(job)
        return job
    except DuplicateKeyError:
        r = await db().agent_jobs.update_one({"_id": job["_id"], "status": "cancelled"},
                                             {"$set": {"status": "scheduled", "run_at": run_at, "payload": payload, "note": "", "finished_at": None}})
        return job if r.modified_count else None


async def cancel(query: dict, note: str) -> int:
    r = await db().agent_jobs.update_many({**query, "status": "scheduled"}, {"$set": {"status": "cancelled", "note": note, "finished_at": now()}})
    return r.modified_count


async def finish(job: dict, status: str, note: str, at: datetime) -> None:
    await db().agent_jobs.update_one({"_id": job["_id"]}, {"$set": {"status": status, "note": note[:300], "finished_at": at}})


async def retry_later(job: dict, at: datetime, note: str) -> None:
    attempts = job.get("attempts", 0) + 1
    if attempts >= MAX_ATTEMPTS:
        await finish(job, "failed", note, at)
        return
    await db().agent_jobs.update_one({"_id": job["_id"]}, {"$set": {"attempts": attempts, "run_at": at + timedelta(minutes=RETRY_MINUTES), "note": note}})


def job_claim_key(job: dict) -> str:
    n = job.get("attempts", 0)
    return job["_id"] if not n else f"{job['_id']}:{n}"
