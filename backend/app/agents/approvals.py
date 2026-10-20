"""
Messages an automation (or an AI staff member) wants to send to a customer wait here — the Send list.

How a message leaves:
- "own":  the owner taps Send on WhatsApp — their own WhatsApp opens with the message typed (every plan; nothing to set up).
- "send": sent from the owner's own connected WhatsApp number (Growth Mentorship and up, once the number is connected):
          as a free-form message when the customer wrote in the last 24 hours, otherwise as the matching approved template.
- on its own: only LegacyWorkforce, only when the owner lets that department act, never for money messages
              (reminders, quotations, invoices) and never for text containing a link.
- v2.1 exception (decision D2): payment reminders the owner approved once ("Automatic payment follow-ups") go on their
  own from the owner's own number — only from Growth Mentorship (callers check `followups.whatsapp_auto`), and never
  with a link unless it is the owner's own payment link from their connected gateway.
The hub's own WhatsApp number never messages customers; it only alerts the owner.

approvals {_id, ws, agent, kind followup|review|reminder|office|customer|quote, title, to_name, to_phone, text, params,
           lead_id, due_id, status pending|sent|skipped|failed|expired, via own|owner-whatsapp, created_at, decided_at, decided_by}
"""
from datetime import timedelta
from urllib.parse import quote

from fastapi import HTTPException

from .. import connections, integrations, plans
from ..db import db, now
from ..services import new_id, track
from ..text import greeting_name
from .jobs import claim, get_settings, may_act, release

KINDS = ("followup", "review", "reminder", "office", "customer", "quote")
MONEY = {"reminder", "quote"}                 # money messages always wait for a person
CORE = {"_id", "ws", "agent", "kind", "title", "to_name", "to_phone", "text", "params", "status", "via", "created_at", "decided_at", "decided_by"}
TEMPLATE = {"followup": "tpl_followup", "office": "tpl_followup", "quote": "tpl_followup", "review": "tpl_review",
            "reminder": "tpl_reminder", "customer": "tpl_customer"}


def own_link(approval: dict) -> str:
    from ..routers.hub import wa_digits
    return f"https://wa.me/{wa_digits(approval.get('to_phone', ''))}?text={quote(approval.get('text', ''))}"


async def owner_whatsapp(ws: str, owner: dict | None = None) -> dict | None:
    """The owner's connected WhatsApp number, when their plan includes it and it is ready to send."""
    owner = owner or await db().users.find_one({"_id": ws}) or {}
    if not plans.has(owner, "whatsapp_ai"):
        return None
    conn = await connections.get(ws, "whatsapp")
    return conn if connections.ready("whatsapp", conn) else None


async def in_window(ws: str, phone: str) -> bool:
    """True when this customer messaged the owner's number in the last 24 hours (free-form replies allowed)."""
    t = await db().wa_threads.find_one({"ws": ws, "phone": integrations.digits(phone)}, {"last_in_at": 1})
    return bool(t and t.get("last_in_at") and t["last_in_at"] > now() - timedelta(hours=23, minutes=50))


async def deliver(approval: dict, owner: dict | None = None) -> dict:
    """Sends from the owner's own WhatsApp number. Never raises; {"sent": False, "error": ...} when it can't."""
    ws = approval["ws"]
    conn = await owner_whatsapp(ws, owner)
    if not conn:
        return {"sent": False, "via": "owner-whatsapp",
                "error": "Your WhatsApp number isn't connected to the hub. Tap Send on WhatsApp to send it from your phone."}
    to = approval.get("to_phone", "")
    if await in_window(ws, to):
        result = {**await integrations.wa_text(ws, conn, to, approval.get("text", "")), "mode": "text"}
    else:
        business = await db().businesses.find_one({"owner_id": ws}, {"name": 1}) or {}
        params = (list(approval.get("params") or []) if approval["kind"] in ("review", "reminder") else
                  [greeting_name(approval.get("to_name", "")) or "there", business.get("name") or "us", approval.get("text", "")])
        result = {**await integrations.wa_template(ws, conn, to, conn.get(TEMPLATE.get(approval["kind"], "tpl_followup"), ""), params,
                                                   approval.get("to_name", ""), approval["kind"]), "mode": "template"}
    if result.get("sent"):
        await record_out(ws, to, approval.get("to_name", ""), approval.get("text", ""), "automation", result.get("id"))
    return result


async def record_out(ws: str, phone: str, name: str, text: str, by: str, wa_id: str | None = None) -> None:
    """Keeps the chat history in WhatsApp chats for messages sent from the owner's number."""
    from ..chat_ai import add_message
    await add_message(ws, phone, name, "out", text, by, wa_id)


async def after_sent(approval: dict, at=None) -> None:
    """What sending changes elsewhere: a followed-up lead counts as contacted; a won customer has been asked for a review."""
    at = at or now()
    lead_id = approval.get("lead_id")
    if approval["kind"] == "quote" and approval.get("quote_id"):
        await db().quotes.update_one({"_id": approval["quote_id"]}, {"$set": {"last_followup_at": at}})   # the count is kept by the agent
    if not lead_id:
        return
    if approval["kind"] in ("followup", "office"):
        lead = await db().leads.find_one({"_id": lead_id, "owner_id": approval["ws"]}, {"status": 1, "first_action_at": 1})
        if lead:
            patch = {"last_followup_at": at, "updated_at": at}
            if lead.get("status") == "new":
                patch["status"] = "contacted"
            if not lead.get("first_action_at"):
                patch["first_action_at"] = at
            await db().leads.update_one({"_id": lead_id}, {"$set": patch})
    elif approval["kind"] == "review":
        await db().leads.update_one({"_id": lead_id, "owner_id": approval["ws"]}, {"$set": {"review_requested_at": at}})


