"""
Win-Back (v2.2): the comeback hello to customers who haven't bought for a while. ONE AI run a week writes a short message
for each kind of customer (bought once / 2–3 times / 4 or more), with the placeholder {name} for the first name.

The AI writes wording only. The hub fills in {name}, and adds the owner's own offer after the message exactly as the owner
typed it. Any message that mentions a price, a discount, an offer, a number, a link, urgency or another placeholder is thrown
away and the fixed wording below is used for that kind of customer instead — so a price or a discount can only ever come from
the owner. Nothing here sends anything.
"""
import re

from .provider import complete
from .tasks import RULES, _v, profile_text

KINDS = {
    "loyal": "a regular who bought 4 or more times",
    "repeat": "a customer who bought 2 or 3 times",
    "once": "a customer who bought once",
}
LABELS = {"loyal": "Regular (4+ purchases)", "repeat": "Bought 2–3 times", "once": "Bought once"}
MAX_CHARS = 320

# words and signs that mean money, deals, numbers, pressure or a promise — the AI may not write any of them
_BANNED = re.compile(
    r"₹|\brs\b|\binr\b|rupee|\bprices?\b|\bpricing\b|\brates?\b|\bcosts?\b|\bcharges?\b|discount|percent|%|\boff\b|\boffers?\b|"
    r"cashback|coupon|\bcode\b|\bdeals?\b|\bsale\b|\bfree (?:gift|delivery|service|sample|installation|consultation|of cost)|\bfor free\b|"
    r"\blakhs?\b|\bcrores?\b|guarantee|\bpromise|limited time|hurry|last chance|today only|\bexpir|\d",
    re.I)
_LINK = re.compile(r"(?i)\b(?:https?|ftp)://|www\.|\b[a-z0-9-]+\.(?:com|in|net|org|io|co|me|ly|link|app|shop|store|biz)\b|\b(?:upi|tel|wa\.me)\b")


def kind_of(purchases: int) -> str:
    return "loyal" if purchases >= 4 else "repeat" if purchases >= 2 else "once"


def fallback(business: str, kind: str) -> dict:
    """Fixed, polite wording — used when the AI's wording can't be used, or there are no AI runs left."""
    b = (business or "").strip() or "us"
    text = {
        "loyal": f"Hello {{name}}, it's been a while since we last heard from you at {b}, and we miss having you with us. "
                 f"Is there anything we can help you with this month? Just reply here.",
        "repeat": f"Hello {{name}}, hope all is well! It's been some time since your last order with {b}. "
                  f"If you need anything, just reply and we'll take care of it.",
        "once": f"Hello {{name}}, thank you again for choosing {b}. If there's anything you need now, just reply here and we'll be happy to help.",
    }[kind]
    return {"whatsapp": text, "subject": f"A quick hello from {b}", "by": "template"}


def _clean(text) -> str:
    return " ".join(str(text or "").split())


def usable(text: str, business: str, need_name: bool, limit: int = MAX_CHARS) -> bool:
    """True when the wording is safe to send in the owner's name: short, no money words or numbers, no links, and only the
    {name} placeholder (required in the message itself). The business's own name may contain anything."""
    if not text or len(text) > limit:
        return False
    holes = re.findall(r"\{[^{}]*\}|\{|\}", text)
    if any(h != "{name}" for h in holes) or (need_name and "{name}" not in text) or text.count("{name}") > 2:
        return False
    rest = text.replace("{name}", " ")
    if business:
        rest = re.sub(re.escape(business.strip()), " ", rest, flags=re.I)
    return not _BANNED.search(rest) and not _LINK.search(rest)


def checked(raw, business: str, kinds: list[str]) -> dict:
    """The AI's answer → {kind: {"whatsapp", "subject", "by": "ai"}} for every kind whose wording is usable. Kinds left out
    get the fixed wording from the caller. Raises when nothing is usable (the run is refunded)."""
    if not isinstance(raw, dict):
        raise ValueError("not an object")
    msgs = raw.get("messages") if isinstance(raw.get("messages"), dict) else raw
    out = {}
    for k in kinds:
        m = msgs.get(k)
        if isinstance(m, str):
            m = {"whatsapp": m}
        if not isinstance(m, dict):
            continue
        text, subject = _clean(m.get("whatsapp")), _clean(m.get("subject"))
        if len(text) < 30 or not usable(text, business, need_name=True):
            continue
        if not subject or not usable(subject, business, need_name=False, limit=80):
            subject = fallback(business, k)["subject"]
        out[k] = {"whatsapp": text, "subject": subject, "by": "ai"}
    if not out:
        raise ValueError("no usable wording")
    return out


def mock(p: dict, kinds: list[str]) -> dict:
    b = _v(p, "name", "us")
    text = {
        "loyal": f"Namaste {{name}}! We haven't seen you at {b} for a while and wanted to say we miss you. Anything you need this month? "
                 f"Just reply to this message.",
        "repeat": f"Hello {{name}}, it's {b} here. It's been a little while since your last order, so we thought we'd check in. "
                  f"Is there anything we can help you with?",
        "once": f"Hello {{name}}, thank you once again for choosing {b}. We'd love to help you again whenever you need us. Just reply here.",
    }
    return {"messages": {k: {"whatsapp": text[k], "subject": f"We miss you at {b}"} for k in kinds}}


async def messages(p: dict, kinds: list[str]):
    wanted = "\n".join(f'- "{k}": {KINDS[k]}' for k in kinds)
    return await complete(RULES + """
You write short WhatsApp messages from a business to past customers who haven't bought for a while — a warm "we miss you" hello.
- Personal and short: at most 45 words, 2 or 3 sentences, as if the owner typed it on their phone.
- Start by greeting {name} — write the placeholder {name} exactly like that; the hub puts each customer's first name there.
- Write the business's name as it is. End with an easy question they can reply to.
- NEVER mention a price, rate, discount, offer, sale, free item, percentage, date or any number. No promises, no urgency
  ("hurry", "last chance"), no links. If the owner has an offer, the hub adds it after your message — don't write one.
- No emojis. Use the business's preferred language.""", f"""{profile_text(p)}

Write one message for each kind of customer:
{wanted}

Also a short email subject line for each (at most 8 words, no {{name}}, no numbers).
JSON only: {{"messages": {{"<kind>": {{"whatsapp": "...", "subject": "..."}}}}}}""", lambda: mock(p, kinds), 700)
