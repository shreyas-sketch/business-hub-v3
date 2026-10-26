"""
Smart Dashboard (v2.1, 3.2) — the owner's landing page once the Knowledge Hub is 60% complete. One dashboard, built from
the widget registry in app/dashboard_widgets.py; the old Control Room is this dashboard with the AI team tile on.

GET  /api/dashboard          the tiles this person can see (computed on the server, cached 60 s per workspace) + teasers
PUT  /api/dashboard/layout   the owner's chosen tiles and order          dashboard_layouts {_id: ws, order, hidden}
Admin → Features & plans → Dashboard widgets: each tile on/off for everyone, and its default order   config {_id: "dashboard_widgets"}
"""
import asyncio
import logging
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import dashboard_cache, plans
from ..config import settings
from ..context import Ctx, feature
from ..plans import ROLES
from ..dashboard_widgets import REGISTRY, Widget
from ..db import IST, db, now
from ..security import require_admin

router = APIRouter(prefix="/api/dashboard")
admin = APIRouter(prefix="/api/admin/dashboard-widgets", dependencies=[Depends(require_admin)])
log = logging.getLogger("dashboard")
LANDING_AT = 60    # % of the Knowledge Hub after which the dashboard becomes the landing page


async def admin_settings() -> dict:
    return (await db().config.find_one({"_id": "dashboard_widgets"}) or {}).get("widgets") or {}


def _role_for(w: Widget) -> str:
    roles = [plans.META[f]["role"] for f in w.features if f in plans.META] or ["manager"]
    return min(roles, key=ROLES.index)


def state_of(owner: dict, w: Widget) -> str | None:
    """on | teaser | None (hidden: switched off, or locked without a teaser)."""
    if not w.features or any(plans.has(owner, f) for f in w.features):
        return "on"
    m = plans.META.get(w.features[0])
    return "teaser" if m and m["on"] and m["teaser"] else None


async def _compute(w: Widget, ws: str, owner: dict, t) -> dict:
    try:
        return await w.compute(ws, owner, t)
    except Exception as e:  # noqa: BLE001  one tile failing never breaks the dashboard
        log.warning("dashboard tile %s failed: %s", w.key, e)
        return {"error": "This tile couldn't load just now."}


async def compute(owner: dict, keys: list[str]) -> tuple[dict, bool, int]:
    """Every unlocked tile's numbers, from the 60-second cache when fresh. Returns (data, cached, milliseconds)."""
    ws = owner["_id"]
    hit = dashboard_cache.get(ws, settings.dashboard_cache_seconds)
    if hit is not None and all(k in hit for k in keys):
        return hit, True, 0
    started = time.perf_counter()
    t = now().astimezone(IST).date()
    results = await asyncio.gather(*[_compute(REGISTRY[k], ws, owner, t) for k in keys])
    data = dict(zip(keys, results))
    dashboard_cache.put(ws, data)
    return data, False, round((time.perf_counter() - started) * 1000)


def _ordered(admin_cfg: dict) -> list[Widget]:
    on = [w for w in REGISTRY.values() if (admin_cfg.get(w.key) or {}).get("on", True)]
    return sorted(on, key=lambda w: ((admin_cfg.get(w.key) or {}).get("order", w.order), w.order))


@router.get("")
async def get_dashboard(ctx: Ctx = Depends(feature("smart_dashboard", "manager"))):
    admin_cfg = await admin_settings()
    layout = await db().dashboard_layouts.find_one({"_id": ctx.ws}) or {}
    hidden = set(layout.get("hidden") or [])
    tiles = []
    for w in _ordered(admin_cfg):
        st = state_of(ctx.owner, w)
        if st and ctx.at_least(_role_for(w)):
            tiles.append((w, st))
    order = {k: i for i, k in enumerate(layout.get("order") or [])}
    tiles.sort(key=lambda x: (x[1] == "teaser", order.get(x[0].key, 1000)))   # unlocked tiles first, in the owner's order
    shown = [(w, st) for w, st in tiles if w.key not in hidden]
    data, cached, ms = await compute(ctx.owner, [w.key for w, st in shown if st == "on"])   # hidden tiles aren't computed
    out = []
    for w, st in shown:
        tile = {"key": w.key, "title": w.title, "size": w.size, "link": w.link, "what": w.what, "state": st}
        if st == "on":
            d = data.get(w.key) or {}
            tile.update(data=d, empty=bool(d.get("empty")), empty_state=w.empty)
        else:
            f = w.features[0]
            tier = plans.tier_of(f)
            tile.update(feature=f, tier=tier, tier_name=plans.PLANS[tier]["name"], feature_label=plans.label_of(f))
            if plans.day_locked(ctx.owner, f):   # v2.3: at the live event, a tile whose engine opens later says which day
                tile.update(opens_day=plans.feature_day(f))
        out.append(tile)
    return {"tiles": out, "hidden": [{"key": w.key, "title": w.title, "state": st} for w, st in tiles if w.key in hidden],
            "cached": cached, "compute_ms": ms, "refresh_seconds": settings.dashboard_cache_seconds, "customised": bool(layout)}


