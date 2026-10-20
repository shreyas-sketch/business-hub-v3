"""
The Smart Dashboard's widget registry (v2.1, 3.2). One entry per tile:

    Widget(key, title, features, size, compute(ws, owner, t) -> dict, empty_state, link)

`features`: the tile shows when the owner has ANY of them (an empty tuple = every owner). Locked tiles show as a teaser when
the admin lets the first feature show as a teaser. Each compute returns {"empty": bool, ...numbers}; when empty, the tile
shows its empty state with one action. All numbers are counted from the hub's own records — nothing is estimated here
except where a tile says so (the cash forecast and the month-end projection).

Adding a widget = one Widget(...) entry and one compute function.
"""
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Awaitable, Callable

from . import cashflow, knowledge, sales
from .db import IST, db, now

STAGES = [("identification", "Identification"), ("logic", "Logic"), ("pain", "Pain"), ("vision", "Vision"), ("close", "Close")]
SOURCES = {"website": "Website", "guide": "Lead magnet", "lead_magnet": "Lead magnet", "gateway": "Payment", "email": "Email", "whatsapp": "WhatsApp", "card": "Card / QR", "qr": "Card / QR",
           "import": "Imported", "manual": "Added by you"}


@dataclass
class Widget:
    key: str
    title: str
    features: tuple
    size: str                      # s | m | l   (grid columns on a desktop: 1, 1, 2)
    compute: Callable[[str, dict, date], Awaitable[dict]]
    empty: dict                    # {"text", "action", "path"}
    link: str
    order: int = 0
    what: str = ""
    roles: tuple = field(default=("owner", "manager"))


def _since(t: date, days: int):
    return sales.ist_midnight(t - timedelta(days=days - 1))


# ───────────────────────── computes ─────────────────────────
async def today_tile(ws: str, owner: dict, t: date) -> dict:
    from . import plans
    items = []
    if plans.has(owner, "approvals"):
        items.append({"key": "send_list", "label": "Messages in your Send list", "n": await db().approvals.count_documents({"ws": ws, "status": "pending"}),
                      "path": "/approvals"})
    items.append({"key": "inquiries", "label": "Inquiries without a reply", "path": "/leads",
                  "n": await db().leads.count_documents({"owner_id": ws, "status": "new", "first_action_at": {"$exists": False}})})
    if plans.has(owner, "money_owed"):
        items.append({"key": "due_today", "label": "Payments due today", "path": "/money",
                      "n": await db().dues.count_documents({"ws": ws, "status": "due", "due_date": t.isoformat()})})
    if plans.has(owner, "compliance_calendar"):
        items.append({"key": "compliance", "label": "Compliance due in 7 days", "path": "/compliance",
                      "n": await db().compliance_items.count_documents({"owner_id": ws, "status": {"$ne": "done"},
                                                                        "due_date": {"$lte": (t + timedelta(days=7)).isoformat()}})})
    if plans.has(owner, "tasks"):
        items.append({"key": "tasks", "label": "Tasks due", "path": "/tasks",
                      "n": await db().tasks.count_documents({"ws": ws, "status": "open", "due": {"$ne": None, "$lte": t.isoformat()}})})
    return {"empty": not any(i["n"] for i in items), "items": items, "total": sum(i["n"] for i in items)}


async def leads_tile(ws: str, owner: dict, t: date) -> dict:
    since30, since7, since1 = _since(t, 30), _since(t, 7), _since(t, 1)
    n30 = n7 = n1 = 0
    by_source: dict[str, int] = {}
    replies = []
    async for x in db().leads.find({"owner_id": ws, "created_at": {"$gte": since30}}, {"created_at": 1, "source": 1, "first_action_at": 1}).limit(20000):
        n30 += 1
        n7 += x["created_at"] >= since7
        n1 += x["created_at"] >= since1
        label = SOURCES.get(x.get("source") or "website", (x.get("source") or "Other").title())
        by_source[label] = by_source.get(label, 0) + 1
        if x.get("first_action_at"):
            replies.append(max((x["first_action_at"] - x["created_at"]).total_seconds(), 0) / 3600)
    total = await db().leads.count_documents({"owner_id": ws}) if not n30 else n30
    return {"empty": not total, "today": n1, "week": n7, "month": n30,
            "by_source": [{"source": k, "n": v} for k, v in sorted(by_source.items(), key=lambda kv: -kv[1])],
            "median_reply_hours": round(statistics.median(replies), 1) if replies else None}


