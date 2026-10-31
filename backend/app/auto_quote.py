"""
Draft quotations for inquiries, and invoices when the customer says yes (both switchable on the Agents page).

Draft quotations ("auto_quote", on every new inquiry):
  every real inquiry (a phone number or email, a message of a few words, not a repeat of one already drafted for that
  customer this week) gets a DRAFT quotation in the background, from what the customer asked for and the business's own
  price list (Business Brain offers, Company Brain documents, the owner's past invoices). It's never sent on its own: the
  owner checks the prices on the Quotations page (or the opportunity) and sends it with one tap. Each draft is one AI run, at
  most DAILY_CAP a day per business. The inquiry is written by a stranger, so a price is only ever one that appears in the
  owner's own profile or documents; anything else is left at 0 for the owner to fill in.
  Every inquiry says what happened to its quotation (leads.auto_quote, shown on Invoicing, on Inquiries and in Admin → the
  owner): drafting, ready (with its number), or no draft and exactly why — so a missing quotation is never a mystery. Where
  the reason can be fixed by hand, "Make quotation" drafts it on the spot (make_draft, manual=True).

Invoice on yes ("auto_invoice"):
  when a quotation is marked accepted, or its opportunity is marked won, the invoice is made from it on its own (Money owed,
  Razorpay pay link, the emailed PDF when that's on) — once. A quotation with a line still at ₹0 isn't invoiced: the owner is
  told to add the price and press Make invoice.
"""
import logging
import re
from datetime import timedelta

from . import gst as gst_slabs, plans
from .agents import catalog, jobs
from .agents.catalog import Skip
from .db import IST, db, now
from .services import new_id, notify, run_ai, track

log = logging.getLogger("auto_quote")
DAILY_CAP = 20
MIN_WORDS = 3


class HasQuote(Skip):
    """The inquiry already has a quotation: its status points to it (not "no draft")."""


def _real(lead: dict) -> str | None:
    """Why this inquiry gets no draft (None: it gets one)."""
    if not (lead.get("phone") or lead.get("email")):
        return "no phone number or email"
    words = re.findall(r"[A-Za-zऀ-ॿ]{2,}", lead.get("message") or "")
    if len(words) < MIN_WORDS:
        return "the message is too short to quote from"
    if re.search(r"https?://|www\.", lead.get("message") or "", re.I) and len(words) < 12:
        return "it looks like spam (a link and little else)"
    return None


def no_feature_reason(owner: dict) -> str:
    """Why this owner's inquiries get no draft quotation: Invoicing isn't open to them (in plain words)."""
    if plans.day_locked(owner, "quotations"):
        return f"Invoicing opens on Day {plans.feature_day('quotations')} of the event"
    t = owner.get("trial") or {}
    if t.get("reason") == "event" and t.get("until") and t["until"] <= now():
        return "your event access has ended"
    tier = plans.tier_of("quotations")
    return f"draft quotations come with {plans.PLANS[tier]['name'] if tier in plans.PLANS else 'a paid plan'}"


async def set_status(lead_id: str, state: str, text: str, quote_id: str | None = None, can_make: bool = False) -> dict:
    """What happened to this inquiry's quotation: drafting | ready | none | failed, in a line the owner reads."""
    st = {"state": state, "text": text[:240], "quote_id": quote_id, "can_make": can_make, "at": now()}
    await db().leads.update_one({"_id": lead_id}, {"$set": {"auto_quote": st}})
    return st


def status_view(lead: dict, numbers: dict[str, str]) -> dict:
    """The inquiry's quotation status for the pages. `numbers`: quote id → number, for the ones shown."""
    st = dict(lead.get("auto_quote") or {})
    if not st:   # an inquiry from before statuses were recorded
        st = ({"state": "ready", "text": "A quotation is made for this inquiry.", "quote_id": lead["quote_id"]} if lead.get("quote_id")
              else {"state": "none", "text": "No draft quotation was made for this inquiry.", "can_make": True})
    qid = st.get("quote_id") or lead.get("quote_id")
    return {"state": st.get("state", "none"), "text": st.get("text", ""), "quote_id": qid, "quote_number": numbers.get(qid or ""),
            "can_make": bool(st.get("can_make")) and not lead.get("quote_id"),
            "at": st["at"].isoformat() if hasattr(st.get("at"), "isoformat") else None}


