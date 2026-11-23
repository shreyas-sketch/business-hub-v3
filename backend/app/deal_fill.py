"""
Pipeline fills itself (v2.4, feature "deal_autofill", AI role "pipeline_keeper"): a conversation becomes the deal's note, next
step and follow-up date, and a suggested stage and value to accept with one tap. The pages are in routers/dealfill.py.

Where conversations come from: a pasted chat or email thread, a WhatsApp "Export chat" file (.txt or .zip), a voice note after a
call, a scored call from Call coach (source "call"), an email from a known contact to the owner's inquiry address (inbound), and
— for members who connected their WhatsApp number — a chat with a known contact that has gone quiet (once a day per chat).

One AI job (1 token) reads it (ai/dealfill_tasks.py). The code then keeps only what it can find in the conversation: a value
only if that amount was said, a follow-up date only if that day was said, a stage only from this business's own stages.

    Applied by itself                         Waits for one tap ("Suggested updates": Apply / Dismiss)
    the summary as a note (by OwnerDesk)      a stage change, the value, won or lost, a new contact and deal
    the next step and the follow-up date
Setting "Move the stage forward by itself" (per business, off by default): a stage further along is applied by itself — never
won or lost, never the value. Everything OwnerDesk changed can be undone; the values from before are kept in `undo`, and an undo
never overwrites something a person changed since.

deal_updates       {_id, ws, lead_id|None, contact_id|None, source paste|upload|voice|call|email|whatsapp, call_id|None, summary,
                    needs, next_step, follow_up_on "YYYY-MM-DD"|None, applied {note, note_id, follow_up_on, follow_up_note, pstage?,
                    value?, status?, lost_reason?, new_deal_id?}, suggested {pstage?, value?, status?, lost_reason?, new_deal?},
                    state pending|applied|dismissed|done, undo {activity_ids, follow_up_on, follow_up_note, pstage, value, status,
                    lost_reason, new_deal}, undone_at|None, at, applied_at|None, by, by_name, written_by ai|rules, tokens 0|1}
deal_fill_settings {_id: ws, auto_forward, updated_at, by}
deal_fill_chats    {_id: wa thread id, ws, filed_upto (the newest customer message filed), filed_day "YYYY-MM-DD", update_id, at}
deal_fill_usage    {_id: "<ws>:<kind>:<day>", n} — voice notes a day
The conversation text itself is not stored here: it goes to the AI call only.
"""
import logging
from datetime import date, datetime, timedelta

from fastapi import HTTPException

from . import crm, dashboard_cache, growth_core, plans
from .agents import catalog
from .agents.catalog import Skip
from .ai import crm_tasks, dealfill_tasks as tasks
from .context import Ctx
from .db import IST, db, now
from .services import new_id, run_ai, runs_status

log = logging.getLogger("deal_fill")

ROLE = "pipeline_keeper"
BY, BY_NAME = "ownerdesk", "OwnerDesk"
SOURCE_LABEL = {"paste": "Pasted conversation", "upload": "WhatsApp export", "voice": "Voice note", "call": "Call coach",
                "email": "Email", "whatsapp": "WhatsApp chat"}
MAX_TEXT = 60_000                 # what is read of one conversation (the newest part is kept)
EMAIL_AI_PER_DAY = 30             # emails read by the AI per business a day; after that the rules version (no token)
CHATS_PER_RUN = 25                # WhatsApp chats filed per business per day
QUIET_MINUTES = 30
CHAT_LOOKBACK_DAYS = 7
VOICE_PER_DAY = 30
FILE_AT = "19:30"                 # India time, once a day (see the agent below)


def today() -> date:
    return crm.today_ist()


def _day_start() -> datetime:
    return now().astimezone(IST).replace(hour=0, minute=0, second=0, microsecond=0)


def ownerdesk_ctx(owner: dict, user: dict | None = None, name: str | None = None) -> Ctx:
    """A Ctx for the deal changes OwnerDesk makes (history lines say "OwnerDesk"), or a person's change from its suggestion."""
    if user:
        return Ctx({**user, "name": name or user.get("name") or "Owner"}, owner, "owner" if user["_id"] == owner["_id"] else user.get("team_role", "staff"))
    return Ctx({"_id": BY, "name": BY_NAME}, owner, "owner")


def _person(ctx: Ctx) -> str:
    return ctx.user.get("name") or ("Owner" if ctx.role == "owner" else "Team")


# ───────────────────────── settings ─────────────────────────
async def settings_of(ws: str) -> dict:
    doc = await db().deal_fill_settings.find_one({"_id": ws}) or {}
    return {"auto_forward": bool(doc.get("auto_forward"))}


