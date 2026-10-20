"""
Day-1 report — Business AI. The one thing a ₹99 webinar attendee does with no login, and the one-page report it makes.

    /report?c=CODE   WhatsApp number, first name, consent → website (optional) → the Score's 10 questions → 6 numbers
    queue            reads the website (the Score's 8 checks — no AI), works out the Business AI Score, picks findings and
                     "imagine" lines from the library below by rules, works out the gap line, renders the PNG and the PDF
    team             Admin → Day-1 reports → Send report / Send all pending (one approved AiSensy media template)
    attendee         /r/<token> (first name only) → "Save with WhatsApp code" → the hub's own login creates a Free account
                     and attaches the run. Any later payment upgrades that same account: the hub has one account per number.

This module is the report itself: the inputs, the library, the rules and the arithmetic. Everything here is plain code
and text; the AI never writes a finding or a number. The admin edits the wording, the order and the 25% cold rate
(Admin → Day-1 reports → Library); the defaults below are what "Reset" goes back to.

Markup inside any text: [words] are systems words (aqua on the report). Rupee amounts are money (amber) automatically.
"""
import copy
import math
import re

from . import score as scoring
from .billing import inr as _inr_paise
from .config import settings
from .db import db

TOOL_KIND = "action_hub_score"
NUMBER_LABEL = "Business AI Score"
CONFIG_ID = "business_ai"
CONSENT_TEXT = "This report is an estimate from the details you gave. It is not an audit, a valuation or professional advice."

# ───────────────────────── the inputs ─────────────────────────
REVENUE_BANDS = [("under_10l", "Under ₹10 L"), ("10_50l", "₹10–50 L"), ("50l_1cr", "₹50 L–1 Cr"), ("1_5cr", "₹1–5 Cr"),
                 ("5cr_plus", "₹5 Cr+")]
TEAM_SIZES = [("0_10", "0–10"), ("10_30", "10–30"), ("30_50", "30–50"), ("50_plus", "50+")]
CLOSE_RATES = [("10", "10%"), ("20", "20%"), ("30", "30%"), ("40", "40%+")]
REVIEWS = [("none", "None"), ("1_5", "1 to 5"), ("6_20", "6 to 20"), ("20_plus", "More than 20"), ("not_sure", "Not sure")]

# Asked with the Score's 10 questions. Not scored (the Score stays comparable month to month); they drive the gap line,
# the tiles and some findings. No GST number is ever asked.
NUMBERS = [
    {"key": "revenue_band", "label": "Yearly revenue", "kind": "choice", "choices": REVENUE_BANDS},
    {"key": "team_size", "label": "People in your team", "kind": "choice", "choices": TEAM_SIZES},
    {"key": "enquiries", "label": "Enquiries in a month", "kind": "number", "min": 0, "max": 100_000,
     "hint": "Calls, WhatsApp messages, forms and walk-ins asking to buy. Your best guess is fine."},
    {"key": "close_rate", "label": "Out of every 10 enquiries, how many buy?", "kind": "choice", "choices": CLOSE_RATES},
    {"key": "aov", "label": "Average order value (₹)", "kind": "number", "min": 1, "max": 10_00_00_000,
     "hint": "What one customer usually pays you for one order."},
    {"key": "reviews_12m", "label": "New Google reviews in the last 12 months", "kind": "choice", "choices": REVIEWS},
]
NUMBER_KEYS = [n["key"] for n in NUMBERS]
_CHOICES = {n["key"]: dict(n["choices"]) for n in NUMBERS if n["kind"] == "choice"}


class InputError(ValueError):
    """A plain sentence the attendee can act on."""


def clean_numbers(raw: dict | None, required: bool = True) -> dict:
    """The six numbers, checked. With required=False (the logged-in Score), missing ones are simply left out."""
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for n in NUMBERS:
        k, v = n["key"], raw.get(n["key"])
        if v is None or v == "":
            if required:
                raise InputError(f"Please answer: {n['label']}.")
            continue
        if n["kind"] == "choice":
            v = str(v)
            if v not in _CHOICES[k]:
                raise InputError(f"Please pick one answer for: {n['label']}.")
            out[k] = v
        else:
            try:
                f = float(str(v).replace(",", "").replace("₹", "").strip())
            except ValueError:
                raise InputError(f"{n['label']}: please type a number.")
            if not math.isfinite(f) or f < n["min"] or f > n["max"]:
                raise InputError(f"{n['label']}: please type a number from {n['min']:,} to {n['max']:,}.")
            out[k] = int(round(f))
    return out


