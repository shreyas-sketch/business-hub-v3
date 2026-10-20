"""
Website with lead magnet (v2.1, 3.3).

1. Edit by prompt — POST /api/site/prompt {instruction}: one AI run returns a patch to only the sections the instruction is
   about. Safety: a number in the patch that isn't in the Business Brain (or already on the page) is taken out of the patch
   and listed as a new fact, so the owner can add it to the Knowledge Hub first (POST /api/knowledge/facts). The patch waits
   as a preview (sites.prompt_preview) until Apply; Discard drops it; Undo restores the content before the last Apply
   (site_versions, last 10). An applied prompt edit is the owner's edit: a Knowledge Hub refresh keeps it and shows the banner.
2. Upload your own lead magnet — up to 5 PDFs per owner (≤ UPLOAD_MAX_MB, checked by their first bytes), each with a title,
   a description and a cover (drawn from the brand kit when none is uploaded), its own address /guide/<slug>, a published
   switch and a download count. Kept in `magnet_files` (the older `lead_magnets` collection holds the AI-written guide,
   keyed by owner) with the files themselves in the `files` collection (decision D6, see app/files.py).
3. Public pages — /s/<site>/guide/<slug>: a form (name, mobile, optional email). After it's filled the lead is saved like
   any website lead (source "lead_magnet", owner alert, automations, Employz.ai sync) and the download link is shown on
   screen, and emailed when the visitor gave an email. The PDF only downloads with a signed link that expires.
"""
import base64
import hashlib
import hmac
import re
import time
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

from .. import brand_kit as bk, designs, files, mailer, plans
from ..ai import site_tasks
from ..ai.tasks import profile_text
from ..config import settings
from ..context import Ctx, feature
from ..db import db, now, public
from ..services import new_id, run_ai, track
from ..sites import base_for, site_url
from .hub import SiteContent, profile_of, site_view

router = APIRouter()
MAX_MAGNETS = 5
LINK_DAYS = 7
VERSIONS_KEPT = 10
SECTION_NAMES = {"headline": "the headline", "subheadline": "the line under the headline", "cta": "the button text", "about": "About us",
                 "offers": "What you offer", "why": "Why customers choose you", "steps": "How it works", "faq": "the questions"}
NUM = re.compile(r"\d+(?:[.,]\d+)*")


# ═════════════════════════ 1. Edit the website by prompt ═════════════════════════
class PromptIn(BaseModel):
    instruction: str = Field(min_length=3, max_length=500)


