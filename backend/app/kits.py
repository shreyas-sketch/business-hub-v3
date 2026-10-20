"""
The hub's working documents ("kits"): one engine for every AI-written document an owner keeps and edits —
SOPs, hiring kits, decision logs, role clarity, culture charters, competence plans and monthly reviews.
Each kind declares its plan feature, who may read and write it, the AI job that drafts it, and the shape it must keep.
"""
from dataclasses import dataclass
from typing import Any, Callable, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError

from .ai import kit_tasks
from .db import db, now, public
from .services import new_id, run_ai, track

S = lambda n: Field(default="", max_length=n)  # noqa: E731


# ───────────────────────── content shapes ─────────────────────────
class Step(BaseModel):
    title: str = Field(max_length=160)
    detail: str = S(800)


class Script(BaseModel):
    label: str = Field(max_length=120)
    text: str = S(900)


class SopContent(BaseModel):
    title: str = Field(min_length=3, max_length=140)
    purpose: str = S(500)
    owner_role: str = S(100)
    when: str = S(300)
    steps: list[Step] = Field(default_factory=list, max_length=25)
    checklist: list[str] = Field(default_factory=list, max_length=15)
    mistakes: list[str] = Field(default_factory=list, max_length=10)
    # v2.1 scenario SOPs and the whole-business set
    trigger: str = S(300)                        # what starts it ("a customer complains that delivery is late")
    scripts: list[Script] = Field(default_factory=list, max_length=8)   # the words and messages to use
    done_when: str = S(400)                      # what "done" looks like
    escalation: str = S(400)                     # who to bring in, and when
    function: str = S(40)                        # Sales, Marketing, Operations/Delivery, Accounts, HR, Customer care


class SopInputs(BaseModel):
    process: str = Field(min_length=3, max_length=300)
    notes: str = S(8000)
    mode: Literal["process", "scenario"] = "process"   # scenario: a situation ("new staff member's first day")
    function: str = S(40)


class Question(BaseModel):
    q: str = Field(max_length=300)
    look_for: str = S(300)


class ScoreRow(BaseModel):
    criterion: str = Field(max_length=120)
    weight: int = Field(default=3, ge=1, le=5)


class JdContent(BaseModel):
    role: str = Field(min_length=2, max_length=100)
    summary: str = S(800)
    responsibilities: list[str] = Field(default_factory=list, max_length=14)
    requirements: list[str] = Field(default_factory=list, max_length=12)
    kpis: list[str] = Field(default_factory=list, max_length=8)
    interview_questions: list[Question] = Field(default_factory=list, max_length=14)
    scorecard: list[ScoreRow] = Field(default_factory=list, max_length=10)
    salary_note: str = S(200)


class JdInputs(BaseModel):
    role: str = Field(min_length=2, max_length=80)
    notes: str = S(2000)


class DecisionItem(BaseModel):
    task: str = Field(max_length=200)
    verdict: Literal["automate", "delegate", "keep"] = "delegate"
    reason: str = S(300)
    next_step: str = S(300)
    status: Literal["todo", "doing", "done"] = "todo"


class DecisionContent(BaseModel):
    items: list[DecisionItem] = Field(default_factory=list, max_length=30)


class DecisionInputs(BaseModel):
    tasks: str = Field(min_length=3, max_length=4000)


class Responsibility(BaseModel):
    name: str = Field(max_length=160)
    tasks: list[str] = Field(default_factory=list, max_length=15)


class RoleContent(BaseModel):
    title: str = Field(min_length=2, max_length=100)
    person_name: str = S(80)
    function: str = S(60)
    level: Literal["creator", "manager", "doer"] = "doer"
    definition: str = S(600)
    responsibilities: list[Responsibility] = Field(default_factory=list, max_length=6)
    metrics: list[str] = Field(default_factory=list, max_length=10)
    reports_to: str = S(80)
    version: int = Field(default=1, ge=1, le=20)


class RoleInputs(BaseModel):
    role: str = Field(min_length=2, max_length=80)
    person_name: str = S(80)
    function: str = S(60)
    notes: str = S(3000)


class Situation(BaseModel):
    heading: Literal["relationships", "energy", "commitment", "performance"] = "relationships"
    situation: str = Field(max_length=300)
    our_way: str = S(500)
    not_our_way: str = S(500)


