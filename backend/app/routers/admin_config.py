"""
Admin → Plans & features: every feature on the owner's side, managed from here.

- Move a feature to another plan (drag it to a plan column), switch it off for everyone, rename it, change its description.
- Reorder the menu (drag), move a page to another menu group, choose whether a locked page shows as a teaser.
- Change a plan's name, price, AI runs, team size and how long it lasts (prices apply to new purchases).
- Give one owner a feature until a date (a grant), and take it back.
- Every change is written to a change log and can be undone.
Changes reach every worker within seconds (plans.load) and this worker at once.
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import plans
from ..db import db, now, public
from ..security import require_admin
from ..services import new_id, notify, track

router = APIRouter(prefix="/api/admin/config", dependencies=[Depends(require_admin)])


class FeaturePatch(BaseModel):
    tier: str | None = Field(default=None, pattern="^(free|lite|program|running|growth|office)$")
    group: str | None = Field(default=None, max_length=20)
    label: str | None = Field(default=None, min_length=2, max_length=60)
    what: str | None = Field(default=None, min_length=3, max_length=240)
    order: int | None = Field(default=None, ge=0, le=100_000)
    on: bool | None = None
    teaser: bool | None = None
    # v2.3: the engine it sits under, whether it shows in the menu, closed during a trial, the event day it opens on
    engine: str | None = Field(default=None, pattern="^(start|lead|sales|conversion|operations|brand|data|more|account)$")
    show: bool | None = None
    paid_only: bool | None = None
    day: int | None = Field(default=None, ge=-1, le=3)
    starter: bool | None = None   # in the L0 starter set new signups get (Admin → Onboarding)


class MenuIn(BaseModel):
    """The whole menu after a drag: each group with its pages in order."""
    groups: dict[str, list[str]]


class PlanPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=40)
    short: str | None = Field(default=None, min_length=2, max_length=24)
    price_minor: int | None = Field(default=None, ge=0, le=10_000_000_000)
    runs: int | None = Field(default=None, ge=0, le=1_000_000)
    team_size: int | None = Field(default=None, ge=0, le=500)
    access_days: int | None = Field(default=None, ge=1, le=3650)
    pitch: str | None = Field(default=None, max_length=160)
    promise: str | None = Field(default=None, max_length=160)
    razorpay_plan_id: str | None = Field(default=None, max_length=60, pattern=r"^(plan_[A-Za-z0-9]{6,40})?$")
    # v2.3: social media images a month, extra AI tokens a month for paying members, the yearly price
    images: int | None = Field(default=None, ge=0, le=10_000)
    extra_runs: int | None = Field(default=None, ge=0, le=100_000)
    price_year_minor: int | None = Field(default=None, ge=0, le=10_000_000_000)


class GrantIn(BaseModel):
    user_id: str = Field(min_length=6, max_length=40)
    feature: str = Field(min_length=2, max_length=40)
    days: int = Field(ge=1, le=3650)
    note: str = Field(default="", max_length=200)


# ───────────────────────── storage ─────────────────────────
async def _overrides(doc_id: str) -> dict:
    return (await db().config.find_one({"_id": doc_id}) or {}).get("overrides") or {}


async def _save(doc_id: str, overrides: dict) -> None:
    await db().config.update_one({"_id": doc_id}, {"$set": {"overrides": overrides, "updated_at": now()}}, upsert=True)
    await plans.load(force=True)


async def _log(admin: dict, kind: str, target: str, before, after, note: str = "") -> None:
    await db().config_changes.insert_one({"_id": new_id(), "at": now(), "by": admin["_id"], "by_phone": admin.get("phone", ""),
                                          "kind": kind, "target": target, "before": before, "after": after, "note": note[:200]})


def _clean(key: str, patch: dict) -> dict:
    """Keeps only the fields that differ from the code's default, so 'back to default' leaves no trace."""
    default = plans.DEFAULT_FEATURES[key]
    return {k: v for k, v in patch.items() if default.get(k) != v}


