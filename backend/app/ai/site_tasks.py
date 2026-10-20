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
