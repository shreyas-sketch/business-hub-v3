"""
The five AI jobs in this release, mapped to the 4321 program (Structure → Systems → Scale).
Rules every job follows: use only facts the owner gave; never invent prices, numbers, years, awards,
client names or testimonials; write for Indian business owners in plain language.
"""
from ..text import greeting_name
from .provider import complete

RULES = """You work inside the Business AI Action Hub for an Indian business owner.
Rules you never break:
1. Use ONLY the facts in the business profile. If something is missing, leave it out. Never invent prices,
   numbers, years in business, awards, client names, reviews or guarantees.
2. Copy prices exactly as the owner wrote them. Do not add tax, discounts or ranges.
3. Plain, warm, confident language a 50-year-old business owner and his customers would use. No jargon, no hype words
   like "revolutionary", "world-class", "best-in-class", no emojis unless asked.
4. Write in the owner's preferred language."""


def profile_text(p: dict) -> str:
    offers = "\n".join(f"  - {o.get('name')}" + (f" — {o.get('price')}" if o.get("price") else "") + (f" {o.get('unit')}" if o.get("unit") else "")
                       for o in p.get("offers", []) if o.get("name")) or "  (none given)"
    return f"""BUSINESS PROFILE
Name: {p.get('name', '')}
City / area served: {p.get('city', '')}
What the business does: {p.get('industry', '')}
Products / services (as written by the owner):
{offers}
Who buys: {p.get('ideal_customer', '')}
Why customers choose them: {p.get('why_us', '')}
Proof the owner can show (may be empty): {p.get('proof', '')}
Preferred language: {p.get('language', 'English')}""" + voice_text(p.get("voice"))


def voice_text(v: dict | None) -> str:
    """The tone of voice from the Brand kit, which every AI writer follows."""
    if not isinstance(v, dict) or not v.get("tone"):
        return ""
    out = f"\nTone of voice: {v['tone']}"
    for label, key in (("Do", "dos"), ("Don't", "donts"), ("Words we use", "use"), ("Words we avoid", "avoid")):
        items = [str(x) for x in (v.get(key) or []) if str(x).strip()][:8]
        if items:
            out += f"\n{label}: " + "; ".join(items)
    return out


def _v(p: dict, key: str, default: str = "") -> str:
    """Profile value, or the default when the owner left it empty."""
    value = (p.get(key) or "").strip()
    return value or default


def _lc(text: str) -> str:
    """Lower-cases only the first letter, so names and places keep their capitals."""
    return text[:1].lower() + text[1:] if text else text


def _short(text: str, words: int = 6) -> str:
    """'Family dental clinic — check-ups, braces' → 'Family dental clinic'."""
    import re as _re
    head = _re.split(r"\s+[—–-]\s+|,|\s+with\s+|\s+for\s+", (text or "").strip())[0]
    return " ".join(head.split()[:words]).rstrip(".")


def _first(text: str, fallback: str) -> str:
    text = (text or "").strip()
    return text.split("\n")[0].split(".")[0].strip() if text else fallback


# ───────────────────────── Structure: brand message ─────────────────────────
async def brand_message(p: dict):
    def mock():
        name, what, who = _v(p, "name", "We"), _v(p, "industry", "what we do").rstrip("."), _v(p, "ideal_customer", "our customers").rstrip(".")
        city = _short(_v(p, "city", "").replace("&", ","), 3)
        why = _first(_v(p, "why_us", ""), "we do the job properly, on time").rstrip(".")
        where = f" in {city}" if city and city.lower() not in who.lower() else ""
        short = _short(what)
        return {"options": [
            {"message": f"{short}{where}, done right the first time.",
             "pitch": f"{name} helps {_lc(who)} with {_lc(what)}{where}. Customers come back to us for one reason: {_lc(why)}. Tell us what you need and we'll give you a clear plan and a fair price."},
            {"message": f"{name}: {_lc(why)}, every single time.",
             "pitch": f"If you need {_lc(short)}{where}, we handle it end to end so you don't have to chase anyone. {why}."},
            {"message": f"One message to {name}, and it's taken care of.",
             "pitch": f"We are {name}{where}. We do {_lc(what)} for {_lc(who)}. {why}. Message us on WhatsApp and we'll reply the same day."},
        ]}
    return await complete(RULES, f"""{profile_text(p)}

Write the owner's brand message (Structure, part of the 4321 program).
Give 3 different options. Each has:
- "message": one line, at most 14 words, that says who they help and the result. No slogans that could fit any business.
- "pitch": a 30-second spoken pitch, 3–4 short sentences.
JSON: {{"options": [{{"message": "...", "pitch": "..."}}, ...]}}""", mock, 1500)


