"""Owner-facing API: Business Brain, brand message, website, leads, AI Writer, outputs, plans, invites."""
import re
from urllib.parse import quote

from bson import Binary
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError
from starlette.datastructures import UploadFile

from .. import knowledge, plans
from .. import photos as photos_mod
from ..ai import tasks
from ..config import settings
from ..db import db, now, public
from ..context import Ctx, get_ctx, manager_ctx, owner_ctx
from ..services import invite_link, new_id, reward_referrer, run_ai, track
from ..security import rate_limit
from ..sites import site_url

router = APIRouter(prefix="/api")

# ───────────────────────── Business Brain Lite ─────────────────────────
class Offer(BaseModel):
    name: str = Field(max_length=80)
    price: str = Field(default="", max_length=40)
    unit: str = Field(default="", max_length=30)


class BusinessIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    city: str = Field(default="", max_length=80)
    industry: str = Field(min_length=3, max_length=200)
    offers: list[Offer] = Field(default_factory=list, max_length=12)
    ideal_customer: str = Field(default="", max_length=300)
    why_us: str = Field(default="", max_length=500)
    proof: str = Field(default="", max_length=500)
    whatsapp: str = Field(default="", max_length=24)
    email: str = Field(default="", max_length=120)
    language: str = Field(default="English", max_length=30)
    review_link: str = Field(default="", max_length=300)    # where happy customers leave a review (agents use it)
    payment_note: str = Field(default="", max_length=200)   # how customers pay you, e.g. a UPI ID (agents use it)


PROFILE_FIELDS = list(BusinessIn.model_fields)


def wa_digits(raw: str) -> str:
    """Any way an Indian number gets typed (98200 12345, 098200 12345, +91 098200 12345) → 919820012345 for wa.me links."""
    d = re.sub(r"\D", "", raw or "")
    if len(d) == 11 and d.startswith("0"):
        d = d[1:]
    elif len(d) == 13 and d.startswith("910"):
        d = "91" + d[3:]
    return ("91" + d) if len(d) == 10 else d


LINK_RE = re.compile(r"^https?://[^\s/$.?#][^\s]*\.[^\s]{2,}$", re.I)


def clean_link(raw: str) -> str:
    """A review link the agents can put in a WhatsApp message: g.page/r/abc → https://g.page/r/abc. Empty stays empty."""
    link = (raw or "").strip()
    if not link:
        return ""
    if not re.match(r"^https?://", link, re.I):
        link = "https://" + link
    if not LINK_RE.match(link) or link.lower().startswith(("http://localhost", "https://localhost")):
        raise HTTPException(400, "Add the full review link, like https://g.page/r/your-business/review")
    return link


@router.get("/business")
async def get_business(ctx: Ctx = Depends(get_ctx)):
    b = await db().businesses.find_one({"owner_id": ctx.ws})
    return public(b) or {}


@router.put("/business")
async def save_business(body: BusinessIn, ctx: Ctx = Depends(owner_ctx)):
    user = ctx.owner
    data = body.model_dump()
    data["offers"] = [o for o in data["offers"] if o["name"].strip()]
    if data["whatsapp"] and not (10 <= len(wa_digits(data["whatsapp"])) <= 13):
        raise HTTPException(400, "Enter the WhatsApp number customers should message")
    data["review_link"] = clean_link(data["review_link"])
    before = await db().businesses.find_one({"owner_id": user["_id"]})
    await db().businesses.update_one({"owner_id": user["_id"]}, {"$set": {**data, "updated_at": now()},
                                                                  "$setOnInsert": {"_id": new_id(), "owner_id": user["_id"], "created_at": now()}}, upsert=True)
    if before:   # the first save is onboarding; later saves update everything built from the Brain
        knowledge.after_save(user, knowledge.changed_sections(before, data))
    return public(await db().businesses.find_one({"owner_id": user["_id"]}))


async def profile_of(ws: str) -> dict:
    b = await db().businesses.find_one({"owner_id": ws})
    if not b or not b.get("name") or not b.get("industry"):
        raise HTTPException(400, "Tell us about your business first")
    return b


async def _profile(ctx: Ctx) -> dict:
    return await profile_of(ctx.ws)


