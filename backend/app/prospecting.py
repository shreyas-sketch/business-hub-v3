"""
Find new customers (v2.4, Lead engine) — the data side: Google Maps search, the owner's kept list, "Do not contact",
what sending changes, and the follow-up agent. The endpoints are in routers/prospects.py; the AI jobs in
ai/prospect_tasks.py.

Google's rules (Places API policies)
- The database keeps only Google's place IDs (allowed for ever), plus the owner's own status, notes, dates and the kind of
  business he searched for. Google's name, address, phone, website and coordinates are NEVER saved: not on a prospect, not
  in the Send list, not in the activity log, not in My outputs. Every page that shows them asks Google again (Place Details)
  and keeps the answer only for that one request (`Live`). The kept list is paged at 20, so one page view costs at most 20
  details calls.
- "Within X km of my business": the owner picks his own business on Google Maps once; only its place ID is saved. Each such
  search asks Google for its location again (one cheap details call) and uses it for that search only, so no coordinates
  are ever stored and nothing needs the 30-day sweep in google_retention.py.
- "Do not contact" for a Google business is kept by place ID, and by a keyed one-way hash of its phone number (so the same
  number is blocked again if it comes back through the owner's own list) — never the number itself.
- Search results show the "Google Maps" attribution; there is no export of Google results.

The owner's own list (an exhibition list, an association directory) is his data, so its name, phone, email, website and city
are saved normally (source "list").

Collections (see docs/V24_BUILD_CONTRACTS.md for prospect_searches, prospects, prospect_block)
prospect_settings        {_id: ws, kinds [str], kinds_by owner|ai|best_customers, kinds_at, area_mode city|area|radius, area,
                          radius_km, home_place_id, home_set_at, updated_at, by}
prospect_customer_kinds  {_id: "<ws>:<customer key>", ws, key, kind, at, by} — the owner's answer for one of his best customers
prospects (besides the contract fields): search_id, steps {first|day3|day7: approval id}, draft {email_subject, email_body,
                          call_opener, by, at}, reply_help {reply, answer, said_no, by, at}, drafts_n, phone_key (list only)
"""
import asyncio
import csv
import hashlib
import hmac
import io
import logging
import math
import re
from datetime import date, datetime, timedelta, timezone

import httpx
from fastapi import HTTPException

from . import competitor_watch as cw
from . import growth_core, salesbook
from .agents import approvals as appr
from .agents import catalog, jobs
from .agents.catalog import Skip
from .config import settings
from .db import IST, db, now
from .services import new_id
from .text import greeting_name

log = logging.getLogger("prospects")

SEARCH_MASK = ("places.id,places.displayName,places.formattedAddress,places.types,places.primaryTypeDisplayName,"
               "places.businessStatus,places.googleMapsUri")
NEAR_MASK = SEARCH_MASK + ",places.location"          # "within X km" only: to leave out places beyond the circle (never stored)
DETAILS_MASK = "displayName,formattedAddress,nationalPhoneNumber,internationalPhoneNumber,websiteUri,googleMapsUri,businessStatus"
HOME_MASK = "places.id,places.displayName,places.formattedAddress"
LOCATION_MASK = "location"
PAGE_SIZE = 20
MAX_KINDS = 6
DETAILS_AT_ONCE = 5
SITE_SECONDS = 25
STATUSES = ("kept", "drafted", "contacted", "replied", "deal", "not_interested", "dnc")
LABELS = {"kept": "Kept", "drafted": "Drafted", "contacted": "Contacted", "replied": "Replied", "deal": "Became a deal",
          "not_interested": "Not interested", "dnc": "Do not contact"}
STOP = {"replied", "deal", "not_interested", "dnc"}       # no more messages from the hub to these
STEPS = ("first", "day3", "day7")
STEP_LABEL = {"first": "First message", "day3": "Day 3 follow-up", "day7": "Day 7 follow-up"}
LEGAL = {"pvt", "private", "ltd", "limited", "llp", "inc", "co", "the", "pvtltd"}
DAILY_WHY = ("Sending many first messages to people who don't know you can get a WhatsApp number blocked, so the hub stops at "
             "{n} a day.")
LINE = "You decide who to contact. Do not message or call people who asked not to be contacted."


# ───────────────────────── small helpers ─────────────────────────
def ist_day(at: datetime | None = None) -> date:
    at = at or now()
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return at.astimezone(IST).date()


