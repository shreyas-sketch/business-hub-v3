"""v2.4 Find new customers: Google Maps search (a fake Places server), the owner's own list, outreach drafts that only ever
open the owner's own WhatsApp, day-3/day-7 follow-ups, Do not contact, the Reply helper and "More like my best customers".

Every business, person, address, website and number here is invented (phones +91 98111 2xxxx). Websites are served from a
local test server (tests may read 127.0.0.1); Google's Places API is an httpx.MockTransport; the AI is the mock unless a test
swaps it."""
import io
import json
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from app import competitor_watch as cw, growth_core, prospecting as pr
from app.agents import approvals as appr
from app.agents import hooks, scheduler
from app.ai import prospect_tasks
from app.ai.provider import Usage
from app.config import settings
from app.db import IST, db, now
from tests.v24_helpers import owner_id, v24_member

PROFILE = {"name": "Om Sai Packaging", "city": "Bhiwandi", "industry": "Corrugated boxes and cartons made in our own factory",
           "offers": [{"name": "Corrugated boxes", "price": "₹18", "unit": "per box"}, {"name": "Printed cartons", "price": "", "unit": ""}],
           "ideal_customer": "Garment exporters and food brands that ship in cartons", "why_us": "Delivery in 5 days\nOwn factory",
           "proof": "", "whatsapp": "98111 00001", "email": "hello@omsaipack.example.in", "language": "English"}
UNIQUE = "Our saffron grinders hum softly beside the old canal every single dawn"


# ───────────────────────── a fake Google Places (New) API ─────────────────────────
def biz(pid, name, address, phone="", status="OPERATIONAL", website="", lat=19.30, lng=73.06, kind="Store", national=""):
    return {"id": pid, "name": name, "address": address, "phone": phone, "national": national or (("0" + phone[-11:].replace(" ", "")) if phone else ""),
            "status": status, "website": website, "lat": lat, "lng": lng, "type": kind}


KESAR = biz("ChIJfakeKesar0001", "Kesar Spice Works", "Gala 7, Kalher Road, Bhiwandi", "+91 98111 20001", kind="Spice store")
NEEL = biz("ChIJfakeNeel00002", "Neelkanth Garments", "Unit 12, Narpoli Estate, Bhiwandi", "+91 98111 20002", kind="Clothing manufacturer")
SHUT = biz("ChIJfakeShut00003", "Shutter Down Traders", "Shop 3, Old Agra Road, Bhiwandi", "+91 98111 20003", status="CLOSED_PERMANENTLY")
RAMDEV = biz("ChIJfakeRamdev004", "Ramdev Exports Pvt. Ltd.", "Survey 41, Vadpe, Bhiwandi", "+91 98111 20004")
LOTUS = biz("ChIJfakeLotus0005", "Lotus Snacks Co", "Plot 9, Purna Village, Bhiwandi", "+91 98111 20005")
OWN = biz("ChIJfakeOmSai0006", "Om Sai Packaging", "Plot 2, Kasheli, Bhiwandi", "+91 98111 00001")
FAR = biz("ChIJfakeFarAway07", "Faraway Foods", "MIDC, Nashik", "+91 98111 20007", lat=20.0, lng=73.8)
NOPHONE = biz("ChIJfakeNoPhone08", "Silent Looms", "Gala 1, Kamatghar, Bhiwandi", "")
GOOGLE_ONLY = [KESAR, NEEL, SHUT, RAMDEV, LOTUS, OWN, FAR, NOPHONE]
SECRETS = [b["name"] for b in GOOGLE_ONLY if b is not OWN] + [b["address"] for b in GOOGLE_ONLY] + \
          [b["phone"] for b in GOOGLE_ONLY if b["phone"] and b is not OWN] + \
          [b["phone"].replace(" ", "")[-10:] for b in GOOGLE_ONLY if b["phone"] and b is not OWN]


def place_json(b: dict, fields: set[str]) -> dict:
    full = {"id": b["id"], "displayName": {"text": b["name"], "languageCode": "en"}, "formattedAddress": b["address"],
            "types": ["store", "establishment"], "primaryTypeDisplayName": {"text": b["type"], "languageCode": "en"},
            "businessStatus": b["status"], "googleMapsUri": f"https://maps.google.com/?cid={b['id'][-4:]}",
            "location": {"latitude": b["lat"], "longitude": b["lng"]}}
    if b["phone"]:
        full.update(nationalPhoneNumber=b["national"], internationalPhoneNumber=b["phone"])
    if b["website"]:
        full["websiteUri"] = b["website"]
    return {k: v for k, v in full.items() if k in fields or k == "id" and "id" in fields}


class FakePlaces:
    def __init__(self, places: list[dict] | None = None):
        self.places = {b["id"]: dict(b) for b in (places or GOOGLE_ONLY)}
        self.calls: list[httpx.Request] = []
        self.fail = False

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        if self.fail:
            return httpx.Response(500, json={})
        mask = req.headers.get("X-Goog-FieldMask", "")
        if req.method == "POST" and req.url.path.endswith("/places:searchText"):
            body = json.loads(req.content)
            fields = {f.removeprefix("places.") for f in mask.split(",")}
            if "Om Sai Packaging" in body["textQuery"]:
                return httpx.Response(200, json={"places": [place_json(self.places[OWN["id"]], fields)]})
            return httpx.Response(200, json={"places": [place_json(b, fields) for b in self.places.values()][:body.get("pageSize", 20)]})
        if req.method == "GET" and "/places/" in req.url.path:
            b = self.places.get(req.url.path.rsplit("/", 1)[-1])
            if not b:
                return httpx.Response(404, json={"error": {"status": "NOT_FOUND"}})
            return httpx.Response(200, json=place_json(b, set(mask.split(","))))
        return httpx.Response(404, json={})

    def searches(self) -> list[dict]:
        return [json.loads(r.content) for r in self.calls if r.method == "POST"]

    def details_calls(self) -> list[httpx.Request]:
        return [r for r in self.calls if r.method == "GET"]


