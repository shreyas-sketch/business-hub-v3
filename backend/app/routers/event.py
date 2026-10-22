"""
Live event link (see app/event.py).

Public, no login:
  GET  /api/event/form?e=CODE              is the link open, the states, the business types, the consent line
  POST /api/event/gstin                    what a GST number says (valid, state, which business types fit) — no portal is called
  POST /api/event/runs                     website + GST + 3 taps → {token, eta}
  GET  /api/event/runs/<token>             progress and everything built so far
  POST /api/event/runs/<token>/brand       pick one of the 3 brand messages
  GET  /api/event/runs/<token>/design/<key>.png   the WhatsApp Status poster or the social post
  POST /api/event/runs/<token>/code        the hub's WhatsApp login code, to the number typed
  POST /api/event/runs/<token>/verify      code + email + name → account created (or logged in), the pack put inside it
Logged in:
  GET  /api/event/mine                     the latest pack saved into this account
Admin (Admin → Live event):
  list, settings (open, AI on, caps, Membership-features-until), live numbers for the speaker, packs, try again
"""
import re
from datetime import datetime, time as dtime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import event as ev, fetch, knowledge
from ..config import settings
from ..db import IST, db, now
from ..security import client_ip, current_user, normalise_phone, rate_limit, require_admin
from ..services import track
from .auth import VerifyIn, login_with_code, send_login_code

router = APIRouter()
admin = APIRouter(prefix="/api/admin/event", dependencies=[Depends(require_admin)])
EMAIL = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


async def _run(token: str) -> dict:
    run = await db().event_runs.find_one({"token": (token or "")[:40]})
    if not run:
        raise HTTPException(404, "We couldn't find this pack. Open the event link again.")
    return run


# ───────────────────────── public: the form ─────────────────────────
@router.get("/api/event/form")
async def form(e: str = ""):
    c = await ev.cohort(e)
    cfg = ev.event_of(c)
    return {"valid": bool(c), "open": bool(c) and cfg["open"], "event": c["code"] if c else None, "name": (c or {}).get("name") or "",
            "program": settings.program_name, "ai": cfg["ai"], "consent": ev.CONSENT_TEXT, "states": knowledge.STATES,
            "entities": [{"value": a, "label": b} for a, b in knowledge.ENTITIES]}


class GstIn(BaseModel):
    gstin: str = Field(max_length=30)


@router.post("/api/event/gstin")
async def gstin(body: GstIn, request: Request):
    await rate_limit(f"event-gst:{client_ip(request)}", settings.event_runs_per_ip * 5, 600, "Too many checks from this network. Try again in a minute.")
    return ev.parse_gstin(body.gstin)


class RunIn(BaseModel):
    event: str = Field(min_length=1, max_length=60)
    website: str | None = Field(default=None, max_length=500)
    business_name: str | None = Field(default=None, max_length=80)    # only without a website
    about: str | None = Field(default=None, max_length=1000)          # only without a website: what you sell, in a line or two
    city: str | None = Field(default=None, max_length=80)
    gstin: str | None = Field(default=None, max_length=30)
    state: str | None = Field(default=None, max_length=60)            # when there's no GST number (or it doesn't say)
    gst_scheme: str = Field(default="monthly", pattern="^(monthly|qrmp)$")
    staff: int = Field(default=0, ge=0, le=1_000_000)
    entity: str | None = Field(default=None, pattern="^(proprietor|partnership|llp|pvt_ltd|opc|other)$")
    consent: bool = False


