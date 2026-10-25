"""All 200 owners press Upload on the bank statement within ~5 seconds — the worst case for the reader (2 statements at a time per
server process). Run after attendees.py, against the same hub (it logs in the same 200 accounts):
  python bank_burst.py <test pack folder>
"""
import asyncio, random, sys, time
import httpx
B = "http://127.0.0.1:8830"; P = sys.argv[1]
csv_, pdf = open(f"{P}/05_Bank_Statement_Jul-Sep2026.csv", "rb").read(), open(f"{P}/05_Bank_Statement_Jul-Sep2026_password_DMK4417.pdf", "rb").read()
res = {"ok": 0, "busy": 0, "other": {}, "times": [], "tries": {}}

async def one(i):
    async with httpx.AsyncClient(base_url=B, timeout=180) as c:
        r = await c.post("/api/auth/otp", json={"phone": f"97{i:08d}"})
        await c.post("/api/auth/verify", json={"phone": f"97{i:08d}", "code": r.json()["dev_code"]})
        await GO.wait()
        await asyncio.sleep(random.uniform(0, 5))
        locked = i % 10 < 3
        t = time.monotonic()
        for attempt in range(1, 6):
            r = await c.post("/api/bank-statement", files={"file": ("s.pdf" if locked else "s.csv", pdf if locked else csv_)},
                             data={"password": "DMK4417" if locked else ""})
            if r.status_code == 200:
                res["ok"] += 1; res["times"].append(time.monotonic() - t); res["tries"][attempt] = res["tries"].get(attempt, 0) + 1
                return
            msg = str(r.json().get("detail"))
            if "right now" in msg:
                res["busy"] += 1
                await asyncio.sleep(random.uniform(10, 30))   # "try again in a minute": the owner taps again
                continue
            res["other"][msg[:80]] = res["other"].get(msg[:80], 0) + 1
            return

async def main():
    global GO
    GO = asyncio.Event()
    tasks = [asyncio.create_task(one(i)) for i in range(1, 201)]
    await asyncio.sleep(25); GO.set(); t0 = time.monotonic()
    await asyncio.gather(*tasks)
    ts = sorted(res["times"])
    print({"read": res["ok"], "busy_answers": res["busy"], "read_on_try": res["tries"], "other": res["other"],
           "wait_p50": round(ts[len(ts)//2], 1) if ts else None, "wait_p95": round(ts[int(len(ts)*.95)], 1) if ts else None,
           "wait_max": round(ts[-1], 1) if ts else None, "all_done_s": round(time.monotonic() - t0, 1)})
asyncio.run(main())
