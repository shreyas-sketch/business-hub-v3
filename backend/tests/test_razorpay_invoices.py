"""Razorpay invoices (app/razorpay_invoices.py) against a stand-in Razorpay: every hub invoice gets one Razorpay invoice —
issued with a pay link while unpaid, a record-only draft when already paid, cancelled when paid another way — and the
account's Razorpay invoices are read into the hub. Every business, customer and key here is invented."""
import json
from datetime import timedelta

import httpx
import pytest

from app import razorpay_invoices as rzp
from app.db import db, now
from tests.conftest import new_owner
from tests.test_v21_p1_invoicing import captured, connected, deliver

KEY_ID, KEY_SECRET = "rzp_test_abc123", "ks_test"


class FakeRazorpay:
    """Razorpay's invoice endpoints, the way the API answers them (receipt unique per account)."""

    def __init__(self):
        self.invoices: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []
        self.fail_next = 0           # answer 503 to the next n calls (after doing the work, like a lost answer)
        self.refuse = False          # 401 for everything

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path))
        if self.refuse:
            return httpx.Response(401, json={"error": {"code": "BAD_REQUEST_ERROR", "description": "Authentication failed"}})
        assert request.headers["authorization"] == httpx.BasicAuth(KEY_ID, KEY_SECRET)._auth_header
        out = self._answer(request, path)
        if self.fail_next:
            self.fail_next -= 1
            return httpx.Response(503, json={"error": {"code": "SERVER_ERROR", "description": "try later"}})
        return out

    def _answer(self, request, path):
        if request.method == "POST" and path == "/v1/invoices":
            body = json.loads(request.content)
            if any(x["receipt"] == body["receipt"] for x in self.invoices.values()):
                return httpx.Response(400, json={"error": {"code": "BAD_REQUEST_ERROR", "description": "receipt already exists"}})
            iid = f"inv_{len(self.invoices) + 1:04d}"
            draft = body.get("draft") == "1"
            inv = {"id": iid, "entity": "invoice", "type": "invoice", "receipt": body["receipt"], "status": "draft" if draft else "issued",
                   "amount": sum(li["amount"] * li["quantity"] for li in body["line_items"]), "amount_paid": 0, "currency": "INR",
                   "customer_details": {"name": body["customer"].get("name"), "contact": body["customer"].get("contact"),
                                        "email": body["customer"].get("email")},
                   "short_url": None if draft else f"https://rzp.io/i/{iid}", "notes": body.get("notes") or {}, "body": body,
                   "created_at": 1761000000 + len(self.invoices)}
            inv["amount_due"] = inv["amount"]
            self.invoices[iid] = inv
            return httpx.Response(200, json=inv)
        if request.method == "GET" and path == "/v1/invoices":
            q = request.url.params
            items = sorted(self.invoices.values(), key=lambda x: -x["created_at"])
            if q.get("receipt"):
                items = [x for x in items if x["receipt"] == q["receipt"]]
            skip, count = int(q.get("skip", 0)), int(q.get("count", 10))
            page = items[skip:skip + count]
            return httpx.Response(200, json={"entity": "collection", "count": len(page), "items": page})
        if request.method == "POST" and path.endswith("/cancel"):
            iid = path.split("/")[-2]
            if iid not in self.invoices or self.invoices[iid]["status"] != "issued":
                return httpx.Response(400, json={"error": {"code": "BAD_REQUEST_ERROR", "description": "can't be cancelled"}})
            self.invoices[iid]["status"] = "cancelled"
            return httpx.Response(200, json=self.invoices[iid])
        return httpx.Response(404, json={"error": {"code": "NOT_FOUND", "description": "not found"}})

    def made(self, mode: str | None = None) -> list[dict]:
        return [x for x in self.invoices.values() if mode is None or (x["status"] == "draft") == (mode == "record")]


@pytest.fixture
def fake(monkeypatch):
    f = FakeRazorpay()
    monkeypatch.setattr(rzp, "_transport", lambda: httpx.MockTransport(f.handler))

    async def instant(_s):
        return None
    monkeypatch.setattr(rzp, "_pause", instant)
    return f


