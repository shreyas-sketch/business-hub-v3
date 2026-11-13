"""
Admin → Onboarding (v2.3): the team's desk for every member who came from the event or paid.

- One list of owners with where their access comes from (event, paid until, ended, switched off) and a setup checklist the
  hub works out by itself: Knowledge Hub, Engine Audit, GST/TAN, website live, lead magnet live, pipeline, WhatsApp tool,
  first inquiry.
- The team's own notes on each member: the onboarding call date, who is handling them, the stage, free notes.
- Mark paid: members pay outside the hub (Razorpay). The team records the payment here — a month (₹1,999) or a year
  (₹19,999) of OwnerDesk Membership — and the member's plan opens at once.
- Extra AI tokens (the event bonus), and switching an account off or on (nothing is deleted).
- Download the members as a CSV to share: name, mobile, email, business, access and the agents they have used, for one access level or all.
Every change is logged (events + config_changes) with who made it.

onboarding {_id: owner id, stage, call_at, handler, notes, payments [{period, ref, amount_minor, until, at, by}], updated_at, by}
"""
import csv
import io
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import kits, knowledge, plans, tools
from ..agents import catalog
from ..agents.office import STAFF
from ..connections import ready as connection_ready
from ..db import IST, db, now
from ..security import require_admin
from ..services import new_id, notify, track
from .crm import _cell

router = APIRouter(prefix="/api/admin/onboarding", dependencies=[Depends(require_admin)])

STAGES = ("new", "call_booked", "onboarding", "set_up", "needs_help")
STAGE_LABEL = {"new": "New", "call_booked": "Call booked", "onboarding": "Onboarding", "set_up": "Set up", "needs_help": "Needs help"}
CHECKS = [("knowledge", "Knowledge Hub filled"), ("audit", "Engine Audit taken"), ("gst_tan", "GST and TAN added"),
          ("website", "Website live"), ("magnet", "Lead magnet live"), ("pipeline", "Pipeline built"),
          ("whatsapp", "WhatsApp tool connected"), ("inquiry", "First inquiry")]
LIST_LIMIT = 500
PERIOD_DAYS = {"month": 31, "year": 366}     # a month or a year from today (or from the current paid date, if later)
ACCESS_STATUSES = ("starter", "threeday", "welcome", "event", "paid", "ended", "free", "off")
OWNERS = {"team_of": {"$in": [None, ""]}, "role": {"$ne": "admin"}}     # not team logins or admins
EXPORT_FILE = {"": "everyone", "starter": "l0-starter", "threeday": "3-day", "welcome": "full-hub", "off": "switched-off"}
EXPORT_CHUNK = 1000
# Saved work that shows an agent was used: the records the setup checklist reads, so a tick on a member's card is always in
# the file, however the record got there. (collection, field holding the owner, what counts, feature)
SAVED_WORK = (("engine_audits", "owner_id", {}, "engine_audit"), ("sites", "owner_id", {"status": "live"}, "website"),
              ("leads", "owner_id", {}, "inquiry_desk"), ("magnet_files", "owner_id", {"published": True}, "lead_magnet"),
              ("lead_magnets", "_id", {"published": True}, "lead_magnet"), ("pipelines", "_id", {"stages.0": {"$exists": True}}, "pipeline"),
              ("levers", "_id", {}, "revenue_levers"))
