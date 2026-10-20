"""
Automatic payment follow-ups (3.8): switch on once after seeing one sample of each stage; exclude customers; see each
amount's reminder history; mark that a customer replied (which stops the chase). The daily run is the
`payment_followups_auto` automation (10am India time).
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import followups, mailer, plans
from ..agents import catalog
from ..agents.catalog import Skip
from ..config import settings
from ..context import Ctx, feature
from ..db import db, now, public
from ..services import track

router = APIRouter(prefix="/api")


class ApproveIn(BaseModel):
    hash: str = Field(min_length=8, max_length=32)       # the samples the owner saw
    whatsapp: bool = False                                # Growth Mentorship and up: also from their own WhatsApp number


class ExcludeIn(BaseModel):
    due_id: str = Field(min_length=4, max_length=40)
    exclude: bool = True


@router.get("/followups/auto")
async def get_auto(ctx: Ctx = Depends(feature("auto_reminders", "owner"))):
    st = await followups.get_state(ctx.ws)
    s = await followups.samples(ctx.ws)
    no_email = await db().dues.count_documents({"ws": ctx.ws, "status": "due", "email": {"$in": [None, ""]}})
    with_email = await db().dues.count_documents({"ws": ctx.ws, "status": "due", "email": {"$nin": [None, ""]}})
    return {"on": bool(st.get("on")), "approved_at": st["approved_at"].isoformat() if st.get("approved_at") else None,
            "approved_current": st.get("approved_hash") == s["hash"], "samples": s["samples"], "whatsapp_sample": s["whatsapp_sample"], "hash": s["hash"],
            "whatsapp": bool(st.get("whatsapp")), "whatsapp_available": followups.whatsapp_auto_allowed(ctx.owner),
            "exclude": st.get("exclude") or [], "email_ready": mailer.configured() or settings.local_dev,
            "dues_with_email": with_email, "dues_without_email": no_email,
            "timing": {"pending_days_before": followups.PENDING_DAYS, "overdue_every_days": settings.reminder_gap_days,
                       "overdue_max": followups.settings_max()}}


@router.get("/followups/status")
async def status(ctx: Ctx = Depends(feature("auto_reminders", "manager"))):
    """For managers on Money owed: are automatic follow-ups on (the wording and the switch stay with the owner)."""
    st = await followups.get_state(ctx.ws)
    return {"on": bool(st.get("on")), "whatsapp": bool(st.get("whatsapp"))}


@router.post("/followups/auto/approve")
async def approve(body: ApproveIn, ctx: Ctx = Depends(feature("auto_reminders", "owner"))):
    """Approve once, it runs: only the samples the owner just saw can be approved."""
    s = await followups.samples(ctx.ws)
    if body.hash != s["hash"]:
        raise HTTPException(409, "The wording changed since you opened it. Please look at the samples again.")
    whatsapp = body.whatsapp and followups.whatsapp_auto_allowed(ctx.owner)
    await db().followup_auto.update_one({"_id": ctx.ws}, {"$set": {"on": True, "approved_at": now(), "approved_by": ctx.actor, "approved_hash": s["hash"],
                                                                   "approved_samples": s["samples"], "whatsapp": whatsapp}}, upsert=True)
    await track("auto_followups_on", ctx.ws, whatsapp=whatsapp)
    return await get_auto(ctx)


@router.post("/followups/auto/off")
async def switch_off(ctx: Ctx = Depends(feature("auto_reminders", "owner"))):
    await db().followup_auto.update_one({"_id": ctx.ws}, {"$set": {"on": False, "off_at": now(), "off_by": ctx.actor}}, upsert=True)
    await track("auto_followups_off", ctx.ws)
    return await get_auto(ctx)


@router.post("/followups/auto/exclude")
async def exclude(body: ExcludeIn, ctx: Ctx = Depends(feature("auto_reminders", "manager"))):
    """Don't chase this customer automatically (every amount they owe)."""
    due = await db().dues.find_one({"_id": body.due_id, "ws": ctx.ws})
    if not due:
        raise HTTPException(404, "Not found")
    key = followups.customer_key(due)
    op = "$addToSet" if body.exclude else "$pull"
    await db().followup_auto.update_one({"_id": ctx.ws}, {op: {"exclude": key}}, upsert=True)
    return {"customer": due.get("customer"), "excluded": body.exclude}


@router.get("/followups/history/{due_id}")
async def history(due_id: str, ctx: Ctx = Depends(feature("money_owed", "manager"))):
    due = await db().dues.find_one({"_id": due_id, "ws": ctx.ws})
    if not due:
        raise HTTPException(404, "Not found")
    emails = {e["_id"]: e async for e in db().email_log.find({"owner_id": ctx.ws, "ref.due_id": due_id})}
    out = []
    for f in due.get("followups") or []:
        e = emails.get(f.get("ref")) or {}
        out.append({"stage": f["stage"], "n": f.get("n", 0), "channel": f["channel"], "at": f["at"].isoformat(), "ok": f.get("ok"),
                    "status": e.get("status") or ("sent" if f.get("ok") else "not sent"), "via": f.get("via"), "replied": bool(e.get("replied_at"))})
    async for a in db().approvals.find({"ws": ctx.ws, "due_id": due_id, "kind": "reminder"}).sort("created_at", 1):
        out.append({"stage": "overdue", "n": a.get("n", 0), "channel": "whatsapp", "at": a["created_at"].isoformat(), "ok": a["status"] == "sent",
                    "status": {"pending": "waiting in your Send list", "sent": "sent", "expired": "replaced", "skipped": "skipped"}.get(a["status"], a["status"]),
                    "via": a.get("via") or ""})
    return {"due": public(due), "history": sorted(out, key=lambda x: x["at"]), "stopped": due.get("auto_stopped")}


@router.post("/money/{due_id}/replied")
async def customer_replied(due_id: str, ctx: Ctx = Depends(feature("money_owed", "manager"))):
    """The customer replied (the owner saw it): automatic follow-ups for this amount stop."""
    if not await db().dues.find_one({"_id": due_id, "ws": ctx.ws}):
        raise HTTPException(404, "Not found")
    await followups.stop(ctx.ws, due_id, "replied")
    return {"ok": True}


async def payment_followups_auto(owner: dict, run: dict):
    r = await followups.run_owner(owner, run["at"])
    if r.get("skipped") == "off":
        raise Skip("Automatic follow-ups are off. Switch them on in Automations.")
    if r.get("skipped") == "wording_changed":
        raise Skip("Paused: the wording changed since you approved it. Read it again in Automations to keep follow-ups going.")
    if not r["sent"] and not r["failed"]:
        raise Skip("No follow-ups due today.")
    return f"{r['sent']} payment follow-up{'s' if r['sent'] != 1 else ''} emailed" + (f"; {r['failed']} couldn't be sent" if r["failed"] else "") + "."


async def _ready(owners: list[dict]) -> set:
    return {s["_id"] async for s in db().followup_auto.find({"_id": {"$in": [o["_id"] for o in owners]}, "on": True}, {"_id": 1})}


catalog.register("payment_followups_auto", {
    "name": "Automatic payment follow-ups", "feature": "auto_reminders", "trigger": "schedule", "customer_facing": True,
    "what": "Once you approve the wording: emails customers 3 days before a payment is due, on the day, and every 3 days after (at most 4), "
            "with the invoice attached. Stops when it's paid or they reply.",
    "when": "Every day at 10am", "schedule": {"every": "day", "at": "10:00", "grace_hours": 9}, "ready": _ready, "group": "approve_once"}, payment_followups_auto)
