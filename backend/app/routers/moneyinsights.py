"""
Customer risk and Getting paid (v2.2), and the sales history upload that feeds them. Read-only analysis: nothing here
sends a message or changes money owed; the WhatsApp check-in is a link the owner taps.

POST   /api/sales-upload               a sales sheet (multipart "file": CSV or Excel .xlsx, ≤ 10 MB, ≤ 20,000 rows)
GET    /api/sales-upload               the uploads so far
DELETE /api/sales-upload/{batch_id}    undo one upload (its rows go)
GET    /api/customer-risk              who the sales depend on (last 12 months) and regular customers who have gone quiet
GET    /api/getting-paid               how long customers really take to pay, and the money still owed by how late it is

Collections (sales_rows, sales_batches) are described in app/salesbook.py, which reads them. Money is stored in paise;
every response gives rupees. Every query is scoped to the business (ctx.ws) and bounded.

Reading a sheet. The header row is found in the first 30 rows (Tally puts the company name and period above it): it
needs a date column, a customer column (Customer / Party / Particulars / Name / Buyer …) and an amount column (Gross
Total / Invoice Value / Amount / Total / Value …, or a taxable value plus tax). Optional: GST (one tax column, or
IGST + CGST + SGST added up), invoice/voucher number, due date, paid/received on, phone, voucher type. Dates are read day
first (dd-mm-yyyy, dd/mm/yy, 1-Apr-25, Excel dates); a sheet is read month first only when its dates prove it (a
second number above 12 and never a first). Amounts may carry ₹, Rs, commas in any place and brackets for minus. A row
without a readable date, amount or customer is counted and reported, never guessed. Totals rows and blank rows are
passed over. A "Credit Note" / "Sales Return" voucher type is a negative row; receipts, payments, purchases, journals
and orders are not sales and are counted apart.

Duplicates: a row is skipped when this business already has it — the same invoice number in the same financial year
(Apr–Mar) with the same customer or an amount within ₹1, or the same date, customer (by name or phone) and amount to the
nearest rupee — from an earlier upload or from the hub's own invoices (counted as "already in the hub"). Two identical
lines inside one sheet both count (two bills on one day), unless they carry the same invoice number.
"""
import asyncio
import csv
import io
import math
import re
import statistics
import zipfile
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from itertools import chain, islice
from urllib.parse import quote as urlquote

from fastapi import APIRouter, Depends, HTTPException, Request
from pymongo.errors import DuplicateKeyError
from starlette.datastructures import UploadFile

from .. import salesbook
from ..cashflow import likelihood
from ..context import Ctx, feature, get_ctx, locked_detail
from ..db import IST, db, now
from ..salesbook import MAX_ROWS, customer_key, group_customers, is_walk_in, name_key, phone_key
from ..security import normalise_phone
from ..services import new_id, track
from .hub import wa_digits

router = APIRouter(prefix="/api")

MAX_MB = 10
MAX_UNZIPPED = 60 * 1024 * 1024        # an .xlsx is a zip: what it unpacks to is checked before it's opened
MAX_ZIP_ENTRIES = 2_000
MAX_COLS = 100
HEADER_SCAN = 30
MAX_RUPEES = 100_000_000               # ₹10 crore on one bill: anything bigger is a typo (same line as Money owed)
UPLOAD_FEATURES = ("customer_risk", "getting_paid", "gst_setaside", "win_back")
RISK_DAYS, HISTORY_DAYS = 365, 730
LOCK_MINUTES = 10
MAX_CELL = 40                          # a date or an amount is never longer: a longer cell isn't read, and never reaches a regex
MAX_TEXT = 200                         # names, numbers and column names are cut to this before anything else is done with them
SHEET_STALE_DAYS = 45                  # a sheet's unpaid rows aren't treated as late money once the upload is older than this


def today() -> date:
    """Today in India. Tests replace this to fix the date."""
    return now().astimezone(IST).date()


