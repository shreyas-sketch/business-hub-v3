"""
GST set-aside (v2.2): GST on your sales minus GST on your bills — about what to keep aside for the next GST payment.

An ESTIMATE from what's in the hub. It is not a GST return and not tax advice: the page says so and sends the owner to
their CA. Two periods are shown: last month (its payment usually falls due this month) and this month so far.

    sales GST        hub invoices (kind "invoice", Razorpay ones included) by the India-time day of created_at; an invoice
                     whose status is cancelled/void is left out (none exist today; the check is there for when one does)
    credit notes     hub credit notes dated in the month: their GST comes off
    uploaded sales   sales_rows dated in the month whose gst isn't None. A row is skipped as "already in the hub" only by
                     the sales upload's own duplicate rule (routers/moneyinsights.py, Seen.match): the same invoice number
                     (spaces ignored, any case) on a hub invoice or credit note of this business IN THE SAME APRIL–MARCH
                     YEAR, AND the same customer (by name or by phone — salesbook.name_key / phone_key, the two halves of
                     the shared customer_key) or a total within ₹1. A number alone is not enough: the hub's INV-0001 style
                     is common in Tally sheets too, and skipping a different sale would make the estimate too low.
    bills GST        expenses dated in the month that are approved and gst_claimable
    estimate         max(0, sales − credit notes + uploaded − bills); when the bills are bigger, the difference is shown as
                     carry_forward and is NOT taken off the next month (keeping more aside is the safer mistake)
Sums are in paise; the API shows rupees.

Due dates are never worked out here. They come from the compliance calendar's matching GST item — GSTR-3B for monthly
filers; for QRMP filers PMT-06 for months 1 and 2 of a quarter and the quarterly GSTR-3B for month 3 — or, when the calendar
has no item for that period, from the same rules table the calendar is built from (compliance_rules.due_dates, with the
admin's overrides). No identity → no date, and the page says what to fill.

"Count it in my cash flow" writes the estimate (rounded to the rupee) as the amount on that compliance item, which Cash
flow already counts as money going out on its due date (app/cashflow.py). Fields written on compliance_items:
    amount, amount_source "gst_setaside", amount_estimate (the value written here), amount_estimated_at, amount_estimate_period
An amount counts as this page's only while amount_source is "gst_setaside" AND amount still equals amount_estimate, so a
number the owner later types over it in the compliance calendar is treated as theirs. An amount the owner typed is never
overwritten without replace=true, and only items still "upcoming" are touched.
"""
import logging
import re
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import compliance_rules as rules_mod
from ..billing import inr
from ..context import Ctx, feature
from ..dashboard_cache import bust
from ..db import IST, db, now
from ..sales import ist_midnight, month_bounds
from ..salesbook import name_key, phone_key
from ..services import track
from . import compliance

log = logging.getLogger("gstsetaside")
router = APIRouter(prefix="/api")
gst_ctx = feature("gst_setaside", "manager")

LABEL = "An estimate from what's in your hub — not your GST return. Confirm with your CA."
SOURCE = "gst_setaside"
VOID = {"cancelled", "canceled", "void", "voided", "draft", "deleted"}   # never counted as a sale or a credit note
CAP = 20000                                                              # documents read per collection per request
PERIOD = re.compile(r"^20\d\d-(0[1-9]|1[0-2])$")
UPLOAD_HINT = "Sales made outside the hub aren't counted — upload your sales sheet on Customer risk or Getting paid."


def today() -> date:
    """The same clock as the compliance calendar (India time)."""
    return compliance.today()


# ───────────────────────── small helpers ─────────────────────────
def _paise(rupees) -> int:
    try:
        return int(round(float(rupees or 0) * 100))
    except (TypeError, ValueError):
        return 0


