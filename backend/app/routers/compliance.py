"""
Compliance calendar (v2.1, 3.9): every filing, licence, renewal and document expiry in one calendar, with reminders.

- Statutory items come from the rules table (app/compliance_rules.py) matched to the Knowledge Hub's business identity,
  for the next 12 months. Dates are computed from the table — the AI never sets one. "Explain for my business" (one AI
  run) only rewrites each item's explanation in plain words; any date it returns is ignored.
- The owner adds their own items (licences, insurance, rent agreement, AMC, PUC…) with an expiry date, reminders and an
  optional document (PDF/JPG/PNG, 10 MB) kept in the document vault, searchable and shareable through a link that expires.
- Reminders go to the owner only (the hub's WhatsApp owner-alert template, plus in the hub) 30, 7 and 1 days before and on
  the day; items due within 7 days also show in the morning digest and on the Dashboard.

compliance_items {_id, owner_id, rule_key|null, title, category, period, due_date "YYYY-MM-DD", recurrence none|monthly|quarterly|yearly,
                  reminder_days [30,7,1,0], status upcoming|done, doc_file_id|null, penalty_note, plain, authority, source_url,
                  source rule|owner, amount|null (₹, for Cash flow), done_at, notes, reminded [offsets], rules_version}
"""
import hashlib
import json
import re
from collections import Counter
from datetime import date, timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from pymongo.errors import DuplicateKeyError

from .. import compliance_rules as rules_mod, files, knowledge, plans
from ..agents import catalog
from ..agents.catalog import Skip
from ..ai.provider import complete
from ..ai.tasks import RULES, profile_text
from ..config import settings
from ..context import Ctx, feature, get_ctx
from ..db import IST, db, now, public
from ..messaging import whatsapp_template
from ..security import require_admin
from ..services import new_id, notify, run_ai, track

router = APIRouter(prefix="/api")
CATEGORIES = ("tax", "labour", "licence", "company", "insurance", "agreement", "document", "other")
RECURRENCE = {"none": 0, "monthly": 1, "quarterly": 3, "yearly": 12}


def today() -> date:
    return now().astimezone(IST).date()


# ───────────────────────── the rules (defaults + the admin's edits) ─────────────────────────
async def rules() -> list[dict]:
    return rules_mod.merged([r async for r in db().compliance_rules.find()])


async def rules_version() -> str:
    review = await db().config.find_one({"_id": "compliance_review"}) or {}
    saved = [r async for r in db().compliance_rules.find().sort("key", 1)]
    raw = json.dumps([{k: v for k, v in r.items() if k not in ("_id", "updated_at")} for r in saved], sort_keys=True, default=str)
    return hashlib.sha1((raw + str(review.get("rules_changed_at"))).encode()).hexdigest()[:12]


