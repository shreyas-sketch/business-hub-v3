"""
Engine Audit (v2.3) — see app/engine_audit.py for what it is and the arithmetic. 0 AI tokens.

Owner (the audit is in the Start section of the menu, owner only):
    GET  /api/audit                  the items with their fixes, the owner's sales a month (for the cap), the saved draft,
                                     the latest result and the history
    PUT  /api/audit/draft            answers so far (the page saves a few seconds after the last tap: one small write)
    POST /api/audit                  the finished audit → totals worked out here, kept in the history (one insert)
    GET  /api/audit/{id}/pdf         the result as an A4 PDF
    GET  /api/audit/{id}/card.png    a 1080×1350 image to share on WhatsApp

Admin (Admin → Engine Audit):
    GET  /api/admin/audit                    every item with its settings, the cap, and how many owners have done it
    PUT  /api/admin/audit/items/{key}        statement, hints, ₹ and hours rates, on/off
    POST /api/admin/audit/items/{key}/reset  back to the default
    PUT  /api/admin/audit/settings           the cap (% of a month's sales)
"""
import asyncio
import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import engine_audit as ea, plans
from ..context import Ctx, feature
from ..db import IST, db, now
from ..security import require_admin
from ..services import new_id, track

router = APIRouter(prefix="/api/audit")
admin = APIRouter(prefix="/api/admin/audit", dependencies=[Depends(require_admin)])
HISTORY = 12


class Answer(BaseModel):
    status: str | None = Field(default=None, pattern="^(have|missing|unsure)$")
    monthly_cost: float | None = Field(default=None, ge=0, le=ea.MAX_COST)
    hours_week: float | None = Field(default=None, ge=0, le=ea.MAX_HOURS)
    cost_unsure: bool = False
    hours_unsure: bool = False


class AuditIn(BaseModel):
    answers: dict[str, Answer] = Field(default_factory=dict, max_length=60)
    revenue_month: float | None = Field(default=None, ge=0, le=1e11)   # the owner's sales in a typical month (optional)


def _answers(body: AuditIn) -> dict:
    return {k[:40]: a.model_dump() for k, a in body.answers.items() if k in ea.ITEM_KEYS}


def _view(doc: dict | None) -> dict | None:
    if not doc:
        return None
    return {"id": doc["_id"], "completed_at": doc["completed_at"].isoformat(), "answers": doc["answers"], "engines": doc["engines"],
            "totals": doc["totals"], "top_gaps": doc.get("top_gaps", []), "revenue": doc.get("revenue") or {},
            "change": doc.get("change"), "note": ea.NOTE}


async def _revenue(ws: str, typed: float | None) -> dict:
    if typed and typed > 0:
        return {"month": round(typed), "source": "the sales a month you typed"}
    return await ea.revenue_hint(ws)


@router.get("")
async def get_audit(ctx: Ctx = Depends(feature("engine_audit", "owner"))):
    cfg = await ea.load_config()
    items = ea.active_items(cfg)
    draft = await db().engine_audit_drafts.find_one({"_id": ctx.ws}) or {}
    history = [d async for d in db().engine_audits.find({"owner_id": ctx.ws}).sort("completed_at", -1).limit(HISTORY)]
    return {"items": [ea.item_view(ctx.owner, it) for it in items],
            "engines": [{"key": e["key"], "label": e["label"], "day": e["day"], "blurb": e["blurb"]} for e in plans.ENGINES
                        if any(it["engine"] == e["key"] for it in items)],
            "cap_pct": cfg["cap_pct"], "revenue": await ea.revenue_hint(ctx.ws),
            "draft": {"answers": draft.get("answers") or {}, "revenue_month": draft.get("revenue_month")} if draft else None,
            "latest": _view(history[0]) if history else None,
            "history": [{"id": h["_id"], "completed_at": h["completed_at"].isoformat(), "in_place": h["totals"]["in_place"],
                         "total": h["totals"]["total"], "cost_month": h["totals"]["cost_month"], "growth_month": h["totals"]["growth_month"],
                         "hours_week": h["totals"]["hours_week"], "hours_back_week": h["totals"]["hours_back_week"]} for h in history],
            "note": ea.NOTE, "cap_note": ea.CAP_NOTE}


