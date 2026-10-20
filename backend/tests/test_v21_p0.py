"""v2.1 P0 — the launch features: Knowledge Hub propagation, the Smart Dashboard, compliance calendar, cash flow,
"Am I on target", automatic payment follow-ups, the brand kit, the SOP set, Mark as posted and ways to get more leads.
Each test follows an acceptance line in the v2.1 change spec. Every name, phone and email here is invented."""
import asyncio
import io
import re
from datetime import date, datetime, timedelta

import pytest
from PIL import Image

from app import cashflow, dashboard_cache, followups, knowledge, plans, sales
from app.agents.catalog import Skip
from app.ai import brand_tasks, kit_tasks
from app.ai.provider import AIError
from app.ai.tasks import RULES, profile_text
from app.db import IST, db, now
from app.routers import compliance, leadideas, sopset
from app.routers.hub import profile_of
from tests.conftest import PROFILE, go_live, new_owner

ADMIN = "9999900000"


def ist(*a) -> datetime:
    return datetime(*a, tzinfo=IST)


async def owner_on(phone: str, plan: str = "running", live: bool = True, profile: dict | None = None):
    c = await new_owner(phone)
    site = await go_live(c, profile) if live else None
    if not live:
        assert (await c.put("/api/business", json=profile or PROFILE)).status_code == 200
    uid = (await c.get("/api/me")).json()["user"]["id"]
    if plan != "free":
        admin = await new_owner(ADMIN)
        assert (await admin.patch(f"/api/admin/users/{uid}", json={"plan": plan})).status_code == 200
    return c, uid, site


async def owner_doc(uid: str) -> dict:
    return await db().users.find_one({"_id": uid})


def today() -> date:
    return now().astimezone(IST).date()


async def used_runs(uid: str) -> int:
    u = await db().usage.find_one({"user_id": uid}) or {}
    return int(u.get("used") or 0)


# ═════════════════════════ 3.1 Knowledge Hub ═════════════════════════
async def test_phone_change_reaches_the_website_and_an_edited_headline_is_kept_with_a_banner(client):
    c, uid, site = await owner_on("9851000001", "lite")
    me = (await c.get("/api/me")).json()
    assert me["knowledge_percent"] < 60 and me["landing"] == "/home"

    # the phone: the live page's WhatsApp button follows the Brain at once, with no AI run
    runs = await used_runs(uid)
    assert (await c.put("/api/business", json={**PROFILE, "whatsapp": "98200 11111"})).status_code == 200
    await knowledge.settle()
    tap = await client.get(f"/s/{site['slug']}/wa", follow_redirects=False)
    assert tap.headers["location"].startswith("https://wa.me/919820011111")
    assert await used_runs(uid) == runs     # a contact-only change rewrites nothing

    # the owner edits the headline, then changes what the business does: their headline stays, and a banner says so
    s = (await c.get("/api/site")).json()
    content = {**s["content"], "headline": "Kitchens that finish on the promised day"}
    assert (await c.put("/api/site", json={"content": content, "accent": s["accent"]})).status_code == 200
    assert (await c.put("/api/business", json={**PROFILE, "whatsapp": "98200 11111", "industry": "Modular kitchens and wardrobes"})).status_code == 200
    await knowledge.settle()
    assert (await c.get("/api/site")).json()["content"]["headline"] == "Kitchens that finish on the promised day"
    k = (await c.get("/api/knowledge")).json()
    [u] = [x for x in k["updates"] if x["key"] == "website"]
    assert u["reason"] == "edited" and "basics" in u["sections"] and u["path"] == "/website"

    # Refresh on the banner is the owner's choice: the copy is rewritten from the Brain and the banner goes
    assert (await c.post("/api/knowledge/updates/website/refresh")).status_code == 200
    assert (await c.get("/api/site")).json()["content"]["headline"] != "Kitchens that finish on the promised day"
    assert not [x for x in (await c.get("/api/knowledge")).json()["updates"] if x["key"] == "website"]


async def test_knowledge_hub_completeness_and_the_landing_page_switch(client):
    c, uid, _ = await owner_on("9851000002", "lite", live=False)
    k0 = (await c.get("/api/knowledge")).json()["completeness"]
    assert (await c.put("/api/knowledge/identity", json={"entity": "pvt_ltd", "gst_registered": True, "gstin": "27ABCDE1234F1Z5",
                                                          "gst_scheme": "monthly", "state": "Maharashtra", "staff": 12, "registrations": []})).status_code == 200
    assert (await c.put("/api/knowledge/money", json={"payment_terms_days": 15, "bank_balance": 350000, "bank_balance_on": today().isoformat(),
                                                       "fixed_costs": [{"name": "Salaries", "amount": 180000, "day": 1, "kind": "salary"}]})).status_code == 200
    assert (await c.put("/api/knowledge/targets", json={"monthly_revenue": 800000})).status_code == 200
    await knowledge.settle()
    k1 = (await c.get("/api/knowledge")).json()["completeness"]
    assert k1["percent"] > k0["percent"]
    # a bank date in the future is refused
    bad = await c.put("/api/knowledge/money", json={"payment_terms_days": 15, "bank_balance": 1, "bank_balance_on": (today() + timedelta(days=2)).isoformat()})
    assert bad.status_code == 422
    me = (await c.get("/api/me")).json()
    assert me["knowledge_percent"] == k1["percent"]
    assert me["landing"] == ("/dashboard" if k1["percent"] >= 60 else "/home")


async def test_every_new_ai_task_reads_the_brain_and_invents_no_prices_or_dates(client, monkeypatch):
    """Each v2.1 AI task gets the hub's rules (never invent prices, numbers or dates) and the owner's profile_text."""
    c, uid, _ = await owner_on("9851000003", "lite", live=False,
                               profile={**PROFILE, "offers": [{"name": "Modular kitchen", "price": "", "unit": ""}]})
    p = await profile_of(uid)
    seen = []

    def spy(module):
        real = module.complete

        async def fake(system, prompt, mock, max_tokens=1200):
            seen.append((module.__name__, system, prompt))
            return await real(system, prompt, mock, max_tokens)
        monkeypatch.setattr(module, "complete", fake)
    for m in (brand_tasks, kit_tasks, leadideas, compliance):
        spy(m)
    outputs = [
        (await brand_tasks.message_system(p, {"message": "Kitchens done right"}))[0],
        (await kit_tasks.process_map(p))[0],
        (await kit_tasks.sop_scripts(p, {"title": "Site visit", "steps": [{"title": "Confirm the time"}]}))[0],
        (await kit_tasks.sop(p, {"process": "A customer says delivery is late", "notes": "", "mode": "scenario", "function": ""}))[0],
        (await leadideas._ideas_job(p, "services", leadideas.fitting("services"), {}))[0],
    ]
    assert len(seen) == 5
    for name, system, prompt in seen:
        assert system == RULES, name
        assert profile_text(p) in prompt, name
    text = str(outputs)
    assert "₹" not in text                                        # the profile has no prices, so nothing has a price
    assert not re.search(r"\b20\d\d-\d\d-\d\d\b|\b\d{1,2} (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w* 20\d\d\b", text)