def _as_paise(value) -> int:
    """A value already in paise (sales_rows, expenses)."""
    try:
        return int(round(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def _rs(paise: int) -> float:
    return round(paise / 100, 2)


def _whole(paise: int) -> int:
    """Rupees rounded half up: ₹10.50 → ₹11, ₹10.49 → ₹10."""
    return (paise + 50) // 100 if paise >= 0 else -((-paise + 50) // 100)


def _money(paise: int) -> str:
    return inr(_whole(abs(paise)) * 100)


def _doc_gst(q: dict) -> int:
    t = q.get("totals") or {}
    if t.get("gst") is not None:
        return _paise(t.get("gst"))
    return sum(_paise(t.get(k)) for k in ("cgst", "sgst", "igst"))


def _ist_day(at) -> date | None:
    if not isinstance(at, datetime):
        return None
    if at.tzinfo is None:          # stored without a zone: MongoDB keeps UTC
        at = at.replace(tzinfo=timezone.utc)
    return at.astimezone(IST).date()


def _day(value) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _inv(number) -> str:
    """An invoice number as the sales upload compares it: spaces removed, upper case."""
    return re.sub(r"\s+", "", str(number or "")).upper()


def _fy(d: date) -> int:
    """The April–March financial year a day falls in, by its starting year (Mar 2026 → 2025, Apr 2026 → 2026)."""
    return d.year if d.month >= 4 else d.year - 1


def _same_sale(row: dict, hub: list[dict]) -> bool:
    """The sales upload's duplicate rule, for a row whose number and financial year match these hub documents: it's the same
    sale when the customer matches (by name or by phone) or the total is within ₹1."""
    nk, pk = name_key(str(row.get("customer") or "")), phone_key(str(row.get("phone") or ""))
    paise = _as_paise(row.get("amount"))
    return any(abs(h["paise"] - paise) <= 100 or (nk and h["nk"] == nk) or (pk and h["pk"] == pk) for h in hub)


def _same(a, b) -> bool:
    try:
        return a is not None and b is not None and abs(float(a) - float(b)) < 0.005
    except (TypeError, ValueError):
        return False


def _n(k: int, one: str, many: str) -> str:
    return f"{k} {one if k == 1 else many}"


def _periods(t: date) -> list[dict]:
    first, nxt = month_bounds(t)
    last_first, _ = month_bounds(first - timedelta(days=1))
    return [{"key": last_first.strftime("%Y-%m"), "first": last_first, "next": first, "complete": True},
            {"key": first.strftime("%Y-%m"), "first": first, "next": nxt, "complete": False}]


# ───────────────────────── the numbers ─────────────────────────
async def _numbers(ws: str, periods: list[dict]) -> tuple[dict[str, dict], list[str]]:
    blank = {"sales": 0, "invoices": 0, "gateway_invoices": 0, "credit": 0, "credit_notes": 0, "uploaded": 0, "upload_rows": 0,
             "upload_rows_no_gst": 0, "upload_rows_duplicate": 0, "bills": 0, "bills_count": 0, "bills_no_gst": 0,
             "bills_not_claimable": 0, "bills_waiting": 0}
    out = {p["key"]: dict(blank) for p in periods}
    capped: list[str] = []

    def which(d: date | None) -> dict | None:
        for p in periods:
            if d and p["first"] <= d < p["next"]:
                return out[p["key"]]
        return None

    # hub invoices and credit notes, by the India-time day they were made
    since, until = ist_midnight(periods[0]["first"]), ist_midnight(periods[-1]["next"])
    seen = 0
    async for q in db().quotes.find({"ws": ws, "kind": {"$in": ["invoice", "credit_note"]}, "created_at": {"$gte": since, "$lt": until}},
                                    {"kind": 1, "status": 1, "totals": 1, "created_at": 1, "source": 1}).limit(CAP):
        seen += 1
        if str(q.get("status") or "").strip().lower() in VOID:
            continue
        o = which(_ist_day(q.get("created_at")))
        if o is None:
            continue
        if q.get("kind") == "invoice":
            o["sales"] += _doc_gst(q)
            o["invoices"] += 1
            o["gateway_invoices"] += q.get("source") == "gateway"
        else:
            o["credit"] += _doc_gst(q)
            o["credit_notes"] += 1
    if seen >= CAP:
        capped.append("invoices")

    first_day, end_day = periods[0]["first"].isoformat(), periods[-1]["next"].isoformat()
    # uploaded sales history (paise, GST included). A row that is a hub invoice or credit note again is already counted
    # from the hub — decided by the sales upload's own duplicate rule (see the module notes), never by the number alone
    rows = [r async for r in db().sales_rows.find({"ws": ws, "date": {"$gte": first_day, "$lt": end_day}},
                                                  {"date": 1, "gst": 1, "invoice_no": 1, "customer": 1, "phone": 1, "amount": 1}).limit(CAP)]
    if len(rows) >= CAP:
        capped.append("uploaded sales")
    hub: dict[tuple[int, str], list[dict]] = {}     # (financial year, invoice number) → the hub documents with it
    if any(_inv(r.get("invoice_no")) for r in rows):
        fy_from = date(_fy(periods[0]["first"]), 4, 1)
        fy_to = date(_fy(periods[-1]["next"] - timedelta(days=1)) + 1, 4, 1)
        async for q in db().quotes.find({"ws": ws, "kind": {"$in": ["invoice", "credit_note"]},
                                         "created_at": {"$gte": ist_midnight(fy_from), "$lt": ist_midnight(fy_to)}},
                                        {"kind": 1, "number": 1, "status": 1, "created_at": 1, "customer": 1, "totals": 1}).limit(CAP * 5):
            day, number = _ist_day(q.get("created_at")), _inv(q.get("number"))
            if not day or not number or str(q.get("status") or "").strip().lower() in VOID:
                continue   # a cancelled hub document doesn't make an uploaded sale a duplicate (as in the upload)
            cust = q.get("customer") or {}
            sign = -1 if q.get("kind") == "credit_note" else 1
            hub.setdefault((_fy(day), number), []).append({"nk": name_key(str(cust.get("name") or "")), "pk": phone_key(str(cust.get("phone") or "")),
                                                           "paise": sign * _paise((q.get("totals") or {}).get("total"))})
    for r in rows:
        d = _day(r.get("date"))
        o = which(d)
        if o is None:
            continue
        number = _inv(r.get("invoice_no"))
        if number and _same_sale(r, hub.get((_fy(d), number), [])):
            o["upload_rows_duplicate"] += 1
            continue
        if r.get("gst") is None:
            o["upload_rows_no_gst"] += 1
            continue
        o["uploaded"] += _as_paise(r["gst"])
        o["upload_rows"] += 1

    # bills: only approved ones marked as GST bills count
    seen = 0
    async for e in db().expenses.find({"ws": ws, "date": {"$gte": first_day, "$lt": end_day}},
                                      {"date": 1, "gst": 1, "gst_claimable": 1, "status": 1}).limit(CAP):
        seen += 1
        o = which(_day(e.get("date")))
        if o is None:
            continue
        gst = _as_paise(e.get("gst"))
        if e.get("status") == "approved":
            if not gst:
                o["bills_no_gst"] += 1
            elif e.get("gst_claimable") is True:
                o["bills"] += gst
                o["bills_count"] += 1
            else:
                o["bills_not_claimable"] += 1
        elif e.get("status") == "to_approve" and gst and e.get("gst_claimable") is True:
            o["bills_waiting"] += 1
    if seen >= CAP:
        capped.append("bills")
    return out, capped


# ───────────────────────── which GST payment, and when ─────────────────────────
def _payment_rule(rules: list[dict], identity: dict, month: int) -> dict | None:
    """Monthly filers pay with GSTR-3B. QRMP filers pay months 1 and 2 of a quarter by PMT-06 and month 3 with the quarterly
    GSTR-3B (its due day depends on the state, which the rules table's own conditions decide)."""
    if (identity.get("gst_scheme") or "monthly") == "qrmp":
        keys = ("gstr3b_qrmp_22", "gstr3b_qrmp_24") if month % 3 == 0 else ("pmt06",)
    else:
        keys = ("gstr3b",)
    return next((r for r in rules if r.get("key") in keys and rules_mod.applies(r, identity)), None)


def _rule_period(rule: dict, first: date) -> str:
    """The period label the compliance calendar gives this rule's item for that month (see compliance_rules.due_dates)."""
    if (rule.get("due") or {}).get("type") == "quarterly":
        qs = date(*rules_mod._add_months(first.year, first.month, -2), 1)
        return f"{qs.strftime('%b')}–{first.strftime('%b %Y')}"
    return first.strftime("%B %Y")


async def _due(ws: str, rule: dict, first: date, t: date) -> tuple[dict | None, dict | None]:
    label = _rule_period(rule, first)
    items = [i async for i in db().compliance_items.find({"owner_id": ws, "rule_key": rule["key"], "period": label, "source": "rule"})
             .sort("due_date", 1).limit(5)]
    item = next((i for i in items if i.get("due_date")), None)
    if item:
        return {"date": item["due_date"], "title": item.get("title") or rule.get("title", ""), "item_id": item["_id"], "period": label,
                "status": compliance.status_of(item, t), "from": "calendar"}, item
    # not in the calendar (e.g. the due date had passed before the business identity was filled): the rules table decides
    when = [w for lab, w in rules_mod.due_dates(rule, first, first + timedelta(days=200)) if lab == label]
    if when:
        return {"date": when[0].isoformat(), "title": rule.get("title", ""), "item_id": None, "period": label,
                "status": "overdue" if when[0] < t else "upcoming", "from": "rules"}, None
    return None, None


# ───────────────────────── the page ─────────────────────────
def _cash_flow(row: dict, item: dict | None, est_rupees: int, has_cash_flow: bool) -> dict:
    """Is this period's GST item in Cash flow, with whose amount, and can this page add or update it?"""
    amount = item.get("amount") if item else None
    ours = bool(item) and item.get("amount_source") == SOURCE and _same(amount, item.get("amount_estimate"))
    upcoming = bool(item) and item.get("status") == "upcoming"
    days_left = row["days_left"]
    ahead = days_left is not None and days_left >= 0          # Cash flow counts from today on
    in_cf = upcoming and ahead and amount is not None and float(amount) > 0
    note, shows_from = None, None
    if not has_cash_flow:
        note = "Counting it in cash flow needs Cash flow and the Compliance calendar in your plan."
    elif item and item.get("status") == "done":
        note = "Marked done in your compliance calendar."
    elif row["due"] and not ahead:
        note = "The due date has passed, so it won't show in your 30-day cash flow. If it's paid, mark it done in your compliance calendar."
    elif row["due"] and not item:
        note = "This GST payment isn't in your compliance calendar, so it can't go into your cash flow."
    elif item and est_rupees <= 0 and not in_cf:
        note = "Nothing to keep aside, so nothing to add to your cash flow."
    can = has_cash_flow and upcoming and ahead and est_rupees > 0
    if (can or in_cf) and days_left is not None and days_left > 29:
        shows_from = (date.fromisoformat(row["due"]["date"]) - timedelta(days=29)).isoformat()
    return {"in_cash_flow": in_cf, "cash_flow_amount": float(amount) if ours else None,
            "owner_amount": float(amount) if (item and amount is not None and not ours) else None,
            "needs_update": bool(ours and upcoming and not _same(amount, est_rupees)), "can_count": can, "can_remove": bool(ours and upcoming),
            "cash_flow_note": note, "cash_flow_shows_from": shows_from}


def _period_notes(p: dict, n: dict, carry: int, registered, scheme, due: dict | None) -> list[str]:
    when = "this month" if not p["complete"] else f"in {p['first'].strftime('%B')}"
    notes = []
    if not p["complete"]:
        notes.append("This month isn't over yet, so the estimate will grow as you invoice and add bills.")
    if not n["invoices"]:
        notes.append(f"No invoices in the hub {when}.")
    if n["bills_no_gst"]:
        k = n["bills_no_gst"]
        notes.append(f"{_n(k, 'bill', 'bills')} {when} {'has' if k == 1 else 'have'} no GST entered. If {'it was a GST bill' if k == 1 else 'any were GST bills'}, "
                     "add the GST in Expenses.")
    if n["bills_waiting"]:
        k = n["bills_waiting"]
        notes.append(f"{_n(k, 'GST bill', 'GST bills')} {when} {'is' if k == 1 else 'are'} waiting for approval in Expenses — not counted until approved.")
    if n["bills_not_claimable"]:
        k = n["bills_not_claimable"]
        notes.append(f"{_n(k, 'bill', 'bills')} with GST {when} {'is' if k == 1 else 'are'}n't marked as GST you can claim back, so "
                     f"{'it is' if k == 1 else 'they are'} not taken off.")
    if n["upload_rows_no_gst"]:
        k = n["upload_rows_no_gst"]
        notes.append(f"{_n(k, 'uploaded sale', 'uploaded sales')} {when} {'has' if k == 1 else 'have'} no GST amount, so "
                     f"{'it is' if k == 1 else 'they are'} not counted. Upload a sheet with a GST column to count {'it' if k == 1 else 'them'}.")
    if n["upload_rows_duplicate"]:
        k = n["upload_rows_duplicate"]
        notes.append(f"{_n(k, 'uploaded sale', 'uploaded sales')} {when} {'is' if k == 1 else 'are'} already in the hub (same invoice number "
                     "and year, same customer or amount) — counted once, from the hub.")
    if carry:
        notes.append(f"The GST on your bills {when} is {_money(carry)} more than the GST on your sales. Your CA may carry this credit forward; "
                     "it isn't taken off any other month here.")
    if registered and scheme == "qrmp" and p["first"].month % 3 == 0:
        m1 = date(*rules_mod._add_months(p["first"].year, p["first"].month, -2), 1).strftime("%B")
        m2 = date(*rules_mod._add_months(p["first"].year, p["first"].month, -1), 1).strftime("%B")
        notes.append(f"This is {p['first'].strftime('%B')}'s GST only. The quarterly GSTR-3B settles the whole quarter: if what you paid by PMT-06 "
                     f"for {m1} and {m2} was different from the GST actually due, the difference is settled with this payment too.")
    if registered is None:
        notes.append("Fill your business identity in the Knowledge Hub to get the due date.")
    elif registered and not due:
        notes.append("Your compliance calendar has no GST payment for this month, so there's no due date here. Ask your CA for it.")
    return notes


async def _compute(ctx: Ctx) -> tuple[dict, dict[str, dict]]:
    """The page, and the compliance item behind each period (for the cash-flow actions)."""
    ws, t = ctx.ws, today()
    b = await db().businesses.find_one({"owner_id": ws}, {"identity": 1}) or {}
    identity = b.get("identity") or None
    registered = identity.get("gst_registered") if identity and isinstance(identity.get("gst_registered"), bool) else None
    scheme = (identity.get("gst_scheme") or "monthly") if registered else None
    periods = _periods(t)
    nums, capped = await _numbers(ws, periods)
    sources = {"invoices": sum(n["invoices"] for n in nums.values()), "gateway_invoices": sum(n["gateway_invoices"] for n in nums.values()),
               "credit_notes": sum(n["credit_notes"] for n in nums.values()), "uploads": sum(n["upload_rows"] for n in nums.values()),
               "bills": sum(n["bills_count"] for n in nums.values())}
    has_cf = ctx.has("cash_flow") and ctx.has("compliance_calendar")
    base = {"label": LABEL, "today": t.isoformat(), "registered": registered, "scheme": scheme, "sources": sources, "can_cash_flow": has_cf}
    capped_note = [f"Only the first {CAP:,} records were read for: {', '.join(capped)}, so these numbers are incomplete — ask your CA."] if capped else []

    if registered is False:
        charged = sum(n["sales"] - n["credit"] + n["uploaded"] for n in nums.values())
        notes = ["If you register for GST later, change it in the Knowledge Hub and this page will start working out the estimate."]
        if charged > 0:
            notes.insert(0, f"Your invoices and uploaded sales show {_money(charged)} of GST charged in {periods[0]['first'].strftime('%B')} and "
                            f"{periods[1]['first'].strftime('%B')}. If you charge GST, check with your CA and update your business identity.")
        return {**base, "periods": [], "notes": notes + capped_note,
                "message": "Your Knowledge Hub says the business isn't registered for GST, so there's no GST payment to keep money aside for."}, {}

    rules: list[dict] = []
    if registered:
        if ctx.has("compliance_calendar"):
            try:
                await compliance._fresh(ws)   # the calendar follows the latest rules (e.g. an extended date) before it's read
            except Exception:  # noqa: BLE001  the estimate still shows; the date falls back to the rules table below
                log.exception("refreshing the compliance calendar for the GST set-aside failed")
        rules = await compliance.rules()

    out, items = [], {}
    for p in periods:
        n = nums[p["key"]]
        net = n["sales"] - n["credit"] + n["uploaded"] - n["bills"]
        est, carry = max(0, net), max(0, -net)
        due, item = None, None
        if registered:
            rule = _payment_rule(rules, identity, p["first"].month)
            if rule:
                due, item = await _due(ws, rule, p["first"], t)
        row = {"period": p["key"], "period_label": p["first"].strftime("%B %Y"), "complete": p["complete"],
               "sales_gst": _rs(n["sales"]), "credit_notes_gst": _rs(n["credit"]), "uploaded_sales_gst": _rs(n["uploaded"]),
               "net_sales_gst": _rs(n["sales"] - n["credit"] + n["uploaded"]), "bills_gst": _rs(n["bills"]),
               "bills_count": n["bills_count"], "bills_without_gst_marked": n["bills_no_gst"],
               "estimate": _rs(est), "estimate_rounded": _whole(est), "carry_forward": _rs(carry),
               "counts": {"invoices": n["invoices"], "gateway_invoices": n["gateway_invoices"], "credit_notes": n["credit_notes"],
                          "uploads": n["upload_rows"], "uploads_without_gst": n["upload_rows_no_gst"],
                          "uploads_already_in_hub": n["upload_rows_duplicate"], "bills": n["bills_count"],
                          "bills_not_claimable": n["bills_not_claimable"], "bills_waiting": n["bills_waiting"]},
               "due": due, "days_left": (date.fromisoformat(due["date"]) - t).days if due else None}
        row.update(_cash_flow(row, item, _whole(est), has_cf))
        row["notes"] = _period_notes(p, n, carry, registered, scheme, due)
        out.append(row)
        if item:
            items[p["key"]] = item

    notes = []
    if not any(n["upload_rows"] + n["upload_rows_no_gst"] + n["upload_rows_duplicate"] for n in nums.values()):
        notes.append(UPLOAD_HINT)
    # an estimate this page put on a GST item that no longer matches either month (e.g. after switching to QRMP)
    if registered:
        shown = {i["_id"] for i in items.values()}
        async for ci in db().compliance_items.find({"owner_id": ws, "amount_source": SOURCE, "status": "upcoming",
                                                    "due_date": {"$gte": t.isoformat()}}).limit(20):
            if ci["_id"] not in shown and _same(ci.get("amount"), ci.get("amount_estimate")) and float(ci.get("amount") or 0) > 0:
                notes.append(f"An earlier estimate of {_money(_paise(ci['amount']))} is still on “{ci.get('title', 'GST')}"
                             f"{' (' + ci['period'] + ')' if ci.get('period') else ''}” in your compliance calendar, due "
                             f"{date.fromisoformat(ci['due_date']).strftime('%d %b')}. If it no longer applies, change it there.")
    message = None
    if registered is None:
        message = ("Fill your business identity in the Knowledge Hub — GST registration, monthly or QRMP filing, and your state — "
                   "so the hub knows which GST payment is due and when.")
    return {**base, "periods": out, "notes": notes + capped_note, "message": message}, items


@router.get("/gst-setaside")
async def get_gst_setaside(ctx: Ctx = Depends(gst_ctx)):
    return (await _compute(ctx))[0]


# ───────────────────────── into Cash flow ─────────────────────────
class CashFlowIn(BaseModel):
    period: str = Field(pattern=r"^20\d\d-(0[1-9]|1[0-2])$")
    replace: bool = False


def _not_registered(page: dict) -> None:
    if page["registered"] is False:
        raise HTTPException(400, "Your Knowledge Hub says the business isn't registered for GST, so there's nothing to add.")
    if page["registered"] is not True:
        raise HTTPException(400, "Fill your business identity in the Knowledge Hub first, so the hub knows which GST payment this is.")


@router.put("/gst-setaside/cash-flow")
async def count_in_cash_flow(body: CashFlowIn, ctx: Ctx = Depends(gst_ctx)):
    """The estimate (rounded to the rupee) becomes the amount on that period's GST item, so Cash flow counts it on its due date."""
    ctx.require("cash_flow")
    ctx.require("compliance_calendar")
    page, items = await _compute(ctx)
    _not_registered(page)
    row = next((r for r in page["periods"] if r["period"] == body.period), None)
    if not row:
        raise HTTPException(400, "Pick last month or this month.")
    item = items.get(body.period)
    if not item:
        raise HTTPException(400, "This GST payment isn't in your compliance calendar, so it can't go into your cash flow.")
    if item.get("status") != "upcoming":
        raise HTTPException(409, "This GST payment is marked done in your compliance calendar, so it can't be changed here.")
    if row["days_left"] is None or row["days_left"] < 0:
        raise HTTPException(400, "The due date has passed, so it won't show in your 30-day cash flow. If it's paid, mark it done in your compliance calendar.")
    amount = row["estimate_rounded"]
    if amount <= 0:
        raise HTTPException(400, "The estimate is ₹0, so there's nothing to add to your cash flow.")
    owner_amount = row["owner_amount"]
    if owner_amount is not None and not body.replace:
        raise HTTPException(409, {"code": "owner_amount", "owner_amount": owner_amount, "estimate": amount,
                                  "message": f"You typed {inr(_paise(owner_amount))} on this GST payment in your compliance calendar. "
                                             f"Replace it with the estimate of {inr(amount * 100)}?"})
    patch = {"amount": float(amount), "amount_source": SOURCE, "amount_estimate": float(amount), "amount_estimated_at": now(),
             "amount_estimate_period": body.period, "updated_at": now()}
    if owner_amount is not None:
        patch["amount_replaced"] = {"amount": owner_amount, "at": now(), "by": ctx.actor}   # what the owner had typed, kept for the record
    # only if nothing changed since it was read (an amount typed in the meantime is never overwritten)
    r = await db().compliance_items.update_one({"_id": item["_id"], "owner_id": ctx.ws, "status": "upcoming", "amount": item.get("amount")},
                                               {"$set": patch})
    if not r.matched_count:
        raise HTTPException(409, "This GST payment changed in the meantime. Refresh the page and try again.")
    bust(ctx.ws)
    await track("gst_setaside_cash_flow", ctx.ws, period=body.period, replaced=owner_amount is not None)
    return (await _compute(ctx))[0]


@router.delete("/gst-setaside/cash-flow")
async def remove_from_cash_flow(period: str, ctx: Ctx = Depends(gst_ctx)):
    """Takes this page's estimate off the GST item. An amount the owner typed stays."""
    if not PERIOD.match(period or ""):
        raise HTTPException(400, "Use a month like 2026-10")
    page, items = await _compute(ctx)
    item = items.get(period)
    if not item:   # an estimate added for a month that's no longer shown
        item = await db().compliance_items.find_one({"owner_id": ctx.ws, "amount_source": SOURCE, "amount_estimate_period": period})
    if not item:
        raise HTTPException(404, "There's no GST estimate from this page in your cash flow for that month.")
    if item.get("status") != "upcoming":
        raise HTTPException(409, "This GST payment is marked done in your compliance calendar, so it can't be changed here.")
    if item.get("amount") is None:
        return page
    if item.get("amount_source") != SOURCE or not _same(item.get("amount"), item.get("amount_estimate")):
        raise HTTPException(409, "This amount was typed in your compliance calendar, not added from this page, so it stays. Change it in Compliance.")
    r = await db().compliance_items.update_one({"_id": item["_id"], "owner_id": ctx.ws, "status": "upcoming", "amount_source": SOURCE,
                                                "amount": item.get("amount")},
                                               {"$set": {"amount": None, "updated_at": now()},
                                                "$unset": {"amount_source": "", "amount_estimate": "", "amount_estimated_at": "",
                                                           "amount_estimate_period": ""}})
    if not r.matched_count:
        raise HTTPException(409, "This GST payment changed in the meantime. Refresh the page and try again.")
    bust(ctx.ws)
    await track("gst_setaside_cash_flow_removed", ctx.ws, period=period)
    return (await _compute(ctx))[0]
