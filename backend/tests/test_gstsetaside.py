"""GST set-aside (v2.2): GST on sales minus GST on bills, the due date from the compliance calendar, and "count it in my cash flow".

Other people are building the invoices/uploads/expenses screens at the same time, so their documents are inserted directly here
in the shapes agreed for v2.2. "Today" is frozen per test (the compliance calendar's clock and Cash flow's) so the periods and
due dates don't depend on the day the suite runs. Every business name, phone number, GSTIN and invoice number here is invented."""
from datetime import date, datetime, timezone

import pytest

from app import knowledge
from app.db import IST, db, now
from app.routers import cashflow as cashflow_router, compliance
from app.routers import gstsetaside as gst
from app.services import new_id
from tests.conftest import new_owner

ADMIN = "9999900000"
GSTIN = "27AAAPZ9999Q1Z9"        # invented, valid shape
BUSINESS = {"name": "Neelkamal Fabricators", "city": "Bhiwandi", "industry": "Sheet-metal fabrication for warehouses",
            "offers": [{"name": "Racking", "price": "", "unit": ""}], "ideal_customer": "Warehouse operators", "why_us": "Own press shop",
            "proof": "", "whatsapp": "90000 11111", "email": "office@neelkamal.example", "language": "English"}
LABEL = "An estimate from what's in your hub — not your GST return. Confirm with your CA."


# ───────────────────────── helpers ─────────────────────────
async def owner_on(phone: str, plan: str = "lite"):
    c = await new_owner(phone)
    assert (await c.put("/api/business", json={**BUSINESS, "whatsapp": phone})).status_code == 200
    uid = (await c.get("/api/me")).json()["user"]["id"]
    if plan != "free":
        admin = await new_owner(ADMIN)
        assert (await admin.patch(f"/api/admin/users/{uid}", json={"plan": plan})).status_code == 200
    return c, uid


async def teammate(phone: str, owner_id: str, role: str):
    c = await new_owner(phone)
    await db().users.update_one({"_id": (await c.get("/api/me")).json()["user"]["id"]}, {"$set": {"team_of": owner_id, "team_role": role}})
    return c


@pytest.fixture
def on_day(monkeypatch):
    """Freezes 'today' for the compliance calendar (which this page reads its clock from) and for Cash flow."""
    def freeze(d: date):
        monkeypatch.setattr(compliance, "today", lambda: d)
        monkeypatch.setattr(cashflow_router, "_today", lambda: d)
    return freeze


async def set_identity(c, registered: bool = True, scheme: str = "monthly", state: str = "Maharashtra"):
    r = await c.put("/api/knowledge/identity", json={"entity": "proprietor", "gst_registered": registered, "gstin": GSTIN if registered else "",
                                                      "gst_scheme": scheme, "state": state, "staff": 4, "registrations": []})
    assert r.status_code == 200, r.text
    await knowledge.settle()        # the compliance calendar is rebuilt from the identity in the background


def ist(*a) -> datetime:
    return datetime(*a, tzinfo=IST)


async def hub_doc(ws: str, number: str, gst: float, at: datetime, kind: str = "invoice", status: str = "due", source: str | None = None,
                  customer: str = "Sagar Steel Traders", phone: str = "+919000022222", total: float | None = None):
    sub = round(gst / 0.18, 2) if total is None else round(total - gst, 2)
    doc = {"_id": new_id(), "ws": ws, "kind": kind, "number": number, "status": status, "created_at": at.astimezone(timezone.utc),
           "customer": {"name": customer, "phone": phone}, "gst_mode": "intra",
           "totals": {"subtotal": sub, "gst": gst, "cgst": round(gst / 2, 2), "sgst": round(gst - round(gst / 2, 2), 2), "igst": 0,
                      "total": round(sub + gst, 2)}}
    if source:
        doc["source"] = source
    await db().quotes.insert_one(doc)
    return doc


