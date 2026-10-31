"""
Running the Business: customers, quotations and invoices (GST worked out by the hub), and the Employz.ai CRM starter.

customers {_id, ws, name, phone, tier A|B|C, total_value, purchases, first_purchase, last_purchase, last_item, reorder_days,
           occasion {label, date "MM-DD"}, source, notes, ...}
quotes    {_id, ws, kind quote|invoice, number, customer {name, phone, gstin, address}, items [{name, qty, unit, rate, gst}],
           gst_mode intra|inter|none, totals {subtotal, gst, cgst, sgst, igst, total}, status draft|sent|accepted|lost (quotes)
           or due|paid (invoices), sent_on, valid_days, notes, terms, quote_id/invoice_id, due_id, followups, created_at}
"""
import csv
import io
import re
from datetime import date, timedelta
from urllib.parse import quote as urlquote

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile as FastUpload
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from pymongo import ReturnDocument
from starlette.datastructures import UploadFile

import secrets

from .. import connections, gst as gst_slabs, plans
from ..config import settings
from ..agents.desk import crm_sync
from ..ai import desk_tasks
from ..context import Ctx, feature
from ..db import IST, db, now, public
from ..security import normalise_phone
from ..services import new_id, run_ai, track
from .hub import profile_of, wa_digits

router = APIRouter(prefix="/api")


def today() -> date:
    return now().astimezone(IST).date()


def _phone(raw: str) -> str:
    try:
        return normalise_phone(str(raw or "")[:24]) if str(raw or "").strip() else ""
    except HTTPException:
        return ""


# ═════════════════════════ Customers ═════════════════════════
class Occasion(BaseModel):
    label: str = Field(default="", max_length=40)
    date: str = Field(default="", pattern=r"^(|\d{2}-\d{2})$")   # MM-DD


class CustomerIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    phone: str = Field(default="", max_length=24)
    total_value: float = Field(default=0, ge=0, le=1e11)
    purchases: int = Field(default=0, ge=0, le=100_000)
    last_purchase: str = Field(default="", pattern=r"^(|\d{4}-\d{2}-\d{2})$")
    last_item: str = Field(default="", max_length=120)
    reorder_days: int | None = Field(default=None, ge=1, le=730)
    occasion: Occasion | None = None
    notes: str = Field(default="", max_length=600)


class CustomerPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=80)
    phone: str | None = Field(default=None, max_length=24)
    total_value: float | None = Field(default=None, ge=0, le=1e11)
    purchases: int | None = Field(default=None, ge=0, le=100_000)
    last_purchase: str | None = Field(default=None, pattern=r"^(|\d{4}-\d{2}-\d{2})$")
    last_item: str | None = Field(default=None, max_length=120)
    reorder_days: int | None = Field(default=None, ge=0, le=730)
    occasion: Occasion | None = None
    notes: str | None = Field(default=None, max_length=600)
    opted_out: bool | None = None


async def retier(ws: str) -> dict:
    """A, B and C by value: A = the customers who make up the first 70% of all value, B = the next 20%, C = the rest."""
    rows = [c async for c in db().customers.find({"ws": ws}, {"total_value": 1}).sort("total_value", -1).limit(20000)]
    total = sum(float(c.get("total_value") or 0) for c in rows)
    run, counts = 0.0, {"A": 0, "B": 0, "C": 0}
    for c in rows:
        v = float(c.get("total_value") or 0)
        tier = "C"
        if total and v > 0:
            tier = "A" if run < total * 0.7 else "B" if run < total * 0.9 else "C"
        run += v
        counts[tier] += 1
        await db().customers.update_one({"_id": c["_id"]}, {"$set": {"tier": tier}})
    return counts


def _cview(c: dict) -> dict:
    out = public(c)
    lp = c.get("last_purchase")
    if lp:
        try:
            out["days_since"] = (today() - date.fromisoformat(lp)).days
        except ValueError:
            pass
    if c.get("reorder_days") and lp:
        try:
            out["reorder_on"] = (date.fromisoformat(lp) + timedelta(days=int(c["reorder_days"]))).isoformat()
        except ValueError:
            pass
    return out


@router.get("/customers")
async def list_customers(tier: str = "", q: str = "", ctx: Ctx = Depends(feature("customers", "manager"))):
    query: dict = {"ws": ctx.ws}
    if tier in ("A", "B", "C"):
        query["tier"] = tier
    if q.strip():
        safe = re.escape(q.strip()[:40])
        query["$or"] = [{"name": {"$regex": safe, "$options": "i"}}, {"phone": {"$regex": safe}}]
    items = [_cview(c) async for c in db().customers.find(query).sort("total_value", -1).limit(1000)]
    counts = {t: await db().customers.count_documents({"ws": ctx.ws, "tier": t}) for t in ("A", "B", "C")}
    return {"items": items, "counts": counts, "total": await db().customers.count_documents({"ws": ctx.ws}),
            "can_import": ctx.has("customer_import")}


