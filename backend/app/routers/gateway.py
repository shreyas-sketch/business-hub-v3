"""
Invoicing (v2.1, 3.5): direct invoices, billing entry, the owner's own payment gateway → automatic invoices and credit
notes, and emailing invoices.

POST /api/invoices                      a direct invoice (no quotation needed); unpaid → also a Money owed entry
POST /api/invoices/draft                billing entry: "Sold 40 boxes to Mehta Traders, ₹850 each, 18% GST, due in 30 days" →
                                        a draft to confirm (one AI run; a rate is only ever what the owner typed)
GET  /api/invoices/settings, PUT        "Email invoices automatically" (off until the owner switches it on)
POST /api/invoices/{id}/email           email one invoice now (the PDF attached)
GET  /api/invoices/{id}/pdf             the invoice or credit note as a PDF
GET  /api/credit-notes                  credit notes (from gateway refunds)
GET  /api/gateway                       the Razorpay connection, its webhook address and what came in
POST /api/hooks/payments/{token}        the owner's Razorpay webhook (signed), see below

A captured payment:
  1. matches an open Money owed entry — by the invoice number in the payment's notes, or by the customer's phone/email and
     the exact amount — and marks it (and its invoice) paid: no second invoice. Each candidate is claimed atomically, so two
     payments arriving together never land on the same due. An invoice-number payment for LESS than what's owed is a part
     payment: the due stays open, its `amount` (paise) drops to what's still owed (`original_amount` keeps the first figure,
     `part_payments` lists each payment, `paid_so_far` adds them up); a due is never closed for less than it's worth;
  2. otherwise becomes exactly one paid invoice (gateway_payments holds one record per payment id): the amount split into
     taxable value + GST at the Knowledge Hub's GST rate (never a rate the payer typed in the notes), CGST+SGST within the
     owner's state, IGST across states. An owner who hasn't filled in their business identity is treated as not
     GST-registered: no GST lines, and the invoice says why.
A refund (refund.processed) becomes one credit note (CN-0001…, unique per refund) against the invoice, with GST in the same
proportion; the last refund takes whatever GST is left. A refund for a payment the hub never invoiced makes no credit note:
it's logged and the owner is told.
Every event is logged in payment_events. Nothing here sends anything on WhatsApp.
"""
import json
import logging
import re
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from pymongo.errors import DuplicateKeyError

from .. import connections, dashboard_cache, invoice_pdf, mailer, plans
from ..agents.jobs import claim
from ..ai.provider import complete
from ..ai.tasks import RULES, profile_text
from ..context import Ctx, feature
from ..db import IST, db, now, public
from ..payments_in import CONNECTORS
from ..payments_in.base import PaymentEvent
from ..services import new_id, run_ai, track
from .desk import Line, _phone, _qview, next_number, today, totals
from .hub import profile_of

router = APIRouter(prefix="/api")
log = logging.getLogger("gateway")
GST_RATES = (0, 0.25, 3, 5, 12, 18, 28)
# the first two digits of a GSTIN are the state
GST_STATE_CODES = {"01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh", "05": "Uttarakhand", "06": "Haryana",
                   "07": "Delhi", "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh", "13": "Nagaland",
                   "14": "Manipur", "15": "Mizoram", "16": "Tripura", "17": "Meghalaya", "18": "Assam", "19": "West Bengal", "20": "Jharkhand",
                   "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat", "25": "Dadra and Nagar Haveli and Daman and Diu",
                   "26": "Dadra and Nagar Haveli and Daman and Diu", "27": "Maharashtra", "29": "Karnataka", "30": "Goa", "31": "Lakshadweep",
                   "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry", "35": "Andaman and Nicobar Islands", "36": "Telangana",
                   "37": "Andhra Pradesh", "38": "Ladakh"}
INVOICE_NOTE_KEYS = ("invoice_no", "invoice_number", "invoice", "inv", "invoice no", "bill_no", "bill")
NO_IDENTITY = "No GST shown: your business identity (GST registration and state) isn't filled in the Knowledge Hub yet."
# vehicle / short codes and old names → the one state name used everywhere (app/knowledge.py STATES)
STATE_SHORT = {"AN": "Andaman and Nicobar Islands", "AP": "Andhra Pradesh", "AR": "Arunachal Pradesh", "AS": "Assam", "BR": "Bihar",
               "CH": "Chandigarh", "CG": "Chhattisgarh", "CT": "Chhattisgarh", "DN": "Dadra and Nagar Haveli and Daman and Diu",
               "DD": "Dadra and Nagar Haveli and Daman and Diu", "DH": "Dadra and Nagar Haveli and Daman and Diu", "DL": "Delhi", "GA": "Goa",
               "GJ": "Gujarat", "HR": "Haryana", "HP": "Himachal Pradesh", "JK": "Jammu and Kashmir", "JH": "Jharkhand", "KA": "Karnataka",
               "KL": "Kerala", "LA": "Ladakh", "LD": "Lakshadweep", "MP": "Madhya Pradesh", "MH": "Maharashtra", "MN": "Manipur", "ML": "Meghalaya",
               "MZ": "Mizoram", "NL": "Nagaland", "OD": "Odisha", "OR": "Odisha", "PY": "Puducherry", "PB": "Punjab", "RJ": "Rajasthan",
               "SK": "Sikkim", "TN": "Tamil Nadu", "TS": "Telangana", "TG": "Telangana", "TR": "Tripura", "UP": "Uttar Pradesh",
               "UK": "Uttarakhand", "UT": "Uttarakhand", "WB": "West Bengal"}
STATE_OLD = {"orissa": "Odisha", "pondicherry": "Puducherry", "uttaranchal": "Uttarakhand", "newdelhi": "Delhi", "nctofdelhi": "Delhi",
             "jk": "Jammu and Kashmir", "jandk": "Jammu and Kashmir", "damananddiu": "Dadra and Nagar Haveli and Daman and Diu",
             "dadraandnagarhaveli": "Dadra and Nagar Haveli and Daman and Diu", "andamanandnicobar": "Andaman and Nicobar Islands",
             "andaman": "Andaman and Nicobar Islands", "chattisgarh": "Chhattisgarh"}
