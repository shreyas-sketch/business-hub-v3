"""Website: edit by prompt (v2.1, 3.3). The owner types what to change ("make it look more premium, add a section about
our 15 years"); the AI returns a patch to only the sections that instruction is about — never a full rewrite — plus any
facts in the instruction that aren't in the Business Brain yet. Those facts are not written onto the page: the owner is
asked to add them to the Knowledge Hub first."""
import json
import re

from .provider import complete
from .tasks import RULES, _first, _v, profile_text

SECTIONS = {
    "headline": "the big line at the top (max 10 words)",
    "subheadline": "one sentence under the headline",
    "cta": "the main button text (max 5 words)",
    "about": "the About us text (2-4 sentences)",
    "offers": "the list of offers [{name, description, price}] — names and prices exactly as in the profile",
    "why": "why customers choose us [{title, text}] (max 6)",
    "steps": "how working with us goes [{title, text}] (max 6)",
    "faq": "questions and answers [{q, a}] (max 10)",
}
STOP = r"(?:and|&|then|also|but|make|put|add|change|please|so|plus|or|move|show|use)\b"
FACT = re.compile(r"\b\d[\d,.]*\s*\+?\s*(?:years?|yrs?|projects?|customers?|clients?|families|homes|kitchens|jobs|orders|"
                  r"branches|stores|outlets|staff|people|cities|awards?|%|percent|lakh|crore|k\b)"
                  rf"(?:\s+(?!{STOP})[A-Za-z'’-]+){{0,4}}", re.I)


def facts_in(text: str) -> list[str]:
    """Number-bearing claims in an instruction: '15 years', '500 projects', '4.8 rating'."""
    out = []
    for m in FACT.finditer(text or ""):
        phrase = re.sub(r"\s+", " ", m.group(0)).strip(" .,")
        if phrase and phrase.lower() not in (x.lower() for x in out):
            out.append(phrase)
    return out[:5]


def targets(instruction: str) -> list[str]:
    """Which sections an instruction is about, by its words (used by the mock and as a hint)."""
    t = (instruction or "").lower()
    hits = []
    rules = [("about", ("about", "story", "years", "experience", "history", "who we are", "team")),
             ("headline", ("headline", "premium", "title", "top line", "heading")),
             ("subheadline", ("subheadline", "tagline", "line under")),
             ("cta", ("button", "whatsapp button", "call to action", "cta")),
             ("faq", ("faq", "question")),
             ("why", ("why choose", "why us", "reasons", "trust")),
             ("steps", ("steps", "process", "how it works", "how we work")),
             ("offers", ("offer", "service", "product", "package"))]
    for key, words in rules:
        if any(w in t for w in words) and key not in hits:
            hits.append(key)
    return hits or ["subheadline"]


async def site_patch(p: dict, content: dict, instruction: str):
    def mock():
        keys = targets(instruction)
        name = _v(p, "name", "We")
        why = _first(_v(p, "why_us", ""), "we do the job properly, on time").rstrip(".")
        brain = profile_text(p).lower()
        known = [f for f in facts_in(instruction) if f.lower() in brain]
        patch = {}
        for key in keys:
            if key == "about":
                extra = f" {name} brings {known[0]} to every job." if known else f" What our customers value most: {why[:1].lower() + why[1:]}."
                patch["about"] = (content.get("about") or "").rstrip() + extra
            elif key == "headline":
                patch["headline"] = (content.get("headline") or name).rstrip(".") + " — done properly."
            elif key == "subheadline":
                patch["subheadline"] = f"{name}: {why[:1].lower() + why[1:]}."
            elif key == "cta":
                patch["cta"] = "Chat on WhatsApp now"
            elif key == "faq":
                patch["faq"] = list(content.get("faq") or []) + [{"q": "How do I get started?", "a": "Send us a message on WhatsApp and we'll reply the same day."}]
            elif key == "why":
                patch["why"] = list(content.get("why") or [])[:5] + [{"title": why[:48], "text": ""}]
            elif key == "steps":
                patch["steps"] = list(content.get("steps") or [])
            elif key == "offers":
                patch["offers"] = list(content.get("offers") or [])
        notes = ["I can change the words on your page; the order of sections and buttons stays as it is."] \
            if any(w in (instruction or "").lower() for w in ("higher", "move", "layout", "colour", "color", "bigger")) else []
        return {"patch": patch, "new_facts": [f for f in facts_in(instruction) if f.lower() not in brain], "notes": notes}

    sections = "\n".join(f"- {k}: {v}" for k, v in SECTIONS.items())
    return await complete(RULES, f"""{profile_text(p)}

The website's current content (JSON):
{json.dumps(content, ensure_ascii=False)[:6000]}

The owner asks: "{instruction}"

Change ONLY the sections this request is about. Sections you may change:
{sections}
Return each changed section in full (for a list, the whole new list). Leave every other section out.
Use only facts from the business profile. If the owner's request states a new fact (a number of years, projects, customers,
an award…) that is NOT in the profile, do NOT put it on the page: list it in "new_facts" so the owner can add it first.
If the request is about layout or colours (things you can't change), say so in "notes".
JSON only: {{"patch": {{"section": value}}, "new_facts": ["..."], "notes": ["..."]}}""", mock, 3000)


