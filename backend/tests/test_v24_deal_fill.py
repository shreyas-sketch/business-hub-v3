"""v2.4 Pipeline fills itself: a conversation (pasted, a WhatsApp export, a voice note, a scored call, an email from a known
contact, a quiet WhatsApp chat on the owner's number) becomes the deal's note, next step and follow-up date; stage, value, won
and lost wait for one tap; every change OwnerDesk makes can be undone; a value only when an amount was said.

Every business, person, number, chat and email here is invented. WhatsApp exports are built in the test."""
import io
import json
import zipfile
from datetime import date, timedelta
from email.message import EmailMessage

import pytest

from app import chat_ai, crm, deal_fill, lead_research, mailer, transcribe
from app.agents import catalog, scheduler
from app.ai import dealfill_tasks as tasks
from app.ai.provider import Usage
from app.config import settings
from app.db import IST, db, month_key, now
from app.routers import inquiries
from tests.v24_helpers import owner_id, v24_member

SECRET = "inbound-secret-path-v24-0001"
DOMAIN = "in.example.in"
ANSWERS = {"sell": "Modular kitchens and wardrobes", "customers": "Families with a new flat", "sources": ["website"], "who": "owner",
           "after_lead": "", "path": ["call", "quote"], "before_pay": "", "reasons": []}


def today() -> date:
    return crm.today_ist()


def dd(d: date) -> str:
    return d.strftime("%d/%m/%y")


async def member(phone: str, paid: bool = True):
    c = await v24_member(phone, paid=paid)
    uid = await owner_id(c)
    return c, uid


async def built(c) -> list[dict]:
    """The business's own stages: New inquiry → First call done → Quotation sent → Follow-up and talks → Order confirmed."""
    r = await c.post("/api/crm/pipeline/build", json={"answers": ANSWERS, "use_ai": False})
    assert r.status_code == 200, r.text
    return r.json()["stages"]


async def deal(c, name="Priya Shah", phone="98200 11111", email="", value=None) -> dict:
    r = await c.post("/api/crm/deals", json={"name": name, "phone": phone, "email": email, "value": value, "source": "whatsapp"})
    assert r.status_code == 200, r.text
    return r.json()


async def tokens_used(uid: str) -> int:
    u = await db().usage.find_one({"user_id": uid, "month": month_key()})
    return int((u or {}).get("used", 0))


CHAT = ("Priya: Hi, we need a modular kitchen for our new 2BHK.\n"
        "Me: Sure ji. Sending you the quotation now.\n"
        "Priya: The budget is ₹1,75,000. Please call me tomorrow, I'll confirm after talking to my husband.")


# ───────────────────────── paste: note and follow-up applied, the rest waits ─────────────────────────
async def test_a_pasted_chat_adds_the_note_and_follow_up_and_the_stage_and_value_wait(client):
    c, uid = await member("9800420001")
    stages = await built(c)
    d = await deal(c)
    before = await tokens_used(uid)
    r = await c.post(f"/api/crm/deals/{d['id']}/conversation", json={"text": CHAT, "source": "paste"})
    assert r.status_code == 200, r.text
    u = r.json()["update"]
    tomorrow = (today() + timedelta(days=1)).isoformat()
    assert u["state"] == "pending" and u["follow_up_on"] == tomorrow and u["applied_follow_up"]
    assert {s["key"] for s in u["suggested"]} == {"pstage", "value"}
    labels = " ".join(s["label"] for s in u["suggested"])
    assert "Quotation sent" in labels and "1,75,000" in labels
    assert await tokens_used(uid) == before + 1
    lead = await db().leads.find_one({"_id": d["id"]})
    assert lead["follow_up_on"] == tomorrow and lead["follow_up_note"]                    # applied by itself
    assert lead["pstage"] == stages[0]["id"] and lead.get("value") is None                # these wait for a tap
    assert await db().agent_jobs.find_one({"_id": f"crmfu:deal:{d['id']}", "status": "scheduled"})
    acts = [a async for a in db().crm_activities.find({"lead_id": d["id"], "by": "ownerdesk"})]
    note = next(a for a in acts if a["kind"] == "note")
    assert "summary by OwnerDesk" in note["text"] and note["by_name"] == "OwnerDesk"
    assert {a["_id"] for a in acts} == set(u["activity_ids"])
    rec = await db().deal_updates.find_one({"_id": u["id"]})
    assert rec["source"] == "paste" and rec["lead_id"] == d["id"] and rec["undo"]["follow_up_on"] is None and "text" not in rec
    log = await db().agent_log.find_one({"ws": uid, "agent": "pipeline_keeper"})
    assert log["ref"] == f"lead:{d['id']}"
    # the deal's page shows it waiting
    g = (await c.get(f"/api/crm/deals/{d['id']}/updates")).json()
    assert [p["id"] for p in g["pending"]] == [u["id"]] and g["settings"]["auto_forward"] is False


