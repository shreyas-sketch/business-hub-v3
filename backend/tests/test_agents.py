"""Automations: follow-ups, the Send list, review requests, payment reminders, the week's plan, digest, monthly review.
The hub's own WhatsApp number never messages customers: below Growth Mentorship every customer message is tapped to send
from the owner's own WhatsApp; with a connected number (Growth Mentorship and up) it can go from that number."""
import asyncio
from datetime import datetime, timedelta
from urllib.parse import quote

from app.agents import hooks, jobs, runners, scheduler
from app.security import normalise_phone
from app.db import IST, db, month_key, now
from tests.conftest import PROFILE, go_live, new_owner

ADMIN = "9999900000"
BUSINESS = PROFILE["name"]


def ist(*a) -> datetime:
    return datetime(*a, tzinfo=IST)


async def owner_on(phone: str, plan: str, live: bool = True):
    c = await new_owner(phone)
    if live:
        site = await go_live(c)
    else:
        site = None
        assert (await c.put("/api/business", json=PROFILE)).status_code == 200
    uid = (await c.get("/api/me")).json()["user"]["id"]
    if plan != "free":
        admin = await new_owner(ADMIN)
        assert (await admin.patch(f"/api/admin/users/{uid}", json={"plan": plan})).status_code == 200
    return c, uid, site


QUIET = ("week_plan", "calendar_auto", "digest", "payment_reminder", "quote_followup", "customer_desk", "ceo_report", "monthly_review",
         "results", "recall")


async def connect_demo_whatsapp(uid: str):
    """A demo connection to the owner's own number: messages are written to the outbox (tests and laptops only)."""
    from app import connections
    await connections.save(uid, "whatsapp", {"provider": "demo", "tpl_instant": "instant_v1", "tpl_followup": "followup_v1",
                                             "tpl_review": "review_v1", "tpl_reminder": "reminder_v1", "tpl_customer": "customer_v1",
                                             "tpl_campaign": "campaign_v1"}, "test", "test")


async def quiet(c, *keys):
    """Switches off scheduled agents a test isn't about, so fake times can't wake them."""
    for k in keys:
        assert (await c.patch(f"/api/agents/{k}", json={"on": False})).status_code in (200, 403), k   # 403: not in this plan anyway


async def inquiry(client, site, name="Neha Joshi", phone="98340 00001", message="Need a modular kitchen for our new 2BHK. What's the price?"):
    r = await client.post(f"/s/{site['slug']}/inquiry", json={"name": name, "phone": phone, "message": message})
    assert r.status_code == 200, r.text
    return await db().leads.find_one({"phone": normalise_phone(phone)}, sort=[("created_at", -1)])


async def followup_jobs(lead_id):
    return [j async for j in db().agent_jobs.find({"agent": "followup", "payload.lead_id": lead_id}).sort("run_at", 1)]


async def pending(c):
    r = await c.get("/api/approvals?status=pending")
    assert r.status_code == 200, r.text
    return r.json()


# ───────────────────────── No customer messages from the hub's number ─────────────────────────
async def test_hub_number_only_alerts_the_owner_and_growth_replies_from_the_owners_number(client):
    c, uid, site = await owner_on("9834000100", "running")
    lead = await inquiry(client, site, "Vikram Shah", "+91 98340 00101", "Office fit-out")
    assert "instant_reply" not in lead and "auto_reply" not in lead
    kinds = [o["kind"] async for o in db().outbox.find({"channel": "whatsapp", "kind": {"$ne": "otp"}})]
    assert kinds == ["lead_alert"]   # the owner's alert, nothing to the customer

    g, guid, gsite = await owner_on("9834000110", "growth")
    lead = await inquiry(client, gsite, "Asha Rao", "98340 00111", "Wardrobe")
    assert lead["instant_reply"]["via"] == "not-connected" and not lead["instant_reply"]["sent"]
    await connect_demo_whatsapp(guid)
    lead = await inquiry(client, gsite, "Neha Joshi", "98340 00112", "Kitchen")
    assert lead["instant_reply"]["sent"]
    out = await db().outbox.find_one({"via": "owner-demo", "kind": "instant"})
    assert out["ws"] == guid and out["to"] == "919834000112" and out["params"] == ["Neha", BUSINESS]
    thread = await db().wa_threads.find_one({"ws": guid, "phone": "919834000112"})
    assert thread and thread["last_dir"] == "out"   # the chat starts in WhatsApp chats
    again = await inquiry(client, gsite, "Neha Joshi", "98340 00112", "Following up")
    assert again["instant_reply"]["via"] == "skipped-repeat"
    await quiet(g, "instant_reply")
    off = await inquiry(client, gsite, "Kiran Shah", "98340 00113", "Bed")
    assert "instant_reply" not in off


