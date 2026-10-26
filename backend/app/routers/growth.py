"""
Growth Mentorship: the 6 AI staff, the 6-month setup, connections to the owner's own accounts, WhatsApp chats, the Company
Brain, money campaigns, Control Room, the Sales Training Gym and the monthly AI results.
Admin endpoints for doing the setup on an owner's behalf live under /api/admin/owners/<id>/....
"""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.datastructures import UploadFile

from .. import campaigns as camp
from .. import chat_ai, company_brain, connections, integrations, plans
from ..agents import growth as g
from ..agents import jobs
from ..ai import chat_tasks, growth_tasks
from ..context import Ctx, feature, get_ctx
from ..db import IST, db, now, public
from ..security import require_admin
from ..services import new_id, run_ai, track
from ..staff import BY_KEY, SETUP_STEPS, STAFF
from .hub import profile_of

router = APIRouter(prefix="/api")


# ═════════════════════════ The 6 AI staff ═════════════════════════
class StaffPatch(BaseModel):
    on: bool


@router.get("/staff")
async def get_staff(ctx: Ctx = Depends(feature("ai_staff", "manager"))):
    live = await g.staff_live(ctx.ws, ctx.owner)
    week_ago = now() - timedelta(days=7)
    out = []
    for s in STAFF:
        log = [{"text": e["text"], "at": e["at"].isoformat(), "status": e["status"]}
               async for e in db().agent_log.find({"ws": ctx.ws, "agent": {"$in": s["agents"]}}).sort("at", -1).limit(5)]
        actions = await db().agent_log.count_documents({"ws": ctx.ws, "agent": {"$in": s["agents"]}, "status": "done", "at": {"$gte": week_ago}})
        out.append({**{k: v for k, v in s.items() if k != "agents"}, "agents": s["agents"], "live": live[s["key"]],
                    "on": await jobs.is_on(ctx.ws, f"staff_{s['key']}"), "actions_week": actions, "recent": log})
    conns = {k: connections.ready(k, await connections.get(ctx.ws, k)) for k in ("whatsapp", "voice", "employz")}
    return {"staff": out, "connections": conns, "calls": [public(c) async for c in db().calls.find({"ws": ctx.ws}).sort("started_at", -1).limit(10)]}


@router.patch("/staff/{key}")
async def switch_staff(key: str, body: StaffPatch, ctx: Ctx = Depends(feature("ai_staff", "owner"))):
    s = BY_KEY.get(key)
    if not s:
        raise HTTPException(404, "Not found")
    await jobs.save_settings(ctx.ws, f"staff_{key}", {"on": body.on})
    for a in s["agents"]:
        await jobs.save_settings(ctx.ws, a, {"on": body.on})
    return {"ok": True, "on": body.on}


# ═════════════════════════ The 6-month setup ═════════════════════════
class StepIn(BaseModel):
    done: bool
    note: str = Field(default="", max_length=300)


async def setup_view(ws: str, owner: dict) -> dict:
    doc = await db().setup.find_one({"_id": ws}) or {}
    steps = doc.get("steps") or {}
    started = doc.get("started_at")
    if not started:
        until = (owner.get("access") or {}).get("growth") or (owner.get("access") or {}).get("office")
        started = (until - timedelta(days=plans.PLANS["growth"].get("access_days") or 180)) if until else owner.get("created_at")
    out = []
    for key, month, label in SETUP_STEPS:
        st = steps.get(key) or {}
        out.append({"key": key, "month": month, "label": label, "done": bool(st.get("done")), "note": st.get("note", ""),
                    "done_at": st["done_at"].isoformat() if st.get("done_at") else None})
    done = sum(1 for s in out if s["done"])
    return {"steps": out, "done": done, "total": len(out), "started_at": started.isoformat() if started else None}


@router.get("/setup")
async def my_setup(ctx: Ctx = Depends(feature("setup_tracker", "owner"))):
    return await setup_view(ctx.ws, ctx.owner)