async def pipeline_tile(ws: str, owner: dict, t: date) -> dict:
    stages = {k: {"key": k, "label": label, "n": 0, "value": 0.0} for k, label in STAGES}
    async for x in db().leads.find({"owner_id": ws, "status": {"$in": ["new", "contacted"]}}, {"stage": 1, "value": 1}).limit(20000):
        s = stages.get(x.get("stage") or "identification", stages["identification"])
        s["n"] += 1
        s["value"] += float(x.get("value") or 0)
    rows = list(stages.values())
    open_n = sum(s["n"] for s in rows)
    stuck = max(rows, key=lambda s: s["n"]) if open_n else None
    return {"empty": not open_n, "stages": rows, "open": open_n, "open_value": round(sum(s["value"] for s in rows)),
            "stuck": {"key": stuck["key"], "label": stuck["label"], "n": stuck["n"]} if stuck else None}


async def target_tile(ws: str, owner: dict, t: date) -> dict:
    s = await sales.target_status(ws, t)
    if not s:
        return {"empty": True}
    keep = ("target", "month_to_date", "month_gone_pct", "projected", "gap", "required_daily", "pct_of_target", "status", "status_label", "line", "mode", "days_left")
    return {"empty": False, **{k: s[k] for k in keep}, "estimated": ["projected"]}


async def money_tile(ws: str, owner: dict, t: date) -> dict:
    today_s, week_end = t.isoformat(), (t + timedelta(days=6 - t.weekday())).isoformat()
    month_start = sales.ist_midnight(t.replace(day=1))
    overdue = overdue_n = due_week = collected = 0
    oldest = None
    any_due = False
    # open amounts and this month's payments only (not every due ever)
    async for d in db().dues.find({"ws": ws, "$or": [{"status": "due"}, {"paid_at": {"$gte": month_start}}]},
                                  {"amount": 1, "status": 1, "due_date": 1, "paid_at": 1}).limit(20000):
        any_due = True
        amt = int(d.get("amount") or 0) / 100
        if d.get("status") == "due":
            day = d.get("due_date") or "9999-12-31"
            if day < today_s:
                overdue += amt
                overdue_n += 1
                oldest = day if oldest is None or day < oldest else oldest
            elif day <= week_end:
                due_week += amt
        elif d.get("paid_at") and d["paid_at"] >= month_start:
            collected += amt
    any_due = any_due or bool(await db().dues.count_documents({"ws": ws}, limit=1))
    return {"empty": not any_due, "overdue": round(overdue), "overdue_count": overdue_n, "due_this_week": round(due_week), "collected_month": round(collected),
            "oldest_overdue_days": (t - date.fromisoformat(oldest)).days if oldest else None}


async def cash_tile(ws: str, owner: dict, t: date) -> dict:
    f = await cashflow.forecast(ws, t)
    if not f.get("ready"):
        return {"empty": True}
    low = f.get("lowest") or {}
    short = f.get("shortfall")
    return {"empty": False, "lowest": low.get("balance"), "lowest_date": low.get("date"), "shortfall_date": (short or {}).get("date"),
            "short_by": (short or {}).get("short_by"), "label": f.get("label"), "estimated": ["lowest", "shortfall_date"]}