# ───────────────────────── Structure: brand message ─────────────────────────
class BrandIn(BaseModel):
    message: str = Field(min_length=5, max_length=160)
    pitch: str = Field(default="", max_length=700)


@router.post("/brand/generate")
async def generate_brand(ctx: Ctx = Depends(owner_ctx)):
    b = await _profile(ctx)
    result = await run_ai(ctx.owner, "brand", tasks.brand_message(b), f"Brand message — {b['name']}")
    options = [{"message": str(o.get("message", ""))[:160], "pitch": str(o.get("pitch", ""))[:700]} for o in result.get("options", [])][:3]
    await db().businesses.update_one({"_id": b["_id"]}, {"$set": {"brand_options": options}})
    return {"options": options}


@router.put("/brand")
async def choose_brand(body: BrandIn, ctx: Ctx = Depends(owner_ctx)):
    b = await _profile(ctx)
    await db().businesses.update_one({"_id": b["_id"]}, {"$set": {"brand": {**body.model_dump(), "chosen_at": now()}}})
    await track("brand_chosen", ctx.ws)
    if (b.get("brand") or {}).get("message") and (b.get("brand") or {}).get("message") != body.message:
        knowledge.after_save(ctx.owner, {"brand"})
    return {"ok": True}


# ───────────────────────── Systems: website ─────────────────────────
ACCENTS = ["#1F4E79", "#0F766E", "#7C2D12", "#B45309", "#334155", "#6D28D9"]
RESERVED = {"admin", "api", "app", "join", "login", "s", "static", "assets", "www", "help", "support", "hub", "pulse"} | set(settings.reserved_subdomains)
SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,38}[a-z0-9])$")


class Item(BaseModel):
    title: str = Field(default="", max_length=80)
    text: str = Field(default="", max_length=300)


class SiteOffer(BaseModel):
    name: str = Field(max_length=80)
    description: str = Field(default="", max_length=300)
    price: str = Field(default="", max_length=60)


class Faq(BaseModel):
    q: str = Field(max_length=160)
    a: str = Field(max_length=500)


class SiteContent(BaseModel):
    headline: str = Field(min_length=3, max_length=120)
    subheadline: str = Field(default="", max_length=300)
    cta: str = Field(default="Get a quote on WhatsApp", max_length=40)
    about: str = Field(default="", max_length=1200)
    offers: list[SiteOffer] = Field(default_factory=list, max_length=12)
    why: list[Item] = Field(default_factory=list, max_length=6)
    steps: list[Item] = Field(default_factory=list, max_length=6)
    faq: list[Faq] = Field(default_factory=list, max_length=10)
    photo_queries: list[str] = Field(default_factory=list, max_length=4)  # stock photo search terms the AI suggested
    niche: str = Field(default="", max_length=30)        # the kind of business the AI read (site_looks.NICHES): picks the look
    pains: list[str] = Field(default_factory=list, max_length=4)   # the customers' problems, only from the owner's own words


class SiteIn(BaseModel):
    content: SiteContent
    accent: str = Field(default=ACCENTS[0])
    slug: str | None = Field(default=None, max_length=40)
    showcase: bool = False
    look: str | None = Field(default=None, max_length=20)   # one of site_looks.LOOKS, or "auto" / empty for the business's kind


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:36].strip("-")
    return s if len(s) >= 3 else f"business-{s}".strip("-")


async def _unique_slug(base: str, owner_id: str) -> str:
    slug, n = base, 1
    while slug in RESERVED or await db().sites.find_one({"slug": slug, "owner_id": {"$ne": owner_id}}):
        n += 1
        slug = f"{base[:33]}-{n}"
    return slug


def site_view(site: dict | None, user: dict) -> dict | None:
    if not site:
        return None
    out = public(site)
    out["url"] = site_url(site["slug"])
    out["preview_url"] = f"{settings.app_url}/s/{site['slug']}?preview=1"
    out["card_url"] = site_url(site["slug"], "/card")
    out["sites_domain"] = settings.sites_domain or None
    out["badge"] = not plans.has(user, "badge_off")
    out["accents"] = ACCENTS
    out["photos"] = photos_mod.resolved(site.get("photos"), f"{settings.app_url}/s/{site['slug']}")
    out["stock_photos"] = bool(settings.pexels_api_key)
    from ..rivals import public as rivals_public
    out["competitors"] = rivals_public(site.get("competitors"))     # the addresses and whether each was read, not their text
    return out


