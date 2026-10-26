"""
Reading a business's Employz.ai sub-account (Employz.ai is our white-label HighLevel: the LeadConnector API v2), read-only.

Every request here is a GET. Nothing in this module creates, changes or deletes anything in Employz.ai (a test fails if a
non-GET call appears). The hub's older one-way push of new leads (integrations.employz_sync_lead) is separate.

    one HTTP client     15 s per request, at most 2 MB per answer, a request id kept for support
    rate limit          per sub-account: at most LIMIT_PER_10S requests in any 10 seconds (HighLevel allows 100), so one
                        busy business never starves the others
    retries             429 and 5xx: up to 3, with backoff and jitter, honouring Retry-After; 400/401/403/404: never
    envelope check      a 200 that carries an error (`error`, `success: false`, a non-2xx `statusCode`) is a failure
    errors              AuthError (reconnect), RateLimited, Malformed, Unsupported, Transient: plain sentences for owners

Readers return Page(records, cursor, total, request_id). Cursors are opaque dicts; a cursor half-given by the API is an
error, never "best effort". Every page is checked against the shape it must have before anything is stored.
"""
import asyncio
import random
import re
import time
from dataclasses import dataclass, field

import httpx

from .config import settings

VERSION = "2021-07-28"          # HighLevel API v2's Version header for these endpoints
TIMEOUT = 15
MAX_BYTES = 2_000_000
LIMIT_PER_10S = 50
PAGE = 100
RETRIES = 3


class EmployzError(Exception):
    code = "error"
    message = "Employz.ai couldn't be read just now. The hub will try again shortly."

    def __init__(self, detail: str = "", request_id: str = ""):
        super().__init__(detail or self.code)
        self.detail, self.request_id = detail, request_id


class AuthError(EmployzError):
    code = "auth"
    message = "Your Employz.ai connection was refused. Check the sub-account (location) id and the private integration token, then save again."


class RateLimited(EmployzError):
    code = "rate_limited"
    message = "Employz.ai asked the hub to slow down. The import continues on its own in a few minutes."


class Malformed(EmployzError):
    code = "malformed"
    message = "Employz.ai sent back something the hub didn't expect, so nothing from that page was saved. The team has been told."


class Unsupported(EmployzError):
    code = "unsupported"
    message = "This Employz.ai token can't read this part of the account. Give the private integration the read permissions listed."


class Transient(EmployzError):
    code = "transient"
    message = "Employz.ai couldn't be reached. The hub will try again shortly."


@dataclass
class Page:
    records: list
    cursor: dict | None = None          # None: this was the last page
    total: int | None = None
    request_id: str = ""
    extra: dict = field(default_factory=dict)


# ───────────────────────── the rate limit, per sub-account ─────────────────────────
_windows: dict[str, list[float]] = {}
_locks: dict[str, asyncio.Lock] = {}


async def _wait_turn(location: str) -> None:
    lock = _locks.setdefault(location, asyncio.Lock())
    async with lock:
        while True:
            t = time.monotonic()
            win = [x for x in _windows.get(location, []) if t - x < 10]
            if len(win) < LIMIT_PER_10S:
                win.append(t)
                _windows[location] = win
                return
            await asyncio.sleep(max(0.05, 10 - (t - win[0])))


def _transport():
    """Tests swap this for an httpx.MockTransport."""
    return None


async def get(conn: dict, path: str, params: dict | None = None) -> tuple[dict, str]:
    """One GET to Employz.ai for this connection's sub-account: (the JSON body, the request id)."""
    location = str(conn.get("location_id") or "")
    headers = {"Authorization": f"Bearer {conn.get('token', '')}", "Version": VERSION, "Accept": "application/json"}
    url = f"{settings.employz_api_base}{path}"
    attempt = 0
    while True:
        await _wait_turn(location)
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, transport=_transport()) as c:
                async with c.stream("GET", url, params=params, headers=headers) as r:
                    rid = r.headers.get("x-request-id") or r.headers.get("request-id") or ""
                    body = b""
                    async for chunk in r.aiter_bytes():
                        body += chunk
                        if len(body) > MAX_BYTES:
                            raise Malformed("answer larger than the size cap", rid)
                    status, retry_after = r.status_code, r.headers.get("retry-after")
        except EmployzError:
            raise
        except (httpx.TimeoutException, httpx.TransportError) as e:
            if attempt >= RETRIES:
                raise Transient(type(e).__name__)
            attempt += 1
            await asyncio.sleep(min(2 ** attempt, 8) + random.random())
            continue
        if status in (401, 403):
            raise AuthError(f"http {status}", rid)
        if status == 404:
            raise Unsupported("http 404", rid)
        if status == 429 or status >= 500:
            if attempt >= RETRIES:
                raise RateLimited(f"http {status}", rid) if status == 429 else Transient(f"http {status}", rid)
            attempt += 1
            try:
                wait = min(float(retry_after), 30.0) if retry_after else None
            except ValueError:
                wait = None
            await asyncio.sleep(wait if wait is not None else min(2 ** attempt, 8) + random.random())
            continue
        if status >= 400:
            raise Unsupported(f"http {status}", rid)
        try:
            import json
            data = json.loads(body or b"{}")
        except ValueError:
            raise Malformed("not JSON", rid)
        if not isinstance(data, dict):
            raise Malformed("not an object", rid)
        sc = data.get("statusCode")
        if data.get("success") is False or (isinstance(sc, int) and not 200 <= sc < 300) or (data.get("error") and not data.get(_first_key(path))):
            if isinstance(sc, int) and sc in (401, 403):
                raise AuthError(f"envelope {sc}", rid)
            raise Malformed("error envelope", rid)
        return data, rid


