"""Competitor Watch (Membership) and Content & company research (Action Program).

Every business, website, place id and number here is invented. Competitor websites are served from a local test server
(tests may read 127.0.0.1); Google's Places API is a fake transport; the AI is the deterministic mock unless a test swaps it."""
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app import competitor_watch as cw, fetch
from app.agents import scheduler
from app.ai.provider import Usage
from app.config import settings
from app.db import IST, db, now
from tests.conftest import new_owner

ADMIN = "9999900000"
PROFILE = {"name": "Sharma Modular Kitchens", "city": "Thane", "industry": "Modular kitchens and wardrobes made in our own workshop",
           "offers": [{"name": "Modular kitchen", "price": "₹1,75,000", "unit": "onwards"}, {"name": "Wardrobe", "price": "", "unit": ""}],
           "ideal_customer": "Families moving into a new flat", "why_us": "Fixed handover date\nOwn workshop", "language": "English"}
RIVAL = "Patel Kitchen Gallery"


# ───────────────────────── a small invented competitor website ─────────────────────────
def html(body: str, title: str = RIVAL) -> str:
    return f"<html><head><title>{title}</title><meta name='viewport' content='width=device-width'></head><body>{body}</body></html>"


HOME_V1 = html("""<nav><a href="/">Home</a><a href="/contact">Contact</a></nav><h1>Patel Kitchen Gallery</h1><p>Kitchens for homes in Thane.</p>
<ul><li><a href="/products">Our products</a></li><li><a href="/about">About us</a></li><li><a href="/contact">Contact</a></li></ul>
<footer>All rights reserved</footer>""")
PRODUCTS_V1 = html("<h2>Products</h2><div class='card'><h3>Modular kitchen</h3><p>₹1,85,000 onwards</p></div><p>Wardrobe Rs. 45,000 per unit</p>")
ABOUT = html("<h2>About</h2><p>We have made kitchens in Thane for many years.</p>")


def site_v1() -> dict:
    return {"/": HOME_V1, "/products": PRODUCTS_V1, "/about": ABOUT}


def site_v2(pages: dict) -> None:
    """A price drops, a festive offer goes up, the About page goes, a Projects page comes."""
    pages["/"] = HOME_V1.replace('<a href="/about">About us</a>', '<a href="/projects">Recent projects</a>')
    pages["/products"] = PRODUCTS_V1.replace("₹1,85,000", "₹1,65,000").replace("</body>", "<p>Diwali offer: flat 10% off on all kitchens</p></body>")
    pages["/projects"] = html("<h2>Projects</h2><p>A U-shaped kitchen for a 2BHK in Thane.</p>")
    del pages["/about"]


@contextmanager
def serve(pages: dict):
    """Serves `pages` (path → HTML, (body, content type, status), or a function that answers itself); records every path."""
    hits: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            path = self.path.split("?")[0]
            hits.append(path)
            page = pages.get(path)
            if callable(page):
                try:
                    page(self)
                except OSError:          # the hub hung up (it gave up on a slow site): that's the point
                    pass
                return
            if page is None:
                body, ctype, status = b"not found", "text/html", 404
            elif isinstance(page, tuple):
                body, ctype, status = page[0].encode() if isinstance(page[0], str) else page[0], page[1], page[2]
            else:
                body, ctype, status = page.encode(), "text/html; charset=utf-8", 200
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", hits
    finally:
        srv.shutdown()


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setattr(cw, "PAUSE_SECONDS", 0)


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


@pytest.fixture
def days(monkeypatch):
    """Moves the hub's clock forward by whole days (the once-a-day check limit follows it)."""
    state = {"n": 0}

    def later(n: int = 1):
        state["n"] += n
    monkeypatch.setattr(cw, "clock", lambda: now() + timedelta(days=state["n"]))
    return later


async def owner_on(phone: str, plan: str, profile: bool = True):
    c = await new_owner(phone)
    if profile:
        assert (await c.put("/api/business", json=PROFILE)).status_code == 200
    uid = (await c.get("/api/me")).json()["user"]["id"]
    if plan != "free":
        admin = await new_owner(ADMIN)
        assert (await admin.patch(f"/api/admin/users/{uid}", json={"plan": plan})).status_code == 200
    return c, uid


async def add(c, name=RIVAL, **kw):
    r = await c.post("/api/competitors", json={"name": name, **kw})
    assert r.status_code == 200, r.text
    return r.json()


async def check(c) -> dict:
    r = await c.post("/api/competitors/check")
    assert r.status_code == 200, r.text
    return r.json()["report"]


async def summary_runs() -> int:
    return await db().runs.count_documents({"kind": "competitor_summary"})