async def _with_looks(view: dict | None, ws: str) -> dict:
    """The site with the looks the owner can pick, and the one their kind of business gets automatically."""
    if not view:
        return {}
    from .. import site_looks
    b = await db().businesses.find_one({"owner_id": ws}) or {}
    site = await db().sites.find_one({"owner_id": ws}, {"content.niche": 1, "slug": 1}) or {}
    return {**view, "look": view.get("look") or "auto", **{f"look_{k}": v for k, v in site_looks.options(b, site).items()}}


@router.get("/site")
async def get_site(ctx: Ctx = Depends(get_ctx)):
    return await _with_looks(site_view(await db().sites.find_one({"owner_id": ctx.ws}), ctx.owner), ctx.ws)


def _blank_nulls(value):
    """AI models sometimes return null for an empty field; treat it as an empty string."""
    if isinstance(value, dict):
        return {k: _blank_nulls(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_blank_nulls(v) for v in value]
    return "" if value is None else value


def _site_from_ai(result: dict) -> dict:
    r = _blank_nulls(result)
    r["photo_queries"] = [q.strip()[:60] for q in (r.get("photo_queries") if isinstance(r.get("photo_queries"), list) else [])
                          if isinstance(q, str) and q.strip()][:4]
    lists = {k: (r.get(k) if isinstance(r.get(k), list) else []) for k in ("offers", "why", "steps", "faq")}
    from ..site_looks import NICHES
    niche = str(r.get("niche") or "").strip().lower()
    pains = [" ".join(str(x).split())[:90] for x in (r.get("pains") if isinstance(r.get("pains"), list) else []) if isinstance(x, str) and x.strip()][:4]
    return SiteContent.model_validate({**r, "offers": lists["offers"][:12], "why": lists["why"][:6],
                                       "steps": lists["steps"][:6], "faq": lists["faq"][:10],
                                       "niche": niche if niche in NICHES else "", "pains": pains}).model_dump()


class GenerateIn(BaseModel):
    competitors: list[str] | None = Field(default=None, max_length=6)   # None: use the ones saved with the site


@router.post("/site/generate")
async def generate_site(body: GenerateIn | None = None, ctx: Ctx = Depends(owner_ctx)):
    from .. import rivals as rv
    user = ctx.owner
    b = await _profile(ctx)
    site = await db().sites.find_one({"owner_id": user["_id"]})
    if body and body.competitors is not None:
        try:
            urls = rv.clean(body.competitors, b.get("website"))
        except rv.RivalError as e:
            raise HTTPException(400, str(e))
        competitors = await rv.read_all(urls)       # read before the AI run is used, so a slow site doesn't hold a run
    else:
        competitors = (site or {}).get("competitors") or []
    content = await run_ai(user, "site", tasks.site_content(b, b.get("brand"), competitors), f"Website — {b['name']}", check=_site_from_ai)
    site = await db().sites.find_one({"owner_id": user["_id"]})
    if site:
        await db().sites.update_one({"_id": site["_id"]}, {"$set": {"content": content, "generated_content": content, "competitors": competitors,
                                                                    "updated_at": now()}})
    else:
        for attempt in range(5):
            slug = await _unique_slug(_slugify(b["name"]) + (f"-{new_id()[:4]}" if attempt else ""), user["_id"])
            try:
                await db().sites.insert_one({"_id": new_id(), "owner_id": user["_id"], "slug": slug, "status": "draft", "content": content,
                                             "generated_content": content, "competitors": competitors,
                                             "accent": ACCENTS[0], "showcase": False, "views": 0, "created_at": now(), "updated_at": now()})
                break
            except DuplicateKeyError:
                if await db().sites.find_one({"owner_id": user["_id"]}):  # a double click created it already
                    await db().sites.update_one({"owner_id": user["_id"]}, {"$set": {"content": content, "generated_content": content, "updated_at": now()}})
                    break
    site = await db().sites.find_one({"owner_id": user["_id"]})
    await photos_mod.refresh_stock(site, b)
    return site_view(await db().sites.find_one({"owner_id": user["_id"]}), user)


# The website copy follows the Knowledge Hub: rewritten when untouched, flagged with a banner when the owner edited it.
# Contact details (WhatsApp, phone) are read live when the page is served, so they never need a rewrite.
async def _site_exists(ws: str) -> bool:
    return bool(await db().sites.find_one({"owner_id": ws}, {"_id": 1}))


async def _site_edited(ws: str) -> bool:
    site = await db().sites.find_one({"owner_id": ws}, {"content": 1, "generated_content": 1}) or {}
    return site.get("generated_content") is None or site.get("content") != site.get("generated_content")


async def _site_words(owner: dict, b: dict) -> dict:
    """The website's words written again from the Knowledge Hub. A website built from the shared inputs (v2.3) is written
    the same way again — the answers and the competitor summary — so a refresh doesn't lose what made it stand out."""
    site = await db().sites.find_one({"owner_id": owner["_id"]}, {"built_from": 1, "competitors": 1}) or {}
    if site.get("built_from"):
        from .inputs import write_content
        return (await write_content(owner, b))[0]
    # otherwise (e.g. written at the live event): the competitor websites saved with it, if any, are used again
    return await run_ai(owner, "site", tasks.site_content(b, b.get("brand"), site.get("competitors")),
                        f"Website refreshed from your Knowledge Hub — {b['name']}", check=_site_from_ai)


async def _site_rewrite(owner: dict) -> None:
    b = await profile_of(owner["_id"])
    content = await _site_words(owner, b)
    await db().sites.update_one({"owner_id": owner["_id"]}, {"$set": {"content": content, "generated_content": content, "updated_at": now(),
                                                                      "brain_refreshed_at": now()}})


async def _site_refresh(owner: dict, sections: set) -> str | None:
    if sections <= {"contact"}:
        return None  # the page reads contact details live; the copy didn't depend on them
    before = await db().sites.find_one({"owner_id": owner["_id"]}, {"updated_at": 1}) or {}
    b = await profile_of(owner["_id"])
    content = await _site_words(owner, b)
    # written only if the owner didn't save the page while the AI was writing; otherwise their version stays, with the banner
    r = await db().sites.update_one({"owner_id": owner["_id"], "updated_at": before.get("updated_at")},
                                    {"$set": {"content": content, "generated_content": content, "updated_at": now(), "brain_refreshed_at": now()}})
    return None if r.matched_count else "kept_edits"


knowledge.register(knowledge.Dependent("website", "Website copy", "website", "/website", {"basics", "brand", "contact"}, _site_exists, _site_edited,
                                       _site_refresh, overwrite=_site_rewrite, note="Your phone and WhatsApp number on the page are always current.", order=1,
                                       cost=lambda sections: 0 if sections <= {"contact"} else 1))


@router.put("/site")
async def save_site(body: SiteIn, ctx: Ctx = Depends(owner_ctx)):
    user = ctx.owner
    site = await db().sites.find_one({"owner_id": user["_id"]})
    if not site:
        raise HTTPException(400, "Create your website first")
    from ..brand import tokens_for
    brand_primary = (await tokens_for(user["_id"])).get("primary")
    accent = body.accent if body.accent in ACCENTS or (brand_primary and body.accent.upper() == brand_primary.upper()) else ACCENTS[0]
    patch = {"content": body.content.model_dump(), "accent": accent, "showcase": body.showcase, "updated_at": now()}
    if accent != site.get("accent", ACCENTS[0]):
        patch["accent_chosen"] = True       # the owner picked a colour: it's used as is (no colour by kind of business)
    if body.look is not None:
        from .. import site_looks
        if body.look not in site_looks.LOOKS and body.look not in ("", "auto"):
            raise HTTPException(400, "Pick one of the looks shown")
        patch["look"] = body.look if body.look in site_looks.LOOKS else None
    if body.slug and body.slug != site["slug"]:
        slug = body.slug.strip().lower()
        if not SLUG.match(slug) or slug in RESERVED:
            raise HTTPException(400, "Use 3–40 lowercase letters, numbers or dashes for the web address")
        if await db().sites.find_one({"slug": slug, "owner_id": {"$ne": user["_id"]}}):
            raise HTTPException(409, "That web address is taken. Try another.")
        patch["slug"] = slug
    try:
        await db().sites.update_one({"_id": site["_id"]}, {"$set": patch})
    except DuplicateKeyError:
        raise HTTPException(409, "That web address is taken. Try another.")
    return await _with_looks(site_view(await db().sites.find_one({"_id": site["_id"]}), user), user["_id"])


@router.post("/site/publish")
async def publish_site(ctx: Ctx = Depends(owner_ctx)):
    user = ctx.owner
    site = await db().sites.find_one({"owner_id": user["_id"]})
    if not site:
        raise HTTPException(400, "Create your website first")
    b = await db().businesses.find_one({"owner_id": user["_id"]}) or {}
    if not wa_digits(b.get("whatsapp", "")):
        raise HTTPException(400, "Add your WhatsApp number so customers can reach you")
    first_time = not site.get("first_live_at")
    await db().sites.update_one({"_id": site["_id"]}, {"$set": {"status": "live", "published_at": now(),
                                                                **({"first_live_at": now()} if first_time else {})}})
    if first_time:
        await track("site_live", user["_id"])
        await reward_referrer(user)
    return site_view(await db().sites.find_one({"_id": site["_id"]}), user)


# ───────── Website photos: the owner's own (hero + gallery), stock photos from Pexels, or the designed artwork ─────────
@router.post("/site/photos")
async def upload_site_photo(request: Request, ctx: Ctx = Depends(owner_ctx)):
    user = ctx.owner
    site = await db().sites.find_one({"owner_id": user["_id"]})
    if not site:
        raise HTTPException(400, "Create your website first")
    form = await request.form()
    try:
        f, slot = form.get("file"), str(form.get("slot") or "gallery")
        if not isinstance(f, UploadFile):
            raise HTTPException(400, "Choose a photo.")
        data = await f.read(photos_mod.UPLOAD_BYTES + 1)
    finally:
        await form.close()
    if slot not in ("hero", "gallery"):
        raise HTTPException(400, "Unknown photo slot")
    if len(data) > photos_mod.UPLOAD_BYTES:
        raise HTTPException(413, "Keep each photo under 3 MB.")
    kind = photos_mod.image_type(data)
    if not kind:
        raise HTTPException(415, "Upload a JPG, PNG or WebP photo.")
    if await db().site_photos.count_documents({"owner_id": user["_id"]}) >= photos_mod.UPLOADS_MAX:
        raise HTTPException(400, f"You can keep up to {photos_mod.UPLOADS_MAX} photos. Remove one first.")
    pid = new_id()
    await db().site_photos.insert_one({"_id": pid, "owner_id": user["_id"], "type": kind, "data": Binary(data), "at": now()})
    ph = site.get("photos") or {"hero": None, "gallery": [], "offers": {}}
    item = {"kind": "upload", "id": pid, "alt": (await _profile(ctx)).get("name", "")[:120]}
    if slot == "hero":
        old = ph.get("hero") or {}
        if old.get("kind") == "upload":
            await db().site_photos.delete_one({"_id": old["id"], "owner_id": user["_id"]})
        ph["hero"] = item
    else:
        gallery = [p for p in ph.get("gallery", []) if p]
        uploads = [p for p in gallery if p.get("kind") == "upload"]
        if len(uploads) >= photos_mod.GALLERY_MAX:
            await db().site_photos.delete_one({"_id": pid})
            raise HTTPException(400, f"The gallery holds {photos_mod.GALLERY_MAX} photos. Remove one first.")
        ph["gallery"] = (uploads + [item] + [p for p in gallery if p.get("kind") != "upload"])[:photos_mod.GALLERY_MAX]
    await db().sites.update_one({"_id": site["_id"]}, {"$set": {"photos": ph, "updated_at": now()}})
    return site_view(await db().sites.find_one({"_id": site["_id"]}), user)


class PhotoRemoveIn(BaseModel):
    slot: str = Field(pattern="^(hero|gallery)$")
    index: int = Field(default=0, ge=0, le=20)


@router.post("/site/photos/remove")
async def remove_site_photo(body: PhotoRemoveIn, ctx: Ctx = Depends(owner_ctx)):
    user = ctx.owner
    site = await db().sites.find_one({"owner_id": user["_id"]})
    if not site:
        raise HTTPException(400, "Create your website first")
    ph = site.get("photos") or {}
    gallery = ph.get("gallery") or []
    if body.slot == "hero":
        gone = ph.get("hero")
    else:
        gone = gallery[body.index] if body.index < len(gallery) else None
    if not gone:
        raise HTTPException(404, "That photo is already gone")
    if body.slot == "hero":
        ph["hero"] = None
    else:
        ph["gallery"] = [p for i, p in enumerate(ph.get("gallery", [])) if i != body.index]
    if gone.get("kind") == "upload":
        await db().site_photos.delete_one({"_id": gone["id"], "owner_id": user["_id"]})
    await db().sites.update_one({"_id": site["_id"]}, {"$set": {"photos": ph, "updated_at": now()}})
    return site_view(await db().sites.find_one({"_id": site["_id"]}), user)


@router.post("/site/photos/stock")
async def refresh_stock_photos(ctx: Ctx = Depends(owner_ctx)):
    """Find stock photos again (keeps the owner's uploads)."""
    user = ctx.owner
    if not settings.pexels_api_key:
        raise HTTPException(400, "Stock photos aren't switched on for this hub. Upload your own photos instead.")
    site = await db().sites.find_one({"owner_id": user["_id"]})
    if not site:
        raise HTTPException(400, "Create your website first")
    await rate_limit(f"stock:{user['_id']}", 10, 3600, "Photos were refreshed many times this hour. Try again later.")
    photos = await photos_mod.refresh_stock(site, await _profile(ctx))
    if not photos.get("hero") and not photos.get("gallery"):
        raise HTTPException(404, "No stock photos found for this business. Upload your own instead.")
    return site_view(await db().sites.find_one({"_id": site["_id"]}), user)


@router.get("/site/qr.png")
async def site_qr(ctx: Ctx = Depends(get_ctx)):
    """A QR code of the live website — shown full screen so the person next to you can scan it and send an inquiry."""
    import io

    import qrcode
    from fastapi.responses import Response
    site = await db().sites.find_one({"owner_id": ctx.ws}, {"slug": 1, "status": 1})
    if not site or site.get("status") != "live":
        raise HTTPException(400, "Publish your website first.")
    q = qrcode.QRCode(border=2, box_size=12, error_correction=qrcode.constants.ERROR_CORRECT_M)
    q.add_data(site_url(site["slug"]))
    q.make(fit=True)
    buf = io.BytesIO()
    q.make_image(fill_color="#14151A", back_color="#FFFFFF").save(buf, "PNG")
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "private, max-age=300"})


