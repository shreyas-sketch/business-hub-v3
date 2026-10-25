"""Events the rest of the app reports to the automations and AI staff. Each hook is safe to call on every plan: it checks
what is switched on. A hook never breaks the request that called it: errors are logged, not raised."""
import asyncio
import logging
from datetime import timedelta

from .. import plans
from ..config import settings
from ..db import db, now
from . import jobs
from .approvals import expire

log = logging.getLogger("agents")

FOLLOWUP_LEADS_PER_SITE_PER_DAY = 40
INSTANT_REPLIES_PER_DAY = 200
LEAD_RESPONSE_SOURCES = ("website", "guide", "lead_magnet", "email")   # a pasted WhatsApp inquiry is already a WhatsApp chat


async def on_new_lead(owner: dict, business: dict, site: dict | None, lead: dict) -> dict:
    """A customer sent an inquiry (website form or the guide). Returns fields to save on the lead."""
    patch: dict = {}
    # Follow-up automation (Action Program and up): day 1, 3 and 7 follow-ups, each drafted when it falls due.
    try:
        if plans.has(owner, "follow_up_agent") and await jobs.is_on_for(owner, "followup") and await _worth_following_up(site, lead):
            await schedule_followups(owner["_id"], lead)
            patch["followups"] = True
    except Exception:
        log.exception("could not schedule follow-ups for lead %s", lead.get("_id"))
    # Growth Mentorship, Lead Response (v2.2): when the owner switched it on, the first reply carries a booking line and replaces
    # the plain instant reply for website, lead-magnet and email inquiries (an email's number only when its sender is verified).
    # If it can't go, it says why and nothing else is sent.
    lead_response = False
    try:
        if plans.has(owner, "lead_response") and lead.get("source") in LEAD_RESPONSE_SOURCES:
            from ..routers.leadresponse import active
            st = await active(owner)
            if st is not None:
                from .growth import lead_response_reply
                got = await lead_response_reply(owner, business, lead, st)
                patch.update(got)
                # it replaced the plain instant reply — unless it couldn't go for a setup reason (number or template gone since it
                # was switched on): then the plain instant reply below still answers, so the customer isn't left with nothing
                lead_response = (got.get("lead_response") or {}).get("via") not in ("not-connected", "no-template")
    except Exception:
        log.exception("Lead Response failed for lead %s", lead.get("_id"))
    # Growth Mentorship: instant reply from the owner's own number, and the AI Telecaller's call — not for an email inquiry,
    # whose number was read out of an email anyone can write (its proposal goes by email, or waits for a tap).
    by_email = lead.get("source") == "email"
    try:
        if not by_email and not lead_response and plans.has(owner, "whatsapp_ai") and await jobs.is_on_for(owner, "instant_reply"):
            from .growth import instant_reply
            patch["instant_reply"] = await instant_reply(owner, business, lead)
    except Exception:
        log.exception("instant reply failed for lead %s", lead.get("_id"))
    try:
        if not by_email and plans.has(owner, "voice_agent") and await jobs.is_on(owner["_id"], "telecaller"):
            from .growth import schedule_call
            if await schedule_call(owner, lead, minutes=3):
                patch["call_scheduled"] = True
    except Exception:
        log.exception("could not schedule a call for lead %s", lead.get("_id"))
    # Inquiries (v2.1): a proposal for every new inquiry, written in the background so the customer's form never waits
    try:
        if plans.has(owner, "inquiry_desk"):
            from ..routers import inquiries
            if await inquiries.wants_proposal(owner["_id"], lead):
                inquiries.after_lead(owner, business, lead)
    except Exception:
        log.exception("could not start the proposal for lead %s", lead.get("_id"))
    # Running the Business: the lead goes into Employz.ai.
    if plans.has(owner, "crm_sync"):
        await sync_later(owner, {**lead, **patch})
    return patch


async def _worth_following_up(site: dict | None, lead: dict) -> bool:
    """One follow-up sequence per customer number per website per week, and a daily ceiling per website,
    so a flood of form submissions can't use up the owner's AI runs."""
    week_ago, day_ago = now() - timedelta(days=7), now() - timedelta(days=1)
    scope = {"site_id": site["_id"]} if site else {"owner_id": lead["owner_id"]}
    if await db().leads.find_one({**scope, "phone": lead["phone"], "_id": {"$ne": lead["_id"]},
                                  "followups": True, "created_at": {"$gt": week_ago}}, {"_id": 1}):
        return False
    return await db().leads.count_documents({**scope, "followups": True, "created_at": {"$gt": day_ago}}) < FOLLOWUP_LEADS_PER_SITE_PER_DAY


async def schedule_followups(ws: str, lead: dict) -> list[dict]:
    base = lead.get("created_at") or now()
    days = settings.followup_days
    made = []
    for n, d in enumerate(days, 1):
        job = await jobs.schedule(ws, "followup", jobs.working_hours(base + timedelta(days=d)),
                                  {"lead_id": lead["_id"], "n": n, "of": len(days)}, job_id=f"followup:{lead['_id']}:{n}")
        if job:
            made.append(job)
    return made


async def on_lead_status(owner: dict, lead: dict, status: str) -> None:
    """A lead moved to contacted, won or lost."""
    ws, lead_id = owner["_id"], lead.get("_id")
    if not lead_id:
        return
    try:
        if status in ("won", "lost"):
            n = await jobs.cancel({"ws": ws, "agent": {"$in": ["followup", "telecaller"]}, "payload.lead_id": lead_id}, f"Lead marked {status}")
            n += await expire({"ws": ws, "kind": "followup", "lead_id": lead_id}, f"Lead marked {status}")
            if n:
                await jobs.record(ws, "followup", "skipped", f"Stopped following up {lead.get('name') or 'a lead'}: marked {status}.", ref=lead_id)
        if status == "won":
            if plans.has(owner, "review_agent") and await jobs.is_on(ws, "review_request"):
                await jobs.schedule(ws, "review_request", jobs.working_hours(now() + timedelta(days=settings.review_delay_days)),
                                    {"lead_id": lead_id}, job_id=f"review:{lead_id}")
            if plans.has(owner, "customers"):
                from .desk import customer_from_lead
                await customer_from_lead(ws, lead)
        else:
            await jobs.cancel({"ws": ws, "agent": "review_request", "payload.lead_id": lead_id}, f"Lead marked {status}")
            await expire({"ws": ws, "kind": "review", "lead_id": lead_id}, f"Lead marked {status}")
    except Exception:
        log.exception("agents could not react to lead %s becoming %s", lead_id, status)


async def on_lead_moved(owner: dict, lead: dict) -> None:
    """A lead's stage or status changed: keep Employz.ai in step."""
    if plans.has(owner, "crm_sync"):
        await sync_later(owner, lead)


async def sync_later(owner: dict, lead: dict) -> None:
    """Pushes the lead to Employz.ai without slowing down the request (at once in tests)."""
    from .desk import crm_sync

    async def go():
        try:
            await crm_sync(owner, lead["_id"])
        except Exception:
            log.exception("Employz.ai sync failed for lead %s", lead.get("_id"))
    if settings.env == "test":
        await go()  # deterministic in tests
    else:
        asyncio.create_task(go())
