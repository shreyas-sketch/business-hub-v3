"""
Social media (v2.1, 3.12): "Mark as posted" on every post the hub wrote — the Content Agent, the content calendar and the
weekly plan — with an optional platform and link, and a posting log that feeds the Dashboard's Social tile.

social_posts {_id, owner_id, post_ref, source, platform, link, hook, posted_at, by}
post_ref    cal:{YYYY-MM}:{YYYY-MM-DD}   a content-calendar post
            week:{week_key}:{n}           a post in a weekly plan
            out:{output_id}:{s}[-{p}]     a Content Agent draft: section s, or post p inside it
A post is counted once: (owner_id, post_ref) is unique, and marking it again only updates the platform or link.
"""
import re
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

from .. import dashboard_cache
from ..agents import rhythm
from ..context import Ctx, get_ctx
from ..db import db, now
from ..services import new_id, track
from .membership import ist_day, ist_midnight

router = APIRouter(prefix="/api/social")
PLATFORMS = ["Instagram", "Facebook", "LinkedIn", "WhatsApp Status", "Google"]
REF = re.compile(r"^(cal:20\d\d-\d\d:20\d\d-\d\d-\d\d|week:[0-9W-]{6,10}:\d{1,2}|out:[A-Za-z0-9_]{6,40}:\d{1,2}(-\d{1,2})?|sm:[a-f0-9]{32})$")
# sm: a post on the v2.3 Social media page (routers/socialmedia.py)
SOURCE_FEATURE = {"cal": "content_calendar", "week": "week", "out": "tool_content", "sm": "social_media"}
FEATURES = tuple(SOURCE_FEATURE.values())


def _can_see(ctx: Ctx) -> None:
    if not any(ctx.has(f) for f in FEATURES):
        ctx.require("tool_content")


async def _source_post(ws: str, ref: str) -> dict | None:
    """The post this ref points to, if it is in this workspace."""
    if ref.startswith("sm:"):
        sm = await db().sm_posts.find_one({"_id": ref[3:], "owner_id": ws})
        return {"hook": (sm["fields"].get("headline") or sm["fields"].get("title") or sm["fields"].get("greeting") or sm["caption"])[:140],
                "caption": sm["caption"]} if sm else None
    kind, a, b = ref.split(":", 2)
    if kind == "cal":
        cal = await db().calendars.find_one({"_id": f"{ws}:{a}"})
        return next((p for p in (cal or {}).get("posts") or [] if p["date"] == b), None)
    if kind == "week":
        w = await db().weeks.find_one({"_id": f"{ws}:{a}"})
        posts = (w or {}).get("posts") or []
        return posts[int(b)] if int(b) < len(posts) else None
    o = await db().outputs.find_one({"_id": a, "user_id": ws, "kind": "tool:content"})
    sections = ((o or {}).get("content") or {}).get("sections") or []
    si, _, pi = b.partition("-")
    if int(si) >= len(sections):
        return None
    s = sections[int(si)]
    if pi:   # one post inside a section ("12 posts" → post 3)
        points = s.get("points") or []
        return {"hook": str(points[int(pi)])[:140], "caption": str(points[int(pi)])} if int(pi) < len(points) else None
    return {"hook": s.get("heading", ""), "caption": s.get("text", "")}


async def _sync_calendar(ws: str, ref: str, posted: bool) -> None:
    if ref.startswith("cal:"):
        _, month, day = ref.split(":", 2)
        cal = await db().calendars.find_one({"_id": f"{ws}:{month}"})
        if cal:
            posts = cal["posts"]
            for p in posts:
                if p["date"] == day:
                    p["posted"] = posted
            await db().calendars.update_one({"_id": cal["_id"]}, {"$set": {"posts": posts}})


async def mark(ws: str, actor: str, ref: str, platform: str = "", link: str = "", at: datetime | None = None) -> dict:
    """Marks a post as posted. Counted once: a second mark only changes the platform or the link."""
    post = await _source_post(ws, ref)
    if post is None:
        raise HTTPException(404, "That post isn't in your hub any more.")
    fields = {"platform": platform, "link": link}
    try:
        await db().social_posts.insert_one({"_id": new_id(), "owner_id": ws, "post_ref": ref, "source": ref.split(":", 1)[0],
                                            "hook": str(post.get("hook") or post.get("caption") or "")[:140], "posted_at": at or now(), "by": actor, **fields})
        await track("social_posted", ws, source=ref.split(":", 1)[0], platform=platform)
    except DuplicateKeyError:
        await db().social_posts.update_one({"owner_id": ws, "post_ref": ref}, {"$set": {k: v for k, v in fields.items() if v}})
    await _sync_calendar(ws, ref, True)
    dashboard_cache.bust(ws)
    return await db().social_posts.find_one({"owner_id": ws, "post_ref": ref})