async def test_apply_a_part_dismiss_and_undo(client):
    c, uid = await member("9800420002")
    stages = await built(c)
    d = await deal(c)
    u = (await c.post(f"/api/crm/deals/{d['id']}/conversation", json={"text": CHAT})).json()["update"]
    r = await c.post(f"/api/crm/updates/{u['id']}/apply", json={"fields": ["pstage"]})
    assert r.status_code == 200, r.text
    assert r.json()["update"]["state"] == "applied"
    lead = await db().leads.find_one({"_id": d["id"]})
    assert lead["pstage"] == "quotation-sent" and lead.get("value") is None             # only what was picked
    line = await db().crm_activities.find_one({"lead_id": d["id"], "kind": "stage"})
    assert "Quotation sent" in line["text"] and "OwnerDesk's suggestion" in line["by_name"]
    assert (await c.post(f"/api/crm/updates/{u['id']}/apply", json={})).status_code == 409
    # Undo takes back all of it: the stage, the follow-up date, OwnerDesk's lines
    r = await c.post(f"/api/crm/updates/{u['id']}/undo")
    assert r.status_code == 200, r.text
    assert r.json()["kept"] == [] and r.json()["update"]["undone_at"]
    lead = await db().leads.find_one({"_id": d["id"]})
    assert lead["pstage"] == stages[0]["id"] and lead["follow_up_on"] is None
    assert not await db().crm_activities.find_one({"_id": {"$in": u["activity_ids"]}})
    assert not await db().agent_jobs.find_one({"_id": f"crmfu:deal:{d['id']}", "status": "scheduled"})
    assert (await c.post(f"/api/crm/updates/{u['id']}/undo")).status_code == 409
    # Dismiss leaves the deal as it is
    u2 = (await c.post(f"/api/crm/deals/{d['id']}/conversation", json={"text": CHAT})).json()["update"]
    snap = await db().leads.find_one({"_id": d["id"]}, {"pstage": 1, "value": 1, "status": 1})
    assert (await c.post(f"/api/crm/updates/{u2['id']}/dismiss")).json()["update"]["state"] == "dismissed"
    assert await db().leads.find_one({"_id": d["id"]}, {"pstage": 1, "value": 1, "status": 1}) == snap
    assert (await c.post(f"/api/crm/updates/{u2['id']}/apply", json={})).status_code == 409


async def test_value_and_won_wait_for_the_tap_and_undo_restores_them(client):
    c, uid = await member("9800420003")
    await built(c)
    d = await deal(c, value=150000)
    text = "Rohit: OK final. ₹1,90,000 for the kitchen and wardrobe. Advance paid just now, order confirmed."
    u = (await c.post(f"/api/crm/deals/{d['id']}/conversation", json={"text": text})).json()["update"]
    keys = {s["key"] for s in u["suggested"]}
    assert {"value", "status"} <= keys and any(s["label"] == "Mark won" for s in u["suggested"])
    lead = await db().leads.find_one({"_id": d["id"]})
    assert lead["value"] == 150000 and lead["status"] == "new"                          # nothing changed by itself
    assert (await c.post(f"/api/crm/updates/{u['id']}/apply", json={"fields": ["value", "status"]})).status_code == 200
    lead = await db().leads.find_one({"_id": d["id"]})
    assert lead["value"] == 190000 and lead["status"] == "won"
    r = (await c.post(f"/api/crm/updates/{u['id']}/undo")).json()
    lead = await db().leads.find_one({"_id": d["id"]})
    assert lead["value"] == 150000 and lead["status"] == "new" and r["kept"] == []


async def test_undo_never_overwrites_what_a_person_changed_since(client):
    c, uid = await member("9800420004")
    d = await deal(c)
    u = (await c.post(f"/api/crm/deals/{d['id']}/conversation", json={"text": "Priya: Please call me tomorrow about the kitchen."})).json()["update"]
    mine = (today() + timedelta(days=5)).isoformat()
    assert (await c.patch(f"/api/crm/deals/{d['id']}", json={"follow_up_on": mine})).status_code == 200
    r = (await c.post(f"/api/crm/updates/{u['id']}/undo")).json()
    assert r["kept"] == ["the follow-up date (changed since)"]
    assert (await db().leads.find_one({"_id": d["id"]}))["follow_up_on"] == mine


