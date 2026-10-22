"""
Draws the Day-1 report in the Business AI "Blueprint" brand: a 1080×1920 PNG for WhatsApp (under 1 MB), a one-page A4
PDF, and a 1200×630 preview image for the share link.

One layout engine places every block (top to bottom, exactly as the spec lists them) and returns drawing operations;
Pillow draws them for the images and reportlab for the PDF. If a report is long, every size steps down together until
the page fits, so the PDF is always one page and the PNG always 1080×1920.

Brand tokens (no icons, photos or gradients): ink #0A1826 · panel #12202D · line #1B2A38 · text #EEF4F6 · grey #8A9BB5;
aqua #3FD6C9 for systems words only, amber #F2A93B for money only; Space Grotesk headlines, Inter body. The fonts are
bundled in app/fonts (SIL Open Font License).
"""
import io
import re
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

TOKENS = {"ink": "#0A1826", "panel": "#12202D", "line": "#1B2A38", "text": "#EEF4F6", "grey": "#8A9BB5", "aqua": "#3FD6C9", "amber": "#F2A93B"}
FONT_DIR = Path(__file__).resolve().parent / "fonts"
FONTS = {"head": "SpaceGrotesk-600.ttf", "head_bold": "SpaceGrotesk-700.ttf", "body": "Inter-400.ttf", "body_medium": "Inter-500.ttf",
         "body_semi": "Inter-600.ttf", "body_bold": "Inter-700.ttf"}
PNG_SIZE = (1080, 1920)
PREVIEW_SIZE = (1200, 630)
MAX_PNG_BYTES = 1_000_000
_AMOUNT = r"\d[\d,]*(?:\.\d+)?(?:\s?(?:L|Cr|lakh|crore)\b)?"
MONEY = re.compile(rf"₹\s?{_AMOUNT}(?:\s?[–-]\s?{_AMOUNT})?")   # ₹85,000 · ₹50 L–1 Cr


# ───────────────────────── text measuring (one per output) ─────────────────────────
class PilMeasure:
    """Pixel sizes are whole numbers, so measuring and drawing use exactly the same font object."""
    @staticmethod
    @lru_cache(maxsize=256)
    def font(key: str, size: float) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(str(FONT_DIR / FONTS[key]), max(6, int(round(size))), layout_engine=ImageFont.Layout.BASIC)

    @staticmethod
    @lru_cache(maxsize=50_000)
    def width(text: str, key: str, size: float) -> float:   # the same words come back on every report
        return PilMeasure.font(key, size).getlength(text)

    def ascent(self, key: str, size: float) -> float:
        return self.font(key, size).getmetrics()[0]

    def size(self, size: float) -> float:
        return max(6, int(round(size)))


_registered = False
# ReportLab keeps each registered font's subset state on the shared TTFont object, so two PDFs drawn at the same moment in
# different threads (reports and invoices are drawn with asyncio.to_thread) can corrupt each other ("list index out of
# range" in makeSubset). Every PDF that uses these fonts is drawn under this lock; one takes a few milliseconds.
PDF_LOCK = threading.RLock()


def _register_pdf_fonts() -> None:
    global _registered
    if _registered:
        return
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    with PDF_LOCK:
        if _registered:
            return
        for key, file in FONTS.items():
            pdfmetrics.registerFont(TTFont(f"day1-{key}", str(FONT_DIR / file)))
        _registered = True


class PdfMeasure:
    def __init__(self):
        _register_pdf_fonts()
        from reportlab.pdfbase import pdfmetrics
        self.m = pdfmetrics

    def width(self, text: str, key: str, size: float) -> float:
        return self.m.stringWidth(text, f"day1-{key}", size)

    def ascent(self, key: str, size: float) -> float:
        return self.m.getAscent(f"day1-{key}", size)

    def size(self, size: float) -> float:
        return size


# ───────────────────────── rich text ─────────────────────────
def segments(text: str, color: str) -> list[tuple[str, str]]:
    """[systems words] → aqua; rupee amounts → amber; the rest in `color`."""
    out = []
    for i, part in enumerate(re.split(r"\[([^\[\]]*)\]", text or "")):
        if i % 2:
            out.append((part, TOKENS["aqua"]))
            continue
        pos = 0
        for m in MONEY.finditer(part):
            if m.start() > pos:
                out.append((part[pos:m.start()], color))
            out.append((m.group(0), TOKENS["amber"]))
            pos = m.end()
        if pos < len(part):
            out.append((part[pos:], color))
    return [(s, c) for s, c in out if s]


