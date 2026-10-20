"""
Cash flow (3.14) and "Am I on target this month?" (3.13): the forecast and its items, the target status, and the two
owner-only alerts (a shortfall within CASHFLOW_ALERT_DAYS; well behind target on the 10th and 20th).
"""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from .. import cashflow, plans, sales
from ..agents import catalog
from ..agents.catalog import Skip
from ..config import settings
from ..context import Ctx, feature
from ..db import IST, db, now, public
from ..messaging import whatsapp_template
from ..services import new_id, notify, track

router = APIRouter(prefix="/api")
cash_ctx = feature("cash_flow", "manager")


def _today() -> date:
    return now().astimezone(IST).date()


class CashItemIn(BaseModel):
    kind: str = Field(pattern="^(in|out)$")
    title: str = Field(min_length=2, max_length=100)
    amount: float = Field(gt=0, le=1e10)               # ₹
    date: str | None = Field(default=None, max_length=10)
    day_of_month: int | None = Field(default=None, ge=1, le=31)
    recurrence: str = Field(default="none", pattern="^(none|monthly)$")

    @field_validator("date")
    @classmethod
    def valid_day(cls, v):
        if v is None:
            return v
        try:
            return date.fromisoformat(v).isoformat()
        except ValueError:
            raise ValueError("Use a date like 2026-11-05")


class CashSettingsIn(BaseModel):
    safety_buffer: float = Field(default=0, ge=0, le=1e10)
    quote_probability: int = Field(default=0, ge=0, le=100)


class BalanceIn(BaseModel):
    amount: float = Field(ge=-1e10, le=1e11)
    on: str | None = Field(default=None, max_length=10)


@router.get("/cash-flow")
async def get_cash_flow(delay_id: str | None = None, delay_days: int = 0, ctx: Ctx = Depends(cash_ctx)):
    """The 30-day forecast. ?delay_id=<due>&delay_days=15 is the what-if: that customer pays 15 days later."""
    delays = {delay_id: max(-60, min(int(delay_days), 120))} if delay_id and delay_days else None
    f = await cashflow.forecast(ctx.ws, _today(), delays)
    f["items"] = [public(i) async for i in db().cash_items.find({"owner_id": ctx.ws}).sort("created_at", -1).limit(300)]
    f["can_follow_up"] = ctx.has("auto_reminders") or ctx.has("reminder_agent")
    return f


def _item(body: CashItemIn) -> dict:
    if body.recurrence == "monthly" and not body.day_of_month:
        raise HTTPException(400, "Give the day of the month it repeats on.")
    if body.recurrence == "none" and not body.date:
        raise HTTPException(400, "Give the date.")
    return {"kind": body.kind, "title": body.title.strip(), "amount": body.amount, "recurrence": body.recurrence,
            "date": body.date if body.recurrence == "none" else None, "day_of_month": body.day_of_month if body.recurrence == "monthly" else None}


@router.post("/cash-flow/items")
async def add_cash_item(body: CashItemIn, ctx: Ctx = Depends(cash_ctx)):
    doc = {"_id": new_id(), "owner_id": ctx.ws, **_item(body), "source": "owner", "created_by": ctx.actor, "created_at": now()}
    await db().cash_items.insert_one(doc)
    await track("cash_item", ctx.ws)
    return public(doc)


@router.put("/cash-flow/items/{item_id}")
async def edit_cash_item(item_id: str, body: CashItemIn, ctx: Ctx = Depends(cash_ctx)):
    r = await db().cash_items.update_one({"_id": item_id, "owner_id": ctx.ws}, {"$set": {**_item(body), "updated_at": now()}})
    if not r.matched_count:
        raise HTTPException(404, "Not found")
    return public(await db().cash_items.find_one({"_id": item_id}))


@router.delete("/cash-flow/items/{item_id}")
async def delete_cash_item(item_id: str, ctx: Ctx = Depends(cash_ctx)):
    r = await db().cash_items.delete_one({"_id": item_id, "owner_id": ctx.ws})
    if not r.deleted_count:
        raise HTTPException(404, "Not found")
    return {"ok": True}


@router.put("/cash-flow/settings")
async def cash_settings(body: CashSettingsIn, ctx: Ctx = Depends(cash_ctx)):
    await db().cash_settings.replace_one({"_id": ctx.ws}, {"_id": ctx.ws, **body.model_dump(), "updated_at": now()}, upsert=True)
    return body.model_dump()


@router.put("/cash-flow/balance")
async def set_balance(body: BalanceIn, ctx: Ctx = Depends(feature("cash_flow", "owner"))):
    """Today's bank balance (kept in the Knowledge Hub's money basics, where the forecast starts from)."""
    on = body.on or _today().isoformat()
    try:
        if date.fromisoformat(on) > _today():
            raise ValueError
    except ValueError:
        raise HTTPException(400, "Use today's date or an earlier one.")
    b = await db().businesses.find_one({"owner_id": ctx.ws})
    if not b:
        raise HTTPException(400, "Tell us about your business first")
    money = {**(b.get("money") or {}), "bank_balance": body.amount, "bank_balance_on": on, "bank_balance_saved_at": now()}
    await db().businesses.update_one({"_id": b["_id"]}, {"$set": {"money": money, "updated_at": now()}})
    return {"ok": True}


# ───────────────────────── Am I on target this month? ─────────────────────────
class CountIn(BaseModel):
    count_when: str = Field(pattern="^(invoiced|paid)$")


