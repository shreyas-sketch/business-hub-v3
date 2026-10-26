"""v2.3 Sales and Brand engines: the Revenue Multiplier's 45 ideas (15 per lever, checked: no rupee promises, no contact
details, no competitor's words), Ways to get more leads on its own page, the two brochures (paid members only, text, contact
details added by the hub), and the Social media page (7 starter posts from the hub's own data, images counted per month: 7 on
the free plan and for event accounts, 30 for paid members). Every business, person and number here is invented."""
from datetime import timedelta

from app import growth_context, plans
from app.ai import v23_sales_tasks
from app.db import db, now
from app.routers import socialmedia
from tests.conftest import PROFILE, new_owner
from tests.v23_helpers import owner_id, setup_v23, v23_member


async def used_tokens(uid: str) -> int:
    u = await db().usage.find_one({"user_id": uid}) or {}
    return int(u.get("used") or 0)


async def event_member(phone: str, cohort: str = "TIMEBACK1"):
    c = await v23_member(phone, paid=False)
    uid = await owner_id(c)
    await db().users.update_one({"_id": uid}, {"$set": {"trial": {"plan": "lite", "until": now() + timedelta(days=3), "reason": "event",
                                                                  "cohort": cohort}}})
    return c, uid


# ═════════════════════════ Revenue Multiplier ═════════════════════════
async def test_multiplier_writes_15_ideas_per_lever_with_sources_and_feature_links(client):
    c = await v23_member("9861000001")
    uid = await owner_id(c)
    me = (await c.get("/api/me")).json()
    sales = next(g for g in me["menu"] if g.get("section") == "sales")
    assert [i["label"] for i in sales["items"]] == ["Revenue Multiplier", "Ways to get more leads", "Invoicing", "Win-Back"]
    assert (await c.put("/api/levers", json={"customers": 120, "avg_value": 150000, "frequency": 1})).json()["levers"]["result"]["multiplier"] == 1.331

    page = (await c.get("/api/multiplier")).json()
    assert page["total"] == 0 and page["tokens_per_lever"] == 1 and page["tokens_all"] == 3
    before = await used_tokens(uid)
    for lever in ("customers", "value", "frequency"):
        r = await c.post(f"/api/multiplier/{lever}")
        assert r.status_code == 200, r.text
    page = r.json()
    assert page["total"] == 45 and await used_tokens(uid) == before + 3
    for lv in page["levers"]:
        assert len(lv["ideas"]) == 15
        for i in lv["ideas"]:
            assert i["title"] and i["why"] and i["first_step"] and i["effort"] in ("low", "medium", "high") and i["impact"] in ("low", "medium", "high")
            assert i["source"] in ("knowledge", "website", "lead_magnet", "competitor", "business_type")
            if i["feature"]:
                assert i["path"] == plans.META[i["feature"]]["path"] and i["locked"] is False
    # no website, guide or competitor yet: no idea claims one as its source
    assert not [i for lv in page["levers"] for i in lv["ideas"] if i["source"] in ("website", "lead_magnet", "competitor")]

    # doing / done are kept when the lever is written again
    first = page["levers"][0]["ideas"][0]
    page = (await c.post(f"/api/multiplier/customers/{first['key']}/status", json={"status": "doing"})).json()
    assert page["doing"] == 1
    page = (await c.post("/api/multiplier/customers")).json()
    kept = page["levers"][0]["ideas"]
    assert len(kept) == 15 and kept[0]["key"] == first["key"] and kept[0]["status"] == "doing"
    assert (await c.post("/api/multiplier/customers/nope/status", json={"status": "done"})).status_code == 404
    assert (await c.post("/api/multiplier/salary")).status_code == 404


