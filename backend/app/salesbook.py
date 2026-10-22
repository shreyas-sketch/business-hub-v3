"""
The sales book (v2.2): the one place that reads a business's sales history. Customer risk and Getting paid read it, and
the GST set-aside may import it too. Nothing here writes — sales sheets are saved by routers/moneyinsights.py.

Where sales come from (`source` on each row):
    invoice   hub invoices (quotes, kind "invoice"), made from a quotation or directly. Credit notes (kind "credit_note")
              come back as negative rows. Invoices with a status in CANCELLED are left out.
    razorpay  invoices the hub made on its own from a Razorpay payment (source "gateway" / gateway "razorpay"), and the
              credit notes for their refunds
    upload    rows from a sales sheet the owner uploaded (Tally's Sales Register, or any Excel/CSV)
Customers on the Customers list who have none of the above keep counting through their own totals
(customers_without_history, marked source "customers list").

What the upload stores (money in paise, dates "YYYY-MM-DD" in India time). Other features read these fields as they are:
sales_rows    {_id, ws, batch_id, date, customer (≤ 80 chars), phone ("" or "+91…"), amount (paise, total including GST;
               negative for a return / credit note), gst (paise, same sign as amount; None when the sheet had no tax
               column), invoice_no ("" when none), due_date ("" or a date), paid_on ("" or a date), created_at}
sales_batches {_id, ws, filename, rows, from, to, total (paise), created_by, created_at,
               columns {field: the sheet's column name(s)}, has_paid_on (the sheet had a "Paid on" column, so an empty
               one there means "not paid yet"; without that column nothing is known about payment)}

Every read is bounded: at most MAX_ROWS from each source, newest first.
"""
import re
from datetime import date, datetime, time

from .cashflow import _key
from .db import IST, db

MAX_ROWS = 20_000
CANCELLED = ("cancelled", "canceled", "void", "voided", "draft")

# Cash sales, walk-ins and unnamed payers are many people under one label: they count in the total, never as one customer
_WALK_IN = {
    "cash", "cash sale", "cash sales", "cash a c", "cash ac", "cash account", "cash in hand", "cash customer", "cash customers",
    "cash party", "walk in", "walkin", "walk in customer", "walk in customers", "walkin customer", "walkin customers",
    "counter", "counter sale", "counter sales", "retail customer", "retail customers", "retail sale", "retail sales",
    "general customer", "misc customer", "miscellaneous customer", "sundry customer", "customer", "customers", "unknown",
    "na", "n a", "none", "guest",
}


def customer_key(name: str, phone: str = "") -> str:
    """How a customer is recognised across the hub — exactly cashflow's key: the last 10 digits of the phone when there
    are 10, otherwise the name in lower case with single spaces. Money owed, Cash flow and these pages match on it."""
    return _key(name, phone)


def name_key(name: str) -> str:
    """The name part of customer_key: lower case, single spaces."""
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def phone_key(phone: str) -> str:
    """The last 10 digits of a phone number, or "" when it hasn't got 10."""
    digits = re.sub(r"\D", "", phone or "")[-10:]
    return digits if len(digits) == 10 else ""


def is_walk_in(name: str) -> bool:
    """True for "Cash", "Cash Sales", "Walk-in Customer", "Counter sale", an unnamed "Customer" and the like."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (name or "").lower())).strip() in _WALK_IN


def ist_date(at) -> date | None:
    """A stored datetime → its date in India. Naive datetimes are taken as UTC (how Motor returns them without tz_aware)."""
    if not isinstance(at, datetime):
        return None
    if at.tzinfo is None:
        from datetime import timezone
        at = at.replace(tzinfo=timezone.utc)
    return at.astimezone(IST).date()


def _iso(v) -> str:
    """A stored "YYYY-MM-DD" (or a datetime) → "YYYY-MM-DD"; anything else → ""."""
    if isinstance(v, datetime):
        d = ist_date(v)
        return d.isoformat() if d else ""
    try:
        return date.fromisoformat(str(v or "")[:10]).isoformat() if v else ""
    except ValueError:
        return ""


def _source(q: dict) -> str:
    return "razorpay" if q.get("gateway") == "razorpay" or q.get("source") == "gateway" else "invoice"


async def history(ws: str, since: date | None = None) -> list[dict]:
    """Every sale this business has in the hub, oldest first, from `since` (a date, India time) when given.

    Each row: {date "YYYY-MM-DD", key (customer_key), customer, phone, amount (₹, incl. GST; negative for a credit
    note or return), gst (₹ or None), invoice_no, source "invoice"|"razorpay"|"upload", due_date ("" or date),
    paid_on ("" or date)}. At most MAX_ROWS from the hub's invoices and MAX_ROWS from uploads (the newest)."""
    out: list[dict] = []
    q: dict = {"ws": ws, "kind": {"$in": ["invoice", "credit_note"]}}
    if since:
        q["created_at"] = {"$gte": datetime.combine(since, time(0), tzinfo=IST)}
    fields = {"kind": 1, "customer": 1, "totals": 1, "number": 1, "status": 1, "due_date": 1, "paid_at": 1, "created_at": 1,
              "source": 1, "gateway": 1}
    async for inv in db().quotes.find(q, fields).sort("created_at", -1).limit(MAX_ROWS):
        if str(inv.get("status") or "").lower() in CANCELLED:
            continue
        day = ist_date(inv.get("created_at"))
        if not day:
            continue
        cust = inv.get("customer") or {}
        name, phone = str(cust.get("name") or "").strip(), str(cust.get("phone") or "").strip()
        t = inv.get("totals") or {}
        sign = -1 if inv.get("kind") == "credit_note" else 1
        gst = t.get("gst")
        out.append({"date": day.isoformat(), "key": customer_key(name, phone), "customer": name, "phone": phone,
                    "amount": round(sign * float(t.get("total") or 0), 2), "gst": None if gst is None else round(sign * float(gst), 2),
                    "invoice_no": str(inv.get("number") or ""), "source": _source(inv),
                    "due_date": _iso(inv.get("due_date")) if sign > 0 else "", "paid_on": _iso(inv.get("paid_at")) if sign > 0 else ""})
    rq: dict = {"ws": ws}
    if since:
        rq["date"] = {"$gte": since.isoformat()}
    async for r in db().sales_rows.find(rq).sort("date", -1).limit(MAX_ROWS):
        gst = r.get("gst")
        out.append({"date": r["date"], "key": customer_key(r.get("customer", ""), r.get("phone", "")), "customer": r.get("customer", ""),
                    "phone": r.get("phone", ""), "amount": round(int(r.get("amount") or 0) / 100, 2),
                    "gst": None if gst is None else round(int(gst) / 100, 2), "invoice_no": r.get("invoice_no", ""), "source": "upload",
                    "due_date": _iso(r.get("due_date")), "paid_on": _iso(r.get("paid_on"))})
    out.sort(key=lambda r: r["date"])
    return out


