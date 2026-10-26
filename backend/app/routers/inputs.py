"""
Shared inputs (v2.3, app/shared_inputs.py) and the website built from them.

GET  /api/inputs                      what's in use, pre-filled from the Knowledge Hub, plus the competitor summary's state
PUT  /api/inputs                      save: their website, a competitor website, the 4 answers (all optional)
POST /api/inputs/competitor/read      read the competitor's website now (1 AI token; at most once per 30 minutes per website)
POST /api/inputs/website/build        the website draft from their own site ("website") or the 4 answers ("answers")

The website draft is never published here: the owner previews it (desktop and phone) and publishes on the Website page.
"""
import re
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from pymongo.errors import DuplicateKeyError

from .. import competitor_watch as cw
from .. import fetch, plans, shared_inputs as si
from ..ai import site_tasks
from ..ai.tasks import profile_text
from ..context import Ctx, owner_ctx
from ..db import db, now
from ..security import rate_limit
from ..services import new_id, run_ai, track

router = APIRouter(prefix="/api/inputs")


def _url(v: str) -> str:
    v = (v or "").strip()
    if not v:
        return ""
    try:
        return fetch.normalise_url(v)
    except fetch.FetchError:
        raise ValueError("Enter the website address, like www.yourbusiness.in")


class InputsIn(BaseModel):
    website_url: str | None = Field(default=None, max_length=300)
    has_website: bool | None = None
    competitors: list[str] | None = Field(default=None, max_length=si.MAX_COMPETITORS)
    about: str | None = Field(default=None, max_length=si.MAX_LEN["about"])
    offers: str | None = Field(default=None, max_length=si.MAX_LEN["offers"])
    audience: str | None = Field(default=None, max_length=si.MAX_LEN["audience"])
    pains: str | None = Field(default=None, max_length=si.MAX_LEN["pains"])

    @field_validator("website_url")
    @classmethod
    def own(cls, v):
        return v if v is None else _url(v)

    @field_validator("competitors")
    @classmethod
    def rivals(cls, v):
        if v is None:
            return v
        out = []
        for x in v:
            u = _url(x)
            if u and u not in out:
                out.append(u)
        return out


async def inputs_view(ws: str, owner: dict) -> dict:
    b = await db().businesses.find_one({"owner_id": ws}) or {}
    inp = await si.get_inputs(ws, b)
    from .competitors import summary_doc
    doc = await summary_doc(ws)
    comp = None
    if doc:
        comp = {"url": doc.get("url"), "host": doc.get("host"), "status": doc.get("status"), "read_at": doc.get("at"),
                "error": doc.get("error"), "matches": doc.get("url") == inp["competitor_url"],
                "summary": doc.get("summary") if doc.get("status") == "ok" else None}
    return {"inputs": inp, "questions": si.QUESTIONS, "hints": si.HINTS, "competitor": comp,
            "can_read_competitor": plans.has(owner, "competitor_watch"), "answered": si.answered(inp)}


@router.get("")
async def get_inputs(ctx: Ctx = Depends(owner_ctx)):
    return await inputs_view(ctx.ws, ctx.owner)


@router.put("")
async def save_inputs(body: InputsIn, ctx: Ctx = Depends(owner_ctx)):
    b = await db().businesses.find_one({"owner_id": ctx.ws})
    if not b:
        raise HTTPException(400, "Tell us about your business first")
    patch = {f"inputs.{k}": (re.sub(r"[ \t]+", " ", v).strip() if isinstance(v, str) else v)
             for k, v in body.model_dump(exclude_none=True).items()}
    if body.website_url and body.has_website is None:
        patch["inputs.has_website"] = True
    if any(k in body.model_fields_set and getattr(body, k) is not None for k in si.QUESTIONS):
        patch["inputs.confirmed_at"] = now()
    if patch:
        patch["inputs.updated_at"] = now()
        await db().businesses.update_one({"_id": b["_id"]}, {"$set": patch})
        await track("inputs_saved", ctx.ws, fields=sorted(k.split(".", 1)[1] for k in patch))
    return await inputs_view(ctx.ws, ctx.owner)


