"""
Call coach (v2.4, Sales engine): a call, pasted or recorded, scored out of 10 with what went well and 3 fixes, for each
person on the team.

- Team members: names the owner types once (no logins), in `call_team` {_id, ws, name, removed_at, at, by}. Removing a
  member keeps their past calls under their name; adding the same name again brings them back with their history.
- A pasted call: lines like "Customer: … / Me: …", or "<name>: …" lines (the team member's own name counts as the
  salesperson). Scoring is 1 AI token: chat_tasks.score_conversation + score_shape, the Sales Training Gym's scorer.
- A recording: read into memory only (never written to disk or to the database), written out by the speech engine
  (transcribe.transcribe), and each line marked Customer or Salesperson (ai/callcoach_tasks.label_lines): 1 AI token. The
  written call waits in `call_drafts` {_id, ws, member_id, member, lines, labelled_by, at, by} — deleted once scored, or
  after 7 days — while the owner fixes labels and words. Scoring is the second AI token.
- A scored call is saved in `gym_sessions` with kind "call", source "call_coach", member, member_id, scored_at and the
  fields the Gym's score-call writes, so the old Gym page shows it too. Each fix gets a line from the call it is about,
  quoted word for word and picked by code (the reused scorer doesn't return one): a line the fix itself quotes, else the line
  sharing the most words with the fix, else a line found by what the fix is about (the end of the call for a next step…).
- Per person: scores over time, and the last 5 calls' average against the 5 before. The team: this month's criteria,
  lowest average first, with how many fixes were about each.
"""
import logging
import re
from datetime import datetime, timedelta

from fastapi import HTTPException

from . import growth_core
from . import transcribe as speech
from .ai import callcoach_tasks, chat_tasks
from .ai.provider import live
from .config import settings
from .db import IST, db, now
from .services import new_id, run_ai, track

log = logging.getLogger("hub")

ROLE = "call_coach"
DRAFT_DAYS = 7
MAX_MEMBERS = 50
MAX_LINES = 400          # lines kept from a written-out call or sent back for scoring
MAX_TURNS = 200          # turns saved with a scored call (the scorer reads the last 40)
MAX_TEXT = 40_000        # characters of a pasted or written-out call
MB = 1024 * 1024
COST = {"paste": 1, "recording": 2}
NO_SPEECH = ("Recordings need a server setting (a speech-to-text key) that isn't switched on in this hub yet. "
             "Pasted calls still work — paste the call as text.")
ALLOWED_LINE = "Only upload calls you are allowed to record."
NOT_KEPT = "The recording is not kept; only the written call is saved."

# the audio types transcribe.py accepts, and the file endings a phone may send without a type
AUDIO_TYPES = set(speech.EXTENSIONS)
EXT_TYPES = {"mp3": "audio/mpeg", "mpeg": "audio/mpeg", "mpga": "audio/mpga", "m4a": "audio/mp4", "mp4": "audio/mp4", "aac": "audio/aac",
             "wav": "audio/wav", "ogg": "audio/ogg", "oga": "audio/ogg", "opus": "audio/ogg", "webm": "audio/webm", "flac": "audio/flac"}


def max_bytes() -> int:
    """25 MB a recording (the speech engine's own limit). With only a Gemini key the file travels inside the request as text
    (a third bigger) and Gemini takes at most 20 MB a request, so recordings stop at 13 MB there."""
    if not settings.openai_api_key and settings.gemini_api_key:
        return 13 * MB
    return 25 * MB


def audio_type(content_type: str, ext: str = "") -> str | None:
    mime = (content_type or "").split(";")[0].strip().lower()
    if mime in AUDIO_TYPES:
        return mime
    if mime in ("", "application/octet-stream", "binary/octet-stream"):
        return EXT_TYPES.get((ext or "").strip().lower().lstrip("."))
    return None


def fmt(n: float | int | None) -> str:
    return "" if n is None else f"{float(n):g}"