async def test_lost_only_when_the_customer_clearly_said_no(client):
    c, uid = await member("9800420005")
    await built(c)
    d = await deal(c, name="Anil Mehta", phone="98200 22222")
    maybe = "Me: If you are not interested, no problem.\nAnil: Let me think and tell you."
    u = (await c.post(f"/api/crm/deals/{d['id']}/conversation", json={"text": maybe})).json()["update"]
    assert not any(s["key"] == "status" for s in u["suggested"])
    no = "Anil: Sorry, we went with another vendor, it was too expensive for us. Not interested now."
    u = (await c.post(f"/api/crm/deals/{d['id']}/conversation", json={"text": no})).json()["update"]
    lost = next(s for s in u["suggested"] if s["key"] == "status")
    assert lost["label"].startswith("Mark lost")
    assert (await db().leads.find_one({"_id": d["id"]}))["status"] == "new"


# ───────────────────────── a value only when an amount was said ─────────────────────────
NO_AMOUNT = ("Kunal: We need 25 boxes, size 12 x 10 inch, 3 ply. Flat 302, Shanti Towers.\n"
             "Kunal: My number is 98200 33333, call after 6. We have a 2/3 BHK sample flat to show.")


async def test_a_conversation_with_no_amount_never_produces_a_value(client, monkeypatch):
    c, uid = await member("9800420006")
    d = await deal(c, name="Kunal Desai", phone="98200 33333")
    assert tasks.amounts(NO_AMOUNT) == [] and tasks.said_dates(tasks.to_messages(NO_AMOUNT, today()), today()) == []
    u = (await c.post(f"/api/crm/deals/{d['id']}/conversation", json={"text": NO_AMOUNT})).json()["update"]
    assert not any(s["key"] == "value" for s in u["suggested"]) and u["follow_up_on"] is None

    # even when the AI says an amount and a date, they're dropped: neither was said
    async def sly(profile, deal, stages, current, reasons, conversation, on, fallback):
        return {"summary": ["They want 25 boxes."], "needs": "Boxes", "next_step": "Call after 6", "follow_up_on": (on + timedelta(days=2)).isoformat(),
                "value": 125000, "pstage": None, "status": None, "lost_reason": None, "customer_name": "Kunal"}, Usage()
    monkeypatch.setattr(deal_fill.tasks, "read_conversation", sly)
    u = (await c.post(f"/api/crm/deals/{d['id']}/conversation", json={"text": NO_AMOUNT})).json()["update"]
    assert u["written_by"] == "ai"
    assert not any(s["key"] == "value" for s in u["suggested"]) and u["follow_up_on"] is None
    lead = await db().leads.find_one({"_id": d["id"]})
    assert lead.get("value") is None and lead.get("follow_up_on") is None
    # …and an amount the AI worked out (25 × ₹450) is not one that was said
    priced = NO_AMOUNT + "\nMe: Rate is ₹450 per box."

    async def maths(*a):
        r, _ = await sly(*a)
        return {**r, "value": 11250}, Usage()
    monkeypatch.setattr(deal_fill.tasks, "read_conversation", maths)
    u = (await c.post(f"/api/crm/deals/{d['id']}/conversation", json={"text": priced})).json()["update"]
    assert not any(s["key"] == "value" for s in u["suggested"])


def test_amounts_and_dates_said():
    assert tasks.amounts("₹1,85,000 final") == [185000] and tasks.amounts("Rs. 1.85 lakh") == [185000]
    assert tasks.amounts("budget 2 lakhs, maybe 85k more, 50000/- advance") == [200000, 85000, 50000]
    assert tasks.amounts("call 98200 12345, flat 1,85,000 sq ft? no") == [] and tasks.amounts("5k followers") == []
    sat = date(2026, 11, 21)          # a Saturday
    assert tasks.dates_in("call me tomorrow", sat) == [date(2026, 11, 22)]
    assert tasks.dates_in("see you on Monday", sat) == [date(2026, 11, 23)]
    assert tasks.dates_in("by the 15th please", sat) == [date(2026, 12, 15)]
    assert tasks.dates_in("15th Dec works, or Dec 18", sat) == [date(2026, 12, 15), date(2026, 12, 18)]
    assert tasks.dates_in("day after tomorrow", sat) == [date(2026, 11, 23)]
    assert tasks.dates_in("deliver on 2/12", sat) == [date(2026, 12, 2)]
    for unsure in ("kal milte hain", "next week", "on the 1st floor", "2/3 BHK flat", "I may 2 people bring", "5.30 pm"):
        assert tasks.dates_in(unsure, sat) == [], unsure


