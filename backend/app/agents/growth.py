"""
Growth Mentorship automations: the instant reply and the AI Telecaller on the owner's own accounts, re-calling old leads,
the Monday CEO report and the monthly AI results. Also the numbers behind Control Room and AI results.
"""
from datetime import datetime, timedelta, timezone

from .. import connections, integrations, plans
from ..config import settings
from ..db import IST, db, now
from ..messaging import whatsapp_template
from ..services import new_id, notify
from ..text import greeting_name
from . import catalog
from .catalog import Skip
from .jobs import claim, is_on, record, release, schedule
from .runners import inr, plural

INSTANT_PER_DAY = 200   # instant replies per owner per day: protects the owner's WhatsApp number

# Minutes a person would have spent, for "hours saved"
MINUTES = {"posts": 30, "reply": 5, "answer": 10, "polish": 10, "brand": 30, "site": 120, "calendar": 120, "week": 40, "report": 30,
           "standup": 10, "kit:sop": 45, "kit:jd": 45, "kit:decision": 30, "kit:role": 40, "kit:culture": 60, "kit:competence": 45,
           "kit:review": 60, "lead_magnet": 120, "offer_ladder": 60, "quote": 20, "followup": 5, "wa_reply": 3, "call": 8, "message": 4}


# ───────────────────────── instant reply on the owner's number ─────────────────────────
async def instant_reply(owner: dict, business: dict, lead: dict) -> dict:
    """The first message to a new website lead from the owner's own WhatsApp number (an approved template: the customer
    asked through the website, so the chat starts on WhatsApp). Once a day per number; a daily ceiling per owner."""
    from .approvals import owner_whatsapp, record_out
    ws = owner["_id"]
    conn = await owner_whatsapp(ws, owner)
    if not conn:
        return {"sent": False, "via": "not-connected"}
    if not conn.get("tpl_instant"):
        return {"sent": False, "via": "no-template"}
    to = integrations.digits(lead["phone"])
    held = await first_reply_held(ws, lead)
    if held:
        return {"sent": False, "via": held}
    key = first_reply_key(ws, to)
    if not await claim(key):           # a double submit a moment ago is already being answered
        return {"sent": False, "via": "skipped-repeat"}
    name = greeting_name(lead.get("name", "")) or "there"
    res = await integrations.wa_template(ws, conn, to, conn["tpl_instant"], [name, business.get("name") or "us"], lead.get("name", ""), "instant")
    if not res.get("sent"):
        await release(key)
    if res.get("sent"):
        await record_out(ws, to, lead.get("name", ""), f"[Instant reply] Hello {name}, thank you for contacting {business.get('name') or 'us'}.",
                         "ai", res.get("id"))
        await record(ws, "instant_reply", "done", f"Replied to {lead.get('name') or 'a new lead'} on your WhatsApp number.", ref=lead["_id"])
    else:
        await record(ws, "instant_reply", "failed", f"Couldn't reply to {lead.get('name') or 'a new lead'}: {res.get('error')}", ref=lead["_id"])
    return {"sent": bool(res.get("sent")), "via": res.get("via"), "at": now()}


def first_reply_key(ws: str, digits: str) -> str:
    """One first reply per customer number per India day, claimed BEFORE sending: a lead's own record of the reply is saved only
    after the hook returns, so two quick submits from the same number would otherwise both get one."""
    return f"first-reply:{ws}:{digits}:{now().astimezone(IST).strftime('%Y-%m-%d')}"


async def first_reply_held(ws: str, lead: dict) -> str | None:
    """The rules for any first reply from the owner's number (the instant reply and Lead Response's): once a day per customer
    number, and a daily ceiling per owner. Lead Response's sent replies are kept on lead.instant_reply too, so both count."""
    day_ago = now() - timedelta(days=1)
    if await db().leads.find_one({"owner_id": ws, "phone": lead["phone"], "_id": {"$ne": lead["_id"]}, "instant_reply.sent": True,
                                  "created_at": {"$gt": day_ago}}, {"_id": 1}):
        return "skipped-repeat"
    if await db().leads.count_documents({"owner_id": ws, "instant_reply.sent": True, "created_at": {"$gt": day_ago}}) >= INSTANT_PER_DAY:
        return "skipped-cap"
    return None


