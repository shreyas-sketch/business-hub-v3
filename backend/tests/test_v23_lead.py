"""v2.3 Lead engine: the shared inputs (their website, a competitor's website and the 4 questions, pre-filled from the
Knowledge Hub), the competitor summary kept behind the scenes (never copied), the website built from their own site or
from the 4 answers with a preview before publishing, and the lead magnet: 8 ideas → pick → a PDF in the brand design with
its download page → on the website → a download is a lead. Every business, person and number here is invented."""
import io
from datetime import timedelta

import pytest
from pypdf import PdfReader

from app import competitor_watch as cw
from app.agents import catalog
from app.ai import magnet_tasks, site_tasks
from app.db import db, now
from app.routers import competitors as comp_router
from tests.test_competitors import html, serve
from tests.v23_helpers import owner_id, v23_member

UNIQUE = "Our zebra marmalade kitchens glow brighter than every other kitchen anywhere in Thane district"
RIVAL_PAGES = {
    "/": html(f"<h1>Kesar Kitchen Studio</h1><p>{UNIQUE}.</p><p>Get a free quote today</p><p>Visit our showroom in Thane</p>"
              "<ul><li><a href='/products'>Our products</a></li></ul>", "Kesar Kitchen Studio | Thane"),
    "/products": html("<h2>Products</h2><p>Modular kitchen ₹1,45,000 onwards</p><p>Island kitchens</p><p>Pantry units</p>", "Products"),
}
OWN_PAGES = {
    "/": html("<h1>Shree Ganesh Interiors</h1><p>We design and build modular kitchens and wardrobes in Thane with our own carpenters.</p>"
              "<p>Fixed timelines and a transparent quotation for every family.</p><a href='https://wa.me/919820000000'>WhatsApp</a>",
              "Shree Ganesh Interiors"),
}


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setattr(cw, "PAUSE_SECONDS", 0)


async def runs_used(c) -> int:
    return (await c.get("/api/me")).json()["runs"]["used"]


async def test_inputs_are_prefilled_from_the_knowledge_hub_and_saved_once(client):
    o = await v23_member("9811000001")
    d = (await o.get("/api/inputs")).json()
    inp = d["inputs"]
    assert inp["about"].startswith("Home and office interiors") and inp["sources"]["about"] == "brain"
    assert "Modular kitchen (₹1,85,000 onwards)" in inp["offers"] and inp["audience"].startswith("Families")
    assert inp["pains"] == "" and inp["has_website"] is None and not inp["confirmed"] and d["answered"]
    r = await o.put("/api/inputs", json={"competitors": ["kesar-kitchens.example.in"], "pains": "Delays and surprise costs", "has_website": False})
    assert r.status_code == 200, r.text
    inp = r.json()["inputs"]
    assert inp["competitor_url"].startswith("https://kesar-kitchens.example.in") and inp["sources"]["pains"] == "you"
    assert inp["has_website"] is False and inp["confirmed"]
    assert (await o.put("/api/inputs", json={"website_url": "not a site"})).status_code == 422
    # the shared inputs never create a Competitor Watch entry, so the Monday report and its WhatsApp alert don't start
    assert await db().competitors.count_documents({}) == 0


