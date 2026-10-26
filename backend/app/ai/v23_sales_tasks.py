"""
v2.3 writers for the Sales and Brand engines: Revenue Multiplier ideas (15 per lever), the two brochures, and a week of
social media posts. Every prompt carries only the owner's data and the competitor summary as quoted data
(app/growth_context.py), and every result is shaped and checked by the router before the owner sees it.
"""
from ..growth_context import sources_text
from .kit_tasks import business_type
from .provider import complete
from .tasks import RULES, _short, _v, profile_text

LEVERS = {
    "customers": ("More customers", "getting more new customers: being found, more inquiries, turning more inquiries into customers, "
                                    "referrals and partners"),
    "value": ("Higher average value", "each customer spending more per purchase: packages, premium versions, add-ons, upgrades, "
                                      "minimum orders, presenting prices better"),
    "frequency": ("More frequent buying", "customers coming back and buying more often: reminders, refills, service plans, "
                                          "loyalty, bringing quiet customers back, seasonal reasons to buy"),
}
SOURCES = ("knowledge", "website", "lead_magnet", "competitor", "business_type")
EFFORT = ("low", "medium", "high")


def _offers(p: dict) -> list[str]:
    return [o["name"] for o in p.get("offers") or [] if o.get("name")]


async def multiplier_ideas(ctx: dict, lever: str, features: dict[str, str]):
    p = ctx["profile"]
    kind = business_type(p)
    label, meaning = LEVERS[lever]

    def mock():
        offers = _offers(p) or [_short(_v(p, "industry", "your service"))]
        city, who = _v(p, "city", "your area"), _short(_v(p, "ideal_customer", "your customers")).lower()
        keys = [k for k in features] or [""]
        starts = {"customers": ["A referral card for", "A free check for", "A partner tie-up for", "A demo day for", "A WhatsApp intro for"],
                  "value": ["A premium version of", "A package around", "An add-on for", "A yearly plan for", "A bundle with"],
                  "frequency": ["A reminder after", "A refill plan for", "A service visit after", "A loyalty card for", "A seasonal offer for"]}[lever]
        ideas = []
        for i in range(15):
            offer = offers[i % len(offers)]
            src = SOURCES[i % len(SOURCES)]
            if (src == "competitor" and not ctx.get("competitor_text")) or (src == "website" and not ctx.get("site_text")) \
                    or (src == "lead_magnet" and not ctx.get("magnet")):
                src = "knowledge"
            ideas.append({"title": f"{starts[i % len(starts)]} {offer.lower()} ({i + 1})"[:80],
                          "why": f"From your {'Knowledge Hub' if src == 'knowledge' else src.replace('_', ' ')}: {who} in {city} buy {offer.lower()}.",
                          "source": src, "first_step": f"This week, write down 5 {who} who bought {offer.lower()} and message one of them.",
                          "effort": EFFORT[i % 3], "impact": EFFORT[(i + 1) % 3], "feature": keys[i % len(keys)]})
        return {"ideas": ideas}

    feature_list = "\n".join(f"- {k}: {v}" for k, v in features.items())
    levers = ctx.get("levers") or {}
    numbers = ""
    if levers and levers.get("customers"):
        numbers = (f"\nTheir own numbers (for context only — never put rupee amounts or promised results in an idea): about "
                   f"{levers.get('customers'):g} customers a year, buying {levers.get('frequency') or 0:g} times a year.")
    return await complete(RULES + "\nYou are the owner's growth adviser. You suggest ideas; you never promise results.", f"""{profile_text(p)}
Business type: {kind}{numbers}

{sources_text(ctx)}

Write exactly 15 ideas to grow this business's revenue through ONE lever: {label} — {meaning}.
Rules:
- Every idea must fit THIS business (what it does, its offers, its customers, its city). No generic tips: say what, for whom, and why here.
- "why": one line that names where the idea comes from — their Knowledge Hub, their website, their lead magnet, or the competitor
  summary (say "a competitor …", never a name) — and why it fits them.
- "source": one of {", ".join(SOURCES)} ("business_type" = common for this kind of business, when no source above shows it).
- Never name a competitor, never copy a competitor's words, never write a web address, email or phone number.
- No rupee amounts, no growth percentages, no promised results. "impact" is only low, medium or high.
- "first_step": one concrete thing the owner can do this week.
- "feature": the OwnerDesk feature that helps do it, by key from this list, or "" when none fits:
{feature_list}
JSON only: {{"ideas": [{{"title": "max 10 words", "why": "one line", "source": "one of the sources", "first_step": "one line",
"effort": "low|medium|high", "impact": "low|medium|high", "feature": "key or empty"}}]}}""", mock, 3800)


