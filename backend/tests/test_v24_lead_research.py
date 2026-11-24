"""v2.4 Lead research: a short brief on every new inquiry — from the inquiry and the lead's own business website only, never a
private person; 1 AI token, refunded when nothing useful is found; the monthly limit, the 20-token floor and the Researcher's
switch stop the automatic briefs but never the "Research this lead" button.

Every business, person, website, number and email here is invented. The lead's website is a small local test server; the
hub's website reader is pointed at it for the invented domains (tests may read 127.0.0.1)."""
import asyncio
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import pytest

from app import fetch, growth_core, lead_research
from app.ai import brief_tasks
from app.ai.provider import Usage
from app.db import db, month_key
from app.routers.public import save_lead
from tests.v24_helpers import owner_id, v24_member

SITE_HOME = """<html><head><title>Acme Snacks Pvt Ltd</title><meta name="description" content="Makers of baked namkeen and millet snacks in Vapi since 2011.">
</head><body><h1>Acme Snacks</h1><h2>Baked namkeen, packed fresh</h2><p>We supply supermarkets in Gujarat.</p>
<a href="/about">About us</a></body></html>"""
SITE_ABOUT = "<html><head><title>About</title></head><body><h2>About Acme Snacks</h2><p>A family business with our own factory.</p></body></html>"


@contextmanager
def serve(pages: dict, delay: float = 0.0):
    hits: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            hits.append(self.path)
            if delay:
                time.sleep(delay)
            body = pages.get(self.path.split("?")[0])
            status = 200 if body else 404
            data = (body or "not found").encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", hits
    finally:
        srv.shutdown()


@pytest.fixture
def sites(monkeypatch):
    """The invented domains in `table` are served by local test servers; every other address 'doesn't exist'. Records each
    address the hub asked for."""
    table: dict[str, str] = {}
    asked: list[str] = []
    real = fetch.read_site

    async def read_site(url: str):
        asked.append(url)
        u = urlparse(url if "://" in url else f"https://{url}")
        host = (u.hostname or "").removeprefix("www.")
        if host not in table:
            raise fetch.FetchError("We couldn't find that website. Check the address and try again.")
        facts = await real(table[host] + (u.path or "/"))
        facts.url = f"https://{host}/"
        return facts
    monkeypatch.setattr(fetch, "read_site", read_site)
    return table, asked


async def member(phone: str, paid: bool = True):
    c = await v24_member(phone, paid=paid)
    uid = await owner_id(c)
    return c, uid


async def lead_in(uid: str, name: str, phone: str, message: str, source: str = "website", **extra) -> dict:
    owner = await db().users.find_one({"_id": uid})
    b = await db().businesses.find_one({"owner_id": uid})
    return await save_lead(None, owner, b, name, phone, message, source, **extra)


async def tokens_used(uid: str) -> int:
    u = await db().usage.find_one({"user_id": uid, "month": month_key()})
    return int((u or {}).get("used", 0))


async def test_a_lead_from_a_company_email_gets_a_brief_with_its_website_as_the_source(client, sites):
    table, asked = sites
    c, uid = await member("9800410001")
    with serve({"/": SITE_HOME, "/about": SITE_ABOUT}) as (base, hits):
        table["acmesnacks.in"] = base
        before = await tokens_used(uid)
        lead = await lead_in(uid, "Neha Joshi", "", "We need printed boxes for our snack packs, about 5000 a month. Please share a rate.",
                             "email", email="neha@acmesnacks.in")
        await lead_research.settle()
    brief = (await db().leads.find_one({"_id": lead["_id"]}))["brief"]
    assert brief["status"] == "done" and brief["looked_up"] is True and brief["by"] == "auto" and brief["tokens"] == 1
    assert brief["sources"] == [{"label": "Their website", "url": "https://acmesnacks.in/"}]
    assert "baked namkeen" in brief["who"].lower() and "Nothing public was looked up" not in brief["who"]
    assert len(brief["questions"]) == 3 and brief["first_line"] and "5000 a month" in brief["asking"]
    assert asked == ["https://acmesnacks.in"] and "/" in hits                 # their own website, read live
    assert await tokens_used(uid) == before + 1                               # 1 AI token
    owner = await db().users.find_one({"_id": uid})
    assert (await growth_core.left(owner, "briefs"))["used"] == 1
    # the history line, and the AI team feed line that links to the deal
    act = await db().crm_activities.find_one({"lead_id": lead["_id"], "kind": "research"})
    assert act["text"].startswith("Lead researched") and act["by_name"] == "OwnerDesk"
    log = await db().agent_log.find_one({"ws": uid, "agent": "researcher"})
    assert log["ref"] == f"lead:{lead['_id']}" and log["status"] == "done"
    # the Brief tab and the Inquiries page read it
    r = (await c.get(f"/api/leads/{lead['_id']}/brief")).json()
    assert r["brief"]["sources"][0]["url"] == "https://acmesnacks.in/" and r["auto_on"] is True and r["left"] == r["limit"] - 1
    assert lead["_id"] in (await c.get("/api/leads/briefs")).json()["items"]


