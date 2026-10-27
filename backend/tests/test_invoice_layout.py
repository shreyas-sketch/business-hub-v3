"""The invoice layout every quotation, tax invoice and credit note uses: the business details from Invoicing → Business
details, SAC and description per line, CGST + SGST or IGST at their rates, the amount in words, bank details and the
authorised signatory — the same on the print page and in the PDF. Every name here is invented."""
import io

from PIL import Image

from app import invoice_doc, invoice_pdf
from app.db import db, now
from tests.test_v21_p0 import owner_on

DETAILS = {"legal_name": "Kestrel Analytics Private Limited", "gstin": "27AABCK1234M1Z5", "address": "Unit 4, Lake Road\nThane 400601",
           "phone": "+91 98200 00000", "email": "accounts@kestrel.example", "cin": "U72900MH2020PTC000001",
           "bank_account_name": "Kestrel Analytics Private Limited", "bank_name": "HDFC Bank", "bank_account": "50200012345678",
           "bank_account_type": "Current", "bank_ifsc": "HDFC0001234", "upi": "", "payment": "Payment within 7 days.",
           "terms": "Subject to Thane jurisdiction.", "signatory": "Asha Rao, Director"}

QUOTE = {"customer": {"name": "Lotus Foods LLP", "phone": "98390 41001", "gstin": "27AAAFL1234Q1Z2", "address": "Andheri, Mumbai"},
         "items": [{"name": "AI receptionist setup", "qty": 1, "rate": 100000, "gst": 18, "sac": "998314",
                    "description": "WhatsApp bot, booking flow and staff training."}], "gst_mode": "intra"}


def png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (240, 80), "white").save(buf, "PNG")
    return buf.getvalue()


def test_amounts_in_indian_figures_and_words():
    assert invoice_doc.inr(118000) == "₹1,18,000" and invoice_doc.inr(12345678.5) == "₹1,23,45,678.50" and invoice_doc.inr(999) == "₹999"
    assert invoice_doc.in_words(118000) == "Rupees One Lakh Eighteen Thousand Only"
    assert invoice_doc.in_words(25012345.75) == "Rupees Two Crore Fifty Lakh Twelve Thousand Three Hundred Forty Five and Seventy Five Paise Only"
    assert invoice_doc.in_words(0) == "Rupees Zero Only"


def test_compose_lays_out_a_tax_invoice():
    q = {"kind": "invoice", "number": "INV-0007", "gst_mode": "intra", "created_at": now(), "due_date": "2026-11-05", "status": "due",
         "customer": {"name": "Lotus Foods LLP", "gstin": "27AAAFL1234Q1Z2"}, "items": [QUOTE["items"][0] | {"amount": 100000}],
         "totals": {"subtotal": 100000, "gst": 18000, "cgst": 9000, "sgst": 9000, "igst": 0, "total": 118000}}
    d = invoice_doc.compose(q, {"name": "Kestrel"}, DETAILS, from_quote="Q-0003")
    assert d["title"] == "Tax Invoice" and d["tag"] == "Original for recipient"
    assert d["seller"]["name"] == "Kestrel Analytics Private Limited" and d["seller"]["brand"] == "Kestrel"
    assert d["seller"]["pan"] == "AABCK1234M"                                  # read from the GSTIN
    meta = dict(d["meta"])
    assert meta["Invoice No."] == "INV-0007" and meta["Ref. Quotation"] == "Q-0003" and meta["Place of supply"] == "Maharashtra (27)"
    assert d["lines"][0]["sac"] == "998314" and d["lines"][0]["gst"] == 18000 and d["show_sac"] and d["gst_head"] == "GST 18%"
    assert d["rows"] == [("Taxable value", 100000), ("CGST @ 9%", 9000), ("SGST @ 9%", 9000)]
    assert d["grand"] == "Amount due" and d["words"] == "Rupees One Lakh Eighteen Thousand Only"
    assert ("IFSC", "HDFC0001234") in d["bank"] and ("UPI", "") not in d["bank"]
    inter = invoice_doc.compose(q | {"gst_mode": "inter", "totals": q["totals"] | {"cgst": 0, "sgst": 0, "igst": 18000},
                                     "customer": {"name": "Gujarat Co", "state": "Gujarat"}}, {"name": "Kestrel"}, DETAILS)
    assert inter["rows"][1] == ("IGST @ 18%", 18000) and dict(inter["meta"])["Place of supply"] == "Gujarat (24)"
    unregistered = invoice_doc.compose(q | {"gst_mode": "none"}, {"name": "Kestrel"}, {})
    assert unregistered["title"] == "Invoice" and not unregistered["show_gst"] and not unregistered["bank"]
    assert invoice_pdf.render(q, {"name": "Kestrel"}, DETAILS)[:4] == b"%PDF"


async def test_details_signature_and_line_codes_reach_the_print_page_and_the_pdf(client):
    c, uid, _ = await owner_on("9873000001", "running")
    assert (await c.put("/api/quotes/settings", json=DETAILS)).status_code == 200
    assert (await c.put("/api/quotes/settings", json=DETAILS | {"pan": "bad"})).status_code == 422
    assert (await c.put("/api/quotes/settings", json=DETAILS | {"bank_ifsc": "HDFC1234"})).status_code == 422

    r = await c.post("/api/quotes/settings/signature", files={"file": ("sign.png", png(), "image/png")})
    assert r.status_code == 200, r.text
    first = (await db().quote_settings.find_one({"_id": uid}))["signature_file_id"]
    await c.put("/api/quotes/settings", json=DETAILS | {"signatory": "Asha Rao"})          # saving the details keeps it
    s = await db().quote_settings.find_one({"_id": uid})
    assert s["signature_file_id"] == first and s["signatory"] == "Asha Rao"
    await c.post("/api/quotes/settings/signature", files={"file": ("sign.png", png(), "image/png")})
    assert (await db().quote_settings.find_one({"_id": uid}))["signature_file_id"] != first
    assert not await db().files.count_documents({"_id": first})                            # the old one is gone
    assert (await c.post("/api/quotes/settings/signature", files={"file": ("s.txt", b"hello", "text/plain")})).status_code in (400, 415)

    q = (await c.post("/api/quotes", json=QUOTE)).json()
    stored = await db().quotes.find_one({"_id": q["id"]})
    assert stored["items"][0]["sac"] == "998314" and stored["items"][0]["description"].startswith("WhatsApp bot")
    assert (await c.post("/api/quotes", json=QUOTE | {"items": [QUOTE["items"][0] | {"sac": "99-83"}]})).status_code == 422

    page = (await c.get(f"/api/quotes/{q['id']}/print")).text
    for want in ("Quotation", "Not a tax invoice", "Kestrel Analytics Private Limited", "998314", "WhatsApp bot, booking flow",
                 "CGST @ 9%", "₹1,18,000", "Rupees One Lakh Eighteen Thousand Only", "HDFC0001234", "Authorised signatory",
                 "Asha Rao", "data:image/png;base64,", "AABCK1234M"):
        assert want in page, want

    pdf = await invoice_pdf.pdf_for(stored)
    assert pdf[:4] == b"%PDF"

    assert (await c.delete("/api/quotes/settings/signature")).status_code == 200
    assert "signature_file_id" not in await db().quote_settings.find_one({"_id": uid})
    assert "data:image/png;base64," not in (await c.get(f"/api/quotes/{q['id']}/print")).text