NAME_RE = re.compile("[^A-Za-zÀ-ɏ .'-]")   # the report's fonts are Latin; other scripts would print as boxes


def clean_name(raw: str, limit: int) -> str:
    """Names and cities print on the report, so they keep letters, spaces and . ' - only (no markup, no links)."""
    s = re.sub(r"\s+", " ", NAME_RE.sub("", raw or "")).strip(" .'-")
    return s[:limit].strip()


def first_name(raw: str) -> str:
    s = clean_name(raw, 60).split(" ")[0]
    return (s[:1].upper() + s[1:])[:30]


def site_host(url: str | None) -> str:
    if not url:
        return ""
    host = re.sub(r"^https?://", "", url.strip(), flags=re.I).split("/")[0].split("?")[0].split("#")[0].split(":")[0]
    host = re.sub(r"^www\.", "", host.lower())
    return re.sub(r"[^a-z0-9.-]", "", host)[:60]


# ───────────────────────── the gap line ─────────────────────────
def gap(numbers: dict, cold_rate_pct: int) -> dict:
    """lost enquiries = enquiries a month × cold rate; × close rate × average order × 12 → about ₹X a year.
    Whole-number arithmetic, so the figure is exactly the formula (rounded to the rupee)."""
    enq, close, aov = numbers["enquiries"], int(numbers["close_rate"]), numbers["aov"]
    year_x10000 = enq * cold_rate_pct * close * aov * 12          # ÷ 100 (cold rate) ÷ 100 (close rate)
    return {"enquiries": enq, "cold_rate_pct": cold_rate_pct, "close_pct": close, "aov": aov,
            "lost_month": enq * cold_rate_pct / 100, "not_closed": enq * (100 - close) / 100,
            "gap_month": round(enq * cold_rate_pct * close * aov / 10_000), "gap_year": round(year_x10000 / 10_000)}


def rupees(n: int | float) -> str:
    return _inr_paise(int(round(n)) * 100)


def count(n: float) -> str:
    """40 → "40" · 2.5 → "2.5" (small numbers keep one decimal, so "about 0" never appears) · 1,25,000 in Indian grouping."""
    if 0 < n < 10 and n != int(n):
        return f"{n:.1f}".rstrip("0").rstrip(".")
    n = round(n)
    return f"{n:,}" if n < 100_000 else rupees(n)[1:]