@router.post("/site/unpublish")
async def unpublish_site(ctx: Ctx = Depends(owner_ctx)):
    user = ctx.owner
    await db().sites.update_one({"owner_id": user["_id"]}, {"$set": {"status": "draft"}})
    return site_view(await db().sites.find_one({"owner_id": user["_id"]}), user)


# ───────────────────────── Systems: leads ─────────────────────────
STAGES = ["identification", "logic", "pain", "vision", "close"]   # the Football Field: 5 stages of a sale


class LeadPatch(BaseModel):
    status: str | None = Field(default=None, pattern="^(new|contacted|won|lost)$")
    stage: str | None = Field(default=None, pattern="^(identification|logic|pain|vision|close)$")
    value: float | None = Field(default=None, ge=0, le=1e11)     # rupees: the deal's value, used by Control Room and results
    note: str | None = Field(default=None, max_length=1000)


@router.get("/leads")
async def list_leads(ctx: Ctx = Depends(get_ctx)):
    return [public(l) async for l in db().leads.find({"owner_id": ctx.ws}).sort("created_at", -1).limit(500)]


@router.patch("/leads/{lead_id}")
async def update_lead(lead_id: str, body: LeadPatch, ctx: Ctx = Depends(get_ctx)):
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    before = await db().leads.find_one({"_id": lead_id, "owner_id": ctx.ws})
    if not before:
        raise HTTPException(404, "Lead not found")
    if "stage" in patch and before.get("stage") != patch["stage"]:
        patch["stage_at"] = now()
    first_action = {"first_action_at": now()} if patch.get("status") and patch["status"] != "new" and not before.get("first_action_at") else {}
    if patch.get("status") and patch["status"] != before.get("status"):   # the day it was won, for "Am I on target" (edits don't move it)
        patch["won_at"] = now() if patch["status"] == "won" else None
    await db().leads.update_one({"_id": lead_id}, {"$set": {**patch, **first_action, "updated_at": now(), "updated_by": ctx.actor}})
    from .. import crm   # v2.3: a line in the contact's history
    await crm.on_lead_patched(ctx.ws, before, patch, ctx.actor, ctx.user.get("name", ""))
    if patch.get("status") and patch["status"] != before.get("status"):
        if patch["status"] == "won":
            await track("lead_won", ctx.ws)
        from ..agents import hooks
        await hooks.on_lead_status(ctx.owner, {**before, **patch}, patch["status"])
    if patch.get("stage") or patch.get("status"):
        from ..agents import hooks
        await hooks.on_lead_moved(ctx.owner, {**before, **patch})
    return {"ok": True}


