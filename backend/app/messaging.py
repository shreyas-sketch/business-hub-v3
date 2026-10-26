"""
The hub's own WhatsApp number — through a "WhatsApp tool" — with MSG91 SMS fallback for login codes.
With no keys configured (development), messages are written to the `outbox` collection instead of sent.

WHATSAPP_PROVIDER picks the tool (v2.3; owners and the team only ever see "WhatsApp tool"):
    aisensy   approved templates sent as API campaigns (the default; what the hub has always used)
    wati      POST <WATI_API_URL>/api/v1/sendTemplateMessage?whatsappNumber=<number>, Bearer token,
              {"template_name", "broadcast_name", "parameters": [{"name": "1", "value": …}, …]}
    interakt  POST https://api.interakt.ai/v1/public/message/, "Authorization: Basic <secret key>",
              {"countryCode", "phoneNumber", "type": "Template", "template": {"name", "languageCode", "bodyValues", …}}
    meta      the WhatsApp Cloud API directly (graph.facebook.com/<version>/<phone number id>/messages)
Wati, Interakt and Meta-direct are written from their public API documents and are UNTESTED until a real account is
connected: try a login code and an owner alert on a test number before switching a live hub over.
The template names are the AISENSY_*_CAMPAIGN settings, whichever tool is used; variables are numbered {{1}}, {{2}}…
"""
import logging
import re

import httpx

from .config import settings
from .db import db, now

log = logging.getLogger("messaging")
AISENSY_URL = "https://backend.aisensy.com/campaign/t1/api/v2"
MSG91_OTP_URL = "https://control.msg91.com/api/v5/otp"
PROVIDERS = ("aisensy", "wati", "interakt", "meta")


def _digits(phone: str) -> str:
    return phone.lstrip("+")


def provider() -> str:
    p = (settings.whatsapp_provider or "aisensy").lower()
    return p if p in PROVIDERS else "aisensy"


def hub_ready() -> bool:
    """The hub's WhatsApp tool has its keys."""
    p = provider()
    if p == "wati":
        return bool(settings.wati_api_url and settings.wati_api_token)
    if p == "interakt":
        return bool(settings.interakt_api_key)
    if p == "meta":
        return bool(settings.whatsapp_hub_phone_number_id and settings.whatsapp_hub_access_token)
    return bool(settings.aisensy_api_key)


def split_number(phone: str) -> tuple[str, str]:
    """(country code with +, the number without it) — Interakt wants them apart. Indian numbers: +91 and 10 digits."""
    d = re.sub(r"\D", "", phone or "")
    if len(d) == 10:
        return "+91", d
    return ("+" + d[:-10], d[-10:]) if len(d) > 10 else ("+91", d)


def bearer(token: str) -> str:
    token = (token or "").strip()
    return token if token.lower().startswith("bearer ") else f"Bearer {token}"


def wati_parameters(params: list[str]) -> list[dict]:
    return [{"name": str(i + 1), "value": p} for i, p in enumerate(params)]


def _param(value) -> str:
    """WhatsApp rejects template parameters that contain new lines, tabs or runs of spaces, or are empty."""
    return re.sub(r"\s+", " ", str(value or "")).strip()[:900] or "-"


async def _outbox(channel: str, to: str, kind: str, params: list[str], **extra) -> dict:
    await db().outbox.insert_one({"channel": channel, "to": to, "kind": kind, "params": params, "at": now(), **extra})
    log.info("DEV %s → %s [%s] %s", channel, to, kind, params)
    return {"sent": True, "via": f"dev-{channel}"}


