"""
Bank statement reader (v2.2, Membership): the owner uploads the statement they download from net banking (PDF, Excel or
CSV); the hub shows money in and out, where it went by category, the biggest payees and the payments that repeat, and puts
the payments they pick into Expenses in one tap.

How it treats the statement:
- The file is read in memory (app/bankstatement.py) and never stored. A PDF password is passed to the PDF reader once and
  never stored or logged.
- No AI and nothing leaves the hub: the transactions are sorted by rules, and by the owner's own corrections, which the
  hub remembers per payee ("always put Shree Estates under Rent").
- Long numbers, e-mail addresses and UPI handles are masked before anything is kept. What is kept — the masked list, so
  the owner can review it and pick what goes into Expenses — is deleted after 24 hours, or at once with "Delete it now".
- Only the owner sees it (bank statements show salaries and personal payments).

- The file is read in a separate, limited process (bankstatement.parse_isolated: memory, time, at most 2 at once), so a
  huge or booby-trapped file can't slow the hub down for everyone else.
- A row whose money in/out we had to guess from its description is never added by "Add all": the owner checks it first.
- Salary rows are only added to Expenses when the owner ticks "include salaries" (managers can see Expenses).

bank_imports {_id, ws, by, created_at, expires_at, format, bank, account_last4, from, to, opening_balance, closing_balance,
              transactions [{ref, date, narration, payee, amount, kind, balance, category, guessed}], warnings,
              imported [bank refs], to_cash_flow [recurring keys], cash_lines {recurring key: cash_item id}}
bank_rules   {_id: "<ws>:<payee key>", ws, payee, category, by, at}       the owner's "remember this" choices
bank_months  {_id: "<ws>:<last4 or ->:<YYYY-MM>", ws, account_last4, bank, period, receipts (paise), count, start, end, import_id, at}
             only the month's total "From customers" — no rows, names or narrations — so GST set-aside can estimate the GST in
             a month's sales after the statement itself is gone. A later statement of the same account replaces a month only
             when it covers at least as many days of it; "Delete it now" removes that statement's totals too.
"""
import asyncio
import hashlib
import re
from datetime import timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from .. import bankstatement as bs
from ..config import settings
from ..context import Ctx, feature
from ..dashboard_cache import bust
from ..db import db, now
from ..security import rate_limit
from ..services import new_id, track
from . import expenses as ex

router = APIRouter(prefix="/api")
owner_only = feature("bank_reader", "owner")
KEEP_HOURS = 24
MAX_MB = 10
PER_HOUR = 30
READ_SECONDS = 60
MAX_PASSWORD = 64


def bank_ref(imp: dict, ref: str) -> str:
    """The Expenses bank_ref for a statement row: the account's last 4 digits (when the statement shows them) and the row's
    fingerprint, so the same payment read twice is added once, and two accounts' look-alike payments are both added."""
    last4 = re.sub(r"\D", "", str(imp.get("account_last4") or ""))[-4:]
    return f"bank:{last4}:{ref}" if last4 else f"bank:{ref}"


def _payee_key(t: dict) -> str:
    """Which payee a remembered choice is for: the payee name, or the narration without numbers when there isn't one."""
    p = (t.get("payee") or "").strip().lower()
    if not p:
        p = re.sub(r"[^a-z ]+", " ", (t.get("narration") or "").lower())
    return re.sub(r"\s+", " ", p).strip()[:60]


def _rule_id(ws: str, key: str) -> str:
    return f"{ws}:{hashlib.sha1(key.encode()).hexdigest()[:16]}"


async def _remembered(ws: str) -> dict[str, str]:
    return {r["payee"]: r["category"] async for r in db().bank_rules.find({"ws": ws}, {"payee": 1, "category": 1}).limit(2000)}


def _labels(cats: list[dict]) -> dict[str, str]:
    out = {c["key"]: c["label"] for c in cats}
    out.update({"cash_withdrawal": "Cash withdrawn", "transfer": "Own transfer or investment", "sales_receipt": "From customers",
                "refund_in": "Refunds", "interest_in": "Interest", "loan_in": "Loan received", "cash_deposit": "Cash deposited",
                "transfer_in": "From own accounts", "other_in": "Other money in"})
    return out


