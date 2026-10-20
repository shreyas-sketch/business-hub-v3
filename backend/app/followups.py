"""
Automatic payment follow-ups (v2.1, 3.8 — decision D2: the owner approves once, then it runs).

Three stages for every amount in Money owed that has the customer's email:
    pending   3 days before the due date   a friendly heads-up with the invoice details
    due       on the due date              a polite reminder
    overdue   every 3 days after, at most 4 (PAYMENT_REMINDER_GAP_DAYS)   polite → firm; the last asks for a date

Channels:
- Email: automatic once the owner has switched it on and approved one sample of each stage. The invoice PDF is attached
  when the amount came from an invoice; Reply-To is the owner (plus the hub's reply address, so a reply stops the chase).
- WhatsApp: below Growth Mentorship it stays in the Send list (tap to send, the existing Payment reminders automation).
  From Growth Mentorship, with the same approval, it goes from the owner's own connected number as the approved
  reminder template. The hub's own number never messages a customer.
It stops when the amount is marked paid, a gateway payment matches it, the customer replies, or the owner excludes that
customer ("don't chase automatically"). The wording is fixed (not AI) so what the owner approved is what goes out.

followup_auto {_id: ws, on, approved_at, approved_by, approved_hash, approved_samples, whatsapp, exclude [customer keys]}
dues.followups [{stage, n, channel, at, ok, via, ref}]  dues.auto_stopped "replied"|... dues.auto_overdue_sent n
"""
import hashlib
import logging
import json
import re
from datetime import date, timedelta

from . import invoice_pdf, mailer, plans
from .config import settings
from .db import IST, db, now
from .text import greeting_name

log = logging.getLogger("followups")

PENDING_DAYS = 3
TRY_AGAIN = ("daily-limit", "email", "paused", "email-not-configured")   # not sent for a reason that may pass
STAGES = ("pending", "due", "overdue")


def inr(paise: int | float) -> str:
    from .agents.runners import inr as _inr
    return _inr(paise)


def pretty(day: str) -> str:
    try:
        d = date.fromisoformat(day)
        return f"{d.day} {d.strftime('%b %Y')}"
    except (TypeError, ValueError):
        return day or ""


def customer_key(due: dict) -> str:
    email = (due.get("email") or "").strip().lower()
    digits = re.sub(r"\D", "", due.get("phone") or "")[-10:]
    return email or (digits if len(digits) == 10 else (due.get("customer") or "").strip().lower())


def message(stage: str, n: int, due: dict, business: dict) -> dict:
    """The fixed wording of each stage. n = which overdue reminder (1–4); the last one asks for a date."""
    name = greeting_name(due.get("customer", "")) or ""
    hello = f"Hello {name}," if name else "Hello,"
    biz = business.get("name") or "us"
    amount, day = inr(due.get("amount", 0)), pretty(due.get("due_date", ""))
    inv = f" for invoice {due['invoice_no']}" if due.get("invoice_no") else ""
    how = (business.get("payment_note") or "").strip().rstrip(".")
    pay = f"\n\nHow to pay: {how}." if how else ""
    sign = f"\n\nThank you,\n{biz}"
    last = int(settings_max())
    if stage == "pending":
        subject = f"Payment of {amount}{inv} is due on {day}"
        body = f"{hello}\n\nA friendly note from {biz}: {amount}{inv} falls due on {day}.{pay}\n\nIf it's already on its way, please ignore this email." + sign
    elif stage == "due":
        subject = f"Payment due today: {amount}{inv}"
        body = f"{hello}\n\nThis is a reminder that {amount}{inv} is due today, {day}.{pay}\n\nIf you've already paid, thank you — please ignore this email." + sign
    elif n < last:
        firm = "" if n == 1 else " This is our reminder number {n}.".format(n=n)
        subject = f"Reminder: {amount}{inv} was due on {day}"
        body = (f"{hello}\n\nOur records show {amount}{inv} was due on {day} and is still open.{firm}{pay}\n\n"
                f"If you've already paid, please reply with the payment details so we can update our records." + sign)
    else:
        subject = f"Final reminder: {amount}{inv} is overdue since {day}"
        body = (f"{hello}\n\n{amount}{inv} has been overdue since {day}, and we've reminded you {last - 1} times.{pay}\n\n"
                f"Please reply to this email with the date you'll pay it, so we can plan for it." + sign)
    return {"stage": stage, "n": n, "subject": subject, "text": body}