async def sales_row(ws: str, day: str, gst: int | None, invoice_no: str = "", amount: int = 1_000_000, customer: str = "Om Packaging Works",
                    phone: str = "+919000033333"):
    await db().sales_rows.insert_one({"_id": new_id(), "ws": ws, "batch_id": "batch-1", "date": day, "customer": customer,
                                      "phone": phone, "amount": amount, "gst": gst, "invoice_no": invoice_no, "due_date": None,
                                      "paid_on": None, "created_at": now()})


async def bill(ws: str, day: str, gst: int, claimable: bool = True, status: str = "approved"):
    await db().expenses.insert_one({"_id": new_id(), "ws": ws, "date": day, "amount": gst * 6 + 100, "gst": gst, "gst_claimable": claimable,
                                    "category": "raw_material", "vendor": "Pragati Sheet Metals", "status": status})


async def gst_item(uid: str, rule_key: str, period: str) -> dict:
    return await db().compliance_items.find_one({"owner_id": uid, "rule_key": rule_key, "period": period})


def by_period(page: dict) -> dict:
    return {p["period"]: p for p in page["periods"]}


# ───────────────────────── the numbers (monthly filer) ─────────────────────────
async def test_monthly_filer_sums_every_source_in_paise_and_takes_the_due_date_from_gstr3b(client, on_day):
    on_day(date(2026, 11, 5))
    c, uid = await owner_on("9000100001")
    other, other_uid = await owner_on("9000100002")
    await set_identity(c)

    # October (last month): hub invoices by the India-time day they were made
    await hub_doc(uid, "INV-0001", 1800.00, ist(2026, 10, 3, 11), status="paid")
    await hub_doc(uid, "INV-0002", 900.45, ist(2026, 10, 31, 23, 30))                  # 18:00 UTC on 31 Oct: still October
    await hub_doc(uid, "INV-0003", 360.18, ist(2026, 10, 1, 0, 10), source="gateway")   # 30 Sep in UTC, 1 Oct in India
    await hub_doc(uid, "INV-0000", 5000.00, ist(2026, 9, 30, 23))                       # September: not shown
    await hub_doc(uid, "INV-0004", 7777.00, ist(2026, 10, 9), status="cancelled")       # a cancelled invoice never counts
    await hub_doc(uid, "CN-0001", 180.09, ist(2026, 10, 20), kind="credit_note", status="issued")
    await hub_doc(other_uid, "INV-0099", 99999.00, ist(2026, 10, 12))                   # another business
    # uploaded sales (paise): with GST, without, one that is a hub invoice again, one matching only ANOTHER business's number
    await sales_row(uid, "2026-10-10", 54_027, "OLD-17")
    await sales_row(uid, "2026-10-11", None, "OLD-18")
    await sales_row(uid, "2026-10-12", 90_045, " inv-0002 ", customer="Sagar  Steel Traders", phone="")   # INV-0002 again: counted once
    await sales_row(uid, "2026-10-13", 10_000, "INV-0099")                              # INV-0099 is not this business's
    await sales_row(other_uid, "2026-10-14", 1_000_000, "X-1")
    # bills (paise): only approved GST bills marked claimable count
    await bill(uid, "2026-10-04", 45_000)
    await bill(uid, "2026-10-28", 12_301)
    await bill(uid, "2026-10-05", 20_000, claimable=False)
    await bill(uid, "2026-10-06", 30_000, status="to_approve")
    await bill(uid, "2026-10-07", 0)
    await bill(uid, "2026-09-30", 80_000)                                                # September
    await bill(other_uid, "2026-10-08", 500_000)
    # November (this month, so far): no hub invoices, one uploaded sale, bills bigger than sales, three bills with no GST
    await sales_row(uid, "2026-11-02", 12_345, "OLD-30")
    await bill(uid, "2026-11-03", 50_000)
    for d in ("2026-11-01", "2026-11-02", "2026-11-04"):
        await bill(uid, d, 0)

    r = await c.get("/api/gst-setaside")
    assert r.status_code == 200, r.text
    page = r.json()
    assert page["label"] == LABEL and page["registered"] is True and page["scheme"] == "monthly"
    assert [p["period"] for p in page["periods"]] == ["2026-10", "2026-11"]
    oct_, nov = by_period(page)["2026-10"], by_period(page)["2026-11"]

    # October: 1,80,000 + 90,045 + 36,018 paise of sales GST; 18,009 credit; 54,027 + 10,000 uploaded; 45,000 + 12,301 bills
    assert oct_["period_label"] == "October 2026" and oct_["complete"] is True
    assert oct_["sales_gst"] == 3060.63 and oct_["credit_notes_gst"] == 180.09 and oct_["uploaded_sales_gst"] == 640.27
    assert oct_["bills_gst"] == 573.01 and oct_["bills_count"] == 2 and oct_["bills_without_gst_marked"] == 1
    assert oct_["estimate"] == 2947.80 and oct_["estimate_rounded"] == 2948 and oct_["carry_forward"] == 0
    assert oct_["counts"] == {"invoices": 3, "gateway_invoices": 1, "credit_notes": 1, "uploads": 2, "uploads_without_gst": 1,
                              "uploads_already_in_hub": 1, "bills": 2, "bills_not_claimable": 1, "bills_waiting": 1,
                              "bank_receipts": 0, "bank_accounts": 0}
    # the due date is the calendar's GSTR-3B item for October, never worked out here
    item = await gst_item(uid, "gstr3b", "October 2026")
    assert item and item["due_date"] == "2026-11-20"
    assert oct_["due"]["date"] == "2026-11-20" and oct_["due"]["item_id"] == item["_id"] and oct_["due"]["from"] == "calendar"
    assert oct_["due"]["title"] == item["title"] and oct_["days_left"] == 15
    assert oct_["in_cash_flow"] is False and oct_["cash_flow_amount"] is None and oct_["owner_amount"] is None and oct_["can_count"] is True
    joined = " ".join(oct_["notes"])
    assert "1 bill in October has no GST entered." in joined and "1 GST bill in October is waiting for approval" in joined
    assert "1 bill with GST in October isn't marked as GST you can claim back" in joined
    assert "1 uploaded sale in October has no GST amount" in joined and "1 uploaded sale in October is already in the hub" in joined

    # November: bills bigger than sales → nothing to keep aside, the difference shows as carry forward (not taken off anything)
    assert nov["complete"] is False and nov["period_label"] == "November 2026"
    assert nov["sales_gst"] == 0 and nov["uploaded_sales_gst"] == 123.45 and nov["bills_gst"] == 500.00
    assert nov["estimate"] == 0 and nov["estimate_rounded"] == 0 and nov["carry_forward"] == 376.55
    assert nov["bills_without_gst_marked"] == 3
    assert "3 bills this month have no GST entered." in " ".join(nov["notes"]) and "No invoices in the hub this month." in nov["notes"]
    assert any("isn't over yet" in n for n in nov["notes"]) and any("₹377 more than the GST on your sales" in n for n in nov["notes"])
    assert nov["due"]["date"] == "2026-12-20" and nov["due"]["item_id"] == (await gst_item(uid, "gstr3b", "November 2026"))["_id"]
    assert nov["can_count"] is False and nov["cash_flow_note"] == "Nothing to keep aside, so nothing to add to your cash flow."
    assert page["sources"] == {"invoices": 3, "gateway_invoices": 1, "credit_notes": 1, "uploads": 3, "bills": 3, "bank_months": 0}
    assert not any("upload your sales sheet" in n for n in page["notes"])   # they did upload

    # the other business sees only its own numbers
    await set_identity(other)
    o = by_period((await other.get("/api/gst-setaside")).json())["2026-10"]
    assert o["sales_gst"] == 99999.00 and o["uploaded_sales_gst"] == 10000.00 and o["bills_gst"] == 5000.00
    assert o["due"]["item_id"] == (await gst_item(other_uid, "gstr3b", "October 2026"))["_id"] != item["_id"]