# ───────────────────────── WhatsApp "Export chat" files ─────────────────────────
def android_export(day1: date) -> str:
    nb = " "
    return (f"{dd(day1)}, 10:01{nb}am - Messages and calls are end-to-end encrypted. No one outside of this chat can read them.\n"
            f"{dd(day1)}, 10:15{nb}am - Priya Shah: Hi, need a quote for a modular kitchen\n"
            f"{dd(day1)}, 10:16{nb}am - Shree Ganesh: Sure. What is the size?\n"
            f"{dd(day1)}, 10:20{nb}am - Priya Shah: <Media omitted>\n"
            f"{dd(day1)}, 10:21{nb}am - Priya Shah: 10 x 8 feet, L shape.\nAlso a loft above\n"
            f"{dd(day1)}, 9:05{nb}pm - Priya Shah: Please call me day after tomorrow, budget around 2 lakh\n")


def iphone_export(day1: date) -> str:
    return (f"[{dd(day1)}, 10:15:42 AM] Priya Shah: Hi, need a quote for a modular kitchen\n"
            f"‎[{dd(day1)}, 10:16:01 AM] Shree Ganesh: ‎image omitted\n"
            f"[{day1.strftime('%d/%m/%Y')}, 21:05:00] Priya Shah: Please call me day after tomorrow, budget around 2 lakh\n")


def test_whatsapp_exports_are_read_in_both_formats():
    y = today() - timedelta(days=1)
    a = tasks.parse_chat(android_export(y))
    assert [m["who"] for m in a] == ["Priya Shah", "Shree Ganesh", "Priya Shah", "Priya Shah"]           # system line and media gone
    assert a[2]["text"] == "10 x 8 feet, L shape.\nAlso a loft above" and all(m["day"] == y for m in a)
    i = tasks.parse_chat(iphone_export(y))
    assert [m["text"][:12] for m in i] == ["Hi, need a q", "Please call "] and i[1]["day"] == y
    copied = tasks.parse_chat(f"[10:15 am, {y.strftime('%d/%m/%Y')}] Priya Shah: Hello\n[10:16 am, {y.strftime('%d/%m/%Y')}] You: Hi")
    assert [m["who"] for m in copied] == ["Priya Shah", "You"]
    # a chat typed or copied without times: one message per "Name: text" line; an email isn't split by its headers
    typed = tasks.to_messages("Priya: Hi\nMe: Hello ji\nPriya: Budget: 2 lakh\nalso a loft", today())
    assert [m["who"] for m in typed] == ["Priya", "Me", "Priya"] and typed[2]["text"] == "Budget: 2 lakh\nalso a loft"
    mail = tasks.to_messages("Subject: Kitchen\nHi, we need a kitchen.\nThanks", today())
    assert len(mail) == 1 and mail[0]["who"] == ""
    # "day after tomorrow" written yesterday is tomorrow — worked out from the day it was written
    assert tasks.said_dates(a, today()) == [today() + timedelta(days=1)]
    long = "".join(f"{dd(y)}, 10:{n % 60:02d} am - Priya Shah: message {n}\n" for n in range(450))
    kept = tasks.parse_chat(long)
    assert len(kept) == tasks.KEEP_MESSAGES and kept[-1]["text"] == "message 449"