def _numbers(value) -> set[str]:
    """Every number in a value (strings inside lists and dicts too), without thousands separators: '1,85,000' → '185000'."""
    if isinstance(value, dict):
        return set().union(*(_numbers(v) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(_numbers(v) for v in value)) if value else set()
    return {n.replace(",", "").rstrip(".") for n in NUM.findall(str(value or ""))}


def _owner_facts(instruction: str, ai_facts: list[str], brain: str) -> list[str]:
    """New facts worth offering for the Knowledge Hub: only what the owner's own words state. A fact the AI lists is kept
    only when its numbers (or, without numbers, its words) are in the instruction."""
    said = _numbers(instruction)
    brain_l, text_l = brain.lower(), instruction.lower()
    out: list[str] = []

    def add(f: str) -> None:
        f = re.sub(r"\s+", " ", f).strip(" ,.")
        if f and f.lower() not in brain_l and f.lower() not in (x.lower() for x in out):
            out.append(f)
    for f in site_tasks.facts_in(instruction):
        add(f)
    for f in ai_facts:
        nums = _numbers(f)
        if (nums and nums <= said and not any(n in _numbers(x) for x in out for n in nums)) or (not nums and f.lower() in text_l):
            add(f)
    return out[:8]


def _guard(patch: dict, content: dict, brain: str, ai_facts: list[str], instruction: str) -> tuple[dict, list[str], list[dict]]:
    """Keeps only real sections whose numbers are all in the Brain or already on the page.
    A section held back for a number the owner stated → that fact is offered for the Knowledge Hub.
    A section held back for a number the AI made up → left out, and the preview says so; nothing is offered."""
    allowed = _numbers(brain) | _numbers(content)
    said = _numbers(instruction)
    facts = _owner_facts(instruction, ai_facts, brain)
    kept, left_out = {}, []
    for key, value in patch.items():
        if key not in SiteContent.model_fields:
            continue
        unknown = _numbers(value) - allowed
        if unknown:
            invented = sorted(unknown - said)
            left_out.append({"section": key, "reason": "invented" if invented else "new_fact", "numbers": invented or sorted(unknown)})
            for n in sorted(unknown & said):   # the owner's own number: offer their words for it
                if not any(n in _numbers(f) for f in facts):
                    m = re.search(rf"\b{re.escape(n)}\b[^.,;!?\n]{{0,30}}", instruction)
                    facts.append(re.sub(r"\s+", " ", m.group(0) if m else n).strip(" ,."))
            continue
        kept[key] = value
    # the patch must still make a valid page; a section that doesn't is left out
    valid = {}
    for key, value in kept.items():
        try:
            SiteContent.model_validate({**content, **valid, key: value})
            valid[key] = SiteContent.model_validate({**content, key: value}).model_dump()[key]
        except Exception:  # noqa: BLE001
            continue
    return valid, facts[:8], left_out


def _shape(r: dict) -> dict:
    if not isinstance(r, dict) or not isinstance(r.get("patch"), dict):
        raise ValueError("no patch")
    return {"patch": r["patch"], "new_facts": [str(x)[:120] for x in (r.get("new_facts") or []) if str(x).strip()][:8],
            "notes": [str(x)[:200] for x in (r.get("notes") or []) if str(x).strip()][:4]}


async def _site(ws: str) -> dict:
    site = await db().sites.find_one({"owner_id": ws})
    if not site:
        raise HTTPException(400, "Create your website first.")
    return site


def _preview_view(site: dict) -> dict | None:
    pv = site.get("prompt_preview")
    if not pv:
        return None
    left = pv.get("left_out") or []
    return {"instruction": pv["instruction"], "changed": pv["changed"], "before": {k: site["content"].get(k) for k in pv["changed"]},
            "after": pv["patch"], "new_facts": pv.get("new_facts") or [], "notes": pv.get("notes") or [], "at": pv["at"].isoformat(),
            "left_out": left, "invented_left_out": any(x["reason"] == "invented" for x in left)}


@router.post("/api/site/prompt")
async def site_prompt(body: PromptIn, ctx: Ctx = Depends(feature("website", "owner"))):
    """One AI run: a preview of the changed sections only. Nothing on the page changes until Apply."""
    site = await _site(ctx.ws)
    p = await profile_of(ctx.ws)
    content = site["content"]
    r = await run_ai(ctx.owner, "site_prompt", site_tasks.site_patch(p, content, body.instruction.strip()),
                     f"Website edit — {body.instruction.strip()[:60]}", save_output=False, check=_shape)
    patch, facts, left_out = _guard(r["patch"], content, profile_text(p), r["new_facts"], body.instruction.strip())
    changed = [k for k, v in patch.items() if v != content.get(k)]
    patch = {k: patch[k] for k in changed}
    pv = {"instruction": body.instruction.strip(), "patch": patch, "changed": changed, "new_facts": facts, "notes": r["notes"],
          "left_out": left_out, "at": now(), "base_updated_at": site.get("updated_at")}
    await db().sites.update_one({"_id": site["_id"]}, {"$set": {"prompt_preview": pv}})
    await track("site_prompt", ctx.ws, changed=len(changed))
    return _preview_view({**site, "prompt_preview": pv})


@router.get("/api/site/prompt")
async def get_prompt(ctx: Ctx = Depends(feature("website", "owner"))):
    site = await db().sites.find_one({"owner_id": ctx.ws})
    if not site:
        return {"preview": None, "can_undo": False}
    return {"preview": _preview_view(site), "can_undo": bool(await db().site_versions.count_documents({"site_id": site["_id"]}, limit=1))}


@router.post("/api/site/prompt/apply")
async def apply_prompt(ctx: Ctx = Depends(feature("website", "owner"))):
    site = await _site(ctx.ws)
    pv = site.get("prompt_preview")
    if not pv:
        raise HTTPException(400, "There's no change waiting. Ask for one first.")
    if pv.get("base_updated_at") != site.get("updated_at"):
        await db().sites.update_one({"_id": site["_id"]}, {"$unset": {"prompt_preview": ""}})
        raise HTTPException(409, "Your page changed since this preview was made. Ask again and you'll see it on the new version.")
    if not pv["changed"]:
        await db().sites.update_one({"_id": site["_id"]}, {"$unset": {"prompt_preview": ""}})
        raise HTTPException(400, "This preview didn't change anything on the page.")
    content = SiteContent.model_validate({**site["content"], **pv["patch"]}).model_dump()
    # what this edit changed, section by section, so Undo can put back only those sections
    await db().site_versions.insert_one({"_id": new_id(), "site_id": site["_id"], "owner_id": ctx.ws, "reason": "prompt",
                                         "instruction": pv["instruction"], "changed": pv["changed"],
                                         "before": {k: site["content"].get(k) for k in pv["changed"]},
                                         "after": {k: content.get(k) for k in pv["changed"]}, "at": now()})
    old = [v["_id"] async for v in db().site_versions.find({"site_id": site["_id"]}, {"_id": 1}).sort("at", -1).skip(VERSIONS_KEPT)]
    if old:
        await db().site_versions.delete_many({"_id": {"$in": old}})
    # an owner's edit: generated_content stays, so a Knowledge Hub refresh keeps this and shows the banner instead
    await db().sites.update_one({"_id": site["_id"]}, {"$set": {"content": content, "updated_at": now(), "updated_by": ctx.actor},
                                                       "$unset": {"prompt_preview": ""}})
    await track("site_prompt_applied", ctx.ws)
    return site_view(await db().sites.find_one({"_id": site["_id"]}), ctx.owner)


@router.post("/api/site/prompt/discard")
async def discard_prompt(ctx: Ctx = Depends(feature("website", "owner"))):
    site = await _site(ctx.ws)
    await db().sites.update_one({"_id": site["_id"]}, {"$unset": {"prompt_preview": ""}})
    return site_view(await db().sites.find_one({"_id": site["_id"]}), ctx.owner)


@router.post("/api/site/prompt/undo")
async def undo_prompt(ctx: Ctx = Depends(feature("website", "owner"))):
    """Puts back only the sections the last prompt edit changed — and only while they still hold that edit's text, so an
    edit the owner made by hand since is never wiped."""
    site = await _site(ctx.ws)
    v = await db().site_versions.find_one({"site_id": site["_id"]}, sort=[("at", -1)])
    if not v or not v.get("changed"):
        raise HTTPException(400, "There's nothing to undo.")
    current = site["content"]
    moved = [k for k in v["changed"] if current.get(k) != v["after"].get(k)]
    if moved:
        names = ", ".join(SECTION_NAMES.get(k, k) for k in moved)
        raise HTTPException(409, f"You've changed {names} since that prompt edit, so Undo would overwrite your own words. Change it back by hand instead.")
    content = SiteContent.model_validate({**current, **v["before"]}).model_dump()
    await db().sites.update_one({"_id": site["_id"], "updated_at": site.get("updated_at")},
                                {"$set": {"content": content, "updated_at": now(), "updated_by": ctx.actor}, "$unset": {"prompt_preview": ""}})
    await db().site_versions.delete_one({"_id": v["_id"]})
    return site_view(await db().sites.find_one({"_id": site["_id"]}), ctx.owner)


# ═════════════════════════ 2. Upload your own lead magnet ═════════════════════════
RESERVED_SLUGS = {"get"}     # /guide/get is the AI guide's form


def _slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40].strip("-")
    s = s if len(s) >= 3 else f"guide-{s}".strip("-")
    return f"{s}-1" if s in RESERVED_SLUGS else s