async def unpaid_invoice(c, name="Rohit Shah", phone="98511 30001", email="rohit@shah.example", rate=1000, qty=3) -> dict:
    r = await c.post("/api/invoices", json={"customer": {"name": name, "phone": phone, "email": email},
                                            "items": [{"name": "Steel brackets", "qty": qty, "unit": "nos", "rate": rate, "gst": 18},
                                                      {"name": "Fitting", "qty": 1, "unit": "job", "rate": 333.33, "gst": 18}],
                                            "due_in_days": 7})
    assert r.status_code == 200, r.text
    return r.json()


async def invoice(c, inv_id: str) -> dict:
    return next(x for x in (await c.get("/api/quotes", params={"kind": "invoice"})).json() if x["id"] == inv_id)


def paid_through(pid: str, paise: int, invoice_id: str) -> dict:
    p = captured(pid, paise)
    p["payload"]["payment"]["entity"]["invoice_id"] = invoice_id
    return p


async def test_an_unpaid_invoice_gets_an_issued_razorpay_invoice_with_its_pay_link(client, fake):
    c, uid, token = await connected("9851140001")
    inv = await unpaid_invoice(c)
    assert inv["rzp"]["state"] == "pending" and inv["rzp"]["mode"] == "issued"
    assert await rzp.drain(uid) == 1
    [r] = fake.made()
    body = r["body"]
    # the same total to the paisa, the hub's number as the receipt, and Razorpay sends nothing itself
    assert r["amount"] == round(inv["totals"]["total"] * 100) and body["receipt"] == inv["number"]
    assert body["sms_notify"] == 0 and body["email_notify"] == 0 and "draft" not in body
    assert body["customer"] == {"name": "Rohit Shah", "contact": "+919851130001", "email": "rohit@shah.example"}
    assert body["notes"]["invoice_no"] == inv["number"] and body["notes"]["hub_invoice_id"] == inv["id"]
    v = await invoice(c, inv["id"])
    assert v["rzp"]["status"] == "issued" and v["rzp"]["pay_url"] == f"https://rzp.io/i/{r['id']}" and v["rzp"]["label"] == "Awaiting payment"
    assert f"Pay online: https://rzp.io/i/{r['id']}" in v["share_text"]
    assert rzp.pay_url(await db().quotes.find_one({"_id": inv["id"]})).endswith(r["id"])       # also printed on the PDF
    assert (await c.get(f"/api/invoices/{inv['id']}/pdf")).status_code == 200
    assert await rzp.drain(uid) == 0 and len(fake.made()) == 1                                   # once only


async def test_paid_through_its_razorpay_invoice_settles_it_once_and_makes_no_new_invoice(client, fake):
    c, uid, token = await connected("9851140002")
    inv = await unpaid_invoice(c)
    await rzp.drain(uid)
    [r] = fake.made()
    paise = r["amount"]
    for _ in range(2):                                                            # delivered twice
        res = await deliver(client, token, paid_through("pay_inv001", paise, r["id"]))
        assert res.status_code == 200, res.text
    # invoice.paid carries the same payment: recorded once
    ev = {"entity": "event", "event": "invoice.paid", "id": "evt_invpaid",
          "payload": {"invoice": {"entity": {"id": r["id"], "status": "paid"}},
                      "payment": {"entity": {"id": "pay_inv001", "amount": paise, "currency": "INR", "invoice_id": r["id"]}}}}
    assert (await deliver(client, token, ev)).status_code == 200
    v = await invoice(c, inv["id"])
    assert v["status"] == "paid" and v["rzp"]["status"] == "paid" and v["rzp"]["pay_url"] is None and "Pay online" not in v["share_text"]
    assert (await db().dues.find_one({"_id": inv["due_id"]}))["status"] == "paid"
    assert await db().quotes.count_documents({"ws": uid, "kind": "invoice"}) == 1                # no second invoice
    assert ("POST", f"/v1/invoices/{r['id']}/cancel") not in fake.calls


