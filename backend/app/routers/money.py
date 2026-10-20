"""Money owed (Running the Business): who owes the business what, by when — and the payment reminder agent's source.

dues {_id, ws, customer, phone, amount (paise), due_date "YYYY-MM-DD", note, invoice_no, status due|paid, paid_at,
      reminders_sent, last_reminded_at, created_by, created_at, updated_at}
"""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from ..agents.approvals import expire
from ..context import Ctx, feature
from ..db import IST, db, now, public
from .. import mailer
from ..dashboard_cache import bust
from ..services import new_id, track
from .hub import wa_digits

router = APIRouter(prefix="/api")
money_ctx = feature("money_owed", "manager")
MAX_RUPEES = 100_000_000  # ₹10 crore: anything bigger is a typo


def _check_date(v: str | None) -> str | None:
    if v is None:
        return v
    try:
        return date.fromisoformat(v).isoformat()
    except ValueError:
        raise ValueError("Use a date like 2026-10-31")


def _check_phone(v: str | None) -> str | None:
    if v is None:
        return v
    v = v.strip()
    if v and not (10 <= len(wa_digits(v)) <= 13):
        raise ValueError("Enter a 10-digit mobile number")
    return v


def _check_email(v: str | None) -> str | None:
    if v is None:
        return v
    v = v.strip()
    if v and not mailer.valid(v):
        raise ValueError("Enter a valid email address")
    return v.lower()


class DueIn(BaseModel):
    customer: str = Field(min_length=2, max_length=80)
    phone: str = Field(default="", max_length=24)
    email: str = Field(default="", max_length=254)          # for automatic follow-ups by email
    amount: float = Field(gt=0, le=MAX_RUPEES)          # rupees
    due_date: str = Field(max_length=10)
    note: str = Field(default="", max_length=300)
    invoice_no: str = Field(default="", max_length=40)

    @field_validator("due_date")
    @classmethod
    def valid_date(cls, v):
        return _check_date(v)

    @field_validator("phone")
    @classmethod
    def valid_phone(cls, v):
        return _check_phone(v)

    @field_validator("email")
    @classmethod
    def valid_email(cls, v):
        return _check_email(v)


class DuePatch(BaseModel):
    customer: str | None = Field(default=None, min_length=2, max_length=80)
    phone: str | None = Field(default=None, max_length=24)
    email: str | None = Field(default=None, max_length=254)
    no_auto: bool | None = None                              # "don't chase this one automatically"
    amount: float | None = Field(default=None, gt=0, le=MAX_RUPEES)
    due_date: str | None = Field(default=None, max_length=10)
    note: str | None = Field(default=None, max_length=300)
    invoice_no: str | None = Field(default=None, max_length=40)
    status: str | None = Field(default=None, pattern="^(due|paid)$")

    @field_validator("due_date")
    @classmethod
    def valid_date(cls, v):
        return _check_date(v)

    @field_validator("phone")
    @classmethod
    def valid_phone(cls, v):
        return _check_phone(v)

    @field_validator("email")
    @classmethod
    def valid_email(cls, v):
        return _check_email(v)


def _today() -> str:
    return now().astimezone(IST).strftime("%Y-%m-%d")


def _view(d: dict, today: str) -> dict:
    out = public(d)
    out["overdue"] = d.get("status") == "due" and d.get("due_date", "") < today
    if out["overdue"]:
        out["days_overdue"] = (date.fromisoformat(today) - date.fromisoformat(d["due_date"])).days
    return out