async def save_settings(ws: str, auto_forward: bool, by: str) -> dict:
    await db().deal_fill_settings.update_one({"_id": ws}, {"$set": {"auto_forward": bool(auto_forward), "updated_at": now(), "by": by}}, upsert=True)
    return await settings_of(ws)


async def take_voice(ws: str) -> None:
    key = f"{ws}:voice:{today().isoformat()}"
    await db().deal_fill_usage.update_one({"_id": key}, {"$setOnInsert": {"n": 0, "at": now()}}, upsert=True)
    if not await db().deal_fill_usage.find_one_and_update({"_id": key, "n": {"$lt": VOICE_PER_DAY}}, {"$inc": {"n": 1}}):
        raise HTTPException(429, f"You've added {VOICE_PER_DAY} voice notes today. More open tomorrow — or paste the conversation as text.")


# ───────────────────────── reading the conversation ─────────────────────────
async def _pipeline(ws: str) -> tuple[list[dict], list[str]]:
    stages = await crm.stages_of(ws)
    p = await crm.pipeline_of(ws)
    return stages, (p or {}).get("lost_reasons") or crm_tasks.BASE_REASONS["services"]


def guard(r: dict, messages: list[dict], stages: list[dict], current: str | None, lead: dict | None, on: date, reasons: list[str]) -> dict:
    """Keeps only what the conversation itself says. A value only when that amount was said; a follow-up date only when that
    day was said and isn't past; a stage from this business's stages, further than where the deal is; won or lost only on an
    open deal."""
    days = {d.isoformat() for d in tasks.said_dates(messages, on)}
    follow = r.get("follow_up_on") if r.get("follow_up_on") in days else None
    value = tasks.said_amount(r.get("value"), tasks.said_value_list(messages)) if r.get("value") not in (None, "", 0) else None
    open_ = lead is None or lead.get("status", "new") in crm.OPEN
    ids = [s["id"] for s in stages]
    pstage = r.get("pstage") if r.get("pstage") in ids and r.get("pstage") != current and open_ else None
    status = r.get("status") if r.get("status") in ("won", "lost") and lead is not None and open_ else None
    reason = None
    if status == "lost":
        pstage = None
        raw = str(r.get("lost_reason") or "").strip()
        reason = next((x for x in reasons if x.lower() == raw.lower()), raw[:80] or None)
    text_all = "\n".join(f"{m.get('who', '')} {m.get('text', '')}" for m in messages).lower()
    name = r.get("customer_name")
    name = name if name and str(name).strip().lower() in text_all else None
    summary = [x for x in (r.get("summary") or []) if x][:3]
    return {"summary": summary, "needs": r.get("needs") or "", "next_step": r.get("next_step") or "", "follow_up_on": follow,
            "value": value, "pstage": pstage, "status": status, "lost_reason": reason, "customer_name": name}


async def read(owner: dict, messages: list[dict], lead: dict | None, rules_when_no_tokens: bool = False,
               use_ai: bool = True) -> tuple[dict, str, int]:
    """(what the conversation says, "ai"|"rules", tokens used). 1 AI token; the AI's answer is checked by `guard`. When the AI's
    answer can't be used, the rules version (no token). With no tokens left: 402, or the rules version when asked."""
    ws = owner["_id"]
    stages, reasons = await _pipeline(ws)
    current = crm.pstage_of(lead, stages) if lead else None
    on = today()
    fallback = tasks.rules_read(messages, stages, current, reasons, on, (lead or {}).get("name", ""))
    business = await db().businesses.find_one({"owner_id": ws}) or {}
    result, how, tokens = fallback, "rules", 0
    if use_ai:
        try:
            result = await run_ai(owner, "deal_fill", tasks.read_conversation(business, lead, stages, current, reasons, tasks.as_text(messages),
                                                                              on, fallback),
                                  f"Conversation — {(lead or {}).get('name') or 'new customer'}", save_output=False, check=tasks.shape)
            how, tokens = "ai", 1
        except HTTPException as e:
            if e.status_code == 402 and not rules_when_no_tokens:
                raise
            if e.status_code not in (402, 502):
                raise
            log.info("conversation read by the rules version: %s", getattr(e, "detail", e))
    out = guard(result, messages, stages, current, lead, on, reasons)
    if not out["summary"]:
        out["summary"] = fallback["summary"] or ["A conversation was added."]
    return out, how, tokens


