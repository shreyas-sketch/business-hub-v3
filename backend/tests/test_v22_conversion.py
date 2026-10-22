"""v2.2 Conversion: the funnel leak finder and the follow-ups in the ₹1,999 Membership (the admin's one-click preset), the
leak finder starting from inquiries when visitors aren't known, follow-ups sent from the Inquiries page (on the Membership
only once the owner switches them on; event accounts start with them on), and both as live event demos on the speaker
screen. Every business, person and number here is invented."""
from datetime import timedelta

from app import event as ev, plans
from app.agents import scheduler
from app.db import db, now
from app.routers import inquiries
from tests.conftest import PROFILE, new_owner
from tests.test_event import CODE, form, make, open_event, site_server
from tests.test_event_demos import save_pack

ADMIN = "9999900000"
V22 = {"lead_magnet", "revenue_levers", "pipeline", "quotations", "money_owed", "funnel_leaks", "follow_up_agent", "approvals"}


async def preset():
    admin = await new_owner(ADMIN)
    state = (await admin.get("/api/admin/config/presets/membership-v22")).json()
    assert {f["key"] for f in state["features"]} == V22 and not state["applied"]
    r = (await admin.post("/api/admin/config/presets/membership-v22")).json()
    assert r["applied"] and set(r["moved"]) == V22
    return admin


async def member(phone: str):
    c = await new_owner(phone, name="Owner Test")
    assert (await c.put("/api/business", json=PROFILE)).status_code == 200
    uid = (await c.get("/api/me")).json()["user"]["id"]
    admin = await new_owner(ADMIN)
    assert (await admin.patch(f"/api/admin/users/{uid}", json={"plan": "lite"})).status_code == 200
    return c, uid


async def test_the_preset_puts_the_conversion_features_in_membership_and_the_code_defaults_stay(client):
    c, _ = await member("9863000001")
    me = (await c.get("/api/me")).json()
    assert not me["features"]["funnel_leaks"] and not me["features"]["follow_up_agent"]
    assert (await c.get("/api/leaks")).status_code == 403
    await preset()
    me = (await c.get("/api/me")).json()
    assert me["features"]["funnel_leaks"] and me["features"]["follow_up_agent"] and me["features"]["approvals"]
    assert plans.DEFAULT_FEATURES["follow_up_agent"]["tier"] == "program"   # the code's default is unchanged
    again = (await (await new_owner(ADMIN)).post("/api/admin/config/presets/membership-v22")).json()
    assert again["moved"] == [] and again["applied"]


async def test_the_leak_finder_starts_from_inquiries_when_visitors_are_not_known(client):
    await preset()
    c, _ = await member("9863000002")
    r = await c.put("/api/leaks", json={"numbers": {"visitors": 0, "inquiries": 40, "meetings": 10, "proposals": 8, "won": 3},
                                        "targets": {}, "avg_sale": 50000})
    assert r.status_code == 200, r.text
    res = r.json()["saved"]["result"]
    assert res["starts_at"] == "inquiries" and res["steps"][1].get("skipped")
    assert res["biggest_leak"]["key"] == "meetings"          # 25% of inquiries met against a 50% target
    assert res["if_fixed"]["extra_customers"] == 3.0 and res["if_fixed"]["extra_sales"] == 150000
    # with visitors given, the first step counts as before
    r = (await c.put("/api/leaks", json={"numbers": {"visitors": 2000, "inquiries": 20, "meetings": 10, "proposals": 8, "won": 3},
                                         "targets": {}, "avg_sale": 50000})).json()["saved"]["result"]
    assert r["starts_at"] == "visitors" and not r["steps"][1].get("skipped") and r["biggest_leak"]["key"] == "inquiries"


async def test_follow_ups_for_a_pasted_whatsapp_inquiry_are_sent_from_the_inquiries_page(client):
    await preset()
    c, uid = await member("9863000003")
    s = (await c.get("/api/inquiries/settings")).json()["followups"]
    # on the Membership, follow-ups (an AI run each) wait for the owner's own switch
    assert s["available"] and not s["on"] and s["opt_in"] and s["days"] == [1, 3, 7] and s["send_list"]
    r = await c.post("/api/inquiries/whatsapp", json={"name": "Early Customer", "phone": "98200 00110", "message": "Do you have racks?"})
    await inquiries.settle()
    assert await db().agent_jobs.count_documents({"ws": uid, "agent": "followup", "payload.lead_id": r.json()["id"]}) == 0
    assert not next(a for a in (await c.get("/api/agents")).json()["agents"] if a["key"] == "followup")["on"]
    assert (await c.patch("/api/agents/followup", json={"on": True})).json()["on"]
    assert (await c.get("/api/inquiries/settings")).json()["followups"]["on"]
    r = await c.post("/api/inquiries/whatsapp", json={"name": "Test Customer", "phone": "98200 00111", "message": "Need 20 steel racks, price?"})
    lead_id = r.json()["id"]
    await inquiries.settle()
    jobs = [j async for j in db().agent_jobs.find({"ws": uid, "agent": "followup", "payload.lead_id": lead_id}).sort("run_at", 1)]
    assert [j["payload"]["n"] for j in jobs] == [1, 2, 3]
    item = next(i for i in (await c.get("/api/inquiries")).json()["items"] if i["id"] == lead_id)
    assert item["followups"]["ready"] is None and item["followups"]["next_n"] == 1
    await scheduler.tick(at=jobs[0]["run_at"] + timedelta(minutes=1))
    item = next(i for i in (await c.get("/api/inquiries")).json()["items"] if i["id"] == lead_id)
    ready = item["followups"]["ready"]
    assert ready and ready["n"] == 1 and ready["of"] == 3 and ready["text"]
    sent = (await c.post(f"/api/approvals/{ready['id']}/own")).json()
    assert sent["link"].startswith("https://wa.me/919820000111")
    item = next(i for i in (await c.get("/api/inquiries")).json()["items"] if i["id"] == lead_id)
    assert item["followups"]["sent"] == 1 and item["followups"]["ready"] is None and item["followups"]["next_n"] == 2
    # won: the rest stop
    assert (await c.patch(f"/api/leads/{lead_id}", json={"status": "won"})).status_code == 200
    assert await db().agent_jobs.count_documents({"ws": uid, "agent": "followup", "payload.lead_id": lead_id, "status": "scheduled"}) == 0
    # switched off: a new inquiry gets none
    assert (await c.patch("/api/agents/followup", json={"on": False})).status_code == 200
    assert not (await c.get("/api/inquiries/settings")).json()["followups"]["on"]
    r = await c.post("/api/inquiries/whatsapp", json={"name": "Second Customer", "phone": "98200 00112", "message": "Do you deliver to Pune?"})
    assert await db().agent_jobs.count_documents({"ws": uid, "agent": "followup", "payload.lead_id": r.json()["id"]}) == 0