# ═════════════════════════ 3.9 Compliance calendar ═════════════════════════
async def test_compliance_items_follow_the_business_identity(client):
    c, uid, _ = await owner_on("9851000010", "lite", live=False)
    assert (await c.put("/api/knowledge/identity", json={"entity": "pvt_ltd", "gst_registered": True, "gstin": "27ABCDE1234F1Z5",
                                                          "gst_scheme": "monthly", "state": "Maharashtra", "staff": 25, "registrations": []})).status_code == 200
    await knowledge.settle()
    d = (await c.get("/api/compliance?days=400")).json()
    keys = {i["rule_key"] for i in d["all"]}
    for k in ("gstr1", "gstr3b", "tds_payment", "tds_return", "pf", "esi", "pt_mh", "advance_tax", "aoc4", "mgt7"):
        assert k in keys, k
    assert d["label"] == "Dates come from the rules table, not from AI." and d["review"]["reviewed"] is False

    p, puid, _ = await owner_on("9851000011", "lite", live=False)
    assert (await p.put("/api/knowledge/identity", json={"entity": "proprietor", "gst_registered": False, "state": "Maharashtra", "staff": 3,
                                                          "registrations": ["fssai"]})).status_code == 200
    await knowledge.settle()
    d = (await p.get("/api/compliance?days=400")).json()
    keys = {i["rule_key"] for i in d["all"]}
    assert keys and not [k for k in keys if k.startswith("gst") or k in ("pf", "esi", "pmt06")]
    assert "itr_july" in keys and "advance_tax" in keys
    # a ticked licence asks for its expiry date; the hub never guesses one
    [fssai] = d["needs_date"]
    assert fssai["rule_key"] == "fssai" and fssai["due_date"] is None and fssai["source"] == "renewal"
    day = (today() + timedelta(days=200)).isoformat()
    assert (await p.patch(f"/api/compliance/items/{fssai['id']}", json={"due_date": day})).json()["status"] == "upcoming"
    d = (await p.get("/api/compliance?days=400")).json()
    assert not d["needs_date"] and [i for i in d["all"] if i["rule_key"] == "fssai"][0]["due_date"] == day


async def test_compliance_reminders_fire_at_30_7_1_and_0_days_to_the_owner_only(client):
    c, uid, _ = await owner_on("9851000012", "lite", live=False)
    await c.put("/api/knowledge/identity", json={"entity": "proprietor", "gst_registered": False, "state": "Maharashtra", "staff": 3, "registrations": []})
    await knowledge.settle()
    await c.get("/api/compliance")
    item = await db().compliance_items.find_one({"owner_id": uid, "due_date": {"$gte": (today() + timedelta(days=31)).isoformat()}}, sort=[("due_date", 1)])
    due = date.fromisoformat(item["due_date"])
    owner = await owner_doc(uid)
    fired = []
    for back in range(31, -1, -1):
        at = ist(due.year, due.month, due.day, 9, 5) - timedelta(days=back)
        try:
            await compliance.compliance_reminders(owner, {"at": at})
        except Skip:
            pass
        now_item = await db().compliance_items.find_one({"_id": item["_id"]})
        if len(now_item["reminded"]) > len(fired):
            fired = list(now_item["reminded"])
    assert fired == [30, 7, 1, 0]
    # owner only: nothing went to anyone but the owner's own number
    assert not await db().outbox.count_documents({"channel": "whatsapp", "kind": {"$ne": "otp"}, "to": {"$ne": owner["phone"]}})


async def test_the_ai_explains_compliance_but_never_sets_a_date(client):
    c, uid, _ = await owner_on("9851000013", "lite", live=False)
    await c.put("/api/knowledge/identity", json={"entity": "pvt_ltd", "gst_registered": True, "gstin": "27ABCDE1234F1Z5", "gst_scheme": "monthly",
                                                  "state": "Maharashtra", "staff": 25, "registrations": []})
    await knowledge.settle()
    before = {i["_id"]: i["due_date"] async for i in db().compliance_items.find({"owner_id": uid})}
    r = await c.post("/api/compliance/explain")   # the mock AI sends back "2099-01-01" for every item
    assert r.status_code == 200 and r.json()["explained"] > 0
    after = {i["_id"]: i async for i in db().compliance_items.find({"owner_id": uid})}
    assert {k: v["due_date"] for k, v in after.items()} == before
    assert not await db().compliance_items.count_documents({"owner_id": uid, "due_date": {"$regex": "^2099"}})
    assert all(v.get("explain") for v in after.values())


# ═════════════════════════ 3.14 Cash flow ═════════════════════════
async def test_cash_flow_shortfall_and_the_due_to_collect_first(client):
    c, uid, _ = await owner_on("9851000020", "running", live=False)
    await c.put("/api/knowledge/money", json={"payment_terms_days": 0, "bank_balance": 200000, "bank_balance_on": today().isoformat(),
                                               "fixed_costs": [{"name": "Salaries", "amount": 300000, "day": 1, "kind": "salary"}]})
    await db().businesses.update_one({"owner_id": uid}, {"$set": {"money.bank_balance_on": "2026-11-28"}})   # the balance on the forecast's first day
    due = (await c.post("/api/money", json={"customer": "Mehta Traders", "amount": 150000, "due_date": "2026-12-05"})).json()
    f = await cashflow.forecast(uid, date(2026, 11, 28))
    assert f["ready"] and f["label"] == "A forecast from what you've entered — not your accounts."
    assert f["shortfall"]["date"] == "2026-12-01" and f["shortfall"]["until"] == "2026-12-04" and f["shortfall"]["short_by"] == 100000
    assert f["close_the_gap"][0]["id"] == due["id"]
    assert f["lowest"] == {"date": "2026-12-01", "balance": -100000}

    # paid: the money is in the bank now, so the forecast moves at once
    assert (await c.patch(f"/api/money/{due['id']}", json={"status": "paid"})).status_code == 200
    f = await cashflow.forecast(uid, date(2026, 11, 28))
    assert f["received_since_balance"] == 150000 and f["shortfall"] is None
    assert not [e for d in f["days"] for e in d["events"] if e.get("id") == due["id"]]

    # a balance typed earlier is rolled forward: the salaries paid since then come off before today
    await db().businesses.update_one({"owner_id": uid}, {"$set": {"money.bank_balance_on": "2026-10-25"}})
    f = await cashflow.forecast(uid, date(2026, 11, 3))
    assert f["since_balance_date"] == -300000 and f["opening"] == 200000 - 300000 + 150000
    # an amount already later than the customer usually pays isn't counted as cash until it comes in
    late = (await c.post("/api/money", json={"customer": "Sharma Stores", "amount": 90000, "due_date": "2026-10-30"})).json()
    f = await cashflow.forecast(uid, date(2026, 11, 3))
    assert not [e for d in f["days"] for e in d["events"] if e.get("id") == late["id"]]
    assert [g for g in f["close_the_gap"] if g["id"] == late["id"]][0]["late"] is True


