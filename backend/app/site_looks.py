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
}
# whole words or word starts only ("art" must not match "spare parts")
STUDIO = re.compile(r"\b(interior|design|architect|decor|furnish|furniture|salon|boutique|studio|photograph|fashion|wedding|bridal|"
                    r"jewel|art|artist|makeover|spa|consult|chartered|modular|wardrobe)", re.I)
WORKS = re.compile(r"\b(engineering|industrial|pump|machine|machining|fabricat|steel|foundry|tool|spare|valve|automation|"
                   r"packaging|plastic|moulding|casting|forging|chemical)", re.I)


def auto_look(business: dict) -> str:
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
    return "signboard"


def look_of(site: dict, business: dict) -> tuple[str, int]:
    chosen = site.get("look")
    look = chosen if chosen in LOOKS else auto_look(business)
    variant = int(hashlib.sha1((site.get("slug") or "").encode()).hexdigest(), 16) % 2
    return look, variant


def options(business: dict) -> dict:
    return {"auto": auto_look(business), "looks": [{"key": k, "name": n, "about": a} for k, (n, a) in LOOKS.items()]}