async def _unique_slug(ws: str, title: str) -> str:
    base = _slug(title)
    for i in range(1, 50):
        slug = base if i == 1 else f"{base}-{i}"
        if not await db().magnet_files.find_one({"owner_id": ws, "slug": slug}, {"_id": 1}):
            return slug
    return f"{base}-{new_id()[:6]}"


async def _cover(ws: str, title: str, description: str) -> bytes:
    """A cover in the owner's brand: the brand kit's colours, fonts and logo on the catalogue-cover template."""
    from .brandkit import _kit, _logo
    kit = await _kit(ws)
    b = await db().businesses.find_one({"owner_id": ws}) or {}
    site = await db().sites.find_one({"owner_id": ws}, {"accent": 1}) or {}
    design = kit.get("design") or bk.default_design(b.get("name", ""), site.get("accent"))
    img = designs.render("catalogue_cover", {"title": title[:60], "sub": (description or "A free download")[:120]}, design, b,
                         await _logo({**kit, "_id": ws}))
    return designs.png(img)


def _magnet_view(m: dict, site: dict | None) -> dict:
    out = public(m)
    out["url"] = site_url(site["slug"], f"/guide/{m['slug']}") if site else None
    out["cover_url"] = f"/api/lead-magnets/{m['_id']}/cover"
    out["file_url"] = f"/api/lead-magnets/{m['_id']}/file"
    out["form_fills"] = m.get("leads", 0)          # people who filled the form (each one a lead)
    out["downloads"] = m.get("downloads", 0)       # times the PDF was actually served with a valid link
    return out


