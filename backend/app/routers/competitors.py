"""
Competitor Watch (v2.2, Membership — feature competitor_watch) and Content & company research (Action Program — feature
market_research). Fetching, comparing and the AI prompts are in app/competitor_watch.py.

Competitor Watch
- Up to 5 competitors per business: a website (read every Monday, compared with the last check), an optional Google match
  (rating and review count, only when the hub has a Google Places key) and a Facebook page name for a one-tap link to
  Meta's public Ad Library — which the owner opens; the hub never reads it.
- Every Monday at 8am (India time) the agent checks each competitor once and saves a report; "Check now" does the same,
  at most once per competitor per day (the Monday run counts). When something changed, ONE AI run writes a short summary
  strictly from the website changes; when nothing changed (or only Google's numbers moved), no AI run — the report says so.
- After the Monday report the owner gets a note in the hub and a WhatsApp alert from the hub's number (owner only).

Content & company research
- Post ideas from the latest competitor report with changes (or the business profile alone, and the page says so).
- A company research plan (kinds of companies, filters, qualifying questions) with search links built here — never by the
  AI — for the owner to open, and a CSV template to fill. Nothing is fetched or scraped; no people's details are collected.

competitors           {_id, ws, name, website, google_place_id, google_name, google_address, google_at (when the name/address
                       last came from Google), facebook_name, notes, created_at, created_by, updated_at, last_checked_at}
competitor_snapshots  {_id: competitor id, ws, website, pages [{url, path, title, hash, lines, prices, offers}], at,
                       google {place_id, rating, count, at} | null}
competitor_checks     {_id: "<ws>:site:<host>:<day>" | "<ws>:place:<place id>:<day>" | "<ws>:competitor:<id>:<day>", ws, competitor_id,
                       part site|google|plain, day, at}  — the once-a-day claims, per website and per Google listing (removing and
                       adding a competitor again can't buy a second crawl or a second billed Google call the same day)
competitor_reports    {_id, ws, week (Monday, ISO date), at, source weekly|manual, items [{competitor_id, name, website,
                       website_changes {...} | null, google {rating, count, rating_change, count_change} | null, google_note,
                       ad_library_link, errors [plain lines], first_check}], changed, summary (AI text) | null, ai_used, note}
competitor_quota      {_id: "<ws>:places:<YYYY-MM-DD>", ws, n, at}  — Google searches per business per day (the key is billed)
research_outputs      {_id, ws, kind content|companies, at, by, input, result, links, csv, based_on {report_id, week} | null, note}
"""
import asyncio
import logging
import re
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .. import competitor_watch as cw
from .. import fetch
from ..agents import catalog
from ..agents.catalog import Skip
from ..config import settings
from ..context import Ctx, feature
from ..db import db, now, public
from ..messaging import whatsapp_template
from ..services import new_id, notify, run_ai, track
from .hub import profile_of

log = logging.getLogger("competitors")
router = APIRouter(prefix="/api")
watch_ctx = feature("competitor_watch", "manager")
research_ctx = feature("market_research", "manager")
PLACES_SEARCHES_PER_DAY = 20
KEEP_REPORTS = 52
SCHEDULE_TEXT = "Every Monday at 8am"


# ───────────────────────── competitors ─────────────────────────
def _website(v: str) -> str:
    v = (v or "").strip()
    if not v:
        return ""
    try:
        return fetch.normalise_url(v)
    except fetch.FetchError:
        raise ValueError("Enter their website address, like www.theirbusiness.in")


def _place(v: str) -> str:
    v = (v or "").strip()
    if v and not cw.PLACE_ID.match(v):
        raise ValueError("Pick their Google listing from the search")
    return v


class CompetitorIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    website: str = Field(default="", max_length=300)
    google_place_id: str = Field(default="", max_length=300)
    google_name: str = Field(default="", max_length=120)
    google_address: str = Field(default="", max_length=200)
    facebook_name: str = Field(default="", max_length=80)
    notes: str = Field(default="", max_length=500)

    @field_validator("website")
    @classmethod
    def site(cls, v):
        return _website(v)

    @field_validator("google_place_id")
    @classmethod
    def place(cls, v):
        return _place(v)


class CompetitorPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=80)
    website: str | None = Field(default=None, max_length=300)
    google_place_id: str | None = Field(default=None, max_length=300)
    google_name: str | None = Field(default=None, max_length=120)
    google_address: str | None = Field(default=None, max_length=200)
    facebook_name: str | None = Field(default=None, max_length=80)
    notes: str | None = Field(default=None, max_length=500)

    @field_validator("website")
    @classmethod
    def site(cls, v):
        return v if v is None else _website(v)

    @field_validator("google_place_id")
    @classmethod
    def place(cls, v):
        return v if v is None else _place(v)


def _clean(d: dict) -> dict:
    return {k: re.sub(r"\s+", " ", v).strip() if isinstance(v, str) and k != "notes" else (v.strip() if isinstance(v, str) else v) for k, v in d.items()}


def comp_view(c: dict, checked_today: bool = False) -> dict:
    out = public(c, "ws", "created_by")
    out["ad_library_link"] = cw.ad_library_link(c.get("facebook_name") or c.get("name", ""))
    out["checked_today"] = checked_today
    return out


def report_view(r: dict | None) -> dict | None:
    return public(r, "ws") if r else None


def _check_keys(c: dict, day: str) -> list[tuple[str, str]]:
    """The once-a-day claims for one competitor: one per website host and one per Google listing."""
    keys = []
    if c.get("website"):
        keys.append(("site", f"{c['ws']}:site:{cw._host(c['website'])}:{day}"))
    if c.get("google_place_id"):
        keys.append(("google", f"{c['ws']}:place:{c['google_place_id']}:{day}"))
    return keys or [("plain", f"{c['ws']}:competitor:{c['_id']}:{day}")]


async def _checked_today(ws: str, comps: list[dict], at: datetime) -> set:
    """Ids of the competitors with every part already checked today."""
    day = cw.ist_day(at)
    claimed = {x["_id"] async for x in db().competitor_checks.find({"ws": ws, "day": day}, {"_id": 1})}
    return {c["_id"] for c in comps if all(k in claimed for _, k in _check_keys(c, day))}


async def _taken(ws: str, data: dict, but: str | None = None) -> str | None:
    """Another competitor of this business already on the same website (by host) or the same Google listing."""
    host = cw._host(data["website"]) if data.get("website") else ""
    async for o in db().competitors.find({"ws": ws, "_id": {"$ne": but}}, {"website": 1, "google_place_id": 1}):
        if host and o.get("website") and cw._host(o["website"]) == host:
            return "You're already watching that website."
        if data.get("google_place_id") and o.get("google_place_id") == data["google_place_id"]:
            return "You're already watching that Google Maps listing."
    return None


GOOGLE_KEEP_DAYS = 30


async def forget_old_google(ws: str, at) -> None:
    """Google's Maps Platform terms limit keeping Places content: a competitor's Google rating and review count are kept at
    most 30 days — in the last reading (used to show the change) and in old reports. The listing's name and address go too
    when the last Google reading is older than that (the place id itself may be kept)."""
    cut = at - timedelta(days=GOOGLE_KEEP_DAYS)
    await db().competitors.update_many(
        {"ws": ws, "$and": [{"$or": [{"google_name": {"$gt": ""}}, {"google_address": {"$gt": ""}}]},
                            {"$or": [{"google_at": {"$lt": cut}}, {"google_at": {"$exists": False}, "created_at": {"$lt": cut}}]}]},
        {"$set": {"google_name": "", "google_address": ""}})
    await db().competitor_snapshots.update_many({"ws": ws, "google.at": {"$lt": cut}}, {"$set": {"google": None}})
    async for r in db().competitor_reports.find({"ws": ws, "at": {"$lt": cut}, "google_cleared": {"$ne": True}}, {"items": 1}).limit(100):
        items = [{**i, "google": None} for i in r.get("items") or []]
        await db().competitor_reports.update_one({"_id": r["_id"], "ws": ws}, {"$set": {"items": items, "google_cleared": True}})


