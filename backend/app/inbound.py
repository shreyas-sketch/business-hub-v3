"""
Email that arrives at the hub (v2.1, 3.6 and 3.8): Amazon SES receives mail for INBOUND_EMAIL_DOMAIN and passes it through
SNS to /api/hooks/email-in/<secret>. Two kinds of address:

    <handle>@in.employz.ai        an owner's inquiry address: the email becomes a lead (source "email")
    r+<thread>@in.employz.ai      a reply to an email the hub sent (a payment follow-up or a proposal): the follow-ups for
                                  that amount stop and the owner is told who replied

Automatic mail (out-of-office, bounces, mailing lists) is ignored so two robots never answer each other, and SES's spam
and virus verdicts are respected.
"""
import base64
import email
import re
from email import policy
from email.utils import getaddresses, parseaddr

from bs4 import BeautifulSoup

QUOTE_START = re.compile(r"^(On .{5,200} wrote:|-----\s*Original Message\s*-----|From: .+|Sent from my \w+|_{5,})\s*$", re.I | re.M)
FORWARD_START = re.compile(r"^-{3,}\s*Forwarded message\s*-{3,}\s*$|^Begin forwarded message:\s*$", re.I | re.M)
ROBOT_SUBJECT = re.compile(r"^(out of (the )?office|automatic reply|auto(matic)?[- ]?(reply|response)|auto:|autoreply|undeliverable|delivery status|"
                           r"returned mail|we('| ha)ve received your|your (ticket|request) .{0,20}(received|#)|ticket #)", re.I)
PHONE = re.compile(r"(?<!\d)(?:\+?91[\s-]?|0)?([6-9]\d{4}[\s-]?\d{5})(?!\d)")
ROBOT_FROM = re.compile(r"^(mailer-daemon|postmaster|no-?reply|donotreply|bounce)", re.I)


def raw_from_sns(message: dict) -> bytes | None:
    """The raw email from an SES 'Received' notification (the SNS action carries it in `content`, UTF-8 or base64)."""
    content = message.get("content")
    if not content:
        return None
    if isinstance(content, str) and re.match(r"^[A-Za-z0-9+/=\s]+$", content[:2000]) and ":" not in content[:200]:
        try:
            return base64.b64decode(content)
        except ValueError:
            pass
    return content.encode() if isinstance(content, str) else None


def _text(msg) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        body = part.get_content()
    except (LookupError, ValueError):
        body = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
    if part.get_content_type() == "text/html":
        body = BeautifulSoup(body, "html.parser").get_text("\n")
    return body


def strip_quoted(body: str) -> str:
    """The new part of a reply: everything before 'On … wrote:' or the quoted lines."""
    m = QUOTE_START.search(body or "")
    body = body[: m.start()] if m else body
    lines = [ln for ln in body.splitlines() if not ln.lstrip().startswith(">")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def parse(raw: bytes) -> dict:
    msg = email.message_from_bytes(raw, policy=policy.default)
    name, addr = parseaddr(str(msg.get("From", "")))
    to = [a.lower() for _, a in getaddresses([str(msg.get("To", "")), str(msg.get("Cc", "")), str(msg.get("Delivered-To", ""))]) if a]
    body = _text(msg)
    from .config import settings
    subject = str(msg.get("Subject", ""))
    auto = (str(msg.get("Auto-Submitted", "no")).lower() not in ("", "no") or str(msg.get("Precedence", "")).lower() in ("bulk", "list", "junk", "auto_reply")
            or bool(msg.get("List-Id")) or bool(ROBOT_FROM.match(addr.split("@")[0] if addr else "")) or bool(msg.get("X-Autoreply"))
            or bool(ROBOT_SUBJECT.match(subject.strip()))
            # the hub's own mail coming back (a forward loop): never a customer
            or bool(msg.get("X-Hub-Thread")) or (addr or "").lower().endswith("@" + settings.email_from_domain.lower()))
    refs = " ".join(str(msg.get(h, "")) for h in ("In-Reply-To", "References"))
    return {"from_name": (name or "").strip()[:80], "from_email": (addr or "").strip().lower()[:254], "to": to,
            "subject": str(msg.get("Subject", ""))[:200], "text": strip_quoted(body)[:4000], "full_text": body[:20000],
            "message_id": str(msg.get("Message-ID", "")).strip()[:300], "in_reply_to": str(msg.get("In-Reply-To", "")).strip()[:300],
            "references": re.findall(r"<[^<>\s]+>", refs)[:20], "automatic": auto}


def forwarded_sender(body: str) -> tuple[str, str] | None:
    """When the owner forwards an inquiry by hand, the customer is in the forwarded header block."""
    m = re.search(r"^From:\s*(.+?)\s*<([^>\s]+@[^>\s]+)>", body or "", re.M)
    if m:
        return m.group(1).strip().strip('"*')[:80], m.group(2).lower()
    m = re.search(r"^From:\s*([^\s<>]+@[^\s<>]+)", body or "", re.M)
    return ("", m.group(1).lower()) if m else None


def forwarded_body(body: str) -> str:
    """The customer's own words in a forwarded email: what follows the forwarded header block (From:, Date:, Subject:, To:)."""
    m = FORWARD_START.search(body or "")
    rest = body[m.end():] if m else body or ""
    lines = rest.splitlines()
    i = 0
    while i < len(lines) and (not lines[i].strip() or re.match(r"^(From|Date|Sent|Subject|To|Cc):", lines[i].strip(), re.I)):
        i += 1
    return strip_quoted("\n".join(lines[i:]))


def phone_in(text: str) -> str | None:
    m = PHONE.search(text or "")
    return "+91" + re.sub(r"\D", "", m.group(1)) if m else None


def local_part(address: str, domain: str) -> str | None:
    a = (address or "").lower()
    return a.rsplit("@", 1)[0] if domain and a.endswith("@" + domain.lower()) else None
