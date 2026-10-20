"""v2.1 P1 — Inquiries + automatic proposal (3.6) and replies to the hub's emails (3.8.4). Every name and address is invented."""
import json
from email.message import EmailMessage

import pytest

from app import followups, mailer
from app.ai import proposal_tasks
from app.config import settings
from app.db import db
from app.routers import inquiries
from tests.conftest import PROFILE
from tests.test_v21_p0 import _due, ist, owner_doc, owner_on, used_runs

SECRET = "inbound-secret-path-000001"
DOMAIN = "in.example.in"


@pytest.fixture
def inbound(monkeypatch):
    old = (settings.inbound_email_domain, settings.email_inbound_secret)
    object.__setattr__(settings, "inbound_email_domain", DOMAIN)
    object.__setattr__(settings, "email_inbound_secret", SECRET)

    async def ok(msg):
        return True
    monkeypatch.setattr(mailer, "sns_verified", ok)
    yield
    object.__setattr__(settings, "inbound_email_domain", old[0])
    object.__setattr__(settings, "email_inbound_secret", old[1])


def raw_mail(frm: str, to: str, subject: str, body: str, **headers) -> str:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = frm, to, subject
    m["Message-ID"] = headers.pop("message_id", f"<{abs(hash((frm, subject, body)))}@mail.example>")
    for k, v in headers.items():
        m[k.replace("_", "-")] = v
    m.set_content(body)
    return m.as_string()


def sns(to: str, raw: str, spam: str = "PASS", dmarc: str = "PASS") -> dict:
    note = {"notificationType": "Received", "receipt": {"recipients": [to], "spamVerdict": {"status": spam}, "virusVerdict": {"status": "PASS"},
                                                        "dmarcVerdict": {"status": dmarc}},
            "mail": {"messageId": "x"}, "content": raw}
    return {"Type": "Notification", "Message": json.dumps(note), "TopicArn": "arn:aws:sns:ap-south-1:1:in"}


async def address_of(c) -> str:
    s = (await c.get("/api/inquiries/settings")).json()
    assert s["address"] and s["address"].endswith("@" + DOMAIN)
    return s["address"]


async def test_an_email_inquiry_becomes_a_lead_and_gets_a_proposal_by_email(client, inbound):
    c, uid, _ = await owner_on("9852000001", "lite")
    addr = await address_of(c)
    assert (await c.put("/api/inquiries/settings", json={"auto_email": True})).json()["auto_email"] is True
    raw = raw_mail("Neha Joshi <neha@family.example>", addr, "Modular kitchen for our 2BHK",
                   "Hi, we need a modular kitchen for a new 2BHK in Thane. Please call me on 98330 11111.")
    r = await client.post(f"/api/hooks/email-in/{SECRET}", json=sns(addr, raw))
    assert r.json() == {"ok": True, "handled": ["lead"]}
    again = await client.post(f"/api/hooks/email-in/{SECRET}", json=sns(addr, raw))      # SNS delivered it twice
    assert again.json()["handled"] == ["duplicate"]
    await inquiries.settle()
    [lead] = [x async for x in db().leads.find({"owner_id": uid})]
    assert lead["source"] == "email" and lead["email"] == "neha@family.example" and lead["phone"] == "+919833011111"
    assert lead["stage"] == "identification" and lead["proposal"]["status"] == "sent"
    [mail] = [m async for m in db().outbox.find({"channel": "email"})]
    assert mail["to"] == "neha@family.example" and PROFILE["email"] in mail["reply_to"]
    assert any(a.startswith("r+") and a.endswith("@" + DOMAIN) for a in mail["reply_to"])
    assert "₹1,85,000" in mail["text"]                         # the price the owner wrote, and only that
    # the owner was alerted on the hub's number; the customer got nothing on WhatsApp
    owner = await owner_doc(uid)
    assert not await db().outbox.count_documents({"channel": "whatsapp", "kind": {"$ne": "otp"}, "to": {"$ne": owner["phone"]}})
    box = (await c.get("/api/inquiries")).json()
    assert box["items"][0]["status"] == "proposal_sent" and box["items"][0]["channel_label"] == "Email"


