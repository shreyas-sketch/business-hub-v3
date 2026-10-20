"""The owner's brand colours, read by everything that draws in them (website, launch kit, invoices, designs)."""
from .db import db

DEFAULT_ACCENT = "#1F4E79"


async def tokens_for(ws: str) -> dict:
    kit = await db().brand_kits.find_one({"_id": ws}, {"design": 1}) or {}
    return (kit.get("design") or {}).get("colors") or {}


async def accent_for(ws: str) -> str:
    """The brand kit's primary colour when there is one, else the website's accent."""
    colors = await tokens_for(ws)
    if colors.get("primary"):
        return colors["primary"]
    site = await db().sites.find_one({"owner_id": ws}, {"accent": 1}) or {}
    return site.get("accent") or DEFAULT_ACCENT
