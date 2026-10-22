"""Google's Maps Platform terms limit keeping Places content: the hub keeps a Google review's words, stars and author, the
rating snapshots, and a competitor's Google rating, for at most 30 days. Everything here is invented."""
from datetime import timedelta

from app.db import db, now
from app.services import new_id
from tests.test_reputation import owner_on


async def test_google_review_words_and_ratings_are_kept_30_days_at_most(client):
    c, uid = await owner_on("9864000001", "lite")
    old, fresh = now() - timedelta(days=31), now() - timedelta(days=2)
    base = {"ws": uid, "source": "google", "platform": "Google", "author_uri": "", "google_link": "", "review_date": "2026-09-01",
            "reply_draft": "Thank you!", "reply_status": "replied", "hidden": False, "created_at": old}
    await db().reviews.insert_many([
        {**base, "_id": new_id(), "google_review_name": "places/x/reviews/old", "author": "Test Reviewer", "rating": 5, "text": "Great service", "fetched_at": old},
        {**base, "_id": new_id(), "google_review_name": "places/x/reviews/new", "author": "Second Reviewer", "rating": 4, "text": "Good", "fetched_at": fresh},
    ])
    await db().reputation_snapshots.insert_many([{"_id": f"{uid}:old", "ws": uid, "at": old, "day": "2026-09-01", "rating": 4.6, "count": 80},
                                                 {"_id": f"{uid}:new", "ws": uid, "at": fresh, "day": "2026-10-19", "rating": 4.7, "count": 84}])
    r = await c.get("/api/reputation")
    assert r.status_code == 200
    reviews = {x["google_review_name"]: x for x in r.json()["reviews"]}
    gone = reviews["places/x/reviews/old"]
    assert gone["google_expired"] and gone["text"] == "" and gone["author"] == "" and gone["rating"] is None
    assert gone["reply_draft"] == "Thank you!" and gone["reply_status"] == "replied"        # the owner's own reply stays
    assert reviews["places/x/reviews/new"]["text"] == "Good"
    assert await db().reputation_snapshots.count_documents({"ws": uid}) == 1


async def test_a_competitors_google_rating_is_kept_30_days_at_most(client):
    c, uid = await owner_on("9864000002", "lite")
    cid = new_id()
    await db().competitors.insert_one({"_id": cid, "ws": uid, "name": "Test Competitor", "website": "", "google_place_id": "ChIJtest",
                                       "facebook_name": "", "notes": "", "created_at": now()})
    old = now() - timedelta(days=31)
    await db().competitor_snapshots.insert_one({"_id": cid, "ws": uid, "pages": [], "at": old,
                                                "google": {"place_id": "ChIJtest", "rating": 4.2, "count": 120, "at": old}})
    await db().competitor_reports.insert_one({"_id": new_id(), "ws": uid, "week": "2026-09-14", "at": old, "source": "weekly", "changed": 1,
                                              "summary": None, "ai_used": False, "note": "",
                                              "items": [{"competitor_id": cid, "name": "Test Competitor", "google": {"rating": 4.2, "count": 120}}]})
    assert (await c.get("/api/competitors")).status_code == 200
    assert (await db().competitor_snapshots.find_one({"_id": cid}))["google"] is None
    rep = await db().competitor_reports.find_one({"ws": uid})
    assert rep["google_cleared"] and rep["items"][0]["google"] is None


async def test_the_hourly_sweep_forgets_old_google_content_even_if_nobody_opens_the_pages(client):
    from app import google_retention
    from app.agents import scheduler
    c, uid = await owner_on("9864000003", "lite")
    old = now() - timedelta(days=31)
    await db().reviews.insert_one({"_id": new_id(), "ws": uid, "source": "google", "platform": "Google", "google_review_name": "places/y/reviews/1",
                                   "author": "Test Reviewer", "rating": 5, "text": "Great service", "author_uri": "", "google_link": "",
                                   "review_date": "2026-09-01", "reply_draft": "", "reply_status": "none", "hidden": False,
                                   "created_at": old, "fetched_at": old})
    cid = new_id()
    await db().competitor_snapshots.insert_one({"_id": cid, "ws": uid, "pages": [], "at": old,
                                                "google": {"place_id": "ChIJtest2", "rating": 4.0, "count": 50, "at": old}})
    out = await scheduler.tick()
    assert out["google_forgotten"] == 1
    r = await db().reviews.find_one({"ws": uid})
    assert r["google_expired"] and r["text"] == "" and r["rating"] is None
    assert (await db().competitor_snapshots.find_one({"_id": cid}))["google"] is None
    assert await google_retention.sweep(now()) == 0          # once an hour, however many servers


async def test_the_sweep_finds_an_old_listing_name_kept_in_the_reputation_settings(client):
    from app import google_retention
    _, uid = await owner_on("9864000004", "lite")
    old = now() - timedelta(days=40)
    await db().reputation_settings.update_one({"_id": uid}, {"$set": {"google_name": "Test Listing", "google_address": "Test Road",
                                                                      "google_at": old, "updated_at": old}}, upsert=True)
    assert uid in await google_retention._holders(now() - timedelta(days=30))
    await google_retention.sweep(now())
    st = await db().reputation_settings.find_one({"_id": uid})
    assert st["google_name"] == "" and st["google_address"] == ""
