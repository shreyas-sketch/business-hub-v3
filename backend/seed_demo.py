"""Creates a demo hub: one workshop cohort and an owner on each of the six plans, with the v2 features filled in —
Free (Mehta Engineering), Membership (Kapoor Dental: week, calendar, score, gaps), Action Program (Arora Traders),
Running the Business (Patel Sweets: customers, quotations, invoices), Growth Mentorship (Sharma Modular Kitchens:
AI staff, WhatsApp chats, Telecaller, campaigns, Company Brain) and LegacyWorkforce (Shree Ganesh Interiors: everything,
30 AI staff, an AI office run, a team, goals, documents, money owed, meetings) — plus test payments that earn referral
commissions, a member call and recordings.
Run with AI_PROVIDER=mock (default). Admin login: the first number in ADMIN_PHONES. Codes appear on the login screen in development."""
import asyncio
import os
from datetime import timedelta

os.environ.setdefault("APP_ENV", "development")  # the demo only runs locally: it reads login codes from the dev outbox
os.environ.setdefault("APP_URL", "http://localhost:8000")
os.environ.setdefault("ADMIN_PHONES", "+919999900000")
import httpx  # noqa: E402
from asgi_lifespan import LifespanManager  # noqa: E402

from app.db import IST, connect, now  # noqa: E402
from app.main import app  # noqa: E402

BASE = os.getenv("APP_URL", "http://localhost:8000")


async def owner(phone, **extra):
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)
    code = (await c.post("/api/auth/otp", json={"phone": phone})).json()["dev_code"]
    await c.post("/api/auth/verify", json={"phone": phone, "code": code, **extra})
    return c


async def live(c, profile, pick=0):
    await c.put("/api/business", json=profile)
    opts = (await c.post("/api/brand/generate")).json()["options"]
    await c.put("/api/brand", json=opts[pick])
    site = (await c.post("/api/site/generate")).json()
    await c.put("/api/site", json={"content": site["content"], "accent": profile.get("_accent", site["accent"]), "showcase": True})
    await c.post("/api/site/publish")
    return (await c.get("/api/site")).json()


