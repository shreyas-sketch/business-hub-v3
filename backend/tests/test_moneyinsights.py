"""v2.2 Customer risk and Getting paid, and the sales history upload that feeds them (plus app/salesbook.py).
Every name, phone number and email here is invented."""
import csv
import hashlib
import hmac
import io
import json
import time
import zipfile
from time import perf_counter
from datetime import date, datetime, timedelta
from urllib.parse import unquote

import openpyxl

from app import salesbook
from app.db import IST, db, now
from app.routers import moneyinsights
from app.services import new_id
from tests.conftest import PROFILE, new_owner

ADMIN = "9999900000"
SECRET = "whsec_test_insights_5521"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


async def owner_on(phone: str, plan: str):
    c = await new_owner(phone)
    assert (await c.put("/api/business", json=PROFILE)).status_code == 200
    uid = (await c.get("/api/me")).json()["user"]["id"]
    if plan != "free":
        admin = await new_owner(ADMIN)
        assert (await admin.patch(f"/api/admin/users/{uid}", json={"plan": plan})).status_code == 200
    return c, uid


def today() -> date:
    return now().astimezone(IST).date()


def d(offset: int) -> date:
    return today() + timedelta(days=offset)


def dmy(offset: int) -> str:
    return d(offset).strftime("%d-%m-%Y")


def as_csv(rows: list[list]) -> bytes:
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    return buf.getvalue().encode()


async def up(c, content: bytes, name: str = "sales.csv", mime: str = "text/csv"):
    return await c.post("/api/sales-upload", files={"file": (name, content, mime)})


# ───────────────────────── upload: CSV, Tally's Sales Register, Indian dates and amounts ─────────────────────────
async def test_a_tally_sales_register_csv_is_read_with_indian_dates_and_amounts(client):
    c, uid = await owner_on("9861100001", "lite")
    sheet = [
        ["Shree Ganesh Interiors"], ["Sales Register"], [f"1-Apr-{d(-60).strftime('%y')} to 31-Mar-{d(300).strftime('%y')}"], [],
        ["Date", "Particulars", "Voucher Type", "Voucher No.", "Value", "CGST", "SGST", "IGST", "Gross Total"],
        [f"{d(-50).day}-{d(-50).strftime('%b-%y')}", "Mehta Traders", "Sales", "101", "1,00,000.00", "9,000.00", "9,000.00", "", "1,18,000.00"],
        [d(-40).strftime("%d/%m/%y"), "Kaveri Foods", "Sales", "102", "10000", "", "", "1800", "₹ 11,800.00"],
        [d(-30).strftime("%d.%m.%Y"), "Cash", "Sales", "103", "500", "45", "45", "", "Rs. 590/-"],
        ["", "", "", "", "", "", "", "", ""],
        [d(-20).strftime("%d-%b-%Y"), "Mehta Traders", "Credit Note", "CN-1", "(1,000.00)", "90", "90", "", "(1,180.00)"],
        [dmy(-10), "Mehta Traders", "Receipt", "R-9", "", "", "", "", "1,18,000.00"],
        ["", "Asha Rao", "Sales", "104", "100", "", "", "", "118"],              # no date
        [dmy(-5), "Asha Rao", "Sales", "105", "100", "", "", "", "n/a"],       # amount we can't read
        [dmy(-4), "", "Sales", "106", "100", "", "", "", "118"],               # no customer
        [d(-3).isoformat(), "Rohit  Shah", "Sales", "107", "200", "18", "18", "", "236"],
        ["Grand Total", "", "", "", "", "", "", "", "1,30,544.00"],
    ]
    r = await up(c, as_csv(sheet))
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["rows"] == 5 and s["skipped_unreadable"] == 3 and s["skipped_reasons"] == {"no_date": 1, "no_amount": 1, "no_customer": 1}
    assert s["skipped_examples"] == [12, 13, 14]           # the sheet's own row numbers
    assert s["skipped_not_sales"] == 1 and s["skipped_duplicates"] == 0
    assert s["from"] == d(-50).isoformat() and s["to"] == d(-3).isoformat()
    assert s["total"] == 118000 + 11800 + 590 - 1180 + 236
    assert s["customers"] == 4 and s["dates_read"] == "day first" and s["lines"][0] == "5 sales added — ₹1,29,446 in all."
    assert s["columns_found"]["amount"] == "Gross Total" and s["columns_found"]["customer"] == "Particulars"
    assert s["columns_found"]["gst"] == ["CGST", "SGST", "IGST"] and s["columns_found"]["invoice_no"] == "Voucher No."
    assert "2 rows skipped — no date or amount we could read." in s["lines"] and "1 row skipped — no customer name." in s["lines"]
    assert "1 row left out — receipts, payments or other entries that aren't sales." in s["lines"]
    rows = {r["invoice_no"]: r async for r in db().sales_rows.find({"ws": uid})}
    assert rows["101"]["amount"] == 11_800_000 and rows["101"]["gst"] == 1_800_000 and rows["101"]["date"] == d(-50).isoformat()
    assert rows["102"]["amount"] == 1_180_000 and rows["102"]["gst"] == 180_000 and rows["102"]["date"] == d(-40).isoformat()
    assert rows["103"]["amount"] == 59_000 and rows["103"]["customer"] == "Cash"
    assert rows["CN-1"]["amount"] == -118_000 and rows["CN-1"]["gst"] == -18_000          # a credit note counts against sales
    assert rows["107"]["customer"] == "Rohit Shah" and rows["107"]["phone"] == "" and rows["107"]["due_date"] == "" and rows["107"]["paid_on"] == ""
    batch = await db().sales_batches.find_one({"ws": uid})
    assert batch["rows"] == 5 and batch["total"] == sum(r["amount"] for r in rows.values()) and batch["has_paid_on"] is False
    assert batch["from"] == d(-50).isoformat() and batch["to"] == d(-3).isoformat() and batch["filename"] == "sales.csv"


