import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pymongo.errors import DuplicateKeyError
from pydantic import BaseModel, Field

from .. import plans
from ..config import settings
from ..db import db, now
from ..messaging import send_otp
from ..security import (client_ip, clear_session, current_user, hash_otp, issue_session, new_otp, normalise_phone,
                        otp_matches, rate_limit)
from ..services import attach_referral, invite_link, new_id, new_ref_code, runs_own_hub, runs_status, track
from ..sites import site_url

router = APIRouter(prefix="/api")
log = logging.getLogger("auth")
MAX_OTP_ATTEMPTS = 5


class OtpIn(BaseModel):
    phone: str = Field(max_length=24)


class VerifyIn(BaseModel):
    phone: str = Field(max_length=24)
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")
    # Attribution comes from links people share and edit, so it is trimmed rather than rejected:
    # a long ?src= value must never stop someone logging in.
    name: str | None = Field(default=None, max_length=200)
    ref: str | None = Field(default=None, max_length=200)
    src: str | None = Field(default=None, max_length=200)
    cohort: str | None = Field(default=None, max_length=200)


def _allowed_country(phone: str) -> bool:
    return any(phone.startswith("+" + cc) for cc in settings.otp_country_codes)


@router.post("/auth/otp")
async def request_otp(body: OtpIn, request: Request):
    return await send_login_code(normalise_phone(body.phone), request)


async def send_login_code(phone: str, request: Request) -> dict:
    """The one login-code sender (the login page and "save your Day-1 report" both use it)."""
    if not _allowed_country(phone):
        raise HTTPException(400, "Please use an Indian mobile number.")
    await rate_limit(f"otp:{phone}", 5, 3600, "Too many codes requested for this number. Try again in an hour.")
    # Generous per-network limit: a whole workshop room can share one Wi-Fi address.
    await rate_limit(f"otp-ip:{client_ip(request)}", settings.otp_per_ip_per_hour, 3600, "Too many attempts from this network. Try again later.")
    code = new_otp()
    await db().otps.delete_many({"phone": phone})
    await db().otp_attempts.delete_many({"phone": phone})
    await db().otps.insert_one({"_id": new_id(), "phone": phone, "hash": hash_otp(phone, code),
                                "expires_at": now() + timedelta(minutes=10)})
    result = await send_otp(phone, code)
    if not result["sent"]:
        raise HTTPException(503, "We couldn't send the code right now. Please try again in a minute.")
    out = {"sent": True, "via": result["via"]}
    if result["via"].startswith("dev-"):
        out["dev_code"] = code  # development only: no WhatsApp/SMS keys configured
    return out


@router.post("/auth/verify")
async def verify_otp(body: VerifyIn, request: Request, response: Response):
    _, created = await login_with_code(normalise_phone(body.phone), body, request, response)
    return {"ok": True, "new": created}


async def login_with_code(phone: str, body: VerifyIn, request: Request, response: Response) -> tuple[dict, bool]:
    """Checks the code, creates the account the first time (Free plan), starts the session. Day-1 reports made with
    this number are attached here, whichever way the person logs in."""
    await rate_limit(f"verify-ip:{client_ip(request)}", settings.otp_per_ip_per_hour * 2, 3600, "Too many attempts from this network. Try again later.")
    otp = await db().otps.find_one({"phone": phone})
    if not otp or otp["expires_at"] < now():
        raise HTTPException(400, "This code has expired. Ask for a new one.")
    if not await _claim_attempt(otp):
        raise HTTPException(429, "Too many wrong codes. Ask for a new one.")
    if not otp_matches(phone, body.code, otp["hash"]):
        raise HTTPException(400, "That code is not right. Check the message and try again.")
    deleted = await db().otps.delete_one({"_id": otp["_id"]})
    if not deleted.deleted_count:  # the same code was used twice at once; only the first wins
        raise HTTPException(400, "This code has expired. Ask for a new one.")

    user = await db().users.find_one({"phone": phone})
    created = False
    if not user:
        user, created = await _create_user(phone, body)
    elif not user.get("team_of"):
        user = await _accept_team_invite(user)
    if user.get("disabled"):
        raise HTTPException(403, "This account has been paused. Please contact the Business AI team.")
    role = "admin" if phone in settings.admin_phones else "owner"  # ADMIN_PHONES is the single source of truth
    await db().users.update_one({"_id": user["_id"]}, {"$set": {"last_seen_at": now(), "role": role}})
    issue_session(response, user)
    from ..day1_runs import attach_runs  # the number is now proven, so reports made with it can be opened here
    try:
        await attach_runs(user)
    except Exception:  # noqa: BLE001  logging in must never depend on the Day-1 report
        log.exception("attaching Day-1 reports failed for %s", user["_id"])
    return user, created