# ───────────────────────── generation ─────────────────────────
async def generate(ws: str, identity: dict | None = None) -> int:
    """(Re)builds the rule items for the next 12 months from the business identity. Done items, and anything the owner added
    to a rule item (an amount, notes, a document), are kept."""
    if identity is None:
        b = await db().businesses.find_one({"owner_id": ws}, {"identity": 1}) or {}
        identity = b.get("identity")
    if not identity:
        return 0
    start, end = today(), today() + timedelta(days=365)
    version = await rules_version()
    occurrences = []
    for r in await rules():
        if not rules_mod.applies(r, identity) or (r.get("due") or {}).get("type") == "owner_date":
            continue
        for label, when in rules_mod.due_dates(r, start, end):
            occurrences.append((r, label, when.isoformat()))
    # an item is "the same filing" by its rule and period (GSTR-3B for Sep 2026), so a government extension moves its date
    # instead of making a second item; a rule without period labels falls back to the date
    labels = Counter((r["key"], label) for r, label, _ in occurrences if label)

    def key(rk: str, label: str, due: str | None) -> tuple:
        return (rk, "period", label) if label and labels[(rk, label)] == 1 else (rk, "date", due)
    wanted = {key(r["key"], label, due): (r, label, due) for r, label, due in occurrences}
    existing = {key(i["rule_key"], i.get("period") or "", i["due_date"]): i
                async for i in db().compliance_items.find({"owner_id": ws, "source": "rule"})}
    for k, item in existing.items():
        keep = item.get("status") == "done" or item.get("amount") or item.get("notes") or item.get("doc_file_id")
        if k not in wanted and not keep and (item.get("due_date") or "") >= start.isoformat():
            await db().compliance_items.delete_one({"_id": item["_id"]})
    made = 0
    for k, (r, label, due) in wanted.items():
        rk = r["key"]
        fields = {"title": r["title"], "category": r.get("category", "tax"), "period": label, "penalty_note": r.get("penalty_note", ""),
                  "plain": r.get("plain", ""), "authority": r.get("authority", ""), "source_url": r.get("source_url", ""), "rules_version": version}
        if k in existing:
            item = existing[k]
            if item.get("due_date") != due and item.get("status") != "done":   # the date moved (an extension): the item follows
                fields.update(due_date=due, reminded=[])
            try:
                await db().compliance_items.update_one({"_id": item["_id"]}, {"$set": fields})
            except DuplicateKeyError:
                pass
            continue
        try:
            await db().compliance_items.insert_one({"_id": new_id(), "owner_id": ws, "rule_key": rk, "due_date": due, "recurrence": "none",
                                                    "reminder_days": list(settings.compliance_reminder_days), "status": "upcoming",
                                                    "doc_file_id": None, "source": "rule", "amount": None, "done_at": None, "notes": "",
                                                    "reminded": [], "created_at": now(), **fields})
            made += 1
        except DuplicateKeyError:
            pass
    made += await _renewals(ws, identity)
    await db().compliance_state.update_one({"_id": ws}, {"$set": {"rules_version": version, "generated_at": now()}}, upsert=True)
    return made


async def _renewals(ws: str, identity: dict) -> int:
    """A licence the owner ticked in the Knowledge Hub (FSSAI, Shops & Establishment…) gets a 'needs a date' item: renewal
    dates differ per licence, so the owner types the expiry date from the certificate — the hub never guesses it."""
    made = 0
    for r in await rules():
        if (r.get("due") or {}).get("type") != "owner_date":
            continue
        pid = f"{ws}:renewal:{r['key']}"
        if rules_mod.applies(r, identity):
            if await db().compliance_items.count_documents({"owner_id": ws, "rule_key": r["key"]}, limit=1):
                continue
            try:
                await db().compliance_items.insert_one({"_id": pid, "owner_id": ws, "rule_key": r["key"], "due_date": None,
                                                        "reminder_days": list(settings.compliance_reminder_days), "status": "upcoming",
                                                        "doc_file_id": None, "source": "renewal", "amount": None, "done_at": None, "notes": "",
                                                        "recurrence": "none",
                                                        "reminded": [], "created_at": now(), "title": r["title"], "category": r.get("category", "licence"),
                                                        "period": "", "penalty_note": r.get("penalty_note", ""), "plain": r.get("plain", ""),
                                                        "authority": r.get("authority", ""), "source_url": r.get("source_url", "")})
                made += 1
            except DuplicateKeyError:
                pass
        else:   # unticked: an empty reminder goes; one with a date or a document stays
            await db().compliance_items.delete_one({"_id": pid, "due_date": None, "doc_file_id": None})
    return made


async def _fresh(ws: str) -> None:
    """Rebuilds when the admin changed the rules since this owner's calendar was made."""
    state = await db().compliance_state.find_one({"_id": ws}) or {}
    if state.get("rules_version") != await rules_version() or not state.get("generated_at") or state["generated_at"] < now() - timedelta(days=7):
        await generate(ws)


async def _has_identity(ws: str) -> bool:
    b = await db().businesses.find_one({"owner_id": ws}, {"identity": 1}) or {}
    return bool(b.get("identity"))


async def _never_edited(ws: str) -> bool:
    return False


async def _regenerate(owner: dict, sections: set) -> None:
    await generate(owner["_id"])


knowledge.register(knowledge.Dependent("compliance", "Compliance calendar", "compliance_calendar", "/compliance", {"identity"}, _has_identity,
                                       _never_edited, _regenerate, note="Your own items and documents are never changed.", order=3,
                                       cost=lambda sections: 0))


