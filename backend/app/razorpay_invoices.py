"""
Razorpay invoices for a business's own Razorpay account (Connections → Payment gateway, key id + key secret).

Every hub invoice gets exactly one Razorpay invoice:
    unpaid              an ISSUED Razorpay invoice: its pay link (short_url) goes in the WhatsApp message and on the PDF.
                        Paid through it, the payment webhook marks the hub invoice and its Money owed entry paid.
    already paid        a RECORD: a Razorpay invoice left as a draft (never issued, never sent, no pay link reaches the
                        customer), noting the payment. A payment through a link, QR code or payment page, cash marked
                        "paid already", etc.
    paid another way    an issued Razorpay invoice whose hub invoice is then paid some other way (cash in Money owed,
                        another Razorpay payment) is CANCELLED, so the customer can't pay twice.
Razorpay sends nothing itself (sms_notify / email_notify 0): only the hub's own WhatsApp does. Razorpay's API can't add GST
details (tax rate, HSN) to an invoice, so the GST tax invoice stays the hub's PDF; the Razorpay invoice is the request to pay
(or the record), for the same total, with the hub's invoice number as its receipt.

The work is a queue on the hub invoice (quotes.rzp): pending → done | failed, retried with backoff by the scheduler, so a
restart or an outage loses nothing. Razorpay's receipt is unique per account, so a retry after a lost answer finds the
invoice it already made instead of making a second one.

Every 15 minutes the account's Razorpay invoices are also read into the hub (rzp_invoices), read-only — including ones made
by hand in the Razorpay dashboard — and linked to the hub invoice with the same number.

quotes.rzp     {mode: issued|record|razorpay, state: pending|working|done|failed|cancel, invoice_id, short_url, status,
                attempts, next_at, error, updated_at}
rzp_invoices   {_id "{ws}:{id}", ws, id, status, receipt, amount, amount_paid, amount_due, customer, short_url, description,
                created_at, issued_at, paid_at, cancelled_at, hub_invoice_id, hub_number, synced_at}
rzp_sync       {_id ws, last_at, lock_until, error}
"""
import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone

import httpx

from . import connections, plans
from .config import settings
from .db import db, now
from .services import notify

log = logging.getLogger("razorpay_invoices")
TIMEOUT = 20
RETRIES = 2                      # inside one attempt, for 429/5xx; the queue retries later as well
BACKOFF = (1, 5, 15, 60, 180)    # minutes between attempts; after the last one the invoice is marked failed
SYNC_MINUTES = 15
SYNC_PAGES = 5                   # the newest 500 Razorpay invoices are read each time
LOCK = timedelta(minutes=5)
STATUS_LABEL = {"draft": "Record", "issued": "Awaiting payment", "partially_paid": "Part paid", "paid": "Paid", "cancelled": "Cancelled",
                "expired": "Expired", "deleted": "Deleted"}


class RzpError(Exception):
    def __init__(self, message: str, status: int = 0, code: str = "", retry: bool = False):
        super().__init__(message)
        self.message, self.status, self.code, self.retry = message, status, code, retry


def _transport():
    """Tests swap this for an httpx.MockTransport."""
    return None


async def _pause(seconds: float) -> None:
    """Between retries (tests make it instant)."""
    await asyncio.sleep(seconds)


async def call(conn: dict, method: str, path: str, *, body: dict | None = None, params: dict | None = None) -> dict:
    """One Razorpay API call with the business's own keys (basic auth). Plain errors; 429/5xx retried briefly."""
    auth = (str(conn.get("key_id") or ""), str(conn.get("key_secret") or ""))
    attempt = 0
    while True:
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, transport=_transport()) as c:
                r = await c.request(method, f"{settings.razorpay_api_base}{path}", json=body, params=params, auth=auth)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            if attempt >= RETRIES:
                raise RzpError("Razorpay couldn't be reached.", retry=True) from e
            attempt += 1
            await _pause(min(2 ** attempt, 8) + random.random())
            continue
        if r.status_code == 429 or r.status_code >= 500:
            if attempt >= RETRIES:
                raise RzpError("Razorpay is busy right now.", r.status_code, retry=True)
            attempt += 1
            await _pause(min(2 ** attempt, 8) + random.random())
            continue
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code in (401, 403):
            raise RzpError("Razorpay refused the key id and key secret. Check them in Connections → Payment gateway.", r.status_code, "auth")
        if r.status_code >= 400:
            err = (data.get("error") or {}) if isinstance(data, dict) else {}
            raise RzpError(str(err.get("description") or f"Razorpay said no (http {r.status_code}).")[:300], r.status_code,
                           str(err.get("code") or ""))
        if not isinstance(data, dict):
            raise RzpError("Razorpay sent back something unexpected.", r.status_code, retry=True)
        return data


