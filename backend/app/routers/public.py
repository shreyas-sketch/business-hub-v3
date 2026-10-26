"""
Public surfaces: member websites, their inquiry form, Chat on WhatsApp, the guide (lead magnet), the digital visiting card,
the showcase and invite lookups.

Each website answers at <name>.SITES_DOMAIN (and at an owner's own domain once live) through the rewrite in main.py,
and at APP_URL/s/<name> on the hub itself. `base` is the prefix links on the page use: '' on the website's own host.
"""
import re
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .. import photos as photos_mod
from .. import plans
from ..config import settings
from ..db import IST, db, now
from ..messaging import whatsapp_template
from ..security import client_ip, normalise_phone, optional_user, rate_limit
from .. import dashboard_cache
from ..services import new_id, track
from ..sites import base_for, site_url
from .hub import wa_digits

router = APIRouter()
templates = Environment(loader=FileSystemLoader(Path(__file__).resolve().parent.parent / "templates"),
                        autoescape=select_autoescape(["html"]))


async def _site_and_owner(slug: str, viewer: dict | None, preview: bool):
    site = await db().sites.find_one({"slug": slug.lower()})
    if not site:
        return None, None, None
    owner = await db().users.find_one({"_id": site["owner_id"]})
    is_owner = bool(viewer and viewer["_id"] == site["owner_id"])
    if not owner or owner.get("disabled") or (site["status"] != "live" and not (preview and is_owner)):
        return None, None, None
    business = await db().businesses.find_one({"owner_id": owner["_id"]}) or {}
    return site, owner, business


def not_found() -> HTMLResponse:
    return HTMLResponse(templates.get_template("not_found.html").render(app_url=settings.app_url), status_code=404)


_not_found = not_found  # older name


FORM_ERRORS = {"phone": "Please add your name and a 10-digit mobile number.",
               "busy": "Too many messages just now. Please try again in a few minutes, or message us on WhatsApp."}


async def _count(site: dict, field: str) -> None:
    """Daily counters on a website (views, WhatsApp taps, guide downloads) for the funnel. Never breaks the page."""
    try:
        day = now().astimezone(IST).strftime("%Y-%m-%d")
        await db().site_views.update_one({"_id": f"{site['_id']}:{day}"},
                                         {"$inc": {field: 1}, "$setOnInsert": {"site_id": site["_id"], "ws": site["owner_id"], "day": day}},
                                         upsert=True)
    except Exception:  # noqa: BLE001
        try:
            await db().site_views.update_one({"_id": f"{site['_id']}:{now().astimezone(IST).strftime('%Y-%m-%d')}"}, {"$inc": {field: 1}})
        except Exception:  # noqa: BLE001
            pass


async def _extras(owner: dict, ws: str) -> dict:
    """Optional website sections: the offer ladder and the guide, when the owner has switched them on."""
    out = {"ladder": None, "guide": None, "logo": "", "fonts": None}
    if plans.has(owner, "offer_ladder"):
        ladder = await db().offer_ladders.find_one({"_id": ws, "show": True})
        if ladder:
            out["ladder"] = ladder.get("content")
    if plans.has(owner, "lead_magnet"):
        # v2.3: the lead magnet the hub built for them comes first; its button opens its own download page
        built = await db().magnet_files.find_one({"owner_id": ws, "published": True, "source": "ai"}, {"title": 1, "slug": 1, "landing": 1},
                                                 sort=[("created_at", -1)])
        g = None if built else await db().lead_magnets.find_one({"_id": ws, "published": True})
        if built:
            land = built.get("landing") or {}
            out["guide"] = {"title": built["title"], "subtitle": land.get("who_for") or land.get("headline") or "", "slug": built["slug"]}
        elif g:
            out["guide"] = g.get("content")
    scan = await db().site_scans.find_one({"_id": ws}, {"logo": 1})
    out["logo"] = (scan or {}).get("logo", "")
    # v2.3: the brand kit's heading and body fonts on the website (when the owner has a brand kit)
    kit = await db().brand_kits.find_one({"_id": ws}, {"design.fonts": 1}) or {}
    fonts = (kit.get("design") or {}).get("fonts") or {}
    out["fonts"] = {k: WEB_FONTS[v] for k, v in fonts.items() if k in ("heading", "body") and v in WEB_FONTS} or None
    return out