async def _read_if_needed(owner: dict, url: str) -> None:
    """Reads the competitor when the hub hasn't yet (or it's a new website, or a month old). A site that can't be read is
    never a reason to stop building: the build goes on without it."""
    from .competitors import refresh_summary
    d = await db().competitor_summaries.find_one({"_id": owner["_id"]}, {"url": 1, "at": 1, "at_try": 1, "status": 1}) or {}
    if d.get("url") == url and d.get("status") == "ok" and d.get("at") and now() - d["at"] < timedelta(days=cw.SUMMARY_MAX_AGE_DAYS):
        return
    if d.get("url") == url and d.get("at_try") and now() - d["at_try"] < timedelta(minutes=30):
        return
    if not plans.has(owner, "competitor_watch"):
        return
    try:
        await refresh_summary(owner, url, "inputs")
    except HTTPException as e:
        if e.status_code == 402:
            raise


@router.post("/competitor/read")
async def read_competitor(ctx: Ctx = Depends(owner_ctx)):
    if not plans.has(ctx.owner, "competitor_watch"):
        raise HTTPException(403, "Reading a competitor's website isn't part of your plan yet.")
    inp = await si.get_inputs(ctx.ws)
    if not inp["competitor_url"]:
        raise HTTPException(400, "Add a competitor's website first.")
    d = await db().competitor_summaries.find_one({"_id": ctx.ws}, {"url": 1, "at_try": 1}) or {}
    if d.get("url") == inp["competitor_url"] and d.get("at_try") and now() - d["at_try"] < timedelta(minutes=30):
        raise HTTPException(429, "That website was read a few minutes ago. Try again in half an hour.")
    from .competitors import refresh_summary
    await refresh_summary(ctx.owner, inp["competitor_url"], "manual")
    return await inputs_view(ctx.ws, ctx.owner)


# ───────────────────────── the website from the inputs ─────────────────────────
class BuildIn(BaseModel):
    source: str = Field(default="answers", pattern="^(website|answers)$")


NUM = re.compile(r"\d+(?:[.,]\d+)*")


def _nums(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in NUM.findall(text or "")}


def _numbers_guard(content: dict, allowed_text: str) -> dict:
    """No number the owner didn't give: an offer price not in the owner's words is emptied, and a sentence with a number
    that isn't in the profile, the answers or their own website is taken out."""
    allowed = _nums(allowed_text)

    def clean(s: str) -> str:
        if not (_nums(s) - allowed):
            return s
        return " ".join(x for x in re.split(r"(?<=[.!?])\s+", s) if not (_nums(x) - allowed)).strip()

    out = dict(content)
    for k in ("headline", "subheadline", "about", "cta"):
        out[k] = clean(str(out.get(k) or ""))
    out["offers"] = [{**o, "name": clean(o.get("name", "")) or o.get("name", ""), "description": clean(o.get("description", "")),
                      "price": o.get("price", "") if not (_nums(o.get("price", "")) - allowed) else ""} for o in out.get("offers") or []]
    out["offers"] = [o for o in out["offers"] if not (_nums(o["name"]) - allowed)]
    out["why"] = [{"title": clean(x.get("title", "")), "text": clean(x.get("text", ""))} for x in out.get("why") or []]
    out["why"] = [x for x in out["why"] if x["title"]]
    out["steps"] = [{"title": clean(x.get("title", "")), "text": clean(x.get("text", ""))} for x in out.get("steps") or []]
    out["steps"] = [x for x in out["steps"] if x["title"]]
    out["faq"] = [{"q": clean(x.get("q", "")), "a": clean(x.get("a", ""))} for x in out.get("faq") or []]
    out["faq"] = [x for x in out["faq"] if x["q"] and x["a"]]
    return out


async def _brand_bits(ws: str, b: dict) -> dict:
    kit = await db().brand_kits.find_one({"_id": ws}, {"messages": 1, "design": 1}) or {}
    m = kit.get("messages") or {}
    web, core = m.get("website") or {}, m.get("core") or {}
    brand = b.get("brand") or {}
    return {"headline": web.get("headline", ""), "message": core.get("one_line") or brand.get("message", ""),
            "pitch": core.get("pitch") or brand.get("pitch", ""), "primary": ((kit.get("design") or {}).get("colors") or {}).get("primary")}