# ───────────────────────── reading ─────────────────────────
def status_of(item: dict, t: date | None = None) -> str:
    if item.get("status") == "done":
        return "done"
    if not item.get("due_date"):
        return "needs_date"
    return "overdue" if item["due_date"] < (t or today()).isoformat() else "upcoming"


def view(item: dict, t: date | None = None) -> dict:
    out = public(item)
    out["status"] = status_of(item, t)
    if item.get("due_date"):
        out["days_left"] = (date.fromisoformat(item["due_date"]) - (t or today())).days
    return out


async def review_stamp() -> dict:
    r = await db().config.find_one({"_id": "compliance_review"}) or {}
    if r.get("reviewed_by") and r.get("reviewed_at"):
        return {"reviewed": True, "reviewed_by": r["reviewed_by"], "reviewed_at": r["reviewed_at"].isoformat(),
                "line": f"Dates reviewed by a CA on {r['reviewed_at'].astimezone(IST).strftime('%d %b %Y')}. Confirm with your CA."}
    return {"reviewed": False, "line": "These dates have not been reviewed by a CA yet. Confirm every date with your CA."}


@router.get("/compliance")
async def get_compliance(days: int = 90, ctx: Ctx = Depends(feature("compliance_calendar", "manager"))):
    await _fresh(ctx.ws)
    t = today()
    days = max(7, min(days, 400))
    until = (t + timedelta(days=days)).isoformat()
    items = [view(i, t) async for i in db().compliance_items.find({"owner_id": ctx.ws}).sort("due_date", 1).limit(2000)]
    upcoming = [i for i in items if i["status"] in ("upcoming", "overdue") and i.get("due_date", "9999") <= until]
    soon = (t + timedelta(days=30)).isoformat()
    expiring = [i for i in items if i.get("doc_file_id") and i["status"] != "done" and i.get("due_date") and i["due_date"] <= soon]
    b = await db().businesses.find_one({"owner_id": ctx.ws}, {"identity": 1}) or {}
    return {"items": upcoming, "all": items, "overdue": [i for i in items if i["status"] == "overdue"],
            "needs_date": [i for i in items if i["status"] == "needs_date"], "expiring_docs": expiring,
            "identity": b.get("identity"), "review": await review_stamp(),
            "renewals": [{"key": r["key"], "title": r["title"], "registration": (r.get("applies") or {}).get("registration")}
                         for r in await rules() if (r.get("due") or {}).get("type") == "owner_date" and r.get("on", True)],
            "categories": list(CATEGORIES), "label": "Dates come from the rules table, not from AI."}


@router.get("/compliance/{month}")
async def compliance_month(month: str, ctx: Ctx = Depends(feature("compliance_calendar", "manager"))):
    if not re.fullmatch(r"20\d\d-(0[1-9]|1[0-2])", month):
        raise HTTPException(400, "Use a month like 2026-11")
    await _fresh(ctx.ws)
    t = today()
    return [view(i, t) async for i in db().compliance_items.find({"owner_id": ctx.ws, "due_date": {"$regex": f"^{month}-"}}).sort("due_date", 1)]


# ───────────────────────── owner items and documents ─────────────────────────
class ItemIn(BaseModel):
    title: str = Field(min_length=2, max_length=120)
    category: str = Field(default="licence")
    due_date: str = Field(max_length=10)
    recurrence: str = Field(default="yearly", pattern="^(none|monthly|quarterly|yearly)$")
    reminder_days: list[int] = Field(default_factory=lambda: list(settings.compliance_reminder_days), max_length=8)
    notes: str = Field(default="", max_length=1000)
    amount: float | None = Field(default=None, ge=0, le=1e10)
    rule_key: str | None = Field(default=None, max_length=40)   # a renewal from the rules table (FSSAI, trade licence…)

    @field_validator("category")
    @classmethod
    def known(cls, v):
        if v not in CATEGORIES:
            raise ValueError("Pick a category from the list")
        return v

    @field_validator("due_date")
    @classmethod
    def day(cls, v):
        try:
            return date.fromisoformat(v).isoformat()
        except ValueError:
            raise ValueError("Use a date like 2027-03-31")

    @field_validator("reminder_days")
    @classmethod
    def offsets(cls, v):
        return sorted({d for d in v if 0 <= d <= 365}, reverse=True) or [30, 7, 1, 0]


