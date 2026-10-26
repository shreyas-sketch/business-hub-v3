"""
Key-person knowledge capture (v2.3, Data engine) — see app/keyperson.py for the flow, the rules and the data.

Owner (feature key_person, the business owner only):
    GET    /api/key-person                         the people, what each AI step costs, tokens left
    POST   /api/key-person                         {name, role, department} a new person
    GET    /api/key-person/{id}                    one person, everything
    PATCH  /api/key-person/{id}                    name, role, department
    DELETE /api/key-person/{id}                    the person (their SOPs stay in the SOP library)
    POST   /api/key-person/{id}/owner-questions    up to 5 questions for the owner (1 AI token)
    PUT    /api/key-person/{id}/owner-answers      the owner's answers
    POST   /api/key-person/{id}/questionnaire      the employee's 12–20 questions + their link (1 AI token)
    PUT    /api/key-person/{id}/questionnaire      the owner edits the questions (until the answers are in)
    POST   /api/key-person/{id}/link               a new link (the old one stops working), 14 more days
    GET    /api/key-person/{id}/questions.pdf      the questions as a PDF, for reading
    PUT    /api/key-person/{id}/answers            the owner types the answers instead {answers {key: text}, submit}
    POST   /api/key-person/{id}/reopen             lets the employee change their answers again
    POST   /api/key-person/{id}/skill              the agent skill, role card and SOPs (1 AI token)
    GET    /api/key-person/{id}/skill.md           the MD skill file
    GET    /api/key-person/{id}/card.png           the role card
    POST   /api/key-person/{id}/add-to-agent       into the Company Brain the AI staff answer from (plans with AI staff)

Employee (no login; the link's token; rate-limited per network and per link):
    GET    /api/kp/{token}                         business, first name, role, the note, the questions, a saved draft
    PUT    /api/kp/{token}                         {answers {key: text}, submit} a draft, or the final answers
    GET    /api/kp/{token}/questions.pdf           the questions as a PDF
"""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field

from .. import keyperson as kp, plans
from ..ai import keyperson_tasks as tasks
from ..config import settings
from ..context import Ctx, feature
from ..db import db, now
from ..security import client_ip, rate_limit
from ..services import new_id, notify, run_ai, runs_status, track
from .hub import profile_of

router = APIRouter(prefix="/api/key-person")
public = APIRouter(prefix="/api/kp")
owner_only = feature("key_person", "owner")
COSTS = {"owner_questions": 1, "questionnaire": 1, "skill": 1}
NOTE = ("These questions are about how your work is done — the steps, the decisions, who you deal with and what usually goes "
        "wrong. {business} will use your answers to write down your know-how, so the team and an AI assistant can help with "
        "it and you aren't the only one who knows. Don't write any password, PIN or bank detail, or anything personal.")


def _link(p: dict) -> str:
    return f"{settings.app_url}/k/{p['token']}" if p.get("token") else ""


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _status(p: dict) -> str:
    if p.get("skill"):
        return "skill"
    if p.get("submitted_at"):
        return "answered"
    if p.get("questions"):
        return "sent"
    if p.get("owner_answers"):
        return "owner_answered"
    if p.get("owner_questions"):
        return "owner_questions"
    return "new"


def _summary(p: dict) -> dict:
    answered = sum(1 for a in p.get("answers") or [] if a.get("a"))
    return {"id": p["_id"], "name": p.get("name", ""), "role": p.get("role", ""), "department": p.get("department", ""),
            "status": _status(p), "questions": len(p.get("questions") or []), "answered": answered,
            "created_at": _iso(p.get("created_at")), "submitted_at": _iso(p.get("submitted_at"))}


