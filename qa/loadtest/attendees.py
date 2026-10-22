"""
200 attendees in one hall, on one Wi-Fi address, doing the whole event flow against a running hub:

  scan the QR → type website + GST → watch the pack build → save it with the WhatsApp code → then the speaker's demos, the
  whole room on the same demo within a minute: website live, send yourself an inquiry, swap with your neighbour, inquiry to
  quotation, the funnel leak finder, brand kit, cash flow + what-if, daily number, score twice, Dashboard — then "the next
  morning": the follow-ups for Day 1's inquiries are written (the hub's own scheduler, driven forward a day) and the room
  sends them from the Inquiries page.

Every person, number, GST number and website here is made up. Usage:
  python attendees.py --base http://127.0.0.1:8830 --n 200 --arrive 90 --typing 20,45 --out results.json
"""
import argparse
import asyncio
import json
import random
import statistics
import threading
import time
from collections import defaultdict
from datetime import date, timedelta

import httpx
import psutil

CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
STATES = ["27", "24", "29", "07", "09", "33", "36", "08", "23", "19"]
ANSWERS = ["focus", "margins", "sales", "payments", "offers", "reply", "followup", "content", "process", "numbers"]


def check_char(first14: str) -> str:
    t = 0
    for i, ch in enumerate(first14):
        p = CHARSET.index(ch) * (2 if i % 2 else 1)
        t += p // 36 + p % 36
    return CHARSET[(36 - t % 36) % 36]


def gstin(i: int, kind: str) -> str:
    first14 = f"{STATES[i % len(STATES)]}{''.join(CHARSET[10 + (i * 7 + k) % 26] for k in range(3))}{kind}{CHARSET[10 + i % 26]}{i % 10000:04d}{CHARSET[10 + (i * 3) % 26]}1Z"
    return first14 + check_char(first14)


class Stats:
    def __init__(self):
        self.calls = defaultdict(list)       # name → [seconds]
        self.errors = defaultdict(lambda: defaultdict(int))   # name → status → count
        self.marks = defaultdict(list)       # journey mark → [seconds]
        self.notes = defaultdict(int)

    def pct(self, xs, p):
        xs = sorted(xs)
        return round(xs[min(len(xs) - 1, int(len(xs) * p))], 2) if xs else None


S = Stats()


async def call(c: httpx.AsyncClient, name: str, method: str, path: str, ok=(200,), **kw):
    t = time.monotonic()
    try:
        r = await c.request(method, path, **kw)
    except Exception as e:  # noqa: BLE001
        S.calls[name].append(time.monotonic() - t)
        S.errors[name][type(e).__name__] += 1
        return None
    S.calls[name].append(time.monotonic() - t)
    if r.status_code not in ok:
        S.errors[name][r.status_code] += 1
    return r


class Room:
    """The speaker: moves the room to the next demo when nearly everyone is done, or after a time limit."""
    def __init__(self, n):
        self.n = n
        self.events: dict[str, asyncio.Event] = defaultdict(asyncio.Event)
        self.done: dict[str, int] = defaultdict(int)
        self.slugs: dict[int, str] = {}

    async def finished(self, stage):
        self.done[stage] += 1

    async def go_when(self, stage, share=0.95, limit=240):
        t = time.monotonic()
        while self.done[stage] < self.n * share and time.monotonic() - t < limit:
            await asyncio.sleep(1)
        self.events[stage + ":next"].set()

    async def wait(self, stage):
        await self.events[stage + ":next"].wait()