def _note(source: str, r: dict) -> str:
    lines = [f"{SOURCE_LABEL.get(source, 'Conversation')} — summary by OwnerDesk:"] + [f"• {x}" for x in r["summary"]]
    if r.get("needs"):
        lines.append(f"Needs: {r['needs']}")
    if r.get("next_step"):
        lines.append(f"Next step: {r['next_step']}")
    return "\n".join(lines)[:2000]


async def _contact_id(ws: str, lead: dict) -> str | None:
    """The deal's contact (made from the deal when it has none, as a note added by hand does)."""
    if lead.get("contact_id"):
        return lead["contact_id"]
    c = await crm.upsert_contact(ws, lead.get("name", ""), lead.get("phone"), lead.get("email"), lead.get("company", ""), lead.get("source", ""))
    if c:
        await db().leads.update_one({"_id": lead["_id"]}, {"$set": {"contact_id": c["_id"]}})
        lead["contact_id"] = c["_id"]
        return c["_id"]
    return None


async def _apply_follow_up(ws: str, lead: dict, cid: str | None, follow: str, step: str, doc: dict) -> None:
    note = (step or lead.get("follow_up_note") or "")[:300]
    doc["undo"].update(follow_up_on=lead.get("follow_up_on"), follow_up_note=lead.get("follow_up_note", ""))
    await db().leads.update_one({"_id": lead["_id"]}, {"$set": {"follow_up_on": follow, "follow_up_note": note, "updated_at": now(), "updated_by": BY}})
    await crm.set_reminder(ws, "deal", lead["_id"], follow)
    a = await crm.add_activity(ws, cid, lead["_id"], "followup", f"Follow-up set for {crm.day_text(follow)}" + (f": {note}" if note else ""), BY, BY_NAME)
    doc["undo"]["activity_ids"].append(a["_id"])
    doc["applied"].update(follow_up_on=follow, follow_up_note=note)


async def file(owner: dict, lead: dict | None, messages: list[dict], source: str, by: str, by_name: str = "", call_id: str | None = None,
               rules_when_no_tokens: bool = False, use_ai: bool = True, new_contact: dict | None = None) -> dict:
    """Reads one conversation and files it: into the deal (note and follow-up applied by themselves, the rest suggested), or —
    with no deal — as a suggestion to add a new contact and deal. Returns the saved deal_updates record."""
    from .routers.crm import _apply_deal_patch
    ws = owner["_id"]
    if not messages:
        raise HTTPException(400, "There's nothing to read in that conversation.")
    async with growth_core.working(ws, ROLE):
        r, how, tokens = await read(owner, messages, lead, rules_when_no_tokens, use_ai)
        doc = {"_id": new_id(), "ws": ws, "lead_id": lead["_id"] if lead else None, "contact_id": (lead or {}).get("contact_id"), "source": source,
               "call_id": call_id, "summary": "\n".join(r["summary"]), "needs": r["needs"], "next_step": r["next_step"],
               "follow_up_on": r["follow_up_on"], "applied": {"note": False, "follow_up_on": None}, "suggested": {}, "state": "done",
               "undo": {"activity_ids": []}, "undone_at": None, "at": now(), "applied_at": None, "by": by, "by_name": by_name[:80],
               "written_by": how, "tokens": tokens, "messages": len(messages)}
        if lead:
            cid = await _contact_id(ws, lead)
            doc["contact_id"] = cid
            a = await crm.add_activity(ws, cid, lead["_id"], "note", _note(source, r), BY, BY_NAME)
            doc["undo"]["activity_ids"].append(a["_id"])
            doc["applied"].update(note=True, note_id=a["_id"])
            open_ = lead.get("status", "new") in crm.OPEN
            if open_ and r["follow_up_on"] and r["follow_up_on"] != lead.get("follow_up_on"):
                await _apply_follow_up(ws, lead, cid, r["follow_up_on"], r["next_step"], doc)
            stages, _ = await _pipeline(ws)
            current = crm.pstage_of(lead, stages)
            ids = [s["id"] for s in stages]
            if r["pstage"]:
                forward = current in ids and ids.index(r["pstage"]) > ids.index(current)
                closing = next((s["ff"] for s in stages if s["id"] == r["pstage"]), "") == "close"   # "Order confirmed" waits, like Won
                if forward and not closing and (await settings_of(ws))["auto_forward"]:
                    fresh = await db().leads.find_one({"_id": lead["_id"]})
                    await _apply_deal_patch(ownerdesk_ctx(owner), fresh, {"pstage": r["pstage"]})
                    doc["undo"]["pstage"] = current
                    doc["applied"]["pstage"] = r["pstage"]
                else:
                    doc["suggested"]["pstage"] = r["pstage"]
            if r["value"] is not None and float(r["value"]) != float(lead.get("value") or 0):
                doc["suggested"]["value"] = r["value"]
            if r["status"]:
                doc["suggested"]["status"] = r["status"]
                if r["status"] == "lost":
                    doc["suggested"]["lost_reason"] = r["lost_reason"]
        else:
            nc = new_contact or {}
            stages, _ = await _pipeline(ws)
            doc["suggested"]["new_deal"] = {
                "name": (nc.get("name") or r["customer_name"] or "")[:80], "phone": nc.get("phone") or "", "email": nc.get("email") or "",
                "company": (nc.get("company") or "")[:120], "pstage": r["pstage"] or stages[0]["id"], "value": r["value"],
                "follow_up_on": r["follow_up_on"], "source": "whatsapp" if any(m.get("who") for m in messages) else "other"}
        if doc["suggested"]:
            doc["state"] = "pending"
        await db().deal_updates.insert_one(doc)
    name = (lead or {}).get("name") or doc["suggested"].get("new_deal", {}).get("name") or "a new customer"
    did = [w for w, ok in (("a note", doc["applied"]["note"]), (f"a follow-up on {crm.day_text(doc['applied']['follow_up_on'])}", doc["applied"]["follow_up_on"]),
                           ("the stage", doc["applied"].get("pstage"))) if ok]
    waiting = len(doc["suggested"])
    text = (f"Filed a {SOURCE_LABEL.get(source, 'conversation').lower()} into {name}'s deal: " + (", ".join(did) or "nothing to change")
            + (f"; {waiting} update{'s' if waiting != 1 else ''} wait for you" if waiting else "")) if lead else \
        f"Read a {SOURCE_LABEL.get(source, 'conversation').lower()} with {name}: a new contact and deal wait for you"
    await growth_core.log_action(ws, ROLE, text, ref=f"lead:{lead['_id']}" if lead else f"update:{doc['_id']}")
    dashboard_cache.bust(ws)
    return doc