async def _view(p: dict, owner: dict) -> dict:
    sk = p.get("skill")
    sops = []
    if p.get("sop_ids"):
        sops = [{"id": d["_id"], "title": d["title"]} async for d in db().kits.find({"_id": {"$in": p["sop_ids"]}, "ws": p["ws"]}, {"title": 1})]
    return {**_summary(p), "owner_questions": p.get("owner_questions") or [], "owner_answers": p.get("owner_answers") or [],
            "questions": p.get("questions") or [], "answers": p.get("answers") or [],
            "link": _link(p) if p.get("questions") else "", "link_expires_at": _iso(p.get("link_expires_at")),
            "link_expired": bool(p.get("link_expires_at") and p["link_expires_at"] <= now()),
            "opened_at": _iso(p.get("opened_at")), "submitted_by": p.get("submitted_by"), "draft_saved_at": _iso(p.get("draft_saved_at")),
            "skill": {k: v for k, v in sk.items() if k != "built_at"} | {"built_at": _iso(sk.get("built_at"))} if sk else None,
            "sops": sops, "in_brain": bool(p.get("brain_doc_id")), "can_add_to_agent": plans.has(owner, "ai_staff") and plans.has(owner, "company_brain"),
            "costs": COSTS, "tokens_left": (await runs_status(owner))["left"],
            "note": NOTE.format(business="The business")}


async def _get(ctx: Ctx, pid: str) -> dict:
    p = await db().key_people.find_one({"_id": pid, "ws": ctx.ws})
    if not p:
        raise HTTPException(404, "Not found")
    return p


async def _set(p: dict, patch: dict) -> dict:
    patch["updated_at"] = now()
    await db().key_people.update_one({"_id": p["_id"]}, {"$set": patch})
    return await db().key_people.find_one({"_id": p["_id"]})


# ───────────────────────── the owner's side ─────────────────────────
class PersonIn(BaseModel):
    name: str = Field(min_length=2, max_length=60)
    role: str = Field(min_length=2, max_length=60)
    department: str = Field(default="", max_length=60)


class PersonPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=60)
    role: str | None = Field(default=None, min_length=2, max_length=60)
    department: str | None = Field(default=None, max_length=60)


@router.get("")
async def people(ctx: Ctx = Depends(owner_only)):
    ps = [_summary(p) async for p in db().key_people.find({"ws": ctx.ws}).sort("created_at", -1).limit(kp.MAX_PEOPLE)]
    return {"people": ps, "costs": COSTS, "tokens_left": (await runs_status(ctx.owner))["left"], "max": kp.MAX_PEOPLE}


@router.post("")
async def add_person(body: PersonIn, ctx: Ctx = Depends(owner_only)):
    if await db().key_people.count_documents({"ws": ctx.ws}) >= kp.MAX_PEOPLE:
        raise HTTPException(400, f"You can keep up to {kp.MAX_PEOPLE} people here. Remove one first.")
    p = {"_id": new_id(), "ws": ctx.ws, "name": kp._line(body.name, 60), "role": kp._line(body.role, 60),
         "department": kp._line(body.department, 60), "created_by": ctx.actor, "created_at": now(), "updated_at": now()}
    await db().key_people.insert_one(p)
    await track("key_person_added", ctx.ws)
    return await _view(p, ctx.owner)


@router.get("/{pid}")
async def one(pid: str, ctx: Ctx = Depends(owner_only)):
    return await _view(await _get(ctx, pid), ctx.owner)


@router.patch("/{pid}")
async def edit(pid: str, body: PersonPatch, ctx: Ctx = Depends(owner_only)):
    p = await _get(ctx, pid)
    patch = {k: kp._line(v, 60) for k, v in body.model_dump(exclude_none=True).items()}
    return await _view(await _set(p, patch) if patch else p, ctx.owner)


@router.delete("/{pid}")
async def remove(pid: str, ctx: Ctx = Depends(owner_only)):
    p = await _get(ctx, pid)
    await db().key_people.delete_one({"_id": p["_id"]})
    return {"ok": True}