class ItemPatch(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=120)
    due_date: str | None = Field(default=None, max_length=10)
    notes: str | None = Field(default=None, max_length=1000)
    amount: float | None = Field(default=None, ge=0, le=1e10)
    reminder_days: list[int] | None = Field(default=None, max_length=8)
    recurrence: str | None = Field(default=None, pattern="^(none|monthly|quarterly|yearly)$")   # your own items and licence renewals

    @field_validator("due_date")
    @classmethod
    def day(cls, v):
        if v is None:
            return v
        try:
            return date.fromisoformat(v).isoformat()
        except ValueError:
            raise ValueError("Use a date like 2027-03-31")


@router.post("/compliance/items")
async def add_item(body: ItemIn, ctx: Ctx = Depends(feature("compliance_calendar", "manager"))):
    r = None
    if body.rule_key:
        r = next((x for x in await rules() if x["key"] == body.rule_key), None)
        if not r:
            raise HTTPException(400, "Unknown renewal")
    doc = {"_id": new_id(), "owner_id": ctx.ws, "rule_key": body.rule_key, "title": body.title.strip(), "category": body.category,
           "period": "", "due_date": body.due_date, "recurrence": body.recurrence, "reminder_days": body.reminder_days, "status": "upcoming",
           "doc_file_id": None, "penalty_note": (r or {}).get("penalty_note", ""), "plain": (r or {}).get("plain", ""),
           "authority": (r or {}).get("authority", ""), "source_url": (r or {}).get("source_url", ""), "source": "owner",
           "amount": body.amount, "done_at": None, "notes": body.notes.strip(), "reminded": [], "created_by": ctx.actor, "created_at": now()}
    await db().compliance_items.insert_one(doc)
    await track("compliance_item", ctx.ws)
    return view(doc)


@router.patch("/compliance/items/{item_id}")
async def edit_item(item_id: str, body: ItemPatch, ctx: Ctx = Depends(feature("compliance_calendar", "manager"))):
    item = await db().compliance_items.find_one({"_id": item_id, "owner_id": ctx.ws})
    if not item:
        raise HTTPException(404, "Not found")
    patch = body.model_dump(exclude_none=True)
    if item["source"] == "rule":   # a statutory date comes from the rules table, never typed
        patch.pop("due_date", None)
        patch.pop("title", None)
        patch.pop("recurrence", None)
    if "due_date" in patch and patch["due_date"] != item.get("due_date"):
        patch["reminded"] = []
    if "amount" in patch and patch["amount"] != item.get("amount"):
        patch["amount_source"] = "owner"   # the owner's own figure: GST set-aside never overwrites it without asking
    if patch:
        await db().compliance_items.update_one({"_id": item_id}, {"$set": {**patch, "updated_at": now()}})
    return view(await db().compliance_items.find_one({"_id": item_id}))


def _roll(day: str, months: int) -> str:
    d = date.fromisoformat(day)
    y, m = rules_mod._add_months(d.year, d.month, months)
    return rules_mod._on(y, m, d.day).isoformat()