# ───────────────────────── Follow-up agent ─────────────────────────
async def test_followups_scheduled_in_working_hours_india_time(client):
    c, uid, site = await owner_on("9834000200", "program")
    lead = await inquiry(client, site)
    js = await followup_jobs(lead["_id"])
    assert [j["payload"]["n"] for j in js] == [1, 2, 3] and all(j["status"] == "scheduled" for j in js)
    for j, d in zip(js, (1, 3, 7)):
        assert abs((j["run_at"] - jobs.working_hours(lead["created_at"] + timedelta(days=d))).total_seconds()) < 1
        assert 10 <= j["run_at"].astimezone(IST).hour < 19

    # a lead at 9:30pm: every follow-up moves to 10am the next morning; one at 2pm keeps its time
    night = await hooks.schedule_followups(uid, {"_id": "night-lead", "created_at": ist(2026, 10, 5, 21, 30)})
    assert [j["run_at"].astimezone(IST) for j in night] == [ist(2026, 10, 7, 10), ist(2026, 10, 9, 10), ist(2026, 10, 13, 10)]
    day = await hooks.schedule_followups(uid, {"_id": "day-lead", "created_at": ist(2026, 10, 5, 14, 0)})
    assert [j["run_at"].astimezone(IST) for j in day] == [ist(2026, 10, 6, 14), ist(2026, 10, 8, 14), ist(2026, 10, 12, 14)]
    early = await hooks.schedule_followups(uid, {"_id": "early-lead", "created_at": ist(2026, 10, 5, 6, 15)})
    assert early[0]["run_at"].astimezone(IST) == ist(2026, 10, 6, 10)
    assert await hooks.schedule_followups(uid, {"_id": "day-lead", "created_at": ist(2026, 10, 5, 14, 0)}) == []  # never twice


async def test_followups_cancelled_when_won_or_lost(client):
    c, uid, site = await owner_on("9834000300", "program")
    won = await inquiry(client, site, "Neha Joshi", "98340 00301")
    lost = await inquiry(client, site, "Rahul Mehta", "98340 00302")
    js = await followup_jobs(won["_id"])
    await scheduler.tick(at=js[0]["run_at"] + timedelta(minutes=1))   # follow-up 1 for both leads
    await scheduler.tick(at=js[1]["run_at"] + timedelta(minutes=1))   # follow-up 2 replaces the unanswered follow-up 1
    mine = [a for a in await pending(c) if a["lead_id"] == won["_id"]]
    assert [a["title"] for a in mine] == ["Follow-up 2 of 3 to Neha Joshi"]
    assert await db().approvals.count_documents({"lead_id": won["_id"], "status": "expired"}) == 1

    assert (await c.patch(f"/api/leads/{won['_id']}", json={"status": "won"})).status_code == 200
    assert (await c.patch(f"/api/leads/{lost['_id']}", json={"status": "lost"})).status_code == 200
    for lid in (won["_id"], lost["_id"]):
        statuses = [j["status"] for j in await followup_jobs(lid)]
        assert statuses == ["done", "done", "cancelled"]
        assert await db().approvals.count_documents({"lead_id": lid, "status": "pending"}) == 0
        assert await db().approvals.count_documents({"lead_id": lid, "status": "expired"}) == 2
    await scheduler.tick(at=js[2]["run_at"] + timedelta(minutes=1))
    assert await pending(c) == []


