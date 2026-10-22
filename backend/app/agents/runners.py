"""
What each built-in agent does when it runs. Attached to the catalog at import (the scheduler and the agents API import this).
Every runner gets (owner, run) and returns a one-line note for the activity log, or raises Skip.
"""
import statistics
import re
from datetime import datetime, timedelta, timezone

from .. import kits, plans
from ..ai import agent_tasks
from ..config import settings
from ..db import IST, db, now
from ..messaging import whatsapp_template
from ..services import notify, run_ai
from ..text import greeting_name
from . import catalog
from .approvals import create_approval, expire
from .catalog import Skip
from .jobs import claim, record

MAX_REMINDERS = 4


# ───────────────────────── helpers ─────────────────────────
def inr(paise: int | float | None) -> str:
    """12345678 paise → '₹1,23,456.78'; whole rupees drop the paise."""
    paise = int(round(paise or 0))
    rupees, rest = divmod(abs(paise), 100)
    s = str(rupees)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        s = ",".join(([head] if head else []) + groups) + "," + tail
    return ("-" if paise < 0 else "") + "₹" + s + (f".{rest:02d}" if rest else "")


def pretty_date(day: str) -> str:
    try:
        d = datetime.strptime(day, "%Y-%m-%d")
        return f"{d.day} {d.strftime('%b %Y')}"
    except (TypeError, ValueError):
        return day or ""


def ist_midnight(at: datetime) -> datetime:
    return at.astimezone(IST).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def ist_day(at: datetime) -> str:
    return at.astimezone(IST).strftime("%Y-%m-%d")


def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


async def _business(ws: str) -> dict:
    return await db().businesses.find_one({"owner_id": ws}) or {}


async def _profile(ws: str) -> dict:
    b = await _business(ws)
    if not b.get("name") or not b.get("industry"):
        raise Skip("The Business Brain isn't filled in yet, so there was nothing to write from.")
    return b


async def _lead_for(owner: dict, job: dict) -> dict:
    lead = await db().leads.find_one({"_id": (job.get("payload") or {}).get("lead_id"), "owner_id": owner["_id"]})
    if not lead:
        raise Skip("The lead was deleted, so nothing was sent.")
    return lead


# ───────────────────────── Follow-up agent ─────────────────────────
async def followup(owner: dict, run: dict):
    ws, job = owner["_id"], run["job"]
    lead = await _lead_for(owner, job)
    who = lead.get("name") or "a lead"
    if lead.get("status") in ("won", "lost"):
        raise Skip(f"{who} is already marked {lead['status']}, so no follow-up was needed.")
    if not lead.get("phone"):
        raise Skip(f"{who} left no phone number to follow up on.")
    profile = await _profile(ws)
    p = job.get("payload") or {}
    n, of = int(p.get("n", 1)), int(p.get("of", len(settings.followup_days) or 1))
    text = await run_ai(owner, "followup", agent_tasks.followup(profile, lead, n, of), f"Follow-up {n} to {who}",
                        save_output=False, check=agent_tasks.followup_text)
    # the AI takes a few seconds: the lead may have been won, lost or deleted meanwhile — then nothing is drafted or sent
    now_lead = await db().leads.find_one({"_id": lead["_id"], "owner_id": ws}, {"status": 1})
    if not now_lead:
        raise Skip(f"{who} was deleted while the follow-up was being written, so nothing was sent.")
    if now_lead.get("status") in ("won", "lost"):
        raise Skip(f"{who} was marked {now_lead['status']} while the follow-up was being written, so nothing was sent.")
    await expire({"ws": ws, "kind": "followup", "lead_id": lead["_id"]}, f"Replaced by follow-up {n}")
    a = await create_approval(owner, ws, "followup", "followup", f"Follow-up {n} of {of} to {who}", lead.get("name", ""), lead["phone"],
                              text, allow_act=not has_link(text), lead_id=lead["_id"], n=n)
    return (f"Sent follow-up {n} to {who} on its own." if a["status"] == "sent"
            else f"Follow-up {n} to {who} is ready in your Send list."), a["_id"]


# ───────────────────────── Review request agent ─────────────────────────
def has_link(text: str) -> bool:
    """AI text that contains a link always waits for the owner: a stranger's inquiry must never steer what the hub sends."""
    return bool(re.search(r"https?://|www\.|\b[a-z0-9-]+\.(?:com|in|net|org|io|co|me|ly|link)\b", text or "", flags=re.I))