@router.post("/{pid}/owner-questions")
async def owner_questions(pid: str, ctx: Ctx = Depends(owner_only)):
    p = await _get(ctx, pid)
    b = await profile_of(ctx.ws)
    qs = await run_ai(ctx.owner, "key_person:owner_questions", tasks.owner_questions(b, p), f"Questions about {p['name']}",
                      save_output=False, check=kp.shape_owner_questions)
    old = {x["q"]: x["a"] for x in p.get("owner_answers") or []}
    return await _view(await _set(p, {"owner_questions": qs, "owner_answers": [{"q": q, "a": old.get(q, "")} for q in qs]}), ctx.owner)


class OwnerAnswersIn(BaseModel):
    answers: list[str] = Field(max_length=5)


@router.put("/{pid}/owner-answers")
async def owner_answers(pid: str, body: OwnerAnswersIn, ctx: Ctx = Depends(owner_only)):
    p = await _get(ctx, pid)
    qs = p.get("owner_questions") or []
    if not qs:
        raise HTTPException(400, "Get the questions first.")
    ans = [{"q": q, "a": kp.scrub(body.answers[i] if i < len(body.answers) else "", 1500)} for i, q in enumerate(qs)]
    if not any(a["a"] for a in ans):
        raise HTTPException(400, "Answer at least one question.")
    return await _view(await _set(p, {"owner_answers": ans}), ctx.owner)


@router.post("/{pid}/questionnaire")
async def make_questionnaire(pid: str, ctx: Ctx = Depends(owner_only)):
    p = await _get(ctx, pid)
    if p.get("submitted_at"):
        raise HTTPException(409, f"{p['name']} has already answered. Reopen the answers first if you want new questions.")
    if not any((x.get("a") or "").strip() for x in p.get("owner_answers") or []):
        raise HTTPException(400, f"Answer the questions about {p['name']} first, so the questionnaire fits their work.")
    b = await profile_of(ctx.ws)
    qs = await run_ai(ctx.owner, "key_person:questionnaire", tasks.questionnaire(b, p, p.get("owner_answers") or []),
                      f"Questionnaire for {p['name']}", save_output=False, check=kp.shape_questions)
    patch = {"questions": qs, "answers": [], "opened_at": None, "draft_saved_at": None}
    if not p.get("token") or not p.get("link_expires_at") or p["link_expires_at"] <= now():
        patch.update(kp.new_link())
    await track("key_person_questionnaire", ctx.ws, questions=len(qs))
    return await _view(await _set(p, patch), ctx.owner)


class QuestionIn(BaseModel):
    key: str = Field(default="", max_length=10)
    section: str = Field(default="", max_length=60)
    q: str = Field(min_length=8, max_length=400)
    hint: str = Field(default="", max_length=240)


class QuestionsIn(BaseModel):
    questions: list[QuestionIn] = Field(min_length=3, max_length=25)


@router.put("/{pid}/questionnaire")
async def edit_questionnaire(pid: str, body: QuestionsIn, ctx: Ctx = Depends(owner_only)):
    p = await _get(ctx, pid)
    if p.get("submitted_at"):
        raise HTTPException(409, "The answers are already in. Reopen them first to change the questions.")
    out = []
    for x in body.questions:
        q = kp.safe_question(kp._line(x.q, 400))
        if not q:
            raise HTTPException(400, f"Leave out personal questions and passwords: “{x.q[:60]}”.")
        out.append({"key": f"q{len(out) + 1}", "section": kp._section(x.section), "q": q, "hint": kp._line(x.hint, 240)})
    old = {a["q"]: a["a"] for a in p.get("answers") or []}
    answers = [{"key": q["key"], "section": q["section"], "q": q["q"], "a": old.get(q["q"], "")} for q in out]
    patch = {"questions": out, "answers": answers}
    if not p.get("token"):
        patch.update(kp.new_link())
    return await _view(await _set(p, patch), ctx.owner)


@router.post("/{pid}/link")
async def renew_link(pid: str, ctx: Ctx = Depends(owner_only)):
    p = await _get(ctx, pid)
    if not p.get("questions"):
        raise HTTPException(400, "Write the questionnaire first.")
    return await _view(await _set(p, kp.new_link()), ctx.owner)


