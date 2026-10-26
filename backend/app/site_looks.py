"""The member website's design, put together from parts (see templates/site.html): a first screen (hero), a services layout,
a finish (how the sections below are styled) and a font pairing, plus the colour and a variant bit that swaps the picture side
and the pattern. Each kind of business has the parts that suit it; the AI picks among them when it writes the page, else the
web address does, so a room of clinics or coaches gets many different pages, not six. The six named looks are the original
bundles of parts, still there for an owner who wants one of them exactly."""
import hashlib
import re

from .ai.kit_tasks import business_type

LOOKS = {
    "signboard": ("Signboard", "Bold and direct: your colour as a shop sign, with a price board. Suits shops, dealers and traders."),
    "bazaar": ("Bazaar", "Warm and festive: a block-print pattern, a round stamp and a menu. Suits sweets, food, caterers and bakeries."),
    "care": ("Care", "Calm and reassuring: soft curves, a book-a-visit card and clear tiles. Suits clinics, dentists and wellness."),
    "works": ("Works", "Precise and solid: a blueprint grid, a spec table and a process flow. Suits manufacturing and engineering."),
    "atelier": ("Atelier", "Quiet and refined: an oversized headline, a large index and wide margins. Suits interiors, design and studios."),
    "launch": ("Launch", "Bold and energetic: chunky type, outlined cards, a moving ticker and a sticky booking bar. Suits coaches, "
               "trainers, fitness, brands, events, agencies and gaming."),
}
# the kinds of business the AI picks from when it writes the website (content.niche) → the look each gets automatically
NICHES = {
    "dental": "care", "clinic": "care", "wellness": "care", "salon_beauty": "atelier", "restaurant_cafe": "bazaar",
    "bakery_sweets": "bazaar", "catering": "bazaar", "interiors": "atelier", "architecture": "atelier", "real_estate": "atelier",
    "fashion_jewellery": "atelier", "photography_events": "launch", "wedding": "atelier", "coaching_education": "launch",
    "fitness_yoga": "launch", "d2c_brand": "launch", "agency_marketing": "launch", "travel": "launch", "it_software": "launch",
    "professional_services": "atelier", "manufacturing": "works", "engineering": "works", "auto_repair": "works",
    "construction": "works", "gaming": "launch", "retail_shop": "signboard", "trading_wholesale": "signboard", "home_services": "signboard",
    "other": "",
}
# colours that suit each kind of business, for pages without a brand colour of their own: each page takes one of them,
# picked from its web address — random across a room of businesses, the same every time the page opens
NICHE_COLOURS = {
    "dental": ("#0E7C86", "#2563EB", "#0F766E", "#1D4ED8", "#0369A1"),
    "clinic": ("#0F766E", "#1D4ED8", "#0E7490", "#047857", "#4338CA"),
    "wellness": ("#3F7D58", "#7C5CBF", "#0F766E", "#A16207", "#9D4E6E"),
    "salon_beauty": ("#B83280", "#8B5E3C", "#9D174D", "#7C3AED", "#BE185D"),
    "restaurant_cafe": ("#C2410C", "#9A3412", "#B91C1C", "#15803D", "#A16207"),
    "bakery_sweets": ("#B45309", "#BE185D", "#C2410C", "#9D174D", "#A16207"),
    "catering": ("#A16207", "#B91C1C", "#C2410C", "#15803D", "#9A3412"),
    "interiors": ("#7C5A3A", "#3F4B5B", "#0F766E", "#7C2D12", "#4B5563"),
    "architecture": ("#334155", "#7C2D12", "#1F2937", "#0F766E", "#57534E"),
    "real_estate": ("#14532D", "#1E3A8A", "#0F766E", "#7C2D12", "#334155"),
    "fashion_jewellery": ("#7C2D5C", "#8A6A1F", "#9D174D", "#1F2937", "#7C3AED"),
    "photography_events": ("#6D28D9", "#DB2777", "#0F766E", "#C2410C", "#1D4ED8"),
    "wedding": ("#9D174D", "#A16207", "#BE185D", "#7C2D12", "#6D28D9"),
    "coaching_education": ("#2B3BEA", "#E4572E", "#7C3AED", "#0F766E", "#DB2777"),
    "fitness_yoga": ("#E4572E", "#16A34A", "#DC2626", "#2B3BEA", "#7C3AED"),
    "d2c_brand": ("#2B3BEA", "#DB2777", "#E4572E", "#7C3AED", "#0F766E"),
    "agency_marketing": ("#2B3BEA", "#7C3AED", "#DB2777", "#E4572E", "#0F172A"),
    "travel": ("#0369A1", "#EA580C", "#0F766E", "#2B3BEA", "#B45309"),
    "it_software": ("#4338CA", "#0F766E", "#2B3BEA", "#7C3AED", "#0369A1"),
    "professional_services": ("#1E3A8A", "#334155", "#14532D", "#7C2D12", "#0F766E"),
    "manufacturing": ("#B45309", "#1F4E79", "#C2410C", "#334155", "#B91C1C"),
    "engineering": ("#C2410C", "#0F766E", "#1F4E79", "#B45309", "#374151"),
    "auto_repair": ("#B91C1C", "#1F2937", "#C2410C", "#1D4ED8", "#B45309"),
    "construction": ("#D97706", "#374151", "#C2410C", "#1F4E79", "#B45309"),
    "gaming": ("#7C3AED", "#DB2777", "#0891B2", "#16A34A", "#E11D48"),
    "retail_shop": ("#B91C1C", "#047857", "#C2410C", "#2B3BEA", "#7C3AED"),
    "trading_wholesale": ("#1F4E79", "#9A3412", "#047857", "#B45309", "#334155"),
    "home_services": ("#0F766E", "#C2410C", "#2B3BEA", "#15803D", "#B91C1C"),
    "other": ("#2B3BEA", "#0F766E", "#C2410C", "#7C3AED", "#B91C1C", "#047857", "#B45309", "#DB2777"),
}
# ── the parts ──
HEROES = {"signboard": "the brand colour as a shop sign, with a price board",
          "bazaar": "a block-print pattern, a round stamp and an arched picture",
          "care": "soft curves and a book-a-visit card",
          "works": "a dark blueprint grid with a spec panel",
          "atelier": "an oversized headline on paper, wide margins",
          "launch": "chunky type, a framed picture, a moving ticker"}