# ───────────────────────── Systems: website content ─────────────────────────
async def site_content(p: dict, brand: dict | None):
    offers = [o for o in p.get("offers", []) if o.get("name")]

    def mock():
        name, city = _v(p, "name", "Our business"), _v(p, "city", "")
        what = _v(p, "industry", "").rstrip(".")
        why_lines = [w.strip(" .-•") for w in (p.get("why_us") or "").replace(";", "\n").split("\n") if w.strip(" .-•")]
        why_lines = (why_lines + ["Clear quote before we start", "We reply the same day", "One person responsible for your job"])[:3]
        return {
            "headline": (brand or {}).get("message") or _short(what) + (f" in {_short(city.replace('&', ','), 3)}" if city else ""),
            "subheadline": (brand or {}).get("pitch", "").split(". ")[0].rstrip(".") + "." if brand else f"{name} — tell us what you need and get a clear quote.",
            "cta": "Get a quote on WhatsApp",
            "about": f"{name} works with {_lc(_v(p, 'ideal_customer', 'customers')).rstrip('.')}" + (f" across {city}" if city else "") + f". We take care of {_lc(_short(what))} from the first conversation to the final handover, so you always know what is happening and what it costs.",
            "offers": [{"name": o["name"], "description": f"Ask us for details on {o['name'].lower()}.", "price": " ".join(x for x in [o.get("price", ""), o.get("unit", "")] if x).strip()} for o in offers[:6]],
            "why": [{"title": w[:48], "text": ""} for w in why_lines],
            "steps": [{"title": "Tell us what you need", "text": "Send a message or fill the form. We reply the same day."},
                      {"title": "Get a clear quote", "text": "You see exactly what is included before anything starts."},
                      {"title": "We get it done", "text": "One person stays responsible until the job is complete."}],
            "faq": [{"q": "How quickly do you reply?", "a": "Usually within a few hours on working days."},
                    {"q": "Which areas do you serve?", "a": f"We work across {city}." if city else "Message us with your location and we'll confirm."}],
        }
    return await complete(RULES, f"""{profile_text(p)}
Chosen brand message: {(brand or {}).get('message', '')}
Pitch: {(brand or {}).get('pitch', '')}

Write the copy for a one-page business website (Systems, part of the 4321 program). Mobile visitors from India.
JSON: {{
 "headline": "max 10 words, the brand message or close to it",
 "subheadline": "one sentence on who it's for and the result",
 "cta": "button text, max 5 words, e.g. Get a quote on WhatsApp",
 "about": "2-3 sentences",
 "offers": [{{"name": "exactly as given", "description": "one sentence", "price": "exactly as given or empty"}}],
 "why": [{{"title": "max 6 words", "text": "one sentence"}}] (3 items, from 'why customers choose them' only),
 "steps": [{{"title": "...", "text": "..."}}] (3 steps: how working with them goes),
 "faq": [{{"q": "...", "a": "..."}}] (2-4 items, only questions answerable from the profile)
}}
Include only the offers listed in the profile.""", mock, 4000)


