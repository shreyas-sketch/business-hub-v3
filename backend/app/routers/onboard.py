"""
Getting started at the workshop, and the Business AI Score.

- /api/onboarding/website: the owner pastes their website; the hub reads it (home, about, services, contact) and fills
  the Business Brain for them to check. The website's logo, colour and 8 health checks are kept for the score.
- /api/onboarding/words: a voice-note transcript (or anything typed) → the same draft profile.
- /api/score: 10 questions (+ the website checks) → a score out of 100 and the top 3 fixes; a share page at /score/<token>.
  The first score is free; re-taking it every month is part of Membership.
"""
import re
import secrets

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .. import day1, fetch, plans, score as scoring
from ..ai import onboard_tasks
from ..config import settings
from ..context import Ctx, owner_ctx
from ..db import db, now, public
from ..security import rate_limit
from ..services import run_ai, track

router = APIRouter()


class WebsiteIn(BaseModel):
    url: str = Field(min_length=4, max_length=500)


class WordsIn(BaseModel):
    text: str = Field(min_length=20, max_length=8000)


class ScoreIn(BaseModel):
    answers: dict[str, str]
    numbers: dict[str, int | float | str | None] | None = None   # the 6 "your numbers" answers (revenue band, team, enquiries…)


def _draft(raw: dict) -> dict:
    """AI profile → the Business Brain form's shape, trimmed to its limits. Nothing invented is added here."""
    if not isinstance(raw, dict):
        raise ValueError("not an object")
    s = lambda k, n: re.sub(r"\s+\n", "\n", str(raw.get(k) or "")).strip()[:n]  # noqa: E731
    offers = []
    for o in (raw.get("offers") or [])[:12]:
        if isinstance(o, dict) and str(o.get("name") or "").strip():
            offers.append({"name": str(o["name"]).strip()[:80], "price": str(o.get("price") or "").strip()[:40],
                           "unit": str(o.get("unit") or "").strip()[:30]})
    wa = re.sub(r"\D", "", str(raw.get("whatsapp") or ""))[-10:]
    return {"name": s("name", 80), "city": s("city", 80), "industry": s("industry", 200), "offers": offers,
            "ideal_customer": s("ideal_customer", 300), "why_us": s("why_us", 500), "proof": s("proof", 500),
            "whatsapp": wa if len(wa) == 10 else "", "email": s("email", 120), "language": s("language", 30) or "English"}


@router.post("/api/onboarding/website")
async def from_website(body: WebsiteIn, ctx: Ctx = Depends(owner_ctx)):
    await rate_limit(f"read-site:{ctx.ws}", 8, 3600, "That's a lot of websites in one hour. Please fill the form, or try again later.")
    try:
        facts = await fetch.read_site(body.url)
    except fetch.FetchError as e:
        raise HTTPException(400, str(e))
    if len(facts.text) < 80 and not facts.title:
        raise HTTPException(400, "We couldn't read much from that website (it may be built from pictures). Please fill the short form instead.")
    draft = await run_ai(ctx.owner, "read_website", onboard_tasks.brain_from_site(facts), f"Read your website — {facts.url[:80]}",
                         save_output=False, check=_draft)
    scan = {"_id": ctx.ws, "url": facts.url, "checks": facts.checks, "logo": facts.logo, "colour": facts.colour,
            "title": facts.title, "at": now()}
    await db().site_scans.replace_one({"_id": ctx.ws}, scan, upsert=True)
    await track("website_read", ctx.ws)
    return {"profile": draft, "logo": facts.logo, "colour": facts.colour, "url": facts.url, "checks": facts.checks}


@router.post("/api/onboarding/words")
async def from_words(body: WordsIn, ctx: Ctx = Depends(owner_ctx)):
    draft = await run_ai(ctx.owner, "read_words", onboard_tasks.brain_from_words(body.text), "Your business, in your words",
                         save_output=False, check=_draft)
    await track("voice_onboarding", ctx.ws)
    return {"profile": draft}


