"""v2.2 Win-Back (Action Program): customers who haven't bought for a while get a comeback hello — drafted for the owner,
sent only on a tap. Every name, phone number and email here is invented."""
import csv
import io
import re
from datetime import date, datetime, time, timedelta

from app.ai import winback_tasks
from app.agents import scheduler
from app.config import Settings
from app.db import IST, db, now
from app.routers import winback
from app.services import new_id
from tests.conftest import PROFILE, new_owner

ADMIN = "9999900000"
BUSINESS = PROFILE["name"]
QUIET = ("week_plan", "calendar_auto", "digest", "payment_reminder", "quote_followup", "customer_desk", "ceo_report", "monthly_review",
         "results", "recall")


async def owner_on(phone: str, plan: str):
    c = await new_owner(phone)
    assert (await c.put("/api/business", json=PROFILE)).status_code == 200
    uid = (await c.get("/api/me")).json()["user"]["id"]
    if plan != "free":
        admin = await new_owner(ADMIN)
        assert (await admin.patch(f"/api/admin/users/{uid}", json={"plan": plan})).status_code == 200
    for k in QUIET:          # scheduled agents this file isn't about, so a fake Tuesday can't wake them
        assert (await c.patch(f"/api/agents/{k}", json={"on": False})).status_code in (200, 403), k
    return c, uid


def today() -> date:
    return now().astimezone(IST).date()


def dmy(days_ago: int) -> str:
    return (today() - timedelta(days=days_ago)).strftime("%d-%m-%Y")


