"""
Membership's weekly rhythm, the content calendar, the monthly member call and the cohort board.
"""
import re
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import festivals, plans
from ..agents import rhythm
from ..context import Ctx, feature
from ..db import IST, db, now, public
from ..security import require_admin
from ..services import new_id, notify, track

router = APIRouter(prefix="/api")


# ───────────────────────── This week ─────────────────────────
class CheckinIn(BaseModel):
    leads: int = Field(default=0, ge=0, le=100_000)
    sales: int = Field(default=0, ge=0, le=100_000)
    revenue: float = Field(default=0, ge=0, le=1e11)
    wins: str = Field(default="", max_length=600)


def _week_view(w: dict | None) -> dict | None:
    if not w:
        return None
    out = public(w)
    out["score"] = rhythm.week_score(w)
    return out


async def _this_week(ws: str) -> dict | None:
    return await db().weeks.find_one({"_id": f"{ws}:{rhythm.week_key(rhythm.today_ist())}"})


@router.get("/week")
async def get_week(ctx: Ctx = Depends(feature("week", "manager"))):
    w = await _this_week(ctx.ws)
    today = await rhythm.todays_action(ctx.owner, w)
    t = rhythm.today_ist()
    post = None
    if w and w.get("posts"):
        post = w["posts"][min(t.weekday(), len(w["posts"]) - 1)]
    cal = await db().calendars.find_one({"_id": f"{ctx.ws}:{t.strftime('%Y-%m')}"})
    cal_post = next((p for p in (cal or {}).get("posts") or [] if p["date"] == t.isoformat()), None)
    last = await db().weeks.find_one({"ws": ctx.ws, "_id": {"$ne": f"{ctx.ws}:{rhythm.week_key(t)}"}}, sort=[("start", -1)])
    return {"week": _week_view(await _this_week(ctx.ws)), "today": today, "today_post": cal_post or post, "streak": await rhythm.streak(ctx.ws),
            "friday": t.weekday() >= 4, "date": t.isoformat(), "last_week_score": rhythm.week_score(last) if last else None,
            "upcoming": festivals.upcoming(t, 3)}


@router.post("/week/plan")
async def plan_week(ctx: Ctx = Depends(feature("week", "manager"))):
    w = await rhythm.build_week(ctx.owner, ctx.actor)
    await track("week_planned", ctx.ws)
    return _week_view(w)


@router.post("/week/today/done")
async def today_done(ctx: Ctx = Depends(feature("week", "manager"))):
    w = await _this_week(ctx.ws)
    t = rhythm.today_ist().isoformat()
    if w:
        item = await rhythm.todays_action(ctx.owner, w)
        await db().weeks.update_one({"_id": w["_id"]}, {"$set": {f"days.{t}": {**item, "done": True, "at": now()}}})
    s = await rhythm.tick_day(ctx.ws)
    await track("daily_done", ctx.ws, streak=s.get("current"))
    return {"streak": await rhythm.streak(ctx.ws), "week": _week_view(await _this_week(ctx.ws))}


@router.post("/week/actions/{n}")
async def toggle_action(n: int, ctx: Ctx = Depends(feature("week", "manager"))):
    w = await _this_week(ctx.ws)
    if not w or not 0 <= n < len(w.get("actions") or []):
        raise HTTPException(404, "Not found")
    done = not w["actions"][n].get("done")
    await db().weeks.update_one({"_id": w["_id"]}, {"$set": {f"actions.{n}.done": done, f"actions.{n}.done_at": now() if done else None}})
    return _week_view(await _this_week(ctx.ws))


@router.post("/week/checkin")
async def checkin(body: CheckinIn, ctx: Ctx = Depends(feature("week", "manager"))):
    w = await _this_week(ctx.ws)
    if not w:
        raise HTTPException(400, "Plan this week first.")
    await db().weeks.update_one({"_id": w["_id"]}, {"$set": {"checkin": {**body.model_dump(), "wins": body.wins.strip(), "at": now(), "by": ctx.actor}}})
    w = await _this_week(ctx.ws)
    await track("week_checkin", ctx.ws, score=rhythm.week_score(w))
    return _week_view(w)


@router.get("/week/history")
async def week_history(ctx: Ctx = Depends(feature("week", "manager"))):
    return [{"week": w["week"], "start": w["start"], "score": rhythm.week_score(w), "checkin": w.get("checkin")}
            async for w in db().weeks.find({"ws": ctx.ws}).sort("start", -1).limit(12)]