def month_start(at: datetime | None = None) -> datetime:
    local = (at or now()).astimezone(IST)
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _iso(v):
    return v.isoformat() if isinstance(v, datetime) else v


# ───────────────────────── team members ─────────────────────────
def clean_name(name: str) -> str:
    name = re.sub(r"\s+", " ", str(name or "")).strip()
    if len(name) < 2 or len(name) > 40:
        raise HTTPException(400, "Type a name of 2 to 40 letters.")
    if not re.search(r"[^\W\d_]", name):
        raise HTTPException(400, "Type the person's name.")
    return name


def member_view(m: dict) -> dict:
    return {"id": m["_id"], "name": m["name"], "removed": bool(m.get("removed_at")), "added_at": _iso(m.get("at"))}


async def members(ws: str) -> list[dict]:
    return [member_view(m) async for m in db().call_team.find({"ws": ws, "removed_at": None}).sort([("name", 1)])]


async def member_of(ws: str, mid: str) -> dict:
    m = await db().call_team.find_one({"_id": mid, "ws": ws, "removed_at": None}) if mid else None
    if not m:
        raise HTTPException(404, "Pick who made the call. Add their name to your team list first.")
    return m


async def add_member(ws: str, name: str, by: str) -> dict:
    name = clean_name(name)
    same = re.compile(f"^{re.escape(name)}$", re.I)
    if await db().call_team.find_one({"ws": ws, "removed_at": None, "name": same}):
        raise HTTPException(409, f"{name} is already on your team list.")
    old = await db().call_team.find_one({"ws": ws, "removed_at": {"$ne": None}, "name": same})
    if await db().call_team.count_documents({"ws": ws, "removed_at": None}) >= MAX_MEMBERS:
        raise HTTPException(400, f"Your team list can have up to {MAX_MEMBERS} names. Remove someone first.")
    if old:   # the same person back again: their past calls come back with them
        await db().call_team.update_one({"_id": old["_id"]}, {"$set": {"removed_at": None, "name": name}})
        await db().gym_sessions.update_many({"ws": ws, "member_id": old["_id"]}, {"$set": {"member": name}})
        return member_view(await db().call_team.find_one({"_id": old["_id"]}))
    doc = {"_id": new_id(), "ws": ws, "name": name, "removed_at": None, "at": now(), "by": by}
    await db().call_team.insert_one(doc)
    return member_view(doc)


async def rename_member(ws: str, mid: str, name: str) -> dict:
    m = await member_of(ws, mid)
    name = clean_name(name)
    if await db().call_team.find_one({"ws": ws, "removed_at": None, "_id": {"$ne": mid}, "name": re.compile(f"^{re.escape(name)}$", re.I)}):
        raise HTTPException(409, f"{name} is already on your team list.")
    await db().call_team.update_one({"_id": m["_id"]}, {"$set": {"name": name}})
    # the same person, spelt differently: their past calls and waiting calls show the new name
    await db().gym_sessions.update_many({"ws": ws, "member_id": mid}, {"$set": {"member": name}})
    await db().call_drafts.update_many({"ws": ws, "member_id": mid}, {"$set": {"member": name}})
    return member_view(await db().call_team.find_one({"_id": mid}))


async def remove_member(ws: str, mid: str) -> None:
    m = await member_of(ws, mid)
    await db().call_team.update_one({"_id": m["_id"]}, {"$set": {"removed_at": now()}})


# ───────────────────────── a pasted call ─────────────────────────
_STAMP = re.compile(r"^\s*(?:\[?\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4},?\s+\d{1,2}:\d{2}(?::\d{2})?\s*(?:[ap]\.?\s?m\.?)?\]?\s*(?:[-–]\s*)?"
                    r"|\[\d{1,2}:\d{2}(?::\d{2})?\]\s*|\(\d{1,2}:\d{2}(?::\d{2})?\)\s*|\d{1,2}:\d{2}(?::\d{2})?\s*[-–]\s*)", re.I)