@router.get("/s/{slug}/photo/{photo_id}")
async def site_photo(slug: str, photo_id: str, viewer: dict | None = Depends(optional_user)):
    site, owner, _ = await _site_and_owner(slug, viewer, True)
    if not site:
        return Response(status_code=404)
    p = await db().site_photos.find_one({"_id": photo_id[:40], "owner_id": owner["_id"]})
    if not p:
        return Response(status_code=404)
    return Response(bytes(p["data"]), media_type=p["type"], headers={"Cache-Control": "public, max-age=604800, immutable"})


# The brand kit's fonts, as Google Fonts families (heading weights, body weights)
SITE_FONTS = {"poppins": ("Poppins", "600;700", "400;500;600"), "mukta": ("Mukta", "600;700", "400;500;600"),
              "hind": ("Hind", "600;700", "400;500;600"), "baloo2": ("Baloo 2", "600;700", "400;500;600"),
              "martel": ("Martel", "700;800", "400;600"), "yatra": ("Yatra One", "400", "400"),
              "playfair": ("Playfair Display", "600;700", "400;500"), "inter": ("Inter", "600;700", "400;500;600"),
              "noto": ("Noto Sans Devanagari", "600;700", "400;500;600")}


def _mix(a: str, b: str, t: float) -> str:
    """t of colour a over colour b (#RRGGBB), worked out here so every phone browser shows the same colour."""
    ca, cb = (tuple(int(x[i:i + 2], 16) for i in (1, 3, 5)) for x in (a, b))
    return "#" + "".join(f"{round(ca[i] * t + cb[i] * (1 - t)):02X}" for i in range(3))


async def site_theme(owner_id: str, accent: str, look: str = "signboard", variant: int = 0, pop: str = "#FFC21A",
                     kit_colours: bool = True, fonts_key: str = "") -> dict:
    """The website's colours and fonts from the owner's brand: the website's own colour leads, the brand kit adds its second
    colour, fonts and corners. Text on the brand colour is white or ink, whichever reads better. `look` is the page's finish
    (its corners); `fonts_key` its font pairing (site_looks.FONT_PAIRS), else the finish's own."""
    from .. import brand_kit as bk
    from ..site_looks import FINISH_FONTS, FONT_PAIRS
    design = ((await db().brand_kits.find_one({"_id": owner_id}, {"design": 1})) or {}).get("design") or {}
    primary = accent if re.fullmatch(r"#[0-9A-Fa-f]{6}", accent or "") else "#1F4E79"
    colors = design.get("colors") or {}
    # the brand kit's second colour, unless the page's colour was picked for its kind of business (it wouldn't match)
    second = colors.get("secondary") if kit_colours and re.fullmatch(r"#[0-9A-Fa-f]{6}", colors.get("secondary") or "") else _mix(primary, "#000000", .55)
    fonts = design.get("fonts") or {}
    if fonts.get("heading") in SITE_FONTS and fonts.get("heading") != "poppins":     # the owner's own choice wins
        h, b = SITE_FONTS[fonts["heading"]], SITE_FONTS.get(fonts.get("body"), SITE_FONTS["mukta"])
        head, body = (h[0], h[1]), (b[0], b[2])
    else:
        own = FINISH_FONTS.get(look, FINISH_FONTS["signboard"])
        head, body = FONT_PAIRS[fonts_key if fonts_key in FONT_PAIRS else own[(variant >> 1) % len(own)]]
    families = {head[0]: head[1], body[0]: body[1]} if head[0] != body[0] else {head[0]: ";".join(sorted(set((head[1] + ";" + body[1]).split(";"))))}
    # corners: the look's own unless the owner moved the brand kit off its default ("soft")
    look_r = {"signboard": "12px", "bazaar": "20px", "care": "26px", "works": "2px", "atelier": "4px", "launch": "22px"}.get(look, "12px")
    radius = {"square": "3px", "round": "22px"}.get(design.get("corners"), look_r)
    ink = _mix(primary, "#0E1116", .16)
    # dark sections: the brand's second colour when it is dark enough to carry white text, else a darker shade of the first
    deep = second if bk.contrast(second, "#FFFFFF") >= 7 else _mix(primary, "#000000", .58 if bk.luminance(primary) > .04 else .8)
    # the brand colour as text on white: darkened step by step until it passes WCAG AA (4.5:1), so a yellow brand stays legible
    text = primary
    for t in (.85, .7, .55, .4, .25):
        if bk.contrast(text, "#FFFFFF") >= 5:      # 5:1 on white keeps 4.5:1 on the pale brand tint too
            break
        text = _mix(primary, "#0E1116", t)
    # and the reverse for dark panels: lightened until it reads on near-black (a deep blue brand would otherwise sink)
    glow = primary
    for t in (.8, .65, .5, .35, .2):
        if bk.contrast(glow, "#0F1318") >= 5:
            break
        glow = _mix(primary, "#FFFFFF", t)
    return {"primary": primary.upper(), "on_primary": bk.text_on(primary), "deep": deep.upper(), "on_deep": bk.text_on(deep), "text": text, "glow": glow,
            "p_url": "%23" + primary.upper()[1:], "s_url": "%23" + second.upper()[1:], "pt_url": "%23" + text.upper()[1:],
            "second": second.upper(), "soft": _mix(primary, "#FFFFFF", .07), "tint": _mix(primary, "#FFFFFF", .16),
            "ink": ink, "line": _mix(primary, "#E7E9EC", .1), "radius": radius,
            "pill": "3px" if design.get("corners") == "square" or (look == "works" and design.get("corners") != "round") else "999px",
            "head_font": head[0], "body_font": body[0],
            # Launch's second colour for buttons and highlights: a warm yellow, or ink when the brand itself is light
            "pop_colour": next((c for c in (pop, "#FFC21A", "#FFFFFF") if bk.luminance(primary) < .45 and bk.contrast(primary, c) >= 2.2), "#0F1236"),
            "fonts_url": "https://fonts.googleapis.com/css2?" + "&".join(f"family={quote(f).replace('%20', '+')}:wght@{w}" for f, w in families.items()) + "&display=swap"}


