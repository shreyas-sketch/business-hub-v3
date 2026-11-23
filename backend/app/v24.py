"""
OwnerDesk v2.4 setup: the growth features, on top of the v2.3 setup.

- Find new customers (Lead engine): businesses near the owner that fit, and a first message drafted for each that the owner
  sends from his own WhatsApp. Paid members only.
- Call coach (Sales engine): a call recording or a pasted call, scored out of 10 with 3 fixes. Paid members only.
- AI team (Start): every AI role, what it did today and this month, counted from the owner's own records.
- Working behind other pages: Lead research (a brief on every new inquiry), Pipeline fills itself (a conversation becomes the
  deal's note, next step and follow-up date), the Weekly growth line and Quick answers (a Dashboard tile).

Like v2.3 (app/v23.py) it writes only admin config overrides for its own features, so the code defaults, the v2.3 setup and
every test written against them stay as they were. It runs once by itself when the server starts (outside tests), after the
v2.3 setup, and again whenever the admin presses "Apply" in Admin → Features & plans. It never touches another feature.
"""
import logging

from . import plans
from .db import db, now
from .services import new_id

log = logging.getLogger("v24")

VERSION = "2.4"

# (key, engine, label, order): menu features. Orders sit between the v2.3 ones (v23.MENU gives (index + 1) * 10).
MENU = [
    ("ai_team", "start", "AI team", 25),             # after Engine Audit (20)
    ("prospects", "lead", "Find new customers", 45),  # after Lead magnet (40)
    ("call_coach", "sales", "Call coach", 75),        # after Invoicing (70)
]
# working behind other pages (show off): the Inquiries and Pipeline pages, the Dashboard and the owner's WhatsApp
BEHIND = {"lead_brief": "conversion", "deal_autofill": "conversion", "growth_weekly": "start", "quick_answers": "start"}
PAID_ONLY = ("prospects", "call_coach")
OWNED = tuple(k for k, *_ in MENU) + tuple(BEHIND)


def desired() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for key, engine, label, order in MENU:
        out[key] = {"on": True, "show": True, "engine": engine, "label": label, "order": order, "teaser": True, "tier": "lite",
                    "paid_only": key in PAID_ONLY, "day": -1}
    for key, engine in BEHIND.items():
        out[key] = {"on": True, "show": False, "engine": engine, "tier": "lite", "paid_only": False, "day": -1}
    return out


def _clean(key: str, patch: dict) -> dict:
    default = plans.DEFAULT_FEATURES[key]
    return {k: v for k, v in patch.items() if default.get(k) != v}


async def state() -> dict:
    ui = await db().config.find_one({"_id": "ui"}) or {}
    return {"applied": bool(ui.get("v24")), "applied_at": (ui.get("v24") or {}).get("at"), "version": VERSION,
            "features": [{"key": k, "label": plans.META[k]["label"], "on": plans.META[k]["on"]} for k in OWNED if k in plans.META]}


async def apply(by: str = "system", by_phone: str = "") -> dict:
    """Writes the v2.4 setup for its own features only. Fields it doesn't own (an admin's renamed description, a grant) are kept."""
    d = db()
    feats = (await d.config.find_one({"_id": "features"}) or {}).get("overrides") or {}
    changed = 0
    for key, want in desired().items():
        if key not in plans.DEFAULT_FEATURES:
            continue
        before = dict(feats.get(key) or {})
        after = _clean(key, {**before, **want})
        if after != before:
            changed += 1
        if after:
            feats[key] = after
        else:
            feats.pop(key, None)
    await d.config.update_one({"_id": "features"}, {"$set": {"overrides": feats, "updated_at": now()}}, upsert=True)
    stamp = {"at": now(), "by": by, "version": VERSION}
    await d.config.update_one({"_id": "ui"}, {"$set": {"v24": stamp, "updated_at": now()}}, upsert=True)
    await d.config_changes.insert_one({"_id": new_id(), "at": now(), "by": by, "by_phone": by_phone, "kind": "preset", "target": "v24",
                                       "before": None, "after": {"features_changed": changed}, "note": "OwnerDesk v2.4 setup"})
    await plans.load(force=True)
    log.info("v2.4 setup applied by %s: %d features changed", by, changed)
    return {"features_changed": changed, **await state()}


async def apply_once() -> None:
    """At server start, after the v2.3 setup: the first time a deployment runs v2.4, switch its features on. Never again after
    that, so the admin's later changes stay."""
    ui = await db().config.find_one({"_id": "ui"}) or {}
    if ui.get("v23") and not ui.get("v24"):
        await apply("system")
