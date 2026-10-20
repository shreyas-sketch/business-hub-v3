"""
SOP Writer (v2.1, 3.10): scenario SOPs (through /api/kits/sop with mode "scenario"), the whole-business SOP set, version
history, assigning an SOP to a role or a team member (Growth Mentorship and up) and a print / save-as-PDF page.

The whole-business set: the Knowledge Hub → a process map by function (one AI run), the owner ticks the processes that
apply, and the hub writes them in batches — 2 AI runs per SOP (the steps, then the words to use, what done looks like and
when to escalate), with the cost shown before it starts. A run that fails is refunded, as everywhere in the hub.

sop_maps {_id: ws, processes [{key, function, title, why, selected, sop_id, status}], edited, generated_at,
          batch {total, done, failed, running, started_at, finished_at}}
"""
import asyncio
import logging
import re
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .. import kits, knowledge, plans
from ..ai import kit_tasks
from ..context import Ctx, feature
from ..db import db, now, public
from ..services import new_id, run_ai, runs_status, track
from .hub import profile_of

router = APIRouter(prefix="/api")
log = logging.getLogger("sopset")
FUNCTIONS = ["Sales", "Marketing", "Operations/Delivery", "Accounts", "HR", "Customer care"]
RUNS_PER_SOP = 2
_tasks: set = set()