GSTIN = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][0-9A-Z]Z[0-9A-Z]$")


def _norm_state(s: str) -> str:
    return re.sub(r"[^a-z]", "", (s or "").lower().replace("&", "and"))


def canonical_state(value: str) -> str:
    """'27', 'MH', 'maharashtra', '27ABCDE1234F1Z5' → 'Maharashtra'. Unknown → ''."""
    from ..knowledge import STATES
    v = (value or "").strip()
    if not v:
        return ""
    if re.fullmatch(r"\d{1,2}", v):
        return GST_STATE_CODES.get(v.zfill(2), "")
    if GSTIN.match(v.upper()):
        return GST_STATE_CODES.get(v[:2], "")
    if re.fullmatch(r"[A-Za-z]{2}", v):
        return STATE_SHORT.get(v.upper(), "")
    n = _norm_state(v)
    return next((st for st in STATES if _norm_state(st) == n), "") or STATE_OLD.get(n, "")


def state_of_gstin(gstin: str) -> str:
    return GST_STATE_CODES.get((gstin or "").strip()[:2], "") if re.match(r"^\d{2}", (gstin or "").strip()) else ""


async def _owner_tax(ws: str) -> dict:
    """The owner's GST position from the Knowledge Hub. Without a business identity there, the hub doesn't assume GST
    registration: invoices carry no GST lines (and say why) until the owner fills it in."""
    b = await db().businesses.find_one({"owner_id": ws}, {"identity": 1, "money": 1}) or {}
    ident = b.get("identity") or {}
    rate = (b.get("money") or {}).get("gst_rate")
    return {"registered": bool(ident.get("gst_registered")), "identity": bool(ident),
            "state": canonical_state(ident.get("state") or "") or canonical_state(ident.get("gstin") or ""),
            "rate": float(rate) if rate is not None and float(rate) in GST_RATES else 18.0}


def gst_mode_for(owner_tax: dict, customer_state: str = "", customer_gstin: str = "") -> str:
    """CGST+SGST inside the owner's state, IGST across states; no GST when the owner isn't registered (or hasn't said)."""
    if not owner_tax["registered"]:
        return "none"
    theirs = canonical_state(customer_state) or canonical_state(customer_gstin)
    if theirs and owner_tax["state"] and theirs != owner_tax["state"]:
        return "inter"
    return "intra"


def _halves(gst: float) -> tuple[float, float]:
    """CGST and SGST are each exactly half: half the tax to the paisa, doubled."""
    half = round(gst / 2, 2)
    return half, round(half * 2, 2)


def even_split(tot: dict, mode: str) -> dict:
    """Totals with CGST = SGST exactly (intra-state); the total is kept, any paisa goes to the taxable value."""
    out = dict(tot)
    if mode == "intra" and out.get("gst"):
        half, gst = _halves(out["gst"])
        out.update(gst=gst, cgst=half, sgst=half, igst=0, subtotal=round(out["total"] - gst, 2))
    return out


def split_inclusive(amount: float, rate: float, mode: str) -> dict:
    """A GST-inclusive amount (what the customer paid) → taxable value + GST that add up to the paisa; CGST = SGST exactly."""
    amount = round(amount, 2)
    if mode == "none" or not rate:
        return {"subtotal": amount, "gst": 0.0, "total": amount, "cgst": 0, "sgst": 0, "igst": 0}
    gst = round(amount - amount / (1 + rate / 100), 2)
    if mode == "inter":
        return {"subtotal": round(amount - gst, 2), "gst": gst, "total": amount, "cgst": 0, "sgst": 0, "igst": gst}
    half, gst = _halves(gst)
    return {"subtotal": round(amount - gst, 2), "gst": gst, "total": amount, "cgst": half, "sgst": half, "igst": 0}


# ───────────────────────── creating invoices ─────────────────────────
async def issue_invoice(owner: dict, ws: str, actor: str, customer: dict, items: list[dict], tot: dict, gst_mode: str, *, due_date: str | None,
                        paid: bool, extra: dict | None = None) -> dict:
    """One numbered invoice; unpaid ones also go into Money owed (linked both ways) so follow-ups and reminders can chase them."""
    inv_id = new_id()
    due_id = None if paid else new_id()
    number = await next_number(ws, "invoice")
    s = await db().quote_settings.find_one({"_id": ws}) or {}
    inv = {"_id": inv_id, "ws": ws, "kind": "invoice", "number": number, "customer": customer, "items": items, "gst_mode": gst_mode, "totals": tot,
           "status": "paid" if paid else "due", "paid_at": now() if paid else None, "due_date": due_date, "due_id": due_id, "notes": "",
           "terms": s.get("terms", ""), "created_by": actor, "created_at": now(), "updated_at": now(), **(extra or {})}
    await db().quotes.insert_one(inv)
    if due_id and plans.has(owner, "money_owed"):
        await db().dues.insert_one({"_id": due_id, "ws": ws, "customer": (customer.get("name") or "")[:80], "phone": customer.get("phone", ""),
                                    "email": (customer.get("email") or "").strip().lower(), "amount": int(round(tot["total"] * 100)),
                                    "due_date": due_date, "note": f"Invoice {number}", "invoice_no": number, "invoice_id": inv_id, "status": "due",
                                    "paid_at": None, "reminders_sent": 0, "last_reminded_at": None, "created_by": actor, "created_at": now(),
                                    "updated_at": now()})
    elif due_id:
        await db().quotes.update_one({"_id": inv_id}, {"$set": {"due_id": None}})
        inv["due_id"] = None
    dashboard_cache.bust(ws)
    await track("invoice", ws, source=(extra or {}).get("source", "direct"))
    await maybe_email(owner, inv)
    return inv


