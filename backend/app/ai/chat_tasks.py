"""
The AI behind WhatsApp chats on the owner's number (Sales Executive and Customer Care Executive), and the Sales Training
Gym's practice customer and scoring. The chat reply never invents a price, a date or a promise: anything that needs the
owner's decision is handed over to a person.
"""
import re

from .provider import complete
from .tasks import RULES, _short, _v, profile_text

HOT = ("visit", "book", "order", "buy", "confirm", "advance", "payment", "pay", "quotation", "quote", "today", "kab aa", "address")
UNHAPPY = ("late", "delay", "problem", "issue", "complaint", "refund", "angry", "not happy", "bad", "worst", "broken", "damage", "kharab")
PRICE = ("price", "rate", "cost", "charges", "kitna", "kitne", "budget", "how much")


def shape(raw: dict) -> dict:
    if not isinstance(raw, dict) or not str(raw.get("reply") or "").strip():
        raise ValueError("no reply")
    intent = raw.get("intent") if raw.get("intent") in ("sales", "support", "other") else "sales"
    return {"reply": str(raw["reply"]).strip()[:1500], "intent": intent, "hot": bool(raw.get("hot")),
            "handover": bool(raw.get("handover")), "why": str(raw.get("why") or "")[:200], "summary": str(raw.get("summary") or "")[:300]}


# Lead Response's "booking only" rule (v2.2): prices, quotes, discounts and negotiation go to a person
ASKS_PRICE = re.compile(r"\b(?:price|prices|pricing|rate|rates|cost|costs|charge|charges|fee|fees|package|packages|tariff|tariffs|payment|"
                        r"amount|kitna|kitne|budget|how much|quote|quotes|quotation|quotations|estimate|discount|discounts|negotiat\w*|"
                        r"best rate|final rate|lowest|cheaper|less price|any offers?|best deal|kam\s+(?:kar|karo|kijiye|ho|hoga)|thoda kam|"
                        r"bhav|daam)\b", re.I)
SAYS_PRICE = re.compile(r"₹|\b(?:rs|inr)\.?\s*\d|\d[\d,]*(?:\.\d+)?\s*(?:/-|rupees?\b|rs\b|inr\b|lakhs?\b|lacs?\b|crores?\b|k\b)|"
                        r"\d+(?:\.\d+)?\s*%|\bdiscount|\bper\s+(?:sq\.?\s*ft|sqft|square feet|piece|pc|unit|kg|month|day|hour|visit|plate|person)\b",
                        re.I)
# booking only: any amount at all — a number of 3 or more digits (1,85,000 · 95000 · 1.85) or a number with L / lac / lakh / k / cr
_AMOUNT = re.compile(r"\d[\d,.]*\d[\d,.]*\d|\d+(?:\.\d+)?\s*(?:l|lac|lacs|lakh|lakhs|k|cr|crore|crores)\b", re.I)
_CLOCK = re.compile(r"\b\d{1,2}(?:[:.]\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.|noon|o'?clock)", re.I)
_URL = re.compile(r"(?i)\bhttps?://\S+")


def names_amount(reply: str, booking_line: str = "") -> bool:
    """Booking only: True when the reply carries a price or any amount — outside the booking line, its link and clock times."""
    rest = (reply or "").replace(booking_line, " ") if booking_line else (reply or "")
    rest = _CLOCK.sub(" ", _URL.sub(" ", rest))
    return bool(SAYS_PRICE.search(rest) or _AMOUNT.search(rest))


def price_handover(p: dict, last: str, booking_line: str = "") -> dict:
    name = _v(p, "name", "us")
    book = f" {booking_line}" if booking_line else ""
    return {"reply": f"Thank you for asking! The {name} team will share the price with you personally — someone will message you shortly.{book}",
            "intent": "sales", "hot": True, "handover": True, "why": "Asked about price — prices and quotes come to you", "summary": last[:200]}


