"""
Reviews & reputation (v2.2, Membership — feature "reputation").

Reviews come in two ways:
- from Google, automatically, when the hub has a Google Places key (settings.google_places_api_key) and the owner picked
  their listing: the "Google review watch" agent reads Place Details every morning (rating, review count and the at most
  5 reviews Google returns), adds the reviews it hasn't seen (de-duplicated by Google's review id), keeps a rating/count
  snapshot for the trend, drafts replies (1 AI run each, at most 5 a day) and tells the owner only when there's something new;
- pasted by the owner from any platform (Google, Facebook, Justdial, IndiaMART, Practo…), with a reply drafted at once.

Nothing is ever posted for the owner: Google needs the owner's own login (Business Profile API) to post a reply, so the flow
is Approve → Copy reply → Open Google → "I've posted it". The hub never writes a review, and the review request asks for
honest feedback with nothing offered in return (Google forbids incentives).

Customer messages are tap-to-send only (a wa.me link that opens the owner's own WhatsApp with the text typed) — the hub's
WhatsApp number only ever alerts the owner. A referral ask goes only to a reviewer matched to a customer by an exact,
unique, full-name match (never fuzzy); otherwise the owner copies the message and sends it themselves.

Google's Maps Platform terms limit keeping Places content, so (forget_old_google, run from the page, the daily agent and the
hub-wide sweep): a Google review's words, stars and author, the rating snapshots, and the listing's name and address are
kept at most 30 days; only the place id and Google's review ids are kept longer. Notices and the activity log carry counts
only — never a reviewer's name or stars.

Billed Google calls are capped per business per day with an atomic counter (reputation_quota): searches, and the first look
when a listing is connected (so disconnecting and reconnecting can't loop). Google returns its 5 "most relevant" reviews,
not the newest, so a review counts as new (alert + draft) only when it was published after the listing was connected
(less a day) or in the last 14 days; an older one entering that top 5 is saved quietly.

reputation_settings  {_id: ws, google_place_id, google_name, google_address, google_at (when the name/address came from Google),
                      google_connected_at, review_link, auto_draft (default true), updated_at}
reviews              {_id, ws, source google|pasted, platform, google_review_name, author, author_uri, google_link, rating 1–5|None,
                      text, review_date "YYYY-MM-DD", fetched_at, reply_draft, reply_status none|drafted|approved|replied, draft_by
                      ai|owner, drafted_at, auto_day, approved_at, replied_at, customer_id, lead_id, match_name,
                      referral_status none|ready|sent|skipped, referral_sent_at, hidden (a deleted Google review, so it isn't
                      fetched again), google_expired, created_at, created_by}
reputation_snapshots {_id: "<ws>:<day>", ws, at, day, rating, count}
reputation_asks      {_id, ws, phone (digits), name, at}         review requests the owner tapped; nobody is asked twice in 90 days
reputation_quota     {_id: "<ws>:search|details:<YYYY-MM-DD>", ws, kind, n, at}   billed Google calls per business per day
"""
import logging
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from ..agents import catalog
from ..agents.catalog import Skip
from ..ai import reputation_tasks as rt
from ..config import settings
from ..context import Ctx, feature
from ..db import IST, db, now, public
from ..messaging import whatsapp_template
from ..security import normalise_phone
from ..services import new_id, notify, run_ai, track
from .hub import clean_link, wa_digits

router = APIRouter(prefix="/api")
log = logging.getLogger("reputation")
rep_ctx = feature("reputation", "manager")
owner_ctx = feature("reputation", "owner")     # the listing and the review link decide where customers are sent

PLATFORMS = ("Google", "Facebook", "Justdial", "IndiaMART", "Practo", "Other")
STATUSES = {"new": "none", "drafted": "drafted", "approved": "approved", "replied": "replied"}
DRAFTS_PER_DAY = 5           # automatic drafts by the daily agent (1 AI run each)
ASK_GAP_DAYS = 90            # nobody is asked for a review twice within this
SEARCHES_PER_DAY = 20        # Google searches per business per day (billed to the hub)
CONNECT_LOOKS_PER_DAY = 3    # Place Details calls when a listing is connected, per business per day (billed)
NEW_WITHIN_DAYS = 14         # a fetched review published in the last 14 days (or since connecting) counts as new
GOOGLE_KEEP_DAYS = 30        # Google's caching limit for Places content
HAPPY_LIMIT = 20
SEARCH_MASK = "places.id,places.displayName,places.formattedAddress,places.rating,places.userRatingCount"
DETAILS_MASK = "rating,userRatingCount,reviews"
PLACE_ID = re.compile(r"^[A-Za-z0-9_-]{10,300}$")
NO_KEY = "Google reviews come in automatically once the hub's Google key is set up. Until then, paste a review below."


def _today() -> date:
    return now().astimezone(IST).date()


# ───────────────────────── Google Places (New) ─────────────────────────
class GoogleError(Exception):
    """Google didn't answer usefully. The message is plain enough to show the owner."""


def http_client() -> httpx.AsyncClient:
    """One place to build the HTTP client, so tests can put a fake Google behind it."""
    return httpx.AsyncClient(timeout=12)


def google_ready() -> bool:
    return bool(settings.google_places_api_key)