def norm_name(s: str) -> str:
    """'Sharma Hardware Pvt. Ltd.' → 'sharma hardware' (to match a Google result against the owner's contacts)."""
    t = re.sub(r"[^0-9a-zऀ-ॿ ]+", " ", str(s or "").lower().replace("&", " and "))
    return " ".join(w for w in t.split() if w not in LEGAL)


def clean_kind(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip(" .,;-")[:60]


def _secret() -> bytes:
    return (settings.jwt_secret or "development-only-secret-do-not-use-in-production-0000").encode()


def phone_hash(ws: str, phone: str) -> str:
    """A keyed one-way hash of a phone number, per business: lets "Do not contact" block a number without keeping it."""
    key = salesbook.phone_key(phone)
    return hmac.new(_secret(), f"prospect-block:{ws}:{key}".encode(), hashlib.sha256).hexdigest()[:40] if key else ""


def _http(url: str) -> str:
    u = str(url or "").strip()
    return u[:500] if re.match(r"^https?://", u, re.I) else ""


def distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lng1, lat2, lng2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(h)))


# ───────────────────────── Google Places (New) ─────────────────────────
def _headers(mask: str) -> dict:
    return {"X-Goog-Api-Key": settings.google_places_api_key, "X-Goog-FieldMask": mask, "Content-Type": "application/json"}


async def text_search(query: str, mask: str = SEARCH_MASK, circle: tuple[float, float, int] | None = None, size: int = PAGE_SIZE) -> list[dict]:
    """One billed Text Search call: up to `size` places, as Google sent them (used in this request only)."""
    body: dict = {"textQuery": query[:200], "pageSize": size, "regionCode": "IN", "languageCode": "en"}
    if circle:
        lat, lng, radius_m = circle
        body["locationBias"] = {"circle": {"center": {"latitude": lat, "longitude": lng}, "radius": float(min(max(radius_m, 1), 50000))}}
    try:
        async with cw._places_client() as client:
            r = await client.post(f"{settings.google_places_api_base}/places:searchText", headers=_headers(mask), json=body)
    except httpx.HTTPError as e:
        raise cw.PlacesError("unreachable") from e
    if r.status_code != 200:
        raise cw.PlacesError(f"status {r.status_code}")
    out = []
    for p in (r.json().get("places") or [])[:size]:
        if isinstance(p, dict) and cw.PLACE_ID.match(str(p.get("id") or "")):
            out.append(p)
    return out


async def place_details(place_id: str, mask: str = DETAILS_MASK) -> dict | None:
    """One billed Place Details call. None when Google no longer has the place."""
    if not cw.PLACE_ID.match(place_id or ""):
        return None
    try:
        async with cw._places_client() as client:
            r = await client.get(f"{settings.google_places_api_base}/places/{place_id}", headers=_headers(mask))
    except httpx.HTTPError as e:
        raise cw.PlacesError("unreachable") from e
    if r.status_code in (400, 404):
        return None
    if r.status_code != 200:
        raise cw.PlacesError(f"status {r.status_code}")
    d = r.json()
    return d if isinstance(d, dict) else None


def _text(v) -> str:
    return str((v or {}).get("text") or "") if isinstance(v, dict) else ""


def _closed(status) -> bool:
    return str(status or "").upper().startswith("CLOSED")


def details_view(d: dict) -> dict:
    return {"name": _text(d.get("displayName"))[:120], "address": str(d.get("formattedAddress") or "")[:200],
            "phone": str(d.get("nationalPhoneNumber") or d.get("internationalPhoneNumber") or "")[:30],
            "phone_intl": str(d.get("internationalPhoneNumber") or d.get("nationalPhoneNumber") or "")[:30],
            "website": _http(d.get("websiteUri")), "maps_url": _http(d.get("googleMapsUri")), "closed": _closed(d.get("businessStatus"))}


