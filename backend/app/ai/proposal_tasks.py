"""Inquiries (v2.1, 3.6): the automatic proposal. Written from the Business Brain and the Company Brain only — a price
appears only when the owner wrote it there; an inquiry that matches no offer gets one or two qualifying questions."""
import re

from .provider import complete
from .tasks import RULES, _v, profile_text
from ..text import greeting_name

STOP = {"the", "and", "for", "with", "our", "your", "you", "need", "want", "please", "hi", "hello", "a", "an", "of", "to", "in", "on", "is",
        "are", "we", "i", "my", "me", "it", "this", "that", "can", "do", "does", "share", "rate", "price", "cost", "about", "get", "have"}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2 and w not in STOP}


def match_offers(p: dict, inquiry: str) -> list[dict]:
    """Offers whose name shares a real word with the inquiry (singular/plural tolerant)."""
    ask = _words(inquiry)
    ask |= {w.rstrip("s") for w in ask}
    out = []
    for o in p.get("offers") or []:
        name = _words(o.get("name", ""))
        if name & ask or {w.rstrip("s") for w in name} & ask:
            out.append(o)
    return out[:3]


def _offer_line(o: dict) -> str:
    price = (o.get("price") or "").strip()
    unit = (o.get("unit") or "").strip()
    return f"• {o['name']}" + (f" — {price}{' ' + unit if unit else ''}" if price else "")


def fallback(p: dict, lead: dict) -> dict:
    """The safe reply with no AI and no prices: thanks, and one or two questions to understand the need."""
    name = greeting_name(lead.get("name", "")) or ""
    biz = _v(p, "name", "us")
    hello = f"Hello {name}," if name else "Hello,"
    qs = ["What exactly do you need, and roughly how much (size or quantity)?", "When do you need it, and where are you located?"]
    text = f"{hello}\n\nThank you for contacting {biz}. To send you the right proposal, could you tell us:\n1. {qs[0]}\n2. {qs[1]}\n\nWe'll get back to you the same day."
    return {"matched": False, "offers": [], "subject": f"Your inquiry to {biz}", "email_text": text + f"\n\nRegards,\n{biz}",
            "whatsapp_text": text, "questions": qs}


async def proposal(p: dict, lead: dict, company: str = ""):
    inquiry = (lead.get("message") or "").strip()

    def mock():
        offers = match_offers(p, inquiry)
        if not offers:
            return fallback(p, lead)
        name = greeting_name(lead.get("name", "")) or ""
        biz = _v(p, "name", "us")
        hello = f"Hello {name}," if name else "Hello,"
        why = [x.strip(" .-•") for x in (p.get("why_us") or "").split("\n") if x.strip(" .-•")][:2]
        lines = "\n".join(_offer_line(o) for o in offers)
        nxt = f"Reply here or WhatsApp us on {p['whatsapp']} to fix a time to talk." if p.get("whatsapp") else "Reply here to fix a time to talk."
        body = (f"{hello}\n\nThank you for your inquiry about {offers[0]['name'].lower()}. Here is what we suggest:\n\n{lines}\n\n"
                + (f"Why {biz}: {'; '.join(why)}.\n\n" if why else "") + nxt)
        return {"matched": True, "offers": [o["name"] for o in offers], "subject": f"{offers[0]['name']} — proposal from {biz}",
                "email_text": body + f"\n\nRegards,\n{biz}", "whatsapp_text": body, "questions": []}

    return await complete(RULES, f"""{profile_text(p)}
Company documents (price lists, FAQs, past quotations) that match this inquiry:
{company or '(none)'}

A customer sent this inquiry ({lead.get('source', 'website')}):
Name: {lead.get('name', '')}
Message: {inquiry[:1500]}

Write the reply proposal. Greet them by name, say what they asked in one line, suggest the matching offer(s) from the profile,
give a price or price range ONLY if that exact price is written in the profile or the company documents, never a discount or a
delivery date that isn't written there, and end with one next step (a call, a visit, or a booking). If nothing in the profile
matches what they asked, don't guess: thank them and ask one or two short qualifying questions instead.
JSON: {{"matched": true|false, "offers": ["offer names used"], "subject": "email subject, max 10 words",
        "email_text": "the email, plain text, signed with the business name", "whatsapp_text": "the same for WhatsApp, max 700 characters",
        "questions": ["the qualifying questions, when not matched"]}}""", mock, 1500)