async def quota(ws: str, kind: str, cap: int) -> bool:
    """Counts one more billed Google call today for this business; False when today's cap is passed. Atomic ($inc on one
    document), so parallel requests can't all slip under the cap."""
    key = f"{ws}:{kind}:{_today().isoformat()}"
    for _ in range(2):
        try:
            doc = await db().reputation_quota.find_one_and_update({"_id": key}, {"$inc": {"n": 1}, "$setOnInsert": {"ws": ws, "kind": kind, "at": now()}},
                                                                  upsert=True, return_document=ReturnDocument.AFTER)
            return (doc or {}).get("n", 1) <= cap
        except DuplicateKeyError:   # the day's first two calls at the same moment: the other one created the counter
            continue
    return False


async def _google(method: str, path: str, mask: str, body: dict | None = None) -> dict:
    headers = {"X-Goog-Api-Key": settings.google_places_api_key, "X-Goog-FieldMask": mask}
    try:
        async with http_client() as client:
            r = await client.request(method, f"{settings.google_places_api_base}{path}", headers=headers, json=body)
    except httpx.HTTPError as e:
        log.warning("Google Places %s unreachable: %s", method, type(e).__name__)
        raise GoogleError("Google couldn't be reached just now. Try again in a minute.") from e
    if r.status_code == 404:
        raise GoogleError("Google doesn't find this listing any more. Pick your business again.")
    if r.status_code >= 400:
        log.warning("Google Places %s %s: %s", method, r.status_code, r.text[:200])
        raise GoogleError("Google didn't answer just now. Try again in a minute.")
    try:
        data = r.json()
    except ValueError as e:
        raise GoogleError("Google sent an answer we couldn't read. Try again in a minute.") from e
    return data if isinstance(data, dict) else {}


def _text(v) -> str:
    """Google's LocalizedText ({"text": …}) or a plain string."""
    if isinstance(v, dict):
        v = v.get("text")
    return re.sub(r"\s+", " ", str(v or "")).strip()


def _rating(v) -> float | None:
    try:
        x = round(float(v), 1)
        return x if 0 < x <= 5 else None
    except (TypeError, ValueError):
        return None


def _stars(v) -> int | None:
    try:
        x = int(round(float(v)))
        return x if 1 <= x <= 5 else None
    except (TypeError, ValueError):
        return None


def _count(v) -> int | None:
    try:
        return max(int(v), 0)
    except (TypeError, ValueError):
        return None


def _published(v) -> datetime | None:
    m = re.match(r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})", str(v or ""))
    if not m:
        return None
    try:
        return datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


async def search_places(q: str) -> list[dict]:
    data = await _google("POST", "/places:searchText", SEARCH_MASK, {"textQuery": q, "languageCode": "en", "regionCode": "IN"})
    out = []
    for pl in data.get("places") or []:
        if not isinstance(pl, dict) or not PLACE_ID.match(str(pl.get("id") or "")):
            continue
        out.append({"place_id": pl["id"], "name": _text(pl.get("displayName"))[:120], "address": _text(pl.get("formattedAddress"))[:200],
                    "rating": _rating(pl.get("rating")), "count": _count(pl.get("userRatingCount"))})
    return out[:5]


async def place_details(place_id: str) -> dict:
    data = await _google("GET", f"/places/{quote(place_id, safe='')}", DETAILS_MASK)
    reviews = []
    for rv in data.get("reviews") or []:
        if not isinstance(rv, dict) or not str(rv.get("name") or "").strip():
            continue
        who = rv.get("authorAttribution") or {}
        when = _published(rv.get("publishTime"))
        uri = str(who.get("uri") or "")
        link = str(rv.get("googleMapsUri") or "")
        reviews.append({"google_review_name": str(rv["name"])[:300], "author": _text(who.get("displayName"))[:80],
                        "author_uri": uri[:500] if uri.startswith("https://") else "",
                        "google_link": link[:500] if link.startswith("https://") else "",
                        "rating": _stars(rv.get("rating")), "text": (_text(rv.get("originalText")) or _text(rv.get("text")))[:3000],
                        "review_date": (when or now()).astimezone(IST).strftime("%Y-%m-%d"), "published_at": when})
    return {"rating": _rating(data.get("rating")), "count": _count(data.get("userRatingCount")), "reviews": reviews}


def maps_link(st: dict) -> str:
    pid = st.get("google_place_id")
    if not pid:
        return ""
    return f"https://www.google.com/maps/search/?api=1&query={quote(st.get('google_name') or 'business')}&query_place_id={quote(pid, safe='')}"


def write_review_link(place_id: str) -> str:
    return f"https://search.google.com/local/writereview?placeid={quote(place_id, safe='')}"


# ───────────────────────── settings ─────────────────────────
async def get_settings(ws: str) -> dict:
    st = await db().reputation_settings.find_one({"_id": ws}) or {}
    return {"google_place_id": "", "google_name": "", "google_address": "", "review_link": "", "auto_draft": True,
            **{k: v for k, v in st.items() if v is not None}}


def review_link_of(st: dict, b: dict) -> tuple[str, str]:
    """The owner's "write a review" link: the one saved here, else the Knowledge Hub's, else Google's standard link for
    the connected listing. (link, where it came from)"""
    if (st.get("review_link") or "").strip():
        return st["review_link"].strip(), "here"
    if (b.get("review_link") or "").strip():
        return b["review_link"].strip(), "knowledge"
    if st.get("google_place_id"):
        return write_review_link(st["google_place_id"]), "google"
    return "", ""