@router.get("/competitors")
async def list_competitors(ctx: Ctx = Depends(watch_ctx)):
    await forget_old_google(ctx.ws, now())
    comps = [c async for c in db().competitors.find({"ws": ctx.ws}).sort("created_at", 1).limit(cw.MAX_COMPETITORS * 2)]
    today = await _checked_today(ctx.ws, comps, cw.clock())
    latest = await db().competitor_reports.find_one({"ws": ctx.ws}, sort=[("at", -1)])
    return {"competitors": [comp_view(c, c["_id"] in today) for c in comps], "latest": report_view(latest),
            "google_available": cw.places_ready(), "max": cw.MAX_COMPETITORS, "schedule": SCHEDULE_TEXT,
            "can_check": any(c["_id"] not in today for c in comps)}


@router.post("/competitors")
async def add_competitor(body: CompetitorIn, ctx: Ctx = Depends(watch_ctx)):
    if await db().competitors.count_documents({"ws": ctx.ws}) >= cw.MAX_COMPETITORS:
        raise HTTPException(400, f"You can watch up to {cw.MAX_COMPETITORS} competitors. Remove one to add another.")
    data = _clean(body.model_dump())
    clash = await _taken(ctx.ws, data)
    if clash:
        raise HTTPException(409, clash)
    if not data["google_place_id"]:
        data.update(google_name="", google_address="")
    doc = {"_id": new_id(), "ws": ctx.ws, **data, "created_by": ctx.actor, "created_at": now(), "updated_at": now(), "last_checked_at": None,
           **({"google_at": now()} if data["google_place_id"] else {})}
    await db().competitors.insert_one(doc)
    if await db().competitors.count_documents({"ws": ctx.ws}) > cw.MAX_COMPETITORS:   # two adds at the same moment
        await db().competitors.delete_one({"_id": doc["_id"]})
        raise HTTPException(400, f"You can watch up to {cw.MAX_COMPETITORS} competitors. Remove one to add another.")
    await track("competitor_added", ctx.ws)
    return comp_view(doc)


@router.patch("/competitors/{competitor_id}")
async def edit_competitor(competitor_id: str, body: CompetitorPatch, ctx: Ctx = Depends(watch_ctx)):
    c = await db().competitors.find_one({"_id": competitor_id, "ws": ctx.ws})
    if not c:
        raise HTTPException(404, "Not found")
    patch = _clean(body.model_dump(exclude_none=True))
    clash = await _taken(ctx.ws, {"website": patch.get("website"), "google_place_id": patch.get("google_place_id")}, competitor_id)
    if clash:
        raise HTTPException(409, clash)
    if patch.get("google_place_id") and patch["google_place_id"] != c.get("google_place_id"):
        patch["google_at"] = now()                       # a fresh pick from Google search brings its name and address
    elif "google_place_id" in patch and not patch["google_place_id"]:
        patch.update(google_name="", google_address="")
    else:   # the same listing: its name and address only ever come from Google, never back from the page
        patch.pop("google_name", None)
        patch.pop("google_address", None)
    if patch:
        await db().competitors.update_one({"_id": competitor_id, "ws": ctx.ws}, {"$set": {**patch, "updated_at": now()}})
    # a new address or a different Google listing starts a fresh first look, never a false "everything changed"
    reset = {}
    if "website" in patch and patch["website"] != c.get("website"):
        reset.update(pages=[], website=patch["website"])
    if "google_place_id" in patch and patch["google_place_id"] != c.get("google_place_id"):
        reset["google"] = None
    if reset:
        await db().competitor_snapshots.update_one({"_id": competitor_id, "ws": ctx.ws}, {"$set": reset})
    return comp_view(await db().competitors.find_one({"_id": competitor_id, "ws": ctx.ws}))


@router.delete("/competitors/{competitor_id}")
async def delete_competitor(competitor_id: str, ctx: Ctx = Depends(watch_ctx)):
    r = await db().competitors.delete_one({"_id": competitor_id, "ws": ctx.ws})
    if not r.deleted_count:
        raise HTTPException(404, "Not found")
    await db().competitor_snapshots.delete_one({"_id": competitor_id, "ws": ctx.ws})
    return {"ok": True}


