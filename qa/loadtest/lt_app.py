"""The hub as it runs in production, with three outside services swapped for stand-ins (everything else is the real code):
- the AI engine: each call asks the shared limiter (provider limits) and waits the answer time it hands out, then returns the
  hub's own built-in draft for that task — so the hub's queue, its "wait your turn" gate and its retries are what's tested;
- WhatsApp: the login code takes 0.3–1.5 s to send (AiSensy's API), and is shown on screen as in development;
- the attendees' websites: served by fakeweb.py on this machine."""
import asyncio
import random

import httpx

from app import messaging
from app.ai import provider
from app.ai.provider import Busy, Usage

LIMITER = "http://127.0.0.1:8841"
_client: dict = {}


def _http() -> httpx.AsyncClient:
    loop = asyncio.get_running_loop()
    if _client.get("loop") is not loop:
        _client.update(loop=loop, c=httpx.AsyncClient(timeout=10, limits=httpx.Limits(max_connections=400)))
    return _client["c"]


async def simulated_engine(provider_name, system, prompt, mock, max_tokens):
    out = int(max_tokens * 0.45)
    eng = provider.current_engine()   # each key of the engine pool is its own account at the limiter (LIMIT_SHARED=1: one account)
    r = await _http().post(f"{LIMITER}/call", json={"out": out, "account": f"{provider_name}:{eng.key if eng else ''}"})
    if r.status_code == 429:
        raise Busy("simulated 429", r.json().get("retry_after"))
    try:
        await asyncio.sleep(r.json()["latency"])
    finally:
        await _http().post(f"{LIMITER}/done", json={"account": f"{provider_name}:{eng.key if eng else ''}"})
    return mock(), Usage(int((len(system) + len(prompt)) / 3.5), out, eng.model if eng else "simulated")

provider._call_once = simulated_engine

_send_otp = messaging.send_otp


async def slow_otp(phone, code):
    await asyncio.sleep(random.uniform(0.3, 1.5))
    return await _send_otp(phone, code)

messaging.send_otp = slow_otp
from app.routers import auth  # noqa: E402
auth.send_otp = slow_otp

from app.main import app  # noqa: E402,F401
import logging  # noqa: E402
logging.getLogger("httpx").setLevel(logging.WARNING)   # the stand-ins' own calls would flood the log


# ── test harness only (this file is never deployed): drive the hub's own scheduler to "the next morning" ──
from datetime import datetime, timedelta  # noqa: E402

from fastapi import APIRouter, HTTPException  # noqa: E402

from app.config import settings as _settings  # noqa: E402
from app.db import db as _db, now as _now  # noqa: E402


def _dev_only():
    # the hub's own test for "development on this laptop": APP_ENV development, a localhost APP_URL, not on a hosting platform
    if not _settings.local_dev or _settings.env == "test":
        raise HTTPException(404, "Not found")


lt = APIRouter()


@lt.post("/__lt/tick")
async def lt_tick(at: str):
    _dev_only()
    from app.agents import scheduler
    return {"jobs": await scheduler.run_due_jobs(datetime.fromisoformat(at))}


@lt.get("/__lt/followups")
async def lt_followups(at: str | None = None):
    _dev_only()
    t = datetime.fromisoformat(at) if at else _now()
    jobs = [j async for j in _db().agent_jobs.find({"agent": "followup"}, {"status": 1, "run_at": 1, "payload": 1, "attempts": 1})]
    first = sorted(j["run_at"] for j in jobs if (j.get("payload") or {}).get("n") == 1)
    by: dict = {}
    for j in jobs:
        if (j.get("payload") or {}).get("n") == 1:
            by[j["status"]] = by.get(j["status"], 0) + 1
    retry = sorted(j["run_at"] for j in jobs if j["status"] == "scheduled" and j.get("attempts") and (j.get("payload") or {}).get("n") == 1)
    return {"first_due": first[0].isoformat() if first else None, "last_first_due": (first[-1] + timedelta(minutes=1)).isoformat() if first else None,
            "due_now": sum(1 for j in jobs if j["status"] == "scheduled" and j["run_at"] <= t and (j.get("payload") or {}).get("n") == 1),
            "retrying": len(retry), "next_retry": (retry[0] + timedelta(minutes=1)).isoformat() if retry else None, "by_status": by,
            "approvals": await _db().approvals.count_documents({"kind": "followup"})}


_before = len(app.router.routes)
app.include_router(lt)
_mine = app.router.routes[_before:]
app.router.routes[:] = _mine + app.router.routes[:_before]   # ahead of the web app's catch-all page route
