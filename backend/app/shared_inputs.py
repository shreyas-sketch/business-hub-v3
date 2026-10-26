"""
Shared inputs (v2.3): the few things several features need, asked once and reused everywhere.

    businesses.inputs = {website_url, has_website (True | False | None: not asked yet), competitors [url, … up to 3; one used today],
                         about, offers, audience, pains, confirmed_at, updated_at}

- Their website (optional) and one competitor's website (optional, recommended).
- The 4 questions — What is your business about? / Your core products, offers or services? / Who is your target
  audience? / What are their pain points? — asked when they have no website, and confirmed before a lead magnet is made.
- Pre-filled: a question the Knowledge Hub (or their website) already answers comes pre-filled for the owner to confirm,
  so nobody types the same thing twice. get_inputs() returns the value in use and where it came from.

Used by: the Website (built from their site or from the 4 answers), the Lead magnet (8 ideas from the 4 answers), and the
competitor summary (app/competitor_watch.py) that the Website, Lead magnet, Revenue Multiplier, Pipeline builder and
Brochures use to position the owner differently.
"""
import re

from .db import db

QUESTIONS = {
    "about": "What is your business about?",
    "offers": "What are your core products, offers or services?",
    "audience": "Who is your target audience?",
    "pains": "What are their pain points?",
}
HINTS = {
    "about": "One or two lines, the way you'd say it to a new customer.",
    "offers": "Your main products or services. Prices only if you share them openly.",
    "audience": "Who buys from you — the kind of person or company.",
    "pains": "The problems or worries they come to you with.",
}
MAX_LEN = {"about": 400, "offers": 600, "audience": 300, "pains": 500}
MAX_COMPETITORS = 3


def _clean(v) -> str:
    return re.sub(r"[ \t]+", " ", str(v or "")).strip()


def derived(b: dict) -> dict:
    """What the Knowledge Hub already says for each question (empty when it doesn't)."""
    about = _clean(b.get("industry"))
    if about and b.get("city"):
        about = f"{about.rstrip('.')} — in {_clean(b['city'])}"
    offers = []
    for o in b.get("offers") or []:
        if o.get("name"):
            price = " ".join(x for x in (_clean(o.get("price")), _clean(o.get("unit"))) if x)
            offers.append(f"{_clean(o['name'])}" + (f" ({price})" if price else ""))
    return {"about": about[:MAX_LEN["about"]], "offers": ", ".join(offers)[:MAX_LEN["offers"]],
            "audience": _clean(b.get("ideal_customer"))[:MAX_LEN["audience"]], "pains": ""}


async def known_website(ws: str, b: dict) -> tuple[str, str]:
    """Their own website as the hub already knows it: (url, where from)."""
    inp = b.get("inputs") or {}
    if inp.get("website_url"):
        return inp["website_url"], "you"
    scan = await db().site_scans.find_one({"_id": ws}, {"url": 1})
    if scan and scan.get("url"):
        return scan["url"], "website_read"
    run = await db().event_runs.find_one({"user_id": ws, "input.website_url": {"$nin": [None, ""]}}, {"input.website_url": 1},
                                         sort=[("created_at", -1)])
    if run:
        return run["input"]["website_url"], "event"
    return "", ""


async def get_inputs(ws: str, b: dict | None = None) -> dict:
    """The inputs in use: the owner's own answer when given, otherwise what the Knowledge Hub says (marked "brain")."""
    if b is None:
        b = await db().businesses.find_one({"owner_id": ws}) or {}
    inp = b.get("inputs") or {}
    d = derived(b)
    out: dict = {}
    sources: dict = {}
    for k in QUESTIONS:
        mine = _clean(inp.get(k))
        out[k] = mine or d[k]
        sources[k] = "you" if mine else ("brain" if d[k] else "")
    url, url_from = await known_website(ws, b)
    has = inp.get("has_website")
    out.update(website_url=url, website_from=url_from, has_website=has if isinstance(has, bool) else (True if url else None),
               competitors=[c for c in inp.get("competitors") or [] if c][:MAX_COMPETITORS], sources=sources,
               confirmed=bool(inp.get("confirmed_at")))
    out["competitor_url"] = out["competitors"][0] if out["competitors"] else ""
    return out


def answers_text(inp: dict) -> str:
    """The 4 answers as lines for an AI prompt."""
    return "\n".join(f"{QUESTIONS[k]} {inp.get(k) or '(not given)'}" for k in QUESTIONS)


def answered(inp: dict) -> bool:
    """Enough to build a website or ideas from: what the business is about, and what it sells or who it's for."""
    return bool(inp.get("about")) and bool(inp.get("offers") or inp.get("audience"))
