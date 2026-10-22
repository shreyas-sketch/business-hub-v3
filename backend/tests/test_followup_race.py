"""The follow-up agent and a lead closed while the AI is writing (review fix): nothing is drafted, and nothing goes out on its own
— even on LegacyWorkforce with the agent allowed to act. Every name and number here is invented."""
from datetime import timedelta

from app.agents import runners, scheduler
from app.db import db
from tests.test_agents import QUIET, connect_demo_whatsapp, followup_jobs, inquiry, owner_on, quiet


async def test_followup_stops_when_the_lead_is_won_or_deleted_while_the_ai_writes(client, monkeypatch):
    c, uid, site = await owner_on("9834501100", "office")
    await quiet(c, *QUIET)
    await connect_demo_whatsapp(uid)
    assert (await c.patch("/api/agents/followup", json={"autonomy": "act"})).status_code == 200      # may send on its own
    won = await inquiry(client, site, "Tara Bhosle", "98345 01101", "Wardrobe for the bedroom")
    gone = await inquiry(client, site, "Nikhil Jain", "98345 01102", "Office cabins")
    real = runners.run_ai

    async def while_writing(owner, kind, job, title, **kw):
        out = await real(owner, kind, job, title, **kw)
        if kind == "followup" and "Tara" in title:            # the owner marks it won while the AI writes
            await db().leads.update_one({"_id": won["_id"]}, {"$set": {"status": "won"}})
        if kind == "followup" and "Nikhil" in title:          # ... or deletes it
            await db().leads.delete_one({"_id": gone["_id"]})
        return out
    monkeypatch.setattr(runners, "run_ai", while_writing)
    jw, jg = (await followup_jobs(won["_id"]))[0], (await followup_jobs(gone["_id"]))[0]
    await scheduler.tick(at=max(jw["run_at"], jg["run_at"]) + timedelta(minutes=1))
    assert await db().approvals.count_documents({"ws": uid}) == 0
    assert await db().outbox.count_documents({"kind": "followup"}) == 0
    jw, jg = await db().agent_jobs.find_one({"_id": jw["_id"]}), await db().agent_jobs.find_one({"_id": jg["_id"]})
    assert jw["status"] == "skipped" and "marked won while the follow-up was being written" in jw["note"]
    assert jg["status"] == "skipped" and "deleted while the follow-up was being written" in jg["note"]

    # an open lead still gets its follow-up, sent on its own as before (LegacyWorkforce, "act")
    monkeypatch.setattr(runners, "run_ai", real)
    open_lead = await inquiry(client, site, "Farah Siddiqui", "98345 01103", "Kitchen")
    j = (await followup_jobs(open_lead["_id"]))[0]
    await scheduler.tick(at=j["run_at"] + timedelta(minutes=1))
    sent = await db().approvals.find_one({"lead_id": open_lead["_id"]})
    assert sent["status"] == "sent" and await db().outbox.count_documents({"kind": "followup"}) == 1
