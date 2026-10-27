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
