"""Bank statement reader (v2.2): upload, the picture, remembered categories, into Expenses once (salaries only when the
owner ticks them, guessed rows only when picked), repeating payments into Cash flow, passwords, privacy (nothing of the
file or the password kept; masked text; 24 hours) and who may see it.
Every business, payee, account and number here is invented."""
from datetime import timedelta

from app.db import IST, db, now
from tests.conftest import PROFILE, new_owner
from tests.test_bankstatement import axis_table_pdf, encrypt, simple_csv

ADMIN = "9999900000"


async def owner_on(phone: str, plan: str):
    c = await new_owner(phone, name="Owner Test")
    assert (await c.put("/api/business", json={**PROFILE, "industry": "Steel fabrication and engineering works"})).status_code == 200
    uid = (await c.get("/api/me")).json()["user"]["id"]
    if plan != "free":
        admin = await new_owner(ADMIN)
        assert (await admin.patch(f"/api/admin/users/{uid}", json={"plan": plan})).status_code == 200
    return c, uid


def d(months_back: int, day: int) -> str:
    t = now().astimezone(IST).date().replace(day=1)
    for _ in range(months_back):
        t = (t - timedelta(days=1)).replace(day=1)
    return t.replace(day=day).strftime("%d/%m/%Y")


def statement() -> bytes:
    rows, bal = [], 500000.0
    for m in (3, 2, 1):
        for date_, narr, out, inn in [
            (d(m, 2), "NEFT CR-ZZBK0000001-MEHTA FABRICATORS-INV 4401", "", "180000.00"),
            (d(m, 5), "UPI/DR/401122334455/SHREE ESTATES/ZZBK/zz.estates01@okzz/Rent", "45000.00", ""),
            (d(m, 7), "SALARY OCT STAFF PAYROLL TRF", "120000.00", ""),
            (d(m, 9), "BILLPAY MSEDCL 000123456789 ELECTRICITY", "18500.00", ""),
            (d(m, 12), "UPI/DR/401199887766/SHARMA STEEL TRADERS/ZZBK/zz.vendor01@okzz/MS sheets", "62000.00", ""),
            (d(m, 15), "ATM WDL 1234XXXXXXXX5678 ANDHERI", "10000.00", ""),
        ]:
            bal = bal - float(out or 0) + float(inn or 0)
            rows.append((date_, narr, out, inn, f"{bal:.2f}"))
    return simple_csv(rows)


async def upload(c, data: bytes, name="statement.csv", password: str | None = None):
    form = {"password": password} if password is not None else None
    return await c.post("/api/bank-statement", files={"file": (name, data, "text/csv")}, data=form)


async def test_a_statement_is_read_sorted_and_kept_masked_for_24_hours_only(client):
    c, uid = await owner_on("9862000001", "lite")
    files_before = await db().files.count_documents({})
    r = await upload(c, statement())
    assert r.status_code == 200, r.text
    s = r.json()
    debits = [t for t in s["transactions"] if t["kind"] == "debit"]
    assert len(s["transactions"]) == 18 and len(debits) == 15
    cats = {t["payee"]: t["category"] for t in debits if t["payee"]}
    assert cats.get("Shree Estates") == "rent" and cats.get("Sharma Steel Traders") == "purchases"
    assert any(t["category"] == "salaries" for t in debits) and any(t["category"] == "electricity" for t in debits)
    assert any(t["category"] == "cash_withdrawal" and not t["expense"] for t in debits)
    assert s["money_in"] == 540000 and s["cash_withdrawn"] == 30000 and s["spent"] == s["money_out"] - 30000
    rent = next(x for x in s["recurring"] if x["category"] == "rent")
    assert rent["amount"] == 45000 and rent["day_of_month"] == 5 and rent["months"] == 3
    # "Add all" would add 9: the 3 salary payments wait for the owner's tick
    assert s["waiting"] == 9 and s["waiting_salaries"] == 3 and s["to_check"] == 0 and s["keep_hours"] == 24
    # privacy: no file stored, no long numbers, no handles; nothing kept past 24 hours
    assert await db().files.count_documents({}) == files_before
    for t in s["transactions"]:
        assert "401122334455" not in t["narration"] and "zz.estates01@" not in t["narration"] and "000123456789" not in t["narration"]
    imp = await db().bank_imports.find_one({"_id": s["id"]})
    assert "password" not in str(imp) and imp["expires_at"] - imp["created_at"] == timedelta(hours=24)
    await db().bank_imports.update_one({"_id": s["id"]}, {"$set": {"expires_at": now() - timedelta(minutes=1)}})
    assert (await c.get("/api/bank-statement")).json()["latest"] is None
    assert (await c.get(f"/api/bank-statement/{s['id']}")).status_code == 404


