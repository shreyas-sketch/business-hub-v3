"""AI runs (allowance + bonus runs), events, notices and referral rewards."""
import logging
import secrets
import uuid
from datetime import timedelta

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from . import plans
from .ai.provider import AIError, Usage, cost_inr
from .config import settings
from .db import db, month_key, now
from .text import greeting_name  # noqa: F401  (re-exported for routers)

log = logging.getLogger("hub")


def new_id() -> str:
    return uuid.uuid4().hex


REF_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def new_ref_code() -> str:
    return "".join(secrets.choice(REF_ALPHABET) for _ in range(7))


def invite_link(user: dict, src: str = "invite") -> str:
    return f"{settings.app_url}/join?ref={user['ref_code']}&src={src}"


async def track(event: str, user_id: str | None = None, **meta) -> None:
    await db().events.insert_one({"_id": new_id(), "type": event, "user_id": user_id, "meta": meta, "at": now()})


async def notify(user_id: str, text: str) -> None:
    await db().notices.insert_one({"_id": new_id(), "user_id": user_id, "text": text, "read": False, "at": now()})


# ───────────────────────── runs ─────────────────────────
async def runs_status(user: dict) -> dict:
    allowance = plans.monthly_runs(user)
    usage = await db().usage.find_one({"user_id": user["_id"], "month": month_key()})
    used = (usage or {}).get("used", 0)
    bonus = user.get("bonus_runs", 0)
    return {"allowance": allowance, "used": used, "bonus": bonus, "left": max(allowance - used, 0) + bonus, "month": month_key()}


def limit_payload(user: dict) -> dict:
    """The only place the product asks for an invite: when the owner has run out (Bullzeye rule — ask at limits)."""
    return {"code": "runs_exhausted",
            "message": "You've used this month's AI runs.",
            "invite": {"link": invite_link(user), "reward": f"+{settings.referrer_bonus_runs} runs for every owner whose website goes live"},
            "upgrade": {"tier": "lite", **plans.PLANS["lite"]}}


async def spend_run(user: dict, kind: str) -> dict:
    month = month_key()
    allowance = plans.monthly_runs(user)
    try:
        await db().usage.update_one({"user_id": user["_id"], "month": month}, {"$setOnInsert": {"used": 0}}, upsert=True)
    except DuplicateKeyError:
        pass  # two jobs started at the same moment; the other request created this month's counter

    took = await db().usage.find_one_and_update({"user_id": user["_id"], "month": month, "used": {"$lt": allowance}},
                                                 {"$inc": {"used": 1}})
    source = "monthly"
    if not took:
        bonus = await db().users.find_one_and_update({"_id": user["_id"], "bonus_runs": {"$gt": 0}}, {"$inc": {"bonus_runs": -1}})
        if not bonus:
            await track("limit_hit", user["_id"], kind=kind)
            raise HTTPException(402, limit_payload(user))
        source = "bonus"
    run = {"_id": new_id(), "user_id": user["_id"], "kind": kind, "month": month, "source": source, "status": "pending", "created_at": now()}
    await db().runs.insert_one(run)
    return run


async def refund_run(run: dict) -> None:
    if run["source"] == "monthly":
        await db().usage.update_one({"user_id": run["user_id"], "month": run["month"]}, {"$inc": {"used": -1}})
    else:
        await db().users.update_one({"_id": run["user_id"]}, {"$inc": {"bonus_runs": 1}})
    await db().runs.update_one({"_id": run["_id"]}, {"$set": {"status": "refunded"}})


async def refund_latest(user: dict, kind: str) -> bool:
    """Gives back the owner's most recent finished run of this kind — for a result that turned out to change nothing."""
    run = await db().runs.find_one({"user_id": user["_id"], "kind": kind, "status": "done"}, sort=[("created_at", -1)])
    if not run:
        return False
    await refund_run(run)
    return True


async def note_ai_failure(kind: str, error: Exception) -> None:
    """Keep the latest engine failure, so the admin's AI check can say what went wrong for an owner. Never breaks the caller."""
    try:
        await db().config.update_one({"_id": "ai_last_failure"}, {"$set": {"at": now(), "kind": kind, "error": str(error)[:300]}}, upsert=True)
    except Exception:  # noqa: BLE001 — a note about a failure must not cause another one
        log.exception("could not note the AI failure")


