"""
Cash flow: the next 30 days (v2.1, 3.14). A forecast from what the owner entered — not their accounts. No Tally.

Opening balance: the bank balance in the Knowledge Hub's money basics (with its date; the page asks for a fresh one when
it's more than 7 days old), rolled forward to today: costs and owner-entered money dated after it come off or on, and
amounts marked paid since it was typed are added. Then, day by day:
    money in   open Money owed entries, each on its due date + that customer's average delay (from how they paid before);
               an amount already later than that isn't counted until it comes in (it's listed under "close the gap"),
               open quotations × the owner's probability (off by default), recurring income the owner adds
    money out  fixed monthly costs from the Knowledge Hub on their day, one-off payments the owner adds, compliance items
               that carry an amount (e.g. the GST estimate), EMIs
The result: the balance every day, the lowest point and its date, the first day below zero (or below the owner's safety
buffer), and "how to close the gap": the dues to collect first, ranked by amount and how reliably that customer pays.
A what-if moves one due later ("if Mehta Traders pays 15 days late"). Computed on request; nothing is stored.

cash_items {_id, owner_id, kind in|out, title, amount (₹), date "YYYY-MM-DD" | day_of_month, recurrence none|monthly,
            source owner, created_at}
"""
import calendar
import re
from datetime import date, timedelta

from .db import IST, db, now

DAYS = 30
LABEL = "A forecast from what you've entered — not your accounts."


def _key(name: str, phone: str = "") -> str:
    digits = re.sub(r"\D", "", phone or "")[-10:]
    return digits if len(digits) == 10 else re.sub(r"\s+", " ", (name or "").strip().lower())


async def payment_habits(ws: str) -> dict[str, float]:
    """Average days late per customer, from dues they've paid (early counts as on time)."""
    late: dict[str, list[int]] = {}
    async for d in db().dues.find({"ws": ws, "status": "paid", "paid_at": {"$ne": None}}, {"customer": 1, "phone": 1, "due_date": 1, "paid_at": 1}).limit(5000):
        try:
            days = (d["paid_at"].astimezone(IST).date() - date.fromisoformat(d["due_date"])).days
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
        late.setdefault(_key(d.get("customer", ""), d.get("phone", "")), []).append(max(days, 0))
    return {k: sum(v) / len(v) for k, v in late.items()}


def likelihood(avg_late: float | None) -> tuple[str, float]:
    if avg_late is None:
        return "no history", 0.7
    if avg_late <= 3:
        return "pays on time", 1.0
    if avg_late <= 15:
        return "usually a little late", 0.7
    return "often late", 0.4


def _monthly_dates(day: int, start: date, end: date) -> list[date]:
    out, y, m = [], start.year, start.month
    while True:
        d = date(y, m, min(day, calendar.monthrange(y, m)[1]))
        if d > end:
            break
        if d >= start:
            out.append(d)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


async def _scheduled(ws: str, money: dict, a: date, b: date, done_too: bool = False) -> list[dict]:
    """Fixed costs from the Knowledge Hub, the owner's own cash items and compliance amounts between two days."""
    out = []
    for c in money.get("fixed_costs") or []:
        for d in _monthly_dates(int(c.get("day") or 1), a, b):
            out.append({"date": d.isoformat(), "kind": "out", "amount": float(c.get("amount") or 0), "title": c.get("name", "Fixed cost"),
                        "source": "brain", "category": c.get("kind", "other")})
    async for item in db().cash_items.find({"owner_id": ws}):
        if item.get("recurrence") == "monthly" and item.get("day_of_month"):
            dates = _monthly_dates(int(item["day_of_month"]), a, b)
        elif item.get("date"):
            d = date.fromisoformat(item["date"])
            dates = [d] if a <= d <= b else []
        else:
            dates = []
        for d in dates:
            out.append({"date": d.isoformat(), "kind": item["kind"], "amount": float(item["amount"]), "title": item["title"], "source": "owner",
                        "id": item["_id"]})
    q = {"owner_id": ws, "amount": {"$gt": 0}, "due_date": {"$gte": a.isoformat(), "$lte": b.isoformat()}}
    if not done_too:
        q["status"] = {"$ne": "done"}
    async for ci in db().compliance_items.find(q):
        out.append({"date": ci["due_date"], "kind": "out", "amount": float(ci["amount"]), "title": ci["title"], "source": "compliance", "id": ci["_id"]})
    return out