class LayoutIn(BaseModel):
    order: list[str] = Field(default_factory=list, max_length=40)
    hidden: list[str] = Field(default_factory=list, max_length=40)


@router.put("/layout")
async def save_layout(body: LayoutIn, ctx: Ctx = Depends(feature("smart_dashboard", "manager"))):
    bad = [k for k in body.order + body.hidden if k not in REGISTRY]
    if bad:
        raise HTTPException(400, f"Unknown tile: {bad[0]}")
    if "today" in body.hidden:
        raise HTTPException(400, "The Today tile always stays on.")
    await db().dashboard_layouts.update_one({"_id": ctx.ws}, {"$set": {"order": list(dict.fromkeys(body.order)), "hidden": list(dict.fromkeys(body.hidden)),
                                                                       "updated_at": now(), "by": ctx.actor}}, upsert=True)
    return await get_dashboard(ctx)


@router.delete("/layout")
async def reset_layout(ctx: Ctx = Depends(feature("smart_dashboard", "manager"))):
    await db().dashboard_layouts.delete_one({"_id": ctx.ws})
    return await get_dashboard(ctx)


async def landing_for(owner: dict, role: str, knowledge_percent: int) -> str:
    """Where an owner lands after login: Home (onboarding steps) until the Knowledge Hub is 60% complete, then the dashboard.
    v2.3 (engine menu): the dashboard straight away; once event access has ended without paying, Plans & billing."""
    if plans.engines_on() and ROLES.index(role) >= ROLES.index("manager"):
        return "/dashboard" if plans.has(owner, "smart_dashboard") else "/plans"
    if plans.has(owner, "smart_dashboard") and ROLES.index(role) >= ROLES.index("manager") and knowledge_percent >= LANDING_AT:
        return "/dashboard"
    return "/home"


# ───────────────────────── Admin → Dashboard widgets ─────────────────────────
@admin.get("")
async def admin_widgets():
    cfg = await admin_settings()
    rows = []
    for w in _ordered({k: {**v, "on": True} for k, v in cfg.items()}):
        c = cfg.get(w.key) or {}
        rows.append({"key": w.key, "title": w.title, "what": w.what, "on": c.get("on", True), "order": c.get("order", w.order),
                     "features": [{"key": f, "label": plans.label_of(f), "tier": plans.tier_of(f)} for f in w.features], "default_order": w.order})
    return {"widgets": rows}


class AdminWidget(BaseModel):
    key: str = Field(max_length=40)
    on: bool = True
    order: int = Field(ge=0, le=10_000)


class AdminWidgetsIn(BaseModel):
    widgets: list[AdminWidget] = Field(max_length=60)


@admin.put("")
async def save_admin_widgets(body: AdminWidgetsIn, user: dict = Depends(require_admin)):
    cfg = {}
    for x in body.widgets:
        if x.key not in REGISTRY:
            raise HTTPException(400, f"Unknown widget: {x.key}")
        if x.key == "today" and not x.on:
            raise HTTPException(400, "The Today tile can't be switched off.")
        cfg[x.key] = {"on": x.on, "order": x.order}
    await db().config.update_one({"_id": "dashboard_widgets"}, {"$set": {"widgets": cfg, "updated_at": now(), "by": user["_id"]}}, upsert=True)
    dashboard_cache._cache.clear()
    return await admin_widgets()