@router.post("/leads/{lead_id}/reply")
async def lead_reply(lead_id: str, ctx: Ctx = Depends(get_ctx)):
    lead = await db().leads.find_one({"_id": lead_id, "owner_id": ctx.ws})
    if not lead:
        raise HTTPException(404, "Lead not found")
    b = await _profile(ctx)
    result = await run_ai(ctx.owner, "reply", tasks.reply_draft(b, lead.get("message", ""), lead.get("name", "")), f"Reply to {lead.get('name') or 'a lead'}")
    reply = str(result.get("reply", ""))[:2000]
    return {"reply": reply, "whatsapp_link": f"https://wa.me/{wa_digits(lead.get('phone', ''))}?text={quote(reply)}" if lead.get("phone") else None}


# ───────────────────────── Scale: AI studio ─────────────────────────
class PostsIn(BaseModel):
    focus: str = Field(default="", max_length=120)


class ReplyIn(BaseModel):
    message: str = Field(min_length=2, max_length=2000)
    customer_name: str = Field(default="", max_length=80)


class AskIn(BaseModel):
    question: str = Field(min_length=5, max_length=1500)


@router.post("/studio/posts")
async def studio_posts(body: PostsIn, ctx: Ctx = Depends(get_ctx)):
    b = await _profile(ctx)
    result = await run_ai(ctx.owner, "posts", tasks.social_posts(b, body.focus), f"Week of posts{' — ' + body.focus if body.focus else ''}")
    return {"posts": result.get("posts", [])[:7]}