# What each kind of AI run (runs.kind) or activity (events.type) counts as: a feature key, shown under its menu name as the
# admin has it now, or plain words where no menu item fits. Only what a member did, or an agent did for them: opening a page
# records nothing, and signups, logins and billing don't appear here.
AGENT_NAMES = {
    # Start
    "engine_audit": "engine_audit", "knowledge_saved": "business_brain", "knowledge_update": "business_brain", "knowledge_fact": "business_brain",
    "knowledge_refresh": "business_brain", "knowledge_keep": "business_brain", "read_website": "business_brain", "read_words": "business_brain",
    "website_read": "business_brain", "voice_onboarding": "business_brain", "score_taken": "business_score",
    "event_saved": "Event pack", "day1_saved": "Day-1 report",
    # Lead
    "site": "website", "site_prompt": "website", "site_prompt_applied": "website", "site_built": "website", "site_live": "website", "inputs_saved": "website",
    "lead_magnet": "lead_magnet", "magnet_build": "lead_magnet", "magnet_built": "lead_magnet", "magnet_ideas": "lead_magnet",
    "lead_magnet_live": "lead_magnet", "lead_magnet_uploaded": "lead_magnet",
    # Sales
    "multiplier_ideas": "revenue_levers", "lead_ideas": "lead_ideas", "offer_ladder": "offer_ladder", "quote": "quotations", "invoice_entry": "quotations",
    "invoice_style": "quotations", "invoice": "gateway_invoicing",
    # Conversion
    "lead": "inquiry_desk", "inquiry_settings": "inquiry_desk", "inquiry_template_sent": "inquiry_desk", "proposal": "Automatic proposal",
    "lead_won": "crm", "crm_imported": "crm", "pipeline": "pipeline", "pipeline_built": "pipeline", "leaks": "funnel_leaks", "wa_reply": "whatsapp_ai",
    "customers_imported": "customers", "call_review": "voice_agent", "campaign": "campaigns", "gym": "training_gym", "gym_score": "training_gym", "gym_scored": "training_gym",
    # Operations
    "sop_map": "sop_library", "sop_set_written": "sop_library", "kit:sop_scripts": "sop_library", "compliance_explain": "compliance_calendar",
    "compliance_setup": "compliance_calendar", "compliance_item": "compliance_calendar", "compliance_done": "compliance_calendar", "due_added": "money_owed",
    "due_paid": "money_owed", "due_part_paid": "money_owed", "cash_item": "cash_flow", "magic_number": "magic_number", "gaps_scan": "gaps_scan",
    "week": "week", "week_planned": "week", "week_checkin": "week", "daily_done": "week", "goal_coach": "goals", "goal_created": "goals",
    "meeting_created": "meetings", "standup": "Morning standup", "office_plan": "AI office: Chief of Staff", "office_run": "AI office: Chief of Staff",
    "report": "AI office: Chief of Staff", "voice_note": "Voice notes", "role_hired": "workforce",
    # Brand
    "brand": "brand_message", "brand_chosen": "brand_message", "brand_kit": "brand_kit", "design": "brand_kit", "brand_design": "brand_kit",
    "social_week": "social_media", "social_starter": "social_media", "social_image": "social_media", "social_posted": "social_media",
    "calendar": "content_calendar", "brochure": "brochures", "research_content": "market_research", "research_companies": "market_research",
    "competitor_read": "competitor_watch", "competitor_summary": "competitor_watch", "competitor_added": "competitor_watch", "competitor_check": "competitor_watch",
    "reputation_reply": "reputation", "reputation_search": "reputation", "review_asked": "reputation", "review_pasted": "reputation", "review_replied": "reputation",
    # Data
    "key_person:owner_questions": "key_person", "key_person:questionnaire": "key_person", "key_person:skill": "key_person", "key_person_added": "key_person",
    "key_person_answered": "key_person", "key_person_skill": "key_person", "expenses:advice": "expenses", "expenses:suggest": "expenses", "expense_added": "expenses",
    "expense_approved": "expenses", "expense_advice": "expenses", "expenses_from_bank": "expenses", "bank_statement_read": "expenses", "sales_upload": "getting_paid",
    "tenders:help": "tender_finder", "tenders:words": "tender_finder", "tender_help": "tender_finder", "tenders_searched": "tender_finder",
    # AI Writer
    "answer": "ai_writer", "polish": "ai_writer", "posts": "ai_writer", "reply": "ai_writer", "brain_answer": "company_brain",
}


def _iso(v) -> str | None:
    return v.isoformat() if isinstance(v, datetime) else None


