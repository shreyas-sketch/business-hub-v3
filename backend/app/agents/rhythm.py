"""
Membership's weekly rhythm: Monday's plan (7 posts + 3 actions), today's one 5-minute action and the streak, Friday's
check-in and the weekly score; and the monthly content calendar with festivals.

weeks     {_id "<ws>:<YYYY-Www>", ws, week, start, posts[7], actions[3 {text, why, done}], days {date: {text, link, done}},
           checkin {leads, sales, revenue, wins, at}, created_at}
streaks   {_id ws, current, best, last_day}
calendars {_id "<ws>:<YYYY-MM>", ws, month, posts [{date, occasion, hook, caption, hashtags}], focus, created_at}
"""
import math
from datetime import date, timedelta

from .. import festivals, plans
from ..ai import program_tasks
from ..config import settings
from ..db import IST, db, now
from ..messaging import whatsapp_template
from ..services import notify, run_ai
from . import catalog
from .catalog import Skip


def today_ist(at=None) -> date:
    return (at or now()).astimezone(IST).date()


def week_key(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


async def week_facts(owner: dict, at=None) -> dict:
    """What the week's plan is built on: the Magic Number gap, waiting leads, the Send list, money overdue, the biggest gaps."""
    ws = owner["_id"]
    t = today_ist(at)
    facts: dict = {"week_of": monday_of(t).isoformat()}
    magic = await db().magic.find_one({"_id": ws})
    if magic and magic.get("result"):
        r = magic["result"]
        facts["inquiries_week"] = r.get("inquiries_week")
        start = monday_of(t)
        from ..routers.membership import ist_midnight
        got = await db().leads.count_documents({"owner_id": ws, "created_at": {"$gte": ist_midnight(start - timedelta(days=7)), "$lt": ist_midnight(start)}})
        facts["inquiries_last_week"] = got
        facts["inquiries_gap"] = max(int(r.get("inquiries_week") or 0) - got, 0)
    facts["leads_waiting"] = await db().leads.count_documents({"owner_id": ws, "status": "new"})
    facts["send_list"] = await db().approvals.count_documents({"ws": ws, "status": "pending"})
    gaps = await db().gap_scans.find_one({"ws": ws}, sort=[("at", -1)])
    score = await db().scores.find_one({"ws": ws}, sort=[("at", -1)])
    facts["fixes"] = [f["fix"] for f in ((gaps or {}).get("fixes") or (score or {}).get("fixes") or [])][:2]
    return facts


async def build_week(owner: dict, actor: str = "agent", at=None) -> dict:
    """Writes this week's plan (one AI run). Running it again in the same week rewrites the posts and actions."""
    from ..routers.hub import profile_of
    ws = owner["_id"]
    profile = await profile_of(ws)
    t = today_ist(at)
    facts = await week_facts(owner, at)
    result = await run_ai(owner, "week", program_tasks.week_plan(profile, facts), f"Your week — {monday_of(t).strftime('%d %b')}")
    posts = [{"day": str(p.get("day", ""))[:12], "hook": str(p.get("hook", ""))[:140], "caption": str(p.get("caption", ""))[:1200],
              "hashtags": [str(h)[:40] for h in (p.get("hashtags") or [])][:4]} for p in (result.get("posts") or [])[:7] if isinstance(p, dict)]
    actions = [{"text": str(a.get("text", ""))[:200], "why": str(a.get("why", ""))[:200], "done": False}
               for a in (result.get("actions") or [])[:3] if isinstance(a, dict) and a.get("text")]
    key = f"{ws}:{week_key(t)}"
    await db().weeks.update_one({"_id": key}, {"$set": {"ws": ws, "week": week_key(t), "start": monday_of(t).isoformat(), "posts": posts,
                                                        "actions": actions, "facts": facts, "by": actor, "updated_at": now()},
                                               "$setOnInsert": {"days": {}, "checkin": None, "created_at": now()}}, upsert=True)
    return await db().weeks.find_one({"_id": key})


async def week_plan(owner: dict, run: dict):
    if not await db().businesses.find_one({"owner_id": owner["_id"], "name": {"$exists": True}}, {"_id": 1}):
        raise Skip("The Business Brain isn't filled in yet.")
    w = await build_week(owner, at=run["at"])
    b = await db().businesses.find_one({"owner_id": owner["_id"]}, {"name": 1}) or {}
    first = (w.get("actions") or [{}])[0].get("text", "")
    await notify(owner["_id"], "Your week is ready: 7 posts and 3 actions are on This week.")
    await whatsapp_template(owner["phone"], settings.aisensy_owner_alert_campaign, owner.get("name", ""),
                            [b.get("name") or "your business", f"Your week is ready in the hub: 7 posts and 3 actions. First up: {first}"[:900]],
                            "week_ready")
    return f"Planned the week: {len(w.get('posts') or [])} posts and {len(w.get('actions') or [])} actions."


# ───────────────────────── today's action and the streak ─────────────────────────
async def todays_action(owner: dict, w: dict | None) -> dict:
    """One 5-minute action for today, chosen by the hub (no AI run) and kept for the rest of the day."""
    ws = owner["_id"]
    t = today_ist().isoformat()
    if w and (w.get("days") or {}).get(t):
        return w["days"][t]
    waiting = await db().leads.count_documents({"owner_id": ws, "status": "new"})
    pending = await db().approvals.count_documents({"ws": ws, "status": "pending"})
    open_action = next((a for a in (w or {}).get("actions") or [] if not a.get("done")), None)
    overdue = await db().dues.count_documents({"ws": ws, "status": "due", "due_date": {"$lt": t}})
    if waiting:
        item = {"text": f"Reply to the {waiting} new inquir{'y' if waiting == 1 else 'ies'} waiting for you", "link": "/leads"}
    elif pending:
        item = {"text": f"Send the {pending} message{'s' if pending != 1 else ''} in your Send list", "link": "/approvals"}
    elif open_action:
        item = {"text": open_action["text"], "link": "/week"}
    elif overdue:
        item = {"text": "Call one customer who owes you money and agree a date", "link": "/money"}
    else:
        item = {"text": "Share today's post on WhatsApp Status and your social pages", "link": "/week"}
    item["done"] = False
    if w:
        await db().weeks.update_one({"_id": w["_id"]}, {"$set": {f"days.{t}": item}})
    return item


async def tick_day(ws: str) -> dict:
    """Today's action is done: the streak grows by one (once a day)."""
    t = today_ist()
    s = await db().streaks.find_one({"_id": ws}) or {"current": 0, "best": 0, "last_day": None}
    if s.get("last_day") == t.isoformat():
        return s
    current = s.get("current", 0) + 1 if s.get("last_day") == (t - timedelta(days=1)).isoformat() else 1
    s = {"current": current, "best": max(current, s.get("best", 0)), "last_day": t.isoformat()}
    await db().streaks.update_one({"_id": ws}, {"$set": s}, upsert=True)
    return s


async def streak(ws: str) -> dict:
    s = await db().streaks.find_one({"_id": ws}) or {"current": 0, "best": 0, "last_day": None}
    t = today_ist()
    if s.get("last_day") not in (t.isoformat(), (t - timedelta(days=1)).isoformat()):
        s["current"] = 0   # a missed day ends the streak
    return {"current": s.get("current", 0), "best": s.get("best", 0), "done_today": s.get("last_day") == t.isoformat()}


def week_score(w: dict | None) -> int:
    """40 for the 3 actions, 40 for the daily actions (6 of 7 days is full marks), 20 for Friday's check-in."""
    if not w:
        return 0
    acts = w.get("actions") or []
    a = (sum(1 for x in acts if x.get("done")) / len(acts)) if acts else 0
    days = sum(1 for d in (w.get("days") or {}).values() if d.get("done"))
    return round(40 * a + 40 * min(days / 6, 1) + (20 if w.get("checkin") else 0))


# ───────────────────────── the content calendar ─────────────────────────
def posting_days(year: int, month: int) -> list[dict]:
    """Monday, Wednesday and Friday of the month, plus every festival or occasion day."""
    first = date(year, month, 1)
    nxt = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    occasions = {f["date"]: f["name"] for f in festivals.in_month(year, month)}
    out = []
    d = first
    while d < nxt:
        iso = d.isoformat()
        if iso in occasions or d.weekday() in (0, 2, 4):
            out.append({"date": iso, "occasion": occasions.get(iso, "")})
        d += timedelta(days=1)
    return out


async def write_calendar(owner: dict, year: int, month: int, focus: str = "", actor: str = "agent") -> dict:
    from ..routers.hub import profile_of
    ws = owner["_id"]
    profile = await profile_of(ws)
    slots = posting_days(year, month)
    label = date(year, month, 1).strftime("%B %Y")
    result = await run_ai(owner, "calendar", program_tasks.content_calendar(profile, label, slots, focus), f"Content calendar — {label}")
    by_date = {str(p.get("date")): p for p in (result.get("posts") or []) if isinstance(p, dict)}
    key = f"{ws}:{year:04d}-{month:02d}"
    old = await db().calendars.find_one({"_id": key}) or {}
    posted = {p["date"]: p for p in old.get("posts") or [] if p.get("posted")}   # a post already out stays as it was posted
    posts = []
    for s in slots:
        if s["date"] in posted:
            posts.append(posted[s["date"]])
            continue
        p = by_date.get(s["date"]) or {}
        posts.append({"date": s["date"], "occasion": s["occasion"], "hook": str(p.get("hook", ""))[:140],
                      "caption": str(p.get("caption", ""))[:1200], "hashtags": [str(h)[:40] for h in (p.get("hashtags") or [])][:4],
                      "posted": False})
    await db().calendars.replace_one({"_id": key}, {"_id": key, "ws": ws, "month": f"{year:04d}-{month:02d}", "label": label, "focus": focus,
                                                    "posts": posts, "by": actor, "created_at": now()}, upsert=True)
    return await db().calendars.find_one({"_id": key})


async def calendar_auto(owner: dict, run: dict):
    local = run["at"].astimezone(IST)
    nxt = (local.replace(day=28) + timedelta(days=4)).replace(day=1)
    if await db().calendars.find_one({"_id": f"{owner['_id']}:{nxt.strftime('%Y-%m')}"}, {"_id": 1}):
        raise Skip(f"{nxt.strftime('%B')}'s calendar is already written.")
    if not await db().businesses.find_one({"owner_id": owner["_id"], "name": {"$exists": True}}, {"_id": 1}):
        raise Skip("The Business Brain isn't filled in yet.")
    cal = await write_calendar(owner, nxt.year, nxt.month)
    await notify(owner["_id"], f"Your posts for {cal['label']} are ready in Content calendar ({len(cal['posts'])} posts).")
    return f"Wrote {len(cal['posts'])} posts for {cal['label']}."


catalog.attach("week_plan", week_plan)
catalog.attach("calendar_auto", calendar_auto)


def magic_result(inputs: dict) -> dict:
    """Revenue target → customers → inquiries → daily activity. All maths here, never in the AI."""
    target = float(inputs.get("monthly_target") or 0)
    avg = float(inputs.get("avg_sale") or 0)
    close = float(inputs.get("close_rate") or 0) / 100
    reply = float(inputs.get("meeting_rate") or 0) / 100
    if target <= 0 or avg <= 0 or close <= 0:
        return {}
    customers = math.ceil(target / avg)
    inquiries = math.ceil(customers / close)
    meetings = math.ceil(customers / close * reply) if reply else None
    return {"customers_month": customers, "inquiries_month": inquiries, "inquiries_week": math.ceil(inquiries / 4.33),
            "inquiries_day": round(inquiries / 26, 1), "meetings_month": meetings,
            "followups_day": round(inquiries / 26 * 3, 1), "posts_week": 3 if inquiries < 40 else 5 if inquiries < 120 else 7}


def has_week(owner: dict) -> bool:
    return plans.has(owner, "week")
