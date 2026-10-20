"""The six areas' live demos, done by attendees whose accounts were made from the event link — with the guide's features
moved into Membership and Membership features open until the event ends. Then the event ends: the pack stays, the paid
features close. Every business, number and address here is made up."""
from datetime import timedelta

import httpx

from app import event as ev
from app.db import IST, db, now
from app.main import app
from app.routers import inquiries
from tests.test_event import CODE, form, make, open_event, site_server

ANSWERS = {k: "partly" for k in ("focus", "margins", "sales", "payments", "offers", "reply", "followup", "content", "process", "numbers")}


def fresh() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://hub.example.in")


async def save_pack(token: str, phone: str, email: str, name: str) -> httpx.AsyncClient:
    c = fresh()
    code = (await c.post(f"/api/event/runs/{token}/code", json={"phone": phone})).json()["dev_code"]
    r = await c.post(f"/api/event/runs/{token}/verify", json={"phone": phone, "code": code, "email": email, "name": name})
    assert r.status_code == 200, r.text
    return c


async def test_every_demo_works_for_an_event_account_and_the_paid_part_closes_when_the_event_ends(client):
    admin = await open_event()
    assert (await admin.post("/api/admin/config/presets/guide-membership")).json()["applied"]
    with site_server() as site:
        a = await make(client, form(site))
        b = await make(client, form(None, business_name="Riya Tiffins", about="Home-cooked lunch tiffins delivered to offices", city="Pune"))
        await ev.drain()
    asha = await save_pack(a["token"], "9833061111", "asha@example-kaveri.in", "Asha")
    riya = await save_pack(b["token"], "9833062222", "riya@example-tiffins.in", "Riya")
    me = (await asha.get("/api/me")).json()
    assert me["user"]["effective_plan"] == "lite" and me["knowledge_percent"] >= 45
    for f in ("quotations", "pipeline", "money_owed", "lead_magnet", "revenue_levers", "inquiry_desk", "cash_flow", "brand_kit", "compliance_calendar"):
        assert me["features"][f], f

    # Lead + Conversion · swap with your neighbour: Asha's website goes live, Riya scans the QR and sends an inquiry
    assert (await asha.post("/api/site/publish")).status_code == 200
    slug = (await asha.get("/api/site")).json()["slug"]
    sent = await riya.post(f"/s/{slug}/inquiry", json={"name": "Riya", "phone": "9833062222", "message": "Fridge not cooling, can someone come today?"})
    assert sent.status_code == 200
    got = (await asha.get("/api/inquiries")).json()["items"]
    assert [i["channel"] for i in got] == ["website"] and got[0]["name"] == "Riya"

    # Conversion · be your own customer: a WhatsApp inquiry pasted in gets a reply written from the Knowledge Hub
    r = await asha.post("/api/inquiries/whatsapp", json={"name": "Test customer", "phone": "9833061111", "message": "What's the price for washing machine repair?"})
    assert r.status_code == 200
    await inquiries.settle()
    lead_id = r.json()["id"]
    item = next(i for i in (await asha.get("/api/inquiries")).json()["items"] if i["id"] == lead_id)
    assert item["proposal"] and item["proposal"]["whatsapp_text"]
    tap = await asha.post(f"/api/inquiries/{lead_id}/whatsapp")
    assert tap.status_code == 200 and tap.json()["link"].startswith("https://wa.me/")

    # Conversion · inquiry to quotation: the message drafts the lines, saved against the inquiry, ready for WhatsApp
    d = await asha.post("/api/quotes/draft", json={"brief": item["message"]})
    assert d.status_code == 200 and d.json()["items"]
    lines = [{**i, "rate": i["rate"] or 299} for i in d.json()["items"]]
    q = await asha.post("/api/quotes", json={"customer": {"name": "Test customer", "phone": "9833061111", "gstin": "", "address": ""}, "items": lines,
                                             "gst_mode": "intra", "notes": "", "terms": "", "valid_days": 15, "lead_id": lead_id})
    assert q.status_code == 200, q.text
    assert q.json()["lead_id"] == lead_id and q.json()["whatsapp_link"].startswith("https://wa.me/") and q.json()["totals"]["gst"] > 0

    # Brand · the brand kit text, written live (colours and logo came with the pack)
    kit = await asha.post("/api/brand-kit/generate")
    assert kit.status_code == 200 and kit.json()["messages"] and kit.json()["has_logo"]

    # Operations · the compliance calendar is already there
    comp = (await asha.get("/api/compliance")).json()
    assert comp["items"] or comp.get("upcoming")

    # Data · the next 30 days of cash, with a what-if
    today = now().astimezone(IST).date()
    money = {"payment_terms_days": 15, "bank_balance": 60000, "bank_balance_on": today.isoformat(), "gst_rate": 18, "safety_buffer": 0,
             "fixed_costs": [{"name": "Shop rent", "amount": 30000, "day": min(today.day + 3, 28), "kind": "rent"},
                             {"name": "Salaries", "amount": 45000, "day": min(today.day + 5, 28), "kind": "salary"}]}
    assert (await asha.put("/api/knowledge/money", json=money)).status_code == 200
    due = await asha.post("/api/money", json={"customer": "Test customer", "amount": 40000, "due_date": (today + timedelta(days=4)).isoformat()})
    assert due.status_code == 200
    cf = (await asha.get("/api/cash-flow")).json()
    late = (await asha.get(f"/api/cash-flow?delay_id={due.json()['id']}&delay_days=15")).json()
    assert cf["ready"] and late["ready"] and late["lowest"]["balance"] < cf["lowest"]["balance"]       # paying late shows a deeper dip

    # Data · your daily number
    assert (await asha.put("/api/knowledge/targets", json={"monthly_revenue": 500000})).status_code == 200
    t = (await asha.get("/api/target")).json()["target"]
    assert t and t["target"] == 500000 and t["required_daily"] > 0

    # Data · Dashboard at 60%: with the money basics and the target in, the Knowledge Hub passes 60% and the Dashboard is home
    me = (await asha.get("/api/me")).json()
    assert me["knowledge_percent"] >= 60 and me["landing"] == "/dashboard"

    # Data · Day 1 vs Day 3: the score, then the re-take members get
    first = await asha.post("/api/score", json={"answers": ANSWERS})
    assert first.status_code == 200 and first.json()["change"] is None
    again = await asha.post("/api/score", json={"answers": {**ANSWERS, "reply": "yes", "followup": "yes"}})
    assert again.status_code == 200 and again.json()["change"] > 0

    live = (await admin.get(f"/api/admin/event/{CODE}/live")).json()
    d = live["demos"]
    assert live["accounts"] == 2 and live["runs"]["saved"] == 2
    assert d["conversion"]["Sent themselves an inquiry"] == 1 and d["conversion"]["Got a website inquiry"] == 1
    assert d["conversion"]["Quotation from an inquiry"] == 1 and d["brand"]["Brand kit written"] == 1
    assert d["data"]["Cash flow ready"] == 1 and d["data"]["Target set"] == 1 and d["data"]["Re-took the score"] == 1

    # the event ends: what they built stays; Membership features need the membership
    await db().users.update_many({"cohort": CODE}, {"$set": {"trial.until": now() - timedelta(minutes=1)}})
    me = (await asha.get("/api/me")).json()
    assert me["user"]["effective_plan"] == "free" and not me["features"]["quotations"]
    assert (await asha.get("/api/quotes")).status_code == 403
    assert (await asha.get("/api/site")).json()["status"] == "live" and (await asha.get("/api/business")).json()["name"]
    assert (await asha.post("/api/score", json={"answers": ANSWERS})).status_code == 403    # re-checking the score is a member feature
    for c in (asha, riya):
        await c.aclose()

