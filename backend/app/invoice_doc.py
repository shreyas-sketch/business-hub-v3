"""
A quotation, tax invoice or credit note as one document, the same on the print page (templates/quote.html) and in the PDF
(invoice_pdf.py): the business's logo and address block, the title and its tag, the reference column (number, date, due
date or validity, the quotation it came from, place of supply), Billed by / Billed to, each line with its SAC or HSN code
and description, the taxable value with CGST + SGST or IGST at their rates, the amount due, the amount in words, bank and
payment details, notes and terms, and the authorised signatory.

build() reads what the document needs (business, Invoicing → Business details, brand colour and logo, signature image,
the owner's state for the place of supply); compose() lays it out from those and is pure, so it can be tested directly.
"""
import re
from datetime import date, datetime, timedelta

from .db import IST, db

KIND = {"quote": "Quotation", "invoice": "Tax Invoice", "credit_note": "Credit Note"}


# ───────────────────────── money and words ─────────────────────────
def inr(x: float, paise: bool = False) -> str:
    """₹1,18,000 — Indian grouping; paise only when there are any (or asked for)."""
    neg = (x or 0) < 0
    whole, frac = f"{abs(x or 0):.2f}".split(".")
    s = whole
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        s = ",".join(([head] if head else []) + groups) + "," + tail
    return ("-" if neg else "") + "₹" + s + (f".{frac}" if paise or frac != "00" else "")