async def forecast(ws: str, start: date | None = None, delays: dict[str, int] | None = None, days: int = DAYS) -> dict:
    start = start or now().astimezone(IST).date()
    end = start + timedelta(days=days - 1)
    delays = delays or {}
    b = await db().businesses.find_one({"owner_id": ws}, {"money": 1, "name": 1}) or {}
    money = b.get("money") or {}
    settings_doc = await db().cash_settings.find_one({"_id": ws}) or {}
    buffer = float(settings_doc.get("safety_buffer", money.get("safety_buffer") or 0))
    probability = int(settings_doc.get("quote_probability") or 0)
    opening = money.get("bank_balance")
    balance_on = money.get("bank_balance_on")
    events: list[dict] = []

    habits = await payment_habits(ws)
    dues = [d async for d in db().dues.find({"ws": ws, "status": "due"}).limit(2000)]
    for d in dues:
        k = _key(d.get("customer", ""), d.get("phone", ""))
        avg = habits.get(k)
        try:
            due_day = date.fromisoformat(d["due_date"])
        except (KeyError, ValueError):
            continue
        expected = due_day + timedelta(days=round(avg or 0) + int(delays.get(d["_id"], 0)))
        if expected < start or expected > end:
            continue   # already later than this customer usually pays: not counted until it comes in (listed under "close the gap")
        events.append({"date": expected.isoformat(), "kind": "in", "amount": int(d.get("amount") or 0) / 100, "title": d.get("customer", ""),
                       "source": "money_owed", "id": d["_id"], "due_date": d["due_date"], "late": False,
                       "habit": likelihood(avg)[0], "delay_days": round(avg or 0)})
    if probability > 0:
        async for q in db().quotes.find({"ws": ws, "kind": "quote", "status": "sent", "invoice_id": None}).limit(500):
            sent = date.fromisoformat(q.get("sent_on") or q["created_at"].astimezone(IST).date().isoformat())
            when = max(sent + timedelta(days=int(q.get("valid_days") or 15) + int(money.get("payment_terms_days") or 0)), start)
            if when <= end:
                events.append({"date": when.isoformat(), "kind": "in", "amount": round(float(q["totals"]["total"]) * probability / 100, 2),
                               "title": f"{(q.get('customer') or {}).get('name', '')} — quotation {q.get('number', '')} × {probability}%",
                               "source": "quote", "id": q["_id"]})
    events += await _scheduled(ws, money, start, end)

    if opening is None:
        return {"ready": False, "label": LABEL, "needs": "bank_balance", "events": sorted(events, key=lambda e: e["date"]),
                "safety_buffer": buffer, "quote_probability": probability}
    # money marked paid after the owner last typed the balance is in the bank now, so it's added to the opening balance
    # the balance is "as of" its date: costs and owner-entered money between then and today come off (or on) first
    rolled = 0.0
    if balance_on and date.fromisoformat(balance_on) < start - timedelta(days=1):
        for e in await _scheduled(ws, money, date.fromisoformat(balance_on) + timedelta(days=1), start - timedelta(days=1), done_too=True):
            rolled += e["amount"] if e["kind"] == "in" else -e["amount"]
    received_since = 0.0
    saved_at = money.get("bank_balance_saved_at")
    if saved_at:
        async for d in db().dues.find({"ws": ws, "status": "paid", "paid_at": {"$gt": saved_at}}, {"amount": 1}).limit(5000):
            received_since += int(d.get("amount") or 0) / 100
        # invoices paid directly (a gateway payment with no Money owed entry) are in the bank too; refunds went out
        async for q in db().quotes.find({"ws": ws, "kind": "invoice", "status": "paid", "due_id": None, "paid_at": {"$gt": saved_at}},
                                        {"totals": 1}).limit(5000):
            received_since += float((q.get("totals") or {}).get("total") or 0)
        async for q in db().quotes.find({"ws": ws, "kind": "credit_note", "created_at": {"$gt": saved_at}}, {"totals": 1}).limit(5000):
            received_since -= float((q.get("totals") or {}).get("total") or 0)
    opening = float(opening) + received_since + rolled
    by_day: dict[str, list] = {}
    for e in events:
        by_day.setdefault(e["date"], []).append(e)
    bal = float(opening)
    series, lowest, first_short = [], None, None
    for i in range(days):
        d = (start + timedelta(days=i)).isoformat()
        evs = sorted(by_day.get(d, []), key=lambda e: (e["kind"] != "out", -e["amount"]))
        inflow = sum(e["amount"] for e in evs if e["kind"] == "in")
        outflow = sum(e["amount"] for e in evs if e["kind"] == "out")
        bal = round(bal + inflow - outflow, 2)
        series.append({"date": d, "in": round(inflow, 2), "out": round(outflow, 2), "balance": bal, "events": evs})
        if lowest is None or bal < lowest["balance"]:
            lowest = {"date": d, "balance": bal}
        if first_short is None and bal < buffer:
            first_short = {"date": d, "balance": bal, "short_by": round(buffer - bal, 2)}
    short_until = None
    if first_short:
        for day in series[[s["date"] for s in series].index(first_short["date"]):]:
            if day["balance"] >= buffer:
                break
            short_until = day["date"]
    # how to close the gap: the dues worth collecting first — bigger and more reliable first
    gap = []
    for e in events:
        if e["source"] != "money_owed":
            continue
        label, weight = likelihood(None if e.get("habit") == "no history" else e.get("delay_days"))
        gap.append({"id": e["id"], "customer": e["title"], "amount": e["amount"], "expected": e["date"], "due_date": e["due_date"], "late": e["late"],
                    "habit": e["habit"], "score": round(e["amount"] * weight, 2),
                    "before_short": bool(first_short) and e["date"] < first_short["date"]})
    # dues not expected in the window still count: collecting them earlier closes the gap too
    in_window = {g["id"] for g in gap}
    for d in dues:
        if d["_id"] in in_window:
            continue
        k = _key(d.get("customer", ""), d.get("phone", ""))
        label, weight = likelihood(habits.get(k))
        amount = int(d.get("amount") or 0) / 100
        gap.append({"id": d["_id"], "customer": d.get("customer", ""), "amount": amount, "expected": None, "due_date": d.get("due_date"),
                    "late": (d.get("due_date") or "9999") < start.isoformat(),
                    "habit": label, "score": round(amount * weight, 2), "before_short": False})
    gap.sort(key=lambda g: -g["score"])
    return {"ready": True, "label": LABEL, "start": start.isoformat(), "end": end.isoformat(), "opening": float(opening), "balance_on": balance_on,
            "received_since_balance": round(received_since, 2), "since_balance_date": round(rolled, 2),
            "balance_days_old": (start - date.fromisoformat(balance_on)).days if balance_on else None, "safety_buffer": buffer,
            "quote_probability": probability, "days": series, "lowest": lowest, "shortfall": first_short and {**first_short, "until": short_until},
            "close_the_gap": gap[:8] if first_short else gap[:3], "money_in": round(sum(e["amount"] for e in events if e["kind"] == "in"), 2),
            "money_out": round(sum(e["amount"] for e in events if e["kind"] == "out"), 2), "delays": delays}
