"""
Lead magnet (v2.3): 8 ideas from the 4 answers, then the chosen one built in full — the content of the PDF and its landing
page — in one AI run each. Same house rules: only the owner's facts, no invented prices, numbers or claims; the competitor
summary is used for positioning only and every result goes through the no-copy guard (routers/program.py).
"""
import re

from .provider import complete
from .tasks import RULES, _v, profile_text

FORMATS = {
    "checklist": "Checklist",
    "guide": "Step-by-step guide",
    "calculator": "Calculator sheet",
    "price_guide": "Price guide",
    "mistakes": "Mistakes to avoid",
    "questions": "Questions to ask before you buy",
}
LONG = ("guide", "mistakes", "questions")        # 6–8 full sections → 6–12 pages
SHORT = ("checklist", "calculator", "price_guide")   # 3–5 sections of points → shorter


def format_key(raw) -> str:
    s = str(raw or "").lower()
    for k, rx in (("calculator", r"calculat|sheet|work out"), ("price_guide", r"price|cost|budget"), ("mistakes", r"mistake|avoid|wrong"),
                  ("questions", r"question|ask"), ("checklist", r"check"), ("guide", r"guide|step|how")):
        if re.search(rx, s):
            return k
    return "guide"


def _words(text: str, n: int = 6) -> str:
    w = (text or "").split()
    return " ".join(w[:n])


def mock_ideas(p: dict, inp: dict) -> dict:
    who = (inp.get("audience") or _v(p, "ideal_customer", "your customers")).rstrip(".")
    what = (inp.get("about") or _v(p, "industry", "this work")).split(" — ")[0].rstrip(".")
    pain = (inp.get("pains") or f"getting {what.lower()} done right").rstrip(".")
    offers = [o["name"] for o in p.get("offers", []) if o.get("name")] or [what]
    o = lambda i: offers[i % len(offers)].lower()  # noqa: E731
    rows = [
        ("checklist", f"The {o(0)} checklist: what to check before you pay", f"Avoid surprises with {o(0)}"),
        ("mistakes", f"7 mistakes people make with {o(1)}", f"Costly mistakes around {pain[:60]}"),
        ("questions", f"10 questions to ask before you choose a {what.lower()[:40]}", "Choosing the wrong provider"),
        ("guide", f"A simple guide to planning your {o(0)}", f"Not knowing where to start with {o(0)}"),
        ("price_guide", f"What goes into the price of {o(2)}", "Comparing quotes that don't match"),
        ("calculator", f"Work out your {o(0)} budget in 5 minutes", "Not knowing how much to keep aside"),
        ("guide", f"Before, during and after: your {o(1)} timeline", "Delays and confusion about who does what"),
        ("checklist", f"Handover checklist for {o(2)}", "Finding problems after the job is done"),
    ]
    return {"ideas": [{"title": t, "who": f"For {who[:1].lower() + who[1:]}", "problem": prob, "format": f,
                       "why": f"Speaks to what {who.split(' ')[0].lower()} worry about most, and leads naturally to a chat with you."}
                      for f, t, prob in rows]}


async def ideas(p: dict, answers: str, competitor_text: str):
    return await complete(RULES + "\nYou suggest lead magnets: free, genuinely useful resources a customer trades their phone number for.",
                          f"""{profile_text(p)}

THE OWNER'S ANSWERS:
{answers}

THE MAIN COMPETITOR (the hub's own summary — for positioning only, never copy it):
{competitor_text}

Suggest exactly 8 different lead magnet ideas for this business's target audience. Each one solves ONE specific, immediate
problem from their pain points, is quick to read or use, shows the owner's expertise, and leads naturally to contacting them.
Mix the formats: checklist, guide, calculator (a sheet to work something out), price_guide, mistakes, questions.
Where the competitor leaves a gap, an idea can fill it. Never promise prices, numbers or results the owner didn't give.
JSON only: {{"ideas": [{{"title": "max 12 words", "who": "who it's for, one line", "problem": "the problem it solves, one line",
"format": "checklist|guide|calculator|price_guide|mistakes|questions", "why": "one line: why it will bring this owner inquiries"}}]}}""",
                          lambda: mock_ideas(p, _parse(answers)), 2500)