def _inputs(body: RunIn) -> tuple[dict, dict, dict | None]:
    """The form, checked: the website (or the business in a line), and the business identity the compliance calendar needs."""
    if not body.consent:
        raise HTTPException(400, "Please tick the box to say this is your business.")
    inp: dict = {"website_url": None, "business_name": (body.business_name or "").strip()[:80], "about": (body.about or "").strip()[:1000],
                 "city": (body.city or "").strip()[:80]}
    if (body.website or "").strip():
        try:
            inp["website_url"] = fetch.normalise_url(body.website)
        except fetch.FetchError as e:
            raise HTTPException(400, str(e))
    elif len(inp["business_name"]) < 2 or len(inp["about"]) < 10:
        raise HTTPException(400, "No website? Type your business name and what you sell, in a line or two.")
    gst = None
    if (body.gstin or "").strip():
        gst = ev.parse_gstin(body.gstin)
        if not gst["valid"]:
            raise HTTPException(400, gst["error"])
        allowed = [x["value"] for x in gst["entities"]]
        entity = body.entity if body.entity in allowed else (allowed[0] if len(allowed) == 1 else None)
        if not entity:
            raise HTTPException(400, f"Your GST number is for a {gst['pan_kind'].lower()}. Pick which one.")
        state = gst["state"] or body.state
    else:
        entity, state = body.entity, body.state
        if not entity:
            raise HTTPException(400, "Pick your type of business.")
    if state not in knowledge.STATES:
        raise HTTPException(400, "Pick your state.")
    identity = {"entity": entity, "gst_registered": bool(gst), "gstin": gst["gstin"] if gst else "", "gst_scheme": body.gst_scheme if gst else "monthly",
                "state": state, "staff": int(body.staff), "registrations": []}
    return inp, identity, gst


@router.post("/api/event/runs")
async def create_run(body: RunIn, request: Request):
    inp, identity, gst = _inputs(body)
    await rate_limit(f"event-ip:{client_ip(request)}", settings.event_runs_per_ip, 600,
                     "Too many packs from this network just now. Please try again in a few minutes.")
    try:
        run = await ev.create(body.event, inp, identity, gst)
    except ev.EventError as e:
        raise HTTPException(e.status, e.message)
    return {"token": run["token"], "status": run["status"], "eta": await ev.eta(run)}


@router.get("/api/event/runs/{token}")
async def run_status(token: str):
    run = await _run(token)
    return {**ev.view(run), "eta": await ev.eta(run),
            "error": "We couldn't build this pack. Please ask the team at the desk." if run["status"] == "failed" else None}


class PickIn(BaseModel):
    pick: int = Field(ge=0, le=2)


@router.post("/api/event/runs/{token}/brand")
async def pick_brand(token: str, body: PickIn):
    run = await _run(token)
    opts = (run.get("brand") or {}).get("options") or []
    if body.pick >= len(opts):
        raise HTTPException(400, "Pick one of the brand messages shown.")
    if run.get("user_id"):
        raise HTTPException(409, "This pack is saved in your hub — change your brand message there.")
    await db().event_runs.update_one({"_id": run["_id"]}, {"$set": {"brand.pick": body.pick}})
    return {"pick": body.pick}


class AboutIn(BaseModel):
    business_name: str = Field(min_length=2, max_length=80)
    about: str = Field(min_length=10, max_length=1000)
    city: str = Field(default="", max_length=80)


@router.post("/api/event/runs/{token}/about")
async def add_about(token: str, body: AboutIn):
    """The website couldn't be read: the business in a line or two, and the hub writes the rest from that."""
    run = await _run(token)
    try:
        run = await ev.add_about(run, body.business_name.strip(), body.about.strip(), body.city.strip())
    except ev.EventError as e:
        raise HTTPException(e.status, e.message)
    return {**ev.view(run), "eta": await ev.eta(run)}


@router.get("/api/event/runs/{token}/design/{name}")
async def design(token: str, name: str):
    m = re.fullmatch(r"(status_poster|social_square)\.png", name or "")
    if not m:
        raise HTTPException(404, "Not found")
    run = await _run(token)
    if not run.get("design") or not (run.get("profile") or {}).get("name"):
        raise HTTPException(404, "Not ready yet")
    await rate_limit(f"event-design:{run['_id']}", 60, 600, "Too many pictures just now. Try again in a minute.")
    data = await ev.design_png(run, m.group(1))
    return Response(data, media_type="image/png", headers={"Cache-Control": "private, max-age=60"})


