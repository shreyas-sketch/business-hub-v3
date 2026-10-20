"""
Sales this month, counted from money (v2.1, 3.13) — shared by "Am I on target?", the Magic Number, the Dashboard and the
10th/20th target alert.

    invoiced (default): invoices dated this month, plus leads won this month that have no invoice of their own
                        (so an invoice for a won lead counts once). With no invoices at all this is the old behaviour:
                        won leads' values.
    paid:               money received this month: dues marked paid, plus invoices paid directly (gateway payments).

Projection and pace (all maths here, never in the AI):
    projected month-end = month to date ÷ days elapsed × days in month
    gap                 = target − projected
    required daily pace = (target − month to date) ÷ days left
"""
import calendar
import math
from datetime import date, datetime, timedelta, timezone

from .db import IST, db, now


def ist_midnight(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=IST).astimezone(timezone.utc)


def month_bounds(t: date) -> tuple[date, date]:
    first = t.replace(day=1)
    nxt = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first, nxt


async def month_sales(ws: str, mode: str = "invoiced", t: date | None = None) -> dict:
    t = t or now().astimezone(IST).date()
    first, nxt = month_bounds(t)
    since, until = ist_midnight(first), ist_midnight(nxt)
    if mode == "paid":
        total, n = 0.0, 0
        async for d in db().dues.find({"ws": ws, "status": "paid", "paid_at": {"$gte": since, "$lt": until}}, {"amount": 1}):
            total += int(d.get("amount") or 0) / 100     # what was still owed when it closed
            n += 1
        # part payments received this month (gateway), on dues open or closed — the rest is counted when the due closes
        async for d in db().dues.find({"ws": ws, "part_payments.at": {"$gte": since, "$lt": until}}, {"part_payments": 1}):
            total += sum(int(p.get("amount") or 0) for p in d.get("part_payments") or []
                         if p.get("at") and since <= p["at"] < until) / 100
        async for q in db().quotes.find({"ws": ws, "kind": "invoice", "status": "paid", "due_id": None, "paid_at": {"$gte": since, "$lt": until}},
                                        {"totals": 1}):
            total += float((q.get("totals") or {}).get("total") or 0)
            n += 1
        async for cn in db().quotes.find({"ws": ws, "kind": "credit_note", "created_at": {"$gte": since, "$lt": until}}, {"totals": 1}):
            total -= float((cn.get("totals") or {}).get("total") or 0)   # money refunded this month
        return {"sales": round(total, 2), "source": "payments", "count": n}
    invoiced, n_inv = 0.0, 0
    async for q in db().quotes.find({"ws": ws, "kind": "invoice", "created_at": {"$gte": since, "$lt": until}}, {"totals": 1}):
        invoiced += float((q.get("totals") or {}).get("total") or 0)
        n_inv += 1
    # leads won this month (by the day they were won; older leads without that date, by their last update), unless the sale
    # has an invoice — in any month — which is what counts then
    window = {"$gte": since, "$lt": until}
    won_leads = [x async for x in db().leads.find({"owner_id": ws, "status": "won", "$or": [{"won_at": window},
                                                                                         {"won_at": {"$exists": False}, "updated_at": window}]},
                                                  {"value": 1}).limit(5000)]
    invoiced_leads = set(await db().quotes.distinct("lead_id", {"ws": ws, "kind": "invoice", "lead_id": {"$in": [x["_id"] for x in won_leads]}}))
    # refunds this month (credit notes) come off what was invoiced
    async for cn in db().quotes.find({"ws": ws, "kind": "credit_note", "created_at": {"$gte": since, "$lt": until}}, {"totals": 1}):
        invoiced -= float((cn.get("totals") or {}).get("total") or 0)
    won, n_won = 0.0, 0
    for lead in won_leads:
        if lead["_id"] in invoiced_leads:
            continue
        won += float(lead.get("value") or 0)
        n_won += 1
    return {"sales": round(invoiced + won, 2), "source": "invoices" if n_inv else "won_leads", "count": n_inv + n_won,
            "invoices": n_inv, "won_without_invoice": n_won}