async def invoices_tile(ws: str, owner: dict, t: date) -> dict:
    since = sales.ist_midnight(t.replace(day=1))
    invoiced = paid = unpaid = 0.0
    n = 0
    async for q in db().quotes.find({"ws": ws, "kind": "invoice", "created_at": {"$gte": since}}, {"totals": 1, "status": 1}).limit(5000):
        total = float((q.get("totals") or {}).get("total") or 0)
        n += 1
        invoiced += total
        if q.get("status") == "paid":
            paid += total
        else:
            unpaid += total
    open_q = 0.0
    n_q = 0
    async for q in db().quotes.find({"ws": ws, "kind": "quote", "status": {"$in": ["draft", "sent"]}}, {"totals": 1}).limit(5000):
        open_q += float((q.get("totals") or {}).get("total") or 0)
        n_q += 1
    any_ever = n or n_q or await db().quotes.count_documents({"ws": ws}, limit=1)
    return {"empty": not any_ever, "invoiced_month": round(invoiced), "invoices_month": n, "paid": round(paid), "unpaid": round(unpaid),
            "open_quotes": n_q, "open_quotes_value": round(open_q)}


async def social_tile(ws: str, owner: dict, t: date) -> dict:
    from .routers import social
    s = await social.summary(ws, t)
    return {"empty": not (s["created_month"] or s["month"] or s["next"]), **s}


async def compliance_tile(ws: str, owner: dict, t: date) -> dict:
    items = [i async for i in db().compliance_items.find({"owner_id": ws, "status": {"$ne": "done"}, "due_date": {"$ne": None}}).sort("due_date", 1).limit(200)]
    if not items and not await db().compliance_items.count_documents({"owner_id": ws}, limit=1):
        return {"empty": True}
    today_s, soon = t.isoformat(), (t + timedelta(days=30)).isoformat()
    nxt = [{"title": i["title"], "due_date": i["due_date"], "overdue": i["due_date"] < today_s} for i in items if i["due_date"] >= today_s][:3]
    return {"empty": False, "next": nxt, "overdue": sum(1 for i in items if i["due_date"] < today_s),
            "expiring_docs": sum(1 for i in items if i.get("doc_file_id") and today_s <= i["due_date"] <= soon)}


async def website_tile(ws: str, owner: dict, t: date) -> dict:
    site = await db().sites.find_one({"owner_id": ws}, {"status": 1, "slug": 1})
    if not site:
        return {"empty": True}
    v7 = v30 = 0
    first7 = (t - timedelta(days=6)).isoformat()
    async for v in db().site_views.find({"ws": ws, "day": {"$gte": (t - timedelta(days=29)).isoformat()}}, {"day": 1, "n": 1}):
        v30 += int(v.get("n") or 0)
        v7 += int(v.get("n") or 0) if v["day"] >= first7 else 0
    since = _since(t, 30)
    leads30 = await db().leads.count_documents({"owner_id": ws, "created_at": {"$gte": since}, "source": {"$in": ["website", "guide", "lead_magnet"]}})
    guide30 = await db().leads.count_documents({"owner_id": ws, "created_at": {"$gte": since}, "source": {"$in": ["guide", "lead_magnet"]}})
    return {"empty": False, "live": site.get("status") == "live", "views_7": v7, "views_30": v30, "magnet_downloads_30": guide30,
            "conversion_pct": round(leads30 / v30 * 100, 1) if v30 else None}


async def sops_tile(ws: str, owner: dict, t: date) -> dict:
    from .routers import sopset
    written = await db().kits.count_documents({"ws": ws, "kind": "sop"})
    without = await sopset.without_sop(ws)
    has_map = bool(await db().sop_maps.count_documents({"_id": ws}, limit=1))
    return {"empty": not written and not has_map, "written": written, "without_sop": without, "has_map": has_map}


async def knowledge_tile(ws: str, owner: dict, t: date) -> dict:
    b = await db().businesses.find_one({"owner_id": ws}) or {}
    c = await knowledge.completeness_for(ws, b)
    nxt = next((x for x in c["sections"] if x["key"] == c.get("next")), None)
    return {"empty": False, "percent": c["percent"], "next": nxt["label"] if nxt else None, "next_key": c.get("next"), "hint": c.get("hint")}


