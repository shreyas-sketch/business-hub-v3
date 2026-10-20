"""
WhatsApp chats on the owner's own number (Growth Mentorship and up).

Every message in or out is kept per customer (wa_threads / wa_messages), so the owner and team see one shared inbox.
When a customer writes, the Sales Executive (sales questions) or the Customer Care Executive (questions after buying)
replies within seconds, 24×7, from the Business Brain and the Company Brain — never inventing a price or a promise.
When the customer is ready to buy, asks for something only a person can decide, or is unhappy, the AI hands over:
the chat is marked for a person, the owner gets an alert from the hub's number, and the AI stops replying in that chat
until someone switches it back on. A chat gets at most AI_REPLIES_PER_CHAT_PER_DAY AI replies a day.
"""
import asyncio
import logging
from datetime import timedelta

from . import integrations, plans
from .ai import chat_tasks
from .config import settings
from .db import IST, db, now
from .messaging import whatsapp_template
from .services import new_id, notify, run_ai, track

log = logging.getLogger("chats")


async def add_message(ws: str, phone: str, name: str, direction: str, text: str, by: str, wa_id: str | None = None) -> dict:
    p = integrations.digits(phone)
    at = now()
    t = await db().wa_threads.find_one({"ws": ws, "phone": p})
    if not t:
        t = {"_id": new_id(), "ws": ws, "phone": p, "name": name or "", "ai_on": True, "needs_person": False, "created_at": at,
             "last_at": at, "unread": 0, "intent": ""}
        try:
            await db().wa_threads.insert_one(t)
        except Exception:  # noqa: BLE001  created by a parallel message a moment ago
            t = await db().wa_threads.find_one({"ws": ws, "phone": p})
    patch = {"last_at": at, "last_text": text[:200], "last_dir": direction}
    if direction == "in":
        patch["last_in_at"] = at
        if name and not t.get("name"):
            patch["name"] = name[:80]
    else:
        patch["last_out_at"] = at
    inc = {"unread": 1} if direction == "in" else {}
    await db().wa_threads.update_one({"_id": t["_id"]}, {"$set": patch, **({"$inc": inc} if inc else {})})
    msg = {"_id": new_id(), "ws": ws, "thread_id": t["_id"], "dir": direction, "text": text[:4000], "by": by, "wa_id": wa_id, "at": at}
    await db().wa_messages.insert_one(msg)
    return {**t, **patch}


async def _lead_for(ws: str, thread: dict) -> dict | None:
    return await db().leads.find_one({"owner_id": ws, "phone": "+" + thread["phone"]}, sort=[("created_at", -1)])


async def inbound(ws: str, conn: dict, msg: dict) -> dict:
    """A customer's WhatsApp message arrived on the owner's number."""
    if msg.get("id") and await db().wa_messages.find_one({"ws": ws, "wa_id": msg["id"], "dir": "in"}, {"_id": 1}):
        return {"duplicate": True}
    thread = await add_message(ws, msg["from"], msg.get("name", ""), "in", msg.get("text", ""), "customer", msg.get("id"))
    await track("wa_in", ws)
    owner = await db().users.find_one({"_id": ws})
    if not owner or owner.get("disabled") or not plans.has(owner, "whatsapp_ai"):
        return {"replied": False, "why": "plan"}
    if settings.env == "test":
        return await reply(owner, conn, thread["_id"])
    asyncio.create_task(_safe_reply(owner, conn, thread["_id"]))
    return {"queued": True}


async def _safe_reply(owner, conn, thread_id):
    try:
        await reply(owner, conn, thread_id)
    except Exception:  # noqa: BLE001
        log.exception("AI chat reply failed")


