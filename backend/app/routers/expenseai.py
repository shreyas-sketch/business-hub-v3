"""
Expenses & bank statement — analysis, AI category suggestions and AI advice (v2.3, Data engine). See app/expense_ai.py.

    GET  /api/expenses-ai/analysis?months=6                    the picture, worked out by the hub (no AI)  [manager]
    GET  /api/expenses-ai/advice                               the last AI advice                         [owner]
    POST /api/expenses-ai/advice {months, include_salaries}    new AI advice from the totals (1 AI token) [owner]
    GET  /api/expenses-ai/statement/{id}/suggestions           AI category suggestions for a statement    [owner]
    POST /api/expenses-ai/statement/{id}/suggest               ask the AI to sort the unsorted payees (1 AI token) [owner]

What the AI sees, and only when the owner taps for it:
- suggestions: the masked payee names of the statement payments the rules left as "Other" (no amounts, dates, balances or
  account numbers) and the business's category names;
- advice: the totals from the analysis (spend by month and category, the payments that repeat, sales by month) — never a
  statement line; salaries only when the owner ticks "include salaries". Numbers it wasn't given are taken out.
expense_advice {_id: ws, at, by, months, include_salaries, advice {headline, findings, recommendations}}
"""
import hashlib

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import bankstatement as bs, expense_ai as ea
from ..ai import expense_tasks
from ..context import Ctx, feature
from ..db import db, now
from ..services import run_ai, runs_status, track
from . import bankreader as br, expenses as ex

router = APIRouter(prefix="/api/expenses-ai")
manager_ctx = feature("expenses", "manager")
owner_ctx = feature("expenses", "owner")
statement_ctx = feature("bank_reader", "owner")
MAX_PAYEES = 60


def _h(payee_key: str) -> str:
    """Payee keys can hold dots and spaces, which a stored field name can't: kept by a short fingerprint instead."""
    return hashlib.sha1(payee_key.encode()).hexdigest()[:16]


async def _business(ws: str) -> str:
    b = await db().businesses.find_one({"owner_id": ws}, {"name": 1, "industry": 1}) or {}
    return f"{b.get('name') or 'A business'} — {b.get('industry') or ''}".strip(" —")


@router.get("/analysis")
async def analysis(months: int = Query(default=6, ge=ea.MIN_MONTHS, le=ea.MAX_MONTHS), ctx: Ctx = Depends(manager_ctx)):
    return await ea.analysis(ctx.ws, months)


def _advice_view(doc: dict | None) -> dict | None:
    if not doc:
        return None
    return {"at": doc["at"].isoformat(), "months": doc.get("months"), "include_salaries": doc.get("include_salaries", False), **doc["advice"]}


@router.get("/advice")
async def last_advice(ctx: Ctx = Depends(owner_ctx)):
    return {"advice": _advice_view(await db().expense_advice.find_one({"_id": ctx.ws})), "cost": 1,
            "tokens_left": (await runs_status(ctx.owner))["left"]}


class AdviceIn(BaseModel):
    months: int = Field(default=6, ge=ea.MIN_MONTHS, le=ea.MAX_MONTHS)
    include_salaries: bool = False


@router.post("/advice")
async def new_advice(body: AdviceIn, ctx: Ctx = Depends(owner_ctx)):
    an = await ea.analysis(ctx.ws, body.months, include_salaries=body.include_salaries)
    if an["total"] <= 0:
        raise HTTPException(400, "There are no expenses in these months yet. Add some, or bring them in from a bank statement, then ask again.")
    text, figs, allowed = ea.facts(an, await _business(ctx.ws))
    advice = await run_ai(ctx.owner, "expenses:advice", expense_tasks.advice(await _business(ctx.ws), text, figs), "Money review",
                          save_output=False, check=lambda r: ea.shape_advice(r, allowed))
    doc = {"_id": ctx.ws, "at": now(), "by": ctx.actor, "months": body.months, "include_salaries": body.include_salaries, "advice": advice}
    await db().expense_advice.replace_one({"_id": ctx.ws}, doc, upsert=True)
    await track("expense_advice", ctx.ws, months=body.months, salaries=body.include_salaries)
    return {"advice": _advice_view(doc), "cost": 1, "tokens_left": (await runs_status(ctx.owner))["left"]}