# ───────────────────────── plans, roles, and one business at a time ─────────────────────────
async def test_plans_roles_and_other_businesses(client):
    free, _ = await owner_on("9000010001", "free")
    assert (await free.get("/api/competitors")).status_code == 403
    assert (await free.get("/api/research")).status_code == 403

    lite, _ = await owner_on("9000010002", "lite")
    assert (await lite.get("/api/competitors")).status_code == 200
    assert (await lite.get("/api/research")).status_code == 403                       # research is in the Action Program
    assert (await lite.post("/api/research/content")).status_code == 403

    program, _ = await owner_on("9000010003", "program")
    assert (await program.get("/api/research")).status_code == 200
    assert (await program.get("/api/competitors")).status_code == 200                 # the Membership comes with it

    growth, _ = await owner_on("9000010004", "growth")                                # team logins
    for phone, role in (("9000010005", "staff"), ("9000010006", "manager")):
        assert (await growth.post("/api/team/invites", json={"phone": phone, "name": f"Test {role}", "role": role})).status_code == 200
    staff, manager = await new_owner("9000010005"), await new_owner("9000010006")
    assert (await staff.get("/api/competitors")).status_code == 403
    assert (await staff.post("/api/competitors", json={"name": RIVAL})).status_code == 403
    assert (await staff.get("/api/research")).status_code == 403
    assert (await manager.get("/api/competitors")).status_code == 200
    mine = await add(manager, "Meher Wardrobe Studio")
    assert [x["id"] for x in (await growth.get("/api/competitors")).json()["competitors"]] == [mine["id"]]   # the owner's hub

    # another business can't see, change, delete or check it
    assert (await lite.get("/api/competitors")).json()["competitors"] == []
    assert (await lite.patch(f"/api/competitors/{mine['id']}", json={"name": "Taken over"})).status_code == 404
    assert (await lite.delete(f"/api/competitors/{mine['id']}")).status_code == 404
    assert (await lite.post("/api/competitors/check")).status_code == 400
    assert (await db().competitors.find_one({"_id": mine["id"]}))["name"] == "Meher Wardrobe Studio"


async def test_five_competitors_at_most_and_the_ad_library_link(client):
    c, _ = await owner_on("9000020001", "lite")
    first = await add(c, "Patel Kitchens & Co", website="www.patel-kitchens.example.in")
    assert first["website"] == "https://www.patel-kitchens.example.in"
    link = urlparse(first["ad_library_link"])
    assert (link.scheme, link.netloc, link.path) == ("https", "www.facebook.com", "/ads/library/")
    assert parse_qs(link.query) == {"active_status": ["active"], "ad_type": ["all"], "country": ["IN"], "q": ["Patel Kitchens & Co"]}
    r = await c.patch(f"/api/competitors/{first['id']}", json={"facebook_name": "Patel Kitchen Gallery Thane"})
    assert parse_qs(urlparse(r.json()["ad_library_link"]).query)["q"] == ["Patel Kitchen Gallery Thane"]

    assert (await c.post("/api/competitors", json={"name": "Copy", "website": "https://www.patel-kitchens.example.in"})).status_code == 409
    assert (await c.post("/api/competitors", json={"name": "Copy", "website": "patel-kitchens.example.in/kitchens"})).status_code == 409
    assert (await c.post("/api/competitors", json={"name": "Bad site", "website": "javascript:alert(1)"})).status_code == 422
    assert (await c.post("/api/competitors", json={"name": "Bad place", "google_place_id": "../../etc"})).status_code == 422
    for n in ("Meher Wardrobe Studio", "Kitchen Craft Thane", "Rathi Interiors", "Om Modular Works"):
        await add(c, n)
    r = await c.post("/api/competitors", json={"name": "One too many"})
    assert r.status_code == 400 and "up to 5" in r.json()["detail"]
    assert len((await c.get("/api/competitors")).json()["competitors"]) == 5
    assert (await c.delete(f"/api/competitors/{first['id']}")).status_code == 200
    await add(c, "One too many")                                                    # room again