# ───────────────────────── Lead Response (v2.2): the first reply with a booking ─────────────────────────
LR_REASONS = {"not-connected": "your WhatsApp number isn't connected", "no-template": "the “first reply with a booking” template isn't set",
              "no-phone": "the inquiry has no mobile number", "email-not-verified": "the email's sender couldn't be confirmed",
              "skipped-repeat": "this number already got a first reply today", "skipped-cap": "today's limit of first replies was reached",
              "owner-whatsapp": "WhatsApp didn't accept it"}


async def lead_response_reply(owner: dict, business: dict, lead: dict, st: dict) -> dict:
    """Lead Response's first reply to a new inquiry, from the owner's own number with the booking line — instead of the plain
    instant reply. If it can't go (not connected, no template…), the reason is logged and nothing else is sent: never from the
    hub's own number. Returns fields to save on the lead."""
    from ..routers import leadresponse as lr
    from .approvals import owner_whatsapp, record_out
    ws = owner["_id"]
    conn = await owner_whatsapp(ws, owner)
    why = ("not-connected" if not conn else "no-template" if not conn.get("tpl_lead_response") else "no-phone" if not lead.get("phone")
           else "email-not-verified" if lead.get("source") == "email" and not lead.get("sender_verified") else None)
    why = why or await first_reply_held(ws, lead)
    to = integrations.digits(lead.get("phone", ""))
    key = first_reply_key(ws, to)
    if not why and not await claim(key):      # a double submit a moment ago is already being answered
        why = "skipped-repeat"
    who = lead.get("name") or "a new lead"
    if why:
        res = {"sent": False, "via": why, "at": now()}
        await lr.log(ws, lead, res, st)
        await record(ws, "lead_response", "skipped", f"No first reply to {who}: {LR_REASONS.get(why, why)}.", ref=lead["_id"])
        if why in ("not-connected", "no-template"):
            await lr.tell_owner(ws, f"Lead Response couldn't reply to {who}: {LR_REASONS[why]}. Fix it on the Lead Response page.")
        return {"lead_response": res}
    line = lr.booking_line(st, now())
    name = greeting_name(lead.get("name", "")) or "there"
    biz = business.get("name") or "us"
    sent = await integrations.wa_template(ws, conn, to, conn["tpl_lead_response"], [name, biz, line], lead.get("name", ""), "lead_response")
    if not sent.get("sent"):
        await release(key)
    at = now()
    res = {"sent": bool(sent.get("sent")), "via": sent.get("via"), "at": at, "booking": st.get("booking"), "line": line}
    if sent.get("sent"):
        res["seconds"] = lr.seconds_since(lead.get("created_at"), at)
        await record_out(ws, to, lead.get("name", ""), f"[First reply] Hello {name}, thank you for contacting {biz}. {line}", "ai", sent.get("id"))
        await record(ws, "lead_response", "done", f"First reply to {who} on your WhatsApp number, with a booking line.", ref=lead["_id"])
    else:
        res["error"] = sent.get("error")
        await record(ws, "lead_response", "failed", f"Couldn't reply to {who}: {sent.get('error')}", ref=lead["_id"])
        await lr.tell_owner(ws, f"Lead Response couldn't reply to {who}: {sent.get('error')}")
    await lr.log(ws, lead, res, st)
    out = {"lead_response": res}
    if res["sent"]:   # the first reply went: the instant-reply rules (once a day per number, the daily cap) and AI results count it
        out["instant_reply"] = {"sent": True, "via": res["via"], "at": at, "by": "lead_response"}
    return out


# ───────────────────────── the AI Telecaller ─────────────────────────
def calling_hours(conn: dict) -> tuple[int, int]:
    try:
        a, b = (int(x) for x in str(conn.get("hours") or "10-19").split("-", 1))
        return (max(0, min(23, a)), max(1, min(24, b))) if a < b else (10, 19)
    except ValueError:
        return 10, 19