async def test_uploading_a_whatsapp_export_txt_and_zip(client):
    c, uid = await member("9800420007")
    d = await deal(c)
    y = today() - timedelta(days=1)
    r = await c.post(f"/api/crm/deals/{d['id']}/conversation/upload",
                     files={"file": ("WhatsApp Chat with Priya Shah.txt", android_export(y).encode(), "text/plain")})
    assert r.status_code == 200, r.text
    u = r.json()["update"]
    assert u["source"] == "upload" and u["follow_up_on"] == (today() + timedelta(days=1)).isoformat()
    assert any(s["key"] == "value" and "2,00,000" in s["label"] for s in u["suggested"])
    # an iPhone export comes as a .zip with the chat and the photos: only the chat is read
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("_chat.txt", iphone_export(y))
        z.writestr("00000012-PHOTO-2026-11-20.jpg", b"\xff\xd8\xff not really a photo")
    r = await c.post(f"/api/crm/deals/{d['id']}/conversation/upload", files={"file": ("WhatsApp Chat - Priya Shah.zip", buf.getvalue(), "application/zip")})
    assert r.status_code == 200, r.text
    assert r.json()["update"]["source"] == "upload" and r.json()["update"]["follow_up_on"] == (today() + timedelta(days=1)).isoformat()
    # a zip with no chat in it, a broken zip, and a file that is neither
    empty = io.BytesIO()
    with zipfile.ZipFile(empty, "w") as z:
        z.writestr("photo.jpg", b"x")
    assert (await c.post(f"/api/crm/deals/{d['id']}/conversation/upload", files={"file": ("a.zip", empty.getvalue(), "application/zip")})).status_code == 400
    assert (await c.post(f"/api/crm/deals/{d['id']}/conversation/upload", files={"file": ("b.zip", b"PK\x03\x04broken", "application/zip")})).status_code == 400
    assert (await c.post(f"/api/crm/deals/{d['id']}/conversation/upload", files={"file": ("c.pdf", b"%PDF-1.4", "application/pdf")})).status_code == 415


async def test_a_voice_note_is_transcribed_then_filed(client, monkeypatch):
    c, uid = await member("9800420008")
    d = await deal(c)
    heard = []

    async def fake(data: bytes, filename: str, content_type: str) -> str:
        heard.append((len(data), content_type))
        return "Spoke to Priya just now. She wants the L shape kitchen. Call her tomorrow, budget is 2 lakh."
    monkeypatch.setattr(transcribe, "transcribe", fake)
    before = await tokens_used(uid)
    r = await c.post(f"/api/crm/deals/{d['id']}/conversation/upload", files={"file": ("voice-note.webm", b"\x1aE\xdf\xa3 fake audio", "audio/webm;codecs=opus")})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["update"]["source"] == "voice" and "L shape kitchen" in out["transcript"] and heard == [(len(b"\x1aE\xdf\xa3 fake audio"), "audio/webm")]
    assert out["update"]["follow_up_on"] == (today() + timedelta(days=1)).isoformat()
    assert await tokens_used(uid) == before + 1                     # one token for the conversation

    async def silent(*a):
        return ""
    monkeypatch.setattr(transcribe, "transcribe", silent)
    assert (await c.post(f"/api/crm/deals/{d['id']}/conversation/upload", files={"file": ("v.mp3", b"ID3 quiet", "audio/mpeg")})).status_code == 400
    assert await tokens_used(uid) == before + 1


async def test_a_scored_call_is_added_once(client):
    c, uid = await member("9800420009")
    d = await deal(c)
    await db().gym_sessions.insert_one({"_id": "call-test-1", "ws": uid, "kind": "call", "source": "call_coach", "member": "Ramesh", "created_at": now()})
    text = "Customer: I need a wardrobe for the master bedroom.\nSalesperson: I'll send the design on Monday."
    a = await c.post(f"/api/crm/deals/{d['id']}/conversation", json={"text": text, "source": "call", "call_id": "call-test-1"})
    assert a.status_code == 200, a.text
    used = await tokens_used(uid)
    b = await c.post(f"/api/crm/deals/{d['id']}/conversation", json={"text": text, "source": "call", "call_id": "call-test-1"})
    assert b.json()["update"]["id"] == a.json()["update"]["id"] and await tokens_used(uid) == used
    assert a.json()["update"]["call_id"] == "call-test-1" and a.json()["update"]["source"] == "call"
    assert (await c.post(f"/api/crm/deals/{d['id']}/conversation", json={"text": text, "source": "call", "call_id": "nope"})).status_code == 404