class CodeIn(BaseModel):
    phone: str = Field(min_length=10, max_length=24)


def _indian(phone_raw: str) -> str:
    phone = normalise_phone(phone_raw)
    if not re.fullmatch(r"\+91[6-9]\d{9}", phone):
        raise HTTPException(400, "Please enter a 10-digit Indian WhatsApp number.")
    return phone


@router.post("/api/event/runs/{token}/code")
async def run_code(token: str, body: CodeIn, request: Request):
    run = await _run(token)
    phone = _indian(body.phone)
    if run.get("user_id"):
        owner = await db().users.find_one({"_id": run["user_id"]}, {"phone": 1})
        # saved, but putting it into the account didn't finish: the same number may finish it
        if run.get("applied") or not owner or owner.get("phone") != phone:
            raise HTTPException(409, "This pack is already saved. Log in with your WhatsApp number to open it.")
    out = await send_login_code(phone, request)
    return {**out, "to": "your WhatsApp number"}


class SaveIn(BaseModel):
    phone: str = Field(min_length=10, max_length=24)
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")
    email: str = Field(min_length=5, max_length=120)
    name: str = Field(min_length=1, max_length=80)


@router.post("/api/event/runs/{token}/verify")
async def run_verify(token: str, body: SaveIn, request: Request, response: Response):
    run = await _run(token)
    email = body.email.strip().lower()
    if not EMAIL.match(email):
        raise HTTPException(400, "That email doesn't look right.")
    name = re.sub(r"\s+", " ", body.name).strip()[:80]
    if not name:
        raise HTTPException(400, "Type your name.")
    phone = _indian(body.phone)
    if run.get("user_id"):
        existing = await db().users.find_one({"_id": run["user_id"]}, {"phone": 1})
        if not existing or existing.get("phone") != phone:
            raise HTTPException(409, "This pack is already saved in another account.")
    user, created = await login_with_code(phone, VerifyIn(phone=phone, code=body.code, name=name, cohort=run["cohort"], src="event"), request, response)
    if not await ev.adopt(run, user):
        raise HTTPException(409, "This pack is already saved in another account.")
    fresh = await db().event_runs.find_one({"_id": run["_id"]})
    applied = fresh.get("applied") or await ev.apply(fresh, user, email, name)
    applied = await ev.late_apply(run["_id"]) or applied       # it finished while we were saving
    pending = bool((await db().event_runs.find_one({"_id": run["_id"]}, {"apply_pending": 1}) or {}).get("apply_pending"))
    return {"ok": True, "new": created, "applied": applied, "pending": pending}


@router.get("/api/event/mine")
async def mine(user: dict = Depends(current_user)):
    run = await db().event_runs.find_one({"user_id": user["_id"]}, sort=[("saved_at", -1)])
    if not run:
        return None
    if not run.get("applied") and not user.get("team_of"):   # saved, but the save stopped before the pack went in: finish it now
        await ev.apply(run, user, user.get("email", ""), user.get("name", ""))
        await ev.late_apply(run["_id"])
        run = await db().event_runs.find_one({"_id": run["_id"]})
    return {"token": run["token"], "applied": run.get("applied") or {}, "saved_at": run["saved_at"].isoformat() if run.get("saved_at") else None,
            **{k: v for k, v in ev.view(run).items() if k in ("steps", "compliance", "check")}}


# ───────────────────────── Admin → Live event ─────────────────────────
def _cfg_out(c: dict) -> dict:
    cfg = ev.event_of(c)
    until = cfg.get("trial_until")
    return {"open": cfg["open"], "ai": cfg["ai"], "pack_cap": cfg["pack_cap"], "ai_cap": cfg["ai_cap"],
            "trial_until": until.astimezone(IST).date().isoformat() if isinstance(until, datetime) else None}