async def test_auto_reply_off_leaves_a_draft_the_owner_sends(client, inbound):
    c, uid, _ = await owner_on("9852000002", "lite")
    addr = await address_of(c)
    await client.post(f"/api/hooks/email-in/{SECRET}", json=sns(addr, raw_mail("ravi@shop.example", addr, "Price for kitchen?", "Need a kitchen.")))
    await inquiries.settle()
    lead = await db().leads.find_one({"owner_id": uid})
    assert lead["proposal"]["status"] == "draft" and not await db().outbox.count_documents({"channel": "email"})
    assert (await c.post(f"/api/inquiries/{lead['_id']}/send-email")).json()["ok"] is True
    assert await db().outbox.count_documents({"channel": "email", "to": "ravi@shop.example"}) == 1
    assert (await c.post(f"/api/inquiries/{lead['_id']}/send-email")).status_code == 409


async def test_spam_robots_wrong_secrets_and_bad_signatures_are_refused(client, inbound, monkeypatch):
    c, uid, _ = await owner_on("9852000003", "lite")
    addr = await address_of(c)
    spam = await client.post(f"/api/hooks/email-in/{SECRET}", json=sns(addr, raw_mail("x@spam.example", addr, "WIN", "Click"), spam="FAIL"))
    assert spam.json()["handled"] == ["rejected"]
    ooo = raw_mail("ravi@shop.example", addr, "Out of office", "I'm away.", Auto_Submitted="auto-replied")
    assert (await client.post(f"/api/hooks/email-in/{SECRET}", json=sns(addr, ooo))).json()["handled"] == ["ignored"]
    other = raw_mail("ravi@shop.example", f"nobody@{DOMAIN}", "Hi", "Hello")
    assert (await client.post(f"/api/hooks/email-in/{SECRET}", json=sns(f"nobody@{DOMAIN}", other))).json()["handled"] == ["unknown_address"]
    assert not await db().leads.count_documents({})
    assert (await client.post("/api/hooks/email-in/wrong", json={})).status_code == 404
    monkeypatch.undo()
    object.__setattr__(settings, "inbound_email_domain", DOMAIN)
    object.__setattr__(settings, "email_inbound_secret", SECRET)
    assert (await client.post(f"/api/hooks/email-in/{SECRET}", json=sns(addr, other))).status_code == 401


async def test_a_reply_to_a_payment_reminder_stops_the_follow_ups_and_tells_the_owner(client, inbound):
    c, uid, _ = await owner_on("9852000004", "running", live=False)
    d = await _due(c, "Mehta Traders", "accounts@mehta.example", "2026-11-20")
    s = (await c.get("/api/followups/auto")).json()
    await c.post("/api/followups/auto/approve", json={"hash": s["hash"]})
    owner = await owner_doc(uid)
    await followups.run_owner(owner, ist(2026, 11, 17, 10, 5))
    [sent] = [m async for m in db().outbox.find({"channel": "email"})]
    thread_addr = next(a for a in sent["reply_to"] if a.startswith("r+"))
    reply = raw_mail("Accounts <accounts@mehta.example>", thread_addr, "Re: " + sent["subject"],
                     "We'll pay on the 25th.\n\nOn Tue, 17 Nov 2026 at 10:05, Shree Ganesh Interiors wrote:\n> Hello")
    r = await client.post(f"/api/hooks/email-in/{SECRET}", json=sns(thread_addr, reply))
    assert r.json()["handled"] == ["reply_due"]
    assert (await db().dues.find_one({"_id": d["id"]}))["auto_stopped"] == "replied"
    assert await db().notices.count_documents({"user_id": uid, "text": {"$regex": "Mehta Traders replied to your payment reminder: “We'll pay on the 25th.”"}}) == 1
    await followups.run_owner(owner, ist(2026, 11, 20, 10, 5))
    assert await db().outbox.count_documents({"channel": "email"}) == 1


