"""A paced load on a live hub through the public event flow: real owner websites, real competitor websites, the server's own
AI key. Nothing is saved to an account and no login code is sent; runs stay under the test event codes given.

Each run is what an attendee's phone does: POST /api/event/runs (website + competitors), then the status every 2 s, as the
event page does, until the pack is done or failed. Alongside, a monitor times /api/health and the event form every 2 s, so a
server that stalls (e.g. while it reads a heavy page) shows as a spike.

  python live_event_load.py --base https://ownerdesk.io --sites sites.json --stages LT-A:5:2,LT-B:10:3,LT-C:15:2 --out live.json

sites.json: [{"website": "https://owner.example", "competitors": ["https://rival1.example", "https://rival2.example"]}, ...]
Stages are CODE:packs-a-minute:minutes. Each pack's owner address gets ?lt=<tag><n>, since the hub builds at most 2 packs
per website per event code.
Stop early when the server errors on more than --max-errors of requests, or health takes more than --max-health seconds.
"""
import argparse
import asyncio
import json
import statistics
import time

import httpx

STEPS = ("site", "compliance", "check", "brand", "knowledge", "brand_message", "lead_ideas", "website")
DONE = ("done", "failed")


def pct(xs, p):
    xs = sorted(x for x in xs if x is not None)
    return round(xs[min(len(xs) - 1, int(len(xs) * p))], 1) if xs else None


