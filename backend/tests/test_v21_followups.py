"""v2.1 follow-ups (20 Oct): the menu, the Growth WhatsApp chat around the clock, "Update my hub", Razorpay payers on the
Customers list, inquiry emails over 150 KB through SES's S3 action, and the demo's website proposals.
Every name, number and address here is made up."""
import json
from datetime import datetime
from email.message import EmailMessage

import httpx

from app import chat_ai, knowledge, mailer
from app.config import settings
from app.db import IST, db
from app.routers import inquiries
from tests.conftest import PROFILE, go_live, new_owner
from tests.test_agents import connect_demo_whatsapp
from tests.test_v21_p0 import owner_on, used_runs
from tests.test_v21_p1_inquiries import DOMAIN, SECRET, address_of, inbound  # noqa: F401  (inbound is a fixture)
from tests.test_v21_p1_invoicing import captured, connected, deliver
from tests.test_v2_growth import meta_message

ADMIN = "9999900000"


# ───────────────────────── 1. the menu ─────────────────────────
async def test_inquiries_and_gateway_invoicing_are_on_for_members(client):
    c, uid, _ = await owner_on("9853000001", "lite", live=False)
    me = (await c.get("/api/me")).json()
    assert me["features"]["inquiry_desk"] and me["features"]["gateway_invoicing"] and me["features"]["connections"]
    systems = {i["key"]: i for g in me["menu"] if g["group"] == "Systems" for i in g["items"]}
    assert systems["inquiry_desk"]["path"] == "/inquiries" and not systems["inquiry_desk"].get("locked")
    assert not systems["connections"].get("locked")                      # Razorpay is connected from Connections
    assert (await c.get("/api/inquiries")).status_code == 200 and (await c.get("/api/gateway")).status_code == 200
    # the gateway's invoices are listed under Quotations & invoices, which is a Running the Business page until D1 moves it
    assert systems["quotations"].get("locked")


# ───────────────────────── 2. Growth: the WhatsApp chat answers on its own, any hour ─────────────────────────
async def test_growth_whatsapp_chat_replies_on_its_own_at_any_hour_and_only_proposals_wait(client, monkeypatch):
    from tests.test_v2_growth import owner_on as growth_owner
    c, uid, site = await growth_owner("9853000002")
    await connect_demo_whatsapp(uid)
    token = (await db().connections.find_one({"_id": uid}))["whatsapp"]["webhook_token"]
    assert (await c.put("/api/inquiries/settings", json={"auto_web": True})).json()["auto_web"] is True
    for hour in (2, 14, 23):                     # 2:30 at night, the afternoon, late evening: no hours anywhere in the chat
        monkeypatch.setattr(chat_ai, "now", lambda h=hour: datetime(2026, 10, 20, h, 30, tzinfo=IST))
        r = await client.post(f"/api/hooks/whatsapp/{token}", json=meta_message(f"91983400{hour:04d}", "Hi, price of a modular kitchen?", f"m{hour}"))
        assert r.json() == {"ok": True, "handled": 1}
    assert await db().outbox.count_documents({"kind": "chat", "ws": uid}) == 3          # three customers, three replies, no one pressed anything
    assert not await db().approvals.count_documents({"ws": uid, "kind": {"$in": ["chat", "wa_reply"]}})
    # a website inquiry: the instant reply goes on its own; the written proposal waits for Send
    await client.post(f"/s/{site['slug']}/inquiry", json={"name": "Rohit", "phone": "98340 00999", "message": "Need a kitchen quote"})
    await inquiries.settle()
    lead = await db().leads.find_one({"owner_id": uid, "source": "website"})
    assert lead["instant_reply"]
    assert lead["proposal"]["approval_id"] and (await db().approvals.find_one({"_id": lead["proposal"]["approval_id"]}))["status"] == "pending"


# ───────────────────────── 3. Update my hub ─────────────────────────
async def test_a_save_spends_no_ai_runs_and_update_my_hub_shows_the_cost_first(client):
    c, uid, site = await owner_on("9853000003", "lite")
    before = (await c.get("/api/site")).json()["content"]
    runs = await used_runs(uid)
    assert (await c.put("/api/business", json={**PROFILE, "industry": "Modular kitchens and wardrobes"})).status_code == 200
    await knowledge.settle()
    assert await used_runs(uid) == runs and (await c.get("/api/site")).json()["content"] == before        # nothing rewritten on save
    [u] = [x for x in (await c.get("/api/knowledge")).json()["updates"] if x["key"] == "website"]
    assert u["reason"] == "ready" and u["runs"] == 1
    plan = (await c.get("/api/knowledge/update")).json()
    assert plan["runs"] == 1 and [i["key"] for i in plan["items"]] == ["website"] and plan["runs_left"] > 0
    # a contact change still applies at once, with no run (the page reads the number live)
    assert (await c.put("/api/business", json={**PROFILE, "industry": "Modular kitchens and wardrobes", "whatsapp": "98200 33333"})).status_code == 200
    await knowledge.settle()
    assert await used_runs(uid) == runs
    done = (await c.post("/api/knowledge/update")).json()
    assert done["result"] == {"website": "refreshed"} and done["plan"]["runs"] == 0 and not done["updates"]
    assert await used_runs(uid) == runs + 1 and (await c.get("/api/site")).json()["content"] != before


