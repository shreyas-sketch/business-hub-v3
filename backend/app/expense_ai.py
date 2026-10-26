"""
Expenses & bank statement — the analysis (worked out by the hub, no AI) and the checks around the two opt-in AI steps
(v2.3, Data engine).

The analysis (GET /api/expenses-ai/analysis): spend month by month, each category over time, the share of each
category, the payees paid the most, and money in against money out — money in from the hub's sales (invoices, Razorpay,
an uploaded sales sheet) or, when there are none, from the bank statement still on the bank statement page.

The AI advice (on the owner's tap, 1 AI token) only ever sees the figures below in `facts()` — totals, never a
statement line — and salaries are left out unless the owner ticks "include salaries". Every number in what comes back is
checked against those figures, and a sentence with a number that isn't there is dropped (`check_numbers`).
"""
import re
from datetime import date

from . import bankstatement as bs, salesbook
from .db import db, now
from .routers import expenses as ex

MIN_MONTHS, MAX_MONTHS = 3, 12


def fmt(rupees: float | int) -> str:
    """₹1,23,456 (whole rupees, Indian grouping)."""
    n = int(round(float(rupees or 0)))
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        s = ",".join(([head] if head else []) + groups) + "," + tail
    return ("-" if n < 0 else "") + "₹" + s


def month_label(m: str) -> str:
    return date(int(m[:4]), int(m[5:7]), 1).strftime("%b %Y")


def _rs(paise: int) -> int:
    return int(round(int(paise or 0) / 100))


async def analysis(ws: str, months: int = 6, include_salaries: bool = True) -> dict:
    months = max(MIN_MONTHS, min(MAX_MONTHS, int(months or 6)))
    kind, custom, _ = await ex._setup(ws)
    this = ex._this_month()
    window = [ex._shift(this, -i) for i in range(months - 1, -1, -1)]
    a, b = ex._bounds(window[0])[0], ex._bounds(this)[1]
    docs = [d async for d in db().expenses.find({"ws": ws, "date": {"$gte": a, "$lte": b}},
                                                {"date": 1, "amount": 1, "category": 1, "vendor": 1, "recurring": 1, "created_at": 1, "cash_item_id": 1})
            .sort("date", -1).limit(ex.SUMMARY_CAP + 1)]
    capped = len(docs) > ex.SUMMARY_CAP
    docs = docs[:ex.SUMMARY_CAP]
    salary_paise = sum(int(d.get("amount") or 0) for d in docs if d.get("category") in ex.SALARY_CATEGORIES)
    if not include_salaries:
        docs = [d for d in docs if d.get("category") not in ex.SALARY_CATEGORIES]

    by_month = {m: 0 for m in window}
    cat: dict[str, dict] = {}
    payees: dict[str, dict] = {}
    for d in docs:
        m, amt, k = str(d.get("date", ""))[:7], int(d.get("amount") or 0), d.get("category") or "other"
        if m not in by_month:
            continue
        by_month[m] += amt
        c = cat.setdefault(k, {m_: 0 for m_ in window})
        c[m] += amt
        vk = ex._vendor_key(d.get("vendor", ""))
        if vk:
            p = payees.setdefault(vk, {"payee": bs.mask(d.get("vendor", ""))[:80], "amount": 0, "count": 0})
            p["amount"] += amt
            p["count"] += 1
    total = sum(by_month.values())
    last_full = window[-2] if len(window) >= 2 else window[-1]          # the current month is still going
    before = [m for m in window[:-2] if by_month[m]]
    categories = []
    for k, per in cat.items():
        t = sum(per.values())
        if not t:
            continue
        prior = [per[m] for m in before]
        avg = (sum(prior) / len(prior)) if prior else None
        last = per.get(last_full, 0)
        categories.append({
            "key": k, "label": ex._label_of(k, kind, custom), "amount": _rs(t), "share": round(t * 100 / total) if total else 0,
            "by_month": [{"month": m, "amount": _rs(per[m])} for m in window], "salary": k in ex.SALARY_CATEGORIES,
            "last_month": _rs(last), "usual": _rs(avg) if avg is not None else None,
            "last_vs_avg_pct": round((last - avg) * 100 / avg) if avg else None})
    categories.sort(key=lambda c: (-c["amount"], c["label"]))
    top = sorted(payees.values(), key=lambda p: (-p["amount"], p["payee"].lower()))[:10]

    # money in vs money out: the hub's own sales first, else the bank statement still on the bank statement page
    money = {"source": None, "months": []}
    sales = await salesbook.history(ws, since=date(int(window[0][:4]), int(window[0][5:7]), 1))
    if sales:
        ins = {m: 0.0 for m in window}
        for r in sales:
            if r["date"][:7] in ins:
                ins[r["date"][:7]] += float(r["amount"] or 0)
        money = {"source": "sales", "months": [{"month": m, "in": int(round(ins[m])), "out": _rs(by_month[m])} for m in window]}
    else:
        imp = await db().bank_imports.find_one({"ws": ws, "expires_at": {"$gt": now()}}, sort=[("created_at", -1)])
        if imp:
            s = bs.summary(imp.get("transactions") or [])
            money = {"source": "bank", "bank": imp.get("bank"), "from": imp.get("from"), "to": imp.get("to"),
                     "months": [{"month": x["month"], "in": int(round(x["in"])), "out": int(round(x["out"]))} for x in s["months"]]}

    # the payments that repeat (the Expenses page's own rule), over the last 3 months
    recent = [d for d in docs if str(d.get("date", ""))[:7] in set(window[-3:])]
    repeats = []
    for r in await ex._recurring(ws, [d for d in recent if d.get("date")], kind, custom):
        if not include_salaries and r.get("category") in ex.SALARY_CATEGORIES:
            continue
        repeats.append({"vendor": bs.mask(r.get("vendor") or r.get("category_label") or "")[:80], "category": r.get("category_label", ""),
                        "amount": int(round(float(r.get("amount") or 0))), "months": r.get("months", 0)})
    return {"months": [{"month": m, "label": month_label(m), "out": _rs(by_month[m]), "partial": m == this} for m in window],
            "total": _rs(total), "categories": categories, "top_payees": [{**p, "amount": _rs(p["amount"])} for p in top],
            "money": money, "repeats": repeats[:10], "include_salaries": include_salaries, "salaries_total": _rs(salary_paise),
            "capped": capped, "count": len(docs), "last_full_month": last_full}