async def _conn(ws: str) -> dict | None:
    """The business's Razorpay keys, when the plan has it and both keys are saved."""
    owner = await db().users.find_one({"_id": ws}) or {}
    if not owner or owner.get("disabled") or not plans.has(owner, "gateway_invoicing"):
        return None
    conn = await connections.get(ws, "razorpay")
    return conn if conn and conn.get("key_id") and conn.get("key_secret") else None


# ───────────────────────── what Razorpay is sent ─────────────────────────
def _paise(x) -> int:
    return int(round(float(x or 0) * 100))


def line_items(inv: dict) -> list[dict]:
    """Each line with its GST included, adding up to the invoice's total to the paisa (the last line takes any paisa)."""
    total = _paise((inv.get("totals") or {}).get("total"))
    mode = inv.get("gst_mode") or "none"
    items = [i for i in (inv.get("items") or []) if isinstance(i, dict)]
    out = []
    for i in items[:50]:
        qty, rate = float(i.get("qty") or 0), float(i.get("rate") or 0)
        amount = float(i.get("amount") if i.get("amount") is not None else qty * rate)
        gst = float(i.get("gst") or 0) if mode != "none" else 0.0
        desc = f"{qty:g} {i.get('unit') or 'nos'} × ₹{rate:,.2f}" + (f" + {gst:g}% GST" if gst else "")
        out.append({"name": str(i.get("name") or "Item")[:120], "description": desc[:200], "amount": _paise(amount * (1 + gst / 100)),
                    "currency": "INR", "quantity": 1})
    if not out or len(items) > 50 or total <= 0:
        return [{"name": f"Invoice {inv.get('number', '')}"[:120], "amount": total, "currency": "INR", "quantity": 1}]
    gap = total - sum(x["amount"] for x in out)
    out[-1]["amount"] += gap
    if out[-1]["amount"] <= 0:           # rounding never makes a line free or negative: one line for the total instead
        return [{"name": f"Invoice {inv.get('number', '')}"[:120], "amount": total, "currency": "INR", "quantity": 1}]
    return out


def payload(inv: dict, business: str, mode: str) -> dict:
    cust = inv.get("customer") or {}
    customer = {"name": str(cust.get("name") or "Customer")[:80]}
    if cust.get("phone"):
        customer["contact"] = str(cust["phone"])[:20]
    if cust.get("email") and "@" in str(cust["email"]):
        customer["email"] = str(cust["email"]).strip().lower()[:120]
    created = inv.get("created_at") if isinstance(inv.get("created_at"), datetime) else now()
    body = {"type": "invoice", "receipt": str(inv["number"])[:40], "currency": "INR", "customer": customer, "line_items": line_items(inv),
            "sms_notify": 0, "email_notify": 0, "date": int((created if created.tzinfo else created.replace(tzinfo=timezone.utc)).timestamp()),
            "notes": {"invoice_no": str(inv["number"])[:40], "hub_invoice_id": str(inv["_id"])[:40], "source": "hub"}}
    if mode == "record":
        paid_by = inv.get("gateway_payment_id") or inv.get("paid_payment_id")
        body["draft"] = "1"
        body["description"] = (f"Record of invoice {inv['number']} from {business}, already paid"
                               + (f" (Razorpay payment {paid_by})" if paid_by else "") + ". Not a request to pay.")[:500]
        body["notes"]["record_only"] = "yes"
        if paid_by:
            body["notes"]["payment_id"] = str(paid_by)[:40]
    else:
        body["description"] = f"Invoice {inv['number']} from {business}"[:500]
    return body


# ───────────────────────── the queue ─────────────────────────
_tasks: set = set()


def _kick(ws: str, inv_id: str) -> None:
    """Works on it right away, in the background (tests run the queue themselves with drain())."""
    if settings.env == "test":
        return
    t = asyncio.create_task(_safe_work(ws, inv_id))
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


async def _safe_work(ws: str, inv_id: str) -> None:
    try:
        await work(ws, inv_id)
    except Exception:  # noqa: BLE001  the scheduler retries it
        log.exception("Razorpay invoice for %s failed", inv_id)