class Live:
    """Google's details for ONE request: each place asked at most once, kept in memory only, never saved."""

    def __init__(self):
        self.memo: dict[str, dict] = {}
        self.calls = 0
        self.ready = cw.places_ready()
        self._sem = asyncio.Semaphore(DETAILS_AT_ONCE)

    async def details(self, place_id: str) -> dict:
        """{"ok": True, name, address, phone, phone_intl, website, maps_url, closed} or {"ok": False, "gone"|"note"}."""
        if place_id in self.memo:
            return self.memo[place_id]
        if not self.ready:
            out = {"ok": False, "gone": False, "note": "Details from Google Maps need a server setting that isn't switched on yet."}
        else:
            try:
                async with self._sem:
                    self.calls += 1
                    d = await place_details(place_id)
                out = {"ok": True, **details_view(d)} if d else {"ok": False, "gone": True, "note": "Google Maps no longer lists this business."}
            except cw.PlacesError:
                out = {"ok": False, "gone": False, "note": "Google Maps didn't answer just now. Open the page again in a minute."}
        self.memo[place_id] = out
        return out

    async def many(self, place_ids: list[str]) -> dict[str, dict]:
        ids = list(dict.fromkeys(i for i in place_ids if i))
        got = await asyncio.gather(*(self.details(i) for i in ids))
        return dict(zip(ids, got))


async def home_location(place_id: str) -> tuple[float, float] | None:
    """The owner's own business location, asked again for each "within X km" search (never stored)."""
    d = await place_details(place_id, LOCATION_MASK)
    loc = (d or {}).get("location") or {}
    try:
        return float(loc["latitude"]), float(loc["longitude"])
    except (KeyError, TypeError, ValueError):
        return None


# ───────────────────────── settings ─────────────────────────
async def get_settings(ws: str) -> dict:
    doc = await db().prospect_settings.find_one({"_id": ws}) or {}
    b = await db().businesses.find_one({"owner_id": ws}, {"city": 1}) or {}
    return {"kinds": [k for k in doc.get("kinds") or [] if k][:MAX_KINDS], "kinds_by": doc.get("kinds_by"),
            "area_mode": doc.get("area_mode") or "city", "area": doc.get("area") or "", "radius_km": int(doc.get("radius_km") or 10),
            "home_place_id": doc.get("home_place_id"), "city": str(b.get("city") or "").strip()}


async def save_settings(ws: str, patch: dict, by: str) -> None:
    await db().prospect_settings.update_one({"_id": ws}, {"$set": {**patch, "updated_at": now(), "by": by}}, upsert=True)


def place_line(st: dict) -> str:
    """Where the search looks, in the owner's words (saved on prospect_searches.area)."""
    if st["area_mode"] == "radius":
        return f"within {st['radius_km']} km of your business"
    where = (st["area"] if st["area_mode"] == "area" else st["city"]) or st["city"]
    return where


# ───────────────────────── who is already known ─────────────────────────
async def known(ws: str) -> dict:
    """Phones (last 10 digits) and names of everyone the owner already deals with — Contacts, Customers, his own list —
    and his own business, so search results leave them out."""
    phones, names = set(), set()
    async for c in db().contacts.find({"ws": ws}, {"name": 1, "phone": 1, "company": 1}).limit(salesbook.MAX_ROWS):
        phones.add(salesbook.phone_key(c.get("phone") or ""))
        names |= {norm_name(c.get("name") or ""), norm_name(c.get("company") or "")}
    async for c in db().customers.find({"ws": ws}, {"name": 1, "phone": 1}).limit(salesbook.MAX_ROWS):
        phones.add(salesbook.phone_key(c.get("phone") or ""))
        names.add(norm_name(c.get("name") or ""))
    listed = set()
    async for p in db().prospects.find({"ws": ws, "source": "list"}, {"phone_key": 1}).limit(salesbook.MAX_ROWS):
        if p.get("phone_key"):
            listed.add(p["phone_key"])
    b = await db().businesses.find_one({"owner_id": ws}, {"name": 1, "whatsapp": 1, "phone": 1}) or {}
    st = await db().prospect_settings.find_one({"_id": ws}, {"home_place_id": 1}) or {}
    return {"phones": phones - {""}, "names": {n for n in names if len(n) >= 3}, "listed": listed,
            "own_names": {n for n in (norm_name(b.get("name") or ""),) if len(n) >= 3},
            "own_phones": {salesbook.phone_key(b.get("whatsapp") or ""), salesbook.phone_key(b.get("phone") or "")} - {""},
            "own_place": st.get("home_place_id")}


async def blocks(ws: str) -> tuple[set[str], set[str]]:
    """(place IDs, phone hashes) marked "Do not contact" by this business."""
    places, hashes = set(), set()
    async for b in db().prospect_block.find({"ws": ws}, {"place_id": 1, "phone_hash": 1}).limit(50_000):
        if b.get("place_id"):
            places.add(b["place_id"])
        if b.get("phone_hash"):
            hashes.add(b["phone_hash"])
    return places, hashes