# ───────────────────────── the library (defaults) ─────────────────────────
# Each finding: when it applies (a fixed rule in code), its label and sentence, and two "imagine" lines that answer it.
# Order = priority: the first three that apply are the three findings. Placeholders are listed in PLACEHOLDERS.
FINDINGS = [
    ("no_website", "No website was given",
     "No website working for you",
     "People who hear about you and search find nothing they can act on, so all {enquiries} enquiries a month depend on someone reaching you by phone.",
     ["A live website at your own employz.ai address, with your services, prices and a [WhatsApp button].",
      "An [enquiry form] that catches visitors at any hour and sends their number to your WhatsApp."]),
    ("no_form", "The website has no enquiry form",
     "Website isn't catching anyone",
     "{site} has no [enquiry form], so a visitor at 11 pm leaves without a way to reach you. At {enquiries} enquiries a month, the ones who never call are the ones you never see.",
     ["An [enquiry form] on {site} that sends every visitor's number to your WhatsApp.",
      "Enquiries that arrive at night saved and waiting for you, not lost."]),
    ("quiet_google", "No new Google reviews in the last 12 months",
     "Google profile is quiet",
     "No new Google reviews in 12 months. Someone comparing you with a competitor sees fresh reviews on theirs and none on yours.",
     ["Fresh Google reviews from this month's customers on your profile.",
      "A [review request] that goes to every happy customer after the job."]),
    ("slow_reply", "Not every enquiry gets a reply within the hour",
     "Enquiries wait for a reply",
     "Not every enquiry gets an answer within the hour. Of your {enquiries} a month, the ones you answer late usually buy from whoever answered first.",
     ["Every enquiry answered within minutes, even when you're busy on a job.",
      "A [ready reply] for the questions you get asked every day."]),
    ("no_followup", "Leads are not followed up to a clear yes or no",
     "Leads stop after the first try",
     "Not every lead is followed up to a clear yes or no. At your {close_rate} close rate, about {not_closed} enquiries a month end without a decision.",
     ["A [follow-up] on day 1, 3 and 7 for every lead, until a clear yes or no.",
      "One list each morning of who to follow up and what to say."]),
    ("no_whatsapp", "The website has no Chat on WhatsApp button",
     "No WhatsApp button on the site",
     "{site} has no [Chat on WhatsApp button], and WhatsApp is how most of your customers would rather reach you.",
     ["A [Chat on WhatsApp button] on every page of your website.",
      "Every WhatsApp enquiry from the site landing with the visitor's question already typed."]),
    ("referral_only", "New enquiries don't come in every week",
     "Enquiries depend on referrals",
     "New enquiries don't arrive every week from a channel you control, so a quiet month of referrals is a quiet month for the business.",
     ["One [channel] that brings enquiries every week, with a weekly target you can see.",
      "A [lead magnet] on your website that turns visitors into enquiries."]),
    ("no_proof", "The website shows no reviews or testimonials",
     "No proof on the website",
     "{site} shows no reviews or testimonials, so a first-time visitor has only your word that you're good.",
     ["Three customer reviews with names on your website.",
      "A short [case study] of one happy customer, ready to share."]),
    ("not_mobile", "The website isn't set up for phones",
     "Website is hard to use on a phone",
     "{site} isn't set up for phones, and most people who find you are on one.",
     ["A website that opens fast and reads well on a phone.",
      "Your phone number and [WhatsApp button] one tap away on every page."]),
    ("irregular_posting", "Fewer than 3 posts a week",
     "Quiet on social media",
     "You don't post 3 times a week, so people who don't need you today have forgotten you by the time they do.",
     ["A week of posts written in your voice, ready every Monday.",
      "Posts going out 3 times a week without you sitting down to write them."]),
    ("no_offer_ladder", "No easy first offer, main offer and premium offer",
     "No easy first offer",
     "There's no small first offer, so at {aov} a new customer has to make a big decision on day one.",
     ["An [offer ladder] on your website: an easy first offer, your main offer and a premium one.",
      "A small first offer that lets a new customer try you without a big decision."]),
    ("runs_through_you", "Main jobs are not written down",
     "It all runs through you",
     "Your main jobs aren't written down, so in a team of {team_size}, work waits for you to explain it again.",
     ["Your most common job written as a [checklist] your team follows without you.",
      "A new team member able to do the job the same way from the first week."]),
    ("no_numbers", "Numbers are not checked every week",
     "Numbers aren't checked weekly",
     "Enquiries, sales and money owed aren't on one page every week, so a slow month shows up only after it's over.",
     ["Four numbers on one page every Monday: enquiries, sales, money owed and money in.",
      "A [weekly score] that tells you in two minutes whether the week is on track."]),
    ("late_payments", "Customers don't pay on time without chasing",
     "Money comes in late",
     "Customers don't always pay on time without being chased, so your cash sits in other people's accounts.",
     ["A polite [payment reminder] that goes out on every due date.",
      "An advance and a due date on every bill you send."]),
    ("margin_blind", "Margin per product is not known",
     "Margins aren't known per product",
     "You don't know the margin on each main product, so some sales at {aov} may be paying you far less than they look.",
     ["The margin on each of your top 5 products, worked out.",
      "Prices you set knowing what each sale leaves you."]),
    ("focus_blind", "The most profitable customers are not identified",
     "Best customers aren't identified",
     "You don't yet know which customers bring the most profit for the least effort, so your time goes to whoever asks first.",
     ["Your best customers named, and most of your goal aimed at them.",
      "A [customer portfolio] that shows who to look after first."]),
]
FINDING_KEYS = [f[0] for f in FINDINGS]

# Used only when fewer than five lines come from the findings (a business already strong on most basics).
DEFAULT_IMAGINE = [
    "Every enquiry caught, answered and followed up from one place.",
    "Your Business AI Score re-checked, with what moved since today.",
    "A weekly plan for the business, ready every Monday.",
    "A live website with a [WhatsApp button] and an [enquiry form] working for you.",
    "Four numbers on one page every Monday: enquiries, sales, money owed and money in.",
]