async def test_an_uploaded_sale_is_not_skipped_for_a_matching_invoice_number_alone(client, on_day):
    """Review repro: the hub's INV-0007 is from May 2025, for another customer. A Tally sheet's INV-0007 in September 2026 is
    a different sale, and its ₹18,000 GST must count — skipping it would make the estimate too low."""
    on_day(date(2026, 10, 5))
    c, uid = await owner_on("9000100007")
    await set_identity(c)
    await hub_doc(uid, "INV-0007", 90.00, ist(2025, 5, 14), total=590.00)
    await sales_row(uid, "2026-09-18", 1_800_000, "INV-0007", amount=11_800_000, customer="Mehta Agro Exports", phone="+919000044444")
    # the same number in the same financial year, but another customer and a total more than ₹1 away: still another sale
    await hub_doc(uid, "INV-0008", 180.00, ist(2026, 6, 2), total=1180.00)
    await sales_row(uid, "2026-09-19", 54_000, "inv-0008", amount=354_000, customer="Mehta Agro Exports", phone="+919000044444")
    # the same number, customer and total, but last financial year (30 Mar 2026 is FY 2025-26; September 2026 is FY 2026-27)
    await hub_doc(uid, "INV-0009", 270.00, ist(2026, 3, 30), customer="Mehta Agro Exports", phone="+919000044444", total=1770.00)
    await sales_row(uid, "2026-09-20", 27_000, "INV-0009", amount=177_000, customer="Mehta Agro Exports", phone="+919000044444")

    sep = by_period((await c.get("/api/gst-setaside")).json())["2026-09"]
    assert sep["uploaded_sales_gst"] == 18810.00 and sep["sales_gst"] == 0 and sep["estimate"] == 18810.00
    assert sep["counts"]["uploads"] == 3 and sep["counts"]["uploads_already_in_hub"] == 0
    assert not any("already in the hub" in n for n in sep["notes"])