async def attendee(i: int, a, room: Room, code: str):
    phone = f"97{i:08d}"
    rng = random.Random(i)
    await asyncio.sleep(rng.uniform(0, a.arrive))
    async with httpx.AsyncClient(base_url=a.base, timeout=180, limits=httpx.Limits(max_connections=6)) as c:
        start = time.monotonic()
        await call(c, "page /event", "GET", f"/event?e={code}")
        await call(c, "GET form", "GET", f"/api/event/form?e={code}")
        lo, hi = a.typing
        await asyncio.sleep(rng.uniform(lo, hi))
        has_site, has_gst = rng.random() < 0.85, rng.random() < 0.9
        kind = rng.choice("PPPPFC")
        body = {"event": code, "consent": True, "staff": rng.choice([0, 2, 5, 12, 25]), "gst_scheme": rng.choice(["monthly", "monthly", "qrmp"])}
        if has_site:
            body["website"] = f"{a.web}/b/{i}/"
        else:
            body.update(business_name=f"Sample Business {i}", about="Home-cooked lunch tiffins delivered to offices nearby", city="Pune")
        if has_gst:
            g = gstin(i, kind)
            await call(c, "POST gstin", "POST", "/api/event/gstin", json={"gstin": g})
            body["gstin"] = g
            body["entity"] = {"P": "proprietor", "F": "partnership", "C": "pvt_ltd"}[kind]
        else:
            body.update(entity="proprietor", state="Maharashtra")
        submitted = time.monotonic()
        r = await call(c, "POST runs (build my pack)", "POST", "/api/event/runs", json=body)
        if not r or r.status_code != 200:
            S.notes["pack refused"] += 1
            return
        token = r.json()["token"]
        first = full = None
        drew = False
        while True:
            await asyncio.sleep(2)
            r = await call(c, "GET pack (poll every 2 s)", "GET", f"/api/event/runs/{token}")
            if not r or r.status_code != 200:
                continue
            v = r.json()
            st = v["steps"]
            if first is None and st["compliance"]["status"] == "done" and st["brand"]["status"] == "done":
                first = time.monotonic() - submitted
                S.marks["first things on screen (s after Build)"].append(first)
            if v["design"] and not drew and v["business"]["name"]:
                drew = True
                await asyncio.gather(call(c, "GET poster png", "GET", f"/api/event/runs/{token}/design/status_poster.png"),
                                     call(c, "GET post png", "GET", f"/api/event/runs/{token}/design/social_square.png"))
            if v.get("needs_about"):   # the website couldn't be read: the attendee types the business in a line
                S.notes["website unreadable → typed a line"] += 1
                await asyncio.sleep(rng.uniform(*a.typing) / 2)
                await call(c, "POST about (site unreadable)", "POST", f"/api/event/runs/{token}/about",
                           json={"business_name": f"Sample Business {i}", "about": "Repairs and servicing for homes and offices", "city": "Pune"})
                continue
            if v["status"] in ("done", "failed"):
                full = time.monotonic() - submitted
                S.marks["whole pack ready (s after Build)"].append(full)
                if v["status"] == "failed":
                    S.notes["pack failed"] += 1
                for k, s in st.items():
                    S.notes[f"step {k}: {s['status']}"] += 1
                if v["brand"]["options"] and rng.random() < 0.5:
                    await call(c, "POST brand pick", "POST", f"/api/event/runs/{token}/brand", json={"pick": 1})
                    await call(c, "GET poster png", "GET", f"/api/event/runs/{token}/design/status_poster.png")
                break
            if time.monotonic() - submitted > 900:
                S.notes["pack not ready in 15 min"] += 1
                return
        await asyncio.sleep(rng.uniform(*a.reading))
        t = time.monotonic()
        r = await call(c, "POST code (WhatsApp OTP)", "POST", f"/api/event/runs/{token}/code", json={"phone": phone})
        if not r or r.status_code != 200:
            S.notes["code not sent"] += 1
            return
        dev = r.json().get("dev_code")
        await asyncio.sleep(rng.uniform(3, 8))   # reading the code off WhatsApp
        r = await call(c, "POST verify (account + pack inside)", "POST", f"/api/event/runs/{token}/verify",
                       json={"phone": phone, "code": dev, "email": f"owner{i}@sample-business.example", "name": f"Owner {i}"})
        if not r or r.status_code != 200:
            S.notes["save failed"] += 1
            return
        S.marks["code asked → in the hub (s)"].append(time.monotonic() - t)
        S.marks["scan → saved in the hub (s)"].append(time.monotonic() - start)
        await asyncio.gather(call(c, "GET /api/me", "GET", "/api/me"), call(c, "page /", "GET", "/"))
        await call(c, "GET dashboard", "GET", "/api/dashboard")
        await room.finished("saved")

        async def demo(stage, fn, window=45):
            await room.wait(stage)
            await asyncio.sleep(rng.uniform(0, window))
            try:
                await fn()
            finally:
                await room.finished(stage)

        async def publish():
            await call(c, "POST site publish", "POST", "/api/site/publish")
            r = await call(c, "GET site", "GET", "/api/site")
            if r and r.status_code == 200 and r.json().get("slug"):
                room.slugs[i] = r.json()["slug"]
                await call(c, "GET site qr", "GET", "/api/site/qr.png")

        async def swap():
            n = i % a.n + 1
            slug = room.slugs.get(n)
            if slug:
                await call(c, "POST neighbour's website form", "POST", f"/s/{slug}/inquiry", ok=(200,),
                           json={"name": f"Owner {i}", "phone": phone, "message": "Saw your website at the event. Please call me."})

        async def own_customer():
            r = await call(c, "POST inquiry (WhatsApp paste)", "POST", "/api/inquiries/whatsapp",
                           json={"name": "Test customer", "phone": phone, "message": "Hi, what's the price and when can you start?"})
            if not r or r.status_code != 200:
                return
            lead, t0 = r.json()["id"], time.monotonic()
            while time.monotonic() - t0 < 300:
                await asyncio.sleep(3)
                r = await call(c, "GET inquiries (poll)", "GET", "/api/inquiries")
                item = next((x for x in (r.json()["items"] if r and r.status_code == 200 else []) if x["id"] == lead), None)
                if item and item.get("proposal"):
                    S.marks["inquiry → reply written (s)"].append(time.monotonic() - t0)
                    room_q[i] = item
                    return
            S.notes["reply not written in 5 min"] += 1

        room_q: dict = {}

        async def quotation():
            msg = (room_q.get(i) or {}).get("message") or "Need a price for the full job"
            r = await call(c, "POST quotes/draft (AI, waits)", "POST", "/api/quotes/draft", json={"brief": msg})
            if r and r.status_code == 200:
                lines = [{**x, "rate": x["rate"] or 1000} for x in r.json()["items"]]
                await call(c, "POST quotes (save)", "POST", "/api/quotes", json={
                    "customer": {"name": "Test customer", "phone": phone, "gstin": "", "address": ""}, "items": lines, "gst_mode": "intra",
                    "notes": "", "terms": "", "valid_days": 15, "lead_id": (room_q.get(i) or {}).get("id")})

        async def leak():
            nums = {"visitors": 0, "inquiries": rng.randint(15, 80), "meetings": rng.randint(4, 14), "proposals": rng.randint(3, 10), "won": rng.randint(1, 4)}
            await call(c, "PUT leaks (funnel leak finder)", "PUT", "/api/leaks", json={"numbers": nums, "targets": {}, "avg_sale": rng.choice([15000, 36000, 120000])})

        async def followups():
            r = await call(c, "GET inquiries (next morning)", "GET", "/api/inquiries")
            ready = [x["followups"]["ready"] for x in (r.json()["items"] if r and r.status_code == 200 else []) if (x.get("followups") or {}).get("ready")]
            S.notes["attendees with a follow-up ready"] += bool(ready)
            for f in ready:
                r = await call(c, "POST follow-up send (tap)", "POST", f"/api/approvals/{f['id']}/own")
                if r and r.status_code == 200 and r.json()["link"].startswith("https://wa.me/"):
                    S.notes["follow-ups sent"] += 1

        async def brand():
            await call(c, "POST brand-kit/generate (AI, waits)", "POST", "/api/brand-kit/generate")

        async def cash():
            today = date.today()
            await call(c, "PUT knowledge money", "PUT", "/api/knowledge/money", json={
                "payment_terms_days": 15, "bank_balance": 150000, "bank_balance_on": today.isoformat(), "gst_rate": 18, "safety_buffer": 0,
                "fixed_costs": [{"name": "Rent", "amount": 40000, "day": 5, "kind": "rent"}, {"name": "Salaries", "amount": 90000, "day": 1, "kind": "salary"}]})
            r = await call(c, "POST money (a due)", "POST", "/api/money", json={"customer": "Test customer", "amount": 60000,
                                                                               "due_date": (today + timedelta(days=6)).isoformat()})
            await call(c, "GET cash-flow", "GET", "/api/cash-flow")
            if r and r.status_code == 200:
                await call(c, "GET cash-flow what-if", "GET", f"/api/cash-flow?delay_id={r.json()['id']}&delay_days=15")

        async def target():
            await call(c, "PUT knowledge targets", "PUT", "/api/knowledge/targets", json={"monthly_revenue": 800000})
            await call(c, "GET target", "GET", "/api/target")
            await call(c, "GET /api/me", "GET", "/api/me")

        async def score():
            ans = {k: rng.choice(["yes", "partly", "no"]) for k in ANSWERS}
            await call(c, "POST score", "POST", "/api/score", json={"answers": ans})
            await call(c, "POST score (re-take)", "POST", "/api/score", json={"answers": {**ans, "reply": "yes"}})

        for stage, fn in DEMOS(publish, swap, own_customer, quotation, leak, brand, cash, target, score, followups):
            await demo(stage, fn)