# ───────────────────────── read ─────────────────────────
@router.get("")
async def overview():
    feats = await _overrides("features")
    plan_over = await _overrides("plans")
    features = []
    for key in plans.META:
        v = plans.feature_view(key)
        v["changed"] = sorted((feats.get(key) or {}).keys())
        v["default_tier"] = plans.DEFAULT_FEATURES[key]["tier"]
        v["default_label"] = plans.DEFAULT_FEATURES[key]["label"]
        features.append(v)
    tiers = []
    for t in plans.TIERS:
        p = plans.PLANS[t]
        tiers.append({"tier": t, **{k: v for k, v in p.items()}, "changed": sorted((plan_over.get(t) or {}).keys()),
                      "features": sum(1 for m in plans.META.values() if m["tier"] == t)})
    return {"tiers": tiers, "features": features, "groups": plans.GROUPS,
            # v2.3: the engine menu's sections, and whether the engine menu is on
            "sections": [{"key": k, "label": plans.SECTION_LABEL[k]} for k in plans.SECTIONS], "engines_on": plans.engines_on()}


# ───────────────────────── features ─────────────────────────
@router.patch("/features/{key}")
async def patch_feature(key: str, body: FeaturePatch, admin: dict = Depends(require_admin)):
    if key not in plans.META:
        raise HTTPException(404, "No such feature")
    patch = body.model_dump(exclude_none=True)
    if not patch:
        return plans.feature_view(key)
    if "group" in patch and patch["group"] not in plans.GROUPS:
        raise HTTPException(400, "Pick one of the menu groups")
    if plans.META[key]["core"] and ("tier" in patch and patch["tier"] != "free" or patch.get("on") is False):
        raise HTTPException(400, f"{plans.META[key]['label']} is part of every plan and can't be switched off or moved.")
    feats = await _overrides("features")
    before = dict(feats.get(key) or {})
    after = _clean(key, {**before, **patch})
    if after:
        feats[key] = after
    else:
        feats.pop(key, None)
    await _save("features", feats)
    await _log(admin, "feature", key, before, after)
    return plans.feature_view(key)


@router.post("/features/{key}/reset")
async def reset_feature(key: str, admin: dict = Depends(require_admin)):
    if key not in plans.META:
        raise HTTPException(404, "No such feature")
    feats = await _overrides("features")
    before = feats.pop(key, None) or {}
    await _save("features", feats)
    await _log(admin, "feature", key, before, {}, "Back to default")
    return plans.feature_view(key)


# The v2.1 feature guide and the AI Advantage Membership event promise every feature in the guide on Membership. Five of them sit
# in higher plans by default (an existing test pins those defaults), so the move is an admin setting: one press here, undone
# per feature with "Reset to default".
GUIDE_MEMBERSHIP = ("lead_magnet", "revenue_levers", "pipeline", "quotations", "money_owed")


@router.get("/presets/guide-membership")
async def guide_membership_state():
    return {"features": [{"key": k, "label": plans.META[k]["label"], "tier": plans.META[k]["tier"]} for k in GUIDE_MEMBERSHIP],
            "applied": all(plans.META[k]["tier"] == "lite" for k in GUIDE_MEMBERSHIP)}


@router.post("/presets/guide-membership")
async def apply_guide_membership(admin: dict = Depends(require_admin)):
    """Moves the guide's five higher-plan features into Membership. Features already in Membership or lower are left alone."""
    feats = await _overrides("features")
    moved = []
    for key in GUIDE_MEMBERSHIP:
        if plans.rank(plans.META[key]["tier"]) <= plans.rank("lite"):
            continue
        before = dict(feats.get(key) or {})
        after = _clean(key, {**before, "tier": "lite"})
        feats[key] = after
        moved.append((key, before, after))
    if moved:
        await _save("features", feats)
        for key, before, after in moved:
            await _log(admin, "feature", key, before, after, "Guide features into Membership")
        await track("preset_guide_membership", admin["_id"], moved=[k for k, *_ in moved])
    return {"moved": [k for k, *_ in moved], **await guide_membership_state()}


# v2.2: everything the Membership is sold with — the guide's five, plus the funnel leak finder and the follow-ups (which
# land in the Send list). The code's defaults stay as they are; this moves them for this deployment, undo per feature.
MEMBERSHIP_V22 = GUIDE_MEMBERSHIP + ("funnel_leaks", "follow_up_agent", "approvals")


def _preset_state(keys: tuple) -> dict:
    return {"features": [{"key": k, "label": plans.META[k]["label"], "tier": plans.META[k]["tier"],
                          "above_membership": plans.rank(plans.META[k]["tier"]) > plans.rank("lite")} for k in keys],
            "applied": all(plans.rank(plans.META[k]["tier"]) <= plans.rank("lite") for k in keys)}