async def test_competitor_summary_is_kept_behind_the_scenes_and_never_copied(client, monkeypatch):
    o = await v23_member("9811000002")
    ws = await owner_id(o)
    with serve(dict(RIVAL_PAGES)) as (base, hits):
        await o.put("/api/inputs", json={"competitors": [base]})
        before = await runs_used(o)
        r = await o.post("/api/inputs/competitor/read")
        assert r.status_code == 200, r.text
        assert await runs_used(o) == before + 1
        assert "/robots.txt" in hits and "/products" in hits
    s = await cw.competitor_summary(ws)
    assert s and s["url"] == base and set(s["buy_path"]) >= {"quote", "visit"} and any("₹1,45,000" in p for p in s["pricing"])
    assert UNIQUE.lower() not in str(s).lower()
    assert "zebra marmalade" not in str(r.json()["competitor"]).lower()
    # a second read straight away is refused (no token spent)
    assert (await o.post("/api/inputs/competitor/read")).status_code == 429

    # the website: an AI that copies the competitor's sentence has it taken out
    orig = site_tasks.mock_site_from_inputs

    def copying(p, brand, inp, own_site, comp):
        c = orig(p, brand, inp, own_site, comp)
        c["about"] = f"{UNIQUE}. " + c["about"]
        c["why"] = [{"title": "Fixed timelines", "text": UNIQUE}] + c["why"]
        return c
    monkeypatch.setattr(site_tasks, "mock_site_from_inputs", copying)
    await o.put("/api/inputs", json={"has_website": False, "pains": "Delays and surprise costs"})
    r = await o.post("/api/inputs/website/build", json={"source": "answers"})
    assert r.status_code == 200, r.text
    content = r.json()["content"]
    assert "zebra marmalade" not in str(content).lower() and content["about"] and content["why"][0]["text"] == ""
    site = await db().sites.find_one({"owner_id": ws})
    assert site["built_with_competitor"] and site["status"] == "draft"


async def test_monthly_read_and_its_schedule(client):
    o = await v23_member("9811000003")
    ws = await owner_id(o)
    spec = catalog.AGENTS["competitor_summary"]
    assert spec.schedule["every"] == "month" and spec.feature == "competitor_watch"
    owner = await db().users.find_one({"_id": ws})
    assert ws not in await spec.ready([owner])
    with serve(dict(RIVAL_PAGES)) as (base, _hits):
        await o.put("/api/inputs", json={"competitors": [base]})
        assert ws in await spec.ready([owner])
        note = await comp_router.monthly_summary(owner, {"at": now()})
        assert "Competitor read again" in note
        with pytest.raises(catalog.Skip):    # read less than a month ago
            await comp_router.monthly_summary(owner, {"at": now() + timedelta(days=3)})
        note = await comp_router.monthly_summary(owner, {"at": now() + timedelta(days=31)})
        assert "Competitor read again" in note


async def test_website_from_the_4_answers_preview_then_publish(client):
    o = await v23_member("9811000004")
    ws = await owner_id(o)
    assert (await o.post("/api/inputs/website/build", json={"source": "website"})).status_code == 400   # no website given
    before = await runs_used(o)
    r = await o.post("/api/inputs/website/build", json={"source": "answers"})
    assert r.status_code == 200, r.text
    site = r.json()
    assert site["status"] == "draft" and await runs_used(o) == before + 1
    offers = {x["name"]: x["price"] for x in site["content"]["offers"]}
    assert offers.get("Modular kitchen") == "₹1,85,000 onwards"            # prices exactly as the owner gave them
    # the preview opens for the owner before anything is public; the public address doesn't
    assert (await o.get(f"/s/{site['slug']}?preview=1")).status_code == 200
    assert (await client.get(f"/s/{site['slug']}")).status_code == 404
    assert (await o.post("/api/site/publish")).status_code == 200
    assert (await client.get(f"/s/{site['slug']}")).status_code == 200
    b = await db().businesses.find_one({"owner_id": ws})
    assert b["inputs"]["has_website"] is False


async def test_website_from_their_own_site_and_no_invented_numbers(client, monkeypatch):
    o = await v23_member("9811000005")
    ws = await owner_id(o)

    orig = site_tasks.mock_site_from_inputs

    def inventing(p, brand, inp, own_site, comp):
        c = orig(p, brand, inp, own_site, comp)
        c["about"] += " Over 900 kitchens delivered. We work with families."
        c["offers"].append({"name": "Wardrobe", "description": "Made to measure.", "price": "₹45,000"})
        return c
    monkeypatch.setattr(site_tasks, "mock_site_from_inputs", inventing)
    with serve(dict(OWN_PAGES)) as (base, _hits):
        await o.put("/api/inputs", json={"website_url": base})
        r = await o.post("/api/inputs/website/build", json={"source": "website"})
    assert r.status_code == 200, r.text
    c = r.json()["content"]
    assert "900" not in c["about"] and "families" in c["about"].lower()
    assert {x["name"]: x["price"] for x in c["offers"]}.get("Wardrobe") == ""
    scan = await db().site_scans.find_one({"_id": ws})
    assert scan and scan["url"].startswith(base)