# ═════════════════════════ reading a sheet ═════════════════════════
def _norm(h) -> str:
    s = str(h if h is not None else "")[:MAX_TEXT].strip().lower().replace("₹", " ").replace(" ", " ")
    s = re.sub(r"[._:#*()\[\]{}/\\,'’\"-]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"( in)? (rs|inr|rupees)$", "", s).strip()
    return s


def _words(h: str) -> set[str]:
    return set(h.split())


_BAD_NAME = ("gstin", "gst", "phone", "mobile", "address", "state", "email", "code", "city", "pin", "contact", "item", "product", "group")
_NOT_AMOUNT = ("tax", "gst", "cgst", "sgst", "igst", "cess", "qty", "quantity", "rate", "discount", "paid", "received", "due", "balance",
               "outstanding", "taxable", "round", "no", "number", "date", "pending", "advance")
# field → (exact names in priority order, a looser test used when no exact name is there). Matched in FIELD_ORDER.
FIELDS = {
    "paid_on": (["paid on", "received on", "payment date", "paid date", "date of payment", "payment received on", "received date",
                 "receipt date", "date paid", "payment received date", "realised on", "realized on", "collection date", "date received"],
                lambda h: ("paid" in _words(h) or "received" in _words(h) or "payment" in _words(h)) and ("on" in _words(h) or "date" in _words(h))),
    "due_date": (["due date", "due on", "due by", "payment due", "payment due date", "due"],
                 lambda h: "due" in _words(h) and not _words(h) & {"amount", "amt", "days", "balance"}),
    "invoice_no": (["invoice no", "invoice number", "inv no", "invoice", "voucher no", "voucher number", "vch no", "bill no", "bill number",
                    "doc no", "document no", "document number", "invoice id", "invoice num", "inv number"],
                   lambda h: bool(_words(h) & {"invoice", "inv", "voucher", "vch", "bill"}) and bool(_words(h) & {"no", "number", "num", "id"})
                   and "ref" not in _words(h)),
    "phone": (["phone", "mobile", "mobile no", "phone no", "mobile number", "phone number", "contact", "contact no", "contact number",
               "whatsapp", "whatsapp no", "whatsapp number", "cell", "cell no"],
              lambda h: bool(_words(h) & {"phone", "mobile", "whatsapp", "cell"})),
    "voucher_type": (["voucher type", "vch type", "document type", "doc type", "invoice type", "transaction type", "type"],
                     lambda h: "voucher type" in h or "vch type" in h),
    "date": (["date", "invoice date", "inv date", "bill date", "voucher date", "vch date", "sale date", "sales date", "txn date",
              "transaction date", "document date", "doc date", "order date"],
             lambda h: "date" in _words(h) and not any(w in h for w in ("due", "paid", "payment", "receiv", "receipt", "birth", "dob",
                                                                         "delivery", "dispatch", "realis", "realiz"))),
    "customer": (["customer", "customer name", "party", "party name", "party a c name", "party ac name", "party account name", "party s name",
                  "partys name", "buyer", "buyer name", "particulars", "client", "client name", "name", "billed to", "bill to", "sold to",
                  "name of customer", "name of party", "name of buyer", "receiver name", "account name", "ledger name"],
                 lambda h: bool(_words(h) & {"customer", "party", "buyer", "client"}) and not any(w in h for w in _BAD_NAME)),
    "amount": (["gross total", "invoice value", "invoice amount", "invoice total", "bill amount", "bill value", "bill total", "total amount",
                "grand total", "total invoice value", "net amount", "net total", "amount", "total", "sale amount", "sales amount", "sale value",
                "sales value", "total value", "value"],
               lambda h: bool(_words(h) & {"total", "amount", "amt"}) and not _words(h) & set(_NOT_AMOUNT)),
    "taxable": (["taxable value", "taxable amount", "taxable", "assessable value", "taxable value of supply"],
                lambda h: "taxable" in h or "assessable" in h),
}
FIELD_ORDER = ("paid_on", "due_date", "invoice_no", "phone", "voucher_type", "date", "customer", "amount", "taxable")
TAX_TOTAL = ["gst", "tax", "tax amount", "total tax", "gst amount", "total gst", "tax total", "total tax amount", "gst total", "tax amt",
             "gst amt", "total gst amount", "gst value", "tax value"]
TAX_PARTS = ("igst", "cgst", "sgst", "utgst")


def _is_rate(h: str) -> bool:
    """"GST %", "CGST Rate", "Tax rate" are rates, not money. "Output CGST @ 9%" (a Tally ledger column) is money."""
    return "rate" in _words(h) or "percent" in h or "percentage" in h or (h.endswith("%") and not re.search(r"\d", h))


def _is_tax_id(h: str) -> bool:
    return "gstin" in h or bool(re.search(r"\bgst (no|number|reg|registration|uin)\b", h)) or "uin" in _words(h)


def match_columns(header: list) -> dict:
    """The sheet's header row → {field: column index}, with "gst" a list of indexes (one tax total, or IGST/CGST/SGST to add up)."""
    norms = [_norm(h) for h in list(header)[:MAX_COLS]]
    used: set[int] = set()
    found: dict = {}
    for field in FIELD_ORDER:
        exact, loose = FIELDS[field]
        pick = None
        for name in exact:
            pick = next((i for i, h in enumerate(norms) if i not in used and h == name), None)
            if pick is not None:
                break
        if pick is None:
            pick = next((i for i, h in enumerate(norms) if i not in used and h and loose(h)), None)
        if pick is not None:
            found[field] = pick
            used.add(pick)
    total = next((i for name in TAX_TOTAL for i, h in enumerate(norms) if i not in used and h == name), None)
    if total is not None:
        found["gst"] = [total]
    else:
        parts = [i for i, h in enumerate(norms) if i not in used and _words(h) & set(TAX_PARTS) and not _is_rate(h) and not _is_tax_id(h)]
        if parts:
            found["gst"] = parts
    return found


def _usable(cols: dict) -> bool:
    return "date" in cols and "customer" in cols and ("amount" in cols or "taxable" in cols)


MONTHS = {m: i + 1 for i, m in enumerate(("january", "february", "march", "april", "may", "june", "july", "august", "september",
                                           "october", "november", "december"))}


def _month(word: str) -> int | None:
    w = word.lower().rstrip(".")
    if len(w) < 3:
        return None
    return next((n for full, n in MONTHS.items() if full.startswith(w) or (w == "sept" and n == 9)), None)


def _year(y: str) -> int:
    return 2000 + int(y) if len(y) == 2 else int(y)


_TIME = re.compile(r"[T ]+\d{1,2}:\d{2}(:\d{2})?(\.\d+)?\s*([ap]\.?m\.?)?$", re.I)
_NUMERIC = re.compile(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{2}|\d{4})")


def parse_date(v, month_first: bool = False) -> str:
    """A cell → "YYYY-MM-DD", or "" when it isn't a date. Day first unless the sheet proved otherwise."""
    if v is None or isinstance(v, bool):
        return ""
    if isinstance(v, datetime):
        d = v.date()
    elif isinstance(v, date):
        d = v
    else:
        if isinstance(v, (int, float)):
            s = repr(v) if isinstance(v, float) else str(v)
        else:
            s = str(v).strip()
        if len(s) > MAX_CELL:
            return ""
        s = _TIME.sub("", s).strip()
        d = None
        try:
            if m := re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s):
                d = date(int(m[1]), int(m[2]), int(m[3]))
            elif m := _NUMERIC.fullmatch(s):
                a, b = int(m[1]), int(m[2])
                d = date(_year(m[3]), a, b) if month_first else date(_year(m[3]), b, a)
            elif m := re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?[-/ .,]+([a-z]{3,9}\.?)[-/ .,]+(\d{2}|\d{4})", s, re.I):
                mo = _month(m[2])
                d = date(_year(m[3]), mo, int(m[1])) if mo else None
            elif m := re.fullmatch(r"([a-z]{3,9}\.?)[-/ .]+(\d{1,2})(?:st|nd|rd|th)?,?[-/ .]+(\d{2}|\d{4})", s, re.I):
                mo = _month(m[1])
                d = date(_year(m[3]), mo, int(m[2])) if mo else None
            elif m := re.fullmatch(r"(20\d{2})(\d{2})(\d{2})", s):
                d = date(int(m[1]), int(m[2]), int(m[3]))
            elif m := re.fullmatch(r"(\d{5})(\.\d+)?", s):          # an Excel date number (days since 30 Dec 1899)
                n = int(m[1])
                d = date(1899, 12, 30) + timedelta(days=n) if 30_000 <= n <= 80_000 else None
        except (ValueError, OverflowError):
            d = None
        if d is None:
            return ""
    if not (2000 <= d.year <= today().year + 1):
        return ""
    return d.isoformat()