def _words(segs: list[tuple[str, str]]) -> list[list[tuple[str, str]]]:
    """Words for wrapping; a word can carry several colours (e.g. '₹4,32,000.' = amber + text)."""
    words, cur = [], []
    for s, c in segs:
        for tok in re.split(r"(\s+)", s):
            if not tok:
                continue
            if tok.isspace():
                if cur:
                    words.append(cur)
                    cur = []
            else:
                cur.append((tok, c))
    if cur:
        words.append(cur)
    return words


@dataclass
class Box:
    ops: list
    y: float


class Page:
    def __init__(self, measure, width: float, height: float, scale: float, margin: float):
        self.m, self.W, self.H, self.s, self.margin = measure, width, height, scale, margin
        self.ops: list[dict] = []
        self.x0, self.x1 = margin, width - margin

    def sz(self, px: float) -> float:
        return self.m.size(px * self.s)

    def rect(self, x, y, w, h, fill=None, stroke=None, r=0):
        self.ops.append({"t": "rect", "x": x, "y": y, "w": w, "h": h, "fill": fill, "stroke": stroke, "r": r})

    def lines(self, text: str, font: str, size: float, width: float) -> list[list[tuple[str, str]]]:
        """Greedy wrap. Returns lines as lists of words (each word a list of (text, colour))."""
        space = self.m.width(" ", font, size)
        out, line, w = [], [], 0.0
        for word in _words(segments(text, "")):
            ww = sum(self.m.width(t, font, size) for t, _ in word)
            if line and w + space + ww > width:
                out.append(line)
                line, w = [], 0.0
            w += (space if line else 0) + ww
            line.append(word)
        if line:
            out.append(line)
        return out

    def text(self, text: str, font: str, px: float, color: str, y: float, x: float | None = None, width: float | None = None,
             leading: float = 1.38, draw: bool = True) -> float:
        """Writes wrapped rich text from y (top). Returns the height used."""
        size = self.sz(px)
        x = self.x0 if x is None else x
        width = (self.x1 - x) if width is None else width
        lh = size * leading
        asc = self.m.ascent(font, size)
        rows = self.lines(text, font, size, width)
        for i, row in enumerate(rows):
            if not draw:
                continue
            runs: list[list] = []          # one drawn string per colour run, spaces included, so a PDF reads as words
            for j, word in enumerate(row):
                for k, (t, c) in enumerate(word):
                    piece = (" " if j and not k else "") + t
                    if runs and runs[-1][1] == c:
                        runs[-1][0] += piece
                    else:
                        runs.append([piece, c])
            cx, base = x, y + i * lh + asc
            for t, c in runs:
                self.ops.append({"t": "text", "x": cx, "y": base, "s": t, "font": font, "size": size, "color": c or color})
                cx += self.m.width(t, font, size)
        return len(rows) * lh