async def test_a_pasted_whatsapp_inquiry_gets_a_tap_to_send_proposal(client):
    c, uid, _ = await owner_on("9852000005", "lite")
    r = await c.post("/api/inquiries/whatsapp", json={"name": "Amit Kale", "phone": "98900 22002", "message": "Need modular kitchen price"})
    assert r.status_code == 200
    await inquiries.settle()
    lead = await db().leads.find_one({"_id": r.json()["id"]})
    assert lead["source"] == "whatsapp" and lead["stage"] == "identification"
    a = await db().approvals.find_one({"_id": lead["proposal"]["approval_id"]})
    assert a["status"] == "pending" and a["kind"] == "quote" and a["to_phone"] == "+919890022002" and "₹1,85,000" in a["text"]
    assert (await c.get("/api/inquiries")).json()["items"][0]["status"] == "new"
    tap = (await c.post(f"/api/inquiries/{lead['_id']}/whatsapp")).json()
    assert tap["link"].startswith("https://wa.me/919890022002?text=")
    assert (await c.get("/api/inquiries")).json()["items"][0]["status"] == "proposal_sent"
    # nothing went out on its own, and not from the hub's number
    owner = await owner_doc(uid)
    assert not await db().outbox.count_documents({"channel": "whatsapp", "kind": {"$ne": "otp"}, "to": {"$ne": owner["phone"]}})


async def test_no_proposal_says_a_price_discount_or_date_the_brain_doesnt(client, monkeypatch):
    c, uid, _ = await owner_on("9852000006", "lite")

    async def greedy(system, prompt, mock, max_tokens=1200):
        return {"matched": True, "offers": ["Modular kitchen"], "subject": "Offer",
                "email_text": "Kitchen at ₹99,999 with a 20% discount, delivered by 30 Nov.", "whatsapp_text": "₹99,999 only!"}, None
    monkeypatch.setattr(proposal_tasks, "complete", greedy)
    before = await used_runs(uid)
    r = await c.post("/api/inquiries/whatsapp", json={"name": "Amit Kale", "phone": "98900 22003", "message": "Kitchen price?"})
    await inquiries.settle()
    lead = await db().leads.find_one({"_id": r.json()["id"]})
    p = lead["proposal"]
    assert p["written_by"] == "template" and "₹" not in p["whatsapp_text"] and "discount" not in p["email_text"]
    assert await used_runs(uid) == before                     # the unusable draft's run was refunded
    assert proposal_tasks.unsafe("Kitchen ₹1,85,000 onwards", "Modular kitchen ₹1,85,000 onwards") == []
    assert proposal_tasks.unsafe("Ready in 10 days", "We take 21 days") == ["number 10"]
    assert "number 1.2" in proposal_tasks.unsafe("₹1.2 lakh", "₹1,20,000")          # strict: only numbers the Brain has
    for said in ("costs 99,999", "1450 per sq ft", "ready next Friday", "in two weeks", "20% discount"):
        assert proposal_tasks.unsafe(said, "Modular kitchen ₹1,85,000. We do not give any discount."), said
    assert proposal_tasks.unsafe("1. What size?\n2. Where?", "") == []                # a numbered list isn't a promise