@router.post("/compliance/items/{item_id}/done")
async def mark_done(item_id: str, receipt: UploadFile | None = File(default=None), ctx: Ctx = Depends(feature("compliance_calendar", "manager"))):
    """Done (optionally with the acknowledgement or receipt). A recurring owner item rolls forward to its next date."""
    item = await db().compliance_items.find_one({"_id": item_id, "owner_id": ctx.ws})
    if not item:
        raise HTTPException(404, "Not found")
    patch = {"status": "done", "done_at": now(), "done_by": ctx.actor}
    if receipt is not None and receipt.filename:
        data, mime = await files.read_upload(receipt, ("application/pdf", "image/png", "image/jpeg"))
        f = await files.put(ctx.ws, data, receipt.filename, mime, "compliance_receipt", item_id=item_id)
        patch["receipt_file_id"] = f["_id"]
    r = await db().compliance_items.update_one({"_id": item_id, "status": {"$ne": "done"}}, {"$set": patch})
    nxt = None
    if r.modified_count and item["source"] in ("owner", "renewal") and RECURRENCE.get(item.get("recurrence"), 0) and item.get("due_date"):
        nxt = {**{k: v for k, v in item.items() if k not in ("_id", "done_at", "done_by", "receipt_file_id")}, "_id": new_id(), "status": "upcoming",
               "due_date": _roll(item["due_date"], RECURRENCE[item["recurrence"]]), "reminded": [], "created_at": now(), "rolled_from": item_id}
        await db().compliance_items.insert_one(nxt)
    elif r.modified_count and item["source"] == "renewal":
        # renewed: the next expiry is on the new certificate, so the hub asks for it rather than guessing a period
        nxt = {**{k: v for k, v in item.items() if k not in ("_id", "done_at", "done_by", "receipt_file_id", "doc_file_id")}, "_id": new_id(),
               "status": "upcoming", "due_date": None, "reminded": [], "created_at": now(), "rolled_from": item_id, "doc_file_id": None}
        await db().compliance_items.insert_one(nxt)
    await track("compliance_done", ctx.ws)
    return {"item": view(await db().compliance_items.find_one({"_id": item_id})), "next": view(nxt) if nxt else None}


@router.post("/compliance/items/{item_id}/undo")
async def undo_done(item_id: str, ctx: Ctx = Depends(feature("compliance_calendar", "manager"))):
    r = await db().compliance_items.update_one({"_id": item_id, "owner_id": ctx.ws}, {"$set": {"status": "upcoming", "done_at": None}})
    if not r.matched_count:
        raise HTTPException(404, "Not found")
    await db().compliance_items.delete_one({"owner_id": ctx.ws, "rolled_from": item_id, "status": "upcoming"})
    return view(await db().compliance_items.find_one({"_id": item_id, "owner_id": ctx.ws}) or {})


@router.delete("/compliance/items/{item_id}")
async def delete_item(item_id: str, ctx: Ctx = Depends(feature("compliance_calendar", "manager"))):
    item = await db().compliance_items.find_one({"_id": item_id, "owner_id": ctx.ws})
    if not item:
        raise HTTPException(404, "Not found")
    if item["source"] == "rule":
        raise HTTPException(400, "Statutory items come from your business details. Change them in the Knowledge Hub.")
    await db().compliance_items.delete_one({"_id": item_id})
    if item.get("doc_file_id"):
        await files.delete(item["doc_file_id"], ctx.ws)
    return {"ok": True}


@router.post("/compliance/items/{item_id}/document")
async def attach_document(item_id: str, file: UploadFile = File(...), ctx: Ctx = Depends(feature("compliance_calendar", "manager"))):
    item = await db().compliance_items.find_one({"_id": item_id, "owner_id": ctx.ws})
    if not item:
        raise HTTPException(404, "Not found")
    data, mime = await files.read_upload(file, ("application/pdf", "image/png", "image/jpeg"))
    f = await files.put(ctx.ws, data, file.filename or "document", mime, "compliance", item_id=item_id, title=item["title"])
    if item.get("doc_file_id"):
        await files.delete(item["doc_file_id"], ctx.ws)
    await db().compliance_items.update_one({"_id": item_id}, {"$set": {"doc_file_id": f["_id"], "doc_name": f["filename"]}})
    return view(await db().compliance_items.find_one({"_id": item_id}))


@router.get("/compliance-documents")
async def documents(q: str = "", ctx: Ctx = Depends(feature("compliance_calendar", "manager"))):
    query: dict = {"owner_id": ctx.ws, "doc_file_id": {"$ne": None}}
    if q.strip():
        safe = re.escape(q.strip()[:60])
        query["$or"] = [{"title": {"$regex": safe, "$options": "i"}}, {"doc_name": {"$regex": safe, "$options": "i"}},
                        {"notes": {"$regex": safe, "$options": "i"}}, {"category": q.strip().lower()}]
    t = today()
    return [view(i, t) async for i in db().compliance_items.find(query).sort("due_date", 1).limit(500)]