# ───────────────────────── the weekly check: what changed ─────────────────────────
async def test_a_change_between_two_checks_is_reported_with_one_ai_run(client, days):
    c, _ = await owner_on("9000030001", "lite")
    pages = site_v1()
    with serve(pages) as (site, hits):
        comp = await add(c, website=site)
        first = await check(c)
        item = first["items"][0]
        assert item["first_check"] and sorted(item["website_changes"]["checked"]) == ["/", "/about", "/products"]
        assert first["note"].startswith("First look saved") and first["summary"] is None and not first["ai_used"]
        assert await summary_runs() == 0
        snap = await db().competitor_snapshots.find_one({"_id": comp["id"]})
        products = next(p for p in snap["pages"] if p["path"] == "/products")
        assert [(p["item"], p["amounts"]) for p in products["prices"]] == [("Modular kitchen — onwards", ["₹1,85,000"]),
                                                                           ("Wardrobe per unit", ["₹45,000"])]
        home = next(p for p in snap["pages"] if p["path"] == "/")
        assert "All rights reserved" not in home["lines"] and "Home" not in home["lines"]     # menu and footer left out

        r = await c.post("/api/competitors/check")                                     # once a day
        assert r.status_code == 429 and "tomorrow" in r.json()["detail"]
        assert not (await c.get("/api/competitors")).json()["can_check"]

        days(1)
        same = await check(c)
        assert same["changed"] == 0 and same["note"] == "No changes this week." and not same["ai_used"] and same["summary"] is None
        assert not same["items"][0]["website_changes"]["any"]
        assert await summary_runs() == 0                                               # nothing changed → no AI run

        site_v2(pages)
        days(1)
        hits.clear()
        report = await check(c)
    assert len([h for h in hits if h != "/robots.txt"]) <= cw.MAX_PAGES
    w = report["items"][0]["website_changes"]
    assert report["changed"] == 1 and w["any"] and not report["items"][0]["first_check"]
    assert [(x["page"], x["item"], x["before"], x["after"]) for x in w["prices_changed"]] == [("/products", "Modular kitchen — onwards", "₹1,85,000", "₹1,65,000")]
    assert [x["line"] for x in w["offers_new"]] == ["Diwali offer: flat 10% off on all kitchens"]
    assert [x["path"] for x in w["pages_added"]] == ["/projects"] and [x["path"] for x in w["pages_removed"]] == ["/about"]
    assert {"page": "/", "line": "Recent projects"} in w["lines_added"] and {"page": "/", "line": "About us"} in w["lines_removed"]
    assert "Diwali offer: flat 10% off on all kitchens" not in [x["line"] for x in w["lines_added"]]   # shown once, as an offer
    assert report["ai_used"] and await summary_runs() == 1                              # exactly one AI run for the whole report
    assert "₹1,65,000" in report["summary"] and RIVAL in report["summary"]

    page = (await c.get("/api/competitors")).json()
    assert page["latest"]["id"] == report["id"] and page["competitors"][0]["checked_today"]
    assert [r["id"] for r in (await c.get("/api/competitors/reports")).json()] == [report["id"], same["id"], first["id"]]


async def test_a_new_website_address_starts_a_fresh_first_look(client, days):
    c, _ = await owner_on("9000030101", "lite")
    with serve(site_v1()) as (one, _), serve({"/": html("<h1>Another site</h1>")}) as (two, _):
        comp = await add(c, website=one)
        await check(c)
        await c.patch(f"/api/competitors/{comp['id']}", json={"website": two})
        days(1)
        report = await check(c)
    assert report["items"][0]["first_check"] and report["changed"] == 0 and await summary_runs() == 0


async def test_a_page_that_fails_once_is_not_called_removed(client, days):
    c, _ = await owner_on("9000030201", "lite")
    pages = site_v1()
    with serve(pages) as (site, _):
        await add(c, website=site)
        await check(c)
        pages["/products"] = ("busy", "text/html", 503)
        days(1)
        report = await check(c)
    item = report["items"][0]
    assert not item["website_changes"]["pages_removed"] and report["changed"] == 0
    assert any("Couldn't open 1 of their pages" in e for e in item["errors"])


async def test_robots_txt_is_obeyed_and_five_pages_at_most(client):
    c, _ = await owner_on("9000040001", "lite")
    many = {"/": html("".join(f'<a href="/{p}">{p}</a> ' for p in ("pricing", "offers", "products", "services", "projects", "about",
                                                                     "catalogue", "packages"))),
            "/robots.txt": ("User-agent: *\nDisallow: /pricing\n\nUser-agent: SomeOtherBot\nDisallow: /", "text/plain", 200)}
    for p in ("offers", "products", "services", "projects", "about", "catalogue", "packages", "pricing"):
        many[f"/{p}"] = html(f"<p>{p} page</p>")
    shut = {"/": html("<p>Private</p>"),
            "/robots.txt": ("User-agent: BusinessAIActionHub\nDisallow: /\n\nUser-agent: *\nAllow: /", "text/plain", 200)}
    down = {"/": html("<p>Up</p>"), "/robots.txt": ("error", "text/plain", 503)}
    with serve(many) as (s1, hits1), serve(shut) as (s2, hits2), serve(down) as (s3, hits3):
        await add(c, "Kitchen Craft Thane", website=s1)
        await add(c, "Rathi Interiors", website=s2)
        await add(c, "Om Modular Works", website=s3)
        report = await check(c)
    by = {i["name"]: i for i in report["items"]}
    assert hits1[0] == "/robots.txt" and "/pricing" not in hits1                         # robots.txt first; the disallowed page never opened
    pages = [h for h in hits1 if h != "/robots.txt"]
    assert len(pages) == cw.MAX_PAGES and pages[0] == "/"
    w = by["Kitchen Craft Thane"]["website_changes"]
    assert w["skipped"] == ["/pricing"] and len(w["checked"]) == cw.MAX_PAGES
    assert hits2 == ["/robots.txt"] and by["Rathi Interiors"]["website_changes"] is None   # our user agent is asked to stay off
    assert "robots.txt" in by["Rathi Interiors"]["errors"][0]
    assert hits3 == ["/robots.txt"] and by["Om Modular Works"]["errors"] == [cw.SITE_DOWN]  # failing site: skipped this time
    assert await db().competitor_snapshots.count_documents({"pages": {"$exists": True}}) == 1