# ───────────────────────── the report layout ─────────────────────────
def _report(page: Page, r: dict, draw_footer_at: float | None) -> tuple[float, float]:
    """Lays the report out on `page`. Returns (bottom of the content, height of the footer)."""
    T, s = TOKENS, page.sz
    gap = s(34)
    y = page.margin
    y += page.text(r["eyebrow"].upper(), "body_semi", 22, T["grey"], y) + s(14)
    y += page.text(r["title"], "head", 62, T["text"], y, leading=1.12) + s(14)
    y += page.text(r["meta"], "body", 22, T["grey"], y, leading=1.42) + gap

    # three tiles: the number, then the two numbers that explain it
    tile_gap = s(16)
    tw = (page.x1 - page.x0 - 2 * tile_gap) / 3
    pad = s(22)
    label_h = max(page.text(t["label"], "body_medium", 20, T["grey"], 0, width=tw - 2 * pad, draw=False) for t in r["tiles"])
    value_size = s(58)
    th = pad + value_size * 1.08 + s(10) + label_h + pad
    for i, t in enumerate(r["tiles"]):
        x = page.x0 + i * (tw + tile_gap)
        page.rect(x, y, tw, th, fill=T["panel"], stroke=T["line"], r=s(14))
        base = y + pad + page.m.ascent("head", value_size)
        page.ops.append({"t": "text", "x": x + pad, "y": base, "s": t["value"], "font": "head", "size": value_size, "color": T["text"]})
        if t.get("suffix"):
            sx = x + pad + page.m.width(t["value"], "head", value_size) + s(4)
            page.ops.append({"t": "text", "x": sx, "y": base, "s": t["suffix"], "font": "head", "size": s(26), "color": T["grey"]})
        page.text(t["label"], "body_medium", 20, T["grey"], y + pad + value_size * 1.08 + s(10), x=x + pad, width=tw - 2 * pad)
    y += th + gap

    # what you're missing — with the Estimate tag
    m = r["missing"]
    head_size = s(28)
    page.text(m["heading"], "head", 28, T["text"], y)
    tag_size = s(16)
    tag_w = page.m.width(m["tag"].upper(), "body_semi", tag_size) + s(24)
    tag_h = s(30)
    tx = page.x0 + page.m.width(m["heading"], "head", head_size) + s(16)
    ty = y + (head_size * 1.38 - tag_h) / 2
    page.rect(tx, ty, tag_w, tag_h, fill=T["panel"], stroke=T["line"], r=tag_h / 2)
    page.ops.append({"t": "text", "x": tx + s(12), "y": ty + (tag_h + page.m.ascent("body_semi", tag_size) * 0.72) / 2, "s": m["tag"].upper(),
                     "font": "body_semi", "size": tag_size, "color": T["grey"]})
    y += head_size * 1.38 + s(6)
    y += page.text(m["line"], "head", 38, T["text"], y, leading=1.16) + s(10)
    y += page.text(m["math"], "body", 23, T["text"], y) + gap

    # where you're lacking — three findings
    y += page.text(r["findings_heading"], "head", 28, T["text"], y) + s(8)
    for i, fnd in enumerate(r["findings"]):
        if i:
            page.rect(page.x0, y + s(6), page.x1 - page.x0, max(1, s(1.5)), fill=T["line"])
            y += s(18)
        y += page.text(fnd["label"], "body_bold", 24, T["text"], y) + s(2)
        y += page.text(fnd["text"], "body", 22, T["grey"], y)
    y += gap

    # imagine — 30 days from now
    y += page.text(r["imagine_heading"], "head", 28, T["text"], y) + s(8)
    num_w = s(34)
    for i, line in enumerate(r["imagine"]):
        page.ops.append({"t": "text", "x": page.x0, "y": y + page.m.ascent("head", s(22)), "s": str(i + 1), "font": "head",
                         "size": s(22), "color": T["grey"]})
        y += page.text(line, "body", 22, T["text"], y, x=page.x0 + num_w) + s(6)
    y += gap - s(6)

    # bottom line
    b = r["bottom"]
    pad = s(26)
    inner = page.x1 - page.x0 - 2 * pad
    h_gap = page.text(b["gap"], "body", 24, T["text"], 0, width=inner, draw=False)
    h_prog = page.text(b["program"], "body_bold", 25, T["text"], 0, width=inner, draw=False)
    h_pic = page.text(b["picture"], "body", 22, T["grey"], 0, width=inner, draw=False)
    bh = pad + h_gap + s(10) + h_prog + s(10) + h_pic + pad
    page.rect(page.x0, y, page.x1 - page.x0, bh, fill=T["panel"], stroke=T["line"], r=s(16))
    yy = y + pad
    yy += page.text(b["gap"], "body", 24, T["text"], yy, x=page.x0 + pad, width=inner) + s(10)
    yy += page.text(b["program"], "body_bold", 25, T["text"], yy, x=page.x0 + pad, width=inner) + s(10)
    page.text(b["picture"], "body", 22, T["grey"], yy, x=page.x0 + pad, width=inner)
    y += bh

    # footer, pinned to the bottom of the page
    f = r["footer"]
    fh = (page.text(f["method"], "body", 16, T["grey"], 0, draw=False) + s(8)
          + page.text(f["disclaimer"], "body", 16, T["grey"], 0, draw=False) + s(8)
          + page.text(f["credits"], "body_semi", 16, T["grey"], 0, draw=False))
    if draw_footer_at is not None:
        fy = draw_footer_at - fh
        page.rect(page.x0, fy - s(20), page.x1 - page.x0, max(1, s(2)), fill=T["line"])
        fy += page.text(f["method"], "body", 16, T["grey"], fy) + s(8)
        fy += page.text(f["disclaimer"], "body", 16, T["grey"], fy) + s(8)
        page.text(f["credits"], "body_semi", 16, T["grey"], fy)
    return y, fh


def layout(report: dict, measure, width: float, height: float, unit: float) -> list[dict]:
    """Every size × unit × a fitting factor that steps down until content + footer fit the page."""
    margin = 64 * unit
    fit = 1.0
    while True:
        page = Page(measure, width, height, unit * fit, margin)
        bottom, footer_h = _report(page, report, None)
        if bottom + 44 * unit * fit + footer_h <= height - margin or fit <= 0.55:
            page = Page(measure, width, height, unit * fit, margin)
            _report(page, report, height - margin)
            return [{"t": "bg", "fill": TOKENS["ink"]}] + page.ops
        fit = round(fit - 0.03, 2)


