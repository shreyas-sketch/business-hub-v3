"""
WhatsApp via AiSensy (approved templates, sent as API campaigns) with MSG91 SMS fallback for OTPs.
With no keys configured (development), messages are written to the `outbox` collection instead of sent.
"""
import logging
import re

import httpx

from .config import settings
from .db import db, now

log = logging.getLogger("messaging")
AISENSY_URL = "https://backend.aisensy.com/campaign/t1/api/v2"
MSG91_OTP_URL = "https://control.msg91.com/api/v5/otp"


def _digits(phone: str) -> str:
    return phone.lstrip("+")


def _param(value) -> str:
    """WhatsApp rejects template parameters that contain new lines, tabs or runs of spaces, or are empty."""
    return re.sub(r"\s+", " ", str(value or "")).strip()[:900] or "-"


async def _outbox(channel: str, to: str, kind: str, params: list[str], **extra) -> dict:
    await db().outbox.insert_one({"channel": channel, "to": to, "kind": kind, "params": params, "at": now(), **extra})
    log.info("DEV %s → %s [%s] %s", channel, to, kind, params)
    return {"sent": True, "via": f"dev-{channel}"}


async def whatsapp_template(to: str, campaign: str, user_name: str, params: list[str], kind: str,
                            media: dict | None = None, buttons: list | None = None) -> dict:
    """Sends one approved AiSensy template. Returns {"sent": bool, "via": str}. Never raises.
    `media` ({"url", "filename"}) is the header of a media template; `buttons` fills a URL button's dynamic part."""
    if not settings.aisensy_api_key or not campaign:
        if not settings.local_dev:
            log.warning("WhatsApp %s not sent: AiSensy key or campaign missing", kind)
            return {"sent": False, "via": "whatsapp-not-configured"}
        extra = {k: v for k, v in (("media", media), ("buttons", buttons)) if v}
        return await _outbox("whatsapp", to, kind, params, **extra)
    params = [_param(p) for p in params]
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
            log.info("AiSensy %s accepted for campaign %r: %s", kind, campaign, reply)
        else:  # AiSensy says why (wrong campaign name, template not approved, low wallet balance…) — keep it in the log
            log.warning("AiSensy %s failed for campaign %r: %s %s", kind, campaign, r.status_code, reply)
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
        log.warning("AiSensy %s error: %s", kind, type(e).__name__)
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
    configured = bool(settings.aisensy_api_key and settings.aisensy_otp_campaign) or bool(settings.msg91_auth_key)
    if not configured:
        if settings.local_dev:
            return await _outbox("whatsapp", to, "otp", [code])
        log.error("Login code not sent: set AISENSY_API_KEY + AISENSY_OTP_CAMPAIGN and/or MSG91_AUTH_KEY + MSG91_OTP_TEMPLATE_ID")
        return {"sent": False, "via": "not-configured"}
    if settings.aisensy_api_key and settings.aisensy_otp_campaign:
        result = await whatsapp_template(to, settings.aisensy_otp_campaign, "", [code], "otp")
        if result["sent"]:
            return result
    return await sms_otp(to, code)