async def email_settings(ws: str) -> dict:
    return await db().invoice_settings.find_one({"_id": ws}) or {"_id": ws, "email_auto": False}


async def send_invoice_email(owner: dict, inv: dict) -> dict:
    """The invoice PDF to the customer's email. An invoice, never a reminder: no chasing words, no payment link added."""
    ws = owner["_id"]
    to = ((inv.get("customer") or {}).get("email") or "").strip()
    if not mailer.valid(to):
        return {"sent": False, "via": "no-email", "error": "The customer has no email address."}
    if not await claim(f"invoice_email:{inv['_id']}"):
        return {"sent": False, "via": "already", "error": "This invoice was already emailed."}
    b = await db().businesses.find_one({"owner_id": ws}) or {}
    qs = await db().quote_settings.find_one({"_id": ws}) or {}
    from ..brand import accent_for
    doc = {"credit_note": "Credit note"}.get(inv.get("kind"), "Invoice")
    name = (inv.get("customer") or {}).get("name") or ""
    t = inv.get("totals") or {}
    paid = inv.get("status") == "paid"
    text = (f"Hello{(' ' + name.split()[0]) if name else ''},\n\nPlease find attached {doc.lower()} {inv['number']} from {b.get('name') or 'us'}"
            f" for ₹{t.get('total', 0):,.2f}.{' It is marked paid — thank you.' if paid and doc == 'Invoice' else ''}"
            f"{(' Due by ' + inv['due_date'] + '.') if inv.get('due_date') and not paid and doc == 'Invoice' else ''}\n\nThank you,\n{b.get('name') or ''}")
    try:
        pdf = invoice_pdf.render(inv, b, qs, await accent_for(ws))
    except Exception as e:  # noqa: BLE001
        log.warning("invoice PDF failed: %s", e)
        from ..agents.jobs import release
        await release(f"invoice_email:{inv['_id']}")
        return {"sent": False, "via": "pdf", "error": "The PDF couldn't be made just now."}
    r = await mailer.send(owner=owner, to=to, subject=f"{doc} {inv['number']} from {b.get('name') or 'us'}", text=text, kind="invoice",
                          from_name=b.get("name") or "Accounts", reply_to=b.get("email"),
                          attachments=[(f"{inv['number']}.pdf", pdf, "application/pdf")], ref={"invoice_id": inv["_id"]})
    await db().quotes.update_one({"_id": inv["_id"]}, {"$set": {"email": {"sent": r["sent"], "via": r["via"], "at": now(), "to": to.lower(),
                                                                         "error": r.get("error")}}})
    if not r["sent"]:
        from ..agents.jobs import release
        await release(f"invoice_email:{inv['_id']}")
    return r


async def maybe_email(owner: dict, inv: dict) -> dict | None:
    """Only when the owner switched "Email invoices automatically" on, and the customer has an email."""
    if not (await email_settings(owner["_id"])).get("email_auto") or not mailer.valid(((inv.get("customer") or {}).get("email") or "")):
        return None
    try:
        return await send_invoice_email(owner, inv)
    except Exception:  # noqa: BLE001  an email problem never undoes the invoice
        log.exception("emailing invoice %s failed", inv.get("number"))
        return None