def parse_amount(v) -> int | None:
    """A cell → paise, or None. ₹ / Rs / INR, Indian commas ("1,23,456.00"), spaces, Dr/Cr marks and (brackets) or a
    minus sign for negative are understood. Nothing else is guessed."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        if isinstance(v, float) and not math.isfinite(v):
            return None
        s = repr(v) if isinstance(v, float) else str(v)
    else:
        s = str(v).strip()
    if len(s) > MAX_CELL:
        return None
    s = s.replace("₹", "").replace(" ", "").replace(",", "").replace(" ", "")
    s = re.sub(r"(?i)^(rs\.?|inr)", "", s)
    s = re.sub(r"(?i)(dr|cr|inr|/-)\.?$", "", s)
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1]
    if s.startswith("-"):
        neg, s = True, s[1:]
    elif s.endswith("-"):
        neg, s = True, s[:-1]
    s = re.sub(r"(?i)^(rs\.?|inr)", "", s)
    if not re.fullmatch(r"\d+(\.\d+)?(e[+-]?\d+)?", s, re.I):
        return None
    try:
        paise = int((Decimal(s) * 100).quantize(Decimal(1)))
    except (InvalidOperation, ValueError, OverflowError):
        return None
    if paise > MAX_RUPEES * 100:
        return None
    return -paise if neg else paise


def _text(v, limit: int) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, (datetime, date)):
        v = v.isoformat()[:10]
    return re.sub(r"\s+", " ", str(v)[:MAX_TEXT]).strip()[:limit]


def _phone(v) -> str:
    raw = _text(v, 24)
    if not raw:
        return ""
    try:
        return normalise_phone(raw)
    except HTTPException:
        return ""


_TOTAL_WORDS = ("total", "grand total", "sub total", "subtotal", "totals", "net total", "closing balance", "opening balance",
                "carried over", "brought forward", "c f", "b f", "page total")
_NOT_SALES = ("receipt", "payment", "purchase", "journal", "contra", "debit note", "sales order", "purchase order", "delivery note",
              "quotation", "proforma", "pro forma", "stock journal", "memorandum", "rejection")
_RETURNS = ("credit note", "sales return", "return")


def _is_total(row: list) -> bool:
    first = next((c for c in row if c is not None and str(c).strip()), None)
    if first is None:
        return False
    s = _norm(first)
    return s in _TOTAL_WORDS or s.startswith("total ") or s.startswith("grand total")


def _month_first(rows: list[list], cols: dict) -> bool:
    """A sheet is read month first only when it proves it: some date has a second number above 12 and none a first."""
    first_big = second_big = False
    for r in rows:
        for f in ("date", "due_date", "paid_on"):
            i = cols.get(f)
            if i is None or i >= len(r) or not isinstance(r[i], str) or len(r[i].strip()) > MAX_CELL:
                continue
            m = _NUMERIC.fullmatch(_TIME.sub("", r[i].strip()).strip())
            if m:
                first_big |= int(m[1]) > 12
                second_big |= int(m[2]) > 12
    return second_big and not first_big


def read_rows(rows: list[list], cols: dict, header_line: int) -> dict:
    """Data rows (after the header) → the sales in them and what couldn't be read. `header_line` is the header's row number
    in the sheet (1-based), so skipped rows can be named the way the owner sees them."""
    month_first = _month_first(rows, cols)
    out, reasons, examples = [], {"no_date": 0, "no_amount": 0, "no_customer": 0}, []
    not_sales = 0
    latest = (today() + timedelta(days=31)).isoformat()

    def cell(r, f):
        i = cols.get(f)
        return r[i] if i is not None and i < len(r) else None

    for n, r in enumerate(rows, start=header_line + 1):
        if not any(c is not None and str(c).strip() for c in r):
            continue
        if _is_total(r):
            continue
        vtype = _norm(cell(r, "voucher_type"))
        if vtype and any(w in vtype for w in _NOT_SALES):
            not_sales += 1
            continue
        day = parse_date(cell(r, "date"), month_first)
        if day and day > latest:
            day = ""
        gst = None
        if cols.get("gst"):
            parts = [parse_amount(r[i]) if i < len(r) else None for i in cols["gst"]]
            if any(p is not None for p in parts):
                gst = sum(p for p in parts if p is not None)
        if "amount" in cols:
            amount = parse_amount(cell(r, "amount"))
        else:
            taxable = parse_amount(cell(r, "taxable"))
            amount = None if taxable is None else taxable + (gst or 0)
        customer = _text(cell(r, "customer"), 80)
        reason = "no_date" if not day else "no_amount" if not amount else "no_customer" if len(customer) < 2 else None
        if reason:
            reasons[reason] += 1
            if len(examples) < 10:
                examples.append(n)
            continue
        if vtype and any(w in vtype for w in _RETURNS):
            amount = -abs(amount)
        if gst is not None:
            gst = abs(gst) if amount > 0 else -abs(gst)   # sheets show tax with either sign; it goes the way the bill goes
            if abs(gst) > abs(amount):
                gst = None                                # tax bigger than the bill: the column isn't what we think, so not read
        out.append({"date": day, "customer": customer, "phone": _phone(cell(r, "phone")), "amount": amount, "gst": gst,
                    "invoice_no": _text(cell(r, "invoice_no"), 40), "due_date": parse_date(cell(r, "due_date"), month_first),
                    "paid_on": parse_date(cell(r, "paid_on"), month_first), "line": n})
    return {"rows": out, "reasons": reasons, "examples": examples, "not_sales": not_sales, "month_first": month_first}


def add_up_lines(rows: list[dict]) -> tuple[list[dict], int, int]:
    """Item-wise sheets put one bill on several lines. Lines with the same invoice number, customer and date are one bill:
    their amounts (and GST) are added up into one row. Lines without an invoice number are never joined.
    Returns (rows, bills that were added up, lines in them)."""
    out: list[dict] = []
    at: dict[tuple, int] = {}
    counts: dict[tuple, int] = {}
    for r in rows:
        inv = _inv(r["invoice_no"])
        if not inv:
            out.append(r)
            continue
        k = (inv, name_key(r["customer"]), r["date"])
        counts[k] = counts.get(k, 0) + 1
        if k not in at:
            at[k] = len(out)
            out.append(dict(r))
            continue
        b = out[at[k]]
        b["amount"] += r["amount"]
        b["gst"] = None if b["gst"] is None and r["gst"] is None else (b["gst"] or 0) + (r["gst"] or 0)
        for f in ("phone", "due_date", "paid_on"):
            b[f] = b[f] or r[f]
    for b in out:
        if b["gst"] is not None and (abs(b["gst"]) > abs(b["amount"]) or (b["gst"] < 0) != (b["amount"] < 0) and b["gst"] != 0):
            b["gst"] = None
    joined = [n for n in counts.values() if n > 1]
    return [b for b in out if b["amount"] != 0], len(joined), sum(joined)


def _find_header(head: list[list]) -> tuple[int, dict] | None:
    for i, r in enumerate(head):
        cols = match_columns(r)
        if _usable(cols):
            return i, cols
    return None


def _too_many() -> HTTPException:
    return HTTPException(400, f"This sheet has more than {MAX_ROWS:,} rows. Upload a shorter period — one year at a time works well.")


def _take(first: list[list], it) -> list[list]:
    """The data rows after the header — `first` (already read with the header) and the rest from `it`: at most MAX_ROWS
    that aren't blank (a Grand Total line may follow them); blank lines are passed over, up to a point."""
    rows, filled = [], 0
    for r in chain(first, islice(it, MAX_ROWS * 3)):
        r = list(r)[:MAX_COLS]
        if any(c is not None and str(c).strip() for c in r):
            filled += 1
            if filled > MAX_ROWS + 1:
                raise _too_many()
        rows.append(r)
    if next(it, None) is not None:
        raise _too_many()
    return rows


def _decode(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            raise HTTPException(400, "We couldn't read that file. Open it in Excel, save it as .xlsx or CSV, and upload that.")
    if b"\x00" in data[:8192]:
        raise HTTPException(400, "That isn't a CSV or Excel file. Upload the sales sheet as .xlsx or .csv.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")        # older Excel "CSV" saves
    sample = text[:8192]
    if sample and sum(1 for ch in sample if ord(ch) < 32 and ch not in "\t\r\n") > len(sample) * 0.01:
        raise HTTPException(400, "That isn't a CSV or Excel file. Upload the sales sheet as .xlsx or .csv.")
    return text


def _csv_rows(data: bytes) -> tuple[list[list], dict, int, list]:
    text = _decode(data)
    sample = "\n".join(line[:2000] for line in text[:65536].splitlines()[:40])     # bounded: the sniffer's regexes see little
    try:
        delim = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        delim = ","
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delim)
    try:
        head = [list(r)[:MAX_COLS] for r in islice(reader, HEADER_SCAN)]
        found = _find_header(head)
        if not found:
            return [], {}, 0, []
        at, cols = found
        rows = _take(head[at + 1:], reader)
    except csv.Error:
        raise HTTPException(400, "We couldn't read that CSV file. Open it in Excel, save it again as .xlsx or CSV, and upload that.")
    return rows, cols, at + 1, head[at]


