"""
AI jobs for Expenses & bank statement (v2.3, Data engine). Both run only when the owner taps for them.

- category suggestions: the AI sees only the payee names of the bank statement payments the rules couldn't sort (already
  masked: no account numbers, UPI IDs or e-mails) and the business's category list — no amounts, dates or balances.
- advice: the AI sees only totals the hub has already worked out — spend by month, by category, the payments that repeat,
  and sales by month when the hub has them. Never a statement line. Every rupee figure it may quote is in that list.
Each job has a deterministic draft for running without an AI key.
"""
import re

from .provider import complete
from .tasks import RULES

HINTS = [(r"PACK|CARTON|BOX", "packing"), (r"STEEL|TRADERS|METAL|ENTERPRISES|SUPPL", "purchases"), (r"TRAVEL|TOURS|IRCTC|UBER|OLA\b", "travel"),
         (r"PETROL|FUEL|HPCL|BPCL|IOCL", "fuel"), (r"COURIER|LOGISTIC|TRANSPORT|CARGO", "freight"), (r"AIRTEL|JIO|BROADBAND|TELECOM", "phone_internet"),
         (r"SOFTWARE|CLOUD|GOOGLE|MICROSOFT|ZOHO", "software"), (r"CA |ASSOCIATES|LEGAL|ADVOCATE|CONSULT", "professional"),
         (r"INSURANCE|LIFE|GENERAL INS", "insurance"), (r"REPAIR|SERVICE CENTRE|SERVICES", "repairs"), (r"ADS|MARKETING|PRINT", "marketing")]


async def suggest_categories(business: str, payees: list[dict], categories: list[dict]):
    keys = {c["key"] for c in categories}

    def mock():
        out = []
        for p in payees:
            name = str(p.get("payee") or "").upper()
            cat = next((c for rx, c in HINTS if re.search(rx, name) and c in keys), "purchases" if "purchases" in keys else None)
            if cat:
                out.append({"id": p["id"], "category": cat})
        return {"suggestions": out}

    cats = "\n".join(f"- {c['key']}: {c['label']}" for c in categories)
    names = "\n".join(f"{p['id']}. {p['payee']}" for p in payees)
    return await complete(RULES, f"""Business: {business}
These are payees from the owner's bank statement that the hub couldn't sort into an expense category. Only the payee names
are given. For each, pick the most likely category key from the list. If you can't tell, leave that payee out — don't guess
"other".

Categories (key: label):
{cats}

Payees:
{names}

JSON only: {{"suggestions": [{{"id": 1, "category": "key"}}]}}""", mock, 1500)


async def advice(business: str, facts: str, figures: dict):
    def mock():
        cats = figures.get("categories") or []
        months = figures.get("months") or []
        out_f, out_r = [], []
        if cats:
            c = cats[0]
            out_f.append({"title": f"{c['label']} is your biggest cost",
                          "detail": f"{c['label']} came to {c['amount_text']} over these months, {c['share']}% of what you spent."})
        jumps = [c for c in cats if c.get("last_vs_avg_pct") is not None and c["last_vs_avg_pct"] >= 25]
        if jumps:
            j = jumps[0]
            out_f.append({"title": f"{j['label']} went up", "detail": f"{j['label']} was {j['last_text']} last month, {j['last_vs_avg_pct']}% above its usual."})
            out_r.append({"title": f"Check what changed in {j['label'].lower()}", "detail": "Look at last month's bills under this head and ask the vendor about the increase.",
                          "why": f"It rose {j['last_vs_avg_pct']}% on its usual."})
        rep = figures.get("repeats") or []
        if rep:
            out_r.append({"title": "Review the payments that repeat", "detail": f"{len(rep)} payments repeat every month. Cancel any you no longer use.",
                          "why": f"The biggest is {rep[0]['vendor']} at {rep[0]['amount_text']} a month."})
        if len(months) >= 2:
            out_f.append({"title": "Month by month", "detail": f"You spent {months[-1]['out_text']} in the latest month, against {months[-2]['out_text']} the month before."})
        out_r.append({"title": "Keep adding every bill", "detail": "The more bills are in Expenses, the clearer this picture gets.", "why": ""})
        return {"headline": f"Where the money went over the last {len(months)} months", "findings": out_f, "recommendations": out_r}

    return await complete(RULES + """
5. You are looking at the owner's expense totals. Use ONLY the figures given below. Quote rupee amounts and percentages
   exactly as they are written there. Never work out a new number, never estimate a saving, never invent a figure.
6. Be practical: what stands out, and what the owner could check or change this month. No accounting jargon.""",
                          f"""Business: {business}

FIGURES (the only numbers you may use)
{facts}

Write a short money review for the owner.
JSON only: {{"headline": "one line", "findings": [{{"title": "...", "detail": "one or two sentences"}}],
"recommendations": [{{"title": "short action", "detail": "what to do, one or two sentences", "why": "the figure behind it"}}]}}
Up to 5 findings and 5 recommendations.""", mock, 2000)
