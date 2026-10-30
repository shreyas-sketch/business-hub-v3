"""
Plans & billing: in-app checkout with Razorpay (one-time tiers as orders, Membership as a monthly subscription),
the Razorpay webhook, the owner's "Invite & earn" commissions and payout details, and the admin payments desk.
The money logic lives in app/billing.py; this file is the HTTP surface.
"""
import json
import logging
import re
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import billing as core
from .. import plans
from ..config import settings
from ..context import Ctx, owner_ctx
from ..db import db, now, public
from ..security import rate_limit, require_admin
from ..services import new_id, notify, track

router = APIRouter(prefix="/api")
log = logging.getLogger("billing")

PAID_TIERS = "^(lite|program|running|growth|office)$"
UPI = re.compile(r"^[\w.\-]{2,256}@[a-zA-Z]{2,64}$", re.ASCII)
GATEWAY_DOWN = "Payments aren't reachable right now. Nothing was charged. Please try again in a minute."


async def ensure_indexes() -> None:
    d = db()
    await d.payments.create_index([("user_id", 1), ("created_at", -1)])
    await d.payments.create_index("rzp_order_id")
    await d.payments.create_index("rzp_payment_id")
    await d.payments.create_index([("status", 1), ("paid_at", -1)])
    await d.commissions.create_index([("referrer_id", 1), ("created_at", -1)])
    await d.commissions.create_index([("status", 1), ("available_at", 1)])
    await d.refunds.create_index("rzp_payment_id")
    await d.users.create_index("sub.id")


# ───────────────────────── shared views ─────────────────────────
def plan_view(user: dict) -> dict:
    """The owner's plan, where it comes from and until when."""
    sources = plans.plan_sources(user)
    effective = plans.effective_plan(user)
    source = next((k for k in ("purchase", "subscription", "trial", "welcome", "admin") if sources.get(k) == effective), "admin")
    sub = user.get("sub") or {}
    until = {"purchase": (user.get("access") or {}).get(effective), "subscription": sub.get("paid_until"),
             "trial": (user.get("trial") or {}).get("until")}.get(source)
    return {"plan": effective, "plan_name": plans.PLANS[effective]["name"], "source": source, "until": None if plans.open_ended(until) else core.iso(until),
            "sources": sources}


async def _fresh(user_id: str) -> dict:
    return await db().users.find_one({"_id": user_id})


def _prefill(user: dict, business: str) -> dict:
    return {"contact": user.get("phone", ""), "name": (business or user.get("name") or "")[:60]}


def _payment_view(p: dict) -> dict:
    out = public(p)
    out["tier_name"] = plans.PLANS.get(p.get("tier"), {}).get("name", p.get("tier"))
    return out


# ───────────────────────── billing info ─────────────────────────
@router.get("/billing")
async def billing_info(ctx: Ctx = Depends(owner_ctx)):
    user = await _fresh(ctx.ws)
    trial = user.get("trial") or {}
    payments = [_payment_view(p) async for p in
                db().payments.find({"user_id": user["_id"], "status": {"$ne": "created"}}).sort("created_at", -1).limit(20)]
    m = core.mode()
    return {
        "mode": m,
        "key_id": settings.razorpay_key_id if m == "razorpay" else None,
        **plan_view(user),
        "sub": core.sub_view(user.get("sub")),
        "member": core.is_member(user.get("sub")),
        "access": {t: core.iso(u) for t, u in (user.get("access") or {}).items()},
        "trial": {"plan": trial.get("plan"), "until": core.iso(trial["until"])} if trial.get("until") and trial["until"] > now() else None,
        "payments": payments,
        "ladder": plans.ladder(),
        "contact_urls": {t: bool(u) for t, u in settings.upgrade_urls.items()},
    }


# ───────────────────────── checkout ─────────────────────────
class CheckoutIn(BaseModel):
    tier: str = Field(pattern=PAID_TIERS)


def _refuse(user: dict, tier: str) -> None:
    if tier == "lite":
        sub = user.get("sub") or {}
        if core.is_member(sub):
            if sub.get("status") == "authenticated" and sub.get("start_at"):
                raise HTTPException(409, f"Your Membership is already set up. The first charge is on {sub['start_at']:%d %b %Y}.")
            raise HTTPException(409, "You're already a member.")
        return
    best = plans.plan_sources(user).get("purchase")
    if best and plans.rank(tier) < plans.rank(best):
        raise HTTPException(409, f"You already have {plans.PLANS[best]['name']}, which includes everything in {plans.PLANS[tier]['name']}.")