@router.get("/presets/v23")
async def v23_state():
    from .. import v23
    return await v23.state()


@router.post("/presets/v23")
async def apply_v23(admin: dict = Depends(require_admin)):
    """The OwnerDesk v2.3 setup: the engine menu, the hidden features, Membership at ₹1,999 / ₹19,999. Safe to run again."""
    from .. import v23
    out = await v23.apply(admin["_id"], admin.get("phone", ""))
    await track("preset_v23", admin["_id"], changed=out["features_changed"])
    return out


class UiPatch(BaseModel):
    """v2.3: the account links every member sees (Account section and Plans & billing) and the menu style."""
    menu: str | None = Field(default=None, pattern="^(groups|engines)$")
    community_link: str | None = Field(default=None, max_length=300, pattern=r"^(https://\S+)?$")
    support_email: str | None = Field(default=None, max_length=120, pattern=r"^([^@\s]+@[^@\s]+\.[^@\s]+)?$")
    pay_monthly_link: str | None = Field(default=None, max_length=300, pattern=r"^(https://\S+)?$")
    pay_yearly_link: str | None = Field(default=None, max_length=300, pattern=r"^(https://\S+)?$")
    welcome_full_hub: bool | None = None   # new signups get the whole hub; owners who already have it keep it
    welcome_starter: bool | None = None    # new signups get only the L0 starter set (wins over the full hub)


@router.get("/ui")
async def ui_settings():
    return {k: plans.UI.get(k) for k in plans.UI_EDITABLE}


@router.patch("/ui")
async def patch_ui(body: UiPatch, admin: dict = Depends(require_admin)):
    patch = body.model_dump(exclude_none=True)
    if patch:
        before = {k: plans.UI.get(k) for k in patch}
        await db().config.update_one({"_id": "ui"}, {"$set": {**patch, "updated_at": now()}}, upsert=True)
        await plans.load(force=True)
        await _log(admin, "ui", "ui", before, patch)
    return await ui_settings()


@router.get("/presets/membership-v22")
async def membership_v22_state():
    return _preset_state(MEMBERSHIP_V22)


@router.post("/presets/membership-v22")
async def apply_membership_v22(admin: dict = Depends(require_admin)):
    """Moves every v2.2 Membership feature that sits in a higher plan into Membership. Ones already there are left alone."""
    feats = await _overrides("features")
    moved = []
    for key in MEMBERSHIP_V22:
        if plans.rank(plans.META[key]["tier"]) <= plans.rank("lite"):
            continue
        before = dict(feats.get(key) or {})
        after = _clean(key, {**before, "tier": "lite"})
        feats[key] = after
        moved.append((key, before, after))
    if moved:
        await _save("features", feats)
        for key, before, after in moved:
            await _log(admin, "feature", key, before, after, "v2.2 Membership features")
        await track("preset_membership_v22", admin["_id"], moved=[k for k, *_ in moved])
    return {"moved": [k for k, *_ in moved], **await membership_v22_state()}


# For a live event, on top of the v2.2 Membership: the agents that sit in higher plans, so every pack made in the room has
# them. WhatsApp chats brings the owner's own number (and with it Lead Response's first reply); its two agents wait for the
# owner's own switch on Membership (agents.jobs.OPT_IN). Moved back after the event in one tap.
EVENT_EXTRAS = ("win_back", "market_research", "whatsapp_ai", "whatsapp_tool", "lead_response")   # whatsapp_tool: connecting their number


@router.get("/presets/event-extras")
async def event_extras_state():
    return _preset_state(EVENT_EXTRAS)


@router.post("/presets/event-extras")
async def apply_event_extras(admin: dict = Depends(require_admin)):
    """Moves the event extras into Membership. Ones already in Membership or below are left alone."""
    feats = await _overrides("features")
    moved = []
    for key in EVENT_EXTRAS:
        if plans.rank(plans.META[key]["tier"]) <= plans.rank("lite"):
            continue
        before = dict(feats.get(key) or {})
        feats[key] = _clean(key, {**before, "tier": "lite"})
        moved.append((key, before, feats[key]))
    if moved:
        await _save("features", feats)
        for key, before, after in moved:
            await _log(admin, "feature", key, before, after, "Live event extras into Membership")
        await track("preset_event_extras", admin["_id"], moved=[k for k, *_ in moved])
    return {"moved": [k for k, *_ in moved], **await event_extras_state()}