def in_hours(t: datetime, hours: tuple[int, int]) -> datetime:
    local = t.astimezone(IST)
    start, end = hours
    if local.hour < start:
        local = local.replace(hour=start, minute=0, second=0, microsecond=0)
    elif local.hour >= end:
        local = (local + timedelta(days=1)).replace(hour=start, minute=0, second=0, microsecond=0)
    return local.astimezone(timezone.utc)


async def schedule_call(owner: dict, lead: dict, minutes: int = 3, job_id: str | None = None, kind: str = "new") -> bool:
    conn = await connections.get(owner["_id"], "voice")
    if not connections.ready("voice", conn) or not lead.get("phone"):
        return False
    when = in_hours(now() + timedelta(minutes=minutes), calling_hours(conn))
    job = await schedule(owner["_id"], "telecaller", when, {"lead_id": lead["_id"], "kind": kind}, job_id=job_id or f"call:{lead['_id']}")
    return bool(job)


async def telecaller(owner: dict, run: dict):
    ws = owner["_id"]
    lead = await db().leads.find_one({"_id": (run["job"].get("payload") or {}).get("lead_id"), "owner_id": ws})
    if not lead:
        raise Skip("The lead was deleted, so no call was made.")
    who = lead.get("name") or "a lead"
    if lead.get("status") in ("won", "lost"):
        raise Skip(f"{who} is already marked {lead['status']}, so no call was made.")
    conn = await connections.get(ws, "voice")
    if not connections.ready("voice", conn):
        raise Skip("The AI Telecaller isn't connected yet.")
    day_start = now().astimezone(IST).replace(hour=0, minute=0, second=0, microsecond=0)
    if await db().calls.count_documents({"ws": ws, "started_at": {"$gte": day_start}}) >= settings.calls_per_owner_per_day:
        raise Skip(f"Today's limit of {settings.calls_per_owner_per_day} calls was reached; {who} wasn't called.")
    b = await db().businesses.find_one({"owner_id": ws}) or {}
    res = await integrations.voice_call(ws, conn, lead["phone"], {"lead_name": greeting_name(lead.get("name", "")) or "there",
                                                                   "business_name": b.get("name", ""), "lead_id": lead["_id"],
                                                                   "inquiry": (lead.get("message") or "")[:200]})
    call = {"_id": new_id(), "ws": ws, "lead_id": lead["_id"], "to": lead["phone"], "name": lead.get("name", ""),
            "conversation_id": res.get("conversation_id"), "status": "calling" if res.get("ok") else "failed",
            "error": res.get("error"), "started_at": now(), "kind": (run["job"].get("payload") or {}).get("kind", "new")}
    await db().calls.insert_one(call)
    await db().leads.update_one({"_id": lead["_id"]}, {"$set": {"last_call_at": now()}})
    if not res.get("ok"):
        return f"Couldn't call {who}: {res.get('error')}", call["_id"]
    return f"Calling {who} now.", call["_id"]


async def recall(owner: dict, run: dict):
    ws = owner["_id"]
    old = now() - timedelta(days=30)
    n = 0
    async for lead in db().leads.find({"owner_id": ws, "status": {"$in": ["new", "contacted"]}, "created_at": {"$lt": old},
                                       "$or": [{"last_call_at": None}, {"last_call_at": {"$lt": old}}]}).sort("created_at", -1).limit(60):
        if n >= 20 or not lead.get("phone"):
            continue
        if await schedule_call(owner, lead, minutes=5 + n * 6, job_id=f"recall:{lead['_id']}:{run.get('slot')}", kind="recall"):
            n += 1
    if not n:
        raise Skip("No quiet leads to re-call this week (or the Telecaller isn't connected).")
    return f"Scheduled {plural(n, 'call')} to leads that went quiet."


