"""
CRM, pipeline and opportunities (v2.3, Conversion engine) — the data side. The pages are in routers/crm.py.

The model is built on what the hub already has, so there is never a second copy of a deal:

leads            = the opportunities (deals). Their existing fields stay as they are (status new|contacted|won|lost,
                   `stage` = the Football Field stage, value, source, message, proposal, crm …). v2.3 adds:
                   pstage (the business's own pipeline stage id), contact_id, expected_close "YYYY-MM-DD",
                   follow_up_on "YYYY-MM-DD", follow_up_note, assignee_id, lost_reason.
                   Moving a deal to a pipeline stage also sets `stage` to that stage's Football Field stage, so the
                   Dashboard, the funnel leak finder, Employz.ai sync and every report keep working unchanged.
contacts         {_id, ws, name, phone ("+91…" or None), email (lower-case or None), company, company_id, source, tags [],
                  notes, follow_up_on, follow_up_note, assignee_id, created_at, updated_at, last_activity_at}
                  One per person per business, matched by phone first, then email. Every new lead from any channel
                  (website form, guide, lead magnet download, email, pasted WhatsApp — all go through public.save_lead)
                  creates or updates its contact; leads made elsewhere (the Growth WhatsApp AI, older data) are picked up
                  the next time the CRM or the pipeline is opened. A customer (desk.py `customers`) is the same person
                  when the phone matches — shown on the contact, not copied.
companies        {_id, ws, name, name_key (lower-case), website, notes, created_at}
crm_activities   {_id, ws, contact_id, lead_id, kind call|meeting|note|created|stage|won|lost|reopened|followup|quote|invoice,
                  text, at, by, by_name}
pipelines        {_id: ws, answers {...}, stages [{id, name, ff, hint}], lost_reasons [..], built_by ai|rules, built_at, updated_at}
                  ONE pipeline per business. Until the owner builds it, the board shows the 5 Football Field stages.

Follow-up reminders: setting a follow-up date on a contact or a deal schedules one agent job ("crm_followup") for 10:00
India time that day; it leaves a notice in the hub (no customer is ever messaged by it). The in-hub list of follow-ups due is
always there too.
"""
import logging
import re
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from .ai.crm_tasks import FF, FF_LABEL
from .db import IST, db, now
from .security import normalise_phone
from .services import new_id, notify

logger = logging.getLogger("crm")

DEFAULT_HINTS = {"identification": "Who they are, and what they need.", "logic": "The reason they'd buy.",
                 "pain": "What it costs them to wait.", "vision": "Their life with your solution.", "close": "Ask for the order."}
OPEN = ("new", "contacted")
SOURCE_LABEL = {"website": "Website", "guide": "Free guide", "lead_magnet": "Lead magnet", "whatsapp": "WhatsApp", "email": "Email",
                "manual": "Added by hand", "import": "Imported", "phone": "Phone", "walk_in": "Walk-in", "referral": "Referral",
                "marketplace": "IndiaMART / Justdial", "social": "Instagram / Facebook", "exhibition": "Exhibition", "other": "Other",
                "customer": "Customer list", "event": "Event", "card": "Card / QR", "qr": "Card / QR", "gateway": "Payment",
                "employz": "Employz.ai"}
TAG_RE = re.compile(r"[^\w\s&+-]", re.UNICODE)


# ───────────────────────── small helpers ─────────────────────────
def norm_phone(raw) -> str | None:
    s = str(raw or "").strip()[:24]
    if not s:
        return None
    try:
        return normalise_phone(s)
    except HTTPException:
        return None


def norm_email(raw) -> str | None:
    s = str(raw or "").strip().lower()[:254]
    return s if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", s) else None


def clean_tags(tags) -> list[str]:
    out = []
    for t in (tags or [])[:20]:
        t = TAG_RE.sub("", str(t or "")).strip()[:30]
        if t and t.lower() not in {x.lower() for x in out}:
            out.append(t)
    return out


def today_ist() -> date:
    return now().astimezone(IST).date()


def iso_day(raw) -> str | None:
    """'2026-11-03' (or a datetime) → '2026-11-03'; anything else → None."""
    if isinstance(raw, datetime):
        return raw.astimezone(IST).date().isoformat()
    s = str(raw or "").strip()[:10]
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError:
        return None


def day_text(day_iso: str | None) -> str:
    """'2026-11-03' → '3 Nov'."""
    try:
        d = date.fromisoformat(str(day_iso)[:10])
    except ValueError:
        return str(day_iso or "")
    return f"{d.day} {d.strftime('%b')}"


