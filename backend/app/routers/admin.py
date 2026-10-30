"""Admin: Pulse (activation → stickiness → referral), owners, workshop cohorts."""
import re
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import plans
from ..config import settings
from ..db import db, now, public
from ..security import require_admin
from ..services import notify, track

router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])


async def _count(coll: str, q: dict) -> int:
    return await db()[coll].count_documents(q)


@router.get("/pulse")
async def pulse(days: int = 7):
    days = max(1, min(days, 90))
    since = now() - timedelta(days=days)
    d = db()
    owners = {"team_of": {"$exists": False}}  # team members log in to someone else's hub; they are not owners
    users_total = await _count("users", owners)
    users_at_start = await _count("users", {**owners, "created_at": {"$lt": since}})
    signups = await _count("users", {**owners, "created_at": {"$gte": since}})
    referred = await _count("users", {**owners, "created_at": {"$gte": since}, "referred_by": {"$exists": True}})
    by_source = {}
    async for e in d.events.find({"type": "signup", "at": {"$gte": since}}, {"meta": 1}):
        src = (e.get("meta") or {}).get("src") or "direct"
        by_source[src] = by_source.get(src, 0) + 1
    visits = {"invite": 0, "badge": 0, "showcase": 0}
    async for e in d.events.find({"type": "invite_visit", "at": {"$gte": since}}, {"meta": 1}):
        src = (e.get("meta") or {}).get("src", "invite")
        visits[src] = visits.get(src, 0) + 1
    profile = await _count("businesses", {"industry": {"$exists": True}})
    brand = await _count("businesses", {"brand.message": {"$exists": True}})
    live = await _count("sites", {"first_live_at": {"$exists": True}})
    with_lead = len(await d.leads.distinct("owner_id"))
    runs_window = [r async for r in d.runs.find({"created_at": {"$gte": since}, "status": "done"}, {"cost_inr": 1, "kind": 1})]
    upgrade_clicks = {}
    async for e in d.events.find({"type": "upgrade_click", "at": {"$gte": since}}, {"meta": 1}):
        t = (e.get("meta") or {}).get("tier", "lite")
        upgrade_clicks[t] = upgrade_clicks.get(t, 0) + 1
    plan_mix = {t: 0 for t in plans.TIERS}
    trials = 0
    async for u in d.users.find(owners, {"plan": 1, "trial": 1, "sub": 1, "access": 1, "phone": 1, "welcome": 1}):
        plan_mix[plans.effective_plan(u)] += 1
        trials += plans.plan_source(u) == "trial"
    cohorts = []
    async for c in d.cohorts.find().sort("created_at", -1).limit(20):
        ids = [u["_id"] async for u in d.users.find({"cohort": c["code"]}, {"_id": 1})]
        cohorts.append({"code": c["code"], "name": c.get("name", ""), "workshop_date": c.get("workshop_date"),
                        "signups": len(ids), "sites_live": await _count("sites", {"owner_id": {"$in": ids}, "first_live_at": {"$exists": True}}),
                        "brought_in": await _count("users", {"referred_by": {"$in": ids}})})
    return {
        "days": days,
        "totals": {"owners": users_total, "signups": signups, "referred_signups": referred,
                   "leads": await _count("leads", {"created_at": {"$gte": since}}), "leads_all_time": await _count("leads", {})},
        "activation": {"owners": users_total, "profile": profile, "brand": brand, "site_live": live, "first_lead": with_lead},
        "viral": {"k": round(referred / users_at_start, 2) if users_at_start else None, "visits": visits, "by_source": by_source,
                  "rewards": await _count("referral_rewards", {"at": {"$gte": since}}),
                  "definition": f"K = owners who joined through a member's invite link or website badge in the last {days} days ÷ owners who existed at the start of that period."},
        "ai": {"runs": len(runs_window), "cost_inr": round(sum(r.get("cost_inr", 0) or 0 for r in runs_window), 2),
               "limit_hits": await _count("events", {"type": "limit_hit", "at": {"$gte": since}})},
        "revenue_intent": {"upgrade_clicks": upgrade_clicks},
        "plans": {"mix": plan_mix, "trials": trials},
        "cohorts": cohorts,
    }


