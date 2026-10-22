"""Tests run against a real MongoDB-wire database (MONGO_URL, default 127.0.0.1:27017) in a throwaway DB."""
import os
import uuid

os.environ.setdefault("APP_ENV", "test")
os.environ["DB_NAME"] = f"action_hub_test_{uuid.uuid4().hex[:8]}"
os.environ["AI_PROVIDER"] = "mock"
os.environ["ADMIN_PHONES"] = "+919999900000"
os.environ["APP_URL"] = "https://hub.example.in"

import httpx  # noqa: E402
import pytest  # noqa: E402
from asgi_lifespan import LifespanManager  # noqa: E402

from app.db import db  # noqa: E402
from app.main import app  # noqa: E402


# HUB_ADMIN_MODE=1: every owner a test puts on the top plan stays on Free and gets an admin number (ADMIN_PHONES)
# instead, so the whole top-plan suite runs through the path admin numbers take ("everything unlocked").
ADMIN_MODE = os.getenv("HUB_ADMIN_MODE") == "1"
_REAL_HANDLE = httpx.ASGITransport.handle_async_request
_MADE_ADMIN: set[str] = set()   # numbers this mode turned into admin numbers, in the current test


async def _admin_mode_handle(self, request):
    import json as _json
    import re as _re
    from app.config import settings as _settings
    m = _re.fullmatch(r"/api/admin/users/([^/]+)", request.url.path)
    if request.method == "PATCH" and m:
        body = _json.loads(request.content or b"{}")
        u = await db().users.find_one({"_id": m.group(1)}, {"phone": 1}) if "plan" in body else None
        if u and body["plan"] == "office":
            object.__setattr__(_settings, "admin_phones", tuple(_settings.admin_phones) + (u["phone"],))
            _MADE_ADMIN.add(u["phone"])
            body.pop("plan")
            request = httpx.Request(request.method, request.url, headers={k: v for k, v in request.headers.items() if k.lower() != "content-length"},
                                    content=_json.dumps(body).encode())
        elif u and u["phone"] in _MADE_ADMIN:
            # moved off the top plan: the admin number this mode gave them goes too, so the new plan is what applies
            object.__setattr__(_settings, "admin_phones", tuple(p for p in _settings.admin_phones if p != u["phone"]))
            _MADE_ADMIN.discard(u["phone"])
    return await _REAL_HANDLE(self, request)


if ADMIN_MODE:
    httpx.ASGITransport.handle_async_request = _admin_mode_handle


@pytest.fixture(autouse=True)
def _reset_admin_numbers():
    from app.config import settings as _settings
    before = _settings.admin_phones
    yield
    object.__setattr__(_settings, "admin_phones", before)
    _MADE_ADMIN.clear()


@pytest.fixture
async def client():
    async with LifespanManager(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://hub.example.in") as c:
            yield c


@pytest.fixture(autouse=True)
async def clean():
    yield
    from app import plans
    from app.db import connect
    d = connect()
    for name in await d.list_collection_names():
        await d[name].delete_many({})
    plans.reset_cache()   # the admin's feature and plan changes are per test


async def login(client: httpx.AsyncClient, phone: str, **extra) -> httpx.AsyncClient:
    r = await client.post("/api/auth/otp", json={"phone": phone})
    assert r.status_code == 200, r.text
    code = r.json()["dev_code"]
    r = await client.post("/api/auth/verify", json={"phone": phone, "code": code, **extra})
    assert r.status_code == 200, r.text
    return client


async def new_owner(phone: str, **extra) -> httpx.AsyncClient:
    """A separate cookie jar per owner, so tests can act as several people at once."""
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://hub.example.in")
    await login(c, phone, **extra)
    return c


PROFILE = {
    "name": "Shree Ganesh Interiors", "city": "Thane", "industry": "Home and office interiors with in-house carpenters",
    "offers": [{"name": "Modular kitchen", "price": "₹1,85,000", "unit": "onwards"}, {"name": "2BHK full interiors", "price": "", "unit": ""}],
    "ideal_customer": "Families who just got possession of a 2/3BHK", "why_us": "Fixed timelines\nTransparent quotation\nOwn factory",
    "proof": "", "whatsapp": "98200 00000", "email": "hello@sgi.in", "language": "English",
}


async def go_live(c: httpx.AsyncClient, profile: dict | None = None) -> dict:
    assert (await c.put("/api/business", json=profile or PROFILE)).status_code == 200
    opts = (await c.post("/api/brand/generate")).json()["options"]
    assert (await c.put("/api/brand", json=opts[0])).status_code == 200
    assert (await c.post("/api/site/generate")).status_code == 200
    r = await c.post("/api/site/publish")
    assert r.status_code == 200, r.text
    return r.json()
