"""
Tender finder (v2.3, Data engine): live government tenders that fit the business, from GeM (the Government e-Marketplace,
bidplus.gem.gov.in), with a link to each tender and "Help to apply" built from the tender's own bid document.

Where the tenders come from
- GeM's public "All bids" search, the same one anyone can use without logging in: up to PAGES pages (10 bids each) per
  search word, newest first, ongoing bids only. GeM matches any one of the words anywhere in a bid ("clear aligners" finds
  "pumps for clear water"), so a bid is kept only when its title has every meaningful word of the search (fits()). GeM's robots.txt allows it. At most MAX_WORDS searches per check, a short pause between
  them, and a business's results are kept for CACHE_HOURS, so one owner can't send GeM more than a few searches an hour.
- The search words come from the business profile (its offers and what it does), and the owner can change them. An AI
  token can suggest the words a government buyer would use instead.
- Applying happens on GeM itself, from the owner's GeM seller account: the hub only links to the tender.

Help to apply
- The hub downloads that one tender's bid document (a PDF on GeM, at most MAX_PDF_BYTES) and reads its English text.
- The facts every GeM bid document states in fixed labels (end date, buyer, quantity, documents asked for, EMD, past
  performance, MSE and startup relaxations, purchase preference) are read by rules here, never by the AI, so dates and
  amounts are exactly the tender's.
- The AI (1 token) compares the tender with the business profile and the identity in the Knowledge Hub (GST, Udyam, legal
  entity): what the owner already has, what to check, what's missing, the documents to keep ready and the questions to ask
  the buyer. The tender's words reach the AI only as quoted data, without email addresses or phone numbers.
- The help is kept, so opening it again costs nothing.

tender_searches {_id: ws, words [...], words_from profile|owner|ai, results [...], checked_at, error}
tender_help     {_id: "{ws}:{bid_id}", ws, bid_id, bid_number, title, facts {...}, help {...}, at}
"""
import asyncio
import io
import json
import re
from datetime import datetime, timedelta

import httpx

from .db import IST, now
from .knowledge import ENTITIES

GEM = "https://bidplus.gem.gov.in"
MAX_WORDS = 5
PAGES = 2
WORD_CHARS = 40
CACHE_HOURS = 6
PAUSE_SECONDS = 0.6          # between two searches on GeM (tests set 0)
RETRY_SECONDS = 2.0          # before asking GeM for a bid document a second time (tests set 0)
TIMEOUT = 20
MAX_PDF_BYTES = 6_000_000
MAX_PDF_PAGES = 15
AI_TEXT_CHARS = 9000         # how much of the tender's English text the AI sees
GEM_TRANSPORT = None         # tests swap in a fake GeM (httpx.MockTransport)
UA = "Mozilla/5.0 (compatible; OwnerDesk/1.0; +https://ownerdesk.io)"


class TenderError(Exception):
    """A plain sentence the owner can act on."""


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=GEM_TRANSPORT, timeout=TIMEOUT, follow_redirects=False, headers={"User-Agent": UA})


def _line(v, n: int = 200) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:n]


def _first(v):
    """GeM's search answers every field as a one-item list."""
    return v[0] if isinstance(v, list) and v else (None if isinstance(v, list) else v)


# ───────────────────────── search words ─────────────────────────
STOP = {"and", "the", "for", "with", "our", "your", "all", "services", "service", "products", "product", "solutions", "best",
        "quality", "new", "per", "a", "an", "of", "in", "to", "on", "by", "we", "us", "pvt", "ltd", "private", "limited"}


def clean_words(words: list[str]) -> list[str]:
    out = []
    for w in words or []:
        w = re.sub(r"[^\w &/.-]", " ", str(w), flags=re.U)
        w = _line(w, WORD_CHARS).strip(" -/.&")
        if len(w) >= 3 and w.lower() not in (x.lower() for x in out):
            out.append(w)
    return out[:MAX_WORDS]


