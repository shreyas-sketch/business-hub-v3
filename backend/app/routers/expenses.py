"""
Expenses (v2.2): every company expense in one place — the bill, the GST that can be claimed back, and where the money
went each month. Entered by the owner and the team (a staff member's entry waits for a manager's approval), or brought
in from a bank statement by the bank reader through `add_from_bank`.

expenses {_id, ws, date "YYYY-MM-DD", amount (paise, the bill total including GST), gst (paise, 0 if none),
          gst_claimable (bool), category (key), vendor (≤80), vendor_gstin ("" or a valid GSTIN), paid_by bank|upi|card|cash|cheque,
          project ("" or ≤60), note (≤300), bill_file_id (or None), status approved|to_approve, recurring (bool),
          source owner|bank, bank_ref ("" unless from a bank statement), cash_item_id (or None), created_by, created_at, updated_at}
expense_settings {_id: ws, kind (industry kind key), budgets {category: rupees a month}, custom [{key, label}] (≤10)}

The GST set-aside and the bank reader read `expenses` with exactly these field names. Money is stored in paise; the
API takes and returns rupees. Everything is computed with find() + Python (no aggregation), so it runs on MongoDB 3.6+.
"""
import csv
import hashlib
import io
import logging
import re
from calendar import monthrange
from datetime import date as Date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from pymongo import ReturnDocument
from pymongo.errors import BulkWriteError, DuplicateKeyError

from .. import files
from ..billing import inr
from ..context import Ctx, feature
from ..dashboard_cache import bust
from ..db import IST, db, now, public
from ..event import parse_gstin
from ..services import new_id, track

router = APIRouter(prefix="/api")
log = logging.getLogger(__name__)
exp_ctx = feature("expenses", "staff")
mgr_ctx = feature("expenses", "manager")

MAX_RUPEES = 100_000_000          # ₹10 crore: anything bigger is a typo
MAX_PAISE = MAX_RUPEES * 100
MAX_CUSTOM = 10
MAX_BANK_ROWS = 2000
LIST_CAP = 1000                   # entries shown in one month's list (totals still count up to TOTALS_CAP)
TOTALS_CAP = 20000                # entries counted in one month's totals
SUMMARY_CAP = 20000               # entries read for the monthly picture (4 months), newest first
CSV_CAP = 50000                   # lines in one CSV file
BILL_TYPES = ("application/pdf", "image/png", "image/jpeg")
PAID_BY = {"bank": "Bank transfer", "upi": "UPI", "card": "Card", "cash": "Cash", "cheque": "Cheque"}
NOT_EXPENSES = {"cash_withdrawal", "transfer"}   # bank rows that move money, not spend it
SALARY_CATEGORIES = {"salaries", "salary", "wages", "payroll"}   # a person's pay: only brought in when the owner picks it
# What add_from_bank's counts mean, in words the bank statement page can show next to them
SKIP_REASONS = {
    "skipped_duplicates": "Already in Expenses.",
    "skipped_not_expenses": "Cash withdrawals, transfers between your own accounts, and money coming in.",
    "skipped_invalid": "Dated more than 3 years ago, or more than ₹10 crore. Check these in the statement and add them by hand if they're right.",
    "skipped_private": "Salary payments. They're left out unless you pick them, because managers can see Expenses.",
}
STAFF_RACE = "This entry was approved or changed while you were working on it. Ask your manager or the business owner to change it now."
# Cash flow lines that stand for a monthly payment, from this page and from the bank statement page
LINE_SOURCES = ("expenses", "bank_statement")
KEY_FIELD = {"expenses": "expense_key", "bank_statement": "bank_key"}

# ───────────────────────── categories ─────────────────────────
UNIVERSAL: list[tuple[str, str | None]] = [
    ("rent", "Rent"), ("salaries", "Salaries & wages"), ("pf_esi", "PF, ESI & professional tax"), ("electricity", "Electricity & power"),
    ("phone_internet", "Phone & internet"), ("software", "Software & subscriptions"), ("travel", "Travel & conveyance"),
    ("fuel", "Fuel & vehicle"), ("freight", "Freight & courier"), ("marketing", "Marketing & ads"), ("professional", "CA, legal & consultants"),
    ("bank_charges", "Bank charges & interest"), ("loan_emi", "Loan EMI"), ("insurance", "Insurance"), ("repairs", "Repairs & maintenance"),
    ("office", "Office supplies & housekeeping"), ("food_staff", "Staff food & welfare"), ("taxes", "Taxes & govt fees"),
    ("purchases", None), ("equipment", "Machines & equipment (one-time)"), ("other", "Other"),
]
KINDS: dict[str, dict] = {
    "manufacturing": {"label": "Manufacturing", "purchases": "Raw material",
                      "extra": [("job_work", "Job work"), ("consumables", "Consumables & spares"), ("packing", "Packing material")]},
    "construction": {"label": "Construction & interiors", "purchases": "Site material",
                     "extra": [("site_labour", "Site labour"), ("subcontract", "Subcontractors"), ("equipment_hire", "Equipment hire")]},
    "trading": {"label": "Trading & distribution", "purchases": "Stock purchases",
                "extra": [("godown", "Godown & warehousing"), ("packing", "Packing material")]},
    "services": {"label": "IT & services", "purchases": "Purchases",
                 "extra": [("contractors", "Freelancers & contractors"), ("cloud", "Hosting & cloud")]},
    "logistics": {"label": "Logistics & transport", "purchases": "Purchases",
                  "extra": [("tolls", "Tolls & permits"), ("vehicle_repair", "Vehicle repair"), ("driver_allowance", "Driver allowances")]},
    "food": {"label": "Hospitality & food", "purchases": "Food & ingredients",
             "extra": [("gas", "Gas & fuel"), ("packing", "Packing"), ("delivery_commission", "Delivery app commission")]},
    "wellness": {"label": "Wellness & healthcare", "purchases": "Medical supplies", "extra": [("equipment_amc", "Equipment AMC")]},
    "consulting": {"label": "Coaching, consulting & education", "purchases": "Purchases",
                   "extra": [("venue", "Venue & events"), ("content", "Content & production")]},
    "other": {"label": "Other", "purchases": "Purchases", "extra": []},
}
UNIVERSAL_LABELS = {k: v for k, v in UNIVERSAL if v}
ANY_EXTRA = {k: v for kd in KINDS.values() for k, v in kd["extra"]}
# expense category → the Knowledge Hub's fixed-cost kind (cash flow already counts those every month)
FIXED_KIND = {"rent": "rent", "salaries": "salary", "loan_emi": "emi", "software": "subscription", "electricity": "utility", "phone_internet": "utility"}