async def test_followup_drafted_then_tapped_to_send_or_sent_from_the_owners_number(client):
    c, uid, site = await owner_on("9834000400", "program")
    await quiet(c, *QUIET)   # only the follow-ups: on the 25th the content calendar would write next month's posts too
    lead = await inquiry(client, site)
    js = await followup_jobs(lead["_id"])
    runs_before = (await c.get("/api/me")).json()["runs"]["used"]

    await scheduler.tick(at=js[0]["run_at"] + timedelta(minutes=1))
    [a] = await pending(c)
    assert a["agent"] == "followup" and a["agent_name"] == "Follow-up automation" and a["kind"] == "followup"
    assert a["title"] == "Follow-up 1 of 3 to Neha Joshi" and a["to_phone"] == "+919834000001"
    assert a["text"].startswith("Hello Neha,") and BUSINESS in a["text"] and "modular kitchen" in a["text"]
    me = (await c.get("/api/me")).json()
    assert me["runs"]["used"] == runs_before + 1 and me["approvals_waiting"] == 1
    assert (await db().agent_jobs.find_one({"_id": js[0]["_id"]}))["status"] == "done"
    assert "ready in your Send list" in (await c.get("/api/agents/activity")).json()[0]["text"]
    assert a["own_link"] == f"https://wa.me/919834000001?text={quote(a['text'])}"
    assert (await db().leads.find_one({"_id": lead["_id"]}))["status"] == "new"  # nothing sent yet

    edited = "Hello Neha, did you get a chance to look at kitchen designs? Happy to visit this week."
    r = await c.patch(f"/api/approvals/{a['id']}", json={"text": edited + "\n\n"})
    assert r.json()["text"] == edited
    r = await c.post(f"/api/approvals/{a['id']}/send")   # no number connected (and not on Growth Mentorship): tap to send instead
    assert r.status_code == 400 and "Send on WhatsApp" in r.json()["detail"]
    assert await db().outbox.count_documents({"kind": "followup"}) == 0
    r = await c.post(f"/api/approvals/{a['id']}/own")
    assert r.json()["link"] == f"https://wa.me/919834000001?text={quote(edited)}" and r.json()["approval"]["via"] == "own"
    assert (await c.post(f"/api/approvals/{a['id']}/send")).status_code == 409
    assert (await c.post(f"/api/approvals/{a['id']}/skip")).status_code == 409
    assert (await c.patch(f"/api/approvals/{a['id']}", json={"text": "too late"})).status_code == 409
    after = await db().leads.find_one({"_id": lead["_id"]})
    assert after["status"] == "contacted" and after["first_action_at"]

    # on Growth Mentorship with the owner's number connected, "send" goes from that number (a template outside 24 hours)
    admin = await new_owner(ADMIN)
    await admin.patch(f"/api/admin/users/{uid}", json={"plan": "growth"})
    await quiet(c, *QUIET)
    await connect_demo_whatsapp(uid)
    await scheduler.tick(at=js[1]["run_at"] + timedelta(minutes=1))
    [b] = await pending(c)
    r = await c.post(f"/api/approvals/{b['id']}/send")
    assert r.status_code == 200 and r.json()["approval"]["via"] == "owner-whatsapp"
    out = await db().outbox.find_one({"kind": "followup", "via": "owner-demo"})
    assert out["to"] == "919834000001" and out["params"] == ["Neha", BUSINESS, b["text"]]

    await scheduler.tick(at=js[2]["run_at"] + timedelta(minutes=1))
    [last] = await pending(c)
    assert last["title"] == "Follow-up 3 of 3 to Neha Joshi" and "one last note" in last["text"]
    assert (await c.post(f"/api/approvals/{last['id']}/skip")).json()["approval"]["status"] == "skipped"
    done = (await c.get("/api/approvals?status=done")).json()
    assert sorted(d["status"] + ":" + (d["via"] or "") for d in done) == ["sent:own", "sent:owner-whatsapp", "skipped:"]
    assert (await c.get("/api/me")).json()["approvals_waiting"] == 0


async def test_followup_skipped_when_lead_already_closed_or_deleted(client):
    c, uid, site = await owner_on("9834000500", "program")
    await quiet(c, *QUIET)   # only the follow-ups: on the 25th the content calendar would write next month's posts too
    a = await inquiry(client, site, "Neha Joshi", "98340 00501")
    b = await inquiry(client, site, "Rahul Mehta", "98340 00502")
    await db().leads.update_one({"_id": a["_id"]}, {"$set": {"status": "won"}})  # changed without the hook
    await db().leads.delete_one({"_id": b["_id"]})
    job_a, job_b = (await followup_jobs(a["_id"]))[0], (await followup_jobs(b["_id"]))[0]
    await scheduler.tick(at=job_a["run_at"] + timedelta(minutes=1))
    assert await db().approvals.count_documents({}) == 0
    ja, jb = await db().agent_jobs.find_one({"_id": job_a["_id"]}), await db().agent_jobs.find_one({"_id": job_b["_id"]})
    assert ja["status"] == "skipped" and "already marked won" in ja["note"]
    assert jb["status"] == "skipped" and "deleted" in jb["note"]
    assert (await c.get("/api/me")).json()["runs"]["used"] == 2  # brand + site only: skipped jobs use no AI run

    # switched off: due jobs are skipped, not sent
    lead = await inquiry(client, site, "Asha Rao", "98340 00503")
    await quiet(c, "followup")
    j = (await followup_jobs(lead["_id"]))[0]
    await scheduler.tick(at=j["run_at"] + timedelta(minutes=1))
    assert (await db().agent_jobs.find_one({"_id": j["_id"]}))["note"] == "The agent was switched off."
    assert await db().approvals.count_documents({}) == 0


