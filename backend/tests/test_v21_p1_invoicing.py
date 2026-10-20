"""v2.1 P1 — invoicing (3.5): direct invoices and billing entry, the owner's own Razorpay → automatic invoices and credit
notes, and "Email invoices automatically". Every name, phone and email here is invented."""
import asyncio
import hashlib
import hmac
import json
import time

from app.db import db
from tests.conftest import PROFILE, new_owner

ADMIN = "9999900000"
SECRET = "whsec_test_kaveri_1234"


async def owner_on(phone: str, plan: str = "running"):
    c = await new_owner(phone)
    assert (await c.put("/api/business", json=PROFILE)).status_code == 200
    uid = (await c.get("/api/me")).json()["user"]["id"]
    if plan != "free":
        admin = await new_owner(ADMIN)
        assert (await admin.patch(f"/api/admin/users/{uid}", json={"plan": plan})).status_code == 200
    return c, uid


async def connected(phone: str, plan: str = "running"):
    c, uid = await owner_on(phone, plan)
    await c.put("/api/knowledge/identity", json={"entity": "pvt_ltd", "gst_registered": True, "gstin": "27ABCDE1234F1Z5", "gst_scheme": "monthly",
                                                  "state": "Maharashtra", "staff": 5, "registrations": []})
    r = await c.put("/api/connections/razorpay", json={"values": {"key_id": "rzp_test_abc123", "key_secret": "ks_test", "webhook_secret": SECRET}})
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["connected"] and v["webhook"].startswith("https://hub.example.in/api/hooks/payments/")
    assert all(f["value"] == "" for f in v["fields"] if f["type"] == "secret")      # secrets never come back
    return c, uid, v["webhook"].rsplit("/", 1)[1]


def captured(pid: str, paise: int, notes: dict | None = None, email: str = "rohit@shah.example", contact: str = "+919851130001") -> dict:
    return {"entity": "event", "event": "payment.captured", "id": f"evt_{pid}",
            "payload": {"payment": {"entity": {"id": pid, "amount": paise, "currency": "INR", "email": email, "contact": contact,
                                               "notes": notes or {}, "created_at": int(time.time()) + 1}}}}


def refund(rid: str, pid: str, paise: int) -> dict:
    return {"entity": "event", "event": "refund.processed", "id": f"evt_{rid}",
            "payload": {"refund": {"entity": {"id": rid, "payment_id": pid, "amount": paise, "currency": "INR", "created_at": int(time.time())}}}}


async def deliver(client, token: str, payload: dict, secret: str = SECRET):
    body = json.dumps(payload).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return await client.post(f"/api/hooks/payments/{token}", content=body, headers={"content-type": "application/json", "x-razorpay-signature": sig})


async def test_a_payment_makes_exactly_one_invoice_however_often_it_arrives(client):
    c, uid, token = await connected("9852000001")
    r = await deliver(client, token, captured("pay_T1", 118000, {"name": "Rohit Shah"}))
    assert r.status_code == 200 and r.json()["result"] == "invoiced"
    again = await deliver(client, token, captured("pay_T1", 118000, {"name": "Rohit Shah"}))
    assert again.status_code == 200 and again.json().get("duplicate")
    await asyncio.gather(*[deliver(client, token, captured("pay_T2", 50000)) for _ in range(4)])
    invs = [q async for q in db().quotes.find({"ws": uid, "kind": "invoice", "source": "gateway"})]
    assert sorted(q["gateway_payment_id"] for q in invs) == ["pay_T1", "pay_T2"]
    inv = next(q for q in invs if q["gateway_payment_id"] == "pay_T1")
    assert inv["status"] == "paid" and inv["customer"]["name"] == "Rohit Shah" and inv["number"].startswith("INV-")
    assert inv["totals"] == {"subtotal": 1000.0, "gst": 180.0, "total": 1180.0, "cgst": 90.0, "sgst": 90.0, "igst": 0}   # within Maharashtra
    assert inv["due_id"] is None and not await db().dues.count_documents({"ws": uid})
    assert await db().payment_events.count_documents({"ws": uid, "result": "invoiced"}) == 2
    # nothing went to the customer on WhatsApp
    assert not await db().outbox.count_documents({"channel": "whatsapp", "to": {"$regex": "9851130001"}})


