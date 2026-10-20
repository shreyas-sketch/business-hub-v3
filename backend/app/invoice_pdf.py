"""
An invoice (or quotation, or credit note) as a one-page A4 PDF — attached to payment follow-up emails and invoice emails.
Same content as the print page (quote.html): seller, buyer, lines, GST split, total, due date, how to pay. The colour is
the owner's brand colour (Brand kit, else the website's accent).
"""
import io

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from . import day1_render
from .db import IST

INK, MUTED, LINE = "#14171C", "#5B6370", "#E6E8EC"


def _inr(x: float) -> str:
    whole, frac = f"{abs(x):.2f}".split(".")
    s = whole
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        s = ",".join(([head] if head else []) + groups) + "," + tail
    return ("-" if x < 0 else "") + "₹" + s + (f".{frac}" if frac != "00" else "")


def render(q: dict, business: dict, qsettings: dict | None = None, accent: str = "#1F4E79") -> bytes:
    day1_render._register_pdf_fonts()
    s = qsettings or {}
    W, H = A4
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, pageCompression=1, initialFontName="day1-body", initialFontSize=10)
    kind = {"quote": "Quotation", "invoice": "Tax invoice", "credit_note": "Credit note"}.get(q.get("kind"), "Invoice")
    c.setTitle(f"{kind} {q.get('number', '')} — {business.get('name', '')}")
    m = 40
    y = H - m

    def text(x, yy, t, size=10, font="body", color=INK, right=False):
        c.setFont(f"day1-{font}", size)
        c.setFillColor(HexColor(color))
        (c.drawRightString if right else c.drawString)(x, yy, str(t or ""))

    text(m, y - 4, business.get("name", ""), 16, "head_bold")
    sub = [s.get("address") or business.get("city") or "", f"GSTIN {s['gstin']}" if s.get("gstin") else "",
           " · ".join(x for x in (business.get("whatsapp"), business.get("email")) if x)]
    yy = y - 22
    for line in [x for x in sub if x]:
        text(m, yy, line[:90], 9, color=MUTED)
        yy -= 13
    text(W - m, y - 2, kind.upper(), 8, "body_semi", MUTED, right=True)
    text(W - m, y - 20, q.get("number", ""), 15, "head_bold", accent, right=True)
    created = q.get("created_at")
    text(W - m, y - 36, f"Date {created.astimezone(IST).strftime('%d %b %Y')}" if created else "", 9, color=MUTED, right=True)
    if q.get("due_date") and q.get("kind") == "invoice":
        text(W - m, y - 49, f"Due {q['due_date']}", 9, color=MUTED, right=True)
    y = min(yy, y - 56) - 6
    c.setStrokeColor(HexColor(accent))
    c.setLineWidth(2.5)
    c.line(m, y, W - m, y)
    y -= 24
    cust = q.get("customer") or {}
    text(m, y, "BILL TO" if q.get("kind") != "quote" else "FOR", 8, "body_semi", MUTED)
    y -= 15
    text(m, y, cust.get("name", ""), 11, "body_semi")
    for line in [cust.get("address"), f"GSTIN {cust['gstin']}" if cust.get("gstin") else "", cust.get("phone"), cust.get("email")]:
        if line:
            y -= 13
            text(m, y, str(line)[:90], 9, color=MUTED)
    y -= 26
    cols = [(m, "Item", False), (W - m - 250, "Qty", True), (W - m - 175, "Rate", True), (W - m - 95, "GST", True), (W - m, "Amount", True)]
    c.setFillColor(HexColor("#F6F7F9"))
    c.rect(m, y - 6, W - 2 * m, 20, stroke=0, fill=1)
    for x, label, right in cols:
        text(x - (4 if right else -4), y, label.upper(), 8, "body_semi", MUTED, right=right)
    y -= 22
    for it in (q.get("items") or [])[:24]:
        text(m + 4, y, str(it.get("name", ""))[:52], 9.5)
        text(W - m - 254, y, f"{float(it.get('qty') or 0):g} {it.get('unit', '')}".strip(), 9.5, right=True)
        text(W - m - 179, y, _inr(it.get("rate", 0)), 9.5, right=True)
        text(W - m - 99, y, f"{float(it.get('gst') or 0):g}%" if q.get("gst_mode") != "none" else "—", 9.5, right=True)
        text(W - m - 4, y, _inr(it.get("amount", float(it.get("qty") or 0) * float(it.get("rate") or 0))), 9.5, right=True)
        c.setStrokeColor(HexColor(LINE))
        c.setLineWidth(0.6)
        c.line(m, y - 7, W - m, y - 7)
        y -= 20
    t = q.get("totals") or {}
    y -= 6
    rows = [("Subtotal", t.get("subtotal"))]
    if t.get("cgst"):
        rows += [("CGST", t["cgst"]), ("SGST", t.get("sgst"))]
    if t.get("igst"):
        rows += [("IGST", t["igst"])]
    for label, v in rows:
        text(W - m - 150, y, label, 9.5, color=MUTED)
        text(W - m - 4, y, _inr(v or 0), 9.5, right=True)
        y -= 15
    c.setStrokeColor(HexColor(INK))
    c.setLineWidth(1.2)
    c.line(W - m - 150, y + 6, W - m, y + 6)
    y -= 8
    text(W - m - 150, y, "Total", 12, "body_bold")
    text(W - m - 4, y, _inr(t.get("total", 0)), 12, "body_bold", right=True)
    y -= 34
    for label, body in (("How to pay", s.get("payment") or business.get("payment_note")), ("Notes", q.get("notes")), ("Terms", q.get("terms"))):
        if body and y > 90:
            text(m, y, label.upper(), 8, "body_semi", MUTED)
            y -= 13
            for line in str(body).splitlines()[:6]:
                text(m, y, line[:110], 9)
                y -= 12
            y -= 8
    text(m, 30, f"{business.get('name', '')} · {kind} {q.get('number', '')}", 8, color=MUTED)
    c.showPage()
    c.save()
    return buf.getvalue()