async def _owner(uid: str) -> dict:
    u = await db().users.find_one({"_id": uid})
    if not u or u.get("team_of"):
        raise HTTPException(404, "Owner not found")
    return u


@router.get("/admin/owners/{uid}/setup")
async def admin_setup(uid: str, _: dict = Depends(require_admin)):
    return await setup_view(uid, await _owner(uid))


@router.put("/admin/owners/{uid}/setup/{step}")
async def admin_setup_step(uid: str, step: str, body: StepIn, admin: dict = Depends(require_admin)):
    owner = await _owner(uid)
    if step not in {k for k, _, _ in SETUP_STEPS}:
        raise HTTPException(404, "No such step")
    await db().setup.update_one({"_id": uid}, {"$set": {f"steps.{step}": {"done": body.done, "note": body.note.strip(),
                                                                          "done_at": now() if body.done else None, "by": admin["_id"]}},
                                               "$setOnInsert": {"started_at": now()}}, upsert=True)
    if body.done:
        from ..services import notify
        label = next(l for k, _, l in SETUP_STEPS if k == step)
        await notify(uid, f"Set up for you: {label}.")
    await track("setup_step", uid, step=step, done=body.done)
    return await setup_view(uid, owner)


# ═════════════════════════ Connections ═════════════════════════
class ConnIn(BaseModel):
    values: dict


async def _stored(ws: str, kind: str) -> dict | None:
    return ((await db().connections.find_one({"_id": ws})) or {}).get(kind)


async def conn_list(ws: str, owner: dict) -> list[dict]:
    out = []
    for kind, spec in connections.SPECS.items():
        if not plans.META.get(spec["feature"], {}).get("on"):
            continue
        out.append(connections.view(kind, await _stored(ws, kind), owner))
    return out


async def test_connection(ws: str, kind: str) -> dict:
    conn = await connections.get(ws, kind)
    if not connections.ready(kind, conn):
        raise HTTPException(400, "Fill in the details first.")
    if kind == "employz":
        res = await integrations.employz_check(conn)
    elif kind == "voice":
        res = await integrations.voice_check(conn)
    elif kind == "whatsapp":
        owner = await db().users.find_one({"_id": ws}) or {}
        b = await db().businesses.find_one({"owner_id": ws}, {"name": 1}) or {}
        tpl = conn.get("tpl_instant") or conn.get("tpl_followup")
        if not tpl:
            raise HTTPException(400, "Add at least the instant-reply template name, then test.")
        r = await integrations.wa_template(ws, conn, owner.get("phone", ""), tpl, ["there", b.get("name") or "us", "This is a test from your hub."],
                                           owner.get("name", ""), "test")
        res = {"ok": bool(r.get("sent")), "note": "A test message was sent to your own WhatsApp." if r.get("sent") else (r.get("error") or "Not sent.")}
    else:
        res = {"ok": True, "note": "Saved."}
    await connections.mark(ws, kind, "ok" if res["ok"] else "error", res["note"])
    return res


@router.get("/connections")
async def my_connections(ctx: Ctx = Depends(get_ctx)):
    if not ctx.at_least("owner"):
        raise HTTPException(403, "Only the business owner can do this.")
    return await conn_list(ctx.ws, ctx.owner)


@router.put("/connections/{kind}")
async def save_connection(kind: str, body: ConnIn, ctx: Ctx = Depends(get_ctx)):
    if kind not in connections.SPECS:
        raise HTTPException(404, "No such connection")
    ctx.require(connections.SPECS[kind]["feature"])
    if not ctx.at_least("owner"):
        raise HTTPException(403, "Only the business owner can do this.")
    await connections.save(ctx.ws, kind, body.values, ctx.actor, "you")
    if kind == "employz":            # saved again: the Employz.ai import resumes (nothing it imported was purged yet)
        from .. import employz_import
        await employz_import.on_connect(ctx.ws)
    return connections.view(kind, await _stored(ctx.ws, kind), ctx.owner)