@admin.get("")
async def events():
    out = []
    async for c in db().cohorts.find().sort("created_at", -1).limit(50):
        out.append({"code": c["code"], "name": c.get("name", ""), "date": c.get("workshop_date"), **_cfg_out(c),
                    "link": f"{settings.app_url}/event?e={c['code']}", "counters": await ev.counters(c["code"])})
    return out


class EventIn(BaseModel):
    open: bool
    ai: bool = True
    pack_cap: int = Field(ge=1, le=20_000)
    ai_cap: int = Field(ge=0, le=20_000)
    trial_until: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")   # the last day Membership features stay open


@admin.put("/{code}")
async def save_event(code: str, body: EventIn, user: dict = Depends(require_admin)):
    c = await ev.cohort(code)
    if not c:
        raise HTTPException(404, "Cohort not found. Create it in Admin first.")
    until = None
    if body.trial_until:
        day = datetime.fromisoformat(body.trial_until).date()
        until = datetime.combine(day, dtime(23, 59, 59), tzinfo=IST)
        if until < now():
            raise HTTPException(400, "Pick a last day that hasn't passed.")
    cfg = {"open": body.open, "ai": body.ai, "pack_cap": body.pack_cap, "ai_cap": min(body.ai_cap, body.pack_cap), "trial_until": until,
           "updated_at": now(), "updated_by": user["_id"]}
    await db().cohorts.update_one({"_id": c["_id"]}, {"$set": {"event": cfg}})
    await track("event_settings", user["_id"], cohort=c["code"], open=body.open)
    return {"code": c["code"], **_cfg_out({"event": cfg})}


@admin.get("/{code}/live")
async def live(code: str):
    c = await ev.cohort(code)
    if not c:
        raise HTTPException(404, "Cohort not found")
    return {**await ev.live(c["code"]), **_cfg_out(c), "name": c.get("name", "")}


@admin.get("/{code}/qr.png")
async def link_qr(code: str):
    """The event link as a QR code, for the projector."""
    import io

    import qrcode
    c = await ev.cohort(code)
    if not c:
        raise HTTPException(404, "Cohort not found")
    q = qrcode.QRCode(border=2, box_size=14, error_correction=qrcode.constants.ERROR_CORRECT_M)
    q.add_data(f"{settings.app_url}/event?e={c['code']}")
    q.make(fit=True)
    buf = io.BytesIO()
    q.make_image(fill_color="#14151A", back_color="#FFFFFF").save(buf, "PNG")
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "private, max-age=300"})


@admin.get("/{code}/runs")
async def runs(code: str, status: str = ""):
    q = {"cohort": code.strip().upper()[:40]}
    if status in ("queued", "rules", "ai_queued", "ai", "done", "failed"):
        q["status"] = status
    out = []
    async for r in db().event_runs.find(q, {"_facts": 0}).sort("created_at", -1).limit(500):
        steps = r.get("steps") or {}
        out.append({"id": r["_id"], "business": (r.get("profile") or {}).get("name") or r["input"].get("business_name") or "",
                    "website": (r.get("site") or {}).get("host") or r["input"].get("website_url"), "status": r["status"], "ai": r.get("ai_allowed"),
                    "steps": {k: (v or {}).get("status") for k, v in steps.items()}, "saved": bool(r.get("user_id")),
                    "seconds": round((r["finished_at"] - r["created_at"]).total_seconds(), 1) if r.get("finished_at") else None,
                    "ai_cost_inr": round(float(r.get("ai_cost_inr") or 0), 2), "error": r.get("error"), "created_at": r["created_at"].isoformat()})
    return out


@admin.post("/runs/{run_id}/retry")
async def retry(run_id: str):
    if not await ev.retry(run_id):
        raise HTTPException(400, "Only a pack that failed can be tried again.")
    return {"ok": True}


async def ensure_indexes() -> None:
    await ev.ensure_indexes()