@router.post("/presets/event-extras/undo")
async def undo_event_extras(admin: dict = Depends(require_admin)):
    """After the event: the extras go back to the plan they sit on in the code. Their other changes (name, on/off) stay."""
    feats = await _overrides("features")
    back = []
    for key in EVENT_EXTRAS:
        before = dict(feats.get(key) or {})
        if "tier" not in before:
            continue
        after = {k: v for k, v in before.items() if k != "tier"}
        if after:
            feats[key] = after
        else:
            feats.pop(key, None)
        back.append((key, before, after))
    if back:
        await _save("features", feats)
        for key, before, after in back:
            await _log(admin, "feature", key, before, after, "Live event extras back to their plans")
    return {"moved_back": [k for k, *_ in back], **await event_extras_state()}


@router.put("/menu")
async def save_menu(body: MenuIn, admin: dict = Depends(require_admin)):
    """After a drag in the menu editor: every page's group and position."""
    feats = await _overrides("features")
    seen: set[str] = set()
    before, after = {}, {}
    for group, keys in body.groups.items():
        if group not in plans.GROUPS:
            raise HTTPException(400, f"Unknown menu group: {group}")
        for i, key in enumerate(keys[:200]):
            if key not in plans.META or key in seen or not plans.META[key]["path"]:
                continue
            seen.add(key)
            before[key] = {k: (feats.get(key) or {}).get(k) for k in ("group", "order") if k in (feats.get(key) or {})}
            merged = _clean(key, {**(feats.get(key) or {}), "group": group, "order": (i + 1) * 10})
            if merged:
                feats[key] = merged
            else:
                feats.pop(key, None)
            after[key] = {k: merged.get(k) for k in ("group", "order") if k in merged}
    await _save("features", feats)
    await _log(admin, "menu", "menu", before, after, f"{len(seen)} pages")
    return {"ok": True, "moved": len(seen)}


# ───────────────────────── plans ─────────────────────────
@router.patch("/plans/{tier}")
async def patch_plan(tier: str, body: PlanPatch, admin: dict = Depends(require_admin)):
    if tier not in plans.TIERS:
        raise HTTPException(404, "No such plan")
    patch = body.model_dump(exclude_none=True)
    if tier == "free" and patch.get("price_minor"):
        raise HTTPException(400, "The free plan stays free.")
    if "access_days" in patch and plans.PLANS[tier]["billing"] != "one_time":
        raise HTTPException(400, "Only one-time programs have a length in days. Membership renews monthly.")
    if "razorpay_plan_id" in patch and tier != "lite":
        raise HTTPException(400, "Only Membership uses a Razorpay plan id.")
    if tier == "lite" and "price_minor" in patch and patch["price_minor"] != plans.PLANS["lite"]["price_minor"] and "razorpay_plan_id" not in patch:
        raise HTTPException(400, "A new Membership price needs a new Razorpay plan: create it in Razorpay at the new price and paste its plan id here too.")
    over = await _overrides("plans")
    before = dict(over.get(tier) or {})
    merged = {**before, **patch}
    merged = {k: v for k, v in merged.items() if plans.DEFAULT_PLANS[tier].get(k) != v}
    if merged:
        over[tier] = merged
    else:
        over.pop(tier, None)
    await _save("plans", over)
    await _log(admin, "plan", tier, before, merged)
    return {"tier": tier, **plans.PLANS[tier]}


@router.post("/plans/{tier}/reset")
async def reset_plan(tier: str, admin: dict = Depends(require_admin)):
    if tier not in plans.TIERS:
        raise HTTPException(404, "No such plan")
    over = await _overrides("plans")
    before = over.pop(tier, None) or {}
    await _save("plans", over)
    await _log(admin, "plan", tier, before, {}, "Back to default")
    return {"tier": tier, **plans.PLANS[tier]}