def test_robots_rules_and_prices_read_plainly():
    r = cw.parse_robots("User-agent: *\nDisallow: /private\nAllow: /private/brochure\nDisallow: /*.php$\nCrawl-delay: 3\n")
    assert not r.allowed("/private/x") and r.allowed("/private/brochure/1") and not r.allowed("/a.php") and r.allowed("/a.php?x=1")
    assert r.allowed("/") and r.delay == 3
    assert cw.parse_robots("User-agent: *\nDisallow:\n").allowed("/anything")
    assert cw.prices_in("Rs.1.5 lakh, ₹ 2 cr, INR 1,00,000.00, Rs 185000/-, ₹999") == ["₹1.5 lakh", "₹2 crore", "₹1,00,000", "₹1,85,000", "₹999"]


# ───────────────────────── Google: only with the hub's key ─────────────────────────
class FakePlaces:
    def __init__(self):
        self.rating, self.count, self.calls, self.name = 4.3, 120, [], RIVAL

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        if req.method == "POST" and req.url.path.endswith("/places:searchText"):
            return httpx.Response(200, json={"places": [{"id": "ChIJinventedPatel01", "displayName": {"text": RIVAL},
                                                         "formattedAddress": "Ghodbunder Road, Thane", "rating": self.rating,
                                                         "userRatingCount": self.count}]})
        if req.method == "GET" and req.url.path.endswith("/places/ChIJinventedPatel01"):
            return httpx.Response(200, json={"rating": self.rating, "userRatingCount": self.count, "displayName": {"text": self.name},
                                             "formattedAddress": "Ghodbunder Road, Thane"})
        return httpx.Response(404, json={})


async def test_google_is_skipped_cleanly_without_a_key(client, setting):
    setting(google_places_api_key="")
    c, _ = await owner_on("9000050001", "lite")
    assert (await c.get("/api/competitors")).json()["google_available"] is False
    r = await c.get("/api/competitors/google-search?q=Patel Kitchen Thane")
    assert r.status_code == 400 and "isn't set up" in r.json()["detail"]
    await add(c, google_place_id="ChIJinventedPatel01")
    report = await check(c)
    item = report["items"][0]
    assert item["google"] is None and "Google key" in item["google_note"] and item["errors"] == []
    assert report["note"].startswith("Nothing could be compared") and not report["ai_used"]


async def test_google_rating_and_reviews_with_a_key(client, setting, monkeypatch, days):
    setting(google_places_api_key="test-places-key")
    fake = FakePlaces()
    monkeypatch.setattr(cw, "PLACES_TRANSPORT", httpx.MockTransport(fake))
    c, _ = await owner_on("9000050101", "program")
    assert (await c.get("/api/competitors")).json()["google_available"] is True
    found = (await c.get("/api/competitors/google-search?q=Patel Kitchen Thane")).json()["results"]
    assert found == [{"place_id": "ChIJinventedPatel01", "name": RIVAL, "address": "Ghodbunder Road, Thane", "rating": 4.3, "count": 120}]
    search = fake.calls[0]
    assert search.headers["X-Goog-Api-Key"] == "test-places-key" and search.headers["X-Goog-FieldMask"] == cw.SEARCH_MASK
    comp = await add(c, google_place_id=found[0]["place_id"], google_name=found[0]["name"], google_address=found[0]["address"])

    first = await check(c)
    assert first["items"][0]["google"] == {"rating": 4.3, "count": 120, "rating_change": None, "count_change": None}
    assert fake.calls[-1].headers["X-Goog-FieldMask"] == cw.DETAILS_MASK and await summary_runs() == 0
    fake.rating, fake.count, fake.name = 4.2, 125, "Patel Kitchen Gallery Thane"
    days(1)
    second = await check(c)
    g = second["items"][0]["google"]
    assert (g["rating"], g["count"], g["rating_change"], g["count_change"]) == (4.2, 125, -0.1, 5)
    assert comp["id"] == second["items"][0]["competitor_id"]
    # Google's numbers are shown, but never handed to the AI or copied into post ideas and My outputs (kept 30 days at most)
    assert second["changed"] == 1 and await summary_runs() == 0 and second["note"].startswith("Only Google Maps")
    ideas = (await c.post("/api/research/content")).json()["output"]
    assert ideas["based_on"] is None and "4.2" not in str(ideas) and "125" not in str(ideas)
    assert cw.moves(second["items"]) == []
    me = await db().competitors.find_one({"_id": comp["id"]})
    assert me["google_name"] == "Patel Kitchen Gallery Thane"                        # the name is refreshed with each reading

    # 30 days after the last reading the listing's name, address and numbers are gone; the place id stays
    month_ago = now() - timedelta(days=31)
    await db().competitors.update_one({"_id": comp["id"]}, {"$set": {"google_at": month_ago}})
    await db().competitor_snapshots.update_one({"_id": comp["id"]}, {"$set": {"google.at": month_ago}})
    listed = (await c.get("/api/competitors")).json()["competitors"][0]
    assert (listed["google_name"], listed["google_address"], listed["google_place_id"]) == ("", "", "ChIJinventedPatel01")
    assert (await db().competitor_snapshots.find_one({"_id": comp["id"]}))["google"] is None
    # editing the competitor can't bring the old name back or restart its 30 days
    await c.patch(f"/api/competitors/{comp['id']}", json={"notes": "x", "google_place_id": "ChIJinventedPatel01", "google_name": RIVAL})
    me = await db().competitors.find_one({"_id": comp["id"]})
    assert me["google_name"] == "" and me["google_at"] < now() - timedelta(days=30)

    for _ in range(20):
        await c.get("/api/competitors/google-search?q=Kitchen Thane")
    assert (await c.get("/api/competitors/google-search?q=Kitchen Thane")).status_code == 429     # the key is billed: 20 a day


