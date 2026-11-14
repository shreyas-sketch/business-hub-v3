"""A CRM follow-up reminder reaches the owner twice: a notice in the hub and the same line on their own WhatsApp, from the
hub's number on the owner-alert template. The customer is never messaged. Every business, person and number is invented."""
from datetime import timedelta

from app.agents import scheduler
from app.crm import today_ist
from app.db import db
from tests.v23_helpers import owner_id, v23_member


async def test_a_follow_up_due_today_goes_to_the_owners_whatsapp(client):
    c = await v23_member("9800940001")
    ws = await owner_id(c)
    d = (await c.post("/api/crm/deals", json={"name": "Ramesh Patel", "phone": "98200 94001"})).json()
    r = await c.patch(f"/api/crm/deals/{d['id']}", json={"follow_up_on": today_ist().isoformat(),
                                                         "follow_up_note": "Send the revised quotation"})
    assert r.status_code == 200, r.text
    job = await db().agent_jobs.find_one({"_id": f"crmfu:deal:{d['id']}"})
    await scheduler.tick(at=job["run_at"] + timedelta(minutes=1))

    notice = await db().notices.find_one({"user_id": ws, "text": {"$regex": "^Follow up today"}})
    assert notice["text"] == "Follow up today: Ramesh Patel — Send the revised quotation"
    wa = [o async for o in db().outbox.find({"channel": "whatsapp", "kind": "crm_followup"})]
    assert len(wa) == 1
    owner = await db().users.find_one({"_id": ws})
    assert wa[0]["to"] == owner["phone"]
    assert wa[0]["params"][1] == "Follow up today: Ramesh Patel — Send the revised quotation"
    assert not await db().outbox.find_one({"to": {"$regex": "9820094001$"}})   # never the customer
