"""The connector interface every payment gateway implements (v2.1, 3.5)."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass
class PaymentEvent:
    kind: str                       # captured | refunded
    payment_id: str
    amount: float                   # rupees (captured: the payment; refunded: the refund)
    currency: str = "INR"
    refund_id: str = ""
    order_id: str = ""
    email: str = ""
    contact: str = ""
    name: str = ""
    notes: dict = field(default_factory=dict)
    at: datetime | None = None
    event_id: str = ""              # the gateway's own id for this delivery, when it sends one
    invoice_id: str = ""            # the gateway's invoice this payment paid (Razorpay inv_…), when it paid one
    invoice_status: str = ""        # kind "invoice": the invoice's new status (paid, partially_paid, expired, cancelled)


class Connector(Protocol):
    name: str

    def verify(self, body: bytes, headers: dict, secret: str) -> bool:
        """True only when the raw body was signed with the owner's webhook secret."""

    def parse(self, payload: dict) -> PaymentEvent | None:
        """The event in the hub's shape, or None for events the hub doesn't act on."""
