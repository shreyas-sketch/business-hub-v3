"""Agents API: the catalog with each agent's switch and last run, the activity log, the approvals queue,
and running the monthly review on request."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import kits, plans
from ..agents import approvals as appr
from ..agents import catalog, jobs, runners  # noqa: F401  (runners attaches the built-in runners)
from ..context import Ctx, feature, manager_ctx, owner_ctx
from ..db import db, now, public
from .hub import profile_of

router = APIRouter(prefix="/api")
DONE = ["sent", "skipped", "failed", "expired"]


# ───────────────────────── agents ─────────────────────────
def agent_name(key: str | None) -> str:
    """Catalog agents by name; the agentic office's staff by their job title."""
    from ..agents.office import STAFF
    if key in catalog.AGENTS:
        return catalog.AGENTS[key].name
    if key and key.startswith("office_") and key[7:] in STAFF:
        return f"{STAFF[key[7:]]['title']} (AI office)"
    return {"crm": "Employz.ai sync"}.get(key or "", "Automation")


class AgentPatch(BaseModel):
    on: bool | None = None
    autonomy: Literal["ask", "act"] | None = None


async def _next_run(ws: str, spec: catalog.Spec) -> str | None:
    if spec.info_only:
        return None
    if spec.trigger == "schedule" and spec.schedule:
        at = now()
        due = catalog.due_slot(spec.schedule, at)
        if due and not await db().job_claims.find_one({"_id": f"{spec.key}:{ws}:{due[0]}"}, {"_id": 1}):
            return at.isoformat()  # its window is open and it hasn't run yet: within the minute
        return catalog.next_occurrence(spec.schedule, at).isoformat()
    job = await db().agent_jobs.find_one({"ws": ws, "agent": spec.key, "status": "scheduled"}, {"run_at": 1}, sort=[("run_at", 1)])
    return job["run_at"].isoformat() if job else None


async def _view(ctx: Ctx, spec: catalog.Spec, s: dict) -> dict:
    owner = ctx.owner
    tier = plans.tier_of(spec.feature)
    available = plans.has(owner, spec.feature)
    can_act = plans.has(owner, "agents_act")
    last = s.get("last_run_at")
    return {"key": spec.key, "name": spec.name, "what": spec.what, "when": spec.when, "feature": spec.feature, "trigger": spec.trigger,
            "customer_facing": spec.customer_facing, "approvals": spec.approvals, "info_only": spec.info_only,
            "tier": tier, "tier_name": plans.PLANS[tier]["name"], "available": available,
            "on": jobs.on_for(owner, spec.key, s), "opt_in": jobs.needs_opt_in(owner, spec.key), "autonomy": s.get("autonomy", "ask") if can_act and spec.approvals else "ask",
            "last_run_at": last.isoformat() if hasattr(last, "isoformat") else None, "last_status": s.get("last_status"),
            "last_note": s.get("last_note"),
            "next_run_at": await _next_run(ctx.ws, spec) if available and jobs.on_for(owner, spec.key, s) else None}


@router.get("/agents")
async def list_agents(ctx: Ctx = Depends(manager_ctx)):
    st = await jobs.all_settings(ctx.ws)
    shown = [spec for spec in catalog.AGENTS.values() if plans.META.get(spec.feature, {}).get("on")]
    shown.sort(key=lambda s: plans.rank(plans.tier_of(s.feature)))

    async def views(group: str) -> list[dict]:
        return [await _view(ctx, spec, st.get(spec.key, jobs.DEFAULTS)) for spec in shown if spec.group == group]
    return {"agents": await views("team"), "alerts": await views("alerts"), "approve_once": await views("approve_once"),
            "can_act": plans.has(ctx.owner, "agents_act"), "act_tier": plans.tier_of("agents_act"),
            "act_tier_name": plans.PLANS[plans.tier_of("agents_act")]["name"],
            "connected": bool(await appr.owner_whatsapp(ctx.ws, ctx.owner))}


@router.get("/agents/activity")
async def activity(limit: int = 50, ctx: Ctx = Depends(manager_ctx)):
    return [{**public(e), "agent_name": agent_name(e.get("agent"))}
            async for e in db().agent_log.find({"ws": ctx.ws}).sort("at", -1).limit(max(1, min(limit, 200)))]


@router.patch("/agents/{key}")
async def update_agent(key: str, body: AgentPatch, ctx: Ctx = Depends(owner_ctx)):
    spec = catalog.get(key)
    if not spec:
        raise HTTPException(404, "Not found")
    if spec.info_only:
        raise HTTPException(400, f"The {spec.name} is set up on its own page.")
    ctx.require(spec.feature)
    patch: dict = {}
    if body.on is not None:
        patch["on"] = body.on
        patch["opted_in"] = body.on        # the owner's own choice (some agents wait for it, see jobs.OPT_IN)
    if body.autonomy is not None:
        if not spec.approvals:
            raise HTTPException(400, f"The {spec.name} doesn't wait for approval, so there is nothing to change.")
        if body.autonomy == "act":
            ctx.require("agents_act")
        patch["autonomy"] = body.autonomy
    if patch:
        await jobs.save_settings(ctx.ws, key, patch)
    st = await jobs.get_settings(ctx.ws, key)
    return await _view(ctx, spec, st)