def settings_max() -> int:
    from .agents.runners import MAX_REMINDERS
    return MAX_REMINDERS


SAMPLE_DUE = {"customer": "Mehta Traders", "amount": 4_250_000, "due_date": "2026-11-15", "invoice_no": "INV-0042", "email": "accounts@example.com"}


async def samples(ws: str) -> dict:
    """One example of each stage, for the owner to approve before anything goes out on its own."""
    b = await db().businesses.find_one({"owner_id": ws}) or {}
    example = SAMPLE_DUE
    out = {"pending": message("pending", 0, example, b), "due": message("due", 0, example, b),
           "overdue": message("overdue", 1, example, b), "final": message("overdue", settings_max(), example, b)}
    # the WhatsApp reminder (Growth Mentorship, from the owner's own number) is part of what the owner approves too
    from .agents.runners import reminder_text
    wa = reminder_text(greeting_name(example["customer"]) or "there", b.get("name") or "us", inr(example["amount"]), pretty(example["due_date"]),
                       (b.get("payment_note") or "").strip(), example["invoice_no"])
    raw = json.dumps({**{k: [v["subject"], v["text"]] for k, v in out.items()}, "whatsapp": wa}, sort_keys=True)
    return {"samples": out, "whatsapp_sample": wa, "hash": hashlib.sha1(raw.encode()).hexdigest()[:16], "example_customer": example["customer"]}


async def approved_current(ws: str, st: dict | None = None) -> bool:
    """Still the wording the owner approved? A change to the business name or how-to-pay note changes it."""
    st = st if st is not None else await get_state(ws)
    return bool(st.get("approved_hash")) and st["approved_hash"] == (await samples(ws))["hash"]


async def get_state(ws: str) -> dict:
    return await db().followup_auto.find_one({"_id": ws}) or {"_id": ws, "on": False, "exclude": []}


def whatsapp_auto_allowed(owner: dict) -> bool:
    """WhatsApp reminders may go on their own only from Growth Mentorship, from the owner's own number."""
    return plans.rank(plans.effective_plan(owner)) >= plans.rank("growth") and plans.has(owner, "whatsapp_ai")


async def whatsapp_auto(owner: dict) -> bool:
    st = await get_state(owner["_id"])
    return bool(st.get("on") and st.get("whatsapp") and plans.has(owner, "auto_reminders") and whatsapp_auto_allowed(owner)
                and await approved_current(owner["_id"], st))


def next_stage(due: dict, t: date) -> tuple[str, int] | None:
    """Which stage (and which overdue reminder) is due today for this amount, if any. Catches up after a missed day."""
    try:
        d = date.fromisoformat(due["due_date"])
    except (KeyError, ValueError):
        return None
    ok = [f for f in due.get("followups") or [] if f.get("channel") == "email" and f.get("ok")]
    sent = {(f.get("stage"), f.get("n", 0)) for f in ok}
    gap, last = max(settings.reminder_gap_days, 1), settings_max()
    if d - timedelta(days=PENDING_DAYS) <= t < d:
        return None if ("pending", 0) in sent else ("pending", 0)
    if t == d:
        return None if ("due", 0) in sent else ("due", 0)
    if t > d:
        n = sum(1 for s, _ in sent if s == "overdue")
        if n >= last:
            return None
        # every `gap` days after the due date — and never sooner than `gap` days after the last overdue email (catching up
        # after a late opt-in doesn't send on consecutive days)
        lasts = [f["at"].astimezone(IST).date() for f in ok if f.get("stage") == "overdue" and f.get("at")]
        if (t - d).days >= gap * (n + 1) and (not lasts or (t - max(lasts)).days >= gap):
            return "overdue", n + 1
    return None