async def test_one_reply_per_sender_a_day_a_daily_cap_and_website_inquiries_opt_in(client, inbound):
    c, uid, site = await owner_on("9852000007", "lite")
    addr = await address_of(c)
    await c.put("/api/inquiries/settings", json={"auto_email": True})
    for n in range(2):
        await client.post(f"/api/hooks/email-in/{SECRET}", json=sns(addr, raw_mail("neha@family.example", addr, f"Kitchen {n}", f"Kitchen please {n}")))
        await inquiries.settle()
    props = [x["proposal"]["status"] async for x in db().leads.find({"owner_id": uid}).sort("created_at", 1)]
    assert props == ["sent", "skipped"]
    old = settings.proposals_per_owner_per_day
    object.__setattr__(settings, "proposals_per_owner_per_day", 1)
    try:
        await client.post(f"/api/hooks/email-in/{SECRET}", json=sns(addr, raw_mail("ravi@shop.example", addr, "Wardrobe", "Wardrobe please")))
        await inquiries.settle()
        assert (await db().leads.find_one({"owner_id": uid, "email": "ravi@shop.example"}))["proposal"]["status"] == "draft"
    finally:
        object.__setattr__(settings, "proposals_per_owner_per_day", old)
    # website inquiries: no proposal (and no AI run) unless the owner switches it on
    await client.post(f"/s/{site['slug']}/inquiry", json={"name": "Kiran Shah", "phone": "98390 30005", "message": "Kitchen"})
    await inquiries.settle()
    assert "proposal" not in (await db().leads.find_one({"owner_id": uid, "name": "Kiran Shah"}))
    await c.put("/api/inquiries/settings", json={"auto_web": True})
    await client.post(f"/s/{site['slug']}/inquiry", json={"name": "Asha Rao", "phone": "98390 30006", "message": "Modular kitchen"})
    await inquiries.settle()
    assert (await db().leads.find_one({"owner_id": uid, "name": "Asha Rao"}))["proposal"]["approval_id"]


async def test_a_forwarded_inquiry_uses_the_customer_not_the_owner(client, inbound):
    c, uid, _ = await owner_on("9852000008", "lite")
    addr = await address_of(c)
    body = "---------- Forwarded message ---------\nFrom: Sunita Kulkarni <sunita@home.example>\nDate: Mon\nSubject: Kitchen\n\nNeed a kitchen."
    r = await client.post(f"/api/hooks/email-in/{SECRET}", json=sns(addr, raw_mail(f"Owner <{PROFILE['email']}>", addr, "Fwd: Kitchen", body)))
    assert r.json()["handled"] == ["lead"]
    lead = await db().leads.find_one({"owner_id": uid})
    assert lead["email"] == "sunita@home.example" and lead["name"] == "Sunita Kulkarni"


# ───────────────────────── fixes from the independent review ─────────────────────────
async def test_an_unconfirmed_sender_gets_no_automatic_email_no_ai_run_and_no_trusted_phone(client, inbound):
    c, uid, _ = await owner_on("9852000020", "growth")
    addr = await address_of(c)
    await c.put("/api/inquiries/settings", json={"auto_email": True})
    before = await used_runs(uid)
    raw = raw_mail("CEO <ceo@bigcorp.example>", addr, "Kitchen", "Call 98330 44444 now")
    assert (await client.post(f"/api/hooks/email-in/{SECRET}", json=sns(addr, raw, dmarc="FAIL"))).json()["handled"] == ["lead"]
    await inquiries.settle()
    lead = await db().leads.find_one({"owner_id": uid})
    assert lead["phone"] == "" and lead["sender_verified"] is False and "98330 44444" in lead["message"]
    assert lead["proposal"]["written_by"] == "template" and lead["proposal"]["status"] == "draft" and "couldn't be confirmed" in lead["proposal"]["held"]
    assert await used_runs(uid) == before and not await db().outbox.count_documents({"channel": "email"})
    assert not await db().calls.count_documents({}) and not await db().agent_jobs.count_documents({"agent": "telecaller"})


