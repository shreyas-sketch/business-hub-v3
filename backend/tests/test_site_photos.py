"""Website photos: Pexels stock photos from the AI's search terms (two searches, cached), the owner's own uploads
(which stock photos never replace), and the designed page when there are none."""
import io
import json

import httpx
import pytest

from app import photos
from app.config import settings
from app.db import db
from tests.conftest import go_live, login

REAL_CLIENT = httpx.AsyncClient
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 200
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200


def pexels_photo(i, q):
    return {"id": i, "url": f"https://www.pexels.com/photo/{i}/", "photographer": f"Ana {i}", "alt": f"{q} {i}",
            "src": {"large2x": f"https://images.pexels.com/{i}-2x.jpg", "large": f"https://images.pexels.com/{i}.jpg",
                    "medium": f"https://images.pexels.com/{i}-m.jpg"}}


@pytest.fixture
def pexels(monkeypatch):
    calls = []

    def handler(req):
        calls.append(req)
        assert req.url.host == "api.pexels.com" and req.url.path == "/v1/search" and req.headers["authorization"] == "px-test"
        q = req.url.params["query"]
        base = 100 if len(calls) == 1 else 200
        return httpx.Response(200, json={"photos": [pexels_photo(base + i, q) for i in range(int(req.url.params["per_page"]))]})

    monkeypatch.setattr(photos.httpx, "AsyncClient", lambda *a, **k: REAL_CLIENT(transport=httpx.MockTransport(handler), timeout=5))
    object.__setattr__(settings, "pexels_api_key", "px-test")
    yield calls
    object.__setattr__(settings, "pexels_api_key", "")


async def test_generated_website_gets_stock_photos_with_credit(client, pexels):
    await login(client, "9827100001")
    site = await go_live(client)
    assert len(pexels) == 2  # at most two searches per website
    assert site["photos"]["hero"]["src"].startswith("https://images.pexels.com/") and site["photos"]["stock"]
    assert len(site["photos"]["gallery"]) == photos.GALLERY_MAX
    assert set(site["photos"]["offers"]) == {"Modular kitchen", "2BHK full interiors"}
    page = (await client.get(f"/s/{site['slug']}")).text
    assert "images.pexels.com" in page and "Pexels" in page  # photos and the credit
    # the same business again uses the cache, not two more searches
    await client.post("/api/site/photos/stock")
    assert len(pexels) == 2


async def test_uploads_win_over_stock_and_are_served_from_the_site(client, pexels):
    await login(client, "9827100002")
    site = await go_live(client)
    r = await client.post("/api/site/photos", data={"slot": "hero"}, files={"file": ("me.jpg", io.BytesIO(JPEG), "image/jpeg")})
    assert r.status_code == 200, r.text
    hero = r.json()["photos"]["hero"]
    assert hero["kind"] == "upload" and hero["src"].endswith(f"/s/{site['slug']}/photo/{hero['id']}")
    r = await client.post("/api/site/photos", data={"slot": "gallery"}, files={"file": ("a.png", io.BytesIO(PNG), "image/png")})
    assert r.json()["photos"]["gallery"][0]["kind"] == "upload"

    img = await client.get(f"/s/{site['slug']}/photo/{hero['id']}")
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg" and img.content == JPEG

    # finding stock photos again keeps the owner's own
    after = (await client.post("/api/site/photos/stock")).json()["photos"]
    assert after["hero"]["id"] == hero["id"] and after["gallery"][0]["kind"] == "upload"

    # removing an upload deletes the file too
    r = await client.post("/api/site/photos/remove", json={"slot": "hero"})
    assert r.status_code == 200 and r.json()["photos"]["hero"] is None
    assert (await client.get(f"/s/{site['slug']}/photo/{hero['id']}")).status_code == 404


async def test_upload_rules(client):
    await login(client, "9827100003")
    await go_live(client)
    bad = await client.post("/api/site/photos", data={"slot": "hero"}, files={"file": ("x.jpg", io.BytesIO(b"<svg onload=alert(1)>"), "image/jpeg")})
    assert bad.status_code == 415  # the bytes decide, not the name
    big = await client.post("/api/site/photos", data={"slot": "hero"}, files={"file": ("x.jpg", io.BytesIO(JPEG + b"\x00" * (3 * 1024 * 1024)), "image/jpeg")})
    assert big.status_code == 413
    for _ in range(photos.GALLERY_MAX):
        assert (await client.post("/api/site/photos", data={"slot": "gallery"}, files={"file": ("p.jpg", io.BytesIO(JPEG), "image/jpeg")})).status_code == 200
    full = await client.post("/api/site/photos", data={"slot": "gallery"}, files={"file": ("p.jpg", io.BytesIO(JPEG), "image/jpeg")})
    assert full.status_code == 400
    assert (await client.post("/api/site/photos/stock")).status_code == 400  # no Pexels key on this hub


async def test_no_photos_still_renders_a_designed_page(client):
    await login(client, "9827100004")
    site = await go_live(client)
    assert site["photos"]["hero"] is None and site["stock_photos"] is False
    page = await client.get(f"/s/{site['slug']}")
    assert page.status_code == 200 and "art-panel" in page.text and "Send inquiry" in page.text and 'name="phone"' in page.text


async def test_other_owners_cannot_read_a_draft_sites_photos(client):
    await login(client, "9827100005")
    await client.put("/api/business", json={**json.loads(json.dumps(__import__("tests.conftest", fromlist=["PROFILE"]).PROFILE))})
    opts = (await client.post("/api/brand/generate")).json()["options"]
    await client.put("/api/brand", json=opts[0])
    site = (await client.post("/api/site/generate")).json()  # draft, not published
    r = await client.post("/api/site/photos", data={"slot": "hero"}, files={"file": ("me.jpg", io.BytesIO(JPEG), "image/jpeg")})
    pid = r.json()["photos"]["hero"]["id"]
    assert (await client.get(f"/s/{site['slug']}/photo/{pid}")).status_code == 200  # the owner previews it
    await client.post("/api/auth/logout")
    assert (await client.get(f"/s/{site['slug']}/photo/{pid}")).status_code == 404


async def test_photos_are_served_on_the_websites_own_address(client):
    """name.employz.ai shows the owner's photo and serves it at /photo/<id>, alongside the lead-magnet paths."""
    old = settings.sites_domain
    object.__setattr__(settings, "sites_domain", "employz.ai")
    try:
        await login(client, "9827100009")
        site = await go_live(client)
        hero = (await client.post("/api/site/photos", data={"slot": "hero"},
                                  files={"file": ("me.jpg", io.BytesIO(JPEG), "image/jpeg")})).json()["photos"]["hero"]
        host = f"https://{site['slug']}.employz.ai"
        page = await client.get(f"{host}/")
        assert page.status_code == 200 and f"/photo/{hero['id']}" in page.text
        img = await client.get(f"{host}/photo/{hero['id']}")
        assert img.status_code == 200 and img.content == JPEG
        assert (await client.get(f"{host}/photo/{hero['id']}/../../api/me")).status_code == 404
    finally:
        object.__setattr__(settings, "sites_domain", old)