# ───────────────────────── the Monday agent ─────────────────────────
async def test_monday_agent_reports_only_to_owners_with_competitors(client):
    watching, wid = await owner_on("9000060001", "lite")
    idle, iid = await owner_on("9000060002", "lite")
    for c in (watching, idle):
        for other in ("week_plan", "calendar_auto"):                                # Monday-morning agents this test isn't about
            assert (await c.patch(f"/api/agents/{other}", json={"on": False})).status_code == 200
    monday = datetime(2026, 10, 26, 8, 5, tzinfo=IST)
    with serve(site_v1()) as (site, _):
        await add(watching, website=site)
        out = await scheduler.tick(at=monday)
        assert out["scheduled"] >= 1
        again = await scheduler.tick(at=monday + timedelta(minutes=10))              # one run per owner per week
    reports = [r async for r in db().competitor_reports.find({})]
    assert [(r["ws"], r["source"], r["week"]) for r in reports] == [(wid, "weekly", "2026-10-26")]
    notes = [n["text"] async for n in db().notices.find({"user_id": wid}) if "Competitor Watch" in n["text"]]
    assert notes == ["Competitor Watch: first look saved for your 1 competitor. From next Monday you'll see what changed."]
    assert not [n async for n in db().notices.find({"user_id": iid}) if "Competitor" in n["text"]]
    alerts = [o async for o in db().outbox.find({"kind": "owner_alert"})]
    assert [(o["to"], o["params"][0]) for o in alerts] == [("+919000060001", PROFILE["name"])]       # the owner only
    log = [e async for e in db().agent_log.find({"agent": "competitor_watch"})]
    assert [(e["ws"], e["status"]) for e in log] == [(wid, "done")]                 # no "skipped" line for the idle owner
    assert again["scheduled"] == 0
    with serve(site_v1()):
        await scheduler.tick(at=monday + timedelta(hours=25))                       # Tuesday: past the day's grace
    assert await db().competitor_reports.count_documents({}) == 1


# ───────────────────────── the summary only says what the hub saw ─────────────────────────
def test_the_summary_drops_numbers_the_hub_did_not_see():
    items = [{"name": RIVAL, "website_changes": {"any": True, "prices_changed": [{"page": "/products", "item": "Modular kitchen", "before": "₹1,85,000",
                                                                                 "after": "₹1,65,000", "line": "x"}],
                                                 "offers_new": [{"page": "/products", "line": "Flat 10% off"}]}, "google": None}]
    text = cw.summary_check(items)({"summary": "Patel Kitchen Gallery cut the kitchen price to ₹1,65,000 and has 10% off. "
                                               "They probably spend ₹50,000 a month on ads. Their sales are up 30%. Keep your quotes clear."})
    assert text == "Patel Kitchen Gallery cut the kitchen price to ₹1,65,000 and has 10% off. Keep your quotes clear."