async def test_update_my_hub_refreshes_a_brand_kit_item_by_item_once(client):
    c, uid, _ = await owner_on("9853000004", "lite", live=False)
    await c.post("/api/brand-kit/generate")
    await c.put("/api/brand-kit/messages", json={"path": "marketing.tagline", "value": "MY TAGLINE"})
    await c.put("/api/business", json={**PROFILE, "industry": "Modular kitchens and wardrobes"})
    await knowledge.settle()
    plan = (await c.get("/api/knowledge/update")).json()
    assert [i["key"] for i in plan["items"]] == ["brand_kit"] and plan["runs"] == 1
    runs = await used_runs(uid)
    assert (await c.post("/api/knowledge/update")).json()["result"] == {"brand_kit": "refreshed_kept_edits"}
    assert (await c.get("/api/brand-kit")).json()["messages"]["marketing"]["tagline"] == "MY TAGLINE" and await used_runs(uid) == runs + 1
    assert (await c.get("/api/knowledge/update")).json()["runs"] == 0          # done: pressing again costs nothing
    [u] = [x for x in (await c.get("/api/knowledge")).json()["updates"] if x["key"] == "brand_kit"]
    assert u["reason"] == "edited"                                              # their own tagline still waits for their choice


# ───────────────────────── 6. Razorpay payers become Customers ─────────────────────────
async def test_razorpay_payers_are_matched_or_added_to_customers_once_per_payment(client):
    c, uid, token = await connected("9853000005")
    await db().customers.insert_one({"_id": "c-known", "ws": uid, "name": "Neha Joshi", "phone": "", "email": "neha@family.example", "tier": "",
                                     "total_value": 5000.0, "purchases": 1, "source": "added"})
    await deliver(client, token, captured("pay_C1", 118000, {"name": "Rohit Shah"}, email="rohit@shah.example", contact="+919851130001"))
    await deliver(client, token, captured("pay_C1", 118000, {"name": "Rohit Shah"}, email="rohit@shah.example", contact="+919851130001"))   # again
    await deliver(client, token, captured("pay_C2", 59000, email="other@shah.example", contact="98511 30001"))        # same phone, typed differently
    await deliver(client, token, captured("pay_C3", 236000, email="neha@family.example", contact=""))                  # known by email
    await deliver(client, token, captured("pay_C4", 10000, email="", contact=""))                                      # nothing to match on
    rohit = await db().customers.find_one({"ws": uid, "phone": "+919851130001"})
    assert rohit["name"] == "Rohit Shah" and rohit["source"] == "razorpay" and rohit["purchases"] == 2 and rohit["total_value"] == 1770.0
    assert rohit["email"] == "rohit@shah.example" and sorted(rohit["payment_ids"]) == ["pay_C1", "pay_C2"]
    neha = await db().customers.find_one({"_id": "c-known"})
    assert neha["purchases"] == 2 and neha["total_value"] == 7360.0 and neha["name"] == "Neha Joshi"
    assert await db().customers.count_documents({"ws": uid}) == 2
    # a payment that settles a Money owed entry: the payer is that entry's customer
    await c.post("/api/money", json={"customer": "Mehta Traders", "phone": "98511 40002", "amount": 4720, "due_date": "2026-11-30"})
    r = await deliver(client, token, captured("pay_C5", 472000, email="", contact="+919851140002"))
    assert r.json()["result"] == "due_paid"
    mehta = await db().customers.find_one({"ws": uid, "phone": "+919851140002"})
    assert mehta["name"] == "Mehta Traders" and mehta["purchases"] == 1


# ───────────────────────── 5. inquiry emails over 150 KB, through SES's S3 action ─────────────────────────
BUCKET = "hub-inbound-mail"