@router.put("/draft")
async def save_draft(body: AuditIn, ctx: Ctx = Depends(feature("engine_audit", "owner"))):
    await db().engine_audit_drafts.update_one({"_id": ctx.ws}, {"$set": {"answers": _answers(body), "revenue_month": body.revenue_month,
                                                                          "updated_at": now()}}, upsert=True)
    return {"ok": True}


@router.post("")
async def finish(body: AuditIn, ctx: Ctx = Depends(feature("engine_audit", "owner"))):
    cfg = await ea.load_config()
    items = ea.active_items(cfg)
    answers = _answers(body)
    missing = [it["key"] for it in items if not (answers.get(it["key"]) or {}).get("status")]
    if missing:
        raise HTTPException(400, f"Answer every statement first — {len(missing)} still to go.")
    revenue = await _revenue(ctx.ws, body.revenue_month)
    result = ea.compute(items, answers, revenue.get("month"), cfg["cap_pct"])
    prev = await db().engine_audits.find_one({"owner_id": ctx.ws}, {"totals": 1, "completed_at": 1}, sort=[("completed_at", -1)])
    change = None
    if prev:
        p, t = prev["totals"], result["totals"]
        change = {"since": prev["completed_at"].isoformat(), "in_place": t["in_place"] - p["in_place"],
                  "cost_month": t["cost_month"] - p["cost_month"], "hours_week": round(t["hours_week"] - p["hours_week"], 1)}
    at = now()
    at = at.replace(microsecond=at.microsecond // 1000 * 1000)    # the database keeps milliseconds: the same time now and later
    doc = {"_id": new_id(), "owner_id": ctx.ws, "by": ctx.actor, "completed_at": at, "answers": result["rows"],
           "engines": result["engines"], "totals": result["totals"], "top_gaps": result["top_gaps"], "revenue": revenue, "change": change}
    await db().engine_audits.insert_one(doc)
    await db().engine_audit_drafts.delete_one({"_id": ctx.ws})
    await track("engine_audit", ctx.ws, in_place=result["totals"]["in_place"], total=result["totals"]["total"])
    return _view(doc)


async def _own_audit(ctx: Ctx, audit_id: str) -> dict:
    doc = await db().engine_audits.find_one({"_id": audit_id[:40], "owner_id": ctx.ws})
    if not doc:
        raise HTTPException(404, "Audit not found")
    return doc


async def _business_name(ws: str) -> str:
    b = await db().businesses.find_one({"owner_id": ws}, {"name": 1}) or {}
    return b.get("name") or "Your business"


def _filename(name: str, ext: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-")[:40] or "business"
    return f"Engine-Audit-{slug}.{ext}"


@router.get("/{audit_id}/pdf")
async def audit_pdf(audit_id: str, ctx: Ctx = Depends(feature("engine_audit", "owner"))):
    doc = await _own_audit(ctx, audit_id)
    name = await _business_name(ctx.ws)
    data = await asyncio.to_thread(ea.pdf, doc, name)
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{_filename(name, "pdf")}"', "Cache-Control": "private, no-store"})


@router.get("/{audit_id}/card.png")
async def audit_card(audit_id: str, ctx: Ctx = Depends(feature("engine_audit", "owner"))):
    doc = await _own_audit(ctx, audit_id)
    name = await _business_name(ctx.ws)
    data = await asyncio.to_thread(ea.card_png, doc, name)
    return Response(data, media_type="image/png",
                    headers={"Content-Disposition": f'inline; filename="{_filename(name, "png")}"', "Cache-Control": "private, no-store"})


# ───────────────────────── Admin → Engine Audit ─────────────────────────
class ItemPatch(BaseModel):
    statement: str | None = Field(default=None, min_length=5, max_length=160)
    hint_cost: str | None = Field(default=None, min_length=3, max_length=200)
    hint_hours: str | None = Field(default=None, min_length=3, max_length=200)
    rate: float | None = Field(default=None, ge=0, le=1)
    hours_rate: float | None = Field(default=None, ge=0, le=1)
    on: bool | None = None


class SettingsIn(BaseModel):
    cap_pct: int = Field(ge=1, le=100)


async def _admin_view() -> dict:
    cfg = await ea.load_config(fresh=True)
    d = ea.defaults()
    items = []
    for it in sorted(cfg["items"].values(), key=lambda i: (plans.ENGINE_KEYS.index(i["engine"]), i["order"])):
        m = plans.META.get(it["feature"]) or {}
        items.append({**{k: it[k] for k in ("key", "engine", "statement", "feature", "hint_cost", "hint_hours", "rate", "hours_rate", "on")},
                      "feature_label": m.get("label", it["feature"]), "feature_on": bool(m.get("on")),
                      "changed": it["key"] in cfg["overrides"], "default": {k: d[it["key"]][k] for k in ("statement", "hint_cost",
                                                                                                           "hint_hours", "rate", "hours_rate")}})
    since = now().astimezone(IST).replace(hour=0, minute=0, second=0, microsecond=0)   # today, India time
    return {"items": items, "cap_pct": cfg["cap_pct"], "default_cap_pct": ea.DEFAULT_CAP_PCT,
            "engines": [{"key": e["key"], "label": e["label"]} for e in plans.ENGINES],
            "stats": {"audits": await db().engine_audits.estimated_document_count(),
                      "owners": len(await db().engine_audits.distinct("owner_id")),
                      "today": await db().engine_audits.count_documents({"completed_at": {"$gte": since}})}}


@admin.get("")
async def admin_get():
    return await _admin_view()


async def _write_items(items: dict, user: dict, key: str, before, after) -> None:
    await db().config.update_one({"_id": "engine_audit"}, {"$set": {ea.ITEMS_FIELD: items, "updated_at": now()}}, upsert=True)
    await db().config_changes.insert_one({"_id": new_id(), "at": now(), "by": user["_id"], "by_phone": user.get("phone", ""),
                                          "kind": "engine_audit", "target": key, "before": before, "after": after, "note": ""})
    ea.forget_cache()


@admin.put("/items/{key}")
async def admin_patch_item(key: str, body: ItemPatch, user: dict = Depends(require_admin)):
    if key not in ea.ITEM_KEYS:
        raise HTTPException(404, "No such audit item")
    patch = body.model_dump(exclude_none=True)
    doc = await db().config.find_one({"_id": "engine_audit"}) or {}
    items = doc.get(ea.ITEMS_FIELD) or {}
    before = dict(items.get(key) or {})
    default = ea.defaults()[key]
    after = {k: v for k, v in {**before, **patch}.items() if default.get(k) != v}
    if after:
        items[key] = after
    else:
        items.pop(key, None)
    await _write_items(items, user, key, before, after)
    return await _admin_view()


@admin.post("/items/{key}/reset")
async def admin_reset_item(key: str, user: dict = Depends(require_admin)):
    if key not in ea.ITEM_KEYS:
        raise HTTPException(404, "No such audit item")
    doc = await db().config.find_one({"_id": "engine_audit"}) or {}
    items = doc.get(ea.ITEMS_FIELD) or {}
    before = items.pop(key, None) or {}
    await _write_items(items, user, key, before, {})
    return await _admin_view()


@admin.put("/settings")
async def admin_settings(body: SettingsIn, user: dict = Depends(require_admin)):
    doc = await db().config.find_one({"_id": "engine_audit"}) or {}
    await db().config.update_one({"_id": "engine_audit"}, {"$set": {"cap_pct": body.cap_pct, "updated_at": now()}}, upsert=True)
    await db().config_changes.insert_one({"_id": new_id(), "at": now(), "by": user["_id"], "by_phone": user.get("phone", ""),
                                          "kind": "engine_audit", "target": "cap_pct", "before": doc.get("cap_pct"),
                                          "after": body.cap_pct, "note": ""})
    ea.forget_cache()
    return await _admin_view()


async def ensure_indexes() -> None:
    await db().engine_audits.create_index([("owner_id", 1), ("completed_at", -1)])
    await db().engine_audits.create_index("completed_at")