async def _clean_up(ws: str) -> None:
    await db().bank_imports.delete_many({"ws": ws, "expires_at": {"$lte": now()}})


def _rows(txns: list[dict], labels: dict, in_expenses: set, remembered: dict) -> list[dict]:
    rows = []
    for t in txns:
        expense = t["kind"] == "debit" and t.get("category") not in bs.NOT_EXPENSES
        rows.append({**t, "guessed": bool(t.get("guessed")), "label": labels.get(t.get("category"), t.get("category")),
                     "in_expenses": t["ref"] in in_expenses, "expense": expense,
                     "salary": expense and t.get("category") in ex.SALARY_CATEGORIES,
                     "remembered": remembered.get(_payee_key(t)) == t.get("category")})
    return rows


async def _view(ws: str, imp: dict) -> dict:
    kind, custom, _ = await ex._setup(ws)
    cats = ex.categories_for(kind, custom)
    labels = _labels(cats)
    txns = imp.get("transactions") or []
    by_ref = {bank_ref(imp, t["ref"]): t["ref"] for t in txns if t["kind"] == "debit"}
    refs = list(by_ref)
    in_expenses: set = set()
    for i in range(0, len(refs), 500):
        in_expenses |= {by_ref[d["bank_ref"]] async for d in db().expenses.find({"ws": ws, "bank_ref": {"$in": refs[i:i + 500]}}, {"bank_ref": 1})}
    s = await asyncio.to_thread(bs.summary, txns)          # a few thousand rows: kept off the event loop
    remembered = await _remembered(ws)
    in_cf = set(imp.get("to_cash_flow") or [])
    lines = imp.get("cash_lines") or {}
    still = {c["_id"] async for c in db().cash_items.find({"owner_id": ws, "_id": {"$in": list(lines.values())}}, {"_id": 1})} if lines else set()
    for r in s["recurring"]:
        r["key"] = _rec_key(r)
        r["label"] = labels.get(r["category"], r["category"])
        r["in_cash_flow"] = r["key"] in in_cf and lines.get(r["key"]) in still     # removed in Cash flow since: can be added again
    rows = await asyncio.to_thread(_rows, txns, labels, in_expenses, remembered)
    open_ = [r for r in rows if r["expense"] and not r["in_expenses"]]
    waiting = [r for r in open_ if not r["guessed"] and not r["salary"]]        # what "Add all" adds
    salaries = [r for r in open_ if not r["guessed"] and r["salary"]]
    to_check = [r for r in open_ if r["guessed"]]
    return {"id": imp["_id"], "format": imp.get("format"), "bank": imp.get("bank"), "account_last4": imp.get("account_last4"),
            "from": imp.get("from"), "to": imp.get("to"), "opening_balance": imp.get("opening_balance"), "closing_balance": imp.get("closing_balance"),
            "created_at": imp["created_at"].isoformat(), "expires_at": imp["expires_at"].isoformat(), "warnings": imp.get("warnings") or [],
            "money_in": s["money_in"], "money_out": s["money_out"], "cash_withdrawn": s["cash_withdrawn"], "transfers_out": s["transfers_out"],
            "spent": round(s["money_out"] - s["cash_withdrawn"] - s["transfers_out"], 2),
            "months": s["months"], "by_category": sorted(({"key": k, "label": labels.get(k, k), "amount": round(v, 2)}
                                                          for k, v in s["by_category"].items() if k not in bs.NOT_EXPENSES),
                                                         key=lambda x: -x["amount"]),
            "top_payees": s["top_payees"], "recurring": s["recurring"], "transactions": rows,
            "waiting": len(waiting), "waiting_amount": round(sum(r["amount"] for r in waiting), 2),
            "waiting_salaries": len(salaries), "waiting_salaries_amount": round(sum(r["amount"] for r in salaries), 2),
            "to_check": len(to_check), "skip_reasons": ex.SKIP_REASONS,
            "categories": [{"key": c["key"], "label": c["label"]} for c in cats] + [{"key": "cash_withdrawal", "label": labels["cash_withdrawal"]},
                                                                                     {"key": "transfer", "label": labels["transfer"]}],
            "credit_categories": [{"key": k, "label": labels[k]} for k in bs.CREDIT_CATEGORIES],
            "keep_hours": KEEP_HOURS}