@router.post("/connections/{kind}/test")
async def test_my_connection(kind: str, ctx: Ctx = Depends(get_ctx)):
    if kind not in connections.SPECS:
        raise HTTPException(404, "No such connection")
    ctx.require(connections.SPECS[kind]["feature"])
    if not ctx.at_least("owner"):
        raise HTTPException(403, "Only the business owner can do this.")
    return await test_connection(ctx.ws, kind)


@router.delete("/connections/{kind}")
async def remove_connection(kind: str, ctx: Ctx = Depends(get_ctx)):
    if kind not in connections.SPECS or not ctx.at_least("owner"):
        raise HTTPException(404, "Not found")
    await connections.remove(ctx.ws, kind)
    if kind == "employz":            # the import stops at once; what it brought in is removed after the grace period
        from .. import employz_import
        await employz_import.on_disconnect(ctx.ws)
    return {"ok": True}


@router.get("/admin/owners/{uid}/connections")
async def admin_connections(uid: str, _: dict = Depends(require_admin)):
    return await conn_list(uid, await _owner(uid))


@router.put("/admin/owners/{uid}/connections/{kind}")
async def admin_save_connection(uid: str, kind: str, body: ConnIn, admin: dict = Depends(require_admin)):
    owner = await _owner(uid)
    if kind not in connections.SPECS:
        raise HTTPException(404, "No such connection")
    await connections.save(uid, kind, body.values, admin["_id"], "the team")
    await track("connection_set", uid, kind=kind, by=admin["_id"])
    return connections.view(kind, await _stored(uid, kind), owner)


@router.post("/admin/owners/{uid}/connections/{kind}/test")
async def admin_test_connection(uid: str, kind: str, _: dict = Depends(require_admin)):
    await _owner(uid)
    if kind not in connections.SPECS:
        raise HTTPException(404, "No such connection")
    return await test_connection(uid, kind)


# ═════════════════════════ WhatsApp chats ═════════════════════════
class ReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class ThreadPatch(BaseModel):
    ai_on: bool | None = None
    needs_person: bool | None = None


@router.get("/chats")
async def chats(ctx: Ctx = Depends(feature("whatsapp_ai"))):
    from ..agents.approvals import owner_whatsapp
    threads = [public(t) async for t in db().wa_threads.find({"ws": ctx.ws}).sort("last_at", -1).limit(200)]
    return {"threads": threads, "connected": bool(await owner_whatsapp(ctx.ws, ctx.owner)),
            "ai_on": jobs.on_for(ctx.owner, "wa_sales", await jobs.get_settings(ctx.ws, "wa_sales")),
            "opt_in": jobs.needs_opt_in(ctx.owner, "wa_sales"),
            "waiting": sum(1 for t in threads if t.get("needs_person"))}


@router.get("/chats/{tid}")
async def chat(tid: str, ctx: Ctx = Depends(feature("whatsapp_ai"))):
    t = await db().wa_threads.find_one({"_id": tid, "ws": ctx.ws})
    if not t:
        raise HTTPException(404, "Chat not found")
    await db().wa_threads.update_one({"_id": tid}, {"$set": {"unread": 0}})
    msgs = [public(m) async for m in db().wa_messages.find({"thread_id": tid}).sort("at", 1).limit(300)]
    window = bool(t.get("last_in_at") and t["last_in_at"] > now() - timedelta(hours=23, minutes=55))
    lead = await db().leads.find_one({"owner_id": ctx.ws, "phone": "+" + t["phone"]}, {"name": 1, "status": 1, "stage": 1})
    return {"thread": public(t), "messages": msgs, "can_reply": window, "lead": public(lead) if lead else None}


@router.post("/chats/{tid}/reply")
async def chat_reply(tid: str, body: ReplyIn, ctx: Ctx = Depends(feature("whatsapp_ai"))):
    return await chat_ai.send_reply(ctx.ws, ctx.actor, tid, body.text.strip())