async def test_a_true_duplicate_of_a_hub_invoice_or_credit_note_is_counted_once(client, on_day):
    on_day(date(2026, 10, 5))
    c, uid = await owner_on("9000100008")
    await set_identity(c)
    # the same number and financial year, and the same customer by name (any case or spacing) — the totals differ by ₹60
    await hub_doc(uid, "INV-0021", 540.00, ist(2026, 9, 3), customer="Om Packaging Works", phone="", total=3540.00)
    await sales_row(uid, "2026-09-03", 54_000, " inv-0021", amount=360_000, customer="OM PACKAGING  WORKS", phone="")
    # ... by phone (the name is written differently); the hub invoice is from August, the same financial year
    await hub_doc(uid, "INV-0022", 180.00, ist(2026, 8, 20), customer="Kiran Hardware", phone="+919000055555", total=1180.00)
    await sales_row(uid, "2026-09-04", 18_000, "INV-0022", amount=200_000, customer="Kiran Hardware Stores", phone="90000 55555")
    # ... by a total within ₹1 (80 paise apart); a total ₹1.01 apart with another name is another sale
    await hub_doc(uid, "INV-0023", 360.00, ist(2026, 9, 5), customer="Patil Brothers", phone="", total=2360.00)
    await sales_row(uid, "2026-09-05", 36_000, "INV-0023", amount=236_080, customer="Patil Bros.", phone="")
    await hub_doc(uid, "INV-0024", 360.00, ist(2026, 9, 6), customer="Patil Brothers", phone="", total=2360.00)
    await sales_row(uid, "2026-09-06", 36_000, "INV-0024", amount=236_101, customer="Patil Bros.", phone="")
    # a refund already in the hub as a credit note is not taken off twice
    await hub_doc(uid, "CN-0003", 90.00, ist(2026, 9, 10), kind="credit_note", status="issued", customer="Om Packaging Works", phone="", total=590.00)
    await sales_row(uid, "2026-09-10", -9_000, "CN-0003", amount=-59_000, customer="Om Packaging Works", phone="")
    # a cancelled hub invoice doesn't make the uploaded sale a duplicate (as in the sales upload)
    await hub_doc(uid, "INV-0025", 99.00, ist(2026, 9, 7), status="cancelled", customer="Om Packaging Works", phone="")
    await sales_row(uid, "2026-09-07", 9_900, "INV-0025", amount=64_900, customer="Om Packaging Works", phone="")

    sep = by_period((await c.get("/api/gst-setaside")).json())["2026-09"]
    # hub: 540 + 360 + 360 of sales GST (INV-0022 is August's; INV-0025 is cancelled), 90 of credit notes
    assert sep["sales_gst"] == 1260.00 and sep["credit_notes_gst"] == 90.00
    # uploads: only INV-0024 (₹360) and INV-0025 (₹99) count; four rows are the hub's own documents again
    assert sep["uploaded_sales_gst"] == 459.00 and sep["estimate"] == 1629.00
    assert sep["counts"]["uploads"] == 2 and sep["counts"]["uploads_already_in_hub"] == 4
    assert any(n.startswith("4 uploaded sales in September are already in the hub") for n in sep["notes"])