async def _business(ws: str) -> dict:
    return await db().businesses.find_one({"owner_id": ws}) or {}


# ───────────────────────── matching a reviewer to a customer ─────────────────────────
def _name_pattern(name: str) -> str | None:
    """Exact full name, any letter case, any spacing between the words. A lone first name ("Ravi") or initials aren't
    enough to be sure who wrote a review, so they never match."""
    words = (name or "").split()
    if len(words) < 2 or sum(len(w.strip(".")) for w in words) < 4:
        return None
    return r"^\s*" + r"\s+".join(re.escape(w) for w in words) + r"\s*$"


async def opted_out_phones(ws: str) -> set[str]:
    """Numbers (as wa.me digits) of customers who asked not to get messages. Checked by number, so the same person reached
    through a won lead — or saved under another name — is never suggested or linked either."""
    return {wa_digits(c["phone"]) async for c in db().customers.find({"ws": ws, "opted_out": True, "phone": {"$nin": [None, ""]}},
                                                                      {"phone": 1}).limit(20000)}


async def match_customer(ws: str, name: str) -> dict | None:
    """The one customer (or won lead) with exactly this name. Two different numbers with the same name → no match.
    `opted_out` is true when that number belongs to a customer who opted out, whichever record matched."""
    pat = _name_pattern(name)
    if not pat:
        return None
    found: dict[str, dict] = {}
    async for c in db().customers.find({"ws": ws, "name": {"$regex": pat, "$options": "i"}, "phone": {"$nin": [None, ""]}},
                                       {"name": 1, "phone": 1, "opted_out": 1}).limit(5):
        found.setdefault(wa_digits(c["phone"]), {"customer_id": c["_id"], "lead_id": None, "name": c.get("name", ""), "phone": c["phone"],
                                                 "opted_out": bool(c.get("opted_out"))})
    async for x in db().leads.find({"owner_id": ws, "status": "won", "name": {"$regex": pat, "$options": "i"}, "phone": {"$nin": [None, ""]}},
                                   {"name": 1, "phone": 1}).limit(5):
        m = found.setdefault(wa_digits(x["phone"]), {"customer_id": None, "lead_id": None, "name": x.get("name", ""), "phone": x["phone"],
                                                     "opted_out": False})
        m["lead_id"] = m["lead_id"] or x["_id"]
    if len(found) != 1:
        return None
    digits, m = next(iter(found.items()))
    m["opted_out"] = m["opted_out"] or digits in await opted_out_phones(ws)
    return m


# ───────────────────────── reviews ─────────────────────────
def view(r: dict, st: dict | None = None) -> dict:
    out = public(r, "ws", "hidden", "created_by", "auto_day")
    out["happy"] = rt.is_happy(r.get("rating"), r.get("text") or "")
    out["matched"] = bool(r.get("customer_id") or r.get("lead_id"))
    out["open_link"] = r.get("google_link") or (maps_link(st or {}) if r.get("platform") == "Google" else "")
    return out


async def _save_review(ws: str, fields: dict, actor: str) -> dict:
    doc = {"_id": new_id(), "ws": ws, "source": "pasted", "platform": "Google", "google_review_name": "", "author": "", "author_uri": "",
           "google_link": "", "rating": None, "text": "", "review_date": _today().isoformat(), "fetched_at": None, "reply_draft": "",
           "reply_status": "none", "draft_by": None, "drafted_at": None, "approved_at": None, "approved_by": None, "replied_at": None,
           "replied_by": None, "customer_id": None, "lead_id": None, "match_name": "", "referral_sent_at": None, "hidden": False,
           "created_at": now(), "created_by": actor, **fields}
    doc["referral_status"] = "ready" if rt.is_happy(doc["rating"], doc["text"]) else "none"
    m = await match_customer(ws, doc["author"])
    if m:
        doc.update(customer_id=m["customer_id"], lead_id=m["lead_id"], match_name=m["name"])
    await db().reviews.insert_one(doc)
    return doc


async def _review(ws: str, review_id: str) -> dict:
    r = await db().reviews.find_one({"_id": review_id, "ws": ws, "hidden": {"$ne": True}})
    if not r:
        raise HTTPException(404, "Not found")
    return r


async def _draft(owner: dict, ws: str, review: dict, p: dict, **extra) -> dict:
    """One AI run → the reply draft. 400 without a Business Brain, 402 out of runs, 502 when the AI was busy or its draft
    broke a rule (the run is refunded)."""
    if not p.get("name") or not p.get("industry"):
        raise HTTPException(400, "Tell us about your business in the Knowledge Hub first — the reply is written from it.")
    text = await run_ai(owner, "reputation_reply", rt.reply(p, review), f"Reply to {review.get('author') or 'a review'}"[:140],
                        save_output=False, check=lambda r: rt.check_reply(r, p, review))
    patch = {"reply_draft": text, "reply_status": "drafted", "draft_by": "ai", "drafted_at": now(), "approved_at": None, "approved_by": None, **extra}
    await db().reviews.update_one({"_id": review["_id"], "ws": ws, "reply_status": {"$ne": "replied"}}, {"$set": patch})
    review.update(patch)
    return review