def _access(u: dict) -> dict:
    """Where the member's access comes from right now, in the team's words."""
    paid_until = (u.get("access") or {}).get("lite")
    trial = u.get("trial") or {}
    if u.get("disabled"):
        status, label = "off", "Switched off"
    elif plans.is_paid(u):
        status, label = "paid", "Paid"
    elif plans.starter_only(u):
        status, label = "starter", "L0 starter"
    elif plans.threeday_only(u):
        status, label = "threeday", "3-Day"
    elif plans.welcome_active(u):
        status, label = "welcome", "Full hub"
    elif plans.event_trial(u):
        status, label = "event", "Event access"
    elif trial.get("reason") == "event":
        status, label = "ended", "Event ended"
    else:
        status, label = "free", "Free"
    return {"status": status, "label": label, "paid_until": _iso(paid_until), "event_until": _iso(trial.get("until")) if trial.get("reason") == "event" and not plans.open_ended(trial.get("until")) else None,
            "cohort": trial.get("cohort") or u.get("cohort"), "plan_name": plans.PLANS[plans.effective_plan(u)]["name"],
            "event_day": plans.event_day(u)}


async def _checklists(ids: list[str]) -> dict[str, dict]:
    """The setup checklist for many owners with one query per collection."""
    d = db()
    out = {i: {k: False for k, _ in CHECKS} for i in ids}
    if not ids:
        return out
    from .dashboard import LANDING_AT
    async for b in d.businesses.find({"owner_id": {"$in": ids}}):
        o = out[b["owner_id"]]
        o["knowledge"] = (await knowledge.completeness_for(b["owner_id"], b))["percent"] >= LANDING_AT
        ident = b.get("identity") or {}
        o["gst_tan"] = "has_tan" in ident and (bool(ident.get("gstin")) or ident.get("gst_registered") is False)
    for i in await d.engine_audits.distinct("owner_id", {"owner_id": {"$in": ids}}):
        out[i]["audit"] = True
    for i in await d.sites.distinct("owner_id", {"owner_id": {"$in": ids}, "status": "live"}):
        out[i]["website"] = True
    for i in await d.magnet_files.distinct("owner_id", {"owner_id": {"$in": ids}, "published": True}):
        out[i]["magnet"] = True
    for i in await d.lead_magnets.distinct("_id", {"_id": {"$in": ids}, "published": True}):
        out[i]["magnet"] = True
    async for p in d.pipelines.find({"_id": {"$in": ids}}, {"stages": 1}):
        out[p["_id"]]["pipeline"] = bool(p.get("stages"))
    async for c in d.connections.find({"_id": {"$in": ids}}, {"whatsapp": 1}):
        out[c["_id"]]["whatsapp"] = connection_ready("whatsapp", c.get("whatsapp"))
    for i in await d.leads.distinct("owner_id", {"owner_id": {"$in": ids}}):
        out[i]["inquiry"] = True
    return out


def _record_view(r: dict | None) -> dict:
    r = r or {}
    call = r.get("call_at")
    return {"stage": r.get("stage", "new"), "stage_label": STAGE_LABEL.get(r.get("stage", "new"), "New"),
            "call_at": call.astimezone(IST).isoformat() if isinstance(call, datetime) else None,      # India time, for the form
            "handler": r.get("handler", ""), "notes": r.get("notes", ""),
            "payments": [{**{k: v for k, v in p.items() if k not in ("at", "until")}, "at": _iso(p.get("at")), "until": _iso(p.get("until"))}
                         for p in (r.get("payments") or [])][-10:]}


