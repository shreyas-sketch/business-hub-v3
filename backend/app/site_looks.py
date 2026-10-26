"""The member website's design, put together from parts (see templates/site.html): a first screen (hero), a services layout,
a finish (how the sections below are styled) and a font pairing, plus the colour and a variant bit that swaps the picture side
and the pattern. Every niche has its own design (NICHE_DESIGN): no two kinds of business get the same page. Within a niche the
fonts, picture side, pattern and colour come from the web address, so two clinics differ too. Nothing here is picked by the
owner or by the AI beyond the niche: the page is put together for them."""
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
    "design_studio": "atelier", "professional_services": "atelier", "manufacturing": "works", "engineering": "works", "auto_repair": "works",
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
    "design_studio": ("#111827", "#DB2777", "#7C3AED", "#0F766E", "#C2410C"),
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
# every niche's own design: its kind's first screen (NICHES), and a services layout and finish no other niche has, so two
# kinds of business never get the same page. Businesses of one niche still differ by fonts, picture side, pattern and colour.
NICHE_DESIGN = {
    # care: clinics and wellness
    "dental": ("tiles", "care"), "clinic": ("board", "care"), "wellness": ("menu", "atelier"),
    # bazaar: food
    "restaurant_cafe": ("menu", "bazaar"), "bakery_sweets": ("cards", "bazaar"), "catering": ("menu", "signboard"),
    # atelier: studios, design and advice
    "interiors": ("index", "atelier"), "architecture": ("index", "works"), "design_studio": ("cards", "atelier"),
    "fashion_jewellery": ("tiles", "atelier"), "salon_beauty": ("menu", "care"), "wedding": ("tiles", "bazaar"),
    "real_estate": ("board", "signboard"), "professional_services": ("index", "signboard"),
    # launch: coaching, fitness, brands, agencies, events, travel, software, gaming
    "coaching_education": ("cards", "launch"), "fitness_yoga": ("tiles", "launch"), "it_software": ("spec", "launch"),
    "agency_marketing": ("index", "launch"), "travel": ("board", "launch"), "gaming": ("cards", "works"),
    "d2c_brand": ("cards", "signboard"), "photography_events": ("index", "atelier"),
    # works: manufacturing, engineering, repair and building
    "manufacturing": ("spec", "works"), "engineering": ("board", "works"), "construction": ("spec", "signboard"),
    "auto_repair": ("board", "signboard"),
    # signboard: shops, traders and home services
    "retail_shop": ("board", "signboard"), "trading_wholesale": ("spec", "signboard"), "home_services": ("tiles", "signboard"),
    # a business nothing places
    "other": ("index", "launch"),
}
OTHER_LOOK = "signboard"     # the first screen and wording of a business nothing places
# and every niche's own take on its look's first screen: a tone (light, the brand colour, dark) and a layout (picture right,
# picture left, centred), none shared by two niches of one look, so even a dentist and a clinic open differently
NICHE_FIRST = {
    "dental": ("light", "right"), "clinic": ("light", "left"), "wellness": ("light", "centre"),
    "restaurant_cafe": ("light", "right"), "bakery_sweets": ("light", "left"), "catering": ("light", "centre"),
    "interiors": ("light", "right"), "architecture": ("dark", "left"), "design_studio": ("dark", "right"),
    "fashion_jewellery": ("light", "centre"), "salon_beauty": ("colour", "centre"), "wedding": ("colour", "right"),
    "real_estate": ("light", "left"), "professional_services": ("dark", "centre"),
    "coaching_education": ("colour", "right"), "fitness_yoga": ("dark", "left"), "it_software": ("dark", "right"),
    "agency_marketing": ("colour", "left"), "travel": ("light", "right"), "gaming": ("dark", "centre"),
    "d2c_brand": ("colour", "centre"), "photography_events": ("light", "left"),
    "manufacturing": ("dark", "right"), "engineering": ("light", "left"), "construction": ("colour", "right"),
    "auto_repair": ("dark", "left"),
    "retail_shop": ("colour", "right"), "trading_wholesale": ("light", "left"), "home_services": ("colour", "left"),
    "other": ("dark", "centre"),
}

# the Launch look's highlight colour, one of these per page (each reads with dark text)
POP_COLOURS = ("#FFC21A", "#FF8FA3", "#5EE6B0", "#FFB347", "#B9E769", "#7FD6FF")

# for a business the niche words don't place: whole words or word starts only ("art" must not match "spare parts")
STUDIO = re.compile(r"\b(interior|design|architect|decor|furnish|furniture|salon|boutique|studio|photograph|fashion|wedding|bridal|"
                    r"jewel|art|artist|makeover|spa|consult|chartered|modular|wardrobe)", re.I)
WORKS = re.compile(r"\b(engineering|industrial|pump|machine|machining|fabricat|steel|foundry|tool|spare|valve|automation|"
                   r"packaging|plastic|moulding|casting|forging|chemical)", re.I)
LAUNCH = re.compile(r"\b(coach|training|trainer|academy|classes|tuition|course|fitness|gym|yoga|event|agency|marketing|travel|tour|"
                    r"software|gam)", re.I)


def niche_for(site: dict, business: dict) -> str:
    """The page's niche: the AI's, else the one the words of what the business does point to, else the closest one its type
    points to; "other" when nothing places it."""
    niche = niche_of(site)
    if niche in ("", "other"):         # the AI couldn't place it: the words still might
        niche = guessed_niche(business or {})
    if niche in NICHES and niche != "other":
        return niche
    b = business or {}
    text = f"{b.get('industry', '')} {b.get('name', '')}"
    kind = business_type(b)
    for hit, closest in ((kind == "food", "restaurant_cafe"), (kind == "clinic", "clinic"),
                         (kind == "manufacturing" or WORKS.search(text), "manufacturing"), (kind == "trading", "trading_wholesale"),
                         (STUDIO.search(text), "design_studio"), (LAUNCH.search(text), "coaching_education")):
        if hit:
            return closest
    return "other"


def auto_look(business: dict, niche: str = "") -> str:
    """The look a business gets: its niche's (the AI's when it named one)."""
    niche = niche if NICHES.get(niche) else niche_for({}, business)
    return NICHES.get(niche) or OTHER_LOOK


def _bucket(slug: str, n: int) -> int:
    return int(hashlib.sha1((slug or "").encode()).hexdigest(), 16) % n


def look_of(site: dict, business: dict) -> tuple[str, int]:
    """The look the page's niche maps to (its first screen and its wording: "Our menu", "Book a visit"), and a variant 0-3 from
    the web address."""
    return auto_look(business, niche_of(site)), _bucket(site.get("slug") or "", 4)


def design_of(site: dict, business: dict) -> dict:
    """The page's parts: its niche's own first screen (its look's hero in the niche's tone and layout, NICHE_FIRST), services
    layout and finish (NICHE_DESIGN), and a font pairing that finish carries, picked from the web address (as the colour is),
    so two businesses of one niche differ too and each page stays the same."""
    niche = niche_for(site, business)
    voice, variant = look_of(site, business)
    services, finish = NICHE_DESIGN[niche]
    tone, layout = NICHE_FIRST[niche]
    fonts = FINISH_FONTS[finish]
    return {"niche": niche, "voice": voice, "variant": variant, "hero": voice, "tone": tone, "layout": layout,
            "services": services, "finish": finish,
            "fonts": fonts[_bucket((site.get("slug") or "") + ":fonts", len(fonts))]}


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