# ───────────────────────── Content & company research ─────────────────────────
async def test_post_ideas_from_the_profile_then_from_the_report(client, days):
    c, _ = await owner_on("9000070001", "program")
    r = await c.post("/api/research/content")
    assert r.status_code == 200, r.text
    out = r.json()["output"]
    assert out["based_on"] is None and "business profile alone" in out["note"]
    assert len(out["result"]["ideas"]) == 5 and {i["answers"] for i in out["result"]["ideas"]} == {cw.OWN}

    pages = site_v1()
    with serve(pages) as (site, _):
        await add(c, website=site)
        await check(c)
        site_v2(pages)
        days(1)
        report = await check(c)
    out = (await c.post("/api/research/content")).json()["output"]
    ideas = out["result"]["ideas"]
    assert out["based_on"] == {"report_id": report["id"], "week": report["week"]} and len(ideas) == 5
    answered = [i["answers"] for i in ideas if i["answers"] != cw.OWN]
    assert answered and all(a.startswith(RIVAL) for a in answered)                  # the moves come from the report itself
    assert any("₹1,85,000 → ₹1,65,000" in a for a in answered)
    assert all(RIVAL.lower() not in (i["hook"] + i["say"]).lower() for i in ideas)  # a post never names the competitor
    assert {i["format"] for i in ideas} <= set(cw.FORMATS)
    page = (await c.get("/api/research")).json()
    assert len(page["content"]) == 2 and page["report"]["id"] == report["id"]
    assert await db().outputs.count_documents({"kind": "research_content"}) == 2   # also kept in My outputs


async def test_company_plan_has_code_built_links_and_a_csv(client):
    c, _ = await owner_on("9000080001", "program")
    r = await c.post("/api/research/companies", json={"who": "Restaurants and cloud kitchens", "city": "Pune", "extra": "50+ covers"})
    assert r.status_code == 200, r.text
    out = r.json()["output"]
    links = {x["label"]: x["url"] for x in out["links"] if not x.get("type")}
    q = "Restaurants and cloud kitchens Pune"
    assert parse_qs(urlparse(links["Google Maps"]).query) == {"api": ["1"], "query": [q]}
    assert parse_qs(urlparse(links["IndiaMART (through Google)"]).query)["q"] == [f"site:indiamart.com {q}"]
    assert parse_qs(urlparse(links["Justdial (through Google)"]).query)["q"] == [f"site:justdial.com {q}"]
    assert links["LinkedIn company search"].startswith("https://www.linkedin.com/search/results/companies/?")
    assert parse_qs(urlparse(links["LinkedIn company search"]).query)["keywords"] == [q]
    assert not any("gem.gov.in" in u for u in links.values())                       # not relevant to restaurants
    assert out["csv"].splitlines() == ["company,website,city,contact page,why they fit,status"]
    assert out["result"]["company_types"] and len(out["result"]["qualify"]) >= 2 and "scrape" in out["note"]

    gem = (await c.post("/api/research/companies", json={"who": "Garment manufacturers", "city": "Tiruppur"})).json()["output"]
    assert any("site:gem.gov.in Garment manufacturers Tiruppur" in parse_qs(urlparse(x["url"]).query).get("q", [""])[0] for x in gem["links"])
    t = await c.get("/api/research/companies/template.csv")
    assert t.status_code == 200 and t.headers["content-type"].startswith("text/csv")
    assert t.text.lstrip("﻿").startswith("company,website,city,contact page,why they fit,status")
    assert (await c.post("/api/research/companies", json={"who": "x" * 201})).status_code == 422


async def test_company_plan_drops_names_phones_and_emails(client, monkeypatch):
    c, _ = await owner_on("9000080101", "program")

    async def sly(system, prompt, mock, max_tokens=1200):
        assert "Never name a real company, person, phone number, email or website" in prompt
        return {"summary": "List exporters near the port.",
                "company_types": [{"type": "Acme Garments Pvt. Ltd., Tiruppur", "why": "Big buyer"},
                                  {"type": "Mid-size garment exporters", "why": "They buy packaging every month"},
                                  {"type": "Knitwear units", "why": "Talk to Mr. Raman at the gate"}],
                "filters": {"size": ["50-200 staff"], "industry": ["Garments"], "location": ["Tiruppur", "Visit www.acme-garments.example.in"]},
                "qualify": ["Call 98765 43210 and ask for the owner", "Write to buyer@acme-garments.example.in for the catalogue",
                            "Do they export to more than 2 countries?", "Do they run more than 50 machines?", "Shree Balaji Exports LLP is a good fit"]}, Usage()
    monkeypatch.setattr(cw, "complete", sly)
    out = (await c.post("/api/research/companies", json={"who": "Garment exporters", "city": "Tiruppur"})).json()["output"]
    plan = out["result"]
    assert [t["type"] for t in plan["company_types"]] == ["Mid-size garment exporters", "Knitwear units"]
    assert plan["company_types"][1]["why"] == ""                                     # the line naming a person went
    assert plan["qualify"] == ["Do they export to more than 2 countries?", "Do they run more than 50 machines?"]
    assert plan["filters"]["location"] == ["Tiruppur"] and plan["dropped"] == 6
    text = str(out)
    for bad in ("Acme", "98765", "@", "Raman", "Balaji"):
        assert bad not in text