# ───────────────────────── safety: nothing the Brain doesn't say ─────────────────────────
AMOUNT = re.compile(r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d+)?)\s*(lakh|lac|l|k|cr|crore)?|([\d,]+(?:\.\d+)?)\s*(?:/-|rupees)", re.I)
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
LIST_MARK = re.compile(r"^\s*\d{1,2}[.)]\s", re.M)
DATE_WORDS = re.compile(r"\b(\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*|\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)\b", re.I)
DAYNAMES = r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|weekend)"
WHEN = re.compile(r"\b(tomorrow|tonight|(?:next|this)\s+(?:week|month|" + DAYNAMES + r")|by\s+" + DAYNAMES + r"|on\s+" + DAYNAMES +
                  r"|(?:within|in)\s+(?:a|an|one|two|three|four|five|six|seven|ten|a\s+few|few)\s+(?:working\s+)?(?:days?|weeks?|months?|hours?)"
                  r"|(?:one|two|three|four|five|six|seven|ten)\s+(?:working\s+)?(?:days?|weeks?|months?))\b", re.I)
DISCOUNT = re.compile(r"\b(discount|%\s*off|off\s+on|cashback|free\s+of\s+cost|offer\s+price|special\s+price)\b", re.I)
BRAIN_DISCOUNT = re.compile(r"\b\d+\s*%\s*(?:off|discount)|\bdiscount\s+of\s+\S*\d|\bflat\s+(?:₹|rs\.?)?\s*\d[\d,]*\s+off", re.I)


def _norm_amount(num: str, unit: str | None) -> str:
    n = float(num.replace(",", "") or 0)
    mult = {"lakh": 1e5, "lac": 1e5, "l": 1e5, "k": 1e3, "cr": 1e7, "crore": 1e7}.get((unit or "").lower(), 1)
    return str(round(n * mult, 2))


def amounts(text: str) -> set[str]:
    return {_norm_amount(m.group(1) or m.group(3), m.group(2)) for m in AMOUNT.finditer(text or "") if (m.group(1) or m.group(3))}


def numbers(text: str) -> set[str]:
    """Every number as written, without its commas (1,85,000 → 185000; 2.50 → 2.5)."""
    out = set()
    for n in NUMBER.findall(text or ""):
        n = n.replace(",", "").rstrip(".")
        out.add(n.rstrip("0").rstrip(".") if "." in n else n)
    return out


def unsafe(text: str, brain: str, also_allowed: str = "") -> list[str]:
    """What the proposal says that the Brain doesn't — strictly: any number that isn't in the Brain, the customer's own message
    or the owner's own number; a discount unless the Brain states one; a date or a time promise the Brain doesn't make.
    Empty = safe."""
    out = []
    allowed = numbers(brain) | numbers(also_allowed) | amounts(brain) | {a.split(".")[0] for a in amounts(brain)}
    body = LIST_MARK.sub(" ", text or "")              # "1. What do you need" is a list, not a promise
    out += [f"number {n}" for n in sorted(numbers(body) - allowed)]
    out += [f"price {a}" for a in sorted(amounts(text) - amounts(brain) - {x + ".0" for x in allowed})]
    if DISCOUNT.search(text or "") and not BRAIN_DISCOUNT.search(brain or ""):
        out.append("a discount")
    low_brain = (brain or "").lower()
    for m in DATE_WORDS.finditer(text or ""):
        if m.group(1).lower() not in low_brain:
            out.append(f"date {m.group(1)}")
    for m in WHEN.finditer(text or ""):
        if m.group(1).lower() not in low_brain:
            out.append(f"time promise '{m.group(1)}'")
    return out
