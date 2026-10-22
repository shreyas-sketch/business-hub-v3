"""Expenses (v2.2): add, list, the monthly picture, GST, staff and manager rules, bills, settings, the CA's CSV,
Cash flow, and bank statement imports. Every business, person, number and GST number here is invented."""
import asyncio
import csv
import io
from datetime import timedelta

import pytest

from app.db import IST, db, now
from app.event import gstin_check_char
from app.routers import expenses as exp
from tests.conftest import PROFILE, new_owner

ADMIN = "9999900000"
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
EXE = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff" + b"\x00" * 64


async def owner_on(phone: str, plan: str, name: str = "Owner Test", industry: str | None = None):
    c = await new_owner(phone, name=name)
    profile = {**PROFILE, "industry": industry} if industry else PROFILE
    assert (await c.put("/api/business", json=profile)).status_code == 200
    uid = (await c.get("/api/me")).json()["user"]["id"]
    if plan != "free":
        admin = await new_owner(ADMIN)
        assert (await admin.patch(f"/api/admin/users/{uid}", json={"plan": plan})).status_code == 200
    return c, uid


async def join(owner, phone: str, name: str, role: str):
    """The owner invites a number (team logins are a Growth feature); that person logs in to the owner's hub."""
    r = await owner.post("/api/team/invites", json={"phone": phone, "name": name, "role": role})
    assert r.status_code == 200, r.text
    member = await new_owner(phone)
    me = (await member.get("/api/me")).json()
    assert me["workspace"]["role"] == role
    return member, me["user"]["id"]


def today():
    return now().astimezone(IST).date()


def day(offset: int) -> str:
    return (today() + timedelta(days=offset)).isoformat()


def month(n: int = 0) -> str:
    y, m = today().year, today().month + n
    while m < 1:
        y, m = y - 1, m + 12
    return f"{y:04d}-{m:02d}"


def gstin(first14: str = "27ABCDE1234F1Z") -> str:
    return first14 + gstin_check_char(first14)


def wrong_gstin() -> str:
    good = gstin()
    return good[:14] + ("A" if good[14] != "A" else "B")