async def test_a_free_mail_lead_with_no_business_name_is_never_looked_up(client, sites):
    table, asked = sites
    c, uid = await member("9800410002")
    lead = await lead_in(uid, "Ravi Kumar", "", "Need a quote for a modular kitchen for my 2BHK flat in Thane. Pics on instagram.com/ravi.home",
                         "email", email="ravi.kumar@gmail.com")
    phone_only = await lead_in(uid, "Sunita", "+919820044444", "Please call me about wardrobes for two bedrooms")
    await lead_research.settle()
    for lid in (lead["_id"], phone_only["_id"]):
        brief = (await db().leads.find_one({"_id": lid}))["brief"]
        assert brief["status"] == "done" and brief["looked_up"] is False and brief["sources"] == []
        assert "Nothing public was looked up." in brief["who"]
        assert len(brief["questions"]) == 3
    assert asked == []            # gmail.com is not a website, and an Instagram link is not theirs: nothing was read


async def test_nothing_useful_writes_questions_only_and_refunds_the_token(client, sites, monkeypatch):
    c, uid = await member("9800410003")
    before = await tokens_used(uid)
    hi = await lead_in(uid, "Customer", "+919820055555", "Hi")
    await lead_research.settle()
    brief = (await db().leads.find_one({"_id": hi["_id"]}))["brief"]
    assert brief["status"] == "empty" and brief["tokens"] == 0 and len(brief["questions"]) == 3
    assert "Nothing public was looked up." in brief["who"]
    assert await tokens_used(uid) == before
    owner = await db().users.find_one({"_id": uid})
    assert (await growth_core.left(owner, "briefs"))["used"] == 0             # an empty brief doesn't count against the month

    # the AI ran and found nothing useful: the token it took is given back
    async def nothing(profile, lead, site):
        return {"who": "", "asking": "", "questions": ["What do you need?", "By when?", "Who decides?"], "first_line": "Hello.",
                "useful": False}, Usage()
    monkeypatch.setattr(lead_research.brief_tasks, "brief", nothing)
    vague = await lead_in(uid, "Mohan", "+919820066666", "saw your page, tell me more about what you do")
    await lead_research.settle()
    brief = (await db().leads.find_one({"_id": vague["_id"]}))["brief"]
    assert brief["status"] == "empty" and brief["questions"] == ["What do you need?", "By when?", "Who decides?"]
    assert await tokens_used(uid) == before
    assert await db().runs.count_documents({"user_id": uid, "kind": "lead_brief", "status": "refunded"}) == 1


async def test_the_monthly_limit_and_the_token_floor_stop_automatic_briefs_but_not_the_button(client, sites):
    c, uid = await member("9800410004")
    owner = await db().users.find_one({"_id": uid})
    limit = (await growth_core.left(owner, "briefs"))["limit"]
    for _ in range(limit):
        await growth_core.take(owner, "briefs")
    over = await lead_in(uid, "Kiran Shah", "+919820077777", "Need a quote for office interiors, 1200 sq ft")
    await lead_research.settle()
    assert "brief" not in await db().leads.find_one({"_id": over["_id"]})
    skipped = await db().agent_log.find_one({"ws": uid, "agent": "researcher", "status": "skipped"})
    assert "automatic briefs are used" in skipped["text"] and skipped["ref"] == f"lead:{over['_id']}"
    before = await tokens_used(uid)
    r = await c.post(f"/api/leads/{over['_id']}/brief")
    assert r.status_code == 200, r.text
    assert r.json()["brief"]["status"] == "done" and r.json()["brief"]["by"] == "you"
    assert await tokens_used(uid) == before + 1                               # a press uses a token…
    assert (await growth_core.left(owner, "briefs"))["used"] == limit         # …not the monthly allowance

    # fewer than 20 tokens left: no automatic brief; the button still works
    await db().growth_usage.delete_many({"ws": uid})
    allowance = (await c.get("/api/me")).json()["runs"]["allowance"]
    await db().usage.update_one({"user_id": uid, "month": month_key()}, {"$set": {"used": allowance - (lead_research.TOKEN_FLOOR - 1)}})
    low = await lead_in(uid, "Asha Patel", "+919820088888", "Want a modular kitchen with a breakfast counter")
    await lead_research.settle()
    assert "brief" not in await db().leads.find_one({"_id": low["_id"]})
    assert await db().agent_log.find_one({"ws": uid, "agent": "researcher", "text": {"$regex": "fewer than 20 AI tokens"}})
    assert (await c.post(f"/api/leads/{low['_id']}/brief")).json()["brief"]["status"] == "done"


