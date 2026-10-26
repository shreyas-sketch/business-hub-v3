"""
Employz.ai in the hub (the owner's Connections → CRM panel and the Pipeline page):

GET  /api/employz/check             what this connection can read (the sub-account, and each kind of data, readable or not)
GET  /api/employz/remote-pipelines  the sub-account's pipelines and stages, live (to pick where new hub leads go)
POST /api/employz/push-pipeline     {pipeline_id, stages?}: the pipeline new hub leads are sent to, and which of its stages each of the
                                    hub's 5 steps goes to (the owner's choice; without one, matched by name)
GET  /api/employz/import            the import's state: last sync, progress, coverage, what's in the hub, errors
POST /api/employz/import            "Import now" (starts in the background; one at a time)

The board for an Employz.ai pipeline is /api/crm/deals?pipeline=employz:<id> (routers/crm.py), read-only.
"""
import asyncio
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import connections, employz_api as api, employz_import as imp
from ..context import Ctx, feature
from ..services import track

router = APIRouter(prefix="/api/employz")
owner_only = feature("crm_sync", "owner")

# the hub's 5 Football Field stages → words their Employz.ai stage names use (the snapshot's names, and common variants)
STAGE_WORDS = {"identification": ("identif", "new lead", "new inquiry", "enquiry", "inquiry"), "logic": ("logic", "reason"),
               "pain": ("pain", "consequence"), "vision": ("vision", "solution"), "close": ("ask", "close", "closing")}


async def _conn(ctx: Ctx) -> dict:
    conn = await connections.get(ctx.ws, "employz")
    if not connections.ready("employz", conn):
        raise HTTPException(400, "Add your Employz.ai sub-account (location) id and private integration token first, then save.")
    return conn


def _plain(e: api.EmployzError) -> HTTPException:
    return HTTPException(401 if isinstance(e, api.AuthError) else 502, e.message)


@router.get("/check")
async def check(ctx: Ctx = Depends(owner_only)):
    return await api.capabilities(await _conn(ctx))


@router.get("/remote-pipelines")
async def remote_pipelines(ctx: Ctx = Depends(owner_only)):
    conn = await _conn(ctx)
    try:
        page = await api.read_pipelines(conn)
    except api.EmployzError as e:
        raise _plain(e)
    out = []
    for p in page.records:
        stages = sorted(p.get("stages") or [], key=lambda s: s.get("position") or 0)
        out.append({"id": p["id"], "name": p.get("name") or "Pipeline",
                    "stages": [{"id": s["id"], "name": s.get("name") or "Stage"} for s in stages],
                    "suggested": match_stages(stages)[0]})
    saved = {k: conn.get(f"stage_{k}") for k in STAGE_WORDS if conn.get(f"stage_{k}")}
    return {"pipelines": out, "selected": conn.get("pipeline_id") or None, "steps": STEP_NAMES, "saved": saved}


STEP_NAMES = {"identification": "Identification", "logic": "Logic", "pain": "Pain", "vision": "Vision", "close": "Close"}


def _ended(s: dict) -> bool:
    name = str(s.get("name") or "").lower()
    return "lost" in name or "won" in name


def match_stages(stages: list[dict]) -> tuple[dict, list[str]]:
    """{hub stage: Employz.ai stage id} by name, then by position for a 5-stage pipeline; and the hub stages left unmatched.
    A won or lost stage is never matched: a deal still open in the hub must not land on Closed Won or Closed Lost."""
    out: dict = {}
    used: set = set()
    for key, words in STAGE_WORDS.items():
        for s in stages:
            name = str(s.get("name") or "").lower()
            if s["id"] not in used and not _ended(s) and any(w in name for w in words):
                out[key] = s["id"]
                used.add(s["id"])
                break
    if len(out) < 5 and len(stages) == 5:                 # five stages with other names: in order
        for key, s in zip(STAGE_WORDS, stages):
            if key not in out and s["id"] not in used and not _ended(s):
                out[key] = s["id"]
                used.add(s["id"])
    return out, [k for k in STAGE_WORDS if k not in out]


class PushIn(BaseModel):
    pipeline_id: str = Field(min_length=4, max_length=60, pattern=r"^[A-Za-z0-9_-]+$")
    stages: dict[str, str] | None = None      # the owner's choice: hub step → Employz.ai stage id (all 5)


@router.post("/push-pipeline")
async def push_pipeline(body: PushIn, ctx: Ctx = Depends(owner_only)):
    conn = await _conn(ctx)
    try:
        page = await api.read_pipelines(conn)
    except api.EmployzError as e:
        raise _plain(e)
    pipe = next((p for p in page.records if p["id"] == body.pipeline_id), None)
    if not pipe:
        raise HTTPException(404, "That pipeline isn't in this Employz.ai sub-account any more. Pick again.")
    stages = sorted(pipe.get("stages") or [], key=lambda s: s.get("position") or 0)
    if body.stages is not None:
        ids = {s["id"] for s in stages}
        if set(body.stages) != set(STAGE_WORDS) or any(v not in ids for v in body.stages.values()):
            raise HTTPException(400, "Pick a stage of this pipeline for each of the 5 steps.")
        matched, missing = dict(body.stages), []
    else:
        matched, missing = match_stages(stages)
    values = {"pipeline_id": pipe["id"], **{f"stage_{k}": matched.get(k, "") for k in STAGE_WORDS}}   # none left from an earlier pipeline
    await connections.save(ctx.ws, "employz", values, ctx.actor, "you")
    await track("employz_push_pipeline", ctx.ws, matched=len(matched))
    names = {s["id"]: s.get("name") for s in stages}
    return {"pipeline": {"id": pipe["id"], "name": pipe.get("name")}, "stages": {k: names.get(v) for k, v in matched.items()},
            "missing": missing}


@router.get("/import")
async def import_status(ctx: Ctx = Depends(owner_only)):
    return await imp.status(ctx.ws)


@router.post("/import")
async def import_now(ctx: Ctx = Depends(owner_only)):
    await _conn(ctx)
    st = await imp.status(ctx.ws)
    if st["running"]:
        return st
    if st["state"] == "disconnected":
        await imp.on_connect(ctx.ws)
    task = asyncio.create_task(imp.run(ctx.ws, "manual"))
    for _ in range(20):                       # answer once it has started, so the page shows its progress
        await asyncio.sleep(0.05)
        st = await imp.status(ctx.ws)
        if st["running"] or task.done():
            break
    await track("employz_import_now", ctx.ws)
    return {**st, "running": not task.done()}


def board_key(ext_id: str) -> str:
    return f"employz:{re.sub(r'[^A-Za-z0-9_-]', '', ext_id)[:60]}"