# ───────────────────────── grants: one feature for one owner ─────────────────────────
@router.get("/grants")
async def list_grants():
    out = []
    async for u in db().users.find({"grants": {"$exists": True, "$ne": {}}}, {"phone": 1, "grants": 1}).limit(500):
        b = await db().businesses.find_one({"owner_id": u["_id"]}, {"name": 1}) or {}
        for key, until in (u.get("grants") or {}).items():
            if isinstance(until, datetime) and key in plans.META:
                out.append({"user_id": u["_id"], "phone": u.get("phone", ""), "business": b.get("name", ""), "feature": key,
                            "label": plans.META[key]["label"], "until": until.isoformat(), "active": until > now()})
    return sorted(out, key=lambda g: g["until"], reverse=True)


@router.post("/grants")
async def add_grant(body: GrantIn, admin: dict = Depends(require_admin)):
    if body.feature not in plans.META:
        raise HTTPException(404, "No such feature")
    user = await db().users.find_one({"_id": body.user_id})
    if not user or user.get("team_of"):
        raise HTTPException(404, "Owner not found")
    before = (user.get("grants") or {}).get(body.feature)
    until = now() + timedelta(days=body.days)
    await db().users.update_one({"_id": user["_id"]}, {"$set": {f"grants.{body.feature}": until}})
    await _log(admin, "grant", f"{user['_id']}:{body.feature}", before.isoformat() if isinstance(before, datetime) else None,
               until.isoformat(), body.note)
    await notify(user["_id"], f"Added to your hub: {plans.META[body.feature]['label']}, until {until.strftime('%d %b %Y')}.")
    await track("grant", user["_id"], feature=body.feature, days=body.days, by=admin["_id"])
    return {"ok": True, "until": until.isoformat()}


@router.delete("/grants/{user_id}/{feature}")
async def remove_grant(user_id: str, feature: str, admin: dict = Depends(require_admin)):
    user = await db().users.find_one({"_id": user_id}, {"grants": 1})
    if not user:
        raise HTTPException(404, "Owner not found")
    before = (user.get("grants") or {}).get(feature)
    await db().users.update_one({"_id": user_id}, {"$unset": {f"grants.{feature}": ""}})
    await _log(admin, "grant", f"{user_id}:{feature}", before.isoformat() if isinstance(before, datetime) else None, None)
    return {"ok": True}


# ───────────────────────── change log ─────────────────────────
@router.get("/changes")
async def changes(limit: int = 100):
    out = []
    async for c in db().config_changes.find().sort("at", -1).limit(max(1, min(limit, 300))):
        v = public(c)
        target = c["target"]
        v["target_label"] = (plans.META[target]["label"] if c["kind"] == "feature" and target in plans.META
                             else plans.PLANS[target]["name"] if c["kind"] == "plan" and target in plans.PLANS
                             else plans.META.get(target.split(":")[-1], {}).get("label", target) if c["kind"] == "grant" else target)
        out.append(v)
    return out


@router.post("/changes/{change_id}/undo")
async def undo(change_id: str, admin: dict = Depends(require_admin)):
    c = await db().config_changes.find_one({"_id": change_id})
    if not c:
        raise HTTPException(404, "Not found")
    if c.get("undone_at"):
        raise HTTPException(409, "This change was already undone.")
    kind, target, before = c["kind"], c["target"], c.get("before")
    if kind == "feature":
        feats = await _overrides("features")
        if before:
            feats[target] = before
        else:
            feats.pop(target, None)
        await _save("features", feats)
    elif kind == "menu":
        feats = await _overrides("features")
        for key, prev in (before or {}).items():
            cur = {k: v for k, v in (feats.get(key) or {}).items() if k not in ("group", "order")}
            cur.update({k: v for k, v in (prev or {}).items() if v is not None})
            if cur:
                feats[key] = cur
            else:
                feats.pop(key, None)
        await _save("features", feats)
    elif kind == "plan":
        over = await _overrides("plans")
        if before:
            over[target] = before
        else:
            over.pop(target, None)
        await _save("plans", over)
    elif kind == "grant":
        user_id, feature = target.split(":", 1)
        if before:
            await db().users.update_one({"_id": user_id}, {"$set": {f"grants.{feature}": datetime.fromisoformat(before)}})
        else:
            await db().users.update_one({"_id": user_id}, {"$unset": {f"grants.{feature}": ""}})
    await db().config_changes.update_one({"_id": change_id}, {"$set": {"undone_at": now(), "undone_by": admin["_id"]}})
    await _log(admin, kind, target, c.get("after"), before, "Undo")
    return {"ok": True}


async def ensure_indexes() -> None:
    await db().config_changes.create_index([("at", -1)])