async def _claim_attempt(otp: dict) -> bool:
    """Each guess must first claim one of MAX_OTP_ATTEMPTS numbered slots. Slot ids are unique, so even
    hundreds of guesses sent at the same instant get at most MAX_OTP_ATTEMPTS comparisons, on any database."""
    for n in range(MAX_OTP_ATTEMPTS):
        try:
            await db().otp_attempts.insert_one({"_id": f"{otp['_id']}:{n}", "phone": otp["phone"], "at": now()})
            return True
        except DuplicateKeyError:
            continue
    return False


async def _accept_team_invite(user: dict) -> dict:
    """Someone who signed up on their own but never set up a business can still join a team they were invited to."""
    invite = await db().team_invites.find_one({"phone": user["phone"], "status": "pending"})
    if not invite or user.get("disabled") or await runs_own_hub(user):  # never fold an existing hub into someone else's team
        return user
    await db().users.update_one({"_id": user["_id"]}, {"$set": {"team_of": invite["owner_id"], "team_role": invite.get("role", "staff"),
                                                                "name": user.get("name") or invite.get("name", "")}})
    await db().team_invites.update_one({"_id": invite["_id"]}, {"$set": {"status": "joined", "user_id": user["_id"], "joined_at": now()}})
    await track("team_join", invite["owner_id"], member=user["_id"])
    return await db().users.find_one({"_id": user["_id"]})


async def _create_user(phone: str, body: VerifyIn) -> tuple[dict, bool]:
    invite = await db().team_invites.find_one({"phone": phone, "status": "pending"})
    cohort = None
    if body.cohort:
        c = await db().cohorts.find_one({"code": body.cohort.strip().upper()[:40]})
        cohort = c["code"] if c else None
    body.ref = (body.ref or "").strip()[:12] or None
    body.src = (body.src or "").strip().lower()[:24] or None
    user = {"_id": new_id(), "phone": phone, "name": (body.name or "").strip()[:80], "plan": "free", "bonus_runs": 0,
            "role": "admin" if phone in settings.admin_phones else "owner", "cohort": cohort, "session_version": 0, "created_at": now()}
    if invite:  # joining someone's team, not starting their own hub
        user.update(team_of=invite["owner_id"], team_role=invite.get("role", "staff"), name=user["name"] or invite.get("name", ""), cohort=None)
    for _ in range(5):
        user["ref_code"] = new_ref_code()
        try:
            await db().users.insert_one(user)
            break
        except DuplicateKeyError:
            existing = await db().users.find_one({"phone": phone})
            if existing:  # the same person verified twice at once
                return existing, False
    else:
        raise HTTPException(500, "Could not create the account. Please try again.")
    if invite:
        await db().team_invites.update_one({"_id": invite["_id"]}, {"$set": {"status": "joined", "user_id": user["_id"], "joined_at": now()}})
        await track("team_join", invite["owner_id"], member=user["_id"])
        return await db().users.find_one({"_id": user["_id"]}), True
    await track("signup", user["_id"], cohort=cohort, src=body.src or ("cohort" if cohort else "direct"))
    await attach_referral(user, body.ref, body.src)
    return await db().users.find_one({"_id": user["_id"]}), True


@router.post("/auth/logout")
async def logout(response: Response):
    clear_session(response)
    return {"ok": True}