def DEMOS(publish, swap, own_customer, quotation, leak, brand, cash, target, score, followups):
    return [("publish", publish), ("swap", swap), ("own_customer", own_customer), ("quotation", quotation), ("leak", leak), ("brand", brand),
            ("cash", cash), ("target", target), ("score", score), ("followups", followups)]


STAGES = ["saved", "publish", "swap", "own_customer", "quotation", "leak", "brand", "cash", "target", "score", "followups"]
NEXT_MORNING: dict = {}


async def next_morning(a):
    """Day 2: the hub's scheduler, driven to the time each Day-1 follow-up falls due (and on, half an hour at a time, for any
    the AI engine was too busy to write), until none is left. Measures how long the hub takes, in real time, to write them."""
    async with httpx.AsyncClient(base_url=a.base, timeout=1800) as c:
        st = (await c.get("/__lt/followups")).json()
        if not st["first_due"]:
            NEXT_MORNING.update(note="no follow-ups were scheduled")
            return
        at = st["last_first_due"]
        t0, ticks = time.monotonic(), []
        for _ in range(8):
            t = time.monotonic()
            r = (await c.post("/__lt/tick", params={"at": at})).json()
            ticks.append({"at": at, "jobs_run": r["jobs"], "seconds": round(time.monotonic() - t, 1)})
            st = (await c.get("/__lt/followups", params={"at": at})).json()
            if not st["due_now"] and not st["retrying"]:
                break
            at = st["next_retry"] or at
        NEXT_MORNING.update(seconds=round(time.monotonic() - t0, 1), ticks=ticks, followups=st["by_status"], approvals=st["approvals"])


