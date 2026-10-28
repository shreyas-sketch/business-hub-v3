"""API for the hub's working documents: /api/kits/{kind} — sop, jd, decision, role, culture, competence, review."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import kits
from ..context import Ctx, get_ctx
from ..db import db
from .hub import profile_of

router = APIRouter(prefix="/api/kits")


class GenerateIn(BaseModel):
    inputs: dict = Field(default_factory=dict)


class ContentIn(BaseModel):
    content: dict


def _allow(ctx: Ctx, k: kits.Kind, write: bool) -> None:
    ctx.require(k.feature)
    role = k.write_role if write else k.read_role
    if not ctx.at_least(role):
        raise HTTPException(403, "Only the business owner can do this." if role == "owner" else "Ask your manager or the business owner to do this.")


@router.get("/{kind}")
async def list_kits(kind: str, ctx: Ctx = Depends(get_ctx)):
    k = kits.kind_of(kind)
    _allow(ctx, k, write=False)
    return [{"id": d["_id"], "title": d["title"], "created_at": d["created_at"].isoformat(), "updated_at": d["updated_at"].isoformat(),
             "by_agent": kits.agent_label(d.get("by_agent"))}
            async for d in db().kits.find({"ws": ctx.ws, "kind": kind}, {"content": 0}).sort("updated_at", -1).limit(200)]


@router.get("/{kind}/{kit_id}")
async def get_kit(kind: str, kit_id: str, ctx: Ctx = Depends(get_ctx)):
    k = kits.kind_of(kind)
    _allow(ctx, k, write=False)
    doc = await db().kits.find_one({"_id": kit_id, "ws": ctx.ws, "kind": kind})
    if not doc:
        raise HTTPException(404, "Not found")
    return kits.view(doc)


@router.post("/{kind}")
async def create_kit(kind: str, body: GenerateIn, ctx: Ctx = Depends(get_ctx)):
    k = kits.kind_of(kind)
    _allow(ctx, k, write=True)
    try:
        k.inputs.model_validate(body.inputs)
    except Exception:
        raise HTTPException(400, "Please fill in the details first.")
    doc = await kits.generate(ctx.owner, ctx.ws, ctx.actor, kind, body.inputs, await profile_of(ctx.ws))
    return kits.view(doc)


@router.put("/{kind}/{kit_id}")
async def save_kit(kind: str, kit_id: str, body: ContentIn, ctx: Ctx = Depends(get_ctx)):
    k = kits.kind_of(kind)
    _allow(ctx, k, write=True)
    return kits.view(await kits.update(ctx.ws, ctx.actor, kind, kit_id, body.content))


@router.delete("/{kind}/{kit_id}")
async def delete_kit(kind: str, kit_id: str, ctx: Ctx = Depends(get_ctx)):
    k = kits.kind_of(kind)
    _allow(ctx, k, write=True)
    await db().kits.delete_one({"_id": kit_id, "ws": ctx.ws, "kind": kind})
    return {"ok": True}


async def ensure_indexes() -> None:
    await db().kits.create_index([("ws", 1), ("kind", 1), ("updated_at", -1)])
