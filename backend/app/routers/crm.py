"""
CRM, pipeline and opportunities (v2.3, Conversion engine). The data model is described in app/crm.py.

Pipeline & Opportunities (feature "pipeline", one page at /pipeline, one pipeline per business)
  GET    /api/crm/pipeline                 the pipeline (or none yet), the 4 questions pre-filled from what the hub knows
  POST   /api/crm/pipeline/build           {answers, use_ai}: stages + lost reasons (1 AI token with use_ai, else free)
  PUT    /api/crm/pipeline/stages          {stages [{id?, name, ff, hint}], lost_reasons}: rename, reorder, add, remove
  GET    /api/crm/deals                    every deal (open first) with its pipeline stage, the stages, totals, this week
  POST   /api/crm/deals                    add an opportunity (and its contact)
  GET    /api/crm/deals/{id}               one deal: contact, history, its quotations and invoices
  PATCH  /api/crm/deals/{id}               value, stage, close date, follow-up, team member, won / lost (+ reason), name
  POST   /api/crm/deals/{id}/move          {pstage}: a drag on the board
CRM (feature "crm", /contacts)
  GET    /api/crm/contacts                 search and filters (source, tag, company, follow-up due)
  POST   /api/crm/contacts                 add a contact
  GET    /api/crm/contacts/{id}            the contact with company, deals, history, customer record, quotations
  PATCH  /api/crm/contacts/{id}            edit (name, phone, email, company, tags, notes, follow-up, team member)
  DELETE /api/crm/contacts/{id}            removes the contact (its deals stay)
  POST   /api/crm/contacts/{id}/activities {kind call|meeting|note, text, lead_id?}
  GET    /api/crm/companies                companies with their contacts and open deal value
  GET    /api/crm/followups                follow-ups overdue, today and in the next 7 days (contacts and deals)
  POST   /api/crm/import                   a CSV or Excel list of contacts (multipart "file")
  GET    /api/crm/export?what=contacts|deals&fmt=csv|xlsx
Nothing here messages a customer. A WhatsApp button only opens the owner's own WhatsApp.
"""
import csv
import io
import re
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from starlette.datastructures import UploadFile

from .. import crm, dashboard_cache
from ..ai import crm_tasks
from ..context import Ctx, feature
from ..db import db, now
from ..services import new_id, run_ai, track
from .desk import rupees

router = APIRouter(prefix="/api/crm")
FF = crm_tasks.FF
SOURCES = [("website", "Website"), ("whatsapp", "WhatsApp"), ("phone", "Phone"), ("walk_in", "Walk-in"), ("referral", "Referral"),
           ("marketplace", "IndiaMART / Justdial"), ("social", "Instagram / Facebook"), ("exhibition", "Exhibitions"), ("email", "Email"),
           ("other", "Other")]
SOURCE_KEYS = {k for k, _ in SOURCES}
PATHS = [("call", "A call"), ("visit", "A visit or site measurement"), ("demo", "A demo or sample"), ("quote", "A quotation"),
         ("proposal", "A proposal"), ("direct", "They buy directly")]
WHO = [("owner", "Me"), ("team", "My team"), ("both", "Both")]
DEFAULT_PATH = {"trading": ["call", "quote"], "manufacturing": ["call", "visit", "quote"], "clinic": ["call", "visit"],
                "food": ["call", "quote"], "services": ["call", "visit", "quote"]}
LEAD_TO_SOURCE = {"website": "website", "guide": "website", "lead_magnet": "website", "whatsapp": "whatsapp", "email": "email"}


# ───────────────────────── helpers ─────────────────────────
def _real_day(v: str | None) -> str | None:
    """A date field: empty, or a day that exists — "2026-02-30" matches the pattern but would break the reminder after the
    deal or contact was already saved."""
    if v:
        try:
            date.fromisoformat(v)
        except ValueError:
            raise ValueError("Use a real date, like 2026-11-05")
    return v


async def _competitor(ws: str) -> dict | None:
    """The competitor summary (fork C's Competitor Watch summary), when the hub has one. Never required."""
    try:
        from ..competitor_watch import competitor_summary   # added in v2.3; older builds don't have it
    except ImportError:
        return None
    try:
        return await competitor_summary(ws)
    except Exception:  # noqa: BLE001
        return None


def _who_name(users: dict, uid: str | None) -> str:
    return users.get(uid, "") if uid else ""


async def _team(ctx: Ctx) -> list[dict]:
    out = [{"id": ctx.ws, "name": (ctx.owner.get("name") or "Owner") + " (owner)"}]
    async for u in db().users.find({"team_of": ctx.ws}, {"name": 1, "phone": 1}).limit(50):
        out.append({"id": u["_id"], "name": u.get("name") or u.get("phone", "Team member")})
    return out


def _deal_view(l: dict, stages: list[dict], users: dict, quotes: dict | None = None) -> dict:
    ps = crm.pstage_of(l, stages)
    q = (quotes or {}).get(l["_id"]) or {}
    at = l.get("stage_at") or l.get("created_at")
    return {"id": l["_id"], "name": l.get("name") or "Customer", "phone": l.get("phone") or "", "email": l.get("email") or "",
            "company": l.get("company") or "", "value": l.get("value"), "status": l.get("status", "new"), "stage": l.get("stage") or "identification",
            "pstage": ps, "source": l.get("source") or "", "source_label": crm.SOURCE_LABEL.get(l.get("source") or "", (l.get("source") or "").replace("_", " ")),
            "message": (l.get("message") or "")[:400], "created_at": crm.iso(l.get("created_at")), "stage_at": crm.iso(at),
            "updated_at": crm.iso(l.get("updated_at")), "expected_close": l.get("expected_close"), "follow_up_on": l.get("follow_up_on"),
            "follow_up_note": l.get("follow_up_note", ""), "assignee_id": l.get("assignee_id"), "assignee_name": _who_name(users, l.get("assignee_id")),
            "lost_reason": l.get("lost_reason", ""), "contact_id": l.get("contact_id"), "won_at": crm.iso(l.get("won_at")),
            "quote": q.get("quote"), "invoice": q.get("invoice")}


async def _quotes_for(ws: str, lead_ids: list[str]) -> dict:
    """The latest quotation and invoice of each deal (quotes.lead_id; an invoice made from a quote keeps its lead_id)."""
    out: dict = {}
    if not lead_ids:
        return out
    async for q in db().quotes.find({"ws": ws, "lead_id": {"$in": lead_ids}}, {"lead_id": 1, "kind": 1, "number": 1, "status": 1,
                                                                             "totals.total": 1, "created_at": 1}).sort("created_at", 1):
        out.setdefault(q["lead_id"], {})[q["kind"] if q["kind"] in ("quote", "invoice") else "quote"] = {
            "id": q["_id"], "number": q.get("number"), "status": q.get("status"), "total": (q.get("totals") or {}).get("total")}
    return out


async def _users(ctx: Ctx) -> dict:
    return {t["id"]: t["name"] for t in await _team(ctx)}


def _actor_name(ctx: Ctx) -> str:
    return ctx.user.get("name") or ("Owner" if ctx.role == "owner" else "Team")