def booking_guard(result: dict, history: list[dict], p: dict, booking_line: str = "") -> dict:
    """With the booking-only rule on: a price question, or any reply that names a price, a discount or any amount, becomes the
    hand-over line (no number in it) and goes to a person. Everything else passes as it is."""
    last = next((m["text"] for m in reversed(history) if m["dir"] == "in"), "")
    if ASKS_PRICE.search(last) or names_amount(result.get("reply") or "", booking_line):
        return {**price_handover(p, last, booking_line), "summary": (result.get("summary") or last)[:300]}
    return result


def mock_reply(p: dict, history: list[dict], booking_only: bool = False, booking_line: str = "") -> dict:
    last = next((m["text"] for m in reversed(history) if m["dir"] == "in"), "")
    low = last.lower()
    name = _v(p, "name", "us")
    if booking_only and not any(w in low for w in UNHAPPY):
        if ASKS_PRICE.search(last):
            return price_handover(p, last, booking_line)
        if any(w in low for w in HOT) and booking_line:
            return {"reply": f"Wonderful! {booking_line} Someone from {name} will confirm it with you.", "intent": "sales", "hot": True,
                    "handover": True, "why": "Ready to go ahead", "summary": last[:200]}
        return booking_guard(mock_reply(p, history), history, p, booking_line)
    if any(w in low for w in UNHAPPY):
        return {"reply": f"I'm sorry to hear that. I've passed this to the {name} team right away — someone will call you shortly to sort it out.",
                "intent": "support", "hot": False, "handover": True, "why": "Customer is unhappy and needs a person", "summary": last[:200]}
    offers = [o for o in p.get("offers", []) if o.get("name")]
    if any(w in low for w in PRICE):
        priced = [f"{o['name']}: {o['price']}{' ' + o['unit'] if o.get('unit') else ''}" for o in offers if o.get("price")]
        lines = ("Here's what we can share: " + "; ".join(priced) + ". ") if priced else ""
        return {"reply": f"Thank you for asking! {lines}The exact price depends on your requirement. Could you share what you need and your location? "
                         f"Our team will send you a clear quote.", "intent": "sales", "hot": any(w in low for w in HOT), "handover": False,
                "why": "Asked for a price", "summary": last[:200]}
    if any(w in low for w in HOT):
        return {"reply": f"Wonderful! I'll have someone from {name} call you to confirm the details. What time suits you today?",
                "intent": "sales", "hot": True, "handover": True, "why": "Ready to go ahead", "summary": last[:200]}
    what = _short(_v(p, "industry", "our work")).lower()
    return {"reply": f"Hello! Thanks for messaging {name}. We help with {what}. Tell me a little about what you need and I'll help you right away.",
            "intent": "sales", "hot": False, "handover": False, "why": "", "summary": last[:200]}