def review_text(name: str, business: str, link: str) -> str:
    hello = f"Hello {name}," if name and name != "there" else "Hello,"
    return (f"{hello} thank you for choosing {business}! If you were happy with our work, could you leave us a quick review? "
            f"It takes a minute and really helps us: {link} Thank you!")


async def review_request(owner: dict, run: dict):
    ws = owner["_id"]
    lead = await _lead_for(owner, run["job"])
    who = lead.get("name") or "your customer"
    if lead.get("status") != "won":
        raise Skip(f"{who} is no longer marked won, so no review was asked for.")
    if not lead.get("phone"):
        raise Skip(f"{who} has no phone number to send a review request to.")
    b = await _business(ws)
    link = (b.get("review_link") or "").strip()
    if not link:
        if await claim(f"review-link-notice:{ws}", run["at"]):
            await notify(ws, "Add your review link on the Automations page, so won customers can be asked for a review.")
        raise Skip(f"{who} wasn't asked for a review: add your review link on the Automations page first.")
    name, business = greeting_name(lead.get("name", "")) or "there", b.get("name") or "us"
    a = await create_approval(owner, ws, "review_request", "review", f"Review request to {who}", lead.get("name", ""), lead["phone"],
                              review_text(name, business, link), params=[name, business, link], lead_id=lead["_id"])
    return (f"Asked {who} for a review on its own." if a["status"] == "sent"
            else f"Review request to {who} is ready in your Send list."), a["_id"]


# ───────────────────────── Payment reminder agent ─────────────────────────
def reminder_text(name: str, business: str, amount: str, due: str, how: str, invoice: str = "") -> str:
    hello = f"Hello {name}," if name and name != "there" else "Hello,"
    inv = f" for invoice {invoice}" if invoice else ""
    pay = f" {how.rstrip('.')}." if how else ""
    return (f"{hello} a gentle reminder from {business} that {amount}{inv} was due on {due}.{pay} "
            f"If you've already paid, please ignore this message. Thank you!")


async def payment_reminder(owner: dict, run: dict):
    """Daily: each unpaid amount past its due date gets a reminder every `reminder_gap_days`, at most 4 times.
    The count and date are written when the reminder is created, so it is never created twice."""
    ws, at = owner["_id"], run["at"]
    today = ist_day(at)
    cutoff = ist_midnight(at) - timedelta(days=max(settings.reminder_gap_days - 1, 0))
    b = await _business(ws)
    business, how = b.get("name") or "us", (b.get("payment_note") or "").strip()
    made, no_phone = 0, 0
    from .. import followups
    auto = await followups.whatsapp_auto(owner)       # approved once, Growth Mentorship and up: goes from the owner's own number
    excluded = set((await followups.get_state(ws)).get("exclude") or [])
    sent = 0
    query = {"ws": ws, "status": "due", "due_date": {"$lte": today}, "reminders_sent": {"$lt": MAX_REMINDERS},
             "$or": [{"last_reminded_at": None}, {"last_reminded_at": {"$lt": cutoff}}]}
    async for due in db().dues.find(query).sort("due_date", 1).limit(500):
        if not due.get("phone"):
            no_phone += 1
            continue
        if not await claim(f"reminder:{due['_id']}:{today}", at):
            continue
        await db().dues.update_one({"_id": due["_id"]}, {"$inc": {"reminders_sent": 1}, "$set": {"last_reminded_at": at}})
        nth = due.get("reminders_sent", 0) + 1
        name = greeting_name(due.get("customer", "")) or "there"
        amount, day = inr(due.get("amount", 0)), pretty_date(due.get("due_date", ""))
        await expire({"ws": ws, "kind": "reminder", "due_id": due["_id"]}, f"Replaced by reminder {nth}")
        # on its own only for customers the owner hasn't excluded, and not after they replied ("don't chase" / "they replied")
        auto_this = auto and followups.customer_key(due) not in excluded and not due.get("no_auto") and not due.get("auto_stopped")
        a = await create_approval(owner, ws, "payment_reminder", "reminder", f"Reminder {nth} of {MAX_REMINDERS} to {due.get('customer', '')} — {amount}",
                                  due.get("customer", ""), due["phone"], reminder_text(name, business, amount, day, how, due.get("invoice_no", "")),
                                  params=[name, business, amount, day, how or "Reply to this message and we'll share the details"],
                                  due_id=due["_id"], n=nth, auto_send=auto_this)
        made += 1
        sent += a.get("status") == "sent"
    if not made:
        raise Skip("No payments to remind about today." + (f" {plural(no_phone, 'amount')} owed {'has' if no_phone == 1 else 'have'} no phone number."
                                                           if no_phone else ""))
    waiting = made - sent
    parts = ([f"{plural(sent, 'payment reminder')} sent from your WhatsApp"] if sent else []) + \
            ([f"{plural(waiting, 'payment reminder')} ready in your Send list"] if waiting else [])
    return "; ".join(parts) + "."