async def queue(ws: str, inv_id: str) -> bool:
    """The hub invoice gets its Razorpay invoice: issued while unpaid, a record when already paid. Once per invoice."""
    if not await _conn(ws):
        return False
    inv = await db().quotes.find_one({"_id": inv_id, "ws": ws, "kind": "invoice"}, {"status": 1, "rzp": 1})
    if not inv or inv.get("rzp"):
        return False
    mode = "record" if inv.get("status") == "paid" else "issued"
    r = await db().quotes.update_one({"_id": inv_id, "rzp": {"$exists": False}},
                                     {"$set": {"rzp": {"mode": mode, "state": "pending", "attempts": 0, "next_at": now(), "updated_at": now()}}})
    if r.modified_count:
        _kick(ws, inv_id)
    return bool(r.modified_count)


async def paid_elsewhere(ws: str, inv_id: str | None, payment_invoice_id: str = "") -> None:
    """The hub invoice was paid, but not through its own Razorpay invoice: an issued one is cancelled (no double payment);
    one not made yet becomes a record instead. Paid through it (`payment_invoice_id` is its id): nothing to do."""
    if not inv_id:
        return
    inv = await db().quotes.find_one({"_id": inv_id, "ws": ws}, {"rzp": 1})
    rz = (inv or {}).get("rzp") or {}
    if not rz:
        await queue(ws, inv_id)             # e.g. connected after the invoice was made: a record of the payment
        return
    if payment_invoice_id and payment_invoice_id == rz.get("invoice_id"):
        await db().quotes.update_one({"_id": inv_id}, {"$set": {"rzp.status": "paid", "rzp.updated_at": now()}})
        return
    if rz.get("mode") != "issued":
        return
    if rz.get("state") in ("pending", "failed"):
        await db().quotes.update_one({"_id": inv_id, "rzp.state": rz["state"]},
                                     {"$set": {"rzp.mode": "record", "rzp.state": "pending", "rzp.attempts": 0, "rzp.next_at": now(), "rzp.error": None}})
        _kick(ws, inv_id)
    elif rz.get("state") in ("done", "working") and rz.get("status") in (None, "issued", "partially_paid"):
        await db().quotes.update_one({"_id": inv_id}, {"$set": {"rzp.state": "cancel", "rzp.attempts": 0, "rzp.next_at": now()}})
        _kick(ws, inv_id)


async def _find_by_receipt(conn: dict, receipt: str) -> dict | None:
    data = await call(conn, "GET", "/invoices", params={"receipt": receipt})
    items = data.get("items") if isinstance(data.get("items"), list) else []
    return next((x for x in items if isinstance(x, dict) and x.get("receipt") == receipt and x.get("id")), None)


async def work(ws: str, inv_id: str) -> str:
    """One step of one invoice's queue: create (issued or record) or cancel. Returns the state it ends in."""
    inv = await db().quotes.find_one_and_update(
        {"_id": inv_id, "ws": ws, "rzp.state": {"$in": ["pending", "cancel"]}, "rzp.next_at": {"$lte": now()}},
        {"$set": {"rzp.working_until": now() + LOCK, "rzp.next_at": now() + LOCK}})
    if not inv:
        return "skipped"
    rz = inv["rzp"]
    conn = await _conn(ws)
    if not conn:
        await db().quotes.update_one({"_id": inv_id}, {"$set": {"rzp.state": "failed", "rzp.error": "Razorpay isn't connected any more."}})
        return "failed"
    b = await db().businesses.find_one({"owner_id": ws}, {"name": 1}) or {}
    try:
        if rz["state"] == "cancel":
            data = await call(conn, "POST", f"/invoices/{rz['invoice_id']}/cancel")
            await db().quotes.update_one({"_id": inv_id}, {"$set": {"rzp.state": "done", "rzp.status": str(data.get("status") or "cancelled"),
                                                                     "rzp.error": None, "rzp.updated_at": now()}})
            return "done"
        try:
            data = await call(conn, "POST", "/invoices", body=payload(inv, b.get("name") or "us", rz["mode"]))
        except RzpError as e:
            # the receipt is unique in Razorpay: made already (an earlier answer was lost) → use that one
            found = await _find_by_receipt(conn, str(inv["number"])) if e.status == 400 and "receipt" in e.message.lower() else None
            if not found:
                raise
            data = found
        if not data.get("id"):
            raise RzpError("Razorpay didn't return the invoice.", retry=True)
        await db().quotes.update_one({"_id": inv_id}, {"$set": {
            "rzp.state": "done", "rzp.invoice_id": str(data["id"]), "rzp.short_url": data.get("short_url") if rz["mode"] == "issued" else None,
            "rzp.status": str(data.get("status") or ("draft" if rz["mode"] == "record" else "issued")), "rzp.error": None, "rzp.updated_at": now()}})
        # paid while it was being made, not through it: cancel it straight away
        fresh = await db().quotes.find_one({"_id": inv_id}, {"status": 1, "paid_payment_id": 1})
        if rz["mode"] == "issued" and (fresh or {}).get("status") == "paid":
            await paid_elsewhere(ws, inv_id)
        return "done"
    except RzpError as e:
        tries = int(rz.get("attempts") or 0) + 1
        final = e.code == "auth" or not e.retry and e.status in (400, 404) or tries > len(BACKOFF)
        upd = {"rzp.attempts": tries, "rzp.error": e.message, "rzp.updated_at": now()}
        if final:
            upd["rzp.state"] = "failed"
            if not rz.get("told"):
                upd["rzp.told"] = True
                await notify(ws, f"Invoice {inv['number']} couldn't be added to your Razorpay account: {e.message} "
                                 "Open Invoices to try again.")
        else:
            upd["rzp.next_at"] = now() + timedelta(minutes=BACKOFF[tries - 1])
        await db().quotes.update_one({"_id": inv_id}, {"$set": upd})
        log.warning("Razorpay invoice %s: %s (%s)", inv_id, e.code or e.status, e.message[:120])
        return "failed" if final else "retry"