async def reply(p: dict, history: list[dict], context: str, lead: dict | None, booking_only: bool = False, booking_line: str = ""):
    """`booking_only` (Lead Response with prices going to a person): never a price, quote, discount or negotiation — the team
    shares those, and the chat is handed over. The caller also runs booking_guard on the result. Off: exactly as before."""
    convo = "\n".join(f"{'CUSTOMER' if m['dir'] == 'in' else 'BUSINESS'}: {m['text'][:600]}" for m in history[-12:])
    docs = f"\nFACTS FROM THE OWNER'S DOCUMENTS (price list, FAQs, policies):\n{context[:5000]}\n" if context else ""
    known = f"\nThis customer is already a lead: {lead.get('name', '')}, status {lead.get('status')}, notes: {lead.get('note', '')[:200]}\n" if lead else ""
    booking = ("""
- BOOKING ONLY: never share any price, rate, quote, estimate or discount, and never negotiate — even if it is in the profile or
  the documents. If they ask, say the team will share it personally and set handover true. Help them book instead."""
               + (f' The booking line you may use: "{booking_line}"' if booking_line else "")) if booking_only else ""
    return await complete(RULES + """
You are the business's assistant replying on its own WhatsApp number. You speak for the business, warmly and briefly.
- Answer only from the business profile and the documents. If a price or detail isn't there, don't guess: ask what they need and say the team will share it.
- Never promise dates, discounts, availability or anything the owner hasn't written down.
- Reply in the customer's language (Hindi, Hinglish, Gujarati, Marathi or English), at most 60 words, no markdown.
- intent: "sales" (wants to buy or know more), "support" (already a customer, needs help), "other".
- hot: true when they are ready to buy, book a visit, or ask for a quote.
- handover: true when a person must step in (ready to buy, unhappy, a decision only the owner can make, or you can't help).""" + booking,
                          f"""{profile_text(p)}
{docs}{known}
THE CHAT SO FAR:
{convo}

Write the next reply from the business.
JSON only: {{"reply": "...", "intent": "sales|support|other", "hot": false, "handover": false, "why": "one line for the owner when hot or handover", "summary": "one line: what the customer wants"}}""",
                          (lambda: mock_reply(p, history, True, booking_line)) if booking_only else (lambda: mock_reply(p, history)), 600)


# ───────────────────────── Sales Training Gym ─────────────────────────
SCENARIOS = {
    "haggler": ("The price haggler", "You like the offer but push hard on price: ask for a big discount, compare with a cheaper competitor, threaten to walk away."),
    "thinker": ("“I'll think about it”", "You are interested but keep postponing: you need to discuss with family, you're not in a hurry, you want time."),
    "comparer": ("Comparing three vendors", "You have two other quotes and want to know why you should choose this business; you ask pointed questions."),
    "unhappy": ("Unhappy customer", "You bought before and something went wrong; you are upset and want it fixed, and you're testing whether they care."),
}
CRITERIA = ["Listening and questions", "Value before price", "Handling the objection", "Clear next step"]


def gym_customer_mock(scenario: str, turns: int) -> dict:
    lines = {"haggler": ["Your price is too high. Another vendor quoted 30% less.", "Okay, but can you at least give 20% off?",
                         "Hmm. What exactly do I get for the extra money?", "Fine, if you include that, I'll think. What's the next step?"],
             "thinker": ["Sounds good, but let me think about it.", "I need to discuss with my family first.",
                         "There's no hurry from my side, maybe next month.", "Okay, if you can hold that for me, call me on Saturday."],
             "comparer": ["I have two other quotes. Why should I choose you?", "The others are offering a longer warranty.",
                          "How do I know you'll finish on time?", "Alright, send me the details in writing."],
             "unhappy": ["I'm not happy. The work you did last month already has a problem.", "This is the second time I'm calling about it!",
                         "I don't want excuses, I want it fixed.", "Okay. When exactly will your person come?"]}
    seq = lines.get(scenario, lines["haggler"])
    return {"reply": seq[min(turns, len(seq) - 1)], "done": turns >= len(seq) - 1}


async def gym_customer(p: dict, scenario: str, history: list[dict]):
    title, brief = SCENARIOS.get(scenario, SCENARIOS["haggler"])
    convo = "\n".join(f"{'CUSTOMER' if m['role'] == 'customer' else 'SALESPERSON'}: {m['text'][:600]}" for m in history[-16:])
    turns = sum(1 for m in history if m["role"] == "customer")
    return await complete("You play a realistic Indian customer in a sales practice role-play. Stay in character. Short, natural "
                          "replies (1–3 sentences), in the language the salesperson uses. Never coach the salesperson.",
                          f"""{profile_text(p)}

YOUR CHARACTER: {title}. {brief}
THE CONVERSATION SO FAR:
{convo or '(you speak first: open the conversation as this customer)'}

Reply as the customer. Set done=true once the salesperson has clearly agreed a next step or after about 6 turns.
JSON only: {{"reply": "...", "done": false}}""", lambda: gym_customer_mock(scenario, turns), 300)