# brand kit font keys → their Google Fonts family names (v2.3's _extras; the looks above pick the fonts the page loads)
WEB_FONTS = {"poppins": "Poppins", "mukta": "Mukta", "hind": "Hind", "baloo2": "Baloo 2", "martel": "Martel", "yatra": "Yatra One",
             "playfair": "Playfair Display", "inter": "Inter", "noto": "Noto Sans Devanagari"}


@router.api_route("/s/{slug}", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def member_site(slug: str, request: Request, preview: int = 0, sent: int = 0, viewer: dict | None = Depends(optional_user)):
    site, owner, business = await _site_and_owner(slug, viewer, bool(preview))
    if not site:
        return not_found()
    if not preview:
        await db().sites.update_one({"_id": site["_id"]}, {"$inc": {"views": 1}})
        from .membership import record_view; await record_view(site)  # noqa: E702  daily views for the funnel and dashboard
    base = base_for(request, site["slug"])
    wa = wa_digits(business.get("whatsapp", ""))
    name = business.get("name", "")
    extras = await _extras(owner, owner["_id"])
    from ..site_looks import design_of, pop_colour
    d = design_of(site, business)
    look, variant = d["voice"], d["variant"]
    accent = site.get("accent", "#1F4E79")
    by_niche = accent == "#1F4E79" and not site.get("brand_accent") and not site.get("accent_chosen")   # no brand colour of its own
    if by_niche:
        from ..site_looks import niche_colour
        accent = niche_colour(site, variant, business)
    html = templates.get_template("site.html").render(
        c=site["content"], name=name, city=business.get("city", ""), accent=site.get("accent", "#1F4E79"), base=base,
        whatsapp_link=f"{base}/wa" if wa else None, email=business.get("email", ""), slug=site["slug"], sent=bool(sent),
        preview=bool(preview), live=site["status"] == "live", form_error=FORM_ERRORS.get(request.query_params.get("err", "")),
        badge=not plans.has(owner, "badge_off"), badge_link=f"{settings.app_url}/join?ref={owner['ref_code']}&src=badge",
        ladder=extras["ladder"], guide=extras["guide"], logo=extras["logo"], url=site_url(site["slug"]),
        program=settings.program_name, year=now().year, photos=photos_mod.resolved(site.get("photos"), base),
        industry=business.get("industry", ""), proof=(business.get("proof") or "").strip(),
        theme=await site_theme(owner["_id"], accent, d["finish"], variant, pop_colour(site), kit_colours=not by_niche, fonts_key=d["fonts"]),
        look=look, variant=variant, d=d)
    photos_mod.fill_later(site, business)
    return HTMLResponse(html, headers={"Cache-Control": "no-store" if preview else "no-cache"})  # edits and plan changes show at once


@router.get("/s/{slug}/wa")
async def whatsapp_tap(slug: str, request: Request, about: str = ""):
    """Chat on WhatsApp: counts the tap, then opens the owner's own WhatsApp with a first line typed for the customer."""
    site, owner, business = await _site_and_owner(slug, None, False)
    if not site:
        return not_found()
    wa = wa_digits(business.get("whatsapp", ""))
    if not wa:
        return RedirectResponse(base_for(request, site["slug"]) or "/", 302)
    await _count(site, "wa")
    first = f"Hi {business.get('name', '')}, I saw your website" + (f" and I'm interested in {about[:60]}" if about else "") + "."
    return RedirectResponse(f"https://wa.me/{wa}?text={quote(first)}", 302)


@router.post("/s/{slug}/inquiry")
async def inquiry(slug: str, request: Request):
    """Website contact form. A plain form post that can't be accepted goes back to the form with a friendly message
    (customers never see a raw error page); JSON callers get the error as JSON."""
    wants_json = "application/json" in request.headers.get("content-type", "")
    base = base_for(request, slug.lower())
    try:
        return await _inquiry(slug, request, wants_json, base)
    except HTTPException as e:
        if wants_json or e.status_code not in (400, 429):
            raise
        return RedirectResponse(f"{base or '/'}?err={'busy' if e.status_code == 429 else 'phone'}#contact", 303)


async def save_lead(site: dict | None, owner: dict, business: dict, name: str, phone: str, message: str, source: str, **extra) -> dict:
    """A new lead from any channel (website form, the guide, a lead magnet download, an email or a pasted WhatsApp
    inquiry): saved at the first pipeline stage, the owner alerted on WhatsApp, and the agents told. `extra` carries
    channel fields (email, magnet_id, message_id…)."""
    lead = {"_id": new_id(), "owner_id": owner["_id"], "site_id": (site or {}).get("_id"), "name": name, "phone": phone,
            "message": message, "status": "new", "stage": "identification", "source": source, "created_at": now(), **extra}
    await db().leads.insert_one(lead)
    dashboard_cache.bust(owner["_id"])
    await track("lead", owner["_id"], source=source)
    # Owner alert on WhatsApp from the hub's own number (every plan). Params: business, lead name, lead phone, message.
    alert = await whatsapp_template(owner["phone"], settings.aisensy_lead_alert_campaign, business.get("name", ""),
                                    [business.get("name", "your business"), name, phone, message[:300] or "(no message)"], "lead_alert")
    patch = {"alert": {"sent": alert["sent"], "via": alert["via"]}}
    from ..agents import hooks
    patch.update(await hooks.on_new_lead(owner, business, site, lead))
    await db().leads.update_one({"_id": lead["_id"]}, {"$set": patch})
    from .. import crm   # v2.3: the lead's contact in the CRM and its first pipeline stage (never fails the lead)
    await crm.on_new_lead(owner["_id"], lead)
    return lead


async def _checked(slug: str, request: Request, data: dict):
    if str(data.get("website") or "").strip():  # honeypot: bots fill every field
        return None, None, None, None, None
    site, owner, business = await _site_and_owner(slug, None, False)
    if not site:
        raise HTTPException(404, "Not found")
    name = str(data.get("name", "")).strip()[:80]
    try:  # a real mobile number, so agents never message a made-up one
        phone = normalise_phone(str(data.get("phone", ""))[:24])
    except HTTPException:
        phone = ""
    if len(name) < 2 or not phone:
        raise HTTPException(400, "Please add your name and a 10-digit mobile number")
    await rate_limit(f"inq:{site['_id']}:{client_ip(request)}", 5, 600, "Too many messages. Please try again in a few minutes.")
    # Ceiling per website, whatever the source: protects the owner's inbox and our WhatsApp spend
    await rate_limit(f"inq-site:{site['_id']}", settings.inquiries_per_site_per_hour, 3600,
                     "This business is getting a lot of messages right now. Please message them on WhatsApp instead.")
    return site, owner, business, name, phone


async def _inquiry(slug: str, request: Request, wants_json: bool, base: str):
    try:
        data = await request.json() if wants_json else dict(await request.form())
    except Exception:
        raise HTTPException(400, "Please add your name and a 10-digit mobile number")
    if not isinstance(data, dict):
        raise HTTPException(400, "Please add your name and a 10-digit mobile number")
    site, owner, business, name, phone = await _checked(slug, request, data)
    if site:
        await save_lead(site, owner, business, name, phone, str(data.get("message", "")).strip()[:2000], "website")
    return JSONResponse({"ok": True}) if wants_json else RedirectResponse(f"{base or '/'}?sent=1#contact", 303)


# ───────────────────────── the guide (lead magnet) ─────────────────────────
async def _guide(slug: str, viewer: dict | None = None, preview: bool = False):
    """The published guide; with preview, the owner's own guide even before it (or the website) is published."""
    site, owner, business = await _site_and_owner(slug, viewer, preview)
    if not site or not plans.has(owner, "lead_magnet"):
        return None
    g = await db().lead_magnets.find_one({"_id": owner["_id"], **({} if preview else {"published": True})})
    if not g:
        return None
    return site, owner, business, g


async def _uploads(slug: str) -> list[dict]:
    """The owner's published uploaded lead magnets (v2.1), listed under the guide."""
    from .sitemagnets import published_magnets
    site, owner, _b = await _site_and_owner(slug, None, False)
    return await published_magnets(owner) if site else []


@router.get("/s/{slug}/guide", response_class=HTMLResponse)
async def guide_page(slug: str, request: Request, preview: int = 0, viewer: dict | None = Depends(optional_user)):
    if preview:   # only the owner reads the whole guide as it will look, published or not; nothing is counted
        found = await _guide(slug, viewer, True)
        if not found or not viewer or viewer["_id"] != found[1]["_id"] or not found[3].get("content"):
            return not_found()
        site, owner, business, g = found
        return HTMLResponse(templates.get_template("guide.html").render(
            g=g["content"], name=business.get("name", ""), accent=site.get("accent", "#1F4E79"), base=base_for(request, site["slug"]),
            unlocked=True, preview=True, form_error=None, program=settings.program_name, year=now().year, uploads=[]), headers={"Cache-Control": "no-store"})
    found = await _guide(slug)
    if not found:
        uploads = await _uploads(slug)   # no AI guide, but an uploaded download: /guide opens that
        if uploads:
            return RedirectResponse(f"{base_for(request, slug.lower())}/guide/{uploads[0]['slug']}", 303)
        return not_found()
    site, owner, business, g = found
    await _count(site, "guide_views")
    return HTMLResponse(templates.get_template("guide.html").render(
        g=g["content"], name=business.get("name", ""), accent=site.get("accent", "#1F4E79"), base=base_for(request, site["slug"]),
        unlocked=False, form_error=FORM_ERRORS.get(request.query_params.get("err", "")), program=settings.program_name, year=now().year,
        uploads=await _uploads(slug)))


@router.post("/s/{slug}/guide/get", response_class=HTMLResponse)
async def guide_get(slug: str, request: Request):
    """The visitor leaves a name and number; the full guide opens and the owner gets a lead."""
    base = base_for(request, slug.lower())
    found = await _guide(slug)
    if not found:
        return not_found()
    site, owner, business, g = found
    try:
        data = dict(await request.form())
        checked = await _checked(slug, request, data)
    except HTTPException as e:
        return RedirectResponse(f"{base}/guide?err={'busy' if e.status_code == 429 else 'phone'}#get", 303)
    if checked[0]:
        _, _, _, name, phone = checked
        await save_lead(site, owner, business, name, phone, f"Downloaded the guide: {g['content'].get('title', '')}"[:300], "guide")
        await _count(site, "guide_leads")
    return HTMLResponse(templates.get_template("guide.html").render(
        g=g["content"], name=business.get("name", ""), accent=site.get("accent", "#1F4E79"), base=base, unlocked=True,
        form_error=None, program=settings.program_name, year=now().year, uploads=await _uploads(slug)), headers={"Cache-Control": "no-store"})


# ───────────────────────── a quotation or invoice, as a PDF, for the customer ─────────────────────────
@router.get("/i/{token}")
async def document_pdf(token: str, request: Request):
    """The link in the WhatsApp message: the quotation, invoice or credit note as a PDF. The token is unguessable and only
    made when the owner shares it; nothing else about the business is reachable from here."""
    import asyncio
    from .. import invoice_pdf
    from ..brand import accent_for
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,40}", token or ""):
        return not_found()
    await rate_limit(f"doc-pdf:{client_ip(request)}", 60, 600, "Too many downloads from this network. Try again in a few minutes.")
    q = await db().quotes.find_one({"share_token": token})
    if not q or str(q.get("status") or "").lower() in ("deleted", "void", "voided", "cancelled", "canceled"):
        return not_found()
    b = await db().businesses.find_one({"owner_id": q["ws"]}) or {}
    qs = await db().quote_settings.find_one({"_id": q["ws"]}) or {}
    pdf = await asyncio.to_thread(invoice_pdf.render, q, b, qs, await accent_for(q["ws"]))
    name = re.sub(r"[^A-Za-z0-9_-]+", "-", q.get("number") or "document")
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{name}.pdf"',
                                                                "Cache-Control": "private, no-store", "X-Robots-Tag": "noindex"})


