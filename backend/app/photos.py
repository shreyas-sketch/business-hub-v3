"""Photos on owners' websites: the owner's own uploads first, then stock photos from Pexels chosen from the AI's search
terms, else the website's designed artwork. Stock photos are hot-linked from Pexels (as their licence allows) with credit.

Two Pexels searches per website at most, cached for a week. A Pexels key allows 200 searches an hour by default, which a
full workshop room opening its new websites at once can use up: then searches pause until Pexels' hour resets, and websites
are left unmarked so they get their photos on a later visit instead of going a day without."""
import asyncio
import logging
import re
import time
from datetime import timedelta

import httpx

from .config import settings
from .db import db, now

log = logging.getLogger("photos")
PEXELS_URL = "https://api.pexels.com/v1/search"
GALLERY_MAX = 6
UPLOADS_MAX = 8
UPLOAD_BYTES = 3 * 1024 * 1024
_running: set[str] = set()
_paused = {"until": 0.0}   # time.time() when Pexels takes searches again, after it said "too many requests"


def image_type(data: bytes) -> str | None:
    """Trust the bytes, not the file name."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def queries_for(content: dict, business: dict) -> list[str]:
    """The AI's search terms, else a short version of what the business does."""
    qs = [q.strip()[:60] for q in (content.get("photo_queries") or []) if isinstance(q, str) and q.strip()]
    if not qs:
        what = re.sub(r"[^A-Za-z ]+", " ", business.get("industry", "") or "").split()
        words = [w for w in what if w.lower() not in {"and", "with", "our", "own", "for", "the", "in", "of", "we", "a"}][:4]
        offers = [o.get("name", "") for o in content.get("offers", []) if o.get("name")]
        qs = [" ".join(words) or (offers[0] if offers else "small business"), *(offers[:1])]
    return qs[:2]


async def _search(query: str, n: int) -> list[dict] | None:
    """The photos for a query; [] when Pexels found none; None when the search couldn't be made (worth trying again later)."""
    key = query.lower().strip()
    cached = await db().photo_cache.find_one({"_id": key})
    if cached and cached["at"] > now() - timedelta(days=7):
        return cached["photos"]
    if time.time() < _paused["until"]:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(PEXELS_URL, headers={"Authorization": settings.pexels_api_key},
                                 params={"query": query, "per_page": n, "orientation": "landscape"})
        if r.status_code == 429 or r.headers.get("x-ratelimit-remaining") == "0":
            try:
                reset = float(r.headers.get("x-ratelimit-reset", ""))
            except ValueError:
                reset = 0.0
            _paused["until"] = reset if time.time() < reset < time.time() + 3600 else time.time() + 600
            log.warning("Pexels hourly limit reached: stock photo searches paused for %d s", _paused["until"] - time.time())
        if r.status_code != 200:
            log.warning("Pexels search %r failed: %s %s", query, r.status_code, r.text[:200])
            return None
        photos = [{"kind": "stock", "src": p["src"].get("large2x") or p["src"].get("large"), "thumb": p["src"].get("large") or p["src"].get("medium"),
                   "alt": (p.get("alt") or query)[:160], "credit": (p.get("photographer") or "Pexels")[:80],
                   "credit_url": p.get("url") or "https://www.pexels.com", "id": f"pexels-{p.get('id')}"}
                  for p in r.json().get("photos", []) if (p.get("src") or {}).get("large")]
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        log.warning("Pexels search %r error: %s", query, type(e).__name__)
        return None
    await db().photo_cache.update_one({"_id": key}, {"$set": {"photos": photos, "at": now()}}, upsert=True)
    return photos


async def stock_for(content: dict, business: dict) -> dict:
    """{"hero", "gallery", "offers"} from at most two searches. Empty when no key or nothing was found; None when a search
    couldn't be made, so the website is tried again later."""
    if not settings.pexels_api_key:
        return {}
    qs = queries_for(content, business)
    results = await asyncio.gather(*[_search(q, 12 if i == 0 else 8) for i, q in enumerate(qs)])
    if any(batch is None for batch in results):
        return None
    pool, seen = [], set()
    for batch in results:
        for p in batch:
            if p["id"] not in seen:
                seen.add(p["id"])
                pool.append(p)
    if not pool:
        return {}
    offers = [o.get("name", "") for o in content.get("offers", []) if o.get("name")][:6]
    rest = pool[1:]
    return {"hero": pool[0], "gallery": rest[:GALLERY_MAX],
            "offers": {name: rest[(i + GALLERY_MAX) % len(rest)] if rest else pool[0] for i, name in enumerate(offers)}}


def merge(current: dict | None, stock: dict) -> dict:
    """New stock photos never replace what the owner uploaded."""
    cur = current or {}
    hero = cur.get("hero") if (cur.get("hero") or {}).get("kind") == "upload" else stock.get("hero")
    uploads = [p for p in cur.get("gallery", []) if p.get("kind") == "upload"]
    gallery = (uploads + [p for p in stock.get("gallery", []) if p is not hero])[:GALLERY_MAX]
    return {"hero": hero, "gallery": gallery, "offers": stock.get("offers", {}), "stock_checked_at": now()}


async def refresh_stock(site: dict, business: dict) -> dict | None:
    """The site's new photos; None when Pexels couldn't be asked just now (the site is left as it was, unmarked, so the next
    visit tries again)."""
    stock = await stock_for(site.get("content", {}), business)
    if stock is None:
        return None
    photos = merge(site.get("photos"), stock)
    await db().sites.update_one({"_id": site["_id"]}, {"$set": {"photos": photos}})
    return photos


def fill_later(site: dict, business: dict) -> None:
    """Websites made before photos existed (or before the key was set) get stock photos on their next visit."""
    checked = (site.get("photos") or {}).get("stock_checked_at")
    if not settings.pexels_api_key or site["_id"] in _running or (checked and checked > now() - timedelta(days=1)):
        return

    async def run():
        _running.add(site["_id"])
        try:
            await refresh_stock(site, business)
        except Exception:  # noqa: BLE001 — a photo search must never break a website
            log.exception("Stock photos for %s failed", site.get("slug"))
        finally:
            _running.discard(site["_id"])
    asyncio.get_running_loop().create_task(run())


def resolved(photos: dict | None, base: str) -> dict:
    """What the website template needs: every photo with a usable src."""
    def one(p):
        if not p:
            return None
        if p.get("kind") == "upload":
            return {**p, "src": f"{base}/photo/{p['id']}", "thumb": f"{base}/photo/{p['id']}"}
        return p
    ph = photos or {}
    return {"hero": one(ph.get("hero")), "gallery": [one(p) for p in ph.get("gallery", []) if p],
            "offers": {k: one(v) for k, v in (ph.get("offers") or {}).items()},
            "stock": any((p or {}).get("kind") == "stock" for p in [ph.get("hero"), *ph.get("gallery", []), *(ph.get("offers") or {}).values()])}
