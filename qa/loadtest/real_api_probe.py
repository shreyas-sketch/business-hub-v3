"""The real AI account under a full room: what its limits are, and what happens when 200 people ask at once.

No hub, no database: it uses the hub's own engine pool (app.ai.provider: its keys, backups, "wait your turn" gate and
retries) and the hub's own website-writing task, so what it measures is what the room would get.

  1. One tiny call per key: prints the account's limits from the provider's answer (requests, input and output tokens a
     minute) and its organisation, so you can see whether two keys are really two accounts.
  2. With --burst N: N website drafts at once (everyone pressing "Build my website" in the same second), as one server
     process would send them. Prints how many were answered, how long people waited, how many were told "busy",
     the output tokens a minute it reached, and the cost.

It spends real money (a website draft is roughly ₹1–2 with the default model), so the burst asks before it starts.
Uses the same variables as the hub: AI_PROVIDER, ANTHROPIC_API_KEY(S), AI_FALLBACK, GEMINI_API_KEY(S), OPENAI_API_KEY(S),
AI_MODEL, AI_CONCURRENCY, AI_RETRIES.

  cd backend && python ../qa/loadtest/real_api_probe.py                 # limits only (a few paise)
  cd backend && python ../qa/loadtest/real_api_probe.py --burst 200     # the room
"""
import argparse
import asyncio
import json
import os
import statistics
import sys
import time

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("APP_URL", "http://localhost:8000")
os.environ.setdefault("AI_PROVIDER", "anthropic")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

import httpx  # noqa: E402

from app.ai import provider, tasks  # noqa: E402
from app.config import settings  # noqa: E402

LIMIT_HEADERS = ("requests-limit", "input-tokens-limit", "output-tokens-limit", "tokens-limit")
OWNERS = [("Shree Ganesh Interiors", "Thane", "Home and office interiors with in-house carpenters", "Modular kitchen"),
          ("Kaveri Sweets", "Pune", "Fresh sweets, farsan and festival boxes", "Diwali gift box"),
          ("Sai Dental Care", "Nashik", "Family dentistry, braces and implants", "Teeth cleaning"),
          ("CoolFix Appliances", "Surat", "Washing machine, fridge and AC repair at home", "AC service"),
          ("Bright Minds Classes", "Indore", "Maths and science coaching for classes 8 to 12", "Class 10 batch")]


def profile(i: int) -> dict:
    name, city, what, offer = OWNERS[i % len(OWNERS)]
    return {"name": f"{name} {i}", "city": city, "industry": what, "offers": [{"name": offer, "price": "", "unit": ""}],
            "ideal_customer": "Families and small businesses nearby", "why_us": "On time\nClear prices", "language": "English"}


async def limits() -> None:
    print(f"Engines in the pool ({len(provider.engines())}):")
    for e in provider.engines():
        if e.provider != "anthropic":
            print(f"  {e.label}: limits are read in the provider's console (only Anthropic sends them with each answer)")
            continue
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post("https://api.anthropic.com/v1/messages", headers={
                "x-api-key": e.key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": e.model, "max_tokens": 16, "messages": [{"role": "user", "content": "Say ok."}]})
        if r.status_code != 200:
            print(f"  {e.label}: {r.status_code} {r.text[:200]}\n    → {provider.explain(f'anthropic {r.status_code}: {r.text[:200]}')}")
            continue
        got = {h: r.headers.get(f"anthropic-ratelimit-{h}") for h in LIMIT_HEADERS if r.headers.get(f"anthropic-ratelimit-{h}")}
        print(f"  {e.label}: organisation {r.headers.get('anthropic-organization-id', '?')}")
        for h, v in got.items():
            print(f"    {h.replace('-limit', '')} a minute: {int(v):,}")
        out = int(got.get("output-tokens-limit") or 0)
        if out:
            # the event's own figures: about 1,400 AI calls and 1.5 million output tokens in about 10 minutes for 200 people
            print(f"    → the 200-person event needs ~150,000 output tokens a minute; this key covers {min(100, out * 100 // 150_000)}%")
    print("  (Keys of the same organisation share one set of limits: only keys on separate accounts add capacity.)")


async def burst(n: int) -> dict:
    results = []

    async def one(i: int):
        t = time.monotonic()
        try:
            _, usage = await tasks.site_content(profile(i), None)
            results.append({"ok": True, "s": time.monotonic() - t, "out": usage.output_tokens, "in": usage.input_tokens,
                            "model": usage.model})
        except provider.AIError as e:
            results.append({"ok": False, "s": time.monotonic() - t, "error": str(e)[:120]})

    start = time.monotonic()
    await asyncio.gather(*(one(i) for i in range(n)))
    took = time.monotonic() - start
    ok = [r for r in results if r["ok"]]
    waits = sorted(r["s"] for r in ok)
    out_tokens = sum(r["out"] for r in ok)
    errors: dict[str, int] = {}
    for r in results:
        if not r["ok"]:
            errors[r["error"]] = errors.get(r["error"], 0) + 1
    by_engine: dict[str, int] = {}
    for r in ok:
        by_engine[r["model"]] = by_engine.get(r["model"], 0) + 1
    report = {
        "asked": n, "answered": len(ok), "failed": n - len(ok), "seconds_all": round(took, 1),
        "wait_p50": round(statistics.median(waits), 1) if waits else None,
        "wait_p95": round(waits[int(len(waits) * 0.95) - 1], 1) if waits else None,
        "output_tokens": out_tokens, "output_tokens_a_minute": int(out_tokens / took * 60) if took else 0,
        "cost_inr": round(sum(provider.cost_inr(provider.Usage(r["in"], r["out"])) for r in ok), 2),
        "answered_by": by_engine, "errors": errors,
        "settings": {"AI_CONCURRENCY": settings.ai_concurrency, "AI_RETRIES": settings.ai_retries, "engines": [e.label for e in provider.engines()]},
    }
    return report


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--burst", type=int, default=0, help="website drafts to ask for at once (0 = limits only)")
    ap.add_argument("--yes", action="store_true", help="don't ask before spending")
    ap.add_argument("--out", default="", help="write the burst report here as JSON")
    a = ap.parse_args()
    if settings.ai_provider == "mock":
        sys.exit("AI_PROVIDER is mock: set AI_PROVIDER=anthropic and ANTHROPIC_API_KEY.")
    await limits()
    if not a.burst:
        return
    if not a.yes and input(f"\nSend {a.burst} real website drafts at once (roughly ₹{a.burst}–{a.burst * 2})? [y/N] ").strip().lower() != "y":
        return
    report = await burst(a.burst)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if a.out:
        with open(a.out, "w") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    asyncio.run(main())