# ───────────────────────── Scale: a week of posts ─────────────────────────
async def social_posts(p: dict, focus: str):
    def mock():
        name, what = _v(p, "name", "We"), _v(p, "industry", "our work").rstrip(".")
        offers = [o["name"] for o in p.get("offers", []) if o.get("name")] or [what]
        themes = [("Monday", "A common mistake customers make", f"Most people only call about {what.lower()} when something has already gone wrong. A 10-minute conversation early saves weeks later."),
                  ("Tuesday", "What we actually do", f"{name}: {what}. No confusion about scope — you get a clear quote first."),
                  ("Wednesday", f"Spotlight: {offers[0]}", f"Thinking about {offers[0].lower()}? Here's what to ask before you choose anyone."),
                  ("Thursday", "Behind the scenes", "This is how a job moves at our end, from your first message to handover."),
                  ("Friday", "Question of the week", f"What's the one thing that worries you most about {what.lower()}? Reply and we'll answer it in a post."),
                  ("Saturday", "Why customers choose us", _first(_v(p, "why_us", ""), "We reply the same day and stay with the job till it's done") + "."),
                  ("Sunday", "Plan your week", f"If {what.lower()} is on your list this month, message us on WhatsApp. We'll tell you honestly what it needs.")]
        if focus:
            themes[2] = ("Wednesday", f"Focus: {focus[:40]}", f"This week we're talking about {focus.lower()}. Ask us anything about it.")
        return {"posts": [{"day": d, "hook": h, "caption": c, "hashtags": []} for d, h, c in themes]}
    return await complete(RULES, f"""{profile_text(p)}
Focus for this week (may be empty): {focus}

Write one week of social media posts (Scale, part of the 4321 program) for Instagram, Facebook and WhatsApp Status.
Mix: teach something useful, show the work, answer a customer question, one soft offer. No fake results.
JSON: {{"posts": [{{"day": "Monday", "hook": "first line, max 10 words", "caption": "2-4 sentences", "hashtags": ["max 4, local and specific"]}}]}} — 7 posts, Monday to Sunday.""", mock, 3500)


# ───────────────────────── Scale: customer reply draft ─────────────────────────
async def reply_draft(p: dict, customer_message: str, customer_name: str = ""):
    def mock():
        who = greeting_name(customer_name)
        greet = f"Hello {who}," if who else "Hello,"
        return {"reply": f"{greet}\n\nThank you for reaching out to {p.get('name', 'us')}. We'd be happy to help. Could you share a few details — what exactly you need, the location, and when you'd like it done? We'll send you a clear quote.\n\nRegards,\n{p.get('name', '')}".strip()}
    return await complete(RULES, f"""{profile_text(p)}

A customer wrote: \"\"\"{customer_message[:2000]}\"\"\"
Customer name: {customer_name or 'unknown'}

Draft the owner's reply for WhatsApp (Scale, part of the 4321 program). Short, warm, helpful. Answer from the profile;
if a fact is missing (price not listed, availability), ask one clarifying question instead of guessing.
JSON: {{"reply": "..."}}""", mock, 1000)


# ───────────────────────── Scale: Business GPT Q&A ─────────────────────────
async def business_qa(p: dict, question: str):
    def mock():
        return {"answer": f"Here's how to think about it for {p.get('name', 'your business')}:\n\n"
                          f"1. Structure — be clear on the one offer and customer this decision is about.\n"
                          f"2. Systems — write down the steps so it happens the same way every time, not only when you remember.\n"
                          f"3. Scale — once it works for 10 customers, put it on your website and your weekly posts.\n\n"
                          f"Start with one small test this week and look at the result next Monday."}
    return await complete(RULES + "\nYou are also the owner's business advisor using the 4321 approach: Structure → Systems → Scale.", f"""{profile_text(p)}

The owner asks: \"\"\"{question[:1500]}\"\"\"

Answer practically for this business in under 180 words: what to do this week, in order. Short numbered steps.
If the question needs facts you don't have (their numbers, their market), say what to check instead of guessing.
JSON: {{"answer": "..."}}""", mock, 1200)