# ───────────────────────── pipeline ─────────────────────────
class Answers(BaseModel):
    sell: str = Field(default="", max_length=400)            # what they sell (business type + products/services)
    customers: str = Field(default="", max_length=300)       # who buys
    sources: list[str] = Field(default_factory=list, max_length=12)
    who: str = Field(default="owner", pattern="^(owner|team|both)$")
    after_lead: str = Field(default="", max_length=300)      # what happens when a new lead comes in
    path: list[str] = Field(default_factory=list, max_length=6)   # call, visit, demo, quote, proposal, direct — in order
    before_pay: str = Field(default="", max_length=300)
    reasons: list[str] = Field(default_factory=list, max_length=12)


class BuildIn(BaseModel):
    answers: Answers
    use_ai: bool = True


class StageIn(BaseModel):
    id: str | None = Field(default=None, max_length=40)
    name: str = Field(min_length=2, max_length=40)
    ff: str = Field(pattern="^(identification|logic|pain|vision|close)$")
    hint: str = Field(default="", max_length=120)


class StagesIn(BaseModel):
    stages: list[StageIn] = Field(min_length=2, max_length=12)
    lost_reasons: list[str] = Field(default_factory=list, max_length=12)


async def _prefill(ctx: Ctx) -> tuple[dict, dict]:
    """The 4 questions answered from what the hub already knows, and where each answer came from."""
    b = await db().businesses.find_one({"owner_id": ctx.ws}) or {}
    kind = crm.business_kind(b)
    comp = await _competitor(ctx.ws)
    where: dict = {}
    offers = ", ".join(o.get("name", "") for o in (b.get("offers") or []) if o.get("name"))[:300]
    sell = " — ".join(x for x in (b.get("industry", ""), offers) if x)[:400]
    if sell:
        where["sell"] = "From your Knowledge Hub"
    customers = (b.get("ideal_customer") or (b.get("inputs") or {}).get("target_audience") or "")[:300]
    if customers:
        where["customers"] = "From your Knowledge Hub"
    seen: list[str] = []
    async for l in db().leads.find({"owner_id": ctx.ws, "created_at": {"$gte": now() - timedelta(days=180)}}, {"source": 1}).limit(2000):
        s = LEAD_TO_SOURCE.get(l.get("source") or "")
        if s and s not in seen:
            seen.append(s)
    site = await db().sites.find_one({"owner_id": ctx.ws, "status": "live"}, {"_id": 1})
    if site and "website" not in seen:
        seen.insert(0, "website")
    if b.get("whatsapp") and "whatsapp" not in seen:
        seen.append("whatsapp")
    if seen:
        where["sources"] = "From the inquiries the hub has seen"
    path = list(DEFAULT_PATH.get(kind, DEFAULT_PATH["services"]))
    where["path"] = "Suggested for your kind of business"
    if comp:
        buy = str(comp.get("how_customers_buy") or comp.get("buying_path") or "").lower()
        found = [k for k, words in (("call", ("call",)), ("visit", ("visit", "site")), ("demo", ("demo", "sample", "trial")),
                                    ("quote", ("quote", "quotation")), ("proposal", ("proposal",)), ("direct", ("buy now", "cart", "order online", "direct")))
                 if any(w in buy for w in words)]
        if found:
            path = found
            where["path"] = "From how your competitor sells — check it fits you"
    pains = (b.get("inputs") or {}).get("pain_points") or b.get("pain_points") or ""
    reasons = list(crm_tasks.BASE_REASONS.get(kind, crm_tasks.BASE_REASONS["services"]))
    where["reasons"] = "Common for your kind of business — edit them to your words"
    team = await db().users.count_documents({"team_of": ctx.ws})
    return ({"sell": sell, "customers": customers, "sources": seen, "who": "both" if team else "owner", "after_lead": "",
             "path": path, "before_pay": "", "reasons": reasons, "pain_points": str(pains)[:300]}, where)


def _pipeline_view(p: dict | None) -> dict | None:
    if not p:
        return None
    return {"answers": p.get("answers") or {}, "stages": p.get("stages") or [], "lost_reasons": p.get("lost_reasons") or [],
            "built_by": p.get("built_by"), "built_at": crm.iso(p.get("built_at")), "updated_at": crm.iso(p.get("updated_at"))}


@router.get("/pipeline")
async def get_pipeline(ctx: Ctx = Depends(feature("pipeline"))):
    p = await crm.pipeline_of(ctx.ws)
    prefill, where = await _prefill(ctx)
    return {"pipeline": _pipeline_view(p), "stages": (p or {}).get("stages") or crm.field_stages(), "prefill": prefill, "where": where,
            "options": {"sources": SOURCES, "paths": PATHS, "who": WHO,
                        "ff": [{"key": k, "label": crm_tasks.FF_LABEL[k], "hint": crm.DEFAULT_HINTS[k]} for k in FF]},
            "token_cost": 1, "can_edit": ctx.at_least("manager")}


def _clean_answers(a: Answers) -> dict:
    out = a.model_dump()
    out["sources"] = [s for s in dict.fromkeys(out["sources"]) if s in SOURCE_KEYS]
    out["path"] = [s for s in dict.fromkeys(out["path"]) if s in dict(PATHS)]
    out["reasons"] = [str(r).strip()[:80] for r in out["reasons"] if str(r).strip()][:10]
    for k in ("sell", "customers", "after_lead", "before_pay"):
        out[k] = out[k].strip()
    return out


async def _save_pipeline(ws: str, answers: dict, built: dict, by: str, how: str) -> dict:
    taken: set = set()
    stages = [{"id": crm.stage_id(s["name"], taken), "name": s["name"], "ff": s["ff"], "hint": s.get("hint", "")} for s in built["stages"]]
    doc = {"_id": ws, "answers": answers, "stages": stages, "lost_reasons": built["lost_reasons"], "built_by": how, "built_at": now(),
           "updated_at": now(), "by": by}
    await db().pipelines.replace_one({"_id": ws}, doc, upsert=True)
    # every open deal lands on the new stage for its place on the field
    async for l in db().leads.find({"owner_id": ws, "status": {"$in": list(crm.OPEN)}}, {"stage": 1, "pstage": 1}).limit(20000):
        await db().leads.update_one({"_id": l["_id"]}, {"$set": {"pstage": crm.pstage_of({**l, "pstage": None}, stages)}})
    return doc


@router.post("/pipeline/build")
async def build_pipeline(body: BuildIn, ctx: Ctx = Depends(feature("pipeline", "manager"))):
    answers = _clean_answers(body.answers)
    if not answers["sell"] and not answers["customers"]:
        raise HTTPException(400, "Tell us in a line what you sell and who buys it.")
    b = await db().businesses.find_one({"owner_id": ctx.ws}) or {}
    kind = crm.business_kind(b)
    comp = await _competitor(ctx.ws)
    fallback = crm_tasks.rules_pipeline(answers, kind, comp)
    if body.use_ai:
        p = b if b.get("name") else {**b, "name": b.get("name") or "the business", "industry": answers["sell"]}
        built = await run_ai(ctx.owner, "pipeline", crm_tasks.pipeline_stages(p, answers, kind, comp), "Your sales pipeline",
                             save_output=False, check=lambda r: crm_tasks.clean_ai(r, fallback))
        how = "ai"
    else:
        built, how = fallback, "rules"
    doc = await _save_pipeline(ctx.ws, answers, built, ctx.actor, how)
    await track("pipeline_built", ctx.ws, how=how, stages=len(doc["stages"]))
    return {"pipeline": _pipeline_view(doc), "stages": doc["stages"]}


