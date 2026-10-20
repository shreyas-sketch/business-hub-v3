"""Live event link (the 3-day AI Advantage Membership event): website + GST number → the pack built before login → saved into
an account with the WhatsApp code. Plus the event's caps, the Membership preset and the AI engine's queue and retries.
Every business, number and GST number here is made up (the GST numbers are built with a valid check character)."""
import asyncio
import io
import threading
from contextlib import contextmanager
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PIL import Image

from app import event as ev, plans
from app.ai import provider, tasks
from app.ai.provider import AIError, Busy, Usage
from app.config import settings
from app.db import IST, db, now
from tests.conftest import PROFILE, go_live, new_owner

ADMIN = "9999900000"
CODE = "AIA-NOV-01"


def gstin(state: str = "27", pan_kind: str = "P") -> str:
    """A made-up GST number with a valid check character: state code, a PAN whose 4th letter is the taxpayer kind."""
    first14 = f"{state}ABC{pan_kind}X1234Q1Z"
    return first14 + ev.gstin_check_char(first14)


# ───────────────────────── a small website with a logo ─────────────────────────
def _logo_png() -> bytes:
    img = Image.new("RGB", (120, 60), "#FFFFFF")
    for x in range(10, 60):
        for y in range(10, 50):
            img.putpixel((x, y), (200, 40, 40))
    for x in range(60, 110):
        for y in range(10, 50):
            img.putpixel((x, y), (20, 90, 160))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


LOGO = _logo_png()
PAGES = {
    "/": (b"""<html><head><title>Kaveri Home Appliances | Repairs in Pune</title>
<meta name="description" content="Washing machine and fridge repair at home in Pune, same-day visits">
<meta name="viewport" content="width=device-width"><meta name="theme-color" content="#0F766E"></head>
<body><img src="/logo.png" alt="Kaveri logo"><h1>Kaveri Home Appliances</h1><h2>Washing machine repair</h2><h2>Fridge repair</h2>
<p>Call 98765 00001. Serving Pune since 2015.</p><a href="https://wa.me/919876500001">WhatsApp</a></body></html>""", "text/html"),
    "/logo.png": (LOGO, "image/png"),
}


