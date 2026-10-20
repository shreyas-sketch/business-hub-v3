"""Brand kit (3.11): the message system, the design system, designs and the brand kit PDF."""
import io

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from PIL import Image
from pydantic import BaseModel, Field, field_validator

from .. import brand_kit as bk, designs, files, knowledge
from ..ai import brand_tasks
from ..context import Ctx, feature
from ..dashboard_cache import bust
from ..db import db, now
from ..services import run_ai, track
from ..sites import site_url
from .hub import profile_of

router = APIRouter(prefix="/api/brand-kit")
read_ctx, write_ctx = feature("brand_kit", "manager"), feature("brand_kit", "owner")


async def _kit(ws: str) -> dict:
    return await db().brand_kits.find_one({"_id": ws}) or {}


async def _logo(kit: dict) -> Image.Image | None:
    fid = (kit.get("design") or {}).get("logo_file_id")
    f = await files.get(fid, kit.get("_id")) if fid else None
    if not f:
        return None
    try:
        return bk.open_image(bytes(f["data"])).convert("RGBA")
    except Exception:  # noqa: BLE001
        return None


def edited_of(kit: dict) -> dict[str, bool]:
    """The items the owner changed, by path ("marketing.tagline"). Kept as a list: a dotted key in a Mongo document would
    turn into nested fields. (An older nested `edited` map is read too.)"""
    out = {p: True for p in kit.get("edited_paths") or []}

    def walk(node, prefix):
        for k, v in (node or {}).items():
            path = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                walk(v, path)
            elif v:
                out[path] = True
    walk(kit.get("edited") or {}, "")
    return out


def _view(kit: dict, b: dict, site: dict | None) -> dict:
    design = kit.get("design") or bk.default_design(b.get("name", ""), (site or {}).get("accent"))
    return {"exists": bool(kit.get("messages")), "messages": kit.get("messages"), "edited": edited_of(kit),
            "design": design, "contrast": bk.checked(design.get("colors") or {}), "has_logo": bool(design.get("logo_file_id")),
            "fonts": [{"key": k, "label": lab, "devanagari": deva} for k, lab, _s, deva in bk.FONTS], "corners": list(bk.CORNERS),
            "spacing": list(bk.SPACING), "sections": bk.SECTION_LABELS,
            "templates": [{"key": t.key, "label": t.label, "group": t.group, "size": list(t.size), "pdf": bool(t.pdf_dpi), "note": t.note,
                           "fields": [{"key": f.key, "label": f.label, "max": f.max, "lines": f.lines} for f in t.fields]} for t in designs.TEMPLATES.values()],
            "updated_at": kit["updated_at"].isoformat() if kit.get("updated_at") else None}


@router.get("")
async def get_kit(ctx: Ctx = Depends(read_ctx)):
    b = await db().businesses.find_one({"owner_id": ctx.ws}) or {}
    site = await db().sites.find_one({"owner_id": ctx.ws}, {"accent": 1, "slug": 1})
    return _view(await _kit(ctx.ws), b, site)


async def _write_voice(ws: str, messages: dict) -> None:
    """The tone of voice lives in the Brain too, where every AI writer reads it (no propagation loop: it's the Brain's own copy)."""
    await db().businesses.update_one({"owner_id": ws}, {"$set": {"voice": messages.get("voice") or {}}})


async def _generate(owner: dict) -> dict:
    p = await profile_of(owner["_id"])
    return await run_ai(owner, "brand_kit", brand_tasks.message_system(p, p.get("brand")), f"Brand kit — {p['name']}", check=bk.shape_messages)


@router.post("/generate")
async def generate(ctx: Ctx = Depends(write_ctx)):
    """Writes the message system from the Knowledge Hub (one AI run). Items the owner edited are kept."""
    kit = await _kit(ctx.ws)
    new = await _generate(ctx.owner)
    edited = edited_of(kit)
    messages = bk.merge_refresh(kit["messages"], new, edited) if kit.get("messages") else new
    b = await db().businesses.find_one({"owner_id": ctx.ws}) or {}
    site = await db().sites.find_one({"owner_id": ctx.ws}, {"accent": 1})
    design = kit.get("design") or bk.default_design(b.get("name", ""), (site or {}).get("accent"))
    await db().brand_kits.update_one({"_id": ctx.ws}, {"$set": {"messages": messages, "generated": new, "edited_paths": sorted(edited), "design": design,
                                                                "updated_at": now(), "brain_version": b.get("brain_version", 0)},
                                                       "$unset": {"edited": ""}}, upsert=True)
    await _write_voice(ctx.ws, messages)
    await track("brand_kit", ctx.ws)
    return await get_kit(ctx)


class ItemIn(BaseModel):
    path: str = Field(min_length=5, max_length=60)
    value: str | list[str]


