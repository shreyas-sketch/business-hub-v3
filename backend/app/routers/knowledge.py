"""
Knowledge Hub (the Business Brain): the four new sections, the completeness meter and the "your Knowledge Hub changed"
banners. The basics (name, offers, contact…) are still saved by PUT /api/business, which now propagates too.
"""
import re
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from .. import knowledge
from ..agents import rhythm
from ..context import Ctx, get_ctx, owner_ctx
from ..db import db, now
from ..services import track

router = APIRouter(prefix="/api/knowledge")
GSTIN = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
GST_RATES = (0, 0.25, 3, 5, 12, 18, 28)


class IdentityIn(BaseModel):
    entity: str = Field(pattern="^(proprietor|partnership|llp|pvt_ltd|opc|other)$")
    gst_registered: bool
    gstin: str = Field(default="", max_length=15)
    gst_scheme: str = Field(default="monthly", pattern="^(monthly|qrmp)$")   # QRMP: quarterly returns, monthly payment
    state: str = Field(max_length=60)
    staff: int = Field(ge=0, le=1_000_000)
    registrations: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("state")
    @classmethod
    def known_state(cls, v):
        if v not in knowledge.STATES:
            raise ValueError("Pick your state from the list")
        return v

    @field_validator("gstin")
    @classmethod
    def valid_gstin(cls, v):
        v = (v or "").strip().upper()
        if v and not GSTIN.match(v):
            raise ValueError("A GSTIN has 15 characters, like 27ABCDE1234F1Z5")
        return v

    @field_validator("registrations")
    @classmethod
    def known_regs(cls, v):
        allowed = {k for k, _ in knowledge.REGISTRATIONS}
        return sorted({r for r in v if r in allowed})


