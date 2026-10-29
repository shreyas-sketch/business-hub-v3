"""
Brand kit designs, drawn on the server in the owner's brand (v2.1, 3.11): templates, not image generation.

Each template has a size, a few text fields (pre-filled from the message system; the owner edits them) and a layout that
uses the brand tokens: colours (text colour picked for contrast), heading and body fonts (Devanagari-capable fonts are
bundled and shaped with Raqm, so Hindi and Marathi render correctly), the logo or a text logo, corner style and spacing.
Output is a PNG, or a PDF for the ones that get printed (visiting card, QR stand, letterhead), plus the brand kit PDF.
"""
import io
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import brand_kit

FONT_DIR = Path(__file__).resolve().parent / "fonts"
DEVANAGARI = re.compile("[ऀ-ॿ]")
STEMS = {k: (stem, deva) for k, _l, stem, deva in brand_kit.FONTS}


@lru_cache(maxsize=128)
def _font(key: str, bold: bool, size: int, deva_needed: bool) -> ImageFont.FreeTypeFont:
    stem, deva = STEMS.get(key, STEMS["poppins"])
    if deva_needed and not deva:
        stem = "NotoSansDevanagari"          # an English-only font can't draw Hindi: fall back for that text
    if stem == "Inter":
        path = FONT_DIR / f"Inter-{700 if bold else 400}.ttf"
    elif stem == "YatraOne":
        path = FONT_DIR / "brand" / "YatraOne-400.ttf"
    else:
        path = FONT_DIR / "brand" / f"{stem}-{700 if bold else 400}.ttf"
    return ImageFont.truetype(str(path), max(8, size), layout_engine=ImageFont.Layout.RAQM)


@dataclass
class Field_:
    key: str
    label: str
    max: int = 160
    lines: int = 1


@dataclass
class Template:
    key: str
    label: str
    size: tuple[int, int]
    fields: list[Field_]
    pdf_dpi: int | None = None           # printed: also offered as a PDF at this resolution
    group: str = "Social"
    note: str = ""
    defaults: dict = field(default_factory=dict)


TEMPLATES = {t.key: t for t in [
    Template("social_square", "Social post (square)", (1080, 1080), [Field_("headline", "Headline", 90), Field_("body", "Text", 260, 3), Field_("cta", "Button", 40)]),
    Template("social_story", "Social post (story)", (1080, 1920), [Field_("headline", "Headline", 90), Field_("body", "Text", 300, 4), Field_("cta", "Button", 40)]),
    Template("festival", "Festival greeting", (1080, 1080), [Field_("greeting", "Greeting", 60), Field_("message", "Message", 200, 3)]),
    Template("offer", "Offer post", (1080, 1080), [Field_("title", "Offer", 80), Field_("detail", "What's included", 220, 3), Field_("price", "Price line (only if it's on your list)", 60),
                                                  Field_("cta", "Button", 40)]),
    Template("testimonial", "Testimonial card", (1080, 1080), [Field_("quote", "What the customer said", 300, 4), Field_("customer", "Customer name (with their consent)", 60),
                                                              Field_("detail", "Detail, e.g. 2BHK kitchen, Thane", 80)]),
    Template("status_poster", "WhatsApp Status poster", (1080, 1920), [Field_("headline", "Headline", 90), Field_("sub", "Line under it", 200, 3), Field_("cta", "Button", 40)],
             group="WhatsApp"),
    Template("catalogue_cover", "WhatsApp catalogue cover", (1080, 1080), [Field_("title", "Title", 60), Field_("sub", "Line under it", 120, 2)], group="WhatsApp"),
    Template("visiting_card", "Visiting card", (1050, 600), [Field_("person", "Your name", 60), Field_("role", "Your role", 60), Field_("phone", "Phone", 30),
                                                             Field_("email", "Email", 60), Field_("web", "Website", 60)], pdf_dpi=300, group="Print"),
    Template("qr_stand", "QR stand (A5)", (1240, 1748), [Field_("headline", "Headline", 60), Field_("sub", "Line under it", 120, 2), Field_("link", "Where the QR goes", 300)],
             pdf_dpi=212, group="Print", note="The QR opens the link you give (your WhatsApp or website)."),
    Template("letterhead", "Letterhead (A4)", (1240, 1754), [Field_("address", "Address", 200, 2), Field_("contact", "Phone · email · website", 160)], pdf_dpi=150, group="Print"),
    Template("email_header", "Email header", (1200, 300), [Field_("tagline", "Tagline", 80)], group="Email"),
]}