async def customers_without_history(ws: str, rows: list[dict] | None = None) -> list[dict]:
    """Customers on the Customers list with no sales in `rows` (history(ws) when not given) — matched by name or by
    phone, so a customer is never counted twice — and with something recorded (a value or purchases).

    Each: {key, customer, phone, total_value (₹, as typed or imported — it can cover any length of time), purchases,
    first_purchase ("" or date), last_purchase ("" or date), reorder_days (int or None), source "customers list"}."""
    if rows is None:
        rows = await history(ws)
    names = {name_key(r.get("customer", "")) for r in rows}
    phones = {phone_key(r.get("phone", "")) for r in rows} - {""}
    out = []
    fields = {"name": 1, "phone": 1, "total_value": 1, "purchases": 1, "first_purchase": 1, "last_purchase": 1, "reorder_days": 1}
    async for c in db().customers.find({"ws": ws}, fields).sort("total_value", -1).limit(MAX_ROWS):
        name, phone = str(c.get("name") or "").strip(), str(c.get("phone") or "").strip()
        if not name or name_key(name) in names or (phone_key(phone) and phone_key(phone) in phones):
            continue
        try:
            value, purchases = max(float(c.get("total_value") or 0), 0.0), max(int(c.get("purchases") or 0), 0)
        except (TypeError, ValueError):
            continue
        if value <= 0 and purchases <= 0:
            continue
        reorder = c.get("reorder_days")
        out.append({"key": customer_key(name, phone), "customer": name, "phone": phone, "total_value": round(value, 2), "purchases": purchases,
                    "first_purchase": _iso(c.get("first_purchase")), "last_purchase": _iso(c.get("last_purchase")),
                    "reorder_days": int(reorder) if isinstance(reorder, (int, float)) and reorder > 0 else None, "source": "customers list"})
    return out


def group_customers(rows: list[dict]) -> list[dict]:
    """Rows ({customer, phone, date?, …}) → one group per customer: {key, customer, phone, rows}.

    Rows group by customer_key. A name-only group (a sheet without phone numbers) joins the group with a phone when
    exactly one such group has that same name; with two or more (two different "Rahul"s) it stays apart rather than
    guess. The name shown is the most recent one used; the phone, the most recent one given."""
    groups: dict[str, list[dict]] = {}
    for r in rows:
        k = r.get("key") or customer_key(r.get("customer", ""), r.get("phone", ""))
        groups.setdefault(k, []).append(r)
    by_name: dict[str, set[str]] = {}
    for k, rs in groups.items():
        if re.fullmatch(r"\d{10}", k):
            for nk in {name_key(r.get("customer", "")) for r in rs}:
                by_name.setdefault(nk, set()).add(k)
    for k in list(groups):
        if re.fullmatch(r"\d{10}", k):
            continue
        targets = by_name.get(k) or set()
        if len(targets) == 1:
            groups[next(iter(targets))].extend(groups.pop(k))
    out = []
    for k, rs in groups.items():
        rs.sort(key=lambda r: r.get("date") or "")
        named = [r for r in rs if (r.get("customer") or "").strip()]
        phoned = [r for r in rs if (r.get("phone") or "").strip()]
        out.append({"key": k, "customer": (named[-1]["customer"] if named else "").strip(), "phone": phoned[-1]["phone"] if phoned else "",
                    "rows": rs})
    return out