def big_mail(to: str) -> bytes:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = "Neha Joshi <neha@family.example>", to, "Kitchen drawing attached"
    m["Message-ID"] = "<s3-mail-1@mail.example>"
    m.set_content("Please quote for the kitchen in the attached drawing. Call me on 98330 11111.")
    m.add_attachment(b"%PDF-1.4\n" + b"0" * 400_000, maintype="application", subtype="pdf", filename="kitchen-drawing.pdf")
    m.add_attachment(b"MZ\x90\x00" + b"1" * 1000, maintype="application", subtype="octet-stream", filename="setup.exe")   # never kept
    return m.as_bytes()


def s3_note(to: str, bucket: str = BUCKET, key: str = "inbound/s3mail1") -> dict:
    note = {"notificationType": "Received", "mail": {"messageId": "s3mail1"},
            "receipt": {"recipients": [to], "spamVerdict": {"status": "PASS"}, "virusVerdict": {"status": "PASS"}, "dmarcVerdict": {"status": "PASS"},
                        "action": {"type": "S3", "topicArn": "arn:aws:sns:ap-south-1:1:in", "bucketName": bucket, "objectKeyPrefix": "inbound/",
                                   "objectKey": key}}}
    return {"Type": "Notification", "Message": json.dumps(note), "TopicArn": "arn:aws:sns:ap-south-1:1:in"}


async def test_an_inquiry_email_over_150kb_is_read_from_s3_with_its_pdf_kept(client, inbound, monkeypatch):  # noqa: F811
    c, uid, _ = await owner_on("9853000006", "lite")
    addr = await address_of(c)
    raw = big_mail(addr)
    assert len(raw) > 150_000
    seen = []

    def s3(req: httpx.Request):
        seen.append(req)
        if req.url.host != f"{BUCKET}.s3.ap-south-1.amazonaws.com" or req.url.path != "/inbound/s3mail1":
            return httpx.Response(404)
        return httpx.Response(200, content=raw, headers={"content-length": str(len(raw))})
    real = httpx.AsyncClient
    monkeypatch.setattr(mailer, "http_client", lambda: real(transport=httpx.MockTransport(s3), timeout=5))
    old = (settings.email_inbound_bucket, settings.aws_access_key_id, settings.aws_secret_access_key, settings.aws_region)
    for k, v in (("email_inbound_bucket", BUCKET), ("aws_access_key_id", "AKIATEST"), ("aws_secret_access_key", "test-secret"),
                 ("aws_region", "ap-south-1")):
        object.__setattr__(settings, k, v)
    try:
        wrong = await client.post(f"/api/hooks/email-in/{SECRET}", json=s3_note(addr, bucket="someone-elses-bucket"))
        assert wrong.json() == {"ok": True, "handled": ["wrong_bucket"]} and not seen          # another bucket is never read
        r = await client.post(f"/api/hooks/email-in/{SECRET}", json=s3_note(addr))
        assert r.json() == {"ok": True, "handled": ["lead"]}, r.text
        auth = seen[-1].headers["authorization"]
        assert "/ap-south-1/s3/aws4_request" in auth and seen[-1].headers["x-amz-content-sha256"]
        missing = await client.post(f"/api/hooks/email-in/{SECRET}", json=s3_note(addr, key="inbound/not-there"))
        assert missing.status_code == 502                                         # not readable yet: SNS will deliver it again
    finally:
        for k, v in zip(("email_inbound_bucket", "aws_access_key_id", "aws_secret_access_key", "aws_region"), old):
            object.__setattr__(settings, k, v)
    await inquiries.settle()
    lead = await db().leads.find_one({"owner_id": uid, "source": "email"})
    assert lead["email"] == "neha@family.example" and "attached drawing" in lead["message"]
    [att] = lead["attachments"]
    assert att["name"] == "kitchen-drawing.pdf" and att["type"] == "application/pdf" and att["size"] > 400_000
    item = (await c.get("/api/inquiries")).json()["items"][0]
    assert item["attachments"][0]["id"] == att["id"]
    f = await c.get(f"/api/inquiries/{lead['_id']}/files/{att['id']}")
    assert f.status_code == 200 and f.content.startswith(b"%PDF") and "attachment" in f.headers["content-disposition"]
    other = await new_owner("9853000007")
    assert (await other.get(f"/api/inquiries/{lead['_id']}/files/{att['id']}")).status_code in (403, 404)


# ───────────────────────── 4. the demo: website inquiries get a proposal ─────────────────────────
def test_the_demo_seed_switches_website_proposals_on():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / "seed_demo.py").read_text()
    assert "json={\"auto_web\": True}" in src and "\"source\": \"website\", \"status\": \"new\"" in src