def _key(function: str, title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", f"{function}-{title}".lower()).strip("-")[:80]


def _shape_map(r: dict) -> list[dict]:
    out, seen = [], set()
    for x in (r.get("processes") or []) if isinstance(r, dict) else []:
        if not isinstance(x, dict):
            continue
        fn = next((f for f in FUNCTIONS if f.lower().split("/")[0] in str(x.get("function", "")).lower()), "Operations/Delivery")
        title = re.sub(r"\s+", " ", str(x.get("title") or "")).strip()[:120]
        if len(title) < 3:
            continue
        key = _key(fn, title)
        if key in seen:
            continue
        seen.add(key)
        out.append({"key": key, "function": fn, "title": title, "why": str(x.get("why") or "")[:200], "selected": True, "sop_id": None, "status": None})
    if len(out) < 5:
        raise ValueError("too few processes")
    return out[:30]


async def _map(ws: str) -> dict:
    return await db().sop_maps.find_one({"_id": ws}) or {}


def _view(m: dict) -> dict:
    procs = m.get("processes") or []
    without = [p for p in procs if not p.get("sop_id")]
    return {"exists": bool(procs), "processes": procs, "functions": FUNCTIONS, "edited": bool(m.get("edited")),
            "batch": m.get("batch"), "without_sop": len(without), "selected_without_sop": sum(1 for p in without if p.get("selected")),
            "runs_per_sop": RUNS_PER_SOP, "generated_at": m["generated_at"].isoformat() if m.get("generated_at") else None}


@router.get("/sop-set")
async def get_set(ctx: Ctx = Depends(feature("sop_library"))):
    v = _view(await _map(ctx.ws))
    v["runs_left"] = (await runs_status(ctx.owner))["left"]
    return v


@router.post("/sop-set/map")
async def make_map(ctx: Ctx = Depends(feature("sop_library", "manager"))):
    """The process map for this business (one AI run). Processes that already have an SOP keep it."""
    p = await profile_of(ctx.ws)
    procs = await run_ai(ctx.owner, "sop_map", kit_tasks.process_map(p), f"Process map — {p['name']}", save_output=False, check=_shape_map)
    old = {x["key"]: x for x in (await _map(ctx.ws)).get("processes") or []}
    for x in procs:
        if x["key"] in old:
            x.update(sop_id=old[x["key"]].get("sop_id"), selected=old[x["key"]].get("selected", True), status=old[x["key"]].get("status"))
    kept = [x for k, x in old.items() if x.get("sop_id") and k not in {p_["key"] for p_ in procs}]
    await db().sop_maps.update_one({"_id": ctx.ws}, {"$set": {"processes": procs + kept, "edited": False, "generated_at": now(),
                                                              "business_type": kit_tasks.business_type(p)}}, upsert=True)
    await track("sop_map", ctx.ws)
    return await get_set(ctx)


class Proc(BaseModel):
    key: str | None = Field(default=None, max_length=80)
    function: str = Field(max_length=40)
    title: str = Field(min_length=3, max_length=120)
    selected: bool = True


class MapIn(BaseModel):
    processes: list[Proc] = Field(max_length=40)


@router.put("/sop-set/map")
async def save_map(body: MapIn, ctx: Ctx = Depends(feature("sop_library", "manager"))):
    old = {x["key"]: x for x in (await _map(ctx.ws)).get("processes") or []}
    out, seen = [], set()
    for x in body.processes:
        if x.function not in FUNCTIONS:
            raise HTTPException(400, f"Function is one of: {', '.join(FUNCTIONS)}")
        key = x.key if x.key in old else _key(x.function, x.title)
        if key in seen:
            continue
        seen.add(key)
        prev = old.get(key, {})
        out.append({"key": key, "function": x.function, "title": x.title.strip(), "why": prev.get("why", ""), "selected": x.selected,
                    "sop_id": prev.get("sop_id"), "status": prev.get("status")})
    await db().sop_maps.update_one({"_id": ctx.ws}, {"$set": {"processes": out, "edited": True}}, upsert=True)
    return await get_set(ctx)


class WriteIn(BaseModel):
    keys: list[str] = Field(min_length=1, max_length=30)


async def _write_one(owner: dict, ws: str, actor: str, proc: dict, profile: dict) -> bool:
    """Two AI runs: the SOP itself, then the scripts / done / escalation pass. Each failed run is refunded."""
    try:
        doc = await kits.generate(owner, ws, actor, "sop", {"process": proc["title"], "notes": "", "mode": "process", "function": proc["function"]},
                                  profile, extra={"by_agent": "sop_set", "function": proc["function"], "process_key": proc["key"]})
    except Exception as e:  # noqa: BLE001
        log.warning("SOP set: %s failed: %s", proc["title"], getattr(e, "detail", e))
        return False
    content = dict(doc["content"])
    try:
        extra = await run_ai(owner, "kit:sop_scripts", kit_tasks.sop_scripts(profile, content), f"SOP scripts — {content['title']}", save_output=False,
                             check=lambda r: {"scripts": [{"label": str(x.get("label", ""))[:120], "text": str(x.get("text", ""))[:900]}
                                                          for x in (r.get("scripts") or []) if isinstance(x, dict) and x.get("text")][:8],
                                              "done_when": str(r.get("done_when") or "")[:400], "escalation": str(r.get("escalation") or "")[:400]})
        content.update(extra, function=proc["function"])
        await db().kits.update_one({"_id": doc["_id"]}, {"$set": {"content": kits.shape(kits.KINDS["sop"], content)}})
    except Exception as e:  # noqa: BLE001  the SOP itself is kept; only the second pass is missing
        log.warning("SOP set: scripts for %s failed: %s", proc["title"], getattr(e, "detail", e))
    await db().sop_maps.update_one({"_id": ws, "processes.key": proc["key"]}, {"$set": {"processes.$.sop_id": doc["_id"], "processes.$.status": "written"}})
    return True


async def run_batch(owner: dict, ws: str, actor: str, procs: list[dict]) -> dict:
    done = failed = 0
    try:
        profile = await profile_of(ws)
        for proc in procs:
            ok = await _write_one(owner, ws, actor, proc, profile)
            done += ok
            failed += not ok
            if not ok:
                await db().sop_maps.update_one({"_id": ws, "processes.key": proc["key"]}, {"$set": {"processes.$.status": "failed"}})
            await db().sop_maps.update_one({"_id": ws}, {"$set": {"batch.done": done, "batch.failed": failed}})
    finally:   # the lock always comes off, whatever happened
        await db().sop_maps.update_one({"_id": ws}, {"$set": {"batch.running": False, "batch.finished_at": now()}})
    await track("sop_set_written", ws, done=done, failed=failed)
    return {"done": done, "failed": failed}


@router.post("/sop-set/write")
async def write(body: WriteIn, ctx: Ctx = Depends(feature("sop_library", "manager"))):
    """Writes the chosen processes' SOPs in the background: 2 AI runs each, shown before it starts."""
    m = await _map(ctx.ws)
    stale = now() - timedelta(minutes=30)     # a batch cut short by a restart doesn't lock the owner out
    b = m.get("batch") or {}
    if b.get("running") and b.get("started_at") and b["started_at"] >= stale:
        raise HTTPException(409, "A batch is already being written.")
    procs = [p for p in m.get("processes") or [] if p["key"] in set(body.keys) and not p.get("sop_id")]
    if not procs:
        raise HTTPException(400, "Pick processes that don't have an SOP yet.")
    need = len(procs) * RUNS_PER_SOP
    left = (await runs_status(ctx.owner))["left"]
    if left < need:
        raise HTTPException(402, {"code": "not_enough_runs", "message": f"Writing {len(procs)} SOPs uses {need} AI runs; you have {left} left this month.",
                                  "need": need, "left": left})
    r = await db().sop_maps.update_one({"_id": ctx.ws, "$or": [{"batch.running": {"$ne": True}}, {"batch.started_at": {"$lt": stale}}]},
                                       {"$set": {"batch": {"total": len(procs), "done": 0, "failed": 0, "running": True, "started_at": now(), "runs": need}}})
    if not r.modified_count:
        raise HTTPException(409, "A batch is already being written.")
    task = asyncio.create_task(run_batch(ctx.owner, ctx.ws, ctx.actor, procs))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"started": len(procs), "runs": need}


