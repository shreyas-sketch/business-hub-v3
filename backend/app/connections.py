"""
Connections to each owner's own accounts (Growth Mentorship and up; set up for them by the team, or by the owner).

    whatsapp   their own WhatsApp Business number, through their "WhatsApp tool": AiSensy, Wati, Interakt, or Meta's
               WhatsApp Cloud API directly (v2.3: owners see "WhatsApp tool" and pick their service from a list)
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

# v2.3: the owner's own WhatsApp tool is its own feature when the registry has one ("whatsapp_tool": opened once the member
# pays), so connecting a number never also switches on the Growth plan's 24×7 AI chat (whatsapp_ai).
WHATSAPP_FEATURE = "whatsapp_tool" if "whatsapp_tool" in plans.DEFAULT_FEATURES else "whatsapp_ai"
# The services behind "WhatsApp tool", as the owner sees them in the picker, and the fields each one needs.
WHATSAPP_SERVICES = {"aisensy": "AiSensy", "interakt": "Interakt", "wati": "Wati", "meta": "Meta WhatsApp Cloud API", "demo": "Demo (test only)"}
TEMPLATE_FIELDS = ["tpl_instant", "tpl_lead_response", "tpl_followup", "tpl_reminder", "tpl_review", "tpl_customer", "tpl_campaign"]
WHATSAPP_FIELDS = {
    "aisensy": ["provider", "number", "api_key", "project_id", "project_password", *TEMPLATE_FIELDS],
    "interakt": ["provider", "number", "api_key", *TEMPLATE_FIELDS, "language"],
    "wati": ["provider", "number", "api_url", "api_token", *TEMPLATE_FIELDS],
    "meta": ["provider", "number", "phone_number_id", "access_token", "app_secret", *TEMPLATE_FIELDS, "language"],
    "demo": ["provider", "number", *TEMPLATE_FIELDS],
}

# kind → (label, feature, fields). Field: (key, label, type, help); type: text | secret | select:<a>,<b> | number
SPECS: dict[str, dict] = {
    "whatsapp": {"label": "WhatsApp tool — your own number", "feature": WHATSAPP_FEATURE, "fields": [
        ("provider", "Your WhatsApp service", "select:aisensy,interakt,wati,meta", "The service your WhatsApp Business number runs on"),
        ("number", "Your WhatsApp Business number", "text", "The number customers message, e.g. 98200 12345"),
        ("api_key", "API key", "secret", "From your WhatsApp service's API or developer settings"),
        ("project_id", "Project id", "text", "For two-way chat replies"),
        ("project_password", "Project API password", "secret", "For two-way chat replies"),
        ("api_url", "API endpoint", "text", "Your account's API address, like https://live-mt-server.wati.io/123456"),
        ("api_token", "Access token", "secret", "From the API page of your WhatsApp service"),
        ("phone_number_id", "Phone number id", "text", "From your Meta app's WhatsApp settings"),
        ("access_token", "Access token", "secret", "A permanent system-user token"),
        ("app_secret", "App secret", "secret", "Checks that incoming messages really come from Meta"),
        ("tpl_instant", "Template: instant reply to a new inquiry", "text", "{{1}} customer name, {{2}} business"),
        ("tpl_lead_response", "Template: first reply with a booking", "text", "{{1}} customer name, {{2}} business, {{3}} the booking line"),
        ("tpl_followup", "Template: follow-up", "text", "{{1}} name, {{2}} business, {{3}} message"),
        ("tpl_reminder", "Template: payment reminder", "text", "{{1}} name, {{2}} business, {{3}} amount, {{4}} due date, {{5}} how to pay"),
        ("tpl_review", "Template: review request", "text", "{{1}} name, {{2}} business, {{3}} review link"),
        ("tpl_customer", "Template: customer message", "text", "{{1}} name, {{2}} business, {{3}} message (reorders, referrals, greetings)"),
        ("tpl_campaign", "Template: money campaign", "text", "{{1}} name, {{2}} business, {{3}} message"),
        ("language", "Template language code", "text", "e.g. en, en_US, hi"),
    ]},
    "employz": {"label": "CRM", "feature": "crm_sync", "fields": [
        ("provider", "Your CRM", "select:employz", "The CRM your deals are kept in"),
        ("location_id", "Sub-account (location) id", "text", "Your CRM → Settings → Business profile → Location ID"),
        ("token", "Private integration token", "secret", "Your CRM → Settings → Private integrations (contacts + opportunities)"),
        ("pipeline_id", "Pipeline id", "text", "The Football Field pipeline from the snapshot"),
        ("stage_identification", "Stage id: Identification", "text", ""),
        ("stage_logic", "Stage id: Logic / reason", "text", ""),
        ("stage_pain", "Stage id: Pain / consequence", "text", ""),
        ("stage_vision", "Stage id: Solution / vision", "text", ""),
        ("stage_close", "Stage id: Ask / close", "text", ""),
        ("app_link", "Link to open this account", "text", "Shown to the owner as 'Open your CRM'"),
    ]},
    "voice": {"label": "AI voice agent", "feature": "voice_agent", "fields": [
        ("provider", "Your voice agent service", "select:elevenlabs", "The service your AI Telecaller runs on"),
        ("api_key", "API key", "secret", "Your voice agent service → Developers → API keys"),
        ("agent_id", "Agent id", "text", "The Telecaller agent built in the bootcamp"),
        ("phone_number_id", "Agent phone number id", "text", "Your voice agent service → Phone numbers"),
        ("mode", "Phone line", "select:twilio,sip", "A cloud phone number, or a SIP trunk from your telephony provider"),
        ("webhook_secret", "Post-call webhook secret", "secret", "Shown when you add the post-call webhook in your voice agent service"),
        ("hours", "Calling hours", "text", "e.g. 10-19 (India time)"),
    ]},
    "tally": {"label": "Accounting software", "feature": "more_connections", "fields": [
        ("provider", "Your accounting software", "select:tally", "The accounting software on your office computer"),
        ("bridge_url", "Bridge address", "text", "The bridge running next to your accounting software on the office computer"),
        ("token", "Bridge token", "secret", ""),
    ]},
    "email": {"label": "Email", "feature": "more_connections", "fields": [
        ("from_address", "Send from", "text", "e.g. hello@yourbusiness.in"),
        ("smtp_host", "SMTP host", "text", ""), ("smtp_port", "SMTP port", "number", "587"),
        ("smtp_user", "SMTP user", "text", ""), ("smtp_password", "SMTP password", "secret", ""),
    ]},
    "calendar": {"label": "Calendar", "feature": "more_connections", "fields": [
        ("booking_link", "Booking link", "text", "Your online booking page — the Meeting Setter shares it"),
    ]},
    # v2.1 (3.5): the owner's own Razorpay account; every captured payment becomes an invoice, a refund a credit note
    "razorpay": {"label": "Payment gateway", "feature": "gateway_invoicing", "fields": [
        ("provider", "Your payment gateway", "select:razorpay", "The gateway your customers pay you through"),
        ("key_id", "Key id", "text", "Your payment gateway → Account & Settings → API keys. Test keys start with rzp_test_"),
        ("key_secret", "Key secret", "secret", "Shown once when you create the key"),
        ("webhook_secret", "Webhook secret", "secret", "The secret you type when you add the webhook below in your payment gateway's webhook settings"),
    ]},
}
# v2.3: every connection is named by what it is (CRM, payment gateway…). The software behind it appears only in the
# "Your …" picker, so another service can be added later without renaming anything the owner sees.
SERVICES = {"employz": {"employz": "Employz.ai"}, "razorpay": {"razorpay": "Razorpay"}, "voice": {"elevenlabs": "ElevenLabs"},
            "tally": {"tally": "Tally"}}
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
        if c.get("provider") == "wati":
            return bool(c.get("api_url") and c.get("api_token"))
        return bool(c.get("api_key"))
    if kind == "employz":
        return bool(c.get("location_id") and c.get("token"))
    if kind == "voice":
        return bool(c.get("api_key") and c.get("agent_id") and c.get("phone_number_id"))
    if kind == "razorpay":    # the webhook secret is what lets payments in; the key id says which account
        return bool(c.get("key_id") and c.get("webhook_secret"))
    return any(v for k, v in c.items() if k not in ("status", "updated_at", "updated_by", "webhook_token", "provider"))


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
        if key == "provider" and not f["value"] and len(f.get("options") or []) == 1:
            f["value"] = f["options"][0]          # one service so far: it's already picked
        fields.append(f)
    hook = WEBHOOKS.get(kind)
    extra = {"services": {k: v for k, v in WHATSAPP_SERVICES.items() if k != "demo" or settings.local_dev},
             "service_fields": WHATSAPP_FIELDS} if kind == "whatsapp" else {"services": SERVICES[kind]} if kind in SERVICES else {}
    return {**extra, "kind": kind, "label": spec["label"], "feature": spec["feature"], "available": plans.has(owner, spec["feature"]),
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