def month_totals(imp: dict) -> list[dict]:
    """Per calendar month the statement covers: the money in marked "From customers", and the days of the month it covers."""
    from datetime import date as _date
    txns = imp.get("transactions") or []
    days = sorted(str(t.get("date") or "")[:10] for t in txns if t.get("date"))
    if not days:
        return []
    start, end = _date.fromisoformat(imp.get("from") or days[0]), _date.fromisoformat(imp.get("to") or days[-1])
    out: dict[str, dict] = {}
    d = start.replace(day=1)
    while d <= end:
        nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
        out[d.strftime("%Y-%m")] = {"period": d.strftime("%Y-%m"), "receipts": 0, "count": 0,
                                    "start": max(start, d).isoformat(), "end": min(end, nxt - timedelta(days=1)).isoformat()}
        d = nxt
    for t in txns:
        m = out.get(str(t.get("date") or "")[:7])
        if m is not None and t.get("kind") == "credit" and t.get("category") == "sales_receipt":
            m["receipts"] += int(round(float(t.get("amount") or 0) * 100))
            m["count"] += 1
    return list(out.values())


async def save_months(ws: str, imp: dict, same_import: bool = False) -> None:
    from datetime import date as _date
    last4 = re.sub(r"\D", "", str(imp.get("account_last4") or ""))[-4:] or "-"
    span = lambda m: (_date.fromisoformat(m["end"]) - _date.fromisoformat(m["start"])).days   # noqa: E731
    for m in month_totals(imp):
        _id = f"{ws}:{last4}:{m['period']}"
        old = await db().bank_months.find_one({"_id": _id}, {"start": 1, "end": 1, "import_id": 1})
        if old and old.get("import_id") != imp["_id"] and (same_import or span(old) > span(m)):
            continue   # another statement covers more of this month (a re-sort only updates this statement's own months)
        await db().bank_months.replace_one({"_id": _id}, {"_id": _id, "ws": ws, "account_last4": None if last4 == "-" else last4,
                                                          "bank": imp.get("bank"), **m, "import_id": imp["_id"], "at": now()}, upsert=True)


def _rec_key(r: dict) -> str:
    return hashlib.sha1(f"{(r.get('payee') or '').lower()}|{r.get('category')}".encode()).hexdigest()[:16]


async def _get(ctx: Ctx, imp_id: str) -> dict:
    await _clean_up(ctx.ws)
    imp = await db().bank_imports.find_one({"_id": imp_id, "ws": ctx.ws})
    if not imp:
        raise HTTPException(404, "This statement isn't here any more — statements are kept for 24 hours. Upload it again.")
    return imp


@router.post("/bank-statement")
async def upload(file: UploadFile = File(...), password: str = Form(default=""), ctx: Ctx = Depends(owner_only)):
    if len(password) > MAX_PASSWORD:       # checked here, not by the form rules: their error would repeat the password back
        raise HTTPException(400, {"code": "wrong_password", "message": "That password is too long. Check it and try again."})
    await rate_limit(f"bank-statement:{ctx.ws}", PER_HOUR, 3600, "That's a lot of statements in an hour. Try again a little later.")
    data = await file.read(MAX_MB * 1024 * 1024 + 1)
    if len(data) > MAX_MB * 1024 * 1024:
        raise HTTPException(413, f"That file is bigger than {MAX_MB} MB. Download fewer months and try again.")
    if not data:
        raise HTTPException(400, "That file is empty.")
    try:   # in a separate process with memory and time limits (and at most 2 at once), see bankstatement.parse_isolated
        st = await asyncio.to_thread(bs.parse_isolated, data, file.filename or "", password or None, READ_SECONDS)
    except bs.StatementError as e:
        raise HTTPException(400, {"code": e.code, "message": e.message})
    finally:
        del data, password
    txns = await asyncio.to_thread(bs.categorize, st["transactions"])
    remembered = await _remembered(ctx.ws)
    for t in txns:   # the owner's own choices ("always put this payee under Rent") win over the built-in rules
        cat = remembered.get(_payee_key(t))
        if cat and t["kind"] == "debit":
            t["category"] = cat
    await _clean_up(ctx.ws)
    imp = {"_id": new_id(), "ws": ctx.ws, "by": ctx.actor, "created_at": now(), "expires_at": now() + timedelta(hours=KEEP_HOURS),
           "format": st["format"], "bank": st.get("bank"), "account_last4": st.get("account_last4"), "from": st.get("from"), "to": st.get("to"),
           "opening_balance": st.get("opening_balance"), "closing_balance": st.get("closing_balance"),
           "transactions": [{k: t.get(k) for k in ("ref", "date", "narration", "payee", "amount", "kind", "balance", "category", "guessed")} for t in txns],
           "warnings": st.get("warnings") or [], "imported": [], "to_cash_flow": [], "cash_lines": {}}
    await db().bank_imports.insert_one(imp)
    await save_months(ctx.ws, imp)
    await track("bank_statement_read", ctx.ws, rows=len(txns), format=st["format"])
    return await _view(ctx.ws, imp)