async def _live_site(o) -> dict:
    site = (await o.post("/api/inputs/website/build", json={"source": "answers"})).json()
    assert (await o.post("/api/site/publish")).status_code == 200
    return site


async def test_lead_magnet_ideas_build_live_and_a_download_is_a_lead(client):
    o = await v23_member("9811000006")
    ws = await owner_id(o)
    st = (await o.get("/api/lead-magnet/studio")).json()
    assert st["ideas"] is None and st["built"] == [] and st["inputs"]["about"]
    before = await runs_used(o)
    r = await o.post("/api/lead-magnet/ideas", json={"pains": "Delays, hidden costs and poor finishing"})
    assert r.status_code == 200, r.text
    ideas = r.json()["ideas"]["items"]
    assert len(ideas) == 8 and await runs_used(o) == before + 1
    assert {i["format"] for i in ideas} >= {"checklist", "guide", "mistakes"} and all(i["title"] and i["who"] and i["problem"] for i in ideas)
    assert (await db().businesses.find_one({"owner_id": ws}))["inputs"]["pains"].startswith("Delays")

    guide_ix = next(i for i, x in enumerate(ideas) if x["format"] == "guide")
    r = await o.post("/api/lead-magnet/build", json={"index": guide_ix})
    assert r.status_code == 200, r.text
    item = r.json()["item"]
    assert item["source"] == "ai" and not item["published"] and 6 <= item["pages"] <= 12
    pdf = await o.get(item["file_url"])
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-" and len(PdfReader(io.BytesIO(pdf.content)).pages) == item["pages"]
    assert (await o.get(item["cover_url"])).headers["content-type"] == "image/png"
    assert len(item["landing"]["benefits"]) == 3 and item["landing"]["what_you_get"]
    # it goes on the website only once the website is live
    assert (await o.post(f"/api/lead-magnet/built/{item['id']}/live", json={"published": True})).status_code == 400
    site = await _live_site(o)
    r = await o.post(f"/api/lead-magnet/built/{item['id']}/live", json={"published": True})
    assert r.status_code == 200 and r.json()["built"][0]["published"]
    home = (await client.get(f"/s/{site['slug']}")).text
    assert f"/guide/{item['slug']}" in home
    page = await client.get(f"/s/{site['slug']}/guide/{item['slug']}")
    assert page.status_code == 200 and "What you'll get" in page.text and item["landing"]["benefits"][0][:20] in page.text
    assert (await client.get(f"/s/{site['slug']}/guide")).headers.get("location", "").endswith(f"/guide/{item['slug']}")
    # the PDF never opens without the form
    assert (await client.get(f"/s/{site['slug']}/guide/{item['slug']}/file")).status_code == 303
    r = await client.post(f"/s/{site['slug']}/guide/{item['slug']}", data={"name": "Neha Joshi", "phone": "9834000001", "email": ""})
    assert r.status_code == 200 and "/file?t=" in r.text
    lead = await db().leads.find_one({"owner_id": ws, "source": "lead_magnet"})
    assert lead and lead["magnet_id"] == item["id"]

    # a checklist is shorter; the owner's own words redraw the PDF without a token
    check_ix = next(i for i, x in enumerate(ideas) if x["format"] == "checklist")
    short = (await o.post("/api/lead-magnet/build", json={"index": check_ix})).json()["item"]
    assert short["pages"] < item["pages"]
    before = await runs_used(o)
    content = {**short["content"], "title": "Kitchen handover checklist"}
    content["sections"][0]["heading"] = "रसोई सौंपने से पहले"         # Hindi draws correctly (Devanagari fonts)
    r = await o.put(f"/api/lead-magnet/built/{short['id']}", json={"content": content})
    assert r.status_code == 200, r.text
    edited = r.json()["item"]
    assert edited["title"] == "Kitchen handover checklist" and edited["file_id"] != short["file_id"] and await runs_used(o) == before