# ───────────────────────── the digital visiting card ─────────────────────────
@router.get("/s/{slug}/card", response_class=HTMLResponse)
async def visiting_card(slug: str, request: Request):
    site, owner, business = await _site_and_owner(slug, None, False)
    if not site:
        return not_found()
    await _count(site, "card_views")
    brand = (business.get("brand") or {}).get("message") or site["content"].get("headline", "")
    wa = wa_digits(business.get("whatsapp", ""))
    scan = await db().site_scans.find_one({"_id": owner["_id"]}, {"logo": 1})
    return HTMLResponse(templates.get_template("card.html").render(
        name=business.get("name", ""), brand=brand, city=business.get("city", ""), accent=site.get("accent", "#1F4E79"),
        phone=("+" + wa) if wa else "", email=business.get("email", ""), url=site_url(site["slug"]), base=base_for(request, site["slug"]),
        logo=(scan or {}).get("logo", ""), badge=not plans.has(owner, "badge_off"),
        badge_link=f"{settings.app_url}/join?ref={owner['ref_code']}&src=card", program=settings.program_name))


def _vcf(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", " ")[:200]


@router.get("/s/{slug}/card.vcf")
async def visiting_card_vcf(slug: str):
    site, owner, business = await _site_and_owner(slug, None, False)
    if not site:
        return not_found()
    wa = wa_digits(business.get("whatsapp", ""))
    lines = ["BEGIN:VCARD", "VERSION:3.0", f"FN:{_vcf(business.get('name'))}", f"ORG:{_vcf(business.get('name'))}"]
    if wa:
        lines.append(f"TEL;TYPE=CELL:+{wa}")
    if business.get("email"):
        lines.append(f"EMAIL:{_vcf(business['email'])}")
    lines += [f"URL:{site_url(site['slug'])}", f"NOTE:{_vcf((business.get('brand') or {}).get('message', ''))}", "END:VCARD", ""]
    return Response("\r\n".join(lines), media_type="text/vcard",
                    headers={"Content-Disposition": f'attachment; filename="{site["slug"]}.vcf"'})


@router.get("/api/public/showcase")
async def showcase():
    out = []
    async for s in db().sites.find({"status": "live", "showcase": True}).sort("published_at", -1).limit(24):
        b = await db().businesses.find_one({"owner_id": s["owner_id"]}, {"name": 1, "city": 1, "industry": 1}) or {}
        out.append({"slug": s["slug"], "url": site_url(s["slug"]), "name": b.get("name", ""), "city": b.get("city", ""),
                    "industry": b.get("industry", "")[:80], "headline": s["content"].get("headline", ""), "accent": s.get("accent")})
    return out


@router.get("/api/public/plans")
async def public_plans():
    """The six plans for the public landing page: names, prices and promises as the admin last set them."""
    await plans.load()
    return [{"tier": p["tier"], "name": p["name"], "price_minor": p["price_minor"], "period": p.get("period", ""),
             "billing": p.get("billing", ""), "promise": p.get("promise", ""), "pitch": p.get("pitch", ""),
             "runs": p.get("runs", 0), "access_days": p.get("access_days"), "features": len(p["unlocks"])} for p in plans.ladder()]


@router.get("/api/public/invite/{code}")
async def invite_info(code: str, src: str = "invite"):
    user = await db().users.find_one({"ref_code": code.strip().upper()[:12]})
    if not user:
        return {"valid": False}
    b = await db().businesses.find_one({"owner_id": user["_id"]}, {"name": 1}) or {}
    await track("invite_visit", user["_id"], src=src if src in ("invite", "badge", "showcase", "score", "card") else "invite")
    return {"valid": True, "business": b.get("name", ""), "trial_days": settings.friend_trial_days}


@router.get("/api/public/cohort/{code}")
async def cohort_info(code: str):
    c = await db().cohorts.find_one({"code": code.strip().upper()[:40]})
    return {"valid": bool(c), "name": c.get("name") if c else None}


@router.get("/api/health")
async def health():
    await db().command("ping")
    return {"ok": True}