async def test_picked_payments_go_into_expenses_once_and_a_remembered_payee_keeps_its_category(client):
    c, uid = await owner_on("9862000002", "lite")
    s = (await upload(c, statement())).json()
    vendor = next(t for t in s["transactions"] if t["payee"] == "Sharma Steel Traders")
    r = await c.patch(f"/api/bank-statement/{s['id']}/transactions/{vendor['ref']}", json={"category": "consumables", "remember": True})
    assert r.status_code == 200, r.text
    s = r.json()
    assert {t["category"] for t in s["transactions"] if t["payee"] == "Sharma Steel Traders"} == {"consumables"}
    assert (await c.patch(f"/api/bank-statement/{s['id']}/transactions/{vendor['ref']}", json={"category": "made_up"})).status_code == 400
    # one tap: every waiting payment (not the cash withdrawal, not the salaries) into Expenses
    r = (await c.post(f"/api/bank-statement/{s['id']}/to-expenses", json={"all_waiting": True})).json()
    assert r["added"] == 9 and r["skipped_private"] == 3 and r["statement"]["waiting"] == 0 and r["statement"]["waiting_salaries"] == 3
    assert "skipped_private" in r["reasons"]
    assert await db().expenses.count_documents({"ws": uid, "source": "bank"}) == 9
    assert await db().expenses.count_documents({"ws": uid, "category": "consumables"}) == 3
    # the salaries only with the owner's tick
    r = (await c.post(f"/api/bank-statement/{s['id']}/to-expenses", json={"all_waiting": True, "include_salaries": True})).json()
    assert r["added"] == 3 and r["statement"]["waiting_salaries"] == 0
    assert await db().expenses.count_documents({"ws": uid, "source": "bank"}) == 12
    # the same statement again: remembered category, nothing added twice
    again = (await upload(c, statement())).json()
    assert {t["category"] for t in again["transactions"] if t["payee"] == "Sharma Steel Traders"} == {"consumables"}
    assert again["waiting"] == 0
    r = (await c.post(f"/api/bank-statement/{again['id']}/to-expenses", json={"all_waiting": True}))
    assert r.status_code == 400   # nothing left to add
    assert await db().expenses.count_documents({"ws": uid, "source": "bank"}) == 12


async def test_a_repeating_payment_goes_into_cash_flow_once(client):
    c, uid = await owner_on("9862000003", "lite")
    s = (await upload(c, statement())).json()
    rent = next(x for x in s["recurring"] if x["category"] == "rent")
    for _ in range(2):
        r = await c.post(f"/api/bank-statement/{s['id']}/recurring-to-cash-flow", json={"key": rent["key"]})
        assert r.status_code == 200, r.text
    items = [i async for i in db().cash_items.find({"owner_id": uid, "source": "bank_statement"})]
    assert len(items) == 1 and items[0]["amount"] == 45000 and items[0]["day_of_month"] == 5 and items[0]["recurrence"] == "monthly"
    assert next(x for x in r.json()["recurring"] if x["key"] == rent["key"])["in_cash_flow"]
    cf = (await c.get("/api/cash-flow")).json()
    assert any(e["title"] == "Shree Estates" and e["kind"] == "out" for e in cf["events"])
    # removed in Cash flow since: the page doesn't claim it's still there, and it can be added again
    await db().cash_items.delete_one({"_id": items[0]["_id"]})
    s = (await c.get(f"/api/bank-statement/{s['id']}")).json()
    assert not next(x for x in s["recurring"] if x["key"] == rent["key"])["in_cash_flow"]
    r = await c.post(f"/api/bank-statement/{s['id']}/recurring-to-cash-flow", json={"key": rent["key"]})
    assert next(x for x in r.json()["recurring"] if x["key"] == rent["key"])["in_cash_flow"]
    assert await db().cash_items.count_documents({"owner_id": uid, "source": "bank_statement"}) == 1


