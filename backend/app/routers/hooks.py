"""
Webhooks from the owner's own accounts. Each connection has its own secret address (/api/hooks/<kind>/<token>), so a
message can only reach the owner it belongs to.

- WhatsApp (AiSensy direct webhooks or Meta): customer messages → WhatsApp chats and the AI reply. GET answers Meta's
  verification handshake (hub.verify_token = the token in the address).
- ElevenLabs post-call: the call's transcript and summary → the call record, a note on the lead, and its score.
- Employz.ai (a workflow's webhook action): an opportunity moved stage, or was won or lost → the hub's lead follows.
"""
import json
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from .. import chat_ai, connections, integrations, plans
from ..ai import chat_tasks
from ..db import db, now
from ..services import notify, run_ai

router = APIRouter(prefix="/api/hooks")
log = logging.getLogger("hooks")


@router.get("/whatsapp/{token}")
async def wa_verify(token: str, request: Request):
    q = request.query_params
    found = await connections.by_webhook("whatsapp", token)
    if found and q.get("hub.mode") == "subscribe" and q.get("hub.verify_token") == token:
        return PlainTextResponse(q.get("hub.challenge", ""))
    raise HTTPException(403, "Not allowed")


@router.post("/whatsapp/{token}")
async def wa_inbound(token: str, request: Request):
    found = await connections.by_webhook("whatsapp", token)
    if not found:
        raise HTTPException(404, "Not found")
    ws, conn = found
    body = await request.body()
    if conn.get("provider") == "meta" and not integrations.meta_signature_ok(conn.get("app_secret", ""), request.headers.get("x-hub-signature-256", ""), body):
        raise HTTPException(401, "Bad signature")
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        raise HTTPException(400, "Not JSON")
    handled = 0
    for msg in integrations.parse_inbound(payload)[:20]:
        if not msg.get("from"):
            continue
        try:
            await chat_ai.inbound(ws, conn, msg)
            handled += 1
        except Exception:  # noqa: BLE001  one bad message must not lose the rest (WhatsApp would retry them all)
            log.exception("inbound WhatsApp message failed")
    return JSONResponse({"ok": True, "handled": handled})


@router.post("/voice/{token}")
async def voice_done(token: str, request: Request):
    found = await connections.by_webhook("voice", token)
    if not found:
        raise HTTPException(404, "Not found")
    ws, conn = found
    body = await request.body()
    if not integrations.elevenlabs_signature_ok(conn.get("webhook_secret", ""), request.headers.get("elevenlabs-signature", ""), body):
        raise HTTPException(401, "Bad signature")
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(400, "Not JSON")
    if payload.get("type") != "post_call_transcription":
        return {"ok": True, "ignored": payload.get("type")}
    data = payload.get("data") or {}
    conv = data.get("conversation_id")
    dyn = ((data.get("conversation_initiation_client_data") or {}).get("dynamic_variables") or {})
    call = await db().calls.find_one({"ws": ws, "conversation_id": conv}) if conv else None
    if not call and dyn.get("lead_id"):
        call = await db().calls.find_one({"ws": ws, "lead_id": dyn["lead_id"]}, sort=[("started_at", -1)])
    analysis = data.get("analysis") or {}
    transcript = [{"role": t.get("role"), "message": str(t.get("message") or "")[:1000]} for t in (data.get("transcript") or []) if isinstance(t, dict)][:200]
    summary = str(analysis.get("transcript_summary") or "")[:1500]
    meta = data.get("metadata") or {}
    patch = {"status": "done", "transcript": transcript, "summary": summary, "outcome": analysis.get("call_successful"),
             "duration": meta.get("call_duration_secs"), "ended_at": now()}
    if call:
        await db().calls.update_one({"_id": call["_id"]}, {"$set": patch})
    else:
        call = {"_id": conv or f"call-{now().timestamp()}", "ws": ws, "lead_id": dyn.get("lead_id"), "conversation_id": conv,
                "started_at": now(), **patch}
        await db().calls.insert_one(call)
    lead_id = call.get("lead_id") or dyn.get("lead_id")
    if lead_id and summary:
        lead = await db().leads.find_one({"_id": lead_id, "owner_id": ws})
        if lead:
            note = (f"{lead.get('note', '')}\n" if lead.get("note") else "") + f"[AI call {now().strftime('%d %b')}] {summary}"
            upd = {"note": note[-1000:], "updated_at": now()}
            if lead.get("status") == "new":
                upd.update(status="contacted", first_action_at=lead.get("first_action_at") or now())
            await db().leads.update_one({"_id": lead_id}, {"$set": upd})
    owner = await db().users.find_one({"_id": ws})
    if owner and plans.has(owner, "training_gym") and len(transcript) >= 2:
        try:
            from .hub import profile_of
            p = await profile_of(ws)
            turns = [{"role": "customer" if t["role"] == "user" else "salesperson", "text": t["message"]} for t in transcript if t["message"]]
            score = await run_ai(owner, "call_review", chat_tasks.score_conversation(p, turns, "call"), "Call review", save_output=False,
                                 check=chat_tasks.score_shape)
            await db().calls.update_one({"_id": call["_id"]}, {"$set": {"score": score}})
        except Exception:  # noqa: BLE001  out of runs or the AI busy: the summary is still saved
            log.info("call review skipped for %s", call.get("_id"))
    if analysis.get("call_successful") == "success" and lead_id:
        await notify(ws, f"The AI Telecaller had a good call with {call.get('name') or 'a lead'}: {summary[:160]}")
    return {"ok": True}