# ═════════════════════════ 3.13 Am I on target ═════════════════════════
async def test_on_target_projection_gap_and_pace(client):
    c, uid, _ = await owner_on("9851000030", "running", live=False)
    assert (await c.put("/api/knowledge/targets", json={"monthly_revenue": 1000000})).status_code == 200
    await db().quotes.insert_one({"_id": "inv-a", "ws": uid, "kind": "invoice", "number": "INV-1", "status": "due", "totals": {"total": 400000},
                                  "created_at": ist(2026, 11, 10, 12), "lead_id": "lead-a"})
    # the won lead this invoice was raised for: counted once, not twice
    await db().leads.insert_one({"_id": "lead-a", "owner_id": uid, "name": "Kavita Desai", "phone": "+919851100001", "status": "won", "value": 400000,
                                 "created_at": ist(2026, 11, 1, 10), "updated_at": ist(2026, 11, 9, 10), "source": "website"})
    s = await sales.target_status(uid, date(2026, 11, 15))
    assert s["month_to_date"] == 400000 and s["days_in_month"] == 30 and s["days_elapsed"] == 15
    assert s["projected"] == 800000 and s["gap"] == 200000 and s["required_daily"] == 40000
    assert s["status"] == "well_behind" and s["month_gone_pct"] == 50
    r = (await c.get("/api/magic")).json()
    assert r.get("brain_target") or r.get("target") is not None


# ═════════════════════════ 3.8 Payment follow-ups ═════════════════════════
async def _due(c, name, email, day, amount=42500):
    r = await c.post("/api/money", json={"customer": name, "phone": "98511 00099", "email": email, "amount": amount, "due_date": day,
                                         "invoice_no": "INV-0042"})
    assert r.status_code == 200, r.text
    return r.json()


async def test_follow_ups_send_nothing_until_the_owner_switches_them_on(client):
    c, uid, _ = await owner_on("9851000040", "running", live=False)
    d = await _due(c, "Mehta Traders", "accounts@mehta.example", "2026-11-20")
    owner = await owner_doc(uid)
    for day in range(14, 30):
        await followups.run_owner(owner, ist(2026, 11, day, 10, 5))
    assert not await db().outbox.count_documents({"channel": "email"})
    assert not (await db().dues.find_one({"_id": d["id"]})).get("followups")


async def test_follow_ups_pending_due_overdue_stop_after_four_and_on_payment(client):
    c, uid, _ = await owner_on("9851000041", "running", live=False)
    one = await _due(c, "Mehta Traders", "accounts@mehta.example", "2026-11-20")
    two = await _due(c, "Sharma Stores", "pay@sharma.example", "2026-11-20", 18000)
    s = (await c.get("/api/followups/auto")).json()
    assert s["on"] is False and set(s["samples"]) == {"pending", "due", "overdue", "final"}
    assert (await c.post("/api/followups/auto/approve", json={"hash": "0" * 16})).status_code == 409   # only the wording they saw
    s = (await c.post("/api/followups/auto/approve", json={"hash": s["hash"], "whatsapp": True})).json()
    assert s["on"] is True and s["whatsapp"] is False          # Running: WhatsApp never goes on its own
    owner = await owner_doc(uid)
    assert await followups.whatsapp_auto(owner) is False

    async def emails(to):
        return [m async for m in db().outbox.find({"channel": "email", "to": to}).sort("at", 1)]

    for day in range(15, 32):
        at = ist(2026, 11, day, 10, 5) if day <= 30 else ist(2026, 12, day - 30, 10, 5)
        await followups.run_owner(owner, at)
        if day == 23:   # Sharma pays after the first overdue reminder
            assert (await c.patch(f"/api/money/{two['id']}", json={"status": "paid"})).status_code == 200
    for day in range(1, 20):
        await followups.run_owner(owner, ist(2026, 12, day, 10, 5))
    m = [x["subject"] for x in await emails("accounts@mehta.example")]
    assert m[0].startswith("Payment of ₹42,500 for invoice INV-0042 is due on")           # 17 Nov: 3 days before
    assert m[1].startswith("Payment due today")                                          # 20 Nov
    assert [x.split(":")[0] for x in m[2:]] == ["Reminder", "Reminder", "Reminder", "Final reminder"]
    assert len(m) == 6                                                                   # stops after 4 overdue
    sharma = [x["subject"] for x in await emails("pay@sharma.example")]
    assert len(sharma) == 3                                                              # pending, due, overdue 1; then paid
    hist = (await c.get(f"/api/followups/history/{one['id']}")).json()["history"]
    assert [h["stage"] for h in hist] == ["pending", "due", "overdue", "overdue", "overdue", "overdue"]


async def test_follow_ups_two_workers_send_once_and_a_reply_stops_them(client):
    c, uid, _ = await owner_on("9851000042", "running", live=False)
    d = await _due(c, "Mehta Traders", "accounts@mehta.example", "2026-11-20")
    s = (await c.get("/api/followups/auto")).json()
    await c.post("/api/followups/auto/approve", json={"hash": s["hash"]})
    owner = await owner_doc(uid)
    await asyncio.gather(*[followups.run_owner(owner, ist(2026, 11, 20, 10, 5)) for _ in range(3)])
    assert await db().outbox.count_documents({"channel": "email"}) == 1
    assert (await c.post(f"/api/money/{d['id']}/replied")).status_code == 200
    await followups.run_owner(owner, ist(2026, 11, 23, 10, 5))
    assert await db().outbox.count_documents({"channel": "email"}) == 1
    # excluded customers aren't chased either
    other = await _due(c, "Sharma Stores", "pay@sharma.example", "2026-11-25")
    assert (await c.post("/api/followups/auto/exclude", json={"due_id": other["id"]})).status_code == 200
    await followups.run_owner(owner, ist(2026, 11, 25, 10, 5))
    assert await db().outbox.count_documents({"channel": "email", "to": "pay@sharma.example"}) == 0