async def whatsapp_template(to: str, campaign: str, user_name: str, params: list[str], kind: str,
                            media: dict | None = None, buttons: list | None = None) -> dict:
    """Sends one approved template from the hub's own number. Returns {"sent": bool, "via": str}. Never raises.
    `media` ({"url", "filename"}) is the header of a media template; `buttons` fills a URL button's dynamic part."""
    if not hub_ready() or not campaign:
        if not settings.local_dev:
            log.warning("WhatsApp %s not sent: the WhatsApp tool's key or the template name is missing (%s)", kind, provider())
            return {"sent": False, "via": "whatsapp-not-configured"}
        extra = {k: v for k, v in (("media", media), ("buttons", buttons)) if v}
        return await _outbox("whatsapp", to, kind, params, **extra)
    params = [_param(p) for p in params]
    if provider() != "aisensy":
        return await _other_tool(to, campaign, params, kind, media, buttons)
    payload = {"apiKey": settings.aisensy_api_key, "campaignName": campaign, "destination": _digits(to),
               "userName": _param(user_name or "there")[:60], "templateParams": params, "source": "action-hub"}
    if media:
        payload["media"] = {"url": media["url"], "filename": media.get("filename") or "report"}
    if buttons:
        payload["buttons"] = buttons
    if kind == "otp" and settings.aisensy_otp_button:
        # Meta authentication templates carry a copy-code button that needs the code as its own parameter
        payload["buttons"] = [{"type": "button", "sub_type": "url", "index": 0, "parameters": [{"type": "text", "text": params[0]}]}]
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(AISENSY_URL, json=payload)
        ok = r.status_code == 200
        reply = re.sub(r"\s+", " ", r.text or "")[:300].replace(settings.aisensy_api_key, "***")
        if ok:
            log.info("WhatsApp tool (aisensy) %s accepted for campaign %r: %s", kind, campaign, reply)
        else:  # AiSensy says why (wrong campaign name, template not approved, low wallet balance…) — keep it in the log
            log.warning("WhatsApp tool (aisensy) %s failed for campaign %r: %s %s", kind, campaign, r.status_code, reply)
        out = {"sent": ok, "via": "whatsapp"}
        try:   # the message id, when AiSensy returns one, lets delivery ticks be matched to this message
            body = r.json() if ok else {}
            mid = next((body.get(k) for k in ("submitted_message_id", "messageId", "message_id", "id") if isinstance(body, dict) and isinstance(body.get(k), str)), None)
        except ValueError:
            mid = None
        if mid:
            out["id"] = mid
        return out
    except httpx.HTTPError as e:
        log.warning("WhatsApp tool (aisensy) %s error: %s", kind, type(e).__name__)
        return {"sent": False, "via": "whatsapp"}


def _otp_button(kind: str, params: list[str]) -> bool:
    return kind == "otp" and settings.aisensy_otp_button and bool(params)


