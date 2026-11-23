"""
Lead research (v2.4, feature "lead_brief"): a short brief on every new inquiry. The work is in app/lead_research.py.

  GET  /api/leads/briefs              the briefs of the latest inquiries (for the Inquiries page), and the automatic allowance
  GET  /api/leads/{lead_id}/brief     one lead's brief (the "Brief" tab of "Edit opportunity")
  POST /api/leads/{lead_id}/brief     "Research this lead": write it now (1 AI token). Works when the automatic briefs are switched
                                      off or this month's are used up — a press uses a token, not the monthly allowance.
"""
from fastapi import APIRouter, Depends, HTTPException

from .. import growth_core, lead_research
from ..context import Ctx, feature
from ..db import db

router = APIRouter(prefix="/api/leads")


async def _allowance(ctx: Ctx) -> dict:
    left = await growth_core.left(ctx.owner, "briefs")
    return {"auto_on": await growth_core.role_on(ctx.ws, lead_research.ROLE), "left": left["left"], "limit": left["limit"],
            "token_floor": lead_research.TOKEN_FLOOR, "can_research": ctx.at_least("manager"), "token_cost": 1}


@router.get("/briefs")
async def briefs(ctx: Ctx = Depends(feature("lead_brief"))):
    items = {}
    async for l in db().leads.find({"owner_id": ctx.ws, "brief": {"$exists": True}}, {"brief": 1}).sort("created_at", -1).limit(300):
        items[l["_id"]] = lead_research.view(l.get("brief"))
    return {"items": items, **await _allowance(ctx)}


async def _lead(ctx: Ctx, lead_id: str) -> dict:
    lead = await db().leads.find_one({"_id": lead_id, "owner_id": ctx.ws})
    if not lead:
        raise HTTPException(404, "Not found")
    return lead


@router.get("/{lead_id}/brief")
async def get_brief(lead_id: str, ctx: Ctx = Depends(feature("lead_brief"))):
    lead = await _lead(ctx, lead_id)
    return {"brief": lead_research.view(lead.get("brief")), **await _allowance(ctx)}


@router.post("/{lead_id}/brief")
async def research(lead_id: str, ctx: Ctx = Depends(feature("lead_brief", "manager"))):
    lead = await _lead(ctx, lead_id)
    brief = await lead_research.write(ctx.owner, lead, ctx.actor)
    return {"brief": lead_research.view(brief), **await _allowance(ctx)}


async def ensure_indexes() -> None:
    await lead_research.ensure_indexes()