@router.put("/pipeline/stages")
async def edit_stages(body: StagesIn, ctx: Ctx = Depends(feature("pipeline", "manager"))):
    old = await crm.pipeline_of(ctx.ws)
    old_stages = (old or {}).get("stages") or crm.field_stages()
    old_by_id = {s["id"]: s for s in old_stages}
    taken: set = set()
    stages = []
    for s in body.stages:
        sid = s.id if s.id and s.id in old_by_id and s.id not in taken else None
        if sid:
            taken.add(sid)
        else:
            sid = crm.stage_id(s.name, taken | set(old_by_id))
        stages.append({"id": sid, "name": s.name.strip(), "ff": s.ff, "hint": s.hint.strip()})
    if len({s["name"].lower() for s in stages}) != len(stages):
        raise HTTPException(400, "Two stages have the same name. Give each stage its own name.")
    reasons = [r.strip()[:80] for r in body.lost_reasons if r.strip()][:12]
    doc = {"_id": ctx.ws, "answers": (old or {}).get("answers") or {}, "stages": stages,
           "lost_reasons": reasons or (old or {}).get("lost_reasons") or crm_tasks.BASE_REASONS["services"],
           "built_by": (old or {}).get("built_by") or "owner", "built_at": (old or {}).get("built_at") or now(), "updated_at": now(), "by": ctx.actor}
    await db().pipelines.replace_one({"_id": ctx.ws}, doc, upsert=True)
    new_by_id = {s["id"]: s for s in stages}
    # a stage that now sits on another part of the field takes its deals with it; deals of a removed stage find their place
    for sid, s in new_by_id.items():
        if sid in old_by_id and old_by_id[sid]["ff"] != s["ff"]:
            await db().leads.update_many({"owner_id": ctx.ws, "pstage": sid}, {"$set": {"stage": s["ff"], "stage_at": now()}})
    gone = [sid for sid in old_by_id if sid not in new_by_id]
    if gone:
        async for l in db().leads.find({"owner_id": ctx.ws, "pstage": {"$in": gone}}, {"stage": 1}).limit(20000):
            await db().leads.update_one({"_id": l["_id"]}, {"$set": {"pstage": crm.pstage_of({**l, "pstage": None}, stages)}})
    dashboard_cache.bust(ctx.ws)
    return {"pipeline": _pipeline_view(doc), "stages": stages}


# ───────────────────────── deals (opportunities) ─────────────────────────
class DealIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    phone: str = Field(default="", max_length=24)
    email: str = Field(default="", max_length=254)
    company: str = Field(default="", max_length=120)
    value: float | None = Field(default=None, ge=0, le=1e11)
    pstage: str | None = Field(default=None, max_length=40)
    source: str = Field(default="manual", max_length=30)
    expected_close: str = Field(default="", pattern=r"^(|\d{4}-\d{2}-\d{2})$")
    follow_up_on: str = Field(default="", pattern=r"^(|\d{4}-\d{2}-\d{2})$")
    note: str = Field(default="", max_length=1000)

    _days = field_validator("expected_close", "follow_up_on")(_real_day)


class DealPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=80)
    company: str | None = Field(default=None, max_length=120)
    value: float | None = Field(default=None, ge=0, le=1e11)
    pstage: str | None = Field(default=None, max_length=40)
    expected_close: str | None = Field(default=None, pattern=r"^(|\d{4}-\d{2}-\d{2})$")
    follow_up_on: str | None = Field(default=None, pattern=r"^(|\d{4}-\d{2}-\d{2})$")
    follow_up_note: str | None = Field(default=None, max_length=300)
    assignee_id: str | None = Field(default=None, max_length=40)
    status: str | None = Field(default=None, pattern="^(new|contacted|won|lost)$")
    lost_reason: str | None = Field(default=None, max_length=80)
    note: str | None = Field(default=None, max_length=1000)

    _days = field_validator("expected_close", "follow_up_on")(_real_day)


class MoveIn(BaseModel):
    pstage: str = Field(min_length=1, max_length=40)


async def _pipelines(ctx: Ctx) -> list[dict]:
    """The pipelines the board can show: the hub's own, then each one imported from Employz.ai (read-only)."""
    out = [{"key": "hub", "name": "Sales pipeline", "readonly": False}]
    async for p in db().ext_pipelines.find({"ws": ctx.ws, "deleted_at": {"$exists": False}}, {"ext_id": 1, "name": 1, "stages": 1}).sort("name", 1):
        out.append({"key": f"employz:{p['ext_id']}", "name": p.get("name") or "Pipeline", "readonly": True, "source": "Employz.ai",
                    "stage_count": len(p.get("stages") or [])})
    return out


EXT_STATUS = {"open": "new", "won": "won", "lost": "lost", "abandoned": "lost"}
STAGE_COLOURS = ["#5B8DEF", "#2BB3A3", "#E0A53B", "#C2649E", "#7A6FF0", "#4FA36B", "#D9734A", "#6C7A89"]