async def test_a_row_whose_direction_was_guessed_is_added_only_when_the_owner_picks_it(client):
    c, uid = await owner_on("9862000011", "lite")
    data = simple_csv([(d(1, 3), "NEFT-PATIL INTERIORS INV 77", "18000.00"),
                       (d(1, 4), "BILLPAY MSEDCL ELECTRICITY", "5000.00")], header="Date,Narration,Amount")
    s = (await upload(c, data)).json()
    assert all(t["guessed"] for t in s["transactions"]) and s["waiting"] == 0 and s["to_check"] >= 1
    assert (await c.post(f"/api/bank-statement/{s['id']}/to-expenses", json={"all_waiting": True})).status_code == 400
    pick = next(t for t in s["transactions"] if t["expense"])
    r = (await c.post(f"/api/bank-statement/{s['id']}/to-expenses", json={"refs": [pick["ref"]]})).json()
    assert r["added"] == 1
    assert (await db().expenses.find_one({"ws": uid, "source": "bank"}))["bank_ref"].startswith("bank:")


async def test_the_same_payment_from_two_accounts_is_added_for_each(client):
    c, uid = await owner_on("9862000012", "lite")
    rows = [(d(1, 5), "UPI/DR/401122334455/SHREE ESTATES/ZZBK/zz.estates01@okzz/Rent", "45000.00", "", "455000.00")]
    for name in ("Acct_Statement_XXXXXXXX1111.csv", "Acct_Statement_XXXXXXXX2222.csv"):
        s = (await upload(c, simple_csv(rows), name)).json()
        assert s["account_last4"] in ("1111", "2222")
        assert (await c.post(f"/api/bank-statement/{s['id']}/to-expenses", json={"all_waiting": True})).json()["added"] == 1
    refs = sorted([e["bank_ref"] async for e in db().expenses.find({"ws": uid, "source": "bank"})])
    assert len(refs) == 2 and refs[0].startswith("bank:1111:") and refs[1].startswith("bank:2222:")


async def test_password_protected_pdf(client):
    c, _ = await owner_on("9862000004", "lite")
    pdf = encrypt(axis_table_pdf(), "zz1234")
    r = await upload(c, pdf, "statement.pdf")
    assert r.status_code == 400 and r.json()["detail"]["code"] == "password_needed"
    r = await upload(c, pdf, "statement.pdf", password="wrong")
    assert r.status_code == 400 and r.json()["detail"]["code"] == "wrong_password"
    r = await upload(c, pdf, "statement.pdf", password="x" * 65)
    assert r.status_code == 400 and "x" * 65 not in r.text           # never repeated back
    r = await upload(c, pdf, "statement.pdf", password="zz1234")
    assert r.status_code == 200, r.text
    assert "zz1234" not in str(await db().bank_imports.find_one({"_id": r.json()["id"]}))


async def test_unreadable_files_and_size_limit(client):
    c, _ = await owner_on("9862000005", "lite")
    r = await upload(c, b"\x89PNG\r\n\x1a\n" + b"\x00" * 200, "photo.png")
    assert r.status_code == 400 and r.json()["detail"]["code"] in ("unsupported", "unreadable")
    r = await upload(c, b"a" * (10 * 1024 * 1024 + 10), "big.csv")
    assert r.status_code == 413


async def test_only_the_owner_on_the_membership_and_only_their_own(client):
    free, _ = await owner_on("9862000006", "free")
    assert (await upload(free, statement())).status_code == 403
    a, _ = await owner_on("9862000007", "lite")
    b, _ = await owner_on("9862000008", "lite")
    s = (await upload(a, statement())).json()
    assert (await b.get(f"/api/bank-statement/{s['id']}")).status_code == 404
    ref = s["transactions"][0]["ref"]
    assert (await b.patch(f"/api/bank-statement/{s['id']}/transactions/{ref}", json={"category": "rent"})).status_code == 404
    assert (await b.post(f"/api/bank-statement/{s['id']}/to-expenses", json={"all_waiting": True})).status_code == 404
    assert (await b.delete(f"/api/bank-statement/{s['id']}")).status_code == 404
    assert (await b.get("/api/bank-statement")).json()["latest"] is None
    # a manager in a Growth team can't open the owner's bank statement
    g, _ = await owner_on("9862000009", "growth")
    assert (await g.post("/api/team/invites", json={"phone": "9862000010", "name": "Manager Test", "role": "manager"})).status_code == 200
    m = await new_owner("9862000010")
    assert (await upload(m, statement())).status_code == 403
    assert (await m.get("/api/bank-statement")).status_code == 403
    # delete it now
    assert (await a.delete(f"/api/bank-statement/{s['id']}")).json()["ok"]
    assert (await a.get(f"/api/bank-statement/{s['id']}")).status_code == 404
