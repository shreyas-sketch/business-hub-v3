"""
Connections to each owner's own accounts (Growth Mentorship and up; set up for them by the team, or by the owner).

    whatsapp   their own WhatsApp Business number: AiSensy (or Meta's WhatsApp Cloud API directly)
    employz    their Employz.ai (GoHighLevel) sub-account: contacts and the sales pipeline (Running the Business and up)
    voice      the AI Telecaller: an ElevenLabs agent with a phone number (Twilio or SIP)
    tally / email / calendar   LegacyWorkforce
    razorpay   their own Razorpay account: captured payments → invoices, refunds → credit notes (gateway_invoicing)

Secrets (API keys, tokens, passwords) are encrypted at rest with CONNECTIONS_KEY (or a key derived from JWT_SECRET) and
are never sent back to the browser — only whether they are set. Each connection that receives events gets its own
unguessable webhook address.
"""
import base64
import hashlib
import secrets

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException

from . import plans
from .config import settings
from .db import db, now

# kind → (label, feature, fields). Field: (key, label, type, help); type: text | secret | select:<a>,<b> | number
SPECS: dict[str, dict] = {
    "whatsapp": {"label": "WhatsApp — your own number", "feature": "whatsapp_ai", "fields": [
        ("provider", "Provider", "select:aisensy,meta", "AiSensy (recommended) or Meta's WhatsApp Cloud API directly"),
        ("number", "Your WhatsApp Business number", "text", "The number customers message, e.g. 98200 12345"),
        ("api_key", "AiSensy API key", "secret", "AiSensy → Manage → API key (for approved templates)"),
        ("project_id", "AiSensy project id", "text", "For two-way chat replies (AiSensy project API)"),
        ("project_password", "AiSensy project API password", "secret", "For two-way chat replies"),
        ("phone_number_id", "Meta phone number id", "text", "Meta only"),
        ("access_token", "Meta access token", "secret", "Meta only: a permanent system-user token"),
        ("app_secret", "Meta app secret", "secret", "Meta only: checks that incoming messages really come from Meta"),
        ("tpl_instant", "Template: instant reply to a new inquiry", "text", "{{1}} customer name, {{2}} business"),
        ("tpl_followup", "Template: follow-up", "text", "{{1}} name, {{2}} business, {{3}} message"),
        ("tpl_reminder", "Template: payment reminder", "text", "{{1}} name, {{2}} business, {{3}} amount, {{4}} due date, {{5}} how to pay"),
        ("tpl_review", "Template: review request", "text", "{{1}} name, {{2}} business, {{3}} review link"),
        ("tpl_customer", "Template: customer message", "text", "{{1}} name, {{2}} business, {{3}} message (reorders, referrals, greetings)"),
        ("tpl_campaign", "Template: money campaign", "text", "{{1}} name, {{2}} business, {{3}} message"),
        ("language", "Template language code", "text", "e.g. en, en_US, hi (Meta only)"),
    ]},
    "employz": {"label": "Employz.ai CRM", "feature": "crm_sync", "fields": [
        ("location_id", "Sub-account (location) id", "text", "Employz.ai → Settings → Business profile → Location ID"),
        ("token", "Private integration token", "secret", "Employz.ai → Settings → Private integrations (contacts + opportunities)"),
        ("pipeline_id", "Pipeline id", "text", "The Football Field pipeline from the snapshot"),
        ("stage_identification", "Stage id: Identification", "text", ""),
        ("stage_logic", "Stage id: Logic / reason", "text", ""),
        ("stage_pain", "Stage id: Pain / consequence", "text", ""),
        ("stage_vision", "Stage id: Solution / vision", "text", ""),
        ("stage_close", "Stage id: Ask / close", "text", ""),
        ("app_link", "Link to open this account", "text", "Shown to the owner as 'Open Employz.ai'"),
    ]},
    "voice": {"label": "AI Telecaller — ElevenLabs", "feature": "voice_agent", "fields": [
        ("api_key", "ElevenLabs API key", "secret", "ElevenLabs → Developers → API keys"),
        ("agent_id", "Agent id", "text", "The Telecaller agent built in the bootcamp"),
        ("phone_number_id", "Agent phone number id", "text", "ElevenLabs → Phone numbers (Twilio or SIP trunk)"),
        ("mode", "Phone line", "select:twilio,sip", "Twilio, or a SIP trunk (e.g. an Indian telephony provider)"),
        ("webhook_secret", "Post-call webhook secret", "secret", "Shown when you add the post-call webhook in ElevenLabs"),
        ("hours", "Calling hours", "text", "e.g. 10-19 (India time)"),
    ]},
    "tally": {"label": "Tally", "feature": "more_connections", "fields": [
        ("bridge_url", "Tally bridge address", "text", "The bridge running next to Tally on the office computer"),
        ("token", "Bridge token", "secret", ""),
    ]},
    "email": {"label": "Email", "feature": "more_connections", "fields": [
        ("from_address", "Send from", "text", "e.g. hello@yourbusiness.in"),
        ("smtp_host", "SMTP host", "text", ""), ("smtp_port", "SMTP port", "number", "587"),
        ("smtp_user", "SMTP user", "text", ""), ("smtp_password", "SMTP password", "secret", ""),
    ]},
    "calendar": {"label": "Calendar", "feature": "more_connections", "fields": [
        ("booking_link", "Booking link", "text", "Your Employz.ai or Google booking page — the Meeting Setter shares it"),
    ]},
    # v2.1 (3.5): the owner's own Razorpay account; every captured payment becomes an invoice, a refund a credit note
    "razorpay": {"label": "Payment gateway — Razorpay", "feature": "gateway_invoicing", "fields": [
        ("key_id", "Key id", "text", "Razorpay → Account & Settings → API keys. Test keys start with rzp_test_"),
        ("key_secret", "Key secret", "secret", "Shown once when you create the key"),
        ("webhook_secret", "Webhook secret", "secret", "The secret you type when you add the webhook below in Razorpay → Webhooks"),
    ]},
}
WEBHOOKS = {"whatsapp": "/api/hooks/whatsapp/", "voice": "/api/hooks/voice/", "employz": "/api/hooks/employz/", "razorpay": "/api/hooks/payments/"}