async def growth_tile(ws: str, owner: dict, t: date) -> dict:
    from .agents.growth import results_data
    week = sales.ist_midnight(t - timedelta(days=t.weekday()))
    month = sales.ist_midnight(t.replace(day=1))
    chats = await db().wa_messages.count_documents({"ws": ws, "dir": "out", "by": "ai", "at": {"$gte": week}})
    calls = await db().calls.count_documents({"ws": ws, "started_at": {"$gte": week}})
    r = await results_data(owner, month, now())
    hours = r.get("hours_saved") or 0
    return {"empty": not (chats or calls or hours), "ai_replies_week": chats, "calls_week": calls, "hours_saved_month": hours,
            "estimated": ["hours_saved_month"]}


REGISTRY: dict[str, Widget] = {w.key: w for w in [
    Widget("today", "Today", (), "l", today_tile, {"text": "Nothing needs you right now.", "action": "See your leads", "path": "/leads"}, "/leads", 0,
           "What needs you today"),
    Widget("leads", "Leads", ("leads",), "m", leads_tile, {"text": "No inquiries yet.", "action": "Share your website", "path": "/website"}, "/leads", 10,
           "New inquiries and where they came from"),
    Widget("pipeline", "Pipeline", ("pipeline",), "m", pipeline_tile, {"text": "No open deals.", "action": "Open your pipeline", "path": "/pipeline"},
           "/pipeline", 20, "Open deals by stage on the Football Field"),
    Widget("target", "Target", ("magic_number",), "m", target_tile, {"text": "No monthly target yet.", "action": "Set your target", "path": "/magic"},
           "/magic", 30, "Month to date against your target"),
    Widget("money", "Money", ("money_owed",), "m", money_tile, {"text": "No payments tracked yet.", "action": "Add your first due", "path": "/money"},
           "/money", 40, "Overdue, due this week and collected"),
    Widget("cash_flow", "Cash flow", ("cash_flow",), "m", cash_tile, {"text": "Add your bank balance to see the next 30 days.", "action": "Add balance",
                                                                       "path": "/cash-flow"}, "/cash-flow", 50, "Lowest balance in the next 30 days"),
    Widget("invoices", "Invoices", ("quotations",), "m", invoices_tile, {"text": "No quotations or invoices yet.", "action": "Make a quotation",
                                                                          "path": "/quotes"}, "/quotes", 60, "Invoiced this month and open quotations"),
    Widget("social", "Social", ("tool_content", "content_calendar"), "m", social_tile, {"text": "No posts planned yet.", "action": "Plan this month",
                                                                                         "path": "/calendar"}, "/calendar", 70, "What you've posted"),
    Widget("compliance", "Compliance", ("compliance_calendar",), "m", compliance_tile, {"text": "Your filings and renewals aren't set up yet.",
                                                                                         "action": "Set up compliance", "path": "/compliance"},
           "/compliance", 80, "Next filings, renewals and expiring documents"),
    Widget("website", "Website", ("website",), "m", website_tile, {"text": "Your website isn't made yet.", "action": "Make your website", "path": "/website"},
           "/website", 90, "Views, lead magnet downloads and conversion"),
    Widget("sops", "SOPs", ("sop_library",), "s", sops_tile, {"text": "No SOPs yet.", "action": "Map your processes", "path": "/sops"}, "/sops", 100,
           "SOPs written and processes still without one"),
    Widget("knowledge", "Knowledge Hub", ("business_brain",), "s", knowledge_tile, {"text": "", "action": "Fill it in", "path": "/knowledge"},
           "/knowledge", 110, "How complete your Knowledge Hub is"),
    Widget("growth", "AI team", ("whatsapp_ai", "voice_agent", "results_report"), "m", growth_tile,
           {"text": "Your AI team hasn't started yet.", "action": "Connect WhatsApp", "path": "/connections"}, "/results", 120,
           "Chats handled by AI, calls made, hours saved"),
]}
