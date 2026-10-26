"""
OwnerDesk v2.3 setup: the hub as it is sold from the 3-day "your time back" event.

What it does (in the same `config` collection as any admin change, so each part can be changed or undone in Admin →
Features & plans, and the code's own defaults — and the tests written against them — stay as they were):

- The menu becomes Start, the 6 engines and Account. Only the features below show in it — his agreed list, plus five that
  were already live in main and stay on (Win-Back, Lead Response, WhatsApp chats, GST set-aside, research).
- Features that other pages need keep working behind the scenes ("show" off).
- Everything else is switched off for everyone. The admin can switch any of it back on.
- All of it is in the Membership plan (₹1,999 a month or ₹19,999 a year, paid outside the hub on Razorpay; the team marks
  the member as paid in Admin → Onboarding). The free plan keeps the Knowledge Hub, the Engine Audit and Plans.
- Paid members get 30 social media images a month; the free plan and event accounts get 7.

It runs once by itself when the server starts (outside tests), and again whenever the admin presses "Apply the v2.3
setup" (Admin → Features & plans). Running it again is safe: it only writes the fields it owns.
"""
import logging

from . import plans
from .db import db, now
from .services import new_id

log = logging.getLogger("v23")

VERSION = "2.3"

# (key, engine, label or None to keep, day override or None): the menu, in order, per section
MENU = [
    ("business_brain", "start", "Knowledge Hub", None),
    ("engine_audit", "start", "Engine Audit", None),
    ("website", "lead", "Website", None),
    ("lead_magnet", "lead", "Lead magnet", None),
    ("revenue_levers", "sales", "Revenue Multiplier", None),
    ("lead_ideas", "sales", "Ways to get more leads", None),
    ("quotations", "sales", "Invoicing", None),
    ("win_back", "sales", "Win-Back", None),                # main's: past customers asked back (kept on with v2.3)
    ("inquiry_desk", "conversion", "Inquiries", None),
    ("crm", "conversion", "CRM", None),
    ("pipeline", "conversion", "Pipeline & Opportunities", None),
    ("lead_response", "conversion", "Lead Response", None),  # main's: kept on with v2.3
    ("whatsapp_ai", "conversion", "WhatsApp chats", None),    # main's: kept on with v2.3
    ("smart_dashboard", "operations", "Dashboard", 0),      # the home page: open from the first minute of Day 1
    ("money_owed", "operations", "Payment follow-up", None),
    ("compliance_calendar", "operations", "Compliance calendar", None),
    ("gst_setaside", "operations", "GST set-aside", None),    # main's: with last month's GST from a bank statement
    ("sop_library", "operations", "SOP Writer", None),
    ("brand_kit", "brand", "Brand kit", None),
    ("social_media", "brand", "Social media", None),
    ("market_research", "brand", "Content & company research", None),   # main's: kept on with v2.3
    ("key_person", "data", "Key-person knowledge", None),
    ("expenses", "data", "Expenses & bank statement", None),
    ("plans_billing", "account", "Plans & billing", None),
    ("connections", "account", "Connections", None),
    ("recordings", "account", "Recordings", None),
]
# Features the menu's pages need (data, automations, drafts), working behind the scenes: on, in the plan, not in the menu.
BEHIND = {
    "home": "start", "leads": "lead", "customers": "conversion", "approvals": "conversion", "follow_up_agent": "conversion",
    "crm_sync": "conversion", "gateway_invoicing": "sales", "quote_followup": "sales", "reminder_agent": "operations",
    "auto_reminders": "operations", "magic_number": "operations", "cash_flow": "operations", "voice_sop": "operations",
    "content_calendar": "brand", "brand_message": "brand", "brochures": "brand", "competitor_watch": "brand", "bank_reader": "data",
    "whatsapp_tool": "account",
}
# opened once the member pays: the brochures and linking Employz.ai. Connecting their own WhatsApp number (whatsapp_tool) is
# open to event accounts too: WhatsApp chats, kept on in main, needs it, and event attendees were promised WhatsApp connections.
PAID_ONLY = ("brochures", "crm_sync")
# names by what they are, not the software behind them (the owner may see these on a locked page or a message)
BEHIND_LABELS = {"crm_sync": "CRM sync", "gateway_invoicing": "Automatic invoices from your payment gateway"}
FREE = ("business_brain", "engine_audit", "plans_billing", "home")   # what an account keeps when its event access ends