def business_kind(b: dict) -> str:
    """The kind of business, for the pipeline's suggestions: a factory that packs food is a manufacturer, not a restaurant."""
    from .ai.kit_tasks import business_type
    text = f"{b.get('industry', '')} {' '.join(o.get('name', '') for o in b.get('offers') or [])}".lower()
    if any(w in text for w in ("manufactur", "factory", "packaging", "corrugated", "boxes", "fabricat", "machining", "moulding", "molding")):
        return "manufacturing"
    return business_type(b) if b else "services"


def _aware(dt):
    return dt if not isinstance(dt, datetime) or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def iso(dt) -> str | None:
    return _aware(dt).isoformat() if isinstance(dt, datetime) else None


# ───────────────────────── pipeline ─────────────────────────
def field_stages() -> list[dict]:
    """The 5 Football Field stages as a pipeline: what the board shows before the owner builds their own."""
    return [{"id": ff, "name": FF_LABEL[ff], "ff": ff, "hint": DEFAULT_HINTS[ff]} for ff in FF]


async def pipeline_of(ws: str) -> dict | None:
    return await db().pipelines.find_one({"_id": ws})


async def stages_of(ws: str) -> list[dict]:
    p = await pipeline_of(ws)
    return (p or {}).get("stages") or field_stages()


def stage_id(name: str, taken: set) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24] or "stage"
    sid, n = base, 2
    while sid in taken:
        sid, n = f"{base}-{n}", n + 1
    taken.add(sid)
    return sid


def pstage_of(lead: dict, stages: list[dict]) -> str:
    """The pipeline stage a deal is in: its own, while that stage still exists and matches its Football Field stage (a move
    made elsewhere — the old Leads page, Employz.ai — changes `stage` only); otherwise the first stage on that field stage."""
    ff = lead.get("stage") if lead.get("stage") in FF else "identification"
    own = lead.get("pstage")
    if own and any(s["id"] == own and s["ff"] == ff for s in stages):
        return own
    for s in stages:
        if s["ff"] == ff:
            return s["id"]
    # no stage on that part of the field: the nearest one before it, else the first
    before = [s for s in stages if FF.index(s["ff"]) <= FF.index(ff)]
    return (before[-1] if before else stages[0])["id"]


# ───────────────────────── contacts ─────────────────────────
async def company_for(ws: str, name: str) -> str | None:
    name = str(name or "").strip()[:120]
    if len(name) < 2:
        return None
    key = name.lower()
    c = await db().companies.find_one({"ws": ws, "name_key": key}, {"_id": 1})
    if c:
        return c["_id"]
    doc = {"_id": new_id(), "ws": ws, "name": name, "name_key": key, "website": "", "notes": "", "created_at": now()}
    try:
        await db().companies.insert_one(doc)
        return doc["_id"]
    except DuplicateKeyError:
        c = await db().companies.find_one({"ws": ws, "name_key": key}, {"_id": 1})
        return c["_id"] if c else None


async def find_contact(ws: str, phone: str | None, email: str | None) -> dict | None:
    if phone:
        c = await db().contacts.find_one({"ws": ws, "phone": phone})
        if c:
            return c
    if email:
        return await db().contacts.find_one({"ws": ws, "email": email})
    return None


async def upsert_contact(ws: str, name: str, phone=None, email=None, company: str = "", source: str = "", tags=None,
                         notes: str = "") -> dict | None:
    """The contact for this phone (or email): created when new; when it exists, only empty fields are filled in (an owner's
    own edits are never overwritten). Returns None when there is neither a phone nor an email to know the person by."""
    phone, email = norm_phone(phone), norm_email(email)
    if not phone and not email:
        return None
    name = str(name or "").strip()[:80]
    for _ in range(2):
        existing = await find_contact(ws, phone, email)
        if existing:
            fill = {}
            if name and (not existing.get("name") or existing.get("name") in ("Customer", "WhatsApp customer")):
                fill["name"] = name
            if phone and not existing.get("phone"):
                fill["phone"] = phone
            if email and not existing.get("email"):
                fill["email"] = email
            if company and not existing.get("company"):
                fill["company"], fill["company_id"] = company.strip()[:120], await company_for(ws, company)
            if fill:
                fill["updated_at"] = now()
                await db().contacts.update_one({"_id": existing["_id"]}, {"$set": fill})
                existing.update(fill)
            return existing
        doc = {"_id": new_id(), "ws": ws, "name": name or "Customer", "phone": phone, "email": email,
               "company": (company or "").strip()[:120], "company_id": await company_for(ws, company) if company else None,
               "source": (source or "manual")[:30], "tags": clean_tags(tags), "notes": (notes or "")[:2000], "follow_up_on": None,
               "follow_up_note": "", "assignee_id": None, "created_at": now(), "updated_at": now(), "last_activity_at": now()}
        try:
            await db().contacts.insert_one(doc)
            return doc
        except DuplicateKeyError:
            continue   # made by a parallel request a moment ago: use that one
    return await find_contact(ws, phone, email)


