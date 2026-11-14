"""/membership serves the standalone sales and checkout page, logged in or not, and never falls through to the React app."""
import pytest

from app import main


@pytest.mark.asyncio
async def test_membership_page_is_the_checkout_page(client, tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<html>react app</html>")
    (tmp_path / "membership.html").write_text("<html>checkout page</html>")
    monkeypatch.setattr(main, "DIST", tmp_path)
    for path in ("/membership", "/membership/"):
        r = await client.get(path)
        assert r.status_code == 200
        assert "checkout page" in r.text
        assert r.headers["cache-control"] == "no-cache"


@pytest.mark.asyncio
async def test_breakthrough_page_is_its_own_sales_page(client, tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<html>react app</html>")
    (tmp_path / "breakthrough.html").write_text("<html>breakthrough page</html>")
    monkeypatch.setattr(main, "DIST", tmp_path)
    for path in ("/breakthrough", "/breakthrough/"):
        r = await client.get(path)
        assert r.status_code == 200
        assert "breakthrough page" in r.text
        assert r.headers["cache-control"] == "no-cache"
