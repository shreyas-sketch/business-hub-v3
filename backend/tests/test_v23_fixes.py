"""Fixes found in the v2.3 Conversion and Operations test pass (28 Oct): impossible dates in the CRM, the "Hello WhatsApp,"
greeting, and team logins on the v2.3 setup. Every business, person and number here is invented."""
from app.db import db
from app.text import greeting_name
from tests.test_team import join, owner_on
from tests.v23_helpers import setup_v23, v23_member


async def test_an_impossible_date_is_refused_before_anything_is_saved(client):
    c = await v23_member("9800900001")
    for day in ("2026-02-30", "2026-13-01", "2027-02-29"):
        r = await c.post("/api/crm/deals", json={"name": "Ravi Shah", "phone": "98200 90001", "follow_up_on": day})
        assert r.status_code == 422 and "real date" in r.text, r.text
        r = await c.post("/api/crm/deals", json={"name": "Ravi Shah", "expected_close": day})
        assert r.status_code == 422, r.text
    assert await db().leads.count_documents({"name": "Ravi Shah"}) == 0          # nothing half-saved
    deal = (await c.post("/api/crm/deals", json={"name": "Meena Iyer", "phone": "98200 90002", "follow_up_on": "2028-02-29"})).json()
    assert deal["follow_up_on"] == "2028-02-29"                                    # a real leap day is fine
    r = await c.patch(f"/api/crm/deals/{deal['id']}", json={"follow_up_on": "2026-04-31"})
    assert r.status_code == 422
    assert (await c.patch(f"/api/crm/deals/{deal['id']}", json={"follow_up_on": ""})).json()["follow_up_on"] is None
    cid = deal["contact"]["id"]
    r = await c.patch(f"/api/crm/contacts/{cid}", json={"follow_up_on": "2026-13-01"})
    assert r.status_code == 422
    assert (await db().contacts.find_one({"_id": cid}))["follow_up_on"] is None
    assert (await c.patch(f"/api/crm/contacts/{cid}", json={"follow_up_on": "2026-11-05"})).json()["follow_up_on"] == "2026-11-05"


async def test_a_whatsapp_inquiry_without_a_name_is_never_greeted_as_whatsapp(client):
    assert greeting_name("WhatsApp customer") == "" and greeting_name("customer") == ""
    assert greeting_name("Neha Joshi") == "Neha" and greeting_name("Mrs. Kulkarni") == "Mrs. Kulkarni"
    c = await v23_member("9800900003")
    r = await c.post("/api/inquiries/whatsapp", json={"name": "", "phone": "98200 90003", "message": "Price for a modular kitchen?"})
    assert r.status_code == 200, r.text
    lead = await db().leads.find_one({"_id": r.json()["id"]})
    assert lead["name"] == "WhatsApp customer"
    prop = lead.get("proposal") or (await c.post(f"/api/inquiries/{lead['_id']}/proposal")).json()["proposal"]
    for text in (prop["email_text"], prop["whatsapp_text"]):
        assert "WhatsApp," not in text and "Hello customer" not in text.lower()
        assert text.startswith("Hello,") or text.startswith("Hi,") or text.startswith("Hello there"), text[:40]


async def test_existing_growth_teams_still_log_in_on_the_v23_setup(client):
    await setup_v23()
    owner, _uid, admin = await owner_on("9800900004", plan="growth")
    staff, _ = await join(owner, "9800900005", "Ravi Kumar")
    assert (await staff.get("/api/crm/deals")).status_code == 200
    menu = (await owner.get("/api/me")).json()["menu"]
    account = next(g for g in menu if g["section"] == "account")
    assert any(i["key"] == "team_logins" and not i["locked"] for i in account["items"])
    # a Membership owner's menu stays as agreed: no Team, not even as a locked teaser
    member = await v23_member("9800900006")
    keys = [i["key"] for g in (await member.get("/api/me")).json()["menu"] for i in g["items"]]
    assert "team_logins" not in keys
    assert (await member.get("/api/team")).status_code == 403