@router.patch("/chats/{tid}")
async def chat_patch(tid: str, body: ThreadPatch, ctx: Ctx = Depends(feature("whatsapp_ai"))):
    patch = body.model_dump(exclude_none=True)
    if patch.get("ai_on"):
        patch["needs_person"] = False
    r = await db().wa_threads.update_one({"_id": tid, "ws": ctx.ws}, {"$set": patch})
    if not r.matched_count:
        raise HTTPException(404, "Chat not found")
    return public(await db().wa_threads.find_one({"_id": tid}))


@router.post("/chats-ai")
async def chats_ai_switch(body: StaffPatch, ctx: Ctx = Depends(feature("whatsapp_ai", "owner"))):
    await jobs.save_settings(ctx.ws, "wa_sales", {"on": body.on, "opted_in": body.on})   # the owner's own choice (jobs.OPT_IN)
    return {"on": body.on}


# ═════════════════════════ Company Brain ═════════════════════════
class BrainText(BaseModel):
    title: str = Field(min_length=2, max_length=120)
    kind: str = Field(default="other", max_length=20)
    text: str = Field(min_length=20, max_length=200_000)


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=1000)


@router.get("/brain-docs")
async def brain_docs(ctx: Ctx = Depends(feature("company_brain"))):
    return {"docs": [public(d) async for d in db().brain_docs.find({"ws": ctx.ws}).sort("created_at", -1).limit(100)],
            "kinds": company_brain.KINDS, "max": company_brain.MAX_DOCS}


@router.post("/brain-docs")
async def add_brain_doc(request: Request, ctx: Ctx = Depends(feature("company_brain", "manager"))):
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("application/json"):
        try:
            body = BrainText.model_validate(await request.json())
        except Exception:
            raise HTTPException(400, "Add a title and at least a few lines of text.")
        doc = await company_brain.add(ctx.ws, ctx.actor, body.title, body.kind, body.text)
    else:
        form = await request.form()
        try:
            f = form.get("file")
            if not isinstance(f, UploadFile):
                raise HTTPException(400, "Choose a file to upload.")
            data = await f.read(company_brain.MAX_FILE + 1)
            filename, ftype = f.filename or "document", (f.content_type or "").lower()
            title, kind = str(form.get("title") or ""), str(form.get("kind") or "other")
        finally:
            await form.close()
        if len(data) > company_brain.MAX_FILE:
            raise HTTPException(413, "Keep each file under 8 MB.")
        doc = await company_brain.add(ctx.ws, ctx.actor, title or filename, kind, company_brain.extract(data, filename, ftype), filename)
    await track("brain_doc", ctx.ws)
    return public(doc)


@router.delete("/brain-docs/{doc_id}")
async def delete_brain_doc(doc_id: str, ctx: Ctx = Depends(feature("company_brain", "manager"))):
    await company_brain.remove(ctx.ws, doc_id)
    return {"ok": True}


@router.post("/brain-docs/ask")
async def ask_brain(body: AskIn, ctx: Ctx = Depends(feature("company_brain"))):
    p = await profile_of(ctx.ws)
    context = await company_brain.context(ctx.ws, body.question, k=6)

    def shape(r):
        if not str(r.get("answer") or "").strip():
            raise ValueError("empty")
        return {"answer": str(r["answer"])[:2000], "found": bool(r.get("found", True))}
    return await run_ai(ctx.owner, "brain_answer", growth_tasks.answer(p, body.question, context), f"Asked the Company Brain: {body.question[:80]}",
                        check=shape)


# ═════════════════════════ Money campaigns ═════════════════════════
class CampaignDraft(BaseModel):
    kind: str = Field(pattern="^(revival|festival|referral|custom)$")
    notes: str = Field(default="", max_length=600)


class Audience(BaseModel):
    tiers: list[str] = Field(default_factory=lambda: ["A", "B", "C"])
    quiet_days: int = Field(default=0, ge=0, le=1000)
    include_leads: bool = False
    lead_days: int = Field(default=30, ge=0, le=1000)


class CampaignIn(BaseModel):
    kind: str = Field(pattern="^(revival|festival|referral|custom)$")
    title: str = Field(min_length=3, max_length=80)
    message: str = Field(min_length=10, max_length=700)
    audience: Audience = Field(default_factory=Audience)
    template: str = Field(default="", max_length=80)