async def test_no_invoices_or_uploads_says_what_is_missing(client, on_day):
    on_day(date(2026, 11, 5))
    c, uid = await owner_on("9000100005")
    await set_identity(c)
    page = (await c.get("/api/gst-setaside")).json()
    assert gst.UPLOAD_HINT in page["notes"]
    for p in page["periods"]:
        assert p["estimate"] == 0 and p["carry_forward"] == 0 and p["can_count"] is False
    assert "No invoices in the hub in October." in by_period(page)["2026-10"]["notes"]


async def test_a_due_date_already_past_comes_from_the_rules_table_and_cannot_go_into_cash_flow(client, on_day):
    # the identity is filled on 25 Nov, after October's GSTR-3B fell due (20 Nov): the calendar has no item for it
    on_day(date(2026, 11, 25))
    c, uid = await owner_on("9000100006")
    await set_identity(c)
    await hub_doc(uid, "INV-0101", 450.00, ist(2026, 10, 9))
    assert await gst_item(uid, "gstr3b", "October 2026") is None
    oct_ = by_period((await c.get("/api/gst-setaside")).json())["2026-10"]
    assert oct_["due"] == {"date": "2026-11-20", "title": "GSTR-3B (GST return and payment)", "item_id": None, "period": "October 2026",
                           "status": "overdue", "from": "rules"}
    assert oct_["days_left"] == -5 and oct_["can_count"] is False and "due date has passed" in oct_["cash_flow_note"]
    r = await c.put("/api/gst-setaside/cash-flow", json={"period": "2026-10"})
    assert r.status_code == 400


# ───────────────────────── QRMP ─────────────────────────
async def test_qrmp_months_one_and_two_are_pmt06_and_month_three_is_the_quarterly_3b(client, on_day):
    # 5 Nov: October and November are months 1 and 2 of Oct–Dec → PMT-06 by the 25th of the next month
    on_day(date(2026, 11, 5))
    b, buid = await owner_on("9000100010")
    await set_identity(b, scheme="qrmp", state="Delhi")
    await hub_doc(buid, "INV-0201", 1000.00, ist(2026, 10, 15))
    page = (await b.get("/api/gst-setaside")).json()
    assert page["scheme"] == "qrmp"
    oct_, nov = by_period(page)["2026-10"], by_period(page)["2026-11"]
    assert oct_["due"]["item_id"] == (await gst_item(buid, "pmt06", "October 2026"))["_id"] and oct_["due"]["date"] == "2026-11-25"
    assert nov["due"]["item_id"] == (await gst_item(buid, "pmt06", "November 2026"))["_id"] and nov["due"]["date"] == "2026-12-25"
    assert oct_["estimate"] == 1000.00 and not any("quarterly GSTR-3B settles" in n for n in oct_["notes"])
    assert gst.UPLOAD_HINT in page["notes"]

    # 5 Jan: December is month 3 → paid with the quarterly GSTR-3B (22nd in Maharashtra, 24th in Delhi); January is PMT-06
    on_day(date(2027, 1, 5))
    a, auid = await owner_on("9000100011")
    await set_identity(a, scheme="qrmp", state="Maharashtra")
    await hub_doc(auid, "INV-0301", 720.00, ist(2026, 12, 10))
    page = (await a.get("/api/gst-setaside")).json()
    dec, jan = by_period(page)["2026-12"], by_period(page)["2027-01"]
    q3b = await gst_item(auid, "gstr3b_qrmp_22", "Oct–Dec 2026")
    assert q3b and dec["due"]["item_id"] == q3b["_id"] and dec["due"]["date"] == "2027-01-22" and dec["days_left"] == 17
    assert dec["estimate"] == 720.00 and any("quarterly GSTR-3B settles the whole quarter" in n for n in dec["notes"])
    assert jan["due"]["item_id"] == (await gst_item(auid, "pmt06", "January 2027"))["_id"] and jan["due"]["date"] == "2027-02-25"
    d, duid = await owner_on("9000100012")
    await set_identity(d, scheme="qrmp", state="Delhi")
    dec = by_period((await d.get("/api/gst-setaside")).json())["2026-12"]
    assert dec["due"]["item_id"] == (await gst_item(duid, "gstr3b_qrmp_24", "Oct–Dec 2026"))["_id"] and dec["due"]["date"] == "2027-01-24"
    # the estimate goes onto the quarterly return's item
    await hub_doc(duid, "INV-0401", 99.50, ist(2026, 12, 2))
    r = await d.put("/api/gst-setaside/cash-flow", json={"period": "2026-12"})
    assert r.status_code == 200, r.text
    assert (await gst_item(duid, "gstr3b_qrmp_24", "Oct–Dec 2026"))["amount"] == 100.0       # ₹99.50 → ₹100, half up


