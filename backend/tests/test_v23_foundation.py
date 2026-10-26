"""v2.3 (OwnerDesk) foundation: the setup, the engine menu, the event's engines opening day by day, the event's tokens and
images, ending event access, Admin → Onboarding (mark paid, tokens, switch off), and the member links. Every business,
person and number here is invented."""
from datetime import timedelta

from app import plans, v23
from app.db import db, now
from tests.conftest import new_owner
from tests.v23_helpers import admin_client, mark_paid, owner_id, setup_v23, v23_member

CODE = "OD-OCT-27"
ENGINE_GROUPS = ["Start", "Lead engine", "Sales engine", "Conversion engine", "Operations engine", "Brand engine", "Data engine", "Account"]


async def event_cohort(day: int | None = None, runs: int | None = None, images: int | None = None):
    ev = {"open": True, "ai": True, "pack_cap": 400, "ai_cap": 300, "trial_until": now() + timedelta(days=3)}
    for k, v in (("day", day), ("runs", runs), ("images", images)):
        if v is not None:
            ev[k] = v
    await db().cohorts.update_one({"_id": CODE}, {"$set": {"code": CODE, "name": "Your time back, Oct", "created_at": now(), "event": ev}},
                                  upsert=True)
    await plans.load(force=True)


async def attendee(phone: str):
    """An owner on the event's access (what saving an event pack gives)."""
    c = await v23_member(phone, paid=False)
    uid = await owner_id(c)
    await db().users.update_one({"_id": uid}, {"$set": {"cohort": CODE, "trial": {"plan": "lite", "until": now() + timedelta(days=3),
                                                                                "reason": "event", "cohort": CODE}}})
    return c, uid


def items(me: dict) -> dict:
    return {i["key"]: i for g in me["menu"] for i in g["items"]}


async def test_the_setup_gives_the_engine_menu_with_only_the_agreed_features(client):
    m = await v23_member("9811100001")
    me = (await m.get("/api/me")).json()
    assert [g["group"] for g in me["menu"]] == ENGINE_GROUPS
    by_group = {g["group"]: [i["label"] for i in g["items"]] for g in me["menu"]}
    assert by_group["Start"] == ["Knowledge Hub", "Engine Audit"]
    assert by_group["Lead engine"] == ["Website", "Lead magnet"]
    assert by_group["Sales engine"] == ["Revenue Multiplier", "Ways to get more leads", "Invoicing"]
    assert by_group["Conversion engine"] == ["Inquiries", "CRM", "Pipeline & Opportunities"]
    assert by_group["Operations engine"] == ["Dashboard", "Payment follow-up", "Compliance calendar", "SOP Writer"]
    assert by_group["Brand engine"] == ["Brand kit", "Social media"]
    assert by_group["Data engine"] == ["Key-person knowledge", "Expenses & bank statement"]
    assert by_group["Account"] == ["Plans & billing", "Connections", "Recordings"]
    assert not any(i["locked"] for i in items(me).values())
    # everything else is switched off, the helpers behind the pages stay on
    assert not plans.META["offer_ladder"]["on"] and not plans.META["week"]["on"] and not plans.META["business_score"]["on"]
    assert plans.META["leads"]["on"] and not plans.META["leads"]["show"]
    assert me["membership"]["price_minor"] == 199_900 and me["membership"]["price_year_minor"] == 1_999_900
    assert me["runs"]["allowance"] == 150 and me["images"]["allowance"] == 30 and me["paid"] is True
    assert me["landing"] == "/dashboard" and len(me["engines"]) == 6 and all(e["open"] for e in me["engines"])
    # the setup is safe to run again
    again = await v23.apply("test")
    assert again["features_changed"] == 0 and again["applied"]


async def test_without_the_setup_the_old_menu_and_plans_stay(client):
    owner = await new_owner("9811100002")
    me = (await owner.get("/api/me")).json()
    assert me["menu"][0]["group"] == "Start" and "Lead engine" not in [g["group"] for g in me["menu"]]
    assert not plans.engines_on() and not plans.META["crm"]["on"] and plans.PLANS["lite"]["name"] == "Membership"