async def is_blocked(ws: str, place_id: str | None, phone: str = "") -> bool:
    q = []
    if place_id:
        q.append({"place_id": place_id})
    h = phone_hash(ws, phone)
    if h:
        q.append({"phone_hash": h})
    return bool(q) and bool(await db().prospect_block.find_one({"ws": ws, "$or": q}, {"_id": 1}))


async def block(ws: str, p: dict, live_phone: str, by: str) -> None:
    """"Do not contact", for ever: by place ID and by phone (the owner's own list keeps the number; Google's is only hashed)."""
    phone = p.get("phone") if p.get("source") == "list" else ""
    doc = {"_id": new_id(), "ws": ws, "place_id": p.get("place_id"), "phone": phone or None,
           "phone_hash": phone_hash(ws, phone or live_phone) or None, "prospect_id": p["_id"], "at": now(), "by": by}
    await db().prospect_block.insert_one(doc)


async def expire_pending(ws: str, prospect_id: str, why: str) -> int:
    return await appr.expire({"ws": ws, "kind": "outreach", "prospect_id": prospect_id}, why)


# ───────────────────────── sending (called by agents/approvals.decide and after_sent) ─────────────────────────
STOPPED_WHY = {"dnc": "You marked this business Do not contact, so this message can't be sent.",
               "not_interested": "You marked this business Not interested, so this message can't be sent.",
               "replied": "This business already replied. Answer them from their page in Find new customers.",
               "deal": "This business is already a deal in your pipeline."}


async def phone_for(p: dict, live: Live | None = None) -> str:
    """The number to open WhatsApp with: the owner's own list keeps it; for a Google business it is asked from Google now."""
    if p.get("source") == "list":
        return p.get("phone") or ""
    live = live or Live()
    d = await live.details(p.get("place_id") or "")
    if not d["ok"]:
        raise HTTPException(400 if d.get("gone") or not live.ready else 502, d["note"])
    return d.get("phone_intl") or ""


async def before_own(approval: dict) -> str:
    """Before an outreach message opens the owner's WhatsApp: the business may still be contacted, the day's first messages
    aren't used up, and there is a mobile number (looked up live for a Google business — never saved). Returns the number."""
    from . import crm
    ws = approval["ws"]
    if not approval.get("prospect_id"):
        # an outreach message made outside Find new customers: its own saved number, still never to a blocked number and
        # still within the day's first messages
        mobile = crm.norm_phone(approval.get("to_phone"))
        if not mobile:
            raise HTTPException(400, "There is no mobile number for this message, so WhatsApp can't open.")
        if await is_blocked(ws, None, mobile):
            raise HTTPException(400, STOPPED_WHY["dnc"])
        if (approval.get("step") or "first") == "first":
            await take_daily(await db().users.find_one({"_id": ws}) or {"_id": ws})
        return mobile
    p = await db().prospects.find_one({"_id": approval.get("prospect_id"), "ws": ws})
    if not p:
        raise HTTPException(400, "This business is no longer on your Find new customers list.")
    if p.get("status") in STOP:
        raise HTTPException(400, STOPPED_WHY[p["status"]])
    raw = approval.get("to_phone") or await phone_for(p)
    mobile = crm.norm_phone(raw)
    if not mobile:
        raise HTTPException(400, "There is no mobile number for this business, so WhatsApp can't open. Call them, or use the email draft."
                            if not raw else "This business's number isn't a mobile number, so WhatsApp can't open. Call them instead.")
    if await is_blocked(ws, p.get("place_id"), mobile):
        raise HTTPException(400, STOPPED_WHY["dnc"])
    if (approval.get("step") or "first") == "first" and p.get("status") in ("kept", "drafted"):
        await take_daily(await db().users.find_one({"_id": ws}) or {"_id": ws})
    return mobile


async def take_daily(owner: dict) -> None:
    try:
        await growth_core.take(owner, "outreach_per_day")
    except HTTPException as e:
        if e.status_code == 429:
            from . import plans
            raise HTTPException(429, f"{e.detail} {DAILY_WHY.format(n=plans.growth_limit(owner, 'outreach_per_day'))}")
        raise


async def mark_contacted(p: dict, at: datetime) -> None:
    day = ist_day(at)
    await db().prospects.update_one({"_id": p["_id"], "status": {"$in": ["kept", "drafted"]}},
                                    {"$set": {"status": "contacted", "contacted_at": at, "followups_sent": 0,
                                              "next_followup_on": (day + timedelta(days=3)).isoformat(), "updated_at": at}})


