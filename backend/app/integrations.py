"""
Talking to the owner's own accounts. Every function returns a small result dict and never raises for a network problem,
so an agent can record what happened and carry on.

WhatsApp (owner's number, through their "WhatsApp tool")
    AiSensy:  approved templates through AiSensy's campaign API (each template is a "campaign" in the owner's AiSensy);
              free-form replies inside the 24-hour window through AiSensy's project API (AISENSY_PROJECT_API_BASE,
              header X-AiSensy-Project-API-Pwd, Meta Cloud API message format).
    Meta:     the WhatsApp Cloud API directly (graph.facebook.com/<version>/<phone number id>/messages).
    Wati:     <api_url>/api/v1/sendTemplateMessage?whatsappNumber= (template) and /api/v1/sendSessionMessage/<number>
              ?messageText= (reply), Bearer token; incoming messages arrive as {"eventType": "message", "waId", "senderName", "text"}.
    Interakt: https://api.interakt.ai/v1/public/message/ with "Authorization: Basic <secret key>", type Template or Text;
              incoming messages arrive as {"type": "message_received", "data": {"customer": …, "message": …}}.
    Wati and Interakt (v2.3) are written from their public API documents and are UNTESTED until a real account is connected.
Employz.ai (GoHighLevel API v2)
    contacts/upsert, opportunities (create, move stage). Header Version: 2021-07-28, Bearer private integration token.
ElevenLabs
    outbound call through the agent's Twilio or SIP-trunk number; post-call webhooks signed with HMAC-SHA256.

With provider "demo" on a laptop, nothing leaves the machine: messages and calls are written to the `outbox` collection.
Tests swap TRANSPORT for a fake server.
"""
import hashlib
import hmac
import logging
import re
import time

import httpx

from .config import settings
from .db import db, now
from .messaging import bearer, split_number, wati_parameters

log = logging.getLogger("integrations")
TRANSPORT: httpx.AsyncBaseTransport | None = None   # tests set a MockTransport
AISENSY_CAMPAIGN_URL = "https://backend.aisensy.com/campaign/t1/api/v2"
GHL_VERSION = "2021-07-28"


def _client(timeout: float = 20) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, transport=TRANSPORT) if TRANSPORT else httpx.AsyncClient(timeout=timeout)


def digits(phone: str) -> str:
    d = re.sub(r"\D", "", phone or "")
    if len(d) == 11 and d.startswith("0"):
        d = d[1:]
    return ("91" + d) if len(d) == 10 else d


def _param(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:900] or "-"


async def _demo(channel: str, ws: str, to: str, kind: str, payload) -> dict:
    await db().outbox.insert_one({"channel": channel, "ws": ws, "to": digits(to), "kind": kind, "params": payload, "at": now(), "via": "owner-demo"})
    return {"sent": True, "via": "demo", "id": f"demo-{int(time.time() * 1000)}"}


# ───────────────────────── WhatsApp on the owner's own number ─────────────────────────
async def wa_template(ws: str, conn: dict, to: str, template: str, params: list, user_name: str = "", kind: str = "") -> dict:
    """A business-started message (outside the 24-hour window): an approved template on the owner's number."""
    if not conn or not template:
        return {"sent": False, "via": "owner-whatsapp", "error": "No template is set up for this message yet."}
    if conn.get("provider") == "demo" and settings.local_dev:
        return await _demo("whatsapp", ws, to, kind or template, [_param(p) for p in params])
    try:
        async with _client() as c:
            if conn.get("provider") == "meta":
                body = {"messaging_product": "whatsapp", "to": digits(to), "type": "template",
                        "template": {"name": template, "language": {"code": conn.get("language") or "en"},
                                     "components": [{"type": "body", "parameters": [{"type": "text", "text": _param(p)} for p in params]}]}}
                r = await c.post(f"{settings.whatsapp_graph_base}/{conn.get('phone_number_id')}/messages",
                                 headers={"Authorization": f"Bearer {conn.get('access_token')}"}, json=body)
            elif conn.get("provider") == "wati":
                r = await c.post(f"{_wati_base(conn)}/api/v1/sendTemplateMessage", params={"whatsappNumber": digits(to)},
                                 headers={"Authorization": bearer(conn.get("api_token", ""))},
                                 json={"template_name": template, "broadcast_name": f"hub-{kind or template}"[:60],
                                       "parameters": wati_parameters([_param(p) for p in params])})
            elif conn.get("provider") == "interakt":
                cc, number = split_number(digits(to))
                r = await c.post(settings.interakt_api_url, headers={"Authorization": f"Basic {conn.get('api_key', '')}"},
                                 json={"countryCode": cc, "phoneNumber": number, "type": "Template", "callbackData": (kind or template)[:60],
                                       "template": {"name": template, "languageCode": conn.get("language") or "en",
                                                    "bodyValues": [_param(p) for p in params]}})
            else:
                r = await c.post(AISENSY_CAMPAIGN_URL, json={"apiKey": conn.get("api_key"), "campaignName": template, "destination": digits(to),
                                                             "userName": _param(user_name or "there")[:60],
                                                             "templateParams": [_param(p) for p in params], "source": "action-hub"})
        ok = r.status_code in (200, 201) and not _refused(r)
        if not ok:
            log.warning("owner WhatsApp template %s failed: %s %s", template, r.status_code, r.text[:200])
        return {"sent": ok, "via": "owner-whatsapp", "id": _msg_id(r) if ok else None,
                "error": None if ok else "WhatsApp didn't accept the message. Check the template name and that it is approved."}
    except httpx.HTTPError as e:
        log.warning("owner WhatsApp error: %s", type(e).__name__)
        return {"sent": False, "via": "owner-whatsapp", "error": "WhatsApp couldn't be reached. Try again in a minute."}