# ───────────────────────── not registered / identity missing ─────────────────────────
async def test_not_registered_shows_no_estimate_but_flags_gst_charged(client, on_day):
    on_day(date(2026, 11, 5))
    c, uid = await owner_on("9000100020")
    await set_identity(c, registered=False)
    page = (await c.get("/api/gst-setaside")).json()
    assert page["registered"] is False and page["scheme"] is None and page["periods"] == [] and page["label"] == LABEL
    assert "isn't registered for GST" in page["message"]
    assert not any("GST charged" in n for n in page["notes"])
    await hub_doc(uid, "INV-0501", 270.00, ist(2026, 10, 9))
    page = (await c.get("/api/gst-setaside")).json()
    assert any("₹270 of GST charged in October and November" in n and "CA" in n for n in page["notes"])
    assert (await c.put("/api/gst-setaside/cash-flow", json={"period": "2026-10"})).status_code == 400


async def test_identity_missing_shows_the_estimate_without_a_date_and_says_what_to_fill(client, on_day):
    on_day(date(2026, 11, 5))
    c, uid = await owner_on("9000100021")
    await hub_doc(uid, "INV-0601", 540.00, ist(2026, 10, 9))
    page = (await c.get("/api/gst-setaside")).json()
    assert page["registered"] is None and page["scheme"] is None
    assert "Knowledge Hub" in page["message"] and "GST registration, monthly or QRMP filing, and your state" in page["message"]
    oct_ = by_period(page)["2026-10"]
    assert oct_["estimate"] == 540.00 and oct_["due"] is None and oct_["days_left"] is None and oct_["can_count"] is False
    assert "Fill your business identity in the Knowledge Hub to get the due date." in oct_["notes"]
    assert not await db().compliance_items.count_documents({"owner_id": uid})        # no date was guessed anywhere
    assert (await c.put("/api/gst-setaside/cash-flow", json={"period": "2026-10"})).status_code == 400