# ───────────────────────── fixes from the independent review ─────────────────────────
def test_robots_wildcards_take_linear_time():
    m = cw._robots_match
    assert m("/*.php$", "/x/y.php") and not m("/*.php$", "/x.php5") and not m("/*.php$", "/a.php?x=1")
    assert m("/a*b*c", "/aXbYcZ") and not m("/a*b*c", "/aXcYb") and m("*", "/anything") and m("/fish*.php", "/fish.php")
    assert m("/x$", "/x") and not m("/x$", "/xy") and m("/a*$", "/abc") and not m("/ab*ba$", "/aba")
    rules = "".join(f"Disallow: /{'*a' * 30}*b{suffix}\n" for suffix in ("", "$"))
    r = cw.parse_robots("User-agent: *\n" + rules)
    path = "/" + "a" * 5000
    started = time.perf_counter()
    assert r.allowed(path) and not r.allowed(path + "b")
    assert time.perf_counter() - started < 0.05                                    # a regex took 21 s on ten of these


def test_prices_pair_only_when_the_other_numbers_match():
    def snap(body: str) -> dict:
        return cw.read_page(fetch.Page("http://127.0.0.1:1/p", html(body), False, 0))[0]
    w = cw.compare([snap("<p>2BHK package ₹3,50,000</p>")], [snap("<p>3BHK package ₹4,50,000</p>")])
    assert w["prices_changed"] == []
    assert [(x["item"], x["price"]) for x in w["prices_added"]] == [("3BHK package", "₹4,50,000")]
    assert [(x["item"], x["price"]) for x in w["prices_removed"]] == [("2BHK package", "₹3,50,000")]
    w = cw.compare([snap("<p>Modular kitchen ₹1,85,000 onwards</p>")], [snap("<p>Modular kitchen now ₹1,65,000 onwards</p>")])
    assert [(x["before"], x["after"]) for x in w["prices_changed"]] == [("₹1,85,000", "₹1,65,000")]


async def test_a_robots_txt_with_a_bom_is_still_obeyed(client):
    c, _ = await owner_on("9000090001", "lite")
    pages = {"/": html('<ul><li><a href="/pricing">Pricing</a></li><li><a href="/about">About</a></li></ul>'),
             "/pricing": html("<p>Kitchen ₹99,000</p>"), "/about": html("<p>About us</p>"),
             "/robots.txt": ("\ufeffUser-agent: *\nDisallow: /pricing\n".encode("utf-8"), "text/plain", 200)}
    with serve(pages) as (site, hits):
        await add(c, website=site)
        report = await check(c)
    assert "/pricing" not in hits and report["items"][0]["website_changes"]["skipped"] == ["/pricing"]


async def test_a_watched_page_not_read_this_time_is_kept_not_called_removed(client, days):
    c, _ = await owner_on("9000090101", "lite")
    pages = site_v1()
    with serve(pages) as (site, hits):
        await add(c, website=site)
        await check(c)
        pages["/robots.txt"] = ("User-agent: *\nDisallow: /products\n", "text/plain", 200)   # now off limits
        days(1)
        hits.clear()
        report = await check(c)
    w = report["items"][0]["website_changes"]
    assert "/products" not in hits and w["skipped"] == ["/products"] and w["kept"] == ["/products"]
    assert "/products" not in w["checked"] and not w["pages_removed"] and not w["any"] and report["changed"] == 0


def trickle(handler):
    """A page that sends one byte every 0.2 s, for 20 s."""
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html")
    handler.send_header("Content-Length", "100000")
    handler.end_headers()
    for _ in range(100):
        handler.wfile.write(b"<")
        handler.wfile.flush()
        time.sleep(0.2)


def cut_off(handler):
    """A page that promises 5,000 bytes, sends 20 and hangs up."""
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html")
    handler.send_header("Content-Length", "5000")
    handler.end_headers()
    handler.wfile.write(b"<html><body>Half a p")
    handler.close_connection = True


async def test_a_site_trickling_bytes_cannot_hold_up_the_monday_run(client, monkeypatch):
    monkeypatch.setattr(cw, "SITE_BUDGET_SECONDS", 0.5)
    monkeypatch.setattr(cw, "SITE_GRACE_SECONDS", 1.0)
    c, uid = await owner_on("9000090201", "lite")
    for other in ("week_plan", "calendar_auto"):
        assert (await c.patch(f"/api/agents/{other}", json={"on": False})).status_code == 200
    with serve({"/": trickle}) as (slow, _), serve(site_v1()) as (fine, _):
        await add(c, "Slow Site Kitchens", website=slow)
        await add(c, website=fine)
        started = time.monotonic()
        await scheduler.tick(at=datetime(2026, 10, 26, 8, 5, tzinfo=IST))
        took = time.monotonic() - started
    assert took < 8                                                                  # 20 s of trickle didn't hold the clock
    by = {i["name"]: i for i in (await db().competitor_reports.find_one({"ws": uid}))["items"]}
    assert by["Slow Site Kitchens"]["errors"] == [cw.SITE_SLOW] and by["Slow Site Kitchens"]["website_changes"] is None
    assert by[RIVAL]["website_changes"]["first_check"]                               # the other competitor still reported


