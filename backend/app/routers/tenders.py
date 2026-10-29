"""
Tender finder (v2.3, Data engine) — see app/tenders.py for where the tenders come from and how the help is built.

Owner and managers (feature tender_finder):
    GET  /api/tenders                     the search words, the last results, what the Knowledge Hub says about their papers
    POST /api/tenders/search              {words?, force?} save new words and/or check GeM again (cached CACHE_HOURS)
    POST /api/tenders/words               suggest the words a government buyer would use (1 AI token)
    GET  /api/tenders/{bid_id}/help       the help already built for this tender (free)
    POST /api/tenders/{bid_id}/help       {again?} read the tender's document and build the help (1 AI token)
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import tenders as tn
from ..ai import tender_tasks as tasks
from ..context import Ctx, feature
from ..db import db, now
from ..security import rate_limit
from ..services import run_ai, runs_status, track
from .hub import profile_of

router = APIRouter(prefix="/api/tenders")
can_use = feature("tender_finder", "manager")
COSTS = {"words": 1, "help": 1}
SEARCHES_PER_HOUR = 0          # GeM checks per business per hour; 0 = no limit (off while the GeM relay is being tried out)


async def ensure_indexes():
    await db().tender_help.create_index([("ws", 1), ("at", -1)])


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _row(b: dict, helped: set) -> dict:
    return {**{k: _iso(v) for k, v in b.items()}, "days_left": tn.days_left(b.get("ends_at")), "helped": b["bid_id"] in helped}


async def _search_doc(ctx: Ctx) -> dict:
    s = await db().tender_searches.find_one({"_id": ctx.ws})
    if not s:
        b = await profile_of(ctx.ws)
        s = {"_id": ctx.ws, "words": tn.words_from_profile(b), "words_from": "profile", "results": [], "checked_at": None, "error": ""}
        await db().tender_searches.insert_one(s)
    return s


async def _view(ctx: Ctx, s: dict) -> dict:
    b = await profile_of(ctx.ws)
    helped = {h["bid_id"] async for h in db().tender_help.find({"ws": ctx.ws}, {"bid_id": 1})}
    rows = [r for r in s.get("results") or [] if not r.get("ends_at") or r["ends_at"] > now()]
    return {"words": s.get("words") or [], "words_from": s.get("words_from") or "profile", "max_words": tn.MAX_WORDS,
            "results": [_row(r, helped) for r in rows], "checked_at": _iso(s.get("checked_at")),
            "stale": tn.stale(s.get("checked_at")), "error": s.get("error") or "", "ready": tn.readiness(b),
            "costs": COSTS, "tokens_left": (await runs_status(ctx.owner))["left"]}


@router.get("")
async def tenders(ctx: Ctx = Depends(can_use)):
    return await _view(ctx, await _search_doc(ctx))


class SearchIn(BaseModel):
    words: list[str] | None = Field(default=None, max_length=10)
    force: bool = False


@router.post("/search")
async def search(body: SearchIn, ctx: Ctx = Depends(can_use)):
    s = await _search_doc(ctx)
    patch = {}
    if body.words is not None:
        words = tn.clean_words(body.words)
        if not words:
            raise HTTPException(400, "Add at least one search word, like “office furniture” or “housekeeping services”.")
        if words != s.get("words"):
            patch.update(words=words, words_from="owner")
    words = patch.get("words") or s.get("words") or []
    if not words:
        raise HTTPException(400, "Add at least one search word, like “office furniture” or “housekeeping services”.")
    if patch or body.force or tn.stale(s.get("checked_at")):
        if SEARCHES_PER_HOUR:
            await rate_limit(f"tenders:{ctx.ws}", SEARCHES_PER_HOUR, 3600,
                             "You've checked GeM a few times this hour. Your last results are below — try again in a little while.")
        try:
            results = await tn.search(words)
            patch.update(results=results, checked_at=now(), error="")
        except tn.TenderError as e:
            patch.update(error=str(e))
        await db().tender_searches.update_one({"_id": ctx.ws}, {"$set": patch})
        await track("tenders_searched", ctx.ws, words=len(words), found=len(patch.get("results") or []))
        s = await db().tender_searches.find_one({"_id": ctx.ws})
    return await _view(ctx, s)


@router.post("/words")
async def suggest_words(ctx: Ctx = Depends(can_use)):
    s = await _search_doc(ctx)
    b = await profile_of(ctx.ws)
    words = await run_ai(ctx.owner, "tenders:words", tasks.search_words(b), "Tender search words", save_output=False, check=tn.shape_words)
    await db().tender_searches.update_one({"_id": s["_id"]}, {"$set": {"words": words, "words_from": "ai"}})
    return await _view(ctx, await db().tender_searches.find_one({"_id": s["_id"]}))


def _help_view(h: dict) -> dict:
    return {"bid_id": h["bid_id"], "bid_number": h["bid_number"], "title": h["title"], "url": h["url"], "facts": h["facts"],
            "help": h["help"], "buyer_emails": h.get("buyer_emails") or [], "steps": tn.GEM_STEPS, "links": tn.LINKS,
            "ready": h.get("ready") or [], "at": _iso(h["at"]), "costs": COSTS}


@router.get("/{bid_id}/help")
async def get_help(bid_id: int, ctx: Ctx = Depends(can_use)):
    h = await db().tender_help.find_one({"_id": f"{ctx.ws}:{bid_id}"})
    if not h:
        raise HTTPException(404, "No help for this tender yet.")
    return _help_view(h)


class HelpIn(BaseModel):
    again: bool = False


@router.post("/{bid_id}/help")
async def build_help(bid_id: int, body: HelpIn | None = None, ctx: Ctx = Depends(can_use)):
    key = f"{ctx.ws}:{bid_id}"
    h = await db().tender_help.find_one({"_id": key})
    if h and not (body and body.again):
        return _help_view(h)
    s = await _search_doc(ctx)
    bid = next((r for r in s.get("results") or [] if r.get("bid_id") == bid_id), None) or (h and {
        k: h[k] for k in ("bid_id", "bid_number", "title", "url")})
    if not bid:
        raise HTTPException(404, "This tender isn't in your results any more. Search again to find it.")
    b = await profile_of(ctx.ws)
    try:
        text = await tn.bid_text(bid["url"])
    except tn.TenderError as e:
        raise HTTPException(502, str(e))
    facts = tn.facts(text)
    ready = tn.readiness(b)
    result = await run_ai(ctx.owner, "tenders:help", tasks.help_to_apply(b, {**bid, "organisation": facts.get("organisation", "")}, facts,
                                                                          tn.for_ai(text), ready),
                          f"Help to apply — {bid['bid_number']}", save_output=False, check=tn.shape_help)
    doc = {"_id": key, "ws": ctx.ws, "bid_id": bid_id, "bid_number": bid["bid_number"], "title": bid["title"], "url": bid["url"],
           "facts": facts, "help": result, "buyer_emails": tn.buyer_emails(text), "ready": ready, "at": now(), "by": ctx.actor}
    await db().tender_help.replace_one({"_id": key}, doc, upsert=True)
    await track("tender_help", ctx.ws, verdict=result["fit"]["verdict"])
    return _help_view(doc)