async def main():
    d = connect()
    for n in await d.list_collection_names():
        await d[n].delete_many({})
    async with LifespanManager(app):
        admin = await owner("+919999900000")
        await admin.post("/api/admin/cohorts", json={"code": "BAI-OCT-11", "name": "Business AI workshop · 11 Oct", "workshop_date": "2026-10-11"})
        rakesh = await owner("9820000001", cohort="BAI-OCT-11")
        site = await live(rakesh, {
            "name": "Shree Ganesh Interiors", "city": "Thane & Navi Mumbai", "industry": "Home and office interiors with our own carpenters",
            "offers": [{"name": "Modular kitchen", "price": "₹1,85,000", "unit": "onwards"}, {"name": "2BHK full interiors", "price": "₹6,50,000", "unit": "onwards"},
                       {"name": "Office fit-out", "price": "₹1,800", "unit": "per sq ft"}, {"name": "Site survey and 3D design", "price": "", "unit": ""}],
            "ideal_customer": "Families who just got possession of a 2/3BHK", "why_us": "Fixed timelines, written in the quote\nTransparent itemised quotation\nOur own factory and carpenters",
            "proof": "", "whatsapp": "98200 00001", "email": "hello@shreeganesh.in", "language": "English", "_accent": "#1F4E79"})
        for name, phone, msg in [("Neha Joshi", "98330 00011", "Got possession of a 2BHK in Ghodbunder Road. Need full interiors, budget around 7 lakh."),
                                 ("Vikram Shah", "98440 00012", "Office fit-out 2,200 sq ft in Andheri East. Please share rate."),
                                 ("Mrs. Kulkarni", "98550 00013", "Only modular kitchen, 10 ft wall.")]:
            await rakesh.post(f"/s/{site['slug']}/inquiry", json={"name": name, "phone": phone, "message": msg}, headers={"x-forwarded-for": f"10.0.0.{len(name)}"})
        await rakesh.post("/api/studio/posts", json={"focus": "Diwali kitchen bookings"})
        ref = (await rakesh.get("/api/me")).json()["invite_link"].split("ref=")[1].split("&")[0]
        kapoor = await owner("9820000002", ref=ref, src="badge")
        await live(kapoor, {"name": "Kapoor Dental Clinic", "city": "Thane West", "industry": "Family dental clinic — check-ups, braces, implants",
                            "offers": [{"name": "Check-up and cleaning", "price": "₹800", "unit": ""}, {"name": "Clear aligners", "price": "", "unit": ""}],
                            "ideal_customer": "Families in Thane West", "why_us": "Same-day appointments\nPain-free treatment\nOpen on Sundays", "whatsapp": "98200 00002",
                            "language": "English", "_accent": "#0F766E"})
        arora = await owner("9820000003", ref=ref, src="invite")
        await arora.put("/api/business", json={"name": "Arora Traders", "city": "Surat", "industry": "Wholesale textile trading", "whatsapp": "98200 00003"})
        mehta = await owner("9820000004", cohort="BAI-OCT-11")
        await live(mehta, {"name": "Mehta Engineering Works", "city": "Surat", "industry": "Industrial pumps and spare parts — sales and service",
                           "offers": [{"name": "Monoblock pumps", "price": "", "unit": ""}, {"name": "Annual maintenance contract", "price": "₹12,000", "unit": "per year"}],
                           "ideal_customer": "Factories and housing societies in Surat", "why_us": "Service engineer within 24 hours\nGenuine spares in stock\n30 years in the trade",
                           "whatsapp": "98200 00004", "language": "English", "_accent": "#7C2D12"})
        # ── Paid plans, bought through the test checkout (they earn Shree Ganesh 30% commissions) ──
        for c, tier in ((kapoor, "lite"), (arora, "program")):
            pay = (await c.post("/api/billing/checkout", json={"tier": tier})).json()
            await c.post("/api/billing/dev-complete", json={"payment_id": pay["payment_id"]})
        await kapoor.post("/api/site/domain", json={"domain": "www.kapoordental.in"})  # waits in Admin → Own domains

        # ── Shree Ganesh Interiors runs the whole hub: LegacyWorkforce ──
        uid = (await rakesh.get("/api/me")).json()["user"]["id"]
        await admin.patch(f"/api/admin/users/{uid}", json={"plan": "office"})
        await d.users.update_one({"_id": uid}, {"$set": {"name": "Rakesh Patil"}})
        biz = (await rakesh.get("/api/business")).json()
        biz.update(review_link="https://g.page/r/shree-ganesh-interiors/review", payment_note="Pay by UPI to shreeganesh@upi or bank transfer")
        await rakesh.put("/api/business", json={k: v for k, v in biz.items() if k not in ("id", "owner_id", "created_at", "updated_at", "brand", "brand_options")})
        team = {}
        for phone, name, role in (("9820000011", "Priya Nair", "manager"), ("9820000012", "Suresh Yadav", "staff")):
            await rakesh.post("/api/team/invites", json={"phone": phone, "name": name, "role": role})
            team[name] = await owner(phone)
        people = {p["name"]: p["id"] for p in (await rakesh.get("/api/team/people")).json()}
        today = now().astimezone(IST).date()
        q_start, q_end = today.replace(day=1), (today.replace(day=1) + timedelta(days=89))
        for g in ({"title": "Win 12 interiors projects this quarter", "metric": "won", "target": 12, "unit": "projects"},
                  {"title": "40 website inquiries this month", "metric": "leads", "target": 40, "unit": "inquiries"},
                  {"title": "₹25 lakh revenue this quarter", "metric": "custom", "target": 2500000, "unit": "₹"}):
            goal = (await rakesh.post("/api/goals", json={**g, "start": str(q_start), "end": str(q_end)})).json()
            if g["metric"] == "custom":
                await rakesh.post(f"/api/goals/{goal['id']}/checkins", json={"value": 1450000, "note": "Two kitchens and one 2BHK billed"})
        for kind, inputs in (("sop", {"process": "Site measurement visit", "notes": "Confirm the time on WhatsApp the day before\nCarry the laser meter and the checklist\nMeasure every wall, window and switchboard\nPhotograph each room\nShare the measurement sheet with the designer the same day"}),
                             ("jd", {"role": "Site supervisor", "notes": "Thane and Navi Mumbai sites, two-wheeler needed"}),
                             ("decision", {"tasks": "Reply to every WhatsApp inquiry\nMake quotations\nVisit sites for measurement\nFollow up on pending payments\nPost on Instagram\nNegotiate with plywood suppliers"}),
                             ("role", {"role": "Telecaller", "person_name": "Priya Nair", "function": "Sales"}),
                             ("culture", {"notes": "Deliveries get delayed and nobody tells the customer\nCarpenters and designers blame each other for mistakes"}),
                             ("competence", {"role": "Site supervisor"})):
            await rakesh.post(f"/api/kits/{kind}", json={"inputs": inputs})
        for due in ({"customer": "Mr. Desai", "phone": "98660 00021", "amount": 45000, "due_date": str(today - timedelta(days=10)), "invoice_no": "SG-1042", "note": "Kitchen — final 10%"},
                    {"customer": "Anita Rao", "phone": "98660 00022", "amount": 120000, "due_date": str(today + timedelta(days=6)), "invoice_no": "SG-1047", "note": "2BHK — second instalment"},
                    {"customer": "Joseph D'Souza", "phone": "98660 00023", "amount": 35000, "due_date": str(today - timedelta(days=3)), "invoice_no": "SG-1039"}):
            await rakesh.post("/api/money", json=due)
        meeting = (await rakesh.post("/api/meetings", json={"type": "tactical", "date": str(today), "attendees": list(people.values())})).json()
        await rakesh.patch(f"/api/meetings/{meeting['id']}", json={"notes": {
            "last_goals": "Closed 2 kitchens. Andheri office quote still pending.",
            "checkins": "Priya: called all new leads within the hour. Suresh: Kulkarni kitchen installation on track.",
            "next_goals": "Priya to send the Andheri revised quote by Friday. Suresh to finish the Ghodbunder site measurement by Thursday.",
            "concerns": "Plywood delivery delays — Rakesh to call the supplier tomorrow."}})
        await rakesh.post(f"/api/meetings/{meeting['id']}/actions")
        await rakesh.post("/api/tasks", json={"title": "Order hinges and channels for the Kulkarni kitchen", "assignee_id": people.get("Suresh Yadav"), "due": str(today + timedelta(days=1))})
        # leads have been waiting two days, so the office has follow-ups to write
        await d.leads.update_many({"owner_id": uid}, {"$set": {"created_at": now() - timedelta(days=2)}})
        await d.leads.update_one({"owner_id": uid, "name": "Vikram Shah"}, {"$set": {"status": "won", "value": 640000, "updated_at": now() - timedelta(days=1),
                                                                             "first_action_at": now() - timedelta(days=1)}})
        for instruction in ("Follow up with every lead that's still waiting", "Remind customers who owe us money",
                            "Write next week's posts about our Diwali kitchen offer"):
            run = (await rakesh.post("/api/office/runs", json={"instruction": instruction})).json()
            for _ in range(60):  # the office works in the background
                if run["status"] != "running":
                    break
                await asyncio.sleep(0.25)
                run = (await rakesh.get(f"/api/office/runs/{run['id']}")).json()
        await rakesh.post("/api/office/standup")
        await rakesh.post("/api/agents/monthly_review/run")

        site_doc = await d.sites.find_one({"owner_id": uid})
        for back, n in enumerate([46, 38, 52, 41, 35, 29, 44, 39, 31, 48, 36, 27, 33, 40]):
            day_ = str(today - timedelta(days=back))
            await d.site_views.update_one({"_id": f"{site_doc['_id']}:{day_}"}, {"$set": {"site_id": site_doc["_id"], "ws": uid, "day": day_, "n": n}}, upsert=True)

        # ── Recordings: programs → sections → call recordings (sample public videos; replace with your own links) ──
        sample_vimeo, sample_yt = "https://vimeo.com/76979871", "https://youtu.be/aqz-KE-bpKQ"
        ago = lambda n: str(today - timedelta(days=n))  # noqa: E731  recordings of calls that already happened
        programs = [
            ("Business AI Workshop · 2-day recordings", "Both days of the last workshop, session by session.", "free", True, [
                ("Day 1", [("Session 1 · Structure: your Business Brain", sample_vimeo, ago(27), 74,
                            "What we covered:\n• The four questions of the Business Brain\n• Why your brand message comes first\n\nHomework: finish your Business Brain tonight."),
                           ("Session 2 · Systems: a website that brings leads", sample_yt, ago(27), 81, "Publish your website and share it on WhatsApp Status before Day 2.")]),
                ("Day 2", [("Session 3 · Scale: AI that writes for you", sample_vimeo, ago(26), 68, ""),
                           ("Session 4 · Your 90-day plan", sample_yt, ago(26), 55, "Download the 90-day planner: https://example.com/90-day-planner.pdf")]),
            ]),
            ("Action Program · October batch", "Weekly live calls of the Action Program, with notes and homework.", "program", False, [
                ("Week 1 · Kickstart", [("Kickstart call · systems that follow up", sample_vimeo, ago(20), 92,
                                         "Key points:\n1. Every lead gets a reply within the hour\n2. Follow up on day 1, 3 and 7\n3. Decide: automate, delegate or keep\n\nHomework: run the decision log on this week's tasks.")]),
                ("Live Q&A calls", [("Q&A call 1", sample_yt, ago(13), 63, ""), ("Q&A call 2", sample_vimeo, ago(6), 58, "")]),
            ]),
            ("Growth Mentorship · Team calls", "Role clarity, culture and meetings — calls to watch with your team.", "growth", True, [
                ("Module 1 · Role clarity", [("Writing role clarity for every person", sample_vimeo, ago(18), 88, "")]),
            ]),
            ("LegacyWorkforce · Strategy calls", "Monthly strategy calls on running the business with the AI office.", "office", False, [
                ("Strategy calls", [("October strategy call", sample_yt, ago(4), 95, "Agenda: the AI office's first month, what to automate next.")]),
            ]),
        ]
        for title, desc, tier, team_ok, sections in programs:
            prog = (await admin.post("/api/admin/programs", json={"title": title, "description": desc, "tier": tier, "team": team_ok, "published": True})).json()
            for s_title, recs in sections:
                sec = (await admin.post(f"/api/admin/programs/{prog['id']}/sections", json={"title": s_title})).json()
                for r_title, link, day_, mins, notes in recs:
                    res = [{"label": "Slides (PDF)", "url": "https://example.com/slides.pdf"}] if notes else []
                    await admin.post("/api/admin/recordings", json={"section_id": sec["id"], "title": r_title, "link": link, "recorded_on": day_,
                                                                    "duration_min": mins, "notes": notes, "resources": res})
        first = (await rakesh.get("/api/recordings")).json()[0]
        detail = (await rakesh.get(f"/api/recordings/{first['id']}")).json()
        for rec in [r for s in detail["sections"] for r in s["recordings"]][:3]:
            await rakesh.post(f"/api/recordings/item/{rec['id']}/watched", json={"watched": True})

        extra = await v2_demo(d, admin, rakesh, uid, site, kapoor, arora, mehta, today)
        extra.append(await v21_demo(d, admin, today))
        for c in (admin, rakesh, kapoor, arora, mehta, *team.values(), *extra):
            await c.aclose()
    print("Demo ready. LegacyWorkforce: 9820000001 (team: 9820000011 manager, 9820000012 staff) · Growth Mentorship: 9820000006 · "
          "Running the Business: 9820000005 · Action Program: 9820000003 · Membership: 9820000002 · Free: 9820000004 · "
          "AI Advantage launch demo (all v2.1 features): 9820000007 · "
          "Admin: 9999900000 · Website: /s/" + site["slug"])