@router.post("/studio/reply")
async def studio_reply(body: ReplyIn, ctx: Ctx = Depends(get_ctx)):
    b = await _profile(ctx)
    result = await run_ai(ctx.owner, "reply", tasks.reply_draft(b, body.message, body.customer_name), f"Reply: {body.message[:60]}")
    return {"reply": str(result.get("reply", ""))[:2000]}


@router.post("/studio/ask")
async def studio_ask(body: AskIn, ctx: Ctx = Depends(get_ctx)):
    b = await _profile(ctx)
    result = await run_ai(ctx.owner, "answer", tasks.business_qa(b, body.question), body.question[:120])
    return {"answer": str(result.get("answer", ""))[:3000]}


class PolishIn(BaseModel):
    text: str = Field(min_length=3, max_length=6000)
    kind: str = Field(default="whatsapp", pattern="^(whatsapp|post|email|letter)$")
    tone: str = Field(default="warm", pattern="^(warm|firm|formal)$")


@router.post("/studio/polish")
async def studio_polish(body: PolishIn, ctx: Ctx = Depends(get_ctx)):
    """English polisher: Hindi, Hinglish or rough English → polished English. Emails, letters and other tones (and voice
    notes, through /voice/transcribe?for=polish) are part of Membership."""
    if body.kind in ("email", "letter") or body.tone != "warm":
        ctx.require("polish_voice")
    b = await _profile(ctx)
    from ..ai import onboard_tasks
    result = await run_ai(ctx.owner, "polish", onboard_tasks.polish(body.text, body.kind, body.tone, b), f"Polished: {body.text[:60]}")
    return {"text": str(result.get("text", ""))[:4000], "subject": str(result.get("subject", ""))[:200]}