def _xlsx_rows(data: bytes) -> tuple[list[list], dict, int, list, str]:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        infos = z.infolist()
    except (zipfile.BadZipFile, ValueError, OSError):
        raise HTTPException(400, "We couldn't open that Excel file. Save it again as .xlsx (or CSV) and upload that.")
    if len(infos) > MAX_ZIP_ENTRIES or sum(max(i.file_size, 0) for i in infos) > MAX_UNZIPPED:
        raise HTTPException(400, "That Excel file unpacks to more than 60 MB. Save just the sales sheet (one year at a time) and upload that.")
    if not any(i.filename.startswith("xl/") for i in infos):
        raise HTTPException(400, "That isn't an Excel (.xlsx) file. Upload the sales sheet as .xlsx or .csv.")
    try:
        import openpyxl
    except ImportError:
        raise HTTPException(415, "Excel files can't be read on this server just now. Save the sheet as CSV and upload that.")
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:  # noqa: BLE001  a broken file is the owner's to fix, not a crash
        raise HTTPException(400, "We couldn't open that Excel file. Save it again as .xlsx (or CSV) and upload that.")
    try:
        for sheet in wb.worksheets[:10]:
            it = sheet.iter_rows(max_col=MAX_COLS, values_only=True)
            head = [list(r) for r in islice(it, HEADER_SCAN)]
            found = _find_header(head)
            if found:
                at, cols = found
                return _take(head[at + 1:], it), cols, at + 1, head[at], sheet.title
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "We couldn't read that Excel file. Save it again as .xlsx (or CSV) and upload that.")
    finally:
        wb.close()
    return [], {}, 0, [], ""


def parse_sheet(data: bytes) -> dict:
    """Bytes of a CSV or .xlsx → {rows, reasons, examples, not_sales, month_first, columns, sheet}. CPU only (no database)."""
    sheet = ""
    if data.startswith(b"PK\x03\x04"):
        rows, cols, header_line, header, sheet = _xlsx_rows(data)
    elif data.startswith(b"\xd0\xcf\x11\xe0"):
        raise HTTPException(400, "This is an old Excel file (.xls). Open it in Excel, save it as .xlsx or CSV, and upload that.")
    elif data.startswith((b"%PDF", b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"PK", b"\x1f\x8b", b"Rar!", b"7z\xbc\xaf")):
        raise HTTPException(400, "That isn't a CSV or Excel file. Upload the sales sheet as .xlsx or .csv.")
    else:
        rows, cols, header_line, header = _csv_rows(data)
    if not cols:
        raise HTTPException(400, "We couldn't find the column names. Put Date, Customer (or Party) and Amount (or Gross Total) as "
                                 "column names in one row near the top, and upload again.")
    out = read_rows(rows, cols, header_line)
    out["rows"], out["added_up_bills"], out["added_up_lines"] = add_up_lines(out["rows"])
    names = lambda i: _text(header[i], 60) if i < len(header) else ""  # noqa: E731
    out["columns"] = {f: ([names(i) for i in cols[f]] if f == "gst" else names(cols[f])) for f in cols}
    out["sheet"] = sheet
    return out


# ═════════════════════════ duplicates ═════════════════════════
def _fy(day: str) -> int:
    d = date.fromisoformat(day)
    return d.year if d.month >= 4 else d.year - 1


def _inv(no: str) -> str:
    return re.sub(r"\s+", "", (no or "").upper())


def _rupee(paise: int) -> int:
    return (paise + 50) // 100 if paise >= 0 else -((-paise + 50) // 100)


class Seen:
    """What this business already has (earlier uploads and hub invoices), to skip a sheet's rows that repeat it."""

    def __init__(self):
        self.recs: list[dict] = []
        self.by_day: dict[tuple, list[int]] = {}
        self.by_inv: dict[tuple, list[int]] = {}
        self.used: set[int] = set()

    def add(self, day: str, customer: str, phone: str, paise: int, invoice_no: str, where: str, used: bool = False) -> None:
        i = len(self.recs)
        nk, pk = name_key(customer), phone_key(phone)
        self.recs.append({"nk": nk, "pk": pk, "paise": paise, "where": where})
        amt = _rupee(paise)
        self.by_day.setdefault((day, "n", nk, amt), []).append(i)
        if pk:
            self.by_day.setdefault((day, "p", pk, amt), []).append(i)
        if _inv(invoice_no):
            self.by_inv.setdefault((_fy(day), _inv(invoice_no)), []).append(i)
        if used:
            self.used.add(i)

    def match(self, row: dict) -> str | None:
        nk, pk = name_key(row["customer"]), phone_key(row["phone"])
        if _inv(row["invoice_no"]):
            for i in self.by_inv.get((_fy(row["date"]), _inv(row["invoice_no"])), []):
                rec = self.recs[i]
                if abs(rec["paise"] - row["amount"]) <= 100 or rec["nk"] == nk or (pk and rec["pk"] == pk):
                    self.used.add(i)
                    return rec["where"]
        amt = _rupee(row["amount"])
        for k in ((row["date"], "n", nk, amt), (row["date"], "p", pk, amt) if pk else None):
            for i in self.by_day.get(k, []) if k else []:
                if i not in self.used:
                    self.used.add(i)
                    return self.recs[i]["where"]
        return None


async def _seen(ws: str) -> Seen:
    s = Seen()
    async for r in db().sales_rows.find({"ws": ws}, {"date": 1, "customer": 1, "phone": 1, "amount": 1, "invoice_no": 1}).limit(MAX_ROWS):
        try:
            s.add(r["date"], r.get("customer", ""), r.get("phone", ""), int(r.get("amount") or 0), r.get("invoice_no", ""), "upload")
        except (KeyError, ValueError, TypeError):
            continue
    fields = {"kind": 1, "customer": 1, "totals": 1, "number": 1, "status": 1, "created_at": 1}
    async for q in db().quotes.find({"ws": ws, "kind": {"$in": ["invoice", "credit_note"]}}, fields).sort("created_at", -1).limit(MAX_ROWS):
        if str(q.get("status") or "").lower() in salesbook.CANCELLED:
            continue
        day = salesbook.ist_date(q.get("created_at"))
        if not day:
            continue
        sign = -1 if q.get("kind") == "credit_note" else 1
        cust = q.get("customer") or {}
        paise = sign * int(round(float((q.get("totals") or {}).get("total") or 0) * 100))
        s.add(day.isoformat(), str(cust.get("name") or ""), str(cust.get("phone") or ""), paise, str(q.get("number") or ""), "hub")
    return s


# ═════════════════════════ upload endpoints ═════════════════════════
async def upload_ctx(ctx: Ctx = Depends(get_ctx)) -> Ctx:
    """Owner or manager, on a plan with Customer risk, Getting paid or the GST set-aside (all of them read these sales)."""
    if not any(ctx.has(f) for f in UPLOAD_FEATURES):
        raise HTTPException(403, locked_detail("customer_risk"))
    if not ctx.at_least("manager"):
        raise HTTPException(403, "Ask your manager or the business owner to do this.")
    return ctx