# ───────────────────────── no deal yet ─────────────────────────
async def test_a_conversation_with_someone_new_suggests_a_contact_and_deal(client):
    c, uid = await member("9800420010")
    text = "Kavita Rao: Hello, I want a quote for a 3BHK full interior. Budget 8 lakh. Can you visit on Monday?"
    r = await c.post("/api/crm/conversation", json={"text": text, "name": "Kavita Rao", "phone": "98200 44444"})
    assert r.status_code == 200, r.text
    u = r.json()["update"]
    assert u["lead_id"] is None and u["state"] == "pending" and [s["key"] for s in u["suggested"]] == ["new_deal"]
    assert not await db().leads.count_documents({"owner_id": uid})                       # waits for the tap
    assert [p["id"] for p in (await c.get("/api/crm/updates")).json()["pending"]] == [u["id"]]
    assert (await db().agent_log.find_one({"ws": uid, "agent": "pipeline_keeper"}))["ref"] == f"update:{u['id']}"
    r = await c.post(f"/api/crm/updates/{u['id']}/apply", json={})
    assert r.status_code == 200, r.text
    lead = await db().leads.find_one({"owner_id": uid})
    assert lead["name"] == "Kavita Rao" and lead["phone"] == "+919820044444" and lead["value"] == 800000 and lead["contact_id"]
    assert await db().crm_activities.find_one({"lead_id": lead["_id"], "by": "ownerdesk", "kind": "note"})
    # undo removes the new deal and the contact it made
    assert (await c.post(f"/api/crm/updates/{u['id']}/undo")).status_code == 200
    assert not await db().leads.count_documents({"owner_id": uid}) and not await db().contacts.count_documents({"ws": uid})
    # no name in the chat and none typed: the owner types it when applying
    anon = (await c.post("/api/crm/conversation", json={"text": "hello, need 2 wardrobes with sliding doors, budget 90k"})).json()["update"]
    assert anon["suggested"][0]["needs_name"] is True
    assert (await c.post(f"/api/crm/updates/{anon['id']}/apply", json={})).status_code == 400
    r = await c.post(f"/api/crm/updates/{anon['id']}/apply", json={"name": "Wardrobe customer", "phone": "98200 66666"})
    assert r.status_code == 200, r.text
    assert await db().leads.find_one({"owner_id": uid, "name": "Wardrobe customer", "phone": "+919820066666", "value": 90000})
    # someone who already has an open deal: filed into it
    d = await deal(c, name="Kavita Rao", phone="98200 44444")
    u = (await c.post("/api/crm/conversation", json={"text": text, "phone": "98200 44444"})).json()["update"]
    assert u["lead_id"] == d["id"] and not any(s["key"] == "new_deal" for s in u["suggested"])


async def test_move_forward_by_itself_moves_the_stage_but_never_the_value_or_won(client):
    c, uid = await member("9800420011")
    stages = await built(c)
    assert (await c.put("/api/crm/dealfill/settings", json={"auto_forward": True})).json()["auto_forward"] is True
    d = await deal(c)
    u = (await c.post(f"/api/crm/deals/{d['id']}/conversation", json={"text": CHAT})).json()["update"]
    lead = await db().leads.find_one({"_id": d["id"]})
    assert lead["pstage"] == "quotation-sent" and u["applied_stage"] == "Quotation sent"       # moved forward by itself
    assert lead.get("value") is None and any(s["key"] == "value" for s in u["suggested"])
    won = (await c.post(f"/api/crm/deals/{d['id']}/conversation", json={"text": "Priya: Advance paid, order confirmed!"})).json()["update"]
    lead = await db().leads.find_one({"_id": d["id"]})
    assert lead["status"] == "new" and any(s["label"] == "Mark won" for s in won["suggested"])
    # the last step of the sale ("Order confirmed") waits for a tap too, like Won
    assert lead["pstage"] == "quotation-sent" and any(s["label"] == "Move to Order confirmed" for s in won["suggested"])
    assert (await c.post(f"/api/crm/updates/{u['id']}/undo")).status_code == 200
    assert (await db().leads.find_one({"_id": d["id"]}))["pstage"] == stages[0]["id"]


# ───────────────────────── email from a known contact ─────────────────────────
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


def sns(to: str, raw: str, dmarc: str = "PASS") -> dict:
    note = {"notificationType": "Received", "receipt": {"recipients": [to], "spamVerdict": {"status": "PASS"}, "virusVerdict": {"status": "PASS"},
                                                        "dmarcVerdict": {"status": dmarc}}, "mail": {"messageId": "x"}, "content": raw}
    return {"Type": "Notification", "Message": json.dumps(note), "TopicArn": "arn:aws:sns:ap-south-1:1:in"}


async def settle():
    await inquiries.settle()
    await lead_research.settle()