async def _business_name(ws: str) -> str:
    b = await db().businesses.find_one({"owner_id": ws}, {"name": 1}) or {}
    return b.get("name") or "Your business"


async def _pdf(p: dict) -> Response:
    from ..brand import accent_for
    data = await asyncio.to_thread(kp.questions_pdf, p, await _business_name(p["ws"]), _link(p), await accent_for(p["ws"]))
    name = kp.slug(p.get("name") or "questions")
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="questions-{name}.pdf"',
                                                                 "Cache-Control": "private, no-store"})


@router.get("/{pid}/questions.pdf")
async def owner_pdf(pid: str, ctx: Ctx = Depends(owner_only)):
    p = await _get(ctx, pid)
    if not p.get("questions"):
        raise HTTPException(400, "Write the questionnaire first.")
    return await _pdf(p)


class AnswersIn(BaseModel):
    answers: dict[str, str] = Field(default_factory=dict, max_length=25)
    submit: bool = False


@router.put("/{pid}/answers")
async def owner_types_answers(pid: str, body: AnswersIn, ctx: Ctx = Depends(owner_only)):
    p = await _get(ctx, pid)
    if not p.get("questions"):
        raise HTTPException(400, "Write the questionnaire first.")
    ans = kp.clean_answers(p["questions"], body.answers)
    patch = {"answers": ans, "draft_saved_at": now()}
    if body.submit:
        if sum(1 for a in ans if a["a"]) < 3:
            raise HTTPException(400, "Answer at least 3 questions first.")
        patch.update(submitted_at=now(), submitted_by="owner")
    return await _view(await _set(p, patch), ctx.owner)


@router.post("/{pid}/reopen")
async def reopen(pid: str, ctx: Ctx = Depends(owner_only)):
    p = await _get(ctx, pid)
    patch = {"submitted_at": None, "submitted_by": None}
    if not p.get("link_expires_at") or p["link_expires_at"] <= now():
        patch.update(kp.new_link())
    return await _view(await _set(p, patch), ctx.owner)


@router.post("/{pid}/skill")
async def build_skill(pid: str, ctx: Ctx = Depends(owner_only)):
    p = await _get(ctx, pid)
    answered = [a for a in p.get("answers") or [] if a.get("a")]
    if not p.get("submitted_at") or len(answered) < 3:
        raise HTTPException(400, "The skill is built from the answers. Wait until they're in (at least 3 answered).")
    b = await profile_of(ctx.ws)
    sk = await run_ai(ctx.owner, "key_person:skill", tasks.skill(b, p, answered, p.get("owner_answers") or []), f"Agent skill — {p['role']}",
                      save_output=False, check=kp.shape_skill)
    sk["md"] = kp.skill_md(p, sk)
    sk["built_at"] = now()
    sop_ids = await kp.save_sops(ctx.ws, ctx.actor, p, sk, b.get("name") or "the business")
    await track("key_person_skill", ctx.ws, tasks=len(sk["tasks"]), sops=len(sop_ids))
    return await _view(await _set(p, {"skill": sk, "sop_ids": sop_ids}), ctx.owner)


def _need_skill(p: dict) -> dict:
    if not p.get("skill"):
        raise HTTPException(400, "Build the skill first.")
    return p["skill"]