async def test_the_subject_and_payment_links_are_checked_too(client, inbound, monkeypatch):
    c, uid, _ = await owner_on("9852000021", "lite")
    addr = await address_of(c)
    await c.put("/api/inquiries/settings", json={"auto_email": True})

    async def sly(system, prompt, mock, max_tokens=1200):
        return {"matched": True, "offers": ["Modular kitchen"], "subject": "Kitchen at ₹99,999 — 20% discount",
                "email_text": "Hello, our modular kitchen is ₹1,85,000 onwards.", "whatsapp_text": "Modular kitchen ₹1,85,000 onwards."}, None
    monkeypatch.setattr(proposal_tasks, "complete", sly)
    await client.post(f"/api/hooks/email-in/{SECRET}", json=sns(addr, raw_mail("neha@family.example", addr, "Kitchen", "Kitchen please")))
    await inquiries.settle()
    lead = await db().leads.find_one({"owner_id": uid})
    assert lead["proposal"]["written_by"] == "template" and "99,999" not in str(await db().outbox.find_one({"channel": "email"}))
    from app.agents.approvals import link_hosts
    assert link_hosts("Pay now: upi://pay?pa=shop@okaxis&am=50000") and link_hosts("tel:+919820000000")


async def test_one_email_to_two_owners_is_a_lead_for_each(client, inbound):
    a, auid, _ = await owner_on("9852000022", "lite")
    b, buid, _ = await owner_on("9852000023", "lite", profile={**PROFILE, "name": "Om Electricals"})
    aaddr, baddr = await address_of(a), await address_of(b)
    raw = raw_mail("neha@family.example", f"{aaddr}, {baddr}", "Quotes please", "Need a quote")
    assert (await client.post(f"/api/hooks/email-in/{SECRET}", json=sns(aaddr, raw))).json()["handled"] == ["lead"]
    assert (await client.post(f"/api/hooks/email-in/{SECRET}", json=sns(baddr, raw))).json()["handled"] == ["lead"]
    assert await db().leads.count_documents({"owner_id": auid}) == 1 and await db().leads.count_documents({"owner_id": buid}) == 1


async def test_a_reply_forwarded_to_the_inquiry_address_still_stops_the_reminders(client, inbound):
    c, uid, _ = await owner_on("9852000024", "running")
    addr = await address_of(c)
    d = await _due(c, "Mehta Traders", "accounts@mehta.example", "2026-11-20")
    s = (await c.get("/api/followups/auto")).json()
    await c.post("/api/followups/auto/approve", json={"hash": s["hash"]})
    await followups.run_owner(await owner_doc(uid), ist(2026, 11, 17, 10, 5))
    entry = await db().email_log.find_one({"owner_id": uid})
    reply = raw_mail("accounts@mehta.example", addr, "Re: payment", "Paying Friday.", In_Reply_To=entry["rfc_id"])
    assert (await client.post(f"/api/hooks/email-in/{SECRET}", json=sns(addr, reply))).json()["handled"] == ["reply_due"]
    assert (await db().dues.find_one({"_id": d["id"]}))["auto_stopped"] == "replied"
    assert not await db().leads.count_documents({"owner_id": uid})          # not mistaken for a new inquiry
    # the owner hears it on their own WhatsApp from the hub's number, too
    owner = await owner_doc(uid)
    assert await db().outbox.count_documents({"channel": "whatsapp", "to": owner["phone"], "kind": "owner_alert"}) >= 1


async def test_every_business_gets_an_address_and_staff_cant_send_proposals(client, inbound):
    c, uid, _ = await owner_on("9852000025", "growth", profile={**PROFILE, "name": "R K Traders"})
    s = await c.get("/api/inquiries/settings")
    assert s.status_code == 200 and s.json()["address"].startswith("r-k-traders")
    r = await c.post("/api/inquiries/whatsapp", json={"name": "Amit", "phone": "98900 22009", "message": "Kitchen price"})
    await inquiries.settle()
    from tests.conftest import new_owner
    staff = await new_owner("9852000026")
    await db().users.update_one({"_id": (await staff.get("/api/me")).json()["user"]["id"]}, {"$set": {"team_of": uid, "team_role": "staff"}})
    assert (await staff.post(f"/api/inquiries/{r.json()['id']}/whatsapp")).status_code == 403