async def _ext_board(ctx: Ctx, ext_id: str, pipelines: list[dict]) -> dict:
    """An Employz.ai pipeline on the board: its own stages and opportunities, read-only (changes are made in Employz.ai)."""
    p = await db().ext_pipelines.find_one({"ws": ctx.ws, "ext_id": ext_id, "deleted_at": {"$exists": False}})
    if not p:
        raise HTTPException(404, "That Employz.ai pipeline isn't in the hub any more.")
    conn = await db().connections.find_one({"_id": ctx.ws}, {"employz.app_link": 1}) or {}
    link = ((conn.get("employz") or {}).get("app_link") or "").strip() or None
    rows = [d async for d in db().ext_deals.find({"ws": ctx.ws, "pipeline_id": ext_id, "deleted_at": {"$exists": False}}).sort("updated_at", -1).limit(2000)]
    stage_ids = {s["id"] for s in p.get("stages") or []}
    deals = []
    for d in rows:
        ct = d.get("contact") or {}
        deals.append({"id": f"x:{d['ext_id']}", "readonly": True, "from_crm": True, "name": ct.get("name") or d.get("name") or "Opportunity",
                      "title": d.get("name") or "", "phone": ct.get("phone") or "", "email": ct.get("email") or "", "company": ct.get("company") or "",
                      "value": d.get("value"), "status": EXT_STATUS.get(d.get("status"), "new"), "crm_status": d.get("status"),
                      "stage": "identification", "pstage": d.get("stage_id") if d.get("stage_id") in stage_ids else None,
                      "source": "employz", "source_label": d.get("source") or "Employz.ai", "message": "",
                      "created_at": crm.iso(d.get("created_at")), "stage_at": crm.iso(d.get("status_at") or d.get("updated_at")),
                      "updated_at": crm.iso(d.get("updated_at")), "expected_close": None, "follow_up_on": None, "follow_up_note": "",
                      "assignee_id": None, "assignee_name": d.get("assignee") or "", "lost_reason": "abandoned" if d.get("status") == "abandoned" else "",
                      "contact_id": d.get("contact_id"), "won_at": crm.iso(d.get("status_at")) if d.get("status") == "won" else None,
                      "quote": None, "invoice": None, "counts": {"note": 0, "call": 0, "meeting": 0, "all": 0},
                      "fields": d.get("fields") or {}, "hub_lead_id": d.get("hub_lead_id"), "open_url": link})
    open_ = [d for d in deals if d["status"] in crm.OPEN]
    t = crm.today_ist().isoformat()
    since30, week_start = now() - timedelta(days=30), now() - timedelta(days=7)

    def val(xs):
        return round(sum(float(x.get("value") or 0) for x in xs), 2)

    def after(iso_at, since):
        return bool(iso_at) and crm._aware(datetime.fromisoformat(iso_at)) >= since
    won30 = [d for d in deals if d["status"] == "won" and after(d["won_at"], since30)]
    columns = [{"id": s["id"], "name": s.get("name") or "Stage", "ff": "identification", "hint": "", "colour": STAGE_COLOURS[i % len(STAGE_COLOURS)],
                "count": sum(1 for d in open_ if d["pstage"] == s["id"]), "value": val([d for d in open_ if d["pstage"] == s["id"]])}
               for i, s in enumerate(p.get("stages") or [])]
    return {"deals": deals, "stages": columns, "built": True, "readonly": True, "source": "Employz.ai", "open_url": link,
            "pipeline": f"employz:{ext_id}", "pipeline_name": p.get("name") or "Pipeline", "pipelines": pipelines,
            "lost_reasons": [], "sync": await _sync_line(ctx.ws),
            "totals": {"open": len(open_), "open_value": val(open_), "won_30": len(won30), "won_30_value": val(won30), "followups_due": 0},
            "week": {"new": sum(1 for d in deals if after(d["created_at"], week_start)),
                     "won": sum(1 for d in deals if d["status"] == "won" and after(d["won_at"], week_start)),
                     "lost": sum(1 for d in deals if d["status"] == "lost" and after(d["stage_at"], week_start)), "moved": 0},
            "team": await _team(ctx), "sources": SOURCES, "today": t}


async def _sync_line(ws: str) -> dict:
    from .. import employz_import
    s = await employz_import.status(ws)
    return {"last_ok_at": s["last_ok_at"], "state": s["state"], "every_minutes": s["every_minutes"],
            "error": (s.get("error") or {}).get("message")}


@router.get("/deals")
async def list_deals(pipeline: str = "", ctx: Ctx = Depends(feature("pipeline"))):
    pipelines = await _pipelines(ctx)
    if pipeline.startswith("employz:"):
        return await _ext_board(ctx, pipeline.split(":", 1)[1][:60], pipelines)
    await crm.backfill(ctx.ws)
    stages = await crm.stages_of(ctx.ws)
    users = await _users(ctx)
    leads = [l async for l in db().leads.find({"owner_id": ctx.ws}).sort("created_at", -1).limit(1000)]
    quotes = await _quotes_for(ctx.ws, [l["_id"] for l in leads])
    deals = [_deal_view(l, stages, users, quotes) for l in leads]
    # how many notes, calls and meetings each deal has (the little counts on a board card)
    counts: dict = {}
    if leads:
        async for g in db().crm_activities.aggregate([
                {"$match": {"ws": ctx.ws, "lead_id": {"$in": [l["_id"] for l in leads]}}},
                {"$group": {"_id": {"l": "$lead_id", "k": "$kind"}, "n": {"$sum": 1}}}]):
            counts.setdefault(g["_id"]["l"], {})[g["_id"]["k"] or "note"] = g["n"]
    for d in deals:
        c = counts.get(d["id"]) or {}
        d["counts"] = {"note": c.get("note", 0), "call": c.get("call", 0), "meeting": c.get("meeting", 0),
                       "all": sum(c.values())}
    open_ = [d for d in deals if d["status"] in crm.OPEN]
    t = crm.today_ist().isoformat()
    since30 = now() - timedelta(days=30)
    week_start = now() - timedelta(days=7)
    won30 = [l for l in leads if l.get("status") == "won" and crm._aware(l.get("won_at") or l.get("updated_at") or l["created_at"]) >= since30]

    def val(xs):
        return round(sum(float(x.get("value") or 0) for x in xs), 2)
    columns = []
    for s in stages:
        col = [d for d in open_ if d["pstage"] == s["id"]]
        columns.append({**s, "count": len(col), "value": val(col)})
    week = {"new": sum(1 for l in leads if crm._aware(l["created_at"]) >= week_start),
            "won": sum(1 for l in leads if l.get("status") == "won" and crm._aware(l.get("won_at") or l["created_at"]) >= week_start),
            "lost": sum(1 for l in leads if l.get("status") == "lost" and crm._aware(l.get("updated_at") or l["created_at"]) >= week_start),
            "moved": sum(1 for l in leads if l.get("stage_at") and crm._aware(l["stage_at"]) >= week_start)}
    pipe = await crm.pipeline_of(ctx.ws)
    return {"deals": deals, "stages": columns, "built": bool(pipe), "pipelines": pipelines, "pipeline": "hub", "readonly": False,
            "lost_reasons": (pipe or {}).get("lost_reasons") or crm_tasks.BASE_REASONS["services"],
            "totals": {"open": len(open_), "open_value": val(open_), "won_30": len(won30), "won_30_value": val(won30),
                       "followups_due": sum(1 for d in open_ if d["follow_up_on"] and d["follow_up_on"] <= t)},
            "week": week, "team": await _team(ctx), "sources": SOURCES, "today": t}


async def _deal(ctx: Ctx, deal_id: str) -> dict:
    l = await db().leads.find_one({"_id": deal_id, "owner_id": ctx.ws})
    if not l:
        raise HTTPException(404, "Deal not found")
    return l


async def _deal_full(ctx: Ctx, l: dict) -> dict:
    stages = await crm.stages_of(ctx.ws)
    users = await _users(ctx)
    quotes = await _quotes_for(ctx.ws, [l["_id"]])
    out = _deal_view(l, stages, users, quotes)
    out["activities"] = [_act(a) async for a in db().crm_activities.find({"ws": ctx.ws, "lead_id": l["_id"]}).sort("at", -1).limit(100)]
    out["documents"] = [{"id": q["_id"], "kind": q["kind"], "number": q.get("number"), "status": q.get("status"),
                         "total": (q.get("totals") or {}).get("total"), "created_at": crm.iso(q.get("created_at"))}
                        async for q in db().quotes.find({"ws": ctx.ws, "lead_id": l["_id"]}).sort("created_at", -1).limit(20)]
    c = await db().contacts.find_one({"_id": l.get("contact_id"), "ws": ctx.ws}) if l.get("contact_id") else None
    out["contact"] = {"id": c["_id"], "name": c.get("name", ""), "phone": c.get("phone"), "email": c.get("email"),
                      "company": c.get("company", "")} if c else None
    return out