@router.get("/users")
async def users(q: str = "", limit: int = 100):
    query = {}
    if q:
        safe = re.escape(q.strip()[:40])
        biz_ids = [b["owner_id"] async for b in db().businesses.find({"name": {"$regex": safe, "$options": "i"}}, {"owner_id": 1})]
        query = {"$or": [{"phone": {"$regex": safe}}, {"_id": {"$in": biz_ids}}, {"cohort": q.strip().upper()}]}
    found = [u async for u in db().users.find(query).sort("created_at", -1).limit(max(1, min(limit, 500)))]
    ids = [u["_id"] for u in found]
    # One query per collection (not per owner), so the list stays fast when the app and database are far apart
    names = {b["owner_id"]: b.get("name", "") async for b in db().businesses.find({"owner_id": {"$in": ids}}, {"owner_id": 1, "name": 1})}
    sites = {s["owner_id"]: s async for s in db().sites.find({"owner_id": {"$in": ids}}, {"owner_id": 1, "status": 1, "slug": 1})}
    leads = {g["_id"]: g["n"] async for g in db().leads.aggregate([{"$match": {"owner_id": {"$in": ids}}}, {"$group": {"_id": "$owner_id", "n": {"$sum": 1}}}])}
    brought = {g["_id"]: g["n"] async for g in db().users.aggregate([{"$match": {"referred_by": {"$in": ids}}}, {"$group": {"_id": "$referred_by", "n": {"$sum": 1}}}])}
    team_owner_ids = list({u["team_of"] for u in found if u.get("team_of")})
    team_names = {b["owner_id"]: b.get("name", "") async for b in db().businesses.find({"owner_id": {"$in": team_owner_ids}}, {"owner_id": 1, "name": 1})}
    out = []
    for u in found:
        s = sites.get(u["_id"], {})
        out.append({"id": u["_id"], "phone": u["phone"], "business": names.get(u["_id"], ""), "cohort": u.get("cohort"),
                    "plan": u.get("plan", "free"), "effective_plan": plans.effective_plan(u), "plan_source": plans.plan_source(u),
                    "bonus_runs": u.get("bonus_runs", 0),
                    "site": s.get("status"), "slug": s.get("slug"), "leads": leads.get(u["_id"], 0),
                    "referred": bool(u.get("referred_by")), "brought_in": brought.get(u["_id"], 0), "disabled": bool(u.get("disabled")),
                    "role": u.get("role", "owner"), "created_at": u["created_at"].isoformat(),
                    "team": {"of": team_names.get(u["team_of"], ""), "role": u.get("team_role", "staff"), "name": u.get("name", "")} if u.get("team_of") else None,
                    "grants": [k for k, v in (u.get("grants") or {}).items() if plans.granted(u, k)]})
    return out