async def test_act_on_its_own_only_with_legacyworkforce(client):
    c, uid, site = await owner_on("9834000600", "running")
    r = await c.patch("/api/agents/followup", json={"autonomy": "act"})
    assert r.status_code == 403 and r.json()["detail"]["feature"] == "agents_act"
    assert (await c.patch("/api/agents/digest", json={"autonomy": "act"})).status_code == 400  # nothing to approve

    admin = await new_owner(ADMIN)
    await admin.patch(f"/api/admin/users/{uid}", json={"plan": "office"})
    await quiet(c, *QUIET)
    await connect_demo_whatsapp(uid)
    r = await c.patch("/api/agents/followup", json={"autonomy": "act"})
    assert r.status_code == 200 and r.json()["autonomy"] == "act"
    lead = await inquiry(client, site)
    js = await followup_jobs(lead["_id"])
    await scheduler.tick(at=js[0]["run_at"] + timedelta(minutes=1))
    assert await pending(c) == []
    sent = await db().approvals.find_one({"lead_id": lead["_id"]})
    assert sent["status"] == "sent" and sent["via"] == "owner-whatsapp" and sent["decided_by"] == "agent"
    out = await db().outbox.find_one({"kind": "followup", "via": "owner-demo"})
    assert out["params"][:2] == ["Neha", BUSINESS] and out["params"][2] == sent["text"]
    assert "on its own" in (await c.get("/api/agents/activity")).json()[0]["text"]

    # back on a plan without agents_act: the stored "act" no longer applies
    await admin.patch(f"/api/admin/users/{uid}", json={"plan": "running"})
    assert next(a for a in (await c.get("/api/agents")).json()["agents"] if a["key"] == "followup")["autonomy"] == "ask"
    await scheduler.tick(at=js[1]["run_at"] + timedelta(minutes=1))
    assert [a["title"] for a in await pending(c)] == ["Follow-up 2 of 3 to Neha Joshi"]


# ───────────────────────── Review request agent ─────────────────────────
async def test_review_request_needs_a_review_link(client):
    c, uid, site = await owner_on("9834000700", "program")
    await quiet(c, "digest")
    first = await inquiry(client, site, "Neha Joshi", "98340 00701")
    second = await inquiry(client, site, "Rahul Mehta", "98340 00702")
    for lead in (first, second):
        await c.patch(f"/api/leads/{lead['_id']}", json={"status": "won"})
    job = await db().agent_jobs.find_one({"_id": f"review:{first['_id']}"})
    assert job["status"] == "scheduled" and 10 <= job["run_at"].astimezone(IST).hour < 19
    assert job["run_at"] - now() > timedelta(days=1, hours=23)

    await scheduler.tick(at=job["run_at"] + timedelta(hours=1))
    assert await db().approvals.count_documents({"kind": "review"}) == 0
    assert (await db().agent_jobs.find_one({"_id": job["_id"]}))["status"] == "skipped"
    notices = [n async for n in db().notices.find({"user_id": uid, "text": {"$regex": "review link"}})]
    assert len(notices) == 1  # once, not once per customer

    link = "https://g.page/r/shree-ganesh/review"
    assert (await c.put("/api/business", json={**PROFILE, "review_link": link})).status_code == 200
    third = await inquiry(client, site, "Mrs. Kulkarni", "98340 00703")
    fourth = await inquiry(client, site, "Asha Rao", "98340 00704")
    await c.patch(f"/api/leads/{third['_id']}", json={"status": "won"})
    await c.patch(f"/api/leads/{fourth['_id']}", json={"status": "won"})
    await c.patch(f"/api/leads/{fourth['_id']}", json={"status": "lost"})  # changed their mind: no review request
    assert (await db().agent_jobs.find_one({"_id": f"review:{fourth['_id']}"}))["status"] == "cancelled"
    job = await db().agent_jobs.find_one({"_id": f"review:{third['_id']}"})
    await scheduler.tick(at=job["run_at"] + timedelta(minutes=1))
    [a] = await pending(c)
    assert a["kind"] == "review" and a["agent_name"] == "Review requests" and link in a["text"] and a["text"].startswith("Hello Mrs. Kulkarni,")
    assert a["params"] == ["Mrs. Kulkarni", BUSINESS, link]   # the template's parameters, for a connected number
    r = await c.post(f"/api/approvals/{a['id']}/own")
    assert r.status_code == 200 and r.json()["link"].startswith("https://wa.me/919834000703?text=")
    assert (await db().leads.find_one({"_id": third["_id"]}))["review_requested_at"]


