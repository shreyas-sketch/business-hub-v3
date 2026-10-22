"""
Win-Back (v2.2, Action Program): customers who haven't bought for a while get a personal hello on WhatsApp — and by email
when there's an address — drafted for the owner and sent only when the owner (or a manager) taps. Nothing goes out on its own.

Who is a candidate: a customer with a WhatsApp number or an email whose last purchase is at least `days` ago (and at most
2 years ago), with at least `min_purchases` purchases, not opted out, and not drafted by Win-Back in the last 90 days.
Purchases come from the sales book (hub invoices, Razorpay, the uploaded Tally/Excel sales sheet — app/salesbook.py) and the
Customers list; when both know a customer the LATER last purchase wins, so nobody who just bought is messaged. A customer
another automation contacted in the last 30 days (customers.last_contacted_at) waits too. Biggest spenders first.

The weekly agent (Tuesday 11am India time, only for owners who switched Win-Back on after reading the sample): up to
`per_week` customers a week (the scheduled run and "Run now" share the week's allowance). ONE AI run a week writes a short
message per kind of customer (ai/winback_tasks.py); the hub fills in the first name and adds the owner's own offer exactly as
typed — never a price or discount the owner didn't type. Each customer gets:
- a Send list item (WhatsApp, kind "customer"): tap-to-send on every plan; "send from my number" on Growth Mentorship with a
  connected number (the "customer" template). Never sent on its own (allow_act=False), whatever the plan.
- with an email address and email switched on: an email draft the owner sends with one tap, from the hub's sender with the
  business's reply-to — only when email is set up on this hub.
Drafts still waiting after 14 days expire.

Collections
winback_settings {_id: ws, on, days 45|60|90|120, min_purchases 1|2, per_week 1–40, offer, channels {whatsapp, email}, updated_at, by}
winback_drafts   {_id, ws, week "YYYY-Www", key, phone_key, name, phone, email, kind, text, subject, email_text, written_by ai|template,
                  approval_id, email_status None|draft|sent|skipped|expired, email_sent_at, email_id, status pending|done|skipped,
                  last_purchase, days_since, purchases, total, source sales|customers list, customer_id, list_total, list_purchases,
                  created_at}
winback_log      {_id, ws, key, phone_key, at, channel whatsapp|email, approval_id, draft_id, status drafted|sent|skipped|expired}
winback_weeks    {_id: "<ws>:<week>", ws, week, texts {kind: {whatsapp, subject, by}}, ai {status, note}, at}

GET  /api/win-back                  settings, the sample, this week's drafts, candidates count, results (manager)
GET  /api/win-back/candidates       who would get a message, biggest spenders first (manager)
PUT  /api/win-back/settings         the owner's settings and the On switch (owner; switching on needs the sample's key)
POST /api/win-back/run              run now — at most once a day (manager)
POST /api/win-back/{id}/whatsapp    tap-to-send: marks it sent by the owner and returns the wa.me link (manager)
POST /api/win-back/{id}/send        send from the owner's own connected number (Growth Mentorship; manager)
POST /api/win-back/{id}/send-email  send the email draft (manager; only when email is set up)
POST /api/win-back/{id}/skip        skip this customer (manager)
"""
import hashlib
import math
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import mailer, salesbook
from ..agents import approvals as appr
from ..agents import catalog, jobs
from ..agents.catalog import Skip
from ..agents.runners import inr, plural, pretty_date
from ..ai import winback_tasks
from ..config import settings as app_settings
from ..context import Ctx, feature
from ..db import IST, db, now
from ..salesbook import customer_key, is_walk_in, name_key, phone_key
from ..services import new_id, run_ai, track
from ..text import greeting_name

router = APIRouter(prefix="/api")

DEFAULTS = {"on": False, "days": 60, "min_purchases": 2, "per_week": 15, "offer": "", "channels": {"whatsapp": True, "email": True}}
DAYS = (45, 60, 90, 120)
MAX_PER_WEEK = 40
OLDEST_DAYS = 730          # a last purchase more than 2 years ago is too old to write to
NO_REPEAT_DAYS = 90        # a customer Win-Back drafted for waits this long
CONTACTED_DAYS = 30        # ... and one another automation contacted this recently waits too
EXPIRE_DAYS = 14           # drafts nobody sent are cleared after this
RESULT_DAYS = 60           # "bought again" = a purchase within this many days after the message
# The Agents page lists the groups "team", "alerts" and "approve_once". Win-Back is switched on and run from its own page
# (its switch is winback_settings.on, off until the owner reads the sample), so it isn't listed there — see the report.
AGENTS_PAGE_GROUP = "own_page"