# ───────────────────────── bank statement: suggested categories ─────────────────────────
def _unsorted(imp: dict, remembered: dict) -> dict[str, dict]:
    """The payees the rules couldn't sort (left as Other), each once: payee key → {payee label, refs}."""
    out: dict[str, dict] = {}
    for t in imp.get("transactions") or []:
        if t.get("kind") != "debit" or t.get("category") != "other":
            continue
        key = br._payee_key(t)
        if not key or key in remembered:
            continue
        label = (t.get("payee") or bs._label(t.get("narration") or "") or "").strip()
        if len(label) < 3:
            continue
        x = out.setdefault(key, {"payee": bs.mask(label)[:60], "refs": []})
        x["refs"].append(t["ref"])
    return out


async def _suggestion_view(ctx: Ctx, imp: dict) -> dict:
    kind, custom, _ = await ex._setup(ctx.ws)
    labels = {c["key"]: c["label"] for c in ex.categories_for(kind, custom)}
    saved = imp.get("ai_suggestions") or {}
    by_ref = {}
    open_ = _unsorted(imp, await br._remembered(ctx.ws))
    for key, x in open_.items():
        cat = saved.get(_h(key))
        if cat and cat in labels:
            for ref in x["refs"]:
                by_ref[ref] = {"category": cat, "label": labels[cat]}
    return {"suggestions": by_ref, "unsorted": sum(len(x["refs"]) for x in open_.values()), "unsorted_payees": len(open_),
            "asked": bool(imp.get("ai_suggested_at")), "cost": 1, "tokens_left": (await runs_status(ctx.owner))["left"]}


@router.get("/statement/{imp_id}/suggestions")
async def suggestions(imp_id: str, ctx: Ctx = Depends(statement_ctx)):
    return await _suggestion_view(ctx, await br._get(ctx, imp_id))


@router.post("/statement/{imp_id}/suggest")
async def suggest(imp_id: str, ctx: Ctx = Depends(statement_ctx)):
    imp = await br._get(ctx, imp_id)
    open_ = _unsorted(imp, await br._remembered(ctx.ws))
    if not open_:
        raise HTTPException(400, "Every payment in this statement already has a category.")
    kind, custom, _ = await ex._setup(ctx.ws)
    cats = [c for c in ex.categories_for(kind, custom) if c["key"] != "other"]
    keys = list(open_)[:MAX_PAYEES]
    payees = [{"id": i + 1, "payee": open_[k]["payee"]} for i, k in enumerate(keys)]
    allowed = {c["key"] for c in cats}

    def shape(r: dict) -> dict:
        out = {}
        for s in (r.get("suggestions") or []) if isinstance(r, dict) else []:
            try:
                i = int(s.get("id")) - 1
            except (TypeError, ValueError, AttributeError):
                continue
            if 0 <= i < len(keys) and s.get("category") in allowed:
                out[keys[i]] = s["category"]
        return out

    found = await run_ai(ctx.owner, "expenses:suggest", expense_tasks.suggest_categories(await _business(ctx.ws), payees, cats),
                         "Bank statement categories", save_output=False, check=shape)
    await db().bank_imports.update_one({"_id": imp["_id"], "ws": ctx.ws}, {"$set": {**{f"ai_suggestions.{_h(k)}": v for k, v in found.items()},
                                                                                     "ai_suggested_at": now()}})
    await track("bank_ai_suggest", ctx.ws, payees=len(keys), suggested=len(found))
    return await _suggestion_view(ctx, await br._get(ctx, imp_id))


async def ensure_indexes() -> None:
    pass
