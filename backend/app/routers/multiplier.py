"""
Revenue Multiplier ideas (v2.3, spec §8): 15 growth ideas for each of the 3 levers — more customers, a higher average value,
buying more often — written for THIS business from the Knowledge Hub, their website, their lead magnet and the competitor
summary (app/growth_context.py). The calculator and the 10×10×10 pledge stay on /api/levers (routers/program.py).

- One AI token per lever (15 ideas); "all 45" is three of those. The cost is shown before each.
- Each idea: title, why it fits (naming its source), the source, a first step, effort and impact (low / medium / high — never a
  rupee promise), and the OwnerDesk feature that helps (with its page), where one fits.
- The owner ticks an idea "doing" or "done". Ideas are kept per calendar month (India time); writing a lever again keeps the
  ones the owner is doing or has done, and fills the rest.
- Checked before the owner sees them: an idea with a rupee amount or a percentage, a web address, an email or a phone number,
  or 6+ words in a row from the competitor's site, is dropped. Fewer than 10 left = the token is given back.

growth_ideas {_id: "{ws}:{YYYY-MM}", owner_id, month, levers {customers|value|frequency: {ideas [...], made_at}}, updated_at}
"""
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import growth_context as gc, plans
from ..agents import rhythm
from ..ai import v23_sales_tasks as t
from ..competitor_watch import has_contact
from ..context import Ctx, feature
from ..db import db, now
from ..services import new_id, run_ai, track
from .hub import profile_of

router = APIRouter(prefix="/api/multiplier")
read_ctx = feature("revenue_levers", "manager")
PERCENT = re.compile(r"\d+(\.\d+)?\s?%")
# the OwnerDesk features an idea may point to (shown only when switched on)
LINKABLE = ("website", "lead_magnet", "lead_ideas", "social_media", "brand_kit", "inquiry_desk", "crm", "pipeline", "quotations",
            "money_owed", "sop_library", "key_person")
SOURCE_LABEL = {"knowledge": "your Knowledge Hub", "website": "your website", "lead_magnet": "your lead magnet",
                "competitor": "your competitor's site", "business_type": "businesses like yours"}
MIN_GOOD = 10


def month_now() -> str:
    return rhythm.today_ist().strftime("%Y-%m")


def linkable() -> dict[str, str]:
    return {k: plans.label_of(k) for k in LINKABLE if k in plans.META and plans.META[k]["on"] and plans.META[k]["path"]}


def shape(raw: dict, ctx: dict, allowed: dict[str, str]) -> list[dict]:
    """The AI's ideas → the ones safe to show. Raises when fewer than 10 survive (the token is given back)."""
    own_phone = (ctx.get("profile") or {}).get("whatsapp", "")
    valid_src = {"knowledge", "business_type"} | ({"website"} if ctx.get("site_text") else set()) \
        | ({"lead_magnet"} if ctx.get("magnet") else set()) | ({"competitor"} if ctx.get("competitor_text") else set())
    out, seen = [], set()
    for x in (raw.get("ideas") or []) if isinstance(raw, dict) else []:
        if not isinstance(x, dict):
            continue
        title = re.sub(r"\s+", " ", str(x.get("title") or "")).strip()[:120]
        why = re.sub(r"\s+", " ", str(x.get("why") or "")).strip()[:300]
        step = re.sub(r"\s+", " ", str(x.get("first_step") or "")).strip()[:300]
        if not title or not why or title.lower() in seen:
            continue
        text = f"{title} {why} {step}"
        if gc.RUPEE.search(text) or PERCENT.search(text) or has_contact(text, own_phone) or gc.copies(text, ctx.get("competitor_shingles") or set()):
            continue   # a rupee promise, a percentage, contact details, or the competitor's own words: dropped
        src = x.get("source") if x.get("source") in valid_src else ("business_type" if x.get("source") == "business_type" else "knowledge")
        feat = x.get("feature") if x.get("feature") in allowed else ""
        seen.add(title.lower())
        out.append({"key": new_id()[:10], "title": title, "why": why, "source": src, "first_step": step,
                    "effort": x.get("effort") if x.get("effort") in t.EFFORT else "medium",
                    "impact": x.get("impact") if x.get("impact") in t.EFFORT else "medium", "feature": feat, "status": "", "status_at": None})
    if len(out) < MIN_GOOD:
        raise ValueError(f"only {len(out)} usable ideas")
    return out[:15]


def _idea_view(i: dict, owner: dict) -> dict:
    feat = i.get("feature") or ""
    m = plans.META.get(feat) if feat else None
    return {**i, "status_at": i["status_at"].isoformat() if i.get("status_at") else None,
            "source_label": SOURCE_LABEL.get(i.get("source"), "your Knowledge Hub"),
            "feature_label": m["label"] if m else None, "path": m["path"] if m else None,
            "locked": bool(m and not plans.has(owner, feat))}