async def _pasted_twin(ws: str, rv: dict) -> dict | None:
    """The owner pasted this Google review before it came in from Google: the same author and the same words."""
    if not rv["author"] or not rv["text"]:
        return None
    async for r in db().reviews.find({"ws": ws, "source": "pasted", "platform": "Google", "google_review_name": "",
                                      "author": {"$regex": f"^{re.escape(rv['author'])}$", "$options": "i"}}).limit(20):
        if re.sub(r"\s+", " ", r.get("text") or "").strip().lower() == rv["text"].lower():
            return r
    return None


async def forget_old_google(ws: str, at: datetime) -> None:
    """Google's Maps Platform terms limit keeping Places content: a Google review's words, stars and author, the daily
    rating snapshots, and the listing's name and address, are kept at most 30 days. The place id, the review's id (so it
    isn't fetched as new again), the owner's own reply and its status stay. Safe to call for any business at any time
    (the page, the daily agent and the hub-wide sweep call it)."""
    cut = at - timedelta(days=GOOGLE_KEEP_DAYS)
    old = {"ws": ws, "source": "google", "fetched_at": {"$lt": cut}, "google_expired": {"$ne": True}}
    await db().reviews.update_many({**old, "referral_status": "ready"}, {"$set": {"referral_status": "none"}})   # no stars left to ask on
    await db().reviews.update_many(old, {"$set": {"text": "", "author": "", "author_uri": "", "rating": None, "google_expired": True,
                                                  "customer_id": None, "lead_id": None, "match_name": ""}})
    await db().reputation_snapshots.delete_many({"ws": ws, "at": {"$lt": cut}})
    st = await db().reputation_settings.find_one({"_id": ws}, {"google_name": 1, "google_address": 1, "google_at": 1, "updated_at": 1})
    if st and (st.get("google_name") or st.get("google_address")):
        # the name and address are as fresh as the last check of the listing (or the search they came from)
        last = await db().reputation_snapshots.find_one({"ws": ws}, {"at": 1}, sort=[("at", -1)])
        seen = [x for x in ((last or {}).get("at"), st.get("google_at") or st.get("updated_at")) if isinstance(x, datetime)]
        if not seen or max(seen) < cut:
            await db().reputation_settings.update_one({"_id": ws}, {"$set": {"google_name": "", "google_address": "", "google_at": None}})


async def check_google(ws: str, st: dict, at: datetime) -> dict:
    """One Place Details call: today's rating snapshot, and the reviews not seen before (Google returns at most 5, its "most
    relevant", not the newest). {"new": reviews published since connecting (less a day) or in the last 14 days — these are
    alerted and drafted; "quiet": older ones entering Google's top 5, saved without an alert or a draft}."""
    d = await place_details(st["google_place_id"])
    day = at.astimezone(IST).strftime("%Y-%m-%d")
    if d["rating"] is not None or d["count"] is not None:
        await db().reputation_snapshots.replace_one({"_id": f"{ws}:{day}"}, {"ws": ws, "at": at, "day": day, "rating": d["rating"], "count": d["count"]},
                                                    upsert=True)
    since = at - timedelta(days=NEW_WITHIN_DAYS)
    if isinstance(st.get("google_connected_at"), datetime):
        since = min(since, st["google_connected_at"] - timedelta(days=1))
    names = [rv["google_review_name"] for rv in d["reviews"]]
    known = set(await db().reviews.distinct("google_review_name", {"ws": ws, "google_review_name": {"$in": names}})) if names else set()
    new, quiet = [], []
    for rv in d["reviews"]:
        if rv["google_review_name"] in known:
            continue
        known.add(rv["google_review_name"])
        twin = await _pasted_twin(ws, rv)
        if twin:
            await db().reviews.update_one({"_id": twin["_id"]}, {"$set": {"google_review_name": rv["google_review_name"],
                                                                          "google_link": rv["google_link"], "author_uri": rv["author_uri"]}})
            continue
        fields = {k: v for k, v in rv.items() if k != "published_at"}
        try:
            doc = await _save_review(ws, {**fields, "source": "google", "platform": "Google", "fetched_at": at}, "google")
        except DuplicateKeyError:   # another worker added it a moment ago
            continue
        pub = rv.get("published_at")
        (new if isinstance(pub, datetime) and pub >= since else quiet).append(doc)   # no publish time: not sure it's new
    return {"new": new, "quiet": quiet, "rating": d["rating"], "count": d["count"]}