async def test_the_hub_number_never_messages_a_customer(client):
    """Regression on v2 rule 1: whatever v2.1 automations run, the hub's own WhatsApp number only ever messages owners."""
    c, uid, site = await owner_on("9851000043", "growth")
    owner = await owner_doc(uid)
    d = await _due(c, "Mehta Traders", "accounts@mehta.example", "2026-11-20")
    s = (await c.get("/api/followups/auto")).json()
    s = (await c.post("/api/followups/auto/approve", json={"hash": s["hash"], "whatsapp": True})).json()
    assert s["whatsapp"] is True and s["whatsapp_available"] is True
    await client.post(f"/s/{site['slug']}/inquiry", json={"name": "Rohit Shah", "phone": "98511 30001", "message": "Kitchen"})
    await c.put("/api/knowledge/identity", json={"entity": "proprietor", "gst_registered": False, "state": "Maharashtra", "staff": 3, "registrations": []})
    await c.put("/api/knowledge/money", json={"payment_terms_days": 0, "bank_balance": 1000, "bank_balance_on": today().isoformat(),
                                               "fixed_costs": [{"name": "Rent", "amount": 50000, "day": 1, "kind": "rent"}]})
    await knowledge.settle()
    from app.agents import scheduler
    for day in range(17, 30):
        await scheduler.tick(at=ist(2026, 11, day, 10, 30))
    await followups.run_owner(owner, ist(2026, 11, 26, 10, 5))
    owner_phone = owner["phone"]
    sent = [m async for m in db().outbox.find({"channel": "whatsapp"})]
    assert sent and all(m["to"] == owner_phone for m in sent if m.get("kind") != "otp")


# ═════════════════════════ 3.11 Brand kit ═════════════════════════
async def test_primary_colour_reaches_the_website_launch_kit_and_next_invoice(client):
    c, uid, _ = await owner_on("9851000050", "running")
    assert (await c.post("/api/brand-kit/generate")).status_code == 200
    r = await c.put("/api/brand-kit/design", json={"colors": {"primary": "#7a2e8e", "secondary": "#4B1C58", "accent": "#F2A93B"},
                                                   "fonts": {"heading": "poppins", "body": "mukta"}})
    assert r.status_code == 200, r.text
    assert (await c.get("/api/site")).json()["accent"] == "#7A2E8E"     # the website, and the launch kit, which draws in the site's accent
    q = (await c.post("/api/quotes", json={"customer": {"name": "Rohit Shah", "phone": "98511 30001"},
                                           "items": [{"name": "Wardrobe", "qty": 1, "rate": 60000}]})).json()
    await c.patch(f"/api/quotes/{q['id']}", json={"status": "accepted"})
    inv = (await c.post(f"/api/quotes/{q['id']}/invoice", json={"due_in_days": 7})).json()
    html = (await c.get(f"/api/quotes/{inv['id']}/print")).text
    assert "--accent:#7A2E8E" in html
    # every message the kit wrote uses only Brain facts: the business name, no invented prices
    kit = (await c.get("/api/brand-kit")).json()
    text = str(kit["messages"])
    assert PROFILE["name"] in text and not re.search(r"₹\s?\d", text.replace("₹1,85,000", ""))


async def test_designs_render_hindi_text(client):
    c, uid, _ = await owner_on("9851000051", "lite")
    r = await c.post("/api/brand-kit/designs/festival", json={"fields": {"greeting": "शुभ दीपावली", "message": "आपके घर में खुशियाँ हों"}})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    img = Image.open(io.BytesIO(r.content))
    assert img.size == (1080, 1080)
    from app import designs
    f = designs._font("playfair", True, 40, True)        # an English-only heading font falls back to a Devanagari one
    assert "NotoSansDevanagari" in f.path
    # the shaped Hindi line has real width (no empty boxes collapsed to nothing)
    assert designs._font("mukta", False, 40, True).getlength("शुभ दीपावली") > 100


# ═════════════════════════ 3.10 SOP set ═════════════════════════
async def test_process_map_differs_for_a_trading_business_and_a_clinic(client):
    t, _, _ = await owner_on("9851000060", "lite", live=False, profile={**PROFILE, "name": "Om Electricals", "industry": "Wholesale distributor of electricals"})
    k, _, _ = await owner_on("9851000061", "lite", live=False, profile={**PROFILE, "name": "Smile Dental", "industry": "Dental clinic"})
    a = (await t.post("/api/sop-set/map")).json()["processes"]
    b = (await k.post("/api/sop-set/map")).json()["processes"]
    assert {x["title"] for x in a} != {x["title"] for x in b}
    assert any("stock" in x["title"].lower() or "dispatch" in x["title"].lower() for x in a)
    assert any("patient" in x["title"].lower() or "appointment" in x["title"].lower() for x in b)


async def test_a_batch_of_ten_sops_uses_twenty_runs_and_refunds_failures(client, monkeypatch):
    c, uid, _ = await owner_on("9851000062", "lite", live=False)
    procs = (await c.post("/api/sop-set/map")).json()["processes"]
    keys = [p["key"] for p in procs[:10]]
    start = await used_runs(uid)
    # not enough runs: refused up front, with the cost shown
    await db().usage.update_one({"user_id": uid}, {"$set": {"used": plans.monthly_runs(await owner_doc(uid)) - 15}})
    r = await c.post("/api/sop-set/write", json={"keys": keys})
    assert r.status_code == 402 and r.json()["detail"]["need"] == 20
    await db().usage.update_one({"user_id": uid}, {"$set": {"used": start}})

    real, calls = kit_tasks.sop_scripts, {"n": 0}

    def flaky(p, sop):
        calls["n"] += 1
        if calls["n"] in (3, 7):
            async def busy():
                raise AIError("busy")
            return busy()
        return real(p, sop)
    monkeypatch.setattr(kit_tasks, "sop_scripts", flaky)
    r = await c.post("/api/sop-set/write", json={"keys": keys})
    assert r.status_code == 200 and r.json() == {"started": 10, "runs": 20}
    await sopset.settle()
    assert await used_runs(uid) == start + 18                       # 20 runs, 2 refunded
    assert await db().runs.count_documents({"user_id": uid, "kind": "kit:sop_scripts", "status": "refunded"}) == 2
    s = (await c.get("/api/sop-set")).json()
    assert s["batch"]["done"] == 10 and s["batch"]["running"] is False
    written = [p for p in s["processes"] if p["key"] in keys]
    assert all(p["sop_id"] for p in written) and s["without_sop"] == len(procs) - 10
    sop = await db().kits.find_one({"_id": written[0]["sop_id"]})
    assert sop["content"]["scripts"] and sop["content"]["done_when"] and sop["function"]
    assert (await c.post("/api/sop-set/write", json={"keys": keys})).status_code == 400     # already written