async def retry(ws: str, inv_id: str) -> dict:
    """The owner's "Try again" on a failed one."""
    inv = await db().quotes.find_one({"_id": inv_id, "ws": ws, "kind": "invoice"}, {"rzp": 1, "status": 1})
    rz = (inv or {}).get("rzp")
    if not rz:
        await queue(ws, inv_id)
    elif rz.get("state") == "failed":
        back = "cancel" if rz.get("invoice_id") else "pending"
        await db().quotes.update_one({"_id": inv_id}, {"$set": {"rzp.state": back, "rzp.attempts": 0, "rzp.next_at": now(), "rzp.told": False}})
    await work(ws, inv_id)
    return view((await db().quotes.find_one({"_id": inv_id}, {"rzp": 1}) or {}).get("rzp"))


def view(rz: dict | None) -> dict | None:
    """What the owner sees on an invoice."""
    if not rz:
        return None
    status = rz.get("status")
    return {"mode": rz.get("mode"), "state": rz.get("state"), "invoice_id": rz.get("invoice_id"), "status": status,
            "label": "Couldn't be added" if rz.get("state") == "failed" else "Adding to Razorpay…" if rz.get("state") in ("pending", "working")
            else "Cancelling…" if rz.get("state") == "cancel" else STATUS_LABEL.get(status or "", status or ""),
            "pay_url": rz.get("short_url") if rz.get("mode") == "issued" and status in ("issued", "partially_paid") else None,
            "error": rz.get("error") if rz.get("state") == "failed" else None}


def pay_url(q: dict) -> str | None:
    """The Razorpay pay link to put in the WhatsApp message and on the PDF, while it can still be paid."""
    v = view(q.get("rzp")) if q.get("kind") == "invoice" and q.get("status") != "paid" else None
    return (v or {}).get("pay_url")


async def on_invoice_event(ws: str, invoice_id: str, status: str) -> None:
    """invoice.paid / partially_paid / expired / cancelled from the webhook: the status on the hub invoice and the read-in list."""
    if not invoice_id or not status:
        return
    await db().quotes.update_one({"ws": ws, "rzp.invoice_id": invoice_id}, {"$set": {"rzp.status": status[:20], "rzp.updated_at": now()}})
    await db().rzp_invoices.update_one({"_id": f"{ws}:{invoice_id}"}, {"$set": {"status": status[:20], "synced_at": now()}})


async def drain(ws: str | None = None) -> int:
    """Every queued step that's due now (the scheduler; tests)."""
    q = {"rzp.state": {"$in": ["pending", "cancel"]}, "rzp.next_at": {"$lte": now()}}
    if ws:
        q["ws"] = ws
    done = 0
    async for inv in db().quotes.find(q, {"ws": 1}).limit(50):
        await _safe_work(inv["ws"], inv["_id"])
        done += 1
    return done


# ───────────────────────── reading Razorpay invoices into the hub ─────────────────────────
def _at(ts) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc) if ts else None
    except (TypeError, ValueError, OverflowError, OSError):
        return None