# ───────────────────────── Payment reminder agent ─────────────────────────
async def test_payment_reminders_every_three_days_at_most_four_never_duplicated(client):
    c, uid, site = await owner_on("9834000800", "running", live=False)
    await quiet(c, *[k for k in QUIET if k != "payment_reminder"])
    await c.put("/api/business", json={**PROFILE, "payment_note": "Pay by UPI to sgi@okicici."})
    owed = (await c.post("/api/money", json={"customer": "Ramesh Patel", "phone": "98340 00801", "amount": 12500, "due_date": "2026-10-01",
                                              "invoice_no": "SGI-104"})).json()
    later = (await c.post("/api/money", json={"customer": "Kiran Shah", "phone": "98340 00802", "amount": 9000, "due_date": "2026-12-01"})).json()
    no_phone = (await c.post("/api/money", json={"customer": "Walk-in", "amount": 500, "due_date": "2026-09-01"})).json()

    before_ten = await scheduler.tick(at=ist(2026, 10, 5, 9, 50))
    assert await db().approvals.count_documents({}) == 0 and before_ten["scheduled"] == 0
    await asyncio.gather(scheduler.tick(at=ist(2026, 10, 5, 10, 5)), scheduler.tick(at=ist(2026, 10, 5, 10, 5)))
    await scheduler.tick(at=ist(2026, 10, 5, 15, 0))
    [a] = await pending(c)
    assert a["kind"] == "reminder" and a["due_id"] == owed["id"] and a["title"] == "Reminder 1 of 4 to Ramesh Patel — ₹12,500"
    assert a["text"] == ("Hello Ramesh, a gentle reminder from Shree Ganesh Interiors that ₹12,500 for invoice SGI-104 was due on 1 Oct 2026. "
                         "Pay by UPI to sgi@okicici. If you've already paid, please ignore this message. Thank you!")
    due = await db().dues.find_one({"_id": owed["id"]})
    assert due["reminders_sent"] == 1 and due["last_reminded_at"] == ist(2026, 10, 5, 10, 5)

    assert a["params"] == ["Ramesh", BUSINESS, "₹12,500", "1 Oct 2026", "Pay by UPI to sgi@okicici."] and a["money"]
    r = await c.post(f"/api/approvals/{a['id']}/own")
    assert r.status_code == 200 and r.json()["approval"]["via"] == "own"

    await scheduler.tick(at=ist(2026, 10, 6, 10, 5))   # only a day later: not yet
    await scheduler.tick(at=ist(2026, 10, 7, 10, 5))   # two days later: not yet
    assert await db().approvals.count_documents({}) == 1
    for day in (8, 11, 14, 17, 20):
        await scheduler.tick(at=ist(2026, 10, day, 10, 5))
    assert (await db().dues.find_one({"_id": owed["id"]}))["reminders_sent"] == 4
    reminders = [x async for x in db().approvals.find({"due_id": owed["id"]}).sort("created_at", 1)]
    assert [x["title"].split(" to ")[0] for x in reminders] == ["Reminder 1 of 4", "Reminder 2 of 4", "Reminder 3 of 4", "Reminder 4 of 4"]
    assert [x["status"] for x in reminders] == ["sent", "expired", "expired", "pending"]  # a newer reminder replaces an unanswered one
    assert await db().approvals.count_documents({"due_id": {"$in": [later["id"], no_phone["id"]]}}) == 0
    assert "no phone number" in (await db().agent_settings.find_one({"_id": f"{uid}:payment_reminder"}))["last_note"]

    # paid: its waiting reminder expires and no more are written
    await c.patch(f"/api/money/{owed['id']}", json={"status": "paid"})
    assert await pending(c) == []
    await c.patch(f"/api/money/{owed['id']}", json={"status": "due", "due_date": "2026-10-25"})  # a new due date starts a fresh round
    await scheduler.tick(at=ist(2026, 10, 26, 10, 5))
    assert [x["title"] for x in await pending(c)] == ["Reminder 1 of 4 to Ramesh Patel — ₹12,500"]


# ───────────────────────── Scheduled agents ─────────────────────────
async def test_week_planned_every_monday_once(client):
    c, uid, site = await owner_on("9834000900", "lite", live=False)
    await quiet(c, "calendar_auto")
    await scheduler.tick(at=ist(2026, 10, 12, 7, 55))  # Monday, before 8am
    assert await db().weeks.count_documents({"ws": uid}) == 0
    await asyncio.gather(*(scheduler.tick(at=ist(2026, 10, 12, 8, 5)) for _ in range(3)))  # several workers at once
    await scheduler.tick(at=ist(2026, 10, 13, 9, 0))   # Tuesday, same week
    [w] = [x async for x in db().weeks.find({"ws": uid})]
    assert w["week"] == "2026-W42" and len(w["posts"]) == 7 and len(w["actions"]) == 3 and w["posts"][0]["day"] == "Monday"
    me = (await c.get("/api/me")).json()
    assert any("Your week is ready" in n["text"] for n in me["notices"])
    alert = await db().outbox.find_one({"kind": "week_ready"})
    assert alert["to"] == "+919834000900" and alert["params"][0] == BUSINESS   # an owner alert from the hub's number
    agent = next(a for a in (await c.get("/api/agents")).json()["agents"] if a["key"] == "week_plan")
    assert agent["last_status"] == "done" and "7 posts" in agent["last_note"] and agent["next_run_at"]
    await scheduler.tick(at=ist(2026, 10, 19, 8, 1))  # next Monday
    assert await db().weeks.count_documents({"ws": uid}) == 2

    free, free_uid, _ = await owner_on("9834000901", "free", live=False)  # not on Membership: never runs
    await scheduler.tick(at=ist(2026, 10, 26, 8, 5))
    assert await db().weeks.count_documents({"ws": free_uid}) == 0


