"""
Knowledge Hub — the Business Brain, filled once and used by the whole hub.

The Business Brain document (`businesses`) keeps the basics it always had (name, city, what the business does, offers,
who buys, why us, proof, contact) and gains four optional sections:

    identity  legal entity, GST (and number, monthly or QRMP), state, staff, registrations held   → Compliance calendar
    money     usual payment terms, bank balance (with its date), fixed monthly costs, usual GST rate → Cash flow, invoices
    targets   monthly revenue target (the same number Magic Number uses)                             → Am I on target
    brand     the chosen brand message and the tone of voice (written in the Brand kit)              → every AI writer

Propagation ("the rest of the hub updates"): every save records a new `brain_version` and the sections that changed.
Each output built from the Brain registers here as a dependent (website copy, brand kit, compliance calendar, SOP map)
with what it watches, whether the owner has edited it by hand, how to refresh it, and how many AI runs a refresh uses:
- a refresh that uses no AI run (the compliance calendar; a phone or email change) → done on save, as before;
- one that uses AI runs → never on save: it waits, and "Update my hub" on the Knowledge Hub shows the runs it will use
  before it runs (the owner presses it when they're done editing — not on every save);
- edited by hand → kept, and a banner offers "Refresh" or "Keep mine". Owner edits are never overwritten silently.
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Awaitable, Callable

from fastapi import HTTPException

from . import plans
from .db import db, now

log = logging.getLogger("knowledge")

ENTITIES = [("proprietor", "Proprietorship"), ("partnership", "Partnership firm"), ("llp", "LLP"), ("pvt_ltd", "Private limited company"),
            ("opc", "One-person company"), ("other", "Other")]
STATES = ["Andaman and Nicobar Islands", "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chandigarh", "Chhattisgarh",
          "Dadra and Nagar Haveli and Daman and Diu", "Delhi", "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jammu and Kashmir", "Jharkhand",
          "Karnataka", "Kerala", "Ladakh", "Lakshadweep", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Odisha",
          "Puducherry", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal"]
REGISTRATIONS = [("udyam", "Udyam (MSME)"), ("fssai", "FSSAI food licence"), ("shops", "Shops & Establishment"), ("trade_licence", "Trade licence"),
                 ("iec", "Import Export Code (IEC)"), ("pt", "Professional tax registration"), ("pf", "Provident Fund (EPF)"), ("esi", "ESI"),
                 ("trademark", "Trademark"), ("fire_noc", "Fire NOC"), ("pollution", "Pollution consent"), ("drug_licence", "Drug licence")]
COST_KINDS = [("salary", "Salaries"), ("rent", "Rent"), ("emi", "EMI / loan"), ("subscription", "Subscription"), ("utility", "Electricity / utilities"),
              ("other", "Other")]

BASICS = ("name", "city", "industry", "offers", "ideal_customer", "why_us", "proof", "language")
CONTACT = ("whatsapp", "email", "review_link", "payment_note")
SECTIONS = ("basics", "contact", "identity", "money", "targets", "brand")


# ───────────────────────── completeness ─────────────────────────
def _text(v) -> bool:
    return bool(str(v or "").strip())


def completeness(b: dict | None, voice: dict | None = None, target: float | None = None) -> dict:
    """How full the Brain is, section by section, and what each missing section switches on."""
    b = b or {}
    ident, money = b.get("identity") or {}, b.get("money") or {}
    target = target if target is not None else float((b.get("targets") or {}).get("monthly_revenue") or 0)
    voice = voice if voice is not None else (b.get("voice") or {})
    num = (int, float)
    rows = [
        ("basics", "Your business", [("Name", _text(b.get("name"))), ("What you do", _text(b.get("industry"))), ("City", _text(b.get("city"))),
                                     ("Offers", any(_text(o.get("name")) for o in b.get("offers") or [])),
                                     ("Who buys", _text(b.get("ideal_customer"))), ("Why customers choose you", _text(b.get("why_us")))],
         ("website", "Website", "/website")),
        ("contact", "Contact", [("WhatsApp number", _text(b.get("whatsapp"))), ("Email", _text(b.get("email")))], ("leads", "Leads", "/leads")),
        ("identity", "Business identity", [("Legal entity", _text(ident.get("entity"))), ("GST registration", isinstance(ident.get("gst_registered"), bool)),
                                           ("State", _text(ident.get("state"))), ("Number of staff", isinstance(ident.get("staff"), int))],
         ("compliance_calendar", "Compliance calendar", "/compliance")),
        ("money", "Money basics", [("Usual payment terms", isinstance(money.get("payment_terms_days"), int)),
                                   ("Bank balance", _text(money.get("bank_balance_on")) and isinstance(money.get("bank_balance"), num)),
                                   ("Fixed monthly costs", bool(money.get("fixed_costs"))), ("Usual GST rate", isinstance(money.get("gst_rate"), num))],
         ("cash_flow", "Cash flow", "/cash-flow")),
        ("targets", "Targets", [("Monthly revenue target", target > 0)], ("magic_number", "Am I on target", "/magic")),
        ("brand", "Brand", [("Brand message", _text((b.get("brand") or {}).get("message"))), ("Tone of voice", _text(voice.get("tone")))],
         ("brand_kit", "Brand kit", "/brand-kit")),
    ]
    sections, done_n, total = [], 0, 0
    for key, label, fields, (feat, feat_label, path) in rows:
        state = [{"label": lab, "done": bool(ok)} for lab, ok in fields]
        n = sum(x["done"] for x in state)
        done_n += n
        total += len(state)
        sections.append({"key": key, "label": label, "fields": state, "done": n == len(state), "missing": [x["label"] for x in state if not x["done"]],
                         "unlocks": {"feature": feat, "label": feat_label, "path": path}})
    percent = round(done_n / total * 100)
    nxt = next((x for x in sections if not x["done"]), None)
    hint = f"{percent}% complete — add your {nxt['missing'][0].lower()} to switch on {nxt['unlocks']['label']}." if nxt else "100% complete."
    return {"percent": percent, "sections": sections, "next": nxt["key"] if nxt else None, "hint": hint}


async def completeness_for(ws: str, b: dict | None = None) -> dict:
    b = b if b is not None else (await db().businesses.find_one({"owner_id": ws}) or {})
    target = float((b.get("targets") or {}).get("monthly_revenue") or 0)
    if not target:
        m = await db().magic.find_one({"_id": ws}, {"inputs": 1}) or {}
        target = float((m.get("inputs") or {}).get("monthly_target") or 0)
    return completeness(b, b.get("voice") or {}, target)


# ───────────────────────── what changed ─────────────────────────
def changed_sections(before: dict | None, after: dict) -> set[str]:
    """Which Brain sections a save changed. Only the fields the save carries are compared (a save of the basics says
    nothing about the brand, so a missing key is never read as 'cleared')."""
    before = before or {}
    out = set()
    def differs(k: str) -> bool:      # "" / [] / missing all mean "not filled in"
        return k in after and (before.get(k) or None) != (after[k] or None)
    if any(differs(k) for k in BASICS):
        out.add("basics")
    if any(differs(k) for k in CONTACT):
        out.add("contact")
    for sec, key in (("identity", "identity"), ("money", "money"), ("targets", "targets"), ("brand", "brand"), ("brand", "voice")):
        if differs(key):
            out.add(sec)
    return out


# ───────────────────────── dependents ─────────────────────────
@dataclass
class Dependent:
    key: str
    label: str
    feature: str
    path: str
    watches: set[str]
    exists: Callable[[str], Awaitable[bool]]
    edited: Callable[[str], Awaitable[bool]]
    refresh: Callable[[dict, set[str]], Awaitable[str | None]]  # (owner, changed sections) — never replaces what the owner wrote;
                                                                # returns "kept_edits" when some of it was left as the owner wrote it
    overwrite: Callable[[dict], Awaitable[None]] | None = None  # "Refresh" on the banner: the owner chose to replace their edits
    cost: Callable[[set[str]], int] | None = None    # AI runs a refresh for these sections uses (default 1); 0 = done on save
    has_edits: Callable[[str], Awaitable[bool]] | None = None   # anything the owner wrote themselves (default: `edited`)
    note: str = ""
    order: int = 0
    meta: dict = field(default_factory=dict)


DEPENDENTS: dict[str, Dependent] = {}


def register(dep: Dependent) -> None:
    DEPENDENTS[dep.key] = dep


async def bump(ws: str, sections: set[str]) -> int:
    """A new Brain version, recording which sections changed."""
    b = await db().businesses.find_one_and_update({"owner_id": ws}, {"$inc": {"brain_version": 1},
                                                                     "$set": {"brain_changed": sorted(sections), "brain_changed_at": now()}},
                                                  return_document=True)
    return int((b or {}).get("brain_version") or 1)


async def mark_stale(ws: str, key: str, version: int, reason: str, sections: set[str]) -> None:
    await db().brain_stale.update_one({"_id": f"{ws}:{key}"}, {"$set": {"ws": ws, "dep": key, "version": version, "reason": reason, "at": now(),
                                                                        "updated": False},
                                                               "$addToSet": {"sections": {"$each": sorted(sections)}}}, upsert=True)


async def clear_stale(ws: str, key: str) -> None:
    await db().brain_stale.delete_one({"_id": f"{ws}:{key}"})


async def settle_sections(ws: str, key: str, done: set[str]) -> None:
    """A free refresh brought these sections up to date. Anything else still waiting (an earlier change that needs AI runs,
    or the owner's edits) keeps waiting."""
    _id = f"{ws}:{key}"
    await db().brain_stale.update_one({"_id": _id}, {"$pullAll": {"sections": sorted(done)}})
    await db().brain_stale.delete_one({"_id": _id, "sections": {"$size": 0}})   # only when nothing else is waiting


def runs_for(dep: Dependent, sections: set[str]) -> int:
    return dep.cost(sections) if dep.cost else 1


async def propagate(owner: dict, sections: set[str]) -> dict:
    """After a save: what costs no AI run is brought up to the new Brain now; what would use AI runs waits for "Update my
    hub" (marked "ready"); what the owner edited is flagged for their choice."""
    ws = owner["_id"]
    if not sections:
        return {}
    version = await bump(ws, sections)
    result = {}
    for dep in sorted(DEPENDENTS.values(), key=lambda d: d.order):
        hit = dep.watches & sections
        if not hit or not plans.has(owner, dep.feature):
            continue
        try:
            if not await dep.exists(ws):
                continue
            if runs_for(dep, hit) > 0:
                edited = await (dep.has_edits or dep.edited)(ws)
                await mark_stale(ws, dep.key, version, "edited" if edited else "ready", hit)
                result[dep.key] = "flagged" if edited else "ready"
                continue
            if await dep.edited(ws):
                await mark_stale(ws, dep.key, version, "edited", hit)
                result[dep.key] = "flagged"
                continue
            outcome = await dep.refresh(owner, hit)
            if outcome == "kept_edits":   # refreshed what the owner never touched; their own edits are flagged, not replaced
                await mark_stale(ws, dep.key, version, "edited", hit)
                result[dep.key] = "refreshed_kept_edits"
            else:
                await settle_sections(ws, dep.key, hit)
                result[dep.key] = "refreshed"
        except HTTPException as e:
            reason = "no_runs" if e.status_code == 402 else "failed"
            await mark_stale(ws, dep.key, version, reason, hit)
            result[dep.key] = reason
        except Exception:  # noqa: BLE001  one output failing must not stop the others
            log.exception("refreshing %s after a Brain change failed", dep.key)
            await mark_stale(ws, dep.key, version, "failed", hit)
            result[dep.key] = "failed"
    return result


_background: set = set()


def after_save(owner: dict, sections: set[str]) -> None:
    """Runs propagation without making the owner wait for AI refreshes."""
    if not sections:
        return
    task = asyncio.create_task(propagate(owner, sections))
    _background.add(task)
    task.add_done_callback(_background.discard)


async def settle() -> None:
    while _background:
        await asyncio.gather(*list(_background), return_exceptions=True)


REASONS = {"edited": "You've edited it, so we kept your version.", "no_runs": "It couldn't refresh: this month's AI runs are used up.",
           "failed": "It couldn't refresh just now.", "ready": "Built from your old details. Update my hub refreshes it."}
WAITING = ("ready", "no_runs", "failed")      # what "Update my hub" refreshes; "edited" stays the owner's choice on its banner


async def updates(owner: dict) -> list[dict]:
    out = []
    async for s in db().brain_stale.find({"ws": owner["_id"]}):
        dep = DEPENDENTS.get(s.get("dep"))
        if not dep or not plans.has(owner, dep.feature):
            continue
        out.append({"key": dep.key, "label": dep.label, "path": dep.path, "reason": s.get("reason"), "why": REASONS.get(s.get("reason"), ""),
                    "sections": s.get("sections") or [], "since": s["at"].isoformat(), "note": dep.note,
                    "runs": runs_for(dep, set(s.get("sections") or SECTIONS)), "updated": bool(s.get("updated"))})
    return sorted(out, key=lambda u: DEPENDENTS[u["key"]].order)


async def _waiting(owner: dict) -> list[dict]:
    """Waiting for "Update my hub": anything marked ready (or that couldn't refresh before), plus an output edited item by item
    (the brand kit) whose untouched items haven't been brought up to date yet — its edits are kept either way."""
    out = []
    for u in await updates(owner):
        if u["reason"] in WAITING:
            out.append(u)
        elif u["reason"] == "edited" and not u["updated"] and not await DEPENDENTS[u["key"]].edited(owner["_id"]):
            out.append(u)
    return out


async def update_plan(owner: dict) -> dict:
    """What "Update my hub" will refresh, and the AI runs it will use — shown before the owner presses it."""
    from .services import runs_status
    items = await _waiting(owner)
    return {"items": [{"key": u["key"], "label": u["label"], "runs": u["runs"]} for u in items], "runs": sum(u["runs"] for u in items),
            "runs_left": (await runs_status(owner))["left"]}


async def update_hub(owner: dict) -> dict:
    """"Update my hub": refreshes everything waiting, one by one. Owner edits stay (item by item where the output allows it);
    if the month's runs run out part-way, what's left keeps waiting."""
    ws = owner["_id"]
    out: dict[str, str] = {}
    for u in await _waiting(owner):
        dep = DEPENDENTS[u["key"]]
        _id = f"{ws}:{dep.key}"
        seen = (await db().brain_stale.find_one({"_id": _id}, {"at": 1}) or {}).get("at")
        try:
            # edited by hand since it was marked (the website's headline, an SOP written on the map): never rewritten from here —
            # it moves to the banner, where replacing the owner's edits is their own choice
            if await dep.edited(ws):
                await db().brain_stale.update_one({"_id": _id}, {"$set": {"reason": "edited"}})
                out[dep.key] = "edited"
                continue
            outcome = await dep.refresh(owner, set(u["sections"] or SECTIONS))
            # a save made while this was running marked it again (a newer "at"): that newer change keeps waiting
            if outcome == "kept_edits":    # the rest is up to date; the owner's own items wait on the banner
                await db().brain_stale.update_one({"_id": _id, "at": seen}, {"$set": {"reason": "edited", "updated": True, "at": now()}})
                out[dep.key] = "refreshed_kept_edits"
            else:
                await db().brain_stale.delete_one({"_id": _id, "at": seen})
                out[dep.key] = "refreshed"
        except HTTPException as e:
            if e.status_code == 402:
                await db().brain_stale.update_one({"_id": f"{ws}:{dep.key}"}, {"$set": {"reason": "no_runs"}})
                out[dep.key] = "no_runs"
                break
            await db().brain_stale.update_one({"_id": f"{ws}:{dep.key}"}, {"$set": {"reason": "failed"}})
            out[dep.key] = "failed"
        except Exception:  # noqa: BLE001
            log.exception("Update my hub: %s failed", dep.key)
            await db().brain_stale.update_one({"_id": f"{ws}:{dep.key}"}, {"$set": {"reason": "failed"}})
            out[dep.key] = "failed"
    return out


async def apply(owner: dict, key: str) -> None:
    """The owner pressed Refresh on the banner: rebuild from the Brain, replacing their edits (their choice)."""
    dep = DEPENDENTS.get(key)
    if not dep or not plans.has(owner, dep.feature):
        raise HTTPException(404, "Not found")
    stale = await db().brain_stale.find_one({"_id": f"{owner['_id']}:{key}"}) or {}
    if dep.overwrite:
        await dep.overwrite(owner)
    else:
        await dep.refresh(owner, set(stale.get("sections") or SECTIONS))
    await clear_stale(owner["_id"], key)


def today_iso() -> str:
    from .db import IST
    return now().astimezone(IST).date().isoformat()


def days_old(day: str | None) -> int | None:
    try:
        return (date.fromisoformat(today_iso()) - date.fromisoformat(day)).days if day else None
    except ValueError:
        return None