def _act(a: dict) -> dict:
    return {"id": a["_id"], "kind": a.get("kind"), "text": a.get("text", ""), "at": crm.iso(a.get("at")), "by_name": a.get("by_name", ""),
            "lead_id": a.get("lead_id"), "contact_id": a.get("contact_id")}


@router.post("/deals")
async def add_deal(body: DealIn, ctx: Ctx = Depends(feature("pipeline"))):
    stages = await crm.stages_of(ctx.ws)
    st = next((s for s in stages if s["id"] == body.pstage), stages[0])
    phone = crm.norm_phone(body.phone)
    if body.phone.strip() and not phone:
        raise HTTPException(400, "Enter a valid 10-digit mobile number, or leave it empty.")
    email = crm.norm_email(body.email)
    source = body.source if body.source in SOURCE_KEYS or body.source == "manual" else "manual"
    lead = {"_id": new_id(), "owner_id": ctx.ws, "site_id": None, "name": body.name.strip(), "phone": phone or "", "email": email or "",
            "company": body.company.strip(), "message": body.note.strip(), "status": "new", "stage": st["ff"], "pstage": st["id"],
            "source": source, "value": body.value, "expected_close": body.expected_close or None,
            "follow_up_on": body.follow_up_on or None, "follow_up_note": "", "created_at": now(), "stage_at": now(), "created_by": ctx.actor}
    await db().leads.insert_one(lead)
    contact = await crm.upsert_contact(ctx.ws, lead["name"], phone, email, lead["company"], source)
    if contact:
        await db().leads.update_one({"_id": lead["_id"]}, {"$set": {"contact_id": contact["_id"]}})
        lead["contact_id"] = contact["_id"]
        await crm.add_activity(ctx.ws, contact["_id"], lead["_id"], "created", f"Opportunity added: {lead['name']}" + (f" — {body.note.strip()[:200]}" if body.note.strip() else ""),
                               ctx.actor, _actor_name(ctx))
    if lead["follow_up_on"]:
        await crm.set_reminder(ctx.ws, "deal", lead["_id"], lead["follow_up_on"])
    dashboard_cache.bust(ctx.ws)
    await track("lead", ctx.ws, source=source)
    return await _deal_full(ctx, lead)


@router.get("/deals/{deal_id}")
async def get_deal(deal_id: str, ctx: Ctx = Depends(feature("pipeline"))):
    return await _deal_full(ctx, await _deal(ctx, deal_id))


async def _apply_deal_patch(ctx: Ctx, before: dict, patch: dict, note: str | None = None) -> dict:
    """Saves a deal change the way the Leads page always has (stage time, first action, won date, the agents' hooks,
    Employz.ai), plus the pipeline stage, and writes the contact's history."""
    from ..agents import hooks
    stages = await crm.stages_of(ctx.ws)
    if "pstage" in patch:
        st = next((s for s in stages if s["id"] == patch["pstage"]), None)
        if not st:
            raise HTTPException(400, "That stage isn't in your pipeline any more. Refresh the page.")
        patch["stage"] = st["ff"]
        if crm.pstage_of(before, stages) != st["id"]:
            patch["stage_at"] = now()
    if "stage" in patch and patch["stage"] == before.get("stage") and "stage_at" not in patch:
        patch.pop("stage")
    if patch.get("status") and patch["status"] != before.get("status"):
        patch["won_at"] = now() if patch["status"] == "won" else None
        if patch["status"] != "lost":
            patch["lost_reason"] = ""
        if patch["status"] != "new" and not before.get("first_action_at"):
            patch["first_action_at"] = now()
    for k in ("expected_close", "follow_up_on"):
        if k in patch and patch[k] == "":
            patch[k] = None
    if patch.get("assignee_id") is not None and patch["assignee_id"] and patch["assignee_id"] not in await _users(ctx):
        raise HTTPException(400, "Pick someone from your team.")
    patch["updated_at"], patch["updated_by"] = now(), ctx.actor
    await db().leads.update_one({"_id": before["_id"]}, {"$set": patch})
    after = {**before, **patch}
    who = _actor_name(ctx)
    cid = before.get("contact_id")
    if cid:
        if patch.get("status") and patch["status"] != before.get("status"):
            words = {"won": "Won", "lost": "Lost", "new": "Reopened", "contacted": "Marked contacted"}[patch["status"]]
            extra = f" ({patch['lost_reason']})" if patch["status"] == "lost" and patch.get("lost_reason") else (
                f" — {rupees(float(after['value']))}" if patch["status"] == "won" and after.get("value") else "")
            await crm.add_activity(ctx.ws, cid, before["_id"], patch["status"] if patch["status"] in ("won", "lost") else "reopened",
                                   f"{after.get('name') or 'Deal'}: {words}{extra}", ctx.actor, who)
        elif patch.get("stage_at"):
            name = next((s["name"] for s in stages if s["id"] == crm.pstage_of(after, stages)), "")
            await crm.add_activity(ctx.ws, cid, before["_id"], "stage", f"{after.get('name') or 'Deal'} moved to {name}", ctx.actor, who)
        if "follow_up_on" in patch and patch["follow_up_on"] != before.get("follow_up_on") and patch["follow_up_on"]:
            await crm.add_activity(ctx.ws, cid, before["_id"], "followup", f"Follow-up set for {crm.day_text(patch['follow_up_on'])}"
                                   + (f": {after.get('follow_up_note')}" if after.get("follow_up_note") else ""), ctx.actor, who)
        if note:
            await crm.add_activity(ctx.ws, cid, before["_id"], "note", note, ctx.actor, who)
    if "follow_up_on" in patch or (patch.get("status") in ("won", "lost")):
        await crm.set_reminder(ctx.ws, "deal", before["_id"], after.get("follow_up_on") if after.get("status") in crm.OPEN else None)
    if patch.get("status") and patch["status"] != before.get("status"):
        if patch["status"] == "won":
            await track("lead_won", ctx.ws)
        await hooks.on_lead_status(ctx.owner, after, patch["status"])
    if patch.get("stage_at") or (patch.get("status") and patch["status"] != before.get("status")):
        await hooks.on_lead_moved(ctx.owner, after)
    dashboard_cache.bust(ctx.ws)
    return after


