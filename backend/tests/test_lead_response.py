"""v2.2 Lead Response (Growth Mentorship): the first reply to every new inquiry, within seconds, from the owner's own WhatsApp
number, with a call or visit to book; prices and quotes go to a person. Every name, phone number and email here is invented."""
import re
from datetime import datetime, timedelta

from app import connections
from app.agents import growth
from app.ai import chat_tasks
from app.db import IST, db
from app.routers import leadresponse
from tests.conftest import PROFILE, go_live, new_owner

ADMIN = "9999900000"
BUSINESS = PROFILE["name"]
LINK = "https://cal.example.in/sgi/site-visit"


async def owner_on(phone: str, plan: str = "growth", live: bool = True):
    c = await new_owner(phone)
    site = await go_live(c) if live else None
    if not live:
        assert (await c.put("/api/business", json=PROFILE)).status_code == 200
    uid = (await c.get("/api/me")).json()["user"]["id"]
    if plan != "free":
        admin = await new_owner(ADMIN)
        assert (await admin.patch(f"/api/admin/users/{uid}", json={"plan": plan})).status_code == 200
    return c, uid, site


async def connect(uid: str, lead_response_template: bool = True):
    """A demo connection to the owner's own number: messages are written to the outbox (tests and laptops only)."""
    values = {"provider": "demo", "tpl_instant": "instant_v1", "tpl_followup": "followup_v1", "tpl_customer": "customer_v1"}
    if lead_response_template:
        values["tpl_lead_response"] = "first_reply_booking_v1"
    await connections.save(uid, "whatsapp", values, "test", "test")


async def inquiry(client, site, name="Ritu Malhotra", phone="98345 00001", message="Need a modular kitchen for our new flat"):
    r = await client.post(f"/s/{site['slug']}/inquiry", json={"name": name, "phone": phone, "message": message})
    assert r.status_code == 200, r.text
    digits = re.sub(r"\D", "", phone)[-10:]
    return await db().leads.find_one({"phone": "+91" + digits}, sort=[("created_at", -1)])


async def to_customer(digits: str) -> list[dict]:
    return [o async for o in db().outbox.find({"channel": "whatsapp", "to": digits})]


async def turn_on(c, **body):
    """Like the page: save the booking, read the first reply (its key), then switch on with that key."""
    if body:
        assert (await c.put("/api/lead-response/settings", json=body)).status_code == 200
    key = (await c.get("/api/lead-response")).json()["preview"]["key"]
    r = await c.put("/api/lead-response/settings", json={"on": True, "sample_key": key})
    assert r.status_code == 200, r.text
    assert r.json()["settings"]["on"] is True
    return r.json()


def meta_message(frm: str, text: str, mid: str, name: str = "Ritu") -> dict:
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {
        "contacts": [{"wa_id": frm, "profile": {"name": name}}],
        "messages": [{"from": frm, "id": mid, "type": "text", "text": {"body": text}}]}}]}]}


# ───────────────────────── the first reply ─────────────────────────
async def test_a_website_inquiry_gets_one_first_reply_with_the_booking_link(client):
    c, uid, site = await owner_on("9834500100")
    await connect(uid)
    d = await turn_on(c, booking="link", booking_link=LINK, what="a site visit")
    assert d["connected"] and d["preview"]["line"] == f"Pick a time for a site visit here: {LINK}"
    lead = await inquiry(client, site)
    sent = await to_customer("919834500001")
    assert len(sent) == 1, sent                                   # ONE message to the customer
    assert sent[0]["via"] == "owner-demo" and sent[0]["kind"] == "lead_response"     # from the owner's own number
    assert sent[0]["params"] == ["Ritu", BUSINESS, f"Pick a time for a site visit here: {LINK}"]
    assert await db().outbox.count_documents({"kind": "instant"}) == 0                # the plain instant reply was not sent too
    lr = lead["lead_response"]
    assert lr["sent"] and 0 <= lr["seconds"] < 120 and lead["instant_reply"]["by"] == "lead_response"
    [entry] = [x async for x in db().lead_response_log.find({"ws": uid})]
    assert entry["lead_id"] == lead["_id"] and entry["sent"] and entry["seconds"] == lr["seconds"] and entry["to"] == "919834500001"
    thread = await db().wa_threads.find_one({"ws": uid, "phone": "919834500001"})
    assert thread and thread["last_dir"] == "out"                 # the chat continues in WhatsApp chats

    # the same number again today: no second first reply (same rule as the instant reply)
    again = await inquiry(client, site, message="Following up on the kitchen")
    assert again["lead_response"]["via"] == "skipped-repeat" and len(await to_customer("919834500001")) == 1
    stats = (await c.get("/api/lead-response")).json()["stats"]
    assert stats["inquiries"] == 2 and stats["first_replies"] == 1 and stats["bookings_asked"] == 1
    assert stats["median_seconds"] == lr["seconds"] and stats["p90_seconds"] == lr["seconds"] and stats["not_sent"] == {"skipped-repeat": 1}