def _membership_start(user: dict):
    """Membership starts when what the owner already has runs out: a referral trial, or a cancelled Membership
    that is still paid up. The mandate is set up now; the first charge happens then."""
    at = now()
    ends = []
    trial = user.get("trial") or {}
    if trial.get("until") and trial["until"] > at and not plans.open_ended(trial["until"]) and plans.rank(trial.get("plan", "free")) >= plans.rank("lite"):
        ends.append(trial["until"])
    paid_until = (user.get("sub") or {}).get("paid_until")
    if paid_until and paid_until > at:
        ends.append(paid_until)
    start = max(ends, default=None)
    return start if start and start > at + timedelta(minutes=15) else None  # Razorpay needs start_at in the future


@router.post("/billing/checkout")
async def checkout(body: CheckoutIn, ctx: Ctx = Depends(owner_ctx)):
    user = await _fresh(ctx.ws)
    tier, plan = body.tier, plans.PLANS[body.tier]
    _refuse(user, tier)
    m = core.mode()
    if m == "razorpay" and tier == "lite" and not (plans.PLANS["lite"].get("razorpay_plan_id") or settings.razorpay_plan_id_lite):
        log.error("Membership checkout unavailable: RAZORPAY_PLAN_ID_LITE is not set")
        m = "contact"
    if m == "contact":
        await track("checkout_contact", user["_id"], tier=tier)
        return {"mode": "contact", "tier": tier, "url": settings.upgrade_urls.get(tier) or None}
    await rate_limit(f"checkout:{user['_id']}", 30, 3600, "Too many checkout attempts. Please try again in an hour.")
    business = await core.business_name(user["_id"], "")
    common = {"tier": tier, "plan_name": plan["name"], "amount": plan["price_minor"], "currency": "INR", "name": settings.program_name,
              "description": f"{plan['name']} · {core.inr(plan['price_minor'])}{' a month' if tier == 'lite' else ''}",
              "prefill": _prefill(user, business)}
    await track("checkout_start", user["_id"], tier=tier, mode=m)

    if m == "dev":
        pid = new_id()
        await db().payments.insert_one({"_id": pid, "user_id": user["_id"], "tier": tier, "kind": "dev", "amount": plan["price_minor"],
                                        "currency": "INR", "status": "created", "created_at": now()})
        return {"mode": "dev", "payment_id": pid, **common}

    if tier == "lite":
        start = _membership_start(user)
        start_unix = int(start.timestamp()) if start else None
        sub = user.get("sub") or {}
        reuse = (sub.get("status") == "created" and str(sub.get("id", "")).startswith("sub_") and sub.get("created_at")
                 and sub["created_at"] > now() - timedelta(hours=12)
                 and (int(sub["start_at"].timestamp()) if sub.get("start_at") else None) == start_unix)
        if reuse:  # the owner closed the checkout and came back: same subscription, no orphan in Razorpay
            sub_id = sub["id"]
        else:
            req = {"plan_id": plans.PLANS["lite"].get("razorpay_plan_id") or settings.razorpay_plan_id_lite, "total_count": 120, "quantity": 1, "customer_notify": 1,
                   "notes": {"user_id": user["_id"]}, **({"start_at": start_unix} if start_unix else {})}
            try:
                entity = await core.rzp("POST", "/subscriptions", req)
            except core.RazorpayError:
                raise HTTPException(502, GATEWAY_DOWN)
            sub_id = str(entity.get("id") or "")
            if not sub_id:
                raise HTTPException(502, GATEWAY_DOWN)
            await db().users.update_one({"_id": user["_id"]}, {"$set": {"sub": {
                "id": sub_id, "status": entity.get("status") or "created", "plan": "lite", "start_at": core.from_unix(start_unix),
                "paid_until": sub.get("paid_until"), "cancel_at_cycle_end": False, "created_at": now()}}})
        return {"mode": "razorpay", "kind": "subscription", "key_id": settings.razorpay_key_id, "subscription_id": sub_id,
                "start_at": core.iso(core.from_unix(start_unix)), "notes": {"user_id": user["_id"]}, **common}

    pid = new_id()
    notes = {"user_id": user["_id"], "tier": tier, "payment_id": pid}
    try:
        order = await core.rzp("POST", "/orders", {"amount": plan["price_minor"], "currency": "INR", "receipt": pid[:40], "notes": notes})
    except core.RazorpayError:
        raise HTTPException(502, GATEWAY_DOWN)
    order_id = str(order.get("id") or "")
    if not order_id:
        raise HTTPException(502, GATEWAY_DOWN)
    await db().payments.insert_one({"_id": pid, "user_id": user["_id"], "tier": tier, "kind": "order", "amount": plan["price_minor"],
                                    "currency": "INR", "status": "created", "rzp_order_id": order_id, "created_at": now()})
    return {"mode": "razorpay", "kind": "order", "key_id": settings.razorpay_key_id, "order_id": order_id, "payment_id": pid,
            "notes": notes, **common}