class CultureContent(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    purpose: str = S(400)
    situations: list[Situation] = Field(default_factory=list, max_length=30)


class CultureInputs(BaseModel):
    notes: str = S(4000)


class Attributes(BaseModel):
    skills: list[str] = Field(default_factory=list, max_length=8)
    knowledge: list[str] = Field(default_factory=list, max_length=8)
    self_image: list[str] = Field(default_factory=list, max_length=8)
    traits: list[str] = Field(default_factory=list, max_length=8)
    motives: list[str] = Field(default_factory=list, max_length=8)


class PreRow(BaseModel):
    attribute: str = Field(max_length=200)
    people: str = S(300)
    resources: str = S(300)
    experiences: str = S(300)
    by_when: str = S(60)


class CompetenceContent(BaseModel):
    role: str = Field(min_length=2, max_length=100)
    attributes: Attributes = Field(default_factory=Attributes)
    plan: list[PreRow] = Field(default_factory=list, max_length=10)


class CompetenceInputs(BaseModel):
    role: str = Field(min_length=2, max_length=80)
    notes: str = S(3000)


class Recommendation(BaseModel):
    action: str = Field(max_length=300)
    why: str = S(300)


class ReviewContent(BaseModel):
    period: str = S(40)
    headline: str = Field(min_length=2, max_length=300)
    numbers: dict[str, str] = Field(default_factory=dict)
    wins: list[str] = Field(default_factory=list, max_length=8)
    concerns: list[str] = Field(default_factory=list, max_length=8)
    recommendations: list[Recommendation] = Field(default_factory=list, max_length=8)
    focus_next_month: str = S(400)


class ReviewInputs(BaseModel):
    period: str = S(40)
    data: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class Kind:
    feature: str
    label: str
    read_role: str
    write_role: str
    task: Callable
    content: type[BaseModel]
    inputs: type[BaseModel]
    title: Callable[[dict], str]


KINDS: dict[str, Kind] = {
    "sop": Kind("sop_library", "SOP", "staff", "manager", kit_tasks.sop, SopContent, SopInputs, lambda c: c["title"]),
    "jd": Kind("hiring", "Hiring kit", "manager", "manager", kit_tasks.jd, JdContent, JdInputs, lambda c: c["role"]),
    "decision": Kind("decision_log", "Decision log", "owner", "owner", kit_tasks.decision, DecisionContent, DecisionInputs,
                     lambda c: f"{len(c['items'])} tasks reviewed"),
    "role": Kind("role_clarity", "Role clarity", "staff", "owner", kit_tasks.role, RoleContent, RoleInputs,
                 lambda c: c["title"] + (f" — {c['person_name']}" if c.get("person_name") else "")),
    "culture": Kind("culture_plan", "Culture charter", "staff", "owner", kit_tasks.culture, CultureContent, CultureInputs, lambda c: c["name"]),
    "competence": Kind("competence_plan", "Competence plan", "staff", "owner", kit_tasks.competence, CompetenceContent, CompetenceInputs, lambda c: c["role"]),
    "review": Kind("monthly_review", "Monthly review", "manager", "owner", kit_tasks.review, ReviewContent, ReviewInputs,
                   lambda c: f"Review — {c.get('period') or 'this month'}"),
}


def kind_of(kind: str) -> Kind:
    if kind not in KINDS:
        raise HTTPException(404, "Not found")
    return KINDS[kind]


def _blank_nulls(value):
    if isinstance(value, dict):
        return {k: _blank_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_blank_nulls(v) for v in value if v is not None]
    return value


def shape(k: Kind, raw: dict) -> dict:
    """Validates AI or owner content against the kind's shape, trimming over-long lists instead of failing."""
    data = _blank_nulls(raw or {})
    for name, f in k.content.model_fields.items():
        limit = next((m.max_length for m in f.metadata if hasattr(m, "max_length")), None)
        if isinstance(data.get(name), list) and limit:
            data[name] = data[name][:limit]
    if k is KINDS["review"] and isinstance(data.get("numbers"), dict):
        data["numbers"] = {str(a)[:60]: str(b)[:120] for a, b in list(data["numbers"].items())[:16]}
    return k.content.model_validate(data).model_dump()


async def generate(owner: dict, ws: str, actor: str, kind: str, inputs: dict, profile: dict, extra: dict | None = None) -> dict:
    """Drafts a document with AI (one run), saves it and returns it. Used by the API, the agents and the office."""
    k = kind_of(kind)
    clean_inputs = k.inputs.model_validate(inputs).model_dump()
    content = await run_ai(owner, f"kit:{kind}", k.task(profile, clean_inputs), f"{k.label} — {_label_for(kind, clean_inputs)}",
                           check=lambda r: shape(k, r))
    doc = {"_id": new_id(), "ws": ws, "kind": kind, "title": k.title(content)[:160], "content": content,
           "inputs": {a: b for a, b in clean_inputs.items() if a != "data"}, "created_by": actor, "created_at": now(), "updated_at": now(),
           **(extra or {})}
    await db().kits.insert_one(doc)
    await track("kit_created", ws, kind=kind)
    return doc


def _label_for(kind: str, inputs: dict) -> str:
    return (inputs.get("process") or inputs.get("role") or inputs.get("period") or "new")[:80]


async def update(ws: str, actor: str, kind: str, kit_id: str, content: dict) -> dict:
    k = kind_of(kind)
    try:
        clean = shape(k, content)
    except ValidationError:
        raise HTTPException(400, "Some fields are empty or too long. Please check and save again.")
    before = await db().kits.find_one({"_id": kit_id, "ws": ws, "kind": kind}, {"content": 1, "title": 1, "updated_at": 1, "updated_by": 1, "created_by": 1})
    if before and before.get("content") != clean:   # the previous version is kept
        await db().kit_versions.insert_one({"_id": new_id(), "kit_id": kit_id, "ws": ws, "kind": kind, "title": before.get("title", ""),
                                            "content": before["content"], "at": before.get("updated_at") or now(),
                                            "by": before.get("updated_by") or before.get("created_by"), "saved_at": now()})
    r = await db().kits.update_one({"_id": kit_id, "ws": ws, "kind": kind},
                                   {"$set": {"content": clean, "title": k.title(clean)[:160], "updated_at": now(), "updated_by": actor}})
    if not r.matched_count:
        raise HTTPException(404, "Not found")
    return await db().kits.find_one({"_id": kit_id})


def view(doc: dict) -> dict:
    return public(doc)