def facts(an: dict, business: str) -> tuple[str, dict, dict]:
    """The only figures the AI sees, as text; the same figures for the draft writer; and the numbers it may quote."""
    rs, pct, other = set(), set(), {len(an["months"])}
    lines = []
    ms = an["months"]
    lines.append(f"Months: {month_label(ms[0]['month'])} to {month_label(ms[-1]['month'])} ({ms[-1]['label']} is the month so far).")
    lines.append("Spent by month: " + "; ".join(f"{m['label']} {fmt(m['out'])}" for m in ms) + ".")
    lines.append(f"Spent over these months: {fmt(an['total'])}.")
    rs.update([m["out"] for m in ms] + [an["total"]])
    figs = {"months": [{"label": m["label"], "out_text": fmt(m["out"])} for m in ms if not m["partial"]] or
            [{"label": m["label"], "out_text": fmt(m["out"])} for m in ms], "categories": [], "repeats": []}
    if an["categories"]:
        lines.append("By category over these months (amount, share of spend; last full month against its usual):")
    for c in an["categories"][:15]:
        line = f"- {c['label']}: {fmt(c['amount'])}, {c['share']}% of spend"
        rs.add(c["amount"])
        pct.add(c["share"])
        if c["usual"] is not None:
            line += f"; {month_label(an['last_full_month'])} {fmt(c['last_month'])}, usual {fmt(c['usual'])}"
            rs.update([c["last_month"], c["usual"]])
            if c["last_vs_avg_pct"] is not None:
                line += f" ({abs(c['last_vs_avg_pct'])}% {'above' if c['last_vs_avg_pct'] >= 0 else 'below'} usual)"
                pct.add(abs(c["last_vs_avg_pct"]))
        lines.append(line)
        figs["categories"].append({"label": c["label"], "amount_text": fmt(c["amount"]), "share": c["share"], "last_text": fmt(c["last_month"]),
                                   "last_vs_avg_pct": c["last_vs_avg_pct"]})
    if an["repeats"]:
        lines.append("Payments that repeat every month:")
        for r in an["repeats"]:
            lines.append(f"- {r['vendor']} ({r['category']}): about {fmt(r['amount'])} a month, in {r['months']} of the last 3 months")
            rs.add(r["amount"])
            other.add(r["months"])
            figs["repeats"].append({"vendor": r["vendor"], "amount_text": fmt(r["amount"])})
        other.add(len(an["repeats"]))
    mo = an["money"]
    if mo.get("months"):
        src = "sales recorded in the hub" if mo["source"] == "sales" else "the bank statement"
        lines.append(f"Money in ({src}) against money out, by month: " +
                     "; ".join(f"{month_label(x['month'])} in {fmt(x['in'])}, out {fmt(x['out'])}" for x in mo["months"]) + ".")
        rs.update([x["in"] for x in mo["months"]] + [x["out"] for x in mo["months"]])
    if not an["include_salaries"]:
        lines.append("Salaries are left out of every figure above, at the owner's choice.")
    years = {int(m["month"][:4]) for m in ms}
    return "\n".join(lines), figs, {"rs": rs, "pct": pct, "other": other | years}