async def wa_text(ws: str, conn: dict, to: str, text: str) -> dict:
    """A free-form message — only allowed within 24 hours of the customer's last message."""
    if not conn:
        return {"sent": False, "via": "owner-whatsapp", "error": "Your WhatsApp number isn't connected yet."}
    if conn.get("provider") == "demo" and settings.local_dev:
        return await _demo("whatsapp", ws, to, "chat", [text[:1000]])
    body = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": digits(to), "type": "text",
            "text": {"preview_url": False, "body": text[:4000]}}
    try:
        async with _client() as c:
            if conn.get("provider") == "meta":
                r = await c.post(f"{settings.whatsapp_graph_base}/{conn.get('phone_number_id')}/messages",
                                 headers={"Authorization": f"Bearer {conn.get('access_token')}"}, json=body)
            elif conn.get("provider") == "wati":
                r = await c.post(f"{_wati_base(conn)}/api/v1/sendSessionMessage/{digits(to)}", params={"messageText": text[:4000]},
                                 headers={"Authorization": bearer(conn.get("api_token", ""))})
            elif conn.get("provider") == "interakt":
                cc, number = split_number(digits(to))
                r = await c.post(settings.interakt_api_url, headers={"Authorization": f"Basic {conn.get('api_key', '')}"},
                                 json={"countryCode": cc, "phoneNumber": number, "type": "Text", "data": {"message": text[:4000]}})
            else:
                if not (conn.get("project_id") and conn.get("project_password")):
                    return {"sent": False, "via": "owner-whatsapp", "error": "Add the AiSensy project id and password to send chat replies."}
                r = await c.post(f"{settings.aisensy_project_api_base}/project/{conn['project_id']}/messages",
                                 headers={"X-AiSensy-Project-API-Pwd": conn["project_password"]}, json=body)
        ok = r.status_code in (200, 201) and not _refused(r)
        if not ok:
            log.warning("owner WhatsApp text failed: %s %s", r.status_code, r.text[:200])
        return {"sent": ok, "via": "owner-whatsapp", "id": _msg_id(r) if ok else None,
                "error": None if ok else "WhatsApp didn't accept the reply."}
    except httpx.HTTPError as e:
        log.warning("owner WhatsApp error: %s", type(e).__name__)
        return {"sent": False, "via": "owner-whatsapp", "error": "WhatsApp couldn't be reached. Try again in a minute."}


def _wati_base(conn: dict) -> str:
    return str(conn.get("api_url") or "").strip().rstrip("/")


def _refused(r: httpx.Response) -> bool:
    """Wati and Interakt answer 200 with {"result": false} when they don't accept a message."""
    try:
        d = r.json()
    except ValueError:
        return False
    return isinstance(d, dict) and d.get("result") is False


def _msg_id(r: httpx.Response) -> str | None:
    try:
        d = r.json()
    except ValueError:
        return None
    msgs = d.get("messages") if isinstance(d, dict) else None
    if isinstance(msgs, list) and msgs and isinstance(msgs[0], dict):
        return str(msgs[0].get("id") or "")[:120] or None
    return str((d or {}).get("id") or (d or {}).get("messageId") or "")[:120] or None


