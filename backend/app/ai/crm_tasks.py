"""
The pipeline builder's one AI job (v2.3, Conversion engine): from the owner's 4 answers, the business profile and (when
known) how the competitor sells, the stages a deal goes through in THIS business, each placed on one of the 5 Football
Field stages, plus the usual reasons people don't buy. The rules version (`rules_pipeline`) is the same shape and is used
for tests, when the owner builds without AI, and whenever the AI's answer can't be used.
"""
from .provider import complete
from .tasks import RULES, profile_text

FF = ("identification", "logic", "pain", "vision", "close")
FF_LABEL = {"identification": "Identification", "logic": "Logic / reason", "pain": "Pain / consequence",
            "vision": "Solution / vision", "close": "Ask / close"}
PATHS = {   # how a customer usually buys → the stage that step adds, and where it sits on the field
    "call": ("Call done", "logic", "Spoke to them and understood the need."),
    "visit": ("Visit / site measurement", "pain", "Saw the place or met in person."),
    "demo": ("Demo or sample", "pain", "They saw it working or tried a sample."),
    "quote": ("Quotation sent", "vision", "Rates are with them."),
    "proposal": ("Proposal sent", "vision", "The full proposal is with them."),
    "direct": ("Ready to buy", "close", "They want to go ahead."),
}
BASE_REASONS = {
    "trading": ["Price too high", "Bought from another supplier", "Stock or size not available", "Needed it faster", "No reply after the quotation"],
    "manufacturing": ["Price too high", "Went with another vendor", "Delivery time too long", "Didn't meet their specification", "Budget or approval not given"],
    "clinic": ["Fees too high", "Went to another clinic", "Timing didn't suit them", "Wanted a second opinion", "No reply after the first call"],
    "food": ["Price too high", "Ordered from somewhere else", "Date or quantity not possible", "Menu didn't suit them", "No reply after the quote"],
    "services": ["Price too high", "Chose a competitor", "Not the right time", "No reply after the quotation", "Budget not approved"],
}


def rules_pipeline(answers: dict, kind: str, competitor: dict | None = None) -> dict:
    """Stages from the buying path, in order, always starting at a new inquiry and ending with the order confirmed."""
    path = [p for p in (answers.get("path") or []) if p in PATHS] or ["call", "quote"]
    stages = [{"name": "New inquiry", "ff": "identification", "hint": "Just came in. Reply first."},
              {"name": "First reply done", "ff": "logic", "hint": "You've replied and know what they want."}]
    for p in path:
        name, ff, hint = PATHS[p]
        if p == "call":
            stages[1] = {"name": "First call done", "ff": "logic", "hint": hint}
            continue
        stages.append({"name": name, "ff": ff, "hint": hint})
    if any(p in path for p in ("quote", "proposal")):
        stages.append({"name": "Follow-up and talks", "ff": "vision", "hint": "Answering questions, settling the details."})
    stages.append({"name": "Order confirmed", "ff": "close", "hint": "Advance or order received."})
    stages = _ordered(stages)
    reasons = list(BASE_REASONS.get(kind, BASE_REASONS["services"]))
    for p in (answers.get("reasons") or []):
        if isinstance(p, str) and p.strip() and p.strip().lower() not in {r.lower() for r in reasons}:
            reasons.insert(0, p.strip()[:80])
    return {"stages": stages[:10], "lost_reasons": reasons[:10]}


def _ordered(stages: list[dict]) -> list[dict]:
    """Keeps each stage once (by name), sorted by its place on the field (stable), so the board reads left to right."""
    seen, out = set(), []
    for s in stages:
        key = s["name"].strip().lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return sorted(out, key=lambda s: FF.index(s["ff"]))


def clean_ai(result: dict, fallback: dict) -> dict:
    """The AI's stages, kept only when they are usable: 3 to 10 named stages, each on a real field stage, starting with a
    new inquiry. Lost reasons are short lines. Otherwise the rules version."""
    raw = result.get("stages") if isinstance(result, dict) else None
    stages = []
    for s in (raw or [])[:12]:
        if not isinstance(s, dict):
            continue
        name = str(s.get("name") or "").strip()[:40]
        ff = str(s.get("ff") or s.get("field_stage") or "").strip().lower()
        if len(name) < 2 or ff not in FF:
            continue
        stages.append({"name": name, "ff": ff, "hint": str(s.get("hint") or "").strip()[:120]})
    stages = _ordered(stages)
    if len(stages) < 3 or stages[0]["ff"] != "identification" or stages[-1]["ff"] != "close":
        raise ValueError("unusable stages")
    reasons = [str(r).strip()[:80] for r in (result.get("lost_reasons") or []) if isinstance(r, str) and len(str(r).strip()) >= 3][:10]
    return {"stages": stages[:10], "lost_reasons": reasons or fallback["lost_reasons"]}


def _answers_text(a: dict) -> str:
    from_path = ", ".join(PATHS[p][0].lower() for p in (a.get("path") or []) if p in PATHS) or "not given"
    return f"""OWNER'S ANSWERS
What they sell and who buys it: {a.get('sell', '')} | Customers: {a.get('customers', '')}
Where leads come from: {', '.join(a.get('sources') or []) or 'not given'}
When a new lead comes in, who handles it: {a.get('who', '')}. What happens: {a.get('after_lead', '')}
Steps before a customer pays: {from_path}. Also: {a.get('before_pay', '')}
Why people don't buy (owner's words): {'; '.join(a.get('reasons') or []) or 'not given'}"""


async def pipeline_stages(p: dict, answers: dict, kind: str, competitor: dict | None):
    fallback = rules_pipeline(answers, kind, competitor)
    comp = ""
    if competitor:
        comp = f"\nHOW A COMPETITOR SELLS (for context only — never copy its words): {str(competitor.get('how_customers_buy') or competitor.get('buying_path') or '')[:300]}"
    return await complete(RULES, f"""{profile_text(p)}

{_answers_text(answers)}{comp}

Design the sales pipeline for THIS business: the stages a deal moves through, from a new inquiry to the order confirmed.
- 4 to 8 stages, named the way this owner would say it (short, plain, max 4 words each).
- Place each stage on one of the 5 Football Field stages, in order: identification (who they are, what they need), logic
  (the reason they'd buy), pain (what waiting costs them), vision (life with the solution, quotation/proposal), close (ask
  for the order). The first stage is a new inquiry (identification); the last is the order confirmed (close).
- A one-line hint per stage: what is true when a deal is in it.
- 5 to 8 short reasons people in this business don't buy, using the owner's words where given. No prices, no numbers.
JSON only: {{"stages": [{{"name": "...", "ff": "identification|logic|pain|vision|close", "hint": "..."}}], "lost_reasons": ["..."]}}""",
                          lambda: fallback, 1200)