@router.get("/target")
async def target(ctx: Ctx = Depends(feature("magic_number", "manager"))):
    return {"target": await sales.target_status(ctx.ws, _today())}


@router.put("/target/count")
async def count_when(body: CountIn, ctx: Ctx = Depends(feature("magic_number", "manager"))):
    """Count a sale when it's invoiced (default) or when the money arrives."""
    await db().magic.update_one({"_id": ctx.ws}, {"$set": {"count_when": body.count_when}}, upsert=True)
    return {"target": await sales.target_status(ctx.ws, _today())}


# ───────────────────────── owner-only alerts ─────────────────────────
async def _alert(owner: dict, text: str, kind: str) -> bool:
    b = await db().businesses.find_one({"owner_id": owner["_id"]}, {"name": 1}) or {}
    sent = await whatsapp_template(owner["phone"], settings.aisensy_owner_alert_campaign, owner.get("name", ""), [b.get("name") or "your business", text[:900]], kind)
    await notify(owner["_id"], text[:600])
    return sent["sent"]


async def cashflow_watch(owner: dict, run: dict):
    ws = owner["_id"]
    t = run["at"].astimezone(IST).date()
    f = await cashflow.forecast(ws, t)
    if not f.get("ready"):
        raise Skip("Add your bank balance in the Knowledge Hub to switch the forecast on.")
    short = f.get("shortfall")
    if not short or date.fromisoformat(short["date"]) > t + timedelta(days=settings.cashflow_alert_days):
        await db().cash_settings.update_one({"_id": ws}, {"$unset": {"alerted": ""}})
        raise Skip("No shortfall in the next %d days." % settings.cashflow_alert_days)
    last = (await db().cash_settings.find_one({"_id": ws}) or {}).get("alerted") or {}
    if last.get("date") == short["date"] and last.get("on") and (t - date.fromisoformat(last["on"])).days < 3:
        raise Skip("Already told you about this shortfall.")
    top = (f.get("close_the_gap") or [None])[0]
    when = date.fromisoformat(short["date"]).strftime("%d %b")
    text = (f"Cash flow: your balance goes below {'your safety buffer' if f['safety_buffer'] else 'zero'} on {when} "
            f"(short by {sales.short_rupees(short['short_by'])}).")
    if top:
        text += f" Collect {top['customer']}'s {sales.short_rupees(top['amount'])} first."
    wa = await _alert(owner, text, "cashflow_alert")
    await db().cash_settings.update_one({"_id": ws}, {"$set": {"alerted": {"date": short["date"], "on": t.isoformat()}}}, upsert=True)
    return text + ("" if wa else " (Shown in the hub; WhatsApp isn't set up.)")


async def target_check(owner: dict, run: dict):
    t = run["at"].astimezone(IST).date()
    st = await sales.target_status(owner["_id"], t)
    if not st:
        raise Skip("No monthly target set yet.")
    if st["status"] != "well_behind":
        raise Skip(st["line"])
    text = st["line"] + (f" {st['activity']['line']}" if st.get("activity") else "") + (f" Next: {st['actions'][0]['text']}." if st["actions"] else "")
    wa = await _alert(owner, text, "target_alert")
    return text + ("" if wa else " (Shown in the hub; WhatsApp isn't set up.)")


async def _cash_ready(owners: list[dict]) -> set:
    ids = [o["_id"] for o in owners]
    return {b["owner_id"] async for b in db().businesses.find({"owner_id": {"$in": ids}, "money.bank_balance_on": {"$exists": True, "$ne": None}},
                                                               {"owner_id": 1})}


async def _target_ready(owners: list[dict]) -> set:
    ids = [o["_id"] for o in owners]
    out = {m["_id"] async for m in db().magic.find({"_id": {"$in": ids}, "inputs.monthly_target": {"$gt": 0}}, {"_id": 1})}
    out |= {b["owner_id"] async for b in db().businesses.find({"owner_id": {"$in": ids}, "targets.monthly_revenue": {"$gt": 0}}, {"owner_id": 1})}
    return out


catalog.register("cashflow_watch", {
    "name": "Cash flow watch", "feature": "cash_flow", "trigger": "schedule",
    "what": "Checks the next 30 days every morning and tells you on WhatsApp when your balance will go short, with the due to collect first.",
    "when": "Every day at 8am, before the morning digest", "schedule": {"every": "day", "at": "08:00", "grace_hours": 4}, "ready": _cash_ready, "group": "alerts"}, cashflow_watch)
catalog.register("target_check", {
    "name": "Target check", "feature": "magic_number", "trigger": "schedule",
    "what": "On the 10th and the 20th, tells you on WhatsApp if the month is well behind target, and the one thing to do about it.",
    "when": "The 10th and the 20th at 10am", "schedule": {"every": "month", "days": [10, 20], "at": "10:00", "grace_hours": 8}, "ready": _target_ready, "group": "alerts"}, target_check)


async def shortfall_line(ws: str) -> str | None:
    """For the morning digest: 'cash short on 3 Nov'."""
    try:
        f = await cashflow.forecast(ws, _today())
    except Exception:  # noqa: BLE001
        return None
    s = f.get("shortfall") if f.get("ready") else None
    return f"cash short on {date.fromisoformat(s['date']).strftime('%d %b')}" if s else None


async def ensure_indexes() -> None:
    await db().cash_items.create_index([("owner_id", 1), ("date", 1)])
