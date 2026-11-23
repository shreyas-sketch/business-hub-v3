"""v2.4 foundation: the setup switches the growth features on without touching v2.3, the limits count and refuse, and the
"working" mark. Every business, person and number here is invented."""
import pytest
from fastapi import HTTPException

from app import growth_core, plans, v23, v24
from app.db import db
from tests.v24_helpers import admin_client, owner_id, setup_v24, v24_member


def items(me: dict) -> dict:
    return {i["key"]: i for g in me["menu"] for i in g["items"]}


async def test_the_v24_setup_adds_its_pages_and_leaves_the_v23_menu_alone(client):
    m = await v24_member("9800400001")
    me = (await m.get("/api/me")).json()
    by_group = {g["group"]: [i["label"] for i in g["items"]] for g in me["menu"]}
    assert by_group["Start"] == ["Knowledge Hub", "Engine Audit", "AI team"]
    assert by_group["Lead engine"] == ["Website", "Lead magnet", "Find new customers"]
    # main's v2.3 setup keeps Win-Back, Lead Response and WhatsApp chats in the menu (533a693); v2.4 leaves them where they are
    assert by_group["Sales engine"] == ["Revenue Multiplier", "Ways to get more leads", "Invoicing", "Call coach", "Win-Back"]
    assert by_group["Conversion engine"] == ["Inquiries", "CRM", "Pipeline & Opportunities", "Lead Response", "WhatsApp chats"]
    for k in plans.V24_NEW:
        assert plans.META[k]["on"], k
    assert not plans.META["lead_brief"]["show"] and not plans.META["quick_answers"]["show"]
    assert me["features"]["lead_brief"] and me["features"]["prospects"]
    # running either setup again changes nothing, and v2.3 doesn't switch v2.4's features off
    assert (await v24.apply("test"))["features_changed"] == 0
    assert (await v23.apply("test"))["features_changed"] == 0
    assert plans.META["prospects"]["on"]


async def test_without_the_v24_setup_its_features_stay_off(client):
    m = await __import__("tests.v23_helpers", fromlist=["v23_member"]).v23_member("9800400002")
    me = (await m.get("/api/me")).json()
    assert not any(k in items(me) for k in ("prospects", "call_coach", "ai_team"))
    assert not any(plans.META[k]["on"] for k in plans.V24_NEW)


async def test_find_new_customers_and_call_coach_wait_for_payment(client):
    f = await v24_member("9800400003", paid=False)
    me = (await f.get("/api/me")).json()
    assert items(me)["prospects"]["locked"] and items(me)["call_coach"]["locked"]
    uid = await owner_id(f)
    user = await db().users.find_one({"_id": uid})
    assert plans.growth_limit(user, "prospect_searches") == 0


async def test_limits_count_refuse_and_give_back(client):
    m = await v24_member("9800400004")
    uid = await owner_id(m)
    user = await db().users.find_one({"_id": uid})
    assert plans.growth_limit(user, "prospect_searches") == 10
    for _ in range(10):
        await growth_core.take(user, "prospect_searches")
    with pytest.raises(HTTPException) as e:
        await growth_core.take(user, "prospect_searches")
    assert e.value.status_code == 429 and "10 business searches" in e.value.detail
    await growth_core.give_back(user, "prospect_searches")
    assert (await growth_core.left(user, "prospect_searches"))["left"] == 1
    # the admin can change a limit per plan
    admin = await admin_client()
    r = await admin.patch("/api/admin/config/plans/lite", json={"prospect_searches": 12})
    assert r.status_code == 200, r.text
    await plans.load(force=True)
    assert (await growth_core.left(user, "prospect_searches"))["left"] == 3


async def test_the_working_mark_is_there_only_while_the_job_runs(client):
    await setup_v24()
    async with growth_core.working("ws-test", "researcher"):
        assert await growth_core.working_now("ws-test") == {"researcher"}
    assert await growth_core.working_now("ws-test") == set()


async def test_admin_can_see_and_apply_the_v24_setup(client):
    await setup_v24()
    admin = await admin_client()
    st = (await admin.get("/api/admin/config/presets/v24")).json()
    assert st["applied"] and st["version"] == "2.4"
    r = await admin.post("/api/admin/config/presets/v24")
    assert r.status_code == 200 and r.json()["features_changed"] == 0


async def test_an_outreach_message_is_never_sent_by_the_hub(client):
    """A first message to a business that never contacted the owner only opens the owner's own WhatsApp — on any plan, with
    any autonomy setting, by any route."""
    from app.agents import approvals as appr
    from app.agents import jobs
    m = await v24_member("9800400005")
    uid = await owner_id(m)
    await db().users.update_one({"_id": uid}, {"$set": {"plan": "office"}})   # the plan whose staff may act on their own
    await jobs.save_settings(uid, "outreach_writer", {"autonomy": "act"})
    owner = await db().users.find_one({"_id": uid})
    a = await appr.create_approval(owner, uid, "outreach_writer", "outreach", "First message", "Invented Traders", "+919811122233",
                                   "Hello, this is a test message.", auto_send=True)
    assert a["status"] == "pending"
    assert (await appr.deliver(a, owner))["sent"] is False
    r = await m.post(f"/api/approvals/{a['_id']}/send")
    assert r.status_code == 400 and "own WhatsApp" in r.json()["detail"]
    r = await m.post(f"/api/approvals/{a['_id']}/own")
    assert r.status_code == 200 and r.json()["link"].startswith("https://wa.me/919811122233?text=")