STAGE_WORDS = {"identification": ("identif", "new"), "logic": ("logic", "reason", "qualif"), "pain": ("pain", "consequence", "attack"),
               "vision": ("vision", "solution", "proposal"), "close": ("close", "ask", "negotiat")}


@router.post("/employz/{token}")
async def employz_event(token: str, request: Request):
    """From an Employz.ai workflow's webhook: {opportunity_id | contact phone, stage (id or name), status}."""
    found = await connections.by_webhook("employz", token)
    if not found:
        raise HTTPException(404, "Not found")
    ws, conn = found
    try:
        p = await request.json()
    except ValueError:
        raise HTTPException(400, "Not JSON")
    if not isinstance(p, dict):
        raise HTTPException(400, "Not an object")
    opp = str(p.get("opportunity_id") or p.get("id") or (p.get("opportunity") or {}).get("id") or "")[:80]
    phone = str(p.get("phone") or (p.get("contact") or {}).get("phone") or "")
    lead = await db().leads.find_one({"owner_id": ws, "crm.opportunity_id": opp}) if opp else None
    if not lead and phone:
        lead = await db().leads.find_one({"owner_id": ws, "phone": "+" + integrations.digits(phone)}, sort=[("created_at", -1)])
    if not lead:
        return {"ok": True, "matched": False}
    stage_raw = str(p.get("pipeline_stage_id") or p.get("pipelineStageId") or p.get("stage") or p.get("pipleline_stage") or "").lower()
    upd: dict = {}
    for key in STAGE_WORDS:
        if stage_raw and (stage_raw == str(conn.get(f"stage_{key}") or "").lower() or any(w in stage_raw for w in STAGE_WORDS[key])):
            upd["stage"] = key
            break
    status = str(p.get("status") or "").lower()
    if status in ("won", "lost"):
        upd["status"] = status
        if status == "won" and p.get("monetary_value"):
            try:
                upd["value"] = float(p["monetary_value"])
            except (TypeError, ValueError):
                pass
    if upd:
        upd["updated_at"] = now()
        await db().leads.update_one({"_id": lead["_id"]}, {"$set": upd})
        if upd.get("status") and upd["status"] != lead.get("status"):   # the day it was won, for "Am I on target"
            await db().leads.update_one({"_id": lead["_id"]}, {"$set": {"won_at": now() if upd["status"] == "won" else None}})
        if upd.get("status") and upd["status"] != lead.get("status"):
            owner = await db().users.find_one({"_id": ws})
            from ..agents import hooks
            await hooks.on_lead_status(owner, {**lead, **upd}, upd["status"])
    return {"ok": True, "matched": True, "changed": sorted(upd)}


# ───────────────────────── Email delivery events (SES → SNS) ─────────────────────────
@router.post("/email-events/{secret}")
async def email_events(secret: str, request: Request):
    """Delivered / bounced / complained, from Amazon SES through SNS. The address carries EMAIL_EVENTS_SECRET and every
    message's SNS signature is checked against Amazon's certificate."""
    import hmac
    from .. import mailer
    from ..config import settings
    if not settings.email_events_secret or not hmac.compare_digest(secret, settings.email_events_secret):
        raise HTTPException(404, "Not found")
    try:
        msg = json.loads(await request.body() or b"{}")
    except ValueError:
        raise HTTPException(400, "Not JSON")
    if not isinstance(msg, dict) or not await mailer.sns_verified(msg):
        raise HTTPException(401, "Bad signature")
    if settings.email_events_topic_arn and msg.get("TopicArn") != settings.email_events_topic_arn:
        raise HTTPException(403, "Not our topic")   # a valid SNS message, but from someone else's AWS account
    if msg.get("Type") == "SubscriptionConfirmation":
        return {"ok": await mailer.sns_confirm(msg)}
    if msg.get("Type") != "Notification":
        return {"ok": True}
    try:
        event = json.loads(msg.get("Message") or "{}")
    except ValueError:
        raise HTTPException(400, "Not JSON")
    return {"ok": True, "recorded": await mailer.record_event(event if isinstance(event, dict) else {})}