async def test_morning_digest_on_whatsapp(client):
    c, uid, site = await owner_on("9834001000", "growth")
    await quiet(c, *[k for k in QUIET if k != "digest"])
    await inquiry(client, site, "Neha Joshi", "98340 01001")
    today = now().astimezone(IST)
    tomorrow = (today + timedelta(days=1)).replace(hour=8, minute=35, second=0, microsecond=0)
    old = (today - timedelta(days=5)).strftime("%Y-%m-%d")
    await c.post("/api/money", json={"customer": "Ramesh Patel", "phone": "9834001002", "amount": 5000, "due_date": old})
    await db().tasks.insert_many([{"_id": "t1", "ws": uid, "title": "Call supplier", "status": "open", "due": tomorrow.strftime("%Y-%m-%d")},
                                  {"_id": "t2", "ws": uid, "title": "Done one", "status": "done", "due": tomorrow.strftime("%Y-%m-%d")}])
    await scheduler.tick(at=tomorrow.replace(hour=8, minute=20))
    assert await db().outbox.count_documents({"kind": "digest"}) == 0
    await scheduler.tick(at=tomorrow)
    await scheduler.tick(at=tomorrow + timedelta(minutes=30))
    [d] = [x async for x in db().outbox.find({"kind": "digest"})]
    line = "1 new lead yesterday · 1 waiting for a first reply · 0 in your Send list · ₹5,000 overdue · 1 task due today"
    assert d["to"] == "+919834001000" and d["params"] == [BUSINESS, line]
    assert any(n["text"] == f"Good morning. {line}." for n in (await c.get("/api/me")).json()["notices"])


async def test_monthly_review_on_the_first_with_last_months_data(client):
    c, uid, site = await owner_on("9834001100", "growth")
    await quiet(c, *[k for k in QUIET if k != "monthly_review"])
    oct5 = ist(2026, 10, 5, 11)
    await db().leads.insert_many([
        {"_id": "l1", "owner_id": uid, "name": "A", "phone": "9834001101", "status": "won", "created_at": oct5, "first_action_at": oct5 + timedelta(hours=1)},
        {"_id": "l2", "owner_id": uid, "name": "B", "phone": "9834001102", "status": "lost", "created_at": oct5, "first_action_at": oct5 + timedelta(hours=3)},
        {"_id": "l3", "owner_id": uid, "name": "C", "phone": "9834001103", "status": "new", "created_at": oct5},
        {"_id": "l4", "owner_id": uid, "name": "D", "phone": "9834001104", "status": "won", "created_at": ist(2026, 9, 20)},  # last month's
    ])
    await db().site_views.insert_many([{"_id": "v1", "site_id": site["id"], "ws": uid, "day": "2026-10-02", "n": 70},
                                       {"_id": "v2", "site_id": site["id"], "ws": uid, "day": "2026-10-30", "n": 50},
                                       {"_id": "v3", "site_id": site["id"], "ws": uid, "day": "2026-11-01", "n": 999}])
    await db().goals.insert_one({"_id": "g1", "ws": uid, "title": "40 kitchens this year", "metric": "kitchens", "target": 40, "unit": "",
                                 "start": "2026-04-01", "end": "2027-03-31",
                                 "checkins": [{"at": ist(2026, 9, 30), "value": 8, "note": ""}, {"at": ist(2026, 10, 28), "value": 10, "note": ""},
                                              {"at": ist(2026, 11, 3), "value": 13, "note": ""}]})
    await db().scores.insert_many([{"_id": "s1", "ws": uid, "token": "tok-s1", "at": ist(2026, 9, 3), "score": 48, "fixes": [{"about": "Follow-up"}]},
                                   {"_id": "s2", "ws": uid, "token": "tok-s2", "at": ist(2026, 10, 20), "score": 62, "fixes": [{"about": "Reply speed"}]}])
    await db().tasks.insert_many([{"_id": "t1", "ws": uid, "title": "x", "status": "open", "due": "2026-10-20"},
                                  {"_id": "t2", "ws": uid, "title": "y", "status": "done", "done_at": ist(2026, 10, 9)}])
    await db().dues.insert_many([
        {"_id": "d1", "ws": uid, "customer": "R", "amount": 1_250_000, "due_date": "2026-10-10", "status": "due", "reminders_sent": 0, "last_reminded_at": None},
        {"_id": "d2", "ws": uid, "customer": "S", "amount": 300_000, "due_date": "2026-10-01", "status": "paid", "paid_at": ist(2026, 10, 15)}])

    owner = await db().users.find_one({"_id": uid})
    data = await runners.month_data(owner, ist(2026, 10, 1), ist(2026, 11, 1))
    assert data["numbers"] == {"leads": 3, "won": 1, "lost": 1, "waiting": 1, "median first reply (hours)": 2.0, "website views": 120,
                               "tasks open": 1, "tasks done": 1, "tasks overdue": 1, "overdue_amount": "₹12,500", "collected": "₹3,000"}
    assert data["goals"] == [{"title": "40 kitchens this year", "metric": "kitchens", "target": 40, "unit": "", "latest": 10.0,
                              "progress_percent": 25, "ends": "2027-03-31"}]
    assert data["business_score"] == {"score": 62, "taken": "2026-10-20", "fixes": ["Reply speed"]}

    await scheduler.tick(at=ist(2026, 10, 31, 23, 0))
    assert await db().kits.count_documents({"kind": "review"}) == 0
    await asyncio.gather(scheduler.tick(at=ist(2026, 11, 1, 9, 5)), scheduler.tick(at=ist(2026, 11, 1, 9, 5)))
    await scheduler.tick(at=ist(2026, 11, 2, 9, 5))
    [kit] = [k async for k in db().kits.find({"kind": "review"})]
    assert kit["by_agent"] == "Monthly review agent" and kit["title"] == "Review — October 2026" and kit["ws"] == uid
    assert kit["content"]["headline"] == "October 2026: 3 inquiries, 1 won." and kit["content"]["numbers"]["Website views"] == "120"
    listed = (await c.get("/api/kits/review")).json()
    assert listed[0]["by_agent"] == "Monthly review agent"
    assert any("review for October 2026 is ready" in n["text"] for n in (await c.get("/api/me")).json()["notices"])


