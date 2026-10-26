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
    "retail_shop": ("#B91C1C", "#047857", "#C2410C", "#2B3BEA", "#7C3AED"),
    "trading_wholesale": ("#1F4E79", "#9A3412", "#047857", "#B45309", "#334155"),
    "home_services": ("#0F766E", "#C2410C", "#2B3BEA", "#15803D", "#B91C1C"),
    "other": ("#2B3BEA", "#0F766E", "#C2410C", "#7C3AED", "#B91C1C", "#047857", "#B45309", "#DB2777"),
}
# the Launch look's highlight colour, one of these per page (each reads with dark text)
POP_COLOURS = ("#FFC21A", "#FF8FA3", "#5EE6B0", "#FFB347", "#B9E769", "#7FD6FF")

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


def niche_colour(site: dict, variant: int = 0) -> str:
    """The colour for a page without a brand colour of its own: one of its kind of business's colours, picked from its web
    address (random across businesses, stable for each); a page the AI couldn't place picks from a general set."""
    palette = NICHE_COLOURS.get(niche_of(site) or "other") or NICHE_COLOURS["other"]
    return palette[_bucket((site.get("slug") or "") + ":colour", len(palette))]


def pop_colour(site: dict) -> str:
    return POP_COLOURS[_bucket((site.get("slug") or "") + ":pop", len(POP_COLOURS))]


def options(business: dict, site: dict | None = None) -> dict:
    return {"auto": auto_look(business, niche_of(site or {}), (site or {}).get("slug") or ""),
            "looks": [{"key": k, "name": n, "about": a} for k, (n, a) in LOOKS.items()]}