_KNOWN = re.compile(r"^(customer|client|cust|c|buyer|lead|agent|me|sales|salesperson|s|caller|ai|telecaller)\s*[:\-–]\s*(.+)$", re.I)
_NAMED = re.compile(r"^([^:：\n]{1,30}?)\s*[:：]\s*(.+)$")
_CUSTOMER_WORDS = {"customer", "client", "cust", "c", "buyer", "lead"}


def _label_ok(label: str, rest: str) -> bool:
    """'Ravi', 'Mr Shah', 'Speaker 1' are speaker labels; 'Note', 'Price 5000', 'https' (a link) look like text."""
    if rest.startswith("//") or re.search(r"[?!,.;\"“”()\[\]/]", label):
        return False
    if re.match(r"^(?:speaker|person|caller|spk)\s*\d{1,2}$", label, re.I):
        return True
    return 1 <= len(label.split()) <= 3 and bool(re.match(r"^[^\W\d_]", label)) and not re.search(r"\d", label)


def _speaker_key(label: str) -> str:
    return re.sub(r"\s+", " ", label).strip().lower()


def paste_turns(text: str, member: str, team: list[str] | None = None) -> dict:
    """A pasted call → {"turns", "unsure", "labels"}. Lines start with a speaker: Customer/Me (as the Gym reads them), the team
    member's own name (or anyone on the team list) for the salesperson, or any other name. Other names are the customer
    when the salesperson is known, the salesperson when only the customer is. When neither side can be told apart, the
    first speaker is taken as the salesperson and `unsure` is set (scoring then asks the owner to check)."""
    text = str(text or "")[:MAX_TEXT]
    me = _speaker_key(member)
    first = me.split()[0] if me else ""
    ours = {me, *(n for n in (_speaker_key(t) for t in team or []) if n)}
    ours_first = {n.split()[0] for n in ours if n and len(n.split()[0]) >= 3}
    rows: list[tuple[str | None, str]] = []   # (speaker key or None for a continuation, text)
    shown: dict[str, str] = {}                 # speaker key → the label as typed
    for raw in text.splitlines():
        line = _STAMP.sub("", raw.strip(), count=1).strip()
        if not line:
            continue
        m = _KNOWN.match(line)
        if m:
            rows.append(("=" + ("customer" if m.group(1).lower() in _CUSTOMER_WORDS else "salesperson"), m.group(2).strip()))
            continue
        m = _NAMED.match(line)
        if m and _label_ok(m.group(1).strip(), m.group(2).strip()):
            key = _speaker_key(m.group(1))
            if key == me or key in ours or (first and key.split()[0] == first) or key.split()[0] in ours_first:
                rows.append(("=salesperson", m.group(2).strip()))
            else:
                shown.setdefault(key, re.sub(r"\s+", " ", m.group(1)).strip())
                rows.append((key, m.group(2).strip()))
            continue
        rows.append((None, line))
    others = list(dict.fromkeys(k for k, _ in rows if k and not k.startswith("=")))
    has_s = any(k == "=salesperson" for k, _ in rows)
    has_c = any(k == "=customer" for k, _ in rows)
    unsure = False
    side: dict[str, str] = {}
    if others:
        if has_s:
            side = {k: "customer" for k in others}
        elif has_c:
            side = {k: "salesperson" for k in others}
        else:   # two (or more) names, none of them on the team list: a guess the owner must check
            unsure = True
            side = {k: ("salesperson" if i == 0 else "customer") for i, k in enumerate(others)}
    # written in the Gym's own format and read by its reader, so both pages read a call the same way
    out = []
    for k, t in rows:
        if k is None:
            out.append(t)
        else:
            role = k[1:] if k.startswith("=") else side[k]
            out.append(f"{'Customer' if role == 'customer' else 'Me'}: {t}")
    turns = chat_tasks.call_lines("\n".join(out))
    return {"turns": turns, "unsure": unsure, "labels": [shown[k] for k in others]}


