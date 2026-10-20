"""
Inquiries + automatic proposal (v2.1, 3.6; decision D3), and replies to the hub's emails (3.8.4).

One inbox for every inquiry (website, lead magnet, email, WhatsApp, added by hand) — these are leads, so they sit in the
pipeline at its first stage. On every new inquiry the hub writes a proposal from the Business Brain and the Company Brain
(prices only when the owner wrote them; no match → one or two qualifying questions), then:

    email inquiry (or any inquiry that left an email)   sent automatically by email when the owner switched
                                                         "Auto-reply to email inquiries" on; otherwise a draft to send with a tap
    a phone number                                       a WhatsApp proposal in the Send list, tap-to-send (every plan: a proposal
                                                         carries prices, so it waits for a person)
Limits: PROPOSALS_PER_OWNER_PER_DAY automatic emails, and one automatic reply per sender per 24 hours. A proposal with a
link always waits. The hub's own WhatsApp number never writes to a customer.

inquiry_settings {_id: ws, handle, auto_email, updated_at}       the inquiry address is <handle>@INBOUND_EMAIL_DOMAIN
inbound_emails   {_id: sha1(message-id), owner_id, kind inquiry|reply, from, subject, text, lead_id, thread, at}
inquiry_settings also: auto_web (proposals for website / lead-magnet inquiries too; email and pasted WhatsApp always get one)
lead.proposal    {subject, email_text, whatsapp_text, matched, offers, written_by ai|template, status draft|sent|skipped,
                  channel, at, sent_at, approval_id, replied_at}
"""
import asyncio
import hashlib
import hmac
import json
import logging
import re
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

from .. import company_brain, dashboard_cache, files, inbound, mailer, plans
from ..agents import catalog
from ..agents.approvals import create_approval, link_hosts
from ..ai import proposal_tasks
from ..ai.tasks import profile_text
from ..config import settings
from ..context import Ctx, feature
from ..db import IST, db, now
from ..security import normalise_phone
from ..services import notify, run_ai, track

router = APIRouter(prefix="/api")
log = logging.getLogger("inquiries")
CHANNELS = {"website": "Website", "guide": "Lead magnet", "lead_magnet": "Lead magnet", "email": "Email", "whatsapp": "WhatsApp",
            "card": "Card / QR", "manual": "Added by hand", "import": "Imported"}
_tasks: set = set()


# ───────────────────────── the inquiry address ─────────────────────────
def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:30] or "inquiries"


async def handle_for(ws: str) -> str:
    st = await db().inquiry_settings.find_one({"_id": ws}) or {}
    if st.get("handle"):
        return st["handle"]
    site = await db().sites.find_one({"owner_id": ws}, {"slug": 1}) or {}
    b = await db().businesses.find_one({"owner_id": ws}, {"name": 1}) or {}
    base = _slug(site.get("slug") or b.get("name") or "inquiries")
    for n in range(0, 50):
        handle = base if n == 0 else f"{base}-{n + 1}"
        if handle in ("r", "postmaster", "abuse", "admin", "hub", "mailer-daemon", "noreply"):   # replies use r+…
            continue
        try:
            await db().inquiry_settings.update_one({"_id": ws, "handle": {"$exists": False}}, {"$set": {"handle": handle}}, upsert=True)
            return (await db().inquiry_settings.find_one({"_id": ws}))["handle"]
        except DuplicateKeyError:
            mine = await db().inquiry_settings.find_one({"_id": ws}) or {}
            if mine.get("handle"):        # a second request made it a moment ago
                return mine["handle"]
            continue
    raise HTTPException(500, "Couldn't make an inquiry address")


def address(handle: str) -> str | None:
    return f"{handle}@{settings.inbound_email_domain}" if settings.inbound_email_domain else None


# ───────────────────────── the proposal ─────────────────────────
def _brain_text(p: dict, company: str) -> str:
    return profile_text(p) + "\n" + (company or "")


EMAIL_AI_PER_DAY = 30      # AI-written proposals for email inquiries per owner per day; after that the safe template (spam can't drain runs)
EMAIL_LEADS_PER_HOUR = 30  # email inquiries turned into leads per owner per hour


