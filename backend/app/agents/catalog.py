"""
Every agent the hub has, in one registry. An agent is a spec (what it does, when it runs, which plan feature
unlocks it) plus a runner. Built-in runners are attached in `runners.py`; other modules add their own with `register`.

    register("standup", {"name": "Standup agent", "what": "...", "when": "Every weekday at 9am",
                         "feature": "agentic_office", "trigger": "schedule", "customer_facing": False,
                         "schedule": {"every": "day", "at": "09:00", "grace_hours": 3}}, runner)

A runner is `async def runner(owner: dict, run: dict) -> str | tuple[str, str | None]`, where
run = {"at": datetime (UTC), "slot": str | None, "job": dict | None, "spec": Spec}. It returns a one-line note
for the activity log (optionally with a reference id), or raises `Skip("why")` when there was nothing to do.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from ..config import settings
from ..db import IST

TRIGGERS = ("lead", "won", "schedule", "meeting")
Runner = Callable[[dict, dict], Awaitable[Any]]


class Skip(Exception):
    """Raised by a runner when there was nothing to do (the lead was already won, no profile yet...)."""


@dataclass
class Spec:
    key: str
    name: str
    what: str                       # one plain sentence
    when: str                       # plain text, shown on the Agents page
    feature: str                    # plans.FEATURES key that unlocks it
    trigger: str                    # lead | won | schedule | meeting
    customer_facing: bool = False
    approvals: bool = False         # its messages wait in Approvals unless the owner lets it act (agents_act)
    info_only: bool = False         # run by another module; shown for information, no switch here
    schedule: dict | None = None    # {"every": "day"|"week"|"month", "at": "HH:MM", "weekday": 0-6, "day": 1-28, "days": [10, 20], "grace_hours": n}
    runner: Runner | None = field(default=None, repr=False)
    # owner-only scheduled checks: runs only for owners who have set the feature up (a bank balance, a target...), so owners
    # who never used it get no "skipped" lines in their activity
    ready: Callable[[list[dict]], Awaitable[set]] | None = field(default=None, repr=False)   # owners → ids of those ready (one query)
    # where the Agents page lists it: "team" (the AI team), "alerts" (checks that only ever message the owner) or
    # "approve_once" (customer-facing, switched on once after the owner approves the wording on its own panel)
    group: str = "team"


AGENTS: dict[str, Spec] = {}


def register(key: str, spec: dict | Spec, runner: Runner | None = None) -> Spec:
    """Adds (or replaces) an agent. `spec` is a Spec or a dict of its fields (without `key`)."""
    s = spec if isinstance(spec, Spec) else Spec(key=key, **spec)
    s.key = key
    if s.trigger not in TRIGGERS:
        raise ValueError(f"trigger must be one of {TRIGGERS}")
    if s.trigger == "schedule" and not s.schedule:
        raise ValueError("a scheduled agent needs a schedule")
    if runner is not None:
        s.runner = runner
    AGENTS[key] = s
    return s


def attach(key: str, runner: Runner) -> None:
    AGENTS[key].runner = runner


def get(key: str) -> Spec | None:
    return AGENTS.get(key)


# ───────────────────────── schedules (all in India time) ─────────────────────────
def _hm(schedule: dict) -> tuple[int, int]:
    h, m = (schedule.get("at") or "00:00").split(":")
    return int(h), int(m)


def _month_back(d: datetime, day: int) -> datetime:
    first = d.replace(day=1) - timedelta(days=1)
    return first.replace(day=min(day, 28))


def last_occurrence(schedule: dict, at: datetime) -> datetime:
    """The most recent scheduled moment at or before `at` (UTC result)."""
    local = at.astimezone(IST)
    h, m = _hm(schedule)
    every = schedule.get("every", "day")
    if every == "week":
        wd = int(schedule.get("weekday", 0))
        cand = (local - timedelta(days=(local.weekday() - wd) % 7)).replace(hour=h, minute=m, second=0, microsecond=0)
        if cand > local:
            cand -= timedelta(days=7)
    elif every == "month" and schedule.get("days"):   # several days a month, e.g. the 10th and the 20th
        days = sorted({min(int(x), 28) for x in schedule["days"]})
        this = [local.replace(day=d, hour=h, minute=m, second=0, microsecond=0) for d in days]
        past = [c for c in this if c <= local]
        cand = past[-1] if past else _month_back(local, days[-1]).replace(hour=h, minute=m, second=0, microsecond=0)
    elif every == "month":
        day = min(int(schedule.get("day", 1)), 28)
        cand = local.replace(day=day, hour=h, minute=m, second=0, microsecond=0)
        if cand > local:
            cand = _month_back(cand, day)
    else:
        cand = local.replace(hour=h, minute=m, second=0, microsecond=0)
        if cand > local:
            cand -= timedelta(days=1)
    return cand.astimezone(timezone.utc)


def next_occurrence(schedule: dict, at: datetime) -> datetime:
    """The next scheduled moment strictly after `at` (UTC result)."""
    last = last_occurrence(schedule, at).astimezone(IST)
    every = schedule.get("every", "day")
    if every == "week":
        nxt = last + timedelta(days=7)
    elif every == "month" and schedule.get("days"):
        days = sorted({min(int(x), 28) for x in schedule["days"]})
        later = [d for d in days if d > last.day]
        nxt = last.replace(day=later[0]) if later else (last.replace(day=28) + timedelta(days=5)).replace(day=days[0])
    elif every == "month":
        nxt = (last.replace(day=28) + timedelta(days=5)).replace(day=min(int(schedule.get("day", 1)), 28))
    else:
        nxt = last + timedelta(days=1)
    return nxt.astimezone(timezone.utc)


def slot_key(schedule: dict, occurrence: datetime) -> str:
    """One run per slot: the date (daily), the ISO week (weekly) or the month (monthly), in India time."""
    local = occurrence.astimezone(IST)
    every = schedule.get("every", "day")
    if every == "week":
        return local.strftime("%G-W%V")
    if every == "month":
        return local.strftime("%Y-%m-%d") if schedule.get("days") else local.strftime("%Y-%m")
    return local.strftime("%Y-%m-%d")


def due_slot(schedule: dict, at: datetime) -> tuple[str, datetime] | None:
    """(slot, occurrence) when `at` falls inside the run window that starts at the last occurrence, else None."""
    occ = last_occurrence(schedule, at)
    if at - occ >= timedelta(hours=float(schedule.get("grace_hours", 3))):
        return None
    return slot_key(schedule, occ), occ


# ───────────────────────── the built-in agents ─────────────────────────
def _days_text(days: tuple) -> str:
    d = [str(x) for x in days] or ["1"]
    return (", ".join(d[:-1]) + " and " + d[-1]) if len(d) > 1 else d[0]


# Action Program
register("followup", {
    "name": "Follow-up automation", "feature": "follow_up_agent", "trigger": "lead", "customer_facing": True, "approvals": True,
    "what": "Writes a short, polite follow-up for every lead that hasn't become a customer yet — ready in your Send list.",
    "when": f"Day {_days_text(settings.followup_days)} after a new lead, between 10am and 7pm; stops when the lead is won or lost"})
register("review_request", {
    "name": "Review requests", "feature": "review_agent", "trigger": "won", "customer_facing": True, "approvals": True,
    "what": "Asks every won customer for a review, with your review link — ready in your Send list.",
    "when": f"{settings.review_delay_days} days after a lead is marked won"})
register("digest", {
    "name": "Morning digest", "feature": "digest_agent", "trigger": "schedule",
    "what": "Sends you one WhatsApp line each morning: new leads, replies waiting, the Send list, money overdue and tasks due.",
    "when": "Every day at 8:30am",
    "schedule": {"every": "day", "at": "08:30", "grace_hours": 3}})
# Membership
register("week_plan", {
    "name": "Your week", "feature": "week", "trigger": "schedule",
    "what": "Plans your week every Monday: 7 posts and 3 actions from your Magic Number and your gaps, and tells you on WhatsApp.",
    "when": "Every Monday at 8am",
    "schedule": {"every": "week", "weekday": 0, "at": "08:00", "grace_hours": 36}})
register("calendar_auto", {
    "name": "Content calendar", "feature": "content_calendar", "trigger": "schedule",
    "what": "Writes next month's posts, with the festivals and occasions in it, before the month starts.",
    "when": "On the 25th of every month at 9am",
    "schedule": {"every": "month", "day": 25, "at": "09:00", "grace_hours": 72}})
# Running the Business
register("payment_reminder", {
    "name": "Payment reminders", "feature": "reminder_agent", "trigger": "schedule", "customer_facing": True, "approvals": True,
    "what": "Prepares a polite reminder for money past its due date, with how to pay you — ready in your Send list.",
    "when": f"Every day at 10am for money past its due date — every {settings.reminder_gap_days} days, at most 4 times",
    "schedule": {"every": "day", "at": "10:00", "grace_hours": 9}})
register("quote_followup", {
    "name": "Quote follow-ups", "feature": "quote_followup", "trigger": "schedule", "customer_facing": True, "approvals": True,
    "what": "Follows up every open quotation on day 2, 5 and 10 — ready in your Send list. Stops when it's accepted or lost.",
    "when": "Every day at 11am",
    "schedule": {"every": "day", "at": "11:00", "grace_hours": 8}})
register("customer_desk", {
    "name": "Customer desk", "feature": "customer_desk", "trigger": "schedule", "customer_facing": True, "approvals": True,
    "what": "Reorder reminders, occasion greetings, referral requests and check-ins for quiet customers — ready in your Send list.",
    "when": "Every day at 11:30am",
    "schedule": {"every": "day", "at": "11:30", "grace_hours": 8}})
# Growth Mentorship
register("instant_reply", {
    "name": "Instant reply", "feature": "whatsapp_ai", "trigger": "lead", "customer_facing": True,
    "what": "Replies to every website inquiry at once from your own WhatsApp number, so the chat starts there.",
    "when": "The moment a customer sends an inquiry (once a day per number)"})
register("wa_sales", {
    "name": "WhatsApp replies", "feature": "whatsapp_ai", "trigger": "lead", "customer_facing": True, "info_only": True,
    "what": "Answers every WhatsApp message on your number, 24×7, and hands hot or unhappy customers to a person.",
    "when": "Whenever a customer writes; switch it off on the WhatsApp chats page"})
register("telecaller", {
    "name": "AI Telecaller", "feature": "voice_agent", "trigger": "lead", "customer_facing": True,
    "what": "Calls every new lead within minutes, in calling hours, and saves a summary of the call on the lead.",
    "when": "A few minutes after a new inquiry, in calling hours"})
register("recall", {
    "name": "Re-call old leads", "feature": "voice_agent", "trigger": "schedule", "customer_facing": True,
    "what": "Every week, calls up to 20 leads that went quiet over a month ago and are not won or lost.",
    "when": "Every Monday at 11am",
    "schedule": {"every": "week", "weekday": 0, "at": "11:00", "grace_hours": 8}})
register("ceo_report", {
    "name": "Monday CEO report", "feature": "control_room", "trigger": "schedule",
    "what": "Your week on one WhatsApp message: target vs actual, pipeline, chats, calls and money.",
    "when": "Every Monday at 9am",
    "schedule": {"every": "week", "weekday": 0, "at": "09:00", "grace_hours": 8}})
register("monthly_review", {
    "name": "Monthly review", "feature": "monthly_review", "trigger": "schedule",
    "what": "Reads last month's leads, customers, goals, money and tasks, and writes an honest review with next steps.",
    "when": "On the 1st of every month at 9am",
    "schedule": {"every": "month", "day": 1, "at": "09:00", "grace_hours": 72}})
register("results", {
    "name": "AI results", "feature": "results_report", "trigger": "schedule",
    "what": "On the 1st, tells you what your AI staff did last month: hours saved, leads handled, calls, money collected.",
    "when": "On the 1st of every month at 10am",
    "schedule": {"every": "month", "day": 1, "at": "10:00", "grace_hours": 72}})
register("meeting_actions", {
    "name": "Meeting actions", "feature": "meetings", "trigger": "meeting", "info_only": True,
    "what": "Turns a meeting's notes into action items with an owner and a due date.",
    "when": "When you process a meeting's notes on the Meetings page"})