# ───────────────────────── helpers ─────────────────────────
def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _day(at: datetime) -> date:
    return _aware(at).astimezone(IST).date()


def week_of(at: datetime) -> str:
    return _aware(at).astimezone(IST).strftime("%G-W%V")


def _iso(v) -> str:
    try:
        return date.fromisoformat(str(v or "")[:10]).isoformat() if v else ""
    except ValueError:
        return ""


async def get_settings(ws: str) -> dict:
    doc = await db().winback_settings.find_one({"_id": ws}) or {}
    st = {**DEFAULTS, **{k: v for k, v in doc.items() if k in DEFAULTS and v is not None}}
    st["channels"] = {**DEFAULTS["channels"], **(doc.get("channels") or {})}
    return st


async def _business(ws: str) -> dict:
    return await db().businesses.find_one({"owner_id": ws}) or {}


def _offer_line(offer: str) -> str:
    o = " ".join((offer or "").split())
    return o if not o or o[-1] in ".!?" else o + "."


def compose(wording: dict, name: str, offer: str, business: str) -> dict:
    """One customer's message: the week's wording with their first name, and the owner's offer exactly as typed."""
    first = greeting_name(name) or "there"
    body = wording["whatsapp"].replace("{name}", first)
    offer_line = _offer_line(offer)
    text = f"{body} {offer_line}".strip() if offer_line else body
    email_text = (f"{text}\n\nWarm regards,\n{business or 'us'}\n\n"
                  f"If you'd rather not get messages like this from us, just reply and tell us.")
    return {"text": text, "subject": wording["subject"], "email_text": email_text}


# ───────────────────────── candidates ─────────────────────────
async def candidates(ws: str, st: dict, at: datetime) -> list[dict]:
    """Everyone Win-Back would write to now, biggest spenders first (see the module notes)."""
    today = _day(at)
    rows = [r for r in await salesbook.history(ws) if r["date"] <= today.isoformat()]
    groups = [g for g in salesbook.group_customers(rows) if g["customer"] and not is_walk_in(g["customer"])]

    by_phone: dict[str, dict] = {}
    by_name: dict[str, list[dict]] = {}
    fields = {"name": 1, "phone": 1, "email": 1, "opted_out": 1, "total_value": 1, "purchases": 1, "last_purchase": 1, "last_contacted_at": 1}
    listed = [c async for c in db().customers.find({"ws": ws}, fields).limit(salesbook.MAX_ROWS)]
    for c in listed:
        pk = phone_key(c.get("phone", ""))
        if pk:
            by_phone.setdefault(pk, c)
        nk = name_key(c.get("name", ""))
        if nk:
            by_name.setdefault(nk, []).append(c)

    def match(name: str, phone: str) -> dict | None:
        pk = phone_key(phone)
        if pk and pk in by_phone:
            return by_phone[pk]
        same = by_name.get(name_key(name)) or []
        if len(same) == 1 and not (pk and phone_key(same[0].get("phone", ""))):   # two different numbers: two people
            return same[0]
        return None

    people: list[dict] = []
    used: set = set()
    for g in groups:
        days = sorted({r["date"] for r in g["rows"] if r["amount"] > 0})
        if not days:
            continue
        c = match(g["customer"], g["phone"])
        last, purchases = days[-1], len(days)
        if c:
            used.add(c["_id"])
            last = max(last, _iso(c.get("last_purchase")))
            purchases = max(purchases, int(c.get("purchases") or 0))
        people.append({"key": g["key"], "name": g["customer"], "phone": g["phone"] or (c or {}).get("phone", ""),
                       "email": ((c or {}).get("email") or "").strip().lower(), "last_purchase": last, "purchases": purchases,
                       "total": round(sum(r["amount"] for r in g["rows"]), 2), "source": "sales", "customer": c})
    for c in listed:
        name = str(c.get("name") or "").strip()
        if c["_id"] in used or not name or is_walk_in(name):
            continue
        last = _iso(c.get("last_purchase"))
        if not last:
            continue                       # no last-purchase date: we can't tell how long it's been
        try:
            total, purchases = max(float(c.get("total_value") or 0), 0.0), max(int(c.get("purchases") or 0), 0)
        except (TypeError, ValueError):
            continue
        people.append({"key": customer_key(name, c.get("phone", "")), "name": name, "phone": c.get("phone", ""),
                       "email": (c.get("email") or "").strip().lower(), "last_purchase": last, "purchases": max(purchases, 1),
                       "total": round(total, 2), "source": "customers list", "customer": c})

    since = now() - timedelta(days=NO_REPEAT_DAYS)
    recent_keys, recent_phones = set(), set()
    async for x in db().winback_log.find({"ws": ws, "at": {"$gte": since}}, {"key": 1, "phone_key": 1}).limit(50_000):
        recent_keys.add(x.get("key"))
        if x.get("phone_key"):
            recent_phones.add(x["phone_key"])
    newest, oldest = (today - timedelta(days=int(st["days"]))).isoformat(), (today - timedelta(days=OLDEST_DAYS)).isoformat()
    contacted_after = at - timedelta(days=CONTACTED_DAYS)
    emails = sorted({p["email"] for p in people if mailer.valid(p["email"])})
    blocked = set()
    if emails:
        blocked = {s["address"] async for s in db().email_suppress.find({"owner_id": ws, "address": {"$in": emails}}, {"address": 1})}

    out = []
    for p in people:
        c = p.pop("customer") or {}
        pk = phone_key(p["phone"])
        if c.get("opted_out") or not (oldest <= p["last_purchase"] <= newest) or p["purchases"] < int(st["min_purchases"]):
            continue
        if p["key"] in recent_keys or (pk and pk in recent_phones):
            continue
        lc = c.get("last_contacted_at")
        if isinstance(lc, datetime) and _aware(lc) > contacted_after:
            continue
        reach = (["whatsapp"] if pk else []) + (["email"] if mailer.valid(p["email"]) and p["email"] not in blocked else [])
        if not reach:
            continue
        p.update(phone_key=pk, reach=reach, channels=[ch for ch in reach if st["channels"].get(ch)],
                 days_since=(today - date.fromisoformat(p["last_purchase"])).days, kind=winback_tasks.kind_of(p["purchases"]),
                 customer_id=c.get("_id"))
        if p["source"] == "customers list":
            p.update(list_total=float(c.get("total_value") or 0), list_purchases=int(c.get("purchases") or 0))
        out.append(p)
    out.sort(key=lambda p: (-p["total"], p["days_since"], p["name"].lower()))
    return out


