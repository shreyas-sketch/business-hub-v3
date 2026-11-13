"""The 3-Day set: people who sign up from a cohort's links (the join link or the event link) while the cohort gives the 3-Day
pages get only those pages (the 3-day event's demos and activities) until they pay. It is kept apart from the L0 starter: the
hub-wide "New signups get" choice and the L0 pages are untouched, and signups from anywhere else carry on as before.
Every business, person and number here is invented."""
from datetime import timedelta

from app import plans
from app.db import IST, db, now

from tests.conftest import login, new_owner
from tests.test_event import open_event
from tests.v23_helpers import mark_paid, setup_v23

ADMIN = "+919999900000"
CODE3 = "ABB-3DAY-01"
THREEDAY = {"home", "business_brain", "website", "sop_library", "brand_kit", "social_media", "revenue_levers", "crm", "pipeline",
            "money_owed", "quotations", "inquiry_desk", "smart_dashboard", "expenses", "plans_billing"}
L0 = {"home", "business_brain", "engine_audit", "smart_dashboard", "website", "revenue_levers", "inquiry_desk", "sop_library",
      "social_media", "plans_billing"}


def menu_pages() -> set[str]:
    return {k for k, m in plans.META.items() if m["on"] and m["path"] and m.get("show", True) and not m.get("grants_only")}


def last_day(days: int = 2) -> str:
    return (now().astimezone(IST) + timedelta(days=days)).date().isoformat()


async def threeday_cohort(client, until: str | None = None, welcome_starter: bool = True):
    """v2.3 on, L0 for new signups hub-wide (so the test shows the 3-Day link wins without touching it), and a 3-Day cohort."""
    if not plans.engines_on():
        await setup_v23()
    await login(client, ADMIN)
    assert (await client.patch("/api/admin/config/ui", json={"welcome_starter": welcome_starter})).status_code == 200
    r = await client.post("/api/admin/cohorts", json={"code": CODE3, "name": "AI Business Breakthrough 2027", "workshop_date": None})
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/admin/event/{CODE3}/threeday", json={"until": until or last_day()})
    assert r.status_code == 200, r.text
    return r.json()


async def test_a_signup_from_the_3day_link_gets_only_the_3day_pages(client):
    saved = await threeday_cohort(client)
    assert saved["threeday_until"] == last_day()
    owner = await new_owner("9834111111", cohort=CODE3)
    me = (await owner.get("/api/me")).json()
    opened = {k for k in menu_pages() if me["features"][k]}
    assert opened == THREEDAY & menu_pages()
    assert me["threeday"] is True and me["starter"] is False and me["paid"] is False
    assert me["feature_locks"]["engine_audit"] == {"lock": "threeday"} and me["feature_locks"]["lead_magnet"] == {"lock": "threeday"}
    locked = {i["key"]: i for s in me["menu"] for i in s["items"] if i["locked"]}
    assert locked and all(i["lock"] == "threeday" for i in locked.values()) and not THREEDAY & set(locked)
    assert me["features"]["leads"] and me["features"]["auto_reminders"]      # behind Website and Payment follow-up: follow the plan
    assert (await owner.get("/api/billing")).json()["threeday"] is True
    assert (await owner.get("/api/crm/contacts")).status_code == 200          # a 3-Day page works on the server
    assert (await owner.get("/api/audit")).status_code in (402, 403)          # a page outside it is closed on the server too

    uid = me["user"]["id"]
    user = await db().users.find_one({"_id": uid})
    assert user["welcome"]["only"] == "threeday" and user["welcome"]["cohort"] == CODE3
    rows = (await client.get(f"/api/admin/onboarding?status=threeday")).json()
    assert uid in {m["id"] for m in rows["members"]} and rows["counts"]["threeday"] == 1
    assert (await client.get(f"/api/admin/owners/{uid}")).json()["only"] == "threeday"

    await mark_paid(uid)                                                      # once they pay, the whole Membership opens
    me = (await owner.get("/api/me")).json()
    assert me["threeday"] is False and me["features"]["engine_audit"] and me["features"]["lead_magnet"]
    await owner.aclose()


async def test_the_l0_starter_and_other_signups_are_untouched(client):
    await threeday_cohort(client)
    elsewhere = await new_owner("9834122222")                                 # no cohort: the hub-wide choice (L0) as before
    me = (await elsewhere.get("/api/me")).json()
    assert me["starter"] is True and me["threeday"] is False
    assert {k for k in menu_pages() if me["features"][k]} == L0 & menu_pages()
    assert all(plans.META[k]["starter"] == (k in L0) for k in plans.META)   # the L0 marks didn't move
    await elsewhere.aclose()