async def write_proposal(owner: dict, business: dict, lead: dict, use_ai: bool = True) -> dict:
    """One AI run. If the AI's draft says anything the Brain doesn't (a number, a discount, a date or time promise — subject
    included), it's thrown away (the run refunded) and the safe reply — thanks and qualifying questions — is used instead.
    Out of runs, or `use_ai` off: the same safe reply, with no AI run."""
    ws = owner["_id"]
    if not use_ai:
        r = {**proposal_tasks.fallback(business, lead), "written_by": "template"}
        return r
    company = await company_brain.context(ws, lead.get("message") or "") if plans.has(owner, "company_brain") else ""
    brain = _brain_text(business, company)
    customer_said = " ".join(str(x or "") for x in (lead.get("message"), lead.get("name"), business.get("whatsapp"), owner.get("phone")))

    def check(r: dict) -> dict:
        out = {"matched": bool(r.get("matched")), "offers": [str(x)[:80] for x in (r.get("offers") or [])][:3],
               "subject": str(r.get("subject") or "").strip()[:120], "email_text": str(r.get("email_text") or "").strip()[:4000],
               "whatsapp_text": str(r.get("whatsapp_text") or "").strip()[:900]}
        if not out["email_text"] or not out["whatsapp_text"]:
            raise ValueError("empty proposal")
        bad = proposal_tasks.unsafe("\n".join((out["subject"], out["email_text"], out["whatsapp_text"])), brain, customer_said)
        if bad:
            raise ValueError(f"says what the Brain doesn't: {bad}")
        return out
    try:
        r = await run_ai(owner, "proposal", proposal_tasks.proposal(business, lead, company), f"Proposal — {lead.get('name', '')}",
                         save_output=False, check=check)
        r["written_by"] = "ai"
    except HTTPException as e:
        log.info("proposal for %s written from the template: %s", lead.get("_id"), getattr(e, "detail", e))
        r = {**proposal_tasks.fallback(business, lead), "written_by": "template"}
    r["subject"] = r.get("subject") or f"Your inquiry to {business.get('name', 'us')}"
    return r


def _today_start():
    return now().astimezone(IST).replace(hour=0, minute=0, second=0, microsecond=0)


async def _sent_today(ws: str) -> int:
    return await db().email_log.count_documents({"owner_id": ws, "kind": "proposal", "status": {"$ne": "failed"}, "at": {"$gte": _today_start()}})


async def _auto_replied_recently(ws: str, address: str) -> bool:
    """One automatic reply per sender per 24 hours: an automatic proposal email that really went out."""
    return bool(await db().email_log.find_one({"owner_id": ws, "to": address.lower(), "kind": "proposal", "ref.auto": True,
                                               "status": {"$nin": ["failed", "sending"]}, "at": {"$gte": now() - timedelta(hours=24)}}, {"_id": 1}))


async def send_email(owner: dict, business: dict, lead: dict, prop: dict, auto: bool = False) -> dict:
    return await mailer.send(owner=owner, to=lead["email"], subject=prop["subject"], text=prop["email_text"], kind="proposal",
                             from_name=business.get("name") or "Sales", reply_to=business.get("email"), ref={"lead_id": lead["_id"], "auto": auto})