async def numbers_for(ws: str, leads: list[dict]) -> dict[str, str]:
    ids = list({x for lead in leads for x in ((lead.get("auto_quote") or {}).get("quote_id"), lead.get("quote_id")) if x})
    return {q["_id"]: q["number"] async for q in db().quotes.find({"ws": ws, "_id": {"$in": ids}}, {"number": 1})} if ids else {}


async def on_inquiry(owner: dict, lead: dict) -> bool:
    """Queues the draft (the customer's form never waits for the AI). Returns True when queued. Either way the inquiry
    says what happened (set_status)."""
    ws = owner["_id"]
    if not plans.has(owner, "quotations"):
        await set_status(lead["_id"], "none", f"No draft quotation: {no_feature_reason(owner)}.")
        return False
    if not await jobs.is_on(ws, "auto_quote"):
        await set_status(lead["_id"], "none", "No draft quotation: the Draft quotations automation is switched off.", can_make=True)
        return False
    why = _real(lead)
    if why:
        await set_status(lead["_id"], "none", f"No draft quotation: {why}.", can_make=bool(lead.get("phone") or lead.get("email")))
        return False
    week_ago = now() - timedelta(days=7)
    if lead.get("phone"):
        q = await db().quotes.find_one({"ws": ws, "source": "inquiry", "customer.phone": lead["phone"], "created_at": {"$gt": week_ago}},
                                       {"number": 1})
        if q:
            await set_status(lead["_id"], "none", f"No new draft: this customer already got draft quotation {q['number']} this week.",
                             quote_id=q["_id"], can_make=True)
            return False
    job = await jobs.schedule(ws, "auto_quote", now(), {"lead_id": lead["_id"]}, job_id=f"auto_quote:{lead['_id']}")
    if job:
        await set_status(lead["_id"], "drafting", "Drafting a quotation from this inquiry. It's ready in about a minute.")
    return bool(job)


def _numbers(text: str) -> set[float]:
    out = set()
    for raw in re.findall(r"\d[\d,]*(?:\.\d+)?", text or ""):
        try:
            out.add(round(float(raw.replace(",", "")), 2))
        except ValueError:
            pass
    return out


async def draft(owner: dict, run: dict):
    """The runner: one draft quotation for one inquiry, with the inquiry told how it went."""
    from fastapi import HTTPException
    lead = await db().leads.find_one({"_id": (run["job"].get("payload") or {}).get("lead_id"), "owner_id": owner["_id"]})
    if not lead:
        raise Skip("The inquiry was deleted, so no quotation was drafted.")
    try:
        return await make_draft(owner, lead)
    except HasQuote:
        raise
    except Skip as e:
        await set_status(lead["_id"], "none", f"No draft quotation: {e}", can_make=lead.get("status") not in ("won", "lost"))
        raise
    except HTTPException as e:
        text = ("No draft quotation: the AI tokens are used up. Add tokens, then tap Make quotation." if e.status_code == 402
                else "The AI was busy, so the quotation will be tried again shortly." if e.status_code == 502
                else f"No draft quotation: {e.detail if isinstance(e.detail, str) else 'the AI could not write it'}.")
        await set_status(lead["_id"], "drafting" if e.status_code == 502 else "failed", text, can_make=True)
        raise
    except Exception:
        await set_status(lead["_id"], "failed", "No draft quotation: the AI could not write it this time. Tap Make quotation to try again.",
                         can_make=True)
        raise


