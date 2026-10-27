"""Fills one owner's hub with three months of everyday use, for showing that account on stage: inquiries from the website
and WhatsApp, customers, quotations and invoices, money owed and paid, expenses, a year of sales, posts, SOPs, goals, the
automations switched on with their history, and the rest of what the owner's plan includes. It only adds: the owner's own
business profile, website and records are kept as they are.

Run it where the hub runs (Railway → the service → Shell), so it reaches the live database:

    python seed_showcase.py --phone 9167630897            # does it
    python seed_showcase.py --phone 9167630897 --check    # only says what it would fill, changes nothing

Nothing is sent while it runs: WhatsApp, SMS, e-mail and payments are switched off in this process, and the scheduler doesn't
run here. Automations are left in "ask first" (their messages wait in Approvals; nothing goes to a customer on its own), no
money reminder is set to e-mail anyone, and no job is left due in the future for these customers. People's names and numbers
are invented; don't press Send on them during a demo.
It writes a list of everything it added to showcase-<phone>-<date>.json, next to this file.
"""
import argparse
import asyncio
import csv
import io
import json
import os
import random
import sys
from datetime import date, datetime, timedelta

# ── never send from this process: the keys that reach real people are removed before the hub is loaded ──
for _k in ("AISENSY_API_KEY", "MSG91_AUTH_KEY", "RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
           "WATI_API_TOKEN", "INTERAKT_API_KEY", "WHATSAPP_HUB_ACCESS_TOKEN", "ELEVENLABS_API_KEY"):
    os.environ.pop(_k, None)
os.environ["EMAIL_PROVIDER"] = ""
os.environ["RUN_SCHEDULER"] = "false"
os.environ["V23_AUTO_APPLY"] = "false"

import httpx  # noqa: E402
import jwt  # noqa: E402
from asgi_lifespan import LifespanManager  # noqa: E402

from app import plans  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import IST, connect, now  # noqa: E402
from app.main import app  # noqa: E402
from app.security import COOKIE, _secret, normalise_phone  # noqa: E402
from app.services import new_id  # noqa: E402

rng = random.Random()
FIRST = ["Rohan", "Sneha", "Amit", "Pallavi", "Nikhil", "Anjali", "Sameer", "Kirti", "Manoj", "Rutuja", "Vivek", "Meera", "Tushar", "Gauri",
         "Prasad", "Swati", "Omkar", "Neha", "Rahul", "Aditi", "Kunal", "Shruti", "Yash", "Pooja", "Ajay", "Priya", "Sachin", "Komal",
         "Harish", "Deepa", "Varun", "Ritu", "Sanjay", "Nisha", "Abhishek", "Kavita", "Mahesh", "Sunita", "Rakesh", "Asha", "Imran", "Farah",
         "Joseph", "Sheetal", "Arjun", "Divya", "Ketan", "Hema", "Girish", "Bhavna"]
LAST = ["Kulkarni", "Patil", "Joshi", "Deshpande", "Bhosale", "Gokhale", "Kale", "Jadhav", "Pawar", "Shinde", "Sathe", "Apte", "More",
        "Chavan", "Shah", "Mehta", "Desai", "Iyer", "Nair", "Reddy", "Gupta", "Agarwal", "Sharma", "Verma", "Khan", "D'Souza", "Rao",
        "Menon", "Pillai", "Thakkar", "Bhatt", "Trivedi", "Naik", "Kamat", "Sawant"]
FIRMS = ["Developers", "Enterprises", "Traders", "Constructions", "Hospitality", "Industries", "Associates", "Retail", "Infra", "Foods"]