@router.post("/agents/monthly_review/run")
async def run_monthly_review(ctx: Ctx = Depends(feature("monthly_review", "owner"))):
    profile = await profile_of(ctx.ws)
    doc = await runners.review_now(ctx.owner, ctx.actor, profile)
    return kits.view(doc)


# ───────────────────────── approvals ─────────────────────────
approver = feature("approvals", "manager")


class ApprovalText(BaseModel):
    text: str = Field(min_length=2, max_length=900)


def _approval(a: dict) -> dict:
    spec = catalog.get(a.get("agent"))
    return {**public(a), "agent_name": spec.name if spec else agent_name(a.get("agent")), "own_link": appr.own_link(a),
            "money": a.get("kind") in appr.MONEY}


@router.get("/approvals")
async def list_approvals(status: Literal["pending", "done"] = "pending", ctx: Ctx = Depends(approver)):
    if status == "pending":
        cur = db().approvals.find({"ws": ctx.ws, "status": "pending"}).sort("created_at", -1).limit(300)
    else:
        cur = db().approvals.find({"ws": ctx.ws, "status": {"$in": DONE}}).sort("decided_at", -1).limit(100)
    return [_approval(a) async for a in cur]


@router.get("/approvals/status")
async def approvals_status(ctx: Ctx = Depends(approver)):
    """Whether "Send from your number" is available (Growth Mentorship and a connected WhatsApp number)."""
    return {"connected": bool(await appr.owner_whatsapp(ctx.ws, ctx.owner)), "plan_has_number": ctx.has("whatsapp_ai")}


@router.patch("/approvals/{approval_id}")
async def edit_approval(approval_id: str, body: ApprovalText, ctx: Ctx = Depends(approver)):
    a = await db().approvals.find_one({"_id": approval_id, "ws": ctx.ws})
    if not a:
        raise HTTPException(404, "Not found")
    if a["status"] != "pending":
        raise HTTPException(409, "This message was already handled.")
    text = " ".join(body.text.split()) if a["kind"] in ("followup", "office") else body.text.strip()
    await db().approvals.update_one({"_id": approval_id, "status": "pending"}, {"$set": {"text": text, "edited_by": ctx.actor, "edited_at": now()}})
    return _approval(await db().approvals.find_one({"_id": approval_id}))


@router.post("/approvals/{approval_id}/send")
async def send_approval(approval_id: str, ctx: Ctx = Depends(approver)):
    out = await appr.decide(ctx.ws, approval_id, ctx.actor, "send")
    return {"ok": True, "approval": _approval(out["approval"])}


@router.post("/approvals/{approval_id}/opened")
async def opened_approval(approval_id: str, ctx: Ctx = Depends(approver)):
    """Opened in the owner's WhatsApp: still waiting until they confirm it went (POST …/own)."""
    out = await appr.opened(ctx.ws, approval_id, ctx.actor)
    return {"link": out["link"], "approval": _approval(out["approval"])}


@router.post("/approvals/{approval_id}/own")
async def own_approval(approval_id: str, ctx: Ctx = Depends(approver)):
    out = await appr.decide(ctx.ws, approval_id, ctx.actor, "own")
    return {"link": out["link"], "approval": _approval(out["approval"])}


@router.post("/approvals/{approval_id}/skip")
async def skip_approval(approval_id: str, ctx: Ctx = Depends(approver)):
    out = await appr.decide(ctx.ws, approval_id, ctx.actor, "skip")
    return {"ok": True, "approval": _approval(out["approval"])}


async def ensure_indexes() -> None:
    d = db()
    await d.agent_jobs.create_index([("status", 1), ("run_at", 1)])
    await d.agent_jobs.create_index([("ws", 1), ("agent", 1), ("status", 1)])
    await d.agent_jobs.create_index("payload.lead_id")
    await d.approvals.create_index([("ws", 1), ("status", 1), ("created_at", -1)])
    await d.approvals.create_index([("ws", 1), ("lead_id", 1)])
    await d.agent_log.create_index([("ws", 1), ("at", -1)])
    await d.agent_settings.create_index([("ws", 1), ("key", 1)])
    try:  # MongoDB clears old run claims on its own; slots and notices only need them for a few weeks
        await d.job_claims.create_index("at", name="job_claims_ttl", expireAfterSeconds=120 * 86400)
    except Exception:
        pass