# ───────────────────────── drawing ─────────────────────────
def _draw_pil(ops: list[dict], size: tuple[int, int]) -> Image.Image:
    img = Image.new("RGB", size, TOKENS["ink"])
    d = ImageDraw.Draw(img)
    for op in ops:
        if op["t"] == "rect":
            box = [round(op["x"]), round(op["y"]), round(op["x"] + op["w"]), round(op["y"] + op["h"])]
            if op["r"]:
                d.rounded_rectangle(box, radius=round(op["r"]), fill=op["fill"], outline=op["stroke"], width=2 if op["stroke"] else 0)
            else:
                d.rectangle(box, fill=op["fill"], outline=op["stroke"])
        elif op["t"] == "text":
            d.text((op["x"], op["y"]), op["s"], font=PilMeasure.font(op["font"], op["size"]), fill=op["color"], anchor="ls")
    return img


def _png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", compress_level=3)   # ~0.05 s and ~245 KB; optimize=True saves 10% for 7× the time
    data = buf.getvalue()
    if len(data) > MAX_PNG_BYTES:  # never seen with this design, but WhatsApp media must stay small
        buf = io.BytesIO()
        img.quantize(colors=128, method=Image.Quantize.MEDIANCUT).save(buf, "PNG", optimize=True)
        data = buf.getvalue()
    return data


def png(report: dict) -> bytes:
    ops = layout(report, PilMeasure(), PNG_SIZE[0], PNG_SIZE[1], 1.0)
    return _png_bytes(_draw_pil(ops, PNG_SIZE))


def pdf(report: dict, title: str = "Your report") -> bytes:
    with PDF_LOCK:
        return _pdf(report, title)


def _pdf(report: dict, title: str) -> bytes:
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    width, height = A4
    ops = layout(report, PdfMeasure(), width, height, width / PNG_SIZE[0])
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, pageCompression=1, initialFontName="day1-body", initialFontSize=10)
    c.setTitle(title)
    c.setAuthor(report.get("footer", {}).get("credits", ""))
    c.setCreator("Business AI Action Hub")
    for op in ops:
        if op["t"] == "bg":
            c.setFillColor(HexColor(op["fill"]))
            c.rect(0, 0, width, height, stroke=0, fill=1)
        elif op["t"] == "rect":
            c.setFillColor(HexColor(op["fill"])) if op["fill"] else None
            if op["stroke"]:
                c.setStrokeColor(HexColor(op["stroke"]))
                c.setLineWidth(1)
            y = height - op["y"] - op["h"]
            if op["r"]:
                c.roundRect(op["x"], y, op["w"], op["h"], op["r"], stroke=1 if op["stroke"] else 0, fill=1 if op["fill"] else 0)
            else:
                c.rect(op["x"], y, op["w"], op["h"], stroke=1 if op["stroke"] else 0, fill=1 if op["fill"] else 0)
        elif op["t"] == "text":
            c.setFont(f"day1-{op['font']}", op["size"])
            c.setFillColor(HexColor(op["color"]))
            c.drawString(op["x"], height - op["y"], op["s"])
    c.showPage()
    c.save()
    return buf.getvalue()


def preview(first_name: str, score: int, eyebrow: str, label: str, note: str = "Open to see the full report") -> bytes:
    """The share link's preview image: first name and the number only."""
    W, H = PREVIEW_SIZE
    page = Page(PilMeasure(), W, H, 1.0, 72)
    T = TOKENS
    y = 72
    y += page.text(eyebrow.upper(), "body_semi", 26, T["grey"], y) + 18
    y += page.text(f"{first_name}'s {label}", "head", 56, T["text"], y, leading=1.1) + 18
    base = y + page.m.ascent("head", 150)
    page.ops.append({"t": "text", "x": 72, "y": base, "s": str(score), "font": "head", "size": 150, "color": T["text"]})
    page.ops.append({"t": "text", "x": 72 + page.m.width(str(score), "head", 150) + 10, "y": base, "s": "/100", "font": "head", "size": 54,
                     "color": T["grey"]})
    page.rect(72, H - 120, W - 144, 2, fill=T["line"])
    page.text(note, "body", 26, T["grey"], H - 100)
    return _png_bytes(_draw_pil(page.ops, PREVIEW_SIZE))


def all_files(report: dict, title: str) -> dict:
    return {"png": png(report), "pdf": pdf(report, title)}