@router.get("/money")
async def list_money(ctx: Ctx = Depends(money_ctx)):
    today = _today()
    month_start = now().astimezone(IST).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    items, due, overdue, collected = [], 0, 0, 0
    from .. import followups
    st = await followups.get_state(ctx.ws)
    excluded = set(st.get("exclude") or [])
    async for d in db().dues.find({"ws": ctx.ws}).sort("due_date", 1).limit(1000):
        v = _view(d, today)
        v["auto_excluded"] = followups.customer_key(d) in excluded or bool(d.get("no_auto"))
        items.append(v)
        amount = int(d.get("amount") or 0)
        if d.get("status") == "due":
            due += amount
            overdue += amount if v["overdue"] else 0
        elif d.get("paid_at") and d["paid_at"] >= month_start:
            collected += amount
    paid = sorted((x for x in items if x["status"] == "paid"), key=lambda x: x.get("paid_at") or "", reverse=True)
    return {"items": [x for x in items if x["status"] == "due"] + paid,
            "totals": {"due": due, "overdue": overdue, "collected_month": collected,
                       "count_due": sum(x["status"] == "due" for x in items), "count_overdue": sum(x["overdue"] for x in items)}}


@router.post("/money")
async def add_due(body: DueIn, ctx: Ctx = Depends(money_ctx)):
    doc = {"_id": new_id(), "ws": ctx.ws, "customer": body.customer.strip(), "phone": body.phone.strip(), "email": body.email,
           "amount": int(round(body.amount * 100)),
           "due_date": body.due_date, "note": body.note.strip(), "invoice_no": body.invoice_no.strip(), "status": "due", "paid_at": None,
           "reminders_sent": 0, "last_reminded_at": None, "created_by": ctx.actor, "created_at": now(), "updated_at": now()}
    await db().dues.insert_one(doc)
    bust(ctx.ws)
    await track("due_added", ctx.ws)
    return _view(doc, _today())


@router.patch("/money/{due_id}")
async def update_due(due_id: str, body: DuePatch, ctx: Ctx = Depends(money_ctx)):
    d = await db().dues.find_one({"_id": due_id, "ws": ctx.ws})
    if not d:
        raise HTTPException(404, "Not found")
    data = body.model_dump(exclude_none=True)
    patch: dict = {k: (v.strip() if isinstance(v, str) else v) for k, v in data.items() if k not in ("amount", "status")}
    if "amount" in data:
        patch["amount"] = int(round(data["amount"] * 100))
    if data.get("due_date") and data["due_date"] != d.get("due_date"):
        patch.update(reminders_sent=0, last_reminded_at=None, followups=[], auto_stopped=None)  # a new due date starts a fresh round of reminders
    if data.get("status") == "paid" and d.get("status") != "paid":
        patch.update(status="paid", paid_at=now(), paid_by=ctx.actor)
    elif data.get("status") == "due" and d.get("status") != "due":
        patch.update(status="due", paid_at=None)
    patch.update(updated_at=now(), updated_by=ctx.actor)
    await db().dues.update_one({"_id": due_id}, {"$set": patch})
    bust(ctx.ws)
    if patch.get("status") == "paid":
        await expire({"ws": ctx.ws, "kind": "reminder", "due_id": due_id}, "Marked paid")
        await track("due_paid", ctx.ws)
        if d.get("invoice_id"):
            await db().quotes.update_one({"_id": d["invoice_id"], "ws": ctx.ws}, {"$set": {"status": "paid", "paid_at": now()}})
    elif patch.get("status") == "due" and d.get("invoice_id"):
        await db().quotes.update_one({"_id": d["invoice_id"], "ws": ctx.ws}, {"$set": {"status": "due", "paid_at": None}})
    return _view(await db().dues.find_one({"_id": due_id}), _today())


@router.delete("/money/{due_id}")
async def delete_due(due_id: str, ctx: Ctx = Depends(money_ctx)):
    r = await db().dues.delete_one({"_id": due_id, "ws": ctx.ws})
    if not r.deleted_count:
        raise HTTPException(404, "Not found")
    await expire({"ws": ctx.ws, "kind": "reminder", "due_id": due_id}, "Removed from money owed")
    bust(ctx.ws)
    return {"ok": True}


async def ensure_indexes() -> None:
    await db().dues.create_index([("ws", 1), ("status", 1), ("due_date", 1)])