async def test_monthly_review_run_now_endpoint(client):
    c, uid, site = await owner_on("9834001200", "growth", live=False)
    r = await c.post("/api/agents/monthly_review/run")
    assert r.status_code == 200, r.text
    month = now().astimezone(IST).strftime("%B %Y")
    assert r.json()["title"] == f"Review — {month} (so far)" and r.json()["by_agent"] == "Monthly review agent"
    assert (await c.get("/api/kits/review")).json()[0]["id"] == r.json()["id"]
    assert "on request" in (await c.get("/api/agents/activity")).json()[0]["text"]

    running, _, _ = await owner_on("9834001201", "running", live=False)
    r = await running.post("/api/agents/monthly_review/run")
    assert r.status_code == 403 and r.json()["detail"]["feature"] == "monthly_review"
    manager = await new_owner("9834001202")
    await db().users.update_one({"_id": (await manager.get("/api/me")).json()["user"]["id"]}, {"$set": {"team_of": uid, "team_role": "manager"}})
    assert (await manager.post("/api/agents/monthly_review/run")).status_code == 403  # the owner's call


async def test_agents_pause_when_ai_runs_run_out(client):
    c, uid, site = await owner_on("9834001300", "growth", live=False)
    await quiet(c, *[k for k in QUIET if k not in ("week_plan", "monthly_review")])
    await db().usage.update_one({"user_id": uid, "month": month_key()}, {"$set": {"used": 3000}}, upsert=True)
    await scheduler.tick(at=ist(2026, 6, 1, 9, 5))  # a Monday that is also the 1st: the week's plan and the monthly review both due
    log = [e async for e in db().agent_log.find({"ws": uid})]
    assert sorted(e["agent"] for e in log) == ["monthly_review", "week_plan"]
    assert all(e["text"] == "paused: no AI runs left" and e["status"] == "paused" for e in log)
    paused = [n async for n in db().notices.find({"user_id": uid, "text": {"$regex": "paused"}})]
    assert len(paused) == 1  # one notice a day, however many agents paused
    assert await db().outputs.count_documents({"user_id": uid}) == 0 and await db().kits.count_documents({}) == 0
    agents = {a["key"]: a for a in (await c.get("/api/agents")).json()["agents"]}
    assert agents["week_plan"]["last_status"] == "paused"

    # a follow-up that falls due while paused is skipped, not retried every minute
    lead = {"_id": "lead-x", "owner_id": uid, "name": "Neha Joshi", "phone": "9834001301", "message": "Kitchen", "status": "new", "created_at": now()}
    await db().leads.insert_one(lead)
    [job, *_] = await hooks.schedule_followups(uid, lead)
    await scheduler.tick(at=job["run_at"] + timedelta(minutes=1))
    j = await db().agent_jobs.find_one({"_id": job["_id"]})
    assert j["status"] == "skipped" and j["note"] == "paused: no AI runs left"
    assert len([n async for n in db().notices.find({"user_id": uid, "text": {"$regex": "paused"}})]) == 2  # a new day, a new notice
    await scheduler.tick(at=job["run_at"] + timedelta(minutes=2))
    assert len([n async for n in db().notices.find({"user_id": uid, "text": {"$regex": "paused"}})]) == 2