@pytest.fixture
def places(monkeypatch):
    fake = FakePlaces()
    old = settings.google_places_api_key
    object.__setattr__(settings, "google_places_api_key", "test-places-key")
    monkeypatch.setattr(cw, "PLACES_TRANSPORT", httpx.MockTransport(fake))
    yield fake
    object.__setattr__(settings, "google_places_api_key", old)


@pytest.fixture
def no_places():
    old = settings.google_places_api_key
    object.__setattr__(settings, "google_places_api_key", "")
    yield
    object.__setattr__(settings, "google_places_api_key", old)


@contextmanager
def serve(pages: dict):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            page = pages.get(self.path.split("?")[0])
            body, status = (page.encode(), 200) if page else (b"not found", 404)
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}/"
    finally:
        srv.shutdown()


SITE = {"/": f"""<html><head><title>Kesar Spice Works</title><meta name="description" content="Spices ground fresh in Bhiwandi"></head>
<body><h1>Kesar Spice Works</h1><p>{UNIQUE}.</p><p>Call 98111 20001 or write to sales@kesar.example.in</p>
<h2>Turmeric, chilli and garam masala in bulk packs</h2></body></html>"""}


# ───────────────────────── helpers ─────────────────────────
async def member(phone: str, paid: bool = True):
    c = await v24_member(phone, paid=paid, profile=PROFILE)
    uid = await owner_id(c)
    return c, uid


async def runs_used(uid: str, kind: str | None = None) -> int:
    q = {"user_id": uid, "status": "done"}
    if kind:
        q["kind"] = kind
    return await db().runs.count_documents(q)


async def search_and_keep(c, names: list[str], kinds=("spice and garment makers",)) -> list[dict]:
    assert (await c.put("/api/prospects/kinds", json={"kinds": list(kinds)})).status_code == 200
    r = await c.post("/api/prospects/search", json={})
    assert r.status_code == 200, r.text
    res = r.json()
    pick = [x for x in res["results"] if x["name"] in names]
    r = await c.post("/api/prospects/keep", json={"search_id": res["search_id"], "items": [{"place_id": x["place_id"], "kind": x["kind"]} for x in pick]})
    assert r.status_code == 200, r.text
    items = (await c.get("/api/prospects/list")).json()["items"]
    return [i for i in items if i["name"] in names]


async def draft(c, ids: list[str]) -> dict:
    r = await c.post("/api/prospects/draft", json={"ids": ids})
    assert r.status_code == 200, r.text
    return r.json()


async def scan_for_google(ws: str) -> list[str]:
    """Google's names, addresses and phones anywhere they must never be saved."""
    found = []
    for coll in ("prospects", "prospect_searches", "prospect_block", "prospect_settings", "approvals", "agent_log", "outputs",
                 "agent_settings", "events", "growth_usage", "job_claims", "runs"):
        async for doc in db()[coll].find({}):
            text = str(doc)
            for s in SECRETS:
                if s in text:
                    found.append(f"{coll}: {s}")
    return found


# ───────────────────────── search ─────────────────────────
async def test_search_leaves_out_closed_known_and_own_and_keep_checks_phones(client, places):
    from app import crm
    c, uid = await member("9800410001")
    await crm.upsert_contact(uid, "Suresh Patil", "98000 41111", None, "Ramdev Exports", "manual")      # known by name (company)
    await crm.upsert_contact(uid, "Anil Kumar", "+91 98111 20005", None, "", "manual")                 # known by phone (Lotus)
    page = (await c.get("/api/prospects")).json()
    assert page["places_ready"] and page["limits"]["searches"]["left"] == 10 and page["line"].startswith("You decide who to contact")
    assert (await c.put("/api/prospects/kinds", json={"kinds": ["Spice makers", "garment units", "spice makers"]})).json()["kinds"] == \
        ["Spice makers", "garment units"]
    r = await c.post("/api/prospects/search", json={})
    assert r.status_code == 200, r.text
    res = r.json()
    names = {x["name"] for x in res["results"]}
    assert {"Kesar Spice Works", "Neelkanth Garments", "Lotus Snacks Co", "Faraway Foods", "Silent Looms"} == names
    assert res["skipped"] == {"closed": 1, "known": 1, "own": 1, "blocked": 0, "far": 0}
    assert res["found"] == 8 and res["new"] == 5 and res["attribution"] == "Google Maps"
    assert res["limits"]["searches"]["left"] == 9
    # one Google call per kind, 20 results each, in the owner's city, and never the phone/website/rating tier
    sent = places.searches()
    assert [s["textQuery"] for s in sent] == ["Spice makers in Bhiwandi", "garment units in Bhiwandi"]
    assert all(s["pageSize"] == 20 and "locationBias" not in s for s in sent)
    assert places.calls[0].headers["X-Goog-FieldMask"] == pr.SEARCH_MASK
    assert "Phone" not in pr.SEARCH_MASK and "website" not in pr.SEARCH_MASK and "rating" not in pr.SEARCH_MASK
    s = await db().prospect_searches.find_one({"ws": uid})
    assert (s["kinds"], s["area"], s["found"], s["new"], s["radius_km"]) == (["Spice makers", "garment units"], "Bhiwandi", 8, 5, None)

    keep = [x for x in res["results"] if x["name"] in ("Kesar Spice Works", "Neelkanth Garments", "Lotus Snacks Co")]
    r = await c.post("/api/prospects/keep", json={"search_id": res["search_id"], "items": [{"place_id": x["place_id"], "kind": x["kind"]} for x in keep]})
    out = r.json()
    assert out["kept"] == 2 and out["skipped"]["known"] == 1 and out["limits"]["kept"]["left"] == 98   # Lotus: same phone as a contact
    assert out["left_out"] == [{"place_id": LOTUS["id"], "why": "Already in your contacts"}]
    assert set(out["kept_place_ids"]) == {KESAR["id"], NEEL["id"]}
    assert places.details_calls()[-1].headers["X-Goog-FieldMask"] == pr.DETAILS_MASK
    # the same search again shows them as already kept
    again = (await c.post("/api/prospects/search", json={})).json()
    kept = {x["name"]: x for x in again["results"] if x["kept"]}
    assert set(kept) == {"Kesar Spice Works", "Neelkanth Garments"} and again["new"] == 3
    # the list shows Google's details fetched live, 20 a page, with the attribution
    lst = (await c.get("/api/prospects/list")).json()
    assert lst["total"] == 2 and lst["page_size"] == 20 and lst["attribution"] == "Google Maps"
    by = {i["name"]: i for i in lst["items"]}
    assert by["Kesar Spice Works"]["phone"] and by["Kesar Spice Works"]["address"] == KESAR["address"] and by["Kesar Spice Works"]["status"] == "kept"
    # the database: place IDs, kinds, statuses and dates — no Google name, address or phone
    docs = [d async for d in db().prospects.find({"ws": uid})]
    assert {d["place_id"] for d in docs} == {KESAR["id"], NEEL["id"]}
    assert all("name" not in d and "phone" not in d and "address" not in d and "website" not in d for d in docs)
    assert await scan_for_google(uid) == []
    log = [x["text"] async for x in db().agent_log.find({"ws": uid, "agent": "prospector"})]
    assert any(t.startswith("Kept 2 businesses from a search for 'Spice makers'") for t in log)