# ───────────────────────── the page's view ─────────────────────────
def _money(v) -> str:
    from .routers.desk import rupees
    return rupees(float(v))


def suggestion_lines(u: dict, stage_names: dict) -> list[dict]:
    s = u.get("suggested") or {}
    out = []
    if s.get("pstage"):
        out.append({"key": "pstage", "label": f"Move to {stage_names.get(s['pstage'], s['pstage'])}"})
    if s.get("value") is not None:
        out.append({"key": "value", "label": f"Set the value to {_money(s['value'])}"})
    if s.get("status") == "won":
        out.append({"key": "status", "label": "Mark won"})
    elif s.get("status") == "lost":
        out.append({"key": "status", "label": "Mark lost" + (f" — {s['lost_reason']}" if s.get("lost_reason") else "")})
    if s.get("new_deal"):
        nd = s["new_deal"]
        bits = [x for x in (nd.get("phone"), nd.get("company"), stage_names.get(nd.get("pstage"), ""),
                            _money(nd["value"]) if nd.get("value") else "", f"follow up {crm.day_text(nd['follow_up_on'])}" if nd.get("follow_up_on") else "") if x]
        out.append({"key": "new_deal", "label": f"Add {nd.get('name') or 'this customer'} as a new opportunity" + (f" ({', '.join(bits)})" if bits else ""),
                    "needs_name": not nd.get("name")})
    return out


def view(u: dict, stage_names: dict) -> dict:
    a = u.get("applied") or {}
    changed = bool(a.get("note") or a.get("follow_up_on") or a.get("pstage") or "value" in a or a.get("status") or a.get("new_deal_id"))
    return {"id": u["_id"], "lead_id": u.get("lead_id"), "contact_id": u.get("contact_id"), "source": u.get("source"),
            "source_label": SOURCE_LABEL.get(u.get("source"), "Conversation"), "call_id": u.get("call_id"),
            "summary": [x for x in (u.get("summary") or "").split("\n") if x], "needs": u.get("needs", ""), "next_step": u.get("next_step", ""),
            "follow_up_on": u.get("follow_up_on"), "applied": {k: v for k, v in a.items() if k != "note_id"},
            "applied_follow_up": crm.day_text(a["follow_up_on"]) if a.get("follow_up_on") else None,
            "applied_stage": stage_names.get(a.get("pstage")) if a.get("pstage") else None,
            "suggested": suggestion_lines(u, stage_names), "state": u.get("state"), "at": crm.iso(u.get("at")),
            "applied_at": crm.iso(u.get("applied_at")), "undone_at": crm.iso(u.get("undone_at")),
            "can_undo": changed and not u.get("undone_at"), "activity_ids": (u.get("undo") or {}).get("activity_ids") or [],
            "by_name": u.get("by_name") or "", "written_by": u.get("written_by", "rules"), "tokens": int(u.get("tokens") or 0),
            "by_ownerdesk": True}