def _first_key(path: str) -> str:
    """The list or object an endpoint's answer must carry (used to tell a real answer from an error envelope)."""
    for part, key in (("/pipelines", "pipelines"), ("/customFields", "customFields"), ("/opportunities", "opportunities"),
                      ("/contacts", "contacts"), ("/users", "users"), ("/locations", "location")):
        if part in path:
            return key
    return ""


# ───────────────────────── checking shapes ─────────────────────────
def _list(data: dict, key: str, rid: str) -> list[dict]:
    v = data.get(key)
    if not isinstance(v, list) or any(not isinstance(x, dict) or not isinstance(x.get("id"), str) or not x.get("id") for x in v):
        raise Malformed(f"'{key}' is not a list of records with ids", rid)
    return v


def _cursor(meta: dict | None, rid: str) -> dict | None:
    """The next page from `meta`: startAfter and startAfterId together, or none at all. One without the other is an error."""
    meta = meta or {}
    sa, sid = meta.get("startAfter"), meta.get("startAfterId")
    if (sa in (None, "") and not sid) and meta.get("nextPageUrl"):   # some answers only give the next page's address
        q = dict(re.findall(r"[?&](startAfter|startAfterId)=([^&]+)", str(meta["nextPageUrl"])))
        sa, sid = q.get("startAfter"), q.get("startAfterId")
    if sa in (None, "") and not sid:
        return None
    if sa in (None, "") or not sid:
        raise Malformed("half a cursor (startAfter without startAfterId, or the other way)", rid)
    return {"startAfter": sa, "startAfterId": str(sid)}


def _total(meta: dict | None) -> int | None:
    try:
        return int((meta or {}).get("total"))
    except (TypeError, ValueError):
        return None


# ───────────────────────── readers ─────────────────────────
async def read_location(conn: dict) -> dict:
    """The sub-account itself. Its id must be the one the owner entered."""
    try:
        data, rid = await get(conn, f"/locations/{conn.get('location_id')}")
    except Unsupported as e:      # a 404 here means the id is wrong or the token can't open it: either way, reconnect
        raise AuthError("this token can't open that sub-account", e.request_id)
    loc = data.get("location")
    if not isinstance(loc, dict) or not loc.get("id"):
        raise Malformed("no location in the answer", rid)
    if loc["id"] != conn.get("location_id"):
        raise AuthError("the token belongs to another sub-account", rid)
    return {"id": loc["id"], "name": str(loc.get("name") or "")[:120], "timezone": str(loc.get("timezone") or "")[:60]}


async def read_users(conn: dict) -> Page:
    data, rid = await get(conn, "/users/", {"locationId": conn.get("location_id")})
    return Page(_list(data, "users", rid), None, None, rid)


async def read_custom_fields(conn: dict) -> Page:
    data, rid = await get(conn, f"/locations/{conn.get('location_id')}/customFields")
    return Page(_list(data, "customFields", rid), None, None, rid)


async def read_pipelines(conn: dict) -> Page:
    data, rid = await get(conn, "/opportunities/pipelines", {"locationId": conn.get("location_id")})
    pipes = _list(data, "pipelines", rid)
    for p in pipes:
        stages = p.get("stages")
        if not isinstance(stages, list) or any(not isinstance(s, dict) or not s.get("id") for s in stages):
            raise Malformed("a pipeline without a list of stages", rid)
    return Page(pipes, None, None, rid)


async def read_contacts(conn: dict, cursor: dict | None = None) -> Page:
    params = {"locationId": conn.get("location_id"), "limit": PAGE, **(cursor or {})}
    data, rid = await get(conn, "/contacts/", params)
    recs = _list(data, "contacts", rid)
    nxt = _cursor(data.get("meta"), rid) if len(recs) >= PAGE else None
    return Page(recs, nxt, _total(data.get("meta")), rid)


async def read_opportunities(conn: dict, cursor: dict | None = None) -> Page:
    params = {"location_id": conn.get("location_id"), "limit": PAGE, **(cursor or {})}
    data, rid = await get(conn, "/opportunities/search", params)
    recs = _list(data, "opportunities", rid)
    nxt = _cursor(data.get("meta"), rid) if len(recs) >= PAGE else None
    return Page(recs, nxt, _total(data.get("meta")), rid)


TYPES = ("location", "users", "custom_fields", "pipelines", "contacts", "opportunities")
LABELS = {"location": "Business profile", "users": "Team members", "custom_fields": "Custom fields", "pipelines": "Pipelines",
          "contacts": "Contacts", "opportunities": "Opportunities"}


async def capabilities(conn: dict) -> dict:
    """The connection check: the sub-account, and for each kind of data whether this token can read it (one record each)."""
    out: dict = {"ok": False, "location": None, "types": []}
    try:
        out["location"] = await read_location(conn)
    except EmployzError as e:
        out["error"] = e.message
        out["types"] = [{"key": "location", "label": LABELS["location"], "readable": False, "reason": e.message}]
        return out
    out["types"].append({"key": "location", "label": LABELS["location"], "readable": True, "reason": ""})
    probes = {"users": read_users, "custom_fields": read_custom_fields, "pipelines": read_pipelines,
              "contacts": read_contacts, "opportunities": read_opportunities}
    for key, fn in probes.items():
        try:
            await fn(conn)
            out["types"].append({"key": key, "label": LABELS[key], "readable": True, "reason": ""})
        except EmployzError as e:
            out["types"].append({"key": key, "label": LABELS[key], "readable": False, "reason": e.message})
    out["ok"] = all(t["readable"] for t in out["types"] if t["key"] in ("location", "pipelines", "contacts", "opportunities"))
    return out