async def test_busy_ai_retries_the_job_later(client, monkeypatch):
    from app.ai import agent_tasks
    from app.ai.provider import AIError
    c, uid, site = await owner_on("9834001400", "program")
    await quiet(c, *QUIET)   # only the follow-ups: on the 25th the content calendar would write next month's posts too
    lead = await inquiry(client, site)
    job = (await followup_jobs(lead["_id"]))[0]

    async def busy(*a, **k):
        raise AIError("overloaded")
    monkeypatch.setattr(agent_tasks, "followup", busy)
    at = job["run_at"] + timedelta(minutes=1)
    await scheduler.tick(at=at)
    j = await db().agent_jobs.find_one({"_id": job["_id"]})
    assert j["status"] == "scheduled" and j["attempts"] == 1 and j["run_at"] == at + timedelta(minutes=30)
    assert (await c.get("/api/me")).json()["runs"]["used"] == 2  # refunded
    monkeypatch.undo()
    await scheduler.tick(at=at + timedelta(minutes=31))
    assert (await db().agent_jobs.find_one({"_id": job["_id"]}))["status"] == "done" and len(await pending(c)) == 1


# ───────────────────────── Agents API ─────────────────────────
async def test_agents_page_shows_every_agent_and_what_unlocks_it(client):
    c, uid, site = await owner_on("9834001500", "lite", live=False)
    body = (await c.get("/api/agents")).json()
    agents = {a["key"]: a for a in body["agents"]}
    assert list(agents) == ["week_plan", "calendar_auto", "followup", "review_request", "digest", "payment_reminder", "quote_followup",
                            "customer_desk", "instant_reply", "wa_sales", "telecaller", "recall", "ceo_report", "monthly_review", "results",
                            "meeting_actions", "office_standup"]
    assert agents["week_plan"]["available"] and agents["week_plan"]["on"] and agents["week_plan"]["tier_name"] == "Membership"
    assert not agents["followup"]["available"] and agents["followup"]["tier"] == "program" and agents["followup"]["approvals"]
    assert agents["digest"]["tier_name"] == "Action Program" and agents["meeting_actions"]["info_only"] and agents["wa_sales"]["info_only"]
    assert body["can_act"] is False and body["act_tier_name"] == "LegacyWorkforce"
    r = await c.patch("/api/agents/followup", json={"on": False})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "locked"
    assert (await c.patch("/api/agents/meeting_actions", json={"on": False})).status_code == 400
    assert (await c.patch("/api/agents/nope", json={"on": False})).status_code == 404
    r = await c.patch("/api/agents/week_plan", json={"on": False})
    assert r.status_code == 200 and r.json()["on"] is False

    free = await new_owner("9834001501")
    assert (await free.get("/api/agents")).status_code == 200  # every plan sees what unlocks
    assert (await free.get("/api/approvals")).status_code == 403

    staff = await new_owner("9834001502")
    await db().users.update_one({"_id": (await staff.get("/api/me")).json()["user"]["id"]}, {"$set": {"team_of": uid, "team_role": "staff"}})
    admin = await new_owner(ADMIN)
    await admin.patch(f"/api/admin/users/{uid}", json={"plan": "growth"})
    assert (await staff.get("/api/agents")).status_code == 403
    assert (await staff.get("/api/approvals")).status_code == 403
    manager = await new_owner("9834001503")
    await db().users.update_one({"_id": (await manager.get("/api/me")).json()["user"]["id"]}, {"$set": {"team_of": uid, "team_role": "manager"}})
    assert (await manager.get("/api/agents")).status_code == 200 and (await manager.get("/api/approvals")).status_code == 200
    assert (await manager.patch("/api/agents/digest", json={"on": False})).status_code == 403  # switches are the owner's


def test_custom_agents_can_be_registered():
    from app.agents import catalog

    async def standup(owner, run):
        return "Standup written."
    spec = catalog.register("standup_test", {"name": "Standup agent", "what": "Writes the team's standup.", "when": "Weekdays at 9am",
                                             "feature": "agentic_office", "trigger": "schedule",
                                             "schedule": {"every": "day", "at": "09:00", "grace_hours": 2}}, standup)
    try:
        assert catalog.AGENTS["standup_test"] is spec and spec.runner is standup
        assert catalog.due_slot(spec.schedule, ist(2026, 10, 8, 9, 30))[0] == "2026-10-08"
        assert catalog.due_slot(spec.schedule, ist(2026, 10, 8, 11, 0)) is None
    finally:
        catalog.AGENTS.pop("standup_test")