@router.get("/compliance-documents/{file_id}")
async def download(file_id: str, ctx: Ctx = Depends(feature("compliance_calendar", "manager"))):
    f = await files.get(file_id, ctx.ws)
    if not f:
        raise HTTPException(404, "Not found")
    return Response(bytes(f["data"]), media_type=f["content_type"], headers={"Content-Disposition": _disposition(f["filename"]), "Cache-Control": "private"})


@router.post("/compliance-documents/{file_id}/share")
async def share(file_id: str, ctx: Ctx = Depends(feature("compliance_calendar", "manager"))):
    if not await files.get(file_id, ctx.ws):
        raise HTTPException(404, "Not found")
    await track("compliance_share", ctx.ws)
    return {"url": files.share_url(file_id, 60 * 24 * 7), "expires_in_days": 7}


def _disposition(name: str) -> str:
    from urllib.parse import quote
    plain = re.sub(r"[^A-Za-z0-9 ._-]", "", name).strip() or "document"
    return f"inline; filename=\"{plain}\"; filename*=UTF-8''{quote(name)}"


@router.get("/files/s/{token}", include_in_schema=False)
async def shared_file(token: str):
    file_id = files.check_token(token)
    f = await files.get(file_id) if file_id else None
    if not f:
        raise HTTPException(404, "This link has expired or doesn't exist.")
    return Response(bytes(f["data"]), media_type=f["content_type"], headers={"Content-Disposition": _disposition(f["filename"]),
                                                                              "Cache-Control": "private, no-store", "X-Robots-Tag": "noindex"})


# ───────────────────────── "Explain for my business" (AI: words only, never dates) ─────────────────────────
async def explain_task(profile: dict, items: list[dict]):
    def mock():   # a real engine may also send dates back: they're thrown away below
        return {"items": [{"key": i["rule_key"], "explain": f"For {profile.get('name', 'your business')}: {i['plain']}", "due_date": "2099-01-01"}
                          for i in items]}
    listing = "\n".join(f"- {i['rule_key']}: {i['title']} — {i['plain']}" for i in items)
    return await complete(RULES, f"""{profile_text(profile)}

These statutory items apply to this business:
{listing}

For each, write one or two plain sentences on what it means for THIS business. Do not give dates, amounts or penalties; they come from elsewhere.
JSON: {{"items": [{{"key": "the key as given", "explain": "..."}}]}}""", mock, 2500)


@router.post("/compliance/explain")
async def explain(ctx: Ctx = Depends(feature("compliance_calendar", "manager"))):
    from .hub import profile_of
    profile = await profile_of(ctx.ws)
    seen, items = set(), []
    async for i in db().compliance_items.find({"owner_id": ctx.ws, "source": "rule", "status": {"$ne": "done"}}):
        if i["rule_key"] not in seen:
            seen.add(i["rule_key"])
            items.append(i)
    if not items:
        raise HTTPException(400, "Add your business identity in the Knowledge Hub first.")

    def shape(r):
        out = {}
        for x in (r.get("items") or []) if isinstance(r, dict) else []:
            if isinstance(x, dict) and x.get("key") in seen and str(x.get("explain") or "").strip():
                out[x["key"]] = re.sub(r"\s+", " ", str(x["explain"])).strip()[:400]     # only the words; any date is dropped
        if not out:
            raise ValueError("nothing usable")
        return out
    words = await run_ai(ctx.owner, "compliance_explain", explain_task(profile, items), "Compliance calendar explained", save_output=False, check=shape)
    for key, text in words.items():
        await db().compliance_items.update_many({"owner_id": ctx.ws, "rule_key": key}, {"$set": {"explain": text}})
    return {"explained": len(words)}