async def test_multiplier_drops_rupee_promises_contacts_and_the_competitors_words(client, monkeypatch):
    c = await v23_member("9861000002")
    uid = await owner_id(c)
    words = "Same day installation with a ten year written warranty on every kitchen"

    async def comp(ws):
        return {"offers": ["Modular kitchens", words], "positioning": "Premium kitchens for new flats"}
    monkeypatch.setattr(growth_context, "competitor", comp)

    def idea(n, **kw):
        return {"title": f"Idea number {n}", "why": "A competitor offers a design visit; you don't yet.", "source": "competitor",
                "first_step": "Call five past customers", "effort": "low", "impact": "high", "feature": "lead_magnet", **kw}
    good = [idea(n) for n in range(11)]
    bad = [idea(20, why="This will add ₹5,00,000 a year."), idea(21, first_step="Call 98201 23456 today"),
           idea(22, title="Grow 40% this quarter"), idea(23, why=f"Say: {words.lower()}."), idea(24, first_step="See www.example-rival.in")]

    async def fake(system, prompt, mock, max_tokens=1200):
        assert "<<<COMPETITOR DATA>>>" in prompt and "never" in prompt.lower()
        return {"ideas": good + bad}, None
    monkeypatch.setattr(v23_sales_tasks, "complete", fake)
    page = (await c.post("/api/multiplier/value")).json()
    ideas = page["levers"][1]["ideas"]
    assert len(ideas) == 11 and {i["title"] for i in ideas} == {f"Idea number {n}" for n in range(11)}
    assert all(i["source"] == "competitor" for i in ideas) and page["sources"]["competitor"] is True

    # too few usable ideas: the token is given back and nothing changes
    async def thin(system, prompt, mock, max_tokens=1200):
        return {"ideas": good[:4] + bad}, None
    monkeypatch.setattr(v23_sales_tasks, "complete", thin)
    before = await used_tokens(uid)
    assert (await c.post("/api/multiplier/frequency")).status_code == 502
    assert await used_tokens(uid) == before


async def test_ways_to_get_more_leads_has_its_own_page(client):
    c = await v23_member("9861000003")
    me = (await c.get("/api/me")).json()
    item = next(i for g in me["menu"] for i in g["items"] if i["key"] == "lead_ideas")
    assert item["path"] == "/lead-ideas" and not item["locked"]
    d = (await c.post("/api/levers/ideas")).json()
    assert 5 <= len(d["current"]["ideas"]) <= 7
    free = await v23_member("9861000004", paid=False)
    assert (await free.get("/api/levers/ideas")).status_code == 403


# ═════════════════════════ Brochures ═════════════════════════
async def test_brochures_are_for_paid_members_and_the_hub_adds_the_contact_details(client, monkeypatch):
    ev, _ = await event_member("9861000010")
    await ev.post("/api/brand-kit/generate")
    d = (await ev.get("/api/brochures")).json()
    assert d["open"] is False and d["locked_note"] == "Opens when you join OwnerDesk Membership."
    assert (await ev.post("/api/brochures/business/generate")).status_code == 403

    c = await v23_member("9861000011")
    uid = await owner_id(c)
    await c.post("/api/brand-kit/generate")
    before = await used_tokens(uid)
    d = (await c.post("/api/brochures/business/generate")).json()
    assert d["open"] is True and await used_tokens(uid) == before + 1
    b = next(k for k in d["kinds"] if k["key"] == "business")["brochure"]["content"]
    assert b["headline"] and len(b["sections"]) >= 2
    assert d["contact"][0] == f"WhatsApp: {PROFILE['whatsapp']}"
    d = (await c.post("/api/brochures/products/generate")).json()
    prod = next(k for k in d["kinds"] if k["key"] == "products")["brochure"]["content"]
    assert prod["sections"][0]["heading"] == "Modular kitchen" and "₹1,85,000 onwards" in prod["sections"][0]["points"]

    # the owner edits a line; the download carries it and the hub's contact block
    b["headline"] = "Kitchens finished on the date we promise"
    assert (await c.put("/api/brochures/business", json=b)).status_code == 200
    txt = (await c.get("/api/brochures/business.txt")).text
    assert "Kitchens finished on the date we promise" in txt and f"WhatsApp: {PROFILE['whatsapp']}" in txt

    # contact details, prices the owner never wrote and invented proof are dropped
    async def fake(system, prompt, mock, max_tokens=1200):
        return {"headline": "Interiors done right", "subheadline": "Call 98200 11111 now",
                "sections": [{"heading": "About us", "text": "We design homes. Write to sales@rival.example.", "points": ["Own factory", "Kitchens from ₹99,000"]},
                             {"heading": "Why us", "text": "Fixed timelines.", "points": ["Transparent quotation"]}],
                "proof": ["500 happy families"], "cta": "Visit www.example.in"}, None
    monkeypatch.setattr(v23_sales_tasks, "complete", fake)
    d = (await c.post("/api/brochures/business/generate")).json()
    b = next(k for k in d["kinds"] if k["key"] == "business")["brochure"]["content"]
    assert b["subheadline"] == "" and b["sections"][0]["text"] == "We design homes." and b["sections"][0]["points"] == ["Own factory"]
    assert b["proof"] == [] and "www" not in b["cta"]