# ───────────────────────── into Cash flow ─────────────────────────
async def test_cash_flow_put_update_replace_and_delete(client, on_day):
    frozen = date(2026, 11, 5)
    on_day(frozen)
    c, uid = await owner_on("9000100030")
    other, other_uid = await owner_on("9000100031")
    await set_identity(c)
    await set_identity(other)
    await c.put("/api/knowledge/money", json={"payment_terms_days": 15, "bank_balance": 500000,
                                               "bank_balance_on": min(now().astimezone(IST).date(), frozen).isoformat(), "fixed_costs": []})
    await hub_doc(uid, "INV-0701", 1000.00, ist(2026, 10, 10))
    oct_item = await gst_item(uid, "gstr3b", "October 2026")
    other_item = await gst_item(other_uid, "gstr3b", "October 2026")

    # an amount the owner typed on the item themselves is shown, and never replaced without asking
    assert (await c.patch(f"/api/compliance/items/{oct_item['_id']}", json={"amount": 4000})).status_code == 200
    oct_ = by_period((await c.get("/api/gst-setaside")).json())["2026-10"]
    assert oct_["owner_amount"] == 4000.0 and oct_["cash_flow_amount"] is None and oct_["in_cash_flow"] is True and oct_["can_count"] is True
    r = await c.put("/api/gst-setaside/cash-flow", json={"period": "2026-10"})
    assert r.status_code == 409 and "₹4,000" in r.json()["detail"]["message"] and "₹1,000" in r.json()["detail"]["message"]
    assert (await gst_item(uid, "gstr3b", "October 2026"))["amount"] == 4000
    r = await c.delete("/api/gst-setaside/cash-flow?period=2026-10")                       # not this page's amount: it stays
    assert r.status_code == 409 and (await gst_item(uid, "gstr3b", "October 2026"))["amount"] == 4000
    r = await c.put("/api/gst-setaside/cash-flow", json={"period": "2026-10", "replace": True})
    assert r.status_code == 200, r.text
    saved = await gst_item(uid, "gstr3b", "October 2026")
    assert saved["amount"] == 1000.0 and saved["amount_source"] == "gst_setaside" and saved["amount_estimate"] == 1000.0
    assert saved["amount_estimated_at"] and saved["amount_replaced"]["amount"] == 4000.0
    oct_ = by_period(r.json())["2026-10"]
    assert oct_["in_cash_flow"] is True and oct_["cash_flow_amount"] == 1000.0 and oct_["owner_amount"] is None and oct_["needs_update"] is False

    # DELETE clears only its own amount; PUT puts it back; Cash flow counts it as money going out on the due date
    r = await c.delete("/api/gst-setaside/cash-flow?period=2026-10")
    assert r.status_code == 200 and by_period(r.json())["2026-10"]["in_cash_flow"] is False
    cleared = await gst_item(uid, "gstr3b", "October 2026")
    assert cleared["amount"] is None and "amount_source" not in cleared
    assert (await c.put("/api/gst-setaside/cash-flow", json={"period": "2026-10"})).status_code == 200
    cf = (await c.get("/api/cash-flow")).json()
    assert cf["ready"] is True and cf["start"] == "2026-11-05"
    [day20] = [d for d in cf["days"] if d["date"] == "2026-11-20"]
    assert [(e["kind"], e["amount"], e["source"], e["id"]) for e in day20["events"]] == [("out", 1000.0, "compliance", oct_item["_id"])]
    assert day20["out"] == 1000.0

    # more invoices later: both numbers show, and "Update it" (PUT again) moves the amount
    await hub_doc(uid, "INV-0702", 250.50, ist(2026, 10, 15))
    oct_ = by_period((await c.get("/api/gst-setaside")).json())["2026-10"]
    assert oct_["cash_flow_amount"] == 1000.0 and oct_["estimate"] == 1250.50 and oct_["estimate_rounded"] == 1251 and oct_["needs_update"] is True
    oct_ = by_period((await c.put("/api/gst-setaside/cash-flow", json={"period": "2026-10"})).json())["2026-10"]
    assert oct_["cash_flow_amount"] == 1251.0 and oct_["needs_update"] is False

    # the owner types over it in the compliance calendar: now it's theirs, and DELETE leaves it alone
    assert (await c.patch(f"/api/compliance/items/{oct_item['_id']}", json={"amount": 1300})).status_code == 200
    oct_ = by_period((await c.get("/api/gst-setaside")).json())["2026-10"]
    assert oct_["owner_amount"] == 1300.0 and oct_["cash_flow_amount"] is None
    assert (await c.delete("/api/gst-setaside/cash-flow?period=2026-10")).status_code == 409
    assert (await gst_item(uid, "gstr3b", "October 2026"))["amount"] == 1300
    # another business's item was never touched
    assert (await db().compliance_items.find_one({"_id": other_item["_id"]})).get("amount") is None

    # done items can't be changed from here
    await hub_doc(uid, "INV-0703", 500.00, ist(2026, 11, 2))
    nov_item = await gst_item(uid, "gstr3b", "November 2026")
    nov = by_period((await c.get("/api/gst-setaside")).json())["2026-11"]
    assert nov["can_count"] is True and nov["cash_flow_shows_from"] == "2026-11-21"   # due 20 Dec: in the 30-day view from 21 Nov
    assert (await c.put("/api/gst-setaside/cash-flow", json={"period": "2026-11"})).status_code == 200
    assert (await c.post(f"/api/compliance/items/{nov_item['_id']}/done")).status_code == 200
    assert (await c.post(f"/api/compliance/items/{oct_item['_id']}/done")).status_code == 200
    assert (await c.put("/api/gst-setaside/cash-flow", json={"period": "2026-11"})).status_code == 409
    assert (await c.delete("/api/gst-setaside/cash-flow?period=2026-11")).status_code == 409
    assert (await c.put("/api/gst-setaside/cash-flow", json={"period": "2026-10", "replace": True})).status_code == 409
    assert (await gst_item(uid, "gstr3b", "November 2026"))["amount"] == 500.0
    assert (await gst_item(uid, "gstr3b", "October 2026"))["amount"] == 1300
    page = by_period((await c.get("/api/gst-setaside")).json())
    assert page["2026-11"]["can_count"] is False and page["2026-11"]["cash_flow_note"] == "Marked done in your compliance calendar."

    # only last month and this month, and only real months
    assert (await c.put("/api/gst-setaside/cash-flow", json={"period": "2026-08"})).status_code == 400
    assert (await c.put("/api/gst-setaside/cash-flow", json={"period": "2026-13"})).status_code == 422
    assert (await c.delete("/api/gst-setaside/cash-flow?period=oct")).status_code == 400