def parse_inbound(payload: dict) -> list[dict]:
    """Incoming WhatsApp messages in Meta's webhook format (AiSensy's direct webhooks forward the same shape), plus a flat
    {phone, name, text} shape. → [{"from", "name", "text", "id", "type"}]"""
    out = []
    if not isinstance(payload, dict):
        return out
    for entry in payload.get("entry") or []:
        for ch in (entry or {}).get("changes") or []:
            v = (ch or {}).get("value") or {}
            names = {c.get("wa_id"): ((c.get("profile") or {}).get("name") or "") for c in v.get("contacts") or [] if isinstance(c, dict)}
            for m in v.get("messages") or []:
                if not isinstance(m, dict):
                    continue
                typ = m.get("type", "text")
                text = ((m.get("text") or {}).get("body") if typ == "text" else
                        (m.get("button") or {}).get("text") if typ == "button" else
                        ((m.get("interactive") or {}).get("button_reply") or {}).get("title") if typ == "interactive" else
                        f"[{typ}]")
                out.append({"from": str(m.get("from", "")), "name": names.get(m.get("from"), ""), "text": str(text or "")[:4000],
                            "id": str(m.get("id", ""))[:120], "type": typ})
    # Wati: one message per call; "owner": true is a message the business itself sent (not a customer)
    if not out and payload.get("waId") and payload.get("eventType") in ("message", None) and not payload.get("owner"):
        typ = str(payload.get("type") or "text")
        text = payload.get("text") if typ == "text" else (((payload.get("buttonReply") or {}).get("text")) or f"[{typ}]")
        out.append({"from": str(payload.get("waId")), "name": str(payload.get("senderName") or "")[:80], "text": str(text or "")[:4000],
                    "id": str(payload.get("whatsappMessageId") or payload.get("id") or "")[:120], "type": typ})
    # Interakt: {"type": "message_received", "data": {"customer": {...}, "message": {...}}}
    if not out and payload.get("type") == "message_received" and isinstance(payload.get("data"), dict):
        cust = payload["data"].get("customer") or {}
        m = payload["data"].get("message") or {}
        if m.get("chat_message_type", "CustomerMessage") == "CustomerMessage":
            ctype = str(m.get("message_content_type") or "Text")
            phone = cust.get("channel_phone_number") or (str(cust.get("country_code") or "").lstrip("+") + str(cust.get("phone_number") or ""))
            out.append({"from": str(phone or ""), "name": str(((cust.get("traits") or {}).get("name")) or "")[:80],
                        "text": str(m.get("message") if ctype.lower() == "text" else f"[{ctype.lower()}]")[:4000],
                        "id": str(m.get("id") or "")[:120], "type": ctype.lower()})
    if not out and (payload.get("phone") or payload.get("from")) and (payload.get("text") or payload.get("message")):
        out.append({"from": str(payload.get("phone") or payload.get("from")), "name": str(payload.get("name") or "")[:80],
                    "text": str(payload.get("text") or payload.get("message"))[:4000], "id": str(payload.get("id") or "")[:120], "type": "text"})
    return out


def meta_signature_ok(app_secret: str, header: str, body: bytes) -> bool:
    if not app_secret:
        return True   # WhatsApp tools without an app secret (AiSensy, Wati, Interakt) rely on the secret webhook address
    expected = "sha256=" + hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header or "")


# ───────────────────────── Employz.ai (GoHighLevel API v2) ─────────────────────────
def _ghl_headers(conn: dict) -> dict:
    return {"Authorization": f"Bearer {conn.get('token')}", "Version": GHL_VERSION, "Accept": "application/json"}


async def employz_check(conn: dict) -> dict:
    if conn.get("provider") == "demo" and settings.local_dev:
        return {"ok": True, "note": "Demo connection"}
    try:
        async with _client() as c:
            r = await c.get(f"{settings.employz_api_base}/opportunities/pipelines", params={"locationId": conn.get("location_id")},
                            headers=_ghl_headers(conn))
        if r.status_code == 200:
            pipes = (r.json() or {}).get("pipelines") or []
            names = ", ".join(p.get("name", "") for p in pipes[:5] if isinstance(p, dict))
            return {"ok": True, "note": f"Connected. Pipelines: {names or 'none yet'}."}
        return {"ok": False, "note": "Employz.ai refused the token. Check the location id and the private integration token."}
    except httpx.HTTPError:
        return {"ok": False, "note": "Employz.ai couldn't be reached. Try again in a minute."}


