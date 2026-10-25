"""Website looks: each kind of business gets its own layout (not the same page in another colour), the owner can pick another,
and the colours stay legible on every look."""
from app import site_looks
from app import brand_kit as bk
from app.routers.public import site_theme
from tests.conftest import go_live, login


def test_auto_look_follows_the_trade():
    cases = {"Sweets and farsan shop": "bazaar", "Dental clinic": "care", "Industrial pumps and spare parts": "works",
             "Interior design studio": "atelier", "Mobile phone retail": "signboard"}
    for industry, look in cases.items():
        assert site_looks.auto_look({"industry": industry, "name": "Shree"}) == look, industry
    # "art" is a whole word only: spare parts are not an art studio
    assert site_looks.auto_look({"industry": "Auto spare parts dealer"}) != "atelier"


def test_variant_is_stable_per_address():
    assert site_looks.look_of({"slug": "abc"}, {})[1] == site_looks.look_of({"slug": "abc"}, {})[1]
    assert {site_looks.look_of({"slug": f"s{i}"}, {})[1] for i in range(20)} == {0, 1}
    assert site_looks.look_of({"slug": "x", "look": "care"}, {"industry": "Sweets"})[0] == "care"


async def test_owner_picks_a_look_and_the_page_changes(client):
    await login(client, "9827200001")
    site = await go_live(client)
    got = (await client.get("/api/site")).json()
    assert got["look"] == "auto" and got["look_auto"] in site_looks.LOOKS and len(got["look_looks"]) == len(site_looks.LOOKS)
    body = {"content": got["content"], "accent": got["accent"], "slug": got["slug"]}
    for look in site_looks.LOOKS:
        r = await client.put("/api/site", json={**body, "look": look})
        assert r.status_code == 200 and r.json()["look"] == look
        page = (await client.get(f"/s/{site['slug']}")).text
        assert f'class="look-{look} ' in page
    assert (await client.put("/api/site", json={**body, "look": "neon"})).status_code == 400
    r = await client.put("/api/site", json={**body, "look": "auto"})
    assert r.json()["look"] == "auto"
    assert f'class="look-{r.json()["look_auto"]} ' in (await client.get(f"/s/{site['slug']}")).text


async def test_brand_colour_reads_on_white_and_on_dark_panels(client):
    for brand in ("#3B5BDB", "#1E293B", "#9E1B32", "#0F766E", "#F2C230"):
        t = await site_theme("nobody", brand, "works")
        assert bk.contrast(t["glow"], "#0F1318") >= 4.5 and bk.contrast(t["text"], "#FFFFFF") >= 4.5, brand