@router.get("")
async def members(q: str = "", status: str = "", cohort: str = ""):
    """Every owner (not team logins or admins), newest first, with access, checklist and the team's notes."""
    d = db()
    query: dict = dict(OWNERS)
    if cohort:
        query["$or"] = [{"cohort": cohort.strip().upper()[:40]}, {"trial.cohort": cohort.strip().upper()[:40]}]
    users = [u async for u in d.users.find(query).sort("created_at", -1).limit(LIST_LIMIT)]
    ids = [u["_id"] for u in users]
    names = {b["owner_id"]: b async for b in d.businesses.find({"owner_id": {"$in": ids}}, {"owner_id": 1, "name": 1, "city": 1})}
    records = {r["_id"]: r async for r in d.onboarding.find({"_id": {"$in": ids}})}
    checks = await _checklists(ids)
    rows = []
    needle = q.strip().lower()
    for u in users:
        b = names.get(u["_id"]) or {}
        acc = _access(u)
        if status and acc["status"] != status:
            continue
        row = {"id": u["_id"], "name": u.get("name", ""), "phone": u.get("phone", ""), "email": u.get("email", ""), "business": b.get("name", ""),
               "city": b.get("city", ""), "created_at": _iso(u.get("created_at")), "access": acc,
               "checks": checks.get(u["_id"], {}), "done": sum(1 for v in checks.get(u["_id"], {}).values() if v),
               "bonus_tokens": u.get("bonus_runs", 0), **_record_view(records.get(u["_id"]))}
        if needle and needle not in " ".join(str(row[k]) for k in ("name", "phone", "email", "business", "city")).lower():
            continue
        rows.append(row)
    counts = {s: sum(1 for r in rows if r["access"]["status"] == s) for s in ACCESS_STATUSES}
    return {"members": rows, "counts": counts, "checks": [{"key": k, "label": label} for k, label in CHECKS],
            "stages": [{"key": s, "label": STAGE_LABEL[s]} for s in STAGES],
            "prices": {"month": plans.PLANS["lite"]["price_minor"], "year": plans.PLANS["lite"].get("price_year_minor", 0)}}


def _agent_name(key: str) -> str:
    """What a member knows an agent as, from a kind of AI run, an activity or an agent's key. The menu's name wins, so the same
    agent never shows twice under two names."""
    if key in AGENT_NAMES:
        name = AGENT_NAMES[key]
        return plans.label_of(name) if name in plans.META else name
    head, _, rest = key.partition(":")
    if head == "tool" and rest in tools.TOOLS:
        return tools.TOOLS[rest].name
    if head == "kit" and rest in kits.KINDS:
        return plans.label_of(kits.KINDS[rest].feature)
    if key.startswith("office_") and key[len("office_"):] in STAFF:
        return f"AI office: {STAFF[key[len('office_'):]]['title']}"
    spec = catalog.get(key)
    return spec.name if spec else key.replace(":", " ").replace("_", " ").capitalize()


def _runs_for_everyone(key: str) -> bool:
    """A scheduled check that only messages the owner runs by itself for every account, so it doesn't show they used it."""
    spec = catalog.get(key)
    return bool(spec) and spec.trigger == "schedule" and not spec.customer_facing


async def _agents_used(ids: list[str]) -> dict[str, set[str]]:
    """Each owner's agents, from four records: AI work that finished (failed and refunded runs don't count), actions agents
    carried out, what the owner did in the hub, and the work saved there. Most of the L0 pages (Engine Audit, Inquiries, Social media...) can be used
    without any AI run, so runs alone would leave those members looking as if they had used nothing."""
    d = db()
    used: dict[str, set[str]] = {i: set() for i in ids}
    for start in range(0, len(ids), EXPORT_CHUNK):
        part = ids[start:start + EXPORT_CHUNK]
        async for r in d.runs.aggregate([{"$match": {"user_id": {"$in": part}, "status": "done"}},
                                         {"$group": {"_id": {"owner": "$user_id", "kind": "$kind"}}}]):
            used[r["_id"]["owner"]].add(_agent_name(r["_id"]["kind"]))
        async for r in d.agent_log.aggregate([{"$match": {"ws": {"$in": part}, "status": "done"}},
                                              {"$group": {"_id": {"owner": "$ws", "agent": "$agent"}}}]):
            if not _runs_for_everyone(r["_id"]["agent"]):
                used[r["_id"]["owner"]].add(_agent_name(r["_id"]["agent"]))
        async for r in d.events.aggregate([{"$match": {"user_id": {"$in": part}, "type": {"$in": list(AGENT_NAMES)}}},
                                           {"$group": {"_id": {"owner": "$user_id", "type": "$type"}}}]):
            used[r["_id"]["owner"]].add(_agent_name(r["_id"]["type"]))
        for collection, owner_field, match, feature in SAVED_WORK:
            for owner in await d[collection].distinct(owner_field, {owner_field: {"$in": part}, **match}):
                used[owner].add(plans.label_of(feature))
    return used


