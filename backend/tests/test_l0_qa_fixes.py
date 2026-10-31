"""Fixes from the L0 click-through on 31 Oct 2026: tokens for a website edit that changes nothing, social post numbers after
a delete, the inquiry's "writing the proposal" state, the Engine Audit tile, the AI team tile's link and the GST 2.0 slabs.
Every business, person and number used here is invented."""
from app import gst, plans
from app.dashboard_widgets import REGISTRY
from app.db import db
from app.routers import dashboard, inquiries, sitemagnets
from tests.test_v21_p0 import owner_on, used_runs
from tests.v23_helpers import owner_id, v23_member


# ───────────────────────── website: edit by prompt ─────────────────────────
async def test_a_prompt_edit_that_changes_nothing_gives_the_token_back_and_offers_only_the_fact(client, monkeypatch):
    c, uid, _ = await owner_on("9871000001", "free")

    async def nothing(p, content, instruction):   # the AI only lists the fact, with its own note after it
        return {"patch": {}, "new_facts": ["3 national awards: not in the business profile yet. Confirm the award names and years"],
                "notes": []}, None
    monkeypatch.setattr(sitemagnets.site_tasks, "site_patch", nothing)
    before = await used_runs(uid)
    pv = (await c.post("/api/site/prompt", json={"instruction": "Say we won 3 national awards"})).json()
    assert pv["changed"] == [] and pv["refunded"] is True
    assert pv["new_facts"] == ["3 national awards"]
    assert await used_runs(uid) == before


async def test_a_fact_keeps_all_its_words():
    from app.ai.site_tasks import facts_in
    assert facts_in("offer 50% off logo design this month") == ["50% off logo design this month"]


# ───────────────────────── social media ─────────────────────────
async def test_new_posts_are_numbered_after_the_highest_even_after_a_delete(client):
    c = await v23_member("9871000002")
    d = (await c.get("/api/social-media")).json()
    third = next(p for p in d["posts"] if p["n"] == 3)
    await c.delete(f"/api/social-media/posts/{third['id']}")
    d = (await c.post("/api/social-media/write")).json()
    ns = [p["n"] for p in d["posts"]]
    assert len(ns) == len(set(ns)) and max(ns) == 14


async def test_starter_posts_never_repeat_a_caption():
    from datetime import date
    from app.routers.socialmedia import starter_posts
    out = starter_posts({"name": "Rangoli Prints", "industry": "Printing for small shops", "city": "Pune"}, {}, {}, date(2026, 10, 31))
    caps = [" ".join(x["caption"].split()).lower() for x in out]
    assert len(caps) == len(set(caps))


# ───────────────────────── inquiries ─────────────────────────
async def test_a_pasted_inquiry_stops_showing_writing_once_the_proposal_is_in(client):
    c, uid, _ = await owner_on("9871000003", "lite")
    r = await c.post("/api/inquiries/whatsapp", json={"name": "Rekha Pawar", "phone": "98900 33003", "message": "Need a kitchen quote"})
    await inquiries.settle()
    lead = await db().leads.find_one({"_id": r.json()["id"]})
    assert "proposal_writing" not in lead and lead["proposal"]
    item = (await c.get("/api/inquiries")).json()["items"][0]
    assert item["proposal_writing"] is False and item["proposal"]
    assert "auto_quote" in (await c.get("/api/inquiries/settings")).json()


# ───────────────────────── dashboard ─────────────────────────
async def test_the_dashboard_shows_the_latest_engine_audit(client):
    c = await v23_member("9871000004")
    uid = await owner_id(c)
    tile = {t["key"]: t for t in (await c.get("/api/dashboard")).json()["tiles"]}["audit"]
    assert tile["empty"] and tile["empty_state"]["path"] == "/audit"
    items = (await c.get("/api/audit")).json()["items"]
    answers = {it["key"]: {"status": "missing" if i == 0 else "have"} for i, it in enumerate(items)}
    assert (await c.post("/api/audit", json={"answers": answers})).status_code == 200
    from app import dashboard_cache
    dashboard_cache.bust(uid)
    tile = {t["key"]: t for t in (await c.get("/api/dashboard")).json()["tiles"]}["audit"]
    d = tile["data"]
    assert not tile["empty"] and d["in_place"] == len(items) - 1 and d["total"] == len(items) and d["top_gap"]["statement"]


async def test_the_ai_team_tile_opens_a_page_the_owner_has(monkeypatch):
    w = REGISTRY["growth"]
    monkeypatch.setattr(plans, "has", lambda owner, f: f == "whatsapp_ai")       # AI results switched off, chats on
    assert dashboard.link_for({}, w) == plans.META["whatsapp_ai"]["path"]
    monkeypatch.setattr(plans, "has", lambda owner, f: f in ("whatsapp_ai", "results_report"))
    assert dashboard.link_for({}, w) == "/results"


# ───────────────────────── GST 2.0 ─────────────────────────
async def test_gst_offers_the_new_slabs_and_still_takes_an_older_one(client):
    c = await v23_member("9871000005")
    assert gst.offered() == [0, 0.25, 3, 5, 18, 40] and 28 in gst.offered(28)
    from app.knowledge import today_iso
    money = {"payment_terms_days": 15, "bank_balance": 50000, "bank_balance_on": today_iso(), "gst_rate": 40}
    assert (await c.put("/api/knowledge/money", json=money)).status_code == 200
    assert (await c.put("/api/knowledge/money", json={**money, "gst_rate": 28})).status_code == 200     # an older setting stays valid
    assert (await c.put("/api/knowledge/money", json={**money, "gst_rate": 13})).status_code == 422