async def test_month_first_dates_only_when_the_sheet_proves_it(client):
    c, uid = await owner_on("9861100002", "lite")
    us = as_csv([["Date", "Customer", "Amount"], ["03/15/2025", "Mehta Traders", "1000"], ["04/02/2025", "Kaveri Foods", "2000"]])
    s = (await up(c, us)).json()
    assert s["dates_read"] == "month first" and (s["from"], s["to"]) == ("2025-03-15", "2025-04-02")
    indian = as_csv([["Date", "Customer", "Amount"], ["04/02/2025", "Asha Rao", "1000"], ["15/03/2025", "Rohit Shah", "2000"]])
    s = (await up(c, indian)).json()
    assert s["dates_read"] == "day first" and (s["from"], s["to"]) == ("2025-02-04", "2025-03-15")
    # ambiguous everywhere (no number above 12): day first, the Indian way
    s = (await up(c, as_csv([["Date", "Customer", "Amount"], ["05/06/2025", "Joshi Exports", "500"]]))).json()
    assert s["from"] == "2025-06-05"


async def test_an_xlsx_with_a_cover_sheet_excel_dates_phones_and_paid_on(client):
    c, uid = await owner_on("9861100003", "lite")
    wb = openpyxl.Workbook()
    cover = wb.active
    cover.title = "Notes"
    cover.append(["Exported for the hub"])
    sh = wb.create_sheet("Sales")
    sh.append(["Kaveri Foods Pvt Ltd — sales"])
    sh.append([])
    sh.append(["Invoice Date", "Customer Name", "Mobile", "Invoice No", "Amount (Rs.)", "GST", "Due Date", "Paid On"])
    serial = (d(-25) - date(1899, 12, 30)).days
    sh.append([datetime.combine(d(-40), datetime.min.time()), "Mehta Traders", 9861200001.0, 501, 11800, 1800, d(-25), d(-20)])
    sh.append([serial, "Asha Rao", "12345", "502", "5,900.00", "900", dmy(-10), None])          # Excel date number; a bad phone is dropped
    sh.append([dmy(-5), "Rohit Shah", "+91 98612 00003", "INV/26-27/503", 2360.5, None, None, None])
    buf = io.BytesIO()
    wb.save(buf)
    r = await up(c, buf.getvalue(), "sales.xlsx", XLSX)
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["rows"] == 3 and s["skipped_unreadable"] == 0 and s["sheet"] == "Sales"
    assert s["columns_found"]["paid_on"] == "Paid On" and s["columns_found"]["phone"] == "Mobile" and s["columns_found"]["gst"] == ["GST"]
    rows = {r["invoice_no"]: r async for r in db().sales_rows.find({"ws": uid})}
    assert rows["501"] == {**rows["501"], "date": d(-40).isoformat(), "phone": "+919861200001", "amount": 1_180_000, "gst": 180_000,
                           "due_date": d(-25).isoformat(), "paid_on": d(-20).isoformat()}
    assert rows["502"]["date"] == d(-25).isoformat() and rows["502"]["phone"] == "" and rows["502"]["amount"] == 590_000 and rows["502"]["paid_on"] == ""
    assert rows["INV/26-27/503"]["phone"] == "+919861200003" and rows["INV/26-27/503"]["amount"] == 236_050 and rows["INV/26-27/503"]["gst"] is None
    assert (await db().sales_batches.find_one({"ws": uid}))["has_paid_on"] is True


# ───────────────────────── duplicates ─────────────────────────
async def test_duplicates_are_skipped_against_earlier_uploads_and_the_hubs_own_invoices(client):
    c, uid = await owner_on("9861100004", "running")
    inv1 = (await c.post("/api/invoices", json={"customer": {"name": "Mehta Traders", "phone": "98612 00011"}, "gst_mode": "intra",
                                                "items": [{"name": "Chimney", "qty": 1, "rate": 10000, "gst": 18}]})).json()
    inv2 = (await c.post("/api/invoices", json={"customer": {"name": "Kaveri Foods"}, "gst_mode": "intra",
                                                "items": [{"name": "Hob", "qty": 1, "rate": 5000, "gst": 18}]})).json()
    assert inv1["totals"]["total"] == 11800 and inv2["totals"]["total"] == 5900
    sheet = [
        ["Date", "Party Name", "Bill No", "Invoice Value"],
        [dmy(0), "Mehta Traders Pvt Ltd", inv1["number"], "11,800.40"],       # the hub's invoice by number (Tally rounded it)
        [dmy(0), "KAVERI  FOODS", "", "5,900.00"],                              # the hub's invoice by date + customer + amount
        [dmy(-400), "Joshi Exports", inv1["number"], "2,500.00"],               # the same number a financial year earlier: a different bill
        [dmy(-7), "Asha Rao", "", "1,000"],
        [dmy(-7), "Asha Rao", "", "1,000"],                                     # two bills on one day: both count
        [dmy(-6), "Rohit Shah", "T-77", "3,000"],
        [dmy(-5), "Rohit Shah", "T-77", "3,000"],                               # the same bill number again, another day: counted once
    ]
    s = (await up(c, as_csv(sheet))).json()
    assert s["rows"] == 4 and s["skipped_in_hub"] == 2 and s["skipped_repeated_in_sheet"] == 1 and s["skipped_duplicates"] == 3
    assert any("already in the hub as invoices" in x for x in s["lines"])
    assert await db().sales_rows.count_documents({"ws": uid, "customer": "Asha Rao"}) == 2
    assert await db().sales_rows.count_documents({"ws": uid, "customer": "Joshi Exports"}) == 1

    again = (await up(c, as_csv(sheet))).json()        # the same file again: nothing new
    assert again["rows"] == 0 and again["batch_id"] is None and again["skipped_duplicates"] == 7 and again["skipped_in_hub"] == 2
    assert again["lines"][0] == "No new sales were added."
    assert await db().sales_rows.count_documents({"ws": uid}) == 4 and await db().sales_batches.count_documents({"ws": uid}) == 1
    # a third Asha Rao bill that day is new; the two already there are not counted again
    more = (await up(c, as_csv([sheet[0], sheet[4], sheet[5], [dmy(-7), "Asha Rao", "", "1,000"]]))).json()
    assert more["rows"] == 1 and more["skipped_duplicates"] == 2
    # the history merges both and counts each sale once
    h = await salesbook.history(uid)
    assert sum(1 for r in h if r["source"] == "invoice") == 2 and sum(1 for r in h if r["source"] == "upload") == 5


