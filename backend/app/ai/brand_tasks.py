"""Brand kit (v2.1, 3.11): the message system — the core message and its versions for the website, marketing, sales and
social media, plus the tone of voice every AI writer then uses. Only facts in the Business Brain; nothing invented."""
from .provider import complete
from .tasks import RULES, _first, _lc, _short, _v, profile_text


def _proof(p: dict) -> list[str]:
    lines = [x.strip(" .-•") for x in (p.get("proof") or "").replace(";", "\n").split("\n") if x.strip(" .-•")]
    lines += [x.strip(" .-•") for x in (p.get("why_us") or "").replace(";", "\n").split("\n") if x.strip(" .-•")]
    return (lines + ["Clear quote before any work starts", "One person responsible for your job", "We reply the same day"])[:3]


async def message_system(p: dict, brand: dict | None):
    def mock():
        name, city = _v(p, "name", "We"), _short(_v(p, "city", "").replace("&", ","), 3)
        what, who = _v(p, "industry", "what we do").rstrip("."), _v(p, "ideal_customer", "our customers").rstrip(".")
        why = _first(_v(p, "why_us", ""), "we do the job properly, on time").rstrip(".")
        one = (brand or {}).get("message") or f"{_short(what)}{' in ' + city if city else ''}, done right the first time."
        pitch = (brand or {}).get("pitch") or f"{name} helps {_lc(who)} with {_lc(what)}. {why}."
        offers = [o["name"] for o in p.get("offers", []) if o.get("name")] or [_short(what)]
        where = f" in {city}" if city else ""
        return {
            "core": {"one_line": one, "pitch": pitch, "positioning": {"who": who, "what": what, "why": why}, "proof": _proof(p)},
            "website": {"headline": one, "subheadline": f"{name}{where} — {_lc(_short(what))} for {_lc(who)}.",
                        "about": f"{name} works with {_lc(who)}{where}. {why}.",
                        "ctas": ["Get a quote on WhatsApp", "Tell us what you need", f"Ask about {offers[0].lower()}"]},
            "marketing": {"ad_headlines": [f"{_short(what)}{where}", f"Planning {offers[0].lower()}? Talk to {name}", f"{why[:60]}",
                                           f"{name}: {_lc(one)}"[:90], f"Ask {name} for a clear quote"],
                          "festival_line": f"This festive season, let {name} take care of {_lc(offers[0])}.", "tagline": one[:60]},
            "sales": {"elevator": pitch, "whatsapp_intro": f"Hello! This is {name}{where}. {why}. Tell us what you need and we'll send you a clear quote.",
                      "proposal_opener": f"Thank you for considering {name}. Here is what we suggest, based on what you told us.",
                      "email_signature": f"{name}{chr(10) + p.get('city', '') if p.get('city') else ''}{chr(10) + 'WhatsApp ' + p['whatsapp'] if p.get('whatsapp') else ''}"},
            "social": {"instagram": f"{_short(what)}{where}. {why}. Message us to get started.", "linkedin": f"{name} — {_lc(what)} for {_lc(who)}{where}.",
                       "facebook": f"{name}{where}: {_lc(what)}. {why}.", "whatsapp_business": f"{_short(what)}{where}. We reply the same day.",
                       "hooks": [f"Most people get {_lc(offers[0])} wrong. Here's why.", "Before you choose anyone, ask these 3 questions.",
                                 "What a good job actually looks like.", f"Why {_lc(who)} come back to us.", "The one thing we never skip."]},
            "voice": {"tone": "Warm, clear and practical — like a trusted local expert.",
                      "dos": ["Use short sentences", "Say what happens next", "Use the customer's words", "Give one clear next step", "Be specific about what's included"],
                      "donts": ["No hype words", "No jargon", "No promises we can't keep", "No prices that aren't on our list", "No pressure tactics"],
                      "use": ["clear quote", "on time", "we'll take care of it"], "avoid": ["best in class", "world-class", "guaranteed lowest"]},
        }
    return await complete(RULES, f"""{profile_text(p)}
Chosen brand message: {(brand or {}).get('message', '')}
Pitch: {(brand or {}).get('pitch', '')}

Write the owner's brand message system (Structure, part of the 4321 program). Every line uses only facts in the profile.
JSON: {{
 "core": {{"one_line": "the brand message, max 14 words", "pitch": "30-second pitch, 3-4 sentences", "positioning": {{"who": "...", "what": "...", "why": "..."}},
          "proof": ["3 proof points, only facts from the profile"]}},
 "website": {{"headline": "max 10 words", "subheadline": "one sentence", "about": "2-3 sentences", "ctas": ["3 button texts, max 5 words"]}},
 "marketing": {{"ad_headlines": ["5, max 8 words each"], "festival_line": "one line for a festival offer post, no price unless in the profile", "tagline": "max 6 words"}},
 "sales": {{"elevator": "...", "whatsapp_intro": "...", "proposal_opener": "...", "email_signature": "name, city, WhatsApp only if given"}},
 "social": {{"instagram": "bio max 150 chars", "linkedin": "...", "facebook": "...", "whatsapp_business": "max 139 chars", "hooks": ["5 caption hooks"]}},
 "voice": {{"tone": "one line", "dos": ["5"], "donts": ["5"], "use": ["words we use"], "avoid": ["words we avoid"]}}
}}""", mock, 3500)