async def _lock(ws: str) -> None:
    await db().sales_upload_locks.delete_one({"_id": ws, "at": {"$lt": now() - timedelta(minutes=LOCK_MINUTES)}})
    try:
        await db().sales_upload_locks.insert_one({"_id": ws, "at": now()})
    except DuplicateKeyError:
        raise HTTPException(409, "Another sales sheet is still being read. Try again in a minute.")


def _inr(rupees: float) -> str:
    """Whole rupees with Indian grouping: ₹1,65,000."""
    whole = str(int(round(abs(rupees))))
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        whole = ",".join(([head] if head else []) + groups) + "," + tail
    return f"{'−' if rupees < 0 else ''}₹{whole}"


def _short(rupees: float) -> str:
    """₹ in the way owners say it, for estimates ("about …"): ₹85,000 · ₹3.4 L · ₹1.2 Cr. Amounts that are known exactly
    (what's owed, what was uploaded) use _inr instead, so nothing is rounded down."""
    r = abs(rupees)
    sign = "−" if rupees < 0 else ""
    if r >= 1e7:
        return f"{sign}₹{r / 1e7:.1f}".rstrip("0").rstrip(".") + " Cr"
    if r >= 1e5:
        return f"{sign}₹{r / 1e5:.1f}".rstrip("0").rstrip(".") + " L"
    return _inr(rupees)


def _day(iso: str) -> str:
    """"2026-10-21" → "21 Oct" (with the year when it isn't this year)."""
    d = date.fromisoformat(iso)
    return f"{d.day} {d.strftime('%b')}" + (f" {d.year}" if d.year != today().year else "")


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


@router.post("/sales-upload")
async def upload_sales(request: Request, ctx: Ctx = Depends(upload_ctx)):
    form = await request.form()
    try:
        f = form.get("file")
        if not isinstance(f, UploadFile):
            raise HTTPException(400, "Choose an Excel (.xlsx) or CSV file.")
        limit = MAX_MB * 1024 * 1024
        data = await f.read(limit + 1)
        filename = (f.filename or "sales").strip()[:120]
    finally:
        await form.close()
    if len(data) > limit:
        raise HTTPException(413, f"That file is bigger than {MAX_MB} MB. Upload a shorter period — one year at a time works well.")
    if not data.strip():
        raise HTTPException(400, "That file is empty.")
    parsed = await asyncio.to_thread(parse_sheet, data)

    await _lock(ctx.ws)
    try:
        seen = await _seen(ctx.ws)
        fresh, dup_upload, dup_hub, dup_sheet = [], 0, 0, 0
        for r in parsed["rows"]:
            where = seen.match(r)
            if where == "hub":
                dup_hub += 1
            elif where == "sheet":
                dup_sheet += 1
            elif where:
                dup_upload += 1
            else:
                fresh.append(r)
                seen.add(r["date"], r["customer"], r["phone"], r["amount"], r["invoice_no"], "sheet", used=True)
        have = await db().sales_rows.count_documents({"ws": ctx.ws})
        if have + len(fresh) > MAX_ROWS:
            raise HTTPException(400, f"You can keep up to {MAX_ROWS:,} uploaded sales. You have {have:,}, and this sheet adds {len(fresh):,}. "
                                     "Remove an older upload first, or upload a shorter period.")
        batch_id = None
        if fresh:
            batch_id = new_id()
            at = now()
            docs = [{"_id": new_id(), "ws": ctx.ws, "batch_id": batch_id, "date": r["date"], "customer": r["customer"], "phone": r["phone"],
                     "amount": r["amount"], "gst": r["gst"], "invoice_no": r["invoice_no"], "due_date": r["due_date"], "paid_on": r["paid_on"],
                     "created_at": at} for r in fresh]
            try:
                for i in range(0, len(docs), 1000):
                    await db().sales_rows.insert_many(docs[i:i + 1000])
                await db().sales_batches.insert_one({
                    "_id": batch_id, "ws": ctx.ws, "filename": filename, "rows": len(docs), "from": min(r["date"] for r in fresh),
                    "to": max(r["date"] for r in fresh), "total": sum(r["amount"] for r in fresh), "created_by": ctx.actor, "created_at": at,
                    "columns": parsed["columns"], "has_paid_on": "paid_on" in parsed["columns"], "sheet": parsed["sheet"]})
            except Exception:
                await db().sales_rows.delete_many({"ws": ctx.ws, "batch_id": batch_id})
                raise
    finally:
        await db().sales_upload_locks.delete_one({"_id": ctx.ws})
    await track("sales_upload", ctx.ws, rows=len(fresh), skipped=sum(parsed["reasons"].values()), duplicates=dup_upload + dup_hub + dup_sheet)

    unreadable = sum(parsed["reasons"].values())
    lines = []
    if fresh:
        lines.append(f"{_plural(len(fresh), 'sale')} added — {_inr(sum(r['amount'] for r in fresh) / 100)} in all.")
    else:
        lines.append("No new sales were added.")
    if parsed["reasons"]["no_date"] + parsed["reasons"]["no_amount"]:
        lines.append(f"{_plural(parsed['reasons']['no_date'] + parsed['reasons']['no_amount'], 'row')} skipped — no date or amount we could read.")
    if parsed["added_up_bills"]:
        lines.append(f"{parsed['added_up_lines']:,} lines with the same invoice number, customer and date were added up into "
                     f"{_plural(parsed['added_up_bills'], 'bill')}.")
    if parsed["reasons"]["no_customer"]:
        lines.append(f"{_plural(parsed['reasons']['no_customer'], 'row')} skipped — no customer name.")
    if dup_hub:
        lines.append(f"{_plural(dup_hub, 'sale')} already in the hub as invoices — not counted twice.")
    if dup_upload:
        lines.append(f"{_plural(dup_upload, 'sale')} already uploaded before — not counted twice.")
    if dup_sheet:
        lines.append(f"{_plural(dup_sheet, 'row')} repeat an invoice number already in this sheet — counted once.")
    if parsed["not_sales"]:
        lines.append(f"{_plural(parsed['not_sales'], 'row')} left out — receipts, payments or other entries that aren't sales.")
    return {"batch_id": batch_id, "rows": len(fresh), "skipped_unreadable": unreadable, "skipped_reasons": parsed["reasons"],
            "skipped_examples": parsed["examples"], "skipped_duplicates": dup_upload + dup_hub + dup_sheet, "skipped_in_hub": dup_hub,
            "skipped_repeated_in_sheet": dup_sheet, "added_up_bills": parsed["added_up_bills"], "added_up_lines": parsed["added_up_lines"],
            "skipped_not_sales": parsed["not_sales"],
            "from": min((r["date"] for r in fresh), default=None), "to": max((r["date"] for r in fresh), default=None),
            "total": round(sum(r["amount"] for r in fresh) / 100, 2), "customers": len({customer_key(r["customer"], r["phone"]) for r in fresh}),
            "columns_found": parsed["columns"], "sheet": parsed["sheet"], "dates_read": "month first" if parsed["month_first"] else "day first",
            "lines": lines}