_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve", "Thirteen",
         "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _below_100(n: int) -> str:
    return _ONES[n] if n < 20 else (_TENS[n // 10] + (" " + _ONES[n % 10] if n % 10 else ""))


def _below_1000(n: int) -> str:
    out = []
    if n >= 100:
        out.append(_ONES[n // 100] + " Hundred")
        n %= 100
    if n:
        out.append(_below_100(n))
    return " ".join(out)


def _indian(n: int) -> str:
    if n == 0:
        return "Zero"
    parts = []
    for size, name in ((10_000_000, "Crore"), (100_000, "Lakh"), (1000, "Thousand")):
        if n >= size:
            parts.append(f"{_indian(n // size) if n // size >= 100 and size == 10_000_000 else _below_1000(n // size)} {name}")
            n %= size
    if n:
        parts.append(_below_1000(n))
    return " ".join(parts)


def in_words(amount: float) -> str:
    """₹1,18,000 → "Rupees One Lakh Eighteen Thousand Only"; paise are said too."""
    amount = round(abs(amount or 0), 2)
    rupees, paise = int(amount), int(round((amount - int(amount)) * 100))
    words = f"Rupees {_indian(rupees)}"
    if paise:
        words += f" and {_below_100(paise)} Paise"
    return words + " Only"


def _day(v) -> str:
    if isinstance(v, datetime):
        return v.astimezone(IST).strftime("%-d %B %Y")
    if isinstance(v, date):
        return v.strftime("%-d %B %Y")
    try:
        return date.fromisoformat(str(v)[:10]).strftime("%-d %B %Y")
    except ValueError:
        return str(v or "")


def _state_code(state: str) -> str:
    from .routers.gateway import GST_STATE_CODES
    return next((k for k, v in GST_STATE_CODES.items() if v == state), "")


def _pan(s: dict) -> str:
    """The PAN as typed, else read from the GSTIN (characters 3-12)."""
    if s.get("pan"):
        return s["pan"]
    g = (s.get("gstin") or "").upper()
    return g[2:12] if re.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z][0-9A-Z]Z[0-9A-Z]", g) else ""


# ───────────────────────── the document ─────────────────────────
def compose(q: dict, business: dict, s: dict | None, accent: str = "#2F5BEA", *, logo: bytes | None = None,
            signature: bytes | None = None, owner_state: str = "", from_quote: str = "", pay_link: str = "") -> dict:
    """Everything the layout shows, worked out once. `s`: Invoicing → Business details."""
    from .routers.gateway import canonical_state
    s = s or {}
    b = business or {}
    kind = q.get("kind") or "invoice"
    mode = q.get("gst_mode") or "intra"
    title = KIND.get(kind, "Invoice")
    if kind == "invoice" and mode == "none":
        title = "Invoice"                     # not registered for GST: a bill of supply, not a tax invoice
    tag = {"quote": "Not a tax invoice", "credit_note": f"Against {q['against']}" if q.get("against") else "Credit note"}.get(
        kind, "Original for recipient")
    legal = (s.get("legal_name") or b.get("name") or "").strip()
    brand = (b.get("name") or legal).strip()

    seller = {"name": legal, "brand": brand, "address": (s.get("address") or b.get("city") or "").strip(),
              "phone": (s.get("phone") or b.get("whatsapp") or "").strip(), "email": (s.get("email") or b.get("email") or "").strip(),
              "gstin": (s.get("gstin") or "").strip().upper(), "pan": _pan(s).upper(), "cin": (s.get("cin") or "").strip().upper()}
    cust = q.get("customer") or {}
    buyer = {"name": cust.get("name", ""), "address": cust.get("address", ""), "gstin": (cust.get("gstin") or "").upper(),
             "phone": cust.get("phone", ""), "email": cust.get("email", "")}

    pos = canonical_state(cust.get("state") or "") or canonical_state(buyer["gstin"]) or (owner_state if mode != "inter" else "")
    meta = [(f"{title.split()[-1] if kind != 'credit_note' else 'Credit Note'} No.", q.get("number", "")), ("Date", _day(q.get("created_at")))]
    if kind == "quote" and q.get("created_at"):
        meta.append(("Valid until", _day(q["created_at"] + timedelta(days=int(q.get("valid_days") or 15)))))
    if kind == "invoice":
        if q.get("status") == "paid":
            meta.append(("Status", "Paid"))
        elif q.get("due_date"):
            meta.append(("Due date", _day(q["due_date"])))
        if from_quote:
            meta.append(("Ref. Quotation", from_quote))
    if kind == "credit_note" and q.get("against"):
        meta.append(("Ref. Invoice", q["against"]))
    if pos and mode != "none":
        code = _state_code(pos)
        meta.append(("Place of supply", f"{pos} ({code})" if code else pos))

    lines, rates = [], set()
    for it in q.get("items") or []:
        qty, rate = float(it.get("qty") or 0), float(it.get("rate") or 0)
        taxable = float(it.get("amount", qty * rate) or 0)
        gst = 0.0 if mode == "none" else float(it.get("gst") or 0)
        rates.add(gst)
        tax = round(taxable * gst / 100, 2)
        sub = f"{qty:g} {it.get('unit') or 'nos'} × {inr(rate)}" if qty != 1 else ""
        lines.append({"name": it.get("name", ""), "description": (it.get("description") or "").strip(), "qty_line": sub,
                      "sac": (it.get("sac") or "").strip(), "taxable": taxable, "gst_rate": gst, "gst": tax, "amount": round(taxable + tax, 2)})
    one_rate = next(iter(rates)) if len(rates) == 1 else None
    gst_head = "" if mode == "none" else (f"GST {one_rate:g}%" if one_rate is not None else "GST")

    t = q.get("totals") or {}
    rows = [("Taxable value", t.get("subtotal", 0))]
    if mode == "intra" and (t.get("cgst") or t.get("sgst")):
        half = f" @ {one_rate / 2:g}%" if one_rate else ""
        rows += [(f"CGST{half}", t.get("cgst", 0)), (f"SGST{half}", t.get("sgst", 0))]
    elif mode == "inter" and t.get("igst"):
        rows += [(f"IGST @ {one_rate:g}%" if one_rate else "IGST", t["igst"])]
    total = float(t.get("total") or 0)
    grand = {"quote": "Total", "credit_note": "Credit total"}.get(kind, "Total (paid)" if q.get("status") == "paid" else "Amount due")

    bank = [(k, v) for k, v in (("Account name", s.get("bank_account_name") or (legal if s.get("bank_account") else "")),
                                ("Bank", s.get("bank_name")), ("A/C number", s.get("bank_account")), ("A/C type", s.get("bank_account_type")),
                                ("IFSC", (s.get("bank_ifsc") or "").upper()), ("UPI", s.get("upi"))) if v]
    pay = [x for x in (f"Pay online: {pay_link}" if pay_link else "", (s.get("payment") or b.get("payment_note") or "").strip()) if x]
    return {"kind": kind, "title": title, "tag": tag, "accent": accent, "number": q.get("number", ""), "seller": seller, "buyer": buyer,
            "meta": meta, "lines": lines, "gst_head": gst_head, "show_gst": mode != "none", "show_sac": any(x["sac"] for x in lines),
            "rows": rows, "grand": grand, "total": total, "words": in_words(total), "bank": bank, "pay": pay,
            "notes": (q.get("notes") or "").strip(), "terms": (q.get("terms") or "").strip(),
            "signatory": (s.get("signatory") or "").strip(), "logo": logo, "signature": signature,
            "footer": " · ".join(x for x in (brand.upper(), f"{title.upper()} {q.get('number', '')}".strip(), buyer["name"].upper(),
                                             f"GSTIN {seller['gstin']}" if seller["gstin"] else "") if x)}


def image_type(data: bytes | None) -> str:
    """The picture's type from its first bytes ("" when it isn't one the PDF and the page can show)."""
    if not data:
        return ""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


def data_uri(data: bytes | None) -> str:
    import base64
    kind = image_type(data)
    return f"data:{kind};base64,{base64.b64encode(data).decode()}" if kind else ""


async def _image(file_id: str | None, ws: str) -> bytes | None:
    if not file_id:
        return None
    from . import files
    f = await files.get(file_id, ws)
    return bytes(f["data"]) if f and str(f.get("content_type", "")).startswith("image/") else None


async def build(q: dict) -> dict:
    """compose() with everything it needs read from the database."""
    from .brand import accent_for
    from .razorpay_invoices import pay_url
    from .routers.gateway import _owner_tax
    ws = q["ws"]
    b = await db().businesses.find_one({"owner_id": ws}) or {}
    s = await db().quote_settings.find_one({"_id": ws}) or {}
    kit = await db().brand_kits.find_one({"_id": ws}, {"design.logo_file_id": 1}) or {}
    from_quote = ""
    if q.get("quote_id"):
        src = await db().quotes.find_one({"_id": q["quote_id"], "ws": ws}, {"number": 1})
        from_quote = (src or {}).get("number", "")
    return compose(q, b, s, await accent_for(ws), logo=await _image((kit.get("design") or {}).get("logo_file_id"), ws),
                   signature=await _image(s.get("signature_file_id"), ws), owner_state=(await _owner_tax(ws))["state"],
                   from_quote=from_quote, pay_link=pay_url(q) or "")