async def _other_tool(to: str, template: str, params: list[str], kind: str, media: dict | None, buttons: list | None) -> dict:
    """Wati, Interakt or Meta directly (see the module notes: untested until a real account is connected)."""
    p = provider()
    lang = settings.whatsapp_template_language or "en"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            if p == "wati":
                if media:
                    log.warning("WhatsApp %s: media headers aren't sent through Wati here; use a text template", kind)
                r = await client.post(f"{settings.wati_api_url}/api/v1/sendTemplateMessage", params={"whatsappNumber": _digits(to)},
                                      headers={"Authorization": bearer(settings.wati_api_token)},
                                      json={"template_name": template, "broadcast_name": f"hub-{kind}"[:60], "parameters": wati_parameters(params)})
            elif p == "interakt":
                cc, number = split_number(to)
                tpl: dict = {"name": template, "languageCode": lang, "bodyValues": params}
                if media:
                    tpl["headerValues"] = [media["url"]]
                    if media.get("filename"):
                        tpl["fileName"] = media["filename"]
                if _otp_button(kind, params):
                    tpl["buttonValues"] = {"0": [params[0]]}
                elif buttons:
                    tpl["buttonValues"] = {str(b.get("index", i)): [str(x.get("text", "")) for x in b.get("parameters") or []]
                                           for i, b in enumerate(buttons) if isinstance(b, dict)}
                r = await client.post(settings.interakt_api_url, headers={"Authorization": f"Basic {settings.interakt_api_key}"},
                                      json={"countryCode": cc, "phoneNumber": number, "type": "Template", "callbackData": kind[:60], "template": tpl})
            else:   # meta
                components: list = [{"type": "body", "parameters": [{"type": "text", "text": x} for x in params]}]
                if media:
                    kind_ = "document" if str(media.get("url", "")).lower().endswith(".pdf") else "image"
                    head = {"link": media["url"], **({"filename": media["filename"]} if kind_ == "document" and media.get("filename") else {})}
                    components.insert(0, {"type": "header", "parameters": [{"type": kind_, kind_: head}]})
                if _otp_button(kind, params):
                    components.append({"type": "button", "sub_type": "url", "index": "0", "parameters": [{"type": "text", "text": params[0]}]})
                elif buttons:
                    components += [{**b, "index": str(b.get("index", 0))} for b in buttons if isinstance(b, dict)]
                r = await client.post(f"{settings.whatsapp_graph_base}/{settings.whatsapp_hub_phone_number_id}/messages",
                                      headers={"Authorization": bearer(settings.whatsapp_hub_access_token)},
                                      json={"messaging_product": "whatsapp", "to": _digits(to), "type": "template",
                                            "template": {"name": template, "language": {"code": lang}, "components": components}})
        try:
            body = r.json()
        except ValueError:
            body = {}
        ok = r.status_code in (200, 201) and not (isinstance(body, dict) and body.get("result") is False)
        if not ok:
            log.warning("WhatsApp tool (%s) %s failed: %s", p, kind, r.status_code)
        out = {"sent": ok, "via": "whatsapp"}
        mid = None
        if ok and isinstance(body, dict):
            msgs = body.get("messages")
            if isinstance(msgs, list) and msgs and isinstance(msgs[0], dict):
                mid = msgs[0].get("id")
            mid = mid or next((body.get(k) for k in ("id", "messageId", "message_id", "localMessageId") if isinstance(body.get(k), str)), None)
        if mid:
            out["id"] = str(mid)[:120]
        return out
    except httpx.HTTPError as e:
        log.warning("WhatsApp tool (%s) %s error: %s", p, kind, type(e).__name__)
        return {"sent": False, "via": "whatsapp"}


async def sms_otp(to: str, code: str) -> dict:
    if not settings.msg91_auth_key or not settings.msg91_otp_template_id:
        return {"sent": False, "via": "sms-not-configured"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(MSG91_OTP_URL, headers={"authkey": settings.msg91_auth_key},
                                  params={"template_id": settings.msg91_otp_template_id, "mobile": _digits(to), "otp": code})
        ok = r.status_code == 200 and r.json().get("type") == "success"
        return {"sent": ok, "via": "sms"}
    except (httpx.HTTPError, ValueError) as e:
        log.warning("MSG91 error: %s", type(e).__name__)
        return {"sent": False, "via": "sms"}


async def send_otp(to: str, code: str) -> dict:
    """WhatsApp first, SMS if WhatsApp is unavailable or fails."""
    wa = hub_ready() and bool(settings.aisensy_otp_campaign)
    configured = wa or bool(settings.msg91_auth_key)
    if not configured:
        if settings.local_dev:
            return await _outbox("whatsapp", to, "otp", [code])
        log.error("Login code not sent: set the WhatsApp tool's key (WHATSAPP_PROVIDER, see .env.example) + AISENSY_OTP_CAMPAIGN "
                  "and/or MSG91_AUTH_KEY + MSG91_OTP_TEMPLATE_ID")
        return {"sent": False, "via": "not-configured"}
    if wa:
        result = await whatsapp_template(to, settings.aisensy_otp_campaign, "", [code], "otp")
        if result["sent"]:
            return result
    return await sms_otp(to, code)