async def stage_names(ws: str) -> dict:
    return {s["id"]: s["name"] for s in await crm.stages_of(ws)}


async def _update(ws: str, uid: str) -> dict:
    u = await db().deal_updates.find_one({"_id": uid, "ws": ws})
    if not u:
        raise HTTPException(404, "Not found")
    return u


# ───────────────────────── Apply, Dismiss, Undo ─────────────────────────
async def apply(ctx: Ctx, uid: str, fields: list[str] | None, name: str | None = None, phone: str | None = None) -> dict:
    from .routers import crm as crm_router
    u = await _update(ctx.ws, uid)
    if u.get("state") != "pending" or u.get("undone_at"):
        raise HTTPException(409, "This was already applied or dismissed.")
    s = u.get("suggested") or {}
    keys = [k for k in ("pstage", "value", "status", "new_deal") if k in s]
    chosen = [k for k in keys if fields is None or k in fields]
    if not chosen:
        raise HTTPException(400, "Pick at least one update to apply.")
    claimed = await db().deal_updates.find_one_and_update({"_id": uid, "state": "pending"}, {"$set": {"state": "applying"}})
    if not claimed:
        raise HTTPException(409, "This was already applied or dismissed.")
    applied, undo = dict(u.get("applied") or {}), dict(u.get("undo") or {"activity_ids": []})
    lead_id = u.get("lead_id")
    person = ownerdesk_ctx(ctx.owner, ctx.user, f"{_person(ctx)}, from OwnerDesk's suggestion")
    try:
        if "new_deal" in chosen:
            nd = {**s["new_deal"], **({"name": name.strip()} if name and name.strip() else {}),
                  **({"phone": phone.strip()} if phone and phone.strip() else {})}
            if len((nd.get("name") or "").strip()) < 2:
                raise HTTPException(400, "Add the customer's name first.")
            body = crm_router.DealIn(name=nd["name"].strip(), phone=nd.get("phone") or "", email=nd.get("email") or "", company=nd.get("company") or "",
                                     value=nd.get("value"), pstage=nd.get("pstage"), source=nd.get("source") or "other",
                                     follow_up_on=nd.get("follow_up_on") or "", note="")
            existed = await crm.find_contact(ctx.ws, crm.norm_phone(body.phone), crm.norm_email(body.email))
            deal = await crm_router.add_deal(body, person)
            lead = await db().leads.find_one({"_id": deal["id"]})
            lead_id = lead["_id"]
            a = await crm.add_activity(ctx.ws, lead.get("contact_id"), lead_id, "note", _note(u["source"], {"summary": (u.get("summary") or "").split("\n"),
                                                                                                         "needs": u.get("needs"), "next_step": u.get("next_step")}),
                                       BY, BY_NAME)
            undo.setdefault("activity_ids", []).append(a["_id"])
            if lead.get("follow_up_on") and u.get("next_step"):
                await db().leads.update_one({"_id": lead_id}, {"$set": {"follow_up_note": u["next_step"][:300]}})
            applied.update(note=True, note_id=a["_id"], new_deal_id=lead_id)
            undo["new_deal"] = {"lead_id": lead_id, "contact_id": lead.get("contact_id"), "contact_created": not existed}
        else:
            lead = await db().leads.find_one({"_id": lead_id, "owner_id": ctx.ws})
            if not lead:
                raise HTTPException(404, "That deal isn't there any more.")
            stages = await crm.stages_of(ctx.ws)
            open_ = lead.get("status", "new") in crm.OPEN
            patch: dict = {}
            if "pstage" in chosen and open_ and any(x["id"] == s["pstage"] for x in stages):
                undo["pstage"] = crm.pstage_of(lead, stages)
                patch["pstage"] = s["pstage"]
            if "value" in chosen:
                undo["value"] = lead.get("value")
                patch["value"] = s["value"]
            if "status" in chosen and open_:
                undo.update(status=lead.get("status", "new"), lost_reason=lead.get("lost_reason", ""))
                patch["status"] = s["status"]
                if s["status"] == "lost":
                    patch["lost_reason"] = s.get("lost_reason") or ""
            if not patch:
                raise HTTPException(409, "This deal has changed since — nothing was applied.")
            await crm_router._apply_deal_patch(person, lead, dict(patch))
            applied.update(patch)
    except BaseException:
        await db().deal_updates.update_one({"_id": uid, "state": "applying"}, {"$set": {"state": "pending"}})
        raise
    await db().deal_updates.update_one({"_id": uid}, {"$set": {"state": "applied", "applied": applied, "undo": undo, "applied_at": now(),
                                                               "applied_by": ctx.actor, "lead_id": lead_id,
                                                               "contact_id": u.get("contact_id") or (await db().leads.find_one({"_id": lead_id}, {"contact_id": 1}) or {}).get("contact_id")}})
    names = await stage_names(ctx.ws)
    lines = [x["label"] for x in suggestion_lines(u, names) if x["key"] in chosen]
    who = (await db().leads.find_one({"_id": lead_id}, {"name": 1}) or {}).get("name") or "a deal"
    await growth_core.log_action(ctx.ws, ROLE, f"{_person(ctx)} applied OwnerDesk's suggestion on {who}: " + "; ".join(lines).lower(), ref=f"lead:{lead_id}")
    return view(await _update(ctx.ws, uid), names)


