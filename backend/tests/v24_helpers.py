"""Shared helpers for the v2.4 (growth features) tests. Every business, person and number used with them is invented.

    owner = await v24_member("9800400001")               # a paying member on the v2.3 + v2.4 setup, profile filled
    owner = await v24_member("9800400002", paid=False)   # a free owner (event ended, or never paid)
"""
from app import plans, v23, v24
from tests.v23_helpers import admin_client, mark_paid, owner_id, v23_member  # noqa: F401  (re-exported for the v2.4 tests)


async def setup_v24() -> None:
    """The v2.3 setup, then the v2.4 one, as a real deployment's first start does."""
    if not plans.engines_on():
        await v23.apply("test")
    await v24.apply("test")


async def v24_member(phone: str, paid: bool = True, profile: dict | None = None):
    await setup_v24()
    return await v23_member(phone, paid=paid, profile=profile, setup=False)