async def add_activity(ws: str, contact_id: str | None, lead_id: str | None, kind: str, text: str, by: str | None = None,
              by_name: str = "", at: datetime | None = None) -> dict:
    doc = {"_id": new_id(), "ws": ws, "contact_id": contact_id, "lead_id": lead_id, "kind": kind, "text": (text or "")[:2000],
           "at": at or now(), "by": by, "by_name": (by_name or "")[:80]}
    await db().crm_activities.insert_one(doc)
    if contact_id:
        await db().contacts.update_one({"_id": contact_id}, {"$set": {"last_activity_at": doc["at"]}})
    return doc


async def on_new_lead(ws: str, lead: dict) -> None:
    """Every new lead: its contact (created or matched), the first pipeline stage, and a line in the contact's history.
    Never raises: a lead must always be saved and alerted, whatever happens here."""
    try:
        contact = await upsert_contact(ws, lead.get("name", ""), lead.get("phone"), lead.get("email"), lead.get("company", ""),
                                       lead.get("source", ""))
        stages = await stages_of(ws)
        patch = {"pstage": pstage_of(lead, stages)}
        if contact:
            patch["contact_id"] = contact["_id"]
        await db().leads.update_one({"_id": lead["_id"]}, {"$set": patch})
        lead.update(patch)
        src = SOURCE_LABEL.get(lead.get("source", ""), (lead.get("source") or "a new channel").replace("_", " "))
        if contact:
            await add_activity(ws, contact["_id"], lead["_id"], "created", f"New inquiry from {src}" + (f": {lead['message'][:200]}" if lead.get("message") else ""),
                      at=lead.get("created_at"))
    except Exception:  # noqa: BLE001
        logger.exception("CRM could not file lead %s", lead.get("_id"))


async def backfill(ws: str, limit: int = 300) -> int:
    """Leads made before v2.3 or outside save_lead get their contact the next time the CRM is opened."""
    n = 0
    async for lead in db().leads.find({"owner_id": ws, "contact_id": {"$exists": False}}).sort("created_at", 1).limit(limit):
        contact = await upsert_contact(ws, lead.get("name", ""), lead.get("phone"), lead.get("email"), "", lead.get("source", ""))
        await db().leads.update_one({"_id": lead["_id"]}, {"$set": {"contact_id": contact["_id"] if contact else None}})
        if contact:
            n += 1
            if not await db().crm_activities.find_one({"lead_id": lead["_id"], "kind": "created"}, {"_id": 1}):
                src = SOURCE_LABEL.get(lead.get("source", ""), (lead.get("source") or "").replace("_", " ") or "an inquiry")
                await add_activity(ws, contact["_id"], lead["_id"], "created", f"New inquiry from {src}", at=lead.get("created_at"))
    return n


async def on_lead_patched(ws: str, before: dict, patch: dict, by: str | None, by_name: str = "") -> None:
    """A deal moved or was decided on another page (the Leads page, a quotation): one line in the contact's history."""
    try:
        cid = before.get("contact_id")
        if not cid:
            return
        name = before.get("name") or "Deal"
        if patch.get("status") and patch["status"] != before.get("status"):
            word = {"won": "won", "lost": "lost", "contacted": "contacted", "new": "reopened"}.get(patch["status"], patch["status"])
            await add_activity(ws, cid, before["_id"], patch["status"] if patch["status"] in ("won", "lost") else "stage",
                      f"{name} marked {word}", by, by_name)
        elif patch.get("stage") and patch["stage"] != before.get("stage"):
            await add_activity(ws, cid, before["_id"], "stage", f"{name} moved to {FF_LABEL.get(patch['stage'], patch['stage'])}", by, by_name)
    except Exception:  # noqa: BLE001
        logger.exception("CRM history line failed for lead %s", before.get("_id"))


# ───────────────────────── follow-up reminders ─────────────────────────
def _remind_at(day_iso: str) -> datetime:
    d = date.fromisoformat(day_iso)
    return datetime(d.year, d.month, d.day, 10, 0, tzinfo=IST).astimezone(timezone.utc)