# ───────────────────────── the page ─────────────────────────
async def happy_list(ws: str, limit: int = HAPPY_LIMIT) -> list[dict]:
    """Recent won leads and customers, newest first, who haven't been asked for a review in the last 90 days (here or by
    the Review requests automation), and customers who haven't opted out of messages."""
    since = now() - timedelta(days=ASK_GAP_DAYS)
    asked = {a["phone"] async for a in db().reputation_asks.find({"ws": ws, "at": {"$gte": since}}, {"phone": 1})}
    asked |= await opted_out_phones(ws)        # never suggested, whether they show up as a customer or as a won lead
    rows: dict[str, dict] = {}

    def when(v) -> str:
        return v.astimezone(IST).strftime("%Y-%m-%d") if isinstance(v, datetime) else str(v or "")[:10]
    async for x in db().leads.find({"owner_id": ws, "status": "won", "phone": {"$nin": [None, ""]}},
                                   {"name": 1, "phone": 1, "won_at": 1, "updated_at": 1, "review_requested_at": 1}).sort("updated_at", -1).limit(300):
        d, rr = wa_digits(x["phone"]), x.get("review_requested_at")
        if d in asked or (isinstance(rr, datetime) and rr >= since):
            asked.add(d)
            continue
        day = when(x.get("won_at") or x.get("updated_at"))
        if d not in rows or day > rows[d]["since"]:
            rows[d] = {"name": x.get("name") or "", "phone": x["phone"], "source": "lead", "since": day}
    async for c in db().customers.find({"ws": ws, "phone": {"$nin": [None, ""]}, "opted_out": {"$ne": True}},
                                       {"name": 1, "phone": 1, "last_purchase": 1, "updated_at": 1}).sort("updated_at", -1).limit(300):
        d = wa_digits(c["phone"])
        if d in asked:
            continue
        day = when(c.get("last_purchase") or c.get("updated_at"))
        if d not in rows or day > rows[d]["since"]:
            rows[d] = {"name": c.get("name") or "", "phone": c["phone"], "source": "customer", "since": day}
    return sorted(rows.values(), key=lambda r: r["since"], reverse=True)[:limit]


async def _counts(ws: str) -> dict:
    return {k: await db().reviews.count_documents({"ws": ws, "hidden": {"$ne": True}, "reply_status": s}) for k, s in STATUSES.items()}


@router.get("/reputation")
async def overview(ctx: Ctx = Depends(rep_ctx)):
    ws = ctx.ws
    await forget_old_google(ws, now())
    st, b = await get_settings(ws), await _business(ws)
    snaps = [s async for s in db().reputation_snapshots.find({"ws": ws}).sort("at", -1).limit(8)][::-1]
    last = snaps[-1] if snaps else {}
    link, link_from = review_link_of(st, b)
    reviews = [view(r, st) async for r in db().reviews.find({"ws": ws, "hidden": {"$ne": True}}).sort([("review_date", -1), ("created_at", -1)]).limit(50)]
    return {
        "google": {"available": google_ready(), "place_id": st["google_place_id"] or None, "name": st["google_name"] or None,
                   "address": st["google_address"] or None, "rating": last.get("rating"), "count": last.get("count"),
                   "checked_at": last["at"].isoformat() if isinstance(last.get("at"), datetime) else None, "maps_link": maps_link(st) or None,
                   "trend": [{"day": s.get("day"), "rating": s.get("rating"), "count": s.get("count")} for s in snaps],
                   "message": None if google_ready() else NO_KEY},
        "review_link": link or None, "review_link_from": link_from or None, "auto_draft": bool(st.get("auto_draft", True)),
        "counts": await _counts(ws), "reviews": reviews,
        "referral_ready": await db().reviews.count_documents({"ws": ws, "hidden": {"$ne": True}, "referral_status": "ready"}),
        "happy_count": len(await happy_list(ws)), "b2b_note": rt.is_b2b(b), "contact": rt.contact_line(b) or None,
        "brain_ready": bool(b.get("name") and b.get("industry")), "platforms": list(PLATFORMS), "drafts_per_day": DRAFTS_PER_DAY,
    }


# ───────────────────────── connecting Google, and the review link ─────────────────────────
@router.get("/reputation/google-search")
async def google_search(q: str = "", ctx: Ctx = Depends(owner_ctx)):
    if not google_ready():
        raise HTTPException(400, NO_KEY)
    q = re.sub(r"\s+", " ", q or "").strip()
    if not 2 <= len(q) <= 120:
        raise HTTPException(400, "Type your business name and area, like “Shree Ganesh Interiors Thane”.")
    if not await quota(ctx.ws, "search", SEARCHES_PER_DAY):
        raise HTTPException(429, "That's a lot of searches today. Try again tomorrow, or paste your reviews below.")
    await track("reputation_search", ctx.ws)
    try:
        return {"places": await search_places(q)}
    except GoogleError as e:
        raise HTTPException(502, str(e))


class SettingsIn(BaseModel):
    google_place_id: str | None = Field(default=None, max_length=300)    # "" disconnects the listing
    google_name: str | None = Field(default=None, max_length=120)
    google_address: str | None = Field(default=None, max_length=200)
    review_link: str | None = Field(default=None, max_length=300)        # "" goes back to the Knowledge Hub's / Google's link
    auto_draft: bool | None = None