class DevCompleteIn(BaseModel):
    payment_id: str = Field(min_length=8, max_length=64)


@router.post("/billing/dev-complete")
async def dev_complete(body: DevCompleteIn, ctx: Ctx = Depends(owner_ctx)):
    if not settings.local_dev:
        raise HTTPException(403, "Test payments only work on a local test setup.")
    payment = await db().payments.find_one({"_id": body.payment_id, "user_id": ctx.ws, "kind": "dev"})
    if not payment:
        raise HTTPException(404, "Payment not found")
    payment = await core.complete_payment(payment["_id"])
    return {"payment": _payment_view(payment), **plan_view(await _fresh(ctx.ws))}


# ───────────────────────── verify (from the checkout handler) ─────────────────────────
class VerifyIn(BaseModel):
    razorpay_payment_id: str = Field(min_length=3, max_length=64)
    razorpay_order_id: str | None = Field(default=None, max_length=64)
    razorpay_subscription_id: str | None = Field(default=None, max_length=64)
    razorpay_signature: str = Field(min_length=8, max_length=256)


NOT_VERIFIED = "We couldn't verify this payment. If money left your account, it will be confirmed or refunded automatically."


@router.post("/billing/verify")
async def verify(body: VerifyIn, ctx: Ctx = Depends(owner_ctx)):
    if not core.configured():
        raise HTTPException(400, "Online payments are not set up.")
    uid = ctx.ws
    if body.razorpay_order_id:
        if not core.order_signature_ok(body.razorpay_order_id, body.razorpay_payment_id, body.razorpay_signature):
            await track("payment_signature_bad", uid, kind="order")
            raise HTTPException(400, NOT_VERIFIED)
        if not await db().payments.find_one({"rzp_order_id": body.razorpay_order_id, "user_id": uid, "kind": "order"}):
            raise HTTPException(404, "Payment not found")
        payment = await core.complete_order(body.razorpay_order_id, body.razorpay_payment_id)
        return {"payment": _payment_view(payment), **plan_view(await _fresh(uid))}
    if body.razorpay_subscription_id:
        sid = body.razorpay_subscription_id
        if not core.subscription_signature_ok(body.razorpay_payment_id, sid, body.razorpay_signature):
            await track("payment_signature_bad", uid, kind="subscription")
            raise HTTPException(400, NOT_VERIFIED)
        user = await _fresh(uid)
        try:
            entity = await core.rzp("GET", f"/subscriptions/{sid}")
        except core.RazorpayError:
            raise HTTPException(502, "Your payment went through. We couldn't confirm it with Razorpay just now; it will show here within a few minutes.")
        if (user.get("sub") or {}).get("id") != sid and str(core.notes_of(entity).get("user_id")) != uid:
            raise HTTPException(404, "Subscription not found")
        await core.apply_subscription({**entity, "id": sid})
        user = await _fresh(uid)
        return {"sub": core.sub_view(user.get("sub")), **plan_view(user)}
    raise HTTPException(400, "Missing order or subscription id")