async def test_a_free_account_keeps_the_knowledge_hub_and_audit_and_lands_on_plans(client):
    f = await v23_member("9811100003", paid=False)
    me = (await f.get("/api/me")).json()
    menu = items(me)
    assert not menu["business_brain"]["locked"] and not menu["engine_audit"]["locked"]
    assert menu["website"]["locked"] and menu["website"]["lock"] == "plan"
    assert me["landing"] == "/plans" and me["images"]["allowance"] == 7 and me["links"]["community_link"] == ""
    assert (await f.get("/api/crm/contacts")).status_code == 403


async def test_the_event_opens_the_engines_day_by_day_and_paying_opens_everything(client):
    await setup_v23()
    await event_cohort(day=1)
    c, uid = await attendee("9811100004")
    me = (await c.get("/api/me")).json()
    menu = items(me)
    assert not menu["website"]["locked"] and not menu["revenue_levers"]["locked"] and not menu["quotations"]["locked"]
    assert menu["crm"]["locked"] and menu["crm"]["lock"] == "day" and menu["crm"]["opens_day"] == 2
    assert menu["social_media"]["opens_day"] == 3 and not menu["smart_dashboard"]["locked"]   # the dashboard is open from Day 1
    assert me["feature_locks"]["crm"] == {"lock": "day", "opens_day": 2} and me["event"]["day"] == 1
    assert [e["open"] for e in me["engines"]] == [True, True, False, False, False, False]
    assert (await c.get("/api/crm/contacts")).status_code == 403
    # helpers behind a Day-1 page keep working on Day 1 (they follow the plan, not the day)
    assert plans.has(await db().users.find_one({"_id": uid}), "leads")

    admin = await admin_client()
    r = await admin.post(f"/api/admin/event/{CODE}/day", json={"day": 2})
    assert r.status_code == 200 and r.json()["day"] == 2
    assert (await c.get("/api/crm/contacts")).status_code == 200
    assert items((await c.get("/api/me")).json())["social_media"]["locked"]
    await admin.post(f"/api/admin/event/{CODE}/day", json={"day": 3})
    me = (await c.get("/api/me")).json()
    assert not any(i["locked"] for i in items(me).values()) and all(e["open"] for e in me["engines"])

    # back to Day 1 for the room, but a member who paid has everything
    await admin.post(f"/api/admin/event/{CODE}/day", json={"day": 1})
    await mark_paid(uid)
    me = (await c.get("/api/me")).json()
    assert not any(i["locked"] for i in items(me).values()) and me["event"] is None and me["paid"]


async def test_event_accounts_get_the_events_tokens_and_seven_images_paid_members_150_and_30(client):
    await setup_v23()
    await event_cohort(day=3)
    c, uid = await attendee("9811100005")
    me = (await c.get("/api/me")).json()
    assert me["runs"]["allowance"] == plans.EVENT_RUNS_DEFAULT == 50 and me["images"]["allowance"] == 7
    admin = await admin_client()
    r = await admin.put(f"/api/admin/event/{CODE}", json={"open": True, "ai": True, "pack_cap": 400, "ai_cap": 300, "tokens": 60, "images": 9})
    assert r.status_code == 200 and r.json()["tokens"] == 60 and r.json()["day"] == 3   # the day set by its button is kept
    me = (await c.get("/api/me")).json()
    assert me["runs"]["allowance"] == 60 and me["images"]["allowance"] == 9
    await mark_paid(uid)
    me = (await c.get("/api/me")).json()
    assert me["runs"]["allowance"] == 150 and me["images"]["allowance"] == 30
    # the paid-only brochures open only after paying
    assert "brochures" not in me["feature_locks"] and me["features"]["brochures"]


async def test_brochures_and_the_whatsapp_tool_wait_for_payment_during_the_event(client):
    await setup_v23()
    await event_cohort(day=3)
    c, _ = await attendee("9811100006")
    me = (await c.get("/api/me")).json()
    assert me["feature_locks"]["brochures"] == {"lock": "paid"} and me["feature_locks"]["whatsapp_tool"] == {"lock": "paid"}
    assert not me["features"]["brochures"]


