"""
Ways to get more leads (v2.1, 3.4) — a panel on Revenue levers. From the Business Brain (industry, city, offers) and the
owner's current lead sources, the AI picks 5–7 ideas, each with one first step and, where the hub does it, a link to that
feature. Only channels that fit the business type are offered to the AI, and anything else it returns is dropped.

lead_ideas {_id: "{ws}:{YYYY-MM}", owner_id, month, ideas [{key, channel, title, why, first_step, feature, path, tried, tried_at}]}
"""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import plans
from ..agents import rhythm
from ..ai import kit_tasks
from ..ai.provider import complete
from ..ai.tasks import RULES, _short, _v, profile_text
from ..context import Ctx, feature
from ..db import db, now
from ..services import run_ai, track
from .hub import profile_of

router = APIRouter(prefix="/api/levers/ideas")

ALL = ("trading", "clinic", "food", "services", "manufacturing")
# key: (label, hub feature or None, path, business types it fits)
CHANNELS = {
    "lead_magnet":    ("A free guide on your website that collects leads", "lead_magnet", "/lead-magnet", ("services", "clinic", "manufacturing", "trading")),
    "qr_stand":       ("A QR stand at your counter, site or stall", "launch_kit", "/launch-kit", ("trading", "clinic", "food", "services")),
    "google_profile": ("Google Business profile posts and review replies", "tool_google", "/tools?tool=google", ALL),
    "festival_posts": ("Festival and occasion posts, planned a month ahead", "content_calendar", "/calendar", ALL),
    "status_poster":  ("A WhatsApp Status poster every week", "brand_kit", "/brand-kit", ("trading", "clinic", "food", "services")),
    "referrals":      ("Referral requests to happy customers", "customer_desk", "/agents", ALL),
    "case_study":     ("Before-and-after stories from real jobs (with consent)", "tool_case_study", "/tools?tool=case_study", ("services", "manufacturing", "clinic")),
    "call_quiet":     ("Calling inquiries that went quiet", "tool_telecalling", "/tools?tool=telecalling", ("services", "trading", "manufacturing")),
    "networks":       ("Business networks and trade associations", None, None, ("services", "manufacturing", "trading")),
    "exhibitions":    ("Trade exhibitions and buyer meets", None, None, ("manufacturing", "trading")),
    "b2b_listing":    ("A listing on B2B marketplaces", None, None, ("manufacturing", "trading")),
    "delivery_apps":  ("Food delivery apps and their in-app offers", None, None, ("food",)),
    "society_stall":  ("A stall or demo in housing societies", None, None, ("services", "food", "clinic")),
    "doctor_referrals": ("Referrals from doctors and chemists nearby", None, None, ("clinic",)),
    "health_camp":    ("A free check-up camp with a housing society or office", None, None, ("clinic",)),
    "corporate_orders": ("Corporate and bulk orders for festivals", None, None, ("food", "trading")),
}


def fitting(kind: str) -> list[str]:
    return [k for k, c in CHANNELS.items() if kind in c[3]]


async def _sources(ws: str) -> dict[str, int]:
    since = now() - timedelta(days=90)
    out: dict[str, int] = {}
    async for x in db().leads.find({"owner_id": ws, "created_at": {"$gte": since}}, {"source": 1}):
        k = x.get("source") or "website"
        out[k] = out.get(k, 0) + 1
    return out


async def _ideas_job(p: dict, kind: str, allowed: list[str], sources: dict):
    def mock():
        city, what = _v(p, "city", "your area"), _short(_v(p, "industry", "your work")).lower()
        out = []
        for k in allowed[:6]:
            label = CHANNELS[k][0]
            out.append({"channel": k, "title": label, "why": f"People looking for {what} in {city} can find you this way.",
                        "first_step": f"This week: start with one — {label.lower()}."})
        return {"ideas": out}

    channels = "\n".join(f"- {k}: {CHANNELS[k][0]}" for k in allowed)
    src = ", ".join(f"{k} {n}" for k, n in sources.items()) or "none recorded yet"
    return await complete(RULES, f"""{profile_text(p)}
Business type: {kind}
Where leads came from in the last 90 days: {src}

Suggest 5 to 7 ways for THIS business to get more leads this month. Use ONLY these channels (by key), the ones that fit best first:
{channels}
Each idea: one concrete first step the owner can do this week. No invented prices or results.
JSON only: {{"ideas": [{{"channel": "key from the list", "title": "max 10 words", "why": "one line, specific to this business", "first_step": "one line"}}]}}""", mock, 1800)