@router.get("/owners/{user_id}")
async def owner_detail(user_id: str):
    """One owner on a page: plan and where it comes from, extras the admin gave, website, and (Growth Mentorship and up)
    the setup and connections the team looks after."""
    u = await db().users.find_one({"_id": user_id})
    if not u or u.get("team_of"):
        raise HTTPException(404, "Owner not found")
    from ..sites import site_url
    b = await db().businesses.find_one({"owner_id": user_id}) or {}
    s = await db().sites.find_one({"owner_id": user_id}, {"slug": 1, "status": 1, "domain": 1}) or {}
    access = {t: v.isoformat() for t, v in (u.get("access") or {}).items() if hasattr(v, "isoformat")}
    grants = [{"feature": k, "label": plans.label_of(k), "until": v.isoformat(), "active": plans.granted(u, k)}
              for k, v in (u.get("grants") or {}).items() if hasattr(v, "isoformat") and k in plans.META]
    team = await db().users.count_documents({"team_of": user_id})
    from .. import auto_quote   # the last inquiries and what happened to each one's draft quotation
    leads = [x async for x in db().leads.find({"owner_id": user_id}, {"name": 1, "phone": 1, "message": 1, "created_at": 1, "auto_quote": 1,
                                                                   "quote_id": 1}).sort("created_at", -1).limit(10)]
    numbers = await auto_quote.numbers_for(user_id, leads)
    return {"inquiries": [{"id": x["_id"], "name": x.get("name", ""), "phone": x.get("phone", ""), "message": (x.get("message") or "")[:160],
                           "created_at": x["created_at"].isoformat(), "quote": auto_quote.status_view(x, numbers)} for x in leads],
            "quotations_open": plans.has(u, "quotations"), "quotations_why": None if plans.has(u, "quotations") else auto_quote.no_feature_reason(u),"id": u["_id"], "phone": u["phone"], "name": u.get("name", ""), "business": b.get("name", ""), "city": b.get("city", ""),
            "industry": b.get("industry", ""), "plan": u.get("plan", "free"), "effective_plan": plans.effective_plan(u),
            "plan_name": plans.PLANS[plans.effective_plan(u)]["name"], "plan_source": plans.plan_source(u), "access": access,
            "welcome": plans.welcome_active(u),
            "grants": grants, "disabled": bool(u.get("disabled")), "bonus_runs": u.get("bonus_runs", 0), "cohort": u.get("cohort"),
            "site": {"slug": s.get("slug"), "status": s.get("status"), "url": site_url(s["slug"]) if s.get("slug") else None,
                     "domain": (s.get("domain") or {}).get("name")} if s else None,
            "team": team, "created_at": u["created_at"].isoformat(),
            "features": [{"key": k, "label": m["label"], "tier": m["tier"], "has": plans.has(u, k)} for k, m in plans.META.items() if m["on"]]}


class UserPatch(BaseModel):
    plan: str | None = Field(default=None, pattern="^(free|lite|program|running|growth|office)$")
    add_bonus_runs: int | None = Field(default=None, ge=-1000, le=1000)
    disabled: bool | None = None
    welcome: bool | None = None   # the full hub given at signup: switched off (or back on) for this owner


@router.patch("/users/{user_id}")
async def patch_user(user_id: str, body: UserPatch, admin: dict = Depends(require_admin)):
    user = await db().users.find_one({"_id": user_id})
    if not user:
        raise HTTPException(404, "Owner not found")
    update: dict = {}
    if body.plan:
        update.setdefault("$set", {})["plan"] = body.plan
    if body.disabled is not None:
        update.setdefault("$set", {})["disabled"] = body.disabled
        update.setdefault("$inc", {})["session_version"] = 1
    if body.add_bonus_runs:
        update.setdefault("$inc", {})["bonus_runs"] = body.add_bonus_runs
    if body.welcome is not None:
        if user.get("team_of"):
            raise HTTPException(400, "Team members use the business owner's plan.")
        update.setdefault("$set", {}).update({"welcome.on": body.welcome, "welcome.changed_at": now(), "welcome.changed_by": admin["_id"]})
    if update:
        await db().users.update_one({"_id": user_id}, update)
        await track("admin_change", user_id, by=admin["_id"], **body.model_dump(exclude_none=True))
    if body.plan and body.plan != user.get("plan", "free"):
        await notify(user_id, f"Your hub is now on {plans.PLANS[body.plan]['name']}.")
    return {"ok": True}


class CohortIn(BaseModel):
    code: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9-]+$")
    name: str = Field(default="", max_length=120)
    workshop_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