def _cand_view(p: dict) -> dict:
    return {"key": p["key"], "name": p["name"], "phone": p["phone"], "email": p["email"], "last_purchase": p["last_purchase"],
            "days_since": p["days_since"], "purchases": p["purchases"], "total": p["total"], "kind": p["kind"],
            "kind_label": winback_tasks.LABELS[p["kind"]], "source": p["source"], "channels": p["channels"], "reach": p["reach"]}


# ───────────────────────── the week's wording (at most one AI run a week) ─────────────────────────
async def week_texts(owner: dict, business: dict, week: str, kinds: set, at: datetime) -> dict:
    ws = owner["_id"]
    doc = await db().winback_weeks.find_one({"_id": f"{ws}:{week}"}) or {}
    texts = dict(doc.get("texts") or {})
    ai = doc.get("ai") or {}
    name = business.get("name") or ""
    if any(k not in texts for k in kinds) and await jobs.claim(f"winback-ai:{ws}:{week}", at):
        try:   # all three kinds in the one run, so a later run this week needs none
            got = await run_ai(owner, "win_back", winback_tasks.messages(business, list(winback_tasks.KINDS)), "Win-Back messages",
                               save_output=False, check=lambda r: winback_tasks.checked(r, name, list(winback_tasks.KINDS)))
            texts.update(got)
            ai = {"status": "done", "note": ""}
        except HTTPException as e:
            ai = {"status": "no_runs" if e.status_code == 402 else "unusable",
                  "note": "No AI runs left this month" if e.status_code == 402 else "The AI's wording couldn't be used"}
    for k in winback_tasks.KINDS:
        texts.setdefault(k, winback_tasks.fallback(name, k))
    await db().winback_weeks.update_one({"_id": f"{ws}:{week}"}, {"$set": {"ws": ws, "week": week, "texts": texts, "ai": ai, "at": at}},
                                        upsert=True)
    return texts