def _shape(r: dict, allowed: list[str]) -> list[dict]:
    out, seen = [], set()
    for x in (r.get("ideas") or []) if isinstance(r, dict) else []:
        if not isinstance(x, dict) or x.get("channel") not in allowed or x["channel"] in seen:
            continue   # a channel that doesn't fit this business is dropped
        seen.add(x["channel"])
        out.append({"channel": x["channel"], "title": str(x.get("title") or CHANNELS[x["channel"]][0])[:120],
                    "why": str(x.get("why") or "")[:240], "first_step": str(x.get("first_step") or "")[:240]})
    if len(out) < 5:
        raise ValueError("fewer than 5 fitting ideas")
    return out[:7]


def _view(doc: dict | None, owner: dict) -> dict | None:
    if not doc:
        return None
    ideas = []
    for i, x in enumerate(doc.get("ideas") or []):
        _, feat, path, _ = CHANNELS.get(x["channel"], ("", None, None, ()))
        ideas.append({**x, "n": i, "feature": feat, "path": path, "locked": bool(feat and not plans.has(owner, feat)),
                      "feature_label": plans.label_of(feat) if feat else None,
                      "tried_at": x["tried_at"].isoformat() if x.get("tried_at") else None})
    return {"month": doc["month"], "ideas": ideas, "tried": sum(1 for x in ideas if x.get("tried")), "created_at": doc["created_at"].isoformat()}


@router.get("")
async def get_ideas(ctx: Ctx = Depends(feature("revenue_levers", "manager"))):
    month = rhythm.today_ist().strftime("%Y-%m")
    doc = await db().lead_ideas.find_one({"_id": f"{ctx.ws}:{month}"})
    past = [{"month": d["month"], "ideas": len(d.get("ideas") or []), "tried": sum(1 for x in d.get("ideas") or [] if x.get("tried"))}
            async for d in db().lead_ideas.find({"owner_id": ctx.ws, "month": {"$ne": month}}).sort("month", -1).limit(6)]
    p = await profile_of(ctx.ws)
    return {"month": month, "current": _view(doc, ctx.owner), "past": past, "business_type": kit_tasks.business_type(p),
            "sources": await _sources(ctx.ws)}


@router.post("")
async def make_ideas(ctx: Ctx = Depends(feature("revenue_levers", "manager"))):
    """This month's ideas (one AI run). Ideas already ticked as tried are kept."""
    p = await profile_of(ctx.ws)
    if not p.get("industry"):
        raise HTTPException(400, "Fill in what your business does in the Knowledge Hub first.")
    kind = kit_tasks.business_type(p)
    allowed = fitting(kind)
    month = rhythm.today_ist().strftime("%Y-%m")
    key = f"{ctx.ws}:{month}"
    old = await db().lead_ideas.find_one({"_id": key}) or {}
    kept = [x for x in old.get("ideas") or [] if x.get("tried")]
    ideas = await run_ai(ctx.owner, "lead_ideas", _ideas_job(p, kind, allowed, await _sources(ctx.ws)), f"Ways to get more leads — {month}",
                         save_output=False, check=lambda r: _shape(r, allowed))
    fresh = [dict(x, tried=False, tried_at=None) for x in ideas if x["channel"] not in {k["channel"] for k in kept}]
    await db().lead_ideas.replace_one({"_id": key}, {"_id": key, "owner_id": ctx.ws, "month": month, "business_type": kind,
                                                     "ideas": kept + fresh[:max(7 - len(kept), 0)], "created_at": now(), "by": ctx.actor}, upsert=True)
    await track("lead_ideas", ctx.ws, kind=kind)
    return await get_ideas(ctx)


class TriedIn(BaseModel):
    tried: bool = True


@router.post("/{n}/tried")
async def tried(n: int, body: TriedIn, ctx: Ctx = Depends(feature("revenue_levers", "manager"))):
    key = f"{ctx.ws}:{rhythm.today_ist().strftime('%Y-%m')}"
    doc = await db().lead_ideas.find_one({"_id": key})
    if not doc or not 0 <= n < len(doc.get("ideas") or []):
        raise HTTPException(404, "Not found")
    await db().lead_ideas.update_one({"_id": key}, {"$set": {f"ideas.{n}.tried": body.tried, f"ideas.{n}.tried_at": now() if body.tried else None}})
    return await get_ideas(ctx)


async def open_count(ws: str) -> int:
    """Ideas this month not tried yet (for Am I on target's actions)."""
    doc = await db().lead_ideas.find_one({"_id": f"{ws}:{rhythm.today_ist().strftime('%Y-%m')}"})
    return sum(1 for x in (doc or {}).get("ideas") or [] if not x.get("tried"))


async def ensure_indexes() -> None:
    await db().lead_ideas.create_index([("owner_id", 1), ("month", -1)])