async def test_gst_is_split_by_state(client):
    c, uid, token = await connected("9852000002")
    await deliver(client, token, captured("pay_K1", 236000, {"state": "Karnataka"}))
    await deliver(client, token, captured("pay_G1", 118000, {"gstin": "24AAACI1234Q1Z2", "gst": "5"}))     # a Gujarat GSTIN; the payer's "5%" is ignored
    await deliver(client, token, captured("pay_M1", 59000, {"state": "Maharashtra"}))
    by = {q["gateway_payment_id"]: q async for q in db().quotes.find({"ws": uid, "source": "gateway"})}
    assert by["pay_K1"]["gst_mode"] == "inter" and by["pay_K1"]["totals"] == {"subtotal": 2000.0, "gst": 360.0, "total": 2360.0, "cgst": 0, "sgst": 0, "igst": 360.0}
    assert by["pay_G1"]["gst_mode"] == "inter" and by["pay_G1"]["totals"]["subtotal"] == 1000.0 and by["pay_G1"]["totals"]["igst"] == 180.0
    assert by["pay_M1"]["gst_mode"] == "intra" and by["pay_M1"]["totals"]["cgst"] == 45.0 and by["pay_M1"]["totals"]["sgst"] == 45.0
    for q in by.values():   # always adds up to what the customer paid
        t = q["totals"]
        assert round(t["subtotal"] + t["gst"], 2) == t["total"] and round(t["cgst"] + t["sgst"] + t["igst"], 2) == t["gst"]


async def test_a_payment_with_an_invoice_number_marks_that_due_paid(client):
    c, uid, token = await connected("9852000003")
    inv = (await c.post("/api/invoices", json={"customer": {"name": "Mehta Traders", "phone": "98511 00099", "email": "accounts@mehta.example"},
                                               "items": [{"name": "Chimney", "qty": 2, "rate": 14500, "gst": 18}], "due_in_days": 15})).json()
    due = await db().dues.find_one({"ws": uid, "invoice_id": inv["id"]})
    assert due and due["email"] == "accounts@mehta.example" and due["amount"] == 3422000 and inv["due_id"] == due["_id"]
    r = await deliver(client, token, captured("pay_D1", 3422000, {"invoice_no": inv["number"]}, email="other@payer.example", contact=""))
    assert r.json()["result"] == "due_paid"
    due = await db().dues.find_one({"_id": due["_id"]})
    assert due["status"] == "paid" and due["gateway_payment_id"] == "pay_D1" and due["paid_by"] == "gateway" and due["auto_stopped"] == "paid"
    assert (await db().quotes.find_one({"_id": inv["id"]}))["status"] == "paid"
    assert not await db().quotes.count_documents({"ws": uid, "source": "gateway"})          # no second invoice
    # by customer + exact amount, when there's no invoice number
    other = (await c.post("/api/money", json={"customer": "Sharma Stores", "phone": "98511 00077", "amount": 18000, "due_date": "2026-11-30"})).json()
    assert (await deliver(client, token, captured("pay_D2", 1800000, contact="+919851100077"))).json()["result"] == "due_paid"
    assert (await db().dues.find_one({"_id": other["id"]}))["status"] == "paid"