async def run_owner(owner: dict, at=None) -> dict:
    """One owner's daily pass. Every send is claimed first, so two workers never send the same reminder twice."""
    from .agents.jobs import claim, release
    ws = owner["_id"]
    st = await get_state(ws)
    if not st.get("on") or not plans.has(owner, "auto_reminders"):
        return {"sent": 0, "skipped": "off"}
    if not await approved_current(ws, st):   # the wording changed since it was approved: nothing goes until the owner looks again
        current = (await samples(ws))["hash"]
        if st.get("alerted_hash") != current:
            from .services import notify
            await notify(ws, "Your payment follow-up wording changed (your business name or how-to-pay note). Automatic follow-ups are paused "
                             "until you read and approve it again in Automations.")
            await db().followup_auto.update_one({"_id": ws}, {"$set": {"alerted_hash": current}})
        return {"sent": 0, "skipped": "wording_changed"}
    t = (at or now()).astimezone(IST).date()
    b = await db().businesses.find_one({"owner_id": ws}) or {}
    exclude = set(st.get("exclude") or [])
    sent = failed = 0
    q = {"ws": ws, "status": "due", "auto_stopped": None, "email": {"$nin": [None, ""]},
         "due_date": {"$lte": (t + timedelta(days=PENDING_DAYS)).isoformat()}}
    async for due in db().dues.find(q).sort("due_date", 1).limit(1000):
        if customer_key(due) in exclude or due.get("no_auto"):
            continue
        stage = next_stage(due, t)
        if not stage:
            continue
        kind, n = stage
        key = f"autofu:{due['_id']}:{due['due_date']}:{kind}:{n}"    # a new due date starts a new round
        if not await claim(key):
            continue
        msg = message(kind, n, due, b)
        attachments = []
        if due.get("invoice_id"):
            try:
                inv = await db().quotes.find_one({"_id": due["invoice_id"], "ws": ws})
                if inv:
                    from .brand import accent_for
                    qs = await db().quote_settings.find_one({"_id": ws}) or {}
                    attachments.append((f"{inv['number']}.pdf", invoice_pdf.render(inv, b, qs, await accent_for(ws)), "application/pdf"))
            except Exception as e:  # noqa: BLE001  the reminder still goes, without the PDF
                log.warning("invoice PDF for follow-up failed: %s", e)
        result = await mailer.send(owner=owner, to=due["email"], subject=msg["subject"], text=msg["text"], kind=f"followup_{kind}",
                                   from_name=b.get("name") or "Accounts", reply_to=b.get("email"), attachments=attachments,
                                   ref={"due_id": due["_id"], "stage": kind, "n": n})
        entry = {"stage": kind, "n": n, "channel": "email", "at": at or now(), "ok": result["sent"], "via": result["via"], "ref": result.get("id"),
                 "thread": result.get("thread")}
        await db().dues.update_one({"_id": due["_id"]}, {"$push": {"followups": entry}, **({"$set": {"last_auto_at": now()}} if result["sent"] else {})})
        sent += result["sent"]
        failed += not result["sent"]
        if not result["sent"] and result["via"] in TRY_AGAIN:   # e.g. today's email limit: the same stage is tried again tomorrow
            await release(key)
    return {"sent": sent, "failed": failed}


async def stop(ws: str, due_id: str, reason: str) -> bool:
    r = await db().dues.update_one({"_id": due_id, "ws": ws, "auto_stopped": None}, {"$set": {"auto_stopped": reason, "auto_stopped_at": now()}})
    return bool(r.modified_count)


async def week_line(ws: str) -> str | None:
    """For the morning digest: 'Reminders sent this week: 12 · ₹1.8 L collected after a reminder'."""
    since = now() - timedelta(days=7)
    n, collected = 0, 0
    async for d in db().dues.find({"ws": ws, "followups.at": {"$gte": since}}, {"followups": 1}):
        n += sum(1 for f in d.get("followups") or [] if f.get("ok") and f.get("at") and f["at"] >= since)
    async for d in db().dues.find({"ws": ws, "status": "paid", "paid_at": {"$gte": since}, "followups.ok": True}, {"amount": 1, "followups": 1, "paid_at": 1}):
        if any(f.get("ok") and f.get("at") and f["at"] <= d["paid_at"] for f in d.get("followups") or []):
            collected += int(d.get("amount") or 0)
    approvals = await db().approvals.count_documents({"ws": ws, "kind": "reminder", "status": "sent", "decided_at": {"$gte": since}})
    n += approvals
    if not n:
        return None
    from .sales import short_rupees
    return f"Reminders sent this week: {n}" + (f" · {short_rupees(collected / 100)} collected after a reminder" if collected else "")