async def run_ai(user: dict, kind: str, job, title: str, save_output: bool = True, check=None) -> dict:
    """Spends one run, calls the AI job, records tokens and cost, refunds on failure, saves to My Outputs.
    `check` (optional) shapes the AI result; if it raises, the draft is unusable and the run is refunded."""
    try:
        run = await spend_run(user, kind)
    except BaseException:
        job.close()  # the AI call never starts when the owner has no runs left
        raise
    try:
        result, usage = await job
    except AIError as e:
        log.warning("AI %s failed, run refunded: %s", kind, e)
        await refund_run(run)
        await note_ai_failure(kind, e)
        raise HTTPException(502, "The AI is busy right now. Your run was not used — please try again in a minute.")
    except Exception:
        await refund_run(run)
        raise
    if check:
        try:
            result = check(result)
        except Exception as e:
            log.warning("AI %s draft rejected, run refunded: %s", kind, str(e)[:300])
            await refund_run(run)
            raise HTTPException(502, "The AI draft came back incomplete. Your run was not used — please try once more.")
    usage = usage or Usage()
    await db().runs.update_one({"_id": run["_id"]}, {"$set": {"status": "done", "model": usage.model, "input_tokens": usage.input_tokens,
                                                              "output_tokens": usage.output_tokens, "cost_inr": cost_inr(usage)}})
    await track("run", user["_id"], kind=kind)
    if save_output:
        await db().outputs.insert_one({"_id": new_id(), "user_id": user["_id"], "kind": kind, "title": title[:140],
                                       "content": result, "created_at": now()})
    return result


# ───────────────────────── referral ─────────────────────────
async def attach_referral(friend: dict, ref_code: str | None, src: str | None) -> None:
    """New owner joined through someone's invite link or website badge: they start with a membership trial."""
    if not ref_code:
        return
    referrer = await db().users.find_one({"ref_code": ref_code.strip().upper()})
    if not referrer or referrer["_id"] == friend["_id"]:
        return
    until = now() + timedelta(days=settings.friend_trial_days)
    await db().users.update_one({"_id": friend["_id"]}, {"$set": {
        "referred_by": referrer["_id"], "referral_source": src if src in ("invite", "badge", "showcase", "score", "card") else "invite",
        "trial": {"plan": "lite", "until": until, "reason": "welcome"}}})
    await track("referral_join", referrer["_id"], friend_id=friend["_id"], src=src or "invite")
    if settings.friend_trial_days:
        await notify(friend["_id"], f"Welcome gift: {settings.friend_trial_days} days of Membership features, from your invite.")


async def reward_referrer(friend: dict) -> None:
    """Called once, the first time a referred owner's website goes live. First live friend = membership days; then bonus runs."""
    referrer_id = friend.get("referred_by")
    if not referrer_id:
        return
    referrer = await db().users.find_one({"_id": referrer_id})
    if not referrer:
        return
    earlier = await db().referral_rewards.count_documents({"referrer_id": referrer_id})
    reward = {"_id": new_id(), "referrer_id": referrer_id, "friend_id": friend["_id"], "at": now()}
    if earlier == 0 and settings.referrer_first_reward_days:
        reward.update(kind="membership_days", days=settings.referrer_first_reward_days)
    else:
        reward.update(kind="bonus_runs", runs=settings.referrer_bonus_runs)
    try:
        await db().referral_rewards.insert_one(reward)
    except DuplicateKeyError:  # already rewarded for this friend
        return
    business = await db().businesses.find_one({"owner_id": friend["_id"]}) or {}
    who = business.get("name") or "An owner you invited"
    if reward["kind"] == "membership_days":
        current = referrer.get("trial") or {}
        start = max(current.get("until") or now(), now()) if current.get("plan") == "lite" else now()
        await db().users.update_one({"_id": referrer_id}, {"$set": {"trial": {"plan": "lite", "until": start + timedelta(days=reward["days"]), "reason": "first_friend"}}})
        await notify(referrer_id, f"{who} just went live. You've unlocked {reward['days']} days of Membership features.")
    else:
        await db().users.update_one({"_id": referrer_id}, {"$inc": {"bonus_runs": reward["runs"]}})
        await notify(referrer_id, f"{who} just went live. +{reward['runs']} AI runs added to your account.")
    await track("referral_reward", referrer_id, friend_id=friend["_id"], kind=reward["kind"])


async def runs_own_hub(user: dict) -> bool:
    """True when a number already has a hub of its own — a business profile, a paid plan or trial, or recordings the
    admin gave it — so it must never be folded into someone else's team."""
    from . import plans
    if any(t != "free" for t in plans.plan_sources(user).values()):
        return True
    if await db().businesses.find_one({"owner_id": user["_id"]}, {"_id": 1}):
        return True
    return bool(await db().programs.find_one({"allowed": user["_id"]}, {"_id": 1}))
