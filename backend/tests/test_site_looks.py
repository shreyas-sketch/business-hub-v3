"""Website looks: each kind of business gets its own layout (not the same page in another colour), the owner can pick another,
and the colours stay legible on every look."""
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
    allowed = site_looks.parts_for(site_looks.look_of({"slug": site["slug"], "content": got["content"]}, {**PROFILE})[0],
                                   got["content"].get("niche", ""))
    assert any(f'class="look-{f} hero-{h} svc-{v} ' in before for f in allowed["finish"] for h in allowed["hero"] for v in allowed["services"])


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
    # a service the words don't place: some Signboard, some Launch — a room of them doesn't all look the same
    looks = {site_looks.auto_look({"industry": "Astrology and vastu advice"}, "", f"biz-{i}") for i in range(12)}
    assert looks == {"signboard", "launch"}


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


def test_pages_are_put_together_from_parts_so_one_kind_of_business_varies():
    clinics = [site_looks.design_of({"slug": f"smile-{i}", "content": {"niche": "dental"}}, {}) for i in range(40)]
    allowed = site_looks.parts_for("care", "dental")
    for d in clinics:
        assert d["voice"] == "care" and d["hero"] in allowed["hero"] and d["services"] in allowed["services"]
        assert d["finish"] in allowed["finish"] and d["fonts"] in site_looks.FINISH_FONTS[d["finish"]]   # weights it can carry
    combos = {(d["hero"], d["services"], d["finish"], d["fonts"]) for d in clinics}
    assert len(combos) > 10                                                            # not one page in six colours
    assert {d["hero"] for d in clinics} == set(allowed["hero"])
    # never a part that doesn't suit: no menu for a clinic, no spec table for a bakery
    assert all(d["services"] != "menu" for d in clinics)
    cakes = [site_looks.design_of({"slug": f"cake-{i}", "content": {"niche": "bakery_sweets"}}, {}) for i in range(30)]
    assert all(d["services"] != "spec" and d["voice"] == "bazaar" for d in cakes)
    # the same page stays the same on every visit
    one = {"slug": "smile-dental", "content": {"niche": "dental"}}
    assert site_looks.design_of(one, {}) == site_looks.design_of(one, {})


def test_the_ai_picks_parts_that_suit_but_never_the_first_screen():
    picked = {"slug": "x", "content": {"niche": "gaming", "design": {"hero": "works", "services": "spec", "finish": "works", "fonts": "oswald"}}}
    d = site_looks.design_of(picked, {})
    assert (d["hero"], d["services"], d["finish"], d["fonts"]) == ("launch", "spec", "works", "oswald")   # the hero is gaming's own
    # a pick that doesn't suit the business (a menu for a gaming arena) or fonts the finish can't carry fall back
    odd = {"slug": "x", "content": {"niche": "gaming", "design": {"services": "menu", "finish": "launch", "fonts": "serif"}}}
    d = site_looks.design_of(odd, {})
    assert d["services"] in ("cards", "spec") and d["fonts"] in site_looks.FINISH_FONTS["launch"]
    # only real parts are kept from the AI's answer, and the first screen isn't its to pick
    assert site_looks.design_choices({"hero": "care", "services": "Tiles", "finish": "neon", "fonts": 3}) == {"services": "tiles"}


def test_two_kinds_of_business_never_open_alike():
    """A design studio and a machine shop were getting the same first screen and services layout."""
    kinds = {"Interior design studio": "interiors", "Graphic design and branding studio": "design_studio",
             "Precision machined components manufacturer": "manufacturing", "Dental clinic": "dental",
             "Bakery and sweets": "bakery_sweets", "Mobile phone shop": "retail_shop", "Business coaching": "coaching_education"}
    from app.ai.tasks import guess_niche
    heroes = {}
    for industry, niche in kinds.items():
        assert guess_niche({"industry": industry}) == niche, industry
        pages = {(d["hero"]) for d in (site_looks.design_of({"slug": f"b{i}", "content": {"niche": niche}}, {}) for i in range(60))}
        assert len(pages) == 1, industry                   # one first screen per kind...
        heroes.setdefault(pages.pop(), set()).add(site_looks.NICHES[niche])
    assert all(len(v) == 1 for v in heroes.values())       # ...and no two kinds share one
    studio = {(d["hero"], d["services"]) for d in (site_looks.design_of({"slug": f"s{i}", "content": {"niche": "interiors"}}, {}) for i in range(60))}
    works = {(d["hero"], d["services"]) for d in (site_looks.design_of({"slug": f"s{i}", "content": {"niche": "manufacturing"}}, {}) for i in range(60))}
    assert not studio & works


async def test_every_part_renders_with_the_kind_of_business_words(client):
    from app.db import db
    await login(client, "9827200010")
    site = await go_live(client)
    got = (await client.get("/api/site")).json()
    for niche, voice, words, never in (("dental", "care", "Treatments and services", "Our menu"),
                                       ("bakery_sweets", "bazaar", "Our menu", "Capabilities")):
        allowed = site_looks.parts_for(voice, niche)
        for hero in allowed["hero"]:
            for services in allowed["services"]:
                content = {**got["content"], "niche": niche, "design": {"hero": hero, "services": services, "finish": allowed["finish"][-1]}}
                await db().sites.update_one({"slug": site["slug"]}, {"$set": {"content": content, "look": None}})
                page = (await client.get(f"/s/{site['slug']}")).text
                assert f'hero-{hero} svc-{services} ' in page and words in page and never not in page, (niche, hero, services)