@router.post("/ai/check")
async def ai_check():
    """One tiny real call to the AI engine, so the admin can see whether it works and, when it doesn't, the reason and the fix.
    Uses no owner's runs; with AI_PROVIDER=mock it says the hub is on demo text."""
    import time
    from ..ai import provider
    model = settings.ai_model or provider.DEFAULT_MODEL.get(settings.ai_provider, "")
    out = {"provider": settings.ai_provider, "model": model, "effort": settings.ai_effort, "key_set": bool(
        {"anthropic": settings.anthropic_api_key, "gemini": settings.gemini_api_key, "openai": settings.openai_api_key}.get(settings.ai_provider, "x"))}
    if settings.ai_provider != "mock":
        out["engines"] = [e.label for e in provider.engines()]   # the main engine's keys, then the backups (AI_FALLBACK)
    last = await db().config.find_one({"_id": "ai_last_failure"})
    if last:
        out["last_failure"] = {"at": last["at"], "kind": last["kind"], "error": last["error"], "fix": provider.explain(last["error"])}
    if settings.ai_provider == "mock":
        return {**out, "ok": False, "error": "AI_PROVIDER is mock", "fix": "The hub is writing fixed demo text, not real AI. Set AI_PROVIDER=anthropic and ANTHROPIC_API_KEY in Railway → Variables."}
    t = time.monotonic()
    try:
        _, usage = await provider.complete("You check that the line works.", 'Reply with {"ok": true}.', lambda: {"ok": True}, 50)
    except provider.AIError as e:
        return {**out, "ok": False, "error": str(e)[:300], "fix": provider.explain(str(e))}
    return {**out, "ok": True, "seconds": round(time.monotonic() - t, 1), "tokens": usage.input_tokens + usage.output_tokens, "answered_by": usage.model}


@router.get("/cohorts")
async def list_cohorts():
    return [{**public(c), "join_link": f"{settings.app_url}/join?c={c['code']}"} async for c in db().cohorts.find().sort("created_at", -1)]


@router.post("/cohorts")
async def create_cohort(body: CohortIn):
    code = body.code.upper()
    try:
        await db().cohorts.insert_one({"_id": code, "code": code, "name": body.name, "workshop_date": body.workshop_date, "created_at": now()})
    except Exception:
        raise HTTPException(409, "A cohort with this code already exists")
    return {"code": code, "join_link": f"{settings.app_url}/join?c={code}"}


# ───────────────────────── Email (v2.1, 4.5) ─────────────────────────
@router.get("/email")
async def email_admin(days: int = 30):
    """Sending-domain health (SPF / DKIM / DMARC), bounce and complaint rates per owner, and who is paused."""
    from .. import mailer
    days = max(1, min(days, 90))
    rows = await mailer.rates(days)
    ids = [r["owner_id"] for r in rows]
    paused = {u["_id"]: u async for u in db().users.find({"email_paused": True}, {"name": 1, "phone": 1, "email_paused_at": 1})}
    names = {u["_id"]: u async for u in db().users.find({"_id": {"$in": ids + list(paused)}}, {"name": 1, "phone": 1})}
    biz = {b["owner_id"]: b.get("name", "") async for b in db().businesses.find({"owner_id": {"$in": ids + list(paused)}}, {"owner_id": 1, "name": 1})}
    for r in rows:
        u = names.get(r["owner_id"]) or {}
        r.update(name=u.get("name", ""), business=biz.get(r["owner_id"], ""), paused=r["owner_id"] in paused)
    return {"health": await mailer.health(), "days": days, "owners": rows,
            "paused": [{"owner_id": k, "name": v.get("name", ""), "business": biz.get(k, ""),
                        "at": v["email_paused_at"].isoformat() if v.get("email_paused_at") else None} for k, v in paused.items()],
            "thresholds": {"bounce_rate": 5, "complaint_rate": 0.1}}


class EmailPauseIn(BaseModel):
    paused: bool


@router.post("/owners/{user_id}/email-pause")
async def email_pause(user_id: str, body: EmailPauseIn, admin: dict = Depends(require_admin)):
    """Stops (or restarts) one owner's automatic emails — follow-ups, proposals and invoices."""
    r = await db().users.update_one({"_id": user_id, "team_of": {"$exists": False}},
                                    {"$set": {"email_paused": body.paused, "email_paused_at": now() if body.paused else None, "email_paused_by": admin["_id"]}})
    if not r.matched_count:
        raise HTTPException(404, "Not found")
    if body.paused:
        await notify(user_id, "Automatic emails from your hub are paused by the Business AI team. Your hub still works; we'll be in touch.")
    await track("email_paused" if body.paused else "email_resumed", user_id)
    return {"ok": True, "paused": body.paused}