@router.get("/bank-statement")
async def latest(ctx: Ctx = Depends(owner_only)):
    await _clean_up(ctx.ws)
    imps = [i async for i in db().bank_imports.find({"ws": ctx.ws}, {"transactions": 0}).sort("created_at", -1).limit(10)]
    out = {"statements": [{"id": i["_id"], "bank": i.get("bank"), "account_last4": i.get("account_last4"), "from": i.get("from"),
                           "to": i.get("to"), "created_at": i["created_at"].isoformat(), "expires_at": i["expires_at"].isoformat()} for i in imps],
           "keep_hours": KEEP_HOURS, "max_mb": MAX_MB, "latest": None}
    if imps:
        out["latest"] = await _view(ctx.ws, await db().bank_imports.find_one({"_id": imps[0]["_id"]}))
    return out


@router.get("/bank-statement/{imp_id}")
async def one(imp_id: str, ctx: Ctx = Depends(owner_only)):
    return await _view(ctx.ws, await _get(ctx, imp_id))


@router.delete("/bank-statement/{imp_id}")
async def delete_now(imp_id: str, ctx: Ctx = Depends(owner_only)):
    r = await db().bank_imports.delete_one({"_id": imp_id, "ws": ctx.ws})
    if not r.deleted_count:
        raise HTTPException(404, "Not found")
    await db().bank_months.delete_many({"ws": ctx.ws, "import_id": imp_id})
    return {"ok": True}


class CategoryIn(BaseModel):
    category: str = Field(min_length=2, max_length=40)
    remember: bool = False


@router.patch("/bank-statement/{imp_id}/transactions/{ref}")
async def set_category(imp_id: str, ref: str, body: CategoryIn, ctx: Ctx = Depends(owner_only)):
    imp = await _get(ctx, imp_id)
    t = next((t for t in imp.get("transactions") or [] if t.get("ref") == ref), None)
    if not t:
        raise HTTPException(404, "Not found")
    kind, custom, _ = await ex._setup(ctx.ws)
    allowed = ({c["key"] for c in ex.categories_for(kind, custom)} | set(bs.NOT_EXPENSES) if t["kind"] == "debit" else set(bs.CREDIT_CATEGORIES))
    if body.category not in allowed:
        raise HTTPException(400, "Pick one of the categories shown.")
    txns = imp["transactions"]
    targets = [t]
    if body.remember and t["kind"] == "debit":
        key = _payee_key(t)
        if key:
            await db().bank_rules.replace_one({"_id": _rule_id(ctx.ws, key)}, {"_id": _rule_id(ctx.ws, key), "ws": ctx.ws, "payee": key,
                                                                               "category": body.category, "by": ctx.actor, "at": now()}, upsert=True)
            targets = [x for x in txns if x["kind"] == "debit" and _payee_key(x) == key]   # the rest of this payee's rows too
    for x in targets:
        x["category"] = body.category
    await db().bank_imports.update_one({"_id": imp["_id"], "ws": ctx.ws}, {"$set": {"transactions": txns}})
    if t["kind"] == "credit":
        await save_months(ctx.ws, imp, same_import=True)   # "From customers" or not changes the GST set-aside's sales
    return await _view(ctx.ws, imp)