async def test_editing_an_sop_keeps_the_previous_version(client):
    c, uid, _ = await owner_on("9851000063", "lite", live=False)
    s = (await c.post("/api/kits/sop", json={"inputs": {"process": "Handling a complaint", "notes": ""}})).json()
    content = {**s["content"], "title": "Handling a customer complaint"}
    assert (await c.put(f"/api/kits/sop/{s['id']}", json={"content": content})).status_code == 200
    [v] = (await c.get(f"/api/kits/sop/{s['id']}/versions")).json()
    assert v["title"] == s["title"] and v["content"]["title"] == s["content"]["title"]
    back = await c.post(f"/api/kits/sop/{s['id']}/versions/{v['id']}/restore")
    assert back.json()["content"]["title"] == s["content"]["title"]
    assert len((await c.get(f"/api/kits/sop/{s['id']}/versions")).json()) == 2
    page = await c.get(f"/api/kits/sop/{s['id']}/print")
    assert page.status_code == 200 and s["content"]["title"] in page.text


# ═════════════════════════ 3.12 Mark as posted ═════════════════════════
async def test_a_post_marked_as_posted_counts_once_and_reaches_the_dashboard(client):
    c, uid, _ = await owner_on("9851000070", "lite")
    month = today().strftime("%Y-%m")
    cal = (await c.post("/api/calendar", json={"month": month})).json()["calendar"]
    post = cal["posts"][0]
    ref = f"cal:{month}:{post['date']}"
    d0 = {t["key"]: t for t in (await c.get("/api/dashboard")).json()["tiles"]}
    assert d0["social"]["data"]["month"] == 0
    for _ in range(2):
        r = await c.post("/api/social/posted", json={"ref": ref, "platform": "Instagram"})
        assert r.status_code == 200
    assert await db().social_posts.count_documents({"owner_id": uid}) == 1
    d1 = {t["key"]: t for t in (await c.get("/api/dashboard")).json()["tiles"]}
    assert d1["social"]["data"]["month"] == 1 and d1["social"]["data"]["by_platform"] == {"Instagram": 1}
    cal = (await c.get(f"/api/calendar/{month}")).json()["calendar"]
    assert cal["posts"][0]["posted"] is True                      # the calendar's tick follows the log
    # the calendar's own toggle un-marks it, and the log follows
    await c.post(f"/api/calendar/{month}/posted/{post['date']}")
    assert await db().social_posts.count_documents({"owner_id": uid}) == 0
    assert (await c.post("/api/social/posted", json={"ref": f"cal:{month}:2020-01-01"})).status_code == 404
    assert (await c.post("/api/social/posted", json={"ref": "nope"})).status_code == 400


# ═════════════════════════ 3.4 Ways to get more leads ═════════════════════════
async def test_lead_ideas_fit_the_business_type_and_open_their_feature(client, monkeypatch):
    c, uid, _ = await owner_on("9851000080", "program", live=False, profile={**PROFILE, "name": "Om Electricals", "industry": "Wholesale distributor of electricals"})
    d = (await c.post("/api/levers/ideas")).json()
    assert d["business_type"] == "trading"
    ideas = d["current"]["ideas"]
    assert 5 <= len(ideas) <= 7
    assert all(i["channel"] in leadideas.fitting("trading") for i in ideas)
    assert not [i for i in ideas if i["channel"] in ("delivery_apps", "doctor_referrals", "health_camp")]
    from app import tools
    for i in ideas:
        if not i["feature"]:
            continue
        path = plans.META[i["feature"]]["path"]
        if path:
            assert i["path"].split("?")[0] == path
        elif i["path"].startswith("/tools?tool="):
            assert tools.TOOLS[i["path"].split("=")[1]].feature == i["feature"]
        else:
            from app.agents import catalog
            assert i["path"] == "/agents" and any(s.feature == i["feature"] for s in catalog.AGENTS.values())
    r = await c.post("/api/levers/ideas/0/tried", json={"tried": True})
    assert r.json()["current"]["tried"] == 1

    # an AI that suggests channels that don't fit is filtered; too few left = the run is refunded
    async def off_topic(system, prompt, mock, max_tokens=1200):
        return {"ideas": [{"channel": "delivery_apps", "title": "x"}, {"channel": "health_camp", "title": "y"}]}, None
    monkeypatch.setattr(leadideas, "complete", off_topic)
    before = await used_runs(uid)
    assert (await c.post("/api/levers/ideas")).status_code == 502
    assert await used_runs(uid) == before


async def test_levers_show_the_compounding_in_rupees(client):
    c, uid, _ = await owner_on("9851000081", "program", live=False)
    r = (await c.put("/api/levers", json={"customers": 100, "avg_value": 10000, "frequency": 2})).json()["levers"]["result"]
    assert r["revenue"] == 2000000 and r["new_revenue"] == 2662000 and r["multiplier"] == 1.331 and r["growth_pct"] == 33.1
    assert [s["after"] for s in r["steps"]] == [2200000, 2420000, 2662000] and r["simple_sum_pct"] == 30


# ═════════════════════════ 3.2 Smart Dashboard ═════════════════════════
TILES = {"free": ({"today", "leads", "website", "knowledge"}, {"pipeline", "target", "money", "cash_flow", "invoices", "social", "compliance", "sops", "growth"}),
         "lite": ({"today", "leads", "target", "cash_flow", "social", "compliance", "website", "sops", "knowledge"}, {"pipeline", "money", "invoices", "growth"}),
         "running": ({"today", "leads", "pipeline", "target", "money", "cash_flow", "invoices", "social", "compliance", "website", "sops", "knowledge"}, {"growth"}),
         "growth": ({"today", "leads", "pipeline", "target", "money", "cash_flow", "invoices", "social", "compliance", "website", "sops", "knowledge", "growth"}, set())}