TEMPLATES = {
    "program": ("Program name on the report", "Business AI"),
    "eyebrow": ("Eyebrow", "{program} · your report"),
    "title": ("Title", "{first_name}, here is where you stand"),
    "tile_score": ("Tile 1 label (the number)", "Business AI Score"),
    "tile_systems": ("Tile 2 label", "Systems in place"),
    "tile_site": ("Tile 3 label", "Website checks"),
    "missing_line": ("What you're missing — the gap line", "About {gap_year} a year in enquiries that go cold"),
    "missing_math": ("What you're missing — the arithmetic",
                     "You get about {enquiries} enquiries a month. At a {cold_rate} cold rate, about {lost_month} a month never get a reply or a [follow-up]. "
                     "At your {close_rate} close rate and {aov} average order, that's about {gap_month} a month — about {gap_year} a year at your numbers."),
    "missing_zero_line": ("Gap line when enquiries are 0", "No enquiries a month yet, so nothing goes cold — the gap is getting them at all"),
    "missing_zero_math": ("Arithmetic when enquiries are 0",
                          "You told us you get no enquiries in a month. At your {aov} average order, every enquiry you start getting is about {per_enquiry} at your {close_rate} close rate."),
    "findings_heading": ("Heading: findings", "Where you're lacking"),
    "imagine_heading": ("Heading: imagine", "Imagine — 30 days from now"),
    "missing_heading": ("Heading: gap", "What you're missing"),
    "bottom_heading": ("Heading: bottom line", "Bottom line"),
    "bottom_gap": ("Bottom line — the gap in one sentence", "At your numbers, about {gap_year} a year walks away in enquiries that go cold."),
    "bottom_gap_zero": ("Bottom line when enquiries are 0", "Today the business gets no enquiries in a month, so every month without a system is a month of sales that never start."),
    "bottom_program": ("Bottom line — the program line (bold)", "The 4-3-2-1 Action Program puts all five in place in the next 30 days"),
    "bottom_picture": ("Bottom line — the picture a month out", "A month from now: every enquiry caught, answered and followed up."),
    "footer_method": ("Footer — how the numbers were made",
                      "How we worked this out: the Business AI Score is 10 questions (60 points) and 8 checks on your website (40 points); without a website the questions count for all 100. "
                      "The gap is your enquiries a month × {cold_rate} that go cold × your close rate × your average order × 12. Nothing here was written by AI."),
    "disclaimer": ("Footer — disclaimer", CONSENT_TEXT),
    "credits": ("Footer — program and speakers", "{program} · {speakers}"),
    "whatsapp_line": ("WhatsApp message — line 2", "Your Business AI Score is {score}/100. At your numbers, about {gap_year} a year goes cold."),
    "whatsapp_line_zero": ("WhatsApp message — line 2 when enquiries are 0", "Your Business AI Score is {score}/100. Your full report is ready."),
}

PLACEHOLDERS = {
    "program": "the program name", "first_name": "their first name", "city": "their city", "site": "their website address",
    "score": "the Business AI Score", "band": "Strong / Getting there / Needs attention", "enquiries": "enquiries a month",
    "cold_rate": "the cold rate (25%)", "lost_month": "enquiries that go cold a month", "not_closed": "enquiries a month that don't buy",
    "close_rate": "their close rate", "aov": "their average order", "gap_month": "the gap a month (₹)", "gap_year": "the gap a year (₹)",
    "per_enquiry": "₹ per enquiry at their close rate", "team_size": "their team size", "revenue": "their revenue band",
    "speakers": "the speakers' names",
}
CONSTANTS = {"cold_rate_pct": 25}
# Texts that appear on the share link (/r/<token> and its preview image), which anyone holding the link can open:
# they may name the person by first name only, so no city, website or business inputs.
PUBLIC_TEMPLATES = {"program", "eyebrow", "title", "tile_score", "missing_line", "missing_zero_line"}
PUBLIC_PLACEHOLDERS = {"program", "first_name", "score", "band", "gap_year", "gap_month", "cold_rate"}


def private_placeholders(text: str) -> list[str]:
    return [p for p in _PH.findall(text or "") if p in PLACEHOLDERS and p not in PUBLIC_PLACEHOLDERS]


def defaults() -> dict:
    return {"templates": {k: v for k, (_, v) in TEMPLATES.items()},
            "findings": {k: {"on": True, "priority": (i + 1) * 10, "label": label, "text": text, "imagine": list(imagine)}
                         for i, (k, _rule, label, text, imagine) in enumerate(FINDINGS)},
            "imagine_defaults": list(DEFAULT_IMAGINE),
            "constants": dict(CONSTANTS)}