def _fernet() -> Fernet:
    raw = settings.connections_key or ("conn:" + (settings.jwt_secret or "development-only-secret-do-not-use-in-production-0000"))
    if settings.connections_key:
        try:
            return Fernet(settings.connections_key.encode())
        except (ValueError, TypeError):
            pass
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest()))


def seal(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def unseal(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        return ""


def _secret_fields(kind: str) -> set[str]:
    return {k for k, _, t, _ in SPECS[kind]["fields"] if t == "secret"}


async def get(ws: str, kind: str) -> dict | None:
    """The connection with secrets decrypted (server-side use only), or None when it isn't set up."""
    doc = await db().connections.find_one({"_id": ws}) or {}
    c = doc.get(kind)
    if not c:
        return None
    out = dict(c)
    for k in _secret_fields(kind):
        if out.get(k):
            out[k] = unseal(out[k])
    return out


def ready(kind: str, c: dict | None) -> bool:
    """Enough is filled in to act."""
    if not c:
        return False
    if c.get("provider") == "demo":
        return settings.local_dev
    if kind == "whatsapp":
        if c.get("provider") == "meta":
            return bool(c.get("phone_number_id") and c.get("access_token"))
        return bool(c.get("api_key"))
    if kind == "employz":
        return bool(c.get("location_id") and c.get("token"))
    if kind == "voice":
        return bool(c.get("api_key") and c.get("agent_id") and c.get("phone_number_id"))
    if kind == "razorpay":    # the webhook secret is what lets payments in; the key id says which account
        return bool(c.get("key_id") and c.get("webhook_secret"))
    return any(v for k, v in c.items() if k not in ("status", "updated_at", "updated_by", "webhook_token"))


def view(kind: str, c: dict | None, owner: dict) -> dict:
    spec = SPECS[kind]
    stored = c or {}
    fields = []
    for key, label, typ, help_ in spec["fields"]:
        f = {"key": key, "label": label, "type": typ.split(":")[0], "help": help_}
        if typ.startswith("select:"):
            f["options"] = typ.split(":", 1)[1].split(",")
        if typ == "secret":
            f["set"] = bool(stored.get(key))
            f["value"] = ""
        else:
            f["value"] = stored.get(key, "")
        fields.append(f)
    hook = WEBHOOKS.get(kind)
    return {"kind": kind, "label": spec["label"], "feature": spec["feature"], "available": plans.has(owner, spec["feature"]),
            "tier_name": plans.PLANS[plans.tier_of(spec["feature"])]["name"], "connected": ready(kind, decrypt_view(kind, stored)),
            "status": stored.get("status", ""), "checked_at": stored.get("checked_at").isoformat() if stored.get("checked_at") else None,
            "note": stored.get("note", ""), "updated_by": stored.get("updated_by_label", ""),
            "webhook": f"{settings.app_url}{hook}{stored['webhook_token']}" if hook and stored.get("webhook_token") else None,
            "fields": fields}


def decrypt_view(kind: str, stored: dict) -> dict:
    out = dict(stored)
    for k in _secret_fields(kind):
        if out.get(k):
            out[k] = "set"
    return out


async def save(ws: str, kind: str, values: dict, by: str, by_label: str) -> dict:
    if kind not in SPECS:
        raise HTTPException(404, "No such connection")
    doc = await db().connections.find_one({"_id": ws}) or {}
    current = dict(doc.get(kind) or {})
    allowed = {k: t for k, _, t, _ in SPECS[kind]["fields"]}
    for key, raw in (values or {}).items():
        if key not in allowed:
            continue
        typ = allowed[key]
        val = str(raw if raw is not None else "").strip()[:2000]
        if typ == "secret":
            if val:
                current[key] = seal(val)
            elif raw is None:
                current.pop(key, None)       # explicit null clears a secret
            continue
        if typ.startswith("select:") and val and val not in typ.split(":", 1)[1].split(",") + (["demo"] if settings.local_dev else []):
            raise HTTPException(400, f"Pick one of the options for {key}")
        if typ == "number" and val and not val.isdigit():
            raise HTTPException(400, f"{key} must be a number")
        current[key] = val
    if kind in WEBHOOKS and not current.get("webhook_token"):
        current["webhook_token"] = secrets.token_urlsafe(18)
    current.update(updated_at=now(), updated_by=by, updated_by_label=by_label, status=current.get("status") or "saved")
    await db().connections.update_one({"_id": ws}, {"$set": {kind: current}}, upsert=True)
    return current


async def mark(ws: str, kind: str, status: str, note: str) -> None:
    await db().connections.update_one({"_id": ws}, {"$set": {f"{kind}.status": status, f"{kind}.note": note[:300], f"{kind}.checked_at": now()}})


async def remove(ws: str, kind: str) -> None:
    await db().connections.update_one({"_id": ws}, {"$unset": {kind: ""}})


async def by_webhook(kind: str, token: str) -> tuple[str, dict] | None:
    """(owner id, connection) for an incoming webhook, or None for an unknown address."""
    if not token or len(token) < 16:
        return None
    doc = await db().connections.find_one({f"{kind}.webhook_token": token})
    if not doc:
        return None
    return doc["_id"], await get(doc["_id"], kind)


async def ensure_indexes() -> None:
    for kind in WEBHOOKS:
        await db().connections.create_index(f"{kind}.webhook_token")