def sample(business: dict, st: dict, texts: dict | None, top: dict | None) -> dict:
    """What a message looks like — shown to the owner before Win-Back can be switched on. The key covers the wording of every
    kind of customer and the offer (not who is on top of the list), so switching on proves this wording was on screen."""
    name = business.get("name") or ""
    words = {k: (texts or {}).get(k) or winback_tasks.fallback(name, k) for k in winback_tasks.KINDS}
    kind = (top or {}).get("kind") or "repeat"
    msg = compose(words[kind], (top or {}).get("name") or "", st["offer"], name)
    key = hashlib.sha1("\n".join([w for k in winback_tasks.KINDS for w in (words[k]["whatsapp"], words[k]["subject"])]
                                 + [_offer_line(st["offer"]), name]).encode()).hexdigest()[:16]
    return {**msg, "key": key, "kind": kind, "kind_label": winback_tasks.LABELS[kind], "written_by": words[kind].get("by", "template"),
            "for_name": (top or {}).get("name") or ""}


# ───────────────────────── drafting ─────────────────────────
async def _expire_old(ws: str, at: datetime) -> None:
    cutoff = at - timedelta(days=EXPIRE_DAYS)
    await appr.expire({"ws": ws, "agent": "win_back", "created_at": {"$lt": cutoff}}, "No longer needed: two weeks passed")
    old = [d["_id"] async for d in db().winback_drafts.find({"ws": ws, "email_status": "draft", "created_at": {"$lt": cutoff}}, {"_id": 1})]
    if old:
        await db().winback_drafts.update_many({"_id": {"$in": old}}, {"$set": {"email_status": "expired"}})
        await db().winback_log.update_many({"draft_id": {"$in": old}, "channel": "email", "status": "drafted"}, {"$set": {"status": "expired"}})


async def _draft(owner: dict, business: dict, st: dict, p: dict, texts: dict, week: str, at: datetime) -> dict:
    ws = owner["_id"]
    msg = compose(texts[p["kind"]], p["name"], st["offer"], business.get("name") or "")
    did = new_id()
    d = {"_id": did, "ws": ws, "week": week, "key": p["key"], "phone_key": p["phone_key"], "name": p["name"][:80], "phone": p["phone"],
         "email": p["email"] if "email" in p["channels"] else "", "kind": p["kind"], **msg, "written_by": texts[p["kind"]].get("by", "template"),
         "approval_id": None, "email_status": "draft" if "email" in p["channels"] else None, "email_sent_at": None, "status": "pending",
         "last_purchase": p["last_purchase"], "days_since": p["days_since"], "purchases": p["purchases"], "total": p["total"],
         "source": p["source"], "customer_id": p.get("customer_id"), "list_total": p.get("list_total"), "list_purchases": p.get("list_purchases"),
         "created_at": at}
    if "whatsapp" in p["channels"]:
        a = await appr.create_approval(owner, ws, "win_back", "customer",
                                       f"Win-Back: {p['name']} — last bought {pretty_date(p['last_purchase'])} ({p['days_since']} days ago)",
                                       p["name"], p["phone"], msg["text"], allow_act=False, winback_id=did, customer_key=p["key"])
        d["approval_id"] = a["_id"]
    await db().winback_drafts.insert_one(d)
    for ch in p["channels"]:
        await db().winback_log.insert_one({"_id": new_id(), "ws": ws, "key": p["key"], "phone_key": p["phone_key"], "at": at, "channel": ch,
                                           "approval_id": d["approval_id"] if ch == "whatsapp" else None, "draft_id": did, "status": "drafted"})
    if p.get("customer_id"):   # the customer desk spaces its own messages by this
        await db().customers.update_one({"_id": p["customer_id"], "ws": ws}, {"$set": {"last_contacted_at": at}})
    return d


async def win_back(owner: dict, run: dict):
    """The weekly agent (and "Run now"). Drafts only: every message waits for a tap."""
    ws, at = owner["_id"], run["at"]
    st = await get_settings(ws)
    if not st["on"]:
        raise Skip("Win-Back is off. Switch it on on the Win-Back page after reading the sample.")
    if not any(st["channels"].values()):
        raise Skip("Both WhatsApp and email are switched off for Win-Back.")
    week = week_of(at)
    room = int(st["per_week"]) - await db().winback_drafts.count_documents({"ws": ws, "week": week})
    if room <= 0:
        raise Skip(f"This week's {st['per_week']} comeback messages are already drafted.")
    await _expire_old(ws, at)
    pick = [p for p in await candidates(ws, st, at) if p["channels"]][:room]
    if not pick:
        raise Skip(f"Nobody to win back this week: no customer with a number or email has gone {st['days']} days without buying "
                   f"(or they were messaged in the last {NO_REPEAT_DAYS} days).")
    business = await _business(ws)
    texts = await week_texts(owner, business, week, {p["kind"] for p in pick}, at)
    made, emails, whatsapp = 0, 0, 0
    for p in pick:
        if not await jobs.claim(f"winback:{ws}:{p['key']}:{week}", at):
            continue
        d = await _draft(owner, business, st, p, texts, week, at)
        made += 1
        emails += d["email_status"] == "draft"
        whatsapp += bool(d["approval_id"])
    if not made:
        raise Skip("These customers were already drafted this week.")
    await track("win_back", ws, drafts=made, emails=emails)
    parts = ([f"{plural(whatsapp, 'WhatsApp message')} in your Send list"] if whatsapp else []) + \
            ([f"{plural(emails, 'email')} ready to send"] if emails else [])
    return f"Win-Back: {plural(made, 'customer')} who haven't bought in {st['days']}+ days — " + " and ".join(parts) + "."