async def test_remove_an_upload_and_another_business_cant_see_or_remove_it(client):
    c, uid = await owner_on("9861100005", "lite")
    s = (await up(c, as_csv([["Date", "Customer", "Amount"], [dmy(-3), "Mehta Traders", "1000"], [dmy(-2), "Kaveri Foods", "2000"]]))).json()
    listing = (await c.get("/api/sales-upload")).json()
    assert [b["id"] for b in listing["batches"]] == [s["batch_id"]] and listing["rows"] == 2 and listing["max_rows"] == 20000
    assert listing["batches"][0]["total"] == 3000 and listing["batches"][0]["rows"] == 2

    other, other_uid = await owner_on("9861100006", "lite")
    assert (await other.get("/api/sales-upload")).json()["batches"] == []
    assert (await other.delete(f"/api/sales-upload/{s['batch_id']}")).status_code == 404
    assert await db().sales_rows.count_documents({"ws": uid}) == 2
    risk = (await other.get("/api/customer-risk")).json()
    assert risk["sources"]["upload"] == 0 and risk["top"] == []
    assert await salesbook.history(other_uid) == []

    r = await c.delete(f"/api/sales-upload/{s['batch_id']}")
    assert r.status_code == 200 and r.json() == {"ok": True, "removed": 2}
    assert await db().sales_rows.count_documents({"ws": uid}) == 0 and (await c.get("/api/sales-upload")).json()["batches"] == []
    assert (await c.delete(f"/api/sales-upload/{s['batch_id']}")).status_code == 404


# ───────────────────────── what isn't accepted ─────────────────────────
async def test_zip_bombs_binaries_old_excel_and_big_files_are_refused(client, monkeypatch):
    c, uid = await owner_on("9861100007", "lite")
    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/worksheets/sheet1.xml", b"0" * (61 * 1024 * 1024))
    assert len(bomb.getvalue()) < 1024 * 1024
    r = await up(c, bomb.getvalue(), "sales.xlsx", XLSX)
    assert r.status_code == 400 and "60 MB" in r.json()["detail"]

    not_excel = io.BytesIO()
    with zipfile.ZipFile(not_excel, "w") as z:
        z.writestr("word/document.xml", "<w/>")
    assert (await up(c, not_excel.getvalue(), "sales.xlsx", XLSX)).status_code == 400
    assert (await up(c, b"PK\x03\x04 this is not really a zip", "sales.xlsx", XLSX)).status_code == 400
    for blob, name in ((b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, "sales.csv"), (b"%PDF-1.7\n%...", "sales.csv"),
                       (bytes(range(256)) * 8, "sales.csv"), (b"Date,Customer,Amount\n\x01\x02\x03\x04\x05\x06" * 50, "sales.csv")):
        r = await up(c, blob, name)
        assert r.status_code == 400 and "CSV or Excel" in r.json()["detail"], name
    r = await up(c, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512, "sales.xls", "application/vnd.ms-excel")
    assert r.status_code == 400 and ".xls" in r.json()["detail"]
    r = await up(c, b"Date,Customer,Amount\n" + b"9" * (10 * 1024 * 1024))
    assert r.status_code == 413
    assert (await up(c, b"")).status_code == 400
    r = await up(c, as_csv([["Item", "Qty"], ["Chimney", "2"]]))
    assert r.status_code == 400 and "column names" in r.json()["detail"]
    r = await c.post("/api/sales-upload", data={"x": "1"})
    assert r.status_code == 400
    many = [["Date", "Customer", "Amount"]] + [[dmy(-1), f"Customer {i}", "100"] for i in range(20_005)]
    r = await up(c, as_csv(many))
    assert r.status_code == 400 and "more than 20,000 rows" in r.json()["detail"]
    assert await db().sales_rows.count_documents({"ws": uid}) == 0

    # what a business keeps in all is capped too (smaller cap here so the test stays quick)
    monkeypatch.setattr(moneyinsights, "MAX_ROWS", 5)
    assert (await up(c, as_csv([["Date", "Customer", "Amount"]] + [[dmy(-1), f"Customer {i}", "100"] for i in range(3)]))).json()["rows"] == 3
    r = await up(c, as_csv([["Date", "Customer", "Amount"]] + [[dmy(-2), f"Customer {i}", "100"] for i in range(3)]))
    assert r.status_code == 400 and "up to 5 uploaded sales" in r.json()["detail"]
    assert await db().sales_rows.count_documents({"ws": uid}) == 3


async def test_one_upload_at_a_time_and_a_stale_lock_doesnt_block(client):
    c, uid = await owner_on("9861100008", "lite")
    sheet = as_csv([["Date", "Customer", "Amount"], [dmy(-1), "Mehta Traders", "100"]])
    await db().sales_upload_locks.insert_one({"_id": uid, "at": now()})
    r = await up(c, sheet)
    assert r.status_code == 409 and await db().sales_rows.count_documents({"ws": uid}) == 0
    await db().sales_upload_locks.update_one({"_id": uid}, {"$set": {"at": now() - timedelta(minutes=30)}})
    assert (await up(c, sheet)).json()["rows"] == 1
    assert await db().sales_upload_locks.count_documents({}) == 0


# ───────────────────────── customer risk ─────────────────────────
def risk_sheet() -> bytes:
    rows = [["Date", "Customer", "Mobile", "Voucher Type", "Amount"]]
    for off in (-10, -40, -70, -100):
        rows.append([dmy(off), "Kaveri Foods", "98612 00021", "Sales", "50,000"])
    rows.append([dmy(-15), "Kaveri Foods", "98612 00021", "Credit Note", "20,000"])
    for off, amt in ((-200, "30,000"), (-230, "30,000"), (-260, "30,000"), (-290, "10,000")):
        rows.append([dmy(off), "Mehta Traders", "98612 00022", "Sales", amt])
    rows.append([dmy(-5), "Asha Rao", "", "Sales", "60,000"])
    for off, amt in ((-520, "20,000"), (-460, "20,000"), (-400, "20,000"), (-100, "40,000")):
        rows.append([dmy(off), "Sharma Stores", "", "Sales", amt])
    for off in (-340, -330, -320, -120):                     # gaps 10, 10, 200: the usual gap (median) is 10 days
        rows.append([dmy(off), "Patel Hardware", "", "Sales", "10,000"])
    for off in (-200, -300):
        rows.append([dmy(off), "Rohit Shah", "", "Sales", "5,000"])
    rows.append([dmy(-50), "Cash", "", "Sales", "50,000"])
    rows.append([dmy(40), "Kaveri Foods", "98612 00021", "Sales", "99,000"])     # dated ahead: not a sale yet
    return as_csv(rows)