class _Site(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body, ctype = PAGES.get(self.path.split("?")[0], (b"not found", "text/html"))
        self.send_response(200 if self.path.split("?")[0] in PAGES else 404)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@contextmanager
def site_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()


@pytest.fixture
def setting():
    old = {}

    def change(**kw):
        for k, v in kw.items():
            old.setdefault(k, getattr(settings, k))
            object.__setattr__(settings, k, v)
    yield change
    for k, v in old.items():
        object.__setattr__(settings, k, v)


async def open_event(pack_cap: int = 400, ai_cap: int = 300, ai: bool = True, trial_days: int = 3):
    admin = await new_owner(ADMIN)
    r = await admin.post("/api/admin/cohorts", json={"code": CODE, "name": "AI Advantage · 3-day event", "workshop_date": "2026-11-01"})
    assert r.status_code == 200, r.text
    until = (now().astimezone(IST) + timedelta(days=trial_days)).date().isoformat()
    r = await admin.put(f"/api/admin/event/{CODE}", json={"open": True, "ai": ai, "pack_cap": pack_cap, "ai_cap": ai_cap, "trial_until": until})
    assert r.status_code == 200, r.text
    return admin


def form(site: str | None, **over) -> dict:
    body = {"event": CODE, "website": site, "gstin": gstin(), "gst_scheme": "monthly", "staff": 4, "consent": True}
    body.update(over)
    return body


async def make(client, body: dict) -> dict:
    r = await client.post("/api/event/runs", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# ───────────────────────── the GST number ─────────────────────────
def test_the_gst_number_gives_the_state_and_which_business_types_fit():
    p = ev.parse_gstin(gstin("27", "P").lower())
    assert p["valid"] and p["state"] == "Maharashtra" and p["pan_kind"] == "Individual" and not p["ask_entity"]
    assert [e["value"] for e in p["entities"]] == ["proprietor"]
    f = ev.parse_gstin(gstin("29", "F"))
    assert f["state"] == "Karnataka" and f["ask_entity"] and [e["value"] for e in f["entities"]] == ["partnership", "llp"]
    c = ev.parse_gstin(gstin("24", "C"))
    assert c["state"] == "Gujarat" and [e["value"] for e in c["entities"]] == ["pvt_ltd", "opc", "other"]
    assert ev.parse_gstin(gstin("07", "H"))["entities"] == [{"value": "other", "label": "Other"}]
    assert ev.parse_gstin(gstin("99", "P"))["state"] is None                      # centre jurisdiction: the form asks for the state
    good = gstin()
    bad = good[:14] + ("A" if good[14] != "A" else "B")
    assert not ev.parse_gstin(bad)["valid"] and "add up" in ev.parse_gstin(bad)["error"]
    assert not ev.parse_gstin("27ABC")["valid"] and not ev.parse_gstin("")["valid"]


# ───────────────────────── the link, its caps ─────────────────────────
async def test_the_link_works_only_while_the_admin_has_it_open_and_stops_at_its_caps(client):
    admin = await new_owner(ADMIN)
    await admin.post("/api/admin/cohorts", json={"code": CODE, "name": "AI Advantage", "workshop_date": "2026-11-01"})
    f = (await client.get(f"/api/event/form?e={CODE}")).json()
    assert f["valid"] and not f["open"]
    body = form(None, business_name="Riya Tiffins", about="Home-cooked lunch tiffins for offices in Pune")
    assert (await client.post("/api/event/runs", json=body)).status_code == 403
    assert (await client.get("/api/event/form?e=NOPE")).json()["valid"] is False
    r = await admin.put(f"/api/admin/event/{CODE}", json={"open": True, "ai": True, "pack_cap": 2, "ai_cap": 1})
    assert r.status_code == 200 and r.json()["open"]
    first, second = await make(client, body), await make(client, {**body, "gstin": gstin("29", "P")})
    third = await client.post("/api/event/runs", json={**body, "gstin": gstin("24", "P")})
    assert third.status_code == 429
    a = await db().event_runs.find_one({"token": first["token"]})
    b = await db().event_runs.find_one({"token": second["token"]})
    assert a["ai_allowed"] and not b["ai_allowed"]                              # the AI cap is separate from the pack cap
    assert b["steps"]["website"]["status"] == "after_login"
    assert (await admin.get(f"/api/admin/event/{CODE}/live")).json()["counters"] == {"packs": 2, "ai": 1}


async def test_the_form_is_checked_before_anything_is_built(client):
    await open_event()
    bad = await client.post("/api/event/runs", json=form(None))
    assert bad.status_code == 400 and "No website" in bad.json()["detail"]
    good = gstin()
    wrong = good[:14] + ("A" if good[14] != "A" else "B")
    r = await client.post("/api/event/runs", json=form("www.example-shop.in", gstin=wrong))
    assert r.status_code == 400 and "add up" in r.json()["detail"]
    r = await client.post("/api/event/runs", json=form("www.example-shop.in", gstin=gstin("29", "F")))
    assert r.status_code == 400 and "firm or llp" in r.json()["detail"].lower()      # a firm's GST number: partnership or LLP?
    r = await client.post("/api/event/runs", json=form("www.example-shop.in", gstin=None, entity=None))
    assert r.status_code == 400 and "type of business" in r.json()["detail"]
    r = await client.post("/api/event/runs", json=form("www.example-shop.in", gstin=None, entity="proprietor", state="Atlantis"))
    assert r.status_code == 400 and "state" in r.json()["detail"]
    r = await client.post("/api/event/runs", json=form("www.example-shop.in", consent=False))
    assert r.status_code == 400
    assert await db().event_runs.count_documents({}) == 0


# ───────────────────────── the whole flow ─────────────────────────
async def test_website_and_gst_build_the_pack_then_the_code_puts_it_in_a_new_account(client):
    admin = await open_event()
    with site_server() as site:
        made = await make(client, form(site))
        assert made["status"] == "queued" and len(made["token"]) >= 20 and made["eta"]["first"] >= 3
        assert await ev.drain() >= 1
    run = await db().event_runs.find_one({"token": made["token"]})
    assert run["status"] == "done" and "_facts" not in run and run["user_id"] is None
    v = (await client.get(f"/api/event/runs/{made['token']}")).json()
    assert all(s["status"] == "done" for s in v["steps"].values()), v["steps"]
    # rules: the calendar from the GST number, the website check, the colours from the logo
    assert v["identity"] == {"entity": "proprietor", "gst_registered": True, "gstin": gstin(), "gst_scheme": "monthly", "state": "Maharashtra",
                             "staff": 4, "registrations": []}
    kinds = v["compliance"]["kinds"]
    assert "GSTR-3B (GST return and payment)" in kinds and "Professional tax (employer)" in kinds and v["compliance"]["count"] >= 24
    assert "CA" in v["compliance"]["review"]
    assert v["check"]["total"] == 8 and v["check"]["passed"] >= 4 and len(v["check"]["fixes"]) <= 3
    assert v["design"]["source"] == "logo" and v["design"]["logo"]
    # AI (the mock engine here): Knowledge Hub, 3 brand messages, lead ideas, a website draft
    assert v["business"]["name"] == "Kaveri Home Appliances" and v["business"]["city"] == "Pune"
    assert len(v["brand"]["options"]) == 3 and len(v["ideas"]) >= 5 and v["website"]["headline"]
    assert v["completeness"]["percent"] >= 50
    assert "email" not in run["input"] and "phone" not in run["input"] and "phone" not in run   # nothing of the attendee's is kept before saving
    # the poster and the post, drawn in their colours, once per brand-message pick
    png = await client.get(f"/api/event/runs/{made['token']}/design/status_poster.png")
    assert png.status_code == 200 and png.content.startswith(b"\x89PNG")
    assert (await client.post(f"/api/event/runs/{made['token']}/brand", json={"pick": 2})).json() == {"pick": 2}
    assert (await client.get(f"/api/event/runs/{made['token']}/design/social_square.png")).status_code == 200
    assert await db().event_files.count_documents({"_id": {"$regex": ":status_poster:"}}) == 1     # drawn once, for the first pick
    assert (await client.get(f"/api/event/runs/{made['token']}/design/status_poster.png")).status_code == 200
    assert await db().event_files.count_documents({"_id": {"$regex": ":status_poster:"}}) == 2     # a new pick, a new poster
    assert (await client.get(f"/api/event/runs/{made['token']}/design/other.png")).status_code == 404

    # email + WhatsApp number → code → a new account with everything inside
    code = await client.post(f"/api/event/runs/{made['token']}/code", json={"phone": "98330 22222"})
    assert code.status_code == 200 and code.json()["dev_code"]
    bad = await client.post(f"/api/event/runs/{made['token']}/verify", json={"phone": "9833022222", "code": code.json()["dev_code"],
                                                                             "email": "not-an-email", "name": "Asha"})
    assert bad.status_code == 400
    saved = await client.post(f"/api/event/runs/{made['token']}/verify", json={"phone": "9833022222", "code": code.json()["dev_code"],
                                                                               "email": "Asha@Example-Kaveri.in", "name": "Asha"})
    assert saved.status_code == 200, saved.text
    applied = saved.json()["applied"]
    assert saved.json()["new"] and applied["knowledge"] == "added" and applied["website"] == "added" and applied["brand"] == "added"
    assert applied["brand_message"] == "added" and applied["lead_ideas"] == "added" and applied["compliance"] == "added" and applied["trial"]
    user = await db().users.find_one({"phone": "+919833022222"})
    assert user["cohort"] == CODE and user["name"] == "Asha" and user["email"] == "asha@example-kaveri.in"
    assert user["trial"]["plan"] == "lite" and user["trial"]["reason"] == "event"
    b = await db().businesses.find_one({"owner_id": user["_id"]})
    assert b["name"] == "Kaveri Home Appliances" and b["whatsapp"] == "9833022222" and b["email"] == "asha@example-kaveri.in"
    assert b["identity"]["state"] == "Maharashtra" and b["brand"]["message"] == v["brand"]["options"][2]["message"]   # the pick they made
    site_doc = await db().sites.find_one({"owner_id": user["_id"]})
    assert site_doc["status"] == "draft" and site_doc["content"]["headline"] and site_doc["source"] == "event"
    kit = await db().brand_kits.find_one({"_id": user["_id"]})
    assert kit["design"]["logo_file_id"] and kit["design"]["source"] == "logo" and not kit.get("messages")   # the brand kit text is a live demo
    assert await db().compliance_items.count_documents({"owner_id": user["_id"]}) >= 24
    assert await db().lead_ideas.count_documents({"owner_id": user["_id"]}) == 1
    assert (await db().site_scans.find_one({"_id": user["_id"]}))["checks"]["mobile"] is True
    me = (await client.get("/api/me")).json()
    assert me["user"]["effective_plan"] == "lite" and me["user"]["trial_until"] and me["progress"]["profile"]
    assert me["features"]["inquiry_desk"] and me["features"]["cash_flow"] and me["features"]["score_recheck"]
    assert me["knowledge_percent"] >= 50
    mine = (await client.get("/api/event/mine")).json()
    assert mine["token"] == made["token"] and mine["applied"]["website"] == "added"
    # the website publishes straight away (their WhatsApp number is already in), for "swap with your neighbour"
    assert (await client.post("/api/site/publish")).status_code == 200
    qr = await client.get("/api/site/qr.png")
    assert qr.status_code == 200 and qr.content.startswith(b"\x89PNG")

    # once saved, it's theirs: another number can't take it, and the brand pick is changed in the hub
    other = await new_owner("9833033333")
    r = await other.post(f"/api/event/runs/{made['token']}/code", json={"phone": "9833033333"})
    assert r.status_code == 409
    assert (await client.post(f"/api/event/runs/{made['token']}/brand", json={"pick": 0})).status_code == 409
    live = (await admin.get(f"/api/admin/event/{CODE}/live")).json()
    assert live["runs"]["saved"] == 1 and live["accounts"] == 1 and live["demos"]["lead"]["Website live"] == 1
    assert live["demos"]["operations"]["Compliance calendars"] == 1 and live["seconds"]["everything_median"] is not None


async def test_an_existing_account_keeps_what_it_has(client):
    await open_event()
    owner = await new_owner("9833044444")
    await go_live(owner)
    before = await db().sites.find_one({})
    with site_server() as site:
        made = await make(client, form(site, gstin=None, entity="partnership", state="Gujarat"))
        await ev.drain()
    code = (await owner.post(f"/api/event/runs/{made['token']}/code", json={"phone": "9833044444"})).json()["dev_code"]
    r = await owner.post(f"/api/event/runs/{made['token']}/verify", json={"phone": "9833044444", "code": code, "email": "owner@example-sgi.in",
                                                                          "name": "Ravi"})
    assert r.status_code == 200 and not r.json()["new"]
    applied = r.json()["applied"]
    assert applied["website"] == "kept" and applied["brand_message"] == "kept" and applied["compliance"] == "added"
    user = await db().users.find_one({"phone": "+919833044444"})
    b = await db().businesses.find_one({"owner_id": user["_id"]})
    assert b["name"] == PROFILE["name"] and b["email"] == PROFILE["email"]          # the Knowledge Hub they wrote is untouched
    assert b["identity"]["entity"] == "partnership" and not b["identity"]["gst_registered"]
    after = await db().sites.find_one({"owner_id": user["_id"]})
    assert after["_id"] == before["_id"] and after["content"] == before["content"]
    assert await db().sites.count_documents({"owner_id": user["_id"]}) == 1


async def test_no_website_the_business_in_a_line_still_gets_a_pack(client):
    await open_event()
    made = await make(client, form(None, business_name="Riya Tiffins", about="Home-cooked lunch tiffins delivered to offices", city="Pune",
                                   gstin=gstin("27", "C"), entity="opc", gst_scheme="qrmp", staff=0))
    await ev.drain()
    v = (await client.get(f"/api/event/runs/{made['token']}")).json()
    assert v["steps"]["site"]["status"] == "skipped" and v["steps"]["check"]["status"] == "skipped"
    assert v["business"]["name"] == "Riya Tiffins" and v["business"]["industry"]
    assert v["identity"]["entity"] == "opc" and v["identity"]["gst_scheme"] == "qrmp"
    assert "GSTR-1 (quarterly, QRMP)" in v["compliance"]["kinds"] and "Professional tax (employer)" not in v["compliance"]["kinds"]
    assert v["design"]["source"] == "default" and v["website"]


async def test_an_ai_step_that_fails_is_left_for_after_login_and_the_pack_still_finishes(client, monkeypatch):
    await open_event()

    async def broken(p):
        raise AIError("engine down")
    monkeypatch.setattr(tasks, "brand_message", broken)
    with site_server() as site:
        made = await make(client, form(site))
        await ev.drain()
    run = await db().event_runs.find_one({"token": made["token"]})
    assert run["status"] == "done" and run["steps"]["brand_message"]["status"] == "after_login"
    assert run["steps"]["website"]["status"] == "done" and run["steps"]["lead_ideas"]["status"] == "done"
    assert (await client.get(f"/api/event/runs/{made['token']}/design/status_poster.png")).status_code == 200   # drawn from the business line


async def test_an_unreadable_website_still_gives_the_calendar_and_brand(client):
    await open_event()
    with site_server() as site:
        made = await make(client, form(site + "/missing"))
        await ev.drain()
    v = (await client.get(f"/api/event/runs/{made['token']}")).json()
    assert v["steps"]["site"]["status"] == "failed" and v["steps"]["check"]["status"] == "failed"
    assert v["steps"]["compliance"]["status"] == "done" and v["compliance"]["count"] > 0 and v["design"]


async def test_a_team_members_login_never_gets_a_pack_in_the_owners_hub(client):
    await open_event()
    made = await make(client, form(None, business_name="Riya Tiffins", about="Home-cooked lunch tiffins delivered to offices"))
    await ev.drain()
    run = await db().event_runs.find_one({"token": made["token"]})
    out = await ev.apply(run, {"_id": "member-1", "phone": "+919833055555", "team_of": "owner-1"}, "a@example.in", "A")
    assert out == {"all": "team"} and await db().businesses.count_documents({}) == 0


# ───────────────────────── the Membership preset ─────────────────────────
async def test_the_preset_moves_the_guides_five_features_into_membership_and_each_can_be_reset(client):
    admin = await new_owner(ADMIN)
    state = (await admin.get("/api/admin/config/presets/guide-membership")).json()
    assert not state["applied"] and {f["key"] for f in state["features"]} == {"lead_magnet", "revenue_levers", "pipeline", "quotations", "money_owed"}
    r = (await admin.post("/api/admin/config/presets/guide-membership")).json()
    assert r["applied"] and sorted(r["moved"]) == sorted(["lead_magnet", "revenue_levers", "pipeline", "quotations", "money_owed"])
    assert all(plans.META[k]["tier"] == "lite" for k in r["moved"])
    assert (await admin.post("/api/admin/config/presets/guide-membership")).json()["moved"] == []     # pressing it again changes nothing
    member = {"_id": "u1", "plan": "lite"}
    assert plans.has(member, "quotations") and plans.has(member, "pipeline") and not plans.has(member, "crm_sync")
    assert (await admin.post("/api/admin/config/features/pipeline/reset")).status_code == 200
    assert plans.META["pipeline"]["tier"] == "program" and not (await admin.get("/api/admin/config/presets/guide-membership")).json()["applied"]
    assert await db().config_changes.count_documents({"note": "Guide features into Membership"}) == 5


# ───────────────────────── the AI engine: a queue and retries ─────────────────────────
async def test_engine_calls_wait_their_turn_and_busy_answers_are_tried_again(setting, monkeypatch):
    setting(ai_provider="anthropic", ai_concurrency=3, ai_retries=2)
    in_flight, peak, calls = 0, 0, {"n": 0}

    async def fake(provider_name, system, prompt, mock, max_tokens):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        try:
            await asyncio.sleep(0.01)
            calls["n"] += 1
            if prompt == "busy-twice" and calls["n"] <= 2:
                raise Busy("anthropic 429", 0)
            if prompt == "always-busy":
                raise Busy("anthropic 529", 0)
            return mock(), Usage(10, 10, "test")
        finally:
            in_flight -= 1
    monkeypatch.setattr(provider, "_call_once", fake)
    results = await asyncio.gather(*(provider.complete("s", f"p{i}", lambda: {"ok": True}) for i in range(12)))
    assert all(r[0] == {"ok": True} for r in results) and peak <= 3
    calls["n"] = 0
    result, _ = await provider.complete("s", "busy-twice", lambda: {"ok": 2})
    assert result == {"ok": 2} and calls["n"] == 3
    with pytest.raises(AIError):
        await provider.complete("s", "always-busy", lambda: {})


async def test_when_the_website_cant_be_read_a_line_about_the_business_brings_the_rest(client):
    await open_event()
    with site_server() as site:
        made = await make(client, form(site + "/missing"))
        await ev.drain()
    v = (await client.get(f"/api/event/runs/{made['token']}")).json()
    assert v["needs_about"] and v["steps"]["website"]["status"] == "after_login" and v["site"]["host"] == "127.0.0.1:" + site.rsplit(":", 1)[1]
    r = await client.post(f"/api/event/runs/{made['token']}/about", json={"business_name": "Kaveri Home Appliances",
                                                                          "about": "Washing machine and fridge repair at home", "city": "Pune"})
    assert r.status_code == 200 and r.json()["status"] == "ai_queued" and not r.json()["needs_about"]
    again = await client.post(f"/api/event/runs/{made['token']}/about", json={"business_name": "Other", "about": "Something else entirely"})
    assert again.status_code == 409                                              # once per pack: no endless AI
    await ev.drain()
    v = (await client.get(f"/api/event/runs/{made['token']}")).json()
    assert v["status"] == "done" and v["business"]["name"] == "Kaveri Home Appliances" and v["business"]["city"] == "Pune"
    assert v["website"] and len(v["brand"]["options"]) == 3 and v["ideas"]


async def test_saved_while_still_being_written_the_rest_goes_in_when_ready(client):
    await open_event()
    with site_server() as site:
        made = await make(client, form(site))
        run = await ev._claim("queued", "rules")              # only phase 1 has run when they press Save
        assert await ev.build_rules(run)
    assert (await db().event_runs.find_one({"token": made["token"]}))["status"] == "ai_queued"
    code = (await client.post(f"/api/event/runs/{made['token']}/code", json={"phone": "9833071111"})).json()["dev_code"]
    r = await client.post(f"/api/event/runs/{made['token']}/verify", json={"phone": "9833071111", "code": code, "email": "a@example-early.in", "name": "A"})
    assert r.status_code == 200 and r.json()["pending"] and "website" not in r.json()["applied"]
    user = await db().users.find_one({"phone": "+919833071111"})
    assert not await db().sites.find_one({"owner_id": user["_id"]})
    assert (await client.get(f"/api/event/runs/{made['token']}")).json()["pending"]
    await ev.drain()                                            # phase 2 finishes: the late part goes in by itself
    run = await db().event_runs.find_one({"token": made["token"]})
    assert run["status"] == "done" and not run["apply_pending"] and run["applied"]["website"] == "added" and run["applied"]["compliance"] == "added"
    assert await db().sites.find_one({"owner_id": user["_id"]}) and await db().lead_ideas.find_one({"owner_id": user["_id"]})
    b = await db().businesses.find_one({"owner_id": user["_id"]})
    assert b["brand"]["message"] and b["email"] == "a@example-early.in"
    assert await ev.late_apply(run["_id"]) is None             # once only


async def test_one_gst_number_gets_at_most_two_packs_per_event(client):
    await open_event()
    body = form(None, business_name="Riya Tiffins", about="Home-cooked lunch tiffins delivered to offices")
    await make(client, body)
    await make(client, body)
    r = await client.post("/api/event/runs", json=body)
    assert r.status_code == 409 and "already built" in r.json()["detail"]
    other = await client.post("/api/event/runs", json={**body, "gstin": gstin("29", "P")})
    assert other.status_code == 200
    assert (await ev.counters(CODE))["packs"] == 3


async def test_a_pack_stuck_in_a_dead_process_is_finished_and_its_unwritten_pieces_wait_for_the_hub(client):
    await open_event()
    made = await make(client, form(None, business_name="Riya Tiffins", about="Home-cooked lunch tiffins delivered to offices"))
    old = now() - timedelta(minutes=30)
    await db().event_runs.update_one({"token": made["token"]}, {"$set": {"status": "ai", "started_at": old, "attempts": 3, "claim": "dead",
                                                                         "steps.website": {"status": "working"}}})
    await ev.requeue_stale()
    run = await db().event_runs.find_one({"token": made["token"]})
    assert run["status"] == "done" and run["steps"]["website"]["status"] == "after_login"
    # a write from the build that was presumed dead lands nowhere
    await ev._set({"_id": run["_id"], "claim": "dead"}, {"steps.website": {"status": "working"}})
    assert (await db().event_runs.find_one({"_id": run["_id"]}))["steps"]["website"]["status"] == "after_login"


async def test_the_event_trial_stretches_a_shorter_one_and_never_touches_a_plan_they_have(client):
    await open_event(trial_days=3)
    made = await make(client, form(None, business_name="Riya Tiffins", about="Home-cooked lunch tiffins delivered to offices"))
    await ev.drain()
    run = await db().event_runs.find_one({"token": made["token"]})
    short = {"_id": "u-short", "phone": "+919833081111", "ref_code": "RSHORT1", "plan": "free", "trial": {"plan": "lite", "until": now() + timedelta(days=1)}}
    await db().users.insert_one(short)
    out = await ev.apply(run, short, "", "")
    assert out.get("trial") and (await db().users.find_one({"_id": "u-short"}))["trial"]["reason"] == "event"
    paid = {"_id": "u-paid", "phone": "+919833081112", "ref_code": "RPAID01", "plan": "lite"}
    await db().users.insert_one(paid)
    await db().businesses.delete_many({})
    assert "trial" not in await ev.apply(run, paid, "", "") and "trial" not in await db().users.find_one({"_id": "u-paid"})


async def test_about_only_when_the_website_gave_nothing_and_logos_must_really_be_images(client):
    await open_event()
    with site_server() as site:
        made = await make(client, form(site))
        await ev.drain()
        r = await client.post(f"/api/event/runs/{made['token']}/about", json={"business_name": "X Business", "about": "Something else entirely"})
        assert r.status_code == 409                                   # its website was read: no second AI round
        PAGES["/fake.png"] = (b"GIF89a" + b"\x00" * 64, "image/png")   # says PNG, isn't
        try:
            from app import fetch
            with pytest.raises(fetch.FetchError):
                await fetch.fetch_image(site + "/fake.png")
            data, kind = await fetch.fetch_image(site + "/logo.png")
            assert kind == "image/png" and data == LOGO
        finally:
            PAGES.pop("/fake.png", None)


async def test_a_picture_asked_for_many_times_at_once_is_drawn_once(client):
    await open_event()
    made = await make(client, form(None, business_name="Riya Tiffins", about="Home-cooked lunch tiffins delivered to offices"))
    await ev.drain()
    rs = await asyncio.gather(*(client.get(f"/api/event/runs/{made['token']}/design/status_poster.png") for _ in range(6)))
    assert all(r.status_code == 200 for r in rs) and len({r.content for r in rs}) == 1
    assert await db().event_files.count_documents({"_id": {"$regex": ":status_poster:"}}) == 1


async def test_when_the_ai_account_is_at_its_limit_a_pack_waits_its_turn_instead_of_dropping_pieces(client, monkeypatch):
    await open_event()
    real, calls = tasks.brand_message, {"n": 0}

    async def busy_twice(p):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise Busy("simulated 429", 0)
        return await real(p)
    monkeypatch.setattr(tasks, "brand_message", busy_twice)
    made = await make(client, form(None, business_name="Riya Tiffins", about="Home-cooked lunch tiffins delivered to offices"))
    await ev.drain()
    run = await db().event_runs.find_one({"token": made["token"]})
    assert calls["n"] == 3 and run["steps"]["brand_message"]["status"] == "done" and len(run["brand"]["options"]) == 3