@router.put("/reputation/settings")
async def save_settings(body: SettingsIn, ctx: Ctx = Depends(owner_ctx)):
    ws = ctx.ws
    before = await get_settings(ws)
    patch: dict = {}
    if body.google_place_id is not None:
        pid = body.google_place_id.strip()
        if pid and not google_ready():
            raise HTTPException(400, NO_KEY)
        if pid and not PLACE_ID.match(pid):
            raise HTTPException(400, "That isn't a Google listing. Search for your business and pick it from the list.")
        patch.update(google_place_id=pid, google_name=re.sub(r"\s+", " ", body.google_name or "").strip()[:120] if pid else "",
                     google_address=re.sub(r"\s+", " ", body.google_address or "").strip()[:200] if pid else "")
        patch["google_at"] = now() if pid else None          # the name and address just came from a Google search
        if pid != before["google_place_id"]:
            patch["google_connected_at"] = now() if pid else None
    if body.review_link is not None:
        patch["review_link"] = clean_link(body.review_link)
    if body.auto_draft is not None:
        patch["auto_draft"] = body.auto_draft
    if patch:
        await db().reputation_settings.update_one({"_id": ws}, {"$set": {**patch, "updated_at": now(), "updated_by": ctx.actor}}, upsert=True)
        await track("reputation_settings", ws, **{k: (bool(v) if k != "auto_draft" else v) for k, v in patch.items()})
    note = None
    pid = patch.get("google_place_id")
    if pid and pid != before["google_place_id"]:
        # a first look at once, so the rating and the latest reviews show today (no AI runs, no alert) — a few times a day
        # at most, so disconnecting and connecting again can't run up the hub's Google bill
        if not await quota(ws, "details", CONNECT_LOOKS_PER_DAY):
            note = "Connected. Your reviews will come in with tomorrow morning's check."
        else:
            try:
                got = await check_google(ws, await get_settings(ws), now())
                n = len(got["new"]) + len(got["quiet"])
                note = f"Connected. {n} review{'s' if n != 1 else ''} came in from Google." if n else "Connected. New Google reviews will come in every morning."
            except GoogleError:
                note = "Connected. Google didn't answer just now, so your reviews will come in with tomorrow morning's check."
    return {**await overview(ctx), "note": note}


# ───────────────────────── reviews: paste, draft, edit, approve, posted ─────────────────────────
class PasteIn(BaseModel):
    platform: str = Field(default="Google", max_length=20)
    author: str = Field(default="", max_length=80)
    rating: int | None = Field(default=None, ge=1, le=5)
    text: str = Field(default="", max_length=3000)
    date: str = Field(default="", max_length=10)
    draft: bool = True                         # write a reply at once (1 AI run)

    @field_validator("platform")
    @classmethod
    def known(cls, v):
        match = next((p for p in PLATFORMS if p.lower() == (v or "").strip().lower()), None)
        if not match:
            raise ValueError("Pick where the review is: " + ", ".join(PLATFORMS))
        return match

    @field_validator("date")
    @classmethod
    def day(cls, v):
        if not (v or "").strip():
            return ""
        try:
            d = date.fromisoformat(v.strip())
        except ValueError:
            raise ValueError("Use a date like 2026-10-05")
        if d > _today() + timedelta(days=1):
            raise ValueError("The review date can't be in the future")
        return d.isoformat()


@router.post("/reputation/reviews")
async def paste_review(body: PasteIn, ctx: Ctx = Depends(rep_ctx)):
    text = re.sub(r"[ \t]+", " ", body.text).strip()
    if not text and not body.rating:
        raise HTTPException(400, "Paste the review's words, or pick its stars.")
    doc = await _save_review(ctx.ws, {"source": "pasted", "platform": body.platform, "author": re.sub(r"\s+", " ", body.author).strip(),
                                      "rating": body.rating, "text": text, "review_date": body.date or _today().isoformat()}, ctx.actor)
    await track("review_pasted", ctx.ws, platform=body.platform)
    draft_error = None
    if body.draft:
        try:
            doc = await _draft(ctx.owner, ctx.ws, doc, await _business(ctx.ws))
        except HTTPException as e:     # the review is kept either way; the owner can draft it later or write their own
            draft_error = ("You've used this month's AI runs. The review is saved — write your reply below, or draft it when runs are back."
                           if e.status_code == 402 else e.detail if isinstance(e.detail, str) else "The reply couldn't be drafted just now.")
    return {"review": view(doc, await get_settings(ctx.ws)), "draft_error": draft_error}


@router.post("/reputation/reviews/{review_id}/draft")
async def redraft(review_id: str, ctx: Ctx = Depends(rep_ctx)):
    r = await _review(ctx.ws, review_id)
    if r["reply_status"] == "replied":
        raise HTTPException(409, "You've already posted a reply to this review.")
    r = await _draft(ctx.owner, ctx.ws, r, await _business(ctx.ws))
    return view(r, await get_settings(ctx.ws))


class ReplyIn(BaseModel):
    reply_draft: str = Field(max_length=1500)


@router.patch("/reputation/reviews/{review_id}")
async def edit_reply(review_id: str, body: ReplyIn, ctx: Ctx = Depends(rep_ctx)):
    r = await _review(ctx.ws, review_id)
    if r["reply_status"] == "replied":
        raise HTTPException(409, "You've already posted this reply. Change it on Google itself.")
    text = body.reply_draft.strip()
    patch = {"reply_draft": text, "reply_status": "drafted" if text else "none"}
    if text != (r.get("reply_draft") or ""):
        patch.update(draft_by="owner", drafted_at=now(), approved_at=None, approved_by=None)   # an edited reply is approved again
    elif r["reply_status"] == "approved":
        patch["reply_status"] = "approved"
    await db().reviews.update_one({"_id": review_id, "ws": ctx.ws}, {"$set": patch})
    return view(await _review(ctx.ws, review_id), await get_settings(ctx.ws))


@router.post("/reputation/reviews/{review_id}/approve")
async def approve(review_id: str, ctx: Ctx = Depends(rep_ctx)):
    r = await _review(ctx.ws, review_id)
    if r["reply_status"] == "replied":
        raise HTTPException(409, "You've already posted a reply to this review.")
    if not (r.get("reply_draft") or "").strip():
        raise HTTPException(400, "Write or draft the reply first.")
    await db().reviews.update_one({"_id": review_id, "ws": ctx.ws}, {"$set": {"reply_status": "approved", "approved_at": now(), "approved_by": ctx.actor}})
    await track("review_reply_approved", ctx.ws)
    return view(await _review(ctx.ws, review_id), await get_settings(ctx.ws))