async def write_content(owner: dict, p: dict, inp: dict | None = None, own_text: str = "", title: str = "") -> tuple[dict, dict | None, dict]:
    """One AI token: the website's words from the profile, the answers (and their own site's text when given), made to stand
    out against the competitor summary — with the no-copy and no-invented-numbers guards. (content, competitor, brand)."""
    from .hub import SiteContent, _site_from_ai
    ws = owner["_id"]
    inp = inp or await si.get_inputs(ws, p)
    comp = await cw.competitor_summary(ws)
    guard = await cw.competitor_guard(ws)
    brand = await _brand_bits(ws, p)
    allowed = "\n".join((profile_text(p), si.answers_text(inp), own_text))

    def shape(result: dict) -> dict:
        c = _site_from_ai(result)
        c = _numbers_guard(guard(c), allowed)
        if len(c.get("headline") or "") < 3:
            c["headline"] = p["name"][:120]
        return SiteContent.model_validate(c).model_dump()

    content = await run_ai(owner, "site", site_tasks.site_from_inputs(p, brand, inp, own_text, comp, cw.summary_text(comp)),
                           title or f"Website refreshed from your Knowledge Hub — {p['name']}", check=shape)
    return content, comp, brand


@router.post("/website/build")
async def build_website(body: BuildIn, ctx: Ctx = Depends(owner_ctx)):
    """One AI token (plus one the first time the competitor's website is read). The draft replaces the page's words, as
    'Rewrite with AI' does; nothing is published."""
    from .hub import ACCENTS, _slugify, _unique_slug, profile_of, site_view
    if not plans.has(ctx.owner, "website"):
        raise HTTPException(403, "The website isn't part of your plan yet.")
    await rate_limit(f"site-build:{ctx.ws}", 10, 3600, "That's a lot of rebuilds in one hour. Edit the page, or try again later.")
    p = await profile_of(ctx.ws)
    inp = await si.get_inputs(ctx.ws, p)
    own_text = ""
    if body.source == "website":
        if not inp["website_url"]:
            raise HTTPException(400, "Add your website's address first.")
        try:
            facts = await fetch.read_site(inp["website_url"])
        except fetch.FetchError as e:
            raise HTTPException(400, f"{e} You can build it from your answers instead.")
        own_text = "\n".join(x for x in (f"TITLE: {facts.title}", f"DESCRIPTION: {facts.description}",
                                         "HEADINGS: " + " | ".join(facts.headings[:25]), facts.text[:9000]) if x)
        if len(facts.text) < 80 and not facts.title:
            raise HTTPException(400, "We couldn't read much from your website (it may be built from pictures). Build it from your answers instead.")
        await db().site_scans.update_one({"_id": ctx.ws}, {"$set": {"url": facts.url, "checks": facts.checks, "logo": facts.logo,
                                                                   "colour": facts.colour, "title": facts.title, "at": now()}}, upsert=True)
    elif not si.answered(inp):
        raise HTTPException(400, "Answer the questions first: what your business is about, and what you sell or who you sell to.")
    if inp["competitor_url"]:
        await _read_if_needed(ctx.owner, inp["competitor_url"])
    content, comp, brand = await write_content(ctx.owner, p, inp, own_text,
                                               f"Website — {p['name']}" + (" (from your website)" if own_text else " (from your answers)"))
    patch = {"content": content, "generated_content": content, "updated_at": now(), "built_from": body.source,
             "built_with_competitor": bool(comp)}
    accent = brand["primary"] if brand.get("primary") else None
    site = await db().sites.find_one({"owner_id": ctx.ws})
    if site:
        await db().sites.update_one({"_id": site["_id"]}, {"$set": {**patch, **({"accent": accent} if accent else {})}, "$unset": {"prompt_preview": ""}})
    else:
        for attempt in range(5):
            slug = await _unique_slug(_slugify(p["name"]) + (f"-{new_id()[:4]}" if attempt else ""), ctx.ws)
            try:
                await db().sites.insert_one({"_id": new_id(), "owner_id": ctx.ws, "slug": slug, "status": "draft", **patch,
                                             "accent": accent or ACCENTS[0], "showcase": False, "views": 0, "created_at": now()})
                break
            except DuplicateKeyError:
                if await db().sites.find_one({"owner_id": ctx.ws}):
                    await db().sites.update_one({"owner_id": ctx.ws}, {"$set": patch})
                    break
    if body.source == "answers" and inp.get("has_website") is None:
        await db().businesses.update_one({"owner_id": ctx.ws}, {"$set": {"inputs.has_website": False}})
    await track("site_built", ctx.ws, source=body.source, competitor=bool(comp))
    return site_view(await db().sites.find_one({"owner_id": ctx.ws}), ctx.owner)