async def dismiss(ctx: Ctx, uid: str) -> dict:
    u = await _update(ctx.ws, uid)
    r = await db().deal_updates.update_one({"_id": uid, "state": "pending"}, {"$set": {"state": "dismissed", "dismissed_at": now(), "dismissed_by": ctx.actor}})
    if not r.modified_count:
        raise HTTPException(409, "This was already applied or dismissed.")
    return view({**u, "state": "dismissed"}, await stage_names(ctx.ws))


async def undo(ctx: Ctx, uid: str) -> dict:
    """Takes back what OwnerDesk did for this conversation (its note and follow-up lines, the follow-up date, a stage it moved, a
    suggestion that was applied). A field a person changed since is left as it is, and named in `kept`."""
    from .routers import crm as crm_router
    u = await _update(ctx.ws, uid)
    if u.get("undone_at"):
        raise HTTPException(409, "This was already undone.")
    if not await db().deal_updates.find_one_and_update({"_id": uid, "undone_at": None, "state": {"$ne": "applying"}},
                                                       {"$set": {"undone_at": now(), "undone_by": ctx.actor}}):
        raise HTTPException(409, "This was already undone.")
    a, un = u.get("applied") or {}, u.get("undo") or {}
    kept: list[str] = []
    if un.get("activity_ids"):
        await db().crm_activities.delete_many({"ws": ctx.ws, "_id": {"$in": un["activity_ids"]}})
    lead = await db().leads.find_one({"_id": u.get("lead_id"), "owner_id": ctx.ws}) if u.get("lead_id") else None
    nd = un.get("new_deal")
    if nd and lead and lead["_id"] == nd.get("lead_id"):
        if await db().quotes.find_one({"ws": ctx.ws, "lead_id": lead["_id"]}, {"_id": 1}):
            kept.append("the new opportunity (it has a quotation)")
        else:
            await crm.set_reminder(ctx.ws, "deal", lead["_id"], None)
            await db().crm_activities.delete_many({"ws": ctx.ws, "lead_id": lead["_id"]})
            await db().leads.delete_one({"_id": lead["_id"], "owner_id": ctx.ws})
            cid = nd.get("contact_id")
            if cid and nd.get("contact_created") and not await db().leads.find_one({"owner_id": ctx.ws, "contact_id": cid}, {"_id": 1}):
                await db().contacts.delete_one({"_id": cid, "ws": ctx.ws})
                await crm.set_reminder(ctx.ws, "contact", cid, None)
        lead = None
    if lead:
        if a.get("follow_up_on"):
            if lead.get("follow_up_on") == a["follow_up_on"]:
                back = un.get("follow_up_on")
                await db().leads.update_one({"_id": lead["_id"]}, {"$set": {"follow_up_on": back, "follow_up_note": un.get("follow_up_note") or "",
                                                                            "updated_at": now(), "updated_by": ctx.actor}})
                await crm.set_reminder(ctx.ws, "deal", lead["_id"], back if lead.get("status", "new") in crm.OPEN else None)
                lead.update(follow_up_on=back, follow_up_note=un.get("follow_up_note") or "")
            else:
                kept.append("the follow-up date (changed since)")
        stages = await crm.stages_of(ctx.ws)
        patch: dict = {}
        if a.get("pstage") and un.get("pstage"):
            if crm.pstage_of(lead, stages) == a["pstage"] and any(x["id"] == un["pstage"] for x in stages) and lead.get("status", "new") in crm.OPEN:
                patch["pstage"] = un["pstage"]
            else:
                kept.append("the stage (changed since)")
        if "value" in a and "value" in un:
            if lead.get("value") == a["value"]:
                patch["value"] = un["value"]
            else:
                kept.append("the value (changed since)")
        if a.get("status") and un.get("status"):
            if lead.get("status") == a["status"]:
                patch["status"] = un["status"]
                if un["status"] == "lost":
                    patch["lost_reason"] = un.get("lost_reason") or ""
            else:
                kept.append("won / lost (changed since)")
        if patch:
            await crm_router._apply_deal_patch(ownerdesk_ctx(ctx.owner, ctx.user, f"{_person(ctx)} (undo)"), lead, patch)
    if u.get("state") == "pending":
        await db().deal_updates.update_one({"_id": uid}, {"$set": {"state": "dismissed", "dismissed_at": now(), "dismissed_by": ctx.actor}})
    name = (lead or {}).get("name") or (u.get("suggested") or {}).get("new_deal", {}).get("name") or "a deal"
    await growth_core.log_action(ctx.ws, ROLE, f"{_person(ctx)} undid OwnerDesk's update on {name}",
                                 ref=f"lead:{lead['_id']}" if lead else f"update:{uid}")
    dashboard_cache.bust(ctx.ws)
    return {"update": view(await _update(ctx.ws, uid), await stage_names(ctx.ws)), "kept": kept}