def merge(turns: list[dict]) -> list[dict]:
    """Lines in a row from the same person become one turn."""
    out: list[dict] = []
    for t in turns:
        text = re.sub(r"\s+", " ", str(t.get("text") or "")).strip()
        if not text:
            continue
        if out and out[-1]["role"] == t["role"]:
            out[-1]["text"] = f"{out[-1]['text']} {text}"[:4000]
        else:
            out.append({"role": t["role"], "text": text[:4000]})
    return out[:MAX_TURNS]


def check_turns(turns: list[dict], member: str) -> None:
    roles = {t["role"] for t in turns}
    if "salesperson" not in roles or "customer" not in roles:
        raise HTTPException(400, f"Mark at least one line as the customer's and one as {member}'s, then score the call.")
    if len(turns) < 2:
        raise HTTPException(400, "Paste the call with who said what, like “Customer: … / Me: …”.")


def as_text(turns: list[dict]) -> str:
    """The call as "Customer: …" / "Salesperson: …" lines (what "Add this call to the deal" sends)."""
    return "\n".join(f"{'Customer' if t['role'] == 'customer' else 'Salesperson'}: {t['text']}" for t in turns)


# ───────────────────────── a recording ─────────────────────────
_SENT = re.compile(r"(?<=[.?!।॥])\s+")
_TAG = re.compile(r"^\s*\[?((?:speaker|person|spk)\s*[\w]{1,3})\]?\s*[:\-–]\s*", re.I)


def split_lines(text: str) -> tuple[list[str], list[str]]:
    """Written-out speech → short lines (one sentence each, long ones cut near 30 words) and any speaker tag the speech
    engine put in front ("Speaker 1: …"), which is taken off the words and kept as a hint for the labels."""
    lines: list[str] = []
    tags: list[str] = []
    for para in str(text or "")[:MAX_TEXT].splitlines():
        para = para.strip()
        if not para:
            continue
        tag = ""
        m = _TAG.match(para)
        if m:
            tag = re.sub(r"\s+", " ", m.group(1)).strip().title()
            para = para[m.end():].strip()
        for sent in _SENT.split(para):
            words = sent.split()
            while words:
                chunk, words = words[:30], words[30:]
                lines.append(" ".join(chunk))
                tags.append(tag)
    return lines[:MAX_LINES], tags[:MAX_LINES]


class NothingHeard(Exception):
    """The speech engine returned no words."""


async def _write_out(p: dict, member: str, data: bytes, mime: str):
    """The first AI token of a recording: speech → text → who said each line. Returns ({"lines", "tags", "raw"}, usage)."""
    ext = speech.EXTENSIONS.get(mime, "webm")
    text = await speech.transcribe(data, f"call.{ext}", mime)
    lines, tags = split_lines(text)
    if not lines:
        raise NothingHeard()
    raw, usage = await callcoach_tasks.label_lines(p, member, lines, tags)
    return {"lines": lines, "tags": tags, "raw": raw}, usage


def _labelled(result: dict, member: str, business: str) -> dict:
    lines, tags = result["lines"], result["tags"]
    try:
        roles, by = callcoach_tasks.clean_labels(result["raw"], len(lines)), ("ai" if live() else "rules")
    except ValueError:   # the AI's labels can't be used: the call is still written out, labelled by the simple rules
        roles, by = callcoach_tasks.rules_labels(lines, member, business, tags), "rules"
    return {"lines": [{"role": r, "text": t} for r, t in zip(roles, lines)], "labelled_by": by}


async def calls_left(owner: dict) -> None:
    """Before a recording is written out: refuse when no call can be scored this month (so no token is spent on it)."""
    left = await growth_core.left(owner, "calls_scored")
    if left["limit"] <= 0:
        raise HTTPException(403, "Your plan doesn't include calls scored.")
    if left["left"] <= 0:
        raise HTTPException(429, f"You've used this month's {left['limit']} calls scored. More open on the 1st.")