async def test_switching_the_researcher_off_stops_automatic_briefs(client, sites):
    c, uid = await member("9800410005")
    r = await c.patch("/api/agents/researcher", json={"on": False})
    assert r.status_code == 200, r.text
    assert not await growth_core.role_on(uid, "researcher")
    lead = await lead_in(uid, "Deepa Iyer", "+919820099999", "Need a quote for a 3BHK full interior")
    await lead_research.settle()
    assert "brief" not in await db().leads.find_one({"_id": lead["_id"]})
    assert not await db().agent_log.find_one({"ws": uid, "agent": "researcher"})
    assert (await c.post(f"/api/leads/{lead['_id']}/brief")).json()["brief"]["status"] == "done"
    assert (await c.get(f"/api/leads/{lead['_id']}/brief")).json()["auto_on"] is False


async def test_the_customer_form_never_waits_for_the_research(client, sites):
    table, _ = sites
    c, uid = await member("9800410006")
    with serve({"/": SITE_HOME}, delay=0.6) as (base, _hits):
        table["acmesnacks.in"] = base
        started = time.monotonic()
        lead = await lead_in(uid, "Neha Joshi", "", "We need printed boxes for our snack packs", "email", email="neha@acmesnacks.in")
        took = time.monotonic() - started
        assert "brief" not in await db().leads.find_one({"_id": lead["_id"]})
        await lead_research.settle()
    assert took < 0.5
    assert (await db().leads.find_one({"_id": lead["_id"]}))["brief"]["looked_up"] is True


async def test_a_website_that_does_not_open_is_said_plainly(client, sites):
    c, uid = await member("9800410007")
    lead = await lead_in(uid, "Imran Shaikh", "+919820012121", "We need steel racks for our new godown, 40 units", "email",
                         email="imran@shaikh-steel.in")
    await lead_research.settle()
    brief = (await db().leads.find_one({"_id": lead["_id"]}))["brief"]
    assert brief["looked_up"] is False and "didn't open" in brief["note"] and "Nothing public was looked up." in brief["who"]


def test_which_addresses_count_as_a_business_website():
    mine = {"sgi.in"}
    assert lead_research.website_of({"email": "a@acme.in"}, mine) == ("https://acme.in", "email")
    for free in ("a@gmail.com", "a@yahoo.co.in", "a@outlook.com", "a@hotmail.com", "a@rediffmail.com", "a@icloud.com", "a@proton.me", "a@live.in"):
        assert lead_research.website_of({"email": free}, mine)[0] is None, free
    assert lead_research.website_of({"email": "me@sgi.in"}, mine)[0] is None                  # the owner's own domain
    assert lead_research.website_of({"email": "x@shop.example"}, mine)[0] is None             # a reserved name, never real
    assert lead_research.website_of({"message": "Our website is www.patel-tiles.in, please check"}, mine)[0] == "https://www.patel-tiles.in"
    assert lead_research.website_of({"message": "saw this on www.houzz.in/abc"}, mine)[0] is None
    assert lead_research.website_of({"message": "my page is instagram.com/someone"}, mine)[0] is None
    assert lead_research.website_of({"website": "acme-tools.in"}, mine) == ("https://acme-tools.in", "gave")


async def test_free_owners_are_refused(client, sites):
    c, uid = await member("9800410008", paid=False)
    lead = await lead_in(uid, "Ravi", "+919820013131", "Need a quote for a kitchen")
    await lead_research.settle()
    assert "brief" not in await db().leads.find_one({"_id": lead["_id"]})
    assert (await c.get(f"/api/leads/{lead['_id']}/brief")).status_code == 403
    assert (await c.post(f"/api/leads/{lead['_id']}/brief")).status_code == 403
    assert (await c.get("/api/leads/briefs")).status_code == 403


async def test_two_presses_at_once_spend_one_token(client, sites):
    c, uid = await member("9800410009")
    lead = await lead_in(uid, "Kavita", "+919820014141", "Need wardrobes for two bedrooms, sliding doors")
    await lead_research.settle()
    before = await tokens_used(uid)
    a, b = await asyncio.gather(c.post(f"/api/leads/{lead['_id']}/brief"), c.post(f"/api/leads/{lead['_id']}/brief"))
    assert sorted([a.status_code, b.status_code]) == [200, 409]
    assert await tokens_used(uid) == before + 1


def test_the_rules_brief_adds_no_facts():
    profile = {"name": "Shree Ganesh Interiors", "offers": [{"name": "Modular kitchen"}]}
    out = brief_tasks.rules_brief(profile, {"name": "Neha", "message": "Need a modular kitchen quote", "source": "website"}, None)
    assert out["who"].endswith("Nothing public was looked up.") and "Modular kitchen" in out["asking"]
    assert out["useful"] and len(out["questions"]) == 3 and "Shree Ganesh Interiors" in out["first_line"]
    assert not brief_tasks.rules_brief(profile, {"name": "X", "message": "hello sir", "source": "website"}, None)["useful"]