async def test_customer_risk_shares_concentration_and_customers_gone_quiet(client):
    c, uid = await owner_on("9861100009", "lite")
    s = (await up(c, risk_sheet())).json()
    assert s["rows"] == 21 and s["skipped_reasons"]["no_date"] == 1
    r = (await c.get("/api/customer-risk")).json()
    assert r["enough_data"] is True and r["as_of"] == today().isoformat()
    # 12 months: Kaveri 1,80,000 · Mehta 1,00,000 · Asha 60,000 · Sharma 40,000 · Patel 40,000 · Rohit 10,000 · cash 50,000
    assert r["total_12m"] == 480_000 and r["customers_12m"] == 6
    assert [(t["name"], t["amount"], t["share_pct"]) for t in r["top"]] == [
        ("Kaveri Foods", 180_000, 37.5), ("Mehta Traders", 100_000, 20.8), ("Asha Rao", 60_000, 12.5), ("Patel Hardware", 40_000, 8.3),
        ("Sharma Stores", 40_000, 8.3), ("Rohit Shah", 10_000, 2.1)]
    assert r["top"][0]["phone"] == "+919861200021"
    assert r["biggest_share_pct"] == 37.5 and r["top3_share_pct"] == 70.8 and r["concentration"] == "high"
    assert r["how_many_make_80"] == 5                      # 4 customers make 3,80,000 — just short of 80% of 4,80,000
    assert r["line"] == "1 customer is 38% of your sales."
    assert r["walk_in_12m"] == 50_000 and r["walk_in_share_pct"] == 10.4 and r["includes_estimates"] is False
    # gone quiet: Mehta (every 30 days, nothing for 200) and Patel (usual gap 10 by the median, nothing for 120)
    assert [q["name"] for q in r["quiet"]] == ["Mehta Traders", "Patel Hardware"] and r["quiet_count"] == 2
    mehta, patel = r["quiet"]
    assert (mehta["usual_gap_days"], mehta["days_since"], mehta["purchases"], mehta["last_purchase"]) == (30, 200, 4, d(-200).isoformat())
    assert mehta["yearly_value"] == round(100_000 / 290 * 365) == 125_862          # estimate: their sales over the days since their first
    assert (patel["usual_gap_days"], patel["days_since"]) == (10, 120) and patel["yearly_value"] == round(40_000 / 340 * 365)
    assert r["at_risk_yearly"] == 125_862 + 42_941
    assert r["quiet_line"] == "2 regular customers have gone quiet — about ₹1.7 L a year (estimate)."
    text = "Hello Mehta Traders, it's been a while! Is there anything you need from Shree Ganesh Interiors this month?"
    assert mehta["message"] == text
    assert mehta["whatsapp_link"].startswith("https://wa.me/919861200022?text=") and unquote(mehta["whatsapp_link"].split("text=")[1]) == text
    assert patel["whatsapp_link"].startswith("https://wa.me/?text=")           # no number: WhatsApp asks whom to send it to
    assert r["sources"] == {"invoices": 0, "razorpay": 0, "upload": 21, "customers_list": 0}
    assert not await db().outbox.count_documents({"to": {"$regex": "9861200022"}})     # nothing is sent by the hub


def test_concentration_levels_and_lines():
    level = moneyinsights._concentration
    assert level(25, 30) == "high" and level(10, 50) == "high"
    assert level(24.9, 49.9) == "medium" and level(15, 20) == "medium" and level(10, 35) == "medium"
    assert level(14.9, 34.9) == "low"
    line = moneyinsights._concentration_line
    assert line("high", [20, 18, 15, 5]) == "3 customers are 53% of your sales."
    assert line("high", [30, 20]) == "1 customer is 30% of your sales." and line("high", [24, 24, 3]) == "3 customers are 51% of your sales."
    assert line("medium", [12, 12, 12, 10]) == "3 customers are 36% of your sales."
    assert line("medium", [16, 5]) == "1 customer is 16% of your sales."
    assert line("low", [9, 8, 7, 6]) == "Your biggest customer is 9% of your sales, and your top 3 are 24%."
    assert moneyinsights._short(85_000) == "₹85,000" and moneyinsights._short(340_000) == "₹3.4 L" and moneyinsights._short(12_000_000) == "₹1.2 Cr"
    assert moneyinsights._short(1_234_567) == "₹12.3 L" and moneyinsights._short(100_000) == "₹1 L"
    assert moneyinsights._inr(165_000) == "₹1,65,000" and moneyinsights._inr(12_34_56_789.4) == "₹12,34,56,789" and moneyinsights._inr(999) == "₹999"


async def connected(phone: str, plan: str = "running"):
    c, uid = await owner_on(phone, plan)
    await c.put("/api/knowledge/identity", json={"entity": "pvt_ltd", "gst_registered": True, "gstin": "27ABCDE1234F1Z5", "gst_scheme": "monthly",
                                                  "state": "Maharashtra", "staff": 5, "registrations": []})
    r = await c.put("/api/connections/razorpay", json={"values": {"key_id": "rzp_test_ins123", "key_secret": "ks_test", "webhook_secret": SECRET}})
    assert r.status_code == 200, r.text
    return c, uid, r.json()["webhook"].rsplit("/", 1)[1]


async def deliver(client, token: str, payload: dict):
    body = json.dumps(payload).encode()
    sig = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    r = await client.post(f"/api/hooks/payments/{token}", content=body, headers={"content-type": "application/json", "x-razorpay-signature": sig})
    assert r.status_code == 200, r.text
    return r.json()


def captured(pid: str, paise: int, name: str, contact: str, email: str) -> dict:
    return {"entity": "event", "event": "payment.captured", "id": f"evt_{pid}",
            "payload": {"payment": {"entity": {"id": pid, "amount": paise, "currency": "INR", "email": email, "contact": contact,
                                               "notes": {"name": name}, "created_at": int(time.time())}}}}


def refund(rid: str, pid: str, paise: int) -> dict:
    return {"entity": "event", "event": "refund.processed", "id": f"evt_{rid}",
            "payload": {"refund": {"entity": {"id": rid, "payment_id": pid, "amount": paise, "currency": "INR", "created_at": int(time.time())}}}}