# ───────────────────────── Morning digest ─────────────────────────
async def digest_line(owner: dict, at: datetime) -> str:
    ws = owner["_id"]
    today_start = ist_midnight(at)
    today = ist_day(at)
    new_yday = await db().leads.count_documents({"owner_id": ws, "created_at": {"$gte": today_start - timedelta(days=1), "$lt": today_start}})
    waiting = await db().leads.count_documents({"owner_id": ws, "status": "new"})
    approvals = await db().approvals.count_documents({"ws": ws, "status": "pending"})
    overdue = 0
    async for d in db().dues.find({"ws": ws, "status": "due", "due_date": {"$lt": today}}, {"amount": 1}):
        overdue += int(d.get("amount") or 0)
    parts = [f"{plural(new_yday, 'new lead')} yesterday", f"{waiting} waiting for a first reply", f"{approvals} in your Send list",
             f"{inr(overdue)} overdue"]
    if plans.has(owner, "tasks"):
        tasks_today = await db().tasks.count_documents({"ws": ws, "status": "open", "due": today})
        parts.append(f"{plural(tasks_today, 'task')} due today")
    if plans.has(owner, "compliance_calendar"):
        week = (ist_midnight(at) + timedelta(days=7)).astimezone(IST).strftime("%Y-%m-%d")
        n = await db().compliance_items.count_documents({"owner_id": ws, "status": {"$ne": "done"}, "due_date": {"$gte": today, "$lte": week}})
        if n:
            parts.append(f"{plural(n, 'compliance item')} due this week")
    if plans.has(owner, "cash_flow"):
        from ..routers.cashflow import shortfall_line
        line = await shortfall_line(ws)
        if line:
            parts.append(line)
    if plans.has(owner, "auto_reminders") or plans.has(owner, "reminder_agent"):
        from ..followups import week_line
        line = await week_line(ws)
        if line:
            parts.append(line)
    return " · ".join(parts)


async def digest(owner: dict, run: dict):
    ws = owner["_id"]
    line = await digest_line(owner, run["at"])
    b = await _business(ws)
    sent = await whatsapp_template(owner["phone"], settings.aisensy_digest_campaign, owner.get("name", ""),
                                   [b.get("name") or "your business", line], "digest")
    await notify(ws, f"Good morning. {line}.")
    return f"{line}." + ("" if sent["sent"] else " (Shown in the hub; WhatsApp isn't set up.)")


