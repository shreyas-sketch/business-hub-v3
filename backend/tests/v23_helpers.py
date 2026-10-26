"""Shared helpers for the v2.3 (OwnerDesk) tests. Every business, person and number used with them is invented.

    owner = await v23_member("9800000001")          # a paying Membership owner on the v2.3 setup, profile filled
    owner = await v23_member("9800000002", paid=False)   # a free owner (the event ended, or never paid)
"""
from datetime import timedelta

from app import plans, v23
from app.db import db, now
from tests.conftest import PROFILE, new_owner

ADMIN = "9999900000"


async def setup_v23() -> None:
    """Switches the test database to the v2.3 setup (the engine menu, hidden features, prices)."""
    await v23.apply("test")


async def admin_client():
    return await new_owner(ADMIN)


async def owner_id(client) -> str:
    return (await client.get("/api/me")).json()["user"]["id"]


async def mark_paid(uid: str, days: int = 30) -> None:
    """What Admin → Onboarding → Mark paid does: Membership until a date."""
    await db().users.update_one({"_id": uid}, {"$set": {"access.lite": now() + timedelta(days=days)}})


async def v23_member(phone: str, paid: bool = True, profile: dict | None = None, setup: bool = True):
    if setup and not plans.engines_on():
        await setup_v23()
    c = await new_owner(phone, name="Owner Test")
    r = await c.put("/api/business", json=profile or PROFILE)
    assert r.status_code == 200, r.text
    if paid:
        await mark_paid(await owner_id(c))
    return c