# ───────────────────────── reminders (owner only) ─────────────────────────
async def compliance_reminders(owner: dict, run: dict):
    ws, t = owner["_id"], run["at"].astimezone(IST).date()
    await _fresh(ws)
    sent = []
    async for item in db().compliance_items.find({"owner_id": ws, "status": {"$ne": "done"}, "due_date": {"$gte": t.isoformat(),
                                                                                                            "$lte": (t + timedelta(days=366)).isoformat()}}):
        left = (date.fromisoformat(item["due_date"]) - t).days
        offsets = item.get("reminder_days") or list(settings.compliance_reminder_days)
        if left not in offsets or left in (item.get("reminded") or []):
            continue
        r = await db().compliance_items.update_one({"_id": item["_id"], "reminded": {"$ne": left}}, {"$push": {"reminded": left}})
        if r.modified_count:
            sent.append((left, item))
    if not sent:
        raise Skip("Nothing due on a reminder day.")
    lines = [f"{i['title']}{' (' + i['period'] + ')' if i.get('period') else ''} — {'today' if left == 0 else f'in {left} day' + ('s' if left != 1 else '')}"
             f" ({date.fromisoformat(i['due_date']).strftime('%d %b')})" for left, i in sorted(sent, key=lambda x: x[0])]
    text = "Compliance: " + "; ".join(lines[:6]) + (f"; and {len(lines) - 6} more" if len(lines) > 6 else "")
    b = await db().businesses.find_one({"owner_id": ws}, {"name": 1}) or {}
    wa = await whatsapp_template(owner["phone"], settings.aisensy_owner_alert_campaign, owner.get("name", ""), [b.get("name") or "your business", text[:900]],
                                 "compliance_reminder")
    await notify(ws, text[:600])
    return f"{len(sent)} compliance reminder{'s' if len(sent) != 1 else ''} sent to you." + ("" if wa["sent"] else " (Shown in the hub; WhatsApp isn't set up.)")


async def _ready(owners: list[dict]) -> set:
    ids = [o["_id"] for o in owners]
    return set(await db().compliance_items.distinct("owner_id", {"owner_id": {"$in": ids}, "status": {"$ne": "done"}, "due_date": {"$ne": None}}))


catalog.register("compliance_reminders", {
    "name": "Compliance reminders", "feature": "compliance_calendar", "trigger": "schedule",
    "what": "Reminds you on WhatsApp before every filing, licence renewal and document expiry: 30, 7 and 1 days before, and on the day.",
    "when": "Every day at 9am", "schedule": {"every": "day", "at": "09:00", "grace_hours": 6}, "ready": _ready, "group": "alerts"}, compliance_reminders)


async def due_soon(ws: str, days: int = 7) -> list[dict]:
    t = today()
    return [view(i, t) async for i in db().compliance_items.find({"owner_id": ws, "status": {"$ne": "done"},
                                                                   "due_date": {"$lte": (t + timedelta(days=days)).isoformat()}}).sort("due_date", 1).limit(50)]


# ───────────────────────── Admin → Compliance rules ─────────────────────────
admin = APIRouter(prefix="/api/admin/compliance", dependencies=[Depends(require_admin)])


class RuleIn(BaseModel):
    title: str = Field(min_length=3, max_length=120)
    category: str = Field(default="tax")
    authority: str = Field(default="", max_length=120)
    applies: dict = Field(default_factory=dict)
    due: dict
    plain: str = Field(default="", max_length=500)
    penalty_note: str = Field(default="", max_length=300)
    source_url: str = Field(default="", max_length=300)
    on: bool = True

    @field_validator("due")
    @classmethod
    def due_rule(cls, v):
        kind = v.get("type")
        if kind not in rules_mod.DUE_TYPES:
            raise ValueError("Unknown due-date rule")
        if kind in ("monthly", "quarterly", "qrmp_payment") and not (1 <= int(v.get("day", 0)) <= 31):
            raise ValueError("Give the day of the month, 1 to 31")
        if kind == "dates" and not all(re.fullmatch(r"(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])", str(d)) for d in v.get("dates") or [None]):
            raise ValueError("Dates look like 07-31")
        for mm, md in (v.get("exceptions") or {}).items():
            try:
                date(2028, int(md[:2]), int(md[3:]))   # a leap year, so 02-29 is allowed
                if not (1 <= int(mm) <= 12) or len(md) != 5 or md[2] != "-":
                    raise ValueError
            except (ValueError, TypeError, IndexError):
                raise ValueError("Exceptions look like {\"03\": \"04-30\"}")
        return v

    @field_validator("applies")
    @classmethod
    def conditions(cls, v):
        allowed = {"entity", "gst", "scheme", "staff_min", "states", "states_not", "registration", "or_registration"}
        if set(v) - allowed:
            raise ValueError(f"Conditions can use: {', '.join(sorted(allowed))}")
        types = {"entity": list, "states": list, "states_not": list, "gst": bool, "staff_min": int, "scheme": str, "registration": str,
                 "or_registration": str}
        for k, t in types.items():   # a wrong type here would break every owner's calendar, so it's refused at the door
            if k in v and (not isinstance(v[k], t) or (t is int and isinstance(v[k], bool)) or (t is list and not all(isinstance(x, str) for x in v[k]))):
                raise ValueError(f"{k} should be {'a list of names' if t is list else t.__name__}")
        return v