async def test_times_mode_offers_two_times_inside_the_owners_hours_never_on_sunday(client, monkeypatch):
    # every half hour across a week: two whole-hour times, at least an hour away, inside the hours, 2 hours apart, no Sunday
    start = datetime(2026, 11, 2, 0, 7, tzinfo=IST)      # a Monday
    for i in range(7 * 48):
        at = start + timedelta(minutes=30 * i)
        s1, s2 = leadresponse.next_slots(at, (11, 18))
        for s in (s1, s2):
            assert s.weekday() != 6 and 11 <= s.hour < 18 and s.minute == 0, (at, s)
        assert s1 - at >= timedelta(hours=1) and s2 - s1 >= timedelta(hours=2)
    sat_evening = datetime(2026, 11, 7, 18, 30, tzinfo=IST)
    assert leadresponse.booking_line({"booking": "times", "what": "a quick call", "hours": "10-19"}, sat_evening) == \
        "Would Monday 9 Nov at 10 am or Monday 9 Nov at 12 noon suit you for a quick call? Just reply with the time."

    c, uid, site = await owner_on("9834500200")
    await connect(uid)
    await turn_on(c, booking="times", hours="11-18", what="a quick call")
    await inquiry(client, site, "Kabir Sethi", "98345 00002")
    [sent] = await to_customer("919834500002")
    assert sent["params"][:2] == ["Kabir", BUSINESS]
    assert re.fullmatch(r"Would (today|tomorrow|\w+day \d+ \w{3}) at \d+ (am|pm|noon) or (today|tomorrow|\w+day \d+ \w{3}) at \d+ (am|pm|noon) "
                        r"suit you for a quick call\? Just reply with the time\.", sent["params"][2].replace("12 noon", "12 noon")), sent["params"][2]
    assert "Sunday" not in sent["params"][2]


async def test_switched_off_the_instant_reply_works_exactly_as_before(client, monkeypatch):
    c, uid, site = await owner_on("9834500300")
    await connect(uid)
    assert (await c.get("/api/lead-response")).json()["settings"]["on"] is False      # off until the owner switches it on
    lead = await inquiry(client, site, "Meher Bajaj", "98345 00003")
    assert "lead_response" not in lead and lead["instant_reply"]["sent"] and "by" not in lead["instant_reply"]
    [sent] = await to_customer("919834500003")
    assert sent["kind"] == "instant" and sent["params"] == ["Meher", BUSINESS]
    assert await db().lead_response_log.count_documents({}) == 0
    # and the shared daily cap still holds for the instant reply
    monkeypatch.setattr(growth, "INSTANT_PER_DAY", 1)
    nxt = await inquiry(client, site, "Ishaan Wadia", "98345 00004")
    assert nxt["instant_reply"] == {**nxt["instant_reply"], "sent": False, "via": "skipped-cap"}