# ───────────────────────── Content calendar ─────────────────────────
MONTH = re.compile(r"^(20\d\d)-(0[1-9]|1[0-2])$")


class CalendarIn(BaseModel):
    month: str = Field(pattern=r"^20\d\d-(0[1-9]|1[0-2])$")
    focus: str = Field(default="", max_length=160)


@router.get("/calendar/{month}")
async def get_calendar(month: str, ctx: Ctx = Depends(feature("content_calendar"))):
    if not MONTH.match(month):
        raise HTTPException(400, "Use a month like 2026-11")
    y, m = int(month[:4]), int(month[5:])
    cal = await db().calendars.find_one({"_id": f"{ctx.ws}:{month}"})
    return {"month": month, "label": date(y, m, 1).strftime("%B %Y"), "calendar": public(cal) if cal else None,
            "festivals": festivals.in_month(y, m), "slots": len(rhythm.posting_days(y, m))}


@router.post("/calendar")
async def make_calendar(body: CalendarIn, ctx: Ctx = Depends(feature("content_calendar", "manager"))):
    y, m = int(body.month[:4]), int(body.month[5:])
    t = rhythm.today_ist()
    if (y, m) < (t.year, t.month) or (y - t.year) * 12 + (m - t.month) > 2:
        raise HTTPException(400, "Write the calendar for this month or one of the next two.")
    cal = await rhythm.write_calendar(ctx.owner, y, m, body.focus.strip(), ctx.actor)
    return {"month": body.month, "label": cal["label"], "calendar": public(cal), "festivals": festivals.in_month(y, m)}


@router.post("/calendar/{month}/posted/{day}")
async def mark_posted(month: str, day: str, ctx: Ctx = Depends(feature("content_calendar"))):
    cal = await db().calendars.find_one({"_id": f"{ctx.ws}:{month}"})
    if not cal:
        raise HTTPException(404, "Not found")
    post = next((p for p in cal["posts"] if p["date"] == day), None)
    if not post:
        raise HTTPException(404, "Not found")
    from . import social   # the posting log is the one record; the calendar's tick follows it
    ref = f"cal:{month}:{day}"
    if post.get("posted"):
        await social.unmark(ctx.ws, ref)
    else:
        await social.mark(ctx.ws, ctx.actor, ref)
    return {"ok": True, "posted": not post.get("posted")}


# ───────────────────────── Member call ─────────────────────────
class CallIn(BaseModel):
    title: str = Field(min_length=3, max_length=120)
    starts_at: datetime
    join_link: str = Field(default="", max_length=500)
    tier: str = Field(default="lite", pattern="^(free|lite|program|running|growth|office)$")
    notes: str = Field(default="", max_length=1000)


class QuestionIn(BaseModel):
    text: str = Field(min_length=5, max_length=600)


def _call_view(c: dict, mine: list | None = None) -> dict:
    out = public(c)
    out["tier_name"] = plans.PLANS[c.get("tier", "lite")]["name"]
    if mine is not None:
        out["my_questions"] = mine
    return out


@router.get("/member-call")
async def member_call(ctx: Ctx = Depends(feature("member_call", "owner"))):
    rank = plans.rank(plans.effective_plan(ctx.owner))
    upcoming, past = [], []
    async for c in db().member_calls.find().sort("starts_at", -1).limit(30):
        if plans.rank(c.get("tier", "lite")) > rank:
            continue
        mine = [public(q) async for q in db().call_questions.find({"call_id": c["_id"], "ws": ctx.ws}).sort("at", 1)]
        (upcoming if c["starts_at"] > now() - timedelta(hours=3) else past).append(_call_view(c, mine))
    return {"upcoming": sorted(upcoming, key=lambda c: c["starts_at"]), "past": past[:12]}