SERVICES = {"board": "a price board, one row each", "menu": "a menu card with dotted price lines",
            "tiles": "soft tiles with an icon each", "spec": "a spec table with item codes",
            "index": "a large-type index", "cards": "outlined cards with offset shadows"}
FINISHES = tuple(LOOKS)            # the sections below the first screen, styled as each look styles them
# font pairings, each with the finishes whose heading weights it carries (a 400-only serif can't do Launch's 800)
FONT_PAIRS = {
    "grotesk": (("Bricolage Grotesque", "600;700;800"), ("Mukta", "400;500;600")),
    "archivo": (("Archivo", "600;800"), ("Inter", "400;500;600")),
    "space": (("Space Grotesk", "600;700"), ("Inter", "400;500;600;700")),
    "rozha": (("Rozha One", "400"), ("Mukta", "400;500;600")),
    "yeseva": (("Yeseva One", "400"), ("Hind", "400;500;600")),
    "fraunces": (("Fraunces", "400;600"), ("Inter", "300;400;500;600")),
    "nunito": (("Nunito", "600;700;800"), ("Mukta", "400;500;600")),
    "jakarta": (("Plus Jakarta Sans", "600;700;800"), ("Inter", "400;500;600")),
    "barlow": (("Barlow Condensed", "600;700"), ("Barlow", "400;500;600")),
    "oswald": (("Oswald", "500;600;700"), ("Roboto", "400;500")),
    "serif": (("Instrument Serif", "400"), ("Mukta", "300;400;500;600")),
    "bricolage": (("Bricolage Grotesque", "600;700;800"), ("Instrument Sans", "400;500;600;700")),
    "dm": (("DM Serif Display", "400"), ("DM Sans", "400;500;600")),
    "sora": (("Sora", "600;700;800"), ("Inter", "400;500;600")),
}
FINISH_FONTS = {"signboard": ("grotesk", "archivo", "space", "sora"), "bazaar": ("rozha", "yeseva", "fraunces", "dm"),
                "care": ("nunito", "jakarta", "sora"), "works": ("barlow", "oswald", "space"),
                "atelier": ("serif", "fraunces", "dm"), "launch": ("bricolage", "space", "sora", "jakarta")}