BROCHURES = {
    "business": ("Business brochure", "a company profile a prospect can read in two minutes: who they are, what they do, who they help, "
                                      "why customers choose them, how working with them goes"),
    "products": ("Products & services brochure", "one section per product or service from the owner's list (names exactly as written; "
                                                 "a price only exactly as the owner wrote it, or none), what each includes and who it is for, then why "
                                                 "customers choose them"),
}


async def brochure(ctx: dict, kind: str):
    p = ctx["profile"]
    title, meaning = BROCHURES[kind]
    msgs = ctx.get("messages") or {}
    core = msgs.get("core") or {}

    def mock():
        name, city = p.get("name", "Your business"), _v(p, "city", "your city")
        why = [x.strip() for x in str(p.get("why_us") or "").splitlines() if x.strip()]
        if kind == "business":
            secs = [{"heading": "About us", "text": f"{name} — {_v(p, 'industry', 'our work')}, in {city}.", "points": []},
                    {"heading": "Who we help", "text": _v(p, "ideal_customer", "Customers who want it done right."), "points": []},
                    {"heading": "Why customers choose us", "text": "", "points": why[:4] or ["Clear answers, on time"]}]
        else:
            secs = [{"heading": o.get("name", ""), "text": f"Ask us about {o.get('name', '').lower()}.",
                     "points": [" ".join(x for x in (o.get("price", ""), o.get("unit", "")) if x)] if o.get("price") else []}
                    for o in p.get("offers") or [] if o.get("name")][:8] or [{"heading": _v(p, "industry", "What we do"), "text": "", "points": []}]
            secs.append({"heading": "Why customers choose us", "text": "", "points": why[:4]})
        return {"headline": core.get("one_line") or name, "subheadline": f"{_v(p, 'industry', '')} in {city}".strip(), "sections": secs,
                "proof": [x for x in str(p.get("proof") or "").splitlines() if x.strip()][:3], "cta": "Message us to talk about what you need."}

    kit = ""
    if core.get("one_line"):
        kit = f"\nTHEIR BRAND KIT: one line — {core.get('one_line')}\nPitch — {core.get('pitch', '')}\nProof they gave — {'; '.join(core.get('proof') or [])}"
    return await complete(RULES + "\nYou write print-ready brochure text in the owner's tone of voice.", f"""{profile_text(p)}{kit}

{sources_text(ctx)}

Write the text of their {title}: {meaning}.
Rules:
- Use only facts from the profile, their website and their brand kit. Use the competitor summary only to make them sound
  DIFFERENT (what they do that a competitor doesn't say) — never copy a competitor's words and never name a competitor.
- No web address, email or phone number anywhere (the hub adds their contact details itself).
- "proof": only lines the owner gave as proof (profile or brand kit). If there is none, an empty list.
- Short, plain sentences a busy buyer reads quickly. 4 to 8 sections.
JSON only: {{"headline": "max 12 words", "subheadline": "one line", "sections": [{{"heading": "max 6 words", "text": "2-3 sentences or empty",
"points": ["short point"]}}], "proof": ["line"], "cta": "one line asking them to get in touch, no contact details"}}""", mock, 2600)


async def social_week(ctx: dict, festivals: list[dict]):
    p = ctx["profile"]
    msgs = ctx.get("messages") or {}

    def mock():
        offers = _offers(p) or [_v(p, "industry", "what we do")]
        posts = []
        for i in range(7):
            o = offers[i % len(offers)]
            posts.append({"headline": f"{o} for {_short(_v(p, 'ideal_customer', 'you'))}"[:80], "body": f"{p.get('name', '')} in {_v(p, 'city', '')}: ask us about {o.lower()}.",
                          "cta": "Message us", "caption": f"{o} — {p.get('name', '')}. Message us on WhatsApp to know more.", "festival": ""})
        return {"posts": posts}

    fest = "\n".join(f"- {f['date']}: {f['name']}" for f in festivals) or "(none in the next weeks)"
    voice = (msgs.get("social") or {}).get("instagram", "")
    return await complete(RULES + "\nYou write a week of social media posts for the owner, ready to post.", f"""{profile_text(p)}
Their Instagram bio, for the voice: {voice}

{sources_text(ctx)}

Write 7 posts for the next 7 days. Each post becomes a square image (headline, a short line, a button) plus a caption.
Mix them: their offers, why customers choose them, who they help, a helpful tip from their work, their free guide if they have one.
Festivals and occasions coming up (use one only if it falls in the next 7 days; then set "festival" to its name):
{fest}
Rules: only facts from the profile and their website; no prices unless copied exactly from the profile; never copy a competitor;
no web addresses, emails or phone numbers; no hashtags spam (at most 3 at the end of the caption).
JSON only: {{"posts": [{{"headline": "max 8 words", "body": "max 25 words", "cta": "max 4 words", "caption": "2-4 short lines",
"festival": "festival name or empty"}}]}}""", mock, 2600)