async def on_outreach_sent(approval: dict, at: datetime) -> None:
    """A first message or follow-up was sent from the owner's own WhatsApp (from the prospect's page or the Send list)."""
    p = await db().prospects.find_one({"_id": approval.get("prospect_id"), "ws": approval["ws"]})
    if not p:
        return
    step = approval.get("step") or "first"
    if step == "first":
        await mark_contacted(p, at)
        return
    if p.get("status") != "contacted":
        return
    if step == "day3":
        contacted = ist_day(p.get("contacted_at") or at)
        nxt = max(contacted + timedelta(days=7), ist_day(at) + timedelta(days=1))
        patch = {"followups_sent": max(int(p.get("followups_sent") or 0), 1), "next_followup_on": nxt.isoformat()}
    else:
        patch = {"followups_sent": 2, "next_followup_on": None}
    await db().prospects.update_one({"_id": p["_id"]}, {"$set": {**patch, "updated_at": at}})


# ───────────────────────── the follow-up agent (day 3 and day 7, rules — no AI) ─────────────────────────
def _what(b: dict) -> str:
    from .ai.tasks import _short
    w = _short(str(b.get("industry") or "what we do"), 10).rstrip(".")
    return w[:1].lower() + w[1:]


def followup_text(b: dict, owner_name: str, step: str, contacted: date) -> str:
    first = greeting_name(owner_name or "")
    who = f"{first} from {b.get('name') or 'our business'}" if first else (b.get("name") or "our business")
    on = f"{contacted.day} {contacted.strftime('%b')}"
    if step == "day3":
        return (f"Hello, this is {who}. I wrote to you on {on} about {_what(b)}. Is this something you need right now? "
                f"A short reply is enough. If it isn't useful, just reply \"no\" and I won't message again.")
    return (f"Hello again from {b.get('name') or 'our business'}. This is my last message about {_what(b)}. If it's useful later, "
            f"you can reply any time. If not, just reply \"no\" and I won't message again.")


async def followups(owner: dict, run: dict):
    """Every day at 10am: a day-3 follow-up for each business the owner messaged that hasn't replied, and a day-7 one after he
    sent the day-3 one. Rules text, no AI and no token. Stops on Replied, Not interested, Do not contact and Became a deal."""
    ws, at = owner["_id"], run["at"]
    if not await growth_core.role_on(ws, "outreach_writer"):
        raise Skip("The outreach writer is switched off.")
    today = ist_day(at).isoformat()
    due = [p async for p in db().prospects.find({"ws": ws, "status": "contacted", "next_followup_on": {"$ne": None, "$lte": today}})
           .sort("next_followup_on", 1).limit(200)]
    if not due:
        raise Skip("No follow-ups are due today.")
    b = await db().businesses.find_one({"owner_id": ws}) or {}
    made, first = 0, None
    async with growth_core.working(ws, "outreach_writer"):
        for p in due:
            steps = p.get("steps") or {}
            step = "day3" if not steps.get("day3") else "day7" if not steps.get("day7") and int(p.get("followups_sent") or 0) >= 1 else None
            if step is None or await is_blocked(ws, p.get("place_id"), p.get("phone") or ""):
                await db().prospects.update_one({"_id": p["_id"]}, {"$set": {"next_followup_on": None}})
                continue
            if not await jobs.claim(f"prospect-fu:{p['_id']}:{step}", at):
                continue
            text = followup_text(b, owner.get("name") or "", step, ist_day(p.get("contacted_at") or at))
            a = await appr.create_approval(owner, ws, "outreach_writer", "outreach", f"{STEP_LABEL[step]} · {p.get('kind') or 'new business'}",
                                           p.get("name") if p.get("source") == "list" else "",
                                           p.get("phone") if p.get("source") == "list" else "", text, allow_act=False,
                                           prospect_id=p["_id"], step=step)
            await db().prospects.update_one({"_id": p["_id"]}, {"$set": {f"steps.{step}": a["_id"], "next_followup_on": None, "updated_at": at}})
            await growth_core.log_action(ws, "outreach_writer", f"Wrote a {STEP_LABEL[step].lower()} for a business you messaged "
                                                                f"({p.get('kind') or 'new business'})", ref=f"prospect:{p['_id']}")
            made += 1
            first = first or p["_id"]
    if not made:
        raise Skip("No follow-ups are due today.")
    return (f"{made} {'follow-up' if made == 1 else 'follow-ups'} for businesses you messaged — ready in your Send list.",
            f"prospect:{first}")