@router.get("/api/lead-magnets")
async def list_magnets(ctx: Ctx = Depends(feature("lead_magnet", "owner"))):
    site = await db().sites.find_one({"owner_id": ctx.ws}, {"slug": 1, "status": 1})
    items = [_magnet_view(m, site) async for m in db().magnet_files.find({"owner_id": ctx.ws}).sort("created_at", 1)]
    return {"items": items, "max": MAX_MAGNETS, "max_mb": settings.upload_max_mb, "site_live": bool(site and site.get("status") == "live")}


def _too_big(request: Request, limit: int) -> bool:
    try:
        return int(request.headers.get("content-length") or 0) > limit
    except ValueError:
        return True


@router.post("/api/lead-magnets")
async def upload_magnet(request: Request, ctx: Ctx = Depends(feature("lead_magnet", "owner"))):
    """multipart: file (PDF), title, description, optional cover (PNG/JPG). At most 5 per owner, enforced after the insert
    so two uploads at once can't make a sixth; anything stored for an upload that fails is removed."""
    if _too_big(request, (settings.upload_max_mb + 1) * 1024 * 1024):     # refused before a byte of the body is read
        raise HTTPException(413, f"That file is bigger than {settings.upload_max_mb} MB.")
    if await db().magnet_files.count_documents({"owner_id": ctx.ws}) >= MAX_MAGNETS:
        raise HTTPException(400, f"You can keep {MAX_MAGNETS} lead magnets. Remove one to add another.")
    form = await request.form(max_files=2, max_fields=6, max_part_size=1024 * 1024)
    file, cover = form.get("file"), form.get("cover")
    title, description = str(form.get("title") or "").strip()[:120], str(form.get("description") or "").strip()[:500]
    if len(title) < 3:
        raise HTTPException(400, "Give your download a title (at least 3 letters).")
    if file is None or isinstance(file, str):
        raise HTTPException(400, "Choose the PDF to upload.")
    stored: list[str] = []
    doc = None
    try:
        data, _mime = await files.read_upload(file, ("application/pdf",))
        pdf = await files.put(ctx.ws, data, file.filename or f"{_slug(title)}.pdf", "application/pdf", "lead_magnet")
        stored.append(pdf["_id"])
        if cover is not None and not isinstance(cover, str) and cover.filename:
            cdata, cmime = await files.read_upload(cover, ("image/png", "image/jpeg"), max_mb=2)
            try:
                bk.open_image(cdata)
            except Exception:  # noqa: BLE001
                raise HTTPException(400, "That cover is too large or isn't a picture. Use a PNG or JPG under 5000 × 5000 pixels.")
            cov, generated = await files.put(ctx.ws, cdata, cover.filename, cmime, "magnet_cover"), False
        else:
            cov, generated = await files.put(ctx.ws, await _cover(ctx.ws, title, description), "cover.png", "image/png", "magnet_cover"), True
        stored.append(cov["_id"])
        for _ in range(3):
            candidate = {"_id": new_id(), "owner_id": ctx.ws, "slug": await _unique_slug(ctx.ws, title), "title": title, "description": description,
                         "file_id": pdf["_id"], "filename": pdf["filename"], "size": pdf["size"], "cover_file_id": cov["_id"],
                         "cover_generated": generated, "published": True, "downloads": 0, "leads": 0, "created_at": now(), "updated_at": now()}
            try:
                await db().magnet_files.insert_one(candidate)
                doc = candidate
                break
            except DuplicateKeyError:
                continue
        if doc is None:
            raise HTTPException(409, "Two uploads with the same name arrived at once. Please try again.")
        # the limit, made safe against uploads at the same moment: the earliest five stay, a later one is rolled back
        ids = [m["_id"] async for m in db().magnet_files.find({"owner_id": ctx.ws}, {"_id": 1}).sort([("created_at", 1), ("_id", 1)])]
        if doc["_id"] in ids[MAX_MAGNETS:]:
            await db().magnet_files.delete_one({"_id": doc["_id"]})
            doc = None
            raise HTTPException(400, f"You can keep {MAX_MAGNETS} lead magnets. Remove one to add another.")
    except BaseException:
        for fid in stored:          # nothing half-stored is left behind
            await files.delete(fid, ctx.ws)
        raise
    await track("lead_magnet_uploaded", ctx.ws)
    site = await db().sites.find_one({"owner_id": ctx.ws}, {"slug": 1})
    return _magnet_view(doc, site)