# ───────────────────────── Monthly review agent ─────────────────────────
def _num(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _when(x) -> str:
    return x.astimezone(IST).strftime("%Y-%m-%d") if isinstance(x, datetime) else str(x or "")[:10]


async def month_data(owner: dict, start: datetime, end: datetime) -> dict:
    """Everything the review agent reads for [start, end): leads, website, goals, wheel, tasks and money. Other modules'
    collections may be empty or missing, so each part is read on its own and left out if unreadable."""
    ws = owner["_id"]
    start_day, end_day = ist_day(start), ist_day(end - timedelta(seconds=1))
    numbers: dict = {}
    data: dict = {"from": start_day, "to": end_day, "numbers": numbers}

    leads = [l async for l in db().leads.find({"owner_id": ws, "created_at": {"$gte": start, "$lt": end}},
                                              {"status": 1, "created_at": 1, "first_action_at": 1})]
    numbers.update(leads=len(leads), won=sum(l.get("status") == "won" for l in leads), lost=sum(l.get("status") == "lost" for l in leads),
                   waiting=sum(l.get("status") == "new" for l in leads))
    hours = [(l["first_action_at"] - l["created_at"]).total_seconds() / 3600 for l in leads
             if isinstance(l.get("first_action_at"), datetime) and isinstance(l.get("created_at"), datetime)]
    if hours:
        numbers["median first reply (hours)"] = round(statistics.median(hours), 1)

    try:
        views, seen = 0, False
        async for v in db().site_views.find({"ws": ws, "day": {"$gte": start_day, "$lte": end_day}}, {"n": 1}):
            views += int(v.get("n") or 0)
            seen = True
        if seen:
            numbers["website views"] = views
    except Exception:
        pass

    try:
        goals = []
        async for g in db().goals.find({"ws": ws}).limit(20):
            if str(g.get("end") or "9999")[:10] < start_day or str(g.get("start") or "0000")[:10] > end_day:
                continue
            checks = [c for c in (g.get("checkins") or []) if isinstance(c, dict) and _when(c.get("at")) <= end_day]
            checks.sort(key=lambda c: _when(c.get("at")))
            latest, target = (_num(checks[-1].get("value")) if checks else None), _num(g.get("target"))
            goals.append({"title": g.get("title", ""), "metric": g.get("metric", ""), "target": g.get("target"), "unit": g.get("unit", ""),
                          "latest": latest, "progress_percent": round(latest / target * 100) if latest is not None and target else None,
                          "ends": str(g.get("end") or "")[:10]})
        if goals:
            data["goals"] = goals
    except Exception:
        pass

    try:
        sc = await db().scores.find_one({"ws": ws, "at": {"$lt": end}}, sort=[("at", -1)])
        if sc:
            data["business_score"] = {"score": sc.get("score"), "taken": _when(sc.get("at")), "fixes": [f.get("about") for f in sc.get("fixes") or []]}
    except Exception:
        pass

    if plans.has(owner, "tasks"):
        try:
            numbers["tasks open"] = await db().tasks.count_documents({"ws": ws, "status": "open"})
            numbers["tasks done"] = await db().tasks.count_documents({"ws": ws, "status": "done", "done_at": {"$gte": start, "$lt": end}})
            numbers["tasks overdue"] = await db().tasks.count_documents({"ws": ws, "status": "open", "due": {"$ne": None, "$lt": ist_day(end)}})
        except Exception:
            pass

    if plans.has(owner, "money_owed"):
        overdue = collected = 0
        async for d in db().dues.find({"ws": ws, "status": "due", "due_date": {"$lt": ist_day(end)}}, {"amount": 1}):
            overdue += int(d.get("amount") or 0)
        async for d in db().dues.find({"ws": ws, "status": "paid", "paid_at": {"$gte": start, "$lt": end}}, {"amount": 1}):
            collected += int(d.get("amount") or 0)
        if overdue:
            numbers["overdue_amount"] = inr(overdue)
        numbers["collected"] = inr(collected)
    return data


async def write_review(owner: dict, start: datetime, end: datetime, period: str, actor: str, profile: dict) -> dict:
    data = await month_data(owner, start, end)
    doc = await kits.generate(owner, owner["_id"], actor, "review", {"period": period, "data": data}, profile,
                              extra={"by_agent": "Monthly review agent"})
    await notify(owner["_id"], f"Your business review for {period} is ready in Monthly reviews.")
    return doc


async def monthly_review(owner: dict, run: dict):
    """On the 1st: the review of the month that just ended."""
    profile = await _profile(owner["_id"])
    local = run["at"].astimezone(IST)
    end = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    start = (end - timedelta(days=1)).replace(day=1)
    period = start.strftime("%B %Y")
    doc = await write_review(owner, start.astimezone(timezone.utc), end.astimezone(timezone.utc), period, "agent", profile)
    return f"Wrote the business review for {period}.", doc["_id"]


async def review_now(owner: dict, actor: str, profile: dict, at: datetime | None = None) -> dict:
    """The owner asked for this month's review now: the month so far. Errors (no runs left) go back to the owner."""
    at = at or now()
    local = at.astimezone(IST)
    start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    period = f"{local.strftime('%B %Y')} (so far)"
    doc = await write_review(owner, start.astimezone(timezone.utc), at, period, actor, profile)
    await record(owner["_id"], "monthly_review", "done", f"Wrote the business review for {period}, on request.", at, doc["_id"])
    return doc


for _key, _fn in {"followup": followup, "review_request": review_request, "payment_reminder": payment_reminder,
                  "digest": digest, "monthly_review": monthly_review}.items():
    catalog.attach(_key, _fn)

from . import desk, growth, rhythm  # noqa: E402,F401  (attach their runners)
