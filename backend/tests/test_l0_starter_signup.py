"""New signups can get only the L0 starter set (the free 2-day webinar's pages): those open from the first minute, whatever
the event day, and every other menu page stays locked until they pay, even with the event's Membership access. Pages that
work behind other pages follow the plan, so an L0 page never breaks. Every business, person and number here is invented."""
from app import plans
from app.db import db

from tests.conftest import login, new_owner
from tests.test_event import open_event
from tests.v23_helpers import mark_paid, setup_v23

ADMIN = "+919999900000"
L0 = {"home", "business_brain", "engine_audit", "smart_dashboard", "website", "revenue_levers", "inquiry_desk", "sop_library",
      "social_media", "plans_billing"}


async def signups_get(client, **body):
    if not plans.engines_on():
        await setup_v23()
    await login(client, ADMIN)
    r = await client.patch("/api/admin/config/ui", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def menu_pages() -> set[str]:
    return {k for k, m in plans.META.items() if m["on"] and m["path"] and m.get("show", True) and not m.get("grants_only")}


async def test_an_event_signup_gets_only_the_l0_pages_and_the_rest_shows_locked(client):
    assert (await signups_get(client, welcome_full_hub=True, welcome_starter=True))["welcome_starter"] is True
    admin = await open_event()
    from tests.test_event import CODE
    assert (await admin.post(f"/api/admin/event/{CODE}/day", json={"day": 1})).status_code == 200
    owner = await new_owner("9834011111", cohort=CODE)
    me = (await owner.get("/api/me")).json()
    opened = {k for k in menu_pages() if me["features"][k]}
    assert opened == L0 & menu_pages()                                      # Social media and SOP Writer open on Day 1 too
    assert me["starter"] is True and me["paid"] is False
    assert me["feature_locks"]["crm"] == {"lock": "starter"} and me["feature_locks"]["quotations"] == {"lock": "starter"}
    locked = {i["key"]: i for s in me["menu"] for i in s["items"] if i["locked"]}
    assert locked and all(i["lock"] == "starter" for i in locked.values()) and not L0 & set(locked)
    assert me["features"]["leads"]                                          # behind the website and inquiries: follows the plan
    assert (await owner.get("/api/billing")).json()["starter"] is True
    assert (await owner.get("/api/crm/contacts")).status_code in (402, 403)     # a locked page is closed on the server too

    uid = me["user"]["id"]
    rows = (await client.get("/api/admin/onboarding?status=starter")).json()
    assert uid in {m["id"] for m in rows["members"]} and rows["counts"]["starter"] == 1
    assert (await client.get(f"/api/admin/owners/{uid}")).json()["starter"] is True

    await mark_paid(uid)                                                    # once they pay, the whole Membership opens
    me = (await owner.get("/api/me")).json()
    assert me["starter"] is False and me["features"]["crm"] and me["features"]["quotations"]
    await owner.aclose()
    await admin.aclose()


async def test_the_admin_opens_the_full_hub_for_one_owner_and_moves_features_in_and_out(client):
    await signups_get(client, welcome_starter=True)
    owner = await new_owner("9834022222")
    uid = (await owner.get("/api/me")).json()["user"]["id"]
    assert (await db().users.find_one({"_id": uid}))["welcome"]["only"] == "starter"

    r = await client.patch("/api/admin/config/features/crm", json={"starter": True})
    assert r.status_code == 200 and r.json()["starter"] is True
    assert (await owner.get("/api/me")).json()["features"]["crm"]
    await client.patch("/api/admin/config/features/website", json={"starter": False})
    assert not (await owner.get("/api/me")).json()["features"]["website"]

    assert (await client.patch(f"/api/admin/users/{uid}", json={"starter": False})).status_code == 200
    me = (await owner.get("/api/me")).json()
    assert me["starter"] is False and me["features"]["website"] and me["features"]["brochures"]   # the full hub
    assert (await client.patch(f"/api/admin/users/{uid}", json={"starter": True})).status_code == 200
    assert (await owner.get("/api/me")).json()["starter"] is True
    await owner.aclose()


async def test_switching_back_to_the_full_hub_keeps_earlier_starter_owners_on_l0(client):
    await signups_get(client, welcome_starter=True)
    early = await new_owner("9834033333")
    await signups_get(client, welcome_starter=False, welcome_full_hub=True)
    late = await new_owner("9834044444")
    assert (await early.get("/api/me")).json()["starter"] is True
    late_me = (await late.get("/api/me")).json()
    assert late_me["starter"] is False and late_me["features"]["crm"]
    await early.aclose()
    await late.aclose()


async def test_only_admins_change_it(client):
    owner = await new_owner("9834055555")
    assert (await owner.patch("/api/admin/config/ui", json={"welcome_starter": True})).status_code == 403
    assert (await owner.patch("/api/admin/users/x", json={"starter": True})).status_code == 403
    await owner.aclose()