async def test_not_connected_or_no_template_sends_nothing_and_says_why(client):
    c, uid, site = await owner_on("9834500400")
    # it can't be switched on before the number and the template are there (on, it would stop the plain instant reply for nothing)
    assert (await c.put("/api/lead-response/settings", json={"booking": "link", "booking_link": LINK})).status_code == 200
    key = (await c.get("/api/lead-response")).json()["preview"]["key"]
    r = await c.put("/api/lead-response/settings", json={"on": True, "sample_key": key})
    assert r.status_code == 400 and "Connect your WhatsApp number" in r.text
    assert (await c.get("/api/lead-response")).json()["settings"]["on"] is False
    await connect(uid, lead_response_template=False)       # the number, but no "first reply with a booking" template
    assert (await c.put("/api/lead-response/settings", json={"on": True, "sample_key": key})).status_code == 400
    assert (await c.post("/api/lead-response/test")).status_code == 400

    # switched on with everything in place; then the number is disconnected: nothing goes, never from the hub's number
    await connect(uid)
    await turn_on(c)
    await connections.remove(uid, "whatsapp")
    lead = await inquiry(client, site, "Zoya Mirza", "98345 00005")
    assert lead["lead_response"]["sent"] is False and lead["lead_response"]["via"] == "not-connected"
    assert not (lead.get("instant_reply") or {}).get("sent") and await to_customer("919834500005") == []
    entry = await db().lead_response_log.find_one({"ws": uid})
    assert entry["sent"] is False and entry["via"] == "not-connected"
    notices = (await c.get("/api/me")).json()["notices"]
    assert any("Lead Response couldn't reply to Zoya Mirza" in n["text"] for n in notices)
    assert (await c.get("/api/lead-response")).json()["connected"] is False

    # the template removed since: the owner is told, and the plain instant reply still answers — one message, not none
    await connect(uid, lead_response_template=False)
    lead = await inquiry(client, site, "Arjun Talwar", "98345 00006")
    assert lead["lead_response"]["via"] == "no-template" and lead["instant_reply"]["sent"]
    sent = await to_customer("919834500006")
    assert len(sent) == 1 and sent[0]["kind"] == "instant" and sent[0]["via"] == "owner-demo"      # from the owner's own number
    kinds = [o["kind"] async for o in db().outbox.find({"channel": "whatsapp", "kind": {"$ne": "otp"}, "to": {"$ne": "919834500006"}})]
    assert set(kinds) <= {"lead_alert"}                    # otherwise only the owner's own alerts went out


async def test_an_email_inquiry_with_a_number_gets_it_only_from_a_verified_sender(client):
    from app.routers.public import save_lead
    c, uid, site = await owner_on("9834500500")
    await connect(uid)
    await turn_on(c)                                     # defaults: two times, "a quick call"
    owner = await db().users.find_one({"_id": uid})
    b = await db().businesses.find_one({"owner_id": uid})
    ok = await save_lead(site, owner, b, "Pallavi Gokhale", "+919834500007", "Kitchen quote please", "email",
                         email="pallavi@family.example", sender_verified=True)
    assert (await db().leads.find_one({"_id": ok["_id"]}))["lead_response"]["sent"]
    assert len(await to_customer("919834500007")) == 1
    no = await save_lead(site, owner, b, "Unknown Sender", "+919834500008", "Call me", "email", email="x@spam.example", sender_verified=False)
    assert (await db().leads.find_one({"_id": no["_id"]}))["lead_response"]["via"] == "email-not-verified"
    assert await to_customer("919834500008") == []
    pasted = await save_lead(site, owner, b, "Dev Anand Shah", "+919834500009", "Hi", "whatsapp", pasted=True)
    assert "lead_response" not in await db().leads.find_one({"_id": pasted["_id"]})   # a pasted WhatsApp chat is already on WhatsApp


