"""Admin → Onboarding → Download CSV: name, email, business, access and the agents each member has used, for one access level or all.
Every business, person and number here is invented."""
import csv
import io
from datetime import timedelta

from app.db import db, now
from app.routers.onboarding import ACCESS_STATUSES, AGENT_NAMES, _agent_name
from app.services import new_id

from tests.conftest import login

ADMIN = "+919999900000"
URL = "/api/admin/onboarding/export.csv"


async def member(name, email="", **extra):
    uid = new_id()
    await db().users.insert_one({"_id": uid, "phone": f"+9198{uid[:8]}", "name": name, "plan": "free", "ref_code": uid[:12], "created_at": now(),
                                 **({"email": email} if email else {}), **extra})
    return uid


async def run(uid, kind, status="done"):
    await db().runs.insert_one({"_id": new_id(), "user_id": uid, "kind": kind, "status": status, "created_at": now()})


async def log(uid, agent, status="done"):
    await db().agent_log.insert_one({"_id": new_id(), "ws": uid, "agent": agent, "status": status, "text": "x", "at": now()})


L0 = {"welcome": {"on": True, "only": "starter", "at": now()}}


def rows_of(r):
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    assert r.content.startswith(b"\xef\xbb\xbf")     # Excel reads the names properly
    return list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))


async def test_the_file_has_name_email_access_and_agents(client):
    a = await member("Asha Rao", "asha@example.in", **L0)
    await run(a, "site")
    await run(a, "site")                  # used twice, listed once
    await run(a, "tool:content")
    await run(a, "kit:sop")
    await log(a, "lead_response")
    await log(a, "office_sales")
    await login(client, ADMIN)
    r = await client.get(URL)
    rows = rows_of(r)
    assert list(rows[0]) == ["Name", "Email", "Business", "Access", "Agents used"]
    row = next(x for x in rows if x["Name"] == "Asha Rao")
    assert row["Email"] == "asha@example.in" and row["Access"] == "L0 starter"
    assert row["Agents used"] == "AI office: Sales; Content Agent; Lead Response; SOP; Website builder"
    assert "attachment" in r.headers["content-disposition"] and "everyone" in r.headers["content-disposition"]
    assert r.headers["cache-control"] == "no-store"


async def test_each_access_level_downloads_only_its_own_members(client):
    await member("Starter Sam", **L0)
    await member("Full Farah", welcome={"on": True, "at": now()})
    await member("Paid Pooja", **L0, access={"lite": now() + timedelta(days=30)})    # joined after L0: she counts as paid now
    await member("Free Faiz")
    await member("Off Omar", **L0, disabled=True)
    await login(client, ADMIN)
    want = {"starter": "Starter Sam", "welcome": "Full Farah", "paid": "Paid Pooja", "free": "Free Faiz", "off": "Off Omar"}
    for status, name in want.items():
        names = [x["Name"] for x in rows_of(await client.get(URL, params={"status": status}))]
        assert names == [name], status
    assert "l0-starter" in (await client.get(URL, params={"status": "starter"})).headers["content-disposition"]
    everyone = [x["Name"] for x in rows_of(await client.get(URL))]
    assert set(want.values()) <= set(everyone)
    assert (await client.get(URL, params={"status": "nonsense"})).status_code == 400


async def test_every_status_the_screen_offers_can_be_downloaded(client):
    await login(client, ADMIN)
    for status in ACCESS_STATUSES:
        assert rows_of(await client.get(URL, params={"status": status})) == []


async def test_only_finished_work_counts_and_background_checks_do_not(client):
    a = await member("Meera Joshi")
    await run(a, "brand", "pending")
    await run(a, "site", "refunded")
    await log(a, "lead_response", "failed")
    await log(a, "followup", "skipped")
    await log(a, "digest")                # goes out by itself for every account
    await log(a, "compliance_reminders")
    await login(client, ADMIN)
    assert rows_of(await client.get(URL))[0]["Agents used"] == ""
    await log(a, "payment_reminder")      # scheduled, but it worked for this owner's customers
    assert rows_of(await client.get(URL))[0]["Agents used"] == "Payment reminders"


async def test_team_logins_and_admins_are_left_out_and_owners_cannot_download(client):
    boss = await member("Owner Oza")
    await member("Team Tara", team_of=boss)
    await member("Admin Anil", role="admin")
    await login(client, ADMIN)
    assert [x["Name"] for x in rows_of(await client.get(URL)) if x["Name"] in ("Owner Oza", "Team Tara", "Admin Anil")] == ["Owner Oza"]
    from tests.conftest import new_owner
    owner = await new_owner("9834055555")
    assert (await owner.get(URL)).status_code in (401, 403)
    await owner.aclose()


async def test_a_phone_signup_uses_the_business_email_and_search_narrows_the_file(client):
    a = await member("Ravi Shah")
    await db().businesses.insert_one({"_id": new_id(), "owner_id": a, "name": "Shah Traders", "city": "Surat", "email": "hello@shah.example.in"})
    b = await member("Lata Menon", "lata@example.in")
    await db().businesses.insert_one({"_id": new_id(), "owner_id": b, "name": "Menon Foods", "email": "shop@menon.example.in"})
    await login(client, ADMIN)
    rows = {x["Name"]: x["Email"] for x in rows_of(await client.get(URL))}
    assert rows["Ravi Shah"] == "hello@shah.example.in"     # no login email
    assert rows["Lata Menon"] == "lata@example.in"          # her own comes first
    assert [x["Name"] for x in rows_of(await client.get(URL, params={"q": "surat"}))] == ["Ravi Shah"]


async def test_a_phone_signup_with_no_name_or_email_is_still_identified_by_the_business(client):
    a = await member("")
    await db().businesses.insert_one({"_id": new_id(), "owner_id": a, "name": "=Mehta Works", "email": ""})
    await login(client, ADMIN)
    r = await client.get(URL)
    row = rows_of(r)[0]
    assert (row["Name"], row["Email"], row["Business"]) == ("", "", "'=Mehta Works")
    assert a[:8] not in r.text                               # the phone number stays out of a file meant for sharing


async def test_a_name_cannot_smuggle_a_formula_into_the_file(client):
    await member('=HYPERLINK("http://bad.example")')
    await member('  @SUM(A1)')
    await login(client, ADMIN)
    names = {x["Name"] for x in rows_of(await client.get(URL))}
    assert names == {"'=HYPERLINK(\"http://bad.example\")", "'@SUM(A1)"}


async def test_the_file_is_not_cut_off_at_the_screens_limit_and_is_logged(client):
    await db().users.insert_many([{"_id": new_id(), "phone": f"+91980{i:07d}", "name": f"Owner {i}", "plan": "free", "ref_code": f"r{i}", "created_at": now()} for i in range(620)])
    await login(client, ADMIN)
    assert len(rows_of(await client.get(URL))) == 620
    logged = await db().events.find_one({"type": "onboarding_export"})
    assert logged["meta"]["members"] == 620 and logged["meta"]["by"]


def test_an_unknown_kind_of_run_still_reads_as_words():
    assert _agent_name("new_thing:deep_dive") == "New thing deep dive"
    assert all(_agent_name(k) == v for k, v in AGENT_NAMES.items())