async def test_an_email_from_a_known_contact_goes_into_the_open_deal_and_a_stranger_still_becomes_an_inquiry(client, inbound):
    c, uid = await member("9800420012")
    addr = (await c.get("/api/inquiries/settings")).json()["address"]
    d = await deal(c, name="Neha Joshi", phone="98200 55555", email="neha@family.example")
    mail = raw_mail("Neha Joshi <neha@family.example>", addr, "Re: kitchen quote", "We liked the design. Please call me tomorrow to fix the date.")
    r = await client.post(f"/api/hooks/email-in/{SECRET}", json=sns(addr, mail))
    assert r.json() == {"ok": True, "handled": ["deal"]}
    assert (await client.post(f"/api/hooks/email-in/{SECRET}", json=sns(addr, mail))).json()["handled"] == ["duplicate"]
    await settle()
    assert await db().leads.count_documents({"owner_id": uid}) == 1                 # no new inquiry
    lead = await db().leads.find_one({"_id": d["id"]})
    assert lead["follow_up_on"] == (today() + timedelta(days=1)).isoformat() and lead["replies"][-1]["from"] == "neha@family.example"
    u = await db().deal_updates.find_one({"ws": uid, "source": "email"})
    assert u["lead_id"] == d["id"] and u["by"] == "ownerdesk"
    assert await db().crm_activities.find_one({"lead_id": d["id"], "kind": "note", "by": "ownerdesk", "text": {"$regex": "^Email"}})
    # a robot (out of office) from the same person is still ignored
    ooo = raw_mail("neha@family.example", addr, "Out of office", "I'm away.", Auto_Submitted="auto-replied")
    assert (await client.post(f"/api/hooks/email-in/{SECRET}", json=sns(addr, ooo))).json()["handled"] == ["ignored"]
    # an address that can't be proven (DMARC failed) is not trusted with the deal: it's an inquiry, as before
    spoof = raw_mail("Neha Joshi <neha@family.example>", addr, "Change of plan", "Cancel everything and send the refund.")
    assert (await client.post(f"/api/hooks/email-in/{SECRET}", json=sns(addr, spoof, dmarc="FAIL"))).json()["handled"] == ["lead"]
    # someone new: a new inquiry, as always
    stranger = raw_mail("Ravi <ravi@shop.example>", addr, "Price for wardrobe?", "Need a wardrobe quote.")
    assert (await client.post(f"/api/hooks/email-in/{SECRET}", json=sns(addr, stranger))).json()["handled"] == ["lead"]
    await settle()
    assert await db().leads.count_documents({"owner_id": uid}) == 3
    assert await db().deal_updates.count_documents({"ws": uid, "source": "email"}) == 1


async def test_with_the_pipeline_keeper_off_an_email_is_an_inquiry_as_before(client, inbound):
    c, uid = await member("9800420013")
    addr = (await c.get("/api/inquiries/settings")).json()["address"]
    await deal(c, name="Neha Joshi", phone="98200 55555", email="neha@family.example")
    assert (await c.patch("/api/agents/pipeline_keeper", json={"on": False})).status_code == 200
    mail = raw_mail("Neha Joshi <neha@family.example>", addr, "Re: kitchen", "Please call me tomorrow.")
    assert (await client.post(f"/api/hooks/email-in/{SECRET}", json=sns(addr, mail))).json()["handled"] == ["lead"]
    await settle()
    assert not await db().deal_updates.count_documents({"ws": uid})


# ───────────────────────── WhatsApp chats on the owner's number ─────────────────────────
async def test_whatsapp_chats_are_filed_once_a_day_per_chat_and_never_answered(client):
    c, uid = await member("9800420014")
    d = await deal(c, name="Priya Shah", phone="98200 11111")
    await chat_ai.add_message(uid, "919820011111", "Priya", "in", "Hi, is the quotation ready? Please call me tomorrow.", "customer")
    await chat_ai.add_message(uid, "919820011111", "", "out", "Yes ji, sending it today.", uid)
    await chat_ai.add_message(uid, "919820099999", "Stranger", "in", "hello, price?", "customer")       # not a contact: left alone
    owner = await db().users.find_one({"_id": uid})
    out_before = await db().wa_messages.count_documents({"ws": uid, "dir": "out"})

    assert await deal_fill.file_chats(owner, now() + timedelta(minutes=10)) == 0          # not quiet for 30 minutes yet
    later = now() + timedelta(minutes=40)
    assert await deal_fill.file_chats(owner, later) == 1
    assert await deal_fill.file_chats(owner, later + timedelta(minutes=5)) == 0          # at most once a day per chat
    u = await db().deal_updates.find_one({"ws": uid, "source": "whatsapp"})
    assert u["lead_id"] == d["id"] and await db().crm_activities.find_one({"lead_id": d["id"], "by": "ownerdesk", "kind": "note"})
    # the next day, with nothing new from the customer: nothing to file; with a new message: filed again
    next_day = later + timedelta(days=1)
    assert await deal_fill.file_chats(owner, next_day) == 0
    msg = await chat_ai.add_message(uid, "919820011111", "Priya", "in", "Got it, will confirm on Monday.", "customer")
    await db().wa_messages.update_many({"thread_id": msg["_id"], "text": {"$regex": "^Got it"}}, {"$set": {"at": next_day - timedelta(hours=2)}})
    await db().wa_threads.update_one({"_id": msg["_id"]}, {"$set": {"last_in_at": next_day - timedelta(hours=2)}})
    assert await deal_fill.file_chats(owner, next_day) == 1
    assert await db().deal_updates.count_documents({"ws": uid, "source": "whatsapp"}) == 2
    # it only read: no message to any customer, from any number. (On main the Membership includes WhatsApp chats, so the
    # WhatsApp AI being in the plan doesn't matter here: filing a chat never answers it.)
    assert await db().wa_messages.count_documents({"ws": uid, "dir": "out"}) == out_before
    owner_phone = owner["phone"]
    assert not await db().outbox.count_documents({"channel": "whatsapp", "kind": {"$ne": "otp"}, "to": {"$ne": owner_phone}})