async def speaker(room: Room, a):
    """Moves the room on: demo k starts when ~95% finished demo k-1 (or after 4 minutes)."""
    await room.go_when("saved", share=a.share, limit=a.stage_limit * 3)
    room.events["publish:next"].set()   # the first demo starts once the room has saved its packs
    marks = {}
    order = STAGES[1:]
    marks["saved"] = time.monotonic()
    for prev, nxt in zip(order, order[1:]):
        await room.go_when(prev, share=a.share, limit=a.stage_limit)
        if nxt == "followups":
            await next_morning(a)
        room.events[nxt + ":next"].set()
        marks[prev] = time.monotonic()


def sampler(stop: threading.Event, out: list):
    procs = {}
    while not stop.is_set():
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            cmd = " ".join(p.info.get("cmdline") or [])
            if p.info["pid"] not in procs and ("mongod" in (p.info.get("name") or "") or "lt_app" in cmd or "uvicorn" in cmd):
                try:
                    p.cpu_percent(None)
                    procs[p.info["pid"]] = p
                except psutil.Error:
                    pass
        machine = psutil.cpu_percent(interval=2)
        row = {"t": time.time(), "machine_cpu": machine, "mem_used_mb": round(psutil.virtual_memory().used / 2**20)}
        for pid, p in list(procs.items()):
            try:
                row[f"{p.name()}:{pid}"] = p.cpu_percent(None)
            except psutil.Error:
                procs.pop(pid, None)
        out.append(row)