async def test_history_merges_invoices_razorpay_credit_notes_uploads_and_the_customers_list(client):
    c, uid, token = await connected("9861100010")
    inv = await c.post("/api/invoices", json={"customer": {"name": "Mehta Traders", "phone": "98612 00031"},
                                              "items": [{"name": "Chimney", "qty": 1, "rate": 10000, "gst": 18}], "due_in_days": 15})
    assert inv.status_code == 200 and inv.json()["totals"]["total"] == 11800
    await db().quotes.insert_one({"_id": new_id(), "ws": uid, "kind": "invoice", "number": "INV-0999", "customer": {"name": "Ghost Co", "phone": ""},
                                  "totals": {"subtotal": 99999, "gst": 0, "total": 99999}, "status": "cancelled", "created_at": now()})
    assert (await deliver(client, token, captured("pay_INS1", 236000, "Rohit Shah", "+919861200032", "rohit@shah.example")))["result"] == "invoiced"
    assert (await deliver(client, token, refund("rfnd_INS1", "pay_INS1", 59000)))["result"] == "credit_note"
    from app.routers import gateway
    await gateway.settle()
    sheet = [["Date", "Customer", "Amount"]] + [[dmy(o), "Mehta Traders", "20,000"] for o in (-30, -60, -90)]
    sheet += [[dmy(o), "Kaveri Foods", "10,000"] for o in (-10, -20, -40, -50)] + [[dmy(-12), "Cash Sales", "5,000"]]
    assert (await up(c, as_csv(sheet))).json()["rows"] == 8
    for body in ({"name": "Asha Rao", "phone": "98612 00033", "total_value": 50000, "purchases": 5, "last_purchase": d(-20).isoformat()},
                 {"name": "Joshi Exports", "phone": "98612 00034", "total_value": 100000, "purchases": 5, "last_purchase": d(-100).isoformat()},
                 {"name": "Desai Cafe", "phone": "98612 00035", "total_value": 60000, "purchases": 6, "last_purchase": d(-150).isoformat()}):
        assert (await c.post("/api/customers", json=body)).status_code == 200
    await db().customers.update_one({"ws": uid, "name": "Joshi Exports"}, {"$set": {"first_purchase": d(-500).isoformat()}})
    await db().customers.update_one({"ws": uid, "name": "Desai Cafe"}, {"$set": {"first_purchase": d(-400).isoformat()}})
    assert await db().customers.count_documents({"ws": uid, "name": "Rohit Shah"}) == 1        # the Razorpay payer is on the list too

    h = await salesbook.history(uid)
    assert [r["date"] for r in h] == sorted(r["date"] for r in h)
    by = {}
    for r in h:
        by.setdefault(r["source"], []).append(r)
    assert len(by["invoice"]) == 1 and by["invoice"][0]["amount"] == 11800 and by["invoice"][0]["gst"] == 1800
    assert by["invoice"][0]["due_date"] == d(15).isoformat() and by["invoice"][0]["key"] == "9861200031"
    assert sorted(r["amount"] for r in by["razorpay"]) == [-590, 2360] and min(r["gst"] for r in by["razorpay"]) == -90
    assert len(by["upload"]) == 8 and not any(r["customer"] == "Ghost Co" for r in h)
    assert salesbook.customer_key("Mehta  Traders ", "") == "mehta traders" and salesbook.customer_key("X", "+91 98612-00031") == "9861200031"
    recent = await salesbook.history(uid, since=d(-15))
    assert sorted(x["customer"] for x in recent) == ["Cash Sales", "Kaveri Foods", "Mehta Traders", "Rohit Shah", "Rohit Shah"]
    assert [x["date"] for x in recent][:2] == [d(-12).isoformat(), d(-10).isoformat()]
    listed = await salesbook.customers_without_history(uid, h)
    assert sorted(x["customer"] for x in listed) == ["Asha Rao", "Desai Cafe", "Joshi Exports"]       # not Rohit: he has sales
    assert all(x["source"] == "customers list" for x in listed)

    r = (await c.get("/api/customer-risk")).json()
    assert r["sources"] == {"invoices": 1, "razorpay": 2, "upload": 8, "customers_list": 3}
    # Mehta's sheet rows (no phone) join the invoice that has his phone: one customer
    assert [(t["name"], t["amount"], t["estimate"]) for t in r["top"]] == [
        ("Mehta Traders", 71_800, False), ("Joshi Exports", 66_000, True), ("Desai Cafe", 51_360, True), ("Asha Rao", 50_000, True),
        ("Kaveri Foods", 40_000, False), ("Rohit Shah", 1_770, False)]
    assert r["top"][0]["phone"] == "+919861200031"
    assert r["total_12m"] == 285_930 and r["includes_estimates"] is True and r["walk_in_12m"] == 5000
    assert [(q["name"], q["from_list"], q["yearly_value"], q["usual_gap_days"]) for q in r["quiet"]] == [("Desai Cafe", True, 54_750, 50)]
    assert r["quiet"][0]["whatsapp_link"].startswith("https://wa.me/919861200035?text=")


async def test_not_enough_data_says_so(client):
    c, uid = await owner_on("9861100011", "lite")
    r = (await c.get("/api/customer-risk")).json()
    assert r["enough_data"] is False and r["top"] == [] and r["quiet"] == [] and r["concentration"] is None
    assert r["sources"] == {"invoices": 0, "razorpay": 0, "upload": 0, "customers_list": 0}
    nine = [["Date", "Customer", "Amount"]] + [[dmy(-i), ["Mehta Traders", "Kaveri Foods", "Asha Rao"][i % 3], "1000"] for i in range(1, 10)]
    await up(c, as_csv(nine))
    r = (await c.get("/api/customer-risk")).json()
    assert r["enough_data"] is False and r["sales_counted"] == 9 and r["customers_counted"] == 3
    await up(c, as_csv([["Date", "Customer", "Amount"], [dmy(-11), "Rohit Shah", "1000"]]))
    assert (await c.get("/api/customer-risk")).json()["enough_data"] is True
    # plenty of sales but only two customers (cash isn't a customer)
    c2, uid2 = await owner_on("9861100012", "lite")
    await up(c2, as_csv([["Date", "Customer", "Amount"]] + [[dmy(-i), ["Mehta Traders", "Kaveri Foods", "Cash"][i % 3], "1000"] for i in range(1, 16)]))
    r = (await c2.get("/api/customer-risk")).json()
    assert r["enough_data"] is False and r["customers_counted"] == 2

    g = (await c.get("/api/getting-paid")).json()
    assert g["enough_data"] is False and g["line"] == "" and g["customers"] == [] and g["avg_days_to_pay"] is None
    assert g["ageing"] == {k: {"amount": 0, "count": 0} for k in ("not_due", "d1_30", "d31_60", "d61_90", "d90_plus")} and g["stuck_line"] == ""