class Cost(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    amount: float = Field(gt=0, le=1e9)            # rupees a month
    day: int = Field(ge=1, le=31)                  # day of the month it goes out
    kind: str = Field(default="other", pattern="^(salary|rent|emi|subscription|utility|other)$")


class MoneyIn(BaseModel):
    payment_terms_days: int = Field(ge=0, le=365)
    bank_balance: float = Field(ge=-1e10, le=1e11)  # rupees; an overdraft is negative
    bank_balance_on: str = Field(max_length=10)
    fixed_costs: list[Cost] = Field(default_factory=list, max_length=40)
    gst_rate: float = Field(default=18)
    safety_buffer: float = Field(default=0, ge=0, le=1e10)

    @field_validator("bank_balance_on")
    @classmethod
    def valid_day(cls, v):
        try:
            d = date.fromisoformat(v)
        except ValueError:
            raise ValueError("Use a date like 2026-10-19")
        if d > date.fromisoformat(knowledge.today_iso()):
            raise ValueError("The balance date can't be in the future")
        return d.isoformat()

    @field_validator("gst_rate")
    @classmethod
    def valid_rate(cls, v):
        if v not in GST_RATES:
            raise ValueError("GST rate is one of 0, 0.25, 3, 5, 12, 18 or 28%")
        return v


class TargetsIn(BaseModel):
    monthly_revenue: float = Field(gt=0, le=1e11)


async def _business(ws: str) -> dict:
    b = await db().businesses.find_one({"owner_id": ws})
    if not b or not b.get("name"):
        raise HTTPException(400, "Tell us about your business first")
    return b


def options() -> dict:
    return {"entities": [{"value": a, "label": b} for a, b in knowledge.ENTITIES], "states": knowledge.STATES,
            "registrations": [{"value": a, "label": b} for a, b in knowledge.REGISTRATIONS],
            "cost_kinds": [{"value": a, "label": b} for a, b in knowledge.COST_KINDS], "gst_rates": list(GST_RATES)}


@router.get("")
async def get_knowledge(ctx: Ctx = Depends(owner_ctx)):
    b = await db().businesses.find_one({"owner_id": ctx.ws}) or {}
    comp = await knowledge.completeness_for(ctx.ws, b)
    money = dict(b.get("money") or {})
    if money.get("bank_balance_on"):
        money["balance_days_old"] = knowledge.days_old(money["bank_balance_on"])
    target = (b.get("targets") or {}).get("monthly_revenue")
    if not target:
        m = await db().magic.find_one({"_id": ctx.ws}, {"inputs": 1}) or {}
        target = (m.get("inputs") or {}).get("monthly_target")
    return {"basics": {k: b.get(k) for k in (*knowledge.BASICS, *knowledge.CONTACT)}, "identity": b.get("identity") or None, "money": money or None,
            "targets": {"monthly_revenue": target} if target else None, "brand": {"message": (b.get("brand") or {}).get("message"),
                                                                                    "voice": b.get("voice") or None},
            "completeness": comp, "version": b.get("brain_version", 0), "options": options(),
            "updates": await knowledge.updates(ctx.owner)}


async def _save(ctx: Ctx, key: str, value: dict) -> dict:
    b = await _business(ctx.ws)
    before = {key: b.get(key)}
    await db().businesses.update_one({"_id": b["_id"]}, {"$set": {key: value, "updated_at": now()}})
    changed = knowledge.changed_sections(before, {key: value})
    knowledge.after_save(ctx.owner, changed)
    await track("knowledge_saved", ctx.ws, section=key)
    return await get_knowledge(ctx)


@router.put("/identity")
async def save_identity(body: IdentityIn, ctx: Ctx = Depends(owner_ctx)):
    data = body.model_dump()
    if not data["gst_registered"]:
        data.update(gstin="", gst_scheme="monthly")
    return await _save(ctx, "identity", data)


@router.put("/money")
async def save_money(body: MoneyIn, ctx: Ctx = Depends(owner_ctx)):
    data = body.model_dump()
    data["fixed_costs"] = [{**c, "name": c["name"].strip()} for c in data["fixed_costs"]]
    b = await db().businesses.find_one({"owner_id": ctx.ws}, {"money": 1}) or {}
    old = b.get("money") or {}
    same = old.get("bank_balance") == data["bank_balance"] and old.get("bank_balance_on") == data["bank_balance_on"]
    data["bank_balance_saved_at"] = old.get("bank_balance_saved_at") if same and old.get("bank_balance_saved_at") else now()
    return await _save(ctx, "money", data)


@router.put("/targets")
async def save_targets(body: TargetsIn, ctx: Ctx = Depends(owner_ctx)):
    """The same target Magic Number tracks: saving it here updates Magic Number too."""
    await sync_magic_target(ctx.ws, body.monthly_revenue, ctx.actor)
    return await _save(ctx, "targets", {"monthly_revenue": body.monthly_revenue})


async def sync_magic_target(ws: str, target: float, actor: str) -> None:
    m = await db().magic.find_one({"_id": ws})
    if m and float((m.get("inputs") or {}).get("monthly_target") or 0) != float(target):
        inputs = {**m["inputs"], "monthly_target": float(target)}
        await db().magic.update_one({"_id": ws}, {"$set": {"inputs": inputs, "result": rhythm.magic_result(inputs), "updated_at": now(), "by": actor}})


@router.get("/updates")
async def get_updates(ctx: Ctx = Depends(get_ctx)):
    return await knowledge.updates(ctx.owner)


@router.post("/updates/{key}/refresh")
async def refresh(key: str, ctx: Ctx = Depends(owner_ctx)):
    await knowledge.apply(ctx.owner, key)
    await track("knowledge_refresh", ctx.ws, dep=key)
    return await knowledge.updates(ctx.owner)


@router.post("/updates/{key}/keep")
async def keep(key: str, ctx: Ctx = Depends(owner_ctx)):
    await knowledge.clear_stale(ctx.ws, key)
    await track("knowledge_keep", ctx.ws, dep=key)
    return await knowledge.updates(ctx.owner)


class FactIn(BaseModel):
    text: str = Field(min_length=3, max_length=200)


@router.post("/facts")
async def add_fact(body: FactIn, ctx: Ctx = Depends(owner_ctx)):
    """'Add this to your Knowledge Hub?' — a fact the owner stated (e.g. while editing the website by prompt) goes into the
    Brain's proof, where every AI writer can then use it. Everything built from the Brain follows (refresh or banner)."""
    b = await _business(ctx.ws)
    fact = re.sub(r"\s+", " ", body.text).strip(" .")
    lines = [x.strip() for x in str(b.get("proof") or "").split("\n") if x.strip()]
    if fact.lower() not in (x.lower().strip(" .") for x in lines):
        lines.append(fact)
        proof = "\n".join(lines)[:2000]
        await db().businesses.update_one({"_id": b["_id"]}, {"$set": {"proof": proof, "updated_at": now()}})
        knowledge.after_save(ctx.owner, {"basics"})
        await track("knowledge_fact", ctx.ws)
    return {"ok": True, "proof": (await _business(ctx.ws)).get("proof", "")}


async def ensure_indexes() -> None:
    await db().brain_stale.create_index("ws")