# ───────────────────────── Business AI Score ─────────────────────────
@router.get("/api/score")
async def get_score(ctx: Ctx = Depends(owner_ctx)):
    history = [public(s) async for s in db().scores.find({"ws": ctx.ws}).sort("at", -1).limit(24)]
    scan = await db().site_scans.find_one({"_id": ctx.ws})
    latest = history[0] if history else None
    can_retake = not latest or ctx.has("score_recheck")
    if latest:
        latest["share_url"] = f"{settings.app_url}/score/{latest['token']}"
    if latest:
        latest["gap"] = await _gap_line(latest.get("numbers"))
    return {"questions": [{"key": k, "area": a, "q": q} for k, a, q, *_ in scoring.QUESTIONS],
            "numbers": [{**{k: v for k, v in n.items() if k != "choices"},
                         **({"choices": [{"value": a, "label": b} for a, b in n["choices"]]} if n["kind"] == "choice" else {})} for n in day1.NUMBERS],
            "website_checks": [{"key": k, "label": l} for k, l, _ in scoring.WEBSITE_CHECKS],
            "scan": {"url": scan["url"], "checks": scan["checks"], "at": scan["at"].isoformat()} if scan else None,
            "latest": latest, "history": history, "can_retake": can_retake,
            "recheck_tier": plans.PLANS[plans.tier_of("score_recheck")]["name"]}


@router.post("/api/score")
async def take_score(body: ScoreIn, ctx: Ctx = Depends(owner_ctx)):
    if await db().scores.find_one({"ws": ctx.ws}, {"_id": 1}) and not ctx.has("score_recheck"):
        ctx.require("score_recheck")
    keys = [k for k, *_ in scoring.QUESTIONS]
    answers = {k: body.answers.get(k) for k in keys}
    if any(v not in scoring.ANSWER_POINTS for v in answers.values()):
        raise HTTPException(400, "Answer every question: yes, partly or no.")
    try:   # "your numbers" are asked with the questions but not scored, so the score stays comparable month to month
        numbers = day1.clean_numbers(body.numbers, required=False)
    except day1.InputError as e:
        raise HTTPException(400, str(e))
    scan = await db().site_scans.find_one({"_id": ctx.ws})
    website = {"url": scan["url"], "checks": scan["checks"]} if scan else None
    result = scoring.compute(answers, website)
    prev = await db().scores.find_one({"ws": ctx.ws}, sort=[("at", -1)])
    b = await db().businesses.find_one({"owner_id": ctx.ws}, {"name": 1, "city": 1}) or {}
    doc = {"_id": secrets.token_hex(8), "ws": ctx.ws, "at": now(), "month": now().strftime("%Y-%m"), "answers": answers,
           "website": website, **result, "change": (result["score"] - prev["score"]) if prev else None,
           "token": secrets.token_urlsafe(9), "business": b.get("name", ""), "city": b.get("city", ""), "numbers": numbers}
    await db().scores.insert_one(doc)
    await track("score_taken", ctx.ws, score=result["score"])
    out = public(doc)
    out["share_url"] = f"{settings.app_url}/score/{doc['token']}"
    out["gap"] = await _gap_line(numbers)
    return out


async def _gap_line(numbers: dict | None) -> dict | None:
    """What you're missing, from the owner's own numbers (the same arithmetic as the Day-1 report)."""
    if not numbers or not all(numbers.get(k) not in (None, "") for k in ("enquiries", "close_rate", "aov")):
        return None
    cfg = await day1.load_config()
    g = day1.gap(numbers, int(cfg["constants"]["cold_rate_pct"]))
    return {"year": g["gap_year"], "month": g["gap_month"], "cold_rate_pct": g["cold_rate_pct"], "lost_month": g["lost_month"],
            "line": f"About {day1.rupees(g['gap_year'])} a year in enquiries that go cold, at your numbers"}


@router.get("/score/{token}", response_class=HTMLResponse)
async def score_page(token: str):
    """The card an owner shares: their score, the three areas to fix, and a link to get your own free score."""
    from .public import not_found, templates
    s = await db().scores.find_one({"token": token[:40]})
    if not s:
        return not_found()
    owner = await db().users.find_one({"_id": s["ws"]}, {"ref_code": 1, "disabled": 1})
    if not owner or owner.get("disabled"):
        return not_found()
    await track("invite_visit", s["ws"], src="score")
    html = templates.get_template("score.html").render(
        s=s, program=settings.program_name, join=f"{settings.app_url}/join?ref={owner['ref_code']}&src=score",
        url=f"{settings.app_url}/score/{token}")
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


async def ensure_indexes() -> None:
    await db().scores.create_index([("ws", 1), ("at", -1)])
    await db().scores.create_index("token", unique=True)