@router.post("/reputation/reviews/{review_id}/replied")
async def replied(review_id: str, ctx: Ctx = Depends(rep_ctx)):
    """The owner says they posted the reply on Google (or Justdial, Facebook…). The hub can't post it or check it."""
    r = await _review(ctx.ws, review_id)
    if r["reply_status"] == "replied":
        return view(r, await get_settings(ctx.ws))
    if r["reply_status"] != "approved":
        raise HTTPException(409, "Approve the reply first, then copy it and post it.")
    await db().reviews.update_one({"_id": review_id, "ws": ctx.ws}, {"$set": {"reply_status": "replied", "replied_at": now(), "replied_by": ctx.actor}})
    await track("review_replied", ctx.ws, platform=r.get("platform"))
    return view(await _review(ctx.ws, review_id), await get_settings(ctx.ws))


@router.delete("/reputation/reviews/{review_id}")
async def delete_review(review_id: str, ctx: Ctx = Depends(rep_ctx)):
    r = await _review(ctx.ws, review_id)
    if r.get("source") == "google":   # kept out of sight, so tomorrow's check doesn't bring it back
        await db().reviews.update_one({"_id": review_id, "ws": ctx.ws}, {"$set": {"hidden": True, "hidden_at": now(), "hidden_by": ctx.actor}})
    else:
        await db().reviews.delete_one({"_id": review_id, "ws": ctx.ws})
    return {"ok": True}


# ───────────────────────── referral ask (happy reviewers) ─────────────────────────
@router.post("/reputation/reviews/{review_id}/referral")
async def referral(review_id: str, ctx: Ctx = Depends(rep_ctx)):
    """The referral ask for a happy reviewer. A wa.me link to the customer's number only when the reviewer matches exactly one
    customer by full name; otherwise just the words, for the owner to send themselves."""
    r = await _review(ctx.ws, review_id)
    if not rt.is_happy(r.get("rating"), r.get("text") or ""):
        raise HTTPException(400, "A referral ask is only for a happy reviewer: 5 stars, or 4 stars with kind words.")
    b = await _business(ctx.ws)
    m = await match_customer(ctx.ws, r.get("author") or "")
    text = rt.referral_text(b, (m or {}).get("name") or r.get("author") or "", r.get("platform") or "Google")   # their name as you saved it
    await db().reviews.update_one({"_id": review_id, "ws": ctx.ws},
                                  {"$set": {"customer_id": m["customer_id"] if m else None, "lead_id": m["lead_id"] if m else None,
                                            "match_name": m["name"] if m else "", **({"referral_status": "ready"} if r.get("referral_status") == "none" else {})}})
    if m and m["opted_out"]:
        return {"matched": True, "link": None, "text": text, "to_name": m["name"], "to_phone": None,
                "note": f"{m['name']} asked not to get messages from you, so there's no link."}
    if not m:
        return {"matched": False, "link": None, "text": text, "to_name": None, "to_phone": None,
                "note": "We couldn't match this reviewer to a customer — copy the message and send it yourself."}
    return {"matched": True, "link": f"https://wa.me/{wa_digits(m['phone'])}?text={quote(text)}", "text": text, "to_name": m["name"],
            "to_phone": m["phone"], "note": None}


@router.post("/reputation/reviews/{review_id}/referral/sent")
async def referral_sent(review_id: str, ctx: Ctx = Depends(rep_ctx)):
    r = await _review(ctx.ws, review_id)
    if r.get("referral_status") != "sent":
        await db().reviews.update_one({"_id": review_id, "ws": ctx.ws}, {"$set": {"referral_status": "sent", "referral_sent_at": now()}})
        await track("review_referral_sent", ctx.ws)
    return view(await _review(ctx.ws, review_id), await get_settings(ctx.ws))


@router.post("/reputation/reviews/{review_id}/referral/skip")
async def referral_skip(review_id: str, ctx: Ctx = Depends(rep_ctx)):
    await _review(ctx.ws, review_id)
    await db().reviews.update_one({"_id": review_id, "ws": ctx.ws, "referral_status": {"$ne": "sent"}}, {"$set": {"referral_status": "skipped"}})
    return view(await _review(ctx.ws, review_id), await get_settings(ctx.ws))


# ───────────────────────── asking customers for a review ─────────────────────────
@router.get("/reputation/happy")
async def happy(ctx: Ctx = Depends(rep_ctx)):
    st, b = await get_settings(ctx.ws), await _business(ctx.ws)
    return {"items": await happy_list(ctx.ws), "review_link": review_link_of(st, b)[0] or None, "gap_days": ASK_GAP_DAYS}


class AskIn(BaseModel):
    name: str = Field(default="", max_length=80)
    phone: str = Field(min_length=6, max_length=24)