async def load_config() -> dict:
    """The defaults with the admin's saved edits on top."""
    cfg = defaults()
    saved = await db().day1_config.find_one({"_id": CONFIG_ID}) or {}
    for k, v in (saved.get("templates") or {}).items():
        if k in cfg["templates"] and isinstance(v, str):
            cfg["templates"][k] = v
    for k, v in (saved.get("findings") or {}).items():
        if k in cfg["findings"] and isinstance(v, dict):
            cfg["findings"][k].update({f: v[f] for f in ("on", "priority", "label", "text", "imagine") if f in v})
    if isinstance(saved.get("imagine_defaults"), list):
        cfg["imagine_defaults"] = [str(x) for x in saved["imagine_defaults"]][:5]
    for k, v in (saved.get("constants") or {}).items():
        if k in cfg["constants"]:
            cfg["constants"][k] = v
    cfg["updated_at"] = saved.get("updated_at")
    return cfg


_PH = re.compile(r"\{([a-z_]*)\}")


def unknown_placeholders(text: str) -> list[str]:
    return [p for p in _PH.findall(text or "") if p not in PLACEHOLDERS]


def fill(template: str, values: dict) -> str:
    """{name} → value. Only names from PLACEHOLDERS are filled; nothing else in the text is evaluated."""
    return _PH.sub(lambda m: str(values.get(m.group(1), m.group(0))), template or "").strip()


# ───────────────────────── rules ─────────────────────────
def _no(answers: dict, key: str) -> bool:
    return answers.get(key) in ("no", "partly")


def triggered(answers: dict, site: dict | None, numbers: dict) -> list[str]:
    """Which findings apply, from the inputs only. `site` is None (no website given) or {"read": bool, "checks": {...}}.
    A website that couldn't be opened gives no website findings at all: nothing is guessed."""
    read = bool(site and site.get("read"))
    checks = (site or {}).get("checks") or {}
    rules = {
        "no_website": site is None,
        "no_form": read and not checks.get("form"),
        "quiet_google": numbers.get("reviews_12m") == "none",
        "slow_reply": _no(answers, "reply"),
        "no_followup": _no(answers, "followup"),
        "no_whatsapp": read and not checks.get("whatsapp"),
        "referral_only": _no(answers, "sales"),
        "no_proof": read and not checks.get("proof"),
        "not_mobile": read and not checks.get("mobile"),
        "irregular_posting": _no(answers, "content"),
        "no_offer_ladder": _no(answers, "offers"),
        "runs_through_you": _no(answers, "process"),
        "no_numbers": _no(answers, "numbers"),
        "late_payments": _no(answers, "payments"),
        "margin_blind": _no(answers, "margins"),
        "focus_blind": _no(answers, "focus"),
    }
    return [k for k in FINDING_KEYS if rules.get(k)]


def choose(cfg: dict, keys: list[str]) -> tuple[list[str], list[str]]:
    """The three findings (by the admin's priority, switched-on only) and the five imagine lines that answer them:
    the first line of each finding, then the second line of findings 1 and 2."""
    lib = cfg["findings"]
    on = sorted((k for k in keys if lib[k].get("on", True)), key=lambda k: (lib[k].get("priority", 999), FINDING_KEYS.index(k)))
    chosen = on[:3]
    lines = [lib[k]["imagine"][0] for k in chosen if lib[k]["imagine"]]
    lines += [lib[k]["imagine"][1] for k in chosen if len(lib[k]["imagine"]) > 1]
    for extra in on[3:]:
        lines += lib[extra]["imagine"][:1]
    lines += cfg["imagine_defaults"]
    seen, imagine = set(), []
    for line in lines:
        if line and line not in seen:
            seen.add(line)
            imagine.append(line)
    return chosen, imagine[:5]


# ───────────────────────── the report ─────────────────────────
def values_for(run: dict, result: dict, numbers: dict, cfg: dict) -> dict:
    cold = int(cfg["constants"].get("cold_rate_pct", 25))
    g = gap(numbers, cold)
    labels = _CHOICES
    close = int(numbers["close_rate"])
    return {
        "program": cfg["templates"]["program"], "first_name": run["first_name"], "city": run.get("city") or "",
        "site": (run.get("site") or {}).get("host") or "your website", "score": result["score"], "band": result["band"],
        "enquiries": count(g["enquiries"]), "cold_rate": f"{cold}%", "lost_month": count(g["lost_month"]),
        "not_closed": count(g["not_closed"]), "close_rate": labels["close_rate"][numbers["close_rate"]], "aov": rupees(g["aov"]),
        "gap_month": rupees(g["gap_month"]), "gap_year": rupees(g["gap_year"]),
        "per_enquiry": rupees(g["aov"] * close / 100), "team_size": labels["team_size"][numbers["team_size"]],
        "revenue": labels["revenue_band"][numbers["revenue_band"]], "speakers": settings.speaker_line,
    }, g