@router.post("/customers")
async def add_customer(body: CustomerIn, ctx: Ctx = Depends(feature("customers", "manager"))):
    phone = _phone(body.phone)
    if body.phone.strip() and not phone:
        raise HTTPException(400, "Enter a valid 10-digit mobile number")
    if phone and await db().customers.find_one({"ws": ctx.ws, "phone": phone}, {"_id": 1}):
        raise HTTPException(409, "A customer with this number is already on the list.")
    doc = {"_id": new_id(), "ws": ctx.ws, **body.model_dump(), "phone": phone, "tier": "", "source": "added",
           "first_purchase": body.last_purchase or None, "created_at": now(), "updated_at": now()}
    doc["occasion"] = body.occasion.model_dump() if body.occasion and body.occasion.date else None
    await db().customers.insert_one(doc)
    await retier(ctx.ws)
    return _cview(await db().customers.find_one({"_id": doc["_id"]}))


@router.patch("/customers/{cid}")
async def edit_customer(cid: str, body: CustomerPatch, ctx: Ctx = Depends(feature("customers", "manager"))):
    c = await db().customers.find_one({"_id": cid, "ws": ctx.ws})
    if not c:
        raise HTTPException(404, "Not found")
    patch = body.model_dump(exclude_none=True)
    if "phone" in patch:
        patch["phone"] = _phone(patch["phone"])
    if "occasion" in patch:
        patch["occasion"] = patch["occasion"] if patch["occasion"].get("date") else None
    if patch.get("reorder_days") == 0:
        patch["reorder_days"] = None
    patch["updated_at"] = now()
    await db().customers.update_one({"_id": cid}, {"$set": patch})
    if "total_value" in patch:
        await retier(ctx.ws)
    return _cview(await db().customers.find_one({"_id": cid}))


@router.delete("/customers/{cid}")
async def delete_customer(cid: str, ctx: Ctx = Depends(feature("customers", "manager"))):
    await db().customers.delete_one({"_id": cid, "ws": ctx.ws})
    return {"ok": True}


COLUMNS = {"phone": ("phone", "mobile", "number", "contact no", "whatsapp", "cell"), "name": ("name", "customer", "client", "party", "contact"),
           "value": ("total", "amount", "value", "sales", "billing", "revenue", "purchase value"),
           "last": ("last purchase", "last order", "date", "last bill", "last visit"), "item": ("item", "product", "service", "bought"),
           "reorder": ("reorder", "cycle", "every"), "birthday": ("birthday", "dob", "date of birth"), "anniversary": ("anniversary",),
           "count": ("orders", "purchases", "visits", "count")}


def _match_columns(header: list[str]) -> dict[str, int]:
    found = {}
    for i, h in enumerate(header):
        low = str(h or "").strip().lower()
        for key, words in COLUMNS.items():
            if key not in found and any(w in low for w in words):
                found[key] = i
                break
    return found


def _date_of(raw) -> str:
    if hasattr(raw, "isoformat"):
        return raw.isoformat()[:10]
    s = str(raw or "").strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d-%b-%Y", "%d %b %Y", "%d/%m/%y"):
        try:
            from datetime import datetime
            return datetime.strptime(s[:11], fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def _money(raw) -> float:
    try:
        return max(0.0, float(re.sub(r"[^\d.]", "", str(raw or "")) or 0))
    except ValueError:
        return 0.0


@router.post("/customers/import")
async def import_customers(request: Request, ctx: Ctx = Depends(feature("customer_import", "manager"))):
    """An Excel or CSV list (old customers): cleaned (real mobile numbers only, duplicates merged), then sorted A, B and C."""
    form = await request.form()
    try:
        f = form.get("file")
        if not isinstance(f, UploadFile):
            raise HTTPException(400, "Choose an Excel or CSV file.")
        data = await f.read(5 * 1024 * 1024 + 1)
        name = (f.filename or "").lower()
    finally:
        await form.close()
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(413, "Keep the file under 5 MB.")
    rows: list[list] = []
    if name.endswith((".xlsx", ".xlsm")):
        import openpyxl
        try:
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception:  # noqa: BLE001
            raise HTTPException(400, "We couldn't open that Excel file. Save it again and retry.")
        rows = [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)][:20001]
    elif name.endswith(".csv"):
        rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig", errors="replace"))))[:20001]
    else:
        raise HTTPException(415, "Upload an Excel (.xlsx) or CSV file.")
    if len(rows) < 2:
        raise HTTPException(400, "The file has no rows.")
    cols = _match_columns(rows[0])
    if "name" not in cols or "phone" not in cols:
        raise HTTPException(400, "We need a name column and a phone/mobile column in the first row.")
    added = merged = skipped = 0
    for r in rows[1:]:
        get = lambda k: r[cols[k]] if k in cols and cols[k] < len(r) else None  # noqa: E731
        phone = _phone(str(get("phone") or ""))
        nm = str(get("name") or "").strip()[:80]
        if not phone or len(nm) < 2:
            skipped += 1
            continue
        value, last = _money(get("value")), _date_of(get("last"))
        occ = None
        for key, label in (("birthday", "birthday"), ("anniversary", "anniversary")):
            d = _date_of(get(key))
            if d:
                occ = {"label": label, "date": d[5:]}
                break
        existing = await db().customers.find_one({"ws": ctx.ws, "phone": phone})
        if existing:
            patch = {"total_value": float(existing.get("total_value") or 0) + value, "updated_at": now()}
            if last and last > (existing.get("last_purchase") or ""):
                patch["last_purchase"] = last
            await db().customers.update_one({"_id": existing["_id"]}, {"$set": patch})
            merged += 1
            continue
        reorder = int(_money(get("reorder"))) or None
        await db().customers.insert_one({"_id": new_id(), "ws": ctx.ws, "name": nm, "phone": phone, "total_value": value,
                                         "purchases": int(_money(get("count"))) or (1 if value else 0), "first_purchase": last or None,
                                         "last_purchase": last or None, "last_item": str(get("item") or "")[:120],
                                         "reorder_days": reorder if reorder and reorder <= 730 else None, "occasion": occ,
                                         "source": "import", "tier": "", "notes": "", "created_at": now(), "updated_at": now()})
        added += 1
    counts = await retier(ctx.ws)
    await track("customers_imported", ctx.ws, added=added)
    return {"added": added, "merged": merged, "skipped": skipped, "tiers": counts}