@router.post("/reputation/ask")
async def ask(body: AskIn, ctx: Ctx = Depends(rep_ctx)):
    """An honest review request — feedback good or bad, nothing offered in return — as a link that opens the owner's own
    WhatsApp with it typed. Recorded, so this number isn't suggested again for 90 days."""
    st, b = await get_settings(ctx.ws), await _business(ctx.ws)
    link, _ = review_link_of(st, b)
    if not link:
        raise HTTPException(400, "Add your review link first (or connect your Google listing), so the message can carry it.")
    phone = normalise_phone(body.phone)
    text = rt.review_ask_text(b, body.name.strip(), link)
    await db().reputation_asks.insert_one({"_id": new_id(), "ws": ctx.ws, "phone": wa_digits(phone), "name": body.name.strip()[:80],
                                           "at": now(), "by": ctx.actor})
    await track("review_asked", ctx.ws)
    return {"link": f"https://wa.me/{wa_digits(phone)}?text={quote(text)}", "text": text}


# ───────────────────────── the daily Google review watch ─────────────────────────
async def reputation_watch(owner: dict, run: dict):
    """Notices, the WhatsApp alert and the activity line say counts only — never a reviewer's name or stars, which Google's
    terms don't let the hub keep beyond 30 days (notices and the log are kept longer)."""
    ws, at = owner["_id"], run["at"]
    await forget_old_google(ws, at)
    if not google_ready():
        raise Skip("The hub's Google key isn't set up, so Google reviews can't be read.")
    st = await get_settings(ws)
    if not st.get("google_place_id"):
        raise Skip("Connect your Google listing on Reviews & reputation first.")
    try:
        got = await check_google(ws, st, at)
    except GoogleError as e:
        log.info("reputation watch for %s: %s", ws, e)
        raise HTTPException(503, "Google didn't answer this morning. We'll check again tomorrow.")
    new, quiet = got["new"], got["quiet"]
    older = (f" {len(quiet)} older Google review{'s' if len(quiet) != 1 else ''} came in without an alert — "
             f"{'it is' if len(quiet) == 1 else 'they are'} on Reviews & reputation.") if quiet else ""
    if not new:
        raise Skip(f"No new Google reviews.{older}")
    drafted, extra = 0, ""
    if st.get("auto_draft", True):
        day = at.astimezone(IST).strftime("%Y-%m-%d")
        room = max(DRAFTS_PER_DAY - await db().reviews.count_documents({"ws": ws, "auto_day": day}), 0)
        p = await _business(ws)
        for doc in new[:room]:
            try:
                await _draft(owner, ws, doc, p, auto_day=day)
                drafted += 1
            except HTTPException as e:
                if e.status_code == 402:
                    extra = " This month's AI runs are used up, so the rest wait for you to reply."
                    break
                if e.status_code == 400:
                    extra = " Fill in your Knowledge Hub so replies can be drafted."
                    break
                continue   # the AI was busy, or its draft broke a rule (run refunded): one tap drafts it on the page
    n = len(new)
    low = sum(1 for r in new if r.get("rating") and r["rating"] <= 3)
    text = f"Reviews: {n} new Google review{'s' if n != 1 else ''}" + (f", {low} {'needs' if low == 1 else 'need'} your attention." if low else ".")
    if drafted and n == 1:
        text += " A reply is drafted — approve it on Reviews & reputation."
    elif drafted:
        text += f" Replies are drafted for {'all of them' if drafted == n else f'{drafted} of them'} — approve them on Reviews & reputation."
    else:
        text += f" Reply to {'it' if n == 1 else 'them'} on Reviews & reputation."
    b = await _business(ws)
    wa = await whatsapp_template(owner["phone"], settings.aisensy_owner_alert_campaign, owner.get("name", ""), [b.get("name") or "your business", text[:900]],
                                 "owner_alert")
    await notify(ws, text[:600])
    return text + older + extra + ("" if wa["sent"] else " (Shown in the hub; WhatsApp isn't set up.)")


async def _ready(owners: list[dict]) -> set:
    if not google_ready():
        return set()
    ids = [o["_id"] for o in owners]
    return {s["_id"] async for s in db().reputation_settings.find({"_id": {"$in": ids}, "google_place_id": {"$nin": [None, ""]}}, {"_id": 1})}


catalog.register("reputation_watch", {
    "name": "Google review watch", "feature": "reputation", "trigger": "schedule", "group": "own_page",
    "what": "Every morning, checks your Google listing for new reviews, drafts a reply to each for you to approve (up to 5 a day, "
            "1 AI run each) and tells you on WhatsApp when there's something new.",
    "when": "Every day at 9:30am, once your Google listing is connected",
    "schedule": {"every": "day", "at": "09:30", "grace_hours": 8}, "ready": _ready}, reputation_watch)


async def ensure_indexes() -> None:
    d = db()
    await d.reviews.create_index([("ws", 1), ("review_date", -1), ("created_at", -1)])
    await d.reviews.create_index([("ws", 1), ("reply_status", 1)])
    await d.reviews.create_index([("ws", 1), ("auto_day", 1)])
    await d.reviews.create_index([("ws", 1), ("google_review_name", 1)], unique=True, partialFilterExpression={"source": "google"})
    await d.reputation_snapshots.create_index([("ws", 1), ("at", -1)])
    await d.reputation_asks.create_index([("ws", 1), ("at", -1)])
    await d.reputation_quota.create_index("ws")
    try:   # yesterday's counters go on their own (MongoDB 3.6+ TTL); the day is part of the key, so a late delete is harmless
        await d.reputation_quota.create_index("at", name="reputation_quota_ttl", expireAfterSeconds=3 * 86400)
    except Exception:  # noqa: BLE001
        pass