@router.put("/messages")
async def edit_item(body: ItemIn, ctx: Ctx = Depends(write_ctx)):
    kit = await _kit(ctx.ws)
    if not kit.get("messages"):
        raise HTTPException(400, "Write your brand kit first.")
    known = bk.paths(kit["messages"])
    if body.path not in known:
        raise HTTPException(400, "Unknown item")
    value = body.value
    if isinstance(known[body.path], list):
        value = [x.strip()[:200] for x in (value if isinstance(value, list) else str(value).split("\n")) if str(x).strip()][:10]
    else:
        value = str(value).strip()[:900]
    messages = kit["messages"]
    bk.set_path(messages, body.path, value)
    await db().brand_kits.update_one({"_id": ctx.ws}, {"$set": {"messages": messages, "updated_at": now()}, "$addToSet": {"edited_paths": body.path}})
    if body.path.startswith("voice."):
        await _write_voice(ctx.ws, messages)
    return await get_kit(ctx)


class PathIn(BaseModel):
    path: str = Field(min_length=5, max_length=60)


@router.post("/messages/reset")
async def reset_item(body: PathIn, ctx: Ctx = Depends(write_ctx)):
    """Back to the last written version for this item; it follows the Knowledge Hub again."""
    kit = await _kit(ctx.ws)
    gen = bk.paths(kit.get("generated") or {})
    if body.path not in gen:
        raise HTTPException(400, "Unknown item")
    messages = kit["messages"]
    bk.set_path(messages, body.path, gen[body.path])
    await db().brand_kits.update_one({"_id": ctx.ws}, {"$set": {"messages": messages, "updated_at": now()}, "$pull": {"edited_paths": body.path}})
    if body.path.startswith("voice."):
        await _write_voice(ctx.ws, messages)
    return await get_kit(ctx)


class Colors(BaseModel):
    primary: str
    secondary: str
    accent: str
    background: str = "#FFFFFF"
    text: str = "#14171C"

    @field_validator("primary", "secondary", "accent", "background", "text")
    @classmethod
    def hex_colour(cls, v):
        if not bk.HEX.match(v or ""):
            raise ValueError("Colours look like #1F4E79")
        return v.upper()


class Fonts(BaseModel):
    heading: str
    body: str

    @field_validator("heading", "body")
    @classmethod
    def known_font(cls, v):
        if v not in bk.FONT_KEYS:
            raise ValueError("Pick a font from the list")
        return v


class DesignIn(BaseModel):
    colors: Colors
    fonts: Fonts
    corners: str = Field(default="soft", pattern="^(square|soft|round)$")
    spacing: str = Field(default="comfortable", pattern="^(compact|comfortable)$")
    text_logo: str = Field(default="", max_length=40)


@router.put("/design")
async def save_design(body: DesignIn, ctx: Ctx = Depends(write_ctx)):
    """The design tokens. The primary colour becomes the website's accent at once, so the website, the launch kit and the next
    invoice all follow it."""
    kit = await _kit(ctx.ws)
    b = await db().businesses.find_one({"owner_id": ctx.ws}) or {}
    design = {**(kit.get("design") or bk.default_design(b.get("name", ""))), **body.model_dump(), "source": "picked"}
    await db().brand_kits.update_one({"_id": ctx.ws}, {"$set": {"design": design, "updated_at": now()}}, upsert=True)
    await db().sites.update_one({"owner_id": ctx.ws}, {"$set": {"accent": design["colors"]["primary"], "brand_accent": True}})
    await db().businesses.update_one({"owner_id": ctx.ws}, {"$set": {"brand_colors": design["colors"]}})
    bust(ctx.ws)
    await track("brand_design", ctx.ws)
    return await get_kit(ctx)


@router.post("/logo")
async def upload_logo(file: UploadFile = File(...), ctx: Ctx = Depends(write_ctx)):
    data, mime = await files.read_upload(file, ("image/png", "image/jpeg"), max_mb=2)
    try:
        bk.open_image(data)
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "That logo is too large or isn't a picture. Use a PNG or JPG under 5000 × 5000 pixels.")
    f = await files.put(ctx.ws, data, file.filename or "logo", mime, "logo")
    kit = await _kit(ctx.ws)
    b = await db().businesses.find_one({"owner_id": ctx.ws}) or {}
    design = kit.get("design") or bk.default_design(b.get("name", ""))
    old = design.get("logo_file_id")
    design["logo_file_id"] = f["_id"]
    await db().brand_kits.update_one({"_id": ctx.ws}, {"$set": {"design": design, "updated_at": now()}}, upsert=True)
    if old:
        await files.delete(old, ctx.ws)
    return {**await get_kit(ctx), "suggested_colors": bk.from_image(data)}


@router.get("/logo")
async def get_logo(ctx: Ctx = Depends(read_ctx)):
    kit = await _kit(ctx.ws)
    f = await files.get((kit.get("design") or {}).get("logo_file_id") or "-", ctx.ws)
    if not f:
        raise HTTPException(404, "No logo yet")
    return Response(bytes(f["data"]), media_type=f["content_type"], headers={"Cache-Control": "private, max-age=300"})


@router.get("/colors/website")
async def colors_from_website(ctx: Ctx = Depends(read_ctx)):
    scan = await db().site_scans.find_one({"_id": ctx.ws}, {"colour": 1}) or {}
    site = await db().sites.find_one({"owner_id": ctx.ws}, {"accent": 1}) or {}
    base = scan.get("colour") or site.get("accent")
    if not base or not bk.HEX.match(base):
        raise HTTPException(404, "We don't have a colour from your website yet.")
    return {"primary": base.upper(), "secondary": bk.partner(base), "accent": "#F2A93B"}