# ───────────────────────── cancel ─────────────────────────
@router.post("/billing/cancel")
async def cancel(ctx: Ctx = Depends(owner_ctx)):
    user = await _fresh(ctx.ws)
    sub = user.get("sub") or {}
    if sub.get("status") not in core.LIVE_SUB:
        raise HTTPException(400, "You don't have a running Membership to cancel.")
    if sub.get("cancel_at_cycle_end"):
        return {"sub": core.sub_view(sub), **plan_view(user)}
    if str(sub.get("id", "")).startswith("dev_") or not core.configured():
        # test Membership: there is no Razorpay to tell us when the period ends, so it ends at paid_until
        await db().users.update_one({"_id": user["_id"], "sub.id": sub["id"]},
                                    {"$set": {"sub.status": "cancelled", "sub.cancel_at_cycle_end": True}})
    else:
        at_cycle_end = sub["status"] == "active"  # a paid-up cycle runs to its end; nothing paid → stop now
        try:
            entity = await core.rzp("POST", f"/subscriptions/{sub['id']}/cancel", {"cancel_at_cycle_end": 1 if at_cycle_end else 0})
        except core.RazorpayError:
            raise HTTPException(502, "We couldn't reach Razorpay to cancel. Nothing has changed; please try again in a minute.")
        patch = {"sub.cancel_at_cycle_end": True}
        if entity.get("status"):
            patch["sub.status"] = entity["status"]
        await db().users.update_one({"_id": user["_id"], "sub.id": sub["id"]}, {"$set": patch})
    await track("subscription_cancel", user["_id"], sub=sub["id"])
    user = await _fresh(ctx.ws)
    return {"sub": core.sub_view(user.get("sub")), **plan_view(user)}


# ───────────────────────── webhook ─────────────────────────
@router.post("/razorpay/webhook")
async def razorpay_webhook(request: Request):
    raw = await request.body()
    if not core.webhook_signature_ok(raw, request.headers.get("x-razorpay-signature")):
        raise HTTPException(400, "Bad signature")
    try:
        event = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "Bad body")
    if not isinstance(event, dict):
        raise HTTPException(400, "Bad body")
    name = str(event.get("event") or "")
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}

    def entity(key: str) -> dict:
        e = (payload.get(key) or {}).get("entity") if isinstance(payload.get(key), dict) else None
        return e if isinstance(e, dict) else {}

    if name == "payment.captured":
        pay = entity("payment")
        if pay.get("order_id"):
            await core.complete_order(str(pay["order_id"]), pay.get("id"), pay.get("amount"))
    elif name == "order.paid":
        order, pay = entity("order"), entity("payment")
        order_id = order.get("id") or pay.get("order_id")
        if order_id:
            await core.complete_order(str(order_id), pay.get("id"), order.get("amount_paid") or pay.get("amount"))
    elif name == "subscription.charged":
        sub, pay = entity("subscription"), entity("payment")
        user, current = await core.apply_subscription(sub, charged=True)
        if user and pay:
            payment, new = await core.record_charge(user, str(sub.get("id") or ""), pay)
            if current and new and not core.from_unix(sub.get("current_end")):
                await core.extend_without_cycle_end(user["_id"], sub["id"], payment["paid_at"])
        elif not user:
            log.warning("subscription.charged for unknown subscription %s", sub.get("id"))
    elif name.startswith("subscription."):
        user, _ = await core.apply_subscription(entity("subscription"))
        if not user:
            log.warning("%s for unknown subscription %s", name, entity("subscription").get("id"))
    elif name == "refund.processed":
        await core.apply_refund(entity("refund"))
    return {"ok": True}


# ───────────────────────── Invite & earn ─────────────────────────
async def _names(ids: list[str]) -> dict:
    return {b["owner_id"]: b.get("name", "") async for b in db().businesses.find({"owner_id": {"$in": ids}}, {"owner_id": 1, "name": 1})}


async def _sums(match: dict) -> dict:
    out = {"pending": 0, "approved": 0, "paid": 0, "void": 0}
    async for g in db().commissions.aggregate([{"$match": match}, {"$group": {"_id": "$status", "n": {"$sum": "$amount"}}}]):
        out[g["_id"]] = g["n"]
    return out


def _commission_view(c: dict, names: dict) -> dict:
    at = now()
    return {"id": c["_id"], "friend": names.get(c["friend_id"]) or "An owner you invited", "tier": c.get("tier"),
            "tier_name": plans.PLANS.get(c.get("tier"), {}).get("name", c.get("tier")), "payment_amount": c.get("payment_amount"),
            "amount": c["amount"], "percent": c["percent"], "status": c["status"], "available_at": core.iso(c["available_at"]),
            "ready": c["status"] == "pending" and c["available_at"] <= at, "approved_at": core.iso(c.get("approved_at")),
            "paid_at": core.iso(c.get("paid_at")), "reference": c.get("reference"), "void_reason": c.get("void_reason"),
            "clawback": bool(c.get("clawback")), "created_at": core.iso(c["created_at"])}