def _batch_view(b: dict) -> dict:
    return {"id": b["_id"], "filename": b.get("filename", ""), "rows": b.get("rows", 0), "from": b.get("from"), "to": b.get("to"),
            "total": round(int(b.get("total") or 0) / 100, 2), "has_paid_on": bool(b.get("has_paid_on")), "columns": b.get("columns") or {},
            "created_at": b["created_at"].isoformat() if isinstance(b.get("created_at"), datetime) else None}


@router.get("/sales-upload")
async def list_uploads(ctx: Ctx = Depends(upload_ctx)):
    batches = [_batch_view(b) async for b in db().sales_batches.find({"ws": ctx.ws}).sort("created_at", -1).limit(200)]
    return {"batches": batches, "rows": await db().sales_rows.count_documents({"ws": ctx.ws}), "max_rows": MAX_ROWS, "max_mb": MAX_MB}


@router.delete("/sales-upload/{batch_id}")
async def delete_upload(batch_id: str, ctx: Ctx = Depends(upload_ctx)):
    b = await db().sales_batches.find_one({"_id": batch_id, "ws": ctx.ws}, {"_id": 1})
    if not b:
        raise HTTPException(404, "Not found")
    r = await db().sales_rows.delete_many({"ws": ctx.ws, "batch_id": batch_id})
    await db().sales_batches.delete_one({"_id": batch_id, "ws": ctx.ws})
    await track("sales_upload_removed", ctx.ws, rows=r.deleted_count)
    return {"ok": True, "removed": r.deleted_count}


# ═════════════════════════ customer risk ═════════════════════════
def _concentration(biggest: float, top3: float) -> str:
    if biggest >= 25 or top3 >= 50:
        return "high"
    if biggest >= 15 or top3 >= 35:
        return "medium"
    return "low"


def _concentration_line(level: str, shares: list[float]) -> str:
    if not shares:
        return ""
    cum = [sum(shares[:k]) for k in range(1, min(3, len(shares)) + 1)]
    if level != "low":
        one, many = (25, 50) if level == "high" else (15, 35)
        k = 1 if shares[0] >= one else next((i + 1 for i, c in enumerate(cum) if i >= 1 and c >= many), len(cum))
        pct = round(cum[k - 1])
        return f"1 customer is {pct}% of your sales." if k == 1 else f"{k} customers are {pct}% of your sales."
    if len(shares) < 3:
        return f"Your biggest customer is {round(shares[0])}% of your sales."
    return f"Your biggest customer is {round(shares[0])}% of your sales, and your top 3 are {round(cum[2])}%."


def _checkin(name: str, phone: str, business: str) -> tuple[str, str]:
    text = f"Hello {name}, it's been a while! Is there anything you need from {business} this month?"
    digits = wa_digits(phone) if phone else ""
    return text, f"https://wa.me/{digits}?text={urlquote(text)}" if digits else f"https://wa.me/?text={urlquote(text)}"


def _list_value_12m(c: dict, start: date, t: date) -> float:
    """A Customers-list total in the last 12 months (an estimate): all of it when their buying is inside the window, else
    the share of their buying span that falls inside it."""
    last = c["last_purchase"]
    if not last or last < start.isoformat() or last > t.isoformat():
        return 0.0
    first = c["first_purchase"] or last
    if first >= start.isoformat():
        return c["total_value"]
    span = (date.fromisoformat(last) - date.fromisoformat(first)).days
    inside = (date.fromisoformat(last) - start).days
    return c["total_value"] * inside / span if span > 0 else c["total_value"]


@router.get("/customer-risk")
async def customer_risk(ctx: Ctx = Depends(feature("customer_risk", "manager"))):
    t = today()
    start12 = t - timedelta(days=RISK_DAYS - 1)
    start24 = t - timedelta(days=HISTORY_DAYS - 1)
    rows = [r for r in await salesbook.history(ctx.ws, since=start24) if r["date"] <= t.isoformat()]
    listed = await salesbook.customers_without_history(ctx.ws, rows)
    b = await db().businesses.find_one({"owner_id": ctx.ws}, {"name": 1}) or {}
    business = (b.get("name") or "").strip() or "us"
    sources = {"invoices": sum(r["source"] == "invoice" for r in rows), "razorpay": sum(r["source"] == "razorpay" for r in rows),
               "upload": sum(r["source"] == "upload" for r in rows), "customers_list": len(listed)}

    everyone = group_customers(rows)
    groups = [g for g in everyone if not is_walk_in(g["customer"])]
    in12 = lambda g: sum(r["amount"] for r in g["rows"] if r["date"] >= start12.isoformat())  # noqa: E731
    # returns and credit notes come off each customer's own sales; a customer whose returns are bigger than their sales in
    # the window counts as zero — never as a minus that shrinks everyone else's total (and inflates their shares)
    walk_in_12m = max(sum(in12(g) for g in everyone if is_walk_in(g["customer"])), 0.0)
    per: list[dict] = []
    netted_out = 0
    for g in groups:
        amt = in12(g)
        if amt > 0:
            per.append({"name": g["customer"], "phone": g["phone"], "amount": amt, "estimate": False})
        elif any(r["date"] >= start12.isoformat() for r in g["rows"]):
            netted_out += 1
    list_12m = 0.0
    for c in listed:
        if is_walk_in(c["customer"]):
            continue
        v = _list_value_12m(c, start12, t)
        if v > 0:
            list_12m += v
            per.append({"name": c["customer"], "phone": c["phone"], "amount": v, "estimate": True})
    total_12m = sum(x["amount"] for x in per) + walk_in_12m      # customers above (list estimates included) + cash and walk-in
    per.sort(key=lambda x: (-x["amount"], x["name"].lower()))

    sale_rows = sum(1 for r in rows if r["amount"] > 0) + sum(c["purchases"] or 1 for c in listed if not is_walk_in(c["customer"]))
    customer_count = len(groups) + sum(1 for c in listed if not is_walk_in(c["customer"]))
    enough = sale_rows >= 10 and customer_count >= 3 and total_12m > 0 and bool(per)

    out = {"as_of": t.isoformat(), "window_from": start12.isoformat(), "sources": sources, "enough_data": enough,
           "sales_counted": sale_rows, "customers_counted": customer_count, "business": business}
    if not enough:
        return {**out, "total_12m": round(max(total_12m, 0), 2), "customers_12m": len(per), "top": [], "how_many_make_80": None,
                "biggest_share_pct": None, "top3_share_pct": None, "concentration": None, "line": "", "quiet": [], "quiet_count": 0,
                "at_risk_yearly": 0, "quiet_line": "", "walk_in_12m": round(walk_in_12m, 2), "walk_in_share_pct": None,
                "includes_estimates": list_12m > 0, "returns_bigger_than_sales": netted_out}

    shares = [x["amount"] / total_12m * 100 for x in per]
    run, k80 = 0.0, None
    for i, x in enumerate(per):
        run += x["amount"]
        if run >= total_12m * 0.8 - 0.005:
            k80 = i + 1
            break
    biggest, top3 = shares[0], sum(shares[:3])
    level = _concentration(biggest, top3)
    top = [{"name": x["name"], "phone": x["phone"], "amount": round(x["amount"], 2), "share_pct": round(s, 1), "estimate": x["estimate"]}
           for x, s in zip(per[:10], shares[:10])]

    # gone quiet: regular customers well past their usual gap
    quiet = []
    for g in groups:
        days = sorted({r["date"] for r in g["rows"] if r["amount"] > 0})
        if len(days) < 3:
            continue
        ds = [date.fromisoformat(d) for d in days]
        gap = statistics.median((b2 - a).days for a, b2 in zip(ds, ds[1:]))
        since = (t - ds[-1]).days
        if not (since > max(2 * gap, gap + 30) and since > 45):
            continue
        sales = sum(r["amount"] for r in g["rows"])
        yearly = max(sales, 0) / max((t - ds[0]).days, 90) * 365
        quiet.append({"name": g["customer"], "phone": g["phone"], "last_purchase": days[-1], "usual_gap_days": round(gap),
                      "days_since": since, "purchases": len(days), "yearly_value": round(yearly), "from_list": False})
    for c in listed:
        if is_walk_in(c["customer"]) or not c["last_purchase"]:
            continue
        regular = c["purchases"] >= 3 or c["reorder_days"]
        first, last = c["first_purchase"], c["last_purchase"]
        gap = None
        if c["purchases"] >= 2 and first and first < last:
            gap = (date.fromisoformat(last) - date.fromisoformat(first)).days / (c["purchases"] - 1)
        elif c["reorder_days"]:
            gap = float(c["reorder_days"])
        if not regular or not gap:
            continue
        since = (t - date.fromisoformat(last)).days
        if not (since > max(2 * gap, gap + 30) and since > 45):
            continue
        if first:
            yearly = c["total_value"] / max((t - date.fromisoformat(first)).days, 90) * 365
        else:
            yearly = c["total_value"] / max(c["purchases"], 1) * 365 / gap
        quiet.append({"name": c["customer"], "phone": c["phone"], "last_purchase": last, "usual_gap_days": round(gap), "days_since": since,
                      "purchases": c["purchases"], "yearly_value": round(yearly), "from_list": True})
    quiet.sort(key=lambda q: (-q["yearly_value"], q["name"].lower()))
    for q in quiet:
        q["message"], q["whatsapp_link"] = _checkin(q["name"], q["phone"], business)
    at_risk = sum(q["yearly_value"] for q in quiet)
    if quiet:
        quiet_line = (f"{_plural(len(quiet), 'regular customer')} {'has' if len(quiet) == 1 else 'have'} gone quiet — "
                      f"about {_short(at_risk)} a year (estimate).")
    else:
        regulars = sum(1 for g in groups if len({r['date'] for r in g['rows'] if r['amount'] > 0}) >= 3)
        quiet_line = ("None of your regular customers has gone quiet." if regulars
                      else "No customer has bought 3 times yet, so there's no usual gap to compare with.")

    return {**out, "total_12m": round(total_12m, 2), "customers_12m": len(per), "top": top, "how_many_make_80": k80,
            "biggest_share_pct": round(biggest, 1), "top3_share_pct": round(top3, 1), "concentration": level,
            "line": _concentration_line(level, shares), "quiet": quiet[:50], "quiet_count": len(quiet), "at_risk_yearly": round(at_risk),
            "quiet_line": quiet_line, "walk_in_12m": round(walk_in_12m, 2),
            "walk_in_share_pct": round(walk_in_12m / total_12m * 100, 1) if walk_in_12m > 0 else None, "includes_estimates": list_12m > 0,
            "returns_bigger_than_sales": netted_out}


