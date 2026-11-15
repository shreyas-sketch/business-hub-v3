"""Regression tests for the independent code review (security and production-readiness)."""
import asyncio
import os
import subprocess
import sys

import httpx

from app.config import settings
from app.db import db
from app.routers.hub import wa_digits
from tests.conftest import PROFILE, go_live, login, new_owner

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


async def test_parallel_wrong_codes_cannot_exceed_five_guesses(client):
    await client.post("/api/auth/otp", json={"phone": "9827011111"})
    guesses = [client.post("/api/auth/verify", json={"phone": "9827011111", "code": f"{n:06d}"}) for n in range(1, 41)]
    results = await asyncio.gather(*guesses)
    checked = [r for r in results if "not right" in r.text]
    assert len(checked) <= 5 and all(r.status_code in (400, 429) for r in results)


async def test_a_code_works_once(client):
    code = (await client.post("/api/auth/otp", json={"phone": "9827022222"})).json()["dev_code"]
    a, b = await asyncio.gather(client.post("/api/auth/verify", json={"phone": "9827022222", "code": code}),
                                client.post("/api/auth/verify", json={"phone": "9827022222", "code": code}))
    assert sorted([a.status_code, b.status_code]) == [200, 400]


async def test_only_indian_numbers_get_codes(client):
    r = await client.post("/api/auth/otp", json={"phone": "+44 7700 900123"})
    assert r.status_code == 400 and "Indian" in r.json()["detail"]


async def test_long_tracking_values_never_block_login(client):
    code = (await client.post("/api/auth/otp", json={"phone": "9827033333"})).json()["dev_code"]
    r = await client.post("/api/auth/verify", json={"phone": "9827033333", "code": code, "src": "instagram-bio-october-campaign",
                                                    "ref": "NOT-A-REAL-CODE-AT-ALL", "cohort": "x" * 120})
    assert r.status_code == 200, r.text


async def test_spoofed_forwarded_for_does_not_reset_limits(client):
    await login(client, "9827044444")
    site = await go_live(client)
    codes = []
    for n in range(7):  # the visitor makes up a new left-hand address each time; our proxy's entry stays the same
        r = await client.post(f"/s/{site['slug'].upper()}/inquiry", json={"name": "Spam Bot", "phone": f"98330000{n:02d}"},
                              headers={"x-forwarded-for": f"10.0.0.{n}, 49.36.10.20"})
        codes.append(r.status_code)
    assert codes[:5] == [200] * 5 and codes[5:] == [429, 429]


async def _demo_whatsapp(uid):
    from app import connections
    await connections.save(uid, "whatsapp", {"provider": "demo", "tpl_instant": "instant_v1"}, "test", "test")


async def test_per_site_ceiling_and_one_instant_reply_per_number(client):
    await login(client, "9827055555")
    site = await go_live(client)
    admin = await new_owner("9999900000")
    uid = (await client.get("/api/me")).json()["user"]["id"]
    await admin.patch(f"/api/admin/users/{uid}", json={"plan": "growth"})
    await _demo_whatsapp(uid)
    for n in range(3):
        await client.post(f"/s/{site['slug']}/inquiry", json={"name": "Ravi Kumar", "phone": "9833012345"}, headers={"x-forwarded-for": f"49.36.0.{n}"})
    replies = [l["instant_reply"] for l in (await client.get("/api/leads")).json()]
    assert sum(1 for a in replies if a["sent"]) == 1 and sum(1 for a in replies if a["via"] == "skipped-repeat") == 2
    object.__setattr__(settings, "inquiries_per_site_per_hour", 3)
    try:
        r = await client.post(f"/s/{site['slug']}/inquiry", json={"name": "Asha", "phone": "9833099999"}, headers={"x-forwarded-for": "49.36.9.9"})
        assert r.status_code == 429
    finally:
        object.__setattr__(settings, "inquiries_per_site_per_hour", 120)


async def test_per_address_limit_is_a_setting(client):
    await login(client, "9827055556")
    site = await go_live(client)
    one_wifi = {"x-forwarded-for": "49.36.7.7"}

    async def send(n):
        return await client.post(f"/s/{site['slug']}/inquiry", json={"name": "Guest", "phone": f"98330200{n:02d}"}, headers=one_wifi)

    for n in range(5):
        assert (await send(n)).status_code == 200
    assert (await send(5)).status_code == 429   # the default: 5 in 10 minutes from one address
    object.__setattr__(settings, "inquiries_per_ip_per_10min", 50)
    try:
        assert (await send(6)).status_code == 200   # a room on one Wi-Fi, once the limit is raised
    finally:
        object.__setattr__(settings, "inquiries_per_ip_per_10min", 5)


async def test_odd_honeypot_values_do_not_crash(client):
    await login(client, "9827066666")
    site = await go_live(client)
    r = await client.post(f"/s/{site['slug']}/inquiry", json={"website": 1, "name": "Bot", "phone": "9833000000"})
    assert r.status_code == 200 and await db().leads.count_documents({}) == 0