# ───────────────────────── a known contact and their open deal ─────────────────────────
async def open_deal_of(ws: str, contact: dict) -> dict | None:
    ors = [{"contact_id": contact["_id"]}]
    if contact.get("email"):
        ors.append({"email": contact["email"]})
    if contact.get("phone"):
        ors.append({"phone": contact["phone"]})
    deals = [x async for x in db().leads.find({"owner_id": ws, "status": {"$in": list(crm.OPEN)}, "$or": ors}).limit(50)]
    if not deals:
        return None
    return max(deals, key=lambda d: crm._aware(d.get("updated_at") or d.get("stage_at") or d.get("created_at") or now()))


# ───────────────────────── email from a known contact ─────────────────────────
async def from_email(owner: dict, mail: dict, name: str, sender: str, text: str, verified: bool) -> str | None:
    """An email to the owner's inquiry address from someone who is already a contact with an open deal: filed into that deal
    instead of becoming a new inquiry. Only for a sender whose address is proven (DMARC), only when the plan has it and the
    Pipeline keeper is switched on. None = not filed (the email goes on as it always has)."""
    from .routers import inquiries
    ws = owner["_id"]
    email = crm.norm_email(sender)
    if not verified or not email or not plans.has(owner, "deal_autofill") or not await growth_core.role_on(ws, ROLE):
        return None
    contact = await crm.find_contact(ws, None, email)
    lead = await open_deal_of(ws, contact) if contact else None
    if not lead:
        return None
    if await inquiries._seen(ws, "deal", mail, lead_id=lead["_id"]):
        return "duplicate"
    said = "\n".join(x for x in (mail.get("subject"), text) if x).strip()[:MAX_TEXT] or "(empty email)"
    used_ai = await db().deal_updates.count_documents({"ws": ws, "source": "email", "tokens": 1, "at": {"$gte": _day_start()}})
    messages = [{"day": today(), "who": (name or email)[:60], "text": said}]
    try:
        await file(owner, lead, messages, "email", BY, BY_NAME, rules_when_no_tokens=True, use_ai=used_ai < EMAIL_AI_PER_DAY)
    except Exception:  # noqa: BLE001  never lose the email: it becomes an inquiry as before
        log.exception("filing an email into deal %s failed", lead["_id"])
        await db().inbound_emails.delete_one({"_id": inquiries._key(ws, "deal", mail)})
        return None
    await db().leads.update_one({"_id": lead["_id"]}, {"$push": {"replies": {"$each": [{"at": now(), "from": email, "text": said[:2000]}], "$slice": -20}}})
    first = (text or mail.get("subject") or "").strip().splitlines()[0][:140] if (text or mail.get("subject")) else ""
    await inquiries._owner_alert(ws, f"{lead.get('name') or email} emailed about their deal: “{first}”. OwnerDesk added it to the deal.")
    return "deal"


# ───────────────────────── WhatsApp chats on the owner's connected number ─────────────────────────
async def _chats_ready(owners: list[dict]) -> set:
    since = now() - timedelta(days=CHAT_LOOKBACK_DAYS)
    return set(await db().wa_threads.distinct("ws", {"ws": {"$in": [o["_id"] for o in owners]}, "last_in_at": {"$gte": since}}))