# ───────────────────────── v2.3: a website from their own site or from the 4 answers ─────────────────────────
def _names(text: str) -> list[str]:
    """'Modular kitchens (₹1,500 per sq ft), wardrobes and false ceilings' → ['Modular kitchens', 'Wardrobes', 'False ceilings']."""
    parts = re.split(r",|;|\n|\band\b|•", re.sub(r"\([^)]*\)", "", text or ""))
    out = []
    for x in parts:
        x = x.strip(" .-–")
        if 2 <= len(x) <= 60 and x.lower() not in (o.lower() for o in out):
            out.append(x[:1].upper() + x[1:])
    return out[:8]


def mock_site_from_inputs(p: dict, brand: dict, inp: dict, own_site: str, comp: dict | None) -> dict:
    name, city = _v(p, "name", "Our business"), _v(p, "city", "")
    about = (inp.get("about") or _v(p, "industry", "")).split(" — ")[0].rstrip(".")
    audience = (inp.get("audience") or _v(p, "ideal_customer", "customers")).rstrip(".")
    pains = (inp.get("pains") or "").rstrip(".")
    offers = [o for o in p.get("offers", []) if o.get("name")]
    offer_list = ([{"name": o["name"], "description": f"Ask us about {o['name'].lower()} for your needs.",
                    "price": " ".join(x for x in [o.get("price", ""), o.get("unit", "")] if x).strip()} for o in offers[:6]]
                  or [{"name": n, "description": f"Ask us about {n.lower()}.", "price": ""} for n in _names(inp.get("offers", ""))[:6]])
    why_lines = [w.strip(" .-•") for w in (p.get("why_us") or "").replace(";", "\n").split("\n") if w.strip(" .-•")]
    why_lines = (why_lines + ["A clear quote before anything starts", "We reply the same day", "One person responsible for your work"])[:3]
    headline = (brand or {}).get("headline") or (brand or {}).get("message") or (about[:60] + (f" in {city}" if city else ""))
    sub = f"For {audience[:1].lower() + audience[1:]}" + (f" who want an end to {pains[:1].lower() + pains[1:]}" if pains else "") + "."
    stand_out = " Unlike most, we tell you exactly what's included before you decide." if comp and comp.get("pricing") else ""
    return {"headline": headline[:120], "subheadline": sub[:300], "cta": "Get a quote on WhatsApp",
            "about": f"{name} — {about[:1].lower() + about[1:]}" + (f", serving {city}" if city else "") + f". We work with {audience[:1].lower() + audience[1:]}." + stand_out,
            "offers": offer_list, "why": [{"title": w[:48], "text": ""} for w in why_lines],
            "steps": [{"title": "Tell us what you need", "text": "Send a message or fill the form."},
                      {"title": "Get a clear quote", "text": "You see what is included before anything starts."},
                      {"title": "We get it done", "text": "One person stays responsible until it's complete."}],
            "faq": [{"q": "How do I start?", "a": "Message us on WhatsApp or fill the form, and we'll get back to you."},
                    {"q": "Which areas do you serve?", "a": f"We work across {city}." if city else "Message us with your location and we'll confirm."}]}


async def site_from_inputs(p: dict, brand: dict, inp: dict, own_site: str, comp: dict | None, competitor_text: str):
    """The one-page website from the owner's own website (their words may be reused — it's their site) or from the 4
    answers, made to stand out against the competitor summary (positioning only, never their words)."""
    from ..shared_inputs import answers_text
    source = (f"THE OWNER'S CURRENT WEBSITE (their own words — you may reuse and improve them):\n{own_site[:9000]}" if own_site
              else "The owner has no website yet. Build it from the profile and the answers above.")
    return await complete(RULES, f"""{profile_text(p)}

THE OWNER'S ANSWERS:
{answers_text(inp)}

BRAND KIT (use when present): headline idea: {(brand or {}).get('headline', '')} · one line: {(brand or {}).get('message', '')} · pitch: {(brand or {}).get('pitch', '')}

{source}

THE MAIN COMPETITOR (the hub's own summary — for positioning only):
{competitor_text}

Write the copy for a better one-page business website for mobile visitors in India. Make the headline and the offers
stand out against the competitor by leaning on what THIS owner really offers (their reasons, their way of working).
Never mention, name or copy the competitor. Never invent prices, numbers, years, awards, clients or guarantees.
JSON: {{
 "headline": "max 10 words",
 "subheadline": "one sentence on who it's for and the result they get",
 "cta": "button text, max 5 words",
 "about": "2-3 sentences",
 "offers": [{{"name": "...", "description": "one sentence", "price": "exactly as given by the owner, or empty"}}],
 "why": [{{"title": "max 6 words", "text": "one sentence"}}] (3 items, only from the owner's real reasons),
 "steps": [{{"title": "...", "text": "..."}}] (3 steps: how working with them goes),
 "faq": [{{"q": "...", "a": "..."}}] (2-4 items, only questions the facts above can answer)
}}""", lambda: mock_site_from_inputs(p, brand, inp, own_site, comp), 4000)