@router.get("/competitors/google-search")
async def google_search(q: str = "", ctx: Ctx = Depends(watch_ctx)):
    if not cw.places_ready():
        raise HTTPException(400, "Google search isn't set up in the hub yet. You can still watch their website and their ads.")
    q = re.sub(r"\s+", " ", q or "").strip()[:120]
    if len(q) < 2:
        raise HTTPException(400, "Type their business name, and the city.")
    key = f"{ctx.ws}:places:{cw.ist_day(cw.clock())}"
    used = await db().competitor_quota.find_one_and_update({"_id": key}, {"$inc": {"n": 1}, "$setOnInsert": {"ws": ctx.ws, "at": now()}},
                                                           upsert=True, return_document=ReturnDocument.AFTER)
    if (used or {}).get("n", 1) > PLACES_SEARCHES_PER_DAY:
        raise HTTPException(429, "That's enough Google searches for today. Try again tomorrow.")
    try:
        return {"results": await cw.places_search(q)}
    except cw.PlacesError:
        raise HTTPException(502, "Google search didn't work right now. Try again in a few minutes.")


@router.get("/competitors/reports")
async def list_reports(ctx: Ctx = Depends(watch_ctx)):
    return [report_view(r) async for r in db().competitor_reports.find({"ws": ctx.ws}).sort("at", -1).limit(12)]


@router.post("/competitors/check")
async def check_now(ctx: Ctx = Depends(watch_ctx)):
    if not await db().competitors.count_documents({"ws": ctx.ws}, limit=1):
        raise HTTPException(400, "Add a competitor first.")
    report = await run_check(ctx.owner, cw.clock(), "manual")
    if report is None:
        raise HTTPException(429, "You've already checked every competitor today. The next check is possible tomorrow.")
    await track("competitor_check", ctx.ws)
    return {"report": report_view(report)}


# ───────────────────────── the check (Check now and the Monday agent) ─────────────────────────
async def _claim(c: dict, at: datetime) -> set:
    """Claims what of this competitor may be read today: {"site", "google"} or {"plain"}; an empty set when all of it was."""
    day, got = cw.ist_day(at), set()
    for part, key in _check_keys(c, day):
        try:
            await db().competitor_checks.insert_one({"_id": key, "ws": c["ws"], "competitor_id": c["_id"], "part": part, "day": day, "at": at})
            got.add(part)
        except DuplicateKeyError:
            pass
    return got


def _item(c: dict) -> dict:
    return {"competitor_id": c["_id"], "name": c["name"], "website": c.get("website", ""), "website_changes": None, "google": None,
            "google_note": "", "ad_library_link": cw.ad_library_link(c.get("facebook_name") or c["name"]), "errors": [], "first_check": False}


async def _check_one(c: dict, snap: dict | None, at: datetime, parts: set) -> dict:
    snap = snap or {}
    item = _item(c)
    save: dict = {"ws": c["ws"], "at": at}
    seen: dict = {"last_checked_at": at}
    if c.get("website") and "site" not in parts:
        item["errors"].append("Their website was already read today, so it wasn't read again.")
    elif c.get("website"):
        previous = snap.get("pages") if snap.get("website") == c["website"] and snap.get("pages") else None
        try:   # a site that trickles its answer a byte at a time can't hold up the run
            res = await asyncio.wait_for(cw.check_site(c["website"], previous), cw.SITE_BUDGET_SECONDS + cw.SITE_GRACE_SECONDS)
        except asyncio.TimeoutError:
            res = cw.SiteResult(errors=[cw.SITE_SLOW])
        item["errors"] += res.errors
        if res.ok:
            item["website_changes"] = cw.compare(previous, res.pages, res.skipped, res.kept)
            item["first_check"] = previous is None
            save.update(website=c["website"], pages=res.pages)
    if c.get("google_place_id"):
        if not cw.places_ready():
            item["google_note"] = "The Google Maps rating needs the hub's Google key, which isn't set up yet — skipped."
        elif "google" not in parts:
            item["google_note"] = "Their Google Maps rating was already read today."
        else:
            try:
                got = await cw.place_rating(c["google_place_id"])
            except cw.PlacesError:
                item["errors"].append("Google didn't answer for their rating this time.")
            else:
                before = snap.get("google") if (snap.get("google") or {}).get("place_id") == c["google_place_id"] else None
                g = {"rating": got["rating"], "count": got["count"], "rating_change": None, "count_change": None}
                if before:
                    if got["rating"] is not None and before.get("rating") is not None:
                        g["rating_change"] = round(got["rating"] - before["rating"], 1)
                    if got["count"] is not None and before.get("count") is not None:
                        g["count_change"] = got["count"] - before["count"]
                elif not c.get("website"):
                    item["first_check"] = True
                item["google"] = g
                save["google"] = {"place_id": c["google_place_id"], "rating": got["rating"], "count": got["count"], "at": at}
                seen.update(google_at=at, **({"google_name": got["name"]} if got["name"] else {}),
                            **({"google_address": got["address"]} if got["address"] else {}))
    await db().competitor_snapshots.update_one({"_id": c["_id"]}, {"$set": save}, upsert=True)
    await db().competitors.update_one({"_id": c["_id"], "ws": c["ws"]}, {"$set": seen})
    return item