def test_whatsapp_numbers_typed_with_a_leading_zero():
    for typed in ("98200 12345", "098200 12345", "+91 98200 12345", "+91 098200 12345", "919820012345"):
        assert wa_digits(typed) == "919820012345", typed


async def test_reply_link_uses_the_normalised_number(client):
    await login(client, "9827077777")
    site = await go_live(client)
    await client.post(f"/s/{site['slug']}/inquiry", json={"name": "Meena Shah", "phone": "098250 12345"})
    lead = (await client.get("/api/leads")).json()[0]
    r = await client.post(f"/api/leads/{lead['id']}/reply")
    assert r.json()["whatsapp_link"].startswith("https://wa.me/919825012345?text=")


async def test_removing_an_admin_phone_revokes_access_at_once(client):
    a = await new_owner("9999900000")
    assert (await a.get("/api/admin/pulse")).status_code == 200
    object.__setattr__(settings, "admin_phones", ())
    try:  # same session, no new login
        assert (await a.get("/api/admin/pulse")).status_code == 403
        assert (await a.get("/api/me")).json()["user"]["role"] == "owner"
    finally:
        object.__setattr__(settings, "admin_phones", ("+919999900000",))


async def test_got_it_clears_only_the_notice_shown(client):
    await login(client, "9827088888")
    uid = (await client.get("/api/me")).json()["user"]["id"]
    from app.services import notify
    await notify(uid, "First"); await notify(uid, "Second")
    shown = (await client.get("/api/me")).json()["notices"][0]
    await client.post("/api/notices/read", json={"ids": [shown["id"]]})
    left = (await client.get("/api/me")).json()["notices"]
    assert len(left) == 1 and left[0]["id"] != shown["id"]


def _boot(env: dict) -> subprocess.CompletedProcess:
    clean = {k: v for k, v in os.environ.items() if k not in ("APP_ENV", "APP_URL", "JWT_SECRET")}
    return subprocess.run([sys.executable, "-c", "import app.config"], cwd=BACKEND, env={**clean, **env}, capture_output=True, text=True)


def test_deployment_fails_closed_when_settings_are_missing():
    assert "JWT_SECRET" in _boot({}).stderr  # nothing set → treated as production, refuses to start without a secret
    assert "APP_URL" in _boot({"JWT_SECRET": "x" * 40}).stderr  # …and without its public https address
    assert _boot({"JWT_SECRET": "x" * 40, "APP_URL": "https://hub.example.in"}).returncode == 0
    assert _boot({"APP_ENV": "development", "APP_URL": "http://localhost:8000"}).returncode == 0  # local dev still easy
    assert _boot({"APP_ENV": "development", "APP_URL": "http://localhost:8000", "RAILWAY_ENVIRONMENT": "production"}).returncode != 0


async def test_pause_takes_the_owner_and_website_offline_until_resumed(client):
    await login(client, "9827099999")
    site = await go_live(client)
    admin = await new_owner("9999900000")
    uid = (await client.get("/api/me")).json()["user"]["id"]
    await admin.patch(f"/api/admin/users/{uid}", json={"disabled": True})
    assert (await client.get("/api/me")).status_code == 401  # logged out everywhere
    assert (await client.get(f"/s/{site['slug']}")).status_code == 404
    row = [u for u in (await admin.get("/api/admin/users?q=9827099999")).json() if u["id"] == uid][0]
    assert row["disabled"] is True and row["leads"] == 0 and row["site"] == "live"
    await admin.patch(f"/api/admin/users/{uid}", json={"disabled": False})
    assert (await client.get(f"/s/{site['slug']}")).status_code == 200


def test_customers_are_addressed_properly():
    from app.text import greeting_name
    assert greeting_name("Mrs. Kulkarni") == "Mrs. Kulkarni"
    assert greeting_name("Dr Mehta Ji") == "Dr Mehta"
    assert greeting_name("Neha Joshi") == "Neha"
    assert greeting_name("Mr.") == "" and greeting_name("") == ""


async def test_instant_reply_and_draft_use_the_title_with_the_surname(client):
    await login(client, "9827012121")
    site = await go_live(client)
    admin = await new_owner("9999900000")
    uid = (await client.get("/api/me")).json()["user"]["id"]
    await admin.patch(f"/api/admin/users/{uid}", json={"plan": "growth"})
    await _demo_whatsapp(uid)
    await client.post(f"/s/{site['slug']}/inquiry", json={"name": "Mrs. Kulkarni", "phone": "9833044444", "message": "Need a kitchen"})
    msg = await db().outbox.find_one({"kind": "instant"})
    assert msg["params"][0] == "Mrs. Kulkarni"
    lead = (await client.get("/api/leads")).json()[0]
    draft = (await client.post(f"/api/leads/{lead['id']}/reply")).json()["reply"]
    assert draft.startswith("Hello Mrs. Kulkarni,")