# ───────────────────────── the WhatsApp AI: booking only ─────────────────────────
async def test_the_ai_chat_hands_over_a_price_question_without_a_number(client):
    c, uid, _ = await owner_on("9834500600", live=False)
    await connect(uid)
    await turn_on(c, booking="link", booking_link=LINK, what="a site visit")
    token = (await db().connections.find_one({"_id": uid}))["whatsapp"]["webhook_token"]
    r = await client.post(f"/api/hooks/whatsapp/{token}", json=meta_message("919834500010", "Hi, what is the price of a modular kitchen?", "lr1"))
    assert r.json() == {"ok": True, "handled": 1}
    t = await db().wa_threads.find_one({"ws": uid, "phone": "919834500010"})
    assert t["needs_person"] and "price" in t["handover_reason"].lower()
    out = await db().wa_messages.find_one({"thread_id": t["_id"], "dir": "out"})
    assert "₹" not in out["text"] and "1,85,000" not in out["text"] and not re.search(r"\d", out["text"].replace(LINK, ""))
    assert "share the price with you personally" in out["text"] and LINK in out["text"]
    alert = await db().outbox.find_one({"kind": "owner_alert"})
    assert alert and alert["to"] == "+919834500600"        # the owner is told, from the hub's number, to the owner only

    # a discount haggle in another chat: handed over too
    await client.post(f"/api/hooks/whatsapp/{token}", json=meta_message("919834500011", "Can you give some discount?", "lr2", "Om"))
    t2 = await db().wa_threads.find_one({"ws": uid, "phone": "919834500011"})
    assert t2["needs_person"] and not re.search(r"\d|₹", (await db().wa_messages.find_one({"thread_id": t2["_id"], "dir": "out"}))["text"]
                                                .replace(LINK, ""))
    # ready to book: the booking line, and a person confirms
    await client.post(f"/api/hooks/whatsapp/{token}", json=meta_message("919834500012", "I want to book a site visit", "lr3", "Neel"))
    t3 = await db().wa_threads.find_one({"ws": uid, "phone": "919834500012"})
    reply = (await db().wa_messages.find_one({"thread_id": t3["_id"], "dir": "out"}))["text"]
    assert t3["needs_person"] and LINK in reply

    # prices to a person switched off: the chat answers from the Business Brain as before (the owner wrote this price)
    await c.put("/api/lead-response/settings", json={"prices_to_person": False})
    await client.post(f"/api/hooks/whatsapp/{token}", json=meta_message("919834500013", "Hi, what is the price of a modular kitchen?", "lr4"))
    t4 = await db().wa_threads.find_one({"ws": uid, "phone": "919834500013"})
    assert "₹1,85,000" in (await db().wa_messages.find_one({"thread_id": t4["_id"], "dir": "out"}))["text"] and not t4["needs_person"]


async def test_the_booking_guard_and_the_unchanged_default(client):
    p = PROFILE
    history = [{"dir": "in", "text": "Do you make wardrobes?"}]
    # off (the default arguments): exactly the reply the chat always gave
    assert chat_tasks.mock_reply(p, history) == chat_tasks.mock_reply(p, history, False, "")
    # a live model that names a price anyway is overruled
    leaked = {"reply": "Yes! Wardrobes start at Rs 45,000 and we can do 10% off.", "intent": "sales", "hot": False, "handover": False,
              "why": "", "summary": "Wardrobes"}
    safe = chat_tasks.booking_guard(leaked, history, p, "")
    assert safe["handover"] and not re.search(r"\d", safe["reply"])
    fine = {"reply": "Yes, we make wardrobes to fit your room. Shall we book a site visit?", "intent": "sales", "hot": False, "handover": False,
            "why": "", "summary": "Wardrobes"}
    assert chat_tasks.booking_guard(fine, history, p, "") == fine
    for ask in ("kitna lagega?", "What are your rates", "please send a quotation", "best price?", "thoda kam karo"):
        assert chat_tasks.ASKS_PRICE.search(ask), ask
    assert not chat_tasks.ASKS_PRICE.search("Do you deliver to Thane?")


