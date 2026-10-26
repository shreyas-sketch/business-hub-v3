"""Razorpay webhooks (v2.1, 3.5). The owner adds a webhook in their own Razorpay dashboard (test or live mode) pointing at
their hub address, with a secret they type there and here. Every delivery is signed: X-Razorpay-Signature is the
HMAC-SHA256 of the raw body with that secret.

Events used: payment.captured (money received), refund.processed (money given back) and invoice.paid / partially_paid /
expired / cancelled (a Razorpay invoice's status; a paid one carries its payment too). refund.created and refund.failed are
ignored. Razorpay
amounts are in paise."""
import hashlib
import hmac
from datetime import datetime, timezone

from .base import PaymentEvent


def _str(v) -> str:
    return str(v or "").strip()


def _notes(raw) -> dict:
    # Razorpay sends notes as an object, or as an empty list when there are none
    return {str(k)[:40]: _str(v)[:300] for k, v in raw.items()} if isinstance(raw, dict) else {}


def _at(ts) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


class Razorpay:
    name = "razorpay"

    def verify(self, body: bytes, headers: dict, secret: str) -> bool:
        sig = _str(headers.get("x-razorpay-signature"))
        if not secret or not sig:
            return False
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, sig)

    def parse(self, payload: dict) -> PaymentEvent | None:
        if not isinstance(payload, dict):
            return None
        event = _str(payload.get("event"))
        body = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        pay = ((body.get("payment") or {}).get("entity")) or {}
        if event == "payment.captured" and pay.get("id"):
            return PaymentEvent(kind="captured", payment_id=_str(pay["id"]), amount=int(pay.get("amount") or 0) / 100,
                                currency=_str(pay.get("currency")) or "INR", order_id=_str(pay.get("order_id")), email=_str(pay.get("email")).lower(),
                                contact=_str(pay.get("contact")), name=_str((pay.get("notes") or {}).get("name") if isinstance(pay.get("notes"), dict) else ""),
                                notes=_notes(pay.get("notes")), at=_at(pay.get("created_at") or payload.get("created_at")),
                                event_id=_str(payload.get("id")), invoice_id=_str(pay.get("invoice_id")))
        if event in ("invoice.paid", "invoice.partially_paid", "invoice.expired", "invoice.cancelled"):
            inv = ((body.get("invoice") or {}).get("entity")) or {}
            if not inv.get("id"):
                return None
            return PaymentEvent(kind="invoice", payment_id=_str(pay.get("id")), amount=int(pay.get("amount") or 0) / 100,
                                currency=_str(pay.get("currency") or inv.get("currency")) or "INR", order_id=_str(pay.get("order_id")),
                                email=_str(pay.get("email")).lower(), contact=_str(pay.get("contact")), notes=_notes(pay.get("notes")),
                                at=_at(pay.get("created_at") or inv.get("paid_at")), event_id=_str(payload.get("id")),
                                invoice_id=_str(inv["id"]), invoice_status=_str(inv.get("status")) or event.split(".", 1)[1])
        # refund.processed only: the money has actually gone back. refund.created can still fail (refund.failed), so it's
        # not acted on; the credit note is unique per refund id either way, so older data issued on "created" isn't doubled
        if event == "refund.processed":
            ref = ((body.get("refund") or {}).get("entity")) or {}
            if not ref.get("id") or not (ref.get("payment_id") or pay.get("id")):
                return None
            return PaymentEvent(kind="refunded", payment_id=_str(ref.get("payment_id") or pay.get("id")), refund_id=_str(ref["id"]),
                                amount=int(ref.get("amount") or 0) / 100, currency=_str(ref.get("currency")) or "INR",
                                email=_str(pay.get("email")).lower(), contact=_str(pay.get("contact")), notes=_notes(ref.get("notes") or pay.get("notes")),
                                at=_at(ref.get("created_at")), event_id=_str(payload.get("id")))
        return None


CONNECTOR = Razorpay()