class OverrideIn(BaseModel):
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")       # the due date as the table gives it
    new_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")   # the extended date (None removes the override)

    @field_validator("date", "new_date")
    @classmethod
    def real_day(cls, v):
        if v is not None:
            date.fromisoformat(v)   # 2026-02-30 is refused here, not discovered on every owner's page
        return v


class ReviewIn(BaseModel):
    reviewed_by: str = Field(min_length=3, max_length=120)   # e.g. "CA Priya Mehta, M. No. 123456"


async def _changed() -> None:
    await db().config.update_one({"_id": "compliance_review"}, {"$set": {"rules_changed_at": now()}}, upsert=True)


@admin.get("/rules")
async def admin_rules():
    return {"rules": await rules(), "review": await review_stamp(), "due_types": list(rules_mod.DUE_TYPES), "states": knowledge.STATES,
            "entities": [k for k, _ in knowledge.ENTITIES], "registrations": [k for k, _ in knowledge.REGISTRATIONS]}


@admin.put("/rules/{key}")
async def save_rule(key: str, body: RuleIn, user: dict = Depends(require_admin)):
    if not re.fullmatch(r"[a-z0-9_]{2,40}", key):
        raise HTTPException(400, "Keys use a–z, 0–9 and _")
    current = next((r for r in await rules() if r["key"] == key), None)
    doc = {"key": key, **body.model_dump(), "overrides": (current or {}).get("overrides", {}), "updated_at": now(), "updated_by": user["_id"]}
    await db().compliance_rules.replace_one({"key": key}, doc, upsert=True)
    await _changed()
    return next(r for r in await rules() if r["key"] == key)


@admin.put("/rules/{key}/override")
async def override(key: str, body: OverrideIn, user: dict = Depends(require_admin)):
    """A government extension: one due date moves. Every owner's calendar picks it up on its next view or reminder run."""
    current = next((r for r in await rules() if r["key"] == key), None)
    if not current:
        raise HTTPException(404, "Unknown rule")
    overrides = dict(current.get("overrides") or {})
    if body.new_date:
        overrides[body.date] = body.new_date
    else:
        overrides.pop(body.date, None)
    await db().compliance_rules.update_one({"key": key}, {"$set": {**{k: v for k, v in current.items() if k not in ("overrides",)},
                                                                   "overrides": overrides, "updated_at": now(), "updated_by": user["_id"]}}, upsert=True)
    await _changed()
    return next(r for r in await rules() if r["key"] == key)


@admin.post("/review")
async def review(body: ReviewIn, user: dict = Depends(require_admin)):
    await db().config.update_one({"_id": "compliance_review"}, {"$set": {"reviewed_by": body.reviewed_by.strip(), "reviewed_at": now(),
                                                                          "recorded_by": user["_id"]}}, upsert=True)
    return await review_stamp()


async def ensure_indexes() -> None:
    d = db()
    await d.compliance_items.create_index([("owner_id", 1), ("due_date", 1)])
    await d.compliance_items.create_index("status")
    await d.compliance_items.create_index([("owner_id", 1), ("rule_key", 1), ("due_date", 1)], unique=True,
                                          partialFilterExpression={"source": "rule"})
    await d.compliance_rules.create_index("key", unique=True)