def sheet(rows: list[tuple]) -> bytes:
    """(customer, mobile, days ago, rupees) → a Tally-like sales register CSV."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerows([[BUSINESS], ["Sales Register"], [], ["Date", "Party Name", "Mobile", "Invoice No", "Gross Total"]])
    for i, (name, phone, ago, amount) in enumerate(rows):
        w.writerow([dmy(ago), name, phone, f"WB-{i + 1:03d}-{ago}", f"{amount:,}"])
    return buf.getvalue().encode()


async def upload(c, rows: list[tuple]):
    r = await c.post("/api/sales-upload", files={"file": ("sales.csv", sheet(rows), "text/csv")})
    assert r.status_code == 200, r.text
    return r.json()


async def customer(ws: str, name: str, phone: str = "", **extra):
    doc = {"_id": new_id(), "ws": ws, "name": name, "phone": phone, "tier": "", "total_value": 0, "purchases": 0, "first_purchase": "",
           "last_purchase": "", "created_at": now(), **extra}
    await db().customers.insert_one(doc)
    return doc


def last_tuesday_1105() -> datetime:
    """The most recent Tuesday 11:05 India time (the agent's window opens at 11:00)."""
    d = today() - timedelta(days=(today().weekday() - 1) % 7)
    at = datetime.combine(d, time(11, 5), tzinfo=IST)
    return at if at <= now() else at - timedelta(days=7)


async def win_back_runs(uid: str) -> int:
    return await db().runs.count_documents({"user_id": uid, "kind": "win_back", "status": "done"})


async def approvals(uid: str, **q) -> list[dict]:
    return [a async for a in db().approvals.find({"ws": uid, "agent": "win_back", **q}).sort("created_at", 1)]


async def switch_on(c, **settings) -> dict:
    if settings:
        assert (await c.put("/api/win-back/settings", json=settings)).status_code == 200
    s = (await c.get("/api/win-back")).json()["sample"]
    r = await c.put("/api/win-back/settings", json={"on": True, "sample_key": s["key"]})
    assert r.status_code == 200, r.text
    assert r.json()["on"] is True
    return r.json()


STANDARD = [   # (name, mobile, days ago, ₹)
    ("Sunrise Fabrics", "98712 00001", 210, 90000), ("Sunrise Fabrics", "98712 00001", 150, 60000), ("Sunrise Fabrics", "98712 00001", 100, 50000),
    ("Kaveri Foods", "98712 00002", 300, 40000), ("Kaveri Foods", "98712 00002", 75, 30000),
    ("Lotus Hardware", "98712 00003", 400, 80000), ("Lotus Hardware", "98712 00003", 20, 20000),        # bought 20 days ago
    ("Tanvi Kapadia", "98712 00004", 95, 15000),                                                      # bought once
    ("Old Mill Traders", "98712 00005", 900, 70000), ("Old Mill Traders", "98712 00005", 800, 70000),  # over 2 years ago
    ("Cash", "", 120, 5000), ("Cash", "", 200, 5000),                                                 # walk-in: never a customer
    ("Pinewood Studio", "", 260, 25000), ("Pinewood Studio", "", 130, 25000),                         # no number in the sheet
    ("Blue Fern Cafe", "98712 00008", 220, 12000), ("Blue Fern Cafe", "98712 00008", 110, 12000),     # just bought (Customers list)
    ("Silverline Tiles", "98712 00009", 240, 33000), ("Silverline Tiles", "98712 00009", 120, 33000),  # messaged 30 days ago
    ("Coral Opticals", "98712 00010", 260, 9000), ("Coral Opticals", "98712 00010", 130, 9000),       # messaged 100 days ago
]


async def standard_data(c, uid: str) -> None:
    await upload(c, STANDARD)
    await customer(uid, "Pinewood Studio", "", email="accounts@pinewood.example")             # only an email: reached by email
    await customer(uid, "Sunrise Fabrics", "+919871200001", email="buy@sunrise.example")         # same phone: its email joins
    await customer(uid, "Blue Fern Cafe", "+919871200008", last_purchase=(today() - timedelta(days=4)).isoformat(), purchases=3)
    await customer(uid, "Greenleaf Agencies", "+919871200011", total_value=120000, purchases=5,
                   last_purchase=(today() - timedelta(days=95)).isoformat(), email="hello@greenleaf.example")   # Customers list only
    await customer(uid, "Opted Out Interiors", "+919871200012", total_value=500000, purchases=6,
                   last_purchase=(today() - timedelta(days=150)).isoformat(), opted_out=True)
    await db().winback_log.insert_many([
        {"_id": new_id(), "ws": uid, "key": "9871200009", "phone_key": "9871200009", "at": now() - timedelta(days=30), "channel": "whatsapp", "status": "sent"},
        {"_id": new_id(), "ws": uid, "key": "9871200010", "phone_key": "9871200010", "at": now() - timedelta(days=100), "channel": "whatsapp", "status": "sent"},
    ])


# ───────────────────────── who is a candidate ─────────────────────────
async def test_candidates_come_from_the_customers_list_and_uploaded_sales(client):
    c, uid = await owner_on("9871100001", "program")
    await standard_data(c, uid)
    r = await c.get("/api/win-back/candidates")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    names = [i["name"] for i in items]
    # biggest spenders first: Sunrise 2,00,000 · Greenleaf 1,20,000 (Customers list) · Kaveri 70,000 · Pinewood 50,000 · Coral 18,000
    assert names == ["Sunrise Fabrics", "Greenleaf Agencies", "Kaveri Foods", "Pinewood Studio", "Coral Opticals"], names
    by = {i["name"]: i for i in items}
    s = by["Sunrise Fabrics"]
    assert s["purchases"] == 3 and s["total"] == 200000 and s["kind"] == "repeat" and s["source"] == "sales"
    assert s["days_since"] == 100 and s["last_purchase"] == (today() - timedelta(days=100)).isoformat()
    assert s["email"] == "buy@sunrise.example" and s["channels"] == ["whatsapp", "email"]
    assert by["Pinewood Studio"]["channels"] == ["email"] and by["Pinewood Studio"]["phone"] == ""
    assert by["Greenleaf Agencies"]["source"] == "customers list" and by["Greenleaf Agencies"]["kind"] == "loyal"
    # left out: bought 20 days ago (Lotus), once (Tanvi, while 2 purchases are needed), over 2 years ago (Old Mill), the walk-in
    # "Cash", just bought per the Customers list (Blue Fern), opted out, messaged by Win-Back 30 days ago (Silverline)
    for gone in ("Lotus Hardware", "Tanvi Kapadia", "Old Mill Traders", "Cash", "Blue Fern Cafe", "Opted Out Interiors", "Silverline Tiles"):
        assert gone not in names, gone

    assert (await c.put("/api/win-back/settings", json={"min_purchases": 1})).status_code == 200
    names = [i["name"] for i in (await c.get("/api/win-back/candidates")).json()["items"]]
    assert "Tanvi Kapadia" in names
    assert (await c.put("/api/win-back/settings", json={"min_purchases": 1, "days": 120})).status_code == 200
    names = [i["name"] for i in (await c.get("/api/win-back/candidates")).json()["items"]]
    assert names == ["Pinewood Studio", "Coral Opticals"]          # only their last purchases (130 days) are 120+ days ago
    assert (await c.put("/api/win-back/settings", json={"days": 75})).status_code == 422      # 45, 60, 90 or 120
    assert (await c.put("/api/win-back/settings", json={"per_week": 41})).status_code == 422


# ───────────────────────── the weekly agent ─────────────────────────
async def test_the_tuesday_agent_drafts_only_when_switched_on_and_only_up_to_per_week(client):
    c, uid = await owner_on("9871100002", "program")
    await standard_data(c, uid)
    tue = last_tuesday_1105()
    d = (await c.get("/api/win-back")).json()
    assert d["on"] is False and d["settings"]["per_week"] == 15 and d["sample"]["text"].startswith("Hello Sunrise,")
    await scheduler.tick(at=tue)
    assert await approvals(uid) == [] and await db().winback_drafts.count_documents({"ws": uid}) == 0     # off: nothing

    # switching on needs the sample that is on screen right now
    assert (await c.put("/api/win-back/settings", json={"on": True})).status_code == 409
    assert (await c.put("/api/win-back/settings", json={"on": True, "sample_key": "stale"})).status_code == 409
    await switch_on(c, per_week=3)
    await scheduler.tick(at=tue)
    made = await approvals(uid)
    assert len(made) == 3 and all(a["status"] == "pending" and a["kind"] == "customer" for a in made)
    assert [a["to_name"] for a in made] == ["Sunrise Fabrics", "Greenleaf Agencies", "Kaveri Foods"]
    assert await win_back_runs(uid) == 1                          # one AI run wrote the week's wording
    texts = [a["text"] for a in made]
    assert texts[0].startswith("Hello Sunrise,") and texts[1].startswith("Namaste Greenleaf!")   # the mock AI's wording per kind
    assert all(BUSINESS in t and not re.search(r"\d|₹|%|discount|offer", t, re.I) for t in texts)   # no offer: none was typed
    # nothing left the hub: no WhatsApp to a customer, no email
    assert await db().outbox.count_documents({"channel": "email"}) == 0
    assert await db().outbox.count_documents({"channel": "whatsapp", "to": {"$regex": "^91987120"}}) == 0
    drafts = (await c.get("/api/win-back")).json()
    assert drafts["this_week"] == 3 and {x["email_draft"]["status"] for x in drafts["drafts"] if x["email_draft"]} == {"draft"}
    assert drafts["results"]["messaged"] == 0 and drafts["results"]["waiting"] == 3

    await scheduler.tick(at=tue + timedelta(minutes=30))           # same slot: no second batch
    assert len(await approvals(uid)) == 3 and await win_back_runs(uid) == 1
    log = (await db().agent_log.find_one({"ws": uid, "agent": "win_back"}, sort=[("at", -1)]))["text"]
    assert log.startswith("Win-Back: 3 customers")

    await scheduler.tick(at=tue + timedelta(days=7))               # the next Tuesday: the rest, never the same customer
    made = await approvals(uid)
    assert len(made) == 4 and len({a["to_name"] for a in made}) == 4       # + Coral; Pinewood has no number: an email draft only
    assert await db().winback_drafts.count_documents({"ws": uid}) == 5
    assert await win_back_runs(uid) == 2                           # one a week


async def test_the_ai_never_adds_an_offer_and_the_owners_offer_goes_in_exactly(client, monkeypatch):
    # the AI's wording is checked: a discount, a number, a link or a missing {name} throws that wording away
    business = BUSINESS
    good = {"whatsapp": "Hello {name}, it's Shree Ganesh Interiors. We miss you! Anything we can help with?", "subject": "We miss you"}
    raw = {"messages": {"loyal": {**good, "whatsapp": "Hello {name}, come back for 20% off this week!"},
                        "repeat": good,
                        "once": {**good, "whatsapp": "Hello {name}, see our new range at www.example-store.in and reply."}}}
    out = winback_tasks.checked(raw, business, list(winback_tasks.KINDS))
    assert list(out) == ["repeat"] and out["repeat"]["by"] == "ai"
    for bad in ("Hello there, we miss you at our shop! Anything you need?", "Hello {name}, flat Rs 500 cashback on your next order!",
                "Hello {name}, our best prices are back. Reply now!", "Hello {name} {city}, we miss you. Anything you need this month?"):
        assert not winback_tasks.usable(bad, business, need_name=True), bad
    assert winback_tasks.usable("Hello {name}, Shree Ganesh 24x7 Interiors misses you. Anything we can help with?", "Shree Ganesh 24x7 Interiors", True)

    c, uid = await owner_on("9871100003", "program")
    await standard_data(c, uid)

    async def greedy(system, prompt, mock, max_tokens=1200):
        return {"messages": {k: {"whatsapp": "Hello {name}! Big Diwali sale — 25% discount on everything, only this week.",
                                 "subject": "Sale!"} for k in winback_tasks.KINDS}}, None
    monkeypatch.setattr(winback_tasks, "complete", greedy)
    offer = "Free delivery on your next order this month"
    await switch_on(c, per_week=2, offer=offer)
    r = await c.post("/api/win-back/run")
    assert r.status_code == 200 and r.json()["status"] == "done", r.text
    made = await approvals(uid)
    assert len(made) == 2
    for a in made:
        assert "25%" not in a["text"] and "sale" not in a["text"].lower()          # the AI's discount never goes out
        assert a["text"].endswith(" " + offer + ".")                              # the owner's offer, exactly as typed
    assert await win_back_runs(uid) == 0          # the unusable draft was refunded: no run used
    week = await db().winback_weeks.find_one({"ws": uid})
    assert week["ai"]["status"] == "unusable" and {t["by"] for t in week["texts"].values()} == {"template"}
    sample = (await c.get("/api/win-back")).json()["sample"]
    assert sample["text"].endswith(offer + ".")

    # a different owner without an offer: no offer line at all
    c2, uid2 = await owner_on("9871100004", "program")
    await standard_data(c2, uid2)
    await switch_on(c2, per_week=1)
    assert (await c2.post("/api/win-back/run")).json()["status"] == "done"
    [a] = await approvals(uid2)
    assert "delivery" not in a["text"].lower() and not re.search(r"\d|₹|%", a["text"])


async def test_email_goes_only_on_a_tap_and_only_when_email_is_set_up(client, monkeypatch):
    c, uid = await owner_on("9871100005", "program")
    await standard_data(c, uid)
    await switch_on(c, per_week=2)
    assert (await c.post("/api/win-back/run")).status_code == 200
    drafts = (await c.get("/api/win-back")).json()["drafts"]
    sunrise = next(x for x in drafts if x["name"] == "Sunrise Fabrics")
    assert sunrise["email_draft"] == {"status": "draft", "to": "buy@sunrise.example"} and sunrise["subject"]
    assert await db().outbox.count_documents({"channel": "email"}) == 0          # drafted, not sent

    monkeypatch.setattr(Settings, "local_dev", property(lambda s: False))       # a real deployment with no email provider
    r = await c.post(f"/api/win-back/{sunrise['id']}/send-email")
    assert r.status_code == 400 and "isn't set up" in r.json()["detail"]
    assert (await c.get("/api/win-back")).json()["email_ready"] is False
    monkeypatch.undo()

    r = await c.post(f"/api/win-back/{sunrise['id']}/send-email")
    assert r.status_code == 200, r.text
    mail = await db().outbox.find_one({"channel": "email"})
    assert mail["to"] == "buy@sunrise.example" and mail["kind"] == "win_back" and BUSINESS in mail["from"]
    assert PROFILE["email"] in mail["reply_to"] and mail["text"].startswith("Hello Sunrise,") and "just reply and tell us" in mail["text"]
    assert (await c.post(f"/api/win-back/{sunrise['id']}/send-email")).status_code == 409
    assert await db().outbox.count_documents({"channel": "email"}) == 1
    d = await db().winback_drafts.find_one({"_id": sunrise["id"]})
    assert d["email_status"] == "sent" and d["status"] == "pending"        # its WhatsApp message still waits for a tap
    res = (await c.get("/api/win-back")).json()["results"]
    assert res["messaged"] == 1


# ───────────────────────── results ─────────────────────────
async def test_results_count_only_a_real_purchase_after_the_message(client):
    c, uid = await owner_on("9871100006", "program")
    await standard_data(c, uid)
    await switch_on(c, per_week=4)
    assert (await c.post("/api/win-back/run")).json()["status"] == "done"
    drafts = {x["name"]: x for x in (await c.get("/api/win-back")).json()["drafts"]}
    # Sunrise: tapped "Send on WhatsApp" — the link opens the owner's own WhatsApp with the message typed
    r = await c.post(f"/api/win-back/{drafts['Sunrise Fabrics']['id']}/whatsapp")
    assert r.status_code == 200 and r.json()["link"].startswith("https://wa.me/919871200001?text=Hello%20Sunrise")
    assert (await c.post(f"/api/win-back/{drafts['Sunrise Fabrics']['id']}/whatsapp")).status_code == 409   # once
    # Kaveri: sent too; Greenleaf (Customers list): sent; Pinewood: skipped
    assert (await c.post(f"/api/win-back/{drafts['Kaveri Foods']['id']}/whatsapp")).status_code == 200
    assert (await c.post(f"/api/win-back/{drafts['Greenleaf Agencies']['id']}/whatsapp")).status_code == 200
    assert (await c.post(f"/api/win-back/{drafts['Pinewood Studio']['id']}/skip")).status_code == 200
    pine = await db().winback_drafts.find_one({"_id": drafts["Pinewood Studio"]["id"]})
    assert pine["status"] == "skipped" and pine["email_status"] == "skipped"
    # as if Sunrise was messaged 10 days ago, Kaveri 70 days ago (after its last purchase, 75 days ago) and Greenleaf 20 days ago
    for name, ago in (("Sunrise Fabrics", 10), ("Kaveri Foods", 70), ("Greenleaf Agencies", 20)):
        d = await db().winback_drafts.find_one({"_id": drafts[name]["id"]})
        await db().approvals.update_one({"_id": d["approval_id"]}, {"$set": {"decided_at": now() - timedelta(days=ago)}})
    # then sales: Sunrise bought 5 days ago (counted), Kaveri 5 days ago (65 days after: too late to count), Pinewood (not messaged)
    await upload(c, [("Sunrise Fabrics", "98712 00001", 5, 45000), ("Kaveri Foods", "98712 00002", 5, 30000), ("Pinewood Studio", "", 3, 8000)])
    # Greenleaf is only on the Customers list: it shows a later purchase there
    await db().customers.update_one({"ws": uid, "name": "Greenleaf Agencies"},
                                    {"$set": {"last_purchase": (today() - timedelta(days=2)).isoformat(), "purchases": 6, "total_value": 135000}})
    res = (await c.get("/api/win-back")).json()["results"]
    assert res["messaged"] == 3 and res["bought_again"] == 2 and res["amount"] == 60000
    assert res["weeks"] == 11 and res["line"] == "3 messaged in 11 weeks · 2 bought again, ₹60,000"


async def test_never_sent_on_its_own_even_where_staff_may_act(client):
    """LegacyWorkforce lets some agents act on their own. Win-Back's messages still wait for a tap, from any plan."""
    from app import connections
    c, uid = await owner_on("9871100013", "office")
    await connections.save(uid, "whatsapp", {"provider": "demo", "tpl_customer": "customer_v1"}, "test", "test")
    assert (await c.patch("/api/agents/win_back", json={"autonomy": "act"})).status_code == 200
    await standard_data(c, uid)
    await switch_on(c, per_week=3)
    assert (await c.post("/api/win-back/run")).json()["status"] == "done"
    made = await approvals(uid)
    assert len(made) == 3 and {a["status"] for a in made} == {"pending"}
    assert await db().outbox.count_documents({"channel": "whatsapp", "to": {"$regex": "^91987120"}}) == 0
    # the owner taps "send from my number" (Growth and up, connected): it goes from the owner's number, as the customer template
    sunrise = next(x for x in (await c.get("/api/win-back")).json()["drafts"] if x["name"] == "Sunrise Fabrics")
    assert (await c.post(f"/api/win-back/{sunrise['id']}/send")).status_code == 200
    [out] = [o async for o in db().outbox.find({"channel": "whatsapp", "to": "919871200001"})]
    assert out["via"] == "owner-demo" and out["kind"] == "customer" and out["params"][0] == "Sunrise"


# ───────────────────────── run now, plans, roles, other businesses ─────────────────────────
async def test_run_now_once_a_day_plans_roles_and_other_businesses(client):
    lite, _ = await owner_on("9871100007", "lite")
    assert (await lite.get("/api/win-back")).status_code == 403
    assert (await lite.post("/api/win-back/run")).status_code == 403

    c, uid = await owner_on("9871100008", "program")
    assert (await c.get("/api/win-back")).status_code == 200
    assert (await c.post("/api/win-back/run")).status_code == 400          # off
    d = await switch_on(c, per_week=2)
    assert d["data"]["little"] is True                                       # no sales yet: the page asks for the Tally sheet
    r = (await c.post("/api/win-back/run")).json()
    assert r["status"] == "skipped" and "Nobody to win back" in r["note"]
    await standard_data(c, uid)
    r = await c.post("/api/win-back/run")                                    # nothing was drafted before, so it may run again today
    assert r.status_code == 200 and r.json()["status"] == "done" and r.json()["this_week"] == 2
    assert (await c.post("/api/win-back/run")).status_code == 429            # at most once a day
    assert len(await approvals(uid)) == 2

    # another business can't see or touch these drafts
    other, ouid = await owner_on("9871100009", "program")
    mine = (await c.get("/api/win-back")).json()["drafts"]
    assert (await other.get("/api/win-back")).json()["drafts"] == []
    for path in ("skip", "send-email", "whatsapp", "send"):
        assert (await other.post(f"/api/win-back/{mine[0]['id']}/{path}")).status_code == 404, path

    # a staff member can't open it; a manager can use it but not change the settings (Growth: team logins)
    g, guid = await owner_on("9871100010", "growth")
    staff, manager = await new_owner("9871100011"), await new_owner("9871100012")
    for person, role in ((staff, "staff"), (manager, "manager")):
        pid = (await person.get("/api/me")).json()["user"]["id"]
        await db().users.update_one({"_id": pid}, {"$set": {"team_of": guid, "team_role": role}})
    assert (await staff.get("/api/win-back")).status_code == 403
    assert (await staff.post("/api/win-back/run")).status_code == 403
    assert (await manager.get("/api/win-back")).status_code == 200
    assert (await manager.put("/api/win-back/settings", json={"per_week": 5})).status_code == 403
    assert (await g.get("/api/win-back")).json()["can_edit"] is True