# ───────────────────────── getting paid ─────────────────────────
def at(day: str, hour: int = 12) -> datetime:
    return datetime.fromisoformat(day).replace(hour=hour, tzinfo=IST)


async def test_getting_paid_days_to_pay_lateness_habits_and_ageing(client, monkeypatch):
    monkeypatch.setattr(moneyinsights, "today", lambda: date(2026, 6, 30))
    c, uid = await owner_on("9861100013", "running")
    await db().businesses.update_one({"owner_id": uid}, {"$set": {"money.payment_terms_days": 15}})

    async def due(customer, rupees, due_date, created, paid=None, phone="", invoice_created=None, original=None, invoice_no=""):
        doc = {"_id": new_id(), "ws": uid, "customer": customer, "phone": phone, "amount": rupees * 100, "due_date": due_date, "note": "",
               "invoice_no": invoice_no, "status": "paid" if paid else "due", "paid_at": at(paid) if paid else None, "created_at": at(created)}
        if original:
            doc["original_amount"] = original * 100
        if invoice_created:
            doc["invoice_id"] = new_id()
            await db().quotes.insert_one({"_id": doc["invoice_id"], "ws": uid, "kind": "invoice", "number": "INV-0001", "status": "paid",
                                          "customer": {"name": customer, "phone": phone}, "totals": {"total": rupees, "gst": 0},
                                          "created_at": at(invoice_created, 10)})
        await db().dues.insert_one(doc)

    # paid (days to pay from the invoice's date; days late from the due date, early = 0)
    await due("Mehta Traders", 10_000, "2026-05-16", "2026-05-05", "2026-06-10", phone="98612 00041", invoice_created="2026-05-01")   # 40 · 25 late
    await due("Mehta Traders", 30_000, "2026-04-16", "2026-04-01", "2026-05-01")                                                    # 30 · 15 late
    await due("Kaveri Foods", 20_000, "2026-05-16", "2026-05-01", "2026-05-14")                                                     # 13 · early
    await due("Kaveri Foods", 20_000, "2026-06-16", "2026-06-01", "2026-06-16")                                                     # 15 · on the day
    await due("Asha Rao", 1_000, "2026-06-10", "2026-06-01", "2026-06-20", original=5_000)                                          # 19 · 10 late (₹5,000)
    await due("Asha Rao", 9_000, "2025-04-10", "2025-04-01", "2025-05-01")                                                          # over a year ago
    # still owed
    await due("Kaveri Foods", 12_000, "2026-06-20", "2026-06-05")             # 10 days late
    await due("Patel Hardware", 2_000, "2026-05-31", "2026-05-01")            # 30
    await due("Patel Hardware", 7_000, "2026-05-25", "2026-05-01", invoice_no="INV-0042")   # 36
    await due("Patel Hardware", 4_000, "2026-05-01", "2026-04-01")            # 60
    await due("Mehta Traders", 50_000, "2026-04-20", "2026-04-01")            # 71
    await due("Mehta Traders", 6_000, "2026-04-01", "2026-03-01")             # 90
    await due("Asha Rao", 3_000, "2026-07-15", "2026-06-25")                  # not due yet
    await due("Asha Rao", 1_000, "2026-06-30", "2026-06-25")                  # due today: not late yet
    sheet = [["Date", "Customer", "Amount", "Due Date", "Received On"],
             ["01-03-2026", "Sharma Stores", "40,000", "31-03-2026", "30-05-2026"],      # 90 · 60 late
             ["01-04-2026", "Sharma Stores", "10,000", "01-05-2026", "05-06-2026"],      # 65 · 35 late
             ["01-02-2026", "Sharma Stores", "25,000", "01-03-2026", ""],                # unpaid, 121 days late
             ["20-06-2026", "Rohit Shah", "8,000", "05-07-2026", ""],                    # unpaid, not due
             ["21-06-2026", "Rohit Shah", "1,000", "", ""]]                              # no due date: not aged
    assert (await up(c, as_csv(sheet))).json()["rows"] == 5
    # a sheet with due dates but no "paid" column says nothing about payment: never counted as owed
    assert (await up(c, as_csv([["Date", "Customer", "Amount", "Due Date"], ["01-01-2026", "Joshi Exports", "99,000", "31-01-2026"]]))).json()["rows"] == 1

    g = (await c.get("/api/getting-paid")).json()
    assert g["as_of"] == "2026-06-30" and g["terms_days"] == 15 and g["enough_data"] is True and g["paid_count"] == 7
    weighted = (10_000 * 40 + 30_000 * 30 + 20_000 * 13 + 20_000 * 15 + 5_000 * 19 + 40_000 * 90 + 10_000 * 65) / 135_000
    assert g["avg_days_to_pay"] == round(weighted, 1) == 46.0
    assert g["median_days_to_pay"] == 30 and g["on_time_pct"] == 29 and g["on_time_based_on"] == 7
    assert g["line"] == "You give 15 days. On average you wait 46."
    assert g["sources"] == {"money_owed": 5, "sales_sheet": 2}
    people = [(x["name"], x["paid_count"], x["avg_days_to_pay"], x["avg_days_late"], x["habit"], x["unpaid"], x["last_paid"]) for x in g["customers"]]
    assert people == [("Sharma Stores", 2, 77.5, 47.5, "often late", 25_000, "2026-06-05"),
                      ("Mehta Traders", 2, 35.0, 20.0, "often late", 56_000, "2026-06-10"),
                      ("Kaveri Foods", 2, 14.0, 0.0, "pays on time", 12_000, "2026-06-16"),
                      ("Asha Rao", 1, 19.0, 10.0, "usually a little late", 4_000, "2026-06-20")]
    assert g["customers"][1]["phone"] == "98612 00041"
    assert g["ageing"] == {"not_due": {"amount": 12_000, "count": 3}, "d1_30": {"amount": 14_000, "count": 2},
                           "d31_60": {"amount": 11_000, "count": 2}, "d61_90": {"amount": 56_000, "count": 2},
                           "d90_plus": {"amount": 25_000, "count": 1}}
    assert g["stuck"] == 81_000 and g["stuck_line"] == "₹81,000 is more than 60 days late." and g["owed"] == 118_000
    assert [(o["customer"], o["amount"], o["days_late"], o["source"]) for o in g["oldest"]] == [
        ("Mehta Traders", 50_000, 71, "money owed"), ("Sharma Stores", 25_000, 121, "sales sheet"), ("Patel Hardware", 7_000, 36, "money owed"),
        ("Mehta Traders", 6_000, 90, "money owed"), ("Patel Hardware", 4_000, 60, "money owed")]
    assert g["oldest"][2]["invoice_no"] == "INV-0042" and g["sheet_has_paid_on"] is True

    # no payment terms in the Knowledge Hub: the line doesn't invent any
    await db().businesses.update_one({"owner_id": uid}, {"$unset": {"money.payment_terms_days": ""}})
    g = (await c.get("/api/getting-paid")).json()
    assert g["terms_days"] is None and g["line"] == "On average you wait 46 days to get paid."
    # within terms
    await db().businesses.update_one({"owner_id": uid}, {"$set": {"money.payment_terms_days": 60}})
    assert (await c.get("/api/getting-paid")).json()["line"] == "You give 60 days. On average you wait 46 — within your terms."