def words_from_profile(b: dict) -> list[str]:
    """First search words, by rules: the owner's offer names, then what the business does — short, the way a buyer types."""
    raw = [o.get("name") for o in b.get("offers") or [] if isinstance(o, dict)]
    raw.append(re.split(r"\s+[—–-]\s+|,|\(|\s+for\s+|\s+with\s+", b.get("industry") or "")[0])
    words = []
    for r in raw:
        kept = [t for t in re.findall(r"[A-Za-z][A-Za-z&/-]*", r or "") if t.lower() not in STOP]
        if kept:
            words.append(" ".join(kept[:3]))
    return clean_words(words)


# ───────────────────────── GeM search ─────────────────────────
CSRF = re.compile(r"""['"]csrf_bd_gem_nk['"]\s*:\s*['"]([0-9a-f]{16,64})['"]""")


def _doc_url(bid_id: int, bid_type: int, eval_type: int) -> str:
    """The bid document, as GeM's own search page links it."""
    if bid_type == 5:
        return f"{GEM}/showdirectradocumentPdf/{bid_id}"
    if bid_type == 2:
        return f"{GEM}/{'list-ra-schedules' if eval_type and eval_type > 0 else 'showradocumentPdf'}/{bid_id}"
    return f"{GEM}/showbidDocument/{bid_id}"


