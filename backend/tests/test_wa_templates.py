"""Ready WhatsApp messages: 20 of them, filled with the customer's first name and the business's own details (no AI run, no
blanks), and sending one to an inquiry answers it and withdraws the AI proposal waiting in the Send list.
Every name and number here is invented."""
from app import wa_templates as wt
from app.db import db
from app.routers import inquiries
from tests.test_v21_p0 import owner_on, used_runs


def test_twenty_templates_in_groups_and_no_blank_left_in_any():
    assert len(wt.TEMPLATES) == 20 and len(wt.IDS) == 20
    assert wt.GROUPS == ["First reply", "Price & quote", "Visit & meeting", "Follow-up", "After the sale"]
    full = {"name": "Amit", "business": "Shree Interiors", "owner": "Riya", "city": "Pune", "website": "https://shree.example"}
    for t in wt.filled(full):
        assert "{" not in t["text"] and "[[" not in t["text"] and "  " not in t["text"], t["id"]
    bare = {t["id"]: t["text"] for t in wt.filled({"business": "Shree Interiors", "owner": "Riya"})}
    assert bare["thanks"].startswith("Hi there, thank you for reaching out to Shree Interiors! I'm Riya.")
    assert "see our work" not in bare["intro"] and " in ." not in bare["intro"]
    assert bare["intro"].startswith("Hi there, this is Shree Interiors — Riya here. We'd be glad to help you.")
    nobody = {t["id"]: t["text"] for t in wt.filled({"business": "Shree Interiors"})}     # no owner name saved yet
    assert "I'm" not in nobody["thanks"] and nobody["busy_now"].endswith("— Shree Interiors")
    assert nobody["intro"].startswith("Hi there, this is Shree Interiors. We'd")
    assert bare["referral"].endswith("We'll take good care of them.")


async def test_templates_are_filled_for_the_business_and_the_customer(client):
    c, uid, site = await owner_on("9853000001", "lite")
    before = await used_runs(uid)
    d = (await c.get("/api/wa-templates", params={"name": "amit kale"})).json()
    assert len(d["templates"]) == 20 and d["groups"][0] == "First reply"
    thanks = next(t for t in d["templates"] if t["id"] == "thanks")["text"]
    b = await db().businesses.find_one({"owner_id": uid})
    assert thanks.startswith(f"Hi Amit, thank you for reaching out to {b['name']}!")
    assert await used_runs(uid) == before


async def test_sending_a_template_answers_the_inquiry_and_withdraws_the_proposal(client):
    c, uid, _ = await owner_on("9853000002", "lite")
    r = await c.post("/api/inquiries/whatsapp", json={"name": "Amit Kale", "phone": "98900 33001", "message": "Need modular kitchen price"})
    await inquiries.settle()
    lead = await db().leads.find_one({"_id": r.json()["id"]})
    aid = lead["proposal"]["approval_id"]
    assert (await c.post(f"/api/inquiries/{lead['_id']}/template", json={"template": "nope", "text": "Hi"})).status_code == 400
    r = await c.post(f"/api/inquiries/{lead['_id']}/template", json={"template": "price_range", "text": "Hi Amit, the price depends…"})
    assert r.status_code == 200, r.text
    assert r.json()["link"].startswith("https://wa.me/919890033001?text=Hi%20Amit")
    assert (await db().approvals.find_one({"_id": aid}))["status"] == "expired"     # no second first reply from the Send list
    item = (await c.get("/api/inquiries")).json()["items"][0]
    assert item["status"] == "proposal_sent" and item["template_reply"]["text"] == "Hi Amit, the price depends…"