async def set_reminder(ws: str, kind: str, target_id: str, day_iso: str | None) -> None:
    """kind: contact | deal. One job per target; a new date moves it, no date cancels it."""
    from .agents import jobs
    job_id = f"crmfu:{kind}:{target_id}"
    if not day_iso:
        await jobs.cancel({"_id": job_id}, "Follow-up date removed")
        return
    run_at = max(_remind_at(day_iso), now())
    payload = {"kind": kind, "id": target_id, "on": day_iso}
    moved = await db().agent_jobs.update_one({"_id": job_id, "status": "scheduled"}, {"$set": {"run_at": run_at, "payload": payload}})
    if not moved.matched_count:
        done = await db().agent_jobs.find_one({"_id": job_id}, {"status": 1})
        if done and done.get("status") != "cancelled":
            await db().agent_jobs.delete_one({"_id": job_id, "status": {"$ne": "scheduled"}})   # last time's reminder already ran
        await jobs.schedule(ws, "crm_followup", run_at, payload, job_id=job_id)


async def _reminder_runner(owner: dict, run: dict):
    from .agents.catalog import Skip
    job = run.get("job") or {}
    p = job.get("payload") or {}
    ws = owner["_id"]
    if p.get("kind") == "deal":
        doc = await db().leads.find_one({"_id": p.get("id"), "owner_id": ws})
        if not doc or doc.get("follow_up_on") != p.get("on") or doc.get("status") not in OPEN:
            raise Skip("The follow-up was changed or the deal is closed.")
        text = f"Follow up today: {doc.get('name') or 'a deal'}" + (f" — {doc['follow_up_note']}" if doc.get("follow_up_note") else "")
    else:
        doc = await db().contacts.find_one({"_id": p.get("id"), "ws": ws})
        if not doc or doc.get("follow_up_on") != p.get("on"):
            raise Skip("The follow-up was changed.")
        text = f"Follow up today: {doc.get('name') or 'a contact'}" + (f" — {doc['follow_up_note']}" if doc.get("follow_up_note") else "")
    await notify(ws, text[:300])
    return text[:200]


def register_agent() -> None:
    from .agents import catalog
    if "crm_followup" in catalog.AGENTS:
        return
    catalog.register("crm_followup", {
        "name": "CRM follow-up reminders", "what": "On the day you set, a reminder in your hub to follow up a contact or a deal.",
        "when": "10am on each follow-up date", "feature": "crm", "trigger": "lead", "customer_facing": False,
        "info_only": True, "group": "alerts"}, _reminder_runner)


register_agent()


async def due_followups(ws: str, days: int = 7) -> dict:
    """Contacts and open deals with a follow-up date up to `days` ahead (overdue ones first)."""
    today = today_ist()
    until = (today + timedelta(days=days)).isoformat()
    items = []
    async for c in db().contacts.find({"ws": ws, "follow_up_on": {"$ne": None, "$lte": until}}).limit(300):
        items.append({"kind": "contact", "id": c["_id"], "contact_id": c["_id"], "name": c.get("name", ""), "phone": c.get("phone"),
                      "on": c["follow_up_on"], "note": c.get("follow_up_note", "")})
    async for d in db().leads.find({"owner_id": ws, "status": {"$in": list(OPEN)}, "follow_up_on": {"$ne": None, "$lte": until}}).limit(300):
        items.append({"kind": "deal", "id": d["_id"], "contact_id": d.get("contact_id"), "name": d.get("name", ""), "phone": d.get("phone"),
                      "on": d["follow_up_on"], "note": d.get("follow_up_note", ""), "value": d.get("value")})
    items.sort(key=lambda x: x["on"])
    t = today.isoformat()
    return {"overdue": [i for i in items if i["on"] < t], "today": [i for i in items if i["on"] == t],
            "upcoming": [i for i in items if i["on"] > t]}


async def ensure_indexes() -> None:
    d = db()
    await d.contacts.create_index([("ws", 1), ("phone", 1)], unique=True, partialFilterExpression={"phone": {"$type": "string"}})
    await d.contacts.create_index([("ws", 1), ("email", 1)], unique=True, partialFilterExpression={"email": {"$type": "string"}})
    await d.contacts.create_index([("ws", 1), ("updated_at", -1)])
    await d.contacts.create_index([("ws", 1), ("follow_up_on", 1)])
    await d.companies.create_index([("ws", 1), ("name_key", 1)], unique=True)
    await d.crm_activities.create_index([("ws", 1), ("contact_id", 1), ("at", -1)])
    await d.crm_activities.create_index([("ws", 1), ("lead_id", 1), ("at", -1)])
    await d.leads.create_index([("owner_id", 1), ("contact_id", 1)])
    await d.leads.create_index([("owner_id", 1), ("follow_up_on", 1)])