PAYMENT_LINK_HOSTS = ("rzp.io", "razorpay.me", "pages.razorpay.com")


def link_hosts(text: str) -> list[str]:
    """Every web address in a message, as a bare host: full links (parsed properly — no tricks with ?, @ or ports) and
    bare domains like pay.example.in. Email addresses are not links."""
    import re as _re
    from urllib.parse import urlsplit
    text = text or ""
    hosts = []
    for url in _re.findall(r"(?i)\b(?:https?|ftp)://\S+", text):
        try:
            hosts.append((urlsplit(url).hostname or "").lower())
        except ValueError:
            hosts.append("?")
    # payment and app links that aren't web addresses (upi://pay…, tel:, intent:…) count as links too
    hosts += [m.lower() + ":" for m in _re.findall(r"(?i)\b(upi|intent|tel|sms|whatsapp|market|javascript|data|tez|phonepe|paytmmp)\s*:", text)]
    rest = _re.sub(r"(?i)\b(?:https?|ftp)://\S+", " ", text)
    rest = _re.sub(r"(?i)\b(?:upi|intent|tel|sms|whatsapp|market|javascript|data|tez|phonepe|paytmmp)\s*:\S*", " ", rest)
    rest = _re.sub(r"\S+@\S+", " ", rest)                       # email addresses
    hosts += [h.lower().rstrip(".") for h in _re.findall(r"(?i)(?<![\w-])((?:[a-z0-9-]+\.)+[a-z]{2,})(?![\w-])", rest)]
    return hosts


def only_payment_links(text: str) -> bool:
    """True when the text has no link, or only links to Razorpay payment pages."""
    return all(any(h == p or h.endswith("." + p) for p in PAYMENT_LINK_HOSTS) for h in link_hosts(text))


async def create_approval(owner: dict, ws: str, agent: str, kind: str, title: str, to_name: str, to_phone: str, text: str,
                          params: list | None = None, allow_act: bool = True, auto_send: bool = False, **refs) -> dict:
    """A message to a customer. Waits in the Send list, or — LegacyWorkforce, when the owner lets this department act,
    for a non-money message without a link — goes out at once from the owner's own number. `auto_send` (a payment
    reminder the owner approved once, Growth Mentorship and up — decided by the caller) also goes at once, unless it
    carries a link other than the owner's own payment link."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    doc = {"_id": new_id(), "ws": ws, "agent": agent, "kind": kind, "title": (title or "")[:160], "to_name": (to_name or "")[:80],
           "to_phone": (to_phone or "")[:24], "text": (text or "")[:900], "params": [str(p) for p in (params or [])],
           "status": "pending", "via": None, "created_at": now(), "decided_at": None, "decided_by": None,
           "lead_id": None, "due_id": None, **{k: v for k, v in refs.items() if k not in CORE}}
    if kind in ("review", "reminder"):  # these go out as a fixed template; keep its wording to show what was really sent
        doc["template_text"] = doc["text"]
    money_ok = auto_send and kind == "reminder" and only_payment_links(doc["text"]) and plans.rank(plans.effective_plan(owner)) >= plans.rank("growth")
    if money_ok or (allow_act and kind not in MONEY and may_act(owner, await get_settings(ws, agent))):
        result = await deliver(doc, owner)
        if result["sent"]:
            doc.update(status="sent", via="owner-whatsapp", decided_at=now(), decided_by="auto-approved" if money_ok else "agent")
        else:
            doc["send_error"] = result.get("error") or "Couldn't send this on its own."
    await db().approvals.insert_one(doc)
    if doc["status"] == "sent":
        await after_sent(doc)
        await track("agent_sent", ws, agent=agent, kind=kind)
    return doc


async def expire(query: dict, note: str = "") -> int:
    r = await db().approvals.update_many({**query, "status": "pending"},
                                         {"$set": {"status": "expired", "decided_at": now(), "decided_by": "agent", "note": note}})
    return r.modified_count


async def decide(ws: str, approval_id: str, actor: str, action: str) -> dict:
    """send | own | skip — exactly once per approval, even with a double click or two managers at once."""
    approval = await db().approvals.find_one({"_id": approval_id, "ws": ws})
    if not approval:
        raise HTTPException(404, "Not found")
    if approval["status"] != "pending":
        raise HTTPException(409, "This message was already handled.")
    key = f"approval:{approval_id}"
    if not await claim(key):
        raise HTTPException(409, "This message was already handled.")
    out: dict = {}
    if action == "send":
        result = await deliver(approval)
        if not result["sent"]:
            await release(key)
            raise HTTPException(400 if "isn't connected" in (result.get("error") or "") else 502,
                                result.get("error") or "Couldn't send right now. Try again, or tap Send on WhatsApp.")
        patch = {"status": "sent", "via": "owner-whatsapp"}
        if result.get("mode") == "template" and approval.get("template_text") and approval.get("text") != approval["template_text"]:
            patch.update(text=approval["template_text"], edited_text=approval.get("text"))  # a fixed template went out, not the edit
    elif action == "own":
        out["link"] = own_link(approval)
        patch = {"status": "sent", "via": "own"}
    else:
        patch = {"status": "skipped"}
    patch.update(decided_at=now(), decided_by=actor, send_error=None)
    await db().approvals.update_one({"_id": approval_id}, {"$set": patch})
    approval.update(patch)
    if patch["status"] == "sent":
        await after_sent(approval)
        await track("approval_sent", ws, agent=approval["agent"], via=patch["via"])
    return {**out, "approval": approval}