async def write_out(owner: dict, actor: str, p: dict, member: dict, data: bytes, mime: str) -> dict:
    """A recording → a draft for the owner to check. 1 AI token, refunded if the speech engine or the AI fails."""
    try:
        async with growth_core.working(owner["_id"], ROLE):
            got = await run_ai(owner, "call_writeout", _write_out(p, member["name"], data, mime), f"Call written out: {member['name']}",
                               save_output=False, check=lambda r: _labelled(r, member["name"], p.get("name", "")))
    except NothingHeard:
        raise HTTPException(400, "We couldn't hear anyone speaking in that recording. Your AI token was not used.")
    except speech.TranscribeError as e:
        log.warning("call recording not written out, token refunded: %s", str(e)[:300])
        raise HTTPException(502, "We couldn't write out that recording right now. Your AI token was not used — please try again.")
    except HTTPException as e:
        if e.status_code == 503:   # no speech engine on this server (the engine's own message names the company)
            raise HTTPException(503, NO_SPEECH)
        raise
    doc = {"_id": new_id(), "ws": owner["_id"], "member_id": member["_id"], "member": member["name"], "lines": got["lines"],
           "labelled_by": got["labelled_by"], "at": now(), "by": actor}
    await db().call_drafts.insert_one(doc)
    await track("call_written_out", owner["_id"], lines=len(got["lines"]))
    return draft_view(doc)


def draft_view(d: dict) -> dict:
    return {"id": d["_id"], "member_id": d.get("member_id"), "member": d.get("member", ""), "lines": d.get("lines") or [],
            "labelled_by": d.get("labelled_by", "rules"), "at": _iso(d.get("at")),
            "until": _iso(d["at"] + timedelta(days=DRAFT_DAYS)) if isinstance(d.get("at"), datetime) else None}


async def drafts(ws: str) -> list[dict]:
    since = now() - timedelta(days=DRAFT_DAYS)
    return [{"id": d["_id"], "member": d.get("member", ""), "member_id": d.get("member_id"), "lines": len(d.get("lines") or []),
             "at": _iso(d.get("at"))}
            async for d in db().call_drafts.find({"ws": ws, "at": {"$gte": since}}, {"lines": 1, "member": 1, "member_id": 1, "at": 1})
            .sort("at", -1).limit(10)]


async def draft_of(ws: str, did: str) -> dict:
    d = await db().call_drafts.find_one({"_id": did, "ws": ws, "at": {"$gte": now() - timedelta(days=DRAFT_DAYS)}}) if did else None
    if not d:
        raise HTTPException(404, "That written-out call isn't here any more. Upload the recording again.")
    return d


# ───────────────────────── the moments the fixes are about ─────────────────────────
THEMES = [   # (criterion, words that show a fix is about it), in the order ties are settled
    ("Clear next step", ("next step", "date", "book", "visit", "follow", "confirm", "close", "meeting", "schedule", "appointment",
                         "end the call", "end every call", "commit", "deadline")),
    ("Value before price", ("value", "include", "benefit", "quality", "warranty", "explain", "before price", "before the price",
                            "result", "story", "proof", "example")),
    ("Handling the objection", ("objection", "discount", "costly", "expensive", "cheaper", "competitor", "other vendor",
                                "another vendor", "worry", "concern", "think about it", "push", "hold the price", "hold your price",
                                "price")),
    ("Listening and questions", ("question", "ask", "listen", "understand", "need", "requirement", "why", "repeat", "worry back")),
]
OBJECTION = re.compile(r"\b(?:costly|expensive|cheaper|too much|too high|high|less|lower|quoted|discount|budget|kam|think about|"
                       r"discuss|not sure|later)\b", re.I)