@router.patch("/deals/{deal_id}")
async def edit_deal(deal_id: str, body: DealPatch, ctx: Ctx = Depends(feature("pipeline"))):
    before = await _deal(ctx, deal_id)
    raw = body.model_dump(exclude_unset=True)
    note = (raw.pop("note", None) or "").strip() or None
    patch = {k: v for k, v in raw.items() if v is not None or k in ("value", "assignee_id")}
    if "name" in patch:
        patch["name"] = patch["name"].strip()
    if "company" in patch:
        patch["company"] = (patch["company"] or "").strip()
    if patch.get("lost_reason") is not None:
        patch["lost_reason"] = patch["lost_reason"].strip()
    if note and not before.get("contact_id"):   # a note needs a contact to live on: make one from the deal
        c = await crm.upsert_contact(ctx.ws, before.get("name", ""), before.get("phone"), before.get("email"), before.get("company", ""),
                                     before.get("source", ""))
        if c:
            await db().leads.update_one({"_id": before["_id"]}, {"$set": {"contact_id": c["_id"]}})
            before["contact_id"] = c["_id"]
    await _apply_deal_patch(ctx, before, patch, note)
    return await _deal_full(ctx, await _deal(ctx, deal_id))


@router.post("/deals/{deal_id}/move")
async def move_deal(deal_id: str, body: MoveIn, ctx: Ctx = Depends(feature("pipeline"))):
    before = await _deal(ctx, deal_id)
    after = await _apply_deal_patch(ctx, before, {"pstage": body.pstage})
    stages = await crm.stages_of(ctx.ws)
    return _deal_view(after, stages, await _users(ctx))


# ───────────────────────── contacts ─────────────────────────
class ContactIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    phone: str = Field(default="", max_length=24)
    email: str = Field(default="", max_length=254)
    company: str = Field(default="", max_length=120)
    source: str = Field(default="manual", max_length=30)
    tags: list[str] = Field(default_factory=list, max_length=20)
    notes: str = Field(default="", max_length=2000)


class ContactPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=80)
    phone: str | None = Field(default=None, max_length=24)
    email: str | None = Field(default=None, max_length=254)
    company: str | None = Field(default=None, max_length=120)
    tags: list[str] | None = Field(default=None, max_length=20)
    notes: str | None = Field(default=None, max_length=2000)
    follow_up_on: str | None = Field(default=None, pattern=r"^(|\d{4}-\d{2}-\d{2})$")
    follow_up_note: str | None = Field(default=None, max_length=300)
    assignee_id: str | None = Field(default=None, max_length=40)

    _days = field_validator("follow_up_on")(_real_day)


class ActivityIn(BaseModel):
    kind: str = Field(pattern="^(call|meeting|note)$")
    text: str = Field(min_length=2, max_length=2000)
    lead_id: str | None = Field(default=None, max_length=40)


def _contact_view(c: dict, stats: dict | None = None, users: dict | None = None) -> dict:
    s = (stats or {}).get(c["_id"]) or {}
    ext = c.get("ext") or {}     # imported from Employz.ai: its custom fields and owner are shown, read-only
    return {"id": c["_id"], "name": c.get("name", ""), "phone": c.get("phone") or "", "email": c.get("email") or "",
            "company": c.get("company", ""), "company_id": c.get("company_id"), "source": c.get("source", ""),
            "source_label": crm.SOURCE_LABEL.get(c.get("source", ""), (c.get("source") or "").replace("_", " ")), "tags": c.get("tags") or [],
            "notes": c.get("notes", ""), "follow_up_on": c.get("follow_up_on"), "follow_up_note": c.get("follow_up_note", ""),
            "assignee_id": c.get("assignee_id"), "assignee_name": (users or {}).get(c.get("assignee_id"), "") if c.get("assignee_id") else "",
            "created_at": crm.iso(c.get("created_at")), "last_activity_at": crm.iso(c.get("last_activity_at")),
            "deals": s.get("deals", 0), "open_deals": s.get("open", 0), "open_value": round(s.get("open_value", 0.0), 2),
            "won_value": round(s.get("won_value", 0.0), 2),
            "from_crm": bool(ext.get("src")) and not ext.get("deleted_at"), "crm_managed": bool(ext.get("created")),
            "crm_fields": ext.get("fields") or {}, "crm_owner": ext.get("owner") or ""}


async def _deal_stats(ws: str, contact_ids: list[str]) -> dict:
    out: dict = {}
    if not contact_ids:
        return out
    async for l in db().leads.find({"owner_id": ws, "contact_id": {"$in": contact_ids}}, {"contact_id": 1, "status": 1, "value": 1}):
        s = out.setdefault(l["contact_id"], {"deals": 0, "open": 0, "open_value": 0.0, "won_value": 0.0})
        s["deals"] += 1
        v = float(l.get("value") or 0)
        if l.get("status") in crm.OPEN:
            s["open"] += 1
            s["open_value"] += v
        elif l.get("status") == "won":
            s["won_value"] += v
    return out


@router.get("/contacts")
async def list_contacts(q: str = "", source: str = "", tag: str = "", company: str = "", due: int = 0,
                        ctx: Ctx = Depends(feature("crm"))):
    await crm.backfill(ctx.ws)
    # a contact the Employz.ai import made, since deleted there, no longer shows
    query: dict = {"ws": ctx.ws, "$nor": [{"ext.created": True, "ext.deleted_at": {"$exists": True}}]}
    if q.strip():
        safe = re.escape(q.strip()[:60])
        digits = re.sub(r"\D", "", q)[-10:]
        ors = [{"name": {"$regex": safe, "$options": "i"}}, {"email": {"$regex": safe, "$options": "i"}},
               {"company": {"$regex": safe, "$options": "i"}}, {"tags": {"$regex": safe, "$options": "i"}}]
        if len(digits) >= 4:
            ors.append({"phone": {"$regex": re.escape(digits)}})
        query["$or"] = ors
    if source:
        query["source"] = source[:30]
    if tag:
        query["tags"] = tag[:30]
    if company:
        query["company_id"] = company[:40]
    if due:
        query["follow_up_on"] = {"$ne": None, "$lte": crm.today_ist().isoformat()}
    rows = [c async for c in db().contacts.find(query).sort("last_activity_at", -1).limit(1000)]
    stats = await _deal_stats(ctx.ws, [c["_id"] for c in rows])
    users = await _users(ctx)
    tags: dict[str, int] = {}
    async for c in db().contacts.find({"ws": ctx.ws, "tags.0": {"$exists": True}}, {"tags": 1}).limit(5000):
        for t in c.get("tags") or []:
            tags[t] = tags.get(t, 0) + 1
    return {"items": [_contact_view(c, stats, users) for c in rows], "total": await db().contacts.count_documents({"ws": ctx.ws, "$nor": query["$nor"]}),
            "tags": sorted(tags, key=lambda t: -tags[t])[:40], "sources": SOURCES, "team": await _team(ctx),
            "can_import": ctx.at_least("manager"), "today": crm.today_ist().isoformat()}


@router.post("/contacts")
async def add_contact(body: ContactIn, ctx: Ctx = Depends(feature("crm"))):
    phone, email = crm.norm_phone(body.phone), crm.norm_email(body.email)
    if body.phone.strip() and not phone:
        raise HTTPException(400, "Enter a valid 10-digit mobile number, or leave it empty.")
    if body.email.strip() and not email:
        raise HTTPException(400, "That email address doesn't look right.")
    if not phone and not email:
        raise HTTPException(400, "Add a mobile number or an email, so you can reach them.")
    if await crm.find_contact(ctx.ws, phone, email):
        raise HTTPException(409, "This person is already in your CRM. Search for them by name or number.")
    c = await crm.upsert_contact(ctx.ws, body.name, phone, email, body.company, body.source if body.source else "manual", body.tags, body.notes)
    await crm.add_activity(ctx.ws, c["_id"], None, "created", "Added to the CRM", ctx.actor, _actor_name(ctx))
    return _contact_view(c, users=await _users(ctx))