# ═════════════════════════ Quotations and invoices ═════════════════════════
class Line(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    qty: float = Field(gt=0, le=1e7)
    unit: str = Field(default="nos", max_length=20)
    rate: float = Field(ge=0, le=1e10)
    gst: float = Field(default=18, ge=0, le=gst_slabs.MAX)
    sac: str = Field(default="", max_length=10, pattern=r"^[0-9 ]*$")    # SAC (services) or HSN (goods) code, printed per line
    description: str = Field(default="", max_length=600)                 # a line or two under the item on the invoice


class QCustomer(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    phone: str = Field(default="", max_length=24)
    email: str = Field(default="", max_length=254)
    gstin: str = Field(default="", max_length=15)
    address: str = Field(default="", max_length=300)


class QuoteIn(BaseModel):
    customer: QCustomer
    items: list[Line] = Field(min_length=1, max_length=60)
    gst_mode: str = Field(default="intra", pattern="^(intra|inter|none)$")
    notes: str = Field(default="", max_length=1000)
    terms: str = Field(default="", max_length=1500)
    valid_days: int = Field(default=15, ge=1, le=365)
    lead_id: str | None = Field(default=None, max_length=40)


class StatusIn(BaseModel):
    status: str = Field(pattern="^(draft|sent|accepted|lost)$")


class InvoiceIn(BaseModel):
    due_in_days: int = Field(default=7, ge=0, le=365)


class DraftIn(BaseModel):
    brief: str = Field(min_length=5, max_length=6000)


class QSettings(BaseModel):
    """Invoicing → Business details: what every quotation and invoice prints (invoice_doc.compose)."""
    gstin: str = Field(default="", max_length=15)
    address: str = Field(default="", max_length=300)
    payment: str = Field(default="", max_length=300)
    terms: str = Field(default="", max_length=1500)
    legal_name: str = Field(default="", max_length=120)       # "Giant Analytics Private Limited", when it differs from the name
    phone: str = Field(default="", max_length=24)
    email: str = Field(default="", max_length=120)
    pan: str = Field(default="", max_length=10, pattern=r"^([A-Za-z]{5}[0-9]{4}[A-Za-z])?$")
    cin: str = Field(default="", max_length=21)
    bank_account_name: str = Field(default="", max_length=120)
    bank_name: str = Field(default="", max_length=80)
    bank_account: str = Field(default="", max_length=30)
    bank_account_type: str = Field(default="", max_length=20)
    bank_ifsc: str = Field(default="", max_length=11, pattern=r"^([A-Za-z]{4}0[A-Za-z0-9]{6})?$")
    upi: str = Field(default="", max_length=80)
    signatory: str = Field(default="", max_length=80)         # the name and designation under "Authorised signatory"


def totals(items: list[dict], mode: str) -> dict:
    sub = gst = 0.0
    for i in items:
        amt = round(i["qty"] * i["rate"], 2)
        i["amount"] = amt
        sub += amt
        gst += 0 if mode == "none" else round(amt * i["gst"] / 100, 2)
    sub, gst = round(sub, 2), round(gst, 2)
    out = {"subtotal": sub, "gst": gst, "total": round(sub + gst, 2)}
    if mode == "intra":
        out.update(cgst=round(gst / 2, 2), sgst=round(gst - round(gst / 2, 2), 2), igst=0)
    elif mode == "inter":
        out.update(cgst=0, sgst=0, igst=gst)
    else:
        out.update(cgst=0, sgst=0, igst=0)
    return out


async def next_number(ws: str, kind: str) -> str:
    c = await db().counters.find_one_and_update({"_id": f"{ws}:{kind}"}, {"$inc": {"n": 1}}, upsert=True, return_document=ReturnDocument.AFTER)
    return f"{ {'quote': 'Q', 'credit_note': 'CN'}.get(kind, 'INV') }-{int(c['n']):04d}"


def rupees(x: float) -> str:
    whole, frac = f"{x:.2f}".split(".")
    s = whole
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        s = ",".join(([head] if head else []) + groups) + "," + tail
    return f"₹{s}" + (f".{frac}" if frac != "00" else "")


DOC_NAME = {"quote": "quotation", "credit_note": "credit note"}


def share_text(q: dict, business: str, pdf_url: str | None = None, pay_url: str | None = None) -> str:
    lines = [f"{ {'quote': 'Quotation', 'credit_note': 'Credit note'}.get(q['kind'], 'Invoice') } {q['number']} from {business}"]
    for i in q["items"][:12]:
        amount = i.get("amount", round(float(i.get("qty") or 0) * float(i.get("rate") or 0), 2))
        lines.append(f"• {i.get('name', '')} — {float(i.get('qty') or 0):g} {i.get('unit') or 'nos'} × {rupees(float(i.get('rate') or 0))} = {rupees(amount)}")
    t = q["totals"]
    lines.append(f"Subtotal {rupees(t['subtotal'])}" + (f" + GST {rupees(t['gst'])}" if t["gst"] else ""))
    lines.append(f"Total: {rupees(t['total'])}")
    if q["kind"] == "quote":
        lines.append(f"Valid for {q.get('valid_days', 15)} days. Reply YES to confirm.")
    elif q.get("due_date"):
        lines.append(f"Due by {q['due_date']}.")
    if pay_url:
        lines.append(f"Pay online: {pay_url}")
    if pdf_url:
        lines.append(f"See the {DOC_NAME.get(q['kind'], 'invoice')} as a PDF: {pdf_url}")
    return "\n".join(lines)


async def pdf_link(q: dict) -> str:
    """The document's own PDF address for the customer: no login, an unguessable token made the first time it's shared."""
    token = q.get("share_token")
    if not token:
        token = secrets.token_urlsafe(16)
        r = await db().quotes.update_one({"_id": q["_id"], "share_token": {"$exists": False}}, {"$set": {"share_token": token}})
        if not r.modified_count:   # made at the same moment by another request: use that one
            token = (await db().quotes.find_one({"_id": q["_id"]}, {"share_token": 1}) or {}).get("share_token") or token
        q["share_token"] = token
    return f"{settings.app_url}/i/{token}"


def needs_price(q: dict) -> bool:
    """A quotation that adds up to ₹0 (its lines have no rates yet): nothing to send a customer until it has a price."""
    return q.get("kind") == "quote" and float((q.get("totals") or {}).get("total") or 0) <= 0


async def _qview(q: dict) -> dict:
    from .. import razorpay_invoices
    out = public(q)
    out.pop("share_token", None)
    out["rzp"] = razorpay_invoices.view(q.get("rzp"))
    if needs_price(q):   # no customer link, PDF link or message until it has a price
        return {**out, "needs_price": True, "share_text": None, "pdf_url": None, "whatsapp_link": None}
    b = await db().businesses.find_one({"owner_id": q["ws"]}, {"name": 1}) or {}
    url = await pdf_link(q)
    text = share_text(q, b.get("name") or "us", url, razorpay_invoices.pay_url(q))
    out["share_text"] = text
    out["pdf_url"] = url
    phone = wa_digits((q.get("customer") or {}).get("phone", ""))
    out["whatsapp_link"] = f"https://wa.me/{phone}?text={urlquote(text)}" if phone else None
    return out


@router.get("/quotes")
async def list_quotes(kind: str = "quote", ctx: Ctx = Depends(feature("quotations", "manager"))):
    kind = "invoice" if kind == "invoice" else "quote"
    return [await _qview(q) async for q in db().quotes.find({"ws": ctx.ws, "kind": kind}).sort("created_at", -1).limit(300)]


@router.get("/quotes/inquiries")
async def quote_inquiries(ctx: Ctx = Depends(feature("quotations", "manager"))):
    """The last 30 days' inquiries and what happened to each one's quotation (auto_quote.status_view)."""
    from .. import auto_quote
    leads = [x async for x in db().leads.find({"owner_id": ctx.ws, "created_at": {"$gte": now() - timedelta(days=30)}},
                                              {"name": 1, "phone": 1, "email": 1, "message": 1, "source": 1, "created_at": 1, "status": 1,
                                               "auto_quote": 1, "quote_id": 1}).sort("created_at", -1).limit(30)]
    numbers = await auto_quote.numbers_for(ctx.ws, leads)
    return [{"id": x["_id"], "name": x.get("name", ""), "phone": x.get("phone", ""), "message": (x.get("message") or "")[:300],
             "source": x.get("source", "website"), "created_at": x["created_at"].isoformat(), "quote": auto_quote.status_view(x, numbers)}
            for x in leads]


@router.post("/quotes/from-inquiry/{lead_id}")
async def quote_from_inquiry(lead_id: str, ctx: Ctx = Depends(feature("quotations", "manager"))):
    """Make quotation: the draft for an inquiry that didn't get one on its own (1 AI token). Never sent by itself."""
    from .. import auto_quote
    from ..agents.catalog import Skip
    lead = await db().leads.find_one({"_id": lead_id, "owner_id": ctx.ws})
    if not lead:
        raise HTTPException(404, "Inquiry not found")
    if not (lead.get("message") or "").strip():
        raise HTTPException(400, "This inquiry has no message to quote from. Write the quotation with New quotation.")
    try:
        await auto_quote.make_draft(ctx.owner, lead, manual=True)
    except Skip:
        pass                                   # it has one already: its status points to it
    fresh = await db().leads.find_one({"_id": lead_id})
    numbers = await auto_quote.numbers_for(ctx.ws, [fresh])
    return auto_quote.status_view(fresh, numbers)


@router.get("/quotes/settings")
async def get_qsettings(ctx: Ctx = Depends(feature("quotations", "manager"))):
    return public(await db().quote_settings.find_one({"_id": ctx.ws})) or {}


@router.put("/quotes/settings")
async def save_qsettings(body: QSettings, ctx: Ctx = Depends(feature("quotations", "owner"))):
    await db().quote_settings.update_one({"_id": ctx.ws}, {"$set": body.model_dump()}, upsert=True)   # the signature stays
    return {"ok": True}


@router.post("/quotes/settings/signature")
async def save_signature(file: FastUpload = File(...), ctx: Ctx = Depends(feature("quotations", "owner"))):
    """A picture of the signature, printed above "Authorised signatory" (PNG or JPG, up to 2 MB)."""
    from .. import files, invoice_doc
    data, ctype = await files.read_upload(file, ("image/png", "image/jpeg"), 2)
    if not invoice_doc.image_type(data):
        raise HTTPException(400, "Upload the signature as a PNG or JPG picture.")
    s = await db().quote_settings.find_one({"_id": ctx.ws}, {"signature_file_id": 1}) or {}
    f = await files.put(ctx.ws, data, "signature", ctype, "signature")
    await db().quote_settings.update_one({"_id": ctx.ws}, {"$set": {"signature_file_id": f["_id"]}}, upsert=True)
    if s.get("signature_file_id"):
        await files.delete(s["signature_file_id"], ctx.ws)
    return {"ok": True}


@router.delete("/quotes/settings/signature")
async def remove_signature(ctx: Ctx = Depends(feature("quotations", "owner"))):
    from .. import files
    s = await db().quote_settings.find_one({"_id": ctx.ws}, {"signature_file_id": 1}) or {}
    if s.get("signature_file_id"):
        await files.delete(s["signature_file_id"], ctx.ws)
    await db().quote_settings.update_one({"_id": ctx.ws}, {"$unset": {"signature_file_id": ""}})
    return {"ok": True}


@router.post("/quotes/draft")
async def draft_quote(body: DraftIn, ctx: Ctx = Depends(feature("quotations", "manager"))):
    """The Quotation Officer: a voice note (already transcribed) or a few lines → lines to check. Not saved until you save it."""
    p = await profile_of(ctx.ws)
    context = ""
    if plans.has(ctx.owner, "company_brain"):
        from ..company_brain import context as brain
        context = await brain(ctx.ws, body.brief)
    from .. import invoice_style as ist   # the owner's own past invoices: how they name items, their units, GST and rates
    own = ist.prompt_block((await db().invoice_styles.find_one({"_id": ctx.ws}) or {}).get("style"))
    if own:
        context = own + ("\n\n" + context if context else "")

    def shape(r):
        items = []
        for i in (r.get("items") or [])[:60]:
            if not isinstance(i, dict) or not str(i.get("name") or "").strip():
                continue
            def num(v, d=0.0):
                try:
                    return max(0.0, float(str(v).replace(",", "")))
                except (TypeError, ValueError):
                    return d
            items.append({"name": str(i["name"])[:160], "qty": num(i.get("qty"), 1) or 1, "unit": str(i.get("unit") or "nos")[:20],
                          "rate": num(i.get("rate")), "gst": min(num(i.get("gst"), 18), gst_slabs.MAX)})
        if not items:
            raise ValueError("no items")
        cust = r.get("customer") if isinstance(r.get("customer"), dict) else {}
        return {"customer": {"name": str(cust.get("name") or "")[:120], "phone": str(cust.get("phone") or "")[:24]}, "items": items,
                "notes": str(r.get("notes") or "")[:1000]}
    out = await run_ai(ctx.owner, "quote", desk_tasks.quote_items(p, body.brief, context), f"Quotation draft: {body.brief[:60]}", save_output=False, check=shape)
    out["missing_rates"] = sum(1 for i in out["items"] if not i["rate"])
    return out


# ───── the owner's own invoice as a reference (see app/invoice_style.py) ─────
async def _style_view(ws: str, filled: list[str] | None = None) -> dict:
    from .. import invoice_style as ist
    d = await db().invoice_styles.find_one({"_id": ws}) or {}
    samples = [{"id": x["id"], "name": x["name"], "kind": x["kind"], "pages": x.get("pages", 1), "at": x["at"].isoformat(),
                "summary": x["style"].get("summary", "")} for x in d.get("samples") or []]
    return {"samples": samples, "style": d.get("style"), "max": ist.MAX_SAMPLES, "max_mb": ist.MAX_MB, **({"filled": filled} if filled is not None else {})}


@router.get("/quotes/style")
async def get_style(ctx: Ctx = Depends(feature("quotations", "manager"))):
    return await _style_view(ctx.ws)


@router.post("/quotes/style")
async def add_style_sample(file: FastUpload = File(...), ctx: Ctx = Depends(feature("quotations", "manager"))):
    """One of the owner's own invoices (PDF or photo) → how this business writes them (1 AI token). The file isn't kept."""
    from .. import invoice_style as ist
    d = await db().invoice_styles.find_one({"_id": ctx.ws}) or {}
    if len(d.get("samples") or []) >= ist.MAX_SAMPLES:
        raise HTTPException(400, f"You have {ist.MAX_SAMPLES} invoices already. Remove one to add another.")
    data = await file.read(ist.MAX_MB * 1024 * 1024 + 1)
    try:
        sample = ist.read(data, file.filename or "", file.content_type or "")
    except ist.StyleError as e:
        raise HTTPException(400, str(e))
    finally:
        del data
    style = await run_ai(ctx.owner, "invoice_style", ist.learn(sample), "Your invoice style", save_output=False, check=ist.shape)
    entry = {"id": new_id(), "name": (file.filename or "invoice")[:120], "kind": sample["kind"], "pages": sample["pages"], "at": now(),
             "style": style}
    samples = [*(d.get("samples") or []), entry]
    merged = ist.merged(samples)
    await db().invoice_styles.replace_one({"_id": ctx.ws}, {"_id": ctx.ws, "samples": samples, "style": merged, "updated_at": now()}, upsert=True)
    # the settings the owner hasn't filled yet come from their own invoice (never overwriting what they typed)
    qs = await db().quote_settings.find_one({"_id": ctx.ws}) or {}
    fill = {k: merged[k] for k in ("terms", "payment") if merged.get(k) and not (qs.get(k) or "").strip()}
    if fill:
        await db().quote_settings.update_one({"_id": ctx.ws}, {"$set": fill}, upsert=True)
    await track("invoice_style_sample", ctx.ws, kind=sample["kind"])
    return await _style_view(ctx.ws, sorted(fill))


@router.delete("/quotes/style/{sid}")
async def remove_style_sample(sid: str, ctx: Ctx = Depends(feature("quotations", "manager"))):
    from .. import invoice_style as ist
    d = await db().invoice_styles.find_one({"_id": ctx.ws}) or {}
    samples = [x for x in d.get("samples") or [] if x["id"] != sid]
    if len(samples) == len(d.get("samples") or []):
        raise HTTPException(404, "Not found")
    if samples:
        await db().invoice_styles.update_one({"_id": ctx.ws}, {"$set": {"samples": samples, "style": ist.merged(samples), "updated_at": now()}})
    else:
        await db().invoice_styles.delete_one({"_id": ctx.ws})
    return await _style_view(ctx.ws)


@router.post("/quotes")
async def save_quote(body: QuoteIn, ctx: Ctx = Depends(feature("quotations", "manager"))):
    items = [i.model_dump() for i in body.items]
    tot = totals(items, body.gst_mode)
    cust = body.customer.model_dump()
    cust["phone"] = _phone(cust["phone"]) or cust["phone"].strip()
    s = await db().quote_settings.find_one({"_id": ctx.ws}) or {}
    doc = {"_id": new_id(), "ws": ctx.ws, "kind": "quote", "number": await next_number(ctx.ws, "quote"), "customer": cust, "items": items,
           "gst_mode": body.gst_mode, "totals": tot, "status": "draft", "notes": body.notes.strip(), "terms": body.terms.strip() or s.get("terms", ""),
           "valid_days": body.valid_days, "lead_id": body.lead_id, "followups": 0, "created_by": ctx.actor, "created_at": now(), "updated_at": now()}
    await db().quotes.insert_one(doc)
    await track("quote", ctx.ws)
    return await _qview(doc)


@router.put("/quotes/{qid}")
async def edit_quote(qid: str, body: QuoteIn, ctx: Ctx = Depends(feature("quotations", "manager"))):
    q = await db().quotes.find_one({"_id": qid, "ws": ctx.ws})
    if not q:
        raise HTTPException(404, "Not found")
    if q["kind"] == "invoice" or q["status"] in ("accepted", "lost"):
        raise HTTPException(400, "This one is closed. Make a new quotation instead.")
    items = [i.model_dump() for i in body.items]
    cust = body.customer.model_dump()
    cust["phone"] = _phone(cust["phone"]) or cust["phone"].strip()
    await db().quotes.update_one({"_id": qid}, {"$set": {"customer": cust, "items": items, "gst_mode": body.gst_mode, "totals": totals(items, body.gst_mode),
                                                         "notes": body.notes.strip(), "terms": body.terms.strip(), "valid_days": body.valid_days,
                                                         "updated_at": now()}})
    return await _qview(await db().quotes.find_one({"_id": qid}))


@router.post("/quotes/{qid}/status")
async def quote_status(qid: str, body: StatusIn, ctx: Ctx = Depends(feature("quotations", "manager"))):
    q = await db().quotes.find_one({"_id": qid, "ws": ctx.ws, "kind": "quote"})
    if not q:
        raise HTTPException(404, "Not found")
    if body.status == "sent" and needs_price(q):
        raise HTTPException(400, "This quotation adds up to ₹0. Press Edit and add the prices before you send it.")
    patch = {"status": body.status, "updated_at": now()}
    if body.status == "sent" and not q.get("sent_on"):
        patch["sent_on"] = today().isoformat()
    await db().quotes.update_one({"_id": qid}, {"$set": patch})
    if body.status in ("accepted", "lost"):
        await _closed(ctx, q, body.status)
    if body.status == "accepted":       # Invoice on yes: the invoice is made from it (once; told when a price is missing)
        from .. import auto_quote
        await auto_quote.invoice_on_yes(ctx.owner, q, ctx.actor)
    return await _qview(await db().quotes.find_one({"_id": qid}))


async def _closed(ctx: Ctx, q: dict, status: str) -> None:
    """Accepted or lost: waiting follow-ups expire, and the linked lead is won (with the quotation's value) or lost."""
    from ..agents.approvals import expire
    await expire({"ws": ctx.ws, "kind": "quote", "quote_id": q["_id"]}, f"Quotation {status}")
    if q.get("lead_id"):
        from ..agents import hooks
        lead = await db().leads.find_one({"_id": q["lead_id"], "owner_id": ctx.ws})
        if lead and lead.get("status") not in ("won", "lost"):
            result = "won" if status == "accepted" else "lost"
            upd = {"status": result, "updated_at": now(), "won_at": now() if result == "won" else None,
                   **({"value": q["totals"]["total"]} if result == "won" else {})}
            await db().leads.update_one({"_id": lead["_id"]}, {"$set": upd})
            from .. import crm   # v2.3: the deal's history in the CRM says why it closed
            await crm.on_lead_patched(ctx.ws, lead, upd, ctx.actor, ctx.user.get("name", ""))
            await hooks.on_lead_status(ctx.owner, {**lead, **upd}, result)


@router.post("/quotes/{qid}/invoice")
async def make_invoice(qid: str, body: InvoiceIn, ctx: Ctx = Depends(feature("quotations", "manager"))):
    """An accepted quotation → a numbered invoice, added to Money owed so the reminders can follow it."""
    q = await db().quotes.find_one({"_id": qid, "ws": ctx.ws, "kind": "quote"})
    if not q:
        raise HTTPException(404, "Not found")
    if q.get("invoice_id"):
        raise HTTPException(409, "This quotation already has an invoice.")
    inv = await invoice_from_quote(ctx.owner, ctx.ws, ctx.actor, q, body.due_in_days, ctx)
    if not inv:
        raise HTTPException(409, "This quotation already has an invoice.")
    return await _qview(inv)


async def invoice_from_quote(owner: dict, ws: str, actor: str, q: dict, due_in_days: int, ctx: Ctx | None = None) -> dict | None:
    """The invoice from a quotation (the Make invoice button, and Invoice on yes): numbered, into Money owed, its Razorpay
    invoice queued, emailed when that's on. None when the quotation got its invoice a moment ago."""
    qid = q["_id"]
    due_date = (today() + timedelta(days=due_in_days)).isoformat()
    inv_id, due_id = new_id(), new_id()
    claimed = await db().quotes.update_one({"_id": qid, "invoice_id": None}, {"$set": {"invoice_id": inv_id}})
    if not claimed.modified_count:
        return None
    number = await next_number(ws, "invoice")
    inv = {**{k: v for k, v in q.items() if k not in ("_id", "number", "status", "sent_on", "followups", "last_followup_at", "share_token")},
           "_id": inv_id, "kind": "invoice", "number": number, "status": "due", "quote_id": qid, "due_date": due_date, "due_id": due_id,
           "created_by": actor, "created_at": now(), "updated_at": now()}
    inv.pop("invoice_id", None)
    inv.pop("auto", None)
    inv.pop("missing_rates", None)
    inv["source"] = "quote"
    try:
        await db().quotes.insert_one(inv)
    except Exception:
        await db().quotes.update_one({"_id": qid, "invoice_id": inv_id}, {"$set": {"invoice_id": None}})   # not made: it can be tried again
        raise
    cust = q.get("customer") or {}
    if plans.has(owner, "money_owed"):
        await db().dues.insert_one({"_id": due_id, "ws": ws, "customer": cust.get("name", "")[:80], "phone": cust.get("phone", ""),
                                    "email": (cust.get("email") or "").strip().lower(),
                                    "amount": int(round(q["totals"]["total"] * 100)), "due_date": due_date, "note": f"Invoice {number}",
                                    "invoice_no": number, "invoice_id": inv_id, "status": "due", "paid_at": None, "reminders_sent": 0,
                                    "last_reminded_at": None, "created_by": actor, "created_at": now(), "updated_at": now()})
    else:
        await db().quotes.update_one({"_id": inv_id}, {"$set": {"due_id": None}})
    await db().quotes.update_one({"_id": qid}, {"$set": {"invoice_id": inv_id, "status": "accepted", "updated_at": now()}})
    from .. import razorpay_invoices   # its Razorpay invoice, with the pay link, when Razorpay is connected
    await razorpay_invoices.queue(ws, inv_id)
    if q.get("status") != "accepted" and ctx is not None:
        await _closed(ctx, q, "accepted")
    await track("invoice", ws)
    from .gateway import maybe_email   # "Email invoices automatically", when the owner switched it on (off by default)
    await maybe_email(owner, await db().quotes.find_one({"_id": inv_id}))
    return await db().quotes.find_one({"_id": inv_id})


@router.delete("/quotes/{qid}")
async def delete_quote(qid: str, ctx: Ctx = Depends(feature("quotations", "manager"))):
    q = await db().quotes.find_one({"_id": qid, "ws": ctx.ws})
    if not q:
        raise HTTPException(404, "Not found")
    if q["kind"] == "invoice":
        raise HTTPException(400, "Invoices are kept for your records. Mark it paid in Money owed instead.")
    await db().quotes.delete_one({"_id": qid})
    return {"ok": True}


@router.get("/quotes/{qid}/print", response_class=HTMLResponse)
async def print_quote(qid: str, ctx: Ctx = Depends(feature("quotations", "manager"))):
    from .public import templates
    q = await db().quotes.find_one({"_id": qid, "ws": ctx.ws})
    if not q:
        raise HTTPException(404, "Not found")
    from .. import invoice_doc
    d = await invoice_doc.build(q)
    return HTMLResponse(templates.get_template("quote.html").render(d=d, inr=invoice_doc.inr, logo=invoice_doc.data_uri(d["logo"]),
                                                                    signature=invoice_doc.data_uri(d["signature"])))


# ═════════════════════════ Employz.ai CRM starter ═════════════════════════
@router.get("/crm")
async def crm_overview(ctx: Ctx = Depends(feature("crm_sync", "owner"))):
    conn = await connections.get(ctx.ws, "employz")
    synced = await db().leads.count_documents({"owner_id": ctx.ws, "crm.opportunity_id": {"$nin": [None, ""]}})
    failed = await db().leads.count_documents({"owner_id": ctx.ws, "crm.error": {"$nin": [None, ""]}})
    total = await db().leads.count_documents({"owner_id": ctx.ws})
    recent = [{"name": l.get("name", ""), "stage": l.get("stage", "identification"), "status": l.get("status"),
               "synced_at": (l.get("crm") or {}).get("synced_at").isoformat() if (l.get("crm") or {}).get("synced_at") else None,
               "error": (l.get("crm") or {}).get("error")}
              async for l in db().leads.find({"owner_id": ctx.ws, "crm": {"$exists": True}}).sort("crm.synced_at", -1).limit(10)]
    return {"connection": connections.view("employz", await _stored(ctx.ws, "employz"), ctx.owner), "ready": connections.ready("employz", conn),
            "open_link": (conn or {}).get("app_link") or None, "synced": synced, "failed": failed, "total": total, "recent": recent}


async def _stored(ws: str, kind: str) -> dict | None:
    return ((await db().connections.find_one({"_id": ws})) or {}).get(kind)


@router.post("/crm/sync")
async def crm_sync_all(ctx: Ctx = Depends(feature("crm_sync", "owner"))):
    conn = await connections.get(ctx.ws, "employz")
    if not connections.ready("employz", conn):
        raise HTTPException(400, "Employz.ai isn't connected yet. The team connects it for you — or add the details under Connections.")
    n = ok = 0
    async for l in db().leads.find({"owner_id": ctx.ws, "$or": [{"crm.opportunity_id": {"$in": [None, ""]}}, {"crm": {"$exists": False}}]},
                                   {"_id": 1}).limit(200):
        res = await crm_sync(ctx.owner, l["_id"])
        n += 1
        ok += bool(res.get("ok"))
    return {"tried": n, "synced": ok}


async def ensure_indexes() -> None:
    await db().customers.create_index([("ws", 1), ("phone", 1)])
    await db().customers.create_index([("ws", 1), ("total_value", -1)])
    await db().quotes.create_index([("ws", 1), ("kind", 1), ("created_at", -1)])
    await db().quotes.create_index([("ws", 1), ("kind", 1), ("status", 1)])
    await db().quotes.create_index("share_token", unique=True, sparse=True)     # the customer's PDF link (/i/<token>)