PRICE_TALK = re.compile(r"₹|\b(?:price|prices|rate|rates|cost|costly|expensive|discount|cheaper|budget|kitna|kitne|quote|quoted|"
                        r"quotation|rupees|rs|lakh|lakhs|less)\b", re.I)
_SENT_ANY = re.compile(r"(?<=[.?!।॥])\s+")
_QUOTED = re.compile(r"“([^”]{6,240})”|\"([^\"]{6,240})\"|‘([^’]{6,240})’")
STOP = {"that", "this", "with", "your", "they", "them", "their", "what", "when", "next", "time", "about", "before", "after", "into",
        "from", "have", "will", "would", "could", "should", "just", "more", "than", "then", "there", "here", "also", "only", "very",
        "each", "every", "call", "customer", "customers", "salesperson", "said", "says", "tell", "told", "like", "make", "sure",
        "need", "needs", "want", "wants", "give", "gave", "were", "been", "being", "does", "dont", "didn", "instead", "them", "theirs",
        "yourself", "always", "never", "first", "talk", "talked", "asked"}


def theme_of(text: str) -> str | None:
    low = (text or "").lower()
    best, hits = None, 0
    for name, words in THEMES:
        n = sum(1 for w in words if w in low)
        if n > hits:
            best, hits = name, n
    return best


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s₹%]", " ", (s or "").lower())).strip()


def _words(s: str) -> set[str]:
    return {w.rstrip("s") for w in _norm(s).split() if len(w) >= 4 and w not in STOP}


def _sentences(turns: list[dict]) -> list[dict]:
    out = []
    for i, t in enumerate(turns):
        for s in _SENT_ANY.split(t["text"]):
            s = s.strip()
            if len(s) >= 2:
                out.append({"turn": i, "who": t["role"], "text": s if len(s) <= 240 else s[:239].rstrip() + "…"})
    return out


def _moment(sents: list[dict], k: int, how: str) -> dict:
    """The line at k, with the other person's line next to it so the owner sees the exchange."""
    hit = sents[k]
    lines = [{**hit, "hit": True}]
    if hit["who"] == "salesperson":
        prev = next((sents[j] for j in range(k - 1, -1, -1) if sents[j]["who"] == "customer"), None)
        if prev and hit["turn"] - prev["turn"] <= 1:
            lines.insert(0, {**prev, "hit": False})
    else:
        nxt = next((sents[j] for j in range(k + 1, len(sents)) if sents[j]["who"] == "salesperson"), None)
        if nxt and nxt["turn"] - hit["turn"] <= 1:
            lines.append({**nxt, "hit": False})
    return {"turn": hit["turn"], "how": how, "lines": [{"who": l["who"], "text": l["text"], "hit": l["hit"]} for l in lines]}


def find_moment(fix: str, sents: list[dict]) -> dict | None:
    if not sents:
        return None
    # 1. a line the fix quotes
    for m in _QUOTED.finditer(fix or ""):
        q = _norm(next(g for g in m.groups() if g))
        if len(q) >= 6:
            for k, s in enumerate(sents):
                if q in _norm(s["text"]):
                    return _moment(sents, k, "quoted")
    # 2. the salesperson's line sharing the most words with the fix (at least two)
    fw = _words(fix)
    best, score = None, 1
    for k, s in enumerate(sents):
        if s["who"] != "salesperson":
            continue
        n = len(fw & _words(s["text"]))
        if n > score:
            best, score = k, n
    if best is not None:
        return _moment(sents, best, "words")
    # 3. by what the fix is about
    theme = theme_of(fix)
    sales = [k for k, s in enumerate(sents) if s["who"] == "salesperson"]
    if not sales:
        return None
    if theme == "Clear next step":
        return _moment(sents, sales[-1], "end")
    if theme == "Value before price":   # the first time the salesperson named a price
        said = next((k for k in sales if PRICE_TALK.search(sents[k]["text"])), None)
        if said is not None:
            return _moment(sents, said, "price")
    if theme in ("Handling the objection", "Value before price"):   # the customer's push back, and the salesperson's answer
        push = next((k for k, s in enumerate(sents) if s["who"] == "customer" and OBJECTION.search(s["text"])), None)
        if push is None:
            push = next((k for k, s in enumerate(sents) if s["who"] == "customer" and PRICE_TALK.search(s["text"])), None)
        if push is None:
            return None
        reply = next((k for k in sales if k > push), None)
        return _moment(sents, reply if reply is not None else push, "objection")
    if theme == "Listening and questions":
        first_c = next((k for k, s in enumerate(sents) if s["who"] == "customer"), -1)
        after = next((k for k in sales if k > first_c), sales[0])
        return _moment(sents, after, "start")
    return None


