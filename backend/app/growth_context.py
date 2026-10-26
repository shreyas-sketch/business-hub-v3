"""
What the hub knows about one business, gathered for the v2.3 Sales and Brand writers (Revenue Multiplier ideas, brochures,
social media posts): the Knowledge Hub, their website on the hub, their lead magnet, the brand kit and the competitor
summary. Only the owner's own data and the competitor's public site (for positioning) are used.

Safety (the same rules as Competitor Watch):
- Words from a competitor's site reach the AI only as quoted data, with web addresses, emails and phone numbers taken out.
- A written line that repeats 6 or more words in a row from the competitor's site is dropped: we never copy their text.
- A line with a rupee amount the owner didn't give, a web address, an email or a phone number (other than the owner's
  own) is dropped.
"""
import re

from .db import db

RUPEE = re.compile(r"(₹\s?[\d,]+(\.\d+)?|\bRs\.?\s?[\d,]+|\bINR\s?[\d,]+|\b\d[\d,.]*\s?(lakh|lakhs|lac|crore|crores|cr)\b)", re.I)
WORD = re.compile(r"[a-z0-9ऀ-ॿ]+", re.I)


def flatten(node, limit: int = 3000) -> str:
    """Every piece of text in a nested dict/list (a website's content, a guide), one per line."""
    out: list[str] = []

    def walk(x):
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, str) and x.strip():
            out.append(x.strip())
    walk(node)
    return "\n".join(out)[:limit]


def shingles(text: str, n: int = 6) -> set[tuple]:
    words = [w.lower() for w in WORD.findall(text or "")]
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def copies(text: str, source: set[tuple], n: int = 6) -> bool:
    """True when `text` repeats n or more words in a row from the competitor's words."""
    return bool(source) and bool(shingles(text, n) & source)


def owner_amounts(p: dict) -> set[str]:
    """The rupee amounts the owner wrote (their price list), digits only, so they may appear where they belong."""
    out = set()
    for o in p.get("offers") or []:
        for m in RUPEE.finditer(str(o.get("price") or "")):
            out.add(re.sub(r"\D", "", m.group(0)))
    return {x for x in out if x}


def has_foreign_amount(text: str, allowed: set[str]) -> bool:
    return any(re.sub(r"\D", "", m.group(0)) not in allowed or not re.sub(r"\D", "", m.group(0)) for m in RUPEE.finditer(text or ""))


async def competitor(ws: str) -> dict | None:
    """The competitor summary Competitor Watch keeps (v2.3). Read defensively: older deployments don't have it."""
    try:
        from . import competitor_watch
        fn = getattr(competitor_watch, "competitor_summary", None)
        return await fn(ws) if fn else None
    except Exception:  # noqa: BLE001  never let the competitor side stop the owner's own writing
        return None


def competitor_text(summary: dict | None, limit: int = 2500) -> str:
    """The competitor summary as plain quoted lines, with web addresses, emails and phone numbers taken out."""
    if not summary:
        return ""
    from .competitor_watch import redact
    lines = []
    for k, v in summary.items():
        if k.startswith("_") or k in ("owner_id", "ws", "url", "urls", "at", "updated_at", "created_at", "refreshed_at", "id"):
            continue
        if isinstance(v, (list, tuple)):
            items = [flatten(x, 300) if isinstance(x, (dict, list)) else str(x) for x in v if x]
            if items:
                lines.append(f"{k.replace('_', ' ')}: " + "; ".join(i.replace("\n", " ") for i in items))
        elif isinstance(v, dict):
            t = flatten(v, 600).replace("\n", "; ")
            if t:
                lines.append(f"{k.replace('_', ' ')}: {t}")
        elif isinstance(v, str) and v.strip():
            lines.append(f"{k.replace('_', ' ')}: {v.strip()}")
    return redact("\n".join(lines))[:limit]


async def gather(ws: str) -> dict:
    """Everything the writers may use, in one place."""
    d = db()
    p = await d.businesses.find_one({"owner_id": ws}) or {}
    site = await d.sites.find_one({"owner_id": ws}, {"content": 1, "status": 1, "slug": 1}) or {}
    guide = await d.lead_magnets.find_one({"_id": ws}, {"content": 1, "published": 1}) or {}
    g = guide.get("content") or {}
    kit = await d.brand_kits.find_one({"_id": ws}, {"messages": 1}) or {}
    levers = await d.levers.find_one({"_id": ws}) or {}
    comp = await competitor(ws)
    comp_text = competitor_text(comp)
    return {
        "profile": p,
        "site_text": flatten(site.get("content") or {}, 2500),
        "site_live": site.get("status") == "live",
        "magnet": {"title": g.get("title", ""), "subtitle": g.get("subtitle", ""), "promise": g.get("promise", ""),
                   "published": bool(guide.get("published"))} if g.get("title") else None,
        "messages": kit.get("messages") or {},
        "levers": {k: levers.get(k) for k in ("customers", "avg_value", "frequency")} if levers else None,
        "competitor_text": comp_text,
        "competitor_shingles": shingles(comp_text),
    }


def sources_text(ctx: dict) -> str:
    """The prompt section with every source, each clearly labelled so the AI can name where an idea comes from."""
    parts = []
    if ctx["site_text"]:
        parts.append(f"THEIR WEBSITE (on the hub):\n{ctx['site_text'][:2000]}")
    else:
        parts.append("THEIR WEBSITE: none yet.")
    m = ctx.get("magnet")
    parts.append(f"THEIR LEAD MAGNET (free guide): {m['title']} — {m['subtitle'] or m['promise']}" if m else "THEIR LEAD MAGNET: none yet.")
    msgs = ctx.get("messages") or {}
    core = msgs.get("core") or {}
    if core.get("one_line"):
        parts.append(f"THEIR BRAND MESSAGE: {core.get('one_line')}\n{core.get('pitch', '')}")
    if ctx["competitor_text"]:
        from .competitor_watch import QUOTED
        parts.append(f"{QUOTED}\n<<<COMPETITOR DATA>>>\n{ctx['competitor_text']}\n<<<END>>>")
    else:
        parts.append("COMPETITOR: no competitor summary yet.")
    return "\n\n".join(parts)