async def test_ending_event_access_closes_it_for_those_who_didnt_pay_and_keeps_their_work(client):
    await setup_v23()
    await event_cohort(day=3)
    a, a_id = await attendee("9811100007")
    b, b_id = await attendee("9811100008")
    await mark_paid(b_id)
    admin = await admin_client()
    r = await admin.post(f"/api/admin/event/{CODE}/end-access")
    assert r.status_code == 200 and r.json()["ended"] == 1 and r.json()["open"] is False
    me = (await a.get("/api/me")).json()
    assert me["landing"] == "/plans" and me["event"] is None and items(me)["website"]["locked"]
    assert (await a.get("/api/business")).status_code == 200            # their data is still there
    assert not any(i["locked"] for i in items((await b.get("/api/me")).json()).values())


async def test_onboarding_lists_members_marks_paid_adds_tokens_and_switches_off(client):
    await setup_v23()
    await event_cohort(day=1)
    c, uid = await attendee("9811100009")
    admin = await admin_client()
    data = (await admin.get("/api/admin/onboarding")).json()
    row = next(m for m in data["members"] if m["id"] == uid)
    assert row["access"]["status"] == "event" and row["access"]["event_day"] == 1 and row["checks"]["knowledge"] in (True, False)
    assert {k["key"] for k in data["checks"]} >= {"knowledge", "audit", "gst_tan", "website", "magnet", "pipeline", "whatsapp"}

    r = await admin.patch(f"/api/admin/onboarding/{uid}", json={"stage": "call_booked", "call_at": "2026-10-28T11:30", "handler": "Desk 2",
                                                                "notes": "Wants the lead magnet first"})
    assert r.status_code == 200 and r.json()["stage"] == "call_booked" and r.json()["call_at"].startswith("2026-10-28T11:30")

    r = await admin.post(f"/api/admin/onboarding/{uid}/paid", json={"period": "year", "ref": "pay_TEST123"})
    assert r.status_code == 200 and r.json()["access"]["status"] == "paid"
    me = (await c.get("/api/me")).json()
    assert me["paid"] and me["runs"]["allowance"] == 150
    row = next(m for m in (await admin.get("/api/admin/onboarding?status=paid")).json()["members"] if m["id"] == uid)
    assert row["payments"][0]["amount_minor"] == 1_999_900 and row["payments"][0]["period"] == "year"

    r = await admin.post(f"/api/admin/onboarding/{uid}/tokens", json={"tokens": 25})
    assert r.json()["bonus_tokens"] == 25
    assert (await c.get("/api/me")).json()["runs"]["bonus"] == 25

    r = await admin.post(f"/api/admin/onboarding/{uid}/off", json={"off": True})
    assert r.json()["access"]["status"] == "off"
    assert (await c.get("/api/me")).status_code == 401
    await admin.post(f"/api/admin/onboarding/{uid}/off", json={"off": False})
    assert (await db().users.find_one({"_id": uid}))["disabled"] is False


async def test_only_admins_reach_onboarding_and_the_links_show_by_who_pays(client):
    owner = await v23_member("9811100010")
    assert (await owner.get("/api/admin/onboarding")).status_code == 403
    admin = await admin_client()
    r = await admin.patch("/api/admin/config/ui", json={"community_link": "https://chat.whatsapp.com/TestGroupLink", "support_email": "help@example.in",
                                                        "pay_monthly_link": "https://rzp.io/l/test-month"})
    assert r.status_code == 200 and r.json()["support_email"] == "help@example.in"
    assert (await admin.patch("/api/admin/config/ui", json={"community_link": "javascript:alert(1)"})).status_code == 422
    links = (await owner.get("/api/me")).json()["links"]
    assert links["community_link"].startswith("https://chat.whatsapp.com/") and links["pay_monthly_link"]
    free = await v23_member("9811100011", paid=False)
    links = (await free.get("/api/me")).json()["links"]
    assert links["community_link"] == "" and links["support_email"] == "help@example.in"