async def test_each_plan_sees_its_own_tiles_plus_teasers(client):
    c, uid, _ = await owner_on("9851000090", "free")
    admin = await new_owner(ADMIN)
    for plan, (on, teasers) in TILES.items():
        if plan != "free":
            await admin.patch(f"/api/admin/users/{uid}", json={"plan": plan})
        d = (await c.get("/api/dashboard")).json()
        got_on = {t["key"] for t in d["tiles"] if t["state"] == "on"}
        got_teasers = {t["key"] for t in d["tiles"] if t["state"] == "teaser"}
        assert (got_on, got_teasers) == (on, teasers), plan
        assert [t["state"] for t in d["tiles"]] == sorted((t["state"] for t in d["tiles"]), key=lambda s: s == "teaser")
    # the admin switches a teaser off: that locked tile disappears for owners without it
    await admin.patch(f"/api/admin/users/{uid}", json={"plan": "lite"})
    assert (await admin.patch("/api/admin/config/features/money_owed", json={"teaser": False})).status_code == 200
    assert "money" not in {t["key"] for t in (await c.get("/api/dashboard")).json()["tiles"]}
    # and switches a tile off for everyone
    w = (await admin.get("/api/admin/dashboard-widgets")).json()["widgets"]
    body = {"widgets": [{"key": x["key"], "on": x["key"] != "sops", "order": x["order"]} for x in w]}
    assert (await admin.put("/api/admin/dashboard-widgets", json=body)).status_code == 200
    assert "sops" not in {t["key"] for t in (await c.get("/api/dashboard")).json()["tiles"]}
    assert (await admin.put("/api/admin/dashboard-widgets", json={"widgets": [{"key": "today", "on": False, "order": 0}]})).status_code == 400


async def test_a_new_inquiry_reaches_leads_and_today_and_a_payment_reaches_money_and_cash(client):
    c, uid, site = await owner_on("9851000091", "running")
    await c.put("/api/knowledge/money", json={"payment_terms_days": 0, "bank_balance": 50000, "bank_balance_on": today().isoformat(), "fixed_costs": []})
    due = (await c.post("/api/money", json={"customer": "Mehta Traders", "amount": 30000, "due_date": (today() + timedelta(days=60)).isoformat()})).json()
    d0 = {t["key"]: t for t in (await c.get("/api/dashboard")).json()["tiles"]}
    again = await c.get("/api/dashboard")
    assert again.json()["cached"] is True
    await client.post(f"/s/{site['slug']}/inquiry", json={"name": "Neha Joshi", "phone": "98511 40001", "message": "Kitchen"})
    d1 = {t["key"]: t for t in (await c.get("/api/dashboard")).json()["tiles"]}
    assert d1["leads"]["data"]["today"] == d0["leads"]["data"]["today"] + 1
    assert d1["leads"]["data"]["by_source"][0] == {"source": "Website", "n": 1}
    inq = lambda d: next(i["n"] for i in d["today"]["data"]["items"] if i["key"] == "inquiries")  # noqa: E731
    assert inq(d1) == inq(d0) + 1
    assert (await c.patch(f"/api/money/{due['id']}", json={"status": "paid"})).status_code == 200
    d2 = {t["key"]: t for t in (await c.get("/api/dashboard")).json()["tiles"]}
    assert d2["money"]["data"]["collected_month"] == 30000 and d0["money"]["data"]["collected_month"] == 0
    assert d2["cash_flow"]["data"]["lowest"] == d0["cash_flow"]["data"]["lowest"] + 30000


async def test_dashboard_layout_staff_and_landing(client):
    c, uid, _ = await owner_on("9851000092", "growth")
    d = (await c.get("/api/dashboard")).json()
    keys = [t["key"] for t in d["tiles"]]
    order = ["money"] + [k for k in keys if k != "money"]
    d = (await c.put("/api/dashboard/layout", json={"order": order, "hidden": ["sops"]})).json()
    assert [t["key"] for t in d["tiles"]][0] == "money" and "sops" not in [t["key"] for t in d["tiles"]]
    assert d["hidden"] == [{"key": "sops", "title": "SOPs", "state": "on"}]
    assert (await c.put("/api/dashboard/layout", json={"hidden": ["today"]})).status_code == 400
    assert (await c.put("/api/dashboard/layout", json={"order": ["nope"]})).status_code == 400
    assert (await c.delete("/api/dashboard/layout")).json()["customised"] is False
    staff = await new_owner("9851000093")
    await db().users.update_one({"_id": (await staff.get("/api/me")).json()["user"]["id"]}, {"$set": {"team_of": uid, "team_role": "staff"}})
    assert (await staff.get("/api/dashboard")).status_code == 403
    assert (await staff.get("/api/me")).json()["landing"] == "/home"
    # twelve tiles in one request, timed on the server (the 1.5 s budget is for the whole page from Singapore)
    dashboard_cache.bust(uid)
    d = (await c.get("/api/dashboard")).json()
    assert len([t for t in d["tiles"] if t["state"] == "on"]) == 13 and d["compute_ms"] < 1500


async def test_one_failing_tile_never_breaks_the_dashboard(client, monkeypatch):
    c, uid, _ = await owner_on("9851000094", "running")
    from app import dashboard_widgets

    async def broken(ws, owner, t):
        raise RuntimeError("boom")
    monkeypatch.setattr(dashboard_widgets.REGISTRY["pipeline"], "compute", broken)
    tiles = {t["key"]: t for t in (await c.get("/api/dashboard")).json()["tiles"]}
    assert tiles["pipeline"]["data"] == {"error": "This tile couldn't load just now."} and tiles["leads"]["data"].get("error") is None


# ═════════════════════════ automations: owner-only checks ═════════════════════════
async def test_owner_checks_only_run_for_owners_who_set_them_up(client):
    from app.agents import catalog
    c, uid, _ = await owner_on("9851000100", "lite", live=False)
    owner = await owner_doc(uid)
    for key in ("cashflow_watch", "compliance_reminders", "target_check", "payment_followups_auto"):
        assert catalog.AGENTS[key].ready and uid not in await catalog.AGENTS[key].ready([owner]), key
    agents = (await c.get("/api/agents")).json()
    assert {a["key"] for a in agents["alerts"]} == {"cashflow_watch", "compliance_reminders", "target_check"}
    assert [a["key"] for a in agents["approve_once"]] == ["payment_followups_auto"]
    await c.put("/api/knowledge/targets", json={"monthly_revenue": 500000})
    await knowledge.settle()
    assert uid in await catalog.AGENTS["target_check"].ready([owner])