# ───────────────────────── direct invoice + billing entry ─────────────────────────
class ICustomer(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    phone: str = Field(default="", max_length=24)
    email: str = Field(default="", max_length=254)
    gstin: str = Field(default="", max_length=15)
    address: str = Field(default="", max_length=300)
    state: str = Field(default="", max_length=60)

    @field_validator("email")
    @classmethod
    def ok_email(cls, v):
        v = (v or "").strip().lower()
        if v and not mailer.valid(v):
            raise ValueError("That email doesn't look right")
        return v


class DirectIn(BaseModel):
    customer: ICustomer
    items: list[Line] = Field(min_length=1, max_length=60)
    gst_mode: str | None = Field(default=None, pattern="^(intra|inter|none)$")   # None: worked out from the states
    due_date: str | None = Field(default=None, max_length=10)
    due_in_days: int = Field(default=7, ge=0, le=365)
    paid_already: bool = False
    notes: str = Field(default="", max_length=1000)
    lead_id: str | None = Field(default=None, max_length=40)

    @field_validator("due_date")
    @classmethod
    def ok_day(cls, v):
        if v:
            date.fromisoformat(v)
        return v or None


@router.post("/invoices")
async def direct_invoice(body: DirectIn, ctx: Ctx = Depends(feature("quotations", "manager"))):
    cust = body.customer.model_dump()
    cust["phone"] = _phone(cust["phone"]) or cust["phone"].strip()
    tax = await _owner_tax(ctx.ws)
    mode = body.gst_mode or gst_mode_for(tax, cust.get("state", ""), cust.get("gstin", ""))
    if cust.get("state"):
        cust["state"] = canonical_state(cust["state"]) or cust["state"]
    items = [i.model_dump() for i in body.items]
    tot = even_split(totals(items, mode), mode)
    due_date = body.due_date or (today() + timedelta(days=body.due_in_days)).isoformat()
    extra = {"source": "direct", "notes": body.notes.strip(), "lead_id": body.lead_id}
    if not body.gst_mode and not tax["identity"]:
        extra["gst_note"] = NO_IDENTITY
    inv = await issue_invoice(ctx.owner, ctx.ws, ctx.actor, cust, items, tot, mode, due_date=None if body.paid_already else due_date,
                              paid=body.paid_already, extra=extra)
    return await _qview(await db().quotes.find_one({"_id": inv["_id"]}))


class EntryIn(BaseModel):
    text: str = Field(min_length=5, max_length=2000)


def _numbers_in(text: str) -> list[float]:
    """Every number the owner wrote (₹12,50,000 → 1250000.0), compared as numbers — never as formatted text."""
    out = []
    for raw in re.findall(r"\d[\d,]*(?:\.\d+)?", text or ""):
        try:
            out.append(float(raw.replace(",", "")))
        except ValueError:
            pass
    return out


def _said(x: float, said: list[float]) -> bool:
    return any(abs(x - n) < 0.005 for n in said)


def parse_entry(text: str, default_rate: float) -> dict:
    """The words-only parse used by the mock provider (and as a check): quantity, item, customer, rate, GST, due days."""
    t = " ".join(text.split())
    m = re.search(r"(?i)(?:sold|supplied|delivered|billed)?\s*(\d+(?:\.\d+)?)\s+([a-z][\w\- ]*?)\s+(?:to|for)\s+([A-Z][\w&.' ]+?)(?=,|\s+@|\s+at\b|$)", t)
    qty, item, cust = (float(m.group(1)), m.group(2).strip(), m.group(3).strip()) if m else (1.0, "", "")
    rate_m = re.search(r"(?i)(?:₹|\brs\.?|\binr)\s?(\d[\d,]*(?:\.\d+)?)", t)
    gst_m = re.search(r"(?i)(\d{1,2}(?:\.\d+)?)\s?%\s?(?:gst|igst|tax)|gst\s?@?\s?(\d{1,2}(?:\.\d+)?)\s?%", t)
    due_m = re.search(r"(?i)due\s+(?:in\s+)?(\d{1,3})\s+days?", t)
    gst = float(gst_m.group(1) or gst_m.group(2)) if gst_m else default_rate
    return {"customer": {"name": cust[:120]}, "items": [{"name": (item or "Item")[:160], "qty": qty, "unit": "nos",
                                                         "rate": float(rate_m.group(1).replace(",", "")) if rate_m else 0.0,
                                                         "gst": gst if gst in GST_RATES else default_rate}],
            "due_in_days": int(due_m.group(1)) if due_m else 7, "paid_already": bool(re.search(r"(?i)\b(paid|received)\b", t))}


async def entry_task(p: dict, text: str, default_rate: float):
    def mock():
        return parse_entry(text, default_rate)
    return await complete(RULES, f"""{profile_text(p)}

The owner typed or spoke a sale to bill: "{text}"
Turn it into invoice lines. Use ONLY numbers the owner said: never invent a rate, quantity or GST rate (leave rate 0 if not said).
JSON only: {{"customer": {{"name": "", "phone": "", "email": "", "gstin": "", "state": ""}},
 "items": [{{"name": "", "qty": 1, "unit": "nos", "rate": 0, "gst": {default_rate:g}}}], "due_in_days": 7, "paid_already": false}}""", mock, 1200)


@router.post("/invoices/draft")
async def billing_entry(body: EntryIn, ctx: Ctx = Depends(feature("quotations", "manager"))):
    """A draft to confirm on screen — nothing is saved. A rate the owner didn't say comes back as 0 ("add the rate")."""
    p = await profile_of(ctx.ws)
    tax = await _owner_tax(ctx.ws)
    said = _numbers_in(body.text)

    def shape(r):
        if not isinstance(r, dict):
            raise ValueError("not an object")
        items = []
        for i in (r.get("items") or [])[:60]:
            if not isinstance(i, dict) or not str(i.get("name") or "").strip():
                continue

            def num(v, d=0.0):
                try:
                    return max(0.0, float(str(v).replace(",", "")))
                except (TypeError, ValueError):
                    return d
            rate, qty = num(i.get("rate")), num(i.get("qty"), 1) or 1
            if rate and not _said(rate, said):
                rate = 0.0      # a rate the owner never said is never used
            if not _said(qty, said):
                qty = 1.0
            gst = num(i.get("gst"), tax["rate"])
            items.append({"name": str(i["name"])[:160], "qty": qty, "unit": str(i.get("unit") or "nos")[:20], "rate": rate,
                          "gst": gst if gst in GST_RATES else tax["rate"]})
        if not items:
            raise ValueError("no items")
        c = r.get("customer") if isinstance(r.get("customer"), dict) else {}
        try:
            days = max(0, min(int(r.get("due_in_days") or 7), 365))
        except (TypeError, ValueError):
            days = 7
        return {"customer": {k: str(c.get(k) or "")[:120] for k in ("name", "phone", "email", "gstin", "state")}, "items": items,
                "due_in_days": days, "paid_already": bool(r.get("paid_already"))}
    out = await run_ai(ctx.owner, "invoice_entry", entry_task(p, body.text, tax["rate"]), f"Billing entry: {body.text[:60]}", save_output=False, check=shape)
    out["gst_mode"] = gst_mode_for(tax, out["customer"].get("state", ""), out["customer"].get("gstin", ""))
    out["missing_rates"] = sum(1 for i in out["items"] if not i["rate"])
    out["totals"] = even_split(totals([dict(i) for i in out["items"]], out["gst_mode"]), out["gst_mode"])
    if not tax["identity"]:
        out["gst_note"] = NO_IDENTITY
    return out


# ───────────────────────── settings, email, PDF, credit notes ─────────────────────────
class SettingsIn(BaseModel):
    email_auto: bool


@router.get("/invoices/settings")
async def get_settings(ctx: Ctx = Depends(feature("quotations", "manager"))):
    from ..knowledge import STATES
    s = await email_settings(ctx.ws)
    tax = await _owner_tax(ctx.ws)
    return {"email_auto": bool(s.get("email_auto")), "email_ready": mailer.configured() or mailer.settings.local_dev, "states": STATES,
            "my_state": tax["state"], "gst_registered": tax["registered"], "identity": tax["identity"]}


@router.put("/invoices/settings")
async def save_settings(body: SettingsIn, ctx: Ctx = Depends(feature("quotations", "owner"))):
    await db().invoice_settings.update_one({"_id": ctx.ws}, {"$set": {"email_auto": body.email_auto, "updated_at": now(), "by": ctx.actor}}, upsert=True)
    await track("invoice_email_auto", ctx.ws, on=body.email_auto)
    return await get_settings(ctx)


async def _doc(ws: str, inv_id: str) -> dict:
    inv = await db().quotes.find_one({"_id": inv_id, "ws": ws, "kind": {"$in": ["invoice", "credit_note"]}})
    if not inv:
        raise HTTPException(404, "Not found")
    return inv


@router.post("/invoices/{inv_id}/email")
async def email_now(inv_id: str, ctx: Ctx = Depends(feature("quotations", "manager"))):
    r = await send_invoice_email(ctx.owner, await _doc(ctx.ws, inv_id))
    if not r["sent"]:
        raise HTTPException(400, r.get("error") or "Not sent.")
    return {"sent": True}


@router.get("/invoices/{inv_id}/pdf")
async def invoice_file(inv_id: str, ctx: Ctx = Depends(feature("quotations", "manager"))):
    inv = await _doc(ctx.ws, inv_id)
    b = await db().businesses.find_one({"owner_id": ctx.ws}) or {}
    qs = await db().quote_settings.find_one({"_id": ctx.ws}) or {}
    from ..brand import accent_for
    return Response(invoice_pdf.render(inv, b, qs, await accent_for(ctx.ws)), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{inv["number"]}.pdf"'})


@router.get("/credit-notes")
async def credit_notes(ctx: Ctx = Depends(feature("quotations", "manager"))):
    return [await _qview(q) async for q in db().quotes.find({"ws": ctx.ws, "kind": "credit_note"}).sort("created_at", -1).limit(200)]


@router.get("/gateway")
async def gateway_status(ctx: Ctx = Depends(feature("gateway_invoicing", "owner"))):
    stored = ((await db().connections.find_one({"_id": ctx.ws})) or {}).get("razorpay")
    since = now() - timedelta(days=30)
    events = [{"kind": e["kind"], "amount": e.get("amount"), "result": e.get("result"), "number": e.get("number"), "at": e["at"].isoformat()}
              async for e in db().payment_events.find({"ws": ctx.ws}).sort("at", -1).limit(10)]
    return {"connection": connections.view("razorpay", stored, ctx.owner),
            "invoices_30d": await db().quotes.count_documents({"ws": ctx.ws, "kind": "invoice", "source": "gateway", "created_at": {"$gte": since}}),
            "matched_30d": await db().dues.count_documents({"ws": ctx.ws, "gateway_payment_id": {"$exists": True}, "paid_at": {"$gte": since}}),
            "credit_notes_30d": await db().quotes.count_documents({"ws": ctx.ws, "kind": "credit_note", "created_at": {"$gte": since}}),
            "events": events, **(await get_settings(ctx))}


# ───────────────────────── the webhook ─────────────────────────
async def _log(ws: str, ev: PaymentEvent, result: str, **more) -> None:
    await db().payment_events.insert_one({"_id": new_id(), "ws": ws, "provider": "razorpay", "kind": ev.kind, "payment_id": ev.payment_id,
                                          "refund_id": ev.refund_id, "amount": ev.amount, "result": result, "at": now(), **more})


def _invoice_ref(notes: dict) -> str:
    low = {k.lower().strip(): v for k, v in (notes or {}).items()}
    for k in INVOICE_NOTE_KEYS:
        if low.get(k):
            return str(low[k]).strip()[:40]
    return ""


async def _after_paid(owner: dict, due: dict, ev: PaymentEvent) -> None:
    """The same as marking it paid in Money owed: reminders expire, follow-ups stop, the invoice is paid, the dashboard updates."""
    ws = owner["_id"]
    from ..agents.approvals import expire
    from .. import followups
    await expire({"ws": ws, "kind": "reminder", "due_id": due["_id"]}, "Paid through your payment gateway")
    await followups.stop(ws, due["_id"], "paid")
    if due.get("invoice_id"):
        await db().quotes.update_one({"_id": due["invoice_id"], "ws": ws}, {"$set": {"status": "paid", "paid_at": ev.at or now(),
                                                                                      "paid_via": "gateway", "paid_payment_id": ev.payment_id}})
    dashboard_cache.bust(ws)
    await track("due_paid", ws, via="gateway")


async def _close(owner: dict, due: dict, ev: PaymentEvent, paise: int) -> bool:
    """Claims this due for this payment — atomically: only if it's still open and still owes exactly what we read."""
    claimed = await db().dues.find_one_and_update(
        {"_id": due["_id"], "ws": owner["_id"], "status": "due", "amount": due["amount"]},
        {"$set": {"status": "paid", "paid_at": ev.at or now(), "paid_by": "gateway", "gateway_payment_id": ev.payment_id, "updated_at": now(),
                  **({"overpaid": paise - due["amount"]} if paise > due["amount"] else {})}})
    if claimed:
        await _after_paid(owner, claimed, ev)
    return bool(claimed)


async def _part(owner: dict, due: dict, ev: PaymentEvent, paise: int) -> bool:
    """Less than what's owed: a part payment. The due stays open and owes the rest (amount, paise); never closed for less."""
    ws = owner["_id"]
    left = due["amount"] - paise
    r = await db().dues.update_one(
        {"_id": due["_id"], "ws": ws, "status": "due", "amount": due["amount"]},
        {"$set": {"amount": left, "original_amount": due.get("original_amount") or due["amount"], "updated_at": now()},
         "$inc": {"paid_so_far": paise},
         "$push": {"part_payments": {"payment_id": ev.payment_id, "amount": paise, "at": ev.at or now(), "via": "gateway"}}})
    if not r.modified_count:
        return False
    from ..agents.approvals import expire
    await expire({"ws": ws, "kind": "reminder", "due_id": due["_id"]}, "Part payment received — the next reminder asks for the rest")
    if due.get("invoice_id"):
        await db().quotes.update_one({"_id": due["invoice_id"], "ws": ws}, {"$inc": {"part_paid": round(paise / 100, 2)}, "$set": {"updated_at": now()}})
    dashboard_cache.bust(ws)
    await track("due_part_paid", ws, via="gateway")
    return True


async def settle_with_dues(owner: dict, ev: PaymentEvent) -> dict | None:
    """An open due this payment settles, claimed atomically; the next candidate when another payment got there first.
    By invoice number (any amount: less than owed is a part payment), else same customer + exactly the amount owed."""
    ws = owner["_id"]
    paise = int(round(ev.amount * 100))
    ref = _invoice_ref(ev.notes)
    if ref:
        for _ in range(5):   # a part payment racing another one re-reads what's owed and tries again
            due = await db().dues.find_one({"ws": ws, "status": "due", "invoice_no": {"$regex": f"^{re.escape(ref)}$", "$options": "i"}})
            if not due:
                break
            if paise >= due["amount"]:
                if await _close(owner, due, ev, paise):
                    return {"result": "due_paid", "number": due.get("invoice_no") or "", "due_id": due["_id"],
                            **({"overpaid": round((paise - due["amount"]) / 100, 2)} if paise > due["amount"] else {})}
            elif await _part(owner, due, ev, paise):
                return {"result": "part_paid", "number": due.get("invoice_no") or "", "due_id": due["_id"],
                        "still_owed": round((due["amount"] - paise) / 100, 2)}
    digits = re.sub(r"\D", "", ev.contact or "")[-10:]
    if len(digits) < 10 and not ev.email:
        return None
    # same customer (phone by its last 10 digits, however it was typed, or email) and exactly this amount; oldest first
    async for due in db().dues.find({"ws": ws, "status": "due", "amount": paise}).sort("due_date", 1).limit(200):
        same = (len(digits) == 10 and re.sub(r"\D", "", due.get("phone") or "")[-10:] == digits) or (ev.email and (due.get("email") or "").lower() == ev.email)
        if same and await _close(owner, due, ev, paise):
            return {"result": "due_paid", "number": due.get("invoice_no") or "", "due_id": due["_id"]}
    return None


async def _customer(ws: str, ev: PaymentEvent) -> dict:
    phone = _phone(ev.contact)
    known = None
    if phone:
        known = await db().customers.find_one({"ws": ws, "phone": phone})
    if not known and ev.email:
        known = await db().customers.find_one({"ws": ws, "email": ev.email})
    notes = {k.lower(): v for k, v in (ev.notes or {}).items()}
    name = (known or {}).get("name") or ev.name or notes.get("name") or notes.get("customer") or ev.email or phone or "Customer"
    state = canonical_state(notes.get("state") or notes.get("place_of_supply") or "")
    return {"name": str(name)[:120], "phone": phone or (known or {}).get("phone", ""), "email": ev.email or (known or {}).get("email", ""),
            "gstin": notes.get("gstin") or (known or {}).get("gstin", ""), "address": (known or {}).get("address", ""),
            "state": state, **({"customer_id": known["_id"]} if known else {})}


async def payer_customer(ws: str, ev: PaymentEvent, name: str = "") -> str | None:
    """Everyone who pays through the gateway is on the Customers list: matched by phone, else by email, else added. Each
    payment is counted once (a webhook delivered twice doesn't double their purchases)."""
    phone = _phone(ev.contact)
    email = (ev.email or "").strip().lower()
    if not phone and not email:
        return None          # nothing to recognise them by next time: not listed as an anonymous "Customer"
    known = await db().customers.find_one({"ws": ws, "phone": phone}) if phone else None
    if not known and email:
        known = await db().customers.find_one({"ws": ws, "email": email})
    rupees = round(float(ev.amount or 0), 2)
    day = today().isoformat()
    if known:
        fill = {**({"email": email} if email and not known.get("email") else {}), **({"phone": phone} if phone and not known.get("phone") else {})}
        await db().customers.update_one({"_id": known["_id"], "payment_ids": {"$ne": ev.payment_id}},
                                        {"$set": {"last_purchase": day, "updated_at": now(), **fill},
                                         "$inc": {"purchases": 1, "total_value": rupees}, "$addToSet": {"payment_ids": ev.payment_id}})
        cid = known["_id"]
    else:
        notes = {k.lower(): v for k, v in (ev.notes or {}).items()}
        label = (name or ev.name or notes.get("name") or notes.get("customer") or "").strip() or (email.split("@")[0].replace(".", " ").title() if email else phone)
        cid = new_id()
        await db().customers.insert_one({"_id": cid, "ws": ws, "name": str(label)[:80], "phone": phone, "email": email, "tier": "",
                                         "total_value": rupees, "purchases": 1, "first_purchase": day, "last_purchase": day, "last_item": "",
                                         "reorder_days": None, "occasion": None, "source": "razorpay", "payment_ids": [ev.payment_id], "notes": "",
                                         "created_at": now(), "updated_at": now()})
    from .desk import retier
    await retier(ws)
    return cid


async def _payer_customer_safely(ws: str, ev: PaymentEvent, name: str = "") -> None:
    try:
        await payer_customer(ws, ev, name)
    except Exception:  # noqa: BLE001  the invoice or the paid due matters more than the list
        log.exception("could not add the payer of %s to Customers", ev.payment_id)


async def captured(owner: dict, ev: PaymentEvent) -> dict:
    ws = owner["_id"]
    existing = await db().quotes.find_one({"ws": ws, "gateway_payment_id": ev.payment_id}, {"number": 1})
    if existing:
        return {"result": "already_invoiced", "number": existing["number"]}
    if await db().dues.find_one({"ws": ws, "$or": [{"gateway_payment_id": ev.payment_id}, {"part_payments.payment_id": ev.payment_id}]}, {"_id": 1}):
        return {"result": "already_matched"}
    settled = await settle_with_dues(owner, ev)
    if settled:
        due = await db().dues.find_one({"_id": settled.get("due_id")}, {"customer": 1}) if settled.get("due_id") else None
        await _payer_customer_safely(ws, ev, (due or {}).get("customer", ""))
        return settled
    tax = await _owner_tax(ws)
    cust = await _customer(ws, ev)
    notes = {k.lower(): v for k, v in (ev.notes or {}).items()}
    rate = tax["rate"]      # the Knowledge Hub's rate only — never a rate the payer typed into the payment notes
    mode = gst_mode_for(tax, cust.get("state", ""), cust.get("gstin", ""))
    tot = split_inclusive(ev.amount, rate, mode)
    items = [{"name": (notes.get("description") or notes.get("item") or "Payment received")[:160], "qty": 1, "unit": "nos",
              "rate": tot["subtotal"], "gst": rate if mode != "none" else 0, "amount": tot["subtotal"]}]
    # one record per payment (its _id is the payment), taken before the invoice is made: a second delivery can't make another
    rec = f"{ws}:pay:{ev.payment_id}"
    try:
        await db().gateway_payments.insert_one({"_id": rec, "ws": ws, "payment_id": ev.payment_id, "at": now()})
    except DuplicateKeyError:
        existing = await db().quotes.find_one({"ws": ws, "gateway_payment_id": ev.payment_id}, {"number": 1})
        return {"result": "already_invoiced", "number": (existing or {}).get("number", "")}
    extra = {"source": "gateway", "gateway": "razorpay", "gateway_payment_id": ev.payment_id, "gateway_order_id": ev.order_id or None,
             "paid_at": ev.at or now(), **({"gst_note": NO_IDENTITY} if not tax["identity"] else {})}
    try:
        inv = await issue_invoice(owner, ws, "gateway", cust, items, tot, mode, due_date=None, paid=True, extra=extra)
    except Exception:
        await db().gateway_payments.delete_one({"_id": rec})   # not made: a retry may make it
        raise
    await db().gateway_payments.update_one({"_id": rec}, {"$set": {"invoice_id": inv["_id"]}})
    await _payer_customer_safely(ws, ev, cust.get("name", "") if cust.get("name") not in (ev.email, cust.get("phone"), "Customer") else "")
    return {"result": "invoiced", "number": inv["number"], "invoice_id": inv["_id"]}


def _credit_totals(inv: dict, amount: float) -> dict:
    """GST in the invoice's proportion; the refund that completes the invoice takes whatever GST is left, so credit notes
    never add up to more GST than the invoice had."""
    t = inv.get("totals") or {}
    mode = inv.get("gst_mode") or "intra"
    inv_total, inv_gst = float(t.get("total") or 0), float(t.get("gst") or 0)
    gst_left = round(inv_gst - float(inv.get("refunded_gst") or 0), 2)
    refunded_after = round(float(inv.get("refunded") or 0) + amount, 2)
    if inv_total and refunded_after >= inv_total - 0.005:
        gst = max(gst_left, 0.0)
    else:
        gst = min(round(amount * (inv_gst / inv_total), 2) if inv_total else 0.0, max(gst_left, 0.0))
    out = {"subtotal": round(amount - gst, 2), "gst": gst, "total": round(amount, 2), "cgst": 0, "sgst": 0, "igst": 0}
    if mode == "inter":
        out["igst"] = gst
    elif mode == "intra" and gst:
        half = round(gst / 2, 2)
        if round(half * 2, 2) != gst and refunded_after < inv_total - 0.005:
            half, gst = _halves(gst)      # not the last one: keep the halves exact, the paisa stays with the taxable value
            out.update(gst=gst, subtotal=round(amount - gst, 2))
        out.update(cgst=half, sgst=round(gst - half, 2))
    return out


async def refunded(owner: dict, ev: PaymentEvent) -> dict:
    ws = owner["_id"]
    existing = await db().quotes.find_one({"ws": ws, "gateway_refund_id": ev.refund_id}, {"number": 1})
    if existing:
        return {"result": "already_credited", "number": existing["number"]}
    inv = await db().quotes.find_one({"ws": ws, "kind": "invoice", "$or": [{"gateway_payment_id": ev.payment_id}, {"paid_payment_id": ev.payment_id}]})
    if not inv:   # a part payment on an invoiced due
        due = await db().dues.find_one({"ws": ws, "part_payments.payment_id": ev.payment_id, "invoice_id": {"$ne": None}})
        if due:
            inv = await db().quotes.find_one({"_id": due["invoice_id"], "ws": ws})
    if not inv:
        # a payment the hub never invoiced: no credit note for an unknown "Customer" — the owner decides
        from ..services import notify
        await notify(ws, f"Razorpay refunded ₹{ev.amount:,.2f} (payment {ev.payment_id}), but the hub has no invoice for that payment, "
                         "so no credit note was made. If it was one of your sales, make the credit note yourself.")
        return {"result": "refund_unknown_payment"}
    rec = f"{ws}:refund:{ev.refund_id}"
    try:
        await db().gateway_payments.insert_one({"_id": rec, "ws": ws, "refund_id": ev.refund_id, "payment_id": ev.payment_id, "at": now()})
    except DuplicateKeyError:
        existing = await db().quotes.find_one({"ws": ws, "gateway_refund_id": ev.refund_id}, {"number": 1})
        return {"result": "already_credited", "number": (existing or {}).get("number", "")}
    tot = _credit_totals(inv, round(ev.amount, 2))
    mode = inv.get("gst_mode") or "intra"
    rate = (inv.get("items") or [{}])[0].get("gst", 0)
    number = await next_number(ws, "credit_note")
    doc = {"_id": new_id(), "ws": ws, "kind": "credit_note", "number": number, "customer": inv.get("customer") or {},
           "items": [{"name": f"Refund against {inv['number']}", "qty": 1, "unit": "nos", "rate": tot["subtotal"], "gst": rate,
                      "amount": tot["subtotal"]}],
           "gst_mode": mode, "totals": tot, "status": "issued", "invoice_id": inv["_id"], "against": inv["number"],
           "source": "gateway", "gateway": "razorpay", "gateway_payment_id_refunded": ev.payment_id, "gateway_refund_id": ev.refund_id,
           "created_by": "gateway", "created_at": now(), "updated_at": now()}
    try:
        await db().quotes.insert_one(doc)
    except Exception:
        await db().gateway_payments.delete_one({"_id": rec})
        raise
    await db().quotes.update_one({"_id": inv["_id"]}, {"$inc": {"refunded": round(ev.amount, 2), "refunded_gst": tot["gst"]},
                                                      "$set": {"updated_at": now()}})
    dashboard_cache.bust(ws)
    await track("credit_note", ws, via="gateway")
    await maybe_email(owner, doc)
    return {"result": "credit_note", "number": number, "credit_note_id": doc["_id"]}


STALE_CLAIM = timedelta(minutes=5)


async def _take(key: str) -> str:
    """'new' | 'done' (acted on before) | 'busy' (another delivery is on it right now). A claim left 'processing' by a
    worker that died is taken over after 5 minutes."""
    try:
        await db().gateway_claims.insert_one({"_id": key, "status": "processing", "started_at": now()})
        return "new"
    except DuplicateKeyError:
        pass
    c = await db().gateway_claims.find_one({"_id": key}) or {}
    if c.get("status") == "done":
        return "done"
    started = c.get("started_at")
    if started and started < now() - STALE_CLAIM:
        took = await db().gateway_claims.find_one_and_update({"_id": key, "status": "processing", "started_at": started},
                                                             {"$set": {"started_at": now(), "taken_over": True}})
        if took:
            return "new"
    return "busy"


@router.post("/hooks/payments/{token}")
async def payment_webhook(token: str, request: Request):
    """The owner's own Razorpay account calls this. Signed with their webhook secret; each payment acts once however often
    it's delivered. A 5xx (or 409 while another delivery is mid-way) makes Razorpay retry; an unexpected failure releases
    the claim and returns 500."""
    found = await connections.by_webhook("razorpay", token)
    if not found:
        raise HTTPException(404, "Not found")
    ws, conn = found
    body = await request.body()
    gw = CONNECTORS["razorpay"]
    if not gw.verify(body, {k.lower(): v for k, v in request.headers.items()}, (conn or {}).get("webhook_secret", "")):
        raise HTTPException(401, "Bad signature")
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        raise HTTPException(400, "Not JSON")
    ev = gw.parse(payload)
    if not ev:
        return {"ok": True, "ignored": str((payload or {}).get("event") or "")[:60]}
    owner = await db().users.find_one({"_id": ws}) or {}
    if not owner or owner.get("disabled") or not plans.has(owner, "gateway_invoicing"):
        # held, not dropped: kept with what's needed to invoice it later, and the owner is told (once per payment)
        if await db().payment_events.find_one({"ws": ws, "result": "held", "kind": ev.kind, "payment_id": ev.payment_id, "refund_id": ev.refund_id},
                                               {"_id": 1}):
            return {"ok": True, "held": "plan"}
        await _log(ws, ev, "held", reason="plan", email=ev.email, contact=ev.contact, notes=ev.notes, payment_at=ev.at)
        if owner and not owner.get("disabled"):
            from ..services import notify
            await notify(ws, f"A payment of ₹{ev.amount:,.2f} came in through Razorpay, but automatic invoices aren't in your plan now, "
                             "so it wasn't invoiced. It's kept for you.")
        return {"ok": True, "held": "plan"}
    if ev.currency and ev.currency != "INR":
        await _log(ws, ev, "ignored_currency", currency=ev.currency)
        return {"ok": True, "ignored": "currency"}
    key = f"pay:{ws}:{ev.kind}:{ev.refund_id or ev.payment_id}"
    state = await _take(key)
    if state == "done":
        return {"ok": True, "duplicate": True}
    if state == "busy":
        raise HTTPException(409, "This payment is being recorded right now; try again shortly.")
    try:
        res = await (captured(owner, ev) if ev.kind == "captured" else refunded(owner, ev))
    except Exception:
        log.exception("payment %s for %s failed", ev.payment_id, ws)
        await db().gateway_claims.delete_one({"_id": key})
        raise HTTPException(500, "Couldn't record this payment yet; it will be retried.")
    await db().gateway_claims.update_one({"_id": key}, {"$set": {"status": "done", "done_at": now(), "result": res["result"]}})
    await _log(ws, ev, res["result"], number=res.get("number"), **({"still_owed": res["still_owed"]} if "still_owed" in res else {}))
    return {"ok": True, **res}


# For the Agents page (spec 4.4): shown for information. It runs on the payment webhook, not on a clock or a lead; "won"
# (money in from a customer) is the closest of the catalog's triggers. Group "inbox" keeps it off the AI-team list.
from ..agents import catalog  # noqa: E402

catalog.register("gateway_invoice", {
    "name": "Automatic invoices", "feature": "gateway_invoicing", "trigger": "won", "info_only": True, "group": "inbox",
    "what": "Every payment through your own Razorpay becomes a GST invoice (or marks the amount you were owed paid); a refund becomes a credit note.",
    "when": "When Razorpay tells the hub a payment came in"})


async def ensure_indexes() -> None:
    # once-only is kept by gateway_payments (one document per payment or refund, by _id); these are for lookups
    q = db().quotes
    await q.create_index([("ws", 1), ("gateway_payment_id", 1)], sparse=True)
    await q.create_index([("ws", 1), ("gateway_refund_id", 1)], sparse=True)
    await db().payment_events.create_index([("ws", 1), ("at", -1)])
    await db().dues.create_index([("ws", 1), ("gateway_payment_id", 1)], sparse=True)