async def test_money_that_came_another_way_becomes_a_record_only_draft(client, fake):
    c, uid, token = await connected("9851140003")
    res = await deliver(client, token, captured("pay_link001", 118000))          # a payment link or QR: no Razorpay invoice
    assert res.json()["result"] == "invoiced"
    await rzp.drain(uid)
    [r] = fake.made()
    assert r["status"] == "draft" and r["short_url"] is None and r["amount"] == 118000
    assert r["body"]["notes"]["record_only"] == "yes" and r["body"]["notes"]["payment_id"] == "pay_link001"
    assert "Not a request to pay" in r["body"]["description"]
    hub = await db().quotes.find_one({"ws": uid, "gateway_payment_id": "pay_link001"})
    assert hub["rzp"]["mode"] == "record" and hub["rzp"]["status"] == "draft" and rzp.pay_url(hub) is None
    # a direct invoice marked "paid already" (cash) is a record too
    r2 = await c.post("/api/invoices", json={"customer": {"name": "Cash Customer"}, "items": [{"name": "Repair", "qty": 1, "unit": "job",
                                                                                               "rate": 500, "gst": 18}], "paid_already": True})
    await rzp.drain(uid)
    assert r2.json()["rzp"]["mode"] == "record" and len(fake.made("record")) == 2


async def test_paid_another_way_cancels_its_issued_razorpay_invoice_and_a_second_payment_is_flagged(client, fake):
    c, uid, token = await connected("9851140004")
    inv = await unpaid_invoice(c)
    await rzp.drain(uid)
    [r] = fake.made()
    assert (await c.patch(f"/api/money/{inv['due_id']}", json={"status": "paid"})).status_code == 200    # cash
    await rzp.drain(uid)
    assert fake.invoices[r["id"]]["status"] == "cancelled"
    v = await invoice(c, inv["id"])
    assert v["rzp"]["status"] == "cancelled" and v["rzp"]["pay_url"] is None
    # had the customer paid the link anyway, the owner is told it was paid twice
    res = await deliver(client, token, paid_through("pay_twice01", r["amount"], r["id"]))
    assert res.json()["result"] == "paid_twice"
    assert await db().notices.find_one({"user_id": uid, "text": {"$regex": "paid twice"}})
    assert await db().quotes.count_documents({"ws": uid, "kind": "invoice"}) == 1


async def test_a_lost_answer_is_retried_and_never_makes_a_second_razorpay_invoice(client, fake):
    c, uid, token = await connected("9851140005")
    inv = await unpaid_invoice(c)
    fake.fail_next = 3                       # Razorpay made it, but every answer of the first attempt was lost
    await rzp.drain(uid)
    q = await db().quotes.find_one({"_id": inv["id"]})
    assert q["rzp"]["state"] == "pending" and q["rzp"]["attempts"] == 1 and q["rzp"]["next_at"] > now()
    await db().quotes.update_one({"_id": inv["id"]}, {"$set": {"rzp.next_at": now() - timedelta(seconds=1)}})
    await rzp.drain(uid)                     # the receipt exists → that invoice is found and used
    assert len(fake.made()) == 1
    q = await db().quotes.find_one({"_id": inv["id"]})
    assert q["rzp"]["state"] == "done" and q["rzp"]["invoice_id"] == next(iter(fake.invoices))


async def test_refused_keys_fail_once_tell_the_owner_and_can_be_tried_again(client, fake):
    c, uid, token = await connected("9851140006")
    inv = await unpaid_invoice(c)
    fake.refuse = True
    await rzp.drain(uid)
    v = await invoice(c, inv["id"])
    assert v["rzp"]["state"] == "failed" and "key" in v["rzp"]["error"] and v["rzp"]["label"] == "Couldn't be added"
    assert await db().notices.count_documents({"user_id": uid, "text": {"$regex": "Razorpay"}}) == 1
    fake.refuse = False
    r = await c.post(f"/api/invoices/{inv['id']}/razorpay")
    assert r.status_code == 200 and r.json()["rzp"]["status"] == "issued" and len(fake.made()) == 1


