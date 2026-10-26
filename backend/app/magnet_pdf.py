"""
The lead magnet as a PDF in the owner's brand (v2.3): a cover, "inside this guide", every section on its own page (a guide)
or flowing with tick boxes (a checklist), and a last page with how to reach the owner and a QR to their website.

Drawn with the brand kit's design (colours, heading and body fonts, logo, corners) on A4 pages at 150 dpi through the
same canvas as the brand kit PDF (app/designs.py): Devanagari-capable fonts shaped with Raqm, so Hindi and Marathi text
render correctly. No AI here.
"""
import io

from PIL import Image

from . import designs

W, H = 1240, 1754          # A4 at 150 dpi
DPI = 150
MUTED = "#5B6370"


class Doc:
    def __init__(self, design: dict, business: dict, logo: Image.Image | None, title: str):
        self.design, self.business, self.logo, self.title = design, business, logo, title
        self.pages: list[designs.Canvas] = []
        self.page: designs.Canvas | None = None
        self.y = 0

    @property
    def pad(self) -> int:
        return self.page.pad if self.page else 110

    def new_page(self) -> designs.Canvas:
        c = designs.Canvas((W, H), self.design, self.logo)
        c.draw.rectangle((0, 0, W, 14), fill=c.primary)
        self.pages.append(c)
        self.page, self.y = c, c.pad
        return c

    def bottom(self) -> int:
        return H - self.pad - 60

    def room(self, need: int) -> None:
        if self.page is None or self.y + need > self.bottom():
            self.new_page()

    def lines(self, text: str, role: str, size: int, width: int, bold: bool | None = None) -> tuple[list[str], object]:
        f = self.page.font(role, size, text, bold)
        return self.page.wrap(text, f, width), f

    def para(self, text: str, size: int = 30, color: str | None = None, role: str = "body", bold: bool | None = None, indent: int = 0,
             leading: float = 1.45, gap: int = 18) -> None:
        """A paragraph that flows onto the next page when it runs out of room."""
        text = (text or "").strip()
        if not text:
            return
        self.room(int(size * leading) * 2)
        width = W - 2 * self.pad - indent
        lines, f = self.lines(text, role, size, width, bold)
        lh = int(size * leading)
        for line in lines:
            if self.y + lh > self.bottom():
                self.new_page()
                f = self.page.font(role, size, text, bold)
            self.page.draw.text((self.pad + indent, self.y), line, font=f, fill=color or self.page.ink)
            self.y += lh
        self.y += gap

    def point(self, text: str, tick: bool, size: int = 30) -> None:
        text = (text or "").strip()
        if not text:
            return
        lh = int(size * 1.4)
        self.room(lh + 10)
        c = self.page
        box = int(size * 0.8)
        x = self.pad
        if tick:
            c.draw.rounded_rectangle((x, self.y + 6, x + box, self.y + 6 + box), radius=4, outline=c.primary, width=3)
        else:
            r = int(size * 0.18)
            c.draw.ellipse((x + box // 2 - r, self.y + lh // 2 - r, x + box // 2 + r, self.y + lh // 2 + r), fill=c.accent)
        self.para(text, size=size, indent=box + 22, leading=1.4, gap=8)

    def heading(self, text: str, number: int | None = None) -> None:
        self.room(260)
        c = self.page
        if number is not None:
            c.pill(f"{number:02d}", self.pad, self.y, 26, c.primary, c.on_primary)
            self.y += 76
        self.para(text, size=52, color=c.primary, role="heading", leading=1.18, gap=26)

    def footers(self) -> None:
        name = self.business.get("name", "")
        total = len(self.pages) + 1          # the cover is page 1
        for i, c in enumerate(self.pages):
            f = c.font("body", 22, name)
            c.draw.text((c.pad, H - c.pad + 10), f"{name} · {self.title}"[:90], font=f, fill=MUTED)
            label = f"{i + 2} / {total}"
            c.draw.text((W - c.pad - f.getlength(label), H - c.pad + 10), label, font=f, fill=MUTED)


def cover(content: dict, design: dict, business: dict, logo: Image.Image | None, kind: str) -> Image.Image:
    c = designs.Canvas((W, H), design, logo)
    c.img.paste(c.primary, (0, 0, W, H))
    c.draw.rectangle((0, H - 40, W, H), fill=c.accent)
    c.brand_mark(business.get("name", ""), c.pad, c.pad, 120, c.on_primary, int(W * 0.55))
    y = int(H * 0.30)
    c.pill(f"Free {kind}", c.pad, y, 30, c.accent, c.on_accent)
    y += 110
    y += c.text(content.get("title", ""), c.pad, y, W - 2 * c.pad, int(H * 0.30), "heading", 104, c.on_primary, leading=1.1, min_size=56) + 40
    c.text(content.get("subtitle", ""), c.pad, y, W - 2 * c.pad, int(H * 0.16), "body", 44, c.on_primary, leading=1.35, min_size=28)
    line = " · ".join(x for x in (business.get("name", ""), business.get("city", "")) if x)
    c.text(line, c.pad, H - c.pad - 90, W - 2 * c.pad, 60, "body", 34, c.on_primary, min_size=20)
    return c.img


def render(content: dict, landing: dict, design: dict, business: dict, logo: Image.Image | None, site_link: str | None,
           fmt: str, long_form: bool) -> tuple[bytes, int]:
    """(PDF bytes, number of pages)."""
    kind = {"checklist": "checklist", "calculator": "calculator sheet", "price_guide": "price guide", "mistakes": "guide",
            "questions": "guide"}.get(fmt, "guide")
    doc = Doc(design, business, logo, content.get("title", ""))
    images = [cover(content, design, business, logo, kind)]
    # inside this guide + the opening
    doc.new_page()
    doc.para("Inside", size=26, color=MUTED, bold=True, gap=6)
    doc.para(content.get("title", ""), size=56, color=doc.page.primary, role="heading", leading=1.15, gap=30)
    doc.para(content.get("intro", ""), size=32, gap=34)
    for i, s in enumerate(content.get("sections") or []):
        doc.point(f"{i + 1}.  {s.get('heading', '')}", tick=False, size=30)
    if landing.get("benefits"):
        doc.y += 20
        doc.para("What you'll get from it", size=26, color=MUTED, bold=True, gap=8)
        for b in landing["benefits"][:3]:
            doc.point(b, tick=False, size=30)
    tick = not long_form
    for i, s in enumerate(content.get("sections") or []):
        if long_form or i == 0:
            doc.new_page()
        else:
            doc.y += 30
        doc.heading(s.get("heading", ""), i + 1)
        for chunk in [x for x in (s.get("text") or "").split("\n\n") if x.strip()]:
            doc.para(chunk, size=31)
        for pt in s.get("points") or []:
            doc.point(pt, tick=tick, size=30)
    # what to do next
    doc.new_page()
    c = doc.page
    c.draw.rectangle((0, 0, W, int(H * 0.36)), fill=c.primary)
    c.text("What to do next", c.pad, c.pad + 40, W - 2 * c.pad, 120, "heading", 72, c.on_primary)
    c.text(content.get("cta", ""), c.pad, c.pad + 170, W - 2 * c.pad, int(H * 0.36) - c.pad - 200, "body", 40, c.on_primary, leading=1.4, min_size=26)
    doc.y = int(H * 0.36) + 70
    contact = [("WhatsApp", business.get("whatsapp", "")), ("Email", business.get("email", "")),
               ("Website", (site_link or "").replace("https://", "").replace("http://", ""))]
    for label, value in contact:
        if value:
            doc.para(label, size=24, color=MUTED, bold=True, gap=2)
            doc.para(value, size=38, gap=22)
    if site_link:
        size = 360
        c.qr(site_link, W - c.pad - size, int(H * 0.36) + 70, size)
    doc.footers()
    images += [p.img for p in doc.pages]
    return designs.pdf(images, DPI), len(images)


def cover_png(content: dict, design: dict, business: dict, logo: Image.Image | None) -> bytes:
    """The square cover shown on the download page and in the hub (the catalogue-cover template)."""
    img = designs.render("catalogue_cover", {"title": (content.get("title") or "")[:60], "sub": (content.get("subtitle") or "A free download")[:120]},
                         design, business, logo)
    return designs.png(img)


def pdf_bytes_ok(data: bytes) -> bool:
    return data[:5] == b"%PDF-"


def page_count(data: bytes) -> int:
    from pypdf import PdfReader
    return len(PdfReader(io.BytesIO(data)).pages)