def pace(target: float, mtd: float, t: date) -> dict:
    first, nxt = month_bounds(t)
    days_in = (nxt - first).days
    elapsed = (t - first).days + 1
    left = days_in - elapsed
    projected = mtd / elapsed * days_in if elapsed else 0.0
    gap = target - projected
    need = max(target - mtd, 0.0)
    ratio = projected / target if target else 0
    status = "on_track" if ratio >= 1 else "slightly_behind" if ratio >= 0.85 else "well_behind"
    # whole rupees on screen; the status is decided on the unrounded projection
    return {"target": target, "month_to_date": round(mtd), "days_elapsed": elapsed, "days_in_month": days_in, "days_left": left,
            "month_gone_pct": round(elapsed / days_in * 100), "projected": round(projected), "gap": round(gap),
            "required_daily": round(need / left) if left > 0 else round(need), "pct_of_target": round(mtd / target * 100) if target else 0,
            "status": status}


STATUS_LINE = {"on_track": "On track", "slightly_behind": "Slightly behind", "well_behind": "Well behind"}


def rupees(x: float) -> str:
    from .billing import inr
    return inr(int(round(x)) * 100)


def short_rupees(x: float) -> str:
    """₹3.2 L · ₹45,000 · ₹1.2 Cr — for one-line statuses."""
    x = abs(x)
    if x >= 1e7:
        return f"₹{x / 1e7:.1f} Cr".replace(".0 ", " ")
    if x >= 1e5:
        return f"₹{x / 1e5:.1f} L".replace(".0 ", " ")
    return rupees(x)


async def target_status(ws: str, t: date | None = None) -> dict | None:
    """Am I on target this month? None when there's no target yet."""
    t = t or now().astimezone(IST).date()
    magic = await db().magic.find_one({"_id": ws}) or {}
    inputs = magic.get("inputs") or {}
    b = await db().businesses.find_one({"owner_id": ws}, {"targets": 1}) or {}
    target = float(inputs.get("monthly_target") or (b.get("targets") or {}).get("monthly_revenue") or 0)
    if target <= 0:
        return None
    mode = magic.get("count_when") or "invoiced"
    sold = await month_sales(ws, mode, t)
    p = pace(target, sold["sales"], t)
    behind = max(target - p["projected"], 0)
    activity = None
    avg, close = float(inputs.get("avg_sale") or 0), float(inputs.get("close_rate") or 0) / 100
    if behind > 0 and avg > 0 and close > 0:
        weeks = max(p["days_left"] / 7, 1)
        per_week = math.ceil(behind / avg / close / weeks)
        activity = {"inquiries_week": per_week, "line": f"{short_rupees(behind)} behind → about {per_week} more inquir{'y' if per_week == 1 else 'ies'} a week."}
    actions = []
    open_q = [q async for q in db().quotes.find({"ws": ws, "kind": "quote", "status": {"$in": ["draft", "sent"]}}, {"totals": 1}).limit(500)]
    if open_q:
        value = sum(float((q.get("totals") or {}).get("total") or 0) for q in open_q)
        actions.append({"text": f"Follow up {len(open_q)} open quotation{'s' if len(open_q) != 1 else ''} worth {short_rupees(value)}", "feature": "quotations",
                        "path": "/quotes"})
    today_s = t.isoformat()
    overdue = [d async for d in db().dues.find({"ws": ws, "status": "due", "due_date": {"$lt": today_s}}, {"amount": 1}).limit(500)]
    if overdue:
        amount = sum(int(d.get("amount") or 0) for d in overdue) / 100
        actions.append({"text": f"Chase {short_rupees(amount)} overdue from {len(overdue)} customer{'s' if len(overdue) != 1 else ''}",
                        "feature": "money_owed", "path": "/money"})
    actions.append({"text": "Try one of this month's ways to get more leads", "feature": "revenue_levers", "path": "/levers"})
    line = f"{STATUS_LINE[p['status']]}: {short_rupees(p['month_to_date'])} so far, heading for {short_rupees(p['projected'])} of {short_rupees(target)}."
    return {**p, "mode": mode, "source": sold["source"], "activity": activity, "actions": actions[:3] if p["status"] != "on_track" else actions[:1],
            "line": line, "status_label": STATUS_LINE[p["status"]]}


def days_in(t: date) -> int:
    return calendar.monthrange(t.year, t.month)[1]