async def _check_safely(c: dict, snap: dict | None, at: datetime, parts: set) -> dict:
    """One competitor going wrong never loses the others' results."""
    try:
        return await _check_one(c, snap, at, parts)
    except Exception:
        log.exception("competitor check failed for %s", c["_id"])
        item = _item(c)
        item["errors"].append("Something went wrong while checking this competitor. It will be tried again at the next check.")
        return item


async def run_check(owner: dict, at: datetime, source: str) -> dict | None:
    """Checks every competitor of this business not yet checked today, saves and returns the report (None: all done today)."""
    ws = owner["_id"]
    comps = [c async for c in db().competitors.find({"ws": ws}).sort("created_at", 1).limit(cw.MAX_COMPETITORS)]
    mine = [(c, parts) for c in comps if (parts := await _claim(c, at))]
    if not mine:
        return None
    snaps = {s["_id"]: s async for s in db().competitor_snapshots.find({"_id": {"$in": [c["_id"] for c, _ in mine]}, "ws": ws})}
    items = list(await asyncio.gather(*(_check_safely(c, snaps.get(c["_id"]), at, parts) for c, parts in mine)))   # sites side by side
    changed = [i for i in items if cw.item_changed(i)]
    on_sites = [i for i in changed if (i.get("website_changes") or {}).get("any")]
    report = {"_id": new_id(), "ws": ws, "week": cw.week_of(at), "at": at, "source": source, "items": items, "changed": len(changed),
              "summary": None, "ai_used": False, "note": ""}
    if not changed:
        looked = [i for i in items if i["website_changes"] is not None or i["google"] is not None]
        report["note"] = ("Nothing could be compared this time — see the notes under each competitor." if not looked
                          else "First look saved. From the next check you'll see what changed." if all(i["first_check"] for i in looked)
                          else "No changes this week.")
    elif not on_sites:   # Google's numbers are shown below but never handed to the AI (Places content is kept 30 days at most)
        report["note"] = "Only Google Maps ratings or review counts moved this time — see below."
    else:
        profile = await db().businesses.find_one({"owner_id": ws}) or {}
        if not profile.get("name") or not profile.get("industry"):
            report["note"] = "Fill in your Business Brain, so the hub can also say what these changes mean for you."
        else:
            try:
                report["summary"] = await run_ai(owner, "competitor_summary", cw.summary_task(profile, on_sites),
                                                 f"Competitor report — week of {report['week']}", save_output=False, check=cw.summary_check(on_sites))
                report["ai_used"] = True
            except HTTPException as e:
                report["note"] = ("No AI runs left this month, so there's no summary — every change is listed below." if e.status_code == 402
                                  else "The summary couldn't be written this time — every change is listed below.")
    await db().competitor_reports.insert_one(report)
    old = [r["_id"] async for r in db().competitor_reports.find({"ws": ws}, {"_id": 1}).sort("at", -1).skip(KEEP_REPORTS)]
    if old:
        await db().competitor_reports.delete_many({"_id": {"$in": old}, "ws": ws})
    return report