# each named look as its bundle of parts (hero, services, finish, first font pairing)
LOOK_PARTS = {"signboard": ("signboard", "board", "signboard", "grotesk"), "bazaar": ("bazaar", "menu", "bazaar", "rozha"),
              "care": ("care", "tiles", "care", "nunito"), "works": ("works", "spec", "works", "barlow"),
              "atelier": ("atelier", "index", "atelier", "serif"), "launch": ("launch", "cards", "launch", "bricolage")}
# the parts that suit each kind of business, by the look its niche maps to; a niche can narrow or widen its own
FAMILY_PARTS = {
    "care": {"hero": ("care", "atelier", "signboard"), "services": ("tiles", "index", "board"), "finish": ("care", "atelier")},
    "bazaar": {"hero": ("bazaar", "signboard", "launch"), "services": ("menu", "cards", "board"), "finish": ("bazaar", "signboard")},
    "works": {"hero": ("works", "signboard", "atelier"), "services": ("spec", "board", "index"), "finish": ("works", "signboard")},
    "atelier": {"hero": ("atelier", "care", "signboard"), "services": ("index", "tiles", "board"), "finish": ("atelier", "care")},
    "signboard": {"hero": ("signboard", "launch", "bazaar"), "services": ("board", "cards", "tiles", "menu"),
                  "finish": ("signboard", "launch", "bazaar")},
    "launch": {"hero": ("launch", "signboard", "atelier"), "services": ("cards", "tiles", "index", "board"),
               "finish": ("launch", "signboard", "atelier")},
}
NICHE_PARTS = {
    "gaming": {"hero": ("launch", "works"), "services": ("cards", "spec"), "finish": ("launch", "works")},
    "it_software": {"hero": ("launch", "works", "atelier"), "services": ("cards", "spec", "index"), "finish": ("launch", "works", "atelier")},
    "wellness": {"hero": ("care", "atelier", "bazaar"), "services": ("tiles", "index", "menu"), "finish": ("care", "atelier", "bazaar")},
    "fitness_yoga": {"hero": ("launch", "care", "signboard"), "services": ("cards", "tiles", "board"), "finish": ("launch", "care")},
    "auto_repair": {"hero": ("works", "signboard", "launch"), "services": ("board", "spec", "cards"), "finish": ("works", "signboard")},
}
AXES = ("hero", "services", "finish", "fonts")

# the Launch look's highlight colour, one of these per page (each reads with dark text)
POP_COLOURS = ("#FFC21A", "#FF8FA3", "#5EE6B0", "#FFB347", "#B9E769", "#7FD6FF")

# whole words or word starts only ("art" must not match "spare parts")
STUDIO = re.compile(r"\b(interior|design|architect|decor|furnish|furniture|salon|boutique|studio|photograph|fashion|wedding|bridal|"
                    r"jewel|art|artist|makeover|spa|consult|chartered|modular|wardrobe)", re.I)
WORKS = re.compile(r"\b(engineering|industrial|pump|machine|machining|fabricat|steel|foundry|tool|spare|valve|automation|"
                   r"packaging|plastic|moulding|casting|forging|chemical)", re.I)


def auto_look(business: dict, niche: str = "", slug: str = "") -> str:
    """The AI's niche when it named one; else the niche the words of what the business does point to; else the business's
    type. A service business nothing places alternates between Signboard and Launch (by its web address), so a room of coaches
    and agencies doesn't all match."""
    b = business or {}
    niche = niche if NICHES.get(niche) else guessed_niche(b)
    if NICHES.get(niche):
        return NICHES[niche]
    kind = business_type(b)
    text = f"{b.get('industry', '')} {b.get('name', '')}"
    if kind == "food":
        return "bazaar"
    if kind == "clinic":
        return "care"
    if kind == "manufacturing" or WORKS.search(text):
        return "works"
    if kind == "trading":
        return "signboard"
    if STUDIO.search(text):
        return "atelier"
    if re.search(r"\b(coach|training|trainer|academy|classes|tuition|course|fitness|gym|yoga|event|agency|marketing|brand|travel|tour|software|gam)",
                 text, re.I):
        return "launch"
    return "launch" if _bucket(slug, 2) else "signboard"


def _bucket(slug: str, n: int) -> int:
    return int(hashlib.sha1((slug or "").encode()).hexdigest(), 16) % n