# ───────────────────────── plans, roles, the test message ─────────────────────────
async def test_plans_roles_settings_and_the_test_message(client):
    prog, _, _ = await owner_on("9834500700", "program", live=False)
    assert (await prog.get("/api/lead-response")).status_code == 403
    c, uid, _ = await owner_on("9834500701", live=False)
    d = (await c.get("/api/lead-response")).json()
    assert d["settings"] == {**leadresponse.DEFAULTS} and d["can_edit"] is True
    ch = {x["key"]: x for x in d["channels"]}
    assert ch["instagram_facebook"]["ok"] is False and "not set up yet" in ch["instagram_facebook"]["note"]
    assert ch["website"]["ok"] and ch["email"]["ok"] and ch["whatsapp"]["ok"] is False      # no number connected yet
    assert d["stats"] == {**d["stats"], "first_replies": 0, "median_seconds": None, "p90_seconds": None}

    bad = (("booking_link", "http://cal.example.in/x"), ("booking_link", "javascript:alert(1)"), ("hours", "19-10"), ("what", "{name}"))
    for k, v in bad:
        assert (await c.put("/api/lead-response/settings", json={k: v})).status_code == 400, (k, v)
    assert (await c.put("/api/lead-response/settings", json={"on": True, "booking": "link"})).status_code == 400   # needs the link
    assert (await c.put("/api/lead-response/settings", json={"on": True})).status_code == 409      # hasn't read the first reply
    assert (await c.put("/api/lead-response/settings", json={"on": True, "sample_key": "stale"})).status_code == 409
    assert (await c.put("/api/lead-response/settings", json={"booking": "fax"})).status_code == 422

    assert (await c.post("/api/lead-response/test")).status_code == 400          # not connected
    await connect(uid)
    await turn_on(c, booking="link", booking_link=LINK)
    r = await c.post("/api/lead-response/test")
    assert r.status_code == 200, r.text
    [sent] = [o async for o in db().outbox.find({"kind": "lead_response_test"})]
    assert sent["to"] == "919834500701" and sent["params"][1:] == [BUSINESS, f"Pick a time for a quick call here: {LINK}"]
    assert (await c.get("/api/lead-response")).json()["stats"]["first_replies"] == 0       # a test isn't counted

    staff, manager = await new_owner("9834500702"), await new_owner("9834500703")
    for person, role in ((staff, "staff"), (manager, "manager")):
        pid = (await person.get("/api/me")).json()["user"]["id"]
        await db().users.update_one({"_id": pid}, {"$set": {"team_of": uid, "team_role": role}})
    assert (await staff.get("/api/lead-response")).status_code == 403
    assert (await manager.get("/api/lead-response")).status_code == 200
    assert (await manager.put("/api/lead-response/settings", json={"on": False})).status_code == 403
    assert (await manager.post("/api/lead-response/test")).status_code == 403


# ───────────────────────── review fixes ─────────────────────────
async def test_a_double_submit_gets_one_first_reply_and_a_failed_send_frees_the_number(client, monkeypatch):
    import asyncio
    from app import integrations
    c, uid, site = await owner_on("9834500800")
    await connect(uid)
    await turn_on(c, booking="link", booking_link=LINK)
    # the same customer taps Send twice: both requests are in flight before either lead has its reply saved
    await asyncio.gather(inquiry(client, site, "Rhea Kulkarni", "98345 00020"), inquiry(client, site, "Rhea Kulkarni", "98345 00020"))
    assert len(await to_customer("919834500020")) == 1
    # the same, called straight on two leads whose patches aren't saved yet (Lead Response, then the plain instant reply)
    owner = await db().users.find_one({"_id": uid})
    b = await db().businesses.find_one({"owner_id": uid})
    st = await leadresponse.settings_of(uid)
    one, two = ({"_id": f"lead-{n}", "owner_id": uid, "name": "Yash Menon", "phone": "+919834500021", "source": "website"} for n in (1, 2))
    assert (await growth.lead_response_reply(owner, b, one, st))["lead_response"]["sent"]
    assert (await growth.lead_response_reply(owner, b, two, st))["lead_response"]["via"] == "skipped-repeat"
    three = {"_id": "lead-3", "owner_id": uid, "name": "Yash Menon", "phone": "+919834500021", "source": "website"}
    assert (await growth.instant_reply(owner, b, three))["via"] == "skipped-repeat"
    assert len(await to_customer("919834500021")) == 1
    # a send WhatsApp refused doesn't use up the number for the day
    real = integrations.wa_template

    async def refused(*a, **k):
        return {"sent": False, "via": "owner-whatsapp", "error": "WhatsApp didn't accept the message."}
    monkeypatch.setattr(integrations, "wa_template", refused)
    first = {"_id": "lead-4", "owner_id": uid, "name": "Isha Bhatt", "phone": "+919834500022", "source": "website"}
    assert (await growth.lead_response_reply(owner, b, first, st))["lead_response"]["sent"] is False
    monkeypatch.setattr(integrations, "wa_template", real)
    again = {"_id": "lead-5", "owner_id": uid, "name": "Isha Bhatt", "phone": "+919834500022", "source": "website"}
    assert (await growth.lead_response_reply(owner, b, again, st))["lead_response"]["sent"]
    # and the plain instant reply (Lead Response off) takes the same claim
    await c.put("/api/lead-response/settings", json={"on": False})
    four, five = ({"_id": f"lead-i{n}", "owner_id": uid, "name": "Om Rawat", "phone": "+919834500023", "source": "website"} for n in (1, 2))
    assert (await growth.instant_reply(owner, b, four))["sent"]
    assert (await growth.instant_reply(owner, b, five))["via"] == "skipped-repeat"
    assert len(await to_customer("919834500023")) == 1