class MagnetPatch(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    published: bool | None = None


async def _mine(ws: str, magnet_id: str) -> dict:
    m = await db().magnet_files.find_one({"_id": magnet_id, "owner_id": ws})
    if not m:
        raise HTTPException(404, "Not found")
    return m


@router.patch("/api/lead-magnets/{magnet_id}")
async def edit_magnet(magnet_id: str, body: MagnetPatch, ctx: Ctx = Depends(feature("lead_magnet", "owner"))):
    m = await _mine(ctx.ws, magnet_id)
    patch = {k: (v.strip() if isinstance(v, str) else v) for k, v in body.model_dump(exclude_none=True).items()}
    if patch:
        await db().magnet_files.update_one({"_id": m["_id"]}, {"$set": {**patch, "updated_at": now()}})
        if m.get("cover_generated") and ("title" in patch or "description" in patch):   # a drawn cover follows the new words
            await files.delete(m["cover_file_id"], ctx.ws)
            cov = await files.put(ctx.ws, await _cover(ctx.ws, patch.get("title", m["title"]), patch.get("description", m["description"])),
                                  "cover.png", "image/png", "magnet_cover")
            await db().magnet_files.update_one({"_id": m["_id"]}, {"$set": {"cover_file_id": cov["_id"]}})
    site = await db().sites.find_one({"owner_id": ctx.ws}, {"slug": 1})
    return _magnet_view(await db().magnet_files.find_one({"_id": m["_id"]}), site)


@router.delete("/api/lead-magnets/{magnet_id}")
async def delete_magnet(magnet_id: str, ctx: Ctx = Depends(feature("lead_magnet", "owner"))):
    m = await _mine(ctx.ws, magnet_id)
    await db().magnet_files.delete_one({"_id": m["_id"]})
    for fid in (m.get("file_id"), m.get("cover_file_id")):
        if fid:
            await files.delete(fid, ctx.ws)
    return {"ok": True}


def _file_response(f: dict | None, name: str, inline: bool) -> Response:
    if not f:
        raise HTTPException(404, "Not found")
    safe = re.sub(r'[^A-Za-z0-9._ -]', "", name)[:100] or "download.pdf"
    return Response(bytes(f["data"]), media_type=f["content_type"],
                    headers={"Content-Disposition": f'{"inline" if inline else "attachment"}; filename="{safe}"',
                             "Cache-Control": "private, no-store", "X-Robots-Tag": "noindex"})


@router.get("/api/lead-magnets/{magnet_id}/file")
async def owner_file(magnet_id: str, ctx: Ctx = Depends(feature("lead_magnet", "owner"))):
    m = await _mine(ctx.ws, magnet_id)
    return _file_response(await files.get(m["file_id"], ctx.ws), m.get("filename") or f"{m['slug']}.pdf", True)


@router.get("/api/lead-magnets/{magnet_id}/cover")
async def owner_cover(magnet_id: str, ctx: Ctx = Depends(feature("lead_magnet", "owner"))):
    m = await _mine(ctx.ws, magnet_id)
    return _file_response(await files.get(m["cover_file_id"], ctx.ws), "cover.png", True)


# ═════════════════════════ 3. The public pages ═════════════════════════
FORM_MAX_BYTES = 20 * 1024


async def _small_body(request: Request, limit: int) -> bool:
    """Reads the body only up to `limit` bytes: False (refuse) as soon as it's bigger, whatever Content-Length says."""
    if _too_big(request, limit):
        return False
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            return False
    request._body = body          # Starlette parses the form from this cached body
    return True


def _sig(magnet_id: str, exp: int) -> str:
    msg = f"magnet:{magnet_id}.{exp}".encode()
    return base64.urlsafe_b64encode(hmac.new(files._secret(), msg, hashlib.sha256).digest()[:18]).decode().rstrip("=")


def download_token(magnet_id: str, days: int = LINK_DAYS) -> str:
    """A download link for one lead magnet, signed and expiring (default 7 days)."""
    exp = int(time.time()) + days * 86400
    return f"{exp}.{_sig(magnet_id, exp)}"


def token_ok(magnet_id: str, token: str | None) -> bool:
    try:
        exp, sig = str(token or "").split(".")
        return int(exp) > time.time() and hmac.compare_digest(_sig(magnet_id, int(exp)), sig)
    except (ValueError, TypeError):
        return False


async def published_magnets(owner: dict) -> list[dict]:
    """The owner's published uploads (for the /guide pages)."""
    if not plans.has(owner, "lead_magnet"):
        return []
    return [m async for m in db().magnet_files.find({"owner_id": owner["_id"], "published": True}, {"title": 1, "slug": 1, "description": 1})
            .sort("created_at", 1).limit(MAX_MAGNETS)]


async def _public(slug: str, mslug: str):
    from .public import _site_and_owner
    site, owner, business = await _site_and_owner(slug, None, False)
    if not site or not plans.has(owner, "lead_magnet"):
        return None
    m = await db().magnet_files.find_one({"owner_id": owner["_id"], "slug": mslug.lower(), "published": True})
    if not m:
        return None
    return site, owner, business, m


def _page(request: Request, site: dict, business: dict, m: dict, others: list[dict], **extra) -> str:
    from .public import FORM_ERRORS, templates
    base = base_for(request, site["slug"])
    return templates.get_template(extra.pop("template", "magnet.html")).render(
        m=m, name=business.get("name", ""), accent=site.get("accent", "#1F4E79"), base=base, cover=f"{base}/guide/{m['slug']}/cover.png",
        others=[o for o in others if o["slug"] != m["slug"]], form_error=FORM_ERRORS.get(request.query_params.get("err", ""))
        or ({"email": "That email address doesn't look right. Leave it empty or check it.",
             "expired": "That download link has expired. Fill in the form for a fresh one."}.get(request.query_params.get("err", ""))),
        program=settings.program_name, year=now().year, **extra)


@router.get("/s/{slug}/guide/{mslug}", response_class=HTMLResponse)
async def magnet_page(slug: str, mslug: str, request: Request):
    from .public import _count, not_found
    found = await _public(slug, mslug)
    if not found:
        return not_found()
    site, owner, business, m = found
    await _count(site, "magnet_views")
    return HTMLResponse(_page(request, site, business, m, await published_magnets(owner)), headers={"Cache-Control": "no-cache"})


@router.post("/s/{slug}/guide/{mslug}", response_class=HTMLResponse)
async def magnet_get(slug: str, mslug: str, request: Request):
    """The visitor fills the form: the lead is saved, the link shows on screen, and it's emailed when they gave an email."""
    from .public import _checked, _count, not_found, save_lead
    base = base_for(request, slug.lower())
    found = await _public(slug, mslug)
    if not found:
        return not_found()
    site, owner, business, m = found
    form = f"{base}/guide/{m['slug']}"
    if not await _small_body(request, FORM_MAX_BYTES):      # a name, a number and an email are never this big
        raise HTTPException(413, "That's too much for this form.")
    try:
        data = {k: v for k, v in (await request.form(max_files=0, max_fields=10)).items() if isinstance(v, str)}
    except Exception:  # noqa: BLE001
        data = {}
    email = str(data.get("email") or "").strip().lower()[:254]
    if email and not mailer.valid(email):
        return RedirectResponse(f"{form}?err=email#get", 303)
    try:
        checked = await _checked(slug, request, data)
    except HTTPException as e:
        return RedirectResponse(f"{form}?err={'busy' if e.status_code == 429 else 'phone'}#get", 303)
    if not checked[0]:          # the honeypot: a bot filled the hidden field — no lead, no file
        return RedirectResponse(form, 303)
    _, _, _, name, phone = checked
    lead = await save_lead(site, owner, business, name, phone, f"Downloaded: {m['title']}"[:300], "lead_magnet",
                           magnet_id=m["_id"], **({"email": email} if email else {}))
    await db().magnet_files.update_one({"_id": m["_id"]}, {"$inc": {"leads": 1}})
    await _count(site, "magnet_leads")
    token = download_token(m["_id"])
    link = f"{base}/guide/{m['slug']}/file?t={quote(token)}"
    emailed = False
    if email:
        absolute = site_url(site["slug"], f"/guide/{m['slug']}/file?t={quote(token)}")
        biz = business.get("name") or "us"
        text = (f"Hello {name.split()[0] if name.split() else ''},\n\nThank you for your interest. Here is your free download, "
                f"\"{m['title']}\":\n{absolute}\n\nThe link works for {LINK_DAYS} days.\n\n{biz}").replace("Hello ,", "Hello,")
        r = await mailer.send(owner=owner, to=email, subject=f"Your download: {m['title']}"[:150], text=text, kind="lead_magnet",
                              from_name=biz, reply_to=business.get("email"), ref={"magnet_id": m["_id"], "lead_id": lead["_id"]})
        emailed = bool(r.get("sent"))
    await track("lead_magnet_download", owner["_id"])
    return HTMLResponse(_page(request, site, business, m, await published_magnets(owner), template="magnet_done.html", link=link,
                              emailed=emailed, email=email), headers={"Cache-Control": "no-store"})


@router.get("/s/{slug}/guide/{mslug}/file")
async def magnet_file(slug: str, mslug: str, request: Request, t: str = ""):
    """The PDF itself — only with a valid, unexpired link from the form; anything else goes back to the form."""
    from .public import _count, not_found
    found = await _public(slug, mslug)
    if not found:
        return not_found()
    site, owner, business, m = found
    form = f"{base_for(request, site['slug'])}/guide/{m['slug']}"
    if not token_ok(m["_id"], t):
        return RedirectResponse(f"{form}?err=expired#get" if t else form, 303)
    f = await files.get(m["file_id"], owner["_id"])
    if not f:
        return not_found()
    await db().magnet_files.update_one({"_id": m["_id"]}, {"$inc": {"downloads": 1}})
    await _count(site, "magnet_downloads")
    return _file_response(f, m.get("filename") or f"{m['slug']}.pdf", False)


@router.get("/s/{slug}/guide/{mslug}/cover.png")
async def magnet_cover(slug: str, mslug: str):
    from .public import not_found
    found = await _public(slug, mslug)
    if not found:
        return not_found()
    _site, owner, _b, m = found
    f = await files.get(m["cover_file_id"], owner["_id"])
    if not f:
        return not_found()
    return Response(bytes(f["data"]), media_type=f["content_type"], headers={"Cache-Control": "public, max-age=3600"})


async def ensure_indexes() -> None:
    await db().magnet_files.create_index([("owner_id", 1), ("slug", 1)], unique=True)
    await db().site_versions.create_index([("site_id", 1), ("at", -1)])