async def view(ws: str, owner: dict) -> dict:
    month = month_now()
    doc = await db().growth_ideas.find_one({"_id": f"{ws}:{month}"}) or {}
    levers = []
    for key, (label, meaning) in t.LEVERS.items():
        lv = (doc.get("levers") or {}).get(key) or {}
        ideas = [_idea_view(i, owner) for i in lv.get("ideas") or []]
        levers.append({"key": key, "label": label, "meaning": meaning, "ideas": ideas,
                       "made_at": lv["made_at"].isoformat() if lv.get("made_at") else None,
                       "doing": sum(1 for i in ideas if i["status"] == "doing"), "done": sum(1 for i in ideas if i["status"] == "done")})
    past = []
    async for d in db().growth_ideas.find({"owner_id": ws, "month": {"$ne": month}}).sort("month", -1).limit(6):
        all_ = [i for lv in (d.get("levers") or {}).values() for i in lv.get("ideas") or []]
        past.append({"month": d["month"], "ideas": len(all_), "doing": sum(1 for i in all_ if i.get("status") == "doing"),
                     "done": sum(1 for i in all_ if i.get("status") == "done")})
    ctx = await gc.gather(ws)
    return {"month": month, "levers": levers, "tokens_per_lever": 1, "tokens_all": len(t.LEVERS),
            "total": sum(len(lv["ideas"]) for lv in levers), "doing": sum(lv["doing"] for lv in levers), "done": sum(lv["done"] for lv in levers),
            "sources": {"knowledge": bool((ctx["profile"] or {}).get("industry")), "website": bool(ctx["site_text"]),
                        "lead_magnet": bool(ctx["magnet"]), "competitor": bool(ctx["competitor_text"])},
            "past": past}


@router.get("")
async def get_ideas(ctx: Ctx = Depends(read_ctx)):
    return await view(ctx.ws, ctx.owner)


@router.post("/{lever}")
async def write_lever(lever: str, ctx: Ctx = Depends(read_ctx)):
    """15 ideas for one lever (1 AI token). Ideas the owner is doing or has done stay; the rest are written fresh."""
    if lever not in t.LEVERS:
        raise HTTPException(404, "Pick one of the three levers.")
    await profile_of(ctx.ws)      # the Knowledge Hub must say what the business does
    g = await gc.gather(ctx.ws)
    allowed = linkable()
    month = month_now()
    key = f"{ctx.ws}:{month}"
    ideas = await run_ai(ctx.owner, "multiplier_ideas", t.multiplier_ideas(g, lever, allowed), f"Revenue Multiplier — {t.LEVERS[lever][0]}",
                         save_output=False, check=lambda r: shape(r, g, allowed))
    doc = await db().growth_ideas.find_one({"_id": key}) or {}
    kept = [i for i in ((doc.get("levers") or {}).get(lever) or {}).get("ideas") or [] if i.get("status") in ("doing", "done")]
    taken = {i["title"].lower() for i in kept}
    fresh = [i for i in ideas if i["title"].lower() not in taken][:max(15 - len(kept), 0)]
    await db().growth_ideas.update_one({"_id": key}, {"$set": {f"levers.{lever}": {"ideas": kept + fresh, "made_at": now(), "by": ctx.actor},
                                                               "updated_at": now()},
                                                      "$setOnInsert": {"owner_id": ctx.ws, "month": month, "created_at": now()}}, upsert=True)
    await track("multiplier_ideas", ctx.ws, lever=lever, kept=len(kept))
    return await view(ctx.ws, ctx.owner)


class StatusIn(BaseModel):
    status: str = Field(pattern="^(|doing|done)$")


@router.post("/{lever}/{idea_key}/status")
async def set_status(lever: str, idea_key: str, body: StatusIn, ctx: Ctx = Depends(read_ctx)):
    if lever not in t.LEVERS:
        raise HTTPException(404, "Not found")
    key = f"{ctx.ws}:{month_now()}"
    doc = await db().growth_ideas.find_one({"_id": key}) or {}
    ideas = ((doc.get("levers") or {}).get(lever) or {}).get("ideas") or []
    n = next((i for i, x in enumerate(ideas) if x.get("key") == idea_key), None)
    if n is None:
        raise HTTPException(404, "That idea isn't in this month's list.")
    await db().growth_ideas.update_one({"_id": key}, {"$set": {f"levers.{lever}.ideas.{n}.status": body.status,
                                                               f"levers.{lever}.ideas.{n}.status_at": now() if body.status else None}})
    return await view(ctx.ws, ctx.owner)


async def ensure_indexes() -> None:
    await db().growth_ideas.create_index([("owner_id", 1), ("month", -1)])