class Showcase:
    def __init__(self, d, owner: dict, business: dict, check: bool, use_ai: bool):
        self.d, self.owner, self.b, self.check, self.use_ai = d, owner, business, check, use_ai
        self.ws = owner["_id"]
        self.today = now().astimezone(IST).date()
        self.added: dict[str, list] = {}
        self.report: list[tuple[str, str]] = []
        self.c: httpx.AsyncClient | None = None
        self.names: list[str] = []
        self.pack: dict = {}

    # ── helpers ──
    def ago(self, days: float, hour: int | None = None) -> datetime:
        t = now() - timedelta(days=days)
        if hour is not None:
            t = t.astimezone(IST).replace(hour=hour, minute=rng.randint(0, 59), second=rng.randint(0, 59)).astimezone(t.tzinfo)
        return t

    def person(self) -> str:
        while True:
            n = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
            if n not in self.names:
                self.names.append(n)
                return n

    @staticmethod
    def phone() -> str:
        return f"+91{rng.choice('98765')}{rng.randint(100000000, 999999999)}"

    async def insert(self, coll: str, doc: dict) -> dict:
        doc.setdefault("_id", new_id())
        await self.d[coll].insert_one(doc)
        self.added.setdefault(coll, []).append(doc["_id"])
        return doc

    def has(self, feature: str) -> bool:
        return plans.has(self.owner, feature)

    async def api(self, method: str, path: str, ok=(200,), **kw):
        r = await self.c.request(method, path, **kw)
        if r.status_code not in ok:
            raise RuntimeError(f"{method} {path} → {r.status_code} {r.text[:160]}")
        return r.json() if r.headers.get("content-type", "").startswith("application/json") else r

    async def step(self, name: str, feature: str | None, fn):
        if feature and not self.has(feature):
            self.report.append((name, f"skipped: not on this plan ({plans.PLANS[plans.effective_plan(self.owner)]['name']})"))
            return
        if self.check:
            self.report.append((name, "would fill"))
            return
        try:
            note = await fn()
            self.report.append((name, note or "done"))
        except Exception as e:  # noqa: BLE001 — one feature that can't be filled never stops the rest
            self.report.append((name, f"not filled: {str(e)[:200]}"))

    # ── what this business sells, in its own words (one AI call; a plain fallback without AI) ──
    async def make_pack(self):
        offers = [o.get("name") for o in self.b.get("offers") or [] if o.get("name")] or [self.b.get("industry") or "our service"]
        fallback = {
            "inquiries": [f"Hi, I'd like to know the price of {o.lower()}." for o in offers] +
                         [f"Can you share details for {o.lower()}? Need it this month." for o in offers] +
                         ["Please call me back, need a quotation.", "Saw your website, what are your charges?", "Is a visit possible this week?"],
            "items": [{"name": o, "rate": 15000 + 5000 * i, "unit": "nos", "gst": 18} for i, o in enumerate(offers[:5])],
            "vendors": [{"name": "Shree Suppliers", "category": "purchases"}, {"name": "City Transport Co.", "category": "freight"}],
            "competitors": ["City Star " + (self.b.get("industry") or "Services").split()[0], "Prime Solutions", "Royal Enterprises"],
            "reviews": ["Very professional and on time. Recommended.", "Good work and fair pricing.", "Quick response on WhatsApp, happy with the service.",
                        "Took a little longer than promised but the result was good.", "Excellent team, clear quotation, no hidden charges."],
            "won_notes": ["Advance received", "Order confirmed on call", "PO received"],
            "lost_notes": ["Went with a cheaper quote", "Postponed to next quarter", "No response after visit"],
            "post_focus": f"{offers[0]} offers this month",
        }
        if not self.use_ai:
            self.pack = fallback
            return
        from app.ai import provider
        from app.ai.tasks import profile_text
        system = ("You write realistic sample business data for an Indian small business's CRM. Plain Indian English, the way real "
                  "customers type on WhatsApp. Never mention that it's sample data.")
        prompt = f"""The business:
{profile_text(self.b)}

Return JSON:
{{"inquiries": [24 different short messages real customers sent this business (WhatsApp/website style, 8-25 words, specific to what it sells,
               some with budgets, dates, sizes or areas of the city)],
 "items": [6 {{"name": "a line item it invoices", "rate": typical rupees per unit (number, before GST), "unit": "nos|kg|sq ft|month|visit|set|job", "gst": 5|12|18}}],
 "vendors": [6 {{"name": "a realistic local supplier or service it pays", "category": "purchases|freight|marketing|rent|electricity|phone_internet|packing|fuel|repairs"}}],
 "competitors": [3 realistic names of local competitors in the same trade and city (invented, not famous brands)],
 "reviews": [6 short Google-review texts from customers, 4 happy, 1 mixed, 1 unhappy about a delay],
 "won_notes": [4 short notes an owner writes when a deal is won], "lost_notes": [4 short notes when a deal is lost],
 "post_focus": "one line: what this business should post about this month"}}"""
        try:
            pack, _ = await provider.complete(system, prompt, lambda: fallback, 3000)
            self.pack = {k: pack.get(k) or v for k, v in fallback.items()}
        except Exception:  # noqa: BLE001
            self.pack = fallback

    # ── the pieces ──
    async def website(self):
        site = await self.d.sites.find_one({"owner_id": self.ws})
        if not site:
            return "no website yet: build it in the hub first, then run this again for site visits and website inquiries"
        n = 0
        for back in range(90):
            day_ = (self.today - timedelta(days=back)).isoformat()
            if await self.d.site_views.find_one({"_id": f"{site['_id']}:{day_}"}):
                continue
            trend = 18 + (90 - back) * 0.35            # visits grow as the owner shares the site and posts
            views = max(4, int(rng.gauss(trend, trend * 0.25)) + (8 if (self.today - timedelta(days=back)).weekday() in (5, 6) else 0))
            await self.insert("site_views", {"_id": f"{site['_id']}:{day_}", "site_id": site["_id"], "ws": self.ws, "day": day_, "n": views})
            n += views
        await self.d.sites.update_one({"_id": site["_id"]}, {"$inc": {"views": n}})
        self.site = site
        return f"{n:,} visits over 90 days"

    async def leads(self):
        site = await self.d.sites.find_one({"owner_id": self.ws})
        msgs = list(self.pack["inquiries"])
        items = self.pack["items"]
        sources = ["website"] * 9 + ["whatsapp"] * 7 + ["card"] * 2 + ["guide"] * 2 + ["google"] * 2
        stages = ["identification", "logic", "pain", "vision", "close"]
        made = {"won": 0, "lost": 0, "open": 0, "new": 0}
        for i in range(62):
            back = 89 * (1 - i / 62) ** 1.15 + rng.uniform(0, 1.5)       # more of them lately
            if i >= 59:                                                    # the last three came in today
                back = rng.uniform(0.05, 0.4)
            at = self.ago(back, hour=None if i >= 59 else rng.choice([9, 10, 11, 12, 13, 15, 16, 17, 18, 19, 20, 21]))
            value = round(rng.choice(items)["rate"] * rng.choice([1, 1, 2, 3, 4, 6]) * 1.18, -2)
            if i >= 59:
                status, stage = "new", "identification"
            elif back < 25 and made["open"] < 9:
                status, stage = "contacted", rng.choice(stages)
            else:
                status = "won" if rng.random() < 0.38 else "lost"
                stage = "close" if status == "won" else rng.choice(stages)
            made["won" if status == "won" else "lost" if status == "lost" else "new" if status == "new" else "open"] += 1
            doc = {"owner_id": self.ws, "site_id": site["_id"] if site else None, "name": self.person(), "phone": self.phone(),
                   "message": msgs[i % len(msgs)], "status": status, "stage": stage, "value": value if status != "lost" else 0,
                   "source": rng.choice(sources), "created_at": at, "updated_at": at + timedelta(hours=rng.uniform(2, 70))}
            if status != "new":
                doc["first_action_at"] = at + timedelta(minutes=rng.choice([4, 7, 9, 12, 18, 25, 40, 65]))
            if status == "won":
                doc["note"] = rng.choice(self.pack["won_notes"])
            elif status == "lost":
                doc["note"] = rng.choice(self.pack["lost_notes"])
            await self.insert("leads", doc)
        return f"62 inquiries: {made['won']} won, {made['open']} open deals, {made['lost']} lost, {made['new']} new today"

    async def customers(self):
        n = 0
        for i in range(36):
            firm = i % 3 == 0
            name = f"{rng.choice(LAST)} {rng.choice(FIRMS)}" if firm else self.person()
            rate = rng.choice(self.pack["items"])["rate"]
            purchases = rng.choice([1, 1, 2, 3, 4, 6, 9]) if not firm else rng.choice([4, 6, 8, 12])
            body = {"name": name, "phone": self.phone(), "total_value": round(rate * purchases * rng.uniform(1, 3), -2),
                    "last_purchase": str(self.today - timedelta(days=rng.randint(3, 260))), "purchases": purchases}
            r = await self.c.post("/api/customers", json=body)
            if r.status_code == 200:
                self.added.setdefault("customers", []).append(r.json().get("id"))
                n += 1
        return f"{n} customers"

    async def quotes(self):
        biz = self.pack["items"]
        made = []
        for i in range(14):
            cust = f"{rng.choice(LAST)} {rng.choice(FIRMS)}" if i % 2 else self.person()
            lines = rng.sample(biz, k=min(len(biz), rng.choice([1, 2, 3])))
            items = [{"name": x["name"], "qty": rng.choice([1, 2, 3, 5, 10]), "unit": x.get("unit") or "nos", "rate": x["rate"], "gst": x.get("gst", 18)} for x in lines]
            q = await self.api("POST", "/api/quotes", json={"customer": {"name": cust, "phone": self.phone()}, "items": items})
            self.added.setdefault("quotes", []).append(q["id"])
            made.append(q)
            back = rng.randint(2, 80)
            if i < 6:
                inv = await self.c.post(f"/api/quotes/{q['id']}/invoice", json={"due_in_days": 15})
                if inv.status_code == 200 and isinstance(inv.json(), dict) and inv.json().get("id"):
                    self.added["quotes"].append(inv.json()["id"])
                    await self.d.quotes.update_one({"_id": inv.json()["id"]}, {"$set": {"created_at": self.ago(back - 1), "updated_at": self.ago(back - 1)}})
            elif i < 11:
                await self.c.post(f"/api/quotes/{q['id']}/status", json={"status": "sent"})
                await self.d.quotes.update_one({"_id": q["id"]}, {"$set": {"sent_on": str(self.today - timedelta(days=min(back, 9)))}})
            await self.d.quotes.update_one({"_id": q["id"]}, {"$set": {"created_at": self.ago(back), "updated_at": self.ago(max(0, back - 2))}})
        return f"{len(made)} quotations, 6 turned into invoices"

    async def dues(self):
        open_n = 0
        for i in range(16):
            paid = i >= 6
            due_day = self.today + timedelta(days=rng.randint(-75, -5) if paid else rng.choice([-14, -6, -2, 3, 8, 15]))
            amount = round(rng.choice(self.pack["items"])["rate"] * rng.choice([1, 2, 3, 4]) * 1.18, -2)
            cust = f"{rng.choice(LAST)} {rng.choice(FIRMS)}" if i % 2 else self.person()
            # no e-mail and no phone: the payment follow-ups never write to an invented customer
            await self.insert("dues", {"ws": self.ws, "customer": cust, "phone": "", "email": "", "amount": int(amount * 100),
                                       "due_date": due_day.isoformat(), "invoice_no": f"INV-{2100 + i}", "note": "",
                                       "status": "paid" if paid else "due",
                                       "paid_at": datetime(due_day.year, due_day.month, due_day.day, 12, tzinfo=IST) + timedelta(days=rng.randint(-3, 12)) if paid else None,
                                       "reminders_sent": 0 if paid else rng.choice([0, 1, 2]), "followups": [], "auto_stopped": None,
                                       "created_at": self.ago((self.today - due_day).days + 20), "updated_at": now()})
            open_n += not paid
        return f"16 payments: {open_n} open (some overdue), 10 paid"

    async def expenses(self):
        month0 = self.today.replace(day=1)
        months = [month0, (month0 - timedelta(days=1)).replace(day=1), ((month0 - timedelta(days=1)).replace(day=1) - timedelta(days=1)).replace(day=1)]
        plan = [(1, "salaries", "Staff salaries", rng.choice([85000, 140000, 220000]), 0, "bank", True),
                (5, "rent", "Office rent", rng.choice([28000, 45000, 65000]), 18, "bank", True),
                (7, "electricity", "MSEDCL", rng.randint(6000, 14000), 0, "upi", False),
                (11, "phone_internet", "Airtel broadband", 2359, 18, "upi", True)]
        for i, v in enumerate(self.pack["vendors"][:6]):
            plan.append((rng.randint(3, 26), v.get("category") or "purchases", v["name"], rng.choice([4500, 8200, 12500, 26000, 48000]), 18, rng.choice(["bank", "upi", "card"]), False))
        n = 0
        for back, m in enumerate(months):
            for day_, cat, vendor, amount, rate, paid_by, rec in plan:
                when = m.replace(day=min(day_, 28))
                if when > self.today:
                    continue
                amt = round(amount * rng.uniform(0.9, 1.12), 0)
                r = await self.c.post("/api/expenses", json={"date": when.isoformat(), "amount": amt, "gst": round(amt * rate / (100 + rate), 2),
                                                             "gst_claimable": rate > 0, "category": cat, "vendor": vendor, "paid_by": paid_by, "recurring": rec})
                if r.status_code == 200:
                    n += 1
                    self.added.setdefault("expenses", []).append(r.json().get("id"))
        return f"{n} expenses over 3 months"

    async def sales_sheet(self):
        end = self.today.replace(day=1) - timedelta(days=1)
        start = (end.replace(day=1) - timedelta(days=330)).replace(day=1)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow([self.b.get("name", "")])
        w.writerow(["Sales Register"])
        w.writerow([f"{start:%d-%b-%Y} to {end:%d-%b-%Y}"])
        w.writerow([])
        w.writerow(["Date", "Particulars", "Phone", "Voucher Type", "Voucher No.", "Gross Total", "GST", "Due Date", "Paid On"])
        regulars = [(f"{rng.choice(LAST)} {rng.choice(FIRMS)}", rng.choice([30, 45, 60, 90]), rng.choice([0, 10, 25, 40]), q) for q in (None, None, None, 120, None, 90)]
        base = max(x["rate"] for x in self.pack["items"])
        n, rows = 0, []
        for name, every, late, quiet in regulars:
            day_ = start + timedelta(days=rng.randint(0, 25))
            stop = end - timedelta(days=quiet) if quiet else end
            while day_ <= stop:
                total = round(base * rng.uniform(1.5, 6), -2)
                n += 1
                due = day_ + timedelta(days=15)
                paid = due + timedelta(days=max(0, late + rng.randint(-8, 12)))
                rows.append((day_, name, f"INV/{n:04d}", total, due, paid if paid <= end else None))
                day_ += timedelta(days=every + rng.randint(-5, 6))
        for _ in range(140):
            day_ = start + timedelta(days=rng.randint(0, (end - start).days))
            total = round(base * rng.uniform(0.3, 1.6), -2)
            n += 1
            rows.append((day_, self.person(), f"INV/{n:04d}", total, day_, day_))
        rows.sort(key=lambda r: r[0])
        for day_, name, no, total, due, paid in rows:
            w.writerow([f"{day_:%d-%b-%Y}", name, "", "Sales", no, f"{total:.2f}", f"{total - total / 1.18:.2f}", f"{due:%d-%b-%Y}",
                        f"{paid:%d-%b-%Y}" if paid else ""])
        await self.api("POST", "/api/sales-upload", files={"file": ("sales-register.csv", buf.getvalue().encode(), "text/csv")})
        return f"12 months of sales, {len(rows)} invoices"

    async def money_setup(self):
        await self.c.put("/api/knowledge/money", json={"payment_terms_days": 15, "bank_balance": rng.choice([240000, 380000, 520000]),
                                                        "bank_balance_on": self.today.isoformat(), "gst_rate": 18, "safety_buffer": 50000,
                                                        "fixed_costs": [{"name": "Salaries", "amount": 140000, "day": 1, "kind": "salary"},
                                                                        {"name": "Office rent", "amount": 45000, "day": 5, "kind": "rent"}]})
        await self.c.post("/api/cash-flow/items", json={"kind": "out", "title": f"Supplier payment — {self.pack['vendors'][0]['name']}",
                                                         "amount": 160000, "date": (self.today + timedelta(days=9)).isoformat()})
        return "bank balance, fixed costs and an upcoming supplier payment"

    async def score(self):
        keys = ("focus", "margins", "sales", "payments", "offers", "reply", "followup", "content", "process", "numbers")
        first = {k: rng.choice(["no", "partly", "partly"]) for k in keys}
        await self.api("POST", "/api/score", json={"answers": first})
        old = await self.d.scores.find_one({"ws": self.ws}, sort=[("at", -1)])
        if old:
            await self.d.scores.update_one({"_id": old["_id"]}, {"$set": {"at": self.ago(74)}})
        mid = {k: ("yes" if i < 5 else "partly") for i, k in enumerate(keys)}
        await self.c.post("/api/score", json={"answers": mid})
        newer = await self.d.scores.find_one({"ws": self.ws}, sort=[("at", -1)])
        if newer:
            await self.d.scores.update_one({"_id": newer["_id"]}, {"$set": {"at": self.ago(38)}})
        await self.c.post("/api/score", json={"answers": {**{k: "yes" for k in keys}, "content": "partly", "numbers": "partly"}})
        return "three Business Scores over 10 weeks, climbing"

    async def rhythm(self):
        out = []
        if self.has("week"):
            await self.c.post("/api/week/plan")
            await self.c.post("/api/week/actions/0")
            await self.c.post("/api/week/today/done")
            out.append("this week's plan")
        if self.has("content_calendar"):
            month = self.today.strftime("%Y-%m")
            cal = (await self.api("POST", "/api/calendar", json={"month": month, "focus": self.pack["post_focus"]}))["calendar"]
            posted = 0
            for p in cal.get("posts", []):
                if p["date"] <= self.today.isoformat():
                    await self.c.post("/api/social/posted", json={"ref": f"cal:{month}:{p['date']}", "platform": ["Instagram", "Facebook", "WhatsApp Status"][posted % 3]})
                    posted += 1
            out.append(f"a month of posts, {posted} posted")
        return ", ".join(out) or "nothing on this plan"

    async def growth_numbers(self):
        out = []
        avg = round(sum(x["rate"] for x in self.pack["items"]) / len(self.pack["items"]) * 2.5, -2)
        if self.has("magic_number"):
            await self.c.put("/api/magic", json={"monthly_target": round(avg * 22, -4), "avg_sale": avg, "close_rate": 32, "meeting_rate": 60})
            out.append("monthly target")
        if self.has("funnel_leaks"):
            await self.c.put("/api/leaks", json={"numbers": {"visitors": 1850, "inquiries": 62, "meetings": 21, "proposals": 14, "won": 9}, "avg_sale": avg})
            out.append("funnel")
        for f, path, body in (("revenue_levers", "/api/levers", {"customers": 140, "avg_value": avg, "frequency": 2.4, "pledge": "10×10×10 by March"}),):
            if self.has(f):
                await self.c.put(path, json=body)
                out.append("levers")
        return ", ".join(out) or "nothing on this plan"

    async def brand(self):
        out = []
        if self.has("brand_kit"):
            await self.c.post("/api/brand-kit/generate")
            out.append("brand kit")
        if self.has("lead_magnet"):
            await self.c.post("/api/lead-magnet/generate", json={"problem": f"Choosing the right {self.pack['items'][0]['name'].lower()} without overpaying", "format": "checklist"})
            await self.c.post("/api/lead-magnet/publish", json={"published": True})
            out.append("lead magnet on the website")
        if self.has("offer_ladder"):
            await self.c.post("/api/offers/generate", json={"notes": "", "entry": "A free first consultation"})
            await self.c.post("/api/offers/show", json={"show": True})
            out.append("offer ladder")
        return ", ".join(out) or "nothing on this plan"

    async def systems(self):
        out = []
        if self.has("sop_library"):
            await self.c.post("/api/sop-set/map")
            keys = [p["key"] for p in (await self.api("GET", "/api/sop-set"))["processes"][:10]]
            await self.c.post("/api/sop-set/write", json={"keys": keys})
            from app.routers import sopset
            await sopset.settle()
            out.append(f"{len(keys)} SOPs")
        if self.has("compliance_calendar"):
            await self.c.get("/api/compliance")
            out.append("compliance calendar")
        return ", ".join(out) or "nothing on this plan"

    async def reputation(self):
        out = []
        if self.has("reputation"):
            for i, text in enumerate(self.pack["reviews"][:6]):
                rating = 5 if i < 4 else 3 if i == 4 else 2
                await self.c.post("/api/reputation/reviews", json={"platform": "Google", "author": self.person(), "rating": rating, "text": text})
            out.append("6 reviews")
        if self.has("competitor_watch"):
            for name in self.pack["competitors"][:3]:
                await self.c.post("/api/competitors", json={"name": name, "facebook_name": name, "notes": ""})
            out.append("3 competitors")
        if self.has("win_back"):
            await self.c.put("/api/win-back/settings", json={"days": 60, "min_purchases": 2, "per_week": 10, "channels": {"whatsapp": True, "email": False}})
            out.append("win-back")
        return ", ".join(out) or "nothing on this plan"

    async def automations(self):
        """The customer-facing agents on, in "ask first", with three months of their work in the activity log and in Approvals."""
        from app.agents import catalog
        on = []
        for spec in catalog.AGENTS.values():
            # only what waits for the owner's OK, or only ever tells the owner: nothing that would contact the owner's real
            # future customers on its own (instant replies, the AI telecaller) is switched on by this script
            if spec.info_only or not self.has(spec.feature) or (spec.customer_facing and not spec.approvals):
                continue
            r = await self.c.patch(f"/api/agents/{spec.key}", json={"on": True})
            if r.status_code == 200:
                on.append(spec)
        won = [x async for x in self.d.leads.find({"owner_id": self.ws, "_id": {"$in": self.added.get("leads", [])}})]
        lines = 0
        for lead in won:
            if lead["status"] == "new":
                continue
            at = lead["created_at"]
            for n, gap in enumerate((1, 3, 7)):
                when = at + timedelta(days=gap, hours=rng.uniform(0, 3))
                if when > now() - timedelta(hours=2) or (lead["status"] in ("won", "lost") and gap > 3 and rng.random() < 0.6):
                    break
                first = lead["name"].split()[0]
                text = rng.choice([f"Hi {first}, just checking if you had a chance to look at the details we shared. Happy to answer any questions.",
                                   f"Hello {first}, following up on your inquiry. Shall we fix a time to discuss?",
                                   f"Hi {first}, we have a slot this week if you'd like to go ahead. Let me know a good time to call."])
                decided = when + timedelta(minutes=rng.randint(6, 180))
                await self.insert("approvals", {"ws": self.ws, "agent": "followup", "kind": "followup", "title": f"Follow-up {n + 1} to {lead['name']}",
                                                "to_name": lead["name"], "to_phone": lead["phone"], "text": text, "params": [], "lead_id": lead["_id"],
                                                "status": "sent", "via": "own", "created_at": when, "decided_at": decided, "decided_by": self.ws})
                await self.insert("agent_log", {"ws": self.ws, "agent": "followup", "text": f"Follow-up {n + 1} written for {lead['name']} — sent after your OK",
                                                "status": "done", "at": decided, "ref": lead["_id"]})
                lines += 1
        for back in range(1, 85 if self.has("digest_agent") else 1):
            day_ = self.ago(back, hour=9)
            if day_.astimezone(IST).weekday() == 6:
                continue
            await self.insert("agent_log", {"ws": self.ws, "agent": "digest", "text": "Morning summary sent to you on WhatsApp", "status": "done",
                                            "at": day_, "ref": None})
        pending = [x for x in won if x["status"] == "contacted"][:3]
        for lead in pending:
            await self.insert("approvals", {"ws": self.ws, "agent": "followup", "kind": "followup", "title": f"Follow-up to {lead['name']}",
                                            "to_name": lead["name"], "to_phone": lead["phone"],
                                            "text": f"Hi {lead['name'].split()[0]}, following up on the quotation we sent. Would you like to go ahead this week?",
                                            "params": [], "lead_id": lead["_id"], "status": "pending", "created_at": self.ago(0.1)})
        return f"{len(on)} automations on (ask first), {lines} follow-ups sent over 3 months, 3 waiting for your OK"

    async def office(self):
        out = []
        if self.has("agentic_office"):
            for instruction in ("Follow up with every lead that's still waiting", "Remind customers who owe us money", "Write next week's posts"):
                run = await self.api("POST", "/api/office/runs", json={"instruction": instruction})
                for _ in range(120):
                    if run.get("status") != "running":
                        break
                    await asyncio.sleep(0.5)
                    run = await self.api("GET", f"/api/office/runs/{run['id']}")
            await self.c.post("/api/office/standup")
            out.append("3 AI office runs and a standup")
        if self.has("goals"):
            q_start = self.today.replace(day=1)
            avg = round(sum(x["rate"] for x in self.pack["items"]) / len(self.pack["items"]) * 2.5, -2)
            for g in ({"title": "Win 15 new customers this quarter", "metric": "won", "target": 15, "unit": "customers"},
                      {"title": "60 inquiries this month", "metric": "leads", "target": 60, "unit": "inquiries"},
                      {"title": "Revenue this quarter", "metric": "custom", "target": round(avg * 60, -4), "unit": "₹"}):
                goal = await self.api("POST", "/api/goals", json={**g, "start": str(q_start), "end": str(q_start + timedelta(days=89))})
                if g["metric"] == "custom":
                    await self.c.post(f"/api/goals/{goal['id']}/checkins", json={"value": round(avg * 38, -4), "note": "On track after a strong month"})
            out.append("3 goals")
        return ", ".join(out) or "nothing on this plan"

    async def run(self):
        token = jwt.encode({"sub": self.ws, "v": self.owner.get("session_version", 0), "exp": now() + timedelta(hours=2)}, _secret(), algorithm="HS256")
        async with LifespanManager(app):
            self.c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver", cookies={COOKIE: token}, timeout=300)
            if not self.check:
                await self.make_pack()
            else:
                self.pack = {"items": [{"rate": 1}]}
            await self.step("Website visits", None, self.website)
            await self.step("Inquiries and pipeline", None, self.leads)
            await self.step("Customers", "customers", self.customers)
            await self.step("Quotations and invoices", "quotations", self.quotes)
            await self.step("Money owed", "money_owed", self.dues)
            await self.step("Expenses", "expenses", self.expenses)
            await self.step("A year of sales", "customer_risk", self.sales_sheet)
            await self.step("Cash flow", "cash_flow", self.money_setup)
            await self.step("Business Score", None, self.score)
            await self.step("Week and posts", None, self.rhythm)
            await self.step("Targets and funnel", None, self.growth_numbers)
            await self.step("Brand", None, self.brand)
            await self.step("SOPs and compliance", None, self.systems)
            await self.step("Reviews, competitors, win-back", None, self.reputation)
            await self.step("Automations", "approvals", self.automations)
            await self.step("AI office and goals", None, self.office)
            await self.c.aclose()


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phone", required=True)
    ap.add_argument("--check", action="store_true", help="only say what would be filled")
    ap.add_argument("--no-ai", action="store_true", help="plain wording instead of one AI call for the business's own words")
    a = ap.parse_args()
    d = connect()
    phone = normalise_phone(a.phone)
    owner = await d.users.find_one({"phone": phone})
    if not owner:
        sys.exit(f"No account with {phone} in database {settings.db_name}.")
    if owner.get("team_of"):
        sys.exit("That number is a team member's login; give the owner's number.")
    business = await d.businesses.find_one({"owner_id": owner["_id"]})
    if not business or not business.get("name"):
        sys.exit("This account has no business profile yet: fill it in the hub first (name, what it sells, city).")
    await plans.load()
    print(f"{business['name']} · {owner.get('name') or phone} · plan: {plans.PLANS[plans.effective_plan(owner)]['name']} · database {settings.db_name}")
    s = Showcase(d, owner, business, a.check, not a.no_ai)
    await s.run()
    for name, note in s.report:
        print(f"  {name}: {note}")
    if not a.check:
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"showcase-{phone.lstrip('+')}-{date.today()}.json")
        try:
            with open(out, "w") as f:
                json.dump({"phone": phone, "at": now().isoformat(), "added": s.added}, f, indent=1, default=str)
            print(f"List of what was added: {out}")
        except OSError:
            print(json.dumps({"added": {k: len(v) for k, v in s.added.items()}}))


if __name__ == "__main__":
    asyncio.run(main())