@router.get("/{pid}/skill.md")
async def skill_file(pid: str, ctx: Ctx = Depends(owner_only)):
    p = await _get(ctx, pid)
    sk = _need_skill(p)
    return PlainTextResponse(sk.get("md") or kp.skill_md(p, sk), media_type="text/markdown; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{kp.slug(p.get("role"))}-skill.md"'})


@router.get("/{pid}/card.png")
async def card(pid: str, ctx: Ctx = Depends(owner_only)):
    from ..brand import accent_for
    p = await _get(ctx, pid)
    sk = _need_skill(p)
    data = await asyncio.to_thread(kp.card_png, p, sk, await _business_name(ctx.ws), await accent_for(ctx.ws))
    return Response(data, media_type="image/png", headers={"Content-Disposition": f'inline; filename="{kp.slug(p.get("role"))}-role-card.png"',
                                                           "Cache-Control": "private, no-store"})


@router.post("/{pid}/add-to-agent")
async def add_to_agent(pid: str, ctx: Ctx = Depends(owner_only)):
    """Plans with AI staff: the skill goes into the Company Brain every AI staff member answers from (replacing an older copy)."""
    if not (ctx.has("ai_staff") and ctx.has("company_brain")):
        raise HTTPException(403, "Adding a skill to an AI agent comes with the plans that include AI staff. Download the skill file instead.")
    from .. import company_brain
    p = await _get(ctx, pid)
    sk = _need_skill(p)
    if p.get("brain_doc_id"):
        await company_brain.remove(ctx.ws, p["brain_doc_id"])
    doc = await company_brain.add(ctx.ws, ctx.actor, f"{p.get('role', 'Team')} — agent skill", "policy", sk.get("md") or kp.skill_md(p, sk),
                                  f"{kp.slug(p.get('role'))}-skill.md")
    await track("key_person_to_agent", ctx.ws)
    return await _view(await _set(p, {"brain_doc_id": doc["_id"]}), ctx.owner)


# ───────────────────────── the employee's link (no login) ─────────────────────────
async def _from_link(token: str, request: Request) -> dict:
    await rate_limit(f"kp-ip:{client_ip(request)}", 120, 600, "Too many tries from this network. Wait a few minutes and open the link again.")
    p = await kp.by_token(token)
    if not p:
        raise HTTPException(404, "This link isn't working. It may have expired — ask for a new one.")
    return p


@public.get("/{token}")
async def form(token: str, request: Request):
    p = await _from_link(token, request)
    if not p.get("opened_at"):
        await db().key_people.update_one({"_id": p["_id"]}, {"$set": {"opened_at": now()}})
    business = await _business_name(p["ws"])
    saved = {a["key"]: a["a"] for a in p.get("answers") or []}
    return {"business": business, "first_name": (p.get("name") or "").split(" ")[0], "role": p.get("role", ""),
            "note": NOTE.format(business=business), "questions": p["questions"], "answers": saved,
            "submitted": bool(p.get("submitted_at")), "expires_at": _iso(p.get("link_expires_at")), "max_answer": kp.MAX_ANSWER}


@public.put("/{token}")
async def save(token: str, body: AnswersIn, request: Request):
    p = await _from_link(token, request)
    await rate_limit(f"kp-link:{p['_id']}", 200, 3600, "That's a lot of saves. Wait a minute and try again.")
    if p.get("submitted_at"):
        raise HTTPException(409, "Your answers are already sent. Thank you! If you need to change something, ask for the link to be reopened.")
    ans = kp.clean_answers(p["questions"], body.answers)
    patch = {"answers": ans, "draft_saved_at": now(), "updated_at": now()}
    if body.submit:
        if sum(1 for a in ans if a["a"]) < 3:
            raise HTTPException(400, "Please answer at least 3 questions before sending.")
        patch.update(submitted_at=now(), submitted_by="employee")
    await db().key_people.update_one({"_id": p["_id"], "submitted_at": {"$in": [None]}}, {"$set": patch})
    if body.submit:
        await notify(p["ws"], f"{p.get('name', 'Your employee')} has sent their answers. Build their agent skill in Key-person knowledge.")
        await track("key_person_answered", p["ws"], by="employee")
    return {"ok": True, "submitted": bool(body.submit), "saved_at": _iso(patch["draft_saved_at"])}


@public.get("/{token}/questions.pdf")
async def public_pdf(token: str, request: Request):
    return await _pdf(await _from_link(token, request))


async def ensure_indexes() -> None:
    await kp.ensure_indexes()