async def file_chats(owner: dict, at: datetime | None = None) -> int:
    """Files each quiet chat with a known contact (an open deal) that has new customer messages since it was last filed — at
    most once a day per chat. Reads wa_messages only: it never replies and never switches the WhatsApp AI on."""
    ws = owner["_id"]
    at = at or now()
    day = at.astimezone(IST).strftime("%Y-%m-%d")
    quiet = at - timedelta(minutes=QUIET_MINUTES)
    since = at - timedelta(days=CHAT_LOOKBACK_DAYS)
    filed = 0
    async for t in db().wa_threads.find({"ws": ws, "last_in_at": {"$gte": since, "$lte": quiet}}).sort("last_in_at", -1).limit(200):
        if filed >= CHATS_PER_RUN:
            break
        mark = await db().deal_fill_chats.find_one({"_id": t["_id"]}) or {}
        if mark.get("filed_day") == day:
            continue
        upto = mark.get("filed_upto")
        if upto and crm._aware(t["last_in_at"]) <= crm._aware(upto):
            continue                                   # nothing new from the customer since the last filing
        contact = await db().contacts.find_one({"ws": ws, "phone": "+" + str(t.get("phone") or "")})
        lead = await open_deal_of(ws, contact) if contact else None
        if not lead:
            continue
        start = max(crm._aware(upto), since) if upto else since
        msgs = [m async for m in db().wa_messages.find({"thread_id": t["_id"], "at": {"$gt": start, "$lte": at}}).sort("at", -1).limit(tasks.KEEP_MESSAGES)][::-1]
        if not any(m.get("dir") == "in" for m in msgs):
            continue
        if (await runs_status(owner))["left"] < 1:
            break
        from .agents.jobs import claim
        if not await claim(f"dealfill-wa:{t['_id']}:{day}", at):
            continue
        cust = contact.get("name") or t.get("name") or "Customer"
        messages = [{"day": crm._aware(m["at"]).astimezone(IST).date(), "who": cust if m.get("dir") == "in" else "You", "text": m.get("text", "")}
                    for m in msgs if (m.get("text") or "").strip()]
        try:
            u = await file(owner, lead, messages, "whatsapp", BY, BY_NAME)
        except HTTPException as e:
            if e.status_code == 402:
                break
            log.info("WhatsApp chat %s not filed: %s", t["_id"], getattr(e, "detail", e))
            continue
        newest_in = max(crm._aware(m["at"]) for m in msgs if m.get("dir") == "in")
        await db().deal_fill_chats.update_one({"_id": t["_id"]}, {"$set": {"ws": ws, "filed_upto": newest_in, "filed_day": day, "update_id": u["_id"],
                                                                          "at": now()}}, upsert=True)
        filed += 1
    return filed


async def _chats_runner(owner: dict, run: dict):
    if not await growth_core.role_on(owner["_id"], ROLE):
        raise Skip("The Pipeline keeper is switched off.")
    n = await file_chats(owner, run.get("at"))
    if not n:
        raise Skip("No WhatsApp chats with your open deals needed filing today.")
    return f"Filed {n} WhatsApp chat{'s' if n != 1 else ''} into your deals."


# The scheduler's clock (agents/scheduler.tick, every minute) runs registered agents once per slot, and the shortest slot the
# registry has is a day ("every": "day"). WhatsApp chats are filed once a day per chat anyway, so the Pipeline keeper looks at
# the chats each evening: every chat quiet for 30 minutes or more, with new customer messages, is filed. (A self-rescheduling
# 30-minute job would write a "nothing to do" line into the activity log every half hour.)
catalog.register(ROLE, {
    "name": "Pipeline keeper", "feature": "deal_autofill", "trigger": "schedule", "group": "team",
    "what": "Files conversations into your deals: a summary in the notes, the next step and the follow-up date when a day was said. "
            "A new stage or value waits for your tap. Emails from a customer with an open deal go into that deal, and — if your "
            "WhatsApp number is connected — each evening the day's chats with your customers are filed (it never replies).",
    "when": "When you add a conversation, when a customer with an open deal emails you, and every evening at 7:30pm for WhatsApp chats",
    "schedule": {"every": "day", "at": FILE_AT, "grace_hours": 4}, "ready": _chats_ready}, _chats_runner)


async def ensure_indexes() -> None:
    d = db()
    await d.deal_updates.create_index([("ws", 1), ("lead_id", 1), ("at", -1)])
    await d.deal_updates.create_index([("ws", 1), ("state", 1), ("at", -1)])
    await d.deal_updates.create_index([("ws", 1), ("call_id", 1)], sparse=True)
    await d.deal_fill_chats.create_index([("ws", 1)])