# ───────────────────────── checking what the AI wrote ─────────────────────────
_RUPEE = re.compile(r"(?:₹|rs\.?|inr)\s?(\d[\d,]*(?:\.\d+)?)\s*(lakhs?|crores?|cr|l|k)?\b", re.I)
_PCT = re.compile(r"(\d+(?:\.\d+)?)\s?(?:%|per\s?cent|percent)", re.I)
_NUM = re.compile(r"(?<![\w₹.,])(\d[\d,]*(?:\.\d+)?)(?![\d%])")
_SCALE = {"lakh": 1e5, "lakhs": 1e5, "l": 1e5, "crore": 1e7, "crores": 1e7, "cr": 1e7, "k": 1e3}


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _near(v: float, allowed: set, tol: float) -> bool:
    return any(abs(v - a) <= max(1.0, abs(a) * tol) for a in allowed)


def sentence_ok(s: str, allowed: dict) -> bool:
    spans = []
    for m in _RUPEE.finditer(s):
        v = _num(m.group(1))
        unit = (m.group(2) or "").lower()
        if unit:
            if not _near(v * _SCALE[unit], allowed["rs"], 0.015):
                return False
        elif not _near(v, allowed["rs"], 0.005):
            return False
        spans.append(m.span())
    for m in _PCT.finditer(s):
        if not any(abs(_num(m.group(1)) - p) <= 1 for p in allowed["pct"]):
            return False
        spans.append(m.span())
    for m in _NUM.finditer(s):
        if any(a <= m.start() < b for a, b in spans):
            continue
        v = _num(m.group(1))
        if v <= 31 or v in allowed["other"] or _near(v, allowed["rs"], 0.005):
            continue
        return False
    return True


def check_numbers(text: str, allowed: dict) -> str:
    """Keeps the sentences whose every number is one of the hub's own figures."""
    parts = re.split(r"(?<=[.!?])\s+", str(text or "").strip())
    return " ".join(p for p in parts if p and sentence_ok(p, allowed)).strip()


def shape_advice(r: dict, allowed: dict) -> dict:
    r = r if isinstance(r, dict) else {}

    def items(key: str, fields: tuple) -> list[dict]:
        out = []
        for x in (r.get(key) or [])[:5]:
            if not isinstance(x, dict):
                continue
            title = check_numbers(str(x.get("title") or "")[:160], allowed)
            detail = check_numbers(str(x.get("detail") or "")[:600], allowed)
            if not title or not detail:
                continue
            item = {"title": title, "detail": detail}
            if "why" in fields:
                item["why"] = check_numbers(str(x.get("why") or "")[:300], allowed)
            out.append(item)
        return out

    out = {"headline": check_numbers(str(r.get("headline") or "")[:200], allowed) or "Where your money went",
           "findings": items("findings", ("title", "detail")), "recommendations": items("recommendations", ("title", "detail", "why"))}
    if not out["findings"] and not out["recommendations"]:
        raise ValueError("nothing usable came back")
    return out