async def wait_for(check, tries=40):
    for _ in range(tries):
        if await check():
            return True
        await asyncio.sleep(0.25)
    return False


async def hook(c, path, payload):
    return await c.post(path, json=payload)


def wa(frm, name, text, mid):
    return {"entry": [{"changes": [{"value": {"contacts": [{"wa_id": frm, "profile": {"name": name}}],
                                              "messages": [{"from": frm, "id": mid, "type": "text", "text": {"body": text}}]}}]}]}


async def v2_demo(d, admin, rakesh, uid, site, kapoor, arora, mehta, today):
    """Fills the v2 features for every plan, so each page has something to show."""
    from app import connections
    month = today.strftime("%Y-%m")
    score_yes = {k: "yes" for k in ("focus", "margins", "sales", "payments", "offers", "reply", "followup", "content", "process", "numbers")}

    # ── Free: the Business Score and the launch kit's website ──
    await mehta.post("/api/score", json={"answers": {**score_yes, "followup": "no", "payments": "partly", "content": "no", "numbers": "partly"}})

    # ── Membership: the weekly rhythm, the calendar, the Magic Number and the Gaps scan ──
    await kapoor.post("/api/score", json={"answers": {**score_yes, "followup": "partly", "content": "no"}})
    await kapoor.post("/api/week/plan")
    await kapoor.post("/api/week/actions/0")
    await kapoor.post("/api/week/today/done")
    await kapoor.post("/api/calendar", json={"month": month, "focus": "Diwali smile check-ups"})
    await kapoor.put("/api/magic", json={"monthly_target": 600000, "avg_sale": 6000, "close_rate": 40, "meeting_rate": 70})
    gaps = (await kapoor.get("/api/gaps")).json()["questions"]
    await kapoor.post("/api/gaps", json={"answers": {q["key"]: (2 if q["area"] in ("Payments", "Reach") else 4) for q in gaps}})
    await kapoor.post("/api/tools/google/run", json={"inputs": {"reviews": "Very gentle with my daughter. — Sneha", "focus": "more families from Thane West"}})

    # ── Action Program: offers, levers, end goals ──
    await arora.post("/api/offers/generate", json={"notes": "Wholesale sarees and dress material for retailers", "entry": "A sample bundle"})
    await arora.put("/api/levers", json={"customers": 180, "avg_value": 42000, "frequency": 6, "pledge": "10×10×10 by March: 198 retailers, ₹46,200 a bill, 6.6 orders a year"})
    await arora.put("/api/end-goals", json={"identity": {"text": "The most trusted textile wholesaler in Surat for small retailers", "by": "2030"},
                                            "income": {"text": "₹4 lakh a month to the family", "by": "2028"}})

    # ── Running the Business: customers, quotations and an invoice ──
    patel = await owner("9820000005", cohort="BAI-OCT-11")
    await live(patel, {"name": "Patel Sweets & Farsan", "city": "Ahmedabad", "industry": "Sweets, farsan and festive gift boxes",
                               "offers": [{"name": "Diwali gift box (1 kg)", "price": "₹1,200", "unit": "per box"}, {"name": "Corporate gifting", "price": "", "unit": ""}],
                               "ideal_customer": "Families and companies in Ahmedabad", "why_us": "Pure ghee\nSame-day delivery in Ahmedabad\n40 years old",
                               "whatsapp": "98200 00005", "language": "English", "_accent": "#9A3412"})
    puid = (await patel.get("/api/me")).json()["user"]["id"]
    await admin.patch(f"/api/admin/users/{puid}", json={"plan": "running"})
    for name, phone, value, last, extra in (("Infinity Tech Pvt Ltd", "98250 10001", 240000, str(today - timedelta(days=330)), {"reorder_days": 365, "last_item": "Diwali boxes for staff"}),
                                            ("Mrs. Shah", "98250 10002", 18000, str(today - timedelta(days=12)), {"occasion": {"label": "anniversary", "date": (today + timedelta(days=3)).strftime("%m-%d")}}),
                                            ("Desai family", "98250 10003", 9500, str(today - timedelta(days=95)), {"purchases": 4}),
                                            ("Hotel Riverview", "98250 10004", 64000, str(today - timedelta(days=28)), {"reorder_days": 30, "last_item": "farsan for the buffet"}),
                                            ("Mr. Trivedi", "98250 10005", 3200, str(today - timedelta(days=9)), {})):
        await patel.post("/api/customers", json={"name": name, "phone": phone, "total_value": value, "last_purchase": last, "purchases": 2, **extra})
    await patel.put("/api/quotes/settings", json={"gstin": "24ABCDE1234F1Z5", "address": "CG Road, Ahmedabad", "payment": "UPI patelsweets@okhdfc",
                                                  "terms": "50% advance with the order. Delivery within Ahmedabad included."})
    q1 = (await patel.post("/api/quotes", json={"customer": {"name": "Infinity Tech Pvt Ltd", "phone": "98250 10001", "gstin": "24AAACI1234Q1Z2"},
                                                "items": [{"name": "Diwali gift box (1 kg)", "qty": 180, "unit": "box", "rate": 1100, "gst": 5},
                                                          {"name": "Branded sleeve printing", "qty": 180, "unit": "nos", "rate": 40, "gst": 18}]})).json()
    await patel.post(f"/api/quotes/{q1['id']}/status", json={"status": "sent"})
    q2 = (await patel.post("/api/quotes", json={"customer": {"name": "Hotel Riverview", "phone": "98250 10004"},
                                                "items": [{"name": "Mixed farsan", "qty": 40, "unit": "kg", "rate": 520, "gst": 5}]})).json()
    await patel.post(f"/api/quotes/{q2['id']}/invoice", json={"due_in_days": 10})
    await d.quotes.update_one({"_id": q1["id"]}, {"$set": {"sent_on": str(today - timedelta(days=2))}})
    from app.agents import desk
    powner = await d.users.find_one({"_id": puid})
    for runner in (desk.quote_followup, desk.customer_desk):   # what the 11am automations would prepare today
        try:
            await runner(powner, {"at": now(), "job": {}})
        except Exception:  # noqa: BLE001  a Skip when there is nothing to prepare
            pass

    # ── Growth Mentorship: the 6 AI staff on the owner's own accounts (demo connections write to the outbox) ──
    sharma = await owner("9820000006", cohort="BAI-OCT-11")
    ssite = await live(sharma, {"name": "Sharma Modular Kitchens", "city": "Pune", "industry": "Modular kitchens and wardrobes, made in our Pune factory",
                                "offers": [{"name": "Modular kitchen", "price": "₹2,10,000", "unit": "onwards"}, {"name": "Wardrobes", "price": "₹55,000", "unit": "onwards"}],
                                "ideal_customer": "Families moving into new flats in Pune", "why_us": "15 years of trusted work\nOwn factory\n10-year hardware warranty",
                                "whatsapp": "98200 00006", "language": "English", "_accent": "#7A1F1F"})
    suid = (await sharma.get("/api/me")).json()["user"]["id"]
    await admin.patch(f"/api/admin/users/{suid}", json={"plan": "growth"})
    await admin.put(f"/api/admin/owners/{suid}/connections/whatsapp", json={"values": {
        "provider": "demo", "number": "98200 00006", "tpl_instant": "instant_reply_v1", "tpl_followup": "followup_v1", "tpl_review": "review_v1",
        "tpl_reminder": "payment_reminder_v1", "tpl_customer": "customer_v1", "tpl_campaign": "campaign_v1"}})
    await d.connections.update_one({"_id": suid}, {"$set": {"voice": {"provider": "demo", "hours": "10-19", "webhook_token": "demo-voice-token-0001",
                                                                      "webhook_secret": connections.seal("demo-secret"), "status": "ok"}}})
    for step in ("m1_employz", "m1_whatsapp", "m1_brain", "m2_sales"):
        await admin.put(f"/api/admin/owners/{suid}/setup/{step}", json={"done": True, "note": "Done on the onboarding call"})
    await sharma.post("/api/brain-docs", json={"title": "Price list 2026", "kind": "price", "text":
        "Modular kitchen, L-shape 10 ft, BWP ply, soft-close: Rs 2,10,000\nParallel kitchen 8+8 ft: Rs 2,60,000\nSliding wardrobe 7 ft: Rs 55,000\n"
        "Chimney + hob combo: Rs 38,000\nWarranty: 10 years on hardware, 1 year on workmanship. Installation in 25 working days."})
    token = (await d.connections.find_one({"_id": suid}))["whatsapp"]["webhook_token"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as pub:
        await hook(pub, f"/api/hooks/whatsapp/{token}", wa("919890011001", "Anjali Kulkarni", "Hi, what's the price of an L-shape kitchen?", "w1"))
        await hook(pub, f"/api/hooks/whatsapp/{token}", wa("919890011002", "Rohan Mehta", "Can I book a site visit this Saturday?", "w2"))
        await hook(pub, f"/api/hooks/whatsapp/{token}", wa("919890011003", "Sunil Joshi", "The drawer channel is broken, this is a problem", "w3"))
        await wait_for(lambda: _count(d.wa_messages, {"ws": suid, "dir": "out"}, 3))
        for name, phone, msg in (("Kavita Rao", "98900 22001", "Need a kitchen for our new 2BHK in Baner"), ("Amit Kale", "98900 22002", "Wardrobes for 3 bedrooms")):
            await pub.post(f"/s/{ssite['slug']}/inquiry", json={"name": name, "phone": phone, "message": msg}, headers={"x-forwarded-for": f"10.1.0.{len(name)}"})
    kavita = await d.leads.find_one({"owner_id": suid, "name": "Kavita Rao"})
    await d.calls.insert_one({"_id": "demo-call-1", "ws": suid, "lead_id": kavita["_id"], "to": kavita["phone"], "name": "Kavita Rao",
                              "conversation_id": "demo-conv-1", "status": "done", "kind": "new", "started_at": now() - timedelta(minutes=40),
                              "ended_at": now() - timedelta(minutes=37), "duration": 168, "outcome": "success",
                              "summary": "Moving into a 2BHK in Baner next month; wants an L-shape kitchen. Site visit booked for Saturday 11am.",
                              "transcript": [{"role": "agent", "message": "Hello Kavita ji, this is Sharma Modular Kitchens. You asked about a kitchen — is this a good time?"},
                                             {"role": "user", "message": "Yes, we get possession next month in Baner."},
                                             {"role": "agent", "message": "Congratulations! What shape is the kitchen, and when would you like it ready?"},
                                             {"role": "user", "message": "L-shape, about 10 feet. Before we move in."},
                                             {"role": "agent", "message": "Shall we book a free site visit on Saturday at 11 to measure and show you designs?"},
                                             {"role": "user", "message": "Okay, Saturday 11 works."}]})
    await d.leads.update_one({"_id": kavita["_id"]}, {"$set": {"status": "contacted", "stage": "pain", "value": 240000,
                                                               "note": "[AI call] Site visit booked for Saturday 11am."}})
    from app.services import new_id
    for name, phone, value, days in (("Pooja Deshmukh", "+919890044001", 265000, 6), ("Harish Nair", "+919890044002", 188000, 4),
                                     ("Snehal Patil", "+919890044003", 412000, 2)):
        await d.leads.insert_one({"_id": new_id(), "owner_id": suid, "site_id": None, "name": name, "phone": phone, "message": "Kitchen + wardrobes",
                                  "status": "won", "stage": "close", "value": value, "source": "website",
                                  "created_at": now() - timedelta(days=days + 9), "updated_at": now() - timedelta(days=days)})
    for name, phone, value in (("Neeta Pawar", "98900 33001", 64000), ("Gaurav Shinde", "98900 33002", 210000), ("Old customer Iyer", "98900 33003", 185000)):
        await sharma.post("/api/customers", json={"name": name, "phone": phone, "total_value": value, "last_purchase": str(today - timedelta(days=200)), "purchases": 2})
    draft = (await sharma.post("/api/campaigns/draft", json={"kind": "festival", "notes": "Diwali: free chimney with any kitchen booked this month"})).json()
    await sharma.post("/api/campaigns", json={"kind": "festival", "title": draft.get("title") or "Diwali offer", "message": draft["message"],
                                              "audience": {"tiers": ["A", "B", "C"], "quiet_days": 90}})
    gym = (await sharma.post("/api/gym/sessions", json={"scenario": "haggler"})).json()
    await sharma.post(f"/api/gym/sessions/{gym['id']}/say", json={"text": "I understand. What size is your kitchen and when do you move in?"})
    await sharma.post(f"/api/gym/sessions/{gym['id']}/say", json={"text": "Our price includes BWP ply and a 10-year warranty. Shall we book a site visit on Saturday?"})
    await sharma.post(f"/api/gym/sessions/{gym['id']}/finish")
    await sharma.put("/api/magic", json={"monthly_target": 2500000, "avg_sale": 220000, "close_rate": 25, "meeting_rate": 60})

    # ── LegacyWorkforce: the full v2 set for Shree Ganesh Interiors ──
    await rakesh.post("/api/score", json={"answers": {**score_yes, "payments": "partly"}})
    await rakesh.post("/api/week/plan")
    await rakesh.post("/api/calendar", json={"month": month, "focus": "Diwali kitchen bookings"})
    await rakesh.put("/api/magic", json={"monthly_target": 2000000, "avg_sale": 250000, "close_rate": 25, "meeting_rate": 60})
    await rakesh.post("/api/workforce/recommended")
    await rakesh.post("/api/lead-magnet/generate", json={"problem": "Choosing a modular kitchen without overpaying", "format": "checklist"})
    await rakesh.post("/api/lead-magnet/publish", json={"published": True})
    await rakesh.post("/api/offers/generate", json={"notes": "", "entry": "Free site visit and 3D design"})
    await rakesh.post("/api/offers/show", json={"show": True})
    await rakesh.put("/api/levers", json={"customers": 60, "avg_value": 320000, "frequency": 1.1, "pledge": "10×10×10 this year"})
    await rakesh.put("/api/portfolio", json={"goal": 30000000, "amazing_share": 75, "segments": [
        {"name": "New 2/3BHK families", "ticket": 650000, "margin": 28, "repeat": "maybe", "effort": 4, "conversion": 25},
        {"name": "Offices 2,000+ sq ft", "ticket": 3600000, "margin": 18, "repeat": "yes", "collect_days": 75, "effort": 8, "kind": "B2B", "conversion": 15},
        {"name": "Kitchen-only jobs", "ticket": 185000, "margin": 30, "repeat": "no", "effort": 3, "conversion": 35},
        {"name": "Builder show-flats", "ticket": 400000, "margin": 8, "repeat": "no", "collect_days": 120, "effort": 9, "kind": "B2B", "conversion": 20}]})
    await rakesh.put("/api/leaks", json={"numbers": {"visitors": 1800, "inquiries": 54, "meetings": 14, "proposals": 10, "won": 4}, "avg_sale": 320000})
    await rakesh.put("/api/end-goals", json={"identity": {"text": "Thane's most reliable interiors firm — known for finishing on the date we promise", "by": "2028"},
                                             "income": {"text": "₹5 lakh a month to the family, without me on every site", "by": "2027"},
                                             "freedom": {"text": "₹3 crore invested outside the business", "by": "2035"},
                                             "legacy": {"text": "A firm my team runs, training 20 young carpenters a year", "by": "2040"}})
    for lead in [x async for x in d.leads.find({"owner_id": uid, "status": {"$in": ["new", "contacted"]}})]:
        stage, value = {"Neha Joshi": ("vision", 720000), "Mrs. Kulkarni": ("pain", 185000)}.get(lead["name"], ("logic", 0))
        await rakesh.patch(f"/api/leads/{lead['_id']}", json={"stage": stage, **({"value": value} if value else {})})
    await admin.put(f"/api/admin/owners/{uid}/connections/whatsapp", json={"values": {"provider": "demo", "number": "98200 00001",
        "tpl_instant": "instant_reply_v1", "tpl_followup": "followup_v1", "tpl_review": "review_v1", "tpl_reminder": "payment_reminder_v1",
        "tpl_customer": "customer_v1", "tpl_campaign": "campaign_v1"}})
    for step in ("m1_employz", "m1_domain", "m1_whatsapp", "m1_brain", "m1_customers", "m2_sales", "m3_voice", "m4_content"):
        await admin.put(f"/api/admin/owners/{uid}/setup/{step}", json={"done": True})

    # ── The admin: a member call next week ──
    starts = (now() + timedelta(days=6)).astimezone(IST).replace(hour=18, minute=0, second=0, microsecond=0).replace(tzinfo=None)
    call = (await admin.post("/api/admin/member-calls", json={"title": "Festive season: pricing and offers that sell", "starts_at": starts.isoformat(),
                                                              "join_link": "https://zoom.us/j/0000000000", "tier": "lite",
                                                              "notes": "Bring one offer you want feedback on."})).json()
    await kapoor.post(f"/api/member-call/{call['id']}/questions", json={"text": "How do I price a Diwali check-up offer without looking cheap?"})
    return [patel, sharma]


async def v21_demo(d, admin, today):
    """The AI Advantage Membership launch account (v2.1, 4.6): every v2.1 demo filled in on one login, so the launch demos
    run from it. Kaveri Home Appliances is an invented business; every customer, phone number and email here is invented."""
    from app import followups, knowledge
    from app.routers import sopset
    from app.services import new_id
    # D5: the Membership plan is called AI Advantage Membership (an admin setting, not code)
    await admin.patch("/api/admin/config/plans/lite", json={"name": "AI Advantage Membership"})
    kav = await owner("9820000007")
    uid = (await kav.get("/api/me")).json()["user"]["id"]
    await admin.patch(f"/api/admin/users/{uid}", json={"plan": "lite"})
    await live(kav, {
        "name": "Kaveri Home Appliances", "city": "Pune", "industry": "Retail and wholesale of kitchen and home appliances, with installation",
        "offers": [{"name": "Chimney with installation", "price": "₹14,500", "unit": "onwards"}, {"name": "Water purifier AMC", "price": "₹3,200", "unit": "per year"},
                   {"name": "Bulk orders for builders", "price": "", "unit": ""}],
        "ideal_customer": "Families setting up a new kitchen, and builders handing over flats", "why_us": "Same-day installation in Pune\nGenuine brands with bills\nService visit within 48 hours",
        "proof": "Authorised dealer for 4 brands", "whatsapp": "98200 00007", "email": "hello@kaveri-appliances.example", "language": "English",
        "payment_note": "Pay by UPI to kaveri@okaxis or bank transfer", "_accent": "#1F4E79"})
    # the launch demo account gets every demo feature, as the admin would grant it (D1 moves these for everyone only when management confirms)
    for f in ("pipeline", "revenue_levers", "quotations", "money_owed", "lead_magnet", "automations", "approvals", "reminder_agent", "customers"):
        await admin.post("/api/admin/config/grants", json={"user_id": uid, "feature": f, "days": 365, "note": "AI Advantage launch demo"})
    await kav.put("/api/knowledge/identity", json={"entity": "pvt_ltd", "gst_registered": True, "gstin": "27AAHCK1234M1Z5", "gst_scheme": "monthly",
                                                    "state": "Maharashtra", "staff": 24, "registrations": ["udyam", "shops", "pt", "pf", "esi", "trademark"]})
    await kav.put("/api/knowledge/money", json={"payment_terms_days": 15, "bank_balance": 180000, "bank_balance_on": today.isoformat(), "gst_rate": 18,
                                                 "safety_buffer": 0, "fixed_costs": [{"name": "Salaries", "amount": 420000, "day": 1, "kind": "salary"},
                                                                                     {"name": "Showroom rent", "amount": 85000, "day": 5, "kind": "rent"},
                                                                                     {"name": "Delivery van EMI", "amount": 18500, "day": 10, "kind": "emi"}]})
    await kav.post("/api/brand-kit/generate")
    await kav.put("/api/brand-kit/design", json={"colors": {"primary": "#1F4E79", "secondary": "#488FD0", "accent": "#F2A93B"},
                                                  "fonts": {"heading": "poppins", "body": "mukta"}, "corners": "soft", "spacing": "comfortable", "text_logo": "Kaveri"})
    site = (await kav.get("/api/site")).json()
    await kav.post("/api/lead-magnet/generate", json={"problem": "Choosing the right chimney for an Indian kitchen", "format": "checklist"})
    await kav.post("/api/lead-magnet/publish", json={"published": True})

    # 25 leads from every source over the last 30 days; 6 open deals across the 5 stages
    names = ["Rohan Kulkarni", "Sneha Patil", "Amit Joshi", "Pallavi Deshpande", "Nikhil Bhosale", "Anjali Gokhale", "Sameer Kale", "Kirti Jadhav",
             "Manoj Pawar", "Rutuja Shinde", "Vivek Sathe", "Meera Apte", "Tushar More", "Gauri Chavan", "Prasad Limaye", "Swati Karve",
             "Omkar Dixit", "Neha Bapat", "Rahul Gadgil", "Aditi Ranade", "Kunal Mahajan", "Shruti Phadke", "Yash Thakur", "Pooja Kelkar", "Ajay Naik"]
    sources = ["website"] * 8 + ["guide"] * 5 + ["whatsapp"] * 5 + ["email"] * 4 + ["card"] * 3
    deals = {0: ("identification", 18000), 1: ("logic", 42000), 2: ("pain", 96000), 3: ("vision", 145000), 4: ("close", 260000), 5: ("logic", 31000)}
    for i, (name, src) in enumerate(zip(names, sources)):
        at = now() - timedelta(days=(i * 29) // 24, hours=i % 7)
        stage, value = deals.get(i, ("identification", 0))
        # 6 open deals, 3 new inquiries still waiting for a reply, the rest won or lost
        status = "contacted" if i in deals else "new" if i > 21 else "won" if i % 3 == 0 else "lost"
        doc = {"_id": new_id(), "owner_id": uid, "site_id": site["id"], "name": name, "phone": f"+9198300{70000 + i:05d}", "message": "Chimney and hob for a new kitchen",
               "status": status, "stage": stage, "value": value or (36000 if status == "won" else 0), "source": src, "created_at": at, "updated_at": at + timedelta(hours=5)}
        if status != "new":
            doc["first_action_at"] = at + timedelta(minutes=20 + i * 7)
        if src == "email":
            doc["email"] = name.lower().replace(" ", ".") + "@mail.example"
        await d.leads.insert_one(doc)
    for i in range(30):
        day_ = (today - timedelta(days=i)).isoformat()
        await d.site_views.insert_one({"_id": f"{site['id']}:{day_}", "site_id": site["id"], "ws": uid, "day": day_, "n": 30 + (i * 7) % 25})

    # 10 invoices this month (3 paid through the payment gateway), 8 dues (2 overdue) with email follow-up history
    month_start = today.replace(day=1)
    elapsed = (today - month_start).days + 1
    for i in range(10):
        total = [48000, 36500, 112000, 22000, 64000, 18500, 91000, 27000, 54000, 40500][i]
        created = now() - timedelta(days=min(elapsed - 1, i * 2))
        gateway = i < 3
        await d.quotes.insert_one({"_id": new_id(), "ws": uid, "kind": "invoice", "number": f"KHA-{1101 + i}", "customer": {"name": names[10 + i]},
                                   "items": [{"name": "Appliances and installation", "qty": 1, "unit": "nos", "rate": total, "gst": 0, "amount": total}],
                                   "totals": {"subtotal": total, "gst": 0, "total": total}, "gst_mode": "intra",
                                   "status": "paid" if gateway or i == 5 else "due", "paid_at": created if gateway else None, "due_id": None,
                                   **({"source": "gateway", "gateway_payment_id": f"pay_demo{i:04d}"} if gateway else {}), "created_at": created, "updated_at": created})
    habits = [("Desai Builders", -40, 82000, 12), ("Sharma Interiors", -25, 46000, 2)]   # paid before, late and on time
    for cust, back, amount, late in habits:
        due_day = today + timedelta(days=back)
        await d.dues.insert_one({"_id": new_id(), "ws": uid, "customer": cust, "phone": "", "email": "", "amount": amount * 100, "due_date": due_day.isoformat(),
                                 "status": "paid", "paid_at": datetime_ist(due_day + timedelta(days=late)), "created_at": now() - timedelta(days=60)})
    dues = [("Desai Builders", "accounts@desai-builders.example", -12, 45000), ("Joshi Caterers", "billing@joshi-caterers.example", -5, 30000),
            ("Sharma Interiors", "pay@sharma-interiors.example", 3, 60000), ("Patil Residency", "office@patil-residency.example", 9, 38000),
            ("Hotel Panchavati", "accounts@hotel-panchavati.example", 12, 125000), ("Gokhale Clinic", "", 15, 22000),
            ("Sai Developers", "finance@sai-developers.example", 20, 210000), ("Mrs. Apte", "", 26, 16500)]
    stage_days = followups.PENDING_DAYS
    for cust, email, offset, amount in dues:
        due_day = today + timedelta(days=offset)
        history = []
        if email and offset < 0:
            history = [{"stage": "pending", "n": 0, "channel": "email", "at": datetime_ist(due_day - timedelta(days=stage_days)), "ok": True, "via": "dev-email"},
                       {"stage": "due", "n": 0, "channel": "email", "at": datetime_ist(due_day), "ok": True, "via": "dev-email"}]
            if offset <= -3:
                history.append({"stage": "overdue", "n": 1, "channel": "email", "at": datetime_ist(due_day + timedelta(days=3)), "ok": True, "via": "dev-email"})
        await d.dues.insert_one({"_id": new_id(), "ws": uid, "customer": cust, "phone": "", "email": email, "amount": amount * 100, "due_date": due_day.isoformat(),
                                 "invoice_no": f"KHA-{1000 + amount // 1000}", "note": "", "status": "due", "paid_at": None, "reminders_sent": 0,
                                 "followups": history, "auto_stopped": None, "created_at": now() - timedelta(days=30), "updated_at": now()})
    s = (await kav.get("/api/followups/auto")).json()
    await kav.post("/api/followups/auto/approve", json={"hash": s["hash"]})
    # cash flow: a supplier payment on day 7 takes the balance below zero
    await kav.post("/api/cash-flow/items", json={"kind": "out", "title": "Supplier payment — Diwali stock", "amount": 380000,
                                                  "date": (today + timedelta(days=6)).isoformat()})

    # compliance: generated from the identity, plus two own items due this week and a document expiring this month
    await kav.get("/api/compliance")
    for title, days, cat in (("Shop insurance premium", 2, "insurance"), ("Fire extinguisher refill", 5, "other")):
        await kav.post("/api/compliance/items", json={"title": title, "due_date": (today + timedelta(days=days)).isoformat(), "category": cat, "recurrence": "yearly"})
    tm = await d.compliance_items.find_one({"owner_id": uid, "rule_key": "trademark"})
    if tm:
        await kav.patch(f"/api/compliance/items/{tm['_id']}", json={"due_date": (today + timedelta(days=24)).isoformat()})
        from app import files as files_store
        f = await files_store.put(uid, b"%PDF-1.4\n% demo certificate placeholder\n", "trademark-certificate.pdf", "application/pdf", "compliance")
        await d.compliance_items.update_one({"_id": tm["_id"]}, {"$set": {"doc_file_id": f["_id"]}})

    # 12 SOPs from the process map, 20 posts (9 posted), a target the month is ₹3.2 L behind
    await kav.post("/api/sop-set/map")
    keys = [p["key"] for p in (await kav.get("/api/sop-set")).json()["processes"][:12]]
    await kav.post("/api/sop-set/write", json={"keys": keys})
    await sopset.settle()
    month = today.strftime("%Y-%m")
    cal = (await kav.post("/api/calendar", json={"month": month, "focus": "Diwali kitchen upgrades"})).json()["calendar"]
    await kav.post("/api/week/plan") if (await kav.get("/api/me")).json()["features"].get("week") else None
    posted = 0
    for p in cal["posts"]:
        if p["date"] <= today.isoformat() and posted < 9:
            await kav.post("/api/social/posted", json={"ref": f"cal:{month}:{p['date']}", "platform": ["Instagram", "Facebook", "WhatsApp Status"][posted % 3]})
            posted += 1
    from app import sales
    sold = (await sales.month_sales(uid, "invoiced", today))["sales"]
    projected = sold / elapsed * (sales.month_bounds(today)[1] - month_start).days
    await kav.put("/api/magic", json={"monthly_target": round(projected + 320000, -3), "avg_sale": 36000, "close_rate": 30, "meeting_rate": 60})

    # P1: an uploaded lead magnet, a pasted WhatsApp inquiry with its proposal, and proposals waiting for two email inquiries
    import io
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf)
    pdf.setFont("Helvetica-Bold", 20)
    pdf.drawString(72, 760, "Chimney buying checklist")
    pdf.setFont("Helvetica", 12)
    for n, line in enumerate(["1. Suction power for your kitchen size", "2. Filterless or baffle filter", "3. Duct length and bends",
                              "4. Noise level", "5. Installation and service in your area"]):
        pdf.drawString(72, 720 - n * 22, line)
    pdf.save()
    await kav.post("/api/lead-magnets", files={"file": ("chimney-checklist.pdf", buf.getvalue(), "application/pdf")},
                   data={"title": "Chimney buying checklist", "description": "Five things to check before you buy a chimney for an Indian kitchen."})
    await kav.post("/api/inquiries/whatsapp", json={"name": "Varsha Kulkarni", "phone": "98300 71001",
                                                    "message": "Hi, need a chimney with installation for our new flat in Baner. What's the price?"})
    from app.routers import inquiries
    # website inquiries get a written proposal too on the launch demo (the owner's switch; off by default for everyone else)
    assert (await kav.put("/api/inquiries/settings", json={"auto_web": True})).json()["auto_web"] is True
    b = await d.businesses.find_one({"owner_id": uid})
    async for lead in d.leads.find({"owner_id": uid, "source": "email", "status": "new"}).limit(2):
        await inquiries.on_new_inquiry(await d.users.find_one({"_id": uid}), b, lead)
    async for lead in d.leads.find({"owner_id": uid, "source": "website", "status": "new"}).limit(2):
        await inquiries.on_new_inquiry(await d.users.find_one({"_id": uid}), b, lead)
    await inquiries.settle()
    await knowledge.settle()
    return kav


def datetime_ist(day):
    from datetime import datetime
    return datetime(day.year, day.month, day.day, 11, 0, tzinfo=IST)


async def _count(coll, query, n):
    return await coll.count_documents(query) >= n


asyncio.run(main())
