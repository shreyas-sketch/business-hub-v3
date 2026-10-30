"""Email and password: sign up, log in, and the rules that stop an unverified number taking over anything."""
from app.db import db
from tests.conftest import login


async def _register(client, **kw):
    body = {"name": "Asha", "email": "asha@example.in", "password": "longenough1", "phone": "9811100001", **kw}
    return await client.post("/api/auth/register", json=body)


async def test_register_then_log_in_with_email(client):
    r = await _register(client)
    assert r.status_code == 200, r.text
    me = (await client.get("/api/me")).json()
    assert me["user"]["email"] == "asha@example.in" and me["user"]["phone"] == "+919811100001"
    assert "password" not in str(me)  # the hash never reaches the browser
    await client.post("/api/auth/logout")
    assert (await client.get("/api/me")).status_code == 401

    bad = await client.post("/api/auth/login", json={"email": "asha@example.in", "password": "wrong-password"})
    assert bad.status_code == 400
    ok = await client.post("/api/auth/login", json={"email": " ASHA@example.in ", "password": "longenough1"})
    assert ok.status_code == 200
    assert (await client.get("/api/me")).json()["user"]["email"] == "asha@example.in"


async def test_signup_rules(client):
    assert (await _register(client, password="short")).status_code == 400
    assert (await _register(client, email="not-an-email")).status_code == 400
    assert (await _register(client, phone="+919999900000")).status_code == 403  # admin numbers use the code
    assert (await _register(client)).status_code == 200
    await client.post("/api/auth/logout")
    assert (await _register(client, phone="9811100002")).status_code == 409  # same email
    assert (await _register(client, email="b@example.in")).status_code == 409  # same number


async def test_email_signup_cannot_take_an_existing_number_or_a_team_invite(client):
    await login(client, "9811100003")  # signed up with the code
    await client.post("/api/auth/logout")
    assert (await _register(client, phone="9811100003")).status_code == 409
    await db().team_invites.insert_one({"_id": "i1", "owner_id": "someone", "phone": "+919811100004", "status": "pending"})
    assert (await _register(client, phone="9811100004", email="c@example.in")).status_code == 409


async def test_add_and_change_email_login_on_a_mobile_account(client):
    await login(client, "9811100005")
    r = await client.put("/api/auth/password", json={"email": "owner@example.in", "password": "firstpass1"})
    assert r.status_code == 200
    # changing it needs the current password
    r = await client.put("/api/auth/password", json={"email": "owner@example.in", "password": "secondpass2"})
    assert r.status_code == 400
    r = await client.put("/api/auth/password", json={"email": "owner@example.in", "password": "secondpass2", "current_password": "firstpass1"})
    assert r.status_code == 200
    await client.post("/api/auth/logout")
    assert (await client.post("/api/auth/login", json={"email": "owner@example.in", "password": "secondpass2"})).status_code == 200


async def test_email_already_used_by_another_account(client):
    assert (await _register(client, email="taken@example.in", phone="9811100006")).status_code == 200
    await client.post("/api/auth/logout")
    await login(client, "9811100007")
    r = await client.put("/api/auth/password", json={"email": "taken@example.in", "password": "whatever12"})
    assert r.status_code == 409


async def test_paused_account_cannot_log_in_by_email(client):
    await _register(client, email="p@example.in", phone="9811100008")
    await client.post("/api/auth/logout")
    await db().users.update_one({"email": "p@example.in"}, {"$set": {"disabled": True}})
    assert (await client.post("/api/auth/login", json={"email": "p@example.in", "password": "longenough1"})).status_code == 403


async def test_the_code_wins_over_an_email_signup_that_used_someone_elses_number(client):
    # someone signs up by email with a number that isn't theirs; the number's real owner then logs in with the code
    assert (await _register(client, email="squat@example.in", phone="9811100009")).status_code == 200
    squatter = (await client.get("/api/me")).json()["user"]["id"]
    await client.post("/api/auth/logout")
    await login(client, "9811100009")
    me = (await client.get("/api/me")).json()["user"]
    assert me["id"] != squatter and me["phone"] == "+919811100009" and not me.get("email")
    await client.post("/api/auth/logout")
    # the email sign-up still logs in with its password, but no longer holds the number
    assert (await client.post("/api/auth/login", json={"email": "squat@example.in", "password": "longenough1"})).status_code == 200
    assert (await client.get("/api/me")).json()["user"]["id"] == squatter
    assert (await db().users.find_one({"_id": squatter}))["released_phone"] == "+919811100009"
