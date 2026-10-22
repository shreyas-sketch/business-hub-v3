"""
The agents' clock. `loop()` (started by main.py outside tests) calls `tick()` every minute. Each tick:
1. runs the agent jobs that are due (follow-ups, review requests), each claimed once in `job_claims` by its id;
2. runs the scheduled agents whose slot is due in India time (weekly posts, digest, monthly review, payment reminders),
   once per owner per slot, claimed as "<agent>:<ws>:<slot>".
Claims make it safe to run several app workers: whoever inserts the claim first does the work.
"""
import asyncio
import logging
from datetime import datetime, timedelta

from .. import plans
from ..db import db, now
from . import runners  # noqa: F401  (attaches the built-in runners to the catalog)
from .catalog import AGENTS, Spec, due_slot
from .jobs import claim, execute, finish, is_on_for, job_claim_key, retry_later

log = logging.getLogger("agents")
PAID = [t for t in plans.TIERS if t != "free"]
CONCURRENCY = 8
JOB_BATCH = 300
STUCK_AFTER = timedelta(minutes=30)
FINAL = {"done": "done", "skipped": "skipped", "paused": "skipped", "failed": "failed"}


def _chunks(items: list, n: int = 400):
    for i in range(0, len(items), n):
        yield items[i:i + n]


async def _bounded(coros) -> list:
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(c):
        async with sem:
            return await c
    return await asyncio.gather(*(one(c) for c in coros))


# ───────────────────────── 1. due jobs ─────────────────────────
async def run_job(job: dict, at: datetime) -> bool:
    if not await claim(job_claim_key(job), at):
        # Another worker has it. If that worker died mid-run long ago, close the job so it can't clog the queue.
        held = await db().job_claims.find_one({"_id": job_claim_key(job)})
        if held and isinstance(held.get("at"), datetime) and at - held["at"] > STUCK_AFTER:
            await finish(job, "failed", "This run did not finish.", at)
        return False
    spec = AGENTS.get(job.get("agent"))
    owner = await db().users.find_one({"_id": job["ws"]})
    reason = ("This agent is no longer available." if not spec or not spec.runner
              else "The account is paused." if not owner or owner.get("disabled")
              else f"{plans.label_of(spec.feature)} isn't part of the current plan." if not plans.has(owner, spec.feature)
              else None)
    if reason is None and not await is_on_for(owner, spec.key):
        reason = "The agent was switched off."
    if reason:
        await finish(job, "skipped", reason, at)
        return True
    status, note = await execute(owner, spec, {"at": at, "slot": None, "job": job, "spec": spec})
    if status == "retry":
        await retry_later(job, at, note)
    else:
        await finish(job, FINAL.get(status, "failed"), note, at)
    return True


async def run_due_jobs(at: datetime) -> int:
    due = [j async for j in db().agent_jobs.find({"status": "scheduled", "run_at": {"$lte": at}}).sort("run_at", 1).limit(JOB_BATCH)]
    results = await _bounded([_safe(run_job(j, at), f"job {j['_id']}") for j in due])
    return sum(1 for r in results if r)


# ───────────────────────── 2. scheduled agents ─────────────────────────
async def candidate_owners() -> list[dict]:
    """Owners (not team members) who might be on a paid plan; plans.has decides per agent. If the admin has moved a
    scheduled automation into the free plan, every owner is a candidate."""
    if any(plans.tier_of(s.feature) == "free" for s in AGENTS.values() if s.trigger == "schedule"):
        return [u async for u in db().users.find({"disabled": {"$ne": True}}) if not u.get("team_of")]
    q = {"disabled": {"$ne": True}, "$or": [{"plan": {"$in": PAID}}, {"access": {"$exists": True}}, {"sub": {"$exists": True}},
                                             {"trial.until": {"$gt": now()}}, {"grants": {"$exists": True}}]}
    return [u async for u in db().users.find(q) if not u.get("team_of")]


async def run_slot(spec: Spec, owners: list[dict], slot: str, at: datetime) -> int:
    eligible = [o for o in owners if plans.has(o, spec.feature)]
    if not eligible:
        return 0
    keys = {o["_id"]: f"{spec.key}:{o['_id']}:{slot}" for o in eligible}
    taken, off = set(), set()
    for part in _chunks(list(keys.values())):
        taken |= {c["_id"] async for c in db().job_claims.find({"_id": {"$in": part}}, {"_id": 1})}
    for part in _chunks(list(keys)):
        off |= {s["ws"] async for s in db().agent_settings.find({"key": spec.key, "on": False, "ws": {"$in": part}}, {"ws": 1})}
    todo = [o for o in eligible if keys[o["_id"]] not in taken and o["_id"] not in off]
    if spec.ready and todo:   # one query for the whole slot, not one per owner
        ok = await _safe(spec.ready(todo), f"{spec.key} ready check") or set()
        todo = [o for o in todo if o["_id"] in ok]

    async def one(owner: dict) -> bool:
        if not await claim(keys[owner["_id"]], at):
            return False
        await execute(owner, spec, {"at": at, "slot": slot, "job": None, "spec": spec})
        return True
    results = await _bounded([_safe(one(o), f"{spec.key} for {o['_id']}") for o in todo])
    return sum(1 for r in results if r)


async def run_recurring(at: datetime) -> int:
    due = []
    for spec in list(AGENTS.values()):
        if spec.trigger == "schedule" and spec.schedule and spec.runner and not spec.info_only:
            slot = due_slot(spec.schedule, at)
            if slot:
                due.append((spec, slot[0]))
    if not due:
        return 0
    owners = await candidate_owners()
    total = 0
    for spec, slot in due:
        total += await _safe(run_slot(spec, owners, slot, at), f"{spec.key} slot {slot}") or 0
    return total


async def _safe(coro, what: str):
    try:
        return await coro
    except Exception:
        log.exception("agent scheduler: %s failed", what)
        return None


async def tick(at: datetime | None = None) -> dict:
    """One pass of the clock. `at` (UTC) lets tests drive time; it defaults to now."""
    at = at or now()
    from .. import campaigns, google_retention
    from . import office
    await plans.load()
    return {"jobs": await run_due_jobs(at), "scheduled": await run_recurring(at),
            "office_resumed": await _safe(office.resume_stale(at), "office resume") or 0,
            "campaign_messages": await _safe(campaigns.tick(at), "campaigns") or 0,
            "google_forgotten": await _safe(google_retention.sweep(at), "Google content sweep") or 0}


async def loop() -> None:
    await asyncio.sleep(5)  # let the app finish starting
    while True:
        try:
            await tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("agent scheduler tick failed")
        await asyncio.sleep(60)