async def test_without_the_feature_there_is_no_follow_up_switch_and_no_follow_ups(client):
    c, uid = await member("9863000004")       # Membership without the preset
    s = (await c.get("/api/inquiries/settings")).json()["followups"]
    assert not s["available"]
    r = await c.post("/api/inquiries/whatsapp", json={"name": "Test Customer", "phone": "98200 00113", "message": "Price for 10 units?"})
    assert await db().agent_jobs.count_documents({"ws": uid, "agent": "followup", "payload.lead_id": r.json()["id"]}) == 0


async def test_event_room_finds_its_leak_and_gets_its_follow_ups_on_day_two(client):
    admin = await open_event()
    await preset()
    with site_server() as site:
        a = await make(client, form(site))
        b = await make(client, form(None, business_name="Riya Tiffins", about="Home-cooked lunch tiffins delivered to offices", city="Pune"))
        await ev.drain()
    asha = await save_pack(a["token"], "9863000011", "asha@example-kaveri.in", "Asha")
    riya = await save_pack(b["token"], "9863000012", "riya@example-tiffins.in", "Riya")
    asha_id = (await asha.get("/api/me")).json()["user"]["id"]
    # Day 1: swap with your neighbour — Riya sends Asha an inquiry from Asha's website
    assert (await asha.post("/api/site/publish")).status_code == 200
    slug = (await asha.get("/api/site")).json()["slug"]
    assert (await riya.post(f"/s/{slug}/inquiry", json={"name": "Riya", "phone": "9863000012", "message": "Fridge not cooling, can someone come today?"})).status_code == 200
    await inquiries.settle()
    # Day 2: the room types last month's numbers — and the follow-up is waiting
    r = await asha.put("/api/leaks", json={"numbers": {"visitors": 0, "inquiries": 30, "meetings": 12, "proposals": 6, "won": 2},
                                           "targets": {}, "avg_sale": 15000})
    assert r.json()["saved"]["result"]["biggest_leak"]
    job = await db().agent_jobs.find_one({"ws": asha_id, "agent": "followup", "payload.n": 1})
    assert job and job["run_at"] - now() > timedelta(hours=12)        # the next day, in working hours
    await scheduler.tick(at=job["run_at"] + timedelta(minutes=1))
    item = (await asha.get("/api/inquiries")).json()["items"][0]
    assert item["followups"]["ready"]
    await asha.post(f"/api/approvals/{item['followups']['ready']['id']}/own")
    live = (await admin.get(f"/api/admin/event/{CODE}/live")).json()["demos"]["conversion"]
    assert live["Found their biggest leak"] == 1 and live["Follow-ups ready"] == 1 and live["Follow-ups sent"] == 1
    for x in (asha, riya):
        await x.aclose()


async def test_all_zero_numbers_show_no_leak_and_an_old_save_is_worked_out_again(client):
    await preset()
    c, uid = await member("9863000005")
    r = (await c.put("/api/leaks", json={"numbers": {"visitors": 0, "inquiries": 0, "meetings": 0, "proposals": 0, "won": 0},
                                         "targets": {}, "avg_sale": 20000})).json()["saved"]["result"]
    assert r["biggest_leak"] is None and r["if_fixed"] is None
    # a step nothing came into isn't judged: 40 inquiries, no meetings -> the leak is meetings, not the steps after it
    r = (await c.put("/api/leaks", json={"numbers": {"visitors": 0, "inquiries": 40, "meetings": 0, "proposals": 0, "won": 0},
                                         "targets": {}, "avg_sale": 20000})).json()["saved"]["result"]
    assert r["biggest_leak"]["key"] == "meetings" and all(s.get("no_data") for s in r["steps"][3:])
    # saved before v2.2 (no starting step recorded): shown worked out with today's rules
    await db().leaks.update_one({"_id": uid}, {"$set": {"numbers": {"visitors": 0, "inquiries": 40, "meetings": 10, "proposals": 8, "won": 3},
                                                        "result": {"steps": [], "biggest_leak": {"key": "inquiries"}, "if_fixed": None}}})
    saved = (await c.get("/api/leaks")).json()["saved"]["result"]
    assert saved["starts_at"] == "inquiries" and saved["biggest_leak"]["key"] == "meetings"