@router.get("/campaigns")
async def list_campaigns(ctx: Ctx = Depends(feature("campaigns", "manager"))):
    from ..agents.approvals import owner_whatsapp
    conn = await owner_whatsapp(ctx.ws, ctx.owner)
    items = [public(c) async for c in db().campaigns.find({"ws": ctx.ws}).sort("created_at", -1).limit(40)]
    month = now().astimezone(IST).strftime("%Y-%m")
    return {"campaigns": items, "connected": bool(conn), "template": (conn or {}).get("tpl_campaign", ""),
            "this_month": sum(1 for c in items if c.get("month") == month and c.get("status") in ("running", "done"))}


@router.post("/campaigns/draft")
async def draft_campaign(body: CampaignDraft, ctx: Ctx = Depends(feature("campaigns", "manager"))):
    p = await profile_of(ctx.ws)

    def shape(r):
        if not str(r.get("message") or "").strip():
            raise ValueError("empty")
        return {"title": str(r.get("title") or "Campaign")[:80], "message": str(r["message"]).strip()[:700]}
    return await run_ai(ctx.owner, "campaign", growth_tasks.campaign_message(p, body.kind, body.notes.strip()), f"Campaign draft: {body.kind}",
                        save_output=False, check=shape)


@router.post("/campaigns/preview")
async def preview_audience(body: Audience, ctx: Ctx = Depends(feature("campaigns", "manager"))):
    people = await camp.audience(ctx.ws, body.model_dump())
    return {"count": len(people), "sample": [p["name"] or ("+" + p["phone"]) for p in people[:8]]}


@router.post("/campaigns")
async def create_campaign(body: CampaignIn, ctx: Ctx = Depends(feature("campaigns", "manager"))):
    if any(x in body.message.lower() for x in ("http://", "https://", "www.")):
        raise HTTPException(400, "Leave links out of the campaign message — WhatsApp templates and customers both trust plain messages more.")
    doc = {"_id": new_id(), "ws": ctx.ws, **body.model_dump(), "status": "draft", "month": now().astimezone(IST).strftime("%Y-%m"),
           "sent": 0, "failed": 0, "recipients": 0, "created_by": ctx.actor, "created_at": now()}
    await db().campaigns.insert_one(doc)
    return public(doc)


@router.post("/campaigns/{cid}/approve")
async def approve_campaign(cid: str, ctx: Ctx = Depends(feature("campaigns", "owner"))):
    c = await db().campaigns.find_one({"_id": cid, "ws": ctx.ws})
    if not c:
        raise HTTPException(404, "Not found")
    if c["status"] != "draft":
        raise HTTPException(409, "This campaign was already approved.")
    people = await camp.audience(ctx.ws, c.get("audience") or {})
    if not people:
        raise HTTPException(400, "Nobody matches this audience yet. Import your customer list, or choose more customers.")
    claimed = await db().campaigns.update_one({"_id": cid, "status": "draft"}, {"$set": {"status": "running", "approved_at": now(),
                                                                                        "approved_by": ctx.actor, "recipients": len(people)}})
    if not claimed.modified_count:
        raise HTTPException(409, "This campaign was already approved.")
    await db().campaign_sends.insert_many([{"_id": f"{cid}:{p['phone']}", "campaign_id": cid, "ws": ctx.ws, "phone": p["phone"], "name": p["name"],
                                            "source": p["source"], "status": "queued", "day": None} for p in people])
    await track("campaign_approved", ctx.ws, recipients=len(people))
    return public(await db().campaigns.find_one({"_id": cid}))


@router.post("/campaigns/{cid}/stop")
async def stop_campaign(cid: str, ctx: Ctx = Depends(feature("campaigns", "owner"))):
    r = await db().campaigns.update_one({"_id": cid, "ws": ctx.ws, "status": "running"}, {"$set": {"status": "stopped", "stopped_at": now()}})
    if not r.matched_count:
        raise HTTPException(409, "This campaign isn't running.")
    await db().campaign_sends.update_many({"campaign_id": cid, "status": "queued"}, {"$set": {"status": "stopped"}})
    return public(await db().campaigns.find_one({"_id": cid}))