async def test_lead_magnet_never_copies_the_competitor_or_invents_money(client, monkeypatch):
    o = await v23_member("9811000007")

    orig = magnet_tasks.mock_build

    def copying(p, inp, idea):
        r = orig(p, inp, idea)
        r["content"]["intro"] = f"{UNIQUE}. " + r["content"]["intro"]
        r["content"]["sections"][0]["text"] = "Most families save ₹50,000 with this one step. " + r["content"]["sections"][0]["text"]
        r["landing"]["benefits"][0] = UNIQUE
        return r
    monkeypatch.setattr(magnet_tasks, "mock_build", copying)
    with serve(dict(RIVAL_PAGES)) as (base, _hits):
        await o.put("/api/inputs", json={"competitors": [base]})
        ideas = (await o.post("/api/lead-magnet/ideas", json={"pains": "Hidden costs"})).json()["ideas"]["items"]
    r = await o.post("/api/lead-magnet/build", json={"index": next(i for i, x in enumerate(ideas) if x["format"] == "guide")})
    assert r.status_code == 200, r.text
    item = r.json()["item"]
    text = str(item["content"]) + str(item["landing"])
    assert "zebra marmalade" not in text.lower() and "₹50,000" not in text
    assert item["content"]["intro"] and len(item["landing"]["benefits"]) == 3


async def test_a_thin_outline_is_refused_and_the_token_given_back(client, monkeypatch):
    o = await v23_member("9811000008")
    ideas = (await o.post("/api/lead-magnet/ideas", json={"pains": "Hidden costs"})).json()["ideas"]["items"]

    orig = magnet_tasks.mock_build

    def outline(p, inp, idea):
        r = orig(p, inp, idea)
        for s in r["content"]["sections"]:
            s["text"] = ""
        return r
    monkeypatch.setattr(magnet_tasks, "mock_build", outline)
    before = await runs_used(o)
    r = await o.post("/api/lead-magnet/build", json={"index": next(i for i, x in enumerate(ideas) if x["format"] == "guide")})
    assert r.status_code == 502 and await runs_used(o) == before


async def test_brand_kit_fonts_and_colour_reach_the_website(client):
    o = await v23_member("9811000009")
    ws = await owner_id(o)
    await db().brand_kits.insert_one({"_id": ws, "design": {"colors": {"primary": "#7C2D12"}, "fonts": {"heading": "baloo2", "body": "mukta"}}})
    site = (await o.post("/api/inputs/website/build", json={"source": "answers"})).json()
    assert site["accent"] == "#7C2D12"
    page = (await o.get(f"/s/{site['slug']}?preview=1")).text
    assert "family=Baloo+2" in page and 'font-family:"Mukta"' in page


async def test_inputs_before_the_business_profile(client):
    from tests.conftest import new_owner
    c = await new_owner("9811000010")
    d = (await c.get("/api/inputs")).json()
    assert d["inputs"]["about"] == "" and not d["answered"] and d["competitor"] is None
    assert (await c.put("/api/inputs", json={"pains": "Delays"})).status_code == 400   # nothing to attach it to yet


async def test_a_knowledge_hub_change_rewrites_the_website_the_same_way(client):
    o = await v23_member("9811000011")
    ws = await owner_id(o)
    await o.put("/api/inputs", json={"pains": "Delays and surprise costs", "has_website": False})
    assert (await o.post("/api/inputs/website/build", json={"source": "answers"})).status_code == 200
    from tests.conftest import PROFILE
    assert (await o.put("/api/business", json={**PROFILE, "ideal_customer": "Young couples buying their first flat"})).status_code == 200
    from app import knowledge
    await knowledge.settle()
    owner = await db().users.find_one({"_id": ws})
    await knowledge.update_hub(owner)
    site = await db().sites.find_one({"owner_id": ws})
    assert site["built_from"] == "answers" and "young couples" in site["content"]["subheadline"].lower()
    assert "surprise costs" in site["content"]["subheadline"].lower()       # still written from the answers
