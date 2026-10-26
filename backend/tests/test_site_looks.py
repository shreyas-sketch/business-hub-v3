"""Website looks: every niche gets its own design (not the same page in another colour), businesses of one niche still differ,
nobody has to pick a look, and the colours stay legible on every look."""
from app import site_looks
from app import brand_kit as bk
from app.routers.public import site_theme
from tests.conftest import PROFILE, go_live, login


def test_auto_look_follows_the_trade():
    cases = {"Sweets and farsan shop": "bazaar", "Dental clinic": "care", "Industrial pumps and spare parts": "works",
             "Interior design studio": "atelier", "Mobile phone retail": "signboard"}
    for industry, look in cases.items():
        assert site_looks.auto_look({"industry": industry, "name": "Shree"}) == look, industry
    # "art" is a whole word only: spare parts are not an art studio
    assert site_looks.auto_look({"industry": "Auto spare parts dealer"}) != "atelier"


def test_variant_is_stable_per_address():
    assert site_looks.look_of({"slug": "abc"}, {})[1] == site_looks.look_of({"slug": "abc"}, {})[1]
    assert {site_looks.look_of({"slug": f"s{i}"}, {})[1] for i in range(40)} == {0, 1, 2, 3}
    # a look an owner picked before pages were put together from parts no longer applies
    assert site_looks.look_of({"slug": "x", "look": "care"}, {"industry": "Sweets"})[0] == "bazaar"


async def test_owners_get_no_look_to_pick_the_page_is_put_together_for_them(client):
    await login(client, "9827200001")
    site = await go_live(client)
    got = (await client.get("/api/site")).json()
    assert not any(k == "look" or k.startswith("look_") for k in got)
    body = {"content": got["content"], "accent": got["accent"], "slug": got["slug"]}
    before = (await client.get(f"/s/{site['slug']}")).text
    r = await client.put("/api/site", json={**body, "look": "atelier"})      # an older app still sending one: ignored
    assert r.status_code == 200 and "look" not in r.json()
    assert (await client.get(f"/s/{site['slug']}")).text.split("<body")[1][:120] == before.split("<body")[1][:120]
    d = site_looks.design_of({"slug": site["slug"], "content": got["content"]}, {**PROFILE})
    assert f'class="look-{d["finish"]} hero-{d["hero"]} svc-{d["services"]} ' in before


async def test_brand_colour_reads_on_white_and_on_dark_panels(client):
    for brand in ("#3B5BDB", "#1E293B", "#9E1B32", "#0F766E", "#F2C230"):
        t = await site_theme("nobody", brand, "works")
        assert bk.contrast(t["glow"], "#0F1318") >= 4.5 and bk.contrast(t["text"], "#FFFFFF") >= 4.5, brand



def test_the_ai_niche_picks_the_look_and_unknown_services_dont_all_match():
    assert site_looks.look_of({"slug": "a", "content": {"niche": "dental"}}, {"industry": "Wardrobes"})[0] == "care"
    assert site_looks.look_of({"slug": "a", "content": {"niche": "coaching_education"}}, {})[0] == "launch"
    assert site_looks.look_of({"slug": "a", "content": {"niche": "bakery_sweets"}}, {})[0] == "bazaar"
    assert site_looks.look_of({"slug": "a", "content": {"niche": "made-up"}}, {"industry": "Dental clinic"})[0] == "care"
    assert site_looks.auto_look({"industry": "Business coaching for founders"}) == "launch"
    # a business nothing places gets the "other" design, its own too
    assert site_looks.niche_for({}, {"industry": "Astrology and vastu advice"}) == "other"
    assert site_looks.auto_look({"industry": "Astrology and vastu advice"}) == site_looks.OTHER_LOOK


async def test_variants_change_the_fonts_and_a_page_without_a_brand_colour_gets_its_niches(client):
    a = await site_theme("nobody", "#1F4E79", "care", 0)
    b = await site_theme("nobody", "#1F4E79", "care", 2)
    assert a["head_font"] != b["head_font"]
    dental = {site_looks.niche_colour({"slug": f"clinic-{i}", "content": {"niche": "dental"}}) for i in range(30)}
    assert dental == set(site_looks.NICHE_COLOURS["dental"])                    # random across a room of clinics
    one = {"slug": "smile-dental", "content": {"niche": "dental"}}
    assert site_looks.niche_colour(one) == site_looks.niche_colour(one)          # and the same each time for one of them
    assert site_looks.niche_colour({"slug": "x", "content": {}}) in site_looks.NICHE_COLOURS["other"]
    assert len({site_looks.pop_colour({"slug": f"s{i}"}) for i in range(40)}) > 3


async def test_the_launch_look_renders_its_pieces(client):
    from app.db import db
    await login(client, "9827200009")
    site = await go_live(client)
    got = (await client.get("/api/site")).json()
    content = {**got["content"], "niche": "coaching_education", "pains": ["Chats all day, zero orders", "Prices cut until no margin"],
               "design": {"hero": "launch", "services": "cards", "finish": "launch"}}
    assert (await client.put("/api/site", json={"content": content, "accent": got["accent"], "slug": got["slug"]})).status_code == 200
    page = (await client.get(f"/s/{site['slug']}")).text
    assert 'class="look-launch ' in page and 'class="ticker"' in page and "Sound familiar?" in page and "Chats all day, zero orders" in page
    assert 'id="ldock"' in page and 'id="lrot"' in page
    saved = await db().sites.find_one({"slug": site["slug"]})
    assert saved["content"]["niche"] == "coaching_education"
    # no pain points given: no "Sound familiar?" (nothing is invented)
    content["pains"] = []
    await client.put("/api/site", json={"content": content, "accent": got["accent"], "slug": got["slug"]})
    assert "Sound familiar?" not in (await client.get(f"/s/{site['slug']}")).text