async def setup(a, code):
    async with httpx.AsyncClient(base_url=a.base, timeout=60) as admin:
        r = await admin.post("/api/auth/otp", json={"phone": "9999900000"})
        if r.status_code != 200 or "dev_code" not in r.json():
            # a hub that sends real WhatsApp codes would message 200 made-up numbers — some of them real people's
            raise SystemExit("This hub sends real login codes. The load test only runs against a development hub "
                             "(APP_ENV=development, APP_URL on localhost, no AiSensy/MSG91 keys). See README.md.")
        await admin.post("/api/auth/verify", json={"phone": "9999900000", "code": r.json()["dev_code"]})
        await admin.post("/api/admin/cohorts", json={"code": code, "name": "Load test event", "workshop_date": date.today().isoformat()})
        r = await admin.put(f"/api/admin/event/{code}", json={"open": True, "ai": True, "pack_cap": 2000, "ai_cap": 2000,
                                                             "trial_until": (date.today() + timedelta(days=3)).isoformat()})
        assert r.status_code == 200, r.text
        await admin.post("/api/admin/config/presets/membership-v22")   # the ₹1,999 Membership: incl. leak finder and follow-ups
        return dict(admin.cookies)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8830")
    ap.add_argument("--web", default="http://127.0.0.1:8850")
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--arrive", type=float, default=90)
    ap.add_argument("--typing", default="20,45")
    ap.add_argument("--reading", default="10,30")
    ap.add_argument("--share", type=float, default=0.95)
    ap.add_argument("--stage-limit", type=float, default=240)
    ap.add_argument("--code", default=f"LT-{int(time.time()) % 100000}")
    ap.add_argument("--out", default="results.json")
    a = ap.parse_args()
    a.typing = tuple(float(x) for x in a.typing.split(","))
    a.reading = tuple(float(x) for x in a.reading.split(","))
    await setup(a, a.code)
    stop, cpu = threading.Event(), []
    th = threading.Thread(target=sampler, args=(stop, cpu), daemon=True)
    th.start()
    room = Room(a.n)
    t0 = time.monotonic()
    sp = asyncio.create_task(speaker(room, a))
    await asyncio.gather(*(attendee(i, a, room, a.code) for i in range(1, a.n + 1)))
    sp.cancel()
    took = time.monotonic() - t0
    stop.set()
    th.join(5)
    async with httpx.AsyncClient(timeout=20) as c:
        lim = (await c.get("http://127.0.0.1:8841/stats")).json()
        r = await c.post(f"{a.base}/api/auth/otp", json={"phone": "9999900000"})
        await c.post(f"{a.base}/api/auth/verify", json={"phone": "9999900000", "code": r.json()["dev_code"]})
        live = (await c.get(f"{a.base}/api/admin/event/{a.code}/live")).json()
    calls = {k: {"n": len(v), "p50": S.pct(v, .5), "p95": S.pct(v, .95), "max": round(max(v), 2),
                 "errors": dict(S.errors.get(k, {}))} for k, v in sorted(S.calls.items())}
    marks = {k: {"n": len(v), "p50": S.pct(v, .5), "p95": S.pct(v, .95), "max": round(max(v), 2)} for k, v in S.marks.items()}
    peak = max((r["machine_cpu"] for r in cpu), default=None)
    avg = round(statistics.mean(r["machine_cpu"] for r in cpu), 1) if cpu else None
    res = {"config": {**{k: v for k, v in vars(a).items()}, "cores": psutil.cpu_count()}, "seconds": round(took), "calls": calls, "journey": marks,
           "notes": dict(S.notes), "next_morning": NEXT_MORNING, "ai_limiter": lim, "speaker_screen": live, "machine_cpu": {"avg": avg, "peak": peak},
           "cpu_samples": cpu}
    json.dump(res, open(a.out, "w"), indent=1, default=str)
    print(json.dumps({k: res[k] for k in ("seconds", "journey", "notes", "next_morning", "machine_cpu")}, indent=1))
    print(json.dumps({k: (v["p50"], v["p95"], v["max"], v["errors"]) for k, v in calls.items()}, indent=0))
    print(json.dumps({k: lim[k] for k in ("calls", "ok", "busy", "peak_inflight", "lat_p50", "lat_p95", "out_tokens")}))


if __name__ == "__main__":
    asyncio.run(main())