async def employz_sync_lead(ws: str, conn: dict, lead: dict, business_name: str = "") -> dict:
    """Creates or updates the contact, then creates the opportunity (first time) or moves it to the lead's stage."""
    if conn.get("provider") == "demo" and settings.local_dev:
        await _demo("employz", ws, lead.get("phone", ""), "sync", {"stage": lead.get("stage"), "status": lead.get("status")})
        return {"ok": True, "contact_id": lead.get("crm", {}).get("contact_id") or f"demo-c-{lead['_id'][:8]}",
                "opportunity_id": lead.get("crm", {}).get("opportunity_id") or f"demo-o-{lead['_id'][:8]}"}
    crm = dict(lead.get("crm") or {})
    stage_id = conn.get(f"stage_{lead.get('stage') or 'identification'}") or conn.get("stage_identification")
    status = {"won": "won", "lost": "lost"}.get(lead.get("status"), "open")
    try:
        async with _client() as c:
            if not crm.get("contact_id"):
                first, _, last = (lead.get("name") or "").partition(" ")
                r = await c.post(f"{settings.employz_api_base}/contacts/upsert", headers=_ghl_headers(conn),
                                 json={"locationId": conn.get("location_id"), "firstName": first[:60], "lastName": last[:60],
                                       "phone": "+" + digits(lead.get("phone", "")), "source": f"Action Hub · {lead.get('source', 'website')}",
                                       "tags": ["action-hub", lead.get("source", "website")]})
                if r.status_code not in (200, 201):
                    return {"ok": False, "error": f"contact {r.status_code}"}
                crm["contact_id"] = ((r.json() or {}).get("contact") or {}).get("id")
            if not crm.get("opportunity_id") and conn.get("pipeline_id") and stage_id and crm.get("contact_id"):
                body = {"pipelineId": conn["pipeline_id"], "locationId": conn.get("location_id"), "pipelineStageId": stage_id,
                        "name": f"{lead.get('name') or 'Lead'} — {business_name}"[:120], "status": status, "contactId": crm["contact_id"]}
                if lead.get("value"):
                    body["monetaryValue"] = float(lead["value"])
                r = await c.post(f"{settings.employz_api_base}/opportunities/", headers=_ghl_headers(conn), json=body)
                if r.status_code in (200, 201):
                    crm["opportunity_id"] = ((r.json() or {}).get("opportunity") or {}).get("id")
            elif crm.get("opportunity_id") and stage_id:
                body = {"pipelineStageId": stage_id, "status": status}
                if lead.get("value"):
                    body["monetaryValue"] = float(lead["value"])
                r = await c.put(f"{settings.employz_api_base}/opportunities/{crm['opportunity_id']}", headers=_ghl_headers(conn), json=body)
                if r.status_code not in (200, 201):
                    return {"ok": False, "error": f"opportunity {r.status_code}", **crm}
        return {"ok": True, **crm}
    except httpx.HTTPError:
        return {"ok": False, "error": "unreachable", **crm}


# ───────────────────────── ElevenLabs ─────────────────────────
async def voice_check(conn: dict) -> dict:
    if conn.get("provider") == "demo" and settings.local_dev:
        return {"ok": True, "note": "Demo connection"}
    try:
        async with _client() as c:
            r = await c.get(f"{settings.elevenlabs_api_base}/v1/convai/agents/{conn.get('agent_id')}", headers={"xi-api-key": conn.get("api_key", "")})
        if r.status_code == 200:
            return {"ok": True, "note": f"Connected to the agent “{(r.json() or {}).get('name', 'Telecaller')}”."}
        return {"ok": False, "note": "ElevenLabs refused the key or the agent id."}
    except httpx.HTTPError:
        return {"ok": False, "note": "ElevenLabs couldn't be reached. Try again in a minute."}


async def voice_call(ws: str, conn: dict, to: str, variables: dict) -> dict:
    if conn.get("provider") == "demo" and settings.local_dev:
        res = await _demo("voice", ws, to, "call", variables)
        return {"ok": True, "conversation_id": res["id"]}
    path = "/v1/convai/sip-trunk/outbound-call" if conn.get("mode") == "sip" else "/v1/convai/twilio/outbound-call"
    body = {"agent_id": conn.get("agent_id"), "agent_phone_number_id": conn.get("phone_number_id"), "to_number": "+" + digits(to),
            "conversation_initiation_client_data": {"dynamic_variables": {k: str(v)[:200] for k, v in variables.items()}}}
    try:
        async with _client(30) as c:
            r = await c.post(f"{settings.elevenlabs_api_base}{path}", headers={"xi-api-key": conn.get("api_key", "")}, json=body)
        d = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code == 200 and d.get("success", True):
            return {"ok": True, "conversation_id": d.get("conversation_id") or d.get("callSid")}
        return {"ok": False, "error": str(d.get("message") or r.status_code)[:200]}
    except httpx.HTTPError:
        return {"ok": False, "error": "ElevenLabs couldn't be reached"}


def elevenlabs_signature_ok(secret: str, header: str, body: bytes, tolerance: int = 1800) -> bool:
    """ElevenLabs-Signature: t=<unix time>,v0=<hex HMAC-SHA256 of '<t>.<raw body>' with the webhook secret>."""
    if not secret or not header:
        return False
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    try:
        ts = int(parts.get("t", ""))
    except ValueError:
        return False
    if abs(time.time() - ts) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, parts.get("v0", ""))