async def test_an_invoice_made_in_razorpay_itself_is_not_recorded_again(client, fake):
    c, uid, token = await connected("9851140007")
    res = await deliver(client, token, paid_through("pay_dash001", 59000, "inv_madeindashboard"))
    assert res.json()["result"] == "invoiced"
    hub = await db().quotes.find_one({"ws": uid, "gateway_payment_id": "pay_dash001"})
    assert hub["rzp"]["mode"] == "razorpay" and hub["rzp"]["invoice_id"] == "inv_madeindashboard"
    await rzp.drain(uid)
    assert fake.made() == []                                                      # no record draft on top


async def test_razorpay_invoices_are_read_into_the_hub_for_that_business_only(client, fake):
    c, uid, token = await connected("9851140008")
    inv = await unpaid_invoice(c)
    await rzp.drain(uid)
    fake.invoices["inv_dash"] = {"id": "inv_dash", "type": "invoice", "receipt": "GST/2026/77", "status": "paid", "amount": 250000,
                                 "amount_paid": 250000, "amount_due": 0, "currency": "INR", "short_url": "https://rzp.io/i/inv_dash",
                                 "customer_details": {"name": "Walk-in Traders", "contact": "+919800000077"}, "notes": {},
                                 "created_at": 1761900000, "paid_at": 1761900500}
    d = (await c.post("/api/gateway/razorpay-invoices/sync")).json()
    assert [x["id"] for x in d["items"]] == ["inv_dash", "inv_0001"] and d["last_at"]
    mine = next(x for x in d["items"] if x["id"] == "inv_0001")
    assert mine["hub_number"] == inv["number"] and mine["from_hub"] and mine["label"] == "Awaiting payment"
    dash = d["items"][0]
    assert dash["hub_number"] is None and dash["amount"] == 2500 and dash["label"] == "Paid" and dash["short_url"] is None
    other, _, _ = await connected("9851140009")
    assert (await other.get("/api/gateway/razorpay-invoices")).json()["items"] == []
    # the clock reads each account every 15 minutes, not every minute
    assert await rzp.tick(now()) == 1            # only the other business was due (this one just synced)
    assert await rzp.tick(now()) == 0
    assert await rzp.tick(now() + timedelta(minutes=rzp.SYNC_MINUTES + 1)) == 2


async def test_without_both_keys_nothing_goes_to_razorpay(client, fake):
    from tests.test_v21_p1_invoicing import owner_on
    c, uid = await owner_on("9851140010")
    await c.put("/api/connections/razorpay", json={"values": {"key_id": KEY_ID, "webhook_secret": "whsec_x"}})   # no key secret
    inv = await unpaid_invoice(c)
    assert inv["rzp"] is None and await rzp.drain(uid) == 0 and fake.calls == []


def test_line_items_add_up_to_the_total_to_the_paisa():
    inv = {"_id": "x", "number": "INV-0009", "gst_mode": "intra", "totals": {"total": 3933.33},
           "items": [{"name": "A", "qty": 3, "rate": 1000, "gst": 18, "amount": 3000}, {"name": "B", "qty": 1, "rate": 333.33, "gst": 18, "amount": 333.33}]}
    lines = rzp.line_items(inv)
    assert sum(x["amount"] for x in lines) == 393333 and all(x["quantity"] == 1 and x["amount"] > 0 for x in lines)
    assert rzp.payload(inv, "Kaveri", "record")["draft"] == "1"


async def test_an_invoice_from_a_quotation_gets_its_razorpay_invoice_too(client, fake):
    c, uid, token = await connected("9851140011")
    q = (await c.post("/api/quotes", json={"customer": {"name": "Rohit Shah", "phone": "98511 30001"},
                                           "items": [{"name": "Gate", "qty": 1, "unit": "nos", "rate": 25000, "gst": 18}]})).json()
    inv = (await c.post(f"/api/quotes/{q['id']}/invoice", json={"due_in_days": 7})).json()
    assert inv["rzp"]["mode"] == "issued"
    await rzp.drain(uid)
    [r] = fake.made()
    assert r["receipt"] == inv["number"] and r["amount"] == round(inv["totals"]["total"] * 100) and r["status"] == "issued"