async def _contact(ctx: Ctx, cid: str) -> dict:
    c = await db().contacts.find_one({"_id": cid, "ws": ctx.ws})
    if not c:
        raise HTTPException(404, "Contact not found")
    return c


@router.get("/contacts/{cid}")
async def get_contact(cid: str, ctx: Ctx = Depends(feature("crm"))):
    c = await _contact(ctx, cid)
    users = await _users(ctx)
    stages = await crm.stages_of(ctx.ws)
    leads = [l async for l in db().leads.find({"owner_id": ctx.ws, "contact_id": cid}).sort("created_at", -1).limit(100)]
    quotes = await _quotes_for(ctx.ws, [l["_id"] for l in leads])
    stats = await _deal_stats(ctx.ws, [cid])
    out = _contact_view(c, stats, users)
    out["deals_list"] = [_deal_view(l, stages, users, quotes) for l in leads]
    out["stage_names"] = {s["id"]: s["name"] for s in stages}
    out["activities"] = [_act(a) async for a in db().crm_activities.find({"ws": ctx.ws, "contact_id": cid}).sort("at", -1).limit(200)]
    cust = await db().customers.find_one({"ws": ctx.ws, "phone": c["phone"]}) if c.get("phone") else None
    out["customer"] = {"id": cust["_id"], "tier": cust.get("tier", ""), "total_value": cust.get("total_value", 0), "purchases": cust.get("purchases", 0),
                       "last_purchase": cust.get("last_purchase"), "last_item": cust.get("last_item", "")} if cust else None
    cq = {"ws": ctx.ws, "$or": [{"lead_id": {"$in": [l["_id"] for l in leads]}}] + ([{"customer.phone": c["phone"]}] if c.get("phone") else [])}
    out["documents"] = [{"id": q["_id"], "kind": q["kind"], "number": q.get("number"), "status": q.get("status"),
                         "total": (q.get("totals") or {}).get("total"), "created_at": crm.iso(q.get("created_at"))}
                        async for q in db().quotes.find(cq).sort("created_at", -1).limit(30)]
    out["team"] = await _team(ctx)
    return out


@router.patch("/contacts/{cid}")
async def edit_contact(cid: str, body: ContactPatch, ctx: Ctx = Depends(feature("crm"))):
    c = await _contact(ctx, cid)
    raw = body.model_dump(exclude_unset=True)
    patch: dict = {}
    if raw.get("name"):
        patch["name"] = raw["name"].strip()
    if "phone" in raw:
        phone = crm.norm_phone(raw["phone"])
        if (raw["phone"] or "").strip() and not phone:
            raise HTTPException(400, "Enter a valid 10-digit mobile number, or leave it empty.")
        if phone and phone != c.get("phone") and await db().contacts.find_one({"ws": ctx.ws, "phone": phone, "_id": {"$ne": cid}}, {"_id": 1}):
            raise HTTPException(409, "Another contact already has this number.")
        patch["phone"] = phone
    if "email" in raw:
        email = crm.norm_email(raw["email"])
        if (raw["email"] or "").strip() and not email:
            raise HTTPException(400, "That email address doesn't look right.")
        if email and email != c.get("email") and await db().contacts.find_one({"ws": ctx.ws, "email": email, "_id": {"$ne": cid}}, {"_id": 1}):
            raise HTTPException(409, "Another contact already has this email.")
        patch["email"] = email
    if (patch.get("phone", c.get("phone")) or patch.get("email", c.get("email"))) is None:
        raise HTTPException(400, "Keep a mobile number or an email, so you can reach them.")
    if "company" in raw:
        name = (raw["company"] or "").strip()[:120]
        patch["company"], patch["company_id"] = name, await crm.company_for(ctx.ws, name) if name else None
    if raw.get("tags") is not None:
        patch["tags"] = crm.clean_tags(raw["tags"])
    if raw.get("notes") is not None:
        patch["notes"] = raw["notes"][:2000]
    if "follow_up_on" in raw:
        patch["follow_up_on"] = raw["follow_up_on"] or None
    if raw.get("follow_up_note") is not None:
        patch["follow_up_note"] = raw["follow_up_note"].strip()[:300]
    if "assignee_id" in raw:
        if raw["assignee_id"] and raw["assignee_id"] not in await _users(ctx):
            raise HTTPException(400, "Pick someone from your team.")
        patch["assignee_id"] = raw["assignee_id"] or None
    patch["updated_at"] = now()
    await db().contacts.update_one({"_id": cid}, {"$set": patch})
    if "follow_up_on" in patch and patch["follow_up_on"] != c.get("follow_up_on"):
        await crm.set_reminder(ctx.ws, "contact", cid, patch["follow_up_on"])
        if patch["follow_up_on"]:
            await crm.add_activity(ctx.ws, cid, None, "followup", f"Follow-up set for {crm.day_text(patch['follow_up_on'])}"
                                   + (f": {patch.get('follow_up_note') or c.get('follow_up_note', '')}" if (patch.get("follow_up_note") or c.get("follow_up_note")) else ""),
                                   ctx.actor, _actor_name(ctx))
    return await get_contact(cid, ctx)


@router.delete("/contacts/{cid}")
async def delete_contact(cid: str, ctx: Ctx = Depends(feature("crm", "manager"))):
    await _contact(ctx, cid)
    await db().contacts.delete_one({"_id": cid, "ws": ctx.ws})
    await db().leads.update_many({"owner_id": ctx.ws, "contact_id": cid}, {"$set": {"contact_id": None}})
    await crm.set_reminder(ctx.ws, "contact", cid, None)
    return {"ok": True}


@router.post("/contacts/{cid}/activities")
async def add_contact_activity(cid: str, body: ActivityIn, ctx: Ctx = Depends(feature("crm"))):
    await _contact(ctx, cid)
    lead_id = None
    if body.lead_id:
        lead = await db().leads.find_one({"_id": body.lead_id, "owner_id": ctx.ws, "contact_id": cid}, {"_id": 1, "status": 1, "first_action_at": 1})
        if lead:
            lead_id = lead["_id"]
            if body.kind in ("call", "meeting") and lead.get("status") == "new":   # a call made counts as the first reply
                await db().leads.update_one({"_id": lead_id}, {"$set": {"status": "contacted", "first_action_at": lead.get("first_action_at") or now(),
                                                                        "updated_at": now()}})
    words = {"call": "Call", "meeting": "Meeting", "note": "Note"}[body.kind]
    a = await crm.add_activity(ctx.ws, cid, lead_id, body.kind, f"{words}: {body.text.strip()}", ctx.actor, _actor_name(ctx))
    return _act(a)


