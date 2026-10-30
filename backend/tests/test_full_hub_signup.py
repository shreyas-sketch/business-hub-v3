"""Every new signup gets the whole hub (paid-only features too) with no end date, without counting as a payment. The admin
switches it off for one owner, or stops giving it to new signups; owners who already have it keep it."""
from app import plans
from app.db import db

from tests.conftest import login, new_owner
from tests.v23_helpers import setup_v23

ADMIN = "+919999900000"


async def full_hub_on(client, on: bool = True):
    if not plans.engines_on():
        await setup_v23()
    await login(client, ADMIN)
    r = await client.patch("/api/admin/config/ui", json={"welcome_full_hub": on})
    assert r.status_code == 200 and r.json()["welcome_full_hub"] is on, r.text


def outside_the_hub() -> set[str]:
    """What a new signup still doesn't get: features only the admin grants, and the ones above Membership (team logins)."""
    return {k for k, m in plans.META.items() if m["on"] and (m.get("grants_only") or plans.rank(m["tier"]) > plans.rank(plans.WELCOME_PLAN))}


async def test_a_new_signup_gets_every_feature_including_paid_only_ones(client):
    await full_hub_on(client)
    owner = await new_owner("9833011111")
    me = (await owner.get("/api/me")).json()
    closed = {k for k, on in me["features"].items() if not on and plans.META[k]["on"]}
    assert closed == outside_the_hub()
    assert me["features"]["brochures"] and me["features"]["crm_sync"]          # paid-only, open anyway
    assert me["user"]["plan_source"] == "welcome" and me["user"]["effective_plan"] == "lite"
    assert me["paid"] is False and me["links"]["community_link"] == ""          # not a payment: members' extras stay for payers
    assert me["user"]["trial_until"] is None and me["user"]["access_until"] is None   # no end date
    plan = (await owner.get("/api/billing")).json()
    assert plan["source"] == "welcome" and plan["until"] is None
    assert (await owner.post("/api/billing/checkout", json={"tier": "lite"})).status_code == 200   # they can still pay
    await owner.aclose()


async def test_email_signups_get_it_and_team_members_use_the_owners_plan(client):
    await full_hub_on(client)
    r = await client.post("/api/auth/register", json={"phone": "9833022222", "email": "asha@example.in", "password": "long-enough"})
    assert r.status_code == 200, r.text
    assert (await client.get("/api/me")).json()["user"]["plan_source"] == "welcome"
    await db().team_invites.insert_one({"_id": "inv1", "owner_id": "someone", "phone": "+919833033333", "status": "pending", "role": "staff"})
    staff = await new_owner("9833033333")
    assert (await db().users.find_one({"phone": "+919833033333"})).get("welcome") is None
    await staff.aclose()


async def test_the_admin_switches_it_off_for_one_owner_and_back_on(client):
    await full_hub_on(client)
    owner = await new_owner("9833044444")
    uid = (await owner.get("/api/me")).json()["user"]["id"]
    assert (await client.get(f"/api/admin/owners/{uid}")).json()["welcome"] is True

    assert (await client.patch(f"/api/admin/users/{uid}", json={"welcome": False})).status_code == 200
    me = (await owner.get("/api/me")).json()
    assert me["user"]["effective_plan"] == "free" and not me["features"]["brochures"]
    assert (await client.get(f"/api/admin/owners/{uid}")).json()["welcome"] is False
    rows = (await client.get("/api/admin/onboarding?status=free")).json()["members"]
    assert uid in {m["id"] for m in rows}

    assert (await client.patch(f"/api/admin/users/{uid}", json={"welcome": True})).status_code == 200
    assert (await owner.get("/api/me")).json()["features"]["brochures"]
    listed = (await client.get("/api/admin/onboarding?status=welcome")).json()
    assert uid in {m["id"] for m in listed["members"]} and listed["counts"]["welcome"] == 1
    await owner.aclose()


async def test_switching_it_off_stops_new_signups_but_keeps_earlier_ones(client):
    await full_hub_on(client)
    early = await new_owner("9833055555")
    await full_hub_on(client, False)
    late = await new_owner("9833066666")
    assert (await early.get("/api/me")).json()["user"]["plan_source"] == "welcome"
    late_me = (await late.get("/api/me")).json()
    assert late_me["user"]["effective_plan"] == "free" and (await db().users.find_one({"phone": "+919833066666"})).get("welcome") is None
    await early.aclose()
    await late.aclose()


async def test_only_admins_change_it(client):
    owner = await new_owner("9833077777")
    assert (await owner.patch("/api/admin/config/ui", json={"welcome_full_hub": True})).status_code == 403
    assert (await owner.patch("/api/admin/users/x", json={"welcome": True})).status_code == 403
    await owner.aclose()


def test_the_full_hub_skips_the_event_days_and_paid_only_locks():
    owner = {"_id": "o", "phone": "+919833088888", "plan": "free", "welcome": {"on": True},
             "trial": {"plan": "lite", "until": plans.EVENT_OPEN_UNTIL, "reason": "event", "cohort": "EV-1"}}
    plans.EVENTS["EV-1"] = {"day": 1, "runs": None, "images": None}
    try:
        later = next(k for k, m in plans.META.items() if m["on"] and m.get("show", True) and plans.feature_day(k) > 1)
        assert not plans.day_locked(owner, later) and plans.has(owner, later)
        assert plans.day_locked({**owner, "welcome": {"on": False}}, later)
    finally:
        plans.EVENTS.pop("EV-1", None)