def look_of(site: dict, business: dict) -> tuple[str, int]:
    """The look the page's kind of business maps to (or the owner's own pick), and the variant 0-3: bit 0 swaps the picture
    side and the pattern. The look sets the page's wording ("Our menu", "Book a visit"); the parts are design_of's."""
    chosen = site.get("look")
    look = chosen if chosen in LOOKS else auto_look(business, niche_of(site), site.get("slug") or "")
    return look, _bucket(site.get("slug") or "", 4)


def parts_for(look: str, niche: str = "") -> dict:
    """The parts that suit a kind of business: its niche's own, else its look's family, plus the fonts its finishes carry."""
    allowed = dict(NICHE_PARTS.get(niche) or FAMILY_PARTS.get(look) or FAMILY_PARTS["signboard"])
    return {**allowed, "fonts": tuple(dict.fromkeys(f for fin in allowed["finish"] for f in FINISH_FONTS[fin]))}


def design_of(site: dict, business: dict) -> dict:
    """The page's parts. An owner's chosen look is its original bundle. Otherwise each part is the AI's pick when it suits the
    business (content.design), else one picked from the web address among those that suit it, so pages of one kind vary."""
    voice, variant = look_of(site, business)
    slug = site.get("slug") or ""
    if site.get("look") in LOOKS:
        hero, services, finish, fonts = LOOK_PARTS[voice]
        return {"voice": voice, "variant": variant, "hero": hero, "services": services, "finish": finish, "fonts": fonts}
    allowed = parts_for(voice, niche_for(site, business))
    ai = ((site.get("content") or {}).get("design") or {})
    out = {"voice": voice, "variant": variant}
    for axis in ("hero", "services", "finish"):
        opts = allowed[axis]
        out[axis] = ai.get(axis) if ai.get(axis) in opts else opts[_bucket(f"{slug}:{axis}", len(opts))]
    fonts = FINISH_FONTS[out["finish"]]           # the pairing must carry the finish's heading weights
    out["fonts"] = ai.get("fonts") if ai.get("fonts") in fonts else fonts[_bucket(f"{slug}:fonts", len(fonts))]
    return out


def design_choices(raw) -> dict:
    """The AI's design picks, kept only where each names a real part (whether it suits the business is design_of's call)."""
    valid = {"hero": HEROES, "services": SERVICES, "finish": FINISHES, "fonts": FONT_PAIRS}
    raw = raw if isinstance(raw, dict) else {}
    picks = {k: str(raw.get(k) or "").strip().lower() for k in AXES}
    return {k: v for k, v in picks.items() if v in valid[k]}


def niche_for(site: dict, business: dict) -> str:
    return niche_of(site) or guessed_niche(business or {})


def design_menu() -> str:
    """The parts, for the AI's prompt: it picks the ones that fit this business."""
    def line(d):
        return "; ".join(f"{k} ({v})" for k, v in d.items())
    return (f'"hero": one of {line(HEROES)}\n "services": one of {line(SERVICES)}\n'
            f' "finish": how the sections below look, one of {", ".join(FINISHES)}\n "fonts": one of {", ".join(FONT_PAIRS)}')


def niche_of(site: dict) -> str:
    n = ((site or {}).get("content") or {}).get("niche") or ""
    return n if n in NICHES else ""


def guessed_niche(business: dict) -> str:
    """The niche from the words of what the business does, for pages the AI gave none (and pages made before niches)."""
    from .ai.tasks import guess_niche
    return guess_niche(business or {}) if business else ""


def niche_colour(site: dict, variant: int = 0, business: dict | None = None) -> str:
    """The colour for a page without a brand colour of its own: one of its kind of business's colours, picked from its web
    address (random across businesses, stable for each); a page nothing places picks from a general set."""
    niche = niche_for(site, business or {})
    palette = NICHE_COLOURS.get(niche if niche in NICHES else "other") or NICHE_COLOURS["other"]
    return palette[_bucket((site.get("slug") or "") + ":colour", len(palette))]


def pop_colour(site: dict) -> str:
    return POP_COLOURS[_bucket((site.get("slug") or "") + ":pop", len(POP_COLOURS))]


def options(business: dict, site: dict | None = None) -> dict:
    return {"auto": auto_look(business, niche_of(site or {}), (site or {}).get("slug") or ""),
            "looks": [{"key": k, "name": n, "about": a} for k, (n, a) in LOOKS.items()]}