async def test_a_refund_makes_one_credit_note(client):
    c, uid, token = await connected("9852000004")
    await deliver(client, token, captured("pay_R1", 118000))
    for _ in range(2):
        r = await deliver(client, token, refund("rfnd_1", "pay_R1", 59000))
        assert r.status_code == 200
    [cn] = [q async for q in db().quotes.find({"ws": uid, "kind": "credit_note"})]
    assert cn["number"] == "CN-0001" and cn["against"].startswith("INV-") and cn["gateway_refund_id"] == "rfnd_1"
    assert cn["totals"] == {"subtotal": 500.0, "gst": 90.0, "total": 590.0, "cgst": 45.0, "sgst": 45.0, "igst": 0}
    assert (await db().quotes.find_one({"ws": uid, "gateway_payment_id": "pay_R1"}))["refunded"] == 590.0
    notes = (await c.get("/api/credit-notes")).json()
    assert [n["number"] for n in notes] == ["CN-0001"] and notes[0]["share_text"].startswith("Credit note CN-0001")
    pdf = await c.get(f"/api/invoices/{cn['_id']}/pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"


async def test_webhook_signature_token_and_plan(client):
    c, uid, token = await connected("9852000005")
    assert (await deliver(client, token, captured("pay_X", 1000), secret="wrong-secret")).status_code == 401
    assert (await client.post(f"/api/hooks/payments/{token}", content=b"{}", headers={"content-type": "application/json"})).status_code == 401
    assert (await deliver(client, "x" * 24, captured("pay_X", 1000))).status_code == 404
    assert (await deliver(client, token, {"event": "order.paid", "payload": {}})).json()["ignored"] == "order.paid"
    assert not await db().quotes.count_documents({"ws": uid, "source": "gateway"})
    free, _ = await owner_on("9852000006", "free")
    assert (await free.put("/api/connections/razorpay", json={"values": {"key_id": "rzp_test_x"}})).status_code == 403
    lst = (await free.get("/api/connections")).json()
    assert next(x for x in lst if x["kind"] == "razorpay")["available"] is False


async def test_direct_invoice_and_billing_entry(client):
    c, uid, _ = await connected("9852000007")
    d = (await c.post("/api/invoices/draft", json={"text": "Sold 40 boxes to Mehta Traders, ₹850 each, 18% GST, due in 30 days"})).json()
    assert d["customer"]["name"] == "Mehta Traders" and d["due_in_days"] == 30 and d["missing_rates"] == 0 and d["gst_mode"] == "intra"
    assert d["items"][0] | {} == {"name": "boxes", "qty": 40.0, "unit": "nos", "rate": 850.0, "gst": 18.0}
    assert d["totals"]["total"] == 40120.0
    no_rate = (await c.post("/api/invoices/draft", json={"text": "Sold 10 chairs to Shah Furniture"})).json()
    assert no_rate["items"][0]["rate"] == 0 and no_rate["missing_rates"] == 1        # never an invented rate
    inv = (await c.post("/api/invoices", json={"customer": {"name": "Mehta Traders", "state": "Gujarat"}, "items": d["items"], "due_in_days": 30})).json()
    assert inv["kind"] == "invoice" and inv["gst_mode"] == "inter" and inv["totals"]["igst"] == 6120.0 and inv["status"] == "due"
    assert (await db().dues.find_one({"_id": inv["due_id"]}))["invoice_id"] == inv["id"]
    paid = (await c.post("/api/invoices", json={"customer": {"name": "Walk-in"}, "items": [{"name": "Hob", "qty": 1, "rate": 9000}],
                                                "paid_already": True})).json()
    assert paid["status"] == "paid" and paid["due_id"] is None
    page = await c.get(f"/api/quotes/{inv['id']}/print")
    assert page.status_code == 200 and inv["number"] in page.text
    assert (await c.post("/api/invoices", json={"customer": {"name": "X Y", "email": "not-an-email"}, "items": d["items"]})).status_code == 422


async def test_invoices_are_emailed_only_when_switched_on(client):
    c, uid, token = await connected("9852000008")
    body = {"customer": {"name": "Mehta Traders", "email": "accounts@mehta.example"}, "items": [{"name": "Chimney", "qty": 1, "rate": 14500}]}
    await c.post("/api/invoices", json=body)
    await deliver(client, token, captured("pay_E1", 118000))
    assert not await db().outbox.count_documents({"channel": "email"})
    s = (await c.put("/api/invoices/settings", json={"email_auto": True})).json()
    assert s["email_auto"] is True
    inv = (await c.post("/api/invoices", json=body)).json()
    [m] = [x async for x in db().outbox.find({"channel": "email"})]
    assert m["to"] == "accounts@mehta.example" and m["kind"] == "invoice" and m["attachments"][0]["name"] == f"{inv['number']}.pdf"
    assert "reminder" not in m["subject"].lower() and m["subject"].startswith(f"Invoice {inv['number']}")
    await deliver(client, token, captured("pay_E2", 59000, email="rohit@shah.example"))
    assert await db().outbox.count_documents({"channel": "email", "to": "rohit@shah.example"}) == 1
    # sending the same invoice again by hand is refused, not doubled
    assert (await c.post(f"/api/invoices/{inv['id']}/email")).status_code == 400
    assert await db().outbox.count_documents({"channel": "email", "to": "accounts@mehta.example"}) == 1


async def test_gateway_page_and_cash_flow_count_direct_payments(client):
    from datetime import date
    from app import cashflow
    c, uid, token = await connected("9852000009")
    await c.put("/api/knowledge/money", json={"payment_terms_days": 0, "bank_balance": 100000, "bank_balance_on": date.today().isoformat(), "fixed_costs": []})
    await deliver(client, token, captured("pay_C1", 118000))
    g = (await c.get("/api/gateway")).json()
    assert g["invoices_30d"] == 1 and g["connection"]["connected"] and g["events"][0]["result"] == "invoiced"
    f = await cashflow.forecast(uid)
    assert f["received_since_balance"] == 1180.0



# ───────────────────────── fixes from the independent review ─────────────────────────
async def test_a_small_payment_with_an_invoice_number_is_a_part_payment_never_a_full_one(client):
    c, uid, token = await connected("9852000010")
    inv = (await c.post("/api/invoices", json={"customer": {"name": "Mehta Traders", "email": "accounts@mehta.example"},
                                               "items": [{"name": "Chimney", "qty": 2, "rate": 14500, "gst": 18}], "due_in_days": 15})).json()
    due_id = inv["due_id"]
    r = (await deliver(client, token, captured("pay_P1", 100, {"invoice_no": inv["number"]}))).json()      # ₹1, typed by the payer
    assert r["result"] == "part_paid" and r["still_owed"] == 34219.0
    due = await db().dues.find_one({"_id": due_id})
    assert due["status"] == "due" and due["amount"] == 3421900 and due["original_amount"] == 3422000 and due["paid_so_far"] == 100
    assert [p["payment_id"] for p in due["part_payments"]] == ["pay_P1"]
    assert (await db().quotes.find_one({"_id": inv["id"]}))["status"] == "due"
    assert await db().payment_events.count_documents({"ws": uid, "result": "part_paid"}) == 1
    # the rest closes it; a payment for more than what's owed closes it too, and the extra is noted
    r = (await deliver(client, token, captured("pay_P2", 3421900, {"invoice_no": inv["number"]}))).json()
    assert r["result"] == "due_paid"
    due = await db().dues.find_one({"_id": due_id})
    assert due["status"] == "paid" and due["gateway_payment_id"] == "pay_P2"
    other = (await c.post("/api/invoices", json={"customer": {"name": "Sharma Stores"}, "items": [{"name": "Hob", "qty": 1, "rate": 1000, "gst": 0}]})).json()
    r = (await deliver(client, token, captured("pay_P3", 150000, {"invoice": other["number"]}))).json()
    assert r["result"] == "due_paid" and r["overpaid"] == 500.0
    assert not await db().quotes.count_documents({"ws": uid, "source": "gateway"})


async def test_two_payments_for_two_equal_dues_settle_both(client):
    c, uid, token = await connected("9852000011")
    a = (await c.post("/api/money", json={"customer": "Mehta Traders", "phone": "98511 00099", "amount": 5000, "due_date": "2026-11-20"})).json()
    b = (await c.post("/api/money", json={"customer": "Mehta Traders", "phone": "98511 00099", "amount": 5000, "due_date": "2026-11-25"})).json()
    rs = await asyncio.gather(deliver(client, token, captured("pay_Q1", 500000, contact="+919851100099")),
                              deliver(client, token, captured("pay_Q2", 500000, contact="+919851100099")))
    out = [r.json() for r in rs]
    assert sorted(o["result"] for o in out) == ["due_paid", "due_paid"] and {o["due_id"] for o in out} == {a["id"], b["id"]}
    for d in (a, b):
        assert (await db().dues.find_one({"_id": d["id"]}))["status"] == "paid"
    # a third, with nothing left to settle, becomes its own invoice
    assert (await deliver(client, token, captured("pay_Q3", 500000, contact="+919851100099"))).json()["result"] == "invoiced"


async def test_refunds_act_on_processed_only_and_never_for_an_unknown_payment(client):
    c, uid, token = await connected("9852000012")
    await deliver(client, token, captured("pay_R2", 118000))
    created = refund("rfnd_9", "pay_R2", 59000)
    created["event"] = "refund.created"
    assert (await deliver(client, token, created)).json()["ignored"] == "refund.created"
    failed = dict(created, event="refund.failed")
    assert (await deliver(client, token, failed)).json()["ignored"] == "refund.failed"
    assert not await db().quotes.count_documents({"ws": uid, "kind": "credit_note"})
    assert (await deliver(client, token, refund("rfnd_9", "pay_R2", 59000))).json()["result"] == "credit_note"
    r = (await deliver(client, token, refund("rfnd_x", "pay_never_seen", 20000))).json()
    assert r["result"] == "refund_unknown_payment"
    assert await db().quotes.count_documents({"ws": uid, "kind": "credit_note"}) == 1
    assert await db().notices.count_documents({"user_id": uid, "text": {"$regex": "no invoice for that payment"}}) == 1


async def test_gst_without_an_identity_states_and_exact_halves(client):
    from app.routers.gateway import canonical_state, split_inclusive
    # (a) no business identity in the Knowledge Hub: not assumed GST-registered
    c, uid = await owner_on("9852000013")
    r = await c.put("/api/connections/razorpay", json={"values": {"key_id": "rzp_test_abc123", "webhook_secret": SECRET}})
    token = r.json()["webhook"].rsplit("/", 1)[1]
    await deliver(client, token, captured("pay_N1", 118000))
    inv = await db().quotes.find_one({"ws": uid, "gateway_payment_id": "pay_N1"})
    assert inv["gst_mode"] == "none" and inv["totals"]["gst"] == 0 and inv["totals"]["subtotal"] == 1180.0 and "Knowledge Hub" in inv["gst_note"]
    direct = (await c.post("/api/invoices", json={"customer": {"name": "Walk-in"}, "items": [{"name": "Hob", "qty": 1, "rate": 9000}]})).json()
    assert direct["gst_mode"] == "none" and direct["gst_note"]
    s = (await c.get("/api/invoices/settings")).json()
    assert len(s["states"]) == 36 and s["identity"] is False
    # (c) one state name, however it's written
    for v in ("27", "MH", "maharashtra", "27ABCDE1234F1Z5", " Maharashtra "):
        assert canonical_state(v) == "Maharashtra", v
    assert canonical_state("KA") == "Karnataka" and canonical_state("Orissa") == "Odisha" and canonical_state("zz") == ""
    c2, uid2, token2 = await connected("9852000014")
    for pid, st in (("pay_S1", "MH"), ("pay_S2", "27"), ("pay_S3", "KA")):
        await deliver(client, token2, captured(pid, 118000, {"state": st}))
    modes = {q["gateway_payment_id"]: q["gst_mode"] async for q in db().quotes.find({"ws": uid2, "source": "gateway"})}
    assert modes == {"pay_S1": "intra", "pay_S2": "intra", "pay_S3": "inter"}
    # (d) CGST and SGST exactly half; taxable + tax == the amount paid, to the paisa
    for amount in (100.0, 99.99, 1.0, 1234.57, 118.0, 333.33):
        t = split_inclusive(amount, 18, "intra")
        assert t["cgst"] == t["sgst"] and round(t["cgst"] * 2, 2) == t["gst"] and round(t["subtotal"] + t["gst"], 2) == amount, amount


async def test_the_last_refund_takes_whatever_gst_is_left(client):
    c, uid, token = await connected("9852000015")
    await deliver(client, token, captured("pay_L1", 118000))          # GST 180
    for i, paise in enumerate((39333, 39333, 39334)):
        await deliver(client, token, refund(f"rfnd_L{i}", "pay_L1", paise))
    notes = [q async for q in db().quotes.find({"ws": uid, "kind": "credit_note"}).sort("number", 1)]
    assert len(notes) == 3
    assert round(sum(n["totals"]["gst"] for n in notes), 2) == 180.0 and round(sum(n["totals"]["total"] for n in notes), 2) == 1180.0
    for n in notes:
        t = n["totals"]
        assert round(t["subtotal"] + t["gst"], 2) == t["total"] and round(t["cgst"] + t["sgst"], 2) == t["gst"]


async def test_billing_entry_keeps_big_rates(client):
    c, uid, _ = await connected("9852000016")
    d = (await c.post("/api/invoices/draft", json={"text": "Sold 1 machine to Rao Industries, ₹12,50,000, 18% GST, due in 45 days"})).json()
    assert d["items"][0]["rate"] == 1250000.0 and d["missing_rates"] == 0 and d["due_in_days"] == 45


async def test_a_stuck_claim_is_taken_over_and_a_lost_plan_holds_the_payment(client):
    from datetime import timedelta
    from app.db import now
    c, uid, token = await connected("9852000017")
    key = f"pay:{uid}:captured:pay_K9"
    await db().gateway_claims.insert_one({"_id": key, "status": "processing", "started_at": now()})
    assert (await deliver(client, token, captured("pay_K9", 118000))).status_code == 409       # someone is on it right now
    await db().gateway_claims.update_one({"_id": key}, {"$set": {"started_at": now() - timedelta(minutes=10)}})
    r = await deliver(client, token, captured("pay_K9", 118000))                                # that worker died: taken over
    assert r.json()["result"] == "invoiced" and (await db().gateway_claims.find_one({"_id": key}))["status"] == "done"
    assert (await deliver(client, token, captured("pay_K9", 118000))).json().get("duplicate")
    admin = await new_owner(ADMIN)
    await admin.patch(f"/api/admin/users/{uid}", json={"plan": "free"})
    for _ in range(2):   # Razorpay may deliver it again: still one record, one notice
        r = await deliver(client, token, captured("pay_K10", 59000))
        assert r.status_code == 200 and r.json()["held"] == "plan"
    held = await db().payment_events.find_one({"ws": uid, "payment_id": "pay_K10"})
    assert held["result"] == "held" and held["amount"] == 590.0
    assert await db().notices.count_documents({"user_id": uid, "text": {"$regex": "wasn't invoiced"}}) == 1
    assert not await db().quotes.count_documents({"ws": uid, "gateway_payment_id": "pay_K10"})


async def test_gateway_invoice_is_listed_for_information_only(client):
    from app.agents import catalog
    spec = catalog.AGENTS["gateway_invoice"]
    assert spec.info_only and spec.group == "inbox" and spec.feature == "gateway_invoicing"
    c, uid, _ = await connected("9852000018", "growth")
    body = (await c.get("/api/agents")).json()
    assert "gateway_invoice" not in [a["key"] for a in body["agents"]]