async def test_estimate_of_zero_is_not_added(client, on_day):
    on_day(date(2026, 11, 5))
    c, uid = await owner_on("9000100032")
    await set_identity(c)
    await hub_doc(uid, "INV-0801", 100.00, ist(2026, 10, 10))
    await bill(uid, "2026-10-11", 10_000)            # ₹100 of bills: nothing left to keep aside
    oct_ = by_period((await c.get("/api/gst-setaside")).json())["2026-10"]
    assert oct_["estimate"] == 0 and oct_["carry_forward"] == 0 and oct_["can_count"] is False
    r = await c.put("/api/gst-setaside/cash-flow", json={"period": "2026-10"})
    assert r.status_code == 400 and "₹0" in r.json()["detail"]


# ───────────────────────── plans and roles ─────────────────────────
async def test_plan_and_role_gating(client, on_day):
    on_day(date(2026, 11, 5))
    free, _ = await owner_on("9000100040", "free")
    r = await free.get("/api/gst-setaside")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "locked" and r.json()["detail"]["tier"] == "lite"
    assert (await free.put("/api/gst-setaside/cash-flow", json={"period": "2026-10"})).status_code == 403
    assert (await free.delete("/api/gst-setaside/cash-flow?period=2026-10")).status_code == 403

    lite, _ = await owner_on("9000100041", "lite")
    assert (await lite.get("/api/gst-setaside")).status_code == 200

    owner, uid = await owner_on("9000100042", "growth")
    await set_identity(owner)
    await hub_doc(uid, "INV-0901", 360.00, ist(2026, 10, 10))
    staff = await teammate("9000100043", uid, "staff")
    manager = await teammate("9000100044", uid, "manager")
    assert (await staff.get("/api/gst-setaside")).status_code == 403
    assert (await staff.put("/api/gst-setaside/cash-flow", json={"period": "2026-10"})).status_code == 403
    assert (await staff.delete("/api/gst-setaside/cash-flow?period=2026-10")).status_code == 403
    r = await manager.get("/api/gst-setaside")
    assert r.status_code == 200 and by_period(r.json())["2026-10"]["estimate"] == 360.00
    assert (await manager.put("/api/gst-setaside/cash-flow", json={"period": "2026-10"})).status_code == 200
    assert (await gst_item(uid, "gstr3b", "October 2026"))["amount"] == 360.0