async def _ready(owners: list[dict]) -> set:
    return {s["_id"] async for s in db().winback_settings.find({"_id": {"$in": [o["_id"] for o in owners]}, "on": True}, {"_id": 1})}


catalog.register("win_back", {
    "name": "Win-Back", "feature": "win_back", "trigger": "schedule", "customer_facing": True, "approvals": True, "group": AGENTS_PAGE_GROUP,
    "what": "Every Tuesday: customers who haven't bought in 60 days or more get a short personal hello on WhatsApp — and by email when "
            "you have their address. Written for you; nothing goes out until you tap Send.",
    "when": "Every Tuesday at 11am",
    "schedule": {"every": "week", "weekday": 1, "at": "11:00", "grace_hours": 8}, "ready": _ready}, win_back)


# ───────────────────────── what happened: sent, and who bought again ─────────────────────────
async def _approval_status(ids: list[str]) -> dict:
    ids = [i for i in ids if i]
    if not ids:
        return {}
    return {a["_id"]: a async for a in db().approvals.find({"_id": {"$in": ids}}, {"status": 1, "via": 1, "decided_at": 1, "text": 1, "to_phone": 1})}


def _sent_at(d: dict, a: dict | None) -> datetime | None:
    times = [t for t in ((a or {}).get("decided_at") if (a or {}).get("status") == "sent" else None,
                         d.get("email_sent_at") if d.get("email_status") == "sent" else None) if isinstance(t, datetime)]
    return min((_aware(t) for t in times), default=None)


async def results(ws: str, at: datetime) -> dict:
    """Counted from what really happened: messages the owner sent (WhatsApp tap / own number, or the email), and customers
    with a sale dated after the message and within RESULT_DAYS days — matched by customer key (or phone) in the sales book,
    or a later purchase on the Customers list. Nothing is estimated."""
    drafts = [d async for d in db().winback_drafts.find({"ws": ws}).sort("created_at", 1).limit(5000)]
    statuses = await _approval_status([d.get("approval_id") for d in drafts])
    sent = []
    for d in drafts:
        t = _sent_at(d, statuses.get(d.get("approval_id")))
        if t:
            sent.append((d, t))
    waiting = sum(1 for d in drafts if (statuses.get(d.get("approval_id")) or {}).get("status") == "pending" or d.get("email_status") == "draft")
    base = {"messaged": len(sent), "waiting": waiting, "bought_again": 0, "amount": 0.0, "weeks": 0, "since": None, "line": "",
            "window_days": RESULT_DAYS}
    if not sent:
        base["line"] = "Nothing sent yet." if not drafts else f"{plural(waiting, 'message')} waiting for you to send."
        return base
    first = min(_day(t) for _, t in sent)
    rows = [r for r in await salesbook.history(ws, since=first) if r["amount"] > 0]
    group_of: dict[int, str] = {}
    for g in salesbook.group_customers(rows):
        for r in g["rows"]:
            group_of[id(r)] = g["key"]
    ids = [d["customer_id"] for d, _ in sent if d.get("source") == "customers list" and d.get("customer_id")]
    listed = {c["_id"]: c async for c in db().customers.find({"_id": {"$in": ids}, "ws": ws}, {"last_purchase": 1, "total_value": 1, "purchases": 1})} if ids else {}
    won, amount = 0, 0.0
    for d, t in sent:
        start, end = _day(t).isoformat(), (_day(t) + timedelta(days=RESULT_DAYS)).isoformat()
        mine = [r for r in rows if start < r["date"] <= end and (r["key"] == d["key"] or group_of.get(id(r)) == d["key"]
                                                                  or (d.get("phone_key") and phone_key(r.get("phone", "")) == d["phone_key"]))]
        if mine:
            won += 1
            amount += sum(r["amount"] for r in mine)
            continue
        c = listed.get(d.get("customer_id"))
        if c and start < _iso(c.get("last_purchase")) <= end:
            won += 1
            if int(c.get("purchases") or 0) > int(d.get("list_purchases") or 0):
                amount += max(float(c.get("total_value") or 0) - float(d.get("list_total") or 0), 0.0)
    weeks = max(1, math.ceil(((_day(at) - first).days + 1) / 7))
    line = f"{len(sent)} messaged in {plural(weeks, 'week')} · {won} bought again" + (f", {inr(round(amount) * 100)}" if won and amount > 0 else "")
    return {**base, "bought_again": won, "amount": round(amount, 2), "weeks": weeks, "since": first.isoformat(), "line": line}