async def test_the_evening_agent_runs_once_a_day_for_owners_with_chats(client):
    c, uid = await member("9800420015")
    await deal(c, name="Priya Shah", phone="98200 11111")
    await chat_ai.add_message(uid, "919820011111", "Priya", "in", "Please call me tomorrow about the kitchen.", "customer")
    owner = await db().users.find_one({"_id": uid})
    spec = catalog.AGENTS["pipeline_keeper"]
    at = (now().astimezone(IST) + timedelta(days=1)).replace(hour=19, minute=35, second=0, microsecond=0)
    slot = catalog.due_slot(spec.schedule, at)
    assert slot is not None
    assert await scheduler.run_slot(spec, [owner], slot[0], at) == 1
    assert await scheduler.run_slot(spec, [owner], slot[0], at) == 0                   # one run per slot
    line = await db().agent_log.find_one({"ws": uid, "agent": "pipeline_keeper", "text": {"$regex": "^Filed 1 WhatsApp chat"}})
    assert line and line["status"] == "done"
    # an owner with the Pipeline keeper switched off is left out
    assert (await c.patch("/api/agents/pipeline_keeper", json={"on": False})).status_code == 200
    nxt = at + timedelta(days=1)
    assert await scheduler.run_slot(spec, [owner], catalog.due_slot(spec.schedule, nxt)[0], nxt) == 0


# ───────────────────────── who may use it ─────────────────────────
async def test_free_owners_are_refused(client):
    c, uid = await member("9800420016", paid=False)
    r = await c.post("/api/crm/deals", json={"name": "Priya Shah", "phone": "98200 11111"})
    lead_id = r.json()["id"] if r.status_code == 200 else "x"
    assert (await c.post(f"/api/crm/deals/{lead_id}/conversation", json={"text": CHAT})).status_code == 403
    assert (await c.post(f"/api/crm/deals/{lead_id}/conversation/upload", files={"file": ("a.txt", b"hello there friend", "text/plain")})).status_code == 403
    assert (await c.post("/api/crm/conversation", json={"text": CHAT})).status_code == 403
    assert (await c.get(f"/api/crm/deals/{lead_id}/updates")).status_code == 403
    owner = await db().users.find_one({"_id": uid})
    assert await deal_fill.from_email(owner, {}, "Priya", "priya@family.example", "call me", True) is None


async def test_out_of_tokens_the_button_says_so_and_an_ai_failure_uses_the_rules_version(client, monkeypatch):
    c, uid = await member("9800420017")
    d = await deal(c)
    allowance = (await c.get("/api/me")).json()["runs"]["allowance"]
    await db().usage.update_one({"user_id": uid, "month": month_key()}, {"$set": {"used": allowance}}, upsert=True)
    r = await c.post(f"/api/crm/deals/{d['id']}/conversation", json={"text": CHAT})
    assert r.status_code == 402
    assert not await db().deal_updates.count_documents({"ws": uid})
    await db().usage.update_one({"user_id": uid, "month": month_key()}, {"$set": {"used": 0}})

    async def broken(*a):
        return {"summary": []}, Usage()
    monkeypatch.setattr(deal_fill.tasks, "read_conversation", broken)
    u = (await c.post(f"/api/crm/deals/{d['id']}/conversation", json={"text": CHAT})).json()["update"]
    assert u["written_by"] == "rules" and u["tokens"] == 0 and u["summary"] and await tokens_used(uid) == 0
    assert u["follow_up_on"] == (today() + timedelta(days=1)).isoformat()