@router.get("/earnings")
async def earnings(ctx: Ctx = Depends(owner_ctx)):
    user = await _fresh(ctx.ws)
    items = [c async for c in db().commissions.find({"referrer_id": user["_id"]}).sort("created_at", -1).limit(300)]
    names = await _names(list({c["friend_id"] for c in items}))
    payout = user.get("payout") or {}
    return {"totals": await _sums({"referrer_id": user["_id"]}),
            "commissions": [_commission_view(c, names) for c in items],
            "payout": {"upi_id": payout["upi_id"], "name": payout.get("name", "")} if payout.get("upi_id") else None,
            "rules": {"percent": settings.commission_percent, "months": settings.commission_months, "hold_days": settings.commission_hold_days}}


class PayoutIn(BaseModel):
    upi_id: str = Field(min_length=4, max_length=330)
    name: str = Field(min_length=2, max_length=80)


@router.put("/earnings/payout")
async def save_payout(body: PayoutIn, ctx: Ctx = Depends(owner_ctx)):
    upi = body.upi_id.strip()
    if not UPI.match(upi):
        raise HTTPException(400, "Enter a UPI ID like yourname@okhdfcbank")
    name = " ".join(body.name.split())
    if len(name) < 2:
        raise HTTPException(400, "Enter the name on the UPI account")
    await db().users.update_one({"_id": ctx.ws}, {"$set": {"payout": {"upi_id": upi, "name": name, "updated_at": now()}}})
    await track("payout_details", ctx.ws)
    return {"payout": {"upi_id": upi, "name": name}}


# ───────────────────────── Admin: payments desk ─────────────────────────
COMMISSION_STATUSES = ("pending", "approved", "paid", "void")
PAYMENT_STATUSES = ("created", "paid", "refunded")


@router.get("/admin/commissions", dependencies=[Depends(require_admin)])
async def admin_commissions(status: str = ""):
    q = {"status": status} if status in COMMISSION_STATUSES else {}
    items = [c async for c in db().commissions.find(q).sort("created_at", -1).limit(500)]
    user_ids = list({c["referrer_id"] for c in items} | {c["friend_id"] for c in items})
    names = await _names(user_ids)
    people = {u["_id"]: u async for u in db().users.find({"_id": {"$in": user_ids}}, {"phone": 1, "payout": 1})}
    payments = {p["_id"]: p async for p in db().payments.find({"_id": {"$in": [c["payment_id"] for c in items]}}, {"status": 1})}
    out = []
    for c in items:
        ref = people.get(c["referrer_id"], {})
        payout = ref.get("payout") or {}
        out.append({**_commission_view(c, names),
                    "referrer": {"id": c["referrer_id"], "business": names.get(c["referrer_id"]) or "—", "phone": ref.get("phone"),
                                 "upi_id": payout.get("upi_id"), "payout_name": payout.get("name")},
                    "friend_phone": people.get(c["friend_id"], {}).get("phone"),
                    "payment_status": payments.get(c["payment_id"], {}).get("status"), "clawback_amount": c.get("clawback_amount")})
    return {"commissions": out, "totals": await _sums({})}


class IdsIn(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=200)


class MarkPaidIn(IdsIn):
    reference: str = Field(min_length=3, max_length=80)


class VoidIn(BaseModel):
    reason: str = Field(default="", max_length=200)


@router.post("/admin/commissions/approve")
async def approve_commissions(body: IdsIn, admin: dict = Depends(require_admin)):
    approved, skipped = [], []
    for cid in dict.fromkeys(str(i)[:80] for i in body.ids):
        at = now()
        cm = await db().commissions.find_one({"_id": cid})
        if not cm or cm["status"] != "pending":
            skipped.append({"id": cid, "reason": "not pending"})
            continue
        if cm["available_at"] > at:
            skipped.append({"id": cid, "reason": "still in the refund window"})
            continue
        payment = await db().payments.find_one({"_id": cm["payment_id"]}, {"status": 1})
        if not payment or payment["status"] != "paid":
            skipped.append({"id": cid, "reason": "payment refunded" if payment else "payment missing"})
            continue
        r = await db().commissions.update_one({"_id": cid, "status": "pending", "available_at": {"$lte": at}},
                                              {"$set": {"status": "approved", "approved_at": at, "approved_by": admin["_id"]}})
        (approved.append(cid) if r.modified_count else skipped.append({"id": cid, "reason": "changed meanwhile"}))
    if approved:
        await track("commissions_approved", admin["_id"], count=len(approved))
    return {"approved": approved, "skipped": skipped}