@router.delete("/campaigns/{cid}")
async def delete_campaign(cid: str, ctx: Ctx = Depends(feature("campaigns", "manager"))):
    r = await db().campaigns.delete_one({"_id": cid, "ws": ctx.ws, "status": "draft"})
    if not r.deleted_count:
        raise HTTPException(400, "Only a draft can be deleted. Stop a running campaign instead.")
    return {"ok": True}


# ═════════════════════════ Control Room ═════════════════════════
@router.get("/control")
async def control(ctx: Ctx = Depends(feature("control_room", "manager"))):
    d = await g.control_room(ctx.owner)
    d["ceo_line"] = g.ceo_line(d)
    return d


# ═════════════════════════ Sales Training Gym ═════════════════════════
class GymStart(BaseModel):
    scenario: str = Field(pattern="^(haggler|thinker|comparer|unhappy)$")


class GymSay(BaseModel):
    text: str = Field(min_length=1, max_length=1500)


class CallScoreIn(BaseModel):
    transcript: str = Field(default="", max_length=20000)
    call_id: str | None = Field(default=None, max_length=40)


@router.get("/gym")
async def gym(ctx: Ctx = Depends(feature("training_gym"))):
    q = {"ws": ctx.ws} if ctx.at_least("manager") else {"ws": ctx.ws, "user_id": ctx.actor}
    sessions = [public(s) async for s in db().gym_sessions.find(q).sort("created_at", -1).limit(20)]
    return {"scenarios": [{"key": k, "title": t, "brief": b} for k, (t, b) in chat_tasks.SCENARIOS.items()], "sessions": sessions,
            "calls": [public(c) async for c in db().calls.find({"ws": ctx.ws, "transcript": {"$exists": True}}).sort("started_at", -1).limit(10)]
            if ctx.at_least("manager") else []}


async def _session(ctx: Ctx, sid: str) -> dict:
    s = await db().gym_sessions.find_one({"_id": sid, "ws": ctx.ws})
    if not s or (s.get("user_id") != ctx.actor and not ctx.at_least("manager")):
        raise HTTPException(404, "Not found")
    return s


@router.post("/gym/sessions")
async def gym_start(body: GymStart, ctx: Ctx = Depends(feature("training_gym"))):
    p = await profile_of(ctx.ws)
    first = await run_ai(ctx.owner, "gym", chat_tasks.gym_customer(p, body.scenario, []), f"Gym: {chat_tasks.SCENARIOS[body.scenario][0]}",
                         save_output=False, check=lambda r: {"reply": str(r.get("reply") or "Hello?")[:600], "done": bool(r.get("done"))})
    doc = {"_id": new_id(), "ws": ctx.ws, "user_id": ctx.actor, "kind": "role-play", "scenario": body.scenario,
           "title": chat_tasks.SCENARIOS[body.scenario][0], "messages": [{"role": "customer", "text": first["reply"]}],
           "status": "open", "created_at": now()}
    await db().gym_sessions.insert_one(doc)
    return public(doc)


@router.post("/gym/sessions/{sid}/say")
async def gym_say(sid: str, body: GymSay, ctx: Ctx = Depends(feature("training_gym"))):
    s = await _session(ctx, sid)
    if s["status"] != "open":
        raise HTTPException(400, "This practice is finished. Start a new one.")
    if len(s["messages"]) >= 30:
        raise HTTPException(400, "That's a long one — finish it to get your score.")
    msgs = s["messages"] + [{"role": "salesperson", "text": body.text.strip()}]
    p = await profile_of(ctx.ws)
    r = await run_ai(ctx.owner, "gym", chat_tasks.gym_customer(p, s["scenario"], msgs), f"Gym: {s['title']}", save_output=False,
                     check=lambda r: {"reply": str(r.get("reply") or "Okay.")[:600], "done": bool(r.get("done"))})
    msgs.append({"role": "customer", "text": r["reply"]})
    await db().gym_sessions.update_one({"_id": sid}, {"$set": {"messages": msgs, "customer_done": r["done"]}})
    return public(await db().gym_sessions.find_one({"_id": sid}))