# ───────────────────────── My Outputs ─────────────────────────
# What each role may see in My outputs: staff only the everyday writing; managers everything but the owner's private reviews.
STAFF_OUTPUT_KINDS = ["posts", "reply", "answer", "polish", "calendar", "tool:content", "tool:video", "tool:google", "tool:proposal",
                      "tool:case_study", "tool:telecalling", "tool:marketing_plan", "tool:persona", "tool:sales_roles"]
OWNER_ONLY_OUTPUT_KINDS = ["kit:decision", "wheel_comment", "goal_coach"]


def _outputs_scope(ctx: Ctx) -> dict:
    if ctx.role == "owner":
        return {}
    if ctx.role == "manager":
        return {"kind": {"$nin": OWNER_ONLY_OUTPUT_KINDS}}
    return {"kind": {"$in": STAFF_OUTPUT_KINDS}}


@router.get("/outputs")
async def list_outputs(kind: str | None = None, ctx: Ctx = Depends(get_ctx)):
    scope = _outputs_scope(ctx)
    if kind and "kind" in scope and ((scope["kind"].get("$in") and kind not in scope["kind"]["$in"]) or kind in scope["kind"].get("$nin", [])):
        return []
    q = {"user_id": ctx.ws, **scope, **({"kind": kind[:40]} if kind else {})}
    from .. import tools
    out = []
    async for o in db().outputs.find(q).sort("created_at", -1).limit(200):
        v = public(o)
        v["kind_label"] = tools.kind_label(o.get("kind", ""))
        out.append(v)
    return out