# ───────────────────────── Control Room numbers ─────────────────────────
async def control_room(owner: dict) -> dict:
    ws = owner["_id"]
    local = now().astimezone(IST)
    month_start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    week_start = (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    today = local.strftime("%Y-%m-%d")
    magic = await db().magic.find_one({"_id": ws}) or {}
    target = float((magic.get("inputs") or {}).get("monthly_target") or 0)
    won_value = won = 0
    async for l in db().leads.find({"owner_id": ws, "status": "won", "updated_at": {"$gte": month_start}}, {"value": 1}):
        won += 1
        won_value += float(l.get("value") or 0)
    stages = {s: {"n": 0, "value": 0.0} for s in ("identification", "logic", "pain", "vision", "close")}
    async for l in db().leads.find({"owner_id": ws, "status": {"$in": ["new", "contacted"]}}, {"stage": 1, "value": 1}).limit(20000):
        s = stages.get(l.get("stage") or "identification", stages["identification"])
        s["n"] += 1
        s["value"] += float(l.get("value") or 0)
    owed = overdue = collected = 0
    async for d in db().dues.find({"ws": ws}, {"amount": 1, "status": 1, "due_date": 1, "paid_at": 1}):
        amt = int(d.get("amount") or 0)
        if d.get("status") == "due":
            owed += amt
            overdue += amt if (d.get("due_date") or "9999") < today else 0
        elif d.get("paid_at") and d["paid_at"] >= month_start:
            collected += amt
    chats_week = await db().wa_threads.count_documents({"ws": ws, "last_in_at": {"$gte": week_start}})
    waiting = await db().wa_threads.count_documents({"ws": ws, "needs_person": True})
    ai_replies = await db().wa_messages.count_documents({"ws": ws, "dir": "out", "by": "ai", "at": {"$gte": week_start}})
    calls_week = await db().calls.count_documents({"ws": ws, "started_at": {"$gte": week_start}})
    calls_done = await db().calls.count_documents({"ws": ws, "started_at": {"$gte": week_start}, "status": "done"})
    new_leads = await db().leads.count_documents({"owner_id": ws, "created_at": {"$gte": week_start}})
    actions = await db().agent_log.count_documents({"ws": ws, "status": "done", "at": {"$gte": week_start}})
    send_list = await db().approvals.count_documents({"ws": ws, "status": "pending"})
    return {"target": target, "won_value": won_value, "won": won, "target_pct": round(won_value / target * 100) if target else None,
            "days_left": (((local.replace(day=28) + timedelta(days=4)).replace(day=1)) - local).days,
            "pipeline": stages, "open_value": sum(s["value"] for s in stages.values()),
            "money": {"owed": owed, "overdue": overdue, "collected": collected},
            "chats": {"this_week": chats_week, "waiting_for_person": waiting, "ai_replies": ai_replies},
            "calls": {"this_week": calls_week, "connected": calls_done}, "new_leads_week": new_leads, "ai_actions_week": actions,
            "send_list": send_list}


def ceo_line(d: dict) -> str:
    tgt = f"₹{d['won_value']:,.0f} of ₹{d['target']:,.0f} target ({d['target_pct']}%)" if d["target"] else f"₹{d['won_value']:,.0f} won this month"
    return (f"{tgt} · {d['new_leads_week']} new leads this week · pipeline ₹{d['open_value']:,.0f} · {d['chats']['this_week']} WhatsApp chats, "
            f"{d['chats']['waiting_for_person']} need you · {d['calls']['this_week']} calls · {inr(d['money']['overdue'])} overdue · "
            f"{d['send_list']} in your Send list")


async def ceo_report(owner: dict, run: dict):
    ws = owner["_id"]
    d = await control_room(owner)
    line = ceo_line(d)
    b = await db().businesses.find_one({"owner_id": ws}, {"name": 1}) or {}
    sent = await whatsapp_template(owner["phone"], settings.aisensy_owner_alert_campaign, owner.get("name", ""),
                                   [b.get("name") or "your business", f"Your week: {line}"[:900]], "ceo_report")
    await notify(ws, f"Monday CEO report: {line}")
    return f"CEO report: {line}" + ("" if sent["sent"] else " (shown in the hub; the WhatsApp alert template isn't set up)")


# ───────────────────────── AI results ─────────────────────────
async def results_data(owner: dict, start: datetime, end: datetime) -> dict:
    ws = owner["_id"]
    rng = {"$gte": start, "$lt": end}
    minutes = 0
    by_kind: dict[str, int] = {}
    async for o in db().outputs.find({"user_id": ws, "created_at": rng}, {"kind": 1, "minutes": 1}).limit(20000):
        kind = o.get("kind", "")
        m = int(o.get("minutes") or MINUTES.get(kind, MINUTES.get(kind.split(":")[0], 15)))
        minutes += m
        by_kind[kind] = by_kind.get(kind, 0) + 1
    sent = await db().approvals.count_documents({"ws": ws, "status": "sent", "decided_at": rng})
    ai_replies = await db().wa_messages.count_documents({"ws": ws, "dir": "out", "by": "ai", "at": rng})
    calls = await db().calls.count_documents({"ws": ws, "started_at": rng})
    minutes += sent * MINUTES["message"] + ai_replies * MINUTES["wa_reply"] + calls * MINUTES["call"]
    chats = await db().wa_threads.count_documents({"ws": ws, "last_in_at": rng})
    followed = await db().leads.count_documents({"owner_id": ws, "$or": [{"last_followup_at": rng}, {"last_call_at": rng}, {"instant_reply.at": rng}]})
    collected = 0
    async for d in db().dues.find({"ws": ws, "status": "paid", "paid_at": rng}, {"amount": 1}):
        collected += int(d.get("amount") or 0)
    touched = won = 0
    async for l in db().leads.find({"owner_id": ws, "status": "won", "updated_at": rng},
                                   {"value": 1, "last_followup_at": 1, "last_call_at": 1, "instant_reply": 1, "phone": 1}):
        thread = await db().wa_threads.find_one({"ws": ws, "phone": integrations.digits(l.get("phone", "")), "last_out_at": {"$exists": True}}, {"_id": 1})
        if l.get("last_followup_at") or l.get("last_call_at") or (l.get("instant_reply") or {}).get("sent") or thread:
            won += 1
            touched += float(l.get("value") or 0)
    actions = await db().agent_log.count_documents({"ws": ws, "status": "done", "at": rng})
    return {"hours_saved": round(minutes / 60, 1), "drafts": sum(by_kind.values()), "messages_sent": sent, "ai_replies": ai_replies,
            "chats": chats, "leads_handled": followed + chats, "calls": calls, "money_collected": collected, "revenue_touched": touched,
            "deals_touched": won, "actions": actions}


def month_bounds(at: datetime, back: int = 0) -> tuple[datetime, datetime, str]:
    local = at.astimezone(IST).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    for _ in range(back):
        local = (local - timedelta(days=1)).replace(day=1)
    end = (local.replace(day=28) + timedelta(days=4)).replace(day=1)
    return local.astimezone(timezone.utc), end.astimezone(timezone.utc), local.strftime("%B %Y")


async def results(owner: dict, run: dict):
    start, end, label = month_bounds(run["at"], back=1)
    d = await results_data(owner, start, end)
    text = (f"Your AI staff in {label}: {d['hours_saved']} hours saved, {d['leads_handled']} leads handled, {d['calls']} calls, "
            f"{inr(d['money_collected'])} collected.")
    await db().results.update_one({"_id": f"{owner['_id']}:{start.astimezone(IST).strftime('%Y-%m')}"},
                                  {"$set": {"ws": owner["_id"], "month": start.astimezone(IST).strftime("%Y-%m"), "label": label, **d, "at": now()}},
                                  upsert=True)
    await notify(owner["_id"], text + " See AI results.")
    return text


for _k, _fn in {"telecaller": telecaller, "recall": recall, "ceo_report": ceo_report, "results": results}.items():
    catalog.attach(_k, _fn)


async def staff_live(ws: str, owner: dict) -> dict[str, bool]:
    """Which of the 6 AI staff are working: switched on, and — for those who work through one of the owner's accounts
    (the Sales Executive needs the WhatsApp number, the Telecaller the voice agent) — that account is connected.
    The setup tracker records the team's progress; it never makes a staff member look live without the connection."""
    from ..staff import STAFF
    conns = {k: connections.ready(k, await connections.get(ws, k)) for k in ("whatsapp", "voice", "employz")}
    out = {}
    for s in STAFF:
        need = s.get("needs")
        out[s["key"]] = plans.has(owner, "ai_staff") and (not need or bool(conns.get(need))) and await is_on(ws, f"staff_{s['key']}")
    return out

