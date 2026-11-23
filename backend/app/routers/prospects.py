"""
Find new customers (v2.4, Lead engine, feature `prospects`): businesses that fit, found on Google Maps or from the owner's
own list; a first message drafted for each (WhatsApp, email and a call opener) that the owner sends from his OWN WhatsApp or
mail app; day-3 and day-7 follow-ups; the Reply helper and "More like my best customers". The data side and Google's rules
are in app/prospecting.py — read its notes first: Google's business details are never saved, only place IDs.

GET    /api/prospects                       the page: who to look for, area, limits left, counts by status
PUT    /api/prospects/kinds                 the owner's own list of kinds (0 tokens)
POST   /api/prospects/kinds/suggest         "Suggest with AI" from the Knowledge Hub's ideal customer (1 token)
PUT    /api/prospects/area                  city | an area name | within 5–50 km of the business
POST   /api/prospects/home/find             find the owner's own business on Google Maps (to search around it)
PUT    /api/prospects/home                  save it (its place ID only) · DELETE to forget it
POST   /api/prospects/search                one search = one of the month's searches; one Google call per kind
POST   /api/prospects/keep                  keep ticked results (counts against the month's kept businesses)
GET    /api/prospects/list?page=&status=    the kept list, 20 a page, details fetched live from Google
POST   /api/prospects/upload                the owner's own list (CSV or Excel)
POST   /api/prospects/draft                 "Write first messages" for up to 5 (1 token)
GET    /api/prospects/best-customers        top 5 customers by sales and what kind of business each is
PUT    /api/prospects/best-customers        the owner's answers (once per customer)
POST   /api/prospects/best-customers/fill   fill "Who to look for" from them (0 tokens, or 1 to word them with AI)
GET    /api/prospects/{id}                  one business: details live, drafts, follow-ups, reply helper, notes
PATCH  /api/prospects/{id}                  notes
POST   /api/prospects/{id}/status           replied | not_interested | dnc (Do not contact is permanent)
POST   /api/prospects/{id}/whatsapp         open the owner's WhatsApp with the message (marks it sent; never sent by the hub)
POST   /api/prospects/{id}/email-sent       the owner sent the email from his own mail app
POST   /api/prospects/{id}/reply            Reply helper: a suggested answer (1 token)
POST   /api/prospects/{id}/deal             Make this a deal: contact + deal at the first stage, source "prospecting"
"""
import asyncio
import re
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError
from starlette.datastructures import UploadFile

from .. import competitor_watch as cw
from .. import crm, dashboard_cache, fetch, growth_core, prospecting as pr, salesbook
from ..agents import approvals as appr
from ..ai import prospect_tasks as tasks
from ..context import Ctx, feature
from ..db import db, now
from ..services import new_id, run_ai, track
from .hub import profile_of

router = APIRouter(prefix="/api/prospects")
gate = feature("prospects", "manager")
NO_KEY = ("Searching Google Maps needs a server setting that isn't switched on yet. Your own list still works: upload it on "
          "My list.")
GOOGLE_DOWN = "Google Maps didn't answer just now. Your search was not used — try again in a minute."
UPLOAD_MAX = 5 * 1024 * 1024
PLACE_ID = r"^[A-Za-z0-9_-]{10,300}$"


# ───────────────────────── views ─────────────────────────
def _iso(v) -> str | None:
    return crm.iso(v)


def _approval_view(a: dict | None) -> dict | None:
    if not a:
        return None
    return {"id": a["_id"], "step": a.get("step") or "first", "label": pr.STEP_LABEL.get(a.get("step") or "first", "Message"),
            "status": a.get("status"), "via": a.get("via"), "text": a.get("text", ""), "created_at": _iso(a.get("created_at")),
            "decided_at": _iso(a.get("decided_at")), "note": a.get("note") or ""}


def view(p: dict, d: dict | None, msgs: list[dict] | None = None, full: bool = False) -> dict:
    """One business on the owner's list. For a Google business, name/address/phone/website come from `d` (asked from Google in
    this request) — never from the database."""
    google = p.get("source") == "google"
    out = {"id": p["_id"], "source": p.get("source"), "from_google": google, "place_id": p.get("place_id") if google else None,
           "kind": p.get("kind", ""), "status": p.get("status"), "status_label": pr.LABELS.get(p.get("status"), p.get("status")),
           "notes": p.get("notes", ""), "kept_at": _iso(p.get("kept_at")), "drafted_at": _iso(p.get("drafted_at")),
           "contacted_at": _iso(p.get("contacted_at")), "replied_at": _iso(p.get("replied_at")), "closed_at": _iso(p.get("closed_at")),
           "lead_id": p.get("lead_id"), "followups_sent": int(p.get("followups_sent") or 0), "next_followup_on": p.get("next_followup_on"),
           "name": "", "address": "", "phone": "", "website": "", "email": "", "city": "", "maps_url": "", "details_note": "", "closed": False}
    if google:
        if d and d.get("ok"):
            out.update(name=d["name"], address=d["address"], phone=d["phone"], website=d["website"], maps_url=d["maps_url"], closed=d["closed"])
        else:
            out["details_note"] = (d or {}).get("note") or "Details from Google Maps aren't loaded."
    else:
        out.update(name=p.get("name", ""), phone=p.get("phone") or "", email=p.get("email") or "", website=p.get("website") or "",
                   city=p.get("city") or "")
    msgs = msgs or []
    out["waiting"] = [m["step"] for m in (_approval_view(a) for a in msgs) if m and m["status"] == "pending"]
    if full:
        by_step: dict[str, dict] = {}
        for a in msgs:                                                  # oldest first, so the newest per step wins
            by_step[a.get("step") or "first"] = _approval_view(a)
        out["messages"] = [by_step[s] for s in pr.STEPS if s in by_step]
        dr = p.get("draft") or {}
        out["draft"] = {"email_subject": dr.get("email_subject", ""), "email_body": dr.get("email_body", ""),
                        "call_opener": dr.get("call_opener", ""), "by": dr.get("by"), "at": _iso(dr.get("at"))} if dr else None
        rh = p.get("reply_help") or {}
        out["reply_help"] = {"reply": rh.get("reply", ""), "answer": rh.get("answer", ""), "said_no": bool(rh.get("said_no")),
                             "by": rh.get("by"), "at": _iso(rh.get("at"))} if rh else None
    return out