def build(run: dict, result: dict, cfg: dict) -> dict:
    """The report, block by block, as plain text with [systems] markup. Rendered to PNG/PDF by day1_render."""
    numbers, answers, site = run["numbers"], run["answers"], run.get("site")
    t = cfg["templates"]
    v, g = values_for(run, result, numbers, cfg)
    keys, imagine = choose(cfg, triggered(answers, site, numbers))
    zero = g["enquiries"] == 0
    yes = sum(1 for k, *_ in scoring.QUESTIONS if answers.get(k) == "yes")
    checks = (site or {}).get("checks") or {}
    passed = sum(1 for k, *_ in scoring.WEBSITE_CHECKS if checks.get(k))
    if site is None:
        site_meta, site_tile = "no website", {"value": "None", "suffix": "", "label": fill(t["tile_site"], v)}
    elif not site.get("read"):
        site_meta, site_tile = f"{site['host']} (couldn't be opened)", {"value": "—", "suffix": "", "label": fill(t["tile_site"], v)}
    else:
        site_meta, site_tile = site["host"], {"value": str(passed), "suffix": f" of {len(scoring.WEBSITE_CHECKS)}", "label": fill(t["tile_site"], v)}
    meta = [run["first_name"], run.get("city") or None, site_meta, f"{v['enquiries']} enquiries a month", f"{v['close_rate']} close rate",
            f"{v['aov']} average order", f"revenue {v['revenue']}", f"team of {v['team_size']}"]
    lib = cfg["findings"]
    return {
        "eyebrow": fill(t["eyebrow"], v),
        "title": fill(t["title"], v),
        "meta": " · ".join(m for m in meta if m),
        "tiles": [{"value": str(result["score"]), "suffix": "/100", "label": fill(t["tile_score"], v)},
                  {"value": str(yes), "suffix": f" of {len(scoring.QUESTIONS)}", "label": fill(t["tile_systems"], v)},
                  site_tile],
        "missing": {"heading": fill(t["missing_heading"], v), "tag": "Estimate",
                    "line": fill(t["missing_zero_line"] if zero else t["missing_line"], v),
                    "math": fill(t["missing_zero_math"] if zero else t["missing_math"], v)},
        "findings_heading": fill(t["findings_heading"], v),
        "findings": [{"key": k, "label": fill(lib[k]["label"], v), "text": fill(lib[k]["text"], v)} for k in keys],
        "imagine_heading": fill(t["imagine_heading"], v),
        "imagine": [fill(line, v) for line in imagine],
        "bottom": {"heading": fill(t["bottom_heading"], v), "gap": fill(t["bottom_gap_zero"] if zero else t["bottom_gap"], v),
                   "program": fill(t["bottom_program"], v), "picture": fill(t["bottom_picture"], v)},
        "footer": {"method": fill(t["footer_method"], v), "disclaimer": fill(t["disclaimer"], v), "credits": fill(t["credits"], v)},
        "whatsapp_line": strip_markup(fill(t["whatsapp_line_zero"] if zero else t["whatsapp_line"], v)),
        "finding_keys": keys,
        "gap": g,
    }


def strip_markup(text: str) -> str:
    return re.sub(r"\[([^\[\]]*)\]", r"\1", text or "")


def sample_run() -> dict:
    """The admin's preview: a made-up attendee (no real person)."""
    return {"first_name": "Riya", "city": "Pune", "site": {"host": "example-interiors.in", "read": True,
            "checks": {"https": True, "mobile": True, "title": True, "contact": True, "whatsapp": False, "form": False, "proof": False, "light": True}},
            "answers": {"focus": "partly", "margins": "yes", "sales": "partly", "payments": "no", "offers": "no", "reply": "partly",
                        "followup": "no", "content": "no", "process": "partly", "numbers": "no"},
            "numbers": {"revenue_band": "50l_1cr", "team_size": "10_30", "enquiries": 40, "close_rate": "20", "aov": 85000, "reviews_12m": "none"}}


def score_for(run: dict) -> dict:
    site = run.get("site")
    website = {"url": site["host"], "checks": site["checks"]} if site and site.get("read") else None
    return scoring.compute(run["answers"], website)


async def preview(sample: dict | None = None, cfg: dict | None = None) -> dict:
    run = copy.deepcopy(sample or sample_run())
    cfg = cfg or await load_config()
    return build(run, score_for(run), cfg)

