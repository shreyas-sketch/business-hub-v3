"""The Dashboard's 60-second cache per workspace, and the writes that clear it at once (a new lead, a payment, a post)."""
import time

_cache: dict[str, tuple[float, dict]] = {}


def get(ws: str, ttl: int) -> dict | None:
    hit = _cache.get(ws)
    return hit[1] if hit and time.monotonic() - hit[0] < ttl else None


def put(ws: str, value: dict) -> None:
    if len(_cache) > 5000:
        _cache.clear()
    _cache[ws] = (time.monotonic(), value)


def bust(ws: str) -> None:
    _cache.pop(ws, None)