# ───────────────────────── the page ─────────────────────────
def _draft_view(d: dict, a: dict | None) -> dict:
    wa = None
    text = (a or {}).get("text") or d.get("text", "")      # the Send list may have edited it
    if d.get("approval_id"):
        status = (a or {}).get("status") or "pending"
        wa = {"status": status, "via": (a or {}).get("via"), "link": appr.own_link({"to_phone": d.get("phone", ""), "text": text})}
    em = {"status": d["email_status"], "to": d.get("email", "")} if d.get("email_status") else None
    return {"id": d["_id"], "name": d["name"], "phone": d.get("phone", ""), "email": d.get("email", ""), "kind": d["kind"],
            "kind_label": winback_tasks.LABELS.get(d["kind"], ""), "text": text, "subject": d["subject"], "email_text": d["email_text"],
            "written_by": d.get("written_by"), "last_purchase": d["last_purchase"], "days_since": d["days_since"],
            "purchases": d["purchases"], "total": d["total"], "week": d["week"], "created_at": _aware(d["created_at"]).isoformat(),
            "whatsapp": wa, "email_draft": em}


def email_ready() -> bool:
    return mailer.configured() or app_settings.local_dev


@router.get("/win-back")
async def overview(ctx: Ctx = Depends(feature("win_back", "manager"))):
    at = now()
    st = await get_settings(ctx.ws)
    b = await _business(ctx.ws)
    cands = await candidates(ctx.ws, st, at)
    week = week_of(at)
    texts = (await db().winback_weeks.find_one({"_id": f"{ctx.ws}:{week}"}) or {}).get("texts")
    drafts = [d async for d in db().winback_drafts.find({"ws": ctx.ws, "$or": [{"week": week}, {"created_at": {"$gte": at - timedelta(days=EXPIRE_DAYS)}}]})
              .sort("created_at", -1).limit(200)]
    statuses = await _approval_status([d.get("approval_id") for d in drafts])
    views = [_draft_view(d, statuses.get(d.get("approval_id"))) for d in drafts]
    rows = await db().sales_rows.count_documents({"ws": ctx.ws})
    invoices = await db().quotes.count_documents({"ws": ctx.ws, "kind": "invoice"})
    dated = await db().customers.count_documents({"ws": ctx.ws, "last_purchase": {"$nin": [None, ""]}})
    last = await db().agent_log.find_one({"ws": ctx.ws, "agent": "win_back"}, sort=[("at", -1)])
    ran_today = bool(await db().job_claims.find_one({"_id": f"winback-run:{ctx.ws}:{_day(at).isoformat()}"}, {"_id": 1}))
    return {"settings": {k: st[k] for k in DEFAULTS}, "on": st["on"], "can_edit": ctx.role == "owner",
            "sample": sample(b, st, texts, cands[0] if cands else None),
            "drafts": views, "this_week": sum(1 for d in drafts if d["week"] == week), "week": week,
            "candidates": {"count": len(cands), "reachable": sum(1 for p in cands if p["channels"]), "top": [_cand_view(p) for p in cands[:5]]},
            "results": await results(ctx.ws, at),
            "data": {"sales_rows": rows, "invoices": invoices, "customers_with_dates": dated, "little": rows + invoices < 10 and dated < 10},
            "email_ready": email_ready(), "connected": bool(await appr.owner_whatsapp(ctx.ws, ctx.owner)),
            "last_run": {"at": _aware(last["at"]).isoformat(), "status": last.get("status"), "note": last.get("text", "")} if last else None,
            "ran_today": ran_today, "next_run_at": catalog.next_occurrence(catalog.AGENTS["win_back"].schedule, at).isoformat() if st["on"] else None,
            "limits": {"days": list(DAYS), "per_week_max": MAX_PER_WEEK, "no_repeat_days": NO_REPEAT_DAYS, "result_days": RESULT_DAYS}}