def score_shape(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("not an object")
    scores = []
    for s in (raw.get("scores") or [])[:6]:
        if isinstance(s, dict) and s.get("criterion"):
            try:
                n = max(0, min(10, int(round(float(s.get("score", 0))))))
            except (TypeError, ValueError):
                n = 0
            scores.append({"criterion": str(s["criterion"])[:60], "score": n, "note": str(s.get("note") or "")[:300]})
    fixes = [str(f)[:300] for f in (raw.get("fixes") or []) if f][:3]
    if not scores or not fixes:
        raise ValueError("incomplete")
    overall = round(sum(s["score"] for s in scores) / len(scores), 1)
    return {"scores": scores, "overall": overall, "fixes": fixes, "best": str(raw.get("best") or "")[:300]}


def score_mock(transcript: list[dict]) -> dict:
    said = " ".join(m["text"].lower() for m in transcript if m["role"] != "customer")
    q = said.count("?")
    value = any(w in said for w in ("include", "value", "quality", "warranty", "result", "because"))
    nxt = any(w in said for w in ("shall we", "book", "visit", "tomorrow", "saturday", "next step", "confirm"))
    discount = any(w in said for w in ("discount", "% off", "reduce", "less price", "kam kar"))
    scores = [{"criterion": "Listening and questions", "score": min(10, 4 + q * 2), "note": f"You asked {q} question(s)."},
              {"criterion": "Value before price", "score": 8 if value and not discount else 5 if value else 3,
               "note": "You explained what's included." if value else "You talked price before value."},
              {"criterion": "Handling the objection", "score": 7 if not discount else 4, "note": "Held the price." if not discount else "Gave a discount quickly."},
              {"criterion": "Clear next step", "score": 8 if nxt else 3, "note": "You asked for a next step." if nxt else "No clear next step was agreed."}]
    fixes = []
    if q < 3:
        fixes.append("Ask at least three questions before you talk about price: what they need, by when, and what matters most.")
    if not value or discount:
        fixes.append("When they push on price, explain what's included and add value instead of cutting the price.")
    if not nxt:
        fixes.append("End every call with a clear next step and a date: “Shall we book the visit for Saturday at 11?”")
    fixes += ["Repeat the customer's main worry back to them in their own words before you answer it.",
              "Use one real customer story that matches their situation."]
    return {"scores": scores, "fixes": fixes[:3], "best": "You stayed polite under pressure."}


async def score_conversation(p: dict, transcript: list[dict], kind: str = "role-play"):
    convo = "\n".join(f"{'CUSTOMER' if m['role'] == 'customer' else 'SALESPERSON'}: {m['text'][:800]}" for m in transcript[-40:])
    return await complete("You are a strict, kind sales coach for Indian small businesses. Score only what the salesperson actually said.",
                          f"""{profile_text(p)}

THE {kind.upper()}:
{convo}

Score the salesperson from 0 to 10 on: {', '.join(CRITERIA)}. One short note each. Then exactly 3 specific fixes for next time,
and the one thing they did best.
JSON only: {{"scores": [{{"criterion": "...", "score": 0, "note": "..."}}], "fixes": ["...", "...", "..."], "best": "..."}}""",
                          lambda: score_mock(transcript), 900)


def call_lines(text: str) -> list[dict]:
    """A pasted call transcript ('Customer: ... / Me: ...') → turns. Lines without a speaker continue the last turn."""
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(customer|client|cust|c|buyer|lead|agent|me|sales|salesperson|s|caller|ai|telecaller)\s*[:\-–]\s*(.+)$", line, re.I)
        if m:
            role = "customer" if m.group(1).lower() in ("customer", "client", "cust", "c", "buyer", "lead") else "salesperson"
            out.append({"role": role, "text": m.group(2)})
        elif out:
            out[-1]["text"] += " " + line
        else:
            out.append({"role": "salesperson", "text": line})
    return out[:80]
