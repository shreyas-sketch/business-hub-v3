"""The member website's looks. Each is its own layout, texture and set of section treatments (see templates/site.html), so a
sweet shop, a clinic and an engineering works don't get the same page in different colours. The look is picked from the kind of
business unless the owner chose one; a variant (0 or 1, from the web address) changes the picture side and the pattern, so two
neighbours in the same trade rarely get the same page."""
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
               "trainers, fitness, brands, events and agencies."),
}
# the kinds of business the AI picks from when it writes the website (content.niche) → the look each gets automatically
NICHES = {
    "dental": "care", "clinic": "care", "wellness": "care", "salon_beauty": "atelier", "restaurant_cafe": "bazaar",
    "bakery_sweets": "bazaar", "catering": "bazaar", "interiors": "atelier", "architecture": "atelier", "real_estate": "atelier",
    "fashion_jewellery": "atelier", "photography_events": "launch", "wedding": "atelier", "coaching_education": "launch",
    "fitness_yoga": "launch", "d2c_brand": "launch", "agency_marketing": "launch", "travel": "launch", "it_software": "launch",
    "professional_services": "atelier", "manufacturing": "works", "engineering": "works", "auto_repair": "works",
    "construction": "works", "retail_shop": "signboard", "trading_wholesale": "signboard", "home_services": "signboard",
    "other": "",
}
# a colour per kind of business for pages that have no brand colour of their own (two each: neighbours differ)
NICHE_COLOURS = {
    "dental": ("#0E7C86", "#2563EB"), "clinic": ("#0F766E", "#1D4ED8"), "wellness": ("#3F7D58", "#7C5CBF"),
    "salon_beauty": ("#B83280", "#8B5E3C"), "restaurant_cafe": ("#C2410C", "#9A3412"), "bakery_sweets": ("#B45309", "#BE185D"),
    "catering": ("#A16207", "#B91C1C"), "interiors": ("#7C5A3A", "#3F4B5B"), "architecture": ("#334155", "#7C2D12"),
    "real_estate": ("#14532D", "#1E3A8A"), "fashion_jewellery": ("#7C2D5C", "#8A6A1F"), "photography_events": ("#6D28D9", "#DB2777"),
    "wedding": ("#9D174D", "#A16207"), "coaching_education": ("#2B3BEA", "#E4572E"), "fitness_yoga": ("#E4572E", "#16A34A"),
    "d2c_brand": ("#2B3BEA", "#DB2777"), "agency_marketing": ("#2B3BEA", "#7C3AED"), "travel": ("#0369A1", "#EA580C"),
    "it_software": ("#4338CA", "#0F766E"), "professional_services": ("#1E3A8A", "#334155"), "manufacturing": ("#B45309", "#1F4E79"),
    "engineering": ("#C2410C", "#0F766E"), "auto_repair": ("#B91C1C", "#1F2937"), "construction": ("#D97706", "#374151"),
    "retail_shop": ("#B91C1C", "#047857"), "trading_wholesale": ("#1F4E79", "#9A3412"), "home_services": ("#0F766E", "#C2410C"),
}

# whole words or word starts only ("art" must not match "spare parts")
STUDIO = re.compile(r"\b(interior|design|architect|decor|furnish|furniture|salon|boutique|studio|photograph|fashion|wedding|bridal|"
                    r"jewel|art|artist|makeover|spa|consult|chartered|modular|wardrobe)", re.I)
WORKS = re.compile(r"\b(engineering|industrial|pump|machine|machining|fabricat|steel|foundry|tool|spare|valve|automation|"
                   r"packaging|plastic|moulding|casting|forging|chemical)", re.I)


def auto_look(business: dict, niche: str = "", slug: str = "") -> str:
    """The AI's niche when it named one; else from the words of what the business does. A service business the words don't
    place alternates between Signboard and Launch (by its web address), so a room of coaches and agencies doesn't all match."""
    if NICHES.get(niche):
        return NICHES[niche]
    b = business or {}
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
    if re.search(r"\b(coach|training|trainer|academy|classes|tuition|course|fitness|gym|yoga|event|agency|marketing|brand|travel|tour|software)", text, re.I):
        return "launch"
    return "launch" if _bucket(slug, 2) else "signboard"


def _bucket(slug: str, n: int) -> int:
    return int(hashlib.sha1((slug or "").encode()).hexdigest(), 16) % n


def look_of(site: dict, business: dict) -> tuple[str, int]:
    """The look and its variant 0-3: bit 0 swaps the picture side and the pattern, bit 1 the font pairing and the colour."""
    chosen = site.get("look")
    look = chosen if chosen in LOOKS else auto_look(business, niche_of(site), site.get("slug") or "")
    return look, _bucket(site.get("slug") or "", 4)


def niche_of(site: dict) -> str:
    n = ((site or {}).get("content") or {}).get("niche") or ""
    return n if n in NICHES else ""


def niche_colour(site: dict, variant: int) -> str | None:
    """The colour for a page without a brand colour of its own: its kind of business's, so neighbours don't all get the default."""
    pair = NICHE_COLOURS.get(niche_of(site))
    return pair[(variant >> 1) & 1] if pair else None


def options(business: dict, site: dict | None = None) -> dict:
    return {"auto": auto_look(business, niche_of(site or {}), (site or {}).get("slug") or ""),
            "looks": [{"key": k, "name": n, "about": a} for k, (n, a) in LOOKS.items()]}