def alert_text(report: dict) -> str:
    """The owner's Monday message, in plain words."""
    items, changed = report["items"], [i for i in report["items"] if cw.item_changed(i)]
    n = len(items)
    if changed:
        bits = []
        for i in changed[:3]:
            w, g, what = i.get("website_changes") or {}, i.get("google") or {}, []
            if w.get("prices_changed"):
                what.append(f"{len(w['prices_changed'])} price{'s' if len(w['prices_changed']) != 1 else ''} changed")
            if w.get("offers_new"):
                what.append(f"{len(w['offers_new'])} new offer line{'s' if len(w['offers_new']) != 1 else ''}")
            if w.get("pages_added") or w.get("pages_removed"):
                what.append("pages added or removed")
            if not what and w.get("pages_changed"):
                what.append("website text changed")
            if g.get("rating_change") or g.get("count_change"):
                what.append("Google Maps rating or reviews moved")
            bits.append(f"{i['name']}: {', '.join(what)}")
        text = (f"Competitor Watch: {len(changed)} of your {n} competitor{'s' if n != 1 else ''} changed something this week. "
                + ". ".join(bits) + ". Open Competitor Watch in the hub for the details.")
    elif all(i["first_check"] for i in items if i["website_changes"] is not None or i["google"] is not None):
        text = f"Competitor Watch: first look saved for your {n} competitor{'s' if n != 1 else ''}. From next Monday you'll see what changed."
    else:
        text = f"Competitor Watch: no changes on your {n} competitor{'s' if n != 1 else ''} this week."
    failed = sum(1 for i in items if i.get("errors"))
    if failed:
        text += f" {failed} couldn't be fully checked — see the report."
    return text


async def weekly_check(owner: dict, run: dict):
    ws = owner["_id"]
    await forget_old_google(ws, run["at"])
    if not await db().competitors.count_documents({"ws": ws}, limit=1):
        raise Skip("No competitors added yet.")
    report = await run_check(owner, run["at"], "weekly")
    if report is None:
        raise Skip("Every competitor was already checked today with Check now.")
    text = alert_text(report)
    b = await db().businesses.find_one({"owner_id": ws}, {"name": 1}) or {}
    await notify(ws, text[:600])
    wa = await whatsapp_template(owner["phone"], settings.aisensy_owner_alert_campaign, owner.get("name", ""),
                                 [b.get("name") or "your business", text[:900]], "owner_alert")
    n = len(report["items"])
    return (f"Competitor report ready: {n} checked, {report['changed']} changed." + ("" if wa["sent"] else " (Shown in the hub; WhatsApp isn't set up.)"),
            report["_id"])


async def _ready(owners: list[dict]) -> set:
    """Only owners who added competitors: nobody else gets a "skipped" line."""
    return set(await db().competitors.distinct("ws", {"ws": {"$in": [o["_id"] for o in owners]}}))


catalog.register("competitor_watch", {
    "name": "Competitor Watch", "feature": "competitor_watch", "trigger": "schedule",
    "what": "Every Monday, checks your competitors' websites (and their Google rating, when available) and tells you on WhatsApp what changed.",
    "when": "Every Monday at 8am", "schedule": {"every": "week", "weekday": 0, "at": "08:00", "grace_hours": 24},
    "ready": _ready, "group": "own_page"}, weekly_check)


# ───────────────────────── Content & company research (Action Program) ─────────────────────────
def research_view(o: dict | None) -> dict | None:
    return public(o, "ws") if o else None


@router.get("/research")
async def research(ctx: Ctx = Depends(research_ctx)):
    async def latest(kind: str) -> list:
        return [research_view(o) async for o in db().research_outputs.find({"ws": ctx.ws, "kind": kind}).sort("at", -1).limit(5)]
    r = await _report_for_content(ctx.ws)
    return {"content": await latest("content"), "companies": await latest("companies"),
            "report": {"id": r["_id"], "week": r["week"], "at": r["at"].isoformat(), "changed": r["changed"]} if r else None,
            "watching": await db().competitors.count_documents({"ws": ctx.ws}), "csv_columns": cw.CSV_COLUMNS}