class Load:
    def __init__(self, a):
        self.a = a
        self.runs: list[dict] = []
        self.health: list[dict] = []
        self.requests = {"ok": 0, "error": 0}
        self.stop = False
        self.why_stopped = ""

    async def req(self, c, method, path, **kw):
        t = time.monotonic()
        try:
            r = await c.request(method, path, **kw)
        except httpx.HTTPError as e:
            self.requests["error"] += 1
            return None, time.monotonic() - t, type(e).__name__
        bad = r.status_code >= 500
        self.requests["error" if bad else "ok"] += 1
        return r, time.monotonic() - t, None

    async def monitor(self, c):
        while not self.stop:
            row = {"at": round(time.time() - self.t0, 1)}
            for name, path in (("health", "/api/health"), ("form", f"/api/event/form?e={self.a.first_code}")):
                r, s, err = await self.req(c, "GET", path)
                row[name] = round(s, 3)
                if err or (r is not None and r.status_code != 200):
                    row[name + "_error"] = err or r.status_code
            row["in_flight"] = sum(1 for x in self.runs if x.get("status") not in DONE and "error" not in x)
            self.health.append(row)
            total = self.requests["ok"] + self.requests["error"]
            if total > 40 and self.requests["error"] / total > self.a.max_errors:
                self.stop, self.why_stopped = True, f"server errors above {self.a.max_errors:.0%}"
            if row["health"] > self.a.max_health:
                self.stop, self.why_stopped = True, f"/api/health took {row['health']} s"
            await asyncio.sleep(2)

    async def one(self, c, code, stage, site):
        run = {"stage": stage, "code": code, "website": site["website"], "competitors": site.get("competitors", [])[:3],
               "started": round(time.time() - self.t0, 1), "steps_at": {}}
        self.runs.append(run)
        t0 = time.monotonic()
        # the hub builds at most 2 packs per website per event code: a tag makes each pack's address its own (same page read)
        self.n = getattr(self, "n", 0) + 1
        tagged = site["website"] + ("&" if "?" in site["website"] else "?") + f"lt={self.a.tag}{self.n}"
        body = {"event": code, "website": tagged, "competitors": run["competitors"], "state": "Maharashtra",
                "entity": "proprietor", "staff": 3, "consent": True}
        r, s, err = await self.req(c, "POST", "/api/event/runs", json=body)
        run["create_s"] = round(s, 2)
        if err or r is None or r.status_code != 200:
            run["error"] = err or f"{r.status_code} {r.text[:160]}"
            return
        run["token"] = r.json()["token"]
        seen: dict[str, str] = {}
        while time.monotonic() - t0 < self.a.run_timeout:
            await asyncio.sleep(2)
            r, s, err = await self.req(c, "GET", f"/api/event/runs/{run['token']}")
            if err or r is None or r.status_code != 200:
                run.setdefault("poll_errors", 0)
                run["poll_errors"] += 1
                continue
            v = r.json()
            el = round(time.monotonic() - t0, 1)
            run["status"] = v["status"]
            for k, st in (v.get("steps") or {}).items():
                if seen.get(k) != st.get("status"):
                    seen[k] = st.get("status")
                    if st.get("status") in ("done", "skipped", "failed", "error"):
                        run["steps_at"][k] = {"s": el, "status": st.get("status"), "note": (st.get("note") or "")[:120]}
            if v.get("competitors") is not None and "competitors_at" not in run and v["competitors"]:
                run["competitors_at"] = el
                run["competitor_results"] = [{"host": x.get("host"), "read": x.get("read"), "error": x.get("error")}
                                             for x in v["competitors"]]
            site_v = v.get("site") or {}
            if site_v and "site_read" not in run and site_v.get("read") is not None:
                run["site_read"] = site_v.get("read")
                run["site_error"] = site_v.get("error")
            if v["status"] in DONE and not v.get("pending"):
                run["total_s"] = el
                return
        run["error"] = f"still {run.get('status')} after {self.a.run_timeout} s"

    async def main(self):
        sites = json.load(open(self.a.sites))
        stages = []
        for part in self.a.stages.split(","):
            code, rate, minutes = part.split(":")
            stages.append((code, float(rate), float(minutes)))
        self.a.first_code = stages[0][0]
        limits = httpx.Limits(max_connections=200, max_keepalive_connections=100)
        async with httpx.AsyncClient(base_url=self.a.base, timeout=60, limits=limits,
                                     headers={"user-agent": "OwnerDesk-loadtest/1"}) as c:
            for code, *_ in stages:
                form = (await c.get(f"/api/event/form?e={code}")).json()
                if not form.get("open"):
                    raise SystemExit(f"Event code {code} isn't open on {self.a.base}: {form.get('valid') and 'closed' or 'not found'}")
            self.t0 = time.time()
            mon = asyncio.create_task(self.monitor(c))
            tasks, used = [], 0
            for code, rate, minutes in stages:
                n = int(rate * minutes)
                stage = f"{rate:g} a minute"
                print(f"stage {stage} ({code}): {n} packs", flush=True)
                for i in range(n):
                    if self.stop:
                        break
                    site = sites[(used + i) % len(sites)]
                    tasks.append(asyncio.create_task(self.one(c, code, stage, site)))
                    await asyncio.sleep(60 / rate)
                used += n
                if self.stop:
                    print("stopped:", self.why_stopped, flush=True)
                    break
            await asyncio.gather(*tasks)
            self.stop = True
            await mon
        return self.report()

    def report(self):
        by_stage = {}
        for code in dict.fromkeys(r["stage"] for r in self.runs):
            rs = [r for r in self.runs if r["stage"] == code]
            ok = [r for r in rs if r.get("status") == "done" and "total_s" in r]
            site = [r["steps_at"]["site"]["s"] for r in rs if "site" in r["steps_at"]]
            web = [r["steps_at"]["website"]["s"] for r in rs if "website" in r["steps_at"]]
            comp = [r["competitors_at"] for r in rs if "competitors_at" in r]
            creates = [r["create_s"] for r in rs if "create_s" in r]
            by_stage[code] = {
                "packs": len(rs), "done": len(ok), "failed": sum(r.get("status") == "failed" for r in rs),
                "errors": [r["error"] for r in rs if "error" in r][:10],
                "create_request_s": {"p50": pct(creates, .5), "p95": pct(creates, .95)},
                "owner_site_read_s": {"p50": pct(site, .5), "p95": pct(site, .95)},
                "competitors_read_s": {"p50": pct(comp, .5), "p95": pct(comp, .95)},
                "website_draft_s": {"p50": pct(web, .5), "p95": pct(web, .95)},
                "whole_pack_s": {"p50": pct([r["total_s"] for r in ok], .5), "p95": pct([r["total_s"] for r in ok], .95)},
                "owner_sites_unreadable": sum(r.get("site_read") is False for r in rs),
                "competitors_unreadable": sum(1 for r in rs for x in r.get("competitor_results") or [] if not x["read"]),
                "ai_steps_failed": sum(1 for r in rs for k in ("brand_message", "lead_ideas", "website")
                                       if r["steps_at"].get(k, {}).get("status") in ("failed", "error")),
            }
        per_site = {}
        for r in self.runs:
            if "site" in r["steps_at"]:
                per_site.setdefault(r["website"], []).append(r["steps_at"]["site"]["s"])
        hs = [h["health"] for h in self.health]
        return {
            "config": {k: v for k, v in vars(self.a).items()},
            "requests": self.requests, "stopped_early": self.why_stopped or None,
            "stages": by_stage,
            "owner_site_read_s_by_site": {k: {"runs": len(v), "median": round(statistics.median(v), 1)} for k, v in per_site.items()},
            "competitor_failures": sorted({(x["host"], x["error"]) for r in self.runs for x in r.get("competitor_results") or [] if not x["read"]}),
            "health_s": {"p50": pct(hs, .5), "p95": pct(hs, .95), "max": max(hs) if hs else None,
                         "over_1s": sum(h > 1 for h in hs), "samples": len(hs)},
            "health": self.health, "runs": self.runs,
        }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--sites", required=True)
    ap.add_argument("--stages", default="LT-A:5:2,LT-B:10:3,LT-C:15:2")
    ap.add_argument("--run-timeout", type=float, default=420)
    ap.add_argument("--max-errors", type=float, default=0.05)
    ap.add_argument("--max-health", type=float, default=10)
    ap.add_argument("--out", default="live.json")
    ap.add_argument("--tag", default=str(int(time.time()) % 100000), help="marks this test's packs (?lt=<tag><n>)")
    a = ap.parse_args()
    rep = asyncio.run(Load(a).main())
    json.dump(rep, open(a.out, "w"), indent=1, default=str)
    print(json.dumps({k: rep[k] for k in ("requests", "stopped_early", "stages", "health_s")}, indent=1))