async def test_habit_thresholds_are_cash_flows(client, monkeypatch):
    monkeypatch.setattr(moneyinsights, "today", lambda: date(2026, 6, 30))
    c, uid = await owner_on("9861100014", "lite")
    rows = [["Date", "Customer", "Amount", "Due Date", "Paid On"]]
    for name, late in (("Mehta Traders", 3), ("Kaveri Foods", 4), ("Asha Rao", 15), ("Rohit Shah", 16), ("Joshi Exports", 0)):
        due_day = date(2026, 5, 1)
        rows.append(["01-04-2026", name, "1000", due_day.strftime("%d-%m-%Y"), (due_day + timedelta(days=late)).strftime("%d-%m-%Y")])
    await up(c, as_csv(rows))
    habits = {x["name"]: x["habit"] for x in (await c.get("/api/getting-paid")).json()["customers"]}
    assert habits == {"Mehta Traders": "pays on time", "Kaveri Foods": "usually a little late", "Asha Rao": "usually a little late",
                      "Rohit Shah": "often late", "Joshi Exports": "pays on time"}


# ───────────────────────── who may see it ─────────────────────────
async def test_free_plan_is_locked_membership_opens_it_and_staff_cant(client):
    free, _ = await owner_on("9861100015", "free")
    for path in ("/api/customer-risk", "/api/getting-paid", "/api/sales-upload"):
        r = await free.get(path)
        assert r.status_code == 403 and r.json()["detail"]["code"] == "locked" and r.json()["detail"]["tier"] == "lite", path
    r = await up(free, as_csv([["Date", "Customer", "Amount"], [dmy(-1), "Mehta Traders", "100"]]))
    assert r.status_code == 403
    assert (await free.delete("/api/sales-upload/anything")).status_code == 403

    member, _ = await owner_on("9861100016", "lite")
    for path in ("/api/customer-risk", "/api/getting-paid", "/api/sales-upload"):
        assert (await member.get(path)).status_code == 200, path

    owner, uid = await owner_on("9861100017", "growth")
    staff = await new_owner("9861100018")
    await db().users.update_one({"_id": (await staff.get("/api/me")).json()["user"]["id"]}, {"$set": {"team_of": uid, "team_role": "staff"}})
    manager = await new_owner("9861100019")
    await db().users.update_one({"_id": (await manager.get("/api/me")).json()["user"]["id"]}, {"$set": {"team_of": uid, "team_role": "manager"}})
    for path in ("/api/customer-risk", "/api/getting-paid", "/api/sales-upload"):
        assert (await staff.get(path)).status_code == 403, path
        assert (await manager.get(path)).status_code == 200, path
    assert (await up(staff, as_csv([["Date", "Customer", "Amount"], [dmy(-1), "Mehta Traders", "100"]]))).status_code == 403
    s = (await up(manager, as_csv([["Date", "Customer", "Amount"], [dmy(-1), "Mehta Traders", "100"]]))).json()
    b = await db().sales_batches.find_one({"_id": s["batch_id"]})
    assert b["ws"] == uid and b["created_by"] != uid
    assert (await staff.delete(f"/api/sales-upload/{s['batch_id']}")).status_code == 403
    assert [x["id"] for x in (await owner.get("/api/sales-upload")).json()["batches"]] == [s["batch_id"]]


# ───────────────────────── review fixes (v2.2) ─────────────────────────
async def test_a_hostile_10_mb_sheet_is_read_in_well_under_2_seconds(client):
    """Long runs of T, spaces, dashes and dots once made the date and amount patterns take seconds per cell (event loop frozen)."""
    c, uid = await owner_on("9861100020", "lite")
    head = '"a",' * 50_000 + "\r\n" + "Date,Customer,Amount,Due Date,Paid On,Voucher Type,Phone,Invoice No\r\n"
    row = ",".join(["1" + "T" * 16_000 + "x", " " * 16_000 + "x", "1" + "-" * 16_000 + "x", "1" + " " * 16_000 + "1:00x",
                    "Rs" + "." * 16_000, "x" * 16_000, "9" * 16_000, "I" * 16_000]) + "\r\n"
    n = (10 * 1024 * 1024 - len(head)) // len(row)
    data = (head + row * n).encode()
    assert 9.5 * 1024 * 1024 < len(data) <= 10 * 1024 * 1024
    started = perf_counter()
    parsed = moneyinsights.parse_sheet(data)
    assert perf_counter() - started < 2.0
    assert parsed["rows"] == [] and sum(parsed["reasons"].values()) == n
    started = perf_counter()
    r = await up(c, data)
    assert perf_counter() - started < 2.0
    assert r.status_code == 200 and r.json()["rows"] == 0 and r.json()["skipped_unreadable"] == n
    # every cell is capped before any pattern sees it
    assert moneyinsights.parse_date("1" + "T" * 50 + "x") == "" and moneyinsights.parse_amount("1" + "." * 50) is None
    assert moneyinsights.parse_date(" " * 5000 + "31-03-2026" + " " * 5000) == "2026-03-31"     # padding alone is fine
    assert len(moneyinsights._text("Mehta " * 10_000, 80)) <= 80