async def _ready(owners: list[dict]) -> set:
    ids = [o["_id"] for o in owners]
    return set(await db().prospects.distinct("ws", {"ws": {"$in": ids}, "status": "contacted", "next_followup_on": {"$ne": None}}))


catalog.register("outreach_writer", {
    "name": "Outreach writer", "feature": "prospects", "trigger": "schedule", "customer_facing": True,
    "what": "Day 3 and day 7 after you message a business from Find new customers, a short follow-up waits in your Send list if "
            "they haven't replied. You send it from your own WhatsApp. Stops when you mark Replied, Not interested or Do not contact.",
    "when": "Every day at 10am",
    "schedule": {"every": "day", "at": "10:00", "grace_hours": 9}, "ready": _ready}, followups)


# ───────────────────────── "More like my best customers" ─────────────────────────
async def top_customers(ws: str, n: int = 5) -> list[dict]:
    """The owner's top customers by sales (the sales book, then the Customers list's own totals), walk-ins left out."""
    rows = [r for r in await salesbook.history(ws) if r["amount"]]
    people = []
    for g in salesbook.group_customers(rows):
        if g["customer"] and not salesbook.is_walk_in(g["customer"]):
            people.append({"key": g["key"], "name": g["customer"], "total": round(sum(r["amount"] for r in g["rows"]), 2)})
    for c in await salesbook.customers_without_history(ws, rows):
        if not salesbook.is_walk_in(c["customer"]):
            people.append({"key": c["key"], "name": c["customer"], "total": c["total_value"]})
    people = [p for p in people if p["total"] > 0]
    people.sort(key=lambda p: (-p["total"], p["name"].lower()))
    top = people[:n]
    kinds = {k["key"]: k["kind"] async for k in db().prospect_customer_kinds.find({"ws": ws, "key": {"$in": [p["key"] for p in top]}})}
    for p in top:
        p["kind"] = kinds.get(p["key"], "")
    return top


# ───────────────────────── the owner's own list (CSV or Excel) ─────────────────────────
COLUMNS = {"name": ("name", "business", "business name", "company", "company name", "firm", "firm name"),
           "phone": ("phone", "mobile", "whatsapp", "phone number", "mobile number", "contact number", "number"),
           "email": ("email", "e-mail", "email address", "mail"),
           "website": ("website", "web", "site", "url", "website address"),
           "city": ("city", "town", "area", "location"),
           "kind": ("kind", "type", "category", "kind of business", "business type")}
MAX_LIST_ROWS = 2000


def read_list(data: bytes, filename: str) -> list[list]:
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        import openpyxl
        try:
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception:  # noqa: BLE001
            raise HTTPException(400, "We couldn't open that Excel file. Save it again and retry.")
        return [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)][:MAX_LIST_ROWS + 1]
    if name.endswith(".csv"):
        return list(csv.reader(io.StringIO(data.decode("utf-8-sig", errors="replace"))))[:MAX_LIST_ROWS + 1]
    raise HTTPException(415, "Upload an Excel (.xlsx) or CSV file.")


def header_of(row: list) -> dict[str, int]:
    found: dict[str, int] = {}
    for i, h in enumerate(row):
        low = re.sub(r"\s+", " ", str(h or "").strip().lower())
        for key, names in COLUMNS.items():
            if key not in found and low in names:
                found[key] = i
                break
    return found


async def ensure_indexes() -> None:
    d = db()
    await d.prospects.create_index([("ws", 1), ("place_id", 1)], unique=True, partialFilterExpression={"place_id": {"$type": "string"}})
    await d.prospects.create_index([("ws", 1), ("status", 1), ("kept_at", -1)])
    await d.prospects.create_index([("ws", 1), ("kept_at", -1)])
    await d.prospects.create_index([("ws", 1), ("phone_key", 1)])
    await d.prospects.create_index([("status", 1), ("next_followup_on", 1)])
    await d.prospect_searches.create_index([("ws", 1), ("at", -1)])
    await d.prospect_block.create_index([("ws", 1), ("place_id", 1)])
    await d.prospect_block.create_index([("ws", 1), ("phone_hash", 1)])
    await d.prospect_customer_kinds.create_index([("ws", 1), ("key", 1)])
    await d.approvals.create_index([("ws", 1), ("prospect_id", 1)])