def categories_for(kind: str, custom: list | None = None) -> list[dict]:
    """The business's categories, its own first: [{key, label, extra}]. `extra` marks the ones that belong to this kind of
    business (its purchases head, its extra heads) and the owner's own; the rest are the everyday heads every business has."""
    kd = KINDS.get(kind) or KINDS["other"]
    own = kind in KINDS and kind != "other"
    out = [{"key": "purchases", "label": kd["purchases"], "extra": own}]
    out += [{"key": k, "label": label, "extra": True} for k, label in kd["extra"]]
    out += [{"key": c["key"], "label": c["label"], "extra": True, "custom": True} for c in (custom or []) if c.get("key") and c.get("label")]
    out += [{"key": k, "label": label, "extra": False} for k, label in UNIVERSAL if label and k != "other"]
    out.append({"key": "other", "label": "Other", "extra": False})
    return out


def _label_of(key: str, kind: str, custom: list | None) -> str:
    """Any category key's label — also for a key no longer offered (the kind changed, or a custom head was removed)."""
    for c in custom or []:
        if c.get("key") == key:
            return c["label"]
    if key == "purchases":
        return (KINDS.get(kind) or KINDS["other"])["purchases"]
    if key in UNIVERSAL_LABELS:
        return UNIVERSAL_LABELS[key]
    if key in ANY_EXTRA:
        return ANY_EXTRA[key]
    words = re.sub(r"^c_", "", key or "").replace("_", " ").strip()
    return words[:1].upper() + words[1:] if words else "Other"


# ───────────────────────── the kind of business, from the Knowledge Hub's industry text ─────────────────────────
# (kind, [(pattern, weight)]): activity words weigh 2, material or loose words 1; the highest total wins, ties go to the
# earlier kind. A pattern is a word start ("manufactur" matches "manufacturing"); "|w" means a whole word (plural s allowed).
_DETECT: list[tuple[str, list[tuple[str, int]]]] = [
    ("manufacturing", [("manufactur", 2), ("fabricat", 2), ("factory", 2), ("factories", 2), ("forging", 2), ("casting", 2),
                       ("plastic", 1), ("steel", 1), ("engineering works", 2)]),
    ("construction", [("construct", 2), ("interior", 2), ("civil", 2), ("infra", 1), ("architect", 2), ("contractor", 1), ("builder", 2)]),
    ("trading", [("trading", 2), ("trader", 2), ("wholesale", 2), ("distribut", 2), ("dealer", 2), ("supplier", 1), ("retail", 2),
                 ("|store", 1)]),
    ("services", [("software", 2), ("|IT", 2), ("digital", 1), ("agenc", 1), ("|app", 1), ("web", 1), ("|saas", 2), ("marketing agenc", 2)]),
    ("logistics", [("logistic", 2), ("transport", 2), ("courier", 2), ("fleet", 2)]),
    ("food", [("restaurant", 2), ("|cafe", 2), ("|café", 2), ("food", 1), ("catering", 2), ("caterer", 2), ("hotel", 1), ("baker", 2),
              ("cloud kitchen", 2)]),
    ("wellness", [("clinic", 2), ("hospital", 2), ("dental", 2), ("wellness", 2), ("salon", 2), ("|gym", 2), ("|spa", 2), ("pharma", 1)]),
    ("consulting", [("consult", 2), ("coach", 2), ("training", 1), ("trainer", 1), ("academy", 2), ("educat", 2), ("school", 2), ("institute", 2)]),
]


def detect_kind(industry: str) -> str:
    text = industry or ""
    low = text.lower()
    best, best_score = "other", 0
    for kind, words in _DETECT:
        score = 0
        for w, weight in words:
            if w == "|IT":   # "IT services", "an IT company" — never the word "it"
                hit = re.search(r"(?<![A-Za-z])IT(?![A-Za-z])", text) or re.search(r"\bit (services|company|solutions|firm|support|consult)", low)
            elif w.startswith("|"):
                hit = re.search(rf"(?<![a-z]){re.escape(w[1:].lower())}s?(?![a-z])", low)
            else:
                hit = re.search(rf"(?<![a-z]){re.escape(w)}", low)
            score += weight if hit else 0
        if score > best_score:
            best, best_score = kind, score
    return best


async def _settings(ws: str) -> dict:
    return await db().expense_settings.find_one({"_id": ws}) or {}


async def kind_for(ws) -> str:
    """The kind the owner chose in Expenses settings, or else the one the Knowledge Hub's industry text points to."""
    s = await _settings(ws)
    if s.get("kind") in KINDS:
        return s["kind"]
    b = await db().businesses.find_one({"owner_id": ws}, {"industry": 1}) or {}
    return detect_kind(b.get("industry") or "")


async def _setup(ws: str) -> tuple[str, list, dict]:
    """(kind, custom categories, budgets) for one business."""
    s = await _settings(ws)
    kind = s["kind"] if s.get("kind") in KINDS else await kind_for(ws)
    return kind, list(s.get("custom") or []), dict(s.get("budgets") or {})