def has_deva(text: str) -> bool:
    return bool(DEVANAGARI.search(text or ""))


class Canvas:
    def __init__(self, size: tuple[int, int], design: dict, logo: Image.Image | None = None):
        self.W, self.H = size
        self.d = design
        c = design.get("colors") or {}
        self.primary, self.secondary, self.accent = c.get("primary", "#1F4E79"), c.get("secondary", "#16384F"), c.get("accent", "#F2A93B")
        self.bg, self.ink = c.get("background", "#FFFFFF"), c.get("text", "#14171C")
        self.on_primary, self.on_accent = brand_kit.text_on(self.primary), brand_kit.text_on(self.accent)
        self.radius = {"square": 0, "soft": 0.03, "round": 0.08}[design.get("corners", "soft")]
        self.pad = int(min(self.W, self.H) * (0.07 if design.get("spacing") == "compact" else 0.09))
        self.img = Image.new("RGB", size, self.bg)
        self.draw = ImageDraw.Draw(self.img)
        self.logo = logo
        fonts = design.get("fonts") or {}
        self.heading, self.body = fonts.get("heading", "poppins"), fonts.get("body", "mukta")

    def font(self, role: str, size: int, text: str, bold: bool | None = None) -> ImageFont.FreeTypeFont:
        key = self.heading if role == "heading" else self.body
        return _font(key, role == "heading" if bold is None else bold, int(size), has_deva(text))

    def r(self, w: int) -> int:
        return int(min(self.W, self.H) * self.radius) if w else 0

    def box(self, xy, fill, outline=None, width=0):
        x0, y0, x1, y1 = xy
        self.draw.rounded_rectangle(xy, radius=min(self.r(1), int((y1 - y0) / 2)), fill=fill, outline=outline, width=width)

    def wrap(self, text: str, font, width: int) -> list[str]:
        lines = []
        for para in (text or "").split("\n"):
            words, cur = para.split(), ""
            for w in words:
                trial = f"{cur} {w}".strip()
                if font.getlength(trial) <= width or not cur:
                    cur = trial
                else:
                    lines.append(cur)
                    cur = w
            lines.append(cur)
        return [x for x in lines if x is not None]

    def text(self, text: str, x: int, y: int, width: int, max_h: int, role: str, size: int, color: str, align: str = "left",
             leading: float = 1.22, min_size: int = 18, bold: bool | None = None) -> int:
        """Wraps and shrinks to fit (width × max_h). Returns the height used."""
        text = (text or "").strip()
        if not text:
            return 0
        while True:
            f = self.font(role, size, text, bold)
            lines = self.wrap(text, f, width)
            lh = int(size * leading)
            # a single long word (a business name in the brand mark) can't wrap: it has to shrink to the width too
            if (len(lines) * lh <= max_h and all(f.getlength(x) <= width for x in lines)) or size <= min_size:
                break
            size = int(size * 0.92)
        lines = lines[: max(1, max_h // lh)]
        for i, line in enumerate(lines):
            w = f.getlength(line)
            lx = x + (width - w) / 2 if align == "center" else x + width - w if align == "right" else x
            self.draw.text((lx, y + i * lh), line, font=f, fill=color)
        return len(lines) * lh

    def pill(self, text: str, x: int, y: int, size: int, fill: str, color: str, center: bool = False) -> int:
        if not text:
            return 0
        f = self.font("body", size, text, True)
        w, h = int(f.getlength(text)) + size * 2, int(size * 2.1)
        x0 = int(x - w / 2) if center else x
        self.draw.rounded_rectangle((x0, y, x0 + w, y + h), radius=h // 2 if self.radius else 0, fill=fill)
        self.draw.text((x0 + size, y + (h - size * 1.25) / 2), text, font=f, fill=color)
        return h

    def brand_mark(self, name: str, x: int, y: int, height: int, color: str, max_w: int, align: str = "left") -> None:
        if self.logo is not None:
            lg = self.logo.copy()
            lg.thumbnail((max_w, height))
            lx = x + (max_w - lg.width) // 2 if align == "center" else x
            self.img.paste(lg, (lx, y), lg if lg.mode == "RGBA" else None)
        else:
            self.text(name, x, y, max_w, height, "heading", int(height * 0.7), color, align=align, min_size=14)

    def qr(self, link: str, x: int, y: int, size: int) -> None:
        import qrcode
        q = qrcode.QRCode(border=2, box_size=10, error_correction=qrcode.constants.ERROR_CORRECT_M)
        q.add_data(link or "https://employz.ai")
        q.make(fit=True)
        im = q.make_image(fill_color=self.ink, back_color="#FFFFFF").convert("RGB").resize((size, size), Image.NEAREST)
        self.img.paste(im, (x, y))


def render(key: str, fields: dict, design: dict, business: dict, logo: Image.Image | None = None) -> Image.Image:
    t = TEMPLATES[key]
    c = Canvas(t.size, design, logo)
    W, H, P = c.W, c.H, c.pad
    name = business.get("name", "")
    f = {k: str(v or "")[: next((x.max for x in t.fields if x.key == k), 300)] for k, v in fields.items()}
    if key in ("social_square", "social_story", "status_poster", "catalogue_cover"):
        c.img.paste(c.primary, (0, 0, W, H))
        c.draw.rectangle((0, H - int(H * 0.012), W, H), fill=c.accent)
        c.brand_mark(name, P, P, int(H * 0.06 if H > W else H * 0.08), c.on_primary, int(W * 0.5))
        top = int(H * (0.22 if H > W else 0.24))
        head = f.get("headline") or f.get("title", "")
        used = c.text(head, P, top, W - 2 * P, int(H * 0.30), "heading", int(W * 0.095), c.on_primary, leading=1.12)
        sub = f.get("body") or f.get("sub", "")
        footer_y = H - P - int(W * 0.03)
        pill_size = int(W * 0.036)
        # the button sits above the footer, and the body stops above the button (it shrinks to fit rather than run under it)
        pill_y = footer_y - int(H * 0.03) - int(pill_size * 2.1)
        body_y = top + used + int(H * 0.03)
        body_end = pill_y - int(H * 0.03) if f.get("cta") else footer_y - int(H * 0.03)
        if body_end - body_y >= int(W * 0.03):
            used += int(H * 0.03) + c.text(sub, P, body_y, W - 2 * P, min(int(H * 0.28), body_end - body_y), "body", int(W * 0.045),
                                           c.on_primary, leading=1.35, min_size=14)
        if f.get("cta"):
            c.pill(f["cta"], P, min(top + used + int(H * 0.05), pill_y), pill_size, c.accent, c.on_accent)
        footer = " · ".join(x for x in (name, business.get("whatsapp", "")) if x)
        c.text(footer, P, footer_y, W - 2 * P, int(W * 0.05), "body", int(W * 0.03), c.on_primary, min_size=14)
    elif key == "festival":
        c.img.paste(c.primary, (0, 0, W, H))
        for i in range(0, W, int(W / 9)):
            c.draw.ellipse((i, -int(W * 0.05), i + int(W * 0.08), int(W * 0.03)), fill=c.accent)
        c.text(f.get("greeting", ""), P, int(H * 0.28), W - 2 * P, int(H * 0.22), "heading", int(W * 0.11), c.on_primary, align="center", leading=1.1)
        c.text(f.get("message", ""), P, int(H * 0.52), W - 2 * P, int(H * 0.22), "body", int(W * 0.045), c.on_primary, align="center", leading=1.35)
        c.brand_mark(name, P, int(H * 0.8), int(H * 0.08), c.on_primary, W - 2 * P, align="center")
    elif key == "offer":
        c.img.paste(c.bg, (0, 0, W, H))
        c.box((P // 2, P // 2, W - P // 2, H - P // 2), fill=c.primary)
        c.brand_mark(name, P, P, int(H * 0.07), c.on_primary, int(W * 0.5))
        used = c.text(f.get("title", ""), P, int(H * 0.2), W - 2 * P, int(H * 0.26), "heading", int(W * 0.09), c.on_primary, leading=1.1)
        y = int(H * 0.2) + used + int(H * 0.03)
        y += c.text(f.get("detail", ""), P, y, W - 2 * P, int(H * 0.22), "body", int(W * 0.042), c.on_primary, leading=1.35) + int(H * 0.03)
        if f.get("price"):
            y += c.text(f["price"], P, y, W - 2 * P, int(H * 0.1), "heading", int(W * 0.06), c.accent) + int(H * 0.02)
        c.pill(f.get("cta", ""), P, min(y, H - P - int(W * 0.1)), int(W * 0.036), c.accent, c.on_accent)
    elif key == "testimonial":
        c.img.paste(c.bg, (0, 0, W, H))
        c.draw.rectangle((0, 0, int(W * 0.03), H), fill=c.primary)
        c.text("“", P, int(H * 0.08), W, int(H * 0.2), "heading", int(W * 0.25), c.accent, leading=1.0)
        used = c.text(f.get("quote", ""), P, int(H * 0.28), W - 2 * P, int(H * 0.42), "heading", int(W * 0.055), c.ink, leading=1.3, bold=False)
        y = int(H * 0.28) + used + int(H * 0.04)
        y += c.text(f.get("customer", ""), P, y, W - 2 * P, int(H * 0.07), "body", int(W * 0.04), c.primary, bold=True)
        c.text(f.get("detail", ""), P, y + 4, W - 2 * P, int(H * 0.06), "body", int(W * 0.032), "#5B6370")
        c.brand_mark(name, P, H - P - int(H * 0.06), int(H * 0.06), c.primary, int(W * 0.5))
    elif key == "visiting_card":
        c.img.paste(c.bg, (0, 0, W, H))
        c.draw.rectangle((0, 0, int(W * 0.36), H), fill=c.primary)
        c.brand_mark(name, int(W * 0.04), int(H * 0.36), int(H * 0.26), c.on_primary, int(W * 0.28), align="center")
        x, w = int(W * 0.42), int(W * 0.54)
        y = int(H * 0.16)
        y += c.text(f.get("person", ""), x, y, w, int(H * 0.16), "heading", int(H * 0.1), c.ink) + 4
        y += c.text(f.get("role", ""), x, y, w, int(H * 0.1), "body", int(H * 0.055), c.primary) + int(H * 0.08)
        for k in ("phone", "email", "web"):
            y += c.text(f.get(k, ""), x, y, w, int(H * 0.09), "body", int(H * 0.05), c.ink, min_size=12) + 6
    elif key == "qr_stand":
        c.img.paste(c.bg, (0, 0, W, H))
        c.draw.rectangle((0, 0, W, int(H * 0.06)), fill=c.primary)
        c.brand_mark(name, P, int(H * 0.1), int(H * 0.08), c.primary, W - 2 * P, align="center")
        c.text(f.get("headline", ""), P, int(H * 0.22), W - 2 * P, int(H * 0.12), "heading", int(W * 0.08), c.ink, align="center")
        size = int(W * 0.56)
        c.qr(f.get("link", ""), (W - size) // 2, int(H * 0.36), size)
        c.text(f.get("sub", ""), P, int(H * 0.36) + size + int(H * 0.04), W - 2 * P, int(H * 0.12), "body", int(W * 0.04), c.ink, align="center")
        c.draw.rectangle((0, H - int(H * 0.04), W, H), fill=c.accent)
    elif key == "letterhead":
        c.img.paste(c.bg, (0, 0, W, H))
        c.brand_mark(name, P, P, int(H * 0.06), c.primary, int(W * 0.45))
        c.text(f.get("address", ""), int(W * 0.5), P, int(W * 0.5) - P, int(H * 0.06), "body", int(W * 0.018), "#5B6370", align="right", min_size=12)
        c.draw.rectangle((P, P + int(H * 0.075), W - P, P + int(H * 0.075) + 4), fill=c.primary)
        c.draw.rectangle((0, H - int(H * 0.035), W, H), fill=c.primary)
        c.text(f.get("contact", ""), P, H - int(H * 0.06), W - 2 * P, int(H * 0.025), "body", int(W * 0.016), "#5B6370", align="center", min_size=11)
    elif key == "email_header":
        c.img.paste(c.primary, (0, 0, W, H))
        c.brand_mark(name, P // 2, int(H * 0.25), int(H * 0.5), c.on_primary, int(W * 0.4))
        c.text(f.get("tagline", ""), int(W * 0.45), int(H * 0.36), int(W * 0.52), int(H * 0.4), "body", int(H * 0.13), c.on_primary, align="right", min_size=12)
    return c.img


def png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", compress_level=6)
    return buf.getvalue()


def pdf(images: list[Image.Image], dpi: int = 150) -> bytes:
    buf = io.BytesIO()
    first, rest = images[0].convert("RGB"), [i.convert("RGB") for i in images[1:]]
    first.save(buf, "PDF", resolution=dpi, save_all=True, append_images=rest)
    return buf.getvalue()


def defaults(key: str, messages: dict, business: dict, site_url: str | None) -> dict:
    m = messages or {}
    core, web, mk, social = m.get("core") or {}, m.get("website") or {}, m.get("marketing") or {}, m.get("social") or {}
    wa = re.sub(r"\D", "", business.get("whatsapp") or "")
    wa_link = f"https://wa.me/{'91' + wa if len(wa) == 10 else wa}" if wa else (site_url or "")
    offers = [o for o in business.get("offers") or [] if o.get("name")]
    first_offer = offers[0] if offers else {}
    price = " ".join(x for x in (first_offer.get("price", ""), first_offer.get("unit", "")) if x)
    return {
        "social_square": {"headline": (social.get("hooks") or [web.get("headline", "")])[0], "body": core.get("pitch", ""), "cta": (web.get("ctas") or ["Message us"])[0]},
        "social_story": {"headline": web.get("headline", ""), "body": web.get("subheadline", ""), "cta": (web.get("ctas") or ["Message us"])[0]},
        "festival": {"greeting": "Happy Diwali", "message": mk.get("festival_line", "")},
        "offer": {"title": first_offer.get("name", "") or mk.get("tagline", ""), "detail": web.get("subheadline", ""), "price": price,
                  "cta": (web.get("ctas") or ["Ask on WhatsApp"])[0]},
        "testimonial": {"quote": "", "customer": "", "detail": ""},
        "status_poster": {"headline": core.get("one_line", ""), "sub": web.get("subheadline", ""), "cta": "Message us on WhatsApp"},
        "catalogue_cover": {"title": business.get("name", ""), "sub": mk.get("tagline", "") or core.get("one_line", "")},
        "visiting_card": {"person": "", "role": "Founder", "phone": business.get("whatsapp", ""), "email": business.get("email", ""), "web": (site_url or "").replace("https://", "")},
        "qr_stand": {"headline": "Scan to WhatsApp us", "sub": core.get("one_line", ""), "link": wa_link},
        "letterhead": {"address": business.get("city", ""), "contact": " · ".join(x for x in (business.get("whatsapp", ""), business.get("email", ""),
                                                                                              (site_url or "").replace("https://", "")) if x)},
        "email_header": {"tagline": mk.get("tagline", "") or core.get("one_line", "")},
    }.get(key, {})


def kit_pages(messages: dict, design: dict, business: dict, logo: Image.Image | None) -> list[Image.Image]:
    """The brand kit as A4 pages (150 dpi): the cover, the message system and the design system."""
    W, H = 1240, 1754
    pages = []
    cover = Canvas((W, H), design, logo)
    cover.img.paste(cover.primary, (0, 0, W, H))
    cover.brand_mark(business.get("name", ""), cover.pad, int(H * 0.3), int(H * 0.08), cover.on_primary, W - 2 * cover.pad)
    cover.text("Brand kit", cover.pad, int(H * 0.42), W - 2 * cover.pad, 120, "heading", 96, cover.on_primary)
    cover.text((messages.get("core") or {}).get("one_line", ""), cover.pad, int(H * 0.5), W - 2 * cover.pad, 300, "body", 48, cover.on_primary, leading=1.35)
    pages.append(cover.img)
    page, y = None, 0

    def new_page():
        nonlocal page, y
        page = Canvas((W, H), design, logo)
        page.draw.rectangle((0, 0, W, 14), fill=page.primary)
        pages.append(page.img)
        y = page.pad

    new_page()
    for sec, label in brand_kit.SECTION_LABELS.items():
        items = (messages or {}).get(sec) or {}
        if y > H - 400:
            new_page()
        y += page.text(label, page.pad, y, W - 2 * page.pad, 80, "heading", 44, page.primary) + 16
        for k, v in items.items():
            if isinstance(v, dict):
                v = " · ".join(f"{a}: {b}" for a, b in v.items() if b)
            if isinstance(v, list):
                v = "\n".join(f"• {x}" for x in v)
            if not v:
                continue
            est = 60 + 40 * (len(str(v)) // 70 + str(v).count("\n") + 1)
            if y + est > H - page.pad:
                new_page()
            y += page.text(k.replace("_", " ").capitalize(), page.pad, y, W - 2 * page.pad, 40, "body", 24, "#5B6370", bold=True) + 4
            y += page.text(str(v), page.pad, y, W - 2 * page.pad, H - page.pad - y, "body", 28, page.ink, leading=1.4, min_size=20) + 24
        y += 20
    new_page()
    y += page.text("Design system", page.pad, y, W - 2 * page.pad, 80, "heading", 44, page.primary) + 30
    sw = (W - 2 * page.pad - 60) // 3
    for i, k in enumerate(("primary", "secondary", "accent")):
        col = (design.get("colors") or {}).get(k, "#CCCCCC")
        x = page.pad + i * (sw + 30)
        page.draw.rounded_rectangle((x, y, x + sw, y + 240), radius=page.r(1), fill=col)
        page.text(f"{k.capitalize()}\n{col}", x + 20, y + 140, sw - 40, 90, "body", 26, brand_kit.text_on(col), bold=True)
    y += 290
    fonts = design.get("fonts") or {}
    labels = {k: lab for k, lab, *_ in brand_kit.FONTS}
    y += page.text(f"Headings: {labels.get(fonts.get('heading'), '')}", page.pad, y, W - 2 * page.pad, 70, "heading", 48, page.ink) + 20
    y += page.text(f"Body text: {labels.get(fonts.get('body'), '')} — नमस्ते, स्वागत है", page.pad, y, W - 2 * page.pad, 60, "body", 32, page.ink) + 20
    page.text(f"Corners: {design.get('corners', 'soft')} · Spacing: {design.get('spacing', 'comfortable')}", page.pad, y, W - 2 * page.pad, 50, "body", 26, "#5B6370")
    return pages