async def test_email_events_need_the_secret_and_a_valid_sns_signature(client, monkeypatch):
    import dataclasses
    from app import config, mailer
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, email_events_secret="s3cret-events-path-0001"))
    assert (await client.post("/api/hooks/email-events/wrong", json={})).status_code == 404
    assert (await client.post("/api/hooks/email-events/s3cret-events-path-0001", json={"Type": "Notification"})).status_code == 401
    c, uid, _ = await owner_on("9851000110", "running", live=False)
    await db().email_log.insert_one({"_id": "e1", "owner_id": uid, "to": "accounts@mehta.example", "message_id": "m-1", "status": "sent", "at": now()})

    async def ok(msg):
        return True
    monkeypatch.setattr(mailer, "sns_verified", ok)
    import json
    event = {"eventType": "Bounce", "mail": {"messageId": "m-1"}, "bounce": {"bounceType": "Permanent"}}
    r = await client.post("/api/hooks/email-events/s3cret-events-path-0001", json={"Type": "Notification", "Message": json.dumps(event)})
    assert r.json() == {"ok": True, "recorded": True}
    assert await mailer.suppressed(uid, "accounts@mehta.example")
    assert not mailer.sns_url_ok("https://evil.example/cert.pem", ".pem") and mailer.sns_url_ok("https://sns.ap-south-1.amazonaws.com/x.pem", ".pem")
    admin = await new_owner(ADMIN)
    e = (await admin.get("/api/admin/email")).json()
    [row] = [x for x in e["owners"] if x["owner_id"] == uid]
    assert row["bounced"] == 1 and row["bounce_rate"] == 100 and row["warning"]
    assert (await admin.post(f"/api/admin/owners/{uid}/email-pause", json={"paused": True})).json()["paused"] is True
    r = await mailer.send(owner=await owner_doc(uid), to="new@customer.example", subject="x", text="y", kind="test", from_name="Shop")
    assert r["via"] == "paused" and not r["sent"]


async def test_each_content_agent_post_and_a_rewritten_calendar_keep_their_posted_marks(client):
    c, uid, _ = await owner_on("9851000071", "lite")
    r = (await c.post("/api/tools/content/run", json={"inputs": {"focus": "Diwali", "platform": "All of them"}})).json()
    out = await db().outputs.find_one({"_id": r["id"]})
    posts = next(i for i, s in enumerate(out["content"]["sections"]) if len(s.get("points") or []) >= 3)
    assert (await c.post("/api/social/posted", json={"ref": f"out:{r['id']}:{posts}-2", "platform": "LinkedIn"})).status_code == 200
    assert (await c.post("/api/social/posted", json={"ref": f"out:{r['id']}:{posts}-99"})).status_code == 404
    refs = (await c.get(f"/api/social/refs?prefix=out:{r['id']}")).json()
    assert list(refs) == [f"out:{r['id']}:{posts}-2"]
    # rewriting the month's calendar never un-posts a post that went out
    month = today().strftime("%Y-%m")
    cal = (await c.post("/api/calendar", json={"month": month})).json()["calendar"]
    first = cal["posts"][0]
    await c.post("/api/social/posted", json={"ref": f"cal:{month}:{first['date']}"})
    again = (await c.post("/api/calendar", json={"month": month, "focus": "Wedding season"})).json()["calendar"]
    kept = next(p for p in again["posts"] if p["date"] == first["date"])
    assert kept["posted"] is True and kept["caption"] == first["caption"]


# ═════════════════════════ fixes from the independent review ═════════════════════════
async def test_brand_kit_edits_survive_a_rewrite_and_a_knowledge_hub_change(client):
    c, uid, _ = await owner_on("9851000200", "lite")
    await c.post("/api/brand-kit/generate")
    assert (await c.put("/api/brand-kit/messages", json={"path": "marketing.tagline", "value": "MY TAGLINE"})).status_code == 200
    kit = (await c.get("/api/brand-kit")).json()
    assert kit["edited"] == {"marketing.tagline": True}
    await c.post("/api/brand-kit/generate")
    assert (await c.get("/api/brand-kit")).json()["messages"]["marketing"]["tagline"] == "MY TAGLINE"
    await c.put("/api/business", json={**PROFILE, "industry": "Modular kitchens and wardrobes"})
    await knowledge.settle()
    kit = (await c.get("/api/brand-kit")).json()
    assert kit["messages"]["marketing"]["tagline"] == "MY TAGLINE"
    assert [u for u in (await c.get("/api/knowledge")).json()["updates"] if u["key"] == "brand_kit"][0]["reason"] == "edited"
    # a phone change rewrites only the signature, without an AI run
    runs = await used_runs(uid)
    await c.put("/api/business", json={**PROFILE, "industry": "Modular kitchens and wardrobes", "whatsapp": "98200 22222"})
    await knowledge.settle()
    assert await used_runs(uid) == runs
    assert "98200 22222" in (await c.get("/api/brand-kit")).json()["messages"]["sales"]["email_signature"]
    reset = (await c.post("/api/brand-kit/messages/reset", json={"path": "marketing.tagline"})).json()
    assert reset["edited"] == {} and reset["messages"]["marketing"]["tagline"] != "MY TAGLINE"


async def test_growth_whatsapp_reminders_respect_exclusions_and_replies(client):
    from app.agents import runners
    c, uid, _ = await owner_on("9851000201", "growth", live=False)
    keep = await _due(c, "Mehta Traders", "accounts@mehta.example", "2026-10-01")
    other = await _due(c, "Sharma Stores", "pay@sharma.example", "2026-10-01")
    await db().dues.update_one({"_id": other["id"]}, {"$set": {"phone": "+919851100002"}})
    s = (await c.get("/api/followups/auto")).json()
    await c.post("/api/followups/auto/approve", json={"hash": s["hash"], "whatsapp": True})
    await c.post("/api/followups/auto/exclude", json={"due_id": keep["id"]})
    await c.post(f"/api/money/{other['id']}/replied")
    owner = await owner_doc(uid)
    note = await runners.payment_reminder(owner, {"at": ist(2026, 10, 5, 10, 5)})
    assert await db().approvals.count_documents({"ws": uid, "kind": "reminder", "status": "sent"}) == 0
    assert await db().approvals.count_documents({"ws": uid, "kind": "reminder", "status": "pending"}) == 2
    assert note == "2 payment reminders ready in your Send list."


async def test_a_wording_change_pauses_follow_ups_until_approved_again(client):
    c, uid, _ = await owner_on("9851000202", "running", live=False)
    await _due(c, "Mehta Traders", "accounts@mehta.example", "2026-11-20")
    s = (await c.get("/api/followups/auto")).json()
    await c.post("/api/followups/auto/approve", json={"hash": s["hash"]})
    await c.put("/api/business", json={**PROFILE, "payment_note": "Pay at https://rzp.io/l/sgi"})
    owner = await owner_doc(uid)
    r = await followups.run_owner(owner, ist(2026, 11, 20, 10, 5))
    assert r["skipped"] == "wording_changed" and not await db().outbox.count_documents({"channel": "email"})
    assert await db().notices.count_documents({"user_id": uid, "text": {"$regex": "wording changed"}}) == 1
    s = (await c.get("/api/followups/auto")).json()
    assert s["on"] and not s["approved_current"]
    await c.post("/api/followups/auto/approve", json={"hash": s["hash"]})
    assert (await followups.run_owner(owner, ist(2026, 11, 20, 10, 5)))["sent"] == 1


