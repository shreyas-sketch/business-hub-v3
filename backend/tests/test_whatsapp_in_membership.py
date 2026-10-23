"""WhatsApp chats moved down to the ₹1,999 Membership (Admin → Features & plans, for a live event): the owner can connect
their own number, but nothing replies to a customer on it — no instant reply, no 24×7 AI reply — until the owner switches
it on. On Growth Mentorship, its usual plan, both are on as before."""
from app.db import db
from tests.conftest import go_live, new_owner
from tests.test_agents import connect_demo_whatsapp, inquiry
from tests.test_v2_growth import meta_message

ADMIN = "9999900000"


async def member(phone: str):
    c = await new_owner(phone)
    site = await go_live(c)
    uid = (await c.get("/api/me")).json()["user"]["id"]
    admin = await new_owner(ADMIN)
    assert (await admin.patch(f"/api/admin/users/{uid}", json={"plan": "lite"})).status_code == 200
    return c, uid, site, admin


async def test_whatsapp_moved_into_membership_waits_for_the_owners_switch(client):
    c, uid, site, admin = await member("9839100001")
    assert (await c.get("/api/chats")).status_code == 403                         # Growth Mentorship in the code
    assert (await admin.patch("/api/admin/config/features/whatsapp_ai", json={"tier": "lite"})).status_code == 200
    assert (await c.put("/api/connections/whatsapp", json={"values": {"provider": "aisensy", "number": "98200 12345"}})).status_code == 200
    await connect_demo_whatsapp(uid)

    chats = (await c.get("/api/chats")).json()
    assert chats["connected"] and chats["ai_on"] is False and chats["opt_in"] is True
    agents = {a["key"]: a for a in (await c.get("/api/agents")).json()["agents"]}
    assert agents["instant_reply"]["on"] is False and agents["instant_reply"]["opt_in"] is True

    # a website inquiry: no instant reply; a WhatsApp message: no AI reply
    lead = await inquiry(client, site, "Asha Rao", "98391 00011", "Need a wardrobe")
    assert "instant_reply" not in lead
    token = (await db().connections.find_one({"_id": uid}))["whatsapp"]["webhook_token"]
    await client.post(f"/api/hooks/whatsapp/{token}", json=meta_message("919839100021", "Hi, price of a kitchen?", "w1"))
    [t] = (await c.get("/api/chats")).json()["threads"]
    assert await db().wa_messages.count_documents({"thread_id": t["id"], "dir": "out"}) == 0
    assert await db().outbox.count_documents({"ws": uid, "via": "owner-demo"}) == 0

    # the owner switches each on: now they reply
    assert (await c.post("/api/chats-ai", json={"on": True})).status_code == 200
    assert (await c.get("/api/chats")).json()["ai_on"] is True
    await client.post(f"/api/hooks/whatsapp/{token}", json=meta_message("919839100022", "Hi, price of a kitchen?", "w2", "Ravi"))
    ravi = next(x for x in (await c.get("/api/chats")).json()["threads"] if x["name"] == "Ravi")
    assert await db().wa_messages.count_documents({"thread_id": ravi["id"], "dir": "out"}) == 1
    assert (await c.patch("/api/agents/instant_reply", json={"on": True})).status_code == 200
    lead = await inquiry(client, site, "Neha Joshi", "98391 00012", "Kitchen")
    assert lead["instant_reply"]["sent"]


async def test_on_growth_mentorship_both_stay_on_by_default(client):
    c = await new_owner("9839100002")
    site = await go_live(c)
    uid = (await c.get("/api/me")).json()["user"]["id"]
    assert (await (await new_owner(ADMIN)).patch(f"/api/admin/users/{uid}", json={"plan": "growth"})).status_code == 200
    await connect_demo_whatsapp(uid)
    chats = (await c.get("/api/chats")).json()
    assert chats["ai_on"] is True and chats["opt_in"] is False
    assert (await inquiry(client, site, "Kiran Shah", "98391 00013", "Bed"))["instant_reply"]["sent"]


async def test_event_extras_move_into_membership_and_back_in_one_tap(client):
    c, uid, site, admin = await member("9839100003")
    assert (await c.get("/api/win-back")).status_code == 403
    assert (await c.post("/api/admin/config/presets/event-extras")).status_code == 403      # admin only
    s = (await admin.get("/api/admin/config/presets/event-extras")).json()
    assert not s["applied"] and {f["key"] for f in s["features"]} == {"win_back", "market_research", "whatsapp_ai", "lead_response"}
    r = (await admin.post("/api/admin/config/presets/event-extras")).json()
    assert r["applied"] and set(r["moved"]) == {"win_back", "market_research", "whatsapp_ai", "lead_response"}
    me = (await c.get("/api/me")).json()["features"]
    assert all(me[k] for k in ("win_back", "market_research", "whatsapp_ai", "lead_response"))
    assert (await c.get("/api/chats")).status_code == 200
    # renamed meanwhile: moving back keeps the name and only undoes the plan
    assert (await admin.patch("/api/admin/config/features/win_back", json={"label": "Bring them back"})).status_code == 200
    back = (await admin.post("/api/admin/config/presets/event-extras/undo")).json()
    assert not back["applied"] and set(back["moved_back"]) == {"win_back", "market_research", "whatsapp_ai", "lead_response"}
    me = (await c.get("/api/me")).json()["features"]
    assert not any(me[k] for k in ("win_back", "market_research", "whatsapp_ai", "lead_response"))
    cfg = {f["key"]: f for f in (await admin.get("/api/admin/config")).json()["features"]}
    assert cfg["win_back"]["label"] == "Bring them back" and cfg["win_back"]["tier"] == "program" and cfg["lead_response"]["tier"] == "growth"
