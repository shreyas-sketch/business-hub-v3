"""
Hub-wide sweep for Google Places content (v2.2).

Google's Maps Platform terms limit how long Places content may be kept. The Reviews & reputation page and Competitor Watch
already forget anything older than 30 days whenever they're opened or their agent runs — but a business that stops using
those pages (or whose plan ends) would otherwise keep old Google content for ever. Once an hour, from the agent scheduler's
clock, this finds every business holding Google content past the 30 days and runs both pages' forget_old_google for it.

One claim per hour (job_claims "google-sweep:<UTC hour>"), so several servers don't sweep at once. At most SWEEP_CAP
businesses per pass; the rest are picked up the next hour.
"""
from datetime import datetime, timedelta

from .agents.jobs import claim
from .db import db

KEEP_DAYS = 30
SWEEP_CAP = 2000


async def _holders(cut: datetime) -> set[str]:
    """Businesses with Google content older than the cut, in any of the places the two pages keep it."""
    d = db()
    found: set[str] = set()
    checks = [
        (d.reviews, {"source": "google", "fetched_at": {"$lt": cut}, "google_expired": {"$ne": True}}),
        (d.reputation_snapshots, {"at": {"$lt": cut}}),
        (d.reputation_settings, {"$or": [{"google_name": {"$gt": ""}}, {"google_address": {"$gt": ""}}],
                                 "google_at": {"$not": {"$gte": cut}}}),          # older, empty or never set
        (d.competitor_snapshots, {"google.at": {"$lt": cut}}),
        (d.competitor_reports, {"at": {"$lt": cut}, "google_cleared": {"$ne": True}}),
        (d.competitors, {"$or": [{"google_name": {"$gt": ""}}, {"google_address": {"$gt": ""}}],
                         "$and": [{"$or": [{"google_at": {"$lt": cut}}, {"google_at": {"$exists": False}, "created_at": {"$lt": cut}}]}]}),
    ]
    for coll, q in checks:
        by_id = coll.name == "reputation_settings"           # its _id is the business
        async for doc in coll.find(q, {"ws": 1, "_id": 1}).limit(SWEEP_CAP):
            ws = doc["_id"] if by_id else doc.get("ws")
            if ws:
                found.add(ws)
            if len(found) >= SWEEP_CAP:
                return found
    return found


async def sweep(at: datetime) -> int:
    """Forgets Google content older than 30 days for every business holding some. Returns how many businesses were swept
    (0 when another server already swept this hour)."""
    if not await claim(f"google-sweep:{at.strftime('%Y-%m-%dT%H')}", at):
        return 0
    from .routers import competitors, reputation
    holders = await _holders(at - timedelta(days=KEEP_DAYS))
    for ws in sorted(holders):
        await reputation.forget_old_google(ws, at)
        await competitors.forget_old_google(ws, at)
    return len(holders)
