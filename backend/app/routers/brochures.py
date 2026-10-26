"""
Brochures (v2.3, Brand engine — paid members): a Business brochure (company profile) and a Products & services brochure,
written as text from the brand kit, their website, the Knowledge Hub and the competitor summary (to sound different, never
to copy). Shown as a tab in Brand kit.

- 1 AI token per brochure; the owner edits any line in place, then copies it or downloads it as a text file.
- Open to paying members only (the "brochures" feature is paid-only in the v2.3 setup): during the event or on the free plan
  the tab shows what it is and "Opens when you join OwnerDesk Membership".
- Checked before the owner sees it: a line with a web address, email or phone number, a rupee amount the owner didn't write in
  their price list, or 6+ words in a row from the competitor's site, is dropped. The contact block (their WhatsApp, email and
  website) is added by the hub from the Knowledge Hub, never by the AI.

brochures {_id: "{ws}:{kind}", owner_id, kind business|products, content {headline, subheadline, sections [{heading, text, points}],
           proof [..], cta}, generated, edited (bool), updated_at, by}
"""
import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import growth_context as gc, plans
from ..ai import v23_sales_tasks as t
from ..competitor_watch import has_contact
from ..context import Ctx, feature
from ..db import db, now
from ..services import run_ai, track
from ..sites import site_url
from .hub import profile_of

router = APIRouter(prefix="/api/brochures")
read_ctx = feature("brand_kit", "manager")      # the tab lives in Brand kit; writing needs the paid "brochures" feature
KINDS = tuple(t.BROCHURES)
BLURB = {"business": "Who you are, what you do, who you help and why customers choose you: a company profile a buyer reads in two minutes",
         "products": "Each of your products and services, what it includes and who it's for, with prices only as you wrote them"}


class Section(BaseModel):
    heading: str = Field(default="", max_length=80)
    text: str = Field(default="", max_length=900)
    points: list[str] = Field(default_factory=list, max_length=10)


class BrochureIn(BaseModel):
    headline: str = Field(min_length=2, max_length=140)
    subheadline: str = Field(default="", max_length=240)
    sections: list[Section] = Field(default_factory=list, max_length=12)
    proof: list[str] = Field(default_factory=list, max_length=5)
    cta: str = Field(default="", max_length=240)


def _clean(s: str, limit: int) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()[:limit]


def shape(raw: dict, ctx: dict) -> dict:
    """The AI's brochure → the lines safe to print. Raises (token given back) when too little is left."""
    p = ctx.get("profile") or {}
    allowed = gc.owner_amounts(p)
    own_phone = p.get("whatsapp", "")
    comp = ctx.get("competitor_shingles") or set()
    proof_ok = " ".join([str(p.get("proof") or "")] + [str(x) for x in ((ctx.get("messages") or {}).get("core") or {}).get("proof") or []]).lower()

    def ok(line: str) -> bool:
        return bool(line) and not has_contact(line, own_phone) and not gc.has_foreign_amount(line, allowed) and not gc.copies(line, comp)

    if not isinstance(raw, dict):
        raise ValueError("not an object")
    sections = []
    for s in (raw.get("sections") or [])[:10]:
        if not isinstance(s, dict):
            continue
        heading = _clean(s.get("heading"), 80)
        text = " ".join(x for x in re.split(r"(?<=[.!?])\s+", _clean(s.get("text"), 900)) if ok(x))
        points = [x for x in (_clean(y, 200) for y in (s.get("points") or []) if y) if ok(x)][:8]
        if heading and ok(heading) and (text or points):
            sections.append({"heading": heading, "text": text, "points": points})
    headline = _clean(raw.get("headline"), 140)
    if not ok(headline) or len(sections) < 2:
        raise ValueError("too little usable text")
    # proof: only lines the owner gave (the profile's proof or the brand kit's) — anything else is dropped
    proof = [x for x in (_clean(y, 200) for y in (raw.get("proof") or []) if y) if ok(x) and x.lower()[:40] in proof_ok][:3]
    cta = _clean(raw.get("cta"), 240)
    return {"headline": headline, "subheadline": _clean(raw.get("subheadline"), 240) if ok(_clean(raw.get("subheadline"), 240)) else "",
            "sections": sections, "proof": proof, "cta": cta if ok(cta) else "Get in touch to talk about what you need."}


async def contact_block(ws: str) -> list[str]:
    """Their contact details, from the Knowledge Hub and the hub's website — added by the hub, never written by the AI."""
    p = await db().businesses.find_one({"owner_id": ws}) or {}
    site = await db().sites.find_one({"owner_id": ws}, {"slug": 1, "status": 1}) or {}
    out = []
    if p.get("whatsapp"):
        out.append(f"WhatsApp: {p['whatsapp']}")
    if p.get("email"):
        out.append(f"Email: {p['email']}")
    if site.get("status") == "live" and site.get("slug"):
        out.append(f"Website: {site_url(site['slug'])}")
    if p.get("city"):
        out.append(p["city"])
    return out