def test_the_words_place_a_page_the_ai_gave_no_niche():
    """Pages from before niches, or when the AI names none, go by the words of what the business does."""
    cases = {"Online store selling electronics and home appliances in Delhi NCR": "signboard",   # a shop, not a D2C brand
             "A 100% plant-based patisserie making cakes and desserts": "bazaar",                 # "plant-based" is not a factory
             "Gaming cafe with PS5 and PC gaming stations": "launch",                             # not a restaurant
             "Indie game development studio": "launch",                                           # not an interiors studio
             "Esports tournaments and LAN events": "launch"}
    for industry, look in cases.items():
        assert site_looks.look_of({"slug": "a", "content": {}}, {"industry": industry})[0] == look, industry
    from app.ai.tasks import guess_niche
    assert guess_niche({"industry": "Yoga institute teaching yoga courses"}) == "fitness_yoga"
    assert guess_niche({"industry": "Dental implants and braces"}) == "dental"
    assert guess_niche({"industry": "Gaming zone and arcade"}) == "gaming"
    # a page without a brand colour, made before niches, still gets its kind of business's colours
    assert site_looks.niche_colour({"slug": "x", "content": {}}, 0, {"industry": "Dental clinic"}) in site_looks.NICHE_COLOURS["dental"]


def test_the_event_card_opens_in_the_colour_the_page_will():
    from app.event import card_colour
    profile = {"name": "Smile Dental", "industry": "Dental clinic"}
    got = card_colour(profile, {"colors": {"primary": "#1F4E79"}}, {"niche": "dental"})
    assert got["accent"] == site_looks.niche_colour({"slug": "smile-dental", "content": {"niche": "dental"}})
    assert got["accent"] in site_looks.NICHE_COLOURS["dental"] and got["on_accent"] == bk.text_on(got["accent"])
    gold = card_colour(profile, {"colors": {"primary": "#D3B982"}}, {"niche": "bakery_sweets"})
    assert gold["accent"] == "#D3B982" and gold["on_accent"] != "#FFFFFF"     # a light brand colour keeps dark text on it


def test_every_niche_has_its_own_design_and_pages_of_one_niche_still_differ():
    designs = {}
    for niche in site_looks.NICHES:
        pages = [site_looks.design_of({"slug": f"biz-{i}", "content": {"niche": niche}}, {}) for i in range(40)]
        own = {(d["hero"], d["tone"], d["layout"], d["services"], d["finish"]) for d in pages}
        assert len(own) == 1, niche                                    # one design per niche, always...
        hero, tone, layout, *_ = own.pop()
        designs.setdefault((hero, tone, layout), []).append(niche)
        assert all(d["fonts"] in site_looks.FINISH_FONTS[d["finish"]] for d in pages)    # fonts its finish can carry
        looks = {(d["fonts"], site_looks.niche_colour({"slug": f"biz-{i}", "content": {"niche": niche}})) for i, d in enumerate(pages)}
        assert len(looks) >= 8, niche                                   # ...and two of one niche still differ
    clash = {k: v for k, v in designs.items() if len(v) > 1}
    assert not clash, clash                                             # ...and no two niches even open alike
    assert set(site_looks.NICHE_FIRST) == set(site_looks.NICHES)
    assert set(site_looks.NICHE_DESIGN) == set(site_looks.NICHES)
    # a design suits its kind: no menu for a clinic, no spec table for a bakery
    assert site_looks.NICHE_DESIGN["dental"][0] != "menu" and site_looks.NICHE_DESIGN["bakery_sweets"][0] != "spec"
    one = {"slug": "smile-dental", "content": {"niche": "dental"}}
    assert site_looks.design_of(one, {}) == site_looks.design_of(one, {})     # the same page on every visit


def test_two_kinds_of_business_never_open_alike():
    """A design studio and a machine shop were getting the same page; now every niche is placed and gets its own."""
    kinds = {"Interior design studio": "interiors", "Graphic design and branding studio": "design_studio",
             "Precision machined components manufacturer": "manufacturing", "Dental clinic": "dental",
             "Bakery and sweets": "bakery_sweets", "Mobile phone shop": "retail_shop", "Business coaching": "coaching_education",
             "Ayurveda wellness centre": "wellness", "Physiotherapy clinic": "clinic", "Gaming cafe": "gaming"}
    from app.ai.tasks import guess_niche
    seen = {}
    for industry, niche in kinds.items():
        assert guess_niche({"industry": industry}) == niche, industry
        d = site_looks.design_of({"slug": "same-address", "content": {}}, {"industry": industry})   # placed by its words
        seen.setdefault((d["hero"], d["tone"], d["layout"]), industry)
    assert len(seen) == len(kinds)


async def test_every_niche_renders_with_its_kind_of_business_words(client):
    from app.db import db
    await login(client, "9827200010")
    site = await go_live(client)
    got = (await client.get("/api/site")).json()
    words = {"care": "Treatments and services", "bazaar": "Our menu", "works": "Capabilities", "atelier": "What we do",
             "launch": "Pick what you need", "signboard": "What we do, and what it costs"}
    for niche in site_looks.NICHES:
        await db().sites.update_one({"slug": site["slug"]}, {"$set": {"content": {**got["content"], "niche": niche}}})
        page = (await client.get(f"/s/{site['slug']}")).text
        d = site_looks.design_of({"slug": site["slug"], "content": {"niche": niche}}, {**PROFILE})
        assert f'class="look-{d["finish"]} hero-{d["hero"]} svc-{d["services"]} tone-{d["tone"]} lay-{d["layout"]} ' in page, niche
        assert words[d["voice"]] in page, niche