class RenderIn(BaseModel):
    fields: dict[str, str] = Field(default_factory=dict)
    format: str = Field(default="png", pattern="^(png|pdf)$")


async def _render_ctx(ctx: Ctx):
    kit = await _kit(ctx.ws)
    b = await db().businesses.find_one({"owner_id": ctx.ws}) or {}
    site = await db().sites.find_one({"owner_id": ctx.ws}, {"slug": 1, "status": 1, "accent": 1})
    design = kit.get("design") or bk.default_design(b.get("name", ""), (site or {}).get("accent"))
    url = site_url(site["slug"]) if site and site.get("status") == "live" else None
    return kit, b, design, url


@router.get("/designs/{key}")
async def design_defaults(key: str, ctx: Ctx = Depends(read_ctx)):
    if key not in designs.TEMPLATES:
        raise HTTPException(404, "Unknown design")
    kit, b, _design, url = await _render_ctx(ctx)
    return {"fields": designs.defaults(key, kit.get("messages") or {}, b, url)}


@router.post("/designs/{key}")
async def render_design(key: str, body: RenderIn, ctx: Ctx = Depends(read_ctx)):
    t = designs.TEMPLATES.get(key)
    if not t:
        raise HTTPException(404, "Unknown design")
    if body.format == "pdf" and not t.pdf_dpi:
        raise HTTPException(400, "This design is an image.")
    kit, b, design, url = await _render_ctx(ctx)
    fields = {**designs.defaults(key, kit.get("messages") or {}, b, url), **{k: v for k, v in body.fields.items() if k in {f.key for f in t.fields}}}
    img = designs.render(key, fields, design, b, await _logo({**kit, "_id": ctx.ws}))
    await track("design", ctx.ws, key=key)
    if body.format == "pdf":
        return Response(designs.pdf([img], t.pdf_dpi), media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="{key}.pdf"'})
    return Response(designs.png(img), media_type="image/png", headers={"Content-Disposition": f'inline; filename="{key}.png"'})


@router.get("/pdf")
async def kit_pdf(ctx: Ctx = Depends(read_ctx)):
    kit, b, design, _url = await _render_ctx(ctx)
    if not kit.get("messages"):
        raise HTTPException(400, "Write your brand kit first.")
    pages = designs.kit_pages(kit["messages"], design, b, await _logo({**kit, "_id": ctx.ws}))
    return Response(designs.pdf(pages, 150), media_type="application/pdf", headers={"Content-Disposition": 'attachment; filename="brand-kit.pdf"'})


# ───────────────────────── follows the Knowledge Hub ─────────────────────────
async def _exists(ws: str) -> bool:
    return bool((await _kit(ws)).get("messages"))


async def _never(ws: str) -> bool:
    return False   # item by item: untouched items refresh, edited ones are kept and flagged


async def _refresh(owner: dict, sections: set) -> str | None:
    ws = owner["_id"]
    kit = await _kit(ws)
    edited = edited_of(kit)
    if sections <= {"contact"}:   # a phone or email change: only the signature follows, written without AI
        if edited.get("sales.email_signature"):
            return "kept_edits"
        p = await profile_of(ws)
        sig = "\n".join(x for x in (p.get("name", ""), p.get("city", ""), f"WhatsApp {p['whatsapp']}" if p.get("whatsapp") else "") if x)
        await db().brand_kits.update_one({"_id": ws}, {"$set": {"messages.sales.email_signature": sig, "generated.sales.email_signature": sig}})
        return None
    new = await _generate(owner)
    messages = bk.merge_refresh(kit["messages"], new, edited)
    # write only if nobody changed the kit while the AI was writing; otherwise the owner's version stays and the banner says so
    r = await db().brand_kits.update_one({"_id": ws, "updated_at": kit.get("updated_at")},
                                         {"$set": {"messages": messages, "generated": new, "updated_at": now(), "brain_refreshed_at": now()}})
    if not r.matched_count:
        return "kept_edits"
    await _write_voice(ws, messages)
    return "kept_edits" if edited else None


async def _overwrite(owner: dict) -> None:
    new = await _generate(owner)
    await db().brand_kits.update_one({"_id": owner["_id"]}, {"$set": {"messages": new, "generated": new, "edited_paths": [], "updated_at": now()},
                                                              "$unset": {"edited": ""}})
    await _write_voice(owner["_id"], new)


async def _has_edits(ws: str) -> bool:
    return bool(edited_of(await _kit(ws)))


knowledge.register(knowledge.Dependent("brand_kit", "Brand kit messages", "brand_kit", "/brand-kit", {"basics", "brand", "contact"}, _exists, _never,
                                       _refresh, overwrite=_overwrite, note="Only the items you edited are waiting for you.", order=2,
                                       cost=lambda sections: 0 if sections <= {"contact"} else 1, has_edits=_has_edits))