# ───────────────────────── money and dates ─────────────────────────
def _paise(rupees: float) -> int:
    return int((Decimal(str(rupees)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _rs(paise: int | None) -> float:
    return float(Decimal(int(paise or 0)) / 100)


def _avg(total: int, n: int) -> int:
    """Average in paise, rounded half up (integers only, so it's exact)."""
    return (2 * total + n) // (2 * n) if n else 0


def _today() -> Date:
    return now().astimezone(IST).date()


def _this_month() -> str:
    return _today().strftime("%Y-%m")


def _shift(month: str, n: int) -> str:
    y, m = int(month[:4]), int(month[5:7]) + n
    while m < 1:
        y, m = y - 1, m + 12
    while m > 12:
        y, m = y + 1, m - 12
    return f"{y:04d}-{m:02d}"


def _bounds(month: str) -> tuple[str, str]:
    y, m = int(month[:4]), int(month[5:7])
    return f"{month}-01", f"{month}-{monthrange(y, m)[1]:02d}"


def _years_ago(d: Date, years: int) -> Date:
    try:
        return d.replace(year=d.year - years)
    except ValueError:   # 29 February
        return d.replace(year=d.year - years, day=28)


def _date_problem(day: str) -> str | None:
    try:
        d = Date.fromisoformat(day)
    except (TypeError, ValueError):
        return "Use a date like 2026-10-21."
    t = _today()
    if d < _years_ago(t, 3):
        return "That date is more than 3 years ago. Check the year."
    if d > t + timedelta(days=7):
        return "That date is too far ahead. Add an expense on the day it's paid, or up to a week before."
    return None


MONTH = r"^\d{4}-(0[1-9]|1[0-2])$"


# ───────────────────────── input ─────────────────────────
class ExpenseIn(BaseModel):
    date: str = Field(max_length=10)
    amount: float = Field(gt=0, le=MAX_RUPEES)           # ₹, the bill total including GST
    gst: float = Field(default=0, ge=0, le=MAX_RUPEES)   # ₹, the GST on the bill
    gst_claimable: bool = False
    category: str = Field(min_length=1, max_length=40)
    vendor: str = Field(default="", max_length=80)
    vendor_gstin: str = Field(default="", max_length=20)
    paid_by: str = Field(default="upi", pattern="^(bank|upi|card|cash|cheque)$")
    project: str = Field(default="", max_length=60)
    note: str = Field(default="", max_length=300)
    recurring: bool = False


class ExpensePatch(BaseModel):
    date: str | None = Field(default=None, max_length=10)
    amount: float | None = Field(default=None, gt=0, le=MAX_RUPEES)
    gst: float | None = Field(default=None, ge=0, le=MAX_RUPEES)
    gst_claimable: bool | None = None
    category: str | None = Field(default=None, min_length=1, max_length=40)
    vendor: str | None = Field(default=None, max_length=80)
    vendor_gstin: str | None = Field(default=None, max_length=20)
    paid_by: str | None = Field(default=None, pattern="^(bank|upi|card|cash|cheque)$")
    project: str | None = Field(default=None, max_length=60)
    note: str | None = Field(default=None, max_length=300)
    recurring: bool | None = None


def _clean(data: dict) -> dict:
    out = {}
    for k, v in data.items():
        if k in ("vendor", "project"):
            out[k] = re.sub(r"\s+", " ", v).strip()
        elif k == "note":
            out[k] = v.strip()
        elif k == "vendor_gstin":
            out[k] = re.sub(r"\s+", "", v).upper()
        elif k == "category":
            out[k] = v.strip().lower()
        elif k in ("amount", "gst"):
            out[k] = _paise(v)
        else:
            out[k] = v
    return out


def _check(doc: dict, changed: set, known: set) -> None:
    """Rules on the whole entry (after an edit is merged in). Only what changed is re-checked, so an old entry stays editable."""
    def bad(msg: str):
        raise HTTPException(422, msg)
    if "date" in changed:
        problem = _date_problem(doc["date"])
        if problem:
            bad(problem)
    if changed & {"amount", "gst"} and doc["gst"] >= doc["amount"]:
        bad("The GST has to be less than the amount. The amount is the bill total, including GST.")
    if "vendor_gstin" in changed and doc["vendor_gstin"]:
        g = parse_gstin(doc["vendor_gstin"])
        if not g["valid"]:
            bad(f"Vendor's GST number: {g['error']}")
    if "category" in changed and doc["category"] not in known:
        bad("Pick a category from the list.")


def _can_edit(ctx: Ctx, d: dict) -> bool:
    return ctx.at_least("manager") or (d.get("created_by") == ctx.actor and d.get("status") == "to_approve")


async def _visible(ctx: Ctx, expense_id: str) -> dict:
    q = {"_id": expense_id, "ws": ctx.ws}
    if not ctx.at_least("manager"):
        q["created_by"] = ctx.actor   # staff see only what they added
    d = await db().expenses.find_one(q)
    if not d:
        raise HTTPException(404, "Not found")
    return d


async def _editable(ctx: Ctx, expense_id: str) -> dict:
    d = await _visible(ctx, expense_id)
    if not _can_edit(ctx, d):
        raise HTTPException(403, "This one is already approved. Ask your manager or the business owner to change it.")
    return d


def _guard(ctx: Ctx, expense_id: str) -> dict:
    """The filter every change is written with. For staff it repeats the rule (their own entry, still waiting), so an
    approval that lands between reading the entry and writing the change wins."""
    q = {"_id": expense_id, "ws": ctx.ws}
    if not ctx.at_least("manager"):
        q.update(created_by=ctx.actor, status="to_approve")
    return q


def _lost(ctx: Ctx) -> HTTPException:
    return HTTPException(404, "Not found") if ctx.at_least("manager") else HTTPException(409, STAFF_RACE)


def _view(d: dict, ctx: Ctx, kind: str, custom: list, names: dict | None = None) -> dict:
    out = {k: v for k, v in public(d).items() if k not in ("ws", "bill_file_id")}
    out["amount"] = _rs(d.get("amount"))
    out["gst"] = _rs(d.get("gst"))
    out["category_label"] = _label_of(d.get("category", ""), kind, custom)
    out["bill"] = bool(d.get("bill_file_id"))
    out["in_cash_flow"] = bool(d.get("cash_item_id"))
    out["can_edit"] = _can_edit(ctx, d)
    if names is not None:
        out["added_by"] = names.get(d.get("created_by"), "")
    return out


async def _names(ids: set) -> dict:
    return {u["_id"]: u.get("name") or "" async for u in db().users.find({"_id": {"$in": list(ids)}}, {"name": 1}).limit(200)} if ids else {}


# ───────────────────────── list, add, change ─────────────────────────
@router.get("/expenses")
async def list_expenses(month: str | None = Query(default=None, pattern=MONTH), category: str = Query(default="", max_length=40),
                        q: str = Query(default="", max_length=100), status: str = Query(default="", pattern="^(|approved|to_approve)$"),
                        ctx: Ctx = Depends(exp_ctx)):
    month = month or _this_month()
    kind, custom, _ = await _setup(ctx.ws)
    a, b = _bounds(month)
    query: dict = {"ws": ctx.ws, "date": {"$gte": a, "$lte": b}}
    if not ctx.at_least("manager"):
        query["created_by"] = ctx.actor
    if category.strip():
        query["category"] = category.strip().lower()
    if status:
        query["status"] = status
    if q.strip():
        safe = re.escape(q.strip()[:60])
        needle = q.strip().lower()
        keys = [c["key"] for c in categories_for(kind, custom) if needle in c["label"].lower()]
        query["$or"] = [{"vendor": {"$regex": safe, "$options": "i"}}, {"note": {"$regex": safe, "$options": "i"}},
                        {"project": {"$regex": safe, "$options": "i"}}] + ([{"category": {"$in": keys}}] if keys else [])
    docs = [d async for d in db().expenses.find(query).sort([("date", -1), ("created_at", -1)]).limit(LIST_CAP + 1)]
    more = len(docs) > LIST_CAP
    docs = docs[:LIST_CAP]
    others = {d.get("created_by") for d in docs if d.get("created_by") and d.get("created_by") != ctx.ws}
    names = await _names(others) if ctx.at_least("manager") else None
    # the totals count every matching entry (up to TOTALS_CAP), not only the ones shown
    sums = docs if not more else [d async for d in db().expenses.find(query, {"amount": 1, "gst": 1, "gst_claimable": 1, "status": 1})
                                  .sort([("date", -1), ("created_at", -1)]).limit(TOTALS_CAP + 1)]
    totals_capped = len(sums) > TOTALS_CAP
    sums = sums[:TOTALS_CAP]
    spent = sum(int(d.get("amount") or 0) for d in sums)
    claim = sum(int(d.get("gst") or 0) for d in sums if d.get("gst_claimable") and d.get("status") == "approved")
    return {"month": month, "items": [_view(d, ctx, kind, custom, names) for d in docs], "more": more, "shown_cap": LIST_CAP,
            "totals": {"spent": _rs(spent), "gst_claimable": _rs(claim), "count": len(sums),
                       "to_approve": sum(d.get("status") == "to_approve" for d in sums)},
            "totals_capped": totals_capped, "totals_cap": TOTALS_CAP}


@router.post("/expenses")
async def add_expense(body: ExpenseIn, ctx: Ctx = Depends(exp_ctx)):
    kind, custom, _ = await _setup(ctx.ws)
    data = _clean(body.model_dump())
    _check(data, set(data), {c["key"] for c in categories_for(kind, custom)})
    doc = {"_id": new_id(), "ws": ctx.ws, **data, "gst_claimable": bool(data["gst_claimable"] and data["gst"] > 0), "bill_file_id": None,
           "status": "approved" if ctx.at_least("manager") else "to_approve", "source": "owner", "bank_ref": "", "cash_item_id": None,
           "created_by": ctx.actor, "created_at": now(), "updated_at": now()}
    await db().expenses.insert_one(doc)
    bust(ctx.ws)
    await track("expense_added", ctx.ws)
    return _view(doc, ctx, kind, custom)


@router.patch("/expenses/{expense_id}")
async def edit_expense(expense_id: str, body: ExpensePatch, ctx: Ctx = Depends(exp_ctx)):
    d = await _editable(ctx, expense_id)
    kind, custom, _ = await _setup(ctx.ws)
    patch = _clean(body.model_dump(exclude_none=True))
    changed = {k for k, v in patch.items() if d.get(k) != v}
    merged = {**d, **patch}
    _check(merged, changed, {c["key"] for c in categories_for(kind, custom)})
    patch["gst_claimable"] = bool(merged.get("gst_claimable") and merged.get("gst", 0) > 0)
    patch.update(updated_at=now(), updated_by=ctx.actor)
    r = await db().expenses.update_one(_guard(ctx, expense_id), {"$set": patch})
    if not r.matched_count:
        raise _lost(ctx)
    bust(ctx.ws)
    return _view(await db().expenses.find_one({"_id": expense_id, "ws": ctx.ws}), ctx, kind, custom)


@router.delete("/expenses/{expense_id}")
async def delete_expense(expense_id: str, ctx: Ctx = Depends(exp_ctx)):
    await _editable(ctx, expense_id)
    d = await db().expenses.find_one_and_delete(_guard(ctx, expense_id))
    if not d:
        raise _lost(ctx)
    if d.get("bill_file_id"):
        await files.delete(d["bill_file_id"], ctx.ws)
    # A monthly cash-flow line made from this entry stays: it is a promise about next month, and dropping it silently
    # would make the forecast look better than it is. The page says so; it can be removed in Cash flow.
    bust(ctx.ws)
    return {"ok": True, "kept_in_cash_flow": bool(d.get("cash_item_id"))}


@router.post("/expenses/{expense_id}/approve")
async def approve_expense(expense_id: str, ctx: Ctx = Depends(mgr_ctx)):
    d = await _visible(ctx, expense_id)
    kind, custom, _ = await _setup(ctx.ws)
    if d.get("status") != "approved":
        await db().expenses.update_one({"_id": expense_id, "ws": ctx.ws},
                                       {"$set": {"status": "approved", "approved_by": ctx.actor, "approved_at": now(), "updated_at": now()}})
        bust(ctx.ws)
        await track("expense_approved", ctx.ws)
    return _view(await db().expenses.find_one({"_id": expense_id, "ws": ctx.ws}), ctx, kind, custom)


# ───────────────────────── the bill ─────────────────────────
def _disposition(name: str) -> str:
    from urllib.parse import quote
    plain = re.sub(r"[^A-Za-z0-9 ._-]", "", name or "").strip() or "bill"
    return f"inline; filename=\"{plain}\"; filename*=UTF-8''{quote(name or 'bill')}"


@router.post("/expenses/{expense_id}/bill")
async def attach_bill(expense_id: str, file: UploadFile = File(...), ctx: Ctx = Depends(exp_ctx)):
    await _editable(ctx, expense_id)
    data, mime = await files.read_upload(file, BILL_TYPES)
    f = await files.put(ctx.ws, data, file.filename or "bill", mime, "expense_bill", expense_id=expense_id)
    # the entry as it was just before this bill went on: its old bill (if any) is the one to delete
    before = await db().expenses.find_one_and_update(_guard(ctx, expense_id), {"$set": {"bill_file_id": f["_id"], "updated_at": now()}},
                                                     return_document=ReturnDocument.BEFORE)
    if not before:
        await files.delete(f["_id"], ctx.ws)
        raise _lost(ctx)
    if before.get("bill_file_id") and before["bill_file_id"] != f["_id"]:
        await files.delete(before["bill_file_id"], ctx.ws)
    kind, custom, _ = await _setup(ctx.ws)
    return _view(await db().expenses.find_one({"_id": expense_id, "ws": ctx.ws}), ctx, kind, custom)


@router.get("/expenses/{expense_id}/bill")
async def get_bill(expense_id: str, ctx: Ctx = Depends(exp_ctx)):
    d = await _visible(ctx, expense_id)
    f = await files.get(d["bill_file_id"], ctx.ws) if d.get("bill_file_id") else None
    if not f:
        raise HTTPException(404, "No bill attached")
    return Response(bytes(f["data"]), media_type=f["content_type"],
                    headers={"Content-Disposition": _disposition(f["filename"]), "Cache-Control": "private, no-store"})


@router.delete("/expenses/{expense_id}/bill")
async def remove_bill(expense_id: str, ctx: Ctx = Depends(exp_ctx)):
    await _editable(ctx, expense_id)
    before = await db().expenses.find_one_and_update(_guard(ctx, expense_id), {"$set": {"bill_file_id": None, "updated_at": now()}},
                                                     return_document=ReturnDocument.BEFORE)
    if not before:
        raise _lost(ctx)
    if before.get("bill_file_id"):
        await files.delete(before["bill_file_id"], ctx.ws)
    kind, custom, _ = await _setup(ctx.ws)
    return _view(await db().expenses.find_one({"_id": expense_id, "ws": ctx.ws}), ctx, kind, custom)


# ───────────────────────── where the money went ─────────────────────────
def _vendor_key(v: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (v or "").lower()).strip()


def _group_key(d: dict) -> str:
    return f"{_vendor_key(d.get('vendor', ''))}|{d.get('category', '')}"


def _within_15(values: list[int]) -> bool:
    return bool(values) and (max(values) - min(values)) * 100 <= 15 * max(values)


def _repeat(monthly: dict[str, int]) -> list[str] | None:
    """The months (2 or 3) whose totals are within 15% of each other, or None."""
    months = sorted(monthly)
    if len(months) >= 3 and _within_15([monthly[m] for m in months]):
        return months
    best = None
    for i in range(len(months)):
        for j in range(i + 1, len(months)):
            pair = [months[i], months[j]]
            if _within_15([monthly[m] for m in pair]) and (best is None or pair[1] > best[1] or (pair[1] == best[1] and pair[0] > best[0])):
                best = pair
    return best


def _typical(values: list[int]) -> int:
    v = sorted(values)
    n = len(v)
    return v[n // 2] if n % 2 else _avg(v[n // 2 - 1] + v[n // 2], 2)


@router.get("/expenses/summary")
async def summary(month: str | None = Query(default=None, pattern=MONTH), ctx: Ctx = Depends(mgr_ctx)):
    month = month or _this_month()
    kind, custom, budgets = await _setup(ctx.ws)
    prev, window = _shift(month, -1), [_shift(month, -i) for i in (2, 1, 0)]           # the last 3 months, this one included
    before = [_shift(month, -i) for i in (3, 2, 1)]                                       # the 3 months before this one
    a, b = _bounds(before[0])[0], _bounds(month)[1]
    docs = [d async for d in db().expenses.find({"ws": ctx.ws, "date": {"$gte": a, "$lte": b}}).sort("date", -1).limit(SUMMARY_CAP + 1)]
    capped = len(docs) > SUMMARY_CAP   # newest first, so what is left out is the oldest of the 4 months
    docs = docs[:SUMMARY_CAP]
    by_month: dict[str, list[dict]] = {}
    for d in docs:
        by_month.setdefault(str(d.get("date", ""))[:7], []).append(d)
    this = by_month.get(month, [])
    spent = sum(int(d.get("amount") or 0) for d in this)
    last = sum(int(d.get("amount") or 0) for d in by_month.get(prev, []))
    claim = sum(int(d.get("gst") or 0) for d in this if d.get("gst_claimable") and d.get("status") == "approved")
    gst_waiting = sum(int(d.get("gst") or 0) for d in this if d.get("gst_claimable") and d.get("status") != "approved")

    # by category: this month, last month, and the average of the months before that have any entries at all (a new
    # user's empty months would otherwise make every head look like a jump)
    avg_months = sum(1 for m in before if by_month.get(m))
    cat: dict[str, dict] = {}
    for m, rows in by_month.items():
        for d in rows:
            c = cat.setdefault(d.get("category", "other"), {"amount": 0, "last_month": 0, "before": 0})
            amt = int(d.get("amount") or 0)
            if m == month:
                c["amount"] += amt
            if m == prev:
                c["last_month"] += amt
            if m in before:
                c["before"] += amt
    for k in budgets:
        cat.setdefault(k, {"amount": 0, "last_month": 0, "before": 0})
    by_category, jumps = [], []
    for k, c in cat.items():
        avg = _avg(c["before"], avg_months) if avg_months else None
        budget = budgets.get(k)
        row = {"key": k, "label": _label_of(k, kind, custom), "amount": _rs(c["amount"]), "last_month": _rs(c["last_month"]),
               "avg_3m": _rs(avg) if avg is not None else None, "budget": budget,
               "over_budget": bool(budget and c["amount"] > _paise(budget))}
        by_category.append((c["amount"], avg or 0, row))
        # a jump: up more than 25% on the usual, and by at least ₹500 (smaller moves are noise)
        if avg and c["amount"] * 100 > avg * 125 and c["amount"] - avg >= 50_000:
            pct = (c["amount"] - avg) * 100 // avg
            jumps.append((c["amount"] - avg, {"key": k, "label": row["label"], "amount": row["amount"], "avg_3m": row["avg_3m"], "pct": pct,
                                              "line": f"{row['label']} is up {pct}% on your usual: {inr(round(c['amount'] / 100) * 100)} this month, "
                                                      f"against {inr(round(avg / 100) * 100)} a month on average."}))
    by_category.sort(key=lambda x: (-x[0], -x[1], x[2]["label"]))
    jumps.sort(key=lambda x: -x[0])

    vendors: dict[str, dict] = {}
    for d in this:
        vk = _vendor_key(d.get("vendor", ""))
        if not vk:
            continue
        v = vendors.setdefault(vk, {"vendor": d.get("vendor", ""), "amount": 0, "count": 0})
        v["amount"] += int(d.get("amount") or 0)
        v["count"] += 1
    top_vendors = sorted(vendors.values(), key=lambda v: (-v["amount"], v["vendor"].lower()))[:5]

    paid_by = {k: 0 for k in PAID_BY}
    for d in this:
        paid_by[d.get("paid_by") if d.get("paid_by") in PAID_BY else "bank"] += int(d.get("amount") or 0)

    projects: dict[str, dict] = {}
    for d in this:
        p = (d.get("project") or "").strip()
        if p:
            x = projects.setdefault(p.lower(), {"project": p, "amount": 0})
            x["amount"] += int(d.get("amount") or 0)

    recurring = await _recurring(ctx.ws, [d for m in window for d in by_month.get(m, [])], kind, custom)
    budget_total = sum(_paise(v) for v in budgets.values())
    budget_spent = sum(cat.get(k, {}).get("amount", 0) for k in budgets)
    return {
        "month": month, "spent": _rs(spent), "last_month_spent": _rs(last),
        "change_pct": round((spent - last) * 100 / last) if last else None,
        "gst_claimable": _rs(claim), "gst_claimable_waiting": _rs(gst_waiting),
        "count": len(this), "to_approve": sum(d.get("status") == "to_approve" for d in this),
        "avg_months": avg_months,
        "by_category": [r for _, _, r in by_category],
        "top_vendors": [{**v, "amount": _rs(v["amount"])} for v in top_vendors],
        "by_paid_by": {k: _rs(v) for k, v in paid_by.items()},
        "recurring": recurring,
        "jumps": [j for _, j in jumps],
        "projects": [{"project": p["project"], "amount": _rs(p["amount"])} for p in sorted(projects.values(), key=lambda p: -p["amount"])],
        "budget_total": _rs(budget_total) if budgets else None, "budget_spent": _rs(budget_spent) if budgets else None,
        "can_cash_flow": ctx.has("cash_flow"), "capped": capped, "cap": SUMMARY_CAP,
    }


async def _recurring(ws: str, rows: list[dict], kind: str, custom: list) -> list[dict]:
    """Payments that repeat: entries marked "repeats every month", plus the same vendor and head in at least 2 of the last
    3 months with month totals within 15% of each other."""
    groups: dict[str, list[dict]] = {}
    for d in rows:
        groups.setdefault(_group_key(d), []).append(d)
    lines = await _lines(ws)
    live = {c["_id"]: c for c in lines}
    b = await db().businesses.find_one({"owner_id": ws}, {"money": 1}) or {}
    fixed = (b.get("money") or {}).get("fixed_costs") or []
    out = []
    for key, items in groups.items():
        marked = any(d.get("recurring") for d in items)
        monthly: dict[str, int] = {}
        for d in items:
            monthly[str(d["date"])[:7]] = monthly.get(str(d["date"])[:7], 0) + int(d.get("amount") or 0)
        months = _repeat(monthly)
        if not marked and not months:
            continue
        latest = max(items, key=lambda d: (d["date"], str(d.get("created_at") or "")))
        linked = next((d for d in items if d.get("cash_item_id") in live), None)
        typical = _typical([monthly[m] for m in (months or sorted(monthly))])
        match = None
        for c in fixed:
            same_kind = FIXED_KIND.get(latest.get("category")) and c.get("kind") == FIXED_KIND.get(latest.get("category"))
            same_name = _vendor_key(c.get("name", "")) and _vendor_key(c.get("name", "")) == _vendor_key(latest.get("vendor", ""))
            if same_name or (same_kind and _within_15([_paise(float(c.get("amount") or 0)), typical])):
                match = {"name": c.get("name", ""), "amount": float(c.get("amount") or 0)}
                break
        title = _line_title(latest, kind, custom)
        # in Cash flow already: linked from an entry, or a monthly line (from here or the bank statement page) for the
        # same payment — same name, about the same amount
        found = bool(linked) or any(_same_line(c, title, p, "expenses", key) for c in lines for p in (typical, int(latest.get("amount") or 0)))
        out.append({"vendor": latest.get("vendor", ""), "category": latest.get("category", ""),
                    "category_label": _label_of(latest.get("category", ""), kind, custom), "amount": _rs(typical),
                    "day_of_month": int(str(latest["date"])[8:10]), "months": len(monthly), "marked": marked,
                    "in_cash_flow": found, "expense_id": (linked or latest)["_id"],
                    "in_knowledge_hub": match})
    out.sort(key=lambda r: -r["amount"])
    return out


# ───────────────────────── settings ─────────────────────────
class CustomIn(BaseModel):
    key: str = Field(default="", max_length=40)
    label: str = Field(min_length=2, max_length=40)


class SettingsIn(BaseModel):
    kind: str | None = Field(default=None, max_length=30)
    budgets: dict[str, float] | None = None
    custom: list[CustomIn] | None = Field(default=None, max_length=MAX_CUSTOM)


async def _settings_view(ws: str, ctx: Ctx) -> dict:
    s = await _settings(ws)
    kind, custom, budgets = await _setup(ws)
    return {"kind": kind, "kind_label": KINDS[kind]["label"], "chosen": s.get("kind") in KINDS,
            "kinds": [{"key": k, "label": v["label"]} for k, v in KINDS.items()],
            "categories": categories_for(kind, custom),
            "budgets": budgets if ctx.at_least("manager") else {},
            "custom": custom, "paid_by": [{"key": k, "label": v} for k, v in PAID_BY.items()]}


@router.get("/expenses/settings")
async def get_settings(ctx: Ctx = Depends(exp_ctx)):
    return await _settings_view(ctx.ws, ctx)


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:28]


@router.put("/expenses/settings")
async def put_settings(body: SettingsIn, ctx: Ctx = Depends(mgr_ctx)):
    s = await _settings(ctx.ws)
    kind, custom, budgets = await _setup(ctx.ws)
    patch: dict = {}
    if body.kind is not None:
        if body.kind not in KINDS:
            raise HTTPException(422, "Pick the kind of business from the list.")
        kind = patch["kind"] = body.kind
    if body.custom is not None:
        old = {c["key"]: c for c in custom}
        standard = {c["label"].lower() for c in categories_for(kind)}
        seen, new = set(), []
        for c in body.custom:
            label = re.sub(r"\s+", " ", c.label).strip()
            if len(label) < 2:
                raise HTTPException(422, "Give each category a name of at least 2 letters.")
            if label.lower() in standard:
                raise HTTPException(422, f"“{label}” is already in the list.")
            if label.lower() in seen:
                raise HTTPException(422, f"“{label}” is there twice.")
            seen.add(label.lower())
            key = c.key if c.key in old else ""
            if not key:
                base = "c_" + (_slug(label) or new_id()[:6])
                key, n = base, 2
                while key in old or any(x["key"] == key for x in new):
                    key, n = f"{base}_{n}", n + 1
            new.append({"key": key, "label": label})
        custom = patch["custom"] = new
    if body.budgets is not None:
        known = {c["key"] for c in categories_for(kind, custom)}
        clean = {}
        for k, v in body.budgets.items():
            if k not in known:
                raise HTTPException(422, "A budget is for a category that isn't in your list.")
            if not (0 <= v <= MAX_RUPEES):
                raise HTTPException(422, "A monthly budget has to be between ₹0 and ₹10 crore.")
            if v > 0:   # 0 means no budget
                clean[k] = float(Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        budgets = patch["budgets"] = clean
    elif "custom" in patch or "kind" in patch:   # a budget for a head that's gone goes with it
        known = {c["key"] for c in categories_for(kind, custom)}
        patch["budgets"] = {k: v for k, v in budgets.items() if k in known}
    if patch:
        if not s.get("kind") and "kind" not in patch:
            patch["kind"] = None   # keep following the Knowledge Hub until the owner picks one
        await db().expense_settings.update_one({"_id": ctx.ws}, {"$set": {**patch, "updated_at": now(), "updated_by": ctx.actor}}, upsert=True)
        bust(ctx.ws)
    return await _settings_view(ctx.ws, ctx)


# ───────────────────────── for the CA ─────────────────────────
def _cell(v) -> str:
    """Spreadsheet-safe: a cell that starts like a formula is shown as text."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


@router.get("/expenses/export.csv")
async def export_csv(from_: str | None = Query(default=None, alias="from", max_length=10), to: str | None = Query(default=None, max_length=10),
                     ctx: Ctx = Depends(mgr_ctx)):
    try:
        a = Date.fromisoformat(from_) if from_ else Date.fromisoformat(_bounds(_this_month())[0])
        b = Date.fromisoformat(to) if to else _today()
    except ValueError:
        raise HTTPException(422, "Use dates like 2026-10-01.")
    if b < a:
        raise HTTPException(422, "The ‘to’ date is before the ‘from’ date.")
    if (b - a).days > 366:
        raise HTTPException(422, "Download at most one year at a time.")
    kind, custom, _ = await _setup(ctx.ws)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Date", "Vendor", "Vendor GSTIN", "Category", "Amount (Rs)", "GST (Rs)", "GST claimable", "Paid by", "Project / site",
                "Note", "Bill attached", "Status"])
    async for d in db().expenses.find({"ws": ctx.ws, "date": {"$gte": a.isoformat(), "$lte": b.isoformat()}}).sort([("date", 1), ("created_at", 1)]).limit(50000):
        w.writerow([_cell(x) for x in (
            d.get("date", ""), d.get("vendor", ""), d.get("vendor_gstin", ""), _label_of(d.get("category", ""), kind, custom),
            f"{Decimal(int(d.get('amount') or 0)) / 100:.2f}", f"{Decimal(int(d.get('gst') or 0)) / 100:.2f}",
            "yes" if d.get("gst_claimable") else "no", PAID_BY.get(d.get("paid_by"), d.get("paid_by", "")), d.get("project", ""),
            d.get("note", ""), "yes" if d.get("bill_file_id") else "no", "Approved" if d.get("status") == "approved" else "To approve")])
    await track("expenses_exported", ctx.ws)
    name = f"expenses-{a.isoformat()}-to-{b.isoformat()}.csv"
    # the BOM lets Excel read ₹ and Hindi vendor names correctly
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "private, no-store"})


# ───────────────────────── into Cash flow ─────────────────────────
def _title_key(title: str) -> str:
    return _vendor_key(title)


def _line_title(d: dict, kind: str, custom: list) -> str:
    return (d.get("vendor") if len(d.get("vendor") or "") >= 2 else _label_of(d.get("category", ""), kind, custom))[:100]


async def _lines(ws: str) -> list[dict]:
    """The monthly "out" lines in Cash flow that stand for a repeating payment (from Expenses or the bank statement page)."""
    return [c async for c in db().cash_items.find({"owner_id": ws, "source": {"$in": list(LINE_SOURCES)}, "recurrence": "monthly"}).limit(1000)]


def _same_line(line: dict, title: str, paise: int, source: str | None = None, key: str | None = None) -> bool:
    """The same payment: the same key from the same page, or the same name (ignoring case and punctuation) at an amount
    within 15%, whichever page added it."""
    if line.get("recurrence") != "monthly" or line.get("kind", "out") != "out":
        return False
    if source and key and line.get("source") == source and line.get(KEY_FIELD.get(source, "line_key")) == key:
        return True
    t = _title_key(title)
    try:
        theirs = _paise(float(line.get("amount") or 0))
    except (TypeError, ValueError):
        return False
    return bool(t) and _title_key(line.get("title", "")) == t and paise > 0 and _within_15([theirs, paise])


async def monthly_out(ws: str, actor: str, title: str, amount: float, day: int, source: str, key: str,
                      ref: dict | None = None) -> tuple[dict, bool]:
    """Puts a repeating payment into Cash flow as a monthly "out" line, once. Returns (cash_item, created).

    If any monthly line for this owner from Expenses or the bank statement page already stands for the same payment (same
    key from the same page, or the same name ignoring case and punctuation with an amount within 15%), that line is returned
    and nothing is added. Otherwise one line is written with an upsert keyed on (owner_id, source, key), so a double tap
    adds it once. `source` is "expenses" or "bank_statement" (the key is stored as expense_key or bank_key); `ref` adds
    fields to the new line, e.g. {"expense_id": ...}."""
    title = re.sub(r"\s+", " ", str(title or "")).strip()[:100] or "Monthly payment"
    amount = round(float(amount), 2)
    if not 0 < amount <= MAX_RUPEES:
        raise ValueError("The amount has to be more than ₹0 and at most ₹10 crore.")
    key = str(key or "").strip()[:200]
    if not key:
        raise ValueError("A key is needed so the same payment is added once.")
    field = KEY_FIELD.get(source, "line_key")
    for line in await _lines(ws):
        if _same_line(line, title, _paise(amount), source, key):
            return line, False
    where = {"owner_id": ws, "source": source, field: key}
    fixed = set(where) | {"_id", "kind", "title", "amount", "date", "day_of_month", "recurrence"}
    doc = {**{k: v for k, v in (ref or {}).items() if k not in fixed}, "_id": new_id(), "kind": "out", "title": title, "amount": amount,
           "date": None, "day_of_month": max(1, min(int(day or 1), 31)), "recurrence": "monthly", "created_by": actor, "created_at": now()}
    try:
        line = await db().cash_items.find_one_and_update(where, {"$setOnInsert": doc}, upsert=True, return_document=ReturnDocument.AFTER)
    except DuplicateKeyError:   # two taps at once: the other one wrote it
        line = await db().cash_items.find_one(where)
    created = line["_id"] == doc["_id"]
    if created:
        bust(ws)
    return line, created


@router.post("/expenses/{expense_id}/to-cash-flow")
async def to_cash_flow(expense_id: str, ctx: Ctx = Depends(mgr_ctx)):
    """Counts this payment every month in the Cash flow forecast (as a monthly "out" line). Doing it twice adds it once,
    and a line the bank statement page already added for the same payment is used instead of a second one."""
    ctx.require("cash_flow")
    d = await _visible(ctx, expense_id)
    kind, custom, _ = await _setup(ctx.ws)
    item = await db().cash_items.find_one({"_id": d["cash_item_id"], "owner_id": ctx.ws}) if d.get("cash_item_id") else None
    created = False
    if not item:   # never added, or the line was removed in Cash flow since
        item, created = await monthly_out(ctx.ws, ctx.actor, _line_title(d, kind, custom), _rs(d.get("amount")), int(d["date"][8:10]),
                                          "expenses", _group_key(d), ref={"expense_id": expense_id})
        if created:
            await track("expense_to_cash_flow", ctx.ws)
    await db().expenses.update_one({"_id": expense_id, "ws": ctx.ws}, {"$set": {"cash_item_id": item["_id"], "updated_at": now()}})
    bust(ctx.ws)
    return {"in_cash_flow": True, "created": created, "cash_item": public(item)}


@router.delete("/expenses/{expense_id}/to-cash-flow")
async def out_of_cash_flow(expense_id: str, ctx: Ctx = Depends(mgr_ctx)):
    """Stops counting this payment in Cash flow: its own line, and any monthly line that stands for the same payment."""
    d = await _visible(ctx, expense_id)
    kind, custom, _ = await _setup(ctx.ws)
    ids = [d["cash_item_id"]] if d.get("cash_item_id") else []
    title, key = _line_title(d, kind, custom), _group_key(d)
    ids += [c["_id"] for c in await _lines(ctx.ws) if _same_line(c, title, int(d.get("amount") or 0), "expenses", key)]
    if ids:
        await db().cash_items.delete_many({"_id": {"$in": ids}, "owner_id": ctx.ws})
        await db().expenses.update_many({"ws": ctx.ws, "cash_item_id": {"$in": ids}}, {"$set": {"cash_item_id": None, "updated_at": now()}})
    bust(ctx.ws)
    return {"in_cash_flow": False}


# ───────────────────────── from a bank statement (called by the bank reader) ─────────────────────────
async def add_from_bank(ws: str, actor: str, rows: list[dict], include_salaries: bool = False) -> dict:
    """Adds bank statement payments as approved expenses (source "bank", paid by bank, no GST).

    rows: [{"date": "YYYY-MM-DD", "amount": ₹ (positive), "payee", "narration", "category", "bank_ref"}], at most 2,000.
    The vendor is the payee ("Bank payment" when there is none). The narration is never stored: managers can see Expenses,
    and a narration can carry a person's name or what a payment was for. Salary rows (category salaries/salary/wages/payroll)
    are left out unless include_salaries is True, so the owner brings them in on purpose.
    Skips a bank_ref this business already has (or that repeats in the rows), cash withdrawals and transfers, and money in
    (an amount of 0 or less). An unknown category becomes "other". A row without a bank_ref gets one from its date, amount,
    payee and narration, so reading the same statement twice adds nothing. Two imports at the same moment can't both add a
    payment: a unique index on (ws, bank_ref) refuses the second, and it is counted as a duplicate.
    Returns {"added", "skipped_duplicates", "skipped_not_expenses", "skipped_invalid" (more than 3 years old, or more than
    ₹10 crore), "skipped_private" (salaries)}. SKIP_REASONS has a plain line for each."""
    if len(rows) > MAX_BANK_ROWS:
        raise ValueError(f"At most {MAX_BANK_ROWS:,} rows at a time.")
    kind, custom, _ = await _setup(ws)
    known = {c["key"] for c in categories_for(kind, custom)}
    counts = {"added": 0, "skipped_duplicates": 0, "skipped_not_expenses": 0, "skipped_invalid": 0, "skipped_private": 0}
    prepared, occurrences = [], {}
    for r in rows:
        r = r or {}
        category = str(r.get("category") or "").strip().lower()
        try:
            amount = float(r.get("amount") or 0)
        except (TypeError, ValueError):
            counts["skipped_invalid"] += 1
            continue
        if category in NOT_EXPENSES or amount <= 0:
            counts["skipped_not_expenses"] += 1
            continue
        if category in SALARY_CATEGORIES and not include_salaries:
            counts["skipped_private"] += 1
            continue
        day = r.get("date")
        day = day.isoformat()[:10] if hasattr(day, "isoformat") else str(day or "")[:10]
        if _date_problem(day) or amount > MAX_RUPEES:
            counts["skipped_invalid"] += 1
            continue
        payee = re.sub(r"\s+", " ", str(r.get("payee") or "")).strip()
        paise = _paise(amount)
        ref = str(r.get("bank_ref") or "").strip()[:120]
        if not ref:   # a fingerprint, not the text: the narration itself is not kept
            narration = re.sub(r"\s+", " ", str(r.get("narration") or "")).strip().lower()
            base = f"{day}|{paise}|{payee.lower()}|{narration}"
            occurrences[base] = occurrences.get(base, 0) + 1
            ref = "auto:" + hashlib.sha256(f"{base}|{occurrences[base]}".encode()).hexdigest()[:20]
        if category in SALARY_CATEGORIES:
            category = "salaries"
        prepared.append({"date": day, "amount": paise, "vendor": (payee or "Bank payment")[:80],
                         "category": category if category in known else "other", "bank_ref": ref})
    refs = list({p["bank_ref"] for p in prepared})
    have: set = set()
    for i in range(0, len(refs), 500):   # ($gt "" lets the partial unique index answer this)
        have |= {d["bank_ref"] async for d in db().expenses.find({"ws": ws, "bank_ref": {"$in": refs[i:i + 500], "$gt": ""}}, {"bank_ref": 1})}
    docs = []
    for p in prepared:
        if p["bank_ref"] in have:
            counts["skipped_duplicates"] += 1
            continue
        have.add(p["bank_ref"])
        docs.append({"_id": new_id(), "ws": ws, "date": p["date"], "amount": p["amount"], "gst": 0, "gst_claimable": False,
                     "category": p["category"], "vendor": p["vendor"], "vendor_gstin": "", "paid_by": "bank", "project": "", "note": "",
                     "bill_file_id": None, "status": "approved", "recurring": False, "source": "bank", "bank_ref": p["bank_ref"],
                     "cash_item_id": None, "created_by": actor, "created_at": now(), "updated_at": now()})
    for i in range(0, len(docs), 500):
        chunk = docs[i:i + 500]
        try:
            await db().expenses.insert_many(chunk, ordered=False)
            counts["added"] += len(chunk)
        except BulkWriteError as e:   # another import added some of these a moment ago
            errors = e.details.get("writeErrors") or []
            if any(w.get("code") != 11000 for w in errors):
                raise
            counts["added"] += int(e.details.get("nInserted") or 0)
            counts["skipped_duplicates"] += len(errors)
    if counts["added"]:
        bust(ws)
        await track("expenses_from_bank", ws, added=counts["added"])
    return counts


async def ensure_indexes() -> None:
    e = db().expenses
    await e.create_index([("ws", 1), ("date", 1)])
    await e.create_index([("ws", 1), ("status", 1)])
    # One bank payment, one expense — even when two imports run at once. Entries typed by hand have bank_ref "" and are
    # left out of the index. The first v2.2 build had a plain (ws, bank_ref) index; MongoDB 3.6 won't keep two indexes
    # on the same fields, so that one goes first.
    try:
        await e.drop_index("ws_1_bank_ref_1")
    except Exception:  # noqa: BLE001 — not there
        pass
    try:
        await e.create_index([("ws", 1), ("bank_ref", 1)], name="ws_bank_ref_unique", unique=True,
                             partialFilterExpression={"bank_ref": {"$gt": ""}})
    except Exception as err:  # noqa: BLE001 — e.g. duplicates already stored: the check before inserting still stops new ones
        log.warning("expenses: unique bank_ref index not created (%s); keeping a plain one", err)
        await e.create_index([("ws", 1), ("bank_ref", 1)])
    # One Cash flow line per repeating payment from Expenses, even on a double tap (only this page's lines are covered).
    try:
        await db().cash_items.create_index([("owner_id", 1), ("expense_key", 1)], name="cash_items_expense_line_unique", unique=True,
                                           partialFilterExpression={"source": "expenses"})
    except Exception as err:  # noqa: BLE001
        log.warning("expenses: unique cash-flow line index not created (%s)", err)