def _parse(answers: str) -> dict:
    """The answers text back into fields (for the test stand-in only)."""
    from ..shared_inputs import QUESTIONS
    out = {}
    for k, q in QUESTIONS.items():
        m = re.search(re.escape(q) + r" (.*)", answers)
        v = (m.group(1).strip() if m else "")
        out[k] = "" if v == "(not given)" else v
    return out


def mock_build(p: dict, inp: dict, idea: dict) -> dict:
    name = _v(p, "name", "Our team")
    fmt = idea.get("format", "guide")
    title = idea.get("title", "Your free guide")
    who = (inp.get("audience") or _v(p, "ideal_customer", "you")).rstrip(".")
    why = [w.strip(" .-•") for w in (p.get("why_us") or "").replace(";", "\n").split("\n") if w.strip(" .-•")] or ["We explain everything in writing"]
    topics = ["Start with what you really need", "Set your budget range before you talk to anyone", "Ask for a written quote, line by line",
              "Check the timeline and who is responsible", "See real work before you decide", "Agree on payment stages", "Plan the handover",
              "Keep everything in writing"]
    n = 7 if fmt in LONG else 4
    sections = []
    for i, h in enumerate(topics[:n]):
        text = (f"{h} is where most people lose time and money. Take a few minutes over it now and the rest of the work becomes far easier. "
                f"Write your answers down so you can compare options calmly, and share them with everyone involved in the decision.\n\n"
                f"Here is how to do it well, step by step. Read each point, tick it when it's done, and come back to this page before every "
                f"meeting so nothing important is missed.") if fmt in LONG else ""
        points = [f"Write down {x}" for x in ("what you want", "what you don't want", "your must-haves", "your deadline")] if i % 2 == 0 else \
                 ["Ask what is included and what is not", "Ask who your single point of contact is", "Ask what happens if there's a delay",
                  "Keep a copy of every agreement"]
        sections.append({"heading": h, "text": text, "points": points + (["Check it again before you pay"] if fmt not in LONG else [])})
    return {"content": {"title": title, "subtitle": idea.get("problem", ""), "intro": f"This {('checklist' if fmt not in LONG else 'guide')} is for "
                        f"{who[:1].lower() + who[1:]}. It comes from the questions our customers ask us every week, so you can decide with confidence.",
                        "sections": sections, "cta": f"Want a second opinion? Message {name} on WhatsApp — we'll go through it with you."},
            "landing": {"headline": title, "who_for": f"For {who[:1].lower() + who[1:]}",
                        "what_you_get": [s["heading"] for s in sections[:5]],
                        "benefits": ["Know exactly what to ask before you decide", "Avoid the mistakes that cost the most", why[0]]}}


async def build(p: dict, answers: str, idea: dict, competitor_text: str):
    fmt = format_key(idea.get("format"))
    length = ("6 to 8 sections. Each section: a heading, 2-3 short paragraphs of real, specific advice (80-150 words), and 3-6 points."
              if fmt in LONG else "3 to 5 sections. Each section: a heading, one line of text, and 5-8 points the reader can tick or fill in.")
    return await complete(RULES + "\nYou write a genuinely useful free resource (a lead magnet) in full, and its download page.", f"""{profile_text(p)}

THE OWNER'S ANSWERS:
{answers}

THE IDEA THE OWNER PICKED:
Title: {idea.get('title', '')}
For: {idea.get('who', '')}
Problem it solves: {idea.get('problem', '')}
Format: {fmt}

THE MAIN COMPETITOR (the hub's own summary — for positioning only, never copy or mention it):
{competitor_text}

Write the whole resource — not an outline. {length}
Useful on its own, specific to this kind of business and its customers in India, written for a busy reader. No fake numbers,
prices, statistics or claims; only the owner's facts. End with a short call to action to message the owner on WhatsApp.
Then write its download page: a headline, who it's for, what they'll get (3-5 items) and exactly 3 benefits.
JSON only: {{"content": {{"title": "max 12 words", "subtitle": "one line", "intro": "2-4 sentences",
"sections": [{{"heading": "...", "text": "paragraphs separated by a blank line", "points": ["..."]}}], "cta": "1-2 sentences"}},
"landing": {{"headline": "max 12 words", "who_for": "one line", "what_you_get": ["..."], "benefits": ["...", "...", "..."]}}}}""",
                          lambda: mock_build(p, _parse(answers), {**idea, "format": fmt}), 7000)