@router.post("/admin/commissions/mark-paid")
async def mark_commissions_paid(body: MarkPaidIn, admin: dict = Depends(require_admin)):
    reference = body.reference.strip()
    if len(reference) < 3:
        raise HTTPException(400, "Enter the UPI or bank reference (UTR)")
    paid, skipped = [], []
    for cid in dict.fromkeys(str(i)[:80] for i in body.ids):
        at = now()
        r = await db().commissions.update_one({"_id": cid, "status": "approved"},
                                              {"$set": {"status": "paid", "paid_at": at, "reference": reference, "paid_by": admin["_id"]}})
        if not r.modified_count:
            skipped.append({"id": cid, "reason": "not approved"})
            continue
        paid.append(cid)
        cm = await db().commissions.find_one({"_id": cid})
        who = await core.business_name(cm["friend_id"], "an owner you invited")
        await notify(cm["referrer_id"], f"We've paid you {core.inr(cm['amount'])} commission for {who}'s payment. Reference: {reference}.")
        await track("commission_paid", cm["referrer_id"], amount=cm["amount"], by=admin["_id"])
    return {"paid": paid, "skipped": skipped}


@router.post("/admin/commissions/{commission_id}/void")
async def void_commission(commission_id: str, body: VoidIn | None = None, admin: dict = Depends(require_admin)):
    r = await db().commissions.update_one({"_id": commission_id[:80], "status": {"$in": ["pending", "approved"]}},
                                          {"$set": {"status": "void", "void_reason": (body.reason.strip() if body else "") or "voided by admin",
                                                    "voided_at": now(), "voided_by": admin["_id"]}})
    if not r.modified_count:
        if not await db().commissions.find_one({"_id": commission_id[:80]}):
            raise HTTPException(404, "Commission not found")
        raise HTTPException(409, "Only pending or approved commissions can be voided.")
    await track("commission_void", admin["_id"], commission=commission_id[:80])
    return {"ok": True}


def _days(days: int, default: int = 30) -> int:
    return max(1, min(days or default, 3650))


@router.get("/admin/payments", dependencies=[Depends(require_admin)])
async def admin_payments(status: str = "", days: int = 30):
    q: dict = {"created_at": {"$gte": now() - timedelta(days=_days(days))}}
    if status in PAYMENT_STATUSES:
        q["status"] = status
    items = [p async for p in db().payments.find(q).sort("created_at", -1).limit(500)]
    ids = list({p["user_id"] for p in items})
    names = await _names(ids)
    phones = {u["_id"]: u.get("phone") async for u in db().users.find({"_id": {"$in": ids}}, {"phone": 1})}
    return [{**_payment_view(p), "business": names.get(p["user_id"]) or "—", "phone": phones.get(p["user_id"])} for p in items]


@router.get("/admin/revenue", dependencies=[Depends(require_admin)])
async def admin_revenue(days: int = 30):
    days = _days(days)
    at = now()
    since = at - timedelta(days=days)
    paid_total, by_tier, count = 0, {}, 0
    async for p in db().payments.find({"status": "paid", "paid_at": {"$gte": since}}, {"tier": 1, "amount": 1, "refunded_amount": 1}):
        net = int(p["amount"]) - int(p.get("refunded_amount") or 0)
        paid_total += net
        by_tier[p["tier"]] = by_tier.get(p["tier"], 0) + net
        count += 1
    refunds = 0
    async for r in db().refunds.find({"at": {"$gte": since}}, {"amount": 1}):
        refunds += int(r.get("amount") or 0)
    members_active = await db().users.count_documents({"$or": [{"sub.status": {"$in": ["active", "pending"]}}, {"sub.paid_until": {"$gt": at}}]})
    renewing = await db().users.count_documents({"sub.status": {"$in": ["active", "pending"]}, "sub.cancel_at_cycle_end": {"$ne": True}})
    totals = await _sums({})
    return {"days": days, "paid_total": paid_total, "payments": count, "by_tier": by_tier, "members_active": members_active,
            "mrr": renewing * plans.PLANS["lite"]["price_minor"], "refunds": refunds,
            "commissions": {k: totals[k] for k in ("pending", "approved", "paid")},
            "clawbacks": await db().commissions.count_documents({"clawback": True})}