async def on_new_inquiry(owner: dict, business: dict, lead: dict) -> dict:
    """Writes the proposal and sends or queues it per channel. Returns lead.proposal."""
    from ..agents.jobs import claim
    ws = owner["_id"]
    st = await db().inquiry_settings.find_one({"_id": ws}) or {}
    email = (lead.get("email") or "").lower()
    from_email = lead.get("source") == "email"
    verified = bool(lead.get("sender_verified"))
    if from_email and email and await _auto_replied_recently(ws, email):
        prop = {"status": "skipped", "why": "This customer got an automatic reply in the last 24 hours.", "at": now()}
        await db().leads.update_one({"_id": lead["_id"]}, {"$set": {"proposal": prop}})
        return prop
    use_ai = True
    if from_email:   # strangers can email the address: AI only for authenticated senders, and only so many a day
        written = await db().leads.count_documents({"owner_id": ws, "source": "email", "proposal.written_by": "ai", "proposal.at": {"$gte": _today_start()}})
        use_ai = verified and written < EMAIL_AI_PER_DAY
    prop = await write_proposal(owner, business, lead, use_ai=use_ai)
    prop.update(at=now(), status="draft", channel=None)
    has_link = any(link_hosts(prop.get(k) or "") for k in ("subject", "email_text", "whatsapp_text"))
    if email:
        prop["channel"] = "email"
        held = ("It has a link, so it waits for you." if has_link else
                "The sender's address couldn't be confirmed, so it waits for you." if from_email and not verified else
                "Add your own email in the Knowledge Hub so replies reach you; until then it waits for you." if not mailer.valid(business.get("email")) else
                "Today's limit for automatic proposals is reached; it waits for you." if await _sent_today(ws) >= settings.proposals_per_owner_per_day else None)
        if st.get("auto_email") and not held:
            day = int(now().timestamp() // 86400)
            if await claim(f"autoreply:{ws}:{email}:{day}") and not await _auto_replied_recently(ws, email):
                res = await send_email(owner, business, lead, prop, auto=True)
                if res["sent"]:
                    prop.update(status="sent", sent_at=now(), email_id=res.get("id"))
                else:
                    prop["send_error"] = res.get("error")
            else:
                prop["held"] = "This customer got an automatic reply in the last 24 hours; it waits for you."
        elif st.get("auto_email"):
            prop["held"] = held
    if lead.get("phone"):
        name = lead.get("name") or "there"
        a = await create_approval(owner, ws, "inquiry_proposal", "quote", f"Proposal to {name}", name, lead["phone"], prop["whatsapp_text"],
                                  allow_act=False, lead_id=lead["_id"])
        prop["approval_id"] = a["_id"]
        prop["channel"] = prop["channel"] or "whatsapp"
    await db().leads.update_one({"_id": lead["_id"]}, {"$set": {"proposal": prop}})
    await track("proposal", ws, by=prop["written_by"], channel=prop["channel"], status=prop["status"])
    return prop


async def wants_proposal(ws: str, lead: dict) -> bool:
    """Email inquiries and pasted WhatsApp inquiries always get a proposal; website and lead-magnet inquiries when the owner
    switched that on (each proposal uses an AI run, and website inquiries already get the owner's follow-ups)."""
    if lead.get("source") == "email" or lead.get("pasted"):
        return True
    st = await db().inquiry_settings.find_one({"_id": ws}, {"auto_web": 1}) or {}
    return bool(st.get("auto_web"))


def after_lead(owner: dict, business: dict, lead: dict) -> None:
    """Runs in the background, so a customer's form never waits for the AI."""
    async def run():
        try:
            await on_new_inquiry(owner, business, lead)
        except Exception:  # noqa: BLE001
            log.exception("proposal for lead %s failed", lead.get("_id"))
    task = asyncio.create_task(run())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def settle() -> None:
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


catalog.register("inquiry_proposal", {
    "name": "Automatic proposal", "feature": "inquiry_desk", "trigger": "lead", "customer_facing": True, "approvals": True, "info_only": True,
    "group": "inbox",
    "what": "Writes a proposal for every new inquiry from your Knowledge Hub. Email inquiries get it by email when you switch that on; "
            "WhatsApp proposals wait in your Send list.",
    "when": "On every new inquiry"})


# ───────────────────────── inbox ─────────────────────────
async def _business(ws: str) -> dict:
    b = await db().businesses.find_one({"owner_id": ws})
    if not b or not b.get("name"):
        raise HTTPException(400, "Tell us about your business first")
    return b


def _status(lead: dict, approvals: dict) -> str:
    if lead.get("status") in ("contacted", "won", "lost") or (lead.get("stage") or "identification") != "identification":
        return "in_pipeline"
    p = lead.get("proposal") or {}
    if p.get("status") == "sent" or approvals.get(p.get("approval_id")) == "sent":
        return "proposal_sent"
    return "new"


@router.get("/inquiries")
async def list_inquiries(channel: str = "", status: str = "", ctx: Ctx = Depends(feature("inquiry_desk"))):
    q: dict = {"owner_id": ctx.ws}
    if channel:
        q["source"] = {"$in": [k for k in CHANNELS if CHANNELS[k] == CHANNELS.get(channel, channel)] or [channel]}
    leads = [x async for x in db().leads.find(q).sort("created_at", -1).limit(300)]
    ids = [(x.get("proposal") or {}).get("approval_id") for x in leads if (x.get("proposal") or {}).get("approval_id")]
    appr = {a["_id"]: a["status"] async for a in db().approvals.find({"_id": {"$in": ids}}, {"status": 1})}
    items = []
    for x in leads:
        st = _status(x, appr)
        if status and st != status:
            continue
        p = dict(x.get("proposal") or {})
        for k in ("at", "sent_at", "replied_at"):
            if p.get(k):
                p[k] = p[k].isoformat()
        if p.get("approval_id"):
            p["approval_status"] = appr.get(p["approval_id"])
        items.append({"id": x["_id"], "name": x.get("name", ""), "phone": x.get("phone", ""), "email": x.get("email", ""), "channel": x.get("source", "website"),
                      "channel_label": CHANNELS.get(x.get("source", "website"), (x.get("source") or "").title()), "message": x.get("message", ""),
                      "created_at": x["created_at"].isoformat(), "status": st, "stage": x.get("stage", "identification"), "proposal": p or None,
                      "replies": [{"at": r["at"].isoformat(), "from": r.get("from", ""), "text": r.get("text", "")} for r in (x.get("replies") or [])[-3:]],
                      "verified": x.get("sender_verified"), "attachments": x.get("attachments") or []})
    counts = {k: sum(1 for i in items if i["status"] == k) for k in ("new", "proposal_sent", "in_pipeline")}
    return {"items": items[:200], "counts": counts, "channels": CHANNELS}


@router.get("/inquiries/{lead_id}/files/{file_id}")
async def inquiry_file(lead_id: str, file_id: str, ctx: Ctx = Depends(feature("inquiry_desk"))):
    """A file a customer attached to their email inquiry — only for this business, and only if it's on that inquiry."""
    lead = await db().leads.find_one({"_id": lead_id, "owner_id": ctx.ws}, {"attachments": 1})
    if not lead or file_id not in {a["id"] for a in lead.get("attachments") or []}:
        raise HTTPException(404, "Not found")
    f = await files.get(file_id, ctx.ws)
    if not f:
        raise HTTPException(404, "Not found")
    name = re.sub(r"[^A-Za-z0-9 ._()-]", "", f.get("filename") or "attachment").strip() or "attachment"
    return Response(bytes(f["data"]), media_type=f["content_type"], headers={"Content-Disposition": f'attachment; filename="{name}"',
                                                                          "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/inquiries/settings")
async def get_settings(ctx: Ctx = Depends(feature("inquiry_desk", "manager"))):
    st = await db().inquiry_settings.find_one({"_id": ctx.ws}) or {}
    b = await db().businesses.find_one({"owner_id": ctx.ws}, {"email": 1}) or {}
    handle = await handle_for(ctx.ws)
    return {"address": address(handle), "handle": handle, "inbound_ready": bool(settings.inbound_email_domain),
            "auto_email": bool(st.get("auto_email")), "auto_web": bool(st.get("auto_web")), "email_ready": mailer.configured() or settings.local_dev, "reply_to": b.get("email") or None,
            "daily_limit": settings.proposals_per_owner_per_day, "sent_today": await _sent_today(ctx.ws)}


class SettingsIn(BaseModel):
    auto_email: bool | None = None     # send proposals to email inquiries automatically
    auto_web: bool | None = None       # also write a proposal for website and lead-magnet inquiries


@router.put("/inquiries/settings")
async def save_settings(body: SettingsIn, ctx: Ctx = Depends(feature("inquiry_desk", "owner"))):
    await handle_for(ctx.ws)
    patch = body.model_dump(exclude_none=True)
    await db().inquiry_settings.update_one({"_id": ctx.ws}, {"$set": {**patch, "updated_at": now(), "by": ctx.actor}})
    await track("inquiry_settings", ctx.ws, **patch)
    return await get_settings(ctx)


class PasteIn(BaseModel):
    name: str = Field(default="", max_length=80)
    phone: str = Field(min_length=6, max_length=24)
    message: str = Field(min_length=2, max_length=3000)


@router.post("/inquiries/whatsapp")
async def paste_whatsapp(body: PasteIn, ctx: Ctx = Depends(feature("inquiry_desk"))):
    """A WhatsApp inquiry pasted or shared into the hub: a lead, and a proposal in the Send list to send with one tap."""
    from .public import save_lead
    phone = normalise_phone(body.phone)
    b = await _business(ctx.ws)
    site = await db().sites.find_one({"owner_id": ctx.ws})
    lead = await save_lead(site, ctx.owner, b, body.name.strip() or "WhatsApp customer", phone, body.message.strip(), "whatsapp", added_by=ctx.actor,
                           pasted=True)
    return {"id": lead["_id"]}


async def _lead(ctx: Ctx, lead_id: str) -> dict:
    lead = await db().leads.find_one({"_id": lead_id, "owner_id": ctx.ws})
    if not lead:
        raise HTTPException(404, "Not found")
    return lead


@router.post("/inquiries/{lead_id}/proposal")
async def rewrite(lead_id: str, ctx: Ctx = Depends(feature("inquiry_desk", "manager"))):
    """Write the proposal again now (1 AI run). It waits for you: nothing is sent."""
    lead = await _lead(ctx, lead_id)
    b = await _business(ctx.ws)
    prop = await write_proposal(ctx.owner, b, lead)
    old = lead.get("proposal") or {}
    prop.update(at=now(), status="draft", channel="email" if lead.get("email") else "whatsapp" if lead.get("phone") else None)
    if lead.get("phone"):
        from ..agents.approvals import expire
        await expire({"ws": ctx.ws, "_id": old.get("approval_id")}, "Replaced by a new proposal")
        a = await create_approval(ctx.owner, ctx.ws, "inquiry_proposal", "quote", f"Proposal to {lead.get('name') or 'customer'}", lead.get("name", ""),
                                  lead["phone"], prop["whatsapp_text"], allow_act=False, lead_id=lead_id)
        prop["approval_id"] = a["_id"]
    await db().leads.update_one({"_id": lead_id}, {"$set": {"proposal": prop}})
    return {"proposal": {**prop, "at": prop["at"].isoformat()}}


@router.post("/inquiries/{lead_id}/send-email")
async def send_now(lead_id: str, ctx: Ctx = Depends(feature("inquiry_desk", "manager"))):
    """The owner (or a manager) sends the proposal email — their own decision, so a draft with a link may go too."""
    lead = await _lead(ctx, lead_id)
    prop = lead.get("proposal") or {}
    if not lead.get("email") or not prop.get("email_text"):
        raise HTTPException(400, "This inquiry has no email address or no proposal yet.")
    if prop.get("status") == "sent":
        raise HTTPException(409, "Already sent.")
    if await _sent_today(ctx.ws) >= settings.proposals_per_owner_per_day:
        raise HTTPException(429, "Today's proposal email limit is reached. It resets at midnight.")
    res = await send_email(ctx.owner, await _business(ctx.ws), lead, prop)
    if not res["sent"]:
        raise HTTPException(502, res.get("error") or "The email couldn't be sent just now.")
    await db().leads.update_one({"_id": lead_id}, {"$set": {"proposal.status": "sent", "proposal.sent_at": now(), "proposal.email_id": res.get("id")}})
    return {"ok": True, "via": res["via"]}


@router.post("/inquiries/{lead_id}/whatsapp")
async def whatsapp_tap(lead_id: str, ctx: Ctx = Depends(feature("inquiry_desk", "manager"))):
    """Tap-to-send from the inbox (Membership has no Send list page): returns the link that opens the owner's own WhatsApp
    with the proposal typed, and marks it sent by them."""
    from ..agents import approvals as appr
    lead = await _lead(ctx, lead_id)
    aid = (lead.get("proposal") or {}).get("approval_id")
    if not aid or not lead.get("phone"):
        raise HTTPException(400, "This inquiry has no WhatsApp proposal.")
    out = await appr.decide(ctx.ws, aid, ctx.actor, "own")
    return {"link": out["link"]}


# ───────────────────────── email in: inquiries and replies ─────────────────────────
def _key(owner_id: str | None, kind: str, mail: dict) -> str:
    """Once per owner and kind: the same email sent to two owners is a lead for each."""
    mid = mail.get("message_id") or (mail["from_email"] + mail["subject"] + mail["text"][:200])
    return hashlib.sha1(f"{owner_id}|{kind}|{mid}".encode()).hexdigest()


async def _seen(owner_id: str | None, kind: str, mail: dict, **extra) -> bool:
    """Records an inbound email once (SNS may deliver twice). True when it was already handled."""
    try:
        await db().inbound_emails.insert_one({"_id": _key(owner_id, kind, mail), "owner_id": owner_id, "kind": kind, "from": mail["from_email"],
                                              "subject": mail["subject"], "text": mail["text"][:2000], "at": now(), **extra})
        return False
    except DuplicateKeyError:
        return True


async def _owner_alert(ws: str, text: str) -> None:
    """In the hub, and on the owner's own WhatsApp from the hub's number (owners only, never customers)."""
    from ..messaging import whatsapp_template
    await notify(ws, text[:600])
    owner = await db().users.find_one({"_id": ws}, {"phone": 1, "name": 1}) or {}
    b = await db().businesses.find_one({"owner_id": ws}, {"name": 1}) or {}
    if owner.get("phone"):
        await whatsapp_template(owner["phone"], settings.aisensy_owner_alert_campaign, owner.get("name", ""), [b.get("name") or "your business", text[:900]],
                                "owner_alert")


async def reply_in(entry: dict, mail: dict) -> str:
    """A reply to an email the hub sent (found by its reply address, or by the email it answers)."""
    if mail["automatic"]:
        return "ignored"
    ws = entry["owner_id"]
    if await _seen(ws, "reply", mail, thread=entry.get("thread")):
        return "duplicate"
    await db().email_log.update_one({"_id": entry["_id"]}, {"$set": {"replied_at": now()}})
    ref = entry.get("ref") or {}
    said = (mail["text"] or mail["subject"]).strip()
    first = said.splitlines()[0][:140] if said else ""
    if ref.get("lead_id"):   # every reply is kept on the lead, so nothing a customer wrote lives only in a notice
        await db().leads.update_one({"_id": ref["lead_id"], "owner_id": ws},
                                    {"$push": {"replies": {"$each": [{"at": now(), "from": mail["from_email"], "text": mail["text"][:2000]}], "$slice": -20}}})
    if ref.get("due_id"):
        from .. import followups
        due = await db().dues.find_one({"_id": ref["due_id"], "ws": ws}) or {}
        await followups.stop(ws, ref["due_id"], "replied")
        await db().dues.update_one({"_id": ref["due_id"], "ws": ws},
                                   {"$push": {"replies": {"$each": [{"at": now(), "from": mail["from_email"], "text": mail["text"][:2000]}], "$slice": -20}}})
        await _owner_alert(ws, f"{due.get('customer') or mail['from_email']} replied to your payment reminder: “{first}”")
        return "reply_due"
    if ref.get("lead_id") and entry.get("kind") == "proposal":
        lead = await db().leads.find_one({"_id": ref["lead_id"], "owner_id": ws}) or {}
        await db().leads.update_one({"_id": ref["lead_id"], "owner_id": ws}, {"$set": {"proposal.replied_at": now()}})
        await _owner_alert(ws, f"{lead.get('name') or mail['from_email']} replied to your proposal: “{first}”")
        return "reply_proposal"
    await _owner_alert(ws, f"{mail['from_email']} replied to your email: “{first}”")
    return "reply"


async def _answering(ws: str, mail: dict) -> dict | None:
    """The hub email this message answers, when it reached an inquiry address (e.g. the owner's mailbox forwarded a reply)."""
    ids = [x for x in [mail.get("in_reply_to"), *mail.get("references", [])] if x]
    if not ids:
        return None
    return await db().email_log.find_one({"owner_id": ws, "rfc_id": {"$in": ids[:20]}})


async def inquiry_in(handle: str, mail: dict, verified: bool) -> str:
    st = await db().inquiry_settings.find_one({"handle": handle})
    if not st:
        return "unknown_address"
    ws = st["_id"]
    owner = await db().users.find_one({"_id": ws})
    if not owner or owner.get("disabled") or not plans.has(owner, "inquiry_desk") or mail["automatic"]:
        return "ignored"
    answered = await _answering(ws, mail)
    if answered:   # a customer's reply to a reminder or proposal, forwarded here: it stops the chase, it isn't a new inquiry
        return await reply_in(answered, mail)
    b = await db().businesses.find_one({"owner_id": ws}) or {}
    name, sender, text = mail["from_name"], mail["from_email"], mail["text"]
    if sender in {(b.get("email") or "").lower(), (owner.get("email") or "").lower()} - {""}:   # the owner forwarded it by hand
        fwd = inbound.forwarded_sender(mail["full_text"])
        if not fwd or not verified:      # only the owner's own, authenticated mail can vouch for a forwarded customer
            return "own_mail"
        (name, sender), text = fwd, inbound.forwarded_body(mail["full_text"])
    if not mailer.valid(sender):
        return "no_sender"
    hour = now() - timedelta(hours=1)
    if await db().leads.count_documents({"owner_id": ws, "source": "email", "created_at": {"$gte": hour}}) >= EMAIL_LEADS_PER_HOUR:
        return "hourly_cap"
    if await _seen(ws, "inquiry", mail):
        return "duplicate"
    from .public import save_lead
    site = await db().sites.find_one({"owner_id": ws})
    message = "\n".join(x for x in (mail["subject"], text) if x).strip()[:2000] or "(empty email)"
    phone = inbound.phone_in(text) if verified else None     # a number in an unauthenticated email isn't trusted as the customer's
    lead = await save_lead(site, owner, b, (name or sender.split("@")[0].replace(".", " ").title())[:80], phone or "", message, "email",
                           email=sender, message_id=mail.get("message_id", "")[:300], sender_verified=verified)
    kept = []
    # the customer's drawings, photos and PDFs stay with the inquiry — only from a sender whose address is proven (DMARC), and
    # within a daily allowance per business, so an anonymous sender can't fill the database with files
    room = ATTACH_BYTES_PER_DAY - await _attached_today(ws) if verified else 0
    for fname, data, mime in mail.get("attachments") or []:
        if len(data) > room:
            break
        f = await files.put(ws, data, fname, mime, "inquiry_attachment", lead_id=lead["_id"])
        kept.append({"id": f["_id"], "name": fname, "size": len(data), "type": mime})
        room -= len(data)
    if kept:
        await db().leads.update_one({"_id": lead["_id"]}, {"$set": {"attachments": kept}})
    dashboard_cache.bust(ws)
    await db().inbound_emails.update_one({"_id": _key(ws, "inquiry", mail)}, {"$set": {"lead_id": lead["_id"]}})
    return "lead"


ATTACH_BYTES_PER_DAY = 50 * 1024 * 1024


async def _attached_today(ws: str) -> int:
    since = now() - timedelta(days=1)
    sizes = [int(f.get("size") or 0) async for f in db().files.find({"owner_id": ws, "kind": "inquiry_attachment", "created_at": {"$gte": since}},
                                                                    {"size": 1})]
    return sum(sizes)


async def route(mail: dict, recipients: list[str], verified: bool = False) -> list[str]:
    out = []
    mine = [(r, inbound.local_part(r, settings.inbound_email_domain)) for r in (recipients or mail["to"])]
    mine = [(r, lp) for r, lp in mine if lp]
    # replies first: if a message reaches both a reply address and an inquiry address, it's a reply
    for _, local in sorted(mine, key=lambda x: not x[1].startswith("r+")):
        if local.startswith("r+"):
            entry = await db().email_log.find_one({"thread": local[2:]})
            out.append(await reply_in(entry, mail) if entry else "ignored")
            if entry:
                break
        else:
            out.append(await inquiry_in(local, mail, verified))
    return out


def _passed(receipt: dict) -> bool:
    """The sender is who they say: DMARC passed (which means SPF or DKIM passed for the From domain)."""
    return (receipt.get("dmarcVerdict") or {}).get("status") == "PASS"


@router.post("/hooks/email-in/{secret}")
async def email_in(secret: str, request: Request):
    """Mail to INBOUND_EMAIL_DOMAIN, from Amazon SES through SNS. The address carries EMAIL_INBOUND_SECRET, every SNS message's
    signature is checked (and its topic, which a live hub must set), and SES's spam and virus verdicts are respected."""
    if not settings.email_inbound_secret or not hmac.compare_digest(secret, settings.email_inbound_secret):
        raise HTTPException(404, "Not found")
    if not settings.email_inbound_topic_arn and not settings.local_dev:
        raise HTTPException(503, "Set EMAIL_INBOUND_TOPIC_ARN before receiving email.")
    try:
        msg = json.loads(await request.body() or b"{}")
    except ValueError:
        raise HTTPException(400, "Not JSON")
    if not isinstance(msg, dict) or not await mailer.sns_verified(msg):
        raise HTTPException(401, "Bad signature")
    if settings.email_inbound_topic_arn and msg.get("TopicArn") != settings.email_inbound_topic_arn:
        raise HTTPException(403, "Not our topic")
    if msg.get("Type") == "SubscriptionConfirmation":
        return {"ok": await mailer.sns_confirm(msg)}
    if msg.get("Type") != "Notification":
        return {"ok": True}
    try:
        note = json.loads(msg.get("Message") or "{}")
    except ValueError:
        raise HTTPException(400, "Not JSON")
    receipt = note.get("receipt") or {}
    if note.get("notificationType") != "Received":
        return {"ok": True, "handled": []}
    if any((receipt.get(v) or {}).get("status") == "FAIL" for v in ("spamVerdict", "virusVerdict")):
        return {"ok": True, "handled": ["rejected"]}
    raw = inbound.raw_from_sns(note)
    action = receipt.get("action") or {}
    if not raw and action.get("type") == "S3":
        # SES's S3 action: the email (any size, attachments included) is in the inbound bucket; SNS only says where
        if not settings.email_inbound_bucket or action.get("bucketName") != settings.email_inbound_bucket:
            return {"ok": True, "handled": ["wrong_bucket"]}
        key = action.get("objectKey") or f"{action.get('objectKeyPrefix') or ''}{(note.get('mail') or {}).get('messageId') or ''}"
        try:
            raw = await mailer.s3_get(action["bucketName"], key)
        except mailer.TooLarge:
            return {"ok": True, "handled": ["too_large"]}
        except ValueError:
            return {"ok": True, "handled": ["bad_key"]}
        except Exception:  # noqa: BLE001  S3 not readable yet: a 5xx makes SNS deliver the notification again
            log.exception("reading an inbound email from S3 failed")
            raise HTTPException(502, "The email couldn't be read from storage yet.")
    if not raw:
        return {"ok": True, "handled": ["no_content"]}
    mail = await asyncio.to_thread(inbound.parse, raw)     # a 30 MB email mustn't hold up everyone else on this server
    return {"ok": True, "handled": await route(mail, [r.lower() for r in receipt.get("recipients") or []], _passed(receipt))}


async def ensure_indexes() -> None:
    await db().inquiry_settings.create_index("handle", unique=True, sparse=True)
    await db().inbound_emails.create_index([("owner_id", 1), ("at", -1)])
    await db().leads.create_index([("owner_id", 1), ("source", 1), ("created_at", -1)])