@router.get("/me")
async def me(user: dict = Depends(current_user)):
    owner, role = user, "owner"
    if user.get("team_of"):
        owner = await db().users.find_one({"_id": user["team_of"]}) or {}
        role = user.get("team_role", "staff")
        if not owner or owner.get("disabled") or not plans.has(owner, "team_logins"):
            return {"blocked": "Your team access is paused. Please ask the business owner.",
                    "user": {"id": user["_id"], "phone": user["phone"], "name": user.get("name", ""), "role": "owner"}}
    ws = owner["_id"]
    business = await db().businesses.find_one({"owner_id": ws}) or {}
    site = await db().sites.find_one({"owner_id": ws}, {"status": 1, "slug": 1, "published_at": 1, "domain": 1})
    leads = await db().leads.count_documents({"owner_id": ws})
    notices = [{"id": n["_id"], "text": n["text"], "at": n["at"].isoformat()}
               async for n in db().notices.find({"user_id": user["_id"], "read": False}).sort("at", -1).limit(5)]
    effective = plans.effective_plan(owner)
    trial = owner.get("trial") or {}
    sources = plans.plan_sources(owner)
    sub = owner.get("sub") or {}
    approvals = await db().approvals.count_documents({"ws": ws, "status": "pending"}) if role != "staff" else 0
    my_tasks = await db().tasks.count_documents({"ws": ws, "assignee_id": user["_id"], "status": "open"})
    from .. import knowledge
    from .dashboard import landing_for
    kh = (await knowledge.completeness_for(ws, business))["percent"] if business else 0
    return {
        "user": {"id": user["_id"], "phone": user["phone"], "name": user.get("name", ""),
                 "role": "admin" if user.get("role") == "admin" and user["phone"] in settings.admin_phones else "owner",
                 "plan": owner.get("plan", "free"), "effective_plan": effective, "plan_name": plans.PLANS[effective]["name"],
                 "plan_source": plans.plan_source(owner),
                 "trial_until": trial["until"].isoformat() if trial.get("until") and sources.get("trial") == effective != owner.get("plan", "free") else None,
                 "access_until": (owner.get("access") or {})[effective].isoformat() if (owner.get("access") or {}).get(effective) else None,
                 "subscription": {"status": sub.get("status"), "paid_until": sub["paid_until"].isoformat() if sub.get("paid_until") else None} if sub else None},
        "workspace": {"role": role, "owner_name": owner.get("name", ""), "is_team": role != "owner"},
        "runs": await runs_status(owner),
        "features": {k: plans.has(owner, k) for k in plans.FEATURES},
        "feature_tiers": {k: v[0] for k, v in plans.FEATURES.items()},
        "feature_info": {k: {"label": m["label"], "what": m["what"], "on": m["on"]} for k, m in plans.META.items()},
        "menu": plans.menu_for(owner, role),
        "tiers": {t: {"name": p["name"], "short": p["short"], "price_minor": p["price_minor"], "period": p["period"]} for t, p in plans.PLANS.items()},
        "sites_domain": settings.sites_domain or None,
        "progress": {"profile": bool(business.get("name") and business.get("industry")),
                     "brand": bool((business.get("brand") or {}).get("message")),
                     "site_live": bool(site and site.get("status") == "live"), "first_lead": leads > 0},
        "business_name": business.get("name", ""),
        "site": {"slug": site["slug"], "status": site["status"], "url": site_url(site["slug"])} if site else None,
        "invite_link": invite_link(owner) if role == "owner" else None,
        "notices": notices,
        "approvals_waiting": approvals,
        "my_open_tasks": my_tasks,
        "knowledge_percent": kh,
        "landing": await landing_for(owner, role, kh),
        "program": {"name": settings.program_name, "speakers": settings.speaker_line},
        **_v23_me(owner),
    }


def _v23_me(owner: dict) -> dict:
    """v2.3 (OwnerDesk): the engines and their event days, why a feature is closed (a later event day, or members only),
    the member's access, and the links every member sees (community, support, how to pay)."""
    trial = plans.event_trial(owner)
    locks = {}
    for k, m in plans.META.items():
        if not m["on"] or plans.has(owner, k):
            continue
        if plans.day_locked(owner, k):
            locks[k] = {"lock": "day", "opens_day": plans.feature_day(k)}
        elif m.get("paid_only") and plans.rank(plans.effective_plan(owner)) >= plans.rank(m["tier"]):
            locks[k] = {"lock": "paid"}
    lite = plans.PLANS["lite"]
    access_until = (owner.get("access") or {}).get("lite")
    return {
        "ui": {"menu": plans.UI.get("menu", "groups")},
        "engines": plans.engines_for(owner),
        "feature_locks": locks,
        "paid": plans.is_paid(owner),
        "event": {"cohort": trial.get("cohort"), "day": plans.event_day(owner), "until": trial["until"].isoformat()} if trial else None,
        "membership": {"name": lite["name"], "price_minor": lite["price_minor"], "price_year_minor": lite.get("price_year_minor", 0),
                       "runs": lite["runs"] + lite.get("extra_runs", 0), "images": lite.get("images", 30),
                       "paid_until": access_until.isoformat() if hasattr(access_until, "isoformat") else None},
        "images": {"allowance": plans.monthly_images(owner)},
        # the community group is for members; support and the payment links are for everyone
        "links": {**{k: plans.UI.get(k, "") for k in ("support_email", "pay_monthly_link", "pay_yearly_link")},
                  "community_link": plans.UI.get("community_link", "") if plans.is_paid(owner) else ""},
    }


class NoticesIn(BaseModel):
    ids: list[str] | None = Field(default=None, max_length=20)  # the notices the owner actually saw; empty = all


@router.post("/notices/read")
async def read_notices(body: NoticesIn | None = None, user: dict = Depends(current_user)):
    query = {"user_id": user["_id"], "read": False}
    if body and body.ids:
        query["_id"] = {"$in": [str(i)[:40] for i in body.ids]}
    await db().notices.update_many(query, {"$set": {"read": True}})
    return {"ok": True}
