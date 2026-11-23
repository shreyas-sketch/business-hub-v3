"""
Call coach (v2.4, Sales engine): feature `call_coach` (paid members; the owner or a manager). The logic is in app/call_coach.py.

    GET    /api/call-coach                       the page: team list, calls left this month, whether recordings work and
                                                 their size limit, written-out calls waiting to be checked, this month so far
    POST   /api/call-coach/members               {name}                      add a team member (just a name, no login)
    PATCH  /api/call-coach/members/{id}          {name}                      rename (their past calls follow)
    DELETE /api/call-coach/members/{id}                                      remove (their past calls stay, under their name)
    POST   /api/call-coach/read                  {member_id, text}           who said what in a pasted call (no AI token)
    POST   /api/call-coach/recording?member_id=&ext=   the recording itself as the request body (an audio type):
                                                 written out and labelled, 1 AI token. Read into memory only — never saved.
    GET    /api/call-coach/drafts/{id}           a written-out call waiting to be checked
    DELETE /api/call-coach/drafts/{id}
    POST   /api/call-coach/score                 {member_id, text} or {member_id, lines, draft_id?}: 1 AI token
    GET    /api/call-coach/calls?member_id=&before=   scored calls, newest first (30 at a time)
    GET    /api/call-coach/calls/{id}            one scored call (the AI team page links to /call-coach?call=<id>)
    GET    /api/call-coach/people                each person's scores over time and trend; the team's weak spots this month
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .. import call_coach as cc
from .. import growth_core
from .. import transcribe as speech
from ..context import Ctx, feature
from ..db import db, now
from .hub import profile_of

router = APIRouter(prefix="/api/call-coach")
GATE = feature("call_coach", "manager")


class NameIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class ReadIn(BaseModel):
    member_id: str = Field(min_length=1, max_length=40)
    text: str = Field(min_length=1, max_length=cc.MAX_TEXT)


class LineIn(BaseModel):
    role: str = Field(pattern="^(customer|salesperson)$")
    text: str = Field(default="", max_length=4000)


class ScoreIn(BaseModel):
    member_id: str = Field(min_length=1, max_length=40)
    text: str | None = Field(default=None, max_length=cc.MAX_TEXT)
    lines: list[LineIn] | None = Field(default=None, max_length=cc.MAX_LINES)
    draft_id: str | None = Field(default=None, max_length=40)


@router.get("")
async def page(ctx: Ctx = Depends(GATE)):
    start = cc.month_start()
    month = [s async for s in db().gym_sessions.find({"ws": ctx.ws, **cc.CALLS, "scored_at": {"$gte": start}}, {"score.overall": 1})]
    scores = [float((s.get("score") or {}).get("overall")) for s in month if (s.get("score") or {}).get("overall") is not None]
    recent = [cc.call_summary(s) async for s in db().gym_sessions.find({"ws": ctx.ws, **cc.CALLS}).sort("scored_at", -1).limit(10)]
    on = speech.available()
    return {"members": await cc.members(ctx.ws), "limit": await growth_core.left(ctx.owner, "calls_scored"),
            "recording": {"on": on, "max_mb": cc.max_bytes() // cc.MB, "minutes": 15, "note": "" if on else cc.NO_SPEECH},
            "drafts": await cc.drafts(ctx.ws), "recent": recent, "costs": cc.COST,
            "month": {"label": start.strftime("%B %Y"), "calls": len(scores), "average": round(sum(scores) / len(scores), 1) if scores else None},
            "lines": {"allowed": cc.ALLOWED_LINE, "not_kept": cc.NOT_KEPT}}


# ───────────────────────── team members ─────────────────────────
@router.post("/members")
async def add_member(body: NameIn, ctx: Ctx = Depends(GATE)):
    return {"member": await cc.add_member(ctx.ws, body.name, ctx.actor), "members": await cc.members(ctx.ws)}


@router.patch("/members/{mid}")
async def rename_member(mid: str, body: NameIn, ctx: Ctx = Depends(GATE)):
    return {"member": await cc.rename_member(ctx.ws, mid, body.name), "members": await cc.members(ctx.ws)}


@router.delete("/members/{mid}")
async def remove_member(mid: str, ctx: Ctx = Depends(GATE)):
    await cc.remove_member(ctx.ws, mid)
    return {"ok": True, "members": await cc.members(ctx.ws)}


# ───────────────────────── a pasted call ─────────────────────────
@router.post("/read")
async def read_paste(body: ReadIn, ctx: Ctx = Depends(GATE)):
    """Who said what, before scoring (no AI token): the lines as read, for the owner to check."""
    member = await cc.member_of(ctx.ws, body.member_id)
    got = cc.paste_turns(body.text, member["name"], [m["name"] for m in await cc.members(ctx.ws)])
    if not got["turns"]:
        raise HTTPException(400, "Paste the call first.")
    return {"lines": got["turns"], "unsure": got["unsure"], "labels": got["labels"]}


# ───────────────────────── a recording ─────────────────────────
@router.post("/recording")
async def recording(request: Request, member_id: str = Query(min_length=1, max_length=40), ext: str = Query(default="", max_length=8),
                    ctx: Ctx = Depends(GATE)):
    """The recording is the request body (not a form upload, which can be buffered to a file on disk), read into memory up to
    the size limit, written out, and dropped with the request. Only the written call is saved, as a draft to check."""
    member = await cc.member_of(ctx.ws, member_id)
    if not speech.available():
        raise HTTPException(503, cc.NO_SPEECH)
    mime = cc.audio_type(request.headers.get("content-type", ""), ext)
    if not mime:
        raise HTTPException(415, "Upload the call as an audio file: MP3, M4A, WAV, OGG, WEBM or FLAC.")
    limit = cc.max_bytes()
    too_big = HTTPException(413, f"That recording is bigger than {limit // cc.MB} MB. Upload a shorter part of the call — up to about 15 minutes.")
    try:
        declared = int(request.headers.get("content-length") or 0)
    except ValueError:
        declared = 0
    if declared > limit:
        raise too_big
    await cc.calls_left(ctx.owner)
    p = await profile_of(ctx.ws)
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > limit:
            raise too_big
    if not data:
        raise HTTPException(400, "That recording is empty. Choose the file again.")
    return {"draft": await cc.write_out(ctx.owner, ctx.actor, p, member, bytes(data), mime)}


@router.get("/drafts/{did}")
async def get_draft(did: str, ctx: Ctx = Depends(GATE)):
    return {"draft": cc.draft_view(await cc.draft_of(ctx.ws, did))}


@router.delete("/drafts/{did}")
async def drop_draft(did: str, ctx: Ctx = Depends(GATE)):
    await db().call_drafts.delete_one({"_id": did, "ws": ctx.ws})
    return {"ok": True}


# ───────────────────────── scoring ─────────────────────────
@router.post("/score")
async def score(body: ScoreIn, ctx: Ctx = Depends(GATE)):
    member = await cc.member_of(ctx.ws, body.member_id)
    draft = await cc.draft_of(ctx.ws, body.draft_id) if body.draft_id else None
    if body.lines is not None:
        turns, source = cc.merge([l.model_dump() for l in body.lines]), ("recording" if draft else "paste")
    elif draft and body.text is None:
        turns, source = cc.merge(draft.get("lines") or []), "recording"
    else:
        if not (body.text or "").strip():
            raise HTTPException(400, "Paste the call first.")
        got = cc.paste_turns(body.text, member["name"], [m["name"] for m in await cc.members(ctx.ws)])
        if got["unsure"]:
            names = " and ".join(f"“{n}”" for n in got["labels"][:3])
            raise HTTPException(400, f"We couldn't tell which of {names} is {member['name']}. Press “Check who said what” to set it, "
                                     f"or start {member['name']}'s lines with “Me:” and the customer's with “Customer:”.")
        turns, source = cc.merge(got["turns"]), "paste"
    cc.check_turns(turns, member["name"])
    p = await profile_of(ctx.ws)
    if draft:   # one score per written-out call, even after a double tap
        claimed = await db().call_drafts.find_one_and_update(
            {"_id": draft["_id"], "$or": [{"scoring_until": None}, {"scoring_until": {"$lt": now()}}]},
            {"$set": {"scoring_until": now() + timedelta(minutes=5)}})
        if not claimed:
            raise HTTPException(409, "This call is being scored already.")
    try:
        doc = await cc.score(ctx.owner, ctx.actor, p, member, turns, source)
    except BaseException:
        if draft:
            await db().call_drafts.update_one({"_id": draft["_id"]}, {"$set": {"scoring_until": None}})
        raise
    if draft:
        await db().call_drafts.delete_one({"_id": draft["_id"]})
    return {"call": cc.call_view(doc)}


# ───────────────────────── history and people ─────────────────────────
@router.get("/calls")
async def calls(member_id: str = Query(default="", max_length=40), before: str = Query(default="", max_length=40), ctx: Ctx = Depends(GATE)):
    q = {"ws": ctx.ws, **cc.CALLS}
    if member_id:
        q["member_id"] = member_id
    if before:
        try:
            q["scored_at"] = {"$lt": datetime.fromisoformat(before)}
        except ValueError:
            raise HTTPException(400, "Bad date")
    items = [cc.call_summary(s) async for s in db().gym_sessions.find(q, {"member": 1, "member_id": 1, "input": 1, "score.overall": 1,
                                                                            "score.fixes": 1, "scored_at": 1}).sort("scored_at", -1).limit(31)]
    return {"calls": items[:30], "more": len(items) > 30}


@router.get("/calls/{cid}")
async def one_call(cid: str, ctx: Ctx = Depends(GATE)):
    s = await db().gym_sessions.find_one({"_id": cid, "ws": ctx.ws, **cc.CALLS})
    if not s:
        raise HTTPException(404, "That call isn't here. It may belong to another business.")
    return {"call": cc.call_view(s, await cc.filed_to(ctx.ws, cid))}


@router.get("/people")
async def people(ctx: Ctx = Depends(GATE)):
    return await cc.people(ctx.ws)


async def ensure_indexes() -> None:
    d = db()
    await d.call_team.create_index([("ws", 1), ("removed_at", 1)])
    await d.call_drafts.create_index([("ws", 1), ("at", -1)])
    await d.call_drafts.create_index("at", expireAfterSeconds=cc.DRAFT_DAYS * 86400)   # a written-out call nobody scored goes after 7 days
    await d.gym_sessions.create_index([("ws", 1), ("source", 1), ("scored_at", -1)])