async def _messages(ws: str, ids: list[str]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    if ids:
        async for a in db().approvals.find({"ws": ws, "kind": "outreach", "prospect_id": {"$in": ids}}).sort("created_at", 1):
            out.setdefault(a["prospect_id"], []).append(a)
    return out


async def _counts(ws: str) -> dict:
    counts = {s: 0 for s in pr.STATUSES}
    async for g in db().prospects.aggregate([{"$match": {"ws": ws}}, {"$group": {"_id": "$status", "n": {"$sum": 1}}}]):
        if g["_id"] in counts:
            counts[g["_id"]] = g["n"]
    return counts


async def _limits(owner: dict) -> dict:
    return {"searches": await growth_core.left(owner, "prospect_searches"), "kept": await growth_core.left(owner, "prospects_kept"),
            "first_messages": await growth_core.left(owner, "outreach_per_day")}


async def _own(ctx: Ctx, pid: str) -> dict:
    p = await db().prospects.find_one({"_id": pid, "ws": ctx.ws})
    if not p:
        raise HTTPException(404, "Not found")
    return p


def _actor_name(ctx: Ctx) -> str:
    return ctx.user.get("name") or ("Owner" if ctx.role == "owner" else ctx.role.title())


# ───────────────────────── the page ─────────────────────────
@router.get("")
async def overview(ctx: Ctx = Depends(gate)):
    st = await pr.get_settings(ctx.ws)
    recent = [{"id": s["_id"], "kinds": s.get("kinds", []), "area": s.get("area", ""), "found": s.get("found", 0), "new": s.get("new", 0),
               "at": _iso(s.get("at"))} async for s in db().prospect_searches.find({"ws": ctx.ws}).sort("at", -1).limit(5)]
    counts = await _counts(ctx.ws)
    return {"places_ready": cw.places_ready(), "kinds": st["kinds"], "kinds_by": st["kinds_by"], "area_mode": st["area_mode"],
            "area": st["area"], "radius_km": st["radius_km"], "city": st["city"], "home_set": bool(st["home_place_id"]),
            "limits": await _limits(ctx.owner), "counts": counts, "total": sum(counts.values()), "recent": recent, "line": pr.LINE,
            "no_key": NO_KEY, "page_size": pr.PAGE_SIZE, "max_kinds": pr.MAX_KINDS,
            "role_on": await growth_core.role_on(ctx.ws, "outreach_writer")}


class KindsIn(BaseModel):
    kinds: list[str] = Field(default_factory=list, max_length=pr.MAX_KINDS)


def _kinds(raw: list[str]) -> list[str]:
    out = []
    for k in raw:
        k = pr.clean_kind(k)
        if len(k) >= 2 and k.lower() not in {x.lower() for x in out}:
            out.append(k)
    return out[:pr.MAX_KINDS]


@router.put("/kinds")
async def save_kinds(body: KindsIn, ctx: Ctx = Depends(gate)):
    kinds = _kinds(body.kinds)
    if not kinds:
        raise HTTPException(400, "Type at least one kind of business, like \"hardware wholesalers\".")
    await pr.save_settings(ctx.ws, {"kinds": kinds, "kinds_by": "owner", "kinds_at": now()}, ctx.actor)
    return await overview(ctx)


def _strip_city(kind: str, city: str) -> str:
    k = pr.clean_kind(kind)
    if city:
        k = re.sub(rf"\s+(?:in|near|around)\s+{re.escape(city)}\b.*$", "", k, flags=re.I).strip()
    return k


@router.post("/kinds/suggest")
async def suggest_kinds(ctx: Ctx = Depends(gate)):
    """1 AI token: 3–6 kinds of businesses from the Knowledge Hub's ideal customer (the research plan job, names never allowed)."""
    p = await profile_of(ctx.ws)
    who = (p.get("ideal_customer") or p.get("industry") or "").strip()
    city = (p.get("city") or "").strip()
    plan = await run_ai(ctx.owner, "prospect_kinds", cw.company_plan(p, who, city, "Kinds of businesses to look up on Google Maps."),
                        "Who to look for", save_output=False, check=cw.plan_check)
    suggested = _kinds([_strip_city(t["type"], city) for t in plan["company_types"]])
    if not suggested:
        raise HTTPException(502, "The AI's suggestions couldn't be used. Type your own kinds of business.")
    own = (await pr.get_settings(ctx.ws))["kinds"]       # the owner's own kinds stay first; the AI only adds to them
    kinds = _kinds(own + suggested)
    added = len(kinds) - len(_kinds(own))
    await pr.save_settings(ctx.ws, {"kinds": kinds, "kinds_by": "ai", "kinds_at": now()}, ctx.actor)
    await growth_core.log_action(ctx.ws, "prospector", f"Suggested {added} {'kind' if added == 1 else 'kinds'} of business to look for",
                                 ref="search:kinds")
    return {**await overview(ctx), "why": [t.get("why", "") for t in plan["company_types"]][:pr.MAX_KINDS]}


class AreaIn(BaseModel):
    mode: Literal["city", "area", "radius"] = "city"
    area: str = Field(default="", max_length=60)
    radius_km: int = Field(default=10, ge=5, le=50)


@router.put("/area")
async def save_area(body: AreaIn, ctx: Ctx = Depends(gate)):
    area = re.sub(r"\s+", " ", body.area).strip()
    if body.mode == "area" and len(area) < 2:
        raise HTTPException(400, "Type the area to search in, like \"Bhiwandi\" or \"Andheri East\".")
    await pr.save_settings(ctx.ws, {"area_mode": body.mode, "area": area, "radius_km": body.radius_km}, ctx.actor)
    return await overview(ctx)


# ───────────────────────── the owner's own business on Google Maps (for "within X km") ─────────────────────────
HOME_FINDS_PER_DAY = 5


@router.post("/home/find")
async def find_home(ctx: Ctx = Depends(gate)):
    """Up to 5 matches for the owner's own business name and city, to pick from. Shown, never saved."""
    if not cw.places_ready():
        raise HTTPException(400, NO_KEY)
    b = await profile_of(ctx.ws)
    day = pr.ist_day().isoformat()
    n = 0
    for i in range(HOME_FINDS_PER_DAY):
        if await pr.jobs.claim(f"prospect-home:{ctx.ws}:{day}:{i}"):
            n = i + 1
            break
    if not n:
        raise HTTPException(429, f"You can look up your business {HOME_FINDS_PER_DAY} times a day. Try again tomorrow.")
    try:
        found = await pr.text_search(f"{b.get('name', '')} {b.get('city', '')}".strip(), pr.HOME_MASK, size=5)
    except cw.PlacesError:
        raise HTTPException(502, "Google Maps didn't answer just now. Try again in a minute.")
    return {"results": [{"place_id": p["id"], "name": pr._text(p.get("displayName"))[:120], "address": str(p.get("formattedAddress") or "")[:200]}
                        for p in found], "attribution": "Google Maps"}


class HomeIn(BaseModel):
    place_id: str = Field(pattern=PLACE_ID)


@router.put("/home")
async def save_home(body: HomeIn, ctx: Ctx = Depends(gate)):
    await pr.save_settings(ctx.ws, {"home_place_id": body.place_id, "home_set_at": now()}, ctx.actor)
    return await overview(ctx)


@router.delete("/home")
async def forget_home(ctx: Ctx = Depends(gate)):
    await pr.save_settings(ctx.ws, {"home_place_id": None, "area_mode": "city"}, ctx.actor)
    return await overview(ctx)


# ───────────────────────── search and keep ─────────────────────────
class SearchIn(BaseModel):
    kinds: list[str] = Field(default_factory=list, max_length=pr.MAX_KINDS)
    mode: Literal["city", "area", "radius"] | None = None
    area: str | None = Field(default=None, max_length=60)
    radius_km: int | None = Field(default=None, ge=5, le=50)


@router.post("/search")
async def search(body: SearchIn, ctx: Ctx = Depends(gate)):
    if not cw.places_ready():
        raise HTTPException(400, NO_KEY)
    ws = ctx.ws
    st = await pr.get_settings(ws)
    kinds = _kinds(body.kinds or st["kinds"])
    if not kinds:
        raise HTTPException(400, "Add at least one kind of business to look for.")
    st.update({k: v for k, v in (("area_mode", body.mode), ("area", re.sub(r"\s+", " ", body.area or "").strip() if body.area is not None else None),
                                  ("radius_km", body.radius_km)) if v is not None})
    mode = st["area_mode"]
    where = st["area"] if mode == "area" else st["city"]
    if mode == "area" and len(st["area"]) < 2:
        raise HTTPException(400, "Type the area to search in, like \"Bhiwandi\" or \"Andheri East\".")
    if mode == "city" and not where:
        raise HTTPException(400, "Add your city in the Knowledge Hub, or type an area to search in.")
    if mode == "radius" and not st["home_place_id"]:
        raise HTTPException(400, "To search around your business, first find your business on Google Maps (below the area).")
    await pr.save_settings(ws, {"area_mode": mode, "area": st["area"], "radius_km": st["radius_km"]}, ctx.actor)
    await growth_core.take(ctx.owner, "prospect_searches")
    try:
        async with growth_core.working(ws, "prospector"):
            centre, circle = None, None
            if mode == "radius":
                centre = await pr.home_location(st["home_place_id"])
                if not centre:
                    await growth_core.give_back(ctx.owner, "prospect_searches")
                    raise HTTPException(400, "Google Maps can't find your business's location any more. Find your business again.")
                circle = (centre[0], centre[1], st["radius_km"] * 1000)
            queries = [k if mode == "radius" else f"{k} in {where}" for k in kinds]
            got = await asyncio.gather(*(pr.text_search(q, pr.NEAR_MASK if circle else pr.SEARCH_MASK, circle) for q in queries),
                                       return_exceptions=True)
    except cw.PlacesError:
        await growth_core.give_back(ctx.owner, "prospect_searches")
        raise HTTPException(502, GOOGLE_DOWN)
    if all(isinstance(g, BaseException) for g in got):
        await growth_core.give_back(ctx.owner, "prospect_searches")
        raise HTTPException(502, GOOGLE_DOWN)
    failed = [kinds[i] for i, g in enumerate(got) if isinstance(g, BaseException)]

    kn = await pr.known(ws)
    blocked_places, _ = await pr.blocks(ws)
    kept = {p["place_id"]: p async for p in db().prospects.find({"ws": ws, "source": "google"}, {"place_id": 1, "status": 1}).limit(50_000)}
    seen: set[str] = set()
    out, skipped = [], {"closed": 0, "known": 0, "own": 0, "blocked": 0, "far": 0}
    found = 0
    for kind, places in zip(kinds, got):
        if isinstance(places, BaseException):
            continue
        for p in places:
            pid = p["id"]
            if pid in seen:
                continue
            seen.add(pid)
            found += 1
            name = pr._text(p.get("displayName"))[:120]
            nk = pr.norm_name(name)
            if pr._closed(p.get("businessStatus")):
                skipped["closed"] += 1
                continue
            if pid == kn["own_place"] or nk in kn["own_names"]:
                skipped["own"] += 1
                continue
            if pid in blocked_places:
                skipped["blocked"] += 1
                continue
            if nk in kn["names"]:
                skipped["known"] += 1
                continue
            dist = None
            if centre:
                loc = p.get("location") or {}
                try:
                    dist = round(pr.distance_km(centre, (float(loc["latitude"]), float(loc["longitude"]))), 1)
                except (KeyError, TypeError, ValueError):
                    dist = None
                if dist is not None and dist > st["radius_km"]:
                    skipped["far"] += 1
                    continue
            mine = kept.get(pid)
            out.append({"place_id": pid, "name": name, "address": str(p.get("formattedAddress") or "")[:200], "kind": kind,
                        "type": pr._text(p.get("primaryTypeDisplayName"))[:60], "maps_url": pr._http(p.get("googleMapsUri")),
                        "distance_km": dist, "kept": bool(mine), "prospect_id": (mine or {}).get("_id"),
                        "status_label": pr.LABELS.get((mine or {}).get("status"), "") if mine else ""})
    new = sum(1 for r in out if not r["kept"])
    area = pr.place_line(st)
    sid = new_id()
    await db().prospect_searches.insert_one({"_id": sid, "ws": ws, "kinds": kinds, "area": area,
                                             "radius_km": st["radius_km"] if mode == "radius" else None, "found": found, "new": new,
                                             "skipped": skipped, "failed_kinds": failed, "at": now(), "by": ctx.actor})
    quoted = ", ".join(f"'{k}'" for k in kinds)
    await growth_core.log_action(ws, "prospector", f"Searched Google Maps for {quoted} {'' if mode == 'radius' else 'in '}{area}: "
                                                   f"{found} found, {new} new", ref=f"search:{sid}")
    await track("prospect_search", ws, kinds=len(kinds), found=found, new=new, mode=mode)
    note = ""
    if failed:
        note = f"Google Maps didn't answer for {', '.join(failed)}. Search again later for those."
    return {"search_id": sid, "results": out, "found": found, "new": new, "skipped": skipped, "area": area, "note": note,
            "attribution": "Google Maps", "limits": await _limits(ctx.owner)}


class KeepItem(BaseModel):
    place_id: str = Field(pattern=PLACE_ID)
    kind: str = Field(default="", max_length=60)


KEEP_WHY = {"already": "Already on your list", "blocked": "You marked it Do not contact", "known": "Already in your contacts",
            "closed": "Google Maps shows it closed", "gone": "Google Maps no longer lists it"}


class KeepIn(BaseModel):
    search_id: str | None = Field(default=None, max_length=40)
    items: list[KeepItem] = Field(min_length=1, max_length=60)


@router.post("/keep")
async def keep(body: KeepIn, ctx: Ctx = Depends(gate)):
    """Ticked results become prospects with status "kept": only the place ID, the kind and dates are saved. Each is checked
    live with Google (Place Details) so people already in the owner's contacts (by phone) are left out."""
    ws = ctx.ws
    items = list({i.place_id: i for i in body.items}.values())
    have = {p["place_id"] async for p in db().prospects.find({"ws": ws, "place_id": {"$in": [i.place_id for i in items]}}, {"place_id": 1})}
    blocked_places, hashes = await pr.blocks(ws)
    skipped = {"already": 0, "blocked": 0, "known": 0, "closed": 0, "gone": 0}
    left_out: list[dict] = []

    def skip(place_id: str, why: str) -> None:
        skipped[why] += 1
        left_out.append({"place_id": place_id, "why": KEEP_WHY[why]})
    todo = []
    for i in items:
        if i.place_id in have:
            skip(i.place_id, "already")
        elif i.place_id in blocked_places:
            skip(i.place_id, "blocked")
        else:
            todo.append(i)
    if not todo:
        return {"kept": 0, "kept_place_ids": [], "left_out": left_out, "skipped": skipped, "limits": await _limits(ctx.owner)}
    left = (await growth_core.left(ctx.owner, "prospects_kept"))["left"]
    if left < len(todo):
        raise HTTPException(429, f"You can keep {left} more {'business' if left == 1 else 'businesses'} this month. Untick some and press Keep again."
                            if left else "You've kept this month's businesses. More open on the 1st.")
    await growth_core.take(ctx.owner, "prospects_kept", len(todo))
    kn = await pr.known(ws)
    live = pr.Live()
    made: list[str] = []
    kept_ids: list[str] = []
    st = await db().prospect_searches.find_one({"_id": body.search_id, "ws": ws}, {"kinds": 1}) if body.search_id else None
    async with growth_core.working(ws, "prospector"):
        details = await live.many([i.place_id for i in todo])
        at = now()
        for i in todo:
            d = details[i.place_id]
            if d["ok"]:
                pk = salesbook.phone_key(d.get("phone_intl") or "")
                if d["closed"]:
                    skip(i.place_id, "closed")
                    continue
                if pk and (pk in kn["phones"] or pk in kn["listed"] or pk in kn["own_phones"]):
                    skip(i.place_id, "known")
                    continue
                if pk and pr.phone_hash(ws, pk) in hashes:
                    skip(i.place_id, "blocked")
                    continue
                if pr.norm_name(d["name"]) in kn["names"] | kn["own_names"]:
                    skip(i.place_id, "known")
                    continue
            elif d.get("gone"):
                skip(i.place_id, "gone")
                continue
            kind = pr.clean_kind(i.kind) or ((st or {}).get("kinds") or ["New business"])[0]
            doc = {"_id": new_id(), "ws": ws, "source": "google", "place_id": i.place_id, "kind": kind, "status": "kept", "notes": "",
                   "search_id": body.search_id if st else None, "kept_at": at, "drafted_at": None, "contacted_at": None, "replied_at": None,
                   "closed_at": None, "lead_id": None, "followups_sent": 0, "next_followup_on": None, "steps": {}, "drafts_n": 0,
                   "updated_at": at, "by": ctx.actor}
            try:
                await db().prospects.insert_one(doc)
                made.append(doc["_id"])
                kept_ids.append(i.place_id)
            except DuplicateKeyError:
                skip(i.place_id, "already")
    if len(made) < len(todo):
        await growth_core.give_back(ctx.owner, "prospects_kept", len(todo) - len(made))
    if made:
        kinds = (st or {}).get("kinds") or sorted({pr.clean_kind(i.kind) for i in todo if i.kind})
        what = ", ".join(f"'{k}'" for k in kinds[:3]) or "new businesses"
        await growth_core.log_action(ws, "prospector", f"Kept {len(made)} {'business' if len(made) == 1 else 'businesses'} from a search for {what}",
                                     ref=f"search:{body.search_id}" if st else f"prospect:{made[0]}")
        await track("prospects_kept", ws, n=len(made))
    return {"kept": len(made), "kept_place_ids": kept_ids, "prospect_ids": dict(zip(kept_ids, made)), "left_out": left_out,
            "skipped": skipped, "limits": await _limits(ctx.owner)}


# ───────────────────────── the kept list ─────────────────────────
OPEN = ["kept", "drafted", "contacted", "replied"]


@router.get("/list")
async def kept_list(page: int = 1, status: str = "", ctx: Ctx = Depends(gate)):
    q: dict = {"ws": ctx.ws}
    if status in pr.STATUSES:
        q["status"] = status
    elif status == "open":
        q["status"] = {"$in": OPEN}
    total = await db().prospects.count_documents(q)
    pages = max(1, -(-total // pr.PAGE_SIZE))
    page = min(max(1, page), pages)
    rows = [p async for p in db().prospects.find(q).sort([("kept_at", -1), ("_id", 1)]).skip((page - 1) * pr.PAGE_SIZE).limit(pr.PAGE_SIZE)]
    live = pr.Live()
    details = await live.many([p["place_id"] for p in rows if p.get("source") == "google" and p.get("place_id")])
    msgs = await _messages(ctx.ws, [p["_id"] for p in rows])
    return {"items": [view(p, details.get(p.get("place_id")), msgs.get(p["_id"])) for p in rows], "total": total, "page": page,
            "pages": pages, "page_size": pr.PAGE_SIZE, "counts": await _counts(ctx.ws), "attribution": "Google Maps",
            "places_ready": live.ready, "limits": await _limits(ctx.owner), "line": pr.LINE}


# ───────────────────────── the owner's own list ─────────────────────────
@router.post("/upload")
async def upload_list(request: Request, ctx: Ctx = Depends(gate)):
    """An Excel or CSV list the owner has (an exhibition list, an association directory): columns name, phone, email,
    website, city, kind. His data, so it is saved as it is. People already in Contacts or on this list are left out."""
    form = await request.form()
    try:
        f = form.get("file")
        if not isinstance(f, UploadFile):
            raise HTTPException(400, "Choose an Excel or CSV file.")
        data = await f.read(UPLOAD_MAX + 1)
        filename = f.filename or ""
    finally:
        await form.close()
    if len(data) > UPLOAD_MAX:
        raise HTTPException(413, "Keep the file under 5 MB.")
    if not data:
        raise HTTPException(400, "That file is empty.")
    rows = pr.read_list(data, filename)
    if len(rows) < 2:
        raise HTTPException(400, "The file has no rows.")
    cols = pr.header_of(rows[0])
    if "name" not in cols or ("phone" not in cols and "email" not in cols):
        raise HTTPException(400, "The first row needs a \"name\" column and a \"phone\" or \"email\" column. "
                                 "Website, city and kind are read when there.")
    ws = ctx.ws
    kn = await pr.known(ws)
    _, hashes = await pr.blocks(ws)
    emails_known = {c.get("email") async for c in db().contacts.find({"ws": ws, "email": {"$type": "string"}}, {"email": 1}).limit(salesbook.MAX_ROWS)}
    listed_names = set()
    async for p in db().prospects.find({"ws": ws, "source": "list"}, {"name": 1, "city": 1, "email": 1}).limit(salesbook.MAX_ROWS):
        listed_names.add((pr.norm_name(p.get("name") or ""), pr.norm_name(p.get("city") or "")))
        if p.get("email"):
            emails_known.add(p["email"])
    skipped = {"no_contact": 0, "known": 0, "duplicate": 0, "blocked": 0, "over_limit": 0}
    todo: list[dict] = []
    seen_phones: set[str] = set()

    def cell(r: list, key: str) -> str:
        i = cols.get(key)
        v = r[i] if i is not None and i < len(r) and r[i] is not None else ""
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        return re.sub(r"\s+", " ", str(v)).strip()
    for r in rows[1:]:
        if not any(str(x or "").strip() for x in r):
            continue
        name = cell(r, "name")[:80]
        phone, email = crm.norm_phone(cell(r, "phone")), crm.norm_email(cell(r, "email"))
        if len(name) < 2 or (not phone and not email):
            skipped["no_contact"] += 1
            continue
        try:
            website = fetch.normalise_url(cell(r, "website")) if cell(r, "website") else ""
        except fetch.FetchError:
            website = ""
        city, kind = cell(r, "city")[:60], pr.clean_kind(cell(r, "kind")) or "From your list"
        pk = salesbook.phone_key(phone or "")
        if pk and (pk in kn["phones"] or pk in kn["own_phones"]) or (email and email in emails_known):
            skipped["known"] += 1
            continue
        if pk and pr.phone_hash(ws, pk) in hashes:
            skipped["blocked"] += 1
            continue
        nkey = (pr.norm_name(name), pr.norm_name(city))
        if (pk and (pk in kn["listed"] or pk in seen_phones)) or (not pk and nkey in listed_names):
            skipped["duplicate"] += 1
            continue
        if pk:
            seen_phones.add(pk)
        listed_names.add(nkey)
        if email:
            emails_known.add(email)
        todo.append({"name": name, "phone": phone, "phone_key": pk or None, "email": email, "website": website[:300], "city": city, "kind": kind})
    left = (await growth_core.left(ctx.owner, "prospects_kept"))["left"]
    if len(todo) > left:
        skipped["over_limit"] = len(todo) - left
        todo = todo[:left]
    if todo:
        await growth_core.take(ctx.owner, "prospects_kept", len(todo))
    at = now()
    docs = [{"_id": new_id(), "ws": ws, "source": "list", "place_id": None, **t, "status": "kept", "notes": "", "kept_at": at,
             "drafted_at": None, "contacted_at": None, "replied_at": None, "closed_at": None, "lead_id": None, "followups_sent": 0,
             "next_followup_on": None, "steps": {}, "drafts_n": 0, "updated_at": at, "by": ctx.actor} for t in todo]
    if docs:
        await db().prospects.insert_many(docs)
        await growth_core.log_action(ws, "prospector", f"Added {len(docs)} {'business' if len(docs) == 1 else 'businesses'} from your own list",
                                     ref=f"prospect:{docs[0]['_id']}")
        await track("prospects_uploaded", ws, n=len(docs))
    return {"added": len(docs), "skipped": skipped, "limits": await _limits(ctx.owner)}


# ───────────────────────── first messages ─────────────────────────
class DraftIn(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=5)


async def read_prospect_site(url: str) -> tuple[dict | None, set[int]]:
    """The business's own website, read live and politely (robots.txt first). Never saved; its words only guard the draft."""
    try:
        async with asyncio.timeout(pr.SITE_SECONDS):
            target = fetch.normalise_url(url)
            async with httpx.AsyncClient(timeout=fetch.TIMEOUT, follow_redirects=False, verify=True) as client:
                robots, _ = await cw._robots_for(target, client)
            if not robots.allowed("/"):
                return None, set()
            facts = await fetch.read_site(target)
    except (fetch.FetchError, cw.SiteDown, TimeoutError, httpx.HTTPError, ValueError, OSError):
        return None, set()
    texts = [facts.title, facts.description, *facts.headings, facts.text]
    return ({"title": facts.title, "description": facts.description, "headings": facts.headings[:10], "text": facts.text[:3000]},
            cw.shingles_of([t for t in texts if t], tasks.COPY_RUN))


@router.post("/draft")
async def draft(body: DraftIn, ctx: Ctx = Depends(gate)):
    """"Write first messages": one AI token for up to 5 kept businesses. Each WhatsApp draft waits in the Send list (it only
    ever opens the owner's own WhatsApp); the email and call opener are kept on the business's page."""
    ws = ctx.ws
    profile = await profile_of(ws)
    owner_name = ctx.owner.get("name") or ""
    ids = list(dict.fromkeys(body.ids))
    found = {p["_id"]: p async for p in db().prospects.find({"ws": ws, "_id": {"$in": ids}})}
    msgs = await _messages(ws, ids)
    kn = await pr.known(ws)
    live = pr.Live()
    skipped: list[dict] = []
    ready: list[dict] = []
    google = [p["place_id"] for p in found.values() if p.get("source") == "google" and p.get("place_id")]
    details = await live.many(google)
    for pid in ids:
        p = found.get(pid)
        if not p:
            skipped.append({"id": pid, "why": "Not on your list."})
            continue
        if p["status"] not in ("kept", "drafted"):
            skipped.append({"id": pid, "why": f"Already {pr.LABELS.get(p['status'], p['status']).lower()}."})
            continue
        if any(a.get("status") == "pending" and (a.get("step") or "first") == "first" for a in msgs.get(pid, [])):
            skipped.append({"id": pid, "why": "A first message is already waiting in your Send list."})
            continue
        d = details.get(p.get("place_id")) if p.get("source") == "google" else None
        phone = (d or {}).get("phone_intl", "") if d else p.get("phone") or ""
        if await pr.is_blocked(ws, p.get("place_id"), phone):
            skipped.append({"id": pid, "why": "You marked this business Do not contact."})
            continue
        if d is not None and not d["ok"] and d.get("gone"):
            skipped.append({"id": pid, "why": d["note"]})
            continue
        pk = salesbook.phone_key(phone)
        if p.get("source") == "google" and pk and (pk in kn["phones"] or pk in kn["own_phones"]):
            skipped.append({"id": pid, "why": "Already in your contacts."})
            continue
        website = (d or {}).get("website", "") if p.get("source") == "google" else p.get("website") or ""
        ready.append({"p": p, "website": website, "hide": [d["name"]] if d and d.get("ok") and d.get("name") else []})
    if not ready:
        raise HTTPException(400, skipped[0]["why"] if len(skipped) == 1 else "None of these can get a first message now: "
                            + "; ".join(sorted({s["why"] for s in skipped})))
    items = []
    async with growth_core.working(ws, "outreach_writer"):
        sites = await asyncio.gather(*(read_prospect_site(r["website"]) if r["website"] else _nothing() for r in ready))
        for n, (r, (site, shingles)) in enumerate(zip(ready, sites), 1):
            p = r["p"]
            items.append({"ref": f"P{n}", "kind": p.get("kind", ""), "source": p.get("source"), "site": site, "shingles": shingles,
                          "hide": r["hide"], "name": p.get("name", "") if p.get("source") == "list" else ""})
        token = True
        try:
            result = await run_ai(ctx.owner, "prospect_drafts", tasks.first_messages(profile, owner_name, items), "First messages",
                                  save_output=False, check=lambda res: tasks.drafts_check(res, items, profile, owner_name))
        except HTTPException as e:
            if e.status_code != 502:
                raise
            token = False        # the AI's answer couldn't be used: the rules version, and the token was given back
            result = {it["ref"]: {**tasks.rules_first(profile, owner_name, it), "dropped": []} for it in items}
        made, at = [], now()
        for r, it in zip(ready, items):
            p, dr = r["p"], result[it["ref"]]
            locked = await db().prospects.find_one_and_update({"_id": p["_id"], "drafts_n": int(p.get("drafts_n") or 0),
                                                               "status": {"$in": ["kept", "drafted"]}}, {"$inc": {"drafts_n": 1}})
            if not locked:
                skipped.append({"id": p["_id"], "why": "It changed a moment ago. Open it again."})
                continue
            listed = p.get("source") == "list"
            a = await appr.create_approval(ctx.owner, ws, "outreach_writer", "outreach", f"First message · {p.get('kind') or 'new business'}",
                                           p.get("name", "") if listed else "", (p.get("phone") or "") if listed else "", dr["whatsapp"],
                                           allow_act=False, prospect_id=p["_id"], step="first")
            await db().prospects.update_one({"_id": p["_id"]}, {"$set": {
                "status": "drafted", "drafted_at": at, "steps.first": a["_id"], "updated_at": at,
                "draft": {"email_subject": dr["email_subject"], "email_body": dr["email_body"], "call_opener": dr["call_opener"],
                          "by": dr["by"], "at": at}}})
            await growth_core.log_action(ws, "outreach_writer", f"Wrote a first message for a business you kept ({p.get('kind') or 'new business'})",
                                         ref=f"prospect:{p['_id']}")
            made.append(p["_id"])
    await track("prospect_drafts", ws, n=len(made), ai=token)
    fresh = {p["_id"]: p async for p in db().prospects.find({"ws": ws, "_id": {"$in": made}})}
    msgs = await _messages(ws, made)
    return {"drafted": len(made), "skipped": skipped, "token_used": token,
            "by_ai": sum(1 for it in items if result[it["ref"]]["by"] == "ai"),
            "items": [view(fresh[i], details.get(fresh[i].get("place_id")), msgs.get(i), full=True) for i in made if i in fresh],
            "note": "" if token else "The AI's answer couldn't be used, so these were written from your Knowledge Hub without AI. No token was used."}


async def _nothing():
    return None, set()


# ───────────────────────── "More like my best customers" ─────────────────────────
@router.get("/best-customers")
async def best_customers(ctx: Ctx = Depends(gate)):
    top = await pr.top_customers(ctx.ws)
    return {"customers": top, "missing": sum(1 for c in top if not c["kind"]), "kinds": _kinds([c["kind"] for c in top if c["kind"]])}


class KindAnswer(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    kind: str = Field(default="", max_length=60)


class KindAnswers(BaseModel):
    answers: list[KindAnswer] = Field(min_length=1, max_length=5)


@router.put("/best-customers")
async def answer_kinds(body: KindAnswers, ctx: Ctx = Depends(gate)):
    """The owner's answer, once per customer: what kind of business it is (kept for next time)."""
    keys = {c["key"] for c in await pr.top_customers(ctx.ws)}
    for a in body.answers:
        if a.key not in keys:
            continue
        kind = pr.clean_kind(a.kind)
        if kind:
            await db().prospect_customer_kinds.update_one({"_id": f"{ctx.ws}:{a.key}"}, {"$set": {"ws": ctx.ws, "key": a.key, "kind": kind,
                                                                                               "at": now(), "by": ctx.actor}}, upsert=True)
        else:
            await db().prospect_customer_kinds.delete_one({"_id": f"{ctx.ws}:{a.key}"})
    return await best_customers(ctx)


class FillIn(BaseModel):
    use_ai: bool = False


@router.post("/best-customers/fill")
async def fill_from_best(body: FillIn, ctx: Ctx = Depends(gate)):
    top = await pr.top_customers(ctx.ws)
    if not top:
        raise HTTPException(400, "No sales yet to find your best customers. Add invoices or upload your sales first.")
    raw = [c["kind"] for c in top if c["kind"]]
    if not raw:
        raise HTTPException(400, "Tell us what kind of business each of your best customers is first.")
    got = tasks.rules_kinds(raw)
    token = False
    if body.use_ai:
        try:
            got = await run_ai(ctx.owner, "prospect_kinds", tasks.word_kinds(await profile_of(ctx.ws), raw), "More like my best customers",
                               save_output=False, check=tasks.kinds_check)
            token = True
        except HTTPException as e:
            if e.status_code != 502:
                raise
    kinds = _kinds(got["kinds"])
    if not kinds:
        raise HTTPException(400, "Those answers can't be used as kinds of business. Type a kind, like \"hotel kitchens\".")
    await pr.save_settings(ctx.ws, {"kinds": kinds, "kinds_by": "best_customers", "kinds_at": now()}, ctx.actor)
    await growth_core.log_action(ctx.ws, "prospector", f"Filled who to look for from your {len(top)} best customers", ref="search:best")
    return {**await overview(ctx), "token_used": token}


# ───────────────────────── one business ─────────────────────────
async def _full(ctx: Ctx, p: dict, live: pr.Live | None = None) -> dict:
    live = live or pr.Live()
    d = await live.details(p["place_id"]) if p.get("source") == "google" and p.get("place_id") else None
    msgs = await _messages(ctx.ws, [p["_id"]])
    out = view(p, d, msgs.get(p["_id"]), full=True)
    out.update(attribution="Google Maps" if p.get("source") == "google" else "", line=pr.LINE,
               first_messages=await growth_core.left(ctx.owner, "outreach_per_day"))
    return out


@router.get("/{pid}")
async def get_prospect(pid: str, ctx: Ctx = Depends(gate)):
    return await _full(ctx, await _own(ctx, pid))


class NotesIn(BaseModel):
    notes: str = Field(default="", max_length=2000)


@router.patch("/{pid}")
async def edit_notes(pid: str, body: NotesIn, ctx: Ctx = Depends(gate)):
    p = await _own(ctx, pid)
    await db().prospects.update_one({"_id": pid}, {"$set": {"notes": body.notes.strip(), "updated_at": now()}})
    p["notes"] = body.notes.strip()
    return await _full(ctx, p)


class StatusIn(BaseModel):
    status: Literal["replied", "not_interested", "dnc"]


@router.post("/{pid}/status")
async def set_status(pid: str, body: StatusIn, ctx: Ctx = Depends(gate)):
    p = await _own(ctx, pid)
    ws, at = ctx.ws, now()
    if p["status"] == "dnc":
        raise HTTPException(409, "Do not contact is permanent for this business.")
    if body.status == p["status"]:
        return await _full(ctx, p)
    if body.status == "replied" and p["status"] not in ("kept", "drafted", "contacted"):
        raise HTTPException(400, f"This business is already {pr.LABELS[p['status']].lower()}.")
    patch: dict = {"status": body.status, "updated_at": at, "next_followup_on": None}
    if body.status == "replied":
        patch["replied_at"] = at
    else:
        patch["closed_at"] = at
    live = pr.Live()
    if body.status == "dnc":
        phone = ""
        if p.get("source") == "google" and p.get("place_id"):
            d = await live.details(p["place_id"])
            phone = d.get("phone_intl", "") if d.get("ok") else ""
        await pr.block(ws, p, phone, ctx.actor)
    await db().prospects.update_one({"_id": pid}, {"$set": patch})
    why = {"replied": "They replied", "not_interested": "Marked Not interested", "dnc": "Marked Do not contact"}[body.status]
    await pr.expire_pending(ws, pid, f"No longer needed: {why.lower()}")
    p.update(patch)
    return await _full(ctx, p, live)


class SendIn(BaseModel):
    step: Literal["first", "day3", "day7"] = "first"
    text: str | None = Field(default=None, min_length=2, max_length=900)


@router.post("/{pid}/whatsapp")
async def send_whatsapp(pid: str, body: SendIn, ctx: Ctx = Depends(gate)):
    """Opens the owner's own WhatsApp with the message typed (the hub never sends outreach itself). The phone of a Google
    business is looked up now and only put in the link."""
    p = await _own(ctx, pid)
    a = await db().approvals.find_one({"ws": ctx.ws, "kind": "outreach", "prospect_id": pid, "step": body.step, "status": "pending"},
                                      sort=[("created_at", -1)])
    if not a:
        raise HTTPException(404, "No message is waiting for this business.")
    if body.text and body.text.strip() != a.get("text"):
        await db().approvals.update_one({"_id": a["_id"], "status": "pending"},
                                        {"$set": {"text": body.text.strip(), "edited_by": ctx.actor, "edited_at": now()}})
    out = await appr.decide(ctx.ws, a["_id"], ctx.actor, "own")
    return {"link": out["link"], "prospect": await _full(ctx, await _own(ctx, pid))}


@router.post("/{pid}/email-sent")
async def email_sent(pid: str, ctx: Ctx = Depends(gate)):
    """The owner opened his own mail app with the draft and sent it himself. The hub never sends outreach email."""
    p = await _own(ctx, pid)
    if p["status"] in pr.STOP:
        raise HTTPException(400, pr.STOPPED_WHY[p["status"]])
    if await pr.is_blocked(ctx.ws, p.get("place_id"), p.get("phone") or ""):
        raise HTTPException(400, pr.STOPPED_WHY["dnc"])
    if p["status"] in ("kept", "drafted"):
        await pr.take_daily(ctx.owner)
        await pr.mark_contacted(p, now())
    return await _full(ctx, await _own(ctx, pid))


class ReplyIn(BaseModel):
    text: str = Field(min_length=2, max_length=3000)


@router.post("/{pid}/reply")
async def reply_helper(pid: str, body: ReplyIn, ctx: Ctx = Depends(gate)):
    """The owner pastes the business's reply: a suggested answer (1 token; the rules version when the AI's can't be used).
    The business is marked Replied and its follow-ups stop."""
    p = await _own(ctx, pid)
    if p["status"] == "dnc":
        raise HTTPException(400, pr.STOPPED_WHY["dnc"])
    profile = await profile_of(ctx.ws)
    owner_name = ctx.owner.get("name") or ""
    reply = body.text.strip()
    first = await db().approvals.find_one({"ws": ctx.ws, "kind": "outreach", "prospect_id": pid, "step": "first"}, sort=[("created_at", -1)])
    token = True
    async with growth_core.working(ctx.ws, "outreach_writer"):
        try:
            got = await run_ai(ctx.owner, "prospect_reply", tasks.reply_answer(profile, owner_name, p.get("kind", ""), (first or {}).get("text", ""), reply),
                               "Reply helper", save_output=False, check=tasks.answer_check(profile, owner_name, reply))
        except HTTPException as e:
            if e.status_code != 502:
                raise
            token = False
            got = tasks.rules_answer(profile, owner_name, reply)
    at = now()
    patch: dict = {"reply_help": {"reply": reply, "answer": got["answer"], "said_no": got["said_no"], "by": got["by"], "at": at}, "updated_at": at}
    if p["status"] in ("kept", "drafted", "contacted"):
        patch.update(status="replied", replied_at=at, next_followup_on=None)
    await db().prospects.update_one({"_id": pid}, {"$set": patch})
    await pr.expire_pending(ctx.ws, pid, "No longer needed: they replied")
    await growth_core.log_action(ctx.ws, "outreach_writer", f"Suggested an answer to a reply from a business you messaged ({p.get('kind') or 'new business'})",
                                 ref=f"prospect:{pid}")
    p.update(patch)
    return {**await _full(ctx, p), "token_used": token}


class DealIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    phone: str = Field(default="", max_length=24)
    email: str = Field(default="", max_length=254)
    value: float | None = Field(default=None, ge=0, le=1e11)
    note: str = Field(default="", max_length=1000)


@router.post("/{pid}/deal")
async def make_deal(pid: str, body: DealIn, ctx: Ctx = Depends(gate)):
    """Make this a deal: the contact (crm.upsert_contact) and a deal at the first pipeline stage with source "prospecting",
    through the same steps as any new lead (agents/hooks.on_new_lead), so lead research and the pipeline see it too.
    The name and number are the ones the owner confirmed — his customer now."""
    from ..agents import hooks
    p = await _own(ctx, pid)
    ws = ctx.ws
    if p["status"] == "deal" and p.get("lead_id"):
        raise HTTPException(409, "This business is already a deal.")
    if p["status"] == "dnc":
        raise HTTPException(400, pr.STOPPED_WHY["dnc"])
    phone = crm.norm_phone(body.phone)
    if body.phone.strip() and not phone:
        raise HTTPException(400, "Enter a valid 10-digit mobile number, or leave it empty.")
    email = crm.norm_email(body.email)
    if not phone and not email and p.get("source") == "google":
        try:
            phone = crm.norm_phone(await pr.phone_for(p))
        except HTTPException:
            phone = None
    if not phone and not email:
        raise HTTPException(400, "Add a mobile number or an email for this business.")
    claim = f"prospect-deal:{pid}"
    if not await pr.jobs.claim(claim):
        raise HTTPException(409, "This business is already a deal.")
    try:
        stages = await crm.stages_of(ws)
        first = stages[0]
        at = now()
        name = body.name.strip()
        reply = ((p.get("reply_help") or {}).get("reply") or "").strip()
        lead = {"_id": new_id(), "owner_id": ws, "site_id": None, "name": name, "phone": phone or "", "email": email or "", "company": name,
                "message": (body.note.strip() or reply)[:2000], "status": "new", "stage": first["ff"], "pstage": first["id"],
                "source": "prospecting", "prospect_id": pid, "value": body.value, "expected_close": None, "follow_up_on": None,
                "follow_up_note": "", "created_at": at, "stage_at": at, "created_by": ctx.actor}
        await db().leads.insert_one(lead)
    except BaseException:
        await pr.jobs.release(claim)
        raise
    contact = await crm.upsert_contact(ws, name, phone, email, name, "prospecting")
    if contact:
        await db().leads.update_one({"_id": lead["_id"]}, {"$set": {"contact_id": contact["_id"]}})
        lead["contact_id"] = contact["_id"]
        await crm.add_activity(ws, contact["_id"], lead["_id"], "created", f"Opportunity added from Find new customers ({p.get('kind') or 'new business'})"
                               + (f" — they wrote: {reply[:200]}" if reply else ""), ctx.actor, _actor_name(ctx))
    business = await db().businesses.find_one({"owner_id": ws}) or {}
    try:   # the same hook every new lead gets (follow-ups, lead research, CRM sync…); it never fails the deal
        patch = await hooks.on_new_lead(ctx.owner, business, None, lead)
        if patch:
            await db().leads.update_one({"_id": lead["_id"]}, {"$set": patch})
    except Exception:  # noqa: BLE001
        pr.log.exception("new-lead hook failed for prospect deal %s", lead["_id"])
    dashboard_cache.bust(ws)
    await track("lead", ws, source="prospecting")
    upd = {"status": "deal", "lead_id": lead["_id"], "closed_at": at, "next_followup_on": None, "updated_at": at}
    if not p.get("replied_at"):
        upd["replied_at"] = at
    await db().prospects.update_one({"_id": pid}, {"$set": upd})
    await pr.expire_pending(ws, pid, "No longer needed: it became a deal")
    await growth_core.log_action(ws, "outreach_writer", f"A business you messaged became a deal ({p.get('kind') or 'new business'})", ref=f"prospect:{pid}")
    p.update(upd)
    return {"lead_id": lead["_id"], "prospect": await _full(ctx, p)}


async def ensure_indexes() -> None:
    await pr.ensure_indexes()
