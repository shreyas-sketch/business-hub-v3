"""Reviews & reputation (v2.2): pasted and Google reviews, reply drafts the owner approves and posts themselves, referral
asks for happy reviewers matched exactly to one customer, honest review requests, and the daily Google review watch.
Every name, number, link and Google listing here is invented; Google is a fake server behind reputation.http_client."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.agents import scheduler
from app.ai import reputation_tasks as rt
from app.ai.provider import Usage
from app.config import settings
from app.db import IST, db, month_key, now
from app.routers import reputation
from app.services import new_id
from tests.conftest import PROFILE, new_owner

ADMIN = "9999900000"
REAL_CLIENT = httpx.AsyncClient
KEY = "test-places-key"
PLACE = "ChIJTestPlace0001abcdef"
B2B = {**PROFILE, "name": "Precision Fasteners", "industry": "Manufacturing of industrial fasteners and bolts", "ideal_customer": "OEM plants"}


def ist(*a) -> datetime:
    return datetime(*a, tzinfo=IST)


def day(offset: int) -> str:
    return (now().astimezone(IST) + timedelta(days=offset)).strftime("%Y-%m-%d")


async def owner_on(phone: str, plan: str, profile: dict | None = None):
    c = await new_owner(phone)
    assert (await c.put("/api/business", json=profile or PROFILE)).status_code == 200
    uid = (await c.get("/api/me")).json()["user"]["id"]
    if plan != "free":
        admin = await new_owner(ADMIN)
        assert (await admin.patch(f"/api/admin/users/{uid}", json={"plan": plan})).status_code == 200
    return c, uid


async def runs_used(uid: str) -> int:
    return ((await db().usage.find_one({"user_id": uid, "month": month_key()})) or {}).get("used", 0)


async def reply_runs(uid: str) -> int:
    return await db().runs.count_documents({"user_id": uid, "kind": "reputation_reply", "status": "done"})


async def paste(c, **kw):
    body = {"platform": "Google", "author": "Meera Kulkarni", "rating": 5, "text": "Lovely kitchen, finished neatly.", **kw}
    r = await c.post("/api/reputation/reviews", json=body)
    assert r.status_code == 200, r.text
    return r.json()


class FakeGoogle:
    """Places API (New): places:searchText and Place Details, as Google shapes them."""

    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.rating, self.count = 4.3, 27
        self.reviews: list[dict] = []
        self.fail = False

    @staticmethod
    def review(n: int, rating: int, text: str, author: str, days_ago: int = 1, at: datetime | None = None) -> dict:
        """`at`: when it was published (default `days_ago` before now)."""
        published = at or now() - timedelta(days=days_ago)
        when = published.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.123456789Z")      # UTC, as Google writes it
        return {"name": f"places/{PLACE}/reviews/R{n:04d}", "relativePublishTimeDescription": "a day ago", "rating": rating,
                "text": {"text": text, "languageCode": "en"}, "originalText": {"text": text, "languageCode": "en"},
                "authorAttribution": {"displayName": author, "uri": f"https://www.google.com/maps/contrib/{n}", "photoUri": ""},
                "publishTime": when, "googleMapsUri": f"https://www.google.com/maps/reviews/data=R{n:04d}"}

    def handle(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        if self.fail:
            return httpx.Response(500, json={"error": {"code": 500, "message": "Internal error"}})
        if req.headers.get("X-Goog-Api-Key") != KEY:
            return httpx.Response(403, json={"error": {"code": 403, "status": "PERMISSION_DENIED"}})
        if req.method == "POST" and req.url.path == "/v1/places:searchText":
            return httpx.Response(200, json={"places": [
                {"id": PLACE, "displayName": {"text": "Shree Ganesh Interiors", "languageCode": "en"}, "formattedAddress": "Shop 4, Lake Road, Thane West",
                 "rating": 4.3, "userRatingCount": 27},
                {"id": "not a place id!", "displayName": {"text": "Broken"}}]})
        if req.method == "GET" and req.url.path == f"/v1/places/{PLACE}":
            return httpx.Response(200, json={"rating": self.rating, "userRatingCount": self.count, "reviews": self.reviews})
        return httpx.Response(404, json={"error": {"code": 404, "status": "NOT_FOUND"}})

    def client(self):
        return REAL_CLIENT(transport=httpx.MockTransport(self.handle), timeout=5)

    def last(self, method: str) -> httpx.Request:
        return [r for r in self.calls if r.method == method][-1]


@pytest.fixture
def google(monkeypatch):
    fake = FakeGoogle()
    old = (settings.google_places_api_key, settings.google_places_api_base)
    object.__setattr__(settings, "google_places_api_key", KEY)
    object.__setattr__(settings, "google_places_api_base", "https://places.test/v1")
    monkeypatch.setattr(reputation, "http_client", fake.client)
    try:
        yield fake
    finally:
        object.__setattr__(settings, "google_places_api_key", old[0])
        object.__setattr__(settings, "google_places_api_base", old[1])


# ───────────────────────── paste → draft → edit → approve → posted ─────────────────────────
async def test_paste_draft_edit_approve_and_replied(client):
    c, uid = await owner_on("9871100001", "lite")
    used = await runs_used(uid)
    out = await paste(c, platform="Justdial", date=day(-2))
    rv = out["review"]
    assert out["draft_error"] is None
    assert rv["reply_status"] == "drafted" and rv["draft_by"] == "ai" and rv["source"] == "pasted" and rv["platform"] == "Justdial"
    assert rv["reply_draft"].startswith("Hello Meera,") and "Shree Ganesh Interiors" in rv["reply_draft"]
    assert rv["review_date"] == day(-2) and rv["open_link"] == ""          # not a Google review: no Google button
    assert await runs_used(uid) == used + 1 and await reply_runs(uid) == 1  # one AI run for the draft

    rid = rv["id"]
    assert (await c.post(f"/api/reputation/reviews/{rid}/replied")).status_code == 409   # approve first
    mine = "Thank you Meera! It was a pleasure doing your kitchen. — Shree Ganesh Interiors"
    r = (await c.patch(f"/api/reputation/reviews/{rid}", json={"reply_draft": mine})).json()
    assert r["reply_draft"] == mine and r["draft_by"] == "owner" and r["reply_status"] == "drafted"
    r = (await c.post(f"/api/reputation/reviews/{rid}/approve")).json()
    assert r["reply_status"] == "approved" and r["approved_at"]
    # saving the same words keeps the approval; changing them asks for approval again
    assert (await c.patch(f"/api/reputation/reviews/{rid}", json={"reply_draft": mine})).json()["reply_status"] == "approved"
    r = (await c.patch(f"/api/reputation/reviews/{rid}", json={"reply_draft": mine + " See you again!"})).json()
    assert r["reply_status"] == "drafted" and r["approved_at"] is None
    assert (await c.post(f"/api/reputation/reviews/{rid}/approve")).json()["reply_status"] == "approved"
    page = (await c.get("/api/reputation")).json()
    assert page["counts"] == {"new": 0, "drafted": 0, "approved": 1, "replied": 0}
    r = (await c.post(f"/api/reputation/reviews/{rid}/replied")).json()
    assert r["reply_status"] == "replied" and r["replied_at"]
    assert (await c.post(f"/api/reputation/reviews/{rid}/replied")).status_code == 200      # a double tap is harmless
    assert (await c.patch(f"/api/reputation/reviews/{rid}", json={"reply_draft": "x"})).status_code == 409
    assert (await c.post(f"/api/reputation/reviews/{rid}/draft")).status_code == 409
    assert await reply_runs(uid) == 1

    # "don't draft it" → no run; a redraft later is one run
    quiet = (await paste(c, author="Kiran Shah", rating=4, text="Good work", draft=False))["review"]
    assert quiet["reply_status"] == "none" and quiet["reply_draft"] == "" and await reply_runs(uid) == 1
    again = await c.post(f"/api/reputation/reviews/{quiet['id']}/draft")
    assert again.status_code == 200 and again.json()["reply_status"] == "drafted" and await reply_runs(uid) == 2
    # clearing the reply puts it back to "to reply"
    assert (await c.patch(f"/api/reputation/reviews/{quiet['id']}", json={"reply_draft": "  "})).json()["reply_status"] == "none"
    assert (await c.post(f"/api/reputation/reviews/{quiet['id']}/approve")).status_code == 400

    page = (await c.get("/api/reputation")).json()
    assert page["counts"] == {"new": 1, "drafted": 0, "approved": 0, "replied": 1} and len(page["reviews"]) == 2

    # bad input
    assert (await c.post("/api/reputation/reviews", json={"platform": "Google", "author": "X", "text": ""})).status_code == 400
    assert (await c.post("/api/reputation/reviews", json={"platform": "Yelp", "text": "ok", "rating": 5})).status_code == 422
    assert (await c.post("/api/reputation/reviews", json={"text": "ok", "rating": 6})).status_code == 422
    assert (await c.post("/api/reputation/reviews", json={"text": "ok", "rating": 5, "date": day(5)})).status_code == 422
    assert (await c.post("/api/reputation/reviews", json={"platform": "indiamart", "text": "ok", "draft": False})).json()["review"]["platform"] == "IndiaMART"

    assert (await c.delete(f"/api/reputation/reviews/{quiet['id']}")).status_code == 200
    assert (await c.post(f"/api/reputation/reviews/{quiet['id']}/draft")).status_code == 404
    assert await db().reviews.count_documents({"_id": quiet["id"]}) == 0


async def test_paste_keeps_the_review_when_no_runs_are_left(client):
    c, uid = await owner_on("9871100002", "lite")
    await db().usage.update_one({"user_id": uid, "month": month_key()}, {"$set": {"used": 10_000}}, upsert=True)
    out = await paste(c)
    assert out["review"]["reply_status"] == "none" and "AI runs" in out["draft_error"]
    assert await db().reviews.count_documents({"ws": uid}) == 1


# ───────────────────────── the reply's rules ─────────────────────────
async def test_low_rating_reply_rules_in_the_mock_path(client):
    c, uid = await owner_on("9871100003", "lite")
    angry = "Work finished 2 weeks late and the site supervisor was rude. I want my money back."
    rv = (await paste(c, author="Vikram Desai", rating=1, text=angry))["review"]
    reply = rv["reply_draft"]
    assert reply.startswith("Hello Vikram,")
    assert "sorry" in reply.lower()
    assert "98200 00000" in reply and "hello@sgi.in" in reply           # taken offline with the business's own contact
    assert not rt.PROMISE.search(reply) and not rt.FAULT.search(reply)  # no refund or discount, no admission of fault
    assert "refund" not in reply.lower() and "money back" not in reply.lower()
    assert len(reply.split()) <= rt.MAX_WORDS
    assert "supervisor" not in reply.lower() and "2 weeks" not in reply   # no facts taken over as if admitted

    happy = (await paste(c, author="Mrs. Anjali Iyer", rating=5, text="Excellent wardrobe work."))["review"]["reply_draft"]
    assert happy.startswith("Hello Mrs. Anjali,") and "thank you" in happy.lower() and "back" in happy.lower()
    nameless = (await paste(c, author="A Google User", rating=3, text="Okay."))["review"]["reply_draft"]
    assert nameless.startswith("Hello,") and "sorry" in nameless.lower()

    # what a real engine might write is checked: each of these is thrown away
    p = {**PROFILE}
    review = {"author": "Vikram Desai", "rating": 1, "text": angry}
    for bad in ("Hello Vikram, sorry! We will refund you in full. — Shree Ganesh Interiors",
                "Hello Vikram, we'll give you a 20% discount next time. — Shree Ganesh Interiors",
                "Hello Vikram, this delay was our fault and we take full responsibility. — Shree Ganesh Interiors",
                "Hello Vikram, sorry. See https://sgi.example.in/complaints — Shree Ganesh Interiors",
                "Hello Vikram, sorry. Our manager Suresh will visit you on 14 Nov. — Shree Ganesh Interiors",
                "Hello Vikram, sorry. We will fix it free of cost. — Shree Ganesh Interiors",
                "Hello Vikram, " + "sorry " * 90,
                ""):
        with pytest.raises(ValueError):
            rt.check_reply({"reply": bad}, p, review)
    ok = "Hello Vikram, thank you for telling us. We're sorry it fell short — please call us on 98200 00000. — Shree Ganesh Interiors"
    assert rt.check_reply({"reply": ok}, p, review) == ok

    # the reviewer's own numbers never come back in the business's voice; money going back is a promise too
    paid = {"author": "Vikram Desai", "rating": 1, "text": "I paid 2500 advance. Call me on 91234 56789 or I'll post more."}
    for bad in ("Hello Vikram, sorry. We will return the 2500 — please call 91234 56789. — Shree Ganesh Interiors",
                "Hello Vikram, sorry. We will return the 2500. — Shree Ganesh Interiors",
                "Hello Vikram, sorry. We will call you on 91234 56789. — Shree Ganesh Interiors",
                "Hello Vikram, sorry. Please call our manager on +91 99887 76655. — Shree Ganesh Interiors",
                "Hello Vikram, sorry about your advance of 2500. — Shree Ganesh Interiors",
                "Hello Vikram, sorry. We will return your money. — Shree Ganesh Interiors",
                "Hello Vikram, sorry. We will give you your advance back. — Shree Ganesh Interiors",
                "Hello Vikram, sorry. We'll pay you back this week. — Shree Ganesh Interiors",
                "Hello Vikram, sorry. We will repay the advance. — Shree Ganesh Interiors",
                "Hello Vikram, sorry. The amount will be credited to you. — Shree Ganesh Interiors"):
        with pytest.raises(ValueError):
            rt.check_reply({"reply": bad}, p, paid)
    # …while ordinary warm words still pass: the star rating, the business's own number (any way it's written), "come back"
    for fine in ("Hello Vikram, thank you for the 1 star feedback. Please call us on +91 98200 00000. — Shree Ganesh Interiors",
                 "Hello Vikram, sorry it fell short. We will get back to you if you call 9820000000. — Shree Ganesh Interiors",
                 "Hello Asha, thank you! We hope you return and we look forward to welcoming you back. — Shree Ganesh Interiors"):
        assert rt.check_reply({"reply": fine}, p, paid) == fine


async def test_an_unsafe_ai_draft_is_refused_and_the_run_refunded(client, monkeypatch):
    c, uid = await owner_on("9871100004", "lite")

    async def promises_refund(p, review):
        return {"reply": "Hello Vikram, so sorry — we will refund your money in full. — Shree Ganesh Interiors"}, Usage()
    monkeypatch.setattr(rt, "reply", promises_refund)
    used = await runs_used(uid)
    out = await paste(c, author="Vikram Desai", rating=1, text="Bad experience.")
    assert out["review"]["reply_status"] == "none" and out["review"]["reply_draft"] == ""
    assert "run was not used" in out["draft_error"]
    assert await runs_used(uid) == used                                   # refunded
    r = await c.post(f"/api/reputation/reviews/{out['review']['id']}/draft")
    assert r.status_code == 502 and await runs_used(uid) == used


# ───────────────────────── Google: search, connect, the daily watch ─────────────────────────
async def test_google_search_settings_and_the_daily_watch(client, google):
    c, uid = await owner_on("9871100005", "lite")
    page = (await c.get("/api/reputation")).json()
    assert page["google"]["available"] is True and page["google"]["place_id"] is None and page["review_link"] is None

    found = await c.get("/api/reputation/google-search", params={"q": "Shree Ganesh Interiors Thane"})
    assert found.status_code == 200, found.text
    assert found.json()["places"] == [{"place_id": PLACE, "name": "Shree Ganesh Interiors", "address": "Shop 4, Lake Road, Thane West",
                                       "rating": 4.3, "count": 27}]          # the malformed id is dropped
    req = google.last("POST")
    assert req.headers["X-Goog-FieldMask"] == "places.id,places.displayName,places.formattedAddress,places.rating,places.userRatingCount"
    assert json.loads(req.content)["textQuery"] == "Shree Ganesh Interiors Thane"
    assert (await c.get("/api/reputation/google-search", params={"q": "x"})).status_code == 400
    assert (await c.put("/api/reputation/settings", json={"google_place_id": "not a place id!"})).status_code == 400

    # connecting: a first look at once — two existing reviews come in, no AI run, no alert
    google.reviews = [google.review(1, 5, "Superb work on our 2BHK.", "Rohan Mehta", 30), google.review(2, 4, "Good finish", "Farah Khan", 20)]
    used = await runs_used(uid)
    r = await c.put("/api/reputation/settings", json={"google_place_id": PLACE, "google_name": "Shree Ganesh Interiors",
                                                     "google_address": "Shop 4, Lake Road, Thane West"})
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["note"] == "Connected. 2 reviews came in from Google."
    assert s["google"]["place_id"] == PLACE and s["google"]["rating"] == 4.3 and s["google"]["count"] == 27
    assert s["review_link"] == f"https://search.google.com/local/writereview?placeid={PLACE}" and s["review_link_from"] == "google"
    assert google.last("GET").url.path == f"/v1/places/{PLACE}" and google.last("GET").headers["X-Goog-FieldMask"] == "rating,userRatingCount,reviews"
    assert {x["author"] for x in s["reviews"]} == {"Rohan Mehta", "Farah Khan"} and all(x["reply_status"] == "none" for x in s["reviews"])
    assert all(x["source"] == "google" and x["open_link"].startswith("https://www.google.com/maps/reviews/") for x in s["reviews"])
    assert await runs_used(uid) == used and await db().notices.count_documents({"user_id": uid, "text": {"$regex": "Google review"}}) == 0

    # day 1 (a Thursday, 9:35am India time): six new reviews → all six saved, five drafted, one alert
    t1 = ist(2026, 11, 12, 9, 35)
    google.reviews += [google.review(10 + i, 5 if i % 2 else 2, f"Review number {i}", f"Customer {chr(65 + i)} Test", at=t1 - timedelta(hours=3))
                       for i in range(6)]
    google.rating, google.count = 4.2, 33
    await scheduler.tick(at=t1)
    assert await db().reviews.count_documents({"ws": uid}) == 8
    assert await db().reviews.count_documents({"ws": uid, "auto_day": "2026-11-12", "reply_status": "drafted"}) == 5
    assert await reply_runs(uid) == 5
    snap = await db().reputation_snapshots.find_one({"ws": uid, "day": "2026-11-12"})
    assert snap["rating"] == 4.2 and snap["count"] == 33
    alerts = [n["text"] async for n in db().notices.find({"user_id": uid, "text": {"$regex": "Google review"}})]
    assert alerts == ["Reviews: 6 new Google reviews, 3 need your attention. Replies are drafted for 5 of them — approve them on Reviews & reputation."]
    log = await db().agent_log.find_one({"ws": uid, "agent": "reputation_watch"})
    assert log["status"] == "done"

    await scheduler.tick(at=t1 + timedelta(minutes=30))   # the same morning: already done
    assert await db().agent_log.count_documents({"ws": uid, "agent": "reputation_watch"}) == 1

    # day 2: nothing new → no duplicates, no alert, but today's rating is kept (in the snapshot, not in the log)
    await scheduler.tick(at=ist(2026, 11, 13, 9, 35))
    assert await db().reviews.count_documents({"ws": uid}) == 8 and await reply_runs(uid) == 5
    assert len([n async for n in db().notices.find({"user_id": uid, "text": {"$regex": "Google review"}})]) == 1
    last = await db().agent_log.find_one({"ws": uid, "agent": "reputation_watch"}, sort=[("at", -1)])
    assert last["status"] == "skipped" and last["text"] == "No new Google reviews."
    assert await db().reputation_snapshots.count_documents({"ws": uid}) == 3

    # day 3: a Google review the owner deleted stays gone; one new low rating is drafted and alerted; a review from last year
    # that has just entered Google's "most relevant" five is saved quietly — no alert, no draft
    rohan = await db().reviews.find_one({"ws": uid, "author": "Rohan Mehta"})
    assert (await c.delete(f"/api/reputation/reviews/{rohan['_id']}")).status_code == 200
    google.reviews.append(google.review(99, 1, "Nobody came for the measurement.", "Sunil Varma", at=ist(2026, 11, 14, 8, 0)))
    google.reviews.append(google.review(98, 5, "Great work, years ago.", "Old Favourite Reviewer", at=ist(2025, 3, 1, 10, 0)))
    await scheduler.tick(at=ist(2026, 11, 14, 9, 35))
    assert await db().reviews.count_documents({"ws": uid}) == 10
    sunil = await db().reviews.find_one({"ws": uid, "author": "Sunil Varma"})
    assert sunil["reply_status"] == "drafted" and "sorry" in sunil["reply_draft"].lower()
    old = await db().reviews.find_one({"ws": uid, "author": "Old Favourite Reviewer"})
    assert old["reply_status"] == "none" and not old.get("auto_day") and old["review_date"] == "2025-03-01"
    assert await reply_runs(uid) == 6
    alerts = [n["text"] async for n in db().notices.find({"user_id": uid, "text": {"$regex": "Google review"}}).sort("at", 1)]
    assert alerts[1:] == ["Reviews: 1 new Google review, 1 needs your attention. A reply is drafted — approve it on Reviews & reputation."]
    last = await db().agent_log.find_one({"ws": uid, "agent": "reputation_watch"}, sort=[("at", -1)])
    assert "1 older Google review came in without an alert" in last["text"]

    # nothing a reviewer wrote or rated is kept in notices or the activity log (they outlive Google's 30 days)
    texts = alerts + [x["text"] async for x in db().agent_log.find({"ws": uid, "agent": "reputation_watch"})]
    texts += [(await db().agent_settings.find_one({"_id": f"{uid}:reputation_watch"}))["last_note"]]
    for t in texts:
        assert "★" not in t and "Sunil" not in t and "Customer" not in t and "Old Favourite" not in t and "4.2" not in t, t

    page = (await c.get("/api/reputation")).json()
    assert [x["day"] for x in page["google"]["trend"]] == [day(0), "2026-11-12", "2026-11-13", "2026-11-14"]
    assert page["google"]["rating"] == 4.2 and page["google"]["count"] == 33
    assert "Rohan Mehta" not in {x["author"] for x in page["reviews"]} and len(page["reviews"]) == 9
    assert page["counts"]["drafted"] == 6 and page["counts"]["new"] == 3

    # auto-draft off: new reviews come in and are alerted, but no AI run
    assert (await c.put("/api/reputation/settings", json={"auto_draft": False})).json()["auto_draft"] is False
    google.reviews.append(google.review(100, 5, "Great team.", "Neha Joshi", at=ist(2026, 11, 15, 7, 0)))
    await scheduler.tick(at=ist(2026, 11, 15, 9, 35))
    neha = await db().reviews.find_one({"ws": uid, "author": "Neha Joshi"})
    assert neha["reply_status"] == "none" and await reply_runs(uid) == 6
    alerts = [n["text"] async for n in db().notices.find({"user_id": uid, "text": {"$regex": "Google review"}}).sort("at", 1)]
    assert alerts[-1] == "Reviews: 1 new Google review. Reply to it on Reviews & reputation."


async def test_a_review_counts_as_new_only_if_published_since_connecting_or_in_the_last_14_days(client, google):
    c, uid = await owner_on("9871100019", "lite")
    assert (await c.put("/api/reputation/settings", json={"google_place_id": PLACE, "google_name": "Shree Ganesh Interiors"})).status_code == 200
    connected = (await db().reputation_settings.find_one({"_id": uid}))["google_connected_at"]
    at = connected + timedelta(days=60)             # two months after connecting
    google.reviews = [
        google.review(1, 5, "Published after connecting.", "Asha Rao", at=connected + timedelta(days=20)),
        google.review(2, 5, "Published the day before connecting.", "Bina Shah", at=connected - timedelta(hours=20)),
        google.review(3, 1, "Published long before connecting.", "Chetan Iyer", at=connected - timedelta(days=200)),
    ]
    no_time = google.review(4, 2, "Google gave no publish time.", "Divya Menon")
    no_time.pop("publishTime")
    google.reviews.append(no_time)
    st = await reputation.get_settings(uid)
    got = await reputation.check_google(uid, st, at)
    assert {r["author"] for r in got["new"]} == {"Asha Rao", "Bina Shah"}
    assert {r["author"] for r in got["quiet"]} == {"Chetan Iyer", "Divya Menon"}


async def test_a_pasted_google_review_is_not_added_twice(client, google):
    c, uid = await owner_on("9871100006", "lite")
    await paste(c, author="Farah Khan", rating=4, text="Good finish", draft=False)
    google.reviews = [google.review(2, 4, "Good finish", "Farah Khan")]
    r = (await c.put("/api/reputation/settings", json={"google_place_id": PLACE, "google_name": "Shree Ganesh Interiors"})).json()
    assert r["note"] == "Connected. New Google reviews will come in every morning."
    assert await db().reviews.count_documents({"ws": uid}) == 1
    linked = await db().reviews.find_one({"ws": uid})
    assert linked["google_review_name"] == f"places/{PLACE}/reviews/R0002" and linked["google_link"].startswith("https://www.google.com/maps/")


async def test_when_google_fails(client, google):
    c, uid = await owner_on("9871100007", "lite")
    google.fail = True
    r = await c.get("/api/reputation/google-search", params={"q": "Shree Ganesh Interiors"})
    assert r.status_code == 502 and "Google" in r.json()["detail"]
    s = (await c.put("/api/reputation/settings", json={"google_place_id": PLACE, "google_name": "Shree Ganesh Interiors"})).json()
    assert s["google"]["place_id"] == PLACE and s["note"].startswith("Connected. Google didn't answer")
    await scheduler.tick(at=ist(2026, 11, 12, 9, 35))
    log = await db().agent_log.find_one({"ws": uid, "agent": "reputation_watch"})
    assert log["status"] == "failed" and "check again tomorrow" in log["text"]
    assert await db().notices.count_documents({"user_id": uid, "text": {"$regex": "Google review"}}) == 0


async def test_without_a_key_the_watch_never_runs_and_the_page_says_so(client):
    assert settings.google_places_api_key == ""
    c, uid = await owner_on("9871100008", "lite")
    # a listing saved earlier (e.g. before the key was taken out): still nothing runs
    await db().reputation_settings.insert_one({"_id": uid, "google_place_id": PLACE, "google_name": "Shree Ganesh Interiors"})
    await scheduler.tick(at=ist(2026, 11, 12, 9, 35))
    assert await db().agent_log.count_documents({"agent": "reputation_watch"}) == 0
    page = (await c.get("/api/reputation")).json()
    assert page["google"]["available"] is False and "Google key" in page["google"]["message"] and page["google"]["rating"] is None
    r = await c.get("/api/reputation/google-search", params={"q": "Shree Ganesh Interiors"})
    assert r.status_code == 400 and "paste a review" in r.json()["detail"]
    r = await c.put("/api/reputation/settings", json={"google_place_id": "ChIJAnotherPlace12345"})
    assert r.status_code == 400 and "Google key" in r.json()["detail"]
    # everything else works without Google
    s = (await c.put("/api/reputation/settings", json={"review_link": "g.page/r/test-sgi/review", "auto_draft": False})).json()
    assert s["review_link"] == "https://g.page/r/test-sgi/review" and s["review_link_from"] == "here" and s["auto_draft"] is False
    assert (await c.put("/api/reputation/settings", json={"review_link": "not a link"})).status_code == 400
    assert (await paste(c))["review"]["reply_status"] == "drafted"


# ───────────────────────── referral asks: exact name only ─────────────────────────
async def test_referral_only_for_an_exact_unique_name_match(client):
    c, uid = await owner_on("9871100009", "lite")
    d = db()
    await d.customers.insert_many([
        {"_id": new_id(), "ws": uid, "name": "Meera Kulkarni", "phone": "+919812300001"},
        {"_id": new_id(), "ws": uid, "name": "Anil Shah", "phone": "+919812300003"},
        {"_id": new_id(), "ws": uid, "name": "anil shah", "phone": "+919812300004"},     # two different Anil Shahs
        {"_id": new_id(), "ws": uid, "name": "Pooja Nair", "phone": "+919812300005", "opted_out": True},
        {"_id": new_id(), "ws": "someone-else", "name": "Kavita Rao", "phone": "+919812300006"},
    ])
    await d.leads.insert_one({"_id": new_id(), "owner_id": uid, "name": "Rohan Mehta", "phone": "+919812300002", "status": "won", "created_at": now()})
    await d.leads.insert_one({"_id": new_id(), "owner_id": uid, "name": "Sameer Gupta", "phone": "+919812300007", "status": "new", "created_at": now()})

    async def ask(author, rating=5, text="Excellent work, thank you!", platform="Google"):
        rv = (await paste(c, author=author, rating=rating, text=text, platform=platform, draft=False))["review"]
        r = await c.post(f"/api/reputation/reviews/{rv['id']}/referral")
        return rv, r

    rv, r = await ask("  meera   KULKARNI ", platform="Justdial")       # letter case and spacing don't matter
    out = r.json()
    assert r.status_code == 200 and out["matched"] is True and out["to_phone"] == "+919812300001"
    link = urlsplit(out["link"])
    assert link.netloc == "wa.me" and link.path == "/919812300001"
    text = parse_qs(link.query)["text"][0]
    assert text == out["text"] and text.startswith("Hello Meera, thank you for the kind words on Justdial!")
    assert "home and office interiors" in text and "98200 00000" in text and not rt.INCENTIVE.search(text)
    assert rv["referral_status"] == "ready" and rv["happy"] is True
    sent = (await c.post(f"/api/reputation/reviews/{rv['id']}/referral/sent")).json()
    assert sent["referral_status"] == "sent" and sent["referral_sent_at"] and sent["matched"] is True

    _, r = await ask("Rohan Mehta")                                           # a won lead counts as a customer
    assert r.json()["matched"] and r.json()["link"].startswith("https://wa.me/919812300002?text=")

    for author in ("Meera Kulkarnii", "Meera Kulkarn", "Meera", "M Kulkarni", "Anil Shah", "Kavita Rao", "Sameer Gupta"):
        _, r = await ask(author)                                              # a typo, a prefix, a first name, initials, two people, another business, not won
        out = r.json()
        assert r.status_code == 200 and out["matched"] is False and out["link"] is None, author
        assert out["note"] == "We couldn't match this reviewer to a customer — copy the message and send it yourself."
        assert out["text"].startswith("Hello")

    _, r = await ask("Pooja Nair")                                            # asked not to be messaged
    assert r.json()["link"] is None and "asked not to get messages" in r.json()["note"]

    # only happy reviewers: 5 stars, or 4 with kind words and no complaint
    _, r = await ask("Meera Kulkarni", rating=3, text="It was fine.")
    assert r.status_code == 400
    _, r = await ask("Meera Kulkarni", rating=4, text="Good work but they came late twice.")
    assert r.status_code == 400
    rv, r = await ask("Meera Kulkarni", rating=4, text="Very professional and neat work.")
    assert r.status_code == 200 and rv["happy"] is True
    skipped = (await c.post(f"/api/reputation/reviews/{rv['id']}/referral/skip")).json()
    assert skipped["referral_status"] == "skipped"
    page = (await c.get("/api/reputation")).json()
    assert page["referral_ready"] == 9      # 11 happy reviews: one sent, one skipped; the 3-star and the 4-star complaint never were ready


# ───────────────────────── asking happy customers for a review ─────────────────────────
async def test_ask_for_a_review_and_the_happy_list(client):
    c, uid = await owner_on("9871100010", "lite")
    assert (await c.post("/api/reputation/ask", json={"name": "Ritu Sharma", "phone": "98123 00011"})).status_code == 400   # no review link yet
    d, t = db(), now()
    await d.leads.insert_many([
        {"_id": new_id(), "owner_id": uid, "name": "Ritu Sharma", "phone": "+919812300011", "status": "won", "won_at": t - timedelta(days=2), "updated_at": t},
        {"_id": new_id(), "owner_id": uid, "name": "Arjun Rao", "phone": "+919812300012", "status": "won", "won_at": t - timedelta(days=5), "updated_at": t,
         "review_requested_at": t - timedelta(days=10)},                                     # asked by the Review requests automation
        {"_id": new_id(), "owner_id": uid, "name": "Lead Not Won", "phone": "+919812300013", "status": "contacted", "updated_at": t},
        {"_id": new_id(), "owner_id": uid, "name": "No Phone", "phone": "", "status": "won", "updated_at": t},
    ])
    await d.customers.insert_many([
        {"_id": new_id(), "ws": uid, "name": "Dev Patel", "phone": "+919812300014", "last_purchase": day(-1), "updated_at": t},
        {"_id": new_id(), "ws": uid, "name": "Opted Out", "phone": "+919812300015", "last_purchase": day(-1), "opted_out": True, "updated_at": t},
        {"_id": new_id(), "ws": uid, "name": "Asked Lately", "phone": "+919812300016", "last_purchase": day(-3), "updated_at": t},
        {"_id": new_id(), "ws": uid, "name": "Asked Long Ago", "phone": "+919812300017", "last_purchase": day(-40), "updated_at": t},
        {"_id": new_id(), "ws": uid, "name": "Ritu Sharma", "phone": "919812300011", "last_purchase": day(-9), "updated_at": t},   # same person as the lead
    ])
    await d.reputation_asks.insert_many([{"_id": new_id(), "ws": uid, "phone": "919812300016", "name": "Asked Lately", "at": t - timedelta(days=30)},
                                         {"_id": new_id(), "ws": uid, "phone": "919812300017", "name": "Asked Long Ago", "at": t - timedelta(days=100)}])
    happy = (await c.get("/api/reputation/happy")).json()
    assert [x["name"] for x in happy["items"]] == ["Dev Patel", "Ritu Sharma", "Asked Long Ago"]
    assert happy["review_link"] is None and (await c.get("/api/reputation")).json()["happy_count"] == 3

    # the Knowledge Hub's review link is used when none is saved here
    assert (await c.put("/api/business", json={**PROFILE, "review_link": "https://g.page/r/test-sgi/review"})).status_code == 200
    page = (await c.get("/api/reputation")).json()
    assert page["review_link"] == "https://g.page/r/test-sgi/review" and page["review_link_from"] == "knowledge"

    r = await c.post("/api/reputation/ask", json={"name": "Ritu Sharma", "phone": "98123 00011"})
    assert r.status_code == 200, r.text
    out = r.json()
    link = urlsplit(out["link"])
    assert link.netloc == "wa.me" and link.path == "/919812300011"
    text = parse_qs(link.query)["text"][0]
    assert text == out["text"] and "https://g.page/r/test-sgi/review" in text and "honest feedback" in text and "good or bad" in text
    assert text.startswith("Hello Ritu,") and not rt.INCENTIVE.search(text)
    assert [x["name"] for x in (await c.get("/api/reputation/happy")).json()["items"]] == ["Dev Patel", "Asked Long Ago"]
    assert await d.reputation_asks.count_documents({"ws": uid, "phone": "919812300011"}) == 1
    assert (await c.post("/api/reputation/ask", json={"name": "Wrong", "phone": "12345 6"})).status_code == 400


# ───────────────────────── plans, roles, other businesses ─────────────────────────
async def test_plan_gating_roles_and_other_businesses(client):
    free, _ = await owner_on("9871100011", "free")
    r = await free.get("/api/reputation")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "locked"
    assert (await free.post("/api/reputation/reviews", json={"text": "ok", "rating": 5})).status_code == 403

    a, aid = await owner_on("9871100012", "lite")
    assert (await a.get("/api/reputation")).status_code == 200
    rv = (await paste(a, draft=False))["review"]

    b, _ = await owner_on("9871100013", "lite")
    assert (await b.get("/api/reputation")).json()["reviews"] == []
    for method, path, body in (("patch", "", {"reply_draft": "x"}), ("post", "/draft", None), ("post", "/approve", None), ("post", "/replied", None),
                               ("post", "/referral", None), ("post", "/referral/sent", None), ("delete", "", None)):
        kw = {"json": body} if body is not None else {}
        r = await getattr(b, method)(f"/api/reputation/reviews/{rv['id']}{path}", **kw)
        assert r.status_code == 404, (method, path)
    assert (await db().reviews.find_one({"_id": rv["id"]}))["reply_status"] == "none"

    # team: staff can't open it; a manager can use it but not change the listing or the review link
    g, gid = await owner_on("9871100014", "growth")
    staff, manager = await new_owner("9871100015"), await new_owner("9871100016")
    for who, role in ((staff, "staff"), (manager, "manager")):
        await db().users.update_one({"_id": (await who.get("/api/me")).json()["user"]["id"]}, {"$set": {"team_of": gid, "team_role": role}})
    assert (await staff.get("/api/reputation")).status_code == 403
    assert (await staff.post("/api/reputation/reviews", json={"text": "ok", "rating": 5})).status_code == 403
    assert (await manager.get("/api/reputation")).status_code == 200
    assert (await manager.post("/api/reputation/reviews", json={"text": "Nice", "rating": 5, "draft": False})).status_code == 200
    assert (await manager.put("/api/reputation/settings", json={"auto_draft": False})).status_code == 403
    assert (await manager.get("/api/reputation/google-search", params={"q": "abc"})).status_code == 403
    assert (await g.put("/api/reputation/settings", json={"auto_draft": False})).status_code == 200


async def test_b2b_note(client):
    c, _ = await owner_on("9871100017", "lite", B2B)
    assert (await c.get("/api/reputation")).json()["b2b_note"] is True
    c2, _ = await owner_on("9871100018", "lite")
    assert (await c2.get("/api/reputation")).json()["b2b_note"] is False
    assert rt.is_b2b({"industry": "Wholesale trading of steel pipes"}) and not rt.is_b2b({"industry": "Dental clinic"})


# ───────────────────────── Google's 30-day limit on the listing's name and address ─────────────────────────
async def test_the_listings_name_and_address_are_forgotten_30_days_after_the_last_check(client):
    c, uid = await owner_on("9871100020", "lite")
    t = now()
    await db().reputation_settings.insert_one({"_id": uid, "google_place_id": PLACE, "google_name": "Shree Ganesh Interiors",
                                               "google_address": "Shop 4, Lake Road, Thane West", "google_at": t - timedelta(days=45)})
    await db().reputation_snapshots.insert_one({"_id": f"{uid}:recent", "ws": uid, "at": t - timedelta(days=3), "day": day(-3), "rating": 4.4, "count": 30})
    await reputation.forget_old_google(uid, t)              # checked 3 days ago: the name is fresh enough
    assert (await db().reputation_settings.find_one({"_id": uid}))["google_name"] == "Shree Ganesh Interiors"

    await reputation.forget_old_google(uid, t + timedelta(days=31))   # no check for 31 days: name and address go, the place id stays
    st = await db().reputation_settings.find_one({"_id": uid})
    assert st["google_name"] == "" and st["google_address"] == "" and st["google_place_id"] == PLACE
    assert await db().reputation_snapshots.count_documents({"ws": uid}) == 0

    # never checked at all (e.g. the key was taken out): 30 days after it came from the search
    other, oid = await owner_on("9871100021", "lite")
    await db().reputation_settings.insert_one({"_id": oid, "google_place_id": PLACE, "google_name": "Precision Fasteners", "google_address": "MIDC",
                                               "google_at": t - timedelta(days=31)})
    page = (await other.get("/api/reputation")).json()          # the page runs it too
    assert page["google"]["name"] is None and page["google"]["address"] is None and page["google"]["place_id"] == PLACE
    assert "query_place_id=" + PLACE in page["google"]["maps_link"]


async def test_an_expired_google_review_loses_its_customer_match_and_referral(client):
    c, uid = await owner_on("9871100022", "lite")
    old = now() - timedelta(days=31)
    rid = new_id()
    await db().reviews.insert_one({"_id": rid, "ws": uid, "source": "google", "platform": "Google", "google_review_name": "places/x/reviews/1",
                                   "author": "Meera Kulkarni", "author_uri": "", "google_link": "", "rating": 5, "text": "Lovely work",
                                   "review_date": "2026-09-01", "fetched_at": old, "reply_draft": "", "reply_status": "none", "hidden": False,
                                   "customer_id": "c1", "lead_id": None, "match_name": "Meera Kulkarni", "referral_status": "ready", "created_at": old})
    page = (await c.get("/api/reputation")).json()
    r = page["reviews"][0]
    assert r["google_expired"] and r["author"] == "" and r["match_name"] == "" and r["customer_id"] is None and r["referral_status"] == "none"
    assert page["referral_ready"] == 0
    assert (await c.post(f"/api/reputation/reviews/{rid}/referral")).status_code == 400


# ───────────────────────── billed Google calls: atomic daily caps ─────────────────────────
async def test_searches_and_connect_time_lookups_are_capped_per_day(client, google):
    c, uid = await owner_on("9871100023", "lite")
    # 25 searches at once: exactly the first 20 reach Google, the rest are refused — none slip past the count
    rs = await asyncio.gather(*[c.get("/api/reputation/google-search", params={"q": f"Shree Ganesh Interiors {i}"}) for i in range(25)])
    assert sorted(r.status_code for r in rs) == [200] * reputation.SEARCHES_PER_DAY + [429] * 5
    assert len([r for r in google.calls if r.method == "POST"]) == reputation.SEARCHES_PER_DAY
    assert (await c.get("/api/reputation/google-search", params={"q": "again"})).status_code == 429

    # disconnecting and connecting again ten times: the first look at Google happens 3 times, then waits for the morning check
    notes = []
    for _ in range(10):
        r = await c.put("/api/reputation/settings", json={"google_place_id": PLACE, "google_name": "Shree Ganesh Interiors"})
        assert r.status_code == 200
        notes.append(r.json()["note"])
        assert (await c.put("/api/reputation/settings", json={"google_place_id": ""})).status_code == 200
    assert len([r for r in google.calls if r.method == "GET"]) == reputation.CONNECT_LOOKS_PER_DAY
    assert notes[-1] == "Connected. Your reviews will come in with tomorrow morning's check."
    # another business has its own allowance
    other, _ = await owner_on("9871100024", "lite")
    assert (await other.get("/api/reputation/google-search", params={"q": "Precision Fasteners"})).status_code == 200


# ───────────────────────── opted-out customers, however they show up ─────────────────────────
async def test_an_opted_out_customer_is_never_suggested_or_linked_through_a_won_lead(client):
    c, uid = await owner_on("9871100025", "lite")
    assert (await c.put("/api/reputation/settings", json={"review_link": "https://g.page/r/test-sgi/review"})).status_code == 200
    t = now()
    # the same number: saved as a customer who opted out (under a short name), and as a won lead under the full name
    await db().customers.insert_one({"_id": new_id(), "ws": uid, "name": "P. Nair", "phone": "+919812300031", "opted_out": True, "updated_at": t})
    await db().leads.insert_many([
        {"_id": new_id(), "owner_id": uid, "name": "Pooja Nair", "phone": "919812300031", "status": "won", "won_at": t, "updated_at": t},
        {"_id": new_id(), "owner_id": uid, "name": "Kunal Bose", "phone": "+919812300032", "status": "won", "won_at": t, "updated_at": t},
    ])
    assert [x["name"] for x in (await c.get("/api/reputation/happy")).json()["items"]] == ["Kunal Bose"]
    rv = (await paste(c, author="Pooja Nair", rating=5, text="Excellent!", draft=False))["review"]
    out = (await c.post(f"/api/reputation/reviews/{rv['id']}/referral")).json()
    assert out["link"] is None and out["to_phone"] is None and "asked not to get messages" in out["note"]
    ok = (await c.post(f"/api/reputation/reviews/{(await paste(c, author='Kunal Bose', rating=5, text='Great', draft=False))['review']['id']}/referral")).json()
    assert ok["link"].startswith("https://wa.me/919812300032?text=")
