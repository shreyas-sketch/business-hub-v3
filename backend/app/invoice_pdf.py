"""
A quotation, tax invoice or credit note as an A4 PDF — the same document as the print page (templates/quote.html), laid
out by invoice_doc.compose: the accent bar, logo and address block, the title and its tag, the reference column, Billed by /
Billed to, the lines with SAC and description, taxable value with CGST + SGST or IGST, the amount due, the amount in words,
how to pay and the bank details, notes and terms, and the authorised signatory. A long document runs onto more pages.
Attached to invoice emails and payment follow-ups, and served at the document's shared link.
"""
import asyncio
import io

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from . import day1_render, invoice_doc

INK, MUTED, FAINT, LINE, SOFT, ADDR = "#14171C", "#6B7280", "#9CA3AF", "#E5E7EB", "#F5F6F8", "#4B5563"
SANS, MEDIUM, SEMI, BOLD = "day1-body", "day1-body_medium", "day1-body_semi", "day1-body_bold"
MONO, MONO_MEDIUM = "inv-mono", "inv-mono-medium"
_mono = False


def _fonts() -> None:
    """Inter (shared with the Day-1 report) and JetBrains Mono for the figures and labels."""
    global _mono
    day1_render._register_pdf_fonts()
    if _mono:
        return
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    with day1_render.PDF_LOCK:
        if not _mono:
            pdfmetrics.registerFont(TTFont(MONO, str(day1_render.FONT_DIR / "JetBrainsMono-400.ttf")))
            pdfmetrics.registerFont(TTFont(MONO_MEDIUM, str(day1_render.FONT_DIR / "JetBrainsMono-500.ttf")))
            _mono = True


def _mix(hex_colour: str, t: float) -> str:
    """t of the colour over white."""
    try:
        r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    except (ValueError, TypeError):
        r, g, b = 47, 91, 234
    return "#" + "".join(f"{round(v * t + 255 * (1 - t)):02X}" for v in (r, g, b))


async def pdf_for(q: dict) -> bytes:
    """The document's PDF, with everything it shows read from the database."""
    d = await invoice_doc.build(q)
    return await asyncio.to_thread(render_doc, d)


def render(q: dict, business: dict, s: dict | None, accent: str = "#2F5BEA") -> bytes:
    """The PDF from what's already at hand (no logo or signature): the AI's own sample invoice in tests and reading."""
    return render_doc(invoice_doc.compose(q, business, s, accent))


def render_doc(d: dict) -> bytes:
    _fonts()
    with day1_render.PDF_LOCK:      # the fonts are shared with the Day-1 report: one PDF at a time (see day1_render)
        return _render(d)