async def reply(owner: dict, conn: dict, thread_id: str) -> dict:
    from .agents.jobs import get_settings, record
    ws = owner["_id"]
    t = await db().wa_threads.find_one({"_id": thread_id})
    if not t or not t.get("ai_on", True) or t.get("needs_person"):
        return {"replied": False, "why": "person"}
    if not (await get_settings(ws, "wa_sales")).get("on", True):
        return {"replied": False, "why": "off"}
    day_start = now().astimezone(IST).replace(hour=0, minute=0, second=0, microsecond=0)
    if await db().wa_messages.count_documents({"thread_id": thread_id, "dir": "out", "by": "ai", "at": {"$gte": day_start}}) >= settings.ai_replies_per_chat_per_day:
        return {"replied": False, "why": "cap"}
    b = await db().businesses.find_one({"owner_id": ws}) or {}
    if not b.get("name"):
        return {"replied": False, "why": "profile"}
    history = [m async for m in db().wa_messages.find({"thread_id": thread_id}).sort("at", -1).limit(12)][::-1]
    last_in = next((m["text"] for m in reversed(history) if m["dir"] == "in"), "")
    context = ""
    if plans.has(owner, "company_brain"):
        from .company_brain import context as brain
        context = await brain(ws, last_in)
    lead = await _lead_for(ws, t)
    result = await run_ai(owner, "wa_reply", chat_tasks.reply(b, history, context, lead), f"WhatsApp reply to {t.get('name') or t['phone']}",
                          save_output=False, check=chat_tasks.shape)
    text = result["reply"]
    who = "Customer Care Executive" if result["intent"] == "support" else "Sales Executive"
    sent = await integrations.wa_text(ws, conn, t["phone"], text)
    if not sent.get("sent"):
        await record(ws, "wa_sales", "failed", f"Couldn't reply to {t.get('name') or t['phone']}: {sent.get('error')}", ref=thread_id)
        return {"replied": False, "why": "send", "error": sent.get("error")}
    await add_message(ws, t["phone"], "", "out", text, "ai", sent.get("id"))
    patch = {"intent": result["intent"], "summary": result.get("summary", "")[:300]}
    if result["handover"]:
        patch.update(needs_person=True, handover_reason=result.get("why", "")[:200])
    await db().wa_threads.update_one({"_id": thread_id}, {"$set": patch})
    await record(ws, "wa_sales", "done", f"{who} replied to {t.get('name') or '+' + t['phone']} on WhatsApp.", ref=thread_id)
    if result["handover"] or result["hot"]:
        await _hand_over(owner, b, t, result, lead)
    return {"replied": True, "text": text, **patch}


async def _hand_over(owner: dict, b: dict, t: dict, result: dict, lead: dict | None) -> None:
    ws = owner["_id"]
    who = t.get("name") or "+" + t["phone"]
    if not lead and result["intent"] != "support":
        lead = {"_id": new_id(), "owner_id": ws, "site_id": None, "name": t.get("name") or "WhatsApp customer", "phone": "+" + t["phone"],
                "message": result.get("summary", "")[:500], "status": "new", "stage": "logic", "source": "whatsapp", "created_at": now()}
        await db().leads.insert_one(lead)
        from . import dashboard_cache
        dashboard_cache.bust(ws)
        await track("lead", ws, source="whatsapp")
        await db().wa_threads.update_one({"_id": t["_id"]}, {"$set": {"lead_id": lead["_id"]}})
    text = f"{who} on WhatsApp: {result.get('why') or result.get('summary') or 'needs you'}"
    await notify(ws, f"Needs you — {text}")
    await whatsapp_template(owner["phone"], settings.aisensy_owner_alert_campaign, owner.get("name", ""),
                            [b.get("name") or "your business", text[:300]], "owner_alert")


async def send_reply(ws: str, actor: str, thread_id: str, text: str) -> dict:
    """The owner or a team member replies from the Chats page (within the 24-hour window)."""
    from fastapi import HTTPException
    from .agents.approvals import owner_whatsapp
    t = await db().wa_threads.find_one({"_id": thread_id, "ws": ws})
    if not t:
        raise HTTPException(404, "Chat not found")
    conn = await owner_whatsapp(ws)
    if not conn:
        raise HTTPException(400, "Your WhatsApp number isn't connected yet.")
    if not t.get("last_in_at") or t["last_in_at"] < now() - timedelta(hours=23, minutes=55):
        raise HTTPException(400, "It's been more than 24 hours since this customer wrote. WhatsApp only allows an approved "
                                 "template now — send a follow-up from the Send list, or call them.")
    sent = await integrations.wa_text(ws, conn, t["phone"], text)
    if not sent.get("sent"):
        raise HTTPException(502, sent.get("error") or "WhatsApp didn't accept the message.")
    await add_message(ws, t["phone"], "", "out", text, actor, sent.get("id"))
    await db().wa_threads.update_one({"_id": thread_id}, {"$set": {"unread": 0}})
    return {"ok": True}


async def ensure_indexes() -> None:
    await db().wa_threads.create_index([("ws", 1), ("phone", 1)], unique=True)
    await db().wa_threads.create_index([("ws", 1), ("last_at", -1)])
    await db().wa_messages.create_index([("thread_id", 1), ("at", 1)])
    await db().wa_messages.create_index([("ws", 1), ("wa_id", 1)])