async def settle() -> None:
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


# ───────────────────────── library upgrades ─────────────────────────
@router.get("/kits/{kind}/{kit_id}/versions")
async def versions(kind: str, kit_id: str, ctx: Ctx = Depends(feature("sop_library"))):
    if kind != "sop":
        raise HTTPException(404, "Not found")
    return [{"id": v["_id"], "title": v["title"], "at": v["at"].isoformat(), "by": v.get("by"), "content": v["content"]}
            async for v in db().kit_versions.find({"kit_id": kit_id, "ws": ctx.ws}).sort("saved_at", -1).limit(30)]


@router.post("/kits/{kind}/{kit_id}/versions/{version_id}/restore")
async def restore(kind: str, kit_id: str, version_id: str, ctx: Ctx = Depends(feature("sop_library", "manager"))):
    v = await db().kit_versions.find_one({"_id": version_id, "kit_id": kit_id, "ws": ctx.ws})
    if not v or kind != "sop":
        raise HTTPException(404, "Not found")
    return kits.view(await kits.update(ctx.ws, ctx.actor, "sop", kit_id, v["content"]))


class AssignIn(BaseModel):
    role: str = Field(default="", max_length=60)
    user_id: str | None = Field(default=None, max_length=40)


@router.put("/kits/sop/{kit_id}/assign")
async def assign(kit_id: str, body: AssignIn, ctx: Ctx = Depends(feature("sop_library", "manager"))):
    """Who follows this SOP: a role, and from Growth Mentorship a team member."""
    name = ""
    if body.user_id:
        if not ctx.has("team_logins"):
            ctx.require("team_logins")
        member = await db().users.find_one({"_id": body.user_id, "team_of": ctx.ws})
        if not member:
            raise HTTPException(400, "That person isn't on your team.")
        name = member.get("name", "")
    r = await db().kits.update_one({"_id": kit_id, "ws": ctx.ws, "kind": "sop"},
                                   {"$set": {"assigned": {"role": body.role.strip(), "user_id": body.user_id, "name": name, "at": now()}}})
    if not r.matched_count:
        raise HTTPException(404, "Not found")
    return kits.view(await db().kits.find_one({"_id": kit_id}))


@router.get("/kits/sop/{kit_id}/print", response_class=HTMLResponse)
async def print_sop(kit_id: str, ctx: Ctx = Depends(feature("sop_library"))):
    from .public import templates
    doc = await db().kits.find_one({"_id": kit_id, "ws": ctx.ws, "kind": "sop"})
    if not doc:
        raise HTTPException(404, "Not found")
    b = await db().businesses.find_one({"owner_id": ctx.ws}) or {}
    from ..brand import accent_for
    return HTMLResponse(templates.get_template("sop_print.html").render(s=doc["content"], b=b, doc=doc, accent=await accent_for(ctx.ws)))


async def without_sop(ws: str) -> int:
    m = await _map(ws)
    return sum(1 for p in m.get("processes") or [] if not p.get("sop_id"))


# ───────────────────────── follows the Knowledge Hub ─────────────────────────
async def _exists(ws: str) -> bool:
    return bool((await _map(ws)).get("processes"))


async def _edited(ws: str) -> bool:
    m = await _map(ws)
    return bool(m.get("edited")) or any(p.get("sop_id") for p in m.get("processes") or [])


async def _refresh(owner: dict, sections: set) -> None:
    """A new map from the Knowledge Hub. Never loses a written SOP: processes that have one keep it (as the banner's Refresh does)."""
    await _overwrite(owner)


async def _overwrite(owner: dict) -> None:
    """Refresh on the banner: a new map; processes that already have an SOP keep it."""
    p = await profile_of(owner["_id"])
    procs = await run_ai(owner, "sop_map", kit_tasks.process_map(p), f"Process map refreshed — {p['name']}", save_output=False, check=_shape_map)
    old = {x["key"]: x for x in (await _map(owner["_id"])).get("processes") or []}
    for x in procs:
        if x["key"] in old:
            x.update(sop_id=old[x["key"]].get("sop_id"), status=old[x["key"]].get("status"))
    kept = [x for k, x in old.items() if x.get("sop_id") and k not in {p_["key"] for p_ in procs}]
    await db().sop_maps.update_one({"_id": owner["_id"]}, {"$set": {"processes": procs + kept, "edited": False, "generated_at": now()}})


knowledge.register(knowledge.Dependent("sop_map", "SOP process map", "sop_library", "/sops", {"basics", "identity"}, _exists, _edited, _refresh,
                                       overwrite=_overwrite, note="SOPs you've already written are never changed.", order=4))