def _render(d: dict) -> bytes:
    from reportlab.pdfbase.pdfmetrics import stringWidth
    W, H = A4
    L, R, TOP, BOTTOM = 62, W - 46, H - 46, 56
    accent = d.get("accent") or "#2F5BEA"
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, pageCompression=1, initialFontName=SANS, initialFontSize=10)
    c.setTitle(f"{d['title']} {d['number']} — {d['seller']['brand']}")

    def width(t, font, size, space=0.0):
        t = str(t or "")
        return stringWidth(t, font, size) + space * max(len(t) - 1, 0)

    def text(x, y, t, size=9, font=SANS, color=INK, right=False, space=0.0):
        t = str(t or "")
        c.setFont(font, size)
        c.setFillColor(HexColor(color))
        if right:
            x -= width(t, font, size, space)
        if space:
            o = c.beginText(x, y)
            o.setFont(font, size)
            o.setCharSpace(space)
            o.textOut(t)
            o.setCharSpace(0)          # the spacing would otherwise carry on into every text after it
            c.drawText(o)
        else:
            c.drawString(x, y, t)

    def money(x, y, amount, size=8.4, font=MONO, color=INK):
        """₹ in Inter (the mono face has no rupee sign), the figures in mono; right-aligned at x."""
        figures = invoice_doc.inr(amount)
        sign, digits = ("-₹", figures[2:]) if figures.startswith("-") else ("₹", figures[1:])
        dw = width(digits, font, size)
        text(x - dw, y, digits, size, font, color)
        text(x - dw - width(sign, SANS, size) - 0.6, y, sign, size, SANS, color)

    def cap(x, y, t, color=FAINT, right=False, size=6.2):
        text(x, y, str(t).upper(), size, MONO_MEDIUM, color, right, space=1.3)

    def wrap(t, font, size, w):
        out = []
        for para in str(t or "").split("\n"):
            line = ""
            for word in para.split():
                trial = f"{line} {word}".strip()
                if width(trial, font, size) <= w or not line:
                    line = trial
                else:
                    out.append(line)
                    line = word
            out.append(line)
        return [x for x in out] if any(out) else []

    def rule(x1, y, x2, color=LINE, w=0.6):
        c.setStrokeColor(HexColor(color))
        c.setLineWidth(w)
        c.line(x1, y, x2, y)

    def page_frame():
        c.setFillColor(HexColor(accent))
        c.rect(34, 40, 4.5, H - 80, stroke=0, fill=1)
        rule(L, 46, R)
        cap(L, 34, d["footer"][:120], size=5.6)

    page_frame()
    y = TOP

    def need(h):
        nonlocal y
        if y - h < BOTTOM:
            c.showPage()
            page_frame()
            y = TOP

    # ── header: logo (or the name as a wordmark) and the legal name; the address block on the right ──
    s = d["seller"]
    logo_bottom = y
    if d.get("logo"):
        try:
            img = ImageReader(io.BytesIO(d["logo"]))
            iw, ih = img.getSize()
            h = 42.0
            w = min(170.0, iw * h / ih)
            h = w * ih / iw
            c.drawImage(img, L, y - h, w, h, mask="auto")
            logo_bottom = y - h
        except Exception:  # noqa: BLE001  a logo that can't be read: the name instead
            d = {**d, "logo": None}
    if not d.get("logo"):
        text(L, y - 18, s["brand"], 18, BOLD)
        logo_bottom = y - 24
    cap(L, logo_bottom - 14, s["name"])
    ay = y - 6
    addr = [ln for part in s["address"].split("\n") for ln in wrap(part, SANS, 7.8, 220)]
    for i, ln in enumerate(addr):
        text(R, ay, ln, 7.8, color=ADDR, right=True)
        if i == 0:
            text(R - width(ln, SANS, 7.8) - 4, ay, "A", 6, MONO_MEDIUM, accent, right=True)
        ay -= 11
    for label, value in (("T", s["phone"]), ("E", s["email"]), ("GST", s["gstin"])):
        if value:
            text(R, ay, value, 7.8, MONO if label == "GST" else SANS, ADDR, right=True)
            text(R - width(value, MONO if label == "GST" else SANS, 7.8) - 4, ay, label, 6, MONO_MEDIUM, accent, right=True)
            ay -= 11
    y = min(logo_bottom - 22, ay - 4)
    rule(L, y, R)
    y -= 26

    # ── title, its tag, and the reference column ──
    text(L, y, d["title"], 19, BOLD)
    tag = d["tag"].upper()
    tw = width(tag, MONO_MEDIUM, 6, 1.5) + 18
    c.setFillColor(HexColor(_mix(accent, .12)))
    c.roundRect(L, y - 24, tw, 14, 7, stroke=0, fill=1)
    text(L + 9, y - 19.4, tag, 6, MONO_MEDIUM, accent, space=1.5)
    my = y + 4
    vw = max((width(v, MONO_MEDIUM, 8) for _, v in d["meta"]), default=0)
    for k, v in d["meta"]:
        text(R, my, v, 8, MONO_MEDIUM, INK, right=True)
        text(R - vw - 10, my, k, 8, SANS, MUTED, right=True)
        my -= 13
    y = min(y - 40, my - 6)

    # ── Billed by / Billed to ──
    b = d["buyer"]
    cw = (R - L - 14) / 2

    def card_lines(who, address, extra, ids):
        rows = [("name", ln) for ln in wrap(who, SEMI, 9.5, cw - 28)]
        rows += [("addr", ln) for ln in wrap(address, SANS, 7.8, cw - 28)]
        rows += [("addr", ln) for ln in wrap(extra, SANS, 7.8, cw - 28)]
        rows += [("ids", x) for x in ids if x]
        return rows
    ids_by = [" ".join(x for x in (f"GSTIN  {s['gstin']}" if s["gstin"] else "",) if x),
              "   ".join(x for x in (f"PAN  {s['pan']}" if s["pan"] else "", f"CIN  {s['cin']}" if s["cin"] else "") if x)]
    left = card_lines(s["name"], s["address"], "", ids_by)
    right = card_lines(b["name"], b["address"], " · ".join(x for x in (b["phone"], b["email"]) if x),
                       [f"GSTIN  {b['gstin']}" if b["gstin"] else ""])
    heights = [sum(13 if k == "name" else 10.5 if k == "addr" else 12 for k, _ in rows) + 34 for rows in (left, right)]
    ch = max(heights)
    need(ch + 10)
    for i, (rows, label) in enumerate(((left, "From" if d["kind"] == "quote" else "Billed by"),
                                        (right, "For" if d["kind"] == "quote" else "Billed to"))):
        x = L + i * (cw + 14)
        c.setStrokeColor(HexColor(LINE))
        c.setLineWidth(0.8)
        c.roundRect(x, y - ch, cw, ch, 8, stroke=1, fill=0)
        cap(x + 14, y - 16, label)
        yy = y - 31
        for k, v in rows:
            if k == "name":
                text(x + 14, yy, v, 9.5, SEMI)
                yy -= 13
            elif k == "addr":
                text(x + 14, yy, v, 7.8, SANS, MUTED)
                yy -= 10.5
            else:
                yy -= 2
                parts = v.split("   ")
                xx = x + 14
                for part in parts:
                    lab, _, val = part.partition("  ")
                    text(xx, yy, lab, 6.8, SANS, FAINT)
                    xx += width(lab, SANS, 6.8) + 5
                    text(xx, yy, val, 7.2, MONO_MEDIUM)
                    xx += width(val, MONO_MEDIUM, 7.2) + 16
                yy -= 11
    y -= ch + 22

    # ── the lines ──
    cols = [("Amount", R)]
    if d["show_gst"]:
        cols.insert(0, (d["gst_head"], R - 72))
    cols.insert(0, ("Taxable", cols[0][1] - 72))
    if d["show_sac"]:
        cols.insert(0, ("SAC / HSN", cols[0][1] - 66))
    desc_w = cols[0][1] - 60 - L

    def table_head():
        nonlocal y
        cap(L, y, "Description")
        for label, x in cols:
            cap(x, y, label, right=True)
        rule(L, y - 8, R)
        y -= 19
    need(60)
    table_head()
    for ln in d["lines"]:
        name = wrap(ln["name"], MEDIUM, 9.2, desc_w)
        sub = [ln["qty_line"] + (f" · GST {ln['gst_rate']:g}%" if d["show_gst"] and not d["gst_head"].endswith("%") else "")] if ln["qty_line"] else []
        desc = wrap(ln["description"], SANS, 7.2, desc_w + 20)
        h = len(name) * 12 + len(sub) * 10 + (len(desc) * 9 + 4 if desc else 0) + 14
        if y - h < BOTTOM:
            need(h + 40)
            table_head()
        top = y
        for t in name:
            text(L, y, t, 9.2, MEDIUM)
            y -= 12
        for t in sub:
            text(L, y, t, 7.2, SANS, FAINT)
            y -= 10
        if desc:
            y -= 2
            for t in desc:
                text(L, y, t, 7.2, SANS, MUTED)
                y -= 9
        values = ([ln["sac"]] if d["show_sac"] else []) + [ln["taxable"]] + ([ln["gst"]] if d["show_gst"] else []) + [ln["amount"]]
        for (label, x), v in zip(cols, values):
            if isinstance(v, str):
                text(x, top, v, 8.4, MONO, INK, right=True)
            else:
                money(x, top, v)
        y -= 4
        rule(L, y, R)
        y -= 14

    # ── totals ──
    need(len(d["rows"]) * 22 + 50)
    tx = R - 210
    y += 4
    for label, v in d["rows"]:
        text(tx + 6, y - 12, label, 8.4, SANS, ADDR)
        money(R - 6, y - 12, v, 8.6)
        y -= 17
        rule(tx, y, R)
    y -= 8
    rule(tx, y, R, INK, 1.6)
    text(tx + 6, y - 17, d["grand"], 10.2, BOLD)
    money(R - 6, y - 17, d["total"], 10.6, MONO_MEDIUM)
    y -= 32

    # ── amount in words ──
    words = wrap(d["words"], SANS, 9, R - L - 32)
    bh = 24 + len(words) * 12
    need(bh + 10)
    c.setFillColor(HexColor(SOFT))
    c.roundRect(L, y - bh, R - L, bh, 6, stroke=0, fill=1)
    c.setFillColor(HexColor(accent))
    c.rect(L, y - bh, 2.6, bh, stroke=0, fill=1)
    cap(L + 14, y - 12, "Amount in words")
    yy = y - 25
    for t in words:
        text(L + 14, yy, t, 9, SANS)
        yy -= 12
    y -= bh + 16

    # ── how to pay, and the bank details ──
    if d["bank"] or d["pay"]:
        half = (R - L - 28) / 2
        pay_lines = [t for p in d["pay"] for t in wrap(p, SANS, 8.2, half)]
        h = max(len(pay_lines) * 11.5, len(d["bank"]) * 12.5) + 14
        need(h + 6)
        if pay_lines:
            cap(L, y, "How to pay", accent)
            yy = y - 16
            for t in pay_lines:
                text(L, yy, t, 8.2)
                yy -= 11.5
        if d["bank"]:
            bx = L + half + 28
            cap(bx, y, "Bank & payment details", accent)
            yy = y - 16
            for k, v in d["bank"]:
                text(bx, yy, k, 8, SANS, FAINT)
                text(bx + 72, yy, v, 8.2, MONO, INK)
                yy -= 12.5
        y -= h + 10

    # ── notes and terms ──
    body = " ".join(x for x in (d["notes"], d["terms"]) if x)
    if body:
        label = "Notes & terms."
        lw = width(label + " ", SEMI, 7.4)
        lines = wrap(body, SANS, 7.4, R - L - lw)          # the first line sits after the bold label
        first, rest = (lines[0], " ".join(lines[1:])) if lines else ("", "")
        lines = [first] + wrap(rest, SANS, 7.4, R - L)
        need(len(lines) * 9.5 + 8)
        text(L, y, label, 7.4, SEMI)
        for i, t in enumerate(lines):
            text(L + (lw if i == 0 else 0), y, t, 7.4, SANS, ADDR)
            y -= 9.5
        y -= 4

    # ── the authorised signatory ──
    need(92)
    sx = R - 170
    y -= 16
    label = f"FOR {s['name'].upper()}"
    text(R - 85 - width(label, MONO_MEDIUM, 5.8, 1.1) / 2, y, label, 5.8, MONO_MEDIUM, FAINT, space=1.1)
    y -= 8
    if d.get("signature"):
        try:
            img = ImageReader(io.BytesIO(d["signature"]))
            iw, ih = img.getSize()
            h = 36.0
            w = min(150.0, iw * h / ih)
            h = w * ih / iw
            c.drawImage(img, R - 85 - w / 2, y - h, w, h, mask="auto")
        except Exception:  # noqa: BLE001
            pass
    y -= 40
    rule(sx, y, R, FAINT, 0.6)
    text(R - 85 - width("Authorised signatory", SEMI, 8.6) / 2, y - 13, "Authorised signatory", 8.6, SEMI)
    if d.get("signatory"):
        text(R - 85 - width(d["signatory"], SANS, 7.4) / 2, y - 25, d["signatory"], 7.4, SANS, MUTED)

    c.showPage()
    c.save()
    return buf.getvalue()