@router.delete("/outputs/{output_id}")
async def delete_output(output_id: str, ctx: Ctx = Depends(manager_ctx)):
    await db().outputs.delete_one({"_id": output_id, "user_id": ctx.ws, **_outputs_scope(ctx)})
    return {"ok": True}


# ───────────────────────── Plans and invites ─────────────────────────
class UpgradeIn(BaseModel):
    tier: str = Field(pattern="^(lite|program|running|growth|office)$")
    from_feature: str | None = Field(default=None, max_length=40)


@router.get("/plans")
async def get_plans(ctx: Ctx = Depends(get_ctx)):
    user = ctx.owner
    return {"current": plans.effective_plan(user), "base": user.get("plan", "free"), "ladder": plans.ladder(),
            "upgrade_available": {k: bool(v) for k, v in settings.upgrade_urls.items()}}


@router.post("/upgrade-click")
async def upgrade_click(body: UpgradeIn, ctx: Ctx = Depends(owner_ctx)):
    user = ctx.owner
    await track("upgrade_click", user["_id"], tier=body.tier, feature=body.from_feature)
    return {"url": settings.upgrade_urls.get(body.tier) or None}


@router.get("/referrals")
async def my_referrals(ctx: Ctx = Depends(owner_ctx)):
    user = ctx.owner
    friends = []
    async for f in db().users.find({"referred_by": user["_id"]}).sort("created_at", -1).limit(200):
        b = await db().businesses.find_one({"owner_id": f["_id"]}, {"name": 1}) or {}
        s = await db().sites.find_one({"owner_id": f["_id"]}, {"first_live_at": 1}) or {}
        friends.append({"business": b.get("name") or "Setting up", "joined_at": f["created_at"].isoformat(), "live": bool(s.get("first_live_at"))})
    rewards = [public(r) async for r in db().referral_rewards.find({"referrer_id": user["_id"]}).sort("at", -1)]
    return {"link": invite_link(user), "badge_link": invite_link(user, "badge"), "friends": friends, "rewards": rewards,
            "rules": {"friend_trial_days": settings.friend_trial_days, "first_reward_days": settings.referrer_first_reward_days,
                      "bonus_runs": settings.referrer_bonus_runs}}