@router.get("/win-back/candidates")
async def list_candidates(ctx: Ctx = Depends(feature("win_back", "manager"))):
    st = await get_settings(ctx.ws)
    cands = await candidates(ctx.ws, st, now())
    return {"items": [_cand_view(p) for p in cands[:300]], "count": len(cands), "days": st["days"], "min_purchases": st["min_purchases"]}


class Channels(BaseModel):
    whatsapp: bool | None = None
    email: bool | None = None


class SettingsIn(BaseModel):
    on: bool | None = None
    days: Literal[45, 60, 90, 120] | None = None
    min_purchases: Literal[1, 2] | None = None
    per_week: int | None = Field(default=None, ge=1, le=MAX_PER_WEEK)
    offer: str | None = Field(default=None, max_length=160)
    channels: Channels | None = None
    sample_key: str | None = Field(default=None, max_length=40)


@router.put("/win-back/settings")
async def save_settings(body: SettingsIn, ctx: Ctx = Depends(feature("win_back", "owner"))):
    st = await get_settings(ctx.ws)
    patch: dict = {k: v for k, v in body.model_dump(exclude_none=True, exclude={"channels", "sample_key", "on"}).items()}
    if "offer" in patch:
        offer = " ".join(patch["offer"].split())
        if "{" in offer or "}" in offer:
            raise HTTPException(400, "Write the offer in plain words, without { or }.")
        patch["offer"] = offer
    if body.channels is not None:
        patch["channels"] = {**st["channels"], **body.channels.model_dump(exclude_none=True)}
    new = {**st, **patch}
    if body.on is True and not st["on"]:
        texts = (await db().winback_weeks.find_one({"_id": f"{ctx.ws}:{week_of(now())}"}) or {}).get("texts")
        current = sample(await _business(ctx.ws), new, texts, None)
        if body.sample_key != current["key"]:
            raise HTTPException(409, "Read the sample message first — it changed. Then switch Win-Back on.")
        if not any(new["channels"].values()):
            raise HTTPException(400, "Switch on WhatsApp or email (or both) first.")
    if body.on is not None:
        patch["on"] = body.on
    if not any(new["channels"].values()) and (patch.get("on") if "on" in patch else st["on"]):
        raise HTTPException(400, "Keep WhatsApp or email switched on, or switch Win-Back off.")
    patch.update(updated_at=now(), by=ctx.actor)
    await db().winback_settings.update_one({"_id": ctx.ws}, {"$set": patch}, upsert=True)
    if "on" in patch:
        await track("win_back_on" if patch["on"] else "win_back_off", ctx.ws)
    return await overview(ctx)


@router.post("/win-back/run")
async def run_now(ctx: Ctx = Depends(feature("win_back", "manager"))):
    at = now()
    st = await get_settings(ctx.ws)
    if not st["on"]:
        raise HTTPException(400, "Switch Win-Back on first (after reading the sample).")
    key = f"winback-run:{ctx.ws}:{_day(at).isoformat()}"
    if not await jobs.claim(key, at):
        raise HTTPException(429, "Win-Back already ran today. You can run it again tomorrow.")
    spec = catalog.AGENTS["win_back"]
    status, note = await jobs.execute(ctx.owner, spec, {"at": at, "slot": None, "job": None, "spec": spec})
    if status in ("skipped", "retry"):
        await jobs.release(key)      # nothing was drafted: the owner may add sales and try again today
    return {"status": status, "note": note, **await overview(ctx)}


async def _own_draft(ctx: Ctx, draft_id: str) -> dict:
    d = await db().winback_drafts.find_one({"_id": draft_id, "ws": ctx.ws})
    if not d:
        raise HTTPException(404, "Not found")
    return d


async def _mark(d: dict, channel: str, status: str) -> None:
    await db().winback_log.update_many({"draft_id": d["_id"], "channel": channel}, {"$set": {"status": status}})