@router.post("/gym/sessions/{sid}/finish")
async def gym_finish(sid: str, ctx: Ctx = Depends(feature("training_gym"))):
    s = await _session(ctx, sid)
    if s["status"] == "scored":
        return public(s)
    if sum(1 for m in s["messages"] if m["role"] == "salesperson") < 1:
        raise HTTPException(400, "Say something to the customer first.")
    p = await profile_of(ctx.ws)
    score = await run_ai(ctx.owner, "gym_score", chat_tasks.score_conversation(p, s["messages"]), f"Gym score: {s['title']}",
                         save_output=False, check=chat_tasks.score_shape)
    await db().gym_sessions.update_one({"_id": sid}, {"$set": {"status": "scored", "score": score, "scored_at": now()}})
    await track("gym_scored", ctx.ws, overall=score["overall"])
    return public(await db().gym_sessions.find_one({"_id": sid}))


@router.post("/gym/score-call")
async def score_call(body: CallScoreIn, ctx: Ctx = Depends(feature("training_gym"))):
    """Score a real call: a pasted transcript (Customer: … / Me: …) or a call the AI Telecaller made."""
    if body.call_id:
        call = await db().calls.find_one({"_id": body.call_id, "ws": ctx.ws})
        if not call or not call.get("transcript"):
            raise HTTPException(404, "That call has no transcript yet.")
        turns = [{"role": "customer" if t.get("role") == "user" else "salesperson", "text": t.get("message", "")}
                 for t in call["transcript"] if t.get("message")]
        title = f"Call with {call.get('name') or call.get('to')}"
    else:
        turns = chat_tasks.call_lines(body.transcript)
        title = "A real call"
    if len(turns) < 2:
        raise HTTPException(400, "Paste the call with who said what, like “Customer: … / Me: …”.")
    p = await profile_of(ctx.ws)
    score = await run_ai(ctx.owner, "gym_score", chat_tasks.score_conversation(p, turns, "call"), f"Call score: {title}",
                         save_output=False, check=chat_tasks.score_shape)
    doc = {"_id": new_id(), "ws": ctx.ws, "user_id": ctx.actor, "kind": "call", "scenario": "call", "title": title, "messages": turns,
           "status": "scored", "score": score, "call_id": body.call_id, "created_at": now(), "scored_at": now()}
    await db().gym_sessions.insert_one(doc)
    if body.call_id:
        await db().calls.update_one({"_id": body.call_id}, {"$set": {"score": score}})
    return public(doc)


# ═════════════════════════ AI results ═════════════════════════
@router.get("/results")
async def results(ctx: Ctx = Depends(feature("results_report", "owner"))):
    start, end, label = g.month_bounds(now())
    current = await g.results_data(ctx.owner, start, now())
    past = [public(r) async for r in db().results.find({"ws": ctx.ws}).sort("month", -1).limit(6)]
    if not past:
        ls, le, ll = g.month_bounds(now(), back=1)
        last = await g.results_data(ctx.owner, ls, le)
        past = [{"month": ls.astimezone(IST).strftime("%Y-%m"), "label": ll, **last}]
    return {"this_month": {"label": f"{label} so far", **current}, "past": past}


async def ensure_indexes() -> None:
    await connections.ensure_indexes()
    await chat_ai.ensure_indexes()
    await company_brain.ensure_indexes()
    await camp.ensure_indexes()
    await db().calls.create_index([("ws", 1), ("started_at", -1)])
    await db().calls.create_index("conversation_id")
    await db().gym_sessions.create_index([("ws", 1), ("created_at", -1)])
    await db().results.create_index([("ws", 1), ("month", -1)])
