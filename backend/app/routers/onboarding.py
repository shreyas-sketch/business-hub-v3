"""
Admin → Onboarding (v2.3): the team's desk for every member who came from the event or paid.

- One list of owners with where their access comes from (event, paid until, ended, switched off) and a setup checklist the
  hub works out by itself: Knowledge Hub, Engine Audit, GST/TAN, website live, lead magnet live, pipeline, WhatsApp tool,
  first inquiry.
- The team's own notes on each member: the onboarding call date, who is handling them, the stage, free notes.
- Mark paid: members pay outside the hub (Razorpay). The team records the payment here — a month (₹1,999) or a year
  (₹19,999) of OwnerDesk Membership — and the member's plan opens at once.
- Extra AI tokens (the event bonus), and switching an account off or on (nothing is deleted).
Every change is logged (events + config_changes) with who made it.

onboarding {_id: owner id, stage, call_at, handler, notes, payments [{period, ref, amount_minor, until, at, by}], updated_at, by}
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import knowledge, plans
from ..connections import ready as connection_ready
from ..db import IST, db, now
from ..security import require_admin
from ..services import new_id, notify, track

router = APIRouter(prefix="/api/admin/onboarding", dependencies=[Depends(require_admin)])

STAGES = ("new", "call_booked", "onboarding", "set_up", "needs_help")
STAGE_LABEL = {"new": "New", "call_booked": "Call booked", "onboarding": "Onboarding", "set_up": "Set up", "needs_help": "Needs help"}
CHECKS = [("knowledge", "Knowledge Hub filled"), ("audit", "Engine Audit taken"), ("gst_tan", "GST and TAN added"),
          ("website", "Website live"), ("magnet", "Lead magnet live"), ("pipeline", "Pipeline built"),
          ("whatsapp", "WhatsApp tool connected"), ("inquiry", "First inquiry")]
LIST_LIMIT = 500
PERIOD_DAYS = {"month": 31, "year": 366}     # a month or a year from today (or from the current paid date, if later)


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
    query: dict = {"team_of": {"$in": [None, ""]}, "role": {"$ne": "admin"}}
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
    counts = {s: sum(1 for r in rows if r["access"]["status"] == s) for s in ("event", "paid", "ended", "free", "off")}
    return {"members": rows, "counts": counts, "checks": [{"key": k, "label": label} for k, label in CHECKS],
            "stages": [{"key": s, "label": STAGE_LABEL[s]} for s in STAGES],
            "prices": {"month": plans.PLANS["lite"]["price_minor"], "year": plans.PLANS["lite"].get("price_year_minor", 0)}}


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