async def _report_for_content(ws: str) -> dict | None:
    """The latest competitor report with website changes, from the last 5 weeks (Google's numbers are never used for ideas)."""
    return await db().competitor_reports.find_one({"ws": ws, "changed": {"$gt": 0}, "items.website_changes.any": True,
                                                   "at": {"$gte": now() - timedelta(days=35)}}, sort=[("at", -1)])


@router.post("/research/content")
async def content_ideas(ctx: Ctx = Depends(research_ctx)):
    profile = await profile_of(ctx.ws)
    report = await _report_for_content(ctx.ws)
    move_list = cw.moves([i for i in (report or {}).get("items", []) if cw.item_changed(i)]) if report else []
    names = [i["name"] for i in (report or {}).get("items", [])] + [c["name"] async for c in db().competitors.find({"ws": ctx.ws}, {"name": 1})]
    result = await run_ai(ctx.owner, "research_content", cw.content_ideas(profile, move_list), "Post ideas from your competitor report"
                          if move_list else "Post ideas from your business profile",
                          check=cw.ideas_check(move_list, names, profile.get("whatsapp", "")))
    note = (f"From your competitor report of the week of {_day(report['week'])}." if move_list
            else "No competitor changes to answer yet, so these ideas come from your business profile alone.")
    doc = {"_id": new_id(), "ws": ctx.ws, "kind": "content", "at": now(), "by": ctx.actor, "input": {}, "result": result,
           "based_on": {"report_id": report["_id"], "week": report["week"]} if move_list else None, "note": note}
    await db().research_outputs.insert_one(doc)
    await track("research_content", ctx.ws)
    return {"output": research_view(doc)}


def _day(iso: str) -> str:
    try:
        d = datetime.strptime(iso, "%Y-%m-%d")
        return f"{d.day} {d.strftime('%b %Y')}"
    except (TypeError, ValueError):
        return iso


class CompaniesIn(BaseModel):
    who: str = Field(min_length=3, max_length=200)
    city: str = Field(default="", max_length=60)
    extra: str = Field(default="", max_length=300)


@router.post("/research/companies")
async def company_research(body: CompaniesIn, ctx: Ctx = Depends(research_ctx)):
    profile = await profile_of(ctx.ws)
    who, city, extra = (re.sub(r"\s+", " ", x).strip() for x in (body.who, body.city, body.extra))
    plan = await run_ai(ctx.owner, "research_companies", cw.company_plan(profile, who, city, extra), f"Company research plan — {who[:60]}",
                        check=cw.plan_check)
    links = cw.search_links(who, city, [t["type"] for t in plan["company_types"]], extra)
    doc = {"_id": new_id(), "ws": ctx.ws, "kind": "companies", "at": now(), "by": ctx.actor, "input": {"who": who, "city": city, "extra": extra},
           "result": plan, "links": links, "csv": cw.csv_template(), "based_on": None,
           "note": "Open these searches yourself and note company details only. The hub doesn't scrape or collect anyone's personal details."}
    await db().research_outputs.insert_one(doc)
    await track("research_companies", ctx.ws)
    return {"output": research_view(doc)}


@router.get("/research/companies/template.csv")
async def csv_template(ctx: Ctx = Depends(research_ctx)):
    return Response("﻿" + cw.csv_template(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="company-research.csv"', "Cache-Control": "private"})


async def ensure_indexes() -> None:
    d = db()
    await d.competitors.create_index([("ws", 1), ("created_at", 1)])
    await d.competitor_snapshots.create_index("ws")
    await d.competitor_checks.create_index([("ws", 1), ("day", 1)])
    await d.competitor_reports.create_index([("ws", 1), ("at", -1)])
    await d.research_outputs.create_index([("ws", 1), ("kind", 1), ("at", -1)])
    try:   # day claims and search counters are only needed for a few days
        await d.competitor_checks.create_index("at", name="competitor_checks_ttl", expireAfterSeconds=8 * 86400)
        await d.competitor_quota.create_index("at", name="competitor_quota_ttl", expireAfterSeconds=3 * 86400)
    except Exception:
        pass
