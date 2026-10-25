"""GST set-aside from a bank statement: upload September's statement and September (last month) shows the GST in the money
received from customers — only when no invoices or uploaded sales already count that month, kept after the statement itself
is deleted at 24 hours, gone with "Delete it now", and following a row re-sorted out of "From customers".
Every business, payee and number here is invented."""
from datetime import date

from app.db import db
from tests.test_bankstatement import simple_csv
from tests.test_gstsetaside import by_period, hub_doc, ist, on_day, owner_on, set_identity  # noqa: F401  (on_day is a fixture)


def september(days=(2, 29)) -> bytes:
    rows, bal = [], 300000.0
    for day, narr, out, inn in [
        (days[0], "NEFT CR-ZZBK0000001-OM PACKAGING WORKS-INV 0912", "", "118000.00"),
        (12, "UPI/DR/401199887766/SHARMA STEEL TRADERS/ZZBK/zz.vendor01@okzz/MS sheets", "62000.00", ""),
        (18, "IMPS CR 5566 SAGAR TRADERS PAYMENT", "", "59000.00"),
        (days[1], "BILLPAY MSEDCL 000123456789 ELECTRICITY", "18500.00", ""),
    ]:
        bal = bal - float(out or 0) + float(inn or 0)
        rows.append((f"{day:02d}/09/2026", narr, out, inn, f"{bal:.2f}"))
    return simple_csv(rows)


async def upload(c, data: bytes):
    r = await c.post("/api/bank-statement", files={"file": ("sep.csv", data, "text/csv")})
    assert r.status_code == 200, r.text
    return r.json()


async def test_septembers_statement_shows_as_last_months_gst(client, on_day):
    on_day(date(2026, 10, 25))
    c, uid = await owner_on("9000300001")
    await set_identity(c)
    s = await upload(c, september())
    assert sum(t["amount"] for t in s["transactions"] if t["kind"] == "credit" and t["category"] == "sales_receipt") == 177000
    sep = by_period((await c.get("/api/gst-setaside")).json())["2026-09"]
    assert sep["complete"] is True                                  # "Last month"
    assert sep["bank_received"] == 177000 and sep["bank_used"] is True and sep["bank_rate"] == 18
    assert sep["bank_sales_gst"] == 27000 and sep["net_sales_gst"] == 27000 and sep["estimate_rounded"] == 27000
    assert any("worked out from your bank statement" in n and "₹1,77,000" in n for n in sep["notes"])
    assert not any(n.startswith("No invoices in the hub") for n in sep["notes"])

    # the statement goes at 24 hours; September's total stays for the GST page
    await db().bank_imports.delete_many({"ws": uid})
    assert by_period((await c.get("/api/gst-setaside")).json())["2026-09"]["estimate_rounded"] == 27000

    # a fresh upload, then "Delete it now": its totals go too
    s = await upload(c, september())
    assert (await c.delete(f"/api/bank-statement/{s['id']}")).status_code == 200
    sep = by_period((await c.get("/api/gst-setaside")).json())["2026-09"]
    assert sep["bank_received"] == 0 and sep["estimate_rounded"] == 0


async def test_a_row_resorted_out_of_from_customers_leaves_the_gst(client, on_day):
    on_day(date(2026, 10, 25))
    c, _ = await owner_on("9000300002")
    await set_identity(c)
    s = await upload(c, september())
    loan = next(t for t in s["transactions"] if t["kind"] == "credit" and t["amount"] == 59000)
    r = await c.patch(f"/api/bank-statement/{s['id']}/transactions/{loan['ref']}", json={"category": "loan_in"})
    assert r.status_code == 200, r.text
    sep = by_period((await c.get("/api/gst-setaside")).json())["2026-09"]
    assert sep["bank_received"] == 118000 and sep["bank_sales_gst"] == 18000


async def test_invoices_in_the_hub_win_so_sales_are_not_counted_twice(client, on_day):
    on_day(date(2026, 10, 25))
    c, uid = await owner_on("9000300003")
    await set_identity(c)
    await hub_doc(uid, "INV-0101", 9000, ist(2026, 9, 10, 11))
    await upload(c, september())
    sep = by_period((await c.get("/api/gst-setaside")).json())["2026-09"]
    assert sep["bank_used"] is False and sep["net_sales_gst"] == 9000 and sep["estimate_rounded"] == 9000
    assert any("isn't added here, because your invoices already count" in n for n in sep["notes"])


async def test_usual_gst_rate_and_a_part_month_statement(client, on_day):
    on_day(date(2026, 10, 25))
    c, uid = await owner_on("9000300004")
    await set_identity(c)
    await db().businesses.update_one({"owner_id": uid}, {"$set": {"money.gst_rate": 12}})
    await upload(c, september(days=(10, 20)))
    sep = by_period((await c.get("/api/gst-setaside")).json())["2026-09"]
    assert sep["bank_rate"] == 12 and sep["bank_sales_gst"] == round(177000 * 12 / 112, 2)
    assert any("covers only 10–20 Sep" in n for n in sep["notes"])
    # a later statement covering more of September replaces the shorter one (same account, counted once)
    await upload(c, september())
    sep = by_period((await c.get("/api/gst-setaside")).json())["2026-09"]
    assert sep["bank_received"] == 177000 and sep["counts"]["bank_accounts"] == 1
    assert not any("covers only" in n for n in sep["notes"])