# ═════════════════════════ getting paid ═════════════════════════
def _bucket(days_late: int) -> str:
    if days_late <= 0:
        return "not_due"
    if days_late <= 30:
        return "d1_30"
    if days_late <= 60:
        return "d31_60"
    if days_late <= 90:
        return "d61_90"
    return "d90_plus"


@router.get("/getting-paid")
async def getting_paid(ctx: Ctx = Depends(feature("getting_paid", "manager"))):
    t = today()
    since = t - timedelta(days=RISK_DAYS - 1)
    b = await db().businesses.find_one({"owner_id": ctx.ws}, {"money": 1}) or {}
    terms = (b.get("money") or {}).get("payment_terms_days")
    terms = int(terms) if isinstance(terms, (int, float)) and not isinstance(terms, bool) else None

    paid: list[dict] = []        # {customer, phone, amount (₹), days_to_pay, days_late|None, paid_on, source}
    dues = [d async for d in db().dues.find({"ws": ctx.ws, "status": "paid", "paid_at": {"$gte": datetime.combine(since, time(0), tzinfo=IST)}},
                                            {"customer": 1, "phone": 1, "amount": 1, "original_amount": 1, "due_date": 1, "paid_at": 1,
                                             "created_at": 1, "invoice_id": 1}).sort("paid_at", -1).limit(MAX_ROWS)]
    inv_ids = list({d["invoice_id"] for d in dues if d.get("invoice_id")})
    inv_day: dict[str, date] = {}
    for i in range(0, len(inv_ids), 500):
        async for q in db().quotes.find({"_id": {"$in": inv_ids[i:i + 500]}, "ws": ctx.ws}, {"created_at": 1}):
            day = salesbook.ist_date(q.get("created_at"))
            if day:
                inv_day[q["_id"]] = day
    for d in dues:
        paid_day = salesbook.ist_date(d.get("paid_at"))
        start = inv_day.get(d.get("invoice_id")) or salesbook.ist_date(d.get("created_at"))
        if not paid_day or not start or paid_day < since or paid_day > t:
            continue
        try:
            due_day = date.fromisoformat(d.get("due_date") or "")
        except ValueError:
            due_day = None
        paid.append({"customer": d.get("customer", ""), "phone": d.get("phone", ""), "amount": int(d.get("original_amount") or d.get("amount") or 0) / 100,
                     "days_to_pay": max((paid_day - start).days, 0), "days_late": max((paid_day - due_day).days, 0) if due_day else None,
                     "paid_on": paid_day.isoformat(), "source": "money owed"})
    async for r in db().sales_rows.find({"ws": ctx.ws, "paid_on": {"$gte": since.isoformat(), "$lte": t.isoformat()}, "amount": {"$gt": 0}},
                                        {"customer": 1, "phone": 1, "amount": 1, "date": 1, "due_date": 1, "paid_on": 1}).limit(MAX_ROWS):
        try:
            paid_day, sold = date.fromisoformat(r["paid_on"]), date.fromisoformat(r["date"])
            due_day = date.fromisoformat(r["due_date"]) if r.get("due_date") else None
        except (KeyError, ValueError, TypeError):
            continue
        paid.append({"customer": r.get("customer", ""), "phone": r.get("phone", ""), "amount": int(r["amount"]) / 100,
                     "days_to_pay": max((paid_day - sold).days, 0), "days_late": max((paid_day - due_day).days, 0) if due_day else None,
                     "paid_on": r["paid_on"], "source": "sales sheet"})

    unpaid: list[dict] = []      # {customer, phone, amount (₹), due_date, days_late, invoice_no, source}
    async for d in db().dues.find({"ws": ctx.ws, "status": "due"}, {"customer": 1, "phone": 1, "amount": 1, "due_date": 1, "invoice_no": 1}).limit(MAX_ROWS):
        try:
            due_day = date.fromisoformat(d.get("due_date") or "")
        except ValueError:
            continue
        unpaid.append({"customer": d.get("customer", ""), "phone": d.get("phone", ""), "amount": int(d.get("amount") or 0) / 100,
                       "due_date": due_day.isoformat(), "days_late": (t - due_day).days, "invoice_no": d.get("invoice_no", ""), "source": "money owed"})
    # A sheet only knows what was paid up to the day it was uploaded: its unpaid rows are aged to that day, not to today.
    # Once the upload is more than SHEET_STALE_DAYS old they may well have been paid since, so they're kept apart (not
    # counted as owed or late) and the page asks for a fresh sheet.
    uploaded_on = {x["_id"]: min(salesbook.ist_date(x.get("created_at")) or t, t)
                   async for x in db().sales_batches.find({"ws": ctx.ws, "has_paid_on": True}, {"_id": 1, "created_at": 1}).limit(500)}
    stale: list[dict] = []
    if uploaded_on:
        async for r in db().sales_rows.find({"ws": ctx.ws, "batch_id": {"$in": list(uploaded_on)}, "paid_on": {"$in": ["", None]},
                                             "due_date": {"$nin": ["", None]}, "amount": {"$gt": 0}},
                                            {"customer": 1, "phone": 1, "amount": 1, "due_date": 1, "invoice_no": 1, "batch_id": 1}).limit(MAX_ROWS):
            try:
                due_day = date.fromisoformat(r["due_date"])
            except (KeyError, ValueError, TypeError):
                continue
            up = uploaded_on.get(r.get("batch_id")) or t
            item = {"customer": r.get("customer", ""), "phone": r.get("phone", ""), "amount": int(r["amount"]) / 100,
                    "due_date": due_day.isoformat(), "days_late": (up - due_day).days, "invoice_no": r.get("invoice_no", ""),
                    "source": "sales sheet", "as_of": up.isoformat()}
            (stale if (t - up).days > SHEET_STALE_DAYS else unpaid).append(item)

    ageing = {k: {"amount": 0.0, "count": 0} for k in ("not_due", "d1_30", "d31_60", "d61_90", "d90_plus")}
    for u in unpaid:
        a = ageing[_bucket(u["days_late"])]
        a["amount"] += u["amount"]
        a["count"] += 1
    for a in ageing.values():
        a["amount"] = round(a["amount"], 2)
    stuck = ageing["d61_90"]["amount"] + ageing["d90_plus"]["amount"]
    owed = sum(u["amount"] for u in unpaid)
    stuck_line = (f"{_inr(stuck)} is more than 60 days late." if stuck > 0
                  else "Nothing is more than 60 days late." if owed > 0 else "")
    oldest = sorted((u for u in unpaid if u["days_late"] > 30), key=lambda u: -u["amount"])[:5]
    oldest = [{**u, "amount": round(u["amount"], 2)} for u in oldest]
    fresh_days = sorted({u["as_of"] for u in unpaid if u.get("as_of")})
    sheet_note = ""
    if fresh_days:
        sheet_note = (f"Amounts from your sales sheet are as of your upload on {_day(fresh_days[0])} — anything paid since isn't known here."
                      if len(fresh_days) == 1 else
                      f"Amounts from your sales sheets are as of the day each was uploaded (the latest on {_day(fresh_days[-1])}) — "
                      "anything paid since isn't known here.")
    stale_amount = round(sum(u["amount"] for u in stale), 2)
    stale_days = sorted({u["as_of"] for u in stale})
    stale_note = ""
    if stale:
        stale_note = (f"{_inr(stale_amount)} unpaid in your sales {'sheet' if len(stale_days) == 1 else 'sheets'} uploaded on "
                      f"{', '.join(_day(x) for x in stale_days[-3:])} isn't counted here: the upload is more than {SHEET_STALE_DAYS} days "
                      "old, so some of it may have been paid since. Upload a fresh sheet to bring it up to date.")

    # per customer: paid ones and what they still owe
    for p in paid:
        p["key"] = customer_key(p["customer"], p["phone"])
        p["date"] = p["paid_on"]
    for u in unpaid:
        u["key"] = customer_key(u["customer"], u["phone"])
        u["date"] = ""
        u["unpaid"] = True
    people = []
    for g in group_customers(paid + unpaid):
        ps = [r for r in g["rows"] if not r.get("unpaid")]
        if not ps:
            continue
        lates = [p["days_late"] for p in ps if p["days_late"] is not None]
        avg_late = sum(lates) / len(lates) if lates else None
        people.append({"name": g["customer"], "phone": g["phone"], "paid_count": len(ps),
                       "avg_days_to_pay": round(sum(p["days_to_pay"] for p in ps) / len(ps), 1),
                       "avg_days_late": round(avg_late, 1) if avg_late is not None else None,
                       "habit": likelihood(avg_late)[0] if avg_late is not None else "no due dates",
                       "unpaid": round(sum(r["amount"] for r in g["rows"] if r.get("unpaid")), 2),
                       "last_paid": max(p["paid_on"] for p in ps)})
    people.sort(key=lambda c: (c["paid_count"] < 2, -c["avg_days_to_pay"], c["name"].lower()))

    weight = sum(p["amount"] for p in paid)
    avg = sum(p["amount"] * p["days_to_pay"] for p in paid) / weight if weight > 0 else None
    med = statistics.median(p["days_to_pay"] for p in paid) if paid else None
    with_due = [p for p in paid if p["days_late"] is not None]
    on_time = round(sum(1 for p in with_due if p["days_late"] == 0) / len(with_due) * 100) if with_due else None
    enough = len(paid) >= 5
    if not enough or avg is None:
        line = ""
    elif terms is None:
        line = f"On average you wait {round(avg)} days to get paid."
    elif round(avg) <= terms:
        line = f"You give {terms} days. On average you wait {round(avg)} — within your terms."
    else:
        line = f"You give {terms} days. On average you wait {round(avg)}."
    return {"as_of": t.isoformat(), "window_from": since.isoformat(), "terms_days": terms, "paid_count": len(paid),
            "avg_days_to_pay": round(avg, 1) if avg is not None and enough else None, "median_days_to_pay": med if enough else None,
            "on_time_pct": on_time if enough else None, "on_time_based_on": len(with_due), "line": line,
            "customers": people[:30] if enough else [], "ageing": ageing, "owed": round(owed, 2), "stuck": round(stuck, 2),
            "stuck_line": stuck_line, "oldest": oldest, "enough_data": enough,
            "sources": {"money_owed": sum(p["source"] == "money owed" for p in paid), "sales_sheet": sum(p["source"] == "sales sheet" for p in paid)},
            "sheet_has_paid_on": bool(uploaded_on), "sheet_note": sheet_note,
            "sheet_stale": {"amount": stale_amount, "count": len(stale), "uploaded_on": stale_days}, "stale_note": stale_note}


async def ensure_indexes() -> None:
    await db().sales_rows.create_index([("ws", 1), ("date", -1)])
    await db().sales_rows.create_index([("ws", 1), ("batch_id", 1)])
    await db().sales_rows.create_index([("ws", 1), ("paid_on", 1)])
    await db().sales_batches.create_index([("ws", 1), ("created_at", -1)])