async def test_booking_only_hands_over_any_amount_but_not_times_or_the_link(client):
    p = PROFILE
    line = f"Pick a time for a site visit here: {LINK}"
    asked = [{"dir": "in", "text": "Tell me about your kitchens"}]
    for reply in ("Our package is 1,85,000 including installation.", "Basic is 95000, premium is 1.4L.", "Around 2 lakh for the full flat.",
                  "Roughly 50k for the wardrobe.", "A villa project is about 1.2 cr.", "It costs Rs 900 per sq ft."):
        out = chat_tasks.booking_guard({"reply": reply, "intent": "sales", "hot": False, "handover": False, "why": "", "summary": ""}, asked, p, line)
        assert out["handover"] and not re.search(r"\d", out["reply"].replace(LINK, "")), reply
    for reply in (f"Sure! {line}", "We're open till 7 pm. Shall I book you at 11am tomorrow?", "Would 10:30 am on Monday suit you?",
                  "Yes, we do 2BHK and 3BHK kitchens. Shall we book a site visit?"):
        ok = {"reply": reply, "intent": "sales", "hot": False, "handover": False, "why": "", "summary": ""}
        assert chat_tasks.booking_guard(ok, asked, p, line) == ok, reply
    for q in ("What are your fees for a 2BHK?", "Send me the package details", "What's the tariff?", "How much is the payment?",
              "What amount should I keep ready?", "Please share a quotation"):
        assert chat_tasks.ASKS_PRICE.search(q), q
    # the deterministic mock path through the real chat: a fees question is handed over, no amount in the reply
    c, uid, _ = await owner_on("9834500900", live=False)
    await connect(uid)
    await turn_on(c, booking="link", booking_link=LINK)
    token = (await db().connections.find_one({"_id": uid}))["whatsapp"]["webhook_token"]
    await client.post(f"/api/hooks/whatsapp/{token}", json=meta_message("919834500030", "What are your fees for a 2BHK?", "f1"))
    t = await db().wa_threads.find_one({"ws": uid, "phone": "919834500030"})
    text = (await db().wa_messages.find_one({"thread_id": t["_id"], "dir": "out"}))["text"]
    assert t["needs_person"] and not re.search(r"\d", text.replace(LINK, ""))


async def test_switching_on_needs_the_wording_read_and_a_changed_booking_switches_it_off(client):
    c, uid, _ = await owner_on("9834501000", live=False)
    await connect(uid)
    await c.put("/api/lead-response/settings", json={"booking": "link", "booking_link": LINK, "what": "a site visit"})
    d = (await c.get("/api/lead-response")).json()
    assert d["preview"]["wording"] == f"Pick a time for a site visit here: {LINK}" and d["note"] == ""
    assert (await c.put("/api/lead-response/settings", json={"on": True})).status_code == 409
    assert (await c.put("/api/lead-response/settings", json={"on": True, "sample_key": d["preview"]["key"]})).json()["settings"]["on"]
    # prices to a person doesn't change the message: stays on
    assert (await c.put("/api/lead-response/settings", json={"prices_to_person": False})).json()["settings"]["on"] is True
    # a new booking link changes it: off, with a plain note, until the owner reads it again
    d = (await c.put("/api/lead-response/settings", json={"booking_link": "https://cal.example.in/sgi/call"})).json()
    assert d["settings"]["on"] is False and "booking message changed" in d["note"]
    for change in ({"what": "a showroom visit"}, {"booking": "times"}, {"booking": "times", "hours": "9-17"}):
        d = await turn_on(c)
        d = (await c.put("/api/lead-response/settings", json=change)).json()
        assert d["settings"]["on"] is False, change
    d = await turn_on(c)
    assert d["note"] == "" and d["preview"]["wording"].startswith("Would [time] or [another time] suit you for a showroom visit?")