async def test_the_11th_search_is_refused_and_a_google_failure_gives_it_back(client, places):
    c, uid = await member("9800410002")
    assert (await c.put("/api/prospects/kinds", json={"kinds": ["spice makers"]})).status_code == 200
    places.fail = True
    r = await c.post("/api/prospects/search", json={})
    assert r.status_code == 502 and "was not used" in r.json()["detail"]
    assert (await c.get("/api/prospects")).json()["limits"]["searches"]["left"] == 10
    places.fail = False
    for _ in range(10):
        assert (await c.post("/api/prospects/search", json={})).status_code == 200
    r = await c.post("/api/prospects/search", json={})
    assert r.status_code == 429 and "10 business searches" in r.json()["detail"]
    assert (await c.get("/api/prospects")).json()["limits"]["searches"] == {"limit": 10, "used": 10, "left": 0, "what": "business searches",
                                                                            "period": "month"}
    assert await db().prospect_searches.count_documents({"ws": uid}) == 10


async def test_within_x_km_uses_the_owners_place_and_stores_no_coordinates(client, places):
    c, uid = await member("9800410003")
    assert (await c.put("/api/prospects/kinds", json={"kinds": ["food brands"]})).status_code == 200
    r = await c.post("/api/prospects/search", json={"mode": "radius", "radius_km": 10})
    assert r.status_code == 400 and "find your business on Google Maps" in r.json()["detail"]
    found = (await c.post("/api/prospects/home/find")).json()
    assert found["results"] == [{"place_id": OWN["id"], "name": OWN["name"], "address": OWN["address"]}]
    assert (await c.put("/api/prospects/home", json={"place_id": OWN["id"]})).json()["home_set"]
    r = await c.post("/api/prospects/search", json={"mode": "radius", "radius_km": 10})
    assert r.status_code == 200, r.text
    res = r.json()
    sent = places.searches()[-1]
    assert sent["textQuery"] == "food brands" and sent["locationBias"]["circle"]["radius"] == 10000.0
    assert sent["locationBias"]["circle"]["center"] == {"latitude": OWN["lat"], "longitude": OWN["lng"]}
    names = {x["name"] for x in res["results"]}
    assert "Faraway Foods" not in names and "Om Sai Packaging" not in names and res["skipped"]["far"] == 1
    assert all(x["distance_km"] is not None and x["distance_km"] <= 10 for x in res["results"])
    st = await db().prospect_settings.find_one({"_id": uid})
    assert st["home_place_id"] == OWN["id"] and "19.3" not in str(st) and "73.06" not in str(st)
    assert (await db().prospect_searches.find_one({"ws": uid}))["area"] == "within 10 km of your business"
    r = await c.post("/api/prospects/search", json={"mode": "radius", "radius_km": 60})
    assert r.status_code == 422


async def test_without_a_places_key_search_says_so_and_the_own_list_still_works(client, no_places):
    c, uid = await member("9800410004")
    page = (await c.get("/api/prospects")).json()
    assert page["places_ready"] is False and "server setting" in page["no_key"]
    assert (await c.put("/api/prospects/kinds", json={"kinds": ["spice makers"]})).status_code == 200
    r = await c.post("/api/prospects/search", json={})
    assert r.status_code == 400 and "server setting" in r.json()["detail"]
    csv = "Name,Phone,City,Kind\nSai Ram Exports,98111 30001,Bhiwandi,Garment exporter\n"
    r = await c.post("/api/prospects/upload", files={"file": ("list.csv", csv.encode(), "text/csv")})
    assert r.status_code == 200 and r.json()["added"] == 1, r.text
    assert (await c.get("/api/prospects")).json()["limits"]["searches"]["left"] == 10