async def unmark(ws: str, ref: str) -> None:
    await db().social_posts.delete_one({"owner_id": ws, "post_ref": ref})
    await _sync_calendar(ws, ref, False)
    dashboard_cache.bust(ws)


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


async def summary(ws: str, t: date | None = None) -> dict:
    """Posts this week and month (by platform), the weekly streak, and the next post the hub has written."""
    t = t or rhythm.today_ist()
    month_start = t.replace(day=1)
    since = ist_midnight(_week_start(t) - timedelta(weeks=26))
    log = [p async for p in db().social_posts.find({"owner_id": ws, "posted_at": {"$gte": since}}, {"posted_at": 1, "platform": 1})]
    days = [(p, ist_day(p["posted_at"])) for p in log]
    week = [p for p, d in days if d >= _week_start(t)]
    month = [p for p, d in days if d >= month_start]
    by_platform: dict[str, int] = {}
    for p in month:
        k = p.get("platform") or "Not said"
        by_platform[k] = by_platform.get(k, 0) + 1
    weeks_with = {_week_start(d) for _, d in days}
    streak, w = 0, _week_start(t)
    if w not in weeks_with:      # this week isn't over: the streak runs to last week
        w -= timedelta(weeks=1)
    while w in weeks_with:
        streak += 1
        w -= timedelta(weeks=1)
    created = 0
    cal = await db().calendars.find_one({"_id": f"{ws}:{t.strftime('%Y-%m')}"})
    if cal:
        created += len(cal.get("posts") or [])
    created += sum([len(x.get("posts") or []) async for x in db().weeks.find({"ws": ws, "start": {"$gte": month_start.isoformat()}}, {"posts": 1})])
    created += await db().sm_posts.count_documents({"owner_id": ws, "month": t.strftime("%Y-%m")})   # v2.3 Social media page
    nxt = next(({"date": p["date"], "hook": p.get("hook", ""), "occasion": p.get("occasion", "")}
                for p in (cal or {}).get("posts") or [] if p["date"] >= t.isoformat() and not p.get("posted")), None)
    return {"week": len(week), "month": len(month), "by_platform": by_platform, "streak_weeks": streak, "created_month": created, "next": nxt}


class MarkIn(BaseModel):
    ref: str = Field(max_length=80)
    platform: str = Field(default="", max_length=30)
    link: str = Field(default="", max_length=500)


def _check_ref(ctx: Ctx, ref: str) -> None:
    if not REF.match(ref):
        raise HTTPException(400, "That isn't a post reference.")
    ctx.require(SOURCE_FEATURE[ref.split(":", 1)[0]])


@router.post("/posted")
async def mark_posted(body: MarkIn, ctx: Ctx = Depends(get_ctx)):
    _check_ref(ctx, body.ref)
    if body.platform and body.platform not in PLATFORMS:
        raise HTTPException(400, f"Platform is one of: {', '.join(PLATFORMS)}")
    link = body.link.strip()
    if link and not re.match(r"^https?://[^\s]+$", link):
        raise HTTPException(400, "The link should start with https://")
    p = await mark(ctx.ws, ctx.actor, body.ref, body.platform, link)
    return {"ref": p["post_ref"], "platform": p.get("platform", ""), "link": p.get("link", ""), "posted_at": p["posted_at"].isoformat()}


@router.delete("/posted")
async def unmark_posted(ref: str, ctx: Ctx = Depends(get_ctx)):
    _check_ref(ctx, ref)
    await unmark(ctx.ws, ref)
    return {"ok": True}


@router.get("/log")
async def get_log(ctx: Ctx = Depends(get_ctx)):
    _can_see(ctx)
    items = [{"ref": p["post_ref"], "source": p.get("source"), "platform": p.get("platform", ""), "link": p.get("link", ""),
              "hook": p.get("hook", ""), "posted_at": p["posted_at"].isoformat()}
             async for p in db().social_posts.find({"owner_id": ctx.ws}).sort("posted_at", -1).limit(60)]
    return {"summary": await summary(ctx.ws), "items": items, "platforms": PLATFORMS}


@router.get("/refs")
async def posted_refs(prefix: str, ctx: Ctx = Depends(get_ctx)):
    """Which posts under a prefix (cal:2026-11, week:2026-W45, out:<id>) are already marked, for the buttons."""
    _can_see(ctx)
    if not re.match(r"^(cal|week|out):[A-Za-z0-9_-]{4,40}$", prefix):
        raise HTTPException(400, "That isn't a post reference.")
    return {p["post_ref"]: {"platform": p.get("platform", ""), "link": p.get("link", "")}
            async for p in db().social_posts.find({"owner_id": ctx.ws, "post_ref": {"$regex": f"^{re.escape(prefix)}:"}})}


async def ensure_indexes() -> None:
    await db().social_posts.create_index([("owner_id", 1), ("post_ref", 1)], unique=True)
    await db().social_posts.create_index([("owner_id", 1), ("posted_at", -1)])