# ═════════════════════════ Social media ═════════════════════════
async def test_event_account_gets_7_ready_posts_and_7_images(client):
    c, uid = await event_member("9861000020")
    await c.post("/api/brand-kit/generate")
    tokens = await used_tokens(uid)
    d = (await c.get("/api/social-media")).json()
    assert len(d["posts"]) == 7 and all(p["image"] and p["image_url"] for p in d["posts"])
    assert d["images"]["limit"] == 7 and d["images"]["used"] == 7 and d["images"]["left"] == 0 and d["images"]["paid"] is False
    assert await used_tokens(uid) == tokens                       # the starter posts use no AI token
    offer = next(p for p in d["posts"] if p["template"] == "offer" and p["fields"]["title"] == "Modular kitchen")
    assert offer["fields"]["price"] == "₹1,85,000 onwards"                # exactly as the owner wrote it
    # opening the page again makes nothing new
    assert len((await c.get("/api/social-media")).json()["posts"]) == 7
    png = await c.get(d["posts"][0]["image_url"])
    assert png.status_code == 200 and png.content[:8] == b"\x89PNG\r\n\x1a\n"
    # at the limit: drawing again is stopped, with the way to Membership
    r = await c.post(f"/api/social-media/posts/{d['posts'][0]['id']}/image")
    assert r.status_code == 429 and r.json()["detail"]["upgrade"] is True and "30 images a month" in r.json()["detail"]["message"]


async def test_paid_member_gets_30_images_a_month_and_posts_reach_the_posting_log(client, monkeypatch):
    c = await v23_member("9861000021")
    uid = await owner_id(c)
    d = (await c.get("/api/social-media")).json()
    assert d["images"]["limit"] == 30 and d["images"]["used"] == 7 and d["images"]["paid"] is True
    before = await used_tokens(uid)
    d = (await c.post("/api/social-media/write")).json()
    assert d["made"] == 7 and d["images_made"] == 7 and d["images"]["used"] == 14 and await used_tokens(uid) == before + 1

    post = d["posts"][0]
    d = (await c.post(f"/api/social-media/posts/{post['id']}/posted", json={"platform": "Instagram"})).json()
    assert next(p for p in d["posts"] if p["id"] == post["id"])["posted"] is True
    log = (await c.get("/api/social/log")).json()
    assert log["summary"]["month"] == 1 and log["items"][0]["ref"] == f"sm:{post['id']}"

    # a caption edit is free; new image text makes the image stale, and drawing it again is a new image
    d = (await c.put(f"/api/social-media/posts/{post['id']}", json={"caption": "New caption"})).json()
    p = next(x for x in d["posts"] if x["id"] == post["id"])
    assert p["caption"] == "New caption" and p["image"] is True and d["images"]["used"] == 14
    d = (await c.put(f"/api/social-media/posts/{post['id']}", json={"fields": {"headline": "Kitchens on time"}})).json()
    assert next(x for x in d["posts"] if x["id"] == post["id"])["image"] is False
    d = (await c.post(f"/api/social-media/posts/{post['id']}/image")).json()
    assert d["images"]["used"] == 15

    # the 1st of the month: the count starts again
    monkeypatch.setattr(socialmedia, "month_now", lambda: "2099-01")
    assert (await c.get("/api/social-media")).json()["images"]["used"] == 0


async def test_social_media_is_locked_for_a_free_account_and_waits_for_the_knowledge_hub(client):
    free = await v23_member("9861000030", paid=False)
    assert (await free.get("/api/social-media")).status_code == 403
    await setup_v23()
    c = await new_owner("9861000031")
    await db().users.update_one({"_id": await owner_id(c)}, {"$set": {"access.lite": now() + timedelta(days=30)}})
    d = (await c.get("/api/social-media")).json()
    assert d["posts"] == [] and d["has_profile"] is False