# ───────────────────────── the owner's own list ─────────────────────────
async def test_own_list_upload_dedupes_and_goes_keep_draft_contact(client, no_places):
    from app import crm
    c, uid = await member("9800410005")
    await crm.upsert_contact(uid, "Meena Shah", "98111 30003", None, "", "manual")
    csv = ("NAME,Mobile,EMAIL,Website,City,Kind,Owner remarks\n"
           "Sai Ram Exports,98111 30001,,,Bhiwandi,Garment exporter,met at expo\n"
           "Sai Ram Exports (again),+91 98111 30001,,,Bhiwandi,Garment exporter,\n"          # same phone in the file
           "Shah Textiles,98111 30003,,,Bhiwandi,Textiles,\n"                                 # already a contact
           "No Way To Reach,,,,Bhiwandi,,\n"                                                  # no phone or email
           "Mail Only Foods,,orders@mailonly.example.in,mailonly.example.in,Thane,Food brand,\n")
    r = await c.post("/api/prospects/upload", files={"file": ("expo.csv", csv.encode(), "text/csv")})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["added"] == 2 and out["skipped"] == {"no_contact": 1, "known": 1, "duplicate": 1, "blocked": 0, "over_limit": 0}
    # the same file again: nothing new
    again = (await c.post("/api/prospects/upload", files={"file": ("expo.csv", csv.encode(), "text/csv")})).json()
    assert again["added"] == 0 and again["skipped"]["duplicate"] == 2
    # an Excel file works too, and needs a name and a phone or email column
    import openpyxl
    wb = openpyxl.Workbook()
    wb.active.append(["name", "phone", "kind"])
    wb.active.append(["Ganga Prints", "98111 30009", "Printer"])
    buf = io.BytesIO()
    wb.save(buf)
    r = await c.post("/api/prospects/upload", files={"file": ("list.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert r.status_code == 200 and r.json()["added"] == 1
    bad = await c.post("/api/prospects/upload", files={"file": ("x.csv", b"Company only\nAcme\n", "text/csv")})
    assert bad.status_code == 400 and "name" in bad.json()["detail"]

    items = {i["name"]: i for i in (await c.get("/api/prospects/list")).json()["items"]}
    sai = items["Sai Ram Exports"]
    assert (sai["source"], sai["phone"], sai["city"], sai["kind"], sai["status"]) == ("list", "+919811130001", "Bhiwandi", "Garment exporter", "kept")
    d = await draft(c, [sai["id"], items["Mail Only Foods"]["id"]])
    assert d["drafted"] == 2
    a = await db().approvals.find_one({"prospect_id": sai["id"]})
    assert (a["kind"], a["agent"], a["step"], a["to_phone"], a["status"]) == ("outreach", "outreach_writer", "first", "+919811130001", "pending")
    # sent from the general Send list: the owner's own WhatsApp, and the business counts as contacted
    r = await c.post(f"/api/approvals/{a['_id']}/own")
    assert r.status_code == 200, r.text
    assert r.json()["link"].startswith("https://wa.me/919811130001?text=")
    p = await db().prospects.find_one({"_id": sai["id"]})
    assert p["status"] == "contacted" and p["next_followup_on"] == (pr.ist_day() + timedelta(days=3)).isoformat()
    # an email-only business: no WhatsApp number, so the message can't open WhatsApp — the email draft is the way
    mail = items["Mail Only Foods"]
    r = await c.post(f"/api/prospects/{mail['id']}/whatsapp", json={})
    assert r.status_code == 400 and "no mobile number" in r.json()["detail"]
    page = (await c.get(f"/api/prospects/{mail['id']}")).json()
    assert page["draft"]["email_subject"] and page["email"] == "orders@mailonly.example.in"
    r = await c.post(f"/api/prospects/{mail['id']}/email-sent")
    assert r.status_code == 200 and r.json()["status"] == "contacted"
    assert (await c.get("/api/prospects")).json()["limits"]["first_messages"]["used"] == 2


# ───────────────────────── drafts ─────────────────────────
async def test_five_drafts_for_one_token_from_the_hub_and_their_own_site_never_copied(client, places, monkeypatch):
    c, uid = await member("9800410006")
    with serve(SITE) as site:
        places.places[KESAR["id"]]["website"] = site
        csv = "name,phone,kind\n" + "".join(f"List Biz {n},98111 4000{n},Food brand\n" for n in range(1, 5))
        assert (await c.post("/api/prospects/upload", files={"file": ("l.csv", csv.encode(), "text/csv")})).json()["added"] == 4
        await search_and_keep(c, ["Kesar Spice Works"])
        items = (await c.get("/api/prospects/list")).json()["items"]
        assert len(items) == 5
        prompts = []
        real = prospect_tasks.complete

        async def spy(system, prompt, mock, max_tokens=1200):
            prompts.append(prompt)
            return await real(system, prompt, mock, max_tokens)
        monkeypatch.setattr(prospect_tasks, "complete", spy)
        before = await runs_used(uid)
        out = await draft(c, [i["id"] for i in items])
    assert out["drafted"] == 5 and out["token_used"] and await runs_used(uid) - before == 1
    assert await db().approvals.count_documents({"ws": uid, "kind": "outreach", "step": "first", "status": "pending"}) == 5
    # the website reached the AI only as quoted data, with its phone and email taken out — and the Google name never did
    assert "<<<PROSPECT DATA>>>" in prompts[0] and UNIQUE in prompts[0]
    assert "98111 20001" not in prompts[0] and "sales@kesar" not in prompts[0]
    for it in out["items"]:
        assert it["status"] == "drafted" and it["messages"][0]["status"] == "pending"
        text = it["messages"][0]["text"]
        assert "Om Sai Packaging" in text and '"no"' in text and "₹" not in text
        assert it["draft"]["email_subject"] and it["draft"]["call_opener"]
        assert UNIQUE not in str(it)
    page = (await c.get("/api/prospects/list")).json()
    assert all(i["waiting"] == ["first"] for i in page["items"])
    log = [x["text"] async for x in db().agent_log.find({"ws": uid, "agent": "outreach_writer"})]
    assert len(log) == 5 and all(t.startswith("Wrote a first message for a business you kept") for t in log)
    assert await scan_for_google(uid) == []
    # more than 5 at once is refused; a business already drafted waits for its message to be sent
    assert (await c.post("/api/prospects/draft", json={"ids": [i["id"] for i in items] + ["x"]})).status_code == 422
    r = await c.post("/api/prospects/draft", json={"ids": [items[0]["id"]]})
    assert r.status_code == 400 and "already waiting" in r.json()["detail"]


async def test_an_ai_draft_that_copies_their_site_or_invents_facts_is_never_used(client, places, monkeypatch):
    c, uid = await member("9800410007")
    with serve(SITE) as site:
        places.places[KESAR["id"]]["website"] = site
        csv = "name,phone,kind\nGanga Prints,98111 40011,Printer\nHari Om Foods,98111 40012,Food brand\n"
        assert (await c.post("/api/prospects/upload", files={"file": ("l.csv", csv.encode(), "text/csv")})).json()["added"] == 2
        kesar = (await search_and_keep(c, ["Kesar Spice Works"]))[0]
        listed = {i["name"]: i for i in (await c.get("/api/prospects/list")).json()["items"]}
        good = ("Hello, this is Owner from Om Sai Packaging in Bhiwandi. We make corrugated boxes in our own factory and wanted to ask "
                "if they could help you ship your prints. Can I send a few details?")

        async def bad_ai(system, prompt, mock, max_tokens=1200):
            refs = [r for r in ("P1", "P2", "P3") if f"[{r}]" in prompt]
            body = {"email_subject": "Boxes for you", "email_body": "Hello, we are Om Sai Packaging and we make boxes. " * 3,
                    "call_opener": "Hello, this is Owner from Om Sai Packaging, calling about boxes."}
            return {"drafts": [
                {"ref": refs[0], **body, "whatsapp": f"Hello from Om Sai Packaging! {UNIQUE}, and so do our box machines."},   # copies their site
                {"ref": refs[1], **body, "whatsapp": "Om Sai Packaging here: 25 years, 400 happy clients, boxes at ₹9 each."},  # invented numbers, price
                {"ref": refs[2], **body, "whatsapp": good}][:len(refs)]}, Usage()
        monkeypatch.setattr(prospect_tasks, "complete", bad_ai)
        before = await runs_used(uid)
        out = await draft(c, [kesar["id"], listed["Hari Om Foods"]["id"], listed["Ganga Prints"]["id"]])
    assert out["drafted"] == 3 and out["token_used"] and out["by_ai"] == 1 and await runs_used(uid) - before == 1
    texts = {i["name"]: i["messages"][0]["text"] for i in out["items"]}
    assert UNIQUE not in str(texts) and "saffron" not in texts["Kesar Spice Works"]
    assert "25 years" not in texts["Hari Om Foods"] and "₹9" not in texts["Hari Om Foods"]
    assert texts["Ganga Prints"].startswith(good) and '"no"' in texts["Ganga Prints"]       # the easy "no" is added when missing
    drafts = {i["name"]: i["draft"]["by"] for i in out["items"]}
    assert drafts == {"Kesar Spice Works": "rules", "Hari Om Foods": "rules", "Ganga Prints": "ai"}
    stored = [a async for a in db().approvals.find({"ws": uid})] + [p async for p in db().prospects.find({"ws": uid})]
    assert UNIQUE not in str(stored)

    # when no AI draft at all can be used, the token is given back and the rules version is written
    csv = "name,phone,kind\nDurga Snacks,98111 40013,Food brand\n"
    assert (await c.post("/api/prospects/upload", files={"file": ("l.csv", csv.encode(), "text/csv")})).json()["added"] == 1
    durga = next(i for i in (await c.get("/api/prospects/list")).json()["items"] if i["name"] == "Durga Snacks")

    async def claims_history(system, prompt, mock, max_tokens=1200):
        return {"drafts": [{"ref": "P1", "whatsapp": "As discussed on our last call, Om Sai Packaging will send the boxes.",
                            "email_subject": "As discussed", "email_body": "As discussed, Om Sai Packaging boxes are ready for you. " * 2,
                            "call_opener": "Hello, this is Om Sai Packaging, as discussed last time."}]}, Usage()
    monkeypatch.setattr(prospect_tasks, "complete", claims_history)
    before = await runs_used(uid)
    out = await draft(c, [durga["id"]])
    assert out["drafted"] == 1 and not out["token_used"] and await runs_used(uid) == before and "No token was used" in out["note"]
    assert "As discussed" not in out["items"][0]["messages"][0]["text"]


# ───────────────────────── sending ─────────────────────────
async def test_outreach_is_never_sent_by_the_hub_and_the_send_list_looks_the_phone_up_live(client, places):
    c, uid = await member("9800410008")
    kept ={i["name"]: i for i in await search_and_keep(c, ["Kesar Spice Works", "Silent Looms"])}
    await draft(c, [kept["Kesar Spice Works"]["id"], kept["Silent Looms"]["id"]])
    a = await db().approvals.find_one({"prospect_id": kept["Kesar Spice Works"]["id"]})
    assert a["to_phone"] == "" and a["to_name"] == "" and "Kesar" not in a["title"]
    # not from the hub, by any route
    r = await c.post(f"/api/approvals/{a['_id']}/send")
    assert r.status_code == 400 and "own WhatsApp" in r.json()["detail"]
    assert (await appr.deliver(a))["sent"] is False
    assert (await db().approvals.find_one({"_id": a["_id"]}))["status"] == "pending"
    # the Send list shows it waiting; "Send on WhatsApp" opens the owner's WhatsApp with the number looked up now
    waiting = (await c.get("/api/approvals?status=pending")).json()
    assert {x["kind"] for x in waiting} == {"outreach"} and all(x["to_phone"] == "" for x in waiting)
    before = len(places.details_calls())
    r = await c.post(f"/api/approvals/{a['_id']}/own")
    assert r.status_code == 200, r.text
    assert r.json()["link"].startswith("https://wa.me/919811120001?text=") and len(places.details_calls()) == before + 1
    saved = await db().approvals.find_one({"_id": a["_id"]})
    assert (saved["status"], saved["via"], saved["to_phone"]) == ("sent", "own", "")
    p = await db().prospects.find_one({"_id": kept["Kesar Spice Works"]["id"]})
    assert p["status"] == "contacted" and p["contacted_at"]
    # a business with no phone on Google Maps: a clear message, nothing marked sent
    b = await db().approvals.find_one({"prospect_id": kept["Silent Looms"]["id"]})
    r = await c.post(f"/api/approvals/{b['_id']}/own")
    assert r.status_code == 400 and "no mobile number" in r.json()["detail"]
    assert (await db().approvals.find_one({"_id": b["_id"]}))["status"] == "pending"
    r = await c.post(f"/api/approvals/{b['_id']}/own")
    assert r.status_code == 400                                          # the claim was given back: it can be tried again
    assert (await c.get("/api/prospects")).json()["limits"]["first_messages"]["used"] == 1
    assert await scan_for_google(uid) == []


async def test_the_21st_first_message_in_a_day_is_refused_with_the_reason(client, places):
    c, uid = await member("9800410009")
    kept = {i["name"]: i for i in await search_and_keep(c, ["Kesar Spice Works", "Neelkanth Garments"])}
    await draft(c, [i["id"] for i in kept.values()])
    user = await db().users.find_one({"_id": uid})
    await growth_core.take(user, "outreach_per_day", 19)
    r = await c.post(f"/api/prospects/{kept['Kesar Spice Works']['id']}/whatsapp", json={"text": "Hello from Om Sai Packaging, edited by me. "
                                                                                             'If not useful, reply "no".'})
    assert r.status_code == 200, r.text
    assert "edited%20by%20me" in r.json()["link"] and r.json()["prospect"]["status"] == "contacted"
    r = await c.post(f"/api/prospects/{kept['Neelkanth Garments']['id']}/whatsapp", json={})
    assert r.status_code == 429
    assert "20 first messages" in r.json()["detail"] and "blocked" in r.json()["detail"]
    a = await db().approvals.find_one({"prospect_id": kept["Neelkanth Garments"]["id"]})
    assert a["status"] == "pending"
    assert (await db().prospects.find_one({"_id": kept["Neelkanth Garments"]["id"]}))["status"] == "drafted"


# ───────────────────────── follow-ups ─────────────────────────
def ten_am(days_ahead: int) -> datetime:
    d = pr.ist_day() + timedelta(days=days_ahead)
    return datetime(d.year, d.month, d.day, 10, 5, tzinfo=IST)


async def _send_first(c, pid: str) -> None:
    r = await c.post(f"/api/prospects/{pid}/whatsapp", json={})
    assert r.status_code == 200, r.text


async def test_follow_ups_on_day_3_and_7_stop_on_reply_and_do_not_contact(client, places):
    c, uid = await member("9800410010")
    kept = {i["name"]: i for i in await search_and_keep(c, ["Kesar Spice Works", "Neelkanth Garments", "Lotus Snacks Co", "Faraway Foods"])}
    ids = {n: i["id"] for n, i in kept.items()}
    await draft(c, list(ids.values()))
    for pid in ids.values():
        await _send_first(c, pid)
    assert (await c.post(f"/api/prospects/{ids['Neelkanth Garments']}/status", json={"status": "replied"})).status_code == 200
    # day 2: nothing yet
    await scheduler.tick(at=ten_am(2))
    assert await db().approvals.count_documents({"ws": uid, "step": {"$in": ["day3", "day7"]}}) == 0
    # day 3: one follow-up for each business that hasn't replied (rules text, no token), and only once
    before = await runs_used(uid)
    await scheduler.tick(at=ten_am(3))
    await scheduler.tick(at=ten_am(3) + timedelta(hours=1))
    day3 = {a["prospect_id"]: a async for a in db().approvals.find({"ws": uid, "step": "day3"})}
    assert set(day3) == {ids["Kesar Spice Works"], ids["Lotus Snacks Co"], ids["Faraway Foods"]} and await runs_used(uid) == before
    t = day3[ids["Kesar Spice Works"]]["text"]
    assert "Om Sai Packaging" in t and '"no"' in t and day3[ids["Kesar Spice Works"]]["to_phone"] == ""
    # Do not contact: its waiting follow-up is cleared; Not interested too
    assert (await c.post(f"/api/prospects/{ids['Lotus Snacks Co']}/status", json={"status": "dnc"})).status_code == 200
    assert (await db().approvals.find_one({"_id": day3[ids["Lotus Snacks Co"]]["_id"]}))["status"] == "expired"
    assert (await c.post(f"/api/prospects/{ids['Faraway Foods']}/status", json={"status": "not_interested"})).status_code == 200
    assert (await db().approvals.find_one({"_id": day3[ids["Faraway Foods"]]["_id"]}))["status"] == "expired"
    # the day-3 follow-up sent (from the prospect's page) → a day-7 one on day 7
    r = await c.post(f"/api/prospects/{ids['Kesar Spice Works']}/whatsapp", json={"step": "day3"})
    assert r.status_code == 200 and r.json()["prospect"]["followups_sent"] == 1
    await scheduler.tick(at=ten_am(7))
    day7 = [a async for a in db().approvals.find({"ws": uid, "step": "day7"})]
    assert [a["prospect_id"] for a in day7] == [ids["Kesar Spice Works"]]
    log = [x["text"] async for x in db().agent_log.find({"ws": uid, "agent": "outreach_writer"}).sort("at", 1)]
    assert any("day 7 follow-up" in t for t in log) and await scan_for_google(uid) == []
    # a reply stops everything that was waiting
    r = await c.post(f"/api/prospects/{ids['Kesar Spice Works']}/reply", json={"text": "Send me your rates for 500 boxes"})
    assert r.status_code == 200 and r.json()["status"] == "replied"
    assert (await db().approvals.find_one({"_id": day7[0]["_id"]}))["status"] == "expired"
    await scheduler.tick(at=ten_am(11))
    assert await db().approvals.count_documents({"ws": uid, "kind": "outreach", "status": "pending"}) == 0


async def test_follow_ups_wait_when_the_outreach_writer_is_switched_off(client, places):
    c, uid = await member("9800410011")
    kesar = (await search_and_keep(c, ["Kesar Spice Works"]))[0]
    await draft(c, [kesar["id"]])
    await _send_first(c, kesar["id"])
    assert (await c.patch("/api/agents/outreach_writer", json={"on": False})).status_code == 200
    await scheduler.tick(at=ten_am(3))
    assert await db().approvals.count_documents({"ws": uid, "step": "day3"}) == 0
    assert (await c.patch("/api/agents/outreach_writer", json={"on": True})).status_code == 200
    await scheduler.tick(at=ten_am(4))
    assert await db().approvals.count_documents({"ws": uid, "step": "day3"}) == 1


async def test_do_not_contact_is_permanent(client, places):
    c, uid = await member("9800410012")
    kesar = (await search_and_keep(c, ["Kesar Spice Works"]))[0]
    await draft(c, [kesar["id"]])
    r = await c.post(f"/api/prospects/{kesar['id']}/status", json={"status": "dnc"})
    assert r.status_code == 200 and r.json()["status"] == "dnc"
    a = await db().approvals.find_one({"prospect_id": kesar["id"]})
    assert a["status"] == "expired"
    for st in ("replied", "not_interested"):
        assert (await c.post(f"/api/prospects/{kesar['id']}/status", json={"status": st})).status_code == 409
    assert (await c.post("/api/prospects/draft", json={"ids": [kesar["id"]]})).status_code == 400
    assert (await c.post(f"/api/prospects/{kesar['id']}/email-sent")).status_code == 400
    assert (await c.post(f"/api/prospects/{kesar['id']}/deal", json={"name": "Kesar"})).status_code == 400
    # kept by place ID and a one-way hash of the number — never Google's number itself
    b = await db().prospect_block.find_one({"ws": uid})
    assert b["place_id"] == KESAR["id"] and b["phone"] is None and b["phone_hash"] and "20001" not in str(b)
    # the next search leaves it out; the same number on the owner's own list is refused too
    res = (await c.post("/api/prospects/search", json={})).json()
    assert "Kesar Spice Works" not in {x["name"] for x in res["results"]} and res["skipped"]["blocked"] == 1
    csv = "name,phone\nKesar Masala (from expo),+91 98111 20001\n"
    out = (await c.post("/api/prospects/upload", files={"file": ("l.csv", csv.encode(), "text/csv")})).json()
    assert out["added"] == 0 and out["skipped"]["blocked"] == 1
    # even an old waiting message can't be sent
    await db().approvals.update_one({"_id": a["_id"]}, {"$set": {"status": "pending"}})
    r = await c.post(f"/api/approvals/{a['_id']}/own")
    assert r.status_code == 400 and "Do not contact" in r.json()["detail"]
    assert await scan_for_google(uid) == []


# ───────────────────────── the Reply helper → a deal ─────────────────────────
async def test_reply_helper_suggests_an_answer_and_makes_a_deal_through_the_new_lead_path(client, places, monkeypatch):
    c, uid = await member("9800410013")
    kesar = (await search_and_keep(c, ["Kesar Spice Works"]))[0]
    await draft(c, [kesar["id"]])
    await _send_first(c, kesar["id"])
    before = await runs_used(uid)
    r = await c.post(f"/api/prospects/{kesar['id']}/reply", json={"text": "Yes, we need 2000 cartons a month. Call me tomorrow."})
    assert r.status_code == 200, r.text
    page = r.json()
    assert page["status"] == "replied" and page["reply_help"]["answer"] and page["token_used"] and await runs_used(uid) - before == 1
    assert page["phone"] and page["name"] == "Kesar Spice Works" and page["attribution"] == "Google Maps"
    seen = []
    real = hooks.on_new_lead

    async def spy(owner, business, site, lead):
        seen.append(lead["_id"])
        return await real(owner, business, site, lead)
    monkeypatch.setattr(hooks, "on_new_lead", spy)
    r = await c.post(f"/api/prospects/{kesar['id']}/deal", json={"name": page["name"], "phone": page["phone"], "value": 85000})
    assert r.status_code == 200, r.text
    lead = await db().leads.find_one({"_id": r.json()["lead_id"]})
    assert (lead["source"], lead["prospect_id"], lead["value"], lead["status"]) == ("prospecting", kesar["id"], 85000, "new")
    stages = (await c.get("/api/crm/pipeline")).json()["stages"]
    assert lead["pstage"] == stages[0]["id"] and lead["stage"] == stages[0]["ff"]
    assert seen == [lead["_id"]] and "2000 cartons" in lead["message"]
    contact = await db().contacts.find_one({"_id": lead["contact_id"]})
    assert contact and contact["phone"] == "+919811120001" and contact["source"] == "prospecting"
    p = await db().prospects.find_one({"_id": kesar["id"]})
    assert (p["status"], p["lead_id"]) == ("deal", lead["_id"])
    assert (await c.post(f"/api/prospects/{kesar['id']}/deal", json={"name": "Again", "phone": "98111 20001"})).status_code == 409
    deals = (await c.get("/api/crm/deals")).json()
    assert lead["_id"] in str(deals)


async def test_reply_helper_rules_version_when_the_ai_answer_cant_be_used(client, places, monkeypatch):
    c, uid = await member("9800410014")
    kesar = (await search_and_keep(c, ["Kesar Spice Works"]))[0]

    async def pricey(system, prompt, mock, max_tokens=1200):
        return {"answer": "Our boxes are ₹12 each and we have 30 years of experience.", "said_no": False}, Usage()
    monkeypatch.setattr(prospect_tasks, "complete", pricey)
    before = await runs_used(uid)
    r = await c.post(f"/api/prospects/{kesar['id']}/reply", json={"text": "Not interested, please don't message again"})
    assert r.status_code == 200
    page = r.json()
    assert not page["token_used"] and await runs_used(uid) == before
    assert page["reply_help"]["said_no"] and "₹12" not in page["reply_help"]["answer"] and "won't message again" in page["reply_help"]["answer"]


# ───────────────────────── More like my best customers ─────────────────────────
async def test_more_like_my_best_customers_fills_who_to_look_for(client):
    c, uid = await member("9800410015")
    at = now()
    rows = [("Shree Exports", 900000), ("Patel Foods", 700000), ("Cash", 2000000), ("Moti Garments", 500000), ("Lakshmi Snacks", 300000),
            ("Ganesh Apparel", 200000), ("Small Buyer", 10000)]
    for name, amount in rows:
        await db().sales_rows.insert_one({"_id": f"s-{name}", "ws": uid, "batch_id": "b1", "date": "2026-10-01", "customer": name, "phone": "",
                                          "amount": amount * 100, "gst": None, "invoice_no": "", "due_date": "", "paid_on": "", "created_at": at})
    best = (await c.get("/api/prospects/best-customers")).json()
    assert [x["name"] for x in best["customers"]] == ["Shree Exports", "Patel Foods", "Moti Garments", "Lakshmi Snacks", "Ganesh Apparel"]
    assert best["missing"] == 5
    r = await c.post("/api/prospects/best-customers/fill", json={})
    assert r.status_code == 400 and "what kind of business" in r.json()["detail"]
    answers = {"Shree Exports": "garment exporters", "Patel Foods": "food brands", "Moti Garments": "Garment exporters",
               "Lakshmi Snacks": "snack makers", "Ganesh Apparel": "apparel brands"}
    r = await c.put("/api/prospects/best-customers", json={"answers": [{"key": x["key"], "kind": answers[x["name"]]} for x in best["customers"]]})
    assert r.status_code == 200 and r.json()["missing"] == 0
    before = await runs_used(uid)
    r = await c.post("/api/prospects/best-customers/fill", json={})
    assert r.status_code == 200, r.text
    assert r.json()["kinds"] == ["Garment exporters", "Food brands", "Snack makers", "Apparel brands"] and await runs_used(uid) == before
    assert r.json()["kinds_by"] == "best_customers"
    # asked once: the answers are kept for next time
    assert (await c.get("/api/prospects/best-customers")).json()["missing"] == 0
    r = await c.post("/api/prospects/best-customers/fill", json={"use_ai": True})
    assert r.status_code == 200 and r.json()["token_used"] and await runs_used(uid) - before == 1


async def test_suggest_with_ai_fills_kinds_from_the_ideal_customer_for_one_token(client):
    c, uid = await member("9800410016")
    before = await runs_used(uid)
    r = await c.post("/api/prospects/kinds/suggest")
    assert r.status_code == 200, r.text
    kinds = r.json()["kinds"]
    assert 1 <= len(kinds) <= 6 and r.json()["kinds_by"] == "ai" and await runs_used(uid) - before == 1
    assert not any("Bhiwandi" in k for k in kinds)
    assert (await c.put("/api/prospects/kinds", json={"kinds": ["  hotel kitchens  "]})).json()["kinds"] == ["hotel kitchens"]


# ───────────────────────── who may use it ─────────────────────────
async def test_a_free_or_unpaid_owner_gets_the_locked_page(client, places):
    f, _ = await member("9800410017", paid=False)
    for method, path, body in (("get", "/api/prospects", None), ("post", "/api/prospects/search", {}), ("get", "/api/prospects/list", None),
                               ("post", "/api/prospects/draft", {"ids": ["x"]})):
        r = await (f.get(path) if method == "get" else f.post(path, json=body))
        assert r.status_code == 403, path
        assert r.json()["detail"]["code"] == "locked"