def with_moments(raw: dict, turns: list[dict]) -> dict:
    """The scorer's answer (checked by chat_tasks.score_shape), and a line from the call for each fix."""
    score = chat_tasks.score_shape(raw)
    sents = _sentences(turns)
    score["moments"] = [find_moment(f, sents) for f in score["fixes"]]
    return score


# ───────────────────────── scoring ─────────────────────────
async def score(owner: dict, actor: str, p: dict, member: dict, turns: list[dict], source: str) -> dict:
    """1 AI token and one of the month's calls scored (both given back if the AI fails). Saved for the Gym page too."""
    await growth_core.take(owner, "calls_scored")
    ws = owner["_id"]
    try:
        async with growth_core.working(ws, ROLE):
            result = await run_ai(owner, "call_score", chat_tasks.score_conversation(p, turns, "call"), f"Call score: {member['name']}",
                                  save_output=False, check=lambda r: with_moments(r, turns))
            at = now()
            doc = {"_id": new_id(), "ws": ws, "user_id": actor, "kind": "call", "scenario": "call", "title": f"Call by {member['name']}",
                   "messages": turns, "status": "scored", "score": result, "call_id": None, "created_at": at, "scored_at": at,
                   "source": "call_coach", "member": member["name"], "member_id": member["_id"], "input": source}
            await db().gym_sessions.insert_one(doc)
            await growth_core.log_action(ws, ROLE, f"Scored a call by {member['name']}: {fmt(result['overall'])}/10", ref=f"call:{doc['_id']}")
    except BaseException:
        await growth_core.give_back(owner, "calls_scored")
        raise
    await track("call_scored", ws, overall=result["overall"], input=source)
    return doc


def call_view(s: dict, filed: list[dict] | None = None) -> dict:
    sc = s.get("score") or {}
    lines = s.get("messages") or []
    return {"id": s["_id"], "member": s.get("member", ""), "member_id": s.get("member_id"), "input": s.get("input", "paste"),
            "title": s.get("title", ""), "scored_at": _iso(s.get("scored_at")), "created_at": _iso(s.get("created_at")),
            "score": {"overall": sc.get("overall"), "scores": sc.get("scores") or [], "fixes": sc.get("fixes") or [],
                      "best": sc.get("best", ""), "moments": sc.get("moments") or [None] * len(sc.get("fixes") or [])},
            "lines": lines, "text": as_text(lines), "filed": filed or []}


def call_summary(s: dict) -> dict:
    sc = s.get("score") or {}
    return {"id": s["_id"], "member": s.get("member", ""), "member_id": s.get("member_id"), "input": s.get("input", "paste"),
            "overall": sc.get("overall"), "scored_at": _iso(s.get("scored_at")), "fix": (sc.get("fixes") or [""])[0]}


CALLS = {"kind": "call", "source": "call_coach", "status": "scored"}