def _mobile(u: dict) -> str:
    """The member's mobile, the way the hub shows it (98200 12345), which Excel keeps as text instead of 9.19E+11. A number
    given up to the person who proved it (auth._release_number) isn't this member's any more, so it's left out."""
    phone = str(u.get("phone") or "").strip()
    if phone.startswith("released:"):
        return ""
    digits = phone.removeprefix("+")
    if digits.isdigit() and len(digits) == 12 and digits.startswith("91"):
        return f"{digits[2:7]} {digits[7:]}"
    return _cell(phone)


@router.get("/export.csv")
async def export_csv(status: str = "", q: str = "", admin: dict = Depends(require_admin)):
    """The members as a CSV to share, for one access level or all. Unlike the list on screen, this is never cut off at
    LIST_LIMIT. An owner who signed up by phone has no login email, so their business's contact email is used; the mobile
    login doesn't ask for a name either, so the business is what identifies them in the file."""
    if status and status not in ACCESS_STATUSES:
        raise HTTPException(400, "Pick an access level from the list.")
    d = db()
    users = [u async for u in d.users.find(OWNERS).sort("created_at", -1)]
    businesses = {}
    for start in range(0, len(users), EXPORT_CHUNK):
        part = [u["_id"] for u in users[start:start + EXPORT_CHUNK]]
        async for b in d.businesses.find({"owner_id": {"$in": part}}, {"owner_id": 1, "name": 1, "city": 1, "email": 1}):
            businesses[b["owner_id"]] = b
    needle = q.strip().lower()
    rows = []
    for u in users:
        b = businesses.get(u["_id"]) or {}
        access = _access(u)
        email = u.get("email") or b.get("email") or ""
        if status and access["status"] != status:
            continue
        if needle and needle not in " ".join(str(v) for v in (u.get("name", ""), u.get("phone", ""), email, b.get("name", ""), b.get("city", ""))).lower():
            continue
        rows.append((u, email, b.get("name", ""), access["label"]))
    used = await _agents_used([r[0]["_id"] for r in rows])
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Name", "Mobile", "Email", "Business", "Access", "Agents used"])
    for u, email, business, label in rows:
        # what owners typed goes into a file others will open: strip it first, so a space can't hide a formula from _cell
        w.writerow([_cell(str(u.get("name") or "").strip()), _mobile(u), _cell(str(email).strip()), _cell(str(business or "").strip()), label,
                    "; ".join(sorted(used[u["_id"]]))])
    await track("onboarding_export", None, by=admin["_id"], status=status, q=bool(needle), members=len(rows))
    stamp = now().astimezone(IST).strftime("%Y-%m-%d")
    name = f"ownerdesk-members-{EXPORT_FILE.get(status, status)}-{stamp}.csv"
    return Response("\ufeff" + out.getvalue(), media_type="text/csv; charset=utf-8",      # the mark tells Excel it's UTF-8
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


async def _owner(uid: str) -> dict:
    u = await db().users.find_one({"_id": uid})
    if not u or u.get("team_of"):
        raise HTTPException(404, "Member not found")
    return u


class RecordIn(BaseModel):
    stage: str | None = Field(default=None, pattern="^(new|call_booked|onboarding|set_up|needs_help)$")
    call_at: str | None = Field(default=None, max_length=25)        # "2026-10-28T11:30" (India time) or "" to clear
    handler: str | None = Field(default=None, max_length=60)
    notes: str | None = Field(default=None, max_length=4000)


@router.patch("/{uid}")
async def save_record(uid: str, body: RecordIn, admin: dict = Depends(require_admin)):
    await _owner(uid)
    patch = body.model_dump(exclude_none=True)
    if "call_at" in patch:
        raw = patch["call_at"].strip()
        if raw:
            try:
                at = datetime.fromisoformat(raw)
            except ValueError:
                raise HTTPException(400, "Pick the call's date and time again.")
            patch["call_at"] = at if at.tzinfo else at.replace(tzinfo=IST)
        else:
            patch["call_at"] = None
    await db().onboarding.update_one({"_id": uid}, {"$set": {**patch, "updated_at": now(), "by": admin["_id"]}}, upsert=True)
    await track("onboarding_note", uid, by=admin["_id"], fields=sorted(patch))
    return _record_view(await db().onboarding.find_one({"_id": uid}))


class PaidIn(BaseModel):
    period: str = Field(pattern="^(month|year)$")
    ref: str = Field(default="", max_length=80)               # the Razorpay payment id or a note
    amount_minor: int | None = Field(default=None, ge=0, le=100_000_000)


@router.post("/{uid}/paid")
async def mark_paid(uid: str, body: PaidIn, admin: dict = Depends(require_admin)):
    """Records a payment made outside the hub and opens OwnerDesk Membership for a month or a year. A second payment adds
    on from the current paid date, so nobody loses days."""
    u = await _owner(uid)
    current = (u.get("access") or {}).get("lite")
    start = max(current, now()) if isinstance(current, datetime) else now()
    until = start + timedelta(days=PERIOD_DAYS[body.period])
    lite = plans.PLANS["lite"]
    amount = body.amount_minor if body.amount_minor is not None else (lite["price_minor"] if body.period == "month" else lite.get("price_year_minor", 0))
    payment = {"id": new_id(), "period": body.period, "ref": body.ref.strip(), "amount_minor": amount, "until": until, "at": now(), "by": admin["_id"]}
    await db().users.update_one({"_id": uid}, {"$set": {"access.lite": until}})
    await db().onboarding.update_one({"_id": uid}, {"$push": {"payments": payment}, "$set": {"updated_at": now(), "by": admin["_id"]}}, upsert=True)
    await track("onboarding_paid", uid, by=admin["_id"], period=body.period, amount_minor=amount)
    await notify(uid, f"Welcome to {lite['name']}. Your hub is open until {until.astimezone(IST).strftime('%d %b %Y')}.")
    return {"paid_until": until.isoformat(), "access": _access(await _owner(uid))}


class OffIn(BaseModel):
    off: bool


@router.post("/{uid}/off")
async def switch(uid: str, body: OffIn, admin: dict = Depends(require_admin)):
    """Switches an account off (they can't log in) or back on. Nothing is deleted."""
    u = await _owner(uid)
    if u.get("role") == "admin":
        raise HTTPException(400, "An admin account can't be switched off here.")
    await db().users.update_one({"_id": uid}, {"$set": {"disabled": body.off}, "$inc": {"session_version": 1}})
    await track("onboarding_off" if body.off else "onboarding_on", uid, by=admin["_id"])
    return {"access": _access(await _owner(uid))}


class TokensIn(BaseModel):
    tokens: int = Field(ge=-1000, le=5000)


@router.post("/{uid}/tokens")
async def add_tokens(uid: str, body: TokensIn, admin: dict = Depends(require_admin)):
    """Extra AI tokens on top of the month's (the event bonus). They stay until used."""
    u = await _owner(uid)
    left = max(0, u.get("bonus_runs", 0) + body.tokens)
    await db().users.update_one({"_id": uid}, {"$set": {"bonus_runs": left}})
    await track("onboarding_tokens", uid, by=admin["_id"], tokens=body.tokens)
    if body.tokens > 0:
        await notify(uid, f"+{body.tokens} AI tokens added to your hub.")
    return {"bonus_tokens": left}


async def ensure_indexes() -> None:
    await db().onboarding.create_index("stage")
    await db().events.create_index([("user_id", 1), ("type", 1)])     # one member's activity, for the CSV