async def make_draft(owner: dict, lead: dict, manual: bool = False):
    """One draft quotation for one inquiry. `manual`: the owner pressed Make quotation, so today's cap doesn't apply.
    Raises Skip when there's nothing to draft; returns (note, quote id)."""
    from .ai import desk_tasks
    from .ai.tasks import profile_text
    from .routers.desk import _phone, next_number, totals
    from .routers.gateway import _owner_tax, gst_mode_for
    from .routers.hub import profile_of
    ws = owner["_id"]
    who = lead.get("name") or "a customer"
    if lead.get("status") in ("won", "lost") and not manual:
        raise Skip(f"{who} is already marked {lead['status']}, so no quotation was drafted.")
    have = await db().quotes.find_one({"ws": ws, "lead_id": lead["_id"], "kind": "quote"}, {"number": 1})
    if have:
        await set_status(lead["_id"], "ready", f"Quotation {have['number']} is made for this inquiry.", quote_id=have["_id"])
        raise HasQuote(f"{who} already has a quotation.")
    day_start = now().astimezone(IST).replace(hour=0, minute=0, second=0, microsecond=0)
    if not manual and await db().quotes.count_documents({"ws": ws, "source": "inquiry", "created_at": {"$gte": day_start}}) >= DAILY_CAP:
        raise Skip(f"Today's {DAILY_CAP} draft quotations are made; {who}'s inquiry can be quoted by hand.")
    p = await profile_of(ws)
    context = ""
    if plans.has(owner, "company_brain"):
        from .company_brain import context as brain
        context = await brain(ws, lead.get("message") or "")
    from . import invoice_style as ist
    own = ist.prompt_block((await db().invoice_styles.find_one({"_id": ws}) or {}).get("style"))
    if own:
        context = own + ("\n\n" + context if context else "")
    allowed = _numbers(profile_text(p)) | _numbers(context)     # a price must be one the owner wrote somewhere

    def shape(r):
        if not isinstance(r, dict):
            raise ValueError("not an object")
        items = []
        for i in (r.get("items") or [])[:30]:
            if not isinstance(i, dict) or not str(i.get("name") or "").strip():
                continue

            def num(v, d=0.0):
                try:
                    return max(0.0, float(str(v).replace(",", "")))
                except (TypeError, ValueError):
                    return d
            rate = num(i.get("rate"))
            if rate and round(rate, 2) not in allowed:
                rate = 0.0                     # not from the owner's own prices: the owner fills it in
            gst = num(i.get("gst"), 18)
            items.append({"name": str(i["name"])[:160], "qty": min(num(i.get("qty"), 1) or 1, 100000), "unit": str(i.get("unit") or "nos")[:20],
                          "rate": rate, "gst": gst if gst in gst_slabs.ACCEPTED else 18})
        if not items:
            raise ValueError("no items")
        return {"items": items, "notes": str(r.get("notes") or "")[:300]}
    out = await run_ai(owner, "quote", desk_tasks.quote_from_inquiry(p, lead.get("message") or "", context),
                       f"Draft quotation for {who}", save_output=False, check=shape)
    have = await db().quotes.find_one({"ws": ws, "lead_id": lead["_id"], "kind": "quote"}, {"number": 1})
    if have:
        await set_status(lead["_id"], "ready", f"Quotation {have['number']} is made for this inquiry.", quote_id=have["_id"])
        raise HasQuote(f"{who} got a quotation while this one was being drafted.")
    tax = await _owner_tax(ws)
    mode = gst_mode_for(tax)
    items = out["items"]
    if mode == "none":
        for i in items:
            i["gst"] = 0
    s = await db().quote_settings.find_one({"_id": ws}) or {}
    missing = sum(1 for i in items if not i["rate"])
    doc = {"_id": new_id(), "ws": ws, "kind": "quote", "number": await next_number(ws, "quote"),
           "customer": {"name": (lead.get("name") or "Customer")[:120], "phone": _phone(lead.get("phone") or "") or (lead.get("phone") or ""),
                        "email": (lead.get("email") or "").strip().lower(), "gstin": "", "address": ""},
           "items": items, "gst_mode": mode, "totals": totals(items, mode), "status": "draft",
           "notes": out["notes"], "terms": s.get("terms", ""), "valid_days": 15, "lead_id": lead["_id"], "followups": 0,
           "source": "inquiry", "auto": True, "missing_rates": missing, "created_by": "make_quotation" if manual else "auto_quote", "created_at": now(), "updated_at": now()}
    await db().quotes.insert_one(doc)
    await db().leads.update_one({"_id": lead["_id"]}, {"$set": {"quote_id": doc["_id"]}})
    await set_status(lead["_id"], "ready", f"Draft quotation {doc['number']} is ready" + (
        f": {missing} {'line needs' if missing == 1 else 'lines need'} a price" if missing else "") + ". Check it, then send it.", quote_id=doc["_id"])
    await track("quote", ws, source="inquiry")
    await notify(ws, f"Draft quotation {doc['number']} for {who} is ready" + (f": {missing} {'line needs' if missing == 1 else 'lines need'} a price"
                                                                                if missing else "") + ". Check it on Quotations, then send it.")
    return (f"Drafted quotation {doc['number']} for {who}" + (f" ({missing} without a price)" if missing else "") + "."), doc["_id"]