def as_text(b: dict, contact: list[str], title: str) -> str:
    c = b.get("content") or {}
    lines = [c.get("headline", "")]
    if c.get("subheadline"):
        lines.append(c["subheadline"])
    for s in c.get("sections") or []:
        lines += ["", s.get("heading", "").upper()]
        if s.get("text"):
            lines.append(s["text"])
        lines += [f"• {x}" for x in s.get("points") or []]
    if c.get("proof"):
        lines += ["", "WHAT OUR CUSTOMERS SAY"] + [f"“{x}”" for x in c["proof"]]
    if c.get("cta"):
        lines += ["", c["cta"]]
    if contact:
        lines += [""] + contact
    return f"{title}\n\n" + "\n".join(lines).strip() + "\n"


async def view(ctx: Ctx) -> dict:
    items = {}
    async for b in db().brochures.find({"owner_id": ctx.ws}):
        items[b["kind"]] = {"content": b.get("content"), "edited": bool(b.get("edited")),
                            "updated_at": b["updated_at"].isoformat() if b.get("updated_at") else None}
    open_ = plans.has(ctx.owner, "brochures")
    return {"open": open_, "paid": plans.is_paid(ctx.owner), "tokens_each": 1,
            "kinds": [{"key": k, "label": lab, "what": BLURB[k], "brochure": items.get(k)} for k, (lab, _what) in t.BROCHURES.items()],
            "contact": await contact_block(ctx.ws),
            "locked_note": None if open_ else "Opens when you join OwnerDesk Membership."}


def _need_open(ctx: Ctx) -> None:
    if not plans.has(ctx.owner, "brochures"):
        raise HTTPException(403, {"code": "locked", "feature": "brochures", "label": "Brochures", "tier": "lite",
                                  "tier_name": plans.PLANS["lite"]["name"], "message": "Brochures open when you join OwnerDesk Membership."})


@router.get("")
async def get_brochures(ctx: Ctx = Depends(read_ctx)):
    return await view(ctx)


@router.post("/{kind}/generate")
async def generate(kind: str, ctx: Ctx = Depends(read_ctx)):
    if kind not in KINDS:
        raise HTTPException(404, "Pick the business brochure or the products & services brochure.")
    _need_open(ctx)
    await profile_of(ctx.ws)
    g = await gc.gather(ctx.ws)
    content = await run_ai(ctx.owner, "brochure", t.brochure(g, kind), f"{t.BROCHURES[kind][0]} — {g['profile'].get('name', '')}",
                           check=lambda r: shape(r, g))
    await db().brochures.update_one({"_id": f"{ctx.ws}:{kind}"}, {"$set": {"owner_id": ctx.ws, "kind": kind, "content": content, "generated": content,
                                                                         "edited": False, "updated_at": now(), "by": ctx.actor}}, upsert=True)
    await track("brochure", ctx.ws, kind=kind)
    return await view(ctx)


@router.put("/{kind}")
async def save(kind: str, body: BrochureIn, ctx: Ctx = Depends(read_ctx)):
    """The owner's own edits, kept as written."""
    if kind not in KINDS:
        raise HTTPException(404, "Not found")
    _need_open(ctx)
    if not await db().brochures.find_one({"_id": f"{ctx.ws}:{kind}"}, {"_id": 1}):
        raise HTTPException(404, "Write this brochure first.")
    content = body.model_dump()
    content["sections"] = [s for s in content["sections"] if s["heading"].strip() or s["text"].strip() or s["points"]]
    await db().brochures.update_one({"_id": f"{ctx.ws}:{kind}"}, {"$set": {"content": content, "edited": True, "updated_at": now(), "by": ctx.actor}})
    return await view(ctx)


@router.get("/{kind}.txt")
async def download(kind: str, ctx: Ctx = Depends(read_ctx)):
    if kind not in KINDS:
        raise HTTPException(404, "Not found")
    _need_open(ctx)
    b = await db().brochures.find_one({"_id": f"{ctx.ws}:{kind}"})
    if not b:
        raise HTTPException(404, "Write this brochure first.")
    text = as_text(b, await contact_block(ctx.ws), t.BROCHURES[kind][0])
    return Response(text.encode("utf-8"), media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{kind}-brochure.txt"'})


async def ensure_indexes() -> None:
    await db().brochures.create_index([("owner_id", 1)])