async def sync(ws: str) -> dict:
    """The newest Razorpay invoices of this account, into rzp_invoices (read-only), linked to hub invoices by id or number."""
    s = await db().rzp_sync.find_one_and_update({"_id": ws, "$or": [{"lock_until": None}, {"lock_until": {"$lt": now()}}]},
                                                {"$set": {"lock_until": now() + LOCK}}, upsert=False)
    if s is None:
        try:
            await db().rzp_sync.insert_one({"_id": ws, "lock_until": now() + LOCK})
        except Exception:  # noqa: BLE001  another worker holds it
            return {"ok": False, "busy": True}
    conn = await _conn(ws)
    if not conn:
        await db().rzp_sync.update_one({"_id": ws}, {"$set": {"lock_until": None}})
        return {"ok": False, "error": "Razorpay isn't connected."}
    seen = 0
    try:
        for page in range(SYNC_PAGES):
            data = await call(conn, "GET", "/invoices", params={"type": "invoice", "count": 100, "skip": page * 100})
            items = data.get("items")
            if not isinstance(items, list):
                raise RzpError("Razorpay sent back something unexpected.", retry=True)
            for x in items:
                if not isinstance(x, dict) or not x.get("id"):
                    continue
                await _store(ws, x)
                seen += 1
            if len(items) < 100:
                break
    except RzpError as e:
        await db().rzp_sync.update_one({"_id": ws}, {"$set": {"lock_until": None, "error": e.message, "tried_at": now()}})
        return {"ok": False, "error": e.message}
    await db().rzp_sync.update_one({"_id": ws}, {"$set": {"lock_until": None, "error": None, "last_at": now(), "count": seen}})
    return {"ok": True, "count": seen}


async def _store(ws: str, x: dict) -> None:
    rid = str(x["id"])
    receipt = str(x.get("receipt") or "")[:40]
    hub = await db().quotes.find_one({"ws": ws, "kind": "invoice", "rzp.invoice_id": rid}, {"number": 1}) or \
        (await db().quotes.find_one({"ws": ws, "kind": "invoice", "number": receipt}, {"number": 1}) if receipt else None)
    c = x.get("customer_details") if isinstance(x.get("customer_details"), dict) else {}
    status = str(x.get("status") or "")[:20]
    doc = {"ws": ws, "id": rid, "status": status, "receipt": receipt, "currency": str(x.get("currency") or "INR")[:3],
           "amount": (x.get("amount") or 0) / 100, "amount_paid": (x.get("amount_paid") or 0) / 100, "amount_due": (x.get("amount_due") or 0) / 100,
           "customer": {"name": str(c.get("name") or c.get("customer_name") or "")[:80], "contact": str(c.get("contact") or c.get("customer_contact") or "")[:20],
                        "email": str(c.get("email") or c.get("customer_email") or "")[:120]},
           "short_url": x.get("short_url") if status in ("issued", "partially_paid") else None, "description": str(x.get("description") or "")[:200],
           "created_at": _at(x.get("created_at")), "issued_at": _at(x.get("issued_at")), "paid_at": _at(x.get("paid_at")),
           "cancelled_at": _at(x.get("cancelled_at")), "hub_invoice_id": (hub or {}).get("_id"), "hub_number": (hub or {}).get("number"),
           "from_hub": ((x.get("notes") or {}) if isinstance(x.get("notes"), dict) else {}).get("source") == "hub", "synced_at": now()}
    await db().rzp_invoices.update_one({"_id": f"{ws}:{rid}"}, {"$set": doc}, upsert=True)
    if hub and status:
        await db().quotes.update_one({"_id": hub["_id"], "rzp.invoice_id": rid}, {"$set": {"rzp.status": status}})


async def tick(at: datetime | None = None) -> int:
    """Every minute: queued steps that are due, then each connected account's read-in every SYNC_MINUTES."""
    n = await drain()
    at = at or now()
    cutoff = at - timedelta(minutes=SYNC_MINUTES)
    async for c in db().connections.find({"razorpay.key_id": {"$nin": [None, ""]}, "razorpay.key_secret": {"$exists": True}}, {"_id": 1}).limit(500):
        s = await db().rzp_sync.find_one({"_id": c["_id"]}, {"last_at": 1, "tried_at": 1}) or {}
        last = max([x for x in (s.get("last_at"), s.get("tried_at")) if x] or [None], key=lambda x: x or datetime.min)
        if last and (last if last.tzinfo else last.replace(tzinfo=timezone.utc)) > (cutoff if cutoff.tzinfo else cutoff.replace(tzinfo=timezone.utc)):
            continue
        if await _conn(c["_id"]):
            await sync(c["_id"])
            n += 1
    return n


async def ensure_indexes() -> None:
    await db().quotes.create_index([("rzp.state", 1), ("rzp.next_at", 1)], sparse=True)
    await db().quotes.create_index([("ws", 1), ("rzp.invoice_id", 1)], sparse=True)
    await db().rzp_invoices.create_index([("ws", 1), ("created_at", -1)])