async def test_an_event_link_signup_on_day_1_still_gets_every_3day_page(client):
    await threeday_cohort(client)
    admin = await open_event()                                                # the other event cohort, on Day 1
    from tests.test_event import CODE
    assert CODE != CODE3
    r = await client.put(f"/api/admin/event/{CODE3}", json={"open": True, "ai": True, "pack_cap": 400, "ai_cap": 300, "trial_until": last_day()})
    assert r.status_code == 200 and r.json()["threeday_until"] == last_day()  # saving the event's settings keeps the 3-Day day
    assert (await client.post(f"/api/admin/event/{CODE3}/day", json={"day": 1})).status_code == 200
    owner = await new_owner("9834133333", cohort=CODE3)
    me = (await owner.get("/api/me")).json()
    assert me["threeday"] is True and me["features"]["inquiry_desk"] and me["features"]["expenses"]   # Day 2 and 3 pages open too
    assert me["event"] is None                                                # no event trial on top: the 3-Day set is what they get
    await owner.aclose()
    await admin.aclose()


async def test_switched_off_or_after_the_last_day_the_link_gives_what_everyone_gets(client):
    await threeday_cohort(client)
    assert (await client.post(f"/api/admin/event/{CODE3}/threeday", json={"until": None})).json()["threeday_until"] is None
    late = await new_owner("9834144444", cohort=CODE3)
    assert (await late.get("/api/me")).json()["starter"] is True             # the hub-wide L0
    await db().cohorts.update_one({"_id": CODE3}, {"$set": {"event.threeday_until": now() - timedelta(minutes=1)}})
    later = await new_owner("9834155555", cohort=CODE3)
    assert (await later.get("/api/me")).json()["threeday"] is False
    r = await client.post(f"/api/admin/event/{CODE3}/threeday", json={"until": "2020-01-01"})
    assert r.status_code == 400
    await late.aclose()
    await later.aclose()


async def test_owners_who_already_had_an_account_keep_what_they_have(client):
    await threeday_cohort(client, welcome_starter=False)
    early = await new_owner("9834166666")                                     # the full hub, signed up before
    await early.post("/api/auth/logout")
    again = await new_owner("9834166666", cohort=CODE3)                      # logs in again through the 3-Day link
    me = (await again.get("/api/me")).json()
    assert me["threeday"] is False and me["features"]["engine_audit"]
    await early.aclose()
    await again.aclose()


async def test_the_admin_moves_features_and_owners_between_sets(client):
    await threeday_cohort(client)
    owner = await new_owner("9834177777", cohort=CODE3)
    uid = (await owner.get("/api/me")).json()["user"]["id"]
    r = await client.patch("/api/admin/config/features/engine_audit", json={"threeday": True})
    assert r.status_code == 200 and r.json()["threeday"] is True and r.json()["starter"] is True
    assert (await owner.get("/api/me")).json()["features"]["engine_audit"]

    assert (await client.patch(f"/api/admin/users/{uid}", json={"only": "starter"})).status_code == 200
    me = (await owner.get("/api/me")).json()
    assert me["starter"] is True and me["threeday"] is False and not me["features"]["crm"]
    assert (await client.patch(f"/api/admin/users/{uid}", json={"only": "full"})).status_code == 200
    me = (await owner.get("/api/me")).json()
    assert me["starter"] is False and me["threeday"] is False and me["features"]["key_person"]
    assert (await client.patch(f"/api/admin/users/{uid}", json={"only": "threeday"})).status_code == 200
    assert (await owner.get("/api/me")).json()["threeday"] is True
    await owner.aclose()


async def test_end_event_access_closes_the_3day_pages_for_those_who_havent_paid(client):
    await threeday_cohort(client)
    stays = await new_owner("9834188888", cohort=CODE3)
    pays = await new_owner("9834199999", cohort=CODE3)
    pays_id = (await pays.get("/api/me")).json()["user"]["id"]
    await mark_paid(pays_id)
    r = await client.post(f"/api/admin/event/{CODE3}/end-access")
    assert r.status_code == 200 and r.json()["ended"] == 1 and r.json()["threeday_until"] is None
    me = (await stays.get("/api/me")).json()
    assert me["threeday"] is False and not me["features"]["crm"] and not me["features"]["sop_library"]
    assert (await pays.get("/api/me")).json()["features"]["crm"]
    newer = await new_owner("9834100000", cohort=CODE3)                       # the link no longer gives the 3-Day pages
    assert (await newer.get("/api/me")).json()["threeday"] is False
    for c in (stays, pays, newer):
        await c.aclose()


async def test_only_admins_change_it(client):
    owner = await new_owner("9834100001")
    assert (await owner.post(f"/api/admin/event/{CODE3}/threeday", json={"until": last_day()})).status_code == 403
    assert (await owner.patch("/api/admin/users/x", json={"only": "threeday"})).status_code == 403
    await owner.aclose()