async def test_follow_ups_retry_after_a_limit_start_again_on_a_new_date_and_space_a_late_catch_up(client, monkeypatch):
    from app import mailer
    c, uid, _ = await owner_on("9851000203", "running", live=False)
    d = await _due(c, "Mehta Traders", "accounts@mehta.example", "2026-10-01")
    s = (await c.get("/api/followups/auto")).json()
    await c.post("/api/followups/auto/approve", json={"hash": s["hash"]})
    owner = await owner_doc(uid)
    real = mailer._take_slot

    async def full(owner_id):
        return False
    monkeypatch.setattr(mailer, "_take_slot", full)
    assert (await followups.run_owner(owner, ist(2026, 11, 1, 10, 5)))["failed"] == 1     # today's limit is reached
    monkeypatch.setattr(mailer, "_take_slot", real)
    sent_days = []
    for day in range(2, 20):
        if (await followups.run_owner(owner, ist(2026, 11, day, 10, 5)))["sent"]:
            sent_days.append(day)
    assert sent_days == [2, 5, 8, 11]       # opted in late: caught up, but 3 days apart, at most 4
    # a new due date starts a fresh round
    assert (await c.patch(f"/api/money/{d['id']}", json={"due_date": "2026-12-10"})).status_code == 200
    assert (await followups.run_owner(owner, ist(2026, 12, 7, 10, 5)))["sent"] == 1


async def test_an_extension_moves_the_filing_instead_of_duplicating_it(client):
    c, uid, _ = await owner_on("9851000204", "lite", live=False)
    await c.put("/api/knowledge/identity", json={"entity": "pvt_ltd", "gst_registered": True, "gstin": "27ABCDE1234F1Z5", "gst_scheme": "monthly",
                                                  "state": "Maharashtra", "staff": 5, "registrations": ["fssai"]})
    await knowledge.settle()
    await c.get("/api/compliance")
    item = await db().compliance_items.find_one({"owner_id": uid, "rule_key": "gstr3b"}, sort=[("due_date", 1)])
    await c.post(f"/api/compliance/items/{item['_id']}/done")
    nxt = await db().compliance_items.find_one({"owner_id": uid, "rule_key": "gstr3b", "status": {"$ne": "done"}}, sort=[("due_date", 1)])
    admin = await new_owner(ADMIN)
    assert (await admin.put("/api/admin/compliance/rules/gstr3b/override", json={"date": "2026-02-30", "new_date": "2026-03-01"})).status_code == 422
    moved = (date.fromisoformat(nxt["due_date"]) + timedelta(days=5)).isoformat()
    for d_, day_ in ((item["due_date"], (date.fromisoformat(item["due_date"]) + timedelta(days=5)).isoformat()), (nxt["due_date"], moved)):
        assert (await admin.put("/api/admin/compliance/rules/gstr3b/override", json={"date": d_, "new_date": day_})).status_code == 200
    await c.get("/api/compliance")
    same = [i async for i in db().compliance_items.find({"owner_id": uid, "rule_key": "gstr3b", "period": nxt["period"]})]
    assert len(same) == 1 and same[0]["due_date"] == moved and same[0]["_id"] == nxt["_id"]
    done = [i async for i in db().compliance_items.find({"owner_id": uid, "rule_key": "gstr3b", "period": item["period"]})]
    assert len(done) == 1 and done[0]["status"] == "done"
    # a renewed licence asks for its new expiry date instead of guessing one
    [fssai] = (await c.get("/api/compliance")).json()["needs_date"]
    await c.patch(f"/api/compliance/items/{fssai['id']}", json={"due_date": (today() + timedelta(days=40)).isoformat()})
    r = (await c.post(f"/api/compliance/items/{fssai['id']}/done")).json()
    assert r["next"]["status"] == "needs_date" and r["next"]["rule_key"] == "fssai"
    assert (await c.post("/api/compliance/items/nope/undo")).status_code == 404


async def test_a_stale_sop_batch_doesnt_lock_the_owner_out(client):
    c, uid, _ = await owner_on("9851000205", "lite", live=False)
    procs = (await c.post("/api/sop-set/map")).json()["processes"]
    await db().sop_maps.update_one({"_id": uid}, {"$set": {"batch": {"running": True, "started_at": now() - timedelta(minutes=5)}}})
    assert (await c.post("/api/sop-set/write", json={"keys": [procs[0]["key"]]})).status_code == 409
    await db().sop_maps.update_one({"_id": uid}, {"$set": {"batch.started_at": now() - timedelta(hours=2)}})
    assert (await c.post("/api/sop-set/write", json={"keys": [procs[0]["key"]]})).status_code == 200
    await sopset.settle()
    assert (await c.get("/api/sop-set")).json()["batch"]["running"] is False


async def test_a_sale_counts_in_the_month_it_was_won_and_once(client):
    c, uid, site = await owner_on("9851000206", "running")
    await client.post(f"/s/{site['slug']}/inquiry", json={"name": "Rohit Shah", "phone": "98511 30001", "message": "Kitchen"})
    lead = await db().leads.find_one({"owner_id": uid})
    await c.patch(f"/api/leads/{lead['_id']}", json={"status": "won", "value": 250000})
    won_at = (await db().leads.find_one({"_id": lead["_id"]}))["won_at"]
    t = won_at.astimezone(IST).date()
    assert (await sales.month_sales(uid, "invoiced", t))["sales"] == 250000
    await c.patch(f"/api/leads/{lead['_id']}", json={"note": "Called again"})          # an edit later doesn't move the sale
    nxt = (t.replace(day=28) + timedelta(days=4)).replace(day=1)
    await db().leads.update_one({"_id": lead["_id"]}, {"$set": {"updated_at": sales.ist_midnight(nxt) + timedelta(hours=12)}})
    assert (await sales.month_sales(uid, "invoiced", nxt))["sales"] == 0
    # invoiced next month: the invoice counts there, the won lead no longer counts here
    await db().quotes.insert_one({"_id": "inv-x", "ws": uid, "kind": "invoice", "lead_id": lead["_id"], "totals": {"total": 250000},
                                  "created_at": sales.ist_midnight(nxt) + timedelta(hours=12)})
    assert (await sales.month_sales(uid, "invoiced", t))["sales"] == 0 and (await sales.month_sales(uid, "invoiced", nxt))["sales"] == 250000


async def test_links_in_automatic_reminders_are_parsed_strictly():
    from app.agents.approvals import only_payment_links
    assert only_payment_links("Pay at https://rzp.io/l/abc") and only_payment_links("Pay by UPI to shop@okaxis")
    for bad in ("https://evil.example?r=a.rzp.io", "see evil.example", "pay.evil.example/x", "https://user@rzp.io.evil.com/x"):
        assert not only_payment_links(bad), bad
