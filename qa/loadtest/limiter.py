"""Stand-in for the AI provider's limits, shared by every hub process: a minute's budget of output tokens and a cap on
requests in flight. Over either → 429 with a retry-after, like the real engines. Also hands out a realistic answer time
(median ~8 s, slower tail up to ~25 s) and counts everything for the report.

Each key in the hub's engine pool is its own account here, with its own budget (as keys on separate provider accounts are);
LIMIT_SHARED=1 puts every key on one account, as several keys of one Anthropic organisation are."""
import math
import os
import random
import time

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

OTPM = int(os.getenv("LIMIT_OTPM", "400000"))          # output tokens a minute (the account's tier)
INFLIGHT = int(os.getenv("LIMIT_INFLIGHT", "200"))     # requests in flight at once
SHARED = os.getenv("LIMIT_SHARED", "") == "1"
rng = random.Random(11)
state = {"inflight": 0, "calls": 0, "ok": 0, "busy": 0, "out_tokens": 0, "started": None, "peak_inflight": 0, "lat": []}
accounts: dict[str, dict] = {}    # account → {"tokens", "at", "inflight", "ok", "busy", "out_tokens"}


def account(name: str) -> dict:
    a = accounts.setdefault("shared" if SHARED else name, {"tokens": float(OTPM), "at": time.monotonic(), "inflight": 0, "ok": 0, "busy": 0, "out_tokens": 0})
    t = time.monotonic()
    a["tokens"] = min(OTPM, a["tokens"] + (t - a["at"]) * OTPM / 60)
    a["at"] = t
    return a


async def call(req: Request):
    body = await req.json()
    out = int(body.get("out", 900))
    a = account(str(body.get("account", "")))
    state["calls"] += 1
    state["started"] = state["started"] or time.time()
    if a["tokens"] < out or a["inflight"] >= INFLIGHT:
        state["busy"] += 1
        a["busy"] += 1
        wait = max(1.0, (out - a["tokens"]) / (OTPM / 60)) if a["tokens"] < out else 2.0
        return JSONResponse({"retry_after": round(min(wait, 20), 1)}, status_code=429)
    a["tokens"] -= out
    a["inflight"] += 1
    state["inflight"] += 1
    state["peak_inflight"] = max(state["peak_inflight"], state["inflight"])
    state["ok"] += 1
    a["ok"] += 1
    state["out_tokens"] += out
    a["out_tokens"] += out
    latency = min(max(rng.lognormvariate(math.log(8), 0.45), 2.5), 30)
    state["lat"].append(latency)
    return JSONResponse({"latency": round(latency, 2)})


async def done(req: Request):
    body = await req.json() if await req.body() else {}
    a = account(str(body.get("account", "")))
    a["inflight"] = max(0, a["inflight"] - 1)
    state["inflight"] = max(0, state["inflight"] - 1)
    return JSONResponse({"ok": True})


async def stats(req: Request):
    lat = sorted(state["lat"])
    return JSONResponse({k: v for k, v in state.items() if k != "lat"} | {
        "accounts": {k: {x: y for x, y in v.items() if x not in ("tokens", "at")} for k, v in accounts.items()},
        "otpm": OTPM, "inflight_cap": INFLIGHT, "lat_p50": lat[len(lat) // 2] if lat else None, "lat_p95": lat[int(len(lat) * .95)] if lat else None})


async def reset(req: Request):
    state.update(inflight=0, calls=0, ok=0, busy=0, out_tokens=0, started=None, peak_inflight=0, lat=[])
    accounts.clear()
    return JSONResponse({"ok": True})


app = Starlette(routes=[Route("/call", call, methods=["POST"]), Route("/done", done, methods=["POST"]), Route("/stats", stats),
                        Route("/reset", reset, methods=["POST"])])
uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("LIMIT_PORT", "8841")), log_level="warning")