@router.get("/companies")
async def list_companies(ctx: Ctx = Depends(feature("crm"))):
    comps = [c async for c in db().companies.find({"ws": ctx.ws}).sort("name_key", 1).limit(2000)]
    counts: dict = {}
    async for c in db().contacts.find({"ws": ctx.ws, "company_id": {"$ne": None}}, {"company_id": 1}):
        counts.setdefault(c["company_id"], []).append(c["_id"])
    out = []
    for co in comps:
        ids = counts.get(co["_id"], [])
        if not ids:
            continue
        stats = await _deal_stats(ctx.ws, ids)
        out.append({"id": co["_id"], "name": co["name"], "contacts": len(ids), "open_deals": sum(s["open"] for s in stats.values()),
                    "open_value": round(sum(s["open_value"] for s in stats.values()), 2),
                    "won_value": round(sum(s["won_value"] for s in stats.values()), 2)})
    return {"items": out}


@router.get("/followups")
async def followups(ctx: Ctx = Depends(feature("crm"))):
    return {**await crm.due_followups(ctx.ws), "date": crm.today_ist().isoformat()}


# ───────────────────────── import / export ─────────────────────────
COLUMNS = {"phone": ("phone", "mobile", "number", "contact no", "whatsapp", "cell"), "email": ("email", "e-mail", "mail"),
           "company": ("company", "firm", "organisation", "organization", "business"),
           "name": ("name", "customer", "client", "party", "contact", "person"), "source": ("source", "lead source", "channel"),
           "tags": ("tags", "tag", "label", "category", "segment"), "notes": ("notes", "note", "remarks", "comment")}


def _match(header: list) -> dict[str, int]:
    found: dict[str, int] = {}
    order = ("phone", "email", "company", "source", "tags", "notes", "name")   # "company name" is a company, not the person
    for i, h in enumerate(header):
        low = str(h or "").strip().lower()
        for key in order:
            if key not in found and any(w in low for w in COLUMNS[key]):
                found[key] = i
                break
    return found


@router.post("/import")
async def import_contacts(request: Request, ctx: Ctx = Depends(feature("crm", "manager"))):
    """A CSV or Excel list: name plus a phone or an email per row. Existing people (same phone or email) are matched, not
    doubled; their empty fields are filled in and tags added."""
    form = await request.form()
    try:
        f = form.get("file")
        if not isinstance(f, UploadFile):
            raise HTTPException(400, "Choose an Excel or CSV file.")
        data = await f.read(5 * 1024 * 1024 + 1)
        name = (f.filename or "").lower()
    finally:
        await form.close()
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(413, "Keep the file under 5 MB.")
    if name.endswith((".xlsx", ".xlsm")):
        import openpyxl
        try:
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception:  # noqa: BLE001
            raise HTTPException(400, "We couldn't open that Excel file. Save it again and retry.")
        rows = [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)][:5001]
    elif name.endswith(".csv"):
        rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig", errors="replace"))))[:5001]
    else:
        raise HTTPException(415, "Upload an Excel (.xlsx) or CSV file.")
    if len(rows) < 2:
        raise HTTPException(400, "The file has no rows.")
    cols = _match(rows[0])
    if "name" not in cols or ("phone" not in cols and "email" not in cols):
        raise HTTPException(400, "We need a name column and a phone or email column in the first row.")
    added = matched = skipped = 0
    for r in rows[1:]:
        def get(k):
            return r[cols[k]] if k in cols and cols[k] < len(r) and r[cols[k]] is not None else ""
        nm = str(get("name")).strip()[:80]
        phone, email = crm.norm_phone(get("phone")), crm.norm_email(get("email"))
        if len(nm) < 2 or (not phone and not email):
            skipped += 1
            continue
        tags = [t for t in re.split(r"[,;|]", str(get("tags"))) if t.strip()]
        src = str(get("source")).strip().lower().replace(" ", "_")[:30] or "import"
        existing = await crm.find_contact(ctx.ws, phone, email)
        c = await crm.upsert_contact(ctx.ws, nm, phone, email, str(get("company")).strip(), src, tags, str(get("notes")).strip())
        if not c:
            skipped += 1
            continue
        if existing:
            if tags:
                await db().contacts.update_one({"_id": c["_id"]}, {"$set": {"tags": crm.clean_tags((c.get("tags") or []) + tags)}})
            matched += 1
        else:
            added += 1
    await track("crm_imported", ctx.ws, added=added)
    return {"added": added, "matched": matched, "skipped": skipped}


def _cell(v):
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@") and not re.match(r"^\+\d{10,15}$", s) else s   # no formulas from customer text


@router.get("/export")
async def export(what: str = "contacts", fmt: str = "csv", ctx: Ctx = Depends(feature("crm", "manager"))):
    if what == "deals":
        stages = await crm.stages_of(ctx.ws)
        names = {s["id"]: s["name"] for s in stages}
        header = ["Name", "Phone", "Email", "Company", "Value (₹)", "Stage", "Status", "Source", "Expected close", "Follow-up", "Lost reason", "Created"]
        rows = []
        async for l in db().leads.find({"owner_id": ctx.ws}).sort("created_at", -1).limit(20000):
            rows.append([l.get("name", ""), l.get("phone", ""), l.get("email", ""), l.get("company", ""), l.get("value") or "",
                         names.get(crm.pstage_of(l, stages), ""), l.get("status", ""), crm.SOURCE_LABEL.get(l.get("source", ""), l.get("source", "")),
                         l.get("expected_close") or "", l.get("follow_up_on") or "", l.get("lost_reason", ""),
                         (crm.iso(l.get("created_at")) or "")[:10]])
    else:
        what = "contacts"
        header = ["Name", "Phone", "Email", "Company", "Source", "Tags", "Notes", "Follow-up", "Open deals", "Open value (₹)", "Won value (₹)", "Added"]
        contacts = [c async for c in db().contacts.find({"ws": ctx.ws}).sort("name", 1).limit(20000)]
        stats = await _deal_stats(ctx.ws, [c["_id"] for c in contacts])
        rows = []
        for c in contacts:
            s = stats.get(c["_id"]) or {}
            rows.append([c.get("name", ""), c.get("phone") or "", c.get("email") or "", c.get("company", ""),
                         crm.SOURCE_LABEL.get(c.get("source", ""), c.get("source", "")), ", ".join(c.get("tags") or []), c.get("notes", ""),
                         c.get("follow_up_on") or "", s.get("open", 0), round(s.get("open_value", 0), 2), round(s.get("won_value", 0), 2),
                         (crm.iso(c.get("created_at")) or "")[:10]])
    stamp = crm.today_ist().isoformat()
    if fmt == "xlsx":
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = what.title()
        ws.append(header)
        for r in rows:
            ws.append([_cell(v) if isinstance(v, str) else v for v in r])
        buf = io.BytesIO()
        wb.save(buf)
        return Response(buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{what}-{stamp}.xlsx"'})
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(header)
    for r in rows:
        w.writerow([_cell(v) for v in r])
    return Response("﻿" + out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{what}-{stamp}.csv"'})


async def ensure_indexes() -> None:
    await crm.ensure_indexes()

