"""v2.4 integration: what joins the growth features to the rest of the hub. Every business and number here is invented."""
from app import crm, lead_research, plans
from app.agents import growth, hooks, jobs
from app.db import db, now
from app.routers import inquiries
from tests.v24_helpers import owner_id, v24_member

PROFILE = {"name": "Invented Packaging Works", "city": "Bhiwandi", "industry": "Corrugated boxes and cartons made in our own factory",
           "offers": [{"name": "Corrugated boxes", "price": "", "unit": ""}], "ideal_customer": "FMCG distributors and garment exporters",
           "why_us": "Delivery in 5 days", "proof": "", "whatsapp": "98200 00071", "email": "hello@invented-pack.example.in", "language": "English"}


async def _everything_on(monkeypatch) -> dict:
    """Every plan feature and switch on, and each automatic action replaced by a recorder, so the test sees what on_new_lead
    would start."""
    started: dict[str, list] = {"followups": [], "instant_reply": [], "call": [], "proposal": [], "brief": []}

    async def is_on(*a, **k):
        return True

    async def worth(*a, **k):
        return True

    async def followups(ws, lead):
        started["followups"].append(lead["_id"])
        return []

    async def reply(owner, business, lead):
        started["instant_reply"].append(lead["_id"])
        return {"sent": False}

    async def call(owner, lead, minutes=3):
        started["call"].append(lead["_id"])
        return True

    async def wants(ws, lead):
        return True

    async def sync(owner, lead):
        return None

    monkeypatch.setattr(plans, "has", lambda owner, feature: True)
    monkeypatch.setattr(jobs, "is_on", is_on)
    monkeypatch.setattr(jobs, "is_on_for", is_on)
    monkeypatch.setattr(hooks, "_worth_following_up", worth)
    monkeypatch.setattr(hooks, "schedule_followups", followups)
    monkeypatch.setattr(hooks, "sync_later", sync)
    monkeypatch.setattr(hooks, "LEAD_RESPONSE_SOURCES", ())          # Lead Response has its own tests; keep it out of the way
    monkeypatch.setattr(growth, "instant_reply", reply)
    monkeypatch.setattr(growth, "schedule_call", call)
    monkeypatch.setattr(inquiries, "wants_proposal", wants)
    monkeypatch.setattr(inquiries, "after_lead", lambda owner, business, lead: started["proposal"].append(lead["_id"]))
    monkeypatch.setattr(lead_research, "after_lead", lambda owner, lead: started["brief"].append(lead["_id"]))
    return started


def _lead(ws: str, lid: str, source: str) -> dict:
    return {"_id": lid, "owner_id": ws, "name": "Invented Traders", "phone": "+919811100901", "email": "buyer@invented-traders.example.in",
            "source": source, "status": "new", "created_at": now()}


async def test_a_deal_from_find_new_customers_is_never_messaged_or_called_on_its_own(client, monkeypatch):
    c = await v24_member("9800420001")
    ws = await owner_id(c)
    owner = {"_id": ws, "phone": "+919800420001"}
    started = await _everything_on(monkeypatch)

    patch = await hooks.on_new_lead(owner, {}, None, _lead(ws, "lead-prospect", "prospecting"))
    assert started["followups"] == [] and started["instant_reply"] == [] and started["call"] == [] and started["proposal"] == []
    assert started["brief"] == ["lead-prospect"]                     # the brief still gets written
    assert "followups" not in patch and "instant_reply" not in patch and "call_scheduled" not in patch

    # the same lead from the website gets every automatic step, so the switch above is what stops them
    await hooks.on_new_lead(owner, {}, None, _lead(ws, "lead-web", "website"))
    assert started["followups"] == ["lead-web"] and started["instant_reply"] == ["lead-web"] and started["call"] == ["lead-web"]
    assert started["proposal"] == ["lead-web"] and started["brief"] == ["lead-prospect", "lead-web"]


def test_find_new_customers_has_a_plain_source_label():
    assert crm.SOURCE_LABEL["prospecting"] == "Find new customers"


async def test_suggest_with_ai_keeps_the_kinds_the_owner_typed(client):
    c = await v24_member("9800420002", profile=PROFILE)
    assert (await c.put("/api/prospects/kinds", json={"kinds": ["hotel kitchens"]})).status_code == 200
    r = await c.post("/api/prospects/kinds/suggest")
    assert r.status_code == 200, r.text
    kinds = r.json()["kinds"]
    assert kinds[0] == "hotel kitchens" and 2 <= len(kinds) <= 6


async def test_the_ai_team_feed_leaves_out_scheduled_runs_with_nothing_to_do(client):
    c = await v24_member("9800420003")
    ws = await owner_id(c)
    await jobs.record(ws, "outreach_writer", "skipped", "No follow-ups are due today.")                      # a daily run, nothing due
    await jobs.record(ws, "researcher", "skipped", "No automatic brief on Invented Traders: this month's automatic briefs are used.",
                      ref="lead:invented")                                                                    # tells the owner something
    await jobs.record(ws, "prospector", "done", "Searched Google Maps for builders in Pune: 3 found, 3 new", ref="search:x")
    assert await db().agent_log.count_documents({"ws": ws}) == 3
    feed = (await c.get("/api/ai-team")).json()["feed"]
    texts = [f["text"] for f in feed]
    assert "No follow-ups are due today." not in texts
    assert any(t.startswith("No automatic brief") for t in texts) and any(t.startswith("Searched Google Maps") for t in texts)


async def test_add_this_call_to_the_deal_files_a_scored_call_once(client):
    """Call coach's button calls the Pipeline's conversation endpoint (built by two different builders)."""
    c = await v24_member("9800420004", profile=PROFILE)
    member = (await c.post("/api/call-coach/members", json={"name": "Invented Salesperson"})).json()["member"]
    text = ("Customer: Do you make printed cartons? We need 2000 a month.\nMe: Yes. What size, and by when do you need them?\n"
            "Customer: 12 by 10 inches, next week. Please call me tomorrow.")
    r = await c.post("/api/call-coach/score", json={"member_id": member["id"], "text": text})
    assert r.status_code == 200, r.text
    call = r.json()["call"]
    deal = (await c.post("/api/crm/deals", json={"name": "Invented Traders", "phone": "98111 20009"})).json()
    deal_id = deal.get("id") or deal.get("deal", {}).get("id")
    body = {"text": call["text"], "source": "call", "call_id": call["id"]}
    first = await c.post(f"/api/crm/deals/{deal_id}/conversation", json=body)
    assert first.status_code == 200, first.text
    again = await c.post(f"/api/crm/deals/{deal_id}/conversation", json=body)            # pressed twice
    assert again.status_code == 200 and again.json()["update"]["id"] == first.json()["update"]["id"]
    assert await db().deal_updates.count_documents({"call_id": call["id"]}) == 1
    filed = (await c.get(f"/api/call-coach/calls/{call['id']}")).json()["call"]["filed"]
    assert len(filed) == 1
    # a call from another business can't be filed here
    other = await c.post(f"/api/crm/deals/{deal_id}/conversation", json={**body, "call_id": "not-mine"})
    assert other.status_code == 404