class ImportIn(BaseModel):
    refs: list[str] = Field(default_factory=list, max_length=ex.MAX_BANK_ROWS)
    all_waiting: bool = False          # every payment not in Expenses yet, except rows to check and (unless ticked) salaries
    include_salaries: bool = False


@router.post("/bank-statement/{imp_id}/to-expenses")
async def to_expenses(imp_id: str, body: ImportIn, ctx: Ctx = Depends(owner_only)):
    ctx.require("expenses")
    imp = await _get(ctx, imp_id)
    picked = set(body.refs)
    rows = [{"date": t["date"], "amount": t["amount"], "payee": t.get("payee") or "", "narration": t.get("narration") or "",
             "category": t.get("category") or "other", "bank_ref": bank_ref(imp, t["ref"])}
            for t in imp.get("transactions") or []
            if t["kind"] == "debit" and t.get("category") not in bs.NOT_EXPENSES
            and (t["ref"] in picked or (body.all_waiting and not t.get("guessed")))]   # a guessed row only when picked
    have: set = set()
    refs = [r["bank_ref"] for r in rows]
    for i in range(0, len(refs), 500):
        have |= {d["bank_ref"] async for d in db().expenses.find({"ws": ctx.ws, "bank_ref": {"$in": refs[i:i + 500]}}, {"bank_ref": 1})}
    rows = [r for r in rows if r["bank_ref"] not in have]
    if not rows:
        raise HTTPException(400, "These payments are already in Expenses." if have else "Pick the payments to add first.")
    rows = rows[:ex.MAX_BANK_ROWS]
    counts = await ex.add_from_bank(ctx.ws, ctx.actor, rows, include_salaries=body.include_salaries)
    await db().bank_imports.update_one({"_id": imp["_id"], "ws": ctx.ws}, {"$addToSet": {"imported": {"$each": [r["bank_ref"] for r in rows]}}})
    reasons = {k: v for k, v in ex.SKIP_REASONS.items() if counts.get(k)}
    return {**counts, "reasons": reasons, "statement": await _view(ctx.ws, await _get(ctx, imp_id))}


class RecurringIn(BaseModel):
    key: str = Field(min_length=6, max_length=40)


@router.post("/bank-statement/{imp_id}/recurring-to-cash-flow")
async def recurring_to_cash_flow(imp_id: str, body: RecurringIn, ctx: Ctx = Depends(owner_only)):
    """A payment that repeats every month goes into Cash flow as a monthly "out" line — once, when the owner taps it."""
    ctx.require("cash_flow")
    imp = await _get(ctx, imp_id)
    summary = await asyncio.to_thread(bs.summary, imp.get("transactions") or [])
    rec = next((r for r in summary["recurring"] if _rec_key(r) == body.key), None)
    if not rec:
        raise HTTPException(404, "Not found")
    # Expenses' own helper: one line per payment, even if Expenses or an earlier statement already added it, and a line that
    # was removed in Cash flow since is added again (no stale "in cash flow" mark).
    try:
        line, created = await ex.monthly_out(ctx.ws, ctx.actor, rec.get("payee") or "Monthly payment", float(rec["amount"]),
                                             int(rec.get("day_of_month") or 1), "bank_statement", body.key)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if created:
        await track("bank_recurring_to_cash_flow", ctx.ws)
    await db().bank_imports.update_one({"_id": imp["_id"], "ws": ctx.ws},
                                       {"$addToSet": {"to_cash_flow": body.key}, "$set": {f"cash_lines.{body.key}": line["_id"]}})
    bust(ctx.ws)
    return await _view(ctx.ws, await _get(ctx, imp_id))


async def ensure_indexes() -> None:
    d = db()
    await d.bank_imports.create_index([("ws", 1), ("created_at", -1)])
    await d.bank_rules.create_index([("ws", 1)])
    await d.bank_months.create_index([("ws", 1), ("period", 1)])
    if settings.env != "test":
        try:   # the database also deletes old statements on its own (the code deletes them on every visit too)
            await d.bank_imports.create_index("expires_at", expireAfterSeconds=0)
        except Exception:  # noqa: BLE001
            pass