@router.post("/member-call/{call_id}/questions")
async def ask(call_id: str, body: QuestionIn, ctx: Ctx = Depends(feature("member_call", "owner"))):
    c = await db().member_calls.find_one({"_id": call_id})
    if not c or plans.rank(c.get("tier", "lite")) > plans.rank(plans.effective_plan(ctx.owner)):
        raise HTTPException(404, "Not found")
    if c["starts_at"] < now():
        raise HTTPException(400, "This call has started. Send your question for the next one.")
    if await db().call_questions.count_documents({"call_id": call_id, "ws": ctx.ws}) >= 3:
        raise HTTPException(400, "Up to 3 questions per call, so everyone gets a turn.")
    b = await db().businesses.find_one({"owner_id": ctx.ws}, {"name": 1}) or {}
    await db().call_questions.insert_one({"_id": new_id(), "call_id": call_id, "ws": ctx.ws, "business": b.get("name", ""),
                                          "text": body.text.strip(), "at": now()})
    return {"ok": True}


@router.get("/admin/member-calls")
async def admin_calls(_: dict = Depends(require_admin)):
    out = []
    async for c in db().member_calls.find().sort("starts_at", -1).limit(50):
        v = _call_view(c)
        v["questions"] = [public(q) async for q in db().call_questions.find({"call_id": c["_id"]}).sort("at", 1)]
        out.append(v)
    return out


@router.post("/admin/member-calls")
async def admin_add_call(body: CallIn, admin: dict = Depends(require_admin)):
    starts = body.starts_at if body.starts_at.tzinfo else body.starts_at.replace(tzinfo=IST)
    doc = {"_id": new_id(), **body.model_dump(), "starts_at": starts.astimezone(timezone.utc), "created_by": admin["_id"], "created_at": now()}
    await db().member_calls.insert_one(doc)
    async for u in db().users.find({"team_of": {"$exists": False}, "disabled": {"$ne": True}}, {"plan": 1, "trial": 1, "sub": 1, "access": 1, "grants": 1, "phone": 1}).limit(20000):
        if plans.has(u, "member_call") and plans.rank(plans.effective_plan(u)) >= plans.rank(body.tier):
            await notify(u["_id"], f"Member call: {body.title} on {starts.astimezone(IST).strftime('%d %b, %I:%M %p')}. Send your questions on Member call.")
    return _call_view(doc)


@router.delete("/admin/member-calls/{call_id}")
async def admin_delete_call(call_id: str, _: dict = Depends(require_admin)):
    await db().member_calls.delete_one({"_id": call_id})
    await db().call_questions.delete_many({"call_id": call_id})
    return {"ok": True}


# ───────────────────────── Cohort board ─────────────────────────
class BoardIn(BaseModel):
    hide: bool


@router.get("/board")
async def board(ctx: Ctx = Depends(feature("cohort_board", "owner"))):
    cohort = ctx.owner.get("cohort")
    week = rhythm.week_key(rhythm.today_ist())
    rows = []
    if cohort:
        async for u in db().users.find({"cohort": cohort, "team_of": {"$exists": False}, "disabled": {"$ne": True}, "board_hide": {"$ne": True}},
                                       {"plan": 1, "trial": 1, "sub": 1, "access": 1, "grants": 1, "phone": 1}).limit(2000):
            if not plans.has(u, "week"):
                continue
            b = await db().businesses.find_one({"owner_id": u["_id"]}, {"name": 1, "city": 1}) or {}
            w = await db().weeks.find_one({"_id": f"{u['_id']}:{week}"})
            s = await rhythm.streak(u["_id"])
            rows.append({"me": u["_id"] == ctx.ws, "business": b.get("name") or "An owner", "city": b.get("city", ""),
                         "streak": s["current"], "best": s["best"], "score": rhythm.week_score(w)})
    rows.sort(key=lambda r: (-r["score"], -r["streak"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    c = await db().cohorts.find_one({"code": cohort}) if cohort else None
    return {"cohort": {"code": cohort, "name": (c or {}).get("name") or cohort} if cohort else None, "rows": rows[:30],
            "me": next((r for r in rows if r["me"]), None), "hidden": bool(ctx.owner.get("board_hide")), "week": week}


@router.post("/board/visibility")
async def board_visibility(body: BoardIn, ctx: Ctx = Depends(feature("cohort_board", "owner"))):
    await db().users.update_one({"_id": ctx.ws}, {"$set": {"board_hide": body.hide}})
    return {"hidden": body.hide}


async def ensure_indexes() -> None:
    await db().weeks.create_index([("ws", 1), ("start", -1)])
    await db().calendars.create_index([("ws", 1), ("month", -1)])
    await db().member_calls.create_index([("starts_at", -1)])
    await db().call_questions.create_index([("call_id", 1), ("ws", 1)])
    await db().users.create_index("cohort")
