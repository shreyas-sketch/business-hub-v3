"""
AI team (v2.4, Start → AI team): every AI role and its real counts; the Weekly growth line and Quick answers.
The counting is in app/ai_team.py. Nothing here uses an AI token.

GET   /api/ai-team                  the page: one card per role (feature on), the month's top strip, hours saved (estimated),
                                    the feed of the latest 30 actions, last week's growth line   (feature ai_team, manager)
PATCH /api/ai-team/roles/{role}     {on: bool} — a role's switch, or "growth_weekly" for the Weekly growth line (owner)
GET   /api/ai-team/answers          the Quick answers questions                                   (feature quick_answers, manager)
GET   /api/ai-team/answers/{key}    one answer, computed from the records (0 AI tokens)
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import ai_team, growth_core, plans
from ..agents import catalog, jobs
from ..context import Ctx, feature, locked_detail

router = APIRouter(prefix="/api/ai-team")


class RoleSwitch(BaseModel):
    on: bool


@router.get("")
async def get_page(ctx: Ctx = Depends(feature("ai_team", "manager"))):
    out = await ai_team.page(ctx.owner)
    out["can_switch"] = ctx.role == "owner"
    return out


@router.patch("/roles/{role}")
async def switch_role(role: str, body: RoleSwitch, ctx: Ctx = Depends(feature("ai_team", "owner"))):
    """Switches a role's automatic work on or off (jobs settings, key = the role — growth_core.role_on reads it). The work
    the owner starts himself (a button) still runs. For an agent that waits for the owner's own choice on his plan
    (jobs.OPT_IN, e.g. follow-ups on Membership) the switch is that choice, as on the Agents page."""
    if role == ai_team.WEEKLY_KEY:
        needed = "growth_weekly"
    elif role in growth_core.ROLES:
        needed = growth_core.ROLES[role][2]
    else:
        raise HTTPException(404, "Not found")
    if not plans.has(ctx.owner, needed):
        raise HTTPException(403, locked_detail(needed))
    patch = {"on": body.on}
    if role in catalog.AGENTS:
        patch["opted_in"] = body.on
    await jobs.save_settings(ctx.ws, role, patch)
    if role == ai_team.WEEKLY_KEY:
        return {"ok": True, "weekly": await ai_team.weekly_view(ctx.owner)}
    st = await jobs.get_settings(ctx.ws, role)
    return {"ok": True, "role": await ai_team.role_view(ctx.owner, role, st, await growth_core.working_now(ctx.ws))}


@router.get("/answers")
async def list_questions(ctx: Ctx = Depends(feature("quick_answers", "manager"))):
    return {"questions": [{"key": k, "question": q} for k, q in ai_team.QUESTIONS]}


@router.get("/answers/{key}")
async def get_answer(key: str, ctx: Ctx = Depends(feature("quick_answers", "manager"))):
    if key not in ai_team.ANSWERS:
        raise HTTPException(404, "Not found")
    return await ai_team.answer(ctx.owner, key)


async def ensure_indexes() -> None:
    """The page reads each role's latest log line; another feature may already have made the same index, which is fine."""
    from ..db import db
    try:
        await db().agent_log.create_index([("ws", 1), ("agent", 1), ("at", -1)])
    except Exception:  # noqa: BLE001  (an index of the same keys under another name never blocks start-up)
        pass
