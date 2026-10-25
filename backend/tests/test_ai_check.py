"""Admin → Pulse → Test the AI: owners only ever see "the AI is busy", so the admin gets the engine's real reason and the fix,
and the last failure an owner hit (in a hub feature or in an event pack)."""
import dataclasses

from app.ai import provider
from app.config import settings
from app.routers import admin as admin_router
from tests.conftest import PROFILE, login, new_owner

ADMIN = "9999900000"


def real_engine(monkeypatch, call):
    """The hub set up for a real engine (settings are frozen, and each module holds its own name for them), with `call` answering."""
    live = dataclasses.replace(settings, ai_provider="anthropic", anthropic_api_key="sk-test")
    for mod in (provider, admin_router):
        monkeypatch.setattr(mod, "settings", live)
    monkeypatch.setattr(provider, "_call_once", call)


def test_each_engine_failure_is_explained():
    say = provider.explain
    assert "out of credit" in say('anthropic 400: {"type":"error","error":{"message":"Your credit balance is too low to access the Anthropic API."}}')
    assert "API key" in say('anthropic 401: {"error":{"type":"authentication_error","message":"invalid x-api-key"}}')
    assert "AI_MODEL" in say('anthropic 404: {"error":{"type":"not_found_error","message":"model: claude-nope"}}')
    assert "AI_EFFORT" in say('anthropic 400: {"error":{"message":"output_config.effort: not supported for this model"}}')
    assert "turning calls away" in say("anthropic 429")
    assert "couldn't reach" in say("The AI engine could not be reached")
    assert "developer" in say("something new")


async def test_only_admins_can_test_and_mock_says_demo_text(client):
    owner = await new_owner("9838100001")
    assert (await owner.post("/api/admin/ai/check")).status_code == 403
    admin = await new_owner(ADMIN)
    r = (await admin.post("/api/admin/ai/check")).json()
    assert r["provider"] == "mock" and r["ok"] is False and "demo text" in r["fix"]


async def test_a_failed_website_build_is_kept_for_the_check(client, monkeypatch):
    owner = await new_owner("9838100002")
    assert (await owner.put("/api/business", json=PROFILE)).status_code == 200

    async def broken(*a, **k):
        raise provider.AIError('anthropic 400: {"error":{"message":"Your credit balance is too low"}}')
    real_engine(monkeypatch, broken)
    r = await owner.post("/api/site/generate")
    assert r.status_code == 502 and "busy" in r.json()["detail"]           # the owner's message is unchanged, and the run is back
    admin = await new_owner(ADMIN)
    c = (await admin.post("/api/admin/ai/check")).json()
    assert c["ok"] is False and c["key_set"] and "out of credit" in c["fix"]
    assert c["last_failure"]["kind"] == "site" and "out of credit" in c["last_failure"]["fix"]


async def test_a_working_engine_says_so(client, monkeypatch):
    async def fine(*a, **k):
        return {"ok": True}, provider.Usage(10, 3, "claude-haiku-5-5")
    real_engine(monkeypatch, fine)
    r = (await (await new_owner(ADMIN)).post("/api/admin/ai/check")).json()
    assert r["ok"] is True and r["model"] == "claude-haiku-5-5" and r["tokens"] == 13


def test_app_url_keeps_only_the_scheme_and_host():
    from app.config import _origin
    assert _origin("https://business-hub-v3-production.up.railway.app/admin/event") == "https://business-hub-v3-production.up.railway.app"
    assert _origin(" https://hub.example.in/ ") == "https://hub.example.in"
    assert _origin("hub.example.in") == "https://hub.example.in"
    assert _origin("http://localhost:8000") == "http://localhost:8000"
