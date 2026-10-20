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
    r = await _http().post(f"{LIMITER}/call", json={"out": out})
    if r.status_code == 429:
        raise Busy("simulated 429", r.json().get("retry_after"))
    try:
        await asyncio.sleep(r.json()["latency"])
    finally:
        await _http().post(f"{LIMITER}/done")
    return mock(), Usage(int((len(system) + len(prompt)) / 3.5), out, "simulated")

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