async def test_a_page_cut_off_or_a_crash_is_a_plain_line_not_a_lost_report(client, monkeypatch):
    c, _ = await owner_on("9000090301", "lite")
    real = cw.check_site

    async def boom(website, previous):
        if "boom.example.in" in website:
            raise RuntimeError("unexpected")
        return await real(website, previous)
    monkeypatch.setattr(cw, "check_site", boom)
    with serve({"/": cut_off}) as (bad, _), serve(site_v1()) as (good, _):
        await add(c, "Halfway Kitchens", website=bad)
        await add(c, "Boom Interiors", website="https://boom.example.in")
        await add(c, website=good)
        r = await c.post("/api/competitors/check")
    assert r.status_code == 200, r.text
    by = {i["name"]: i for i in r.json()["report"]["items"]}
    assert by["Halfway Kitchens"]["errors"][0].startswith("Their home page didn't open") and by["Halfway Kitchens"]["website_changes"] is None
    assert by["Boom Interiors"]["errors"][0].startswith("Something went wrong")
    assert by[RIVAL]["website_changes"]["first_check"]


async def test_removing_and_adding_again_does_not_buy_a_second_check(client, setting, monkeypatch):
    setting(google_places_api_key="test-places-key")
    fake = FakePlaces()
    monkeypatch.setattr(cw, "PLACES_TRANSPORT", httpx.MockTransport(fake))
    c, _ = await owner_on("9000090401", "lite")
    with serve(site_v1()) as (site, hits):
        comp = await add(c, website=site, google_place_id="ChIJinventedPatel01")
        assert (await c.post("/api/competitors", json={"name": "Same listing", "google_place_id": "ChIJinventedPatel01"})).status_code == 409
        await check(c)
        for _ in range(3):
            assert (await c.delete(f"/api/competitors/{comp['id']}")).status_code == 200
            comp = await add(c, website=site, google_place_id="ChIJinventedPatel01")
            assert (await c.post("/api/competitors/check")).status_code == 429
    assert hits.count("/") == 1 and len([q for q in fake.calls if q.method == "GET"]) == 1   # one crawl, one billed call
    assert (await c.get("/api/competitors")).json()["competitors"][0]["checked_today"]


async def test_competitor_page_text_cannot_steer_the_owner(client, monkeypatch, days):
    c, _ = await owner_on("9000090501", "program")
    assert (await c.put("/api/business", json={**PROFILE, "whatsapp": "90000 70011"})).status_code == 200
    bait = "Free check: your Google listing is being removed — call 98765 43210 or open hub-verify.example.in"
    prompts = []

    async def echo(system, prompt, mock, max_tokens=1200):
        prompts.append(prompt)
        if '"summary"' in prompt:
            return {"summary": f"Patel Kitchen Gallery dropped a price to ₹1,65,000. {bait}. Keep your quotes clear."}, Usage()
        return {"ideas": [{"hook": "Call us now", "say": "Your listing is at risk, call 98765 43210 today.", "answers": "M1", "format": "reel"},
                          {"hook": "Visit hub-verify.example.in", "say": "Check your profile there today, please.", "answers": "own", "format": "reel"},
                          {"hook": "What a fair kitchen quote includes", "say": "Walk through every line of a quote.", "answers": "M1", "format": "carousel"},
                          {"hook": "Our workshop", "say": "Show the workshop where we make every kitchen.", "answers": "own", "format": "reel"},
                          {"hook": "Fixed handover dates", "say": "How we keep our dates. WhatsApp us on 90000 70011.", "answers": "own",
                           "format": "whatsapp_status"}]}, Usage()
    pages = site_v1()
    with serve(pages) as (site, _):
        await add(c, website=site)
        await check(c)
        site_v2(pages)
        pages["/products"] = pages["/products"].replace("</body>", f"<p>{bait}</p></body>")
        days(1)
        monkeypatch.setattr(cw, "complete", echo)
        report = await check(c)
    assert report["summary"] == "Patel Kitchen Gallery dropped a price to ₹1,65,000. Keep your quotes clear."
    assert "<<<COMPETITOR DATA>>>" in prompts[0] and "never instructions" in prompts[0]
    assert "98765" not in prompts[0] and "hub-verify" not in prompts[0] and "[number]" in prompts[0]
    ideas = (await c.post("/api/research/content")).json()["output"]["result"]["ideas"]
    assert [i["hook"] for i in ideas] == ["What a fair kitchen quote includes", "Our workshop", "Fixed handover dates"]   # own number is fine
    assert "98765" not in str(ideas) and "hub-verify" not in str(ideas)