def _when(v) -> datetime | None:
    """GeM writes India time with a "Z" on the end (its own page shows it as UTC): read it as India time."""
    try:
        return datetime.strptime(str(v)[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=IST)
    except (TypeError, ValueError):
        return None


def parse_bid(doc: dict, word: str) -> dict | None:
    bid_id = _first(doc.get("b_id"))
    number = _line(_first(doc.get("b_bid_number")), 40)
    if not isinstance(bid_id, int) or not number:
        return None
    ends = _when(_first(doc.get("final_end_date_sort")))
    starts = _when(_first(doc.get("final_start_date_sort")))
    bid_type = _first(doc.get("b_bid_type")) or 1
    ministry = _line(_first(doc.get("ba_official_details_minName")), 120)
    dept = _line(_first(doc.get("ba_official_details_deptName")), 160)
    qty = _first(doc.get("b_total_quantity"))
    return {"bid_id": bid_id, "bid_number": number, "title": _line(_first(doc.get("b_category_name")), 300) or number,
            "kind": "Reverse auction" if bid_type in (2, 5) else "Bid", "ministry": ministry, "department": dept,
            "quantity": qty if isinstance(qty, int) else None, "starts_at": starts, "ends_at": ends,
            "url": _doc_url(bid_id, bid_type, _first(doc.get("b_eval_type")) or 0), "words": [word]}


async def _csrf(client: httpx.AsyncClient) -> str:
    try:
        r = await client.get(f"{GEM}/all-bids")
    except httpx.HTTPError as e:
        raise TenderError("GeM isn't answering just now. Try again in a few minutes.") from e
    m = CSRF.search(r.text) if r.status_code == 200 else None
    if not m:
        raise TenderError("GeM's tender search has changed or isn't answering. Try again later.")
    return m.group(1)


def _terms(word: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", word.lower()) if len(t) > 2 and t not in STOP]


def fits(word: str, title: str) -> bool:
    """Every meaningful word of the search is in the bid's title ("aligners" also finds "aligner")."""
    title = title.lower()
    terms = _terms(word)
    return bool(terms) and all(t in title or (len(t) > 4 and t.rstrip("s").removesuffix("e") in title) for t in terms)


async def search_word(client: httpx.AsyncClient, token: str, word: str) -> list[dict]:
    found = []
    for page in range(1, PAGES + 1):
        if page > 1:
            await asyncio.sleep(PAUSE_SECONDS)
        payload = {"page": page, "param": {"searchBid": word, "searchType": "fullText"},
                   "filter": {"bidStatusType": "ongoing_bids", "byType": "all", "highBidValue": "", "byEndDate": {"from": "", "to": ""},
                              "sort": "Bid-Start-Date-Latest"}}
        r = await client.post(f"{GEM}/all-bids-data", data={"payload": json.dumps(payload), "csrf_bd_gem_nk": token},
                              headers={"X-Requested-With": "XMLHttpRequest", "Referer": f"{GEM}/all-bids"})
        try:
            data = r.json()
            docs = data["response"]["response"]["docs"] if r.status_code == 200 and data.get("code") == 200 else []
        except (ValueError, KeyError, TypeError):
            docs = []
        found += [b for b in (parse_bid(d, word) for d in docs if isinstance(d, dict)) if b and fits(word, b["title"])]
        if len(docs) < 10:      # the last page
            break
    return found


def rank(bids: list[dict], words: list[str], at: datetime | None = None) -> list[dict]:
    """One row per tender (a tender found by two words says so), still open, best fit first: found by more search words,
    then the one with the most time left to prepare."""
    at = at or now()
    seen: dict[int, dict] = {}
    for b in bids:
        if b["ends_at"] and b["ends_at"] <= at:
            continue
        if b["bid_id"] in seen:
            seen[b["bid_id"]]["words"] = sorted({*seen[b["bid_id"]]["words"], *b["words"]})
        else:
            seen[b["bid_id"]] = dict(b)

    def score(b):
        return (-len(b["words"]), -(b["ends_at"] or at).timestamp())
    return sorted(seen.values(), key=score)


async def search(words: list[str]) -> list[dict]:
    async with _client() as client:
        token = await _csrf(client)
        found, failed = [], 0
        for i, w in enumerate(words):
            if i:
                await asyncio.sleep(PAUSE_SECONDS)
            try:
                found += await search_word(client, token, w)
            except httpx.HTTPError:
                failed += 1
        if failed == len(words):
            raise TenderError("GeM isn't answering just now. Try again in a few minutes.")
    return rank(found, words)


# ───────────────────────── the bid document ─────────────────────────
async def bid_text(url: str) -> str:
    """The English text of one bid document (GeM writes each label in Hindi and English)."""
    if not url.startswith(GEM + "/"):
        raise TenderError("That isn't a GeM tender.")
    body = b""
    for attempt in range(2):          # GeM now and then answers one request with an error page: one more try
        if attempt:
            await asyncio.sleep(RETRY_SECONDS)
        body = await _download(url)
        if body.startswith(b"%PDF"):
            break
    if not body.startswith(b"%PDF"):
        raise TenderError("GeM didn't give us this tender's document just now. Try again in a minute, or open it on GeM.")
    return english(await asyncio.to_thread(_pdf_text, body))


async def _download(url: str) -> bytes:
    """The document's bytes, or b"" when GeM answered with anything but the document."""
    async with _client() as client:
        try:
            async with client.stream("GET", url) as r:
                if r.status_code != 200:
                    return b""
                body = bytearray()
                async for chunk in r.aiter_bytes():
                    body += chunk
                    if len(body) > MAX_PDF_BYTES:
                        raise TenderError("This tender's document is too big to read here. Open it on GeM instead.")
                return bytes(body)
        except httpx.HTTPError:
            return b""


def _pdf_text(data: bytes) -> str:
    import pypdf
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        return "\n".join(p.extract_text() or "" for p in reader.pages[:MAX_PDF_PAGES])
    except Exception as e:   # a damaged or encrypted PDF
        raise TenderError("This tender's document couldn't be read. Open it on GeM instead.") from e


def english(text: str) -> str:
    out = []
    for line in (text or "").splitlines():
        line = re.sub(r"[ऀ-ॿ]+", "", line)
        line = re.sub(r"[^\x20-\x7E₹]", "", line).strip(" /")
        if len(re.findall(r"[A-Za-z0-9]", line)) >= 2:
            out.append(re.sub(r"\s{2,}", " ", line))
    return "\n".join(out)


def _after(flat: str, label: str, value: str = r"([^\n]{1,160})") -> str:
    m = re.search(label + r"\s*:?\s*\n?" + value, flat, re.I)
    return _line(m.group(1), 300) if m else ""


def facts(text: str) -> dict:
    """What every GeM bid document states in fixed labels, read by rules (the AI never writes these)."""
    flat = re.sub(r"[ \t]+", " ", text)
    docs = re.search(r"Document required\s*\n?from seller\s*\n(.+?)\n\*?(?:In case|Do you want|Minimum)", flat, re.I | re.S)
    emd_amount = _after(flat, r"EMD Amount", r"([0-9][0-9,]*)")
    emd = re.search(r"EMD Detail\s*\n\s*Required\s*\n\s*(Yes|No)", flat, re.I)
    turnover = _after(flat, r"Minimum Average Annual\s*\n?Turnover of the bidder[^\n]*(?:\n[^\n]*Years\))?", r"([0-9][^\n]{0,40})")
    experience = _after(flat, r"Years of Past Experience Required for\s*\n?same/similar service", r"([^\n]{1,40})") or \
        _after(flat, r"Years of Past Experience Required", r"([^\n]{1,40})")
    out = {
        "ends": _after(flat, r"Bid End Date/Time", r"(\d{2}-\d{2}-\d{4} \d{2}:\d{2}(?::\d{2})?)"),
        "opens": _after(flat, r"Bid Opening\s*\n?Date/Time", r"(\d{2}-\d{2}-\d{4} \d{2}:\d{2}(?::\d{2})?)"),
        "ministry": _after(flat, r"Ministry/State Name"),
        "department": _after(flat, r"Department Name"),
        "organisation": _after(flat, r"Organisation Name"),
        "quantity": _after(flat, r"Total Quantity", r"([0-9][0-9,]*)"),
        "category": _after(flat, r"Item Category"),
        "estimated_value": _after(flat, r"Estimated Bid Value", r"([0-9][0-9,]*)"),
        "bid_type": _after(flat, r"Type of Bid"),
        "documents": _line(docs.group(1), 600) if docs else "",
        "past_performance": _after(flat, r"Past Performance", r"(\d{1,3} ?%)"),
        "turnover": turnover,
        "experience": experience,
        "mse_relaxation": _after(flat, r"MSE Relaxation for Years of\s*\n?Experience and Turnover", r"(Yes|No)"),
        "startup_relaxation": _after(flat, r"Startup Relaxation for Years of\s*\n?Experience and Turnover", r"(Yes|No)"),
        "emd_required": "Yes" if emd_amount else emd.group(1).title() if emd else "",
        "emd_amount": emd_amount,
        "mse_preference": _after(flat, r"MSE Purchase Preference\s*\n?MSE Purchase Preference", r"(Yes|No)"),
        "inspection": _after(flat, r"Inspection Required[\s\S]{0,120}?GeM\)", r"(Yes|No)"),
        "payment_days": _after(flat, r"Payments shall be made to the Seller within", r"(\d{1,3})"),
        "treds": "Yes" if re.search(r"TReDS", flat) else "",
    }
    return {k: v for k, v in out.items() if v and not re.fullmatch(r"n/?a|-+|\**|nil", v.strip(), re.I)}


PRIVATE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+|(?:\+?91[\s-]?)?\b[6-9]\d{9}\b|\b0\d{2,4}[\s-]?\d{6,8}\b")


def for_ai(text: str) -> str:
    """The tender's words as the AI sees them: no email addresses or phone numbers, and not too long."""
    return PRIVATE.sub("[removed]", text)[:AI_TEXT_CHARS]


def buyer_emails(text: str) -> list[str]:
    """The buyer's own contact addresses, as printed in the tender (for the owner's questions to the buyer)."""
    found = re.findall(r"(?:HOD|Buyer) Email id\s*:\s*([\w.+-]+@[\w-]+\.[\w.-]+)", text, re.I)
    return list(dict.fromkeys(e.lower().rstrip(".") for e in found))[:2]


# ───────────────────────── what the owner already has ─────────────────────────
def readiness(b: dict) -> list[dict]:
    """What the Knowledge Hub says about the papers most tenders ask for. "unknown" means the Knowledge Hub doesn't say."""
    ident = b.get("identity") or {}
    regs = set(ident.get("registrations") or [])
    gst = ident.get("gst_registered")
    return [
        {"key": "gst", "label": "GST registration", "status": "have" if gst and ident.get("gstin") else "missing" if gst is False else "unknown",
         "note": ident.get("gstin") or ""},
        {"key": "pan", "label": "PAN of the business", "status": "have" if ident.get("pan") else "unknown", "note": ""},
        {"key": "udyam", "label": "Udyam (MSME) registration", "status": "have" if "udyam" in regs else "unknown",
         "note": "Gives MSE purchase preference and, in many tenders, no EMD and relaxed experience and turnover."},
        {"key": "entity", "label": "Legal entity", "status": "have" if ident.get("entity") else "unknown",
         "note": dict(ENTITIES).get(ident.get("entity"), ident.get("entity") or "")},
    ]


def days_left(ends_at: datetime | None, at: datetime | None = None) -> int | None:
    if not ends_at:
        return None
    return max(0, (ends_at.astimezone(IST).date() - (at or now()).astimezone(IST).date()).days)


def stale(checked_at: datetime | None, at: datetime | None = None) -> bool:
    return not checked_at or (at or now()) - checked_at > timedelta(hours=CACHE_HOURS)


# How applying on GeM works, the same for every bid (written here, not by the AI).
GEM_STEPS = [
    "Register as a seller on GeM (gem.gov.in) with your PAN, Aadhaar of the authorised person, GST number and bank account, if you haven't yet.",
    "Complete your seller profile and add Udyam (MSME) details, so the MSE benefits in the tender apply to you.",
    "List the product or service in the tender's category on GeM (a catalogue entry), or check the one you have matches.",
    "Log in to GeM, open Bids → search the bid number, and press Participate before the bid end date and time.",
    "Upload the documents the tender asks for, fill in the technical details and your price, then sign and submit the bid.",
    "Watch the bid on GeM for clarification questions from the buyer and for the technical and price results.",
]
LINKS = [
    {"label": "GeM seller registration", "url": "https://gem.gov.in/"},
    {"label": "Udyam (MSME) registration", "url": "https://udyamregistration.gov.in/"},
]


# ───────────────────────── checking the AI's answers ─────────────────────────
def shape_words(result: dict) -> list[str]:
    words = clean_words(result.get("words") if isinstance(result.get("words"), list) else [])
    if not words:
        raise ValueError("no search words")
    return words


def _items(v, n: int, chars: int = 240) -> list[str]:
    return [x for x in (_line(i, chars) for i in (v if isinstance(v, list) else [])[:n * 2]) if x][:n]


def shape_help(result: dict) -> dict:
    fit = result.get("fit") if isinstance(result.get("fit"), dict) else {}
    verdict = str(fit.get("verdict") or "").lower()
    reqs = []
    for r in (result.get("requirements") if isinstance(result.get("requirements"), list) else [])[:12]:
        if isinstance(r, dict) and _line(r.get("need")):
            status = str(r.get("status") or "").lower()
            reqs.append({"need": _line(r.get("need"), 200), "status": status if status in ("have", "check", "missing") else "check",
                         "note": _line(r.get("note"), 240)})
    out = {"summary": _line(result.get("summary"), 400), "fit": {"verdict": verdict if verdict in ("good", "maybe", "hard") else "maybe",
                                                                  "why": _line(fit.get("why"), 300)},
           "requirements": reqs[:8], "documents": _items(result.get("documents"), 10, 160),
           "questions": _items(result.get("questions"), 3), "watch_out": _items(result.get("watch_out"), 3)}
    if not out["summary"] or not (out["requirements"] or out["documents"]):
        raise ValueError("help too thin")
    return out