PLAN_PATCH = {
    "free": {"images": plans.EVENT_IMAGES_DEFAULT},
    "lite": {"name": "OwnerDesk Membership", "short": "Membership", "price_minor": 199_900, "price_year_minor": 1_999_900, "runs": 150,
             "images": 30, "pitch": "All 6 engines of your business on one hub, with 2 calls a month",
             "promise": "Your time back: the hub runs the routine, you run the business"},
}
OWNED_FIELDS = ("tier", "on", "engine", "show", "paid_only", "label", "order", "day", "teaser")


def desired() -> dict[str, dict]:
    """The v2.3 value of every field this setup owns, for every feature."""
    out: dict[str, dict] = {}
    for i, (key, engine, label, day) in enumerate(MENU):
        p = {"on": True, "show": True, "engine": engine, "order": (i + 1) * 10, "teaser": True,
             "tier": "free" if key in FREE else "lite", "paid_only": key in PAID_ONLY, "day": -1 if day is None else day}
        if label:
            p["label"] = label
        out[key] = p
    for key, engine in BEHIND.items():
        out[key] = {"on": True, "show": False, "engine": engine, "tier": "free" if key in FREE else "lite", "paid_only": key in PAID_ONLY,
                    "day": -1, **({"label": BEHIND_LABELS[key]} if key in BEHIND_LABELS else {})}
    for key in plans.DEFAULT_FEATURES:
        if key not in out:
            out[key] = {"on": False, "show": False}
    return out


def _clean(key: str, patch: dict) -> dict:
    default = plans.DEFAULT_FEATURES[key]
    return {k: v for k, v in patch.items() if default.get(k) != v}


async def state() -> dict:
    ui = await db().config.find_one({"_id": "ui"}) or {}
    return {"applied": bool(ui.get("v23")), "applied_at": (ui.get("v23") or {}).get("at"), "menu": ui.get("menu", "groups"),
            "version": VERSION}


async def apply(by: str = "system", by_phone: str = "") -> dict:
    """Writes the v2.3 setup. Fields it doesn't own (an admin's renamed description, a grant) are kept."""
    d = db()
    feats = (await d.config.find_one({"_id": "features"}) or {}).get("overrides") or {}
    changed = 0
    for key, want in desired().items():
        if key not in plans.DEFAULT_FEATURES:
            continue
        if plans.DEFAULT_FEATURES[key]["core"]:
            want = {k: v for k, v in want.items() if k not in ("tier", "on")}   # core pages stay free and on, whatever happens
        before = dict(feats.get(key) or {})
        after = _clean(key, {**before, **want})
        if after != before:
            changed += 1
        if after:
            feats[key] = after
        else:
            feats.pop(key, None)
    await d.config.update_one({"_id": "features"}, {"$set": {"overrides": feats, "updated_at": now()}}, upsert=True)
    plan_over = (await d.config.find_one({"_id": "plans"}) or {}).get("overrides") or {}
    for tier, patch in PLAN_PATCH.items():
        plan_over[tier] = {**(plan_over.get(tier) or {}), **patch}
    await d.config.update_one({"_id": "plans"}, {"$set": {"overrides": plan_over, "updated_at": now()}}, upsert=True)
    stamp = {"at": now(), "by": by, "version": VERSION}
    await d.config.update_one({"_id": "ui"}, {"$set": {"menu": "engines", "v23": stamp, "updated_at": now()}}, upsert=True)
    await d.config_changes.insert_one({"_id": new_id(), "at": now(), "by": by, "by_phone": by_phone, "kind": "preset", "target": "v23",
                                       "before": None, "after": {"features_changed": changed}, "note": "OwnerDesk v2.3 setup"})
    await plans.load(force=True)
    log.info("v2.3 setup applied by %s: %d features changed", by, changed)
    return {"features_changed": changed, **await state()}


async def apply_once() -> None:
    """At server start: the first time a deployment runs v2.3, switch it to the v2.3 setup. Never again after that, so the
    admin's later changes stay."""
    ui = await db().config.find_one({"_id": "ui"}) or {}
    if not ui.get("v23"):
        await apply("system")