async def _settle(d: dict) -> None:
    """The draft is done when neither channel is still waiting."""
    d = await db().winback_drafts.find_one({"_id": d["_id"]}) or d
    a = await db().approvals.find_one({"_id": d.get("approval_id")}, {"status": 1}) if d.get("approval_id") else None
    if (a or {}).get("status") == "pending" or d.get("email_status") == "draft":
        return
    sent = (a or {}).get("status") == "sent" or d.get("email_status") == "sent"
    await db().winback_drafts.update_one({"_id": d["_id"]}, {"$set": {"status": "done" if sent else "skipped"}})


async def _decide(ctx: Ctx, draft_id: str, action: str) -> dict:
    d = await _own_draft(ctx, draft_id)
    if not d.get("approval_id"):
        raise HTTPException(400, "This customer has no WhatsApp number.")
    out = await appr.decide(ctx.ws, d["approval_id"], ctx.actor, action)
    await _mark(d, "whatsapp", "sent" if out["approval"]["status"] == "sent" else "skipped")
    await _settle(d)
    return out


@router.post("/win-back/{draft_id}/whatsapp")
async def whatsapp_tap(draft_id: str, ctx: Ctx = Depends(feature("win_back", "manager"))):
    """Tap-to-send: opens the owner's own WhatsApp with the message typed (the page opens the link in the tap itself)."""
    out = await _decide(ctx, draft_id, "own")
    return {"link": out["link"]}


@router.post("/win-back/{draft_id}/send")
async def send_from_number(draft_id: str, ctx: Ctx = Depends(feature("win_back", "manager"))):
    """Growth Mentorship with a connected number: sent from the owner's own WhatsApp number (the customer template)."""
    await _decide(ctx, draft_id, "send")
    return {"ok": True}


@router.post("/win-back/{draft_id}/send-email")
async def send_email(draft_id: str, ctx: Ctx = Depends(feature("win_back", "manager"))):
    """The owner (or a manager) sends the email draft: from the hub's sender, replies to the business's own email."""
    d = await _own_draft(ctx, draft_id)
    if not d.get("email") or d.get("email_status") not in ("draft", "sent"):
        raise HTTPException(400, "There's no email to send for this customer.")
    if d["email_status"] == "sent":
        raise HTTPException(409, "Already sent.")
    if not email_ready():
        raise HTTPException(400, "Email isn't set up on this hub yet. Send it on WhatsApp, or copy the text into your own email.")
    key = f"winback-email:{draft_id}"
    if not await jobs.claim(key):
        raise HTTPException(409, "Already sent.")
    b = await _business(ctx.ws)
    res = await mailer.send(owner=ctx.owner, to=d["email"], subject=d["subject"], text=d["email_text"], kind="win_back",
                            from_name=b.get("name") or "Business", reply_to=b.get("email"), ref={"winback_id": draft_id})
    if not res["sent"]:
        await jobs.release(key)
        raise HTTPException(400 if res.get("via") in ("suppressed", "no-email", "email-not-configured", "paused", "daily-limit") else 502,
                            res.get("error") or "The email couldn't be sent just now.")
    await db().winback_drafts.update_one({"_id": draft_id}, {"$set": {"email_status": "sent", "email_sent_at": now(), "email_id": res.get("id"),
                                                                       "email_by": ctx.actor}})
    await _mark(d, "email", "sent")
    await _settle(d)
    return {"ok": True, "via": res.get("via")}


@router.post("/win-back/{draft_id}/skip")
async def skip(draft_id: str, ctx: Ctx = Depends(feature("win_back", "manager"))):
    """Skip this customer: neither message goes. They're not drafted again for 90 days."""
    d = await _own_draft(ctx, draft_id)
    if d.get("approval_id"):
        a = await db().approvals.find_one({"_id": d["approval_id"], "ws": ctx.ws}, {"status": 1})
        if (a or {}).get("status") == "pending":
            try:
                await appr.decide(ctx.ws, d["approval_id"], ctx.actor, "skip")
            except HTTPException as e:
                if e.status_code != 409:       # handled a moment ago elsewhere: fine
                    raise
            await _mark(d, "whatsapp", "skipped")
    if d.get("email_status") == "draft":
        await db().winback_drafts.update_one({"_id": draft_id, "email_status": "draft"}, {"$set": {"email_status": "skipped"}})
        await _mark(d, "email", "skipped")
    await _settle(d)
    return {"ok": True}


async def ensure_indexes() -> None:
    d = db()
    await d.winback_drafts.create_index([("ws", 1), ("week", 1)])
    await d.winback_drafts.create_index([("ws", 1), ("created_at", -1)])
    await d.winback_log.create_index([("ws", 1), ("at", -1)])
    await d.winback_log.create_index("draft_id")