async def add(c, **kw):
    body = {"date": f"{month()}-01", "amount": 100, "category": "other", "paid_by": "upi", **kw}
    r = await c.post("/api/expenses", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# ───────────────────────── the numbers ─────────────────────────
async def test_add_list_and_the_monthly_picture(client):
    c, uid = await owner_on("9840001000", "lite")
    assert (await c.get("/api/expenses")).json() == {"month": month(), "items": [], "more": False, "shown_cap": 1000,
                                                    "totals": {"spent": 0.0, "gst_claimable": 0.0, "count": 0, "to_approve": 0},
                                                    "totals_capped": False, "totals_cap": 20000}
    m0, m1, m2, m3 = (f"{month(-i)}" for i in range(4))
    # three months before this one
    await add(c, date=f"{m3}-05", amount=8000, category="fuel", vendor="Bharat Fuels Thane")
    await add(c, date=f"{m3}-03", amount=45000, category="rent", vendor="Shree Estates", paid_by="bank")
    await add(c, date=f"{m2}-05", amount=9000, category="fuel", vendor="Highway Fuel Point")
    await add(c, date=f"{m2}-03", amount=45000, category="rent", vendor="Shree Estates", paid_by="bank")
    await add(c, date=f"{m2}-05", amount=8000, category="electricity", vendor="City Power Co")
    await add(c, date=f"{m2}-07", amount=1000, category="phone_internet", vendor="Fastnet Broadband")
    await add(c, date=f"{m1}-05", amount=10000, category="fuel", vendor="Ring Road Fuels")
    await add(c, date=f"{m1}-03", amount=45000, category="rent", vendor="shree  estates", paid_by="bank")   # same vendor, typed differently
    await add(c, date=f"{m1}-05", amount=9100, category="electricity", vendor="City Power Co")
    await add(c, date=f"{m1}-07", amount=1300, category="phone_internet", vendor="Fastnet Broadband")
    # this month (all on the 1st, so the test passes on any day)
    first = f"{m0}-01"
    fuel = await add(c, date=first, amount=13500, category="fuel", vendor="Lake Road Fuels", paid_by="card")
    await add(c, date=first, amount=45000, category="rent", vendor="Shree Estates", paid_by="bank")
    await add(c, date=first, amount=0.1, category="office", vendor="Corner Tea Stall", paid_by="cash")
    await add(c, date=first, amount=0.2, category="office", vendor="Corner Tea Stall", paid_by="cash")
    steel = await add(c, date=first, amount=11800, gst=1800, gst_claimable=True, category="purchases", vendor="Sharma Steel Traders",
                      vendor_gstin=" 27abcde1234f1z" + gstin()[14].lower(), project="Site A - Powai", note="TMT bars")
    soft = await add(c, date=first, amount=999, category="software", vendor="Ledgerly Cloud Books", recurring=True)

    assert steel["amount"] == 11800.0 and steel["gst"] == 1800.0 and steel["gst_claimable"] is True and steel["vendor_gstin"] == gstin()
    assert steel["category_label"] == "Site material" and steel["status"] == "approved" and steel["source"] == "owner"
    assert steel["bill"] is False and steel["can_edit"] is True and steel["in_cash_flow"] is False and steel["bank_ref"] == ""
    stored = await db().expenses.find_one({"_id": steel["id"]})
    assert stored["amount"] == 1_180_000 and stored["gst"] == 180_000 and stored["ws"] == uid and stored["created_by"] == uid
    assert await db().events.count_documents({"type": "expense_added", "user_id": uid}) == 16

    body = (await c.get("/api/expenses")).json()
    assert body["month"] == m0 and len(body["items"]) == 6
    assert body["items"][0]["id"] == soft["id"] and body["items"][-1]["id"] == fuel["id"]   # newest first
    assert body["totals"] == {"spent": 71299.3, "gst_claimable": 1800.0, "count": 6, "to_approve": 0}   # 0.1 + 0.2 is exactly 0.3 here
    assert [i["vendor"] for i in (await c.get(f"/api/expenses?month={m1}&category=fuel")).json()["items"]] == ["Ring Road Fuels"]
    assert [i["vendor"] for i in (await c.get("/api/expenses?q=steel")).json()["items"]] == ["Sharma Steel Traders"]
    assert [i["vendor"] for i in (await c.get("/api/expenses?q=powai")).json()["items"]] == ["Sharma Steel Traders"]   # the project
    assert [i["category"] for i in (await c.get("/api/expenses?q=Site material")).json()["items"]] == ["purchases"]   # a category's name
    assert (await c.get("/api/expenses?status=to_approve")).json()["items"] == []
    assert (await c.get("/api/expenses?month=2026-13")).status_code == 422

    s = (await c.get("/api/expenses/summary")).json()
    assert s["month"] == m0 and s["spent"] == 71299.3 and s["last_month_spent"] == 65400.0 and s["change_pct"] == 9
    assert s["gst_claimable"] == 1800.0 and s["count"] == 6 and s["avg_months"] == 3
    cats = {r["key"]: r for r in s["by_category"]}
    assert s["by_category"][0]["key"] == "rent"   # the biggest head first
    assert cats["fuel"] == {"key": "fuel", "label": "Fuel & vehicle", "amount": 13500.0, "last_month": 10000.0, "avg_3m": 9000.0,
                            "budget": None, "over_budget": False}
    assert cats["phone_internet"]["avg_3m"] == 766.67 and cats["phone_internet"]["amount"] == 0.0   # 2,300 ÷ 3, to the paisa
    assert cats["office"]["amount"] == 0.3 and cats["purchases"]["avg_3m"] == 0.0
    assert [j["key"] for j in s["jumps"]] == ["fuel"]   # rent is steady; heads with no history never "jump"
    assert s["jumps"][0]["pct"] == 50
    assert s["jumps"][0]["line"] == "Fuel & vehicle is up 50% on your usual: ₹13,500 this month, against ₹9,000 a month on average."
    assert s["top_vendors"][0] == {"vendor": "Shree Estates", "amount": 45000.0, "count": 1}
    assert {"vendor": "Corner Tea Stall", "amount": 0.3, "count": 2} in s["top_vendors"] and len(s["top_vendors"]) == 5
    assert s["by_paid_by"] == {"bank": 45000.0, "upi": 12799.0, "card": 13500.0, "cash": 0.3, "cheque": 0.0}
    assert s["projects"] == [{"project": "Site A - Powai", "amount": 11800.0}]
    rec = {r["category"]: r for r in s["recurring"]}
    assert set(rec) == {"rent", "electricity", "software"}   # fuel vendors differ; broadband moved 30%
    assert rec["rent"]["amount"] == 45000.0 and rec["rent"]["months"] == 3 and rec["rent"]["marked"] is False and rec["rent"]["day_of_month"] == 1
    assert rec["electricity"]["amount"] == 8550.0 and rec["electricity"]["months"] == 2 and rec["electricity"]["day_of_month"] == 5
    assert rec["software"]["marked"] is True and rec["software"]["months"] == 1 and rec["software"]["expense_id"] == soft["id"]
    assert all(r["in_cash_flow"] is False for r in s["recurring"]) and s["can_cash_flow"] is True

    # budgets: over, under, and the total used
    r = await c.put("/api/expenses/settings", json={"budgets": {"fuel": 12000, "rent": 50000, "office": 0}})
    assert r.status_code == 200 and r.json()["budgets"] == {"fuel": 12000.0, "rent": 50000.0}   # 0 means no budget
    s = (await c.get("/api/expenses/summary")).json()
    cats = {r["key"]: r for r in s["by_category"]}
    assert cats["fuel"]["budget"] == 12000.0 and cats["fuel"]["over_budget"] is True
    assert cats["rent"]["budget"] == 50000.0 and cats["rent"]["over_budget"] is False
    assert s["budget_total"] == 62000.0 and s["budget_spent"] == 58500.0

    # last month's own view: its "before" months are m2..m4 (only two have entries)
    s1 = (await c.get(f"/api/expenses/summary?month={m1}")).json()
    assert s1["spent"] == 65400.0 and s1["last_month_spent"] == 63000.0 and s1["avg_months"] == 2
    assert {r["key"]: r for r in s1["by_category"]}["fuel"]["avg_3m"] == 8500.0
    empty = (await c.get(f"/api/expenses/summary?month={month(-12)}")).json()
    assert empty["spent"] == 0.0 and empty["change_pct"] is None and empty["avg_months"] == 0 and empty["jumps"] == []


async def test_recurring_needs_two_of_three_months_within_15_percent(client):
    c, _ = await owner_on("9840001100", "lite")
    m0, m1, m2 = month(0), month(-1), month(-2)
    await add(c, date=f"{m2}-10", amount=10000, category="repairs", vendor="Lift Care AMC")       # 11,764 is within 15% of 10,000…
    await add(c, date=f"{m1}-10", amount=11764, category="repairs", vendor="Lift Care AMC")
    await add(c, date=f"{m2}-12", amount=10000, category="freight", vendor="Quick Parcel Co")     # …11,765 is not
    await add(c, date=f"{m1}-12", amount=11765, category="freight", vendor="Quick Parcel Co")
    await add(c, date=f"{m0}-01", amount=5000, category="marketing", vendor="Ad Board Agency")    # once, not marked
    await add(c, date=f"{month(-3)}-02", amount=7000, category="insurance", vendor="Safe Cover")  # outside the 3-month window
    await add(c, date=f"{m2}-02", amount=7000, category="insurance", vendor="Safe Cover")
    s = (await c.get("/api/expenses/summary")).json()
    assert [(r["vendor"], r["amount"], r["months"]) for r in s["recurring"]] == [("Lift Care AMC", 10882.0, 2)]


async def test_validation_errors_and_the_gst_on_a_bill(client):
    c, _ = await owner_on("9840002000", "lite")   # an interiors business: construction
    ok = {"date": day(0), "amount": 11800, "category": "purchases", "paid_by": "upi"}

    async def refused(body, word):
        r = await c.post("/api/expenses", json={**ok, **body})
        assert r.status_code == 422, (body, r.text)
        detail = r.json()["detail"]
        assert word in (detail if isinstance(detail, str) else str(detail)), (word, detail)

    await refused({"gst": 1800, "vendor_gstin": wrong_gstin()}, "doesn't add up")
    await refused({"vendor_gstin": "27ABCDE1234"}, "15 characters")
    await refused({"gst": 11800}, "less than the amount")
    await refused({"gst": 12000}, "less than the amount")
    await refused({"category": "jet_fuel"}, "category")
    await refused({"category": "tolls"}, "category")   # a logistics head, not an interiors one
    await refused({"date": day(8)}, "too far ahead")
    await refused({"date": (today() - timedelta(days=3 * 366)).isoformat()}, "3 years")
    await refused({"date": "21/10/2026"}, "date like")
    await refused({"amount": 0}, "amount")
    await refused({"amount": 100_000_001}, "amount")
    await refused({"gst": -1}, "gst")
    await refused({"paid_by": "gold"}, "paid_by")
    await refused({"vendor": "x" * 81}, "vendor")

    week = await add(c, **{**ok, "date": day(7), "category": "site_labour"})   # up to a week ahead is fine
    assert week["category_label"] == "Site labour"
    # 18% GST inside a ₹11,800 bill is ₹1,800 (the page works it out; the hub stores what is sent, to the paisa)
    bill = await add(c, **ok, gst=1800, gst_claimable=True, vendor_gstin=gstin())
    assert bill["gst"] == 1800.0 and bill["gst_claimable"] is True
    odd = await add(c, **{**ok, "amount": 1000, "gst": round(1000 * 5 / 105, 2)})
    assert odd["gst"] == 47.62 and (await db().expenses.find_one({"_id": odd["id"]}))["gst"] == 4762
    plain = await add(c, **{**ok, "gst": 0, "gst_claimable": True})   # nothing to claim without GST
    assert plain["gst_claimable"] is False

    # edits are checked against the whole entry
    r = await c.patch(f"/api/expenses/{bill['id']}", json={"amount": 1500})
    assert r.status_code == 422 and "less than the amount" in r.json()["detail"]
    r = await c.patch(f"/api/expenses/{bill['id']}", json={"gst": 0})
    assert r.status_code == 200 and r.json()["gst"] == 0.0 and r.json()["gst_claimable"] is False
    r = await c.patch(f"/api/expenses/{bill['id']}", json={"vendor_gstin": wrong_gstin()})
    assert r.status_code == 422
    r = await c.patch(f"/api/expenses/{bill['id']}", json={"category": "rent", "vendor": "  Shree   Estates ", "note": "October"})
    assert r.status_code == 200 and r.json()["category_label"] == "Rent" and r.json()["vendor"] == "Shree Estates"
    # an entry older than 3 years can still have its note changed (only what changes is re-checked)
    await db().expenses.update_one({"_id": bill["id"]}, {"$set": {"date": (today() - timedelta(days=4 * 365)).isoformat()}})
    assert (await c.patch(f"/api/expenses/{bill['id']}", json={"note": "old"})).status_code == 200
    assert (await c.delete(f"/api/expenses/{bill['id']}")).status_code == 200
    assert (await c.delete(f"/api/expenses/{bill['id']}")).status_code == 404


# ───────────────────────── who may do what ─────────────────────────
async def test_staff_and_manager_rules(client):
    owner, uid = await owner_on("9840003000", "growth")
    staff, staff_id = await join(owner, "9840003001", "Ravi Staff", "staff")
    manager, _ = await join(owner, "9840003002", "Meena Manager", "manager")
    staff2, _ = await join(owner, "9840003003", "Kiran Staff", "staff")

    mine = await add(owner, amount=5000, category="rent", vendor="Shree Estates")
    s_exp = await add(staff, amount=640, category="travel", vendor="City Auto Rickshaw", paid_by="cash")
    assert s_exp["status"] == "to_approve" and s_exp["can_edit"] is True
    m_exp = await add(manager, amount=1200, category="office", vendor="Stationery Corner")
    assert m_exp["status"] == "approved"

    # staff see only their own entries, totals included
    body = (await staff.get("/api/expenses")).json()
    assert [i["id"] for i in body["items"]] == [s_exp["id"]] and body["totals"]["spent"] == 640.0 and body["totals"]["to_approve"] == 1
    assert (await staff2.get("/api/expenses")).json()["items"] == []
    everyone = (await owner.get("/api/expenses")).json()
    assert {i["id"] for i in everyone["items"]} == {mine["id"], s_exp["id"], m_exp["id"]} and everyone["totals"]["to_approve"] == 1
    assert {i["id"]: i["added_by"] for i in everyone["items"]}[s_exp["id"]] == "Ravi Staff"
    assert (await owner.get("/api/expenses?status=to_approve")).json()["items"][0]["id"] == s_exp["id"]
    # waiting entries count in what was spent, but their GST isn't claimable until approved
    g = await add(staff, amount=1180, gst=180, gst_claimable=True, category="purchases", vendor="Sharma Steel Traders")
    assert (await owner.get("/api/expenses")).json()["totals"]["gst_claimable"] == 0.0
    s = (await owner.get("/api/expenses/summary")).json()
    assert s["gst_claimable"] == 0.0 and s["gst_claimable_waiting"] == 180.0 and s["to_approve"] == 2

    # staff can't reach anyone else's entry, or the manager-only parts
    for method, path in (("PATCH", f"/api/expenses/{mine['id']}"), ("DELETE", f"/api/expenses/{mine['id']}"),
                         ("GET", f"/api/expenses/{mine['id']}/bill")):
        r = await staff.request(method, path, json={"note": "x"} if method == "PATCH" else None)
        assert r.status_code == 404, (method, path)
    assert (await staff2.patch(f"/api/expenses/{s_exp['id']}", json={"note": "x"})).status_code == 404
    for method, path, body in (("POST", f"/api/expenses/{s_exp['id']}/approve", None), ("GET", "/api/expenses/summary", None),
                               ("GET", "/api/expenses/export.csv", None), ("PUT", "/api/expenses/settings", {"kind": "food"}),
                               ("POST", f"/api/expenses/{s_exp['id']}/to-cash-flow", None)):
        r = await staff.request(method, path, json=body)
        assert r.status_code == 403, (path, r.text)
    st = (await staff.get("/api/expenses/settings")).json()
    assert st["budgets"] == {} and st["kind"] == "construction" and st["categories"][0]["label"] == "Site material"

    # their own waiting entry: they can change or remove it, until it's approved
    r = await staff.patch(f"/api/expenses/{s_exp['id']}", json={"amount": 660, "note": "Site visit, Powai"})
    assert r.status_code == 200 and r.json()["amount"] == 660.0 and r.json()["status"] == "to_approve"
    assert (await staff.delete(f"/api/expenses/{g['id']}")).status_code == 200
    r = await manager.post(f"/api/expenses/{s_exp['id']}/approve")
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert (await manager.post(f"/api/expenses/{s_exp['id']}/approve")).status_code == 200   # twice is fine
    assert (await db().expenses.find_one({"_id": s_exp["id"]}))["approved_by"] != staff_id
    r = await staff.patch(f"/api/expenses/{s_exp['id']}", json={"note": "changed"})
    assert r.status_code == 403 and "approved" in r.json()["detail"]
    assert (await staff.delete(f"/api/expenses/{s_exp['id']}")).status_code == 403
    assert (await staff.post(f"/api/expenses/{s_exp['id']}/bill", files={"file": ("bill.pdf", PDF, "application/pdf")})).status_code == 403
    assert (await staff.get("/api/expenses")).json()["items"][0]["can_edit"] is False
    # managers change anyone's entries
    assert (await manager.patch(f"/api/expenses/{mine['id']}", json={"note": "Checked"})).status_code == 200
    assert (await manager.get("/api/expenses/export.csv")).status_code == 200


async def test_another_business_never_sees_or_changes_an_entry(client):
    c, _ = await owner_on("9840004000", "lite")
    e = await add(c, amount=2500, category="repairs", vendor="Sai Electricals")
    assert (await c.post(f"/api/expenses/{e['id']}/bill", files={"file": ("bill.pdf", PDF, "application/pdf")})).status_code == 200
    other, _ = await owner_on("9840004001", "lite")
    assert (await other.get("/api/expenses")).json()["items"] == []
    assert (await other.get("/api/expenses/summary")).json()["spent"] == 0.0
    for method, path in (("PATCH", f"/api/expenses/{e['id']}"), ("DELETE", f"/api/expenses/{e['id']}"),
                         ("POST", f"/api/expenses/{e['id']}/approve"), ("GET", f"/api/expenses/{e['id']}/bill"),
                         ("DELETE", f"/api/expenses/{e['id']}/bill"), ("POST", f"/api/expenses/{e['id']}/to-cash-flow"),
                         ("DELETE", f"/api/expenses/{e['id']}/to-cash-flow")):
        r = await other.request(method, path, json={"note": "x"} if method == "PATCH" else None)
        assert r.status_code == 404, (method, path, r.text)
    r = await other.post(f"/api/expenses/{e['id']}/bill", files={"file": ("bill.pdf", PDF, "application/pdf")})
    assert r.status_code == 404
    assert "Sai" not in (await other.get("/api/expenses/export.csv")).text
    assert (await db().expenses.find_one({"_id": e["id"]}))["note"] == ""


async def test_plan_gating(client):
    free, _ = await owner_on("9840005000", "free")
    for method, path in (("GET", "/api/expenses"), ("POST", "/api/expenses"), ("GET", "/api/expenses/summary"),
                         ("GET", "/api/expenses/settings"), ("GET", "/api/expenses/export.csv")):
        r = await free.request(method, path, json={"date": day(0), "amount": 10, "category": "other"} if method == "POST" else None)
        assert r.status_code == 403 and r.json()["detail"]["code"] == "locked" and r.json()["detail"]["feature"] == "expenses", path
    member, _ = await owner_on("9840005001", "lite")
    assert (await member.get("/api/expenses")).status_code == 200
    assert (await member.post("/api/expenses", json={"date": day(0), "amount": 10, "category": "other"})).status_code == 200


# ───────────────────────── bills ─────────────────────────
async def test_bill_upload_type_check_download_and_delete(client):
    c, uid = await owner_on("9840006000", "lite")
    e = await add(c, amount=4720, gst=720, category="repairs", vendor="Sai Electricals")
    r = await c.post(f"/api/expenses/{e['id']}/bill", files={"file": ("bill.pdf", EXE, "application/pdf")})   # a program renamed .pdf
    assert r.status_code == 400 and "PDF" in r.json()["detail"]
    assert await db().files.count_documents({}) == 0
    r = await c.post(f"/api/expenses/{e['id']}/bill", files={"file": ("bill.pdf", b"", "application/pdf")})
    assert r.status_code == 400

    r = await c.post(f"/api/expenses/{e['id']}/bill", files={"file": ("Sai bill Oct.pdf", PDF, "application/pdf")})
    assert r.status_code == 200 and r.json()["bill"] is True
    got = await c.get(f"/api/expenses/{e['id']}/bill")
    assert got.status_code == 200 and got.content == PDF and got.headers["content-type"] == "application/pdf"
    assert "private" in got.headers["cache-control"]
    f = await db().files.find_one({})
    assert f["owner_id"] == uid and f["kind"] == "expense_bill" and f["meta"]["expense_id"] == e["id"]

    # a photo replaces the PDF; the old file goes
    r = await c.post(f"/api/expenses/{e['id']}/bill", files={"file": ("photo.png", PNG, "image/png")})
    assert r.status_code == 200
    assert await db().files.count_documents({}) == 1 and (await c.get(f"/api/expenses/{e['id']}/bill")).headers["content-type"] == "image/png"
    assert (await c.delete(f"/api/expenses/{e['id']}/bill")).json()["bill"] is False
    assert await db().files.count_documents({}) == 0
    assert (await c.get(f"/api/expenses/{e['id']}/bill")).status_code == 404

    # deleting the entry deletes its bill
    assert (await c.post(f"/api/expenses/{e['id']}/bill", files={"file": ("b.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 32, "image/jpeg")})).status_code == 200
    assert await db().files.count_documents({}) == 1
    assert (await c.delete(f"/api/expenses/{e['id']}")).status_code == 200
    assert await db().files.count_documents({}) == 0


# ───────────────────────── settings ─────────────────────────
@pytest.mark.parametrize("text,kind", [
    ("Home and office interiors with in-house carpenters", "construction"),
    ("CNC machining and steel fabrication unit", "manufacturing"),
    ("Pharma manufacturing (tablets)", "manufacturing"),
    ("Steel trading and distribution", "trading"),
    ("Wholesale dealer of electrical goods", "trading"),
    ("IT services, websites and mobile apps", "services"),
    ("Digital marketing agency", "services"),
    ("IT training institute", "consulting"),
    ("We make it easy to buy sarees", "other"),
    ("Transport and fleet operator", "logistics"),
    ("Cloud kitchen and catering", "food"),
    ("Dental clinic", "wellness"),
    ("Unisex salon and spa", "wellness"),
    ("Coaching for CA students", "consulting"),
    ("Civil contractor and builder", "construction"),
    ("", "other"),
])
def test_kind_detection_from_industry_text(text, kind):
    assert exp.detect_kind(text) == kind


async def test_settings_kind_and_custom_categories(client):
    c, uid = await owner_on("9840007000", "lite", industry="Plastic moulding factory")
    s = (await c.get("/api/expenses/settings")).json()
    assert s["kind"] == "manufacturing" and s["kind_label"] == "Manufacturing" and s["chosen"] is False
    assert [x["key"] for x in s["categories"][:4]] == ["purchases", "job_work", "consumables", "packing"]
    assert s["categories"][0] == {"key": "purchases", "label": "Raw material", "extra": True}
    assert s["categories"][-1]["key"] == "other" and not any(x["extra"] for x in s["categories"][4:])
    assert len(s["kinds"]) == 9 and await exp.kind_for(uid) == "manufacturing"
    assert [x["key"] for x in exp.categories_for("logistics")[:4]] == ["purchases", "tolls", "vehicle_repair", "driver_allowance"]
    assert exp.categories_for("other")[0] == {"key": "purchases", "label": "Purchases", "extra": False}

    # the Knowledge Hub's industry text is followed until the owner picks a kind
    assert (await c.put("/api/business", json={**PROFILE, "industry": "Restaurant and bakery"})).status_code == 200
    assert (await c.get("/api/expenses/settings")).json()["kind"] == "food"
    r = await c.put("/api/expenses/settings", json={"kind": "trading"})
    assert r.status_code == 200 and r.json()["kind"] == "trading" and r.json()["chosen"] is True
    assert r.json()["categories"][0]["label"] == "Stock purchases"
    assert (await c.put("/api/expenses/settings", json={"kind": "space_travel"})).status_code == 422
    assert (await c.put("/api/expenses/settings", json={"budgets": {"tolls": 100}})).status_code == 422   # not a trading head
    assert (await c.put("/api/expenses/settings", json={"budgets": {"rent": -1}})).status_code == 422
    assert (await c.put("/api/expenses/settings", json={"budgets": {"rent": 100_000_001}})).status_code == 422

    # the owner's own categories
    r = await c.put("/api/expenses/settings", json={"custom": [{"label": "Puja & festivals"}, {"label": "  Trade   fairs "}]})
    assert r.status_code == 200, r.text
    assert r.json()["custom"] == [{"key": "c_puja_festivals", "label": "Puja & festivals"}, {"key": "c_trade_fairs", "label": "Trade fairs"}]
    keys = [x["key"] for x in r.json()["categories"]]
    assert keys.index("c_trade_fairs") < keys.index("rent")   # the business's own come first
    fair = await add(c, amount=25000, category="c_trade_fairs", vendor="Expo Grounds Mumbai")
    assert fair["category_label"] == "Trade fairs"
    assert (await c.put("/api/expenses/settings", json={"budgets": {"c_trade_fairs": 30000}})).json()["budgets"] == {"c_trade_fairs": 30000.0}
    for bad, word in (([{"label": "Rent"}], "already"), ([{"label": "Gifts"}, {"label": "gifts"}], "twice"), ([{"label": "X"}], "")):
        r = await c.put("/api/expenses/settings", json={"custom": bad})
        assert r.status_code == 422 and word in str(r.json()["detail"]), bad
    r = await c.put("/api/expenses/settings", json={"custom": [{"label": f"Head {i}"} for i in range(11)]})
    assert r.status_code == 422
    # renaming keeps the key (and every entry under it); removing keeps old entries readable and drops the budget
    r = await c.put("/api/expenses/settings", json={"custom": [{"key": "c_trade_fairs", "label": "Exhibitions"}]})
    assert r.json()["custom"] == [{"key": "c_trade_fairs", "label": "Exhibitions"}]
    assert (await c.get("/api/expenses")).json()["items"][0]["category_label"] == "Exhibitions"
    r = await c.put("/api/expenses/settings", json={"custom": []})
    assert r.json()["custom"] == [] and r.json()["budgets"] == {}
    assert (await c.get("/api/expenses")).json()["items"][0]["category_label"] == "Trade fairs"
    assert (await c.post("/api/expenses", json={"date": day(0), "amount": 10, "category": "c_trade_fairs"})).status_code == 422
    assert (await c.patch(f"/api/expenses/{fair['id']}", json={"note": "Stall 14"})).status_code == 200   # the old head stays on it


# ───────────────────────── for the CA ─────────────────────────
async def test_csv_export_for_the_ca(client):
    c, _ = await owner_on("9840008000", "lite")
    a = await add(c, date=f"{month(-1)}-05", amount=11800, gst=1800, gst_claimable=True, category="purchases", vendor="Sharma Steel Traders",
                  vendor_gstin=gstin(), paid_by="bank", project="Site A", note="TMT bars")
    await c.post(f"/api/expenses/{a['id']}/bill", files={"file": ("bill.pdf", PDF, "application/pdf")})
    await add(c, date=f"{month()}-01", amount=250.5, category="other", vendor="=HYPERLINK(\"http://example.in\")", paid_by="cash",
              note="+91 98200 00001 called")
    await add(c, date=f"{month()}-01", amount=99, category="office", vendor="@Office Mart", note="-5 discount", project="  Site  B ")
    await add(c, date=f"{month(-4)}-01", amount=1, category="office", vendor="Too Early Stores")

    r = await c.get(f"/api/expenses/export.csv?from={month(-1)}-01&to={day(0)}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"] and ".csv" in r.headers["content-disposition"]
    text = r.content.decode("utf-8")
    assert text.startswith("﻿")
    rows = list(csv.reader(io.StringIO(text[1:])))
    assert rows[0] == ["Date", "Vendor", "Vendor GSTIN", "Category", "Amount (Rs)", "GST (Rs)", "GST claimable", "Paid by", "Project / site",
                       "Note", "Bill attached", "Status"]
    assert len(rows) == 4 and "Too Early Stores" not in text
    assert rows[1] == [f"{month(-1)}-05", "Sharma Steel Traders", gstin(), "Site material", "11800.00", "1800.00", "yes", "Bank transfer",
                       "Site A", "TMT bars", "yes", "Approved"]
    hyper = rows[2]
    assert hyper[1] == "'=HYPERLINK(\"http://example.in\")" and hyper[4] == "250.50" and hyper[9] == "'+91 98200 00001 called"
    assert hyper[6] == "no" and hyper[7] == "Cash" and hyper[10] == "no"
    assert rows[3][1] == "'@Office Mart" and rows[3][9] == "'-5 discount" and rows[3][8] == "Site B"
    for row in rows[1:]:
        assert not any(cell[:1] in ("=", "+", "-", "@", "\t", "\r") for cell in row)
    # a tab or carriage return can't reach a cell through the API (spaces are tidied), but the guard covers them too
    assert [exp._cell(v) for v in ("\tx", "\rx", "=1+1", "Plain", "", None, 12.5)] == ["'\tx", "'\rx", "'=1+1", "Plain", "", "", "12.5"]
    assert (await c.get(f"/api/expenses/export.csv?from={day(-400)}&to={day(0)}")).status_code == 422   # more than a year
    assert (await c.get(f"/api/expenses/export.csv?from={day(0)}&to={day(-1)}")).status_code == 422
    assert (await c.get("/api/expenses/export.csv?from=01-10-2026")).status_code == 422
    assert (await c.get("/api/expenses/export.csv")).status_code == 200   # this month by default


# ───────────────────────── into Cash flow ─────────────────────────
async def test_to_cash_flow_is_idempotent_and_the_forecast_counts_it(client, monkeypatch):
    c, uid = await owner_on("9840009000", "lite")
    assert (await c.put("/api/cash-flow/balance", json={"amount": 500000})).status_code == 200
    e = await add(c, date=f"{month()}-01", amount=45000, category="rent", vendor="Shree Estates", paid_by="bank")
    one = await c.post(f"/api/expenses/{e['id']}/to-cash-flow")
    assert one.status_code == 200, one.text
    item = one.json()["cash_item"]
    assert item["kind"] == "out" and item["title"] == "Shree Estates" and item["amount"] == 45000.0 and item["day_of_month"] == 1
    assert item["recurrence"] == "monthly" and item["source"] == "expenses" and item["date"] is None
    two = await c.post(f"/api/expenses/{e['id']}/to-cash-flow")
    assert two.json()["cash_item"]["id"] == item["id"] and await db().cash_items.count_documents({"owner_id": uid}) == 1

    f = (await c.get("/api/cash-flow")).json()
    assert f["ready"] and any(i["id"] == item["id"] for i in f["items"])
    outs = [ev for d in f["days"] for ev in d["events"] if ev.get("id") == item["id"]]
    assert outs and all(ev["kind"] == "out" and ev["amount"] == 45000.0 and ev["date"][8:10] == "01" for ev in outs)
    assert f["money_out"] >= 45000.0
    assert (await c.get("/api/expenses")).json()["items"][0]["in_cash_flow"] is True

    # last month's rent (the same payment) links to the same line instead of adding another
    old = await add(c, date=f"{month(-1)}-01", amount=45000, category="rent", vendor="SHREE ESTATES", paid_by="bank")
    assert (await c.post(f"/api/expenses/{old['id']}/to-cash-flow")).json()["cash_item"]["id"] == item["id"]
    s = (await c.get("/api/expenses/summary")).json()
    assert [(r["vendor"], r["in_cash_flow"]) for r in s["recurring"]] == [("Shree Estates", True)]

    # removed in Cash flow → adding again makes a new line
    assert (await c.delete(f"/api/cash-flow/items/{item['id']}")).status_code == 200
    assert (await c.get("/api/expenses/summary")).json()["recurring"][0]["in_cash_flow"] is False
    again = (await c.post(f"/api/expenses/{e['id']}/to-cash-flow")).json()["cash_item"]
    assert again["id"] != item["id"] and await db().cash_items.count_documents({"owner_id": uid}) == 1

    # taking it out of cash flow
    r = await c.delete(f"/api/expenses/{old['id']}/to-cash-flow")
    assert r.status_code == 200 and await db().cash_items.count_documents({"owner_id": uid}) == 0
    assert all(i["in_cash_flow"] is False for i in (await c.get("/api/expenses")).json()["items"])
    assert not [ev for d in (await c.get("/api/cash-flow")).json()["days"] for ev in d["events"] if ev.get("id") == again["id"]]

    # deleting an expense leaves its cash-flow line (the forecast keeps counting the payment until it's removed there)
    third = (await c.post(f"/api/expenses/{e['id']}/to-cash-flow")).json()["cash_item"]
    r = await c.delete(f"/api/expenses/{e['id']}")
    assert r.json() == {"ok": True, "kept_in_cash_flow": True}
    assert await db().cash_items.find_one({"_id": third["id"]})

    # a business without Cash flow on its plan can't add to it
    from app import plans
    lone = await add(c, date=f"{month()}-01", amount=999, category="software", vendor="Ledgerly Cloud Books")
    original = plans.has
    monkeypatch.setattr(plans, "has", lambda owner, key: False if key == "cash_flow" else original(owner, key))
    r = await c.post(f"/api/expenses/{lone['id']}/to-cash-flow")
    assert r.status_code == 403 and r.json()["detail"]["feature"] == "cash_flow"
    assert (await c.get("/api/expenses/summary")).json()["can_cash_flow"] is False


# ───────────────────────── from a bank statement ─────────────────────────
async def test_add_from_bank(client):
    c, uid = await owner_on("9840010000", "lite")   # interiors: construction
    d1, d2 = day(-3), day(-2)
    rows = [
        {"date": d1, "amount": 2360.0, "payee": "Fastnet Broadband", "narration": "UPI/FASTNET/OCT", "category": "phone_internet", "bank_ref": "UTR001"},
        {"date": d1, "amount": 2360.0, "payee": "Fastnet Broadband", "narration": "UPI/FASTNET/OCT", "category": "phone_internet", "bank_ref": "UTR001"},  # repeated
        {"date": d1, "amount": 20000, "payee": "", "narration": "ATM WDL MG ROAD", "category": "cash_withdrawal", "bank_ref": "UTR002"},
        {"date": d1, "amount": 150000, "payee": "Own savings a/c", "narration": "SELF TRANSFER", "category": "transfer", "bank_ref": "UTR003"},
        {"date": d2, "amount": 812.5, "payee": "", "narration": "NEFT   TOLL PLAZA   FASTAG RECHARGE   REF 99812", "category": "tolls", "bank_ref": "UTR004"},
        {"date": d2, "amount": 4500, "payee": "Site Labour Gang", "narration": "IMPS", "category": "SITE_LABOUR", "bank_ref": "UTR005"},
        {"date": d2, "amount": -500, "payee": "Refund", "narration": "money in", "category": "other", "bank_ref": "UTR006"},
        {"date": "31/12/2026", "amount": 10, "payee": "Bad date", "narration": "", "category": "other", "bank_ref": "UTR007"},
        {"date": d2, "amount": 50, "payee": "Corner Tea Stall", "narration": "UPI TEA", "category": "food_staff", "bank_ref": ""},
        {"date": d2, "amount": 50, "payee": "Corner Tea Stall", "narration": "UPI TEA", "category": "food_staff", "bank_ref": ""},  # a second cup
        {"date": d2, "amount": 0, "payee": "", "narration": "", "category": "", "bank_ref": "UTR008"},
        {"date": d2, "amount": 25000, "payee": "Employee A", "narration": "SALARY OCT EMPLOYEE A", "category": "salaries", "bank_ref": "UTR009"},
        {"date": d2, "amount": 18000, "payee": "Employee B", "narration": "SAL OCT", "category": "Salary", "bank_ref": "UTR010"},
        {"date": (today() - timedelta(days=4 * 365)).isoformat(), "amount": 99, "payee": "Old Vendor", "narration": "", "category": "other", "bank_ref": "UTR011"},
    ]
    r = await exp.add_from_bank(uid, uid, rows)
    assert r == {"added": 5, "skipped_duplicates": 1, "skipped_not_expenses": 4, "skipped_invalid": 2, "skipped_private": 2}
    assert set(r) <= set(exp.SKIP_REASONS) | {"added"}   # every skip has a plain line the bank page can show
    docs = {d["bank_ref"]: d async for d in db().expenses.find({"ws": uid})}
    assert len(docs) == 5
    net = docs["UTR001"]
    assert net["amount"] == 236_000 and net["vendor"] == "Fastnet Broadband" and net["category"] == "phone_internet"
    assert net["source"] == "bank" and net["paid_by"] == "bank" and net["status"] == "approved" and net["gst"] == 0
    assert net["gst_claimable"] is False and net["created_by"] == uid and net["bill_file_id"] is None
    assert net["vendor_gstin"] == "" and net["project"] == "" and net["recurring"] is False and net["cash_item_id"] is None
    toll = docs["UTR004"]
    assert toll["category"] == "other" and toll["vendor"] == "Bank payment" and toll["amount"] == 81_250   # not an interiors head; no payee
    assert docs["UTR005"]["category"] == "site_labour"
    tea = [d for k, d in docs.items() if k.startswith("auto:")]
    assert len(tea) == 2 and tea[0]["bank_ref"] != tea[1]["bank_ref"]
    # the narration (names, what a payment was for) is never stored, so a manager can't read it
    assert all(d["note"] == "" for d in docs.values())
    stored = str([d async for d in db().expenses.find({"ws": uid})])
    for text in ("UPI/FASTNET", "FASTAG", "TOLL PLAZA", "UPI TEA", "SALARY", "Employee"):
        assert text not in stored, text

    # the owner picks salaries on purpose; the rest is already in
    again = await exp.add_from_bank(uid, uid, rows, include_salaries=True)
    assert again == {"added": 2, "skipped_duplicates": 6, "skipped_not_expenses": 4, "skipped_invalid": 2, "skipped_private": 0}
    pay = [d async for d in db().expenses.find({"ws": uid, "bank_ref": {"$in": ["UTR009", "UTR010"]}})]
    assert sorted(d["vendor"] for d in pay) == ["Employee A", "Employee B"] and {d["category"] for d in pay} == {"salaries"}
    assert all(d["note"] == "" for d in pay)
    # the same statement a third time adds nothing
    third = await exp.add_from_bank(uid, uid, rows, include_salaries=True)
    assert third["added"] == 0 and third["skipped_duplicates"] == 8
    assert await db().expenses.count_documents({"ws": uid}) == 7
    # another business can import the same reference
    other, other_id = await owner_on("9840010001", "lite")
    assert (await exp.add_from_bank(other_id, other_id, rows[:1]))["added"] == 1

    docs = {d["bank_ref"]: d async for d in db().expenses.find({"ws": uid})}
    for m in {d1[:7], d2[:7]}:
        listed = (await c.get(f"/api/expenses?month={m}")).json()
        assert listed["totals"]["count"] == sum(d["date"][:7] == m for d in docs.values())
        assert all(i["source"] == "bank" and i["status"] == "approved" for i in listed["items"])
    with pytest.raises(ValueError):
        await exp.add_from_bank(uid, uid, [rows[0]] * 2001)
    assert await db().expenses.count_documents({"ws": uid}) == 7


# ───────────────────────── two taps at once ─────────────────────────
def yielding(monkeypatch, *names):
    """Real Motor hands control back to the event loop on every database call; the in-memory double doesn't. This makes
    the named calls yield first, so two requests at once interleave the way they would in production."""
    import mongomock_motor
    cls = mongomock_motor.AsyncMongoMockCollection
    for name in names:
        original = getattr(cls, name)

        def make(original):
            async def call(self, *a, **k):
                await asyncio.sleep(0)
                return await original(self, *a, **k)
            return call
        monkeypatch.setattr(cls, name, make(original))


async def test_two_bank_imports_at_once_add_each_payment_once(client, monkeypatch):
    c, uid = await owner_on("9840012000", "lite")
    info = await db().expenses.index_information()
    assert info["ws_bank_ref_unique"]["unique"] and info["ws_bank_ref_unique"]["partialFilterExpression"] == {"bank_ref": {"$gt": ""}}
    assert "ws_1_bank_ref_1" not in info
    yielding(monkeypatch, "insert_many", "insert_one", "find_one", "update_one")
    rows = [{"date": day(-1), "amount": 1000 + i, "payee": f"Supplier {i}", "narration": "", "category": "purchases", "bank_ref": f"UTR{i:03d}"}
            for i in range(12)]
    a, b = await asyncio.gather(exp.add_from_bank(uid, uid, rows), exp.add_from_bank(uid, uid, rows))
    assert await db().expenses.count_documents({"ws": uid}) == 12
    assert a["added"] + b["added"] == 12 and a["skipped_duplicates"] + b["skipped_duplicates"] == 12
    # entries typed by hand all have bank_ref "" and are never blocked by the index
    for _ in range(3):
        await add(c, amount=10, category="other")
    assert await db().expenses.count_documents({"ws": uid}) == 15


async def test_a_double_tap_adds_one_cash_flow_line(client, monkeypatch):
    c, uid = await owner_on("9840012100", "lite")
    e = await add(c, date=f"{month()}-01", amount=45000, category="rent", vendor="Shree Estates", paid_by="bank")
    yielding(monkeypatch, "insert_one", "find_one", "update_one", "find_one_and_update")
    one, two = await asyncio.gather(c.post(f"/api/expenses/{e['id']}/to-cash-flow"), c.post(f"/api/expenses/{e['id']}/to-cash-flow"))
    assert one.status_code == two.status_code == 200, (one.text, two.text)
    assert await db().cash_items.count_documents({"owner_id": uid}) == 1
    assert one.json()["cash_item"]["id"] == two.json()["cash_item"]["id"]
    assert sorted([one.json()["created"], two.json()["created"]]) == [False, True]
    info = await db().cash_items.index_information()
    assert info["cash_items_expense_line_unique"]["unique"]


# ───────────────────────── one payment, one Cash flow line (from either page) ─────────────────────────
async def test_monthly_out_counts_a_payment_once_across_expenses_and_the_bank_page(client):
    c, uid = await owner_on("9840013000", "lite")
    # a line the bank statement page made for the rent (the lead's bankreader stores its key as bank_key)
    await db().cash_items.insert_one({"_id": "bankline1", "owner_id": uid, "kind": "out", "title": "SHREE ESTATES.", "amount": 44000.0, "date": None,
                                      "day_of_month": 3, "recurrence": "monthly", "source": "bank_statement", "bank_key": "rec-rent-1",
                                      "created_by": uid, "created_at": now()})
    rent = await add(c, date=f"{month()}-01", amount=45000, category="rent", vendor="Shree Estates", paid_by="bank", recurring=True)
    s = (await c.get("/api/expenses/summary")).json()
    assert [(r["vendor"], r["in_cash_flow"]) for r in s["recurring"]] == [("Shree Estates", True)]   # 44,000 vs 45,000: same payment
    r = (await c.post(f"/api/expenses/{rent['id']}/to-cash-flow")).json()
    assert r["created"] is False and r["cash_item"]["id"] == "bankline1"
    assert await db().cash_items.count_documents({"owner_id": uid}) == 1

    # same name, but 30% apart: a different payment, so it gets its own line
    net = await add(c, date=f"{month()}-01", amount=1000, category="phone_internet", vendor="Fastnet Broadband")
    await db().cash_items.insert_one({"_id": "bankline2", "owner_id": uid, "kind": "out", "title": "Fastnet Broadband", "amount": 1300.0, "date": None,
                                      "day_of_month": 7, "recurrence": "monthly", "source": "bank_statement", "bank_key": "rec-net-1",
                                      "created_by": uid, "created_at": now()})
    r = (await c.post(f"/api/expenses/{net['id']}/to-cash-flow")).json()
    assert r["created"] is True and r["cash_item"]["source"] == "expenses" and r["cash_item"]["expense_id"] == net["id"]
    assert await db().cash_items.count_documents({"owner_id": uid}) == 3

    # the bank page calling the helper finds the line Expenses made (and vice versa), whatever the case or punctuation
    line, created = await exp.monthly_out(uid, uid, "FASTNET  broadband!", 1050, 7, "bank_statement", "rec-net-2")
    assert created is False and line["source"] == "expenses" and line["expense_id"] == net["id"]
    # the same key from the same page twice: one line
    first, made = await exp.monthly_out(uid, uid, "Ledgerly Cloud Books", 999, 21, "bank_statement", "rec-soft-1", ref={"import_id": "imp-1"})
    again, made_again = await exp.monthly_out(uid, uid, "Ledgerly Cloud Books (renamed)", 1500, 21, "bank_statement", "rec-soft-1")
    assert made is True and made_again is False and again["_id"] == first["_id"]
    assert first["bank_key"] == "rec-soft-1" and first["import_id"] == "imp-1" and first["recurrence"] == "monthly" and first["kind"] == "out"
    assert first["day_of_month"] == 21 and first["amount"] == 999.0 and first["date"] is None
    for bad in ({"amount": 0, "key": "k"}, {"amount": 100, "key": ""}):
        with pytest.raises(ValueError):
            await exp.monthly_out(uid, uid, "X Co", bad["amount"], 1, "expenses", bad["key"])
    # a line someone typed by hand in Cash flow isn't matched (only the two pages' lines are)
    await db().cash_items.insert_one({"_id": "manual", "owner_id": uid, "kind": "out", "title": "Sai Electricals", "amount": 5000.0, "date": None,
                                      "day_of_month": 9, "recurrence": "monthly", "source": "owner", "created_at": now()})
    _, made = await exp.monthly_out(uid, uid, "Sai Electricals", 5000, 9, "expenses", "sai electricals|repairs")
    assert made is True

    # taking the rent out of Cash flow from Expenses removes the bank page's line for it too
    assert (await c.delete(f"/api/expenses/{rent['id']}/to-cash-flow")).status_code == 200
    assert not await db().cash_items.find_one({"_id": "bankline1"})
    assert (await c.get("/api/expenses/summary")).json()["recurring"][0]["in_cash_flow"] is False

    # the Knowledge Hub warning still shows for a fixed cost cash flow counts already
    await db().businesses.update_one({"owner_id": uid}, {"$set": {"money": {"fixed_costs": [{"name": "Office rent", "amount": 45000, "day": 3, "kind": "rent"}]}}})
    rec = (await c.get("/api/expenses/summary")).json()["recurring"]
    assert rec[0]["in_knowledge_hub"] == {"name": "Office rent", "amount": 45000.0}


# ───────────────────────── a staff edit that lands after the approval ─────────────────────────
async def test_a_staff_change_that_lands_after_approval_is_refused(client, monkeypatch):
    owner, uid = await owner_on("9840014000", "growth")
    staff, _ = await join(owner, "9840014001", "Ravi Staff", "staff")
    e = await add(staff, amount=640, category="travel", vendor="City Auto Rickshaw", paid_by="cash")
    assert (await staff.post(f"/api/expenses/{e['id']}/bill", files={"file": ("bill.pdf", PDF, "application/pdf")})).status_code == 200
    original = exp._editable

    async def approved_meanwhile(ctx, expense_id):   # the manager approves just after the staff member's request was checked
        d = await original(ctx, expense_id)
        await db().expenses.update_one({"_id": expense_id}, {"$set": {"status": "approved"}})
        return d
    monkeypatch.setattr(exp, "_editable", approved_meanwhile)
    for method, path, kw in (("PATCH", f"/api/expenses/{e['id']}", {"json": {"amount": 6400}}),
                             ("DELETE", f"/api/expenses/{e['id']}", {}),
                             ("POST", f"/api/expenses/{e['id']}/bill", {"files": {"file": ("new.png", PNG, "image/png")}}),
                             ("DELETE", f"/api/expenses/{e['id']}/bill", {})):
        await db().expenses.update_one({"_id": e["id"]}, {"$set": {"status": "to_approve"}})
        r = await staff.request(method, path, **kw)
        assert r.status_code == 409 and "approved or changed while you were working" in r.json()["detail"], (method, path, r.text)
    d = await db().expenses.find_one({"_id": e["id"]})
    assert d and d["amount"] == 64_000 and d["bill_file_id"]   # nothing changed, nothing removed
    assert await db().files.count_documents({}) == 1 and (await db().files.find_one({}))["content_type"] == "application/pdf"   # the refused PNG wasn't kept
    # a manager isn't held to the staff rule
    monkeypatch.setattr(exp, "_editable", original)
    assert (await owner.patch(f"/api/expenses/{e['id']}", json={"note": "Checked"})).status_code == 200


# ───────────────────────── the caps are reported, not hidden ─────────────────────────
async def test_caps_are_reported(client, monkeypatch):
    c, _ = await owner_on("9840015000", "lite")
    for i in range(5):
        await add(c, date=f"{month()}-01", amount=100 + i, category="other", vendor=f"Vendor {i}")
    monkeypatch.setattr(exp, "LIST_CAP", 2)
    monkeypatch.setattr(exp, "TOTALS_CAP", 3)
    monkeypatch.setattr(exp, "SUMMARY_CAP", 4)
    body = (await c.get("/api/expenses")).json()
    assert len(body["items"]) == 2 and body["more"] is True and body["shown_cap"] == 2
    assert body["totals"]["count"] == 3 and body["totals_capped"] is True and body["totals_cap"] == 3
    s = (await c.get("/api/expenses/summary")).json()
    assert s["capped"] is True and s["cap"] == 4 and s["count"] == 4
    monkeypatch.setattr(exp, "TOTALS_CAP", 20000)
    body = (await c.get("/api/expenses")).json()
    assert body["totals"]["count"] == 5 and body["totals"]["spent"] == 510.0 and body["totals_capped"] is False   # all 5, though 2 are shown