async def invoice_on_yes(owner: dict, q: dict | None, actor: str = "auto_invoice") -> dict | None:
    """The customer said yes: the invoice from this quotation, once. None when it isn't made (and why is told)."""
    if not q or q.get("kind") != "quote":
        return None
    ws = owner["_id"]
    if not plans.has(owner, "quotations") or not await jobs.is_on(ws, "auto_invoice"):
        return None
    q = await db().quotes.find_one({"_id": q["_id"], "ws": ws})
    if not q or q.get("invoice_id") or q.get("status") == "lost":
        return None
    if any(not float(i.get("rate") or 0) for i in q.get("items") or []) or not float((q.get("totals") or {}).get("total") or 0):
        if await jobs.claim(f"auto_invoice_told:{q['_id']}"):
            await notify(ws, f"Quotation {q['number']} was accepted, but a line has no price, so no invoice was made. "
                             "Add the price, then press Make invoice.")
        return None
    if not await jobs.claim(f"auto_invoice:{q['_id']}"):
        return None
    from .routers.desk import invoice_from_quote
    try:
        inv = await invoice_from_quote(owner, ws, actor, q, 7)
    except Exception:
        await jobs.release(f"auto_invoice:{q['_id']}")
        raise
    if inv:
        await jobs.record(ws, "auto_invoice", "done", f"Made invoice {inv['number']} from quotation {q['number']}.", ref=inv["_id"])
    return inv


async def on_won(owner: dict, lead: dict) -> None:
    """An opportunity marked won: its latest open quotation is accepted and invoiced."""
    q = await db().quotes.find_one({"ws": owner["_id"], "kind": "quote", "lead_id": lead["_id"], "status": {"$in": ["draft", "sent", "accepted"]},
                                    "invoice_id": None}, sort=[("created_at", -1)])
    if not q:
        return
    if q["status"] != "accepted":
        await db().quotes.update_one({"_id": q["_id"]}, {"$set": {"status": "accepted", "updated_at": now()}})
    await invoice_on_yes(owner, q)


catalog.register("auto_quote", {
    "name": "Draft quotations", "feature": "quotations", "trigger": "lead",
    "what": "Drafts a quotation for every new inquiry from what the customer asked and your price list — waiting for you to check "
            "the prices and send it. Prices only ever come from your own price list.",
    "when": f"The moment an inquiry comes in (at most {DAILY_CAP} a day)"}, draft)
catalog.register("auto_invoice", {
    "name": "Invoice on yes", "feature": "quotations", "trigger": "won", "info_only": False,
    "what": "Makes the invoice from the quotation the moment it's accepted or the opportunity is won — into Money owed, with your "
            "payment gateway's pay link.",
    "when": "When a quotation is accepted or its opportunity is marked won"})