async def test_item_lines_of_one_bill_are_added_up_before_duplicates_are_checked(client):
    c, uid = await owner_on("9861100021", "lite")
    sheet = [["Date", "Party Name", "Invoice No", "Item", "Amount", "GST"],
             [dmy(-9), "Mehta Traders", "INV-101", "Chimney", "50,000", "7,500"],
             [dmy(-9), "Mehta Traders", "INV-101", "Hob", "20,000", "3,000"],
             [dmy(-9), "Mehta  Traders", "inv-101", "Fitting", "12,000", "1,800"],
             [dmy(-8), "Kaveri Foods", "INV-102", "Hob", "5,000", ""],
             [dmy(-7), "Asha Rao", "", "Repair", "1,000", ""],
             [dmy(-7), "Asha Rao", "", "Repair", "1,000", ""]]                   # no invoice number: never joined
    s = (await up(c, as_csv(sheet))).json()
    assert s["rows"] == 4 and s["added_up_bills"] == 1 and s["added_up_lines"] == 3 and s["skipped_duplicates"] == 0
    assert s["total"] == 82_000 + 5_000 + 2_000
    assert "3 lines with the same invoice number, customer and date were added up into 1 bill." in s["lines"]
    bill = await db().sales_rows.find_one({"ws": uid, "invoice_no": "INV-101"})
    assert bill["amount"] == 8_200_000 and bill["gst"] == 1_230_000 and bill["customer"] == "Mehta Traders"
    assert (await db().sales_rows.find_one({"ws": uid, "invoice_no": "INV-102"}))["gst"] is None
    again = (await up(c, as_csv(sheet))).json()
    assert again["rows"] == 0 and again["skipped_duplicates"] == 4


async def test_a_customer_who_returned_more_than_they_bought_counts_as_zero_not_a_minus(client):
    c, uid = await owner_on("9861100022", "lite")
    rows = [["Date", "Customer", "Voucher Type", "Amount"]]
    rows += [[dmy(o), "Kaveri Foods", "Sales", "30,000"] for o in (-10, -40, -70)]
    rows += [[dmy(-20), "Asha Rao", "Sales", "10,000"], [dmy(-30), "Rohit Shah", "Sales", "10,000"]]
    rows += [[dmy(-400), "Mehta Traders", "Sales", "1,50,000"], [dmy(-60), "Mehta Traders", "Credit Note", "1,20,000"]]
    rows += [[dmy(o), "Patel Hardware", "Sales", "5,000"] for o in (-15, -25)] + [[dmy(o), "Joshi Exports", "Sales", "5,000"] for o in (-35, -45)]
    assert (await up(c, as_csv(rows))).json()["rows"] == 11
    r = (await c.get("/api/customer-risk")).json()
    assert r["enough_data"] is True and r["total_12m"] == 130_000 and r["customers_12m"] == 5
    assert [t["name"] for t in r["top"]][0] == "Kaveri Foods" and "Mehta Traders" not in [t["name"] for t in r["top"]]
    assert r["biggest_share_pct"] == 69.2 and r["line"] == "1 customer is 69% of your sales." and r["returns_bigger_than_sales"] == 1
    assert all(0 < t["share_pct"] <= 100 for t in r["top"]) and round(sum(t["share_pct"] for t in r["top"])) == 100


async def test_sheet_rows_are_aged_to_their_upload_day_and_an_old_sheet_isnt_late_money(client, monkeypatch):
    monkeypatch.setattr(moneyinsights, "today", lambda: date(2026, 6, 30))
    c, uid = await owner_on("9861100023", "lite")
    head = ["Date", "Customer", "Amount", "Due Date", "Paid On"]
    a = (await up(c, as_csv([head, ["01-03-2026", "Sharma Stores", "30,000", "20-03-2026", ""],
                                   ["01-06-2026", "Rohit Shah", "5,000", "20-06-2026", ""],
                                   ["01-02-2026", "Asha Rao", "8,000", "01-03-2026", "15-03-2026"]]))).json()
    b = (await up(c, as_csv([head, ["01-01-2026", "Joshi Exports", "50,000", "31-01-2026", ""]]))).json()
    await db().sales_batches.update_one({"_id": a["batch_id"]}, {"$set": {"created_at": at("2026-06-10")}})     # 20 days ago
    await db().sales_batches.update_one({"_id": b["batch_id"]}, {"$set": {"created_at": at("2026-03-01")}})     # 121 days ago
    await db().dues.insert_one({"_id": new_id(), "ws": uid, "customer": "Kaveri Foods", "phone": "", "amount": 1_200_000, "due_date": "2026-06-20",
                                "status": "due", "paid_at": None, "created_at": at("2026-06-05")})
    g = (await c.get("/api/getting-paid")).json()
    # Sharma: 82 days late on the upload day (102 today); Rohit: not yet due on the upload day (10 days late today)
    assert g["ageing"] == {"not_due": {"amount": 5_000, "count": 1}, "d1_30": {"amount": 12_000, "count": 1},
                           "d31_60": {"amount": 0, "count": 0}, "d61_90": {"amount": 30_000, "count": 1}, "d90_plus": {"amount": 0, "count": 0}}
    assert g["stuck"] == 30_000 and g["stuck_line"] == "₹30,000 is more than 60 days late." and g["owed"] == 47_000
    assert [(o["customer"], o["days_late"], o["as_of"]) for o in g["oldest"]] == [("Sharma Stores", 82, "2026-06-10")]
    assert g["sheet_note"] == "Amounts from your sales sheet are as of your upload on 10 Jun — anything paid since isn't known here."
    assert g["sheet_stale"] == {"amount": 50_000, "count": 1, "uploaded_on": ["2026-03-01"]}
    assert g["stale_note"].startswith("₹50,000 unpaid in your sales sheet uploaded on 1 Mar isn't counted here") and "Upload a fresh sheet" in g["stale_note"]

    # four months on, nothing from either sheet is called late any more; Money owed still is
    monkeypatch.setattr(moneyinsights, "today", lambda: date(2026, 10, 28))
    g = (await c.get("/api/getting-paid")).json()
    assert g["owed"] == 12_000 and g["stuck"] == 12_000 and g["stuck_line"] == "₹12,000 is more than 60 days late."
    assert g["oldest"] == [{**g["oldest"][0], "customer": "Kaveri Foods", "source": "money owed"}] and g["sheet_note"] == ""
    assert g["sheet_stale"] == {"amount": 85_000, "count": 3, "uploaded_on": ["2026-03-01", "2026-06-10"]}
    assert "₹85,000 unpaid in your sales sheets uploaded on 1 Mar, 10 Jun" in g["stale_note"]