async def filed_to(ws: str, call_id: str) -> list[dict]:
    """The deals this call was added to (Pipeline fills itself writes deal_updates with the call's id)."""
    ups = [u async for u in db().deal_updates.find({"ws": ws, "call_id": call_id}, {"lead_id": 1, "at": 1, "state": 1}).sort("at", -1).limit(5)]
    names = {}
    ids = [u["lead_id"] for u in ups if u.get("lead_id")]
    if ids:
        names = {l["_id"]: l.get("name") or "Customer" async for l in db().leads.find({"_id": {"$in": ids}, "owner_id": ws}, {"name": 1})}
    return [{"lead_id": u.get("lead_id"), "deal": names.get(u.get("lead_id"), ""), "at": _iso(u.get("at")), "state": u.get("state")} for u in ups]


# ───────────────────────── per person, and the team ─────────────────────────
def _avg(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 1) if xs else None


async def people(ws: str) -> dict:
    team = {m["_id"]: m async for m in db().call_team.find({"ws": ws})}
    groups: dict[str, dict] = {}
    for mid, m in team.items():
        if not m.get("removed_at"):
            groups[mid] = {"member_id": mid, "name": m["name"], "removed": False, "calls": []}
    async for s in db().gym_sessions.find({"ws": ws, **CALLS}, {"member": 1, "member_id": 1, "score.overall": 1, "scored_at": 1}) \
            .sort("scored_at", -1).limit(3000):
        mid = s.get("member_id")
        key = mid if mid in team else f"name:{(s.get('member') or '').lower()}"
        if key not in groups:
            m = team.get(mid)
            groups[key] = {"member_id": mid if m else None, "name": (m or {}).get("name") or s.get("member") or "Someone",
                           "removed": bool(not m or m.get("removed_at")), "calls": []}
        overall = (s.get("score") or {}).get("overall")
        if overall is not None:
            groups[key]["calls"].append({"id": s["_id"], "overall": overall, "scored_at": _iso(s.get("scored_at"))})
    out = []
    for g in groups.values():
        scores = [c["overall"] for c in g["calls"]]   # newest first
        last, prev = scores[:5], scores[5:10]
        la, pa = _avg(last), _avg(prev)
        trend = None
        if la is not None and pa is not None:
            trend = "up" if la - pa >= 0.5 else "down" if pa - la >= 0.5 else "same"
        out.append({**g, "count": len(scores), "last5": la, "last_n": len(last), "prev5": pa, "prev_n": len(prev), "trend": trend,
                    "calls": g["calls"][:30]})
    out.sort(key=lambda g: (g["removed"], -g["count"], g["name"].lower()))
    return {"people": out, "team": await team_month(ws)}


async def team_month(ws: str) -> dict:
    """This month's scored calls: each criterion's average, lowest first, and how many of the fixes were about it."""
    start = month_start()
    crit: dict[str, dict] = {}
    themes: dict[str, int] = {}
    examples: dict[str, str] = {}
    n, total = 0, 0.0
    async for s in db().gym_sessions.find({"ws": ws, **CALLS, "scored_at": {"$gte": start}}, {"score": 1}).sort("scored_at", -1):
        sc = s.get("score") or {}
        if sc.get("overall") is None:
            continue
        n += 1
        total += float(sc["overall"])
        for row in sc.get("scores") or []:
            name = str(row.get("criterion") or "").strip()
            if not name:
                continue
            c = crit.setdefault(name.lower(), {"criterion": name, "sum": 0.0, "calls": 0})
            c["sum"] += float(row.get("score") or 0)
            c["calls"] += 1
        for f in sc.get("fixes") or []:
            t = (theme_of(f) or "other").lower()
            themes[t] = themes.get(t, 0) + 1
            examples.setdefault(t, f)
    rows = [{"criterion": c["criterion"], "average": round(c["sum"] / c["calls"], 1), "calls": c["calls"],
             "fixes": themes.get(key, 0), "example": examples.get(key, "")} for key, c in crit.items()]
    rows.sort(key=lambda r: (r["average"], -r["fixes"]))
    other = sum(v for k, v in themes.items() if k not in crit)
    label = start.strftime("%B %Y")
    return {"month": label, "calls": n, "average": round(total / n, 1) if n else None, "criteria": rows, "other_fixes": other}
