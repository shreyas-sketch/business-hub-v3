"""
Key-person knowledge capture (v2.3, Data engine): what one key employee knows, written down so the business doesn't
depend on them alone.

    1. The owner picks the person (name, role, department).
    2. Up to 5 questions to the owner about that person (1 AI token), answered by the owner.
    3. The employee's questionnaire: 12–20 questions shaped from the owner's answers (1 AI token).
    4. The employee answers on their phone through a link (no login, expires) — or the owner types the answers in the hub.
       A PDF of the questions is there for reading. Answers are typed; there are no photo or handwritten answers.
    5. The agent skill (1 AI token): an MD skill file, an agent role card (also as a PNG) and SOPs in the SOP library.
       Where the plan has AI staff, "Add to AI staff" puts the skill into the Company Brain they answer from.

Only work knowledge is collected. The questions never ask for passwords, PINs, OTPs, bank or card details or anything
personal, and whatever is typed is cleaned before it is kept: a password, PIN or OTP someone types anyway is removed, and
long numbers, e-mail and UPI IDs and PAN are hidden (the bank statement reader's masking).

key_people {_id, ws, name, role, department, created_by, created_at, updated_at,
            owner_questions [str], owner_answers [{q, a}],
            questions [{key, section, q, hint}], token, link_expires_at, opened_at, submitted_at, submitted_by employee|owner,
            answers [{key, section, q, a}], draft_saved_at,
            skill {description, responsibilities, tasks [{title, when, steps}], decision_rules [{when, then}], contacts [{who, for}],
                   systems [{name, for, access}], problems [{problem, fix}], escalate, card {agent_name, does, takes_over, hands_back},
                   md, built_at}, sop_ids [kit ids], brain_doc_id}
"""
import io
import re
import secrets
from datetime import timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import bankstatement as bs
from .ai.keyperson_tasks import SECTIONS
from .db import db, now
from .services import new_id

LINK_DAYS = 14
MAX_ANSWER = 3000          # characters per answer
MAX_ANSWERS_TOTAL = 40_000
MAX_PEOPLE = 20
FONT_DIR = Path(__file__).resolve().parent / "fonts" / "brand"
_SECRET = re.compile(r"(?i)\b(password|passwd|pwd|passcode|pass code|login code|m?pin|upi pin|atm pin|otp|cvv)\b(\s*(?:is|was|=|:|-|—)\s*)\S+")
_PERSONAL_Q = re.compile(r"(?i)\b(salary|salaries|home address|your address|family|health|medical|religion|caste|aadhaar|aadhar|pan number|"
                         r"bank account|account number|card number|cvv|date of birth)\b")
_SECRET_Q = re.compile(r"(?i)\b(password|passwords|passcode|pin|otp|logins?|log-ins?)\b")


def scrub(text: str, limit: int = MAX_ANSWER) -> str:
    """Work knowledge only: removes a password / PIN / OTP someone typed anyway, and hides long numbers, e-mail and UPI
    IDs and PAN (the bank statement reader's masking)."""
    s = str(text or "")[:limit]
    s = _SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}[removed]", s)
    return bs.mask(s).strip()


def _line(v, n: int = 300) -> str:
    return re.sub(r"\s+", " ", scrub(v, n)).strip()[:n]


# ───────────────────────── shaping what the AI returns ─────────────────────────
def shape_owner_questions(r: dict) -> list[str]:
    out = []
    for q in (r.get("questions") or []) if isinstance(r, dict) else []:
        q = _line(q if isinstance(q, str) else (q or {}).get("q", ""), 300)
        if len(q) >= 8 and not _PERSONAL_Q.search(q) and not _SECRET_Q.search(q) and q not in out:
            out.append(q)
    if len(out) < 2:
        raise ValueError("too few questions")
    return out[:5]


def _section(s: str) -> str:
    s = str(s or "").strip().lower()
    for name in SECTIONS:
        if s == name.lower() or s.split(" ")[0] == name.lower().split(" ")[0]:
            return name
    return SECTIONS[-1]


def safe_question(q: str) -> str | None:
    """A question the employee may be asked: never personal; about logins only to ask where access is kept."""
    if _PERSONAL_Q.search(q):
        return None
    if _SECRET_Q.search(q):
        if not re.search(r"(?i)\b(where|kept|stored|who else)\b", q):
            return None
        if not re.search(r"(?i)\b(don'?t|do not|never)\b", q):
            q = q.rstrip() + " (Don't write the password itself.)"
    return q


def shape_questions(r: dict) -> list[dict]:
    out, seen = [], set()
    for x in (r.get("questions") or []) if isinstance(r, dict) else []:
        if not isinstance(x, dict):
            continue
        q = safe_question(_line(x.get("q"), 400))
        if not q or len(q) < 8 or q.lower() in seen:
            continue
        seen.add(q.lower())
        out.append({"key": f"q{len(out) + 1}", "section": _section(x.get("section")), "q": q, "hint": _line(x.get("hint"), 240)})
    if len(out) < 8:
        raise ValueError("too few questions")
    order = {s: i for i, s in enumerate(SECTIONS)}
    out.sort(key=lambda x: order.get(x["section"], 99))   # stable: keeps the AI's order inside a section
    for i, x in enumerate(out[:20]):
        x["key"] = f"q{i + 1}"
    return out[:20]


def _list(v, n: int, size: int = 300) -> list[str]:
    out = []
    for x in v if isinstance(v, list) else []:
        t = _line(x, size)
        if t and t not in out:
            out.append(t)
    return out[:n]


def shape_skill(r: dict) -> dict:
    r = r if isinstance(r, dict) else {}
    tasks = []
    for t in r.get("tasks") or []:
        if not isinstance(t, dict):
            continue
        steps = _list(t.get("steps"), 15, 400)
        title = _line(t.get("title"), 120)
        if title and steps:
            tasks.append({"title": title, "when": _line(t.get("when"), 200), "steps": steps})
    pairs = lambda key, a, b, n: [{a: _line(x.get(a), 240), b: _line(x.get(b), 300)} for x in (r.get(key) or [])   # noqa: E731
                                 if isinstance(x, dict) and _line(x.get(a), 240)][:n]
    systems = [{"name": _line(x.get("name"), 120), "for": _line(x.get("for"), 240), "access": _line(x.get("access"), 240)}
               for x in (r.get("systems") or []) if isinstance(x, dict) and _line(x.get("name"), 120)][:10]
    card = r.get("card") if isinstance(r.get("card"), dict) else {}
    out = {
        "description": _line(r.get("description"), 300),
        "responsibilities": _list(r.get("responsibilities"), 10),
        "tasks": tasks[:8],
        "decision_rules": pairs("decision_rules", "when", "then", 12),
        "contacts": pairs("contacts", "who", "for", 12),
        "systems": systems,
        "problems": pairs("problems", "problem", "fix", 10),
        "escalate": _list(r.get("escalate"), 8),
        "card": {"agent_name": _line(card.get("agent_name"), 60), "does": _line(card.get("does"), 240),
                 "takes_over": _list(card.get("takes_over"), 6, 160), "hands_back": _list(card.get("hands_back"), 6, 160)},
    }
    if not out["description"] or not (out["tasks"] or out["responsibilities"]):
        raise ValueError("the skill came back empty")
    return out


# ───────────────────────── the MD skill file ─────────────────────────
def slug(role: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (role or "team-member").lower()).strip("-")[:40] or "team-member"


def skill_md(person: dict, sk: dict) -> str:
    role = person.get("role") or "Team member"
    desc = re.sub(r"\s+", " ", sk.get("description") or f"Does the work of the {role}.").replace('"', "'")
    out = ["---", f"name: {slug(role)}-skill", f"description: {desc}", "---", f"# {role} — skills", ""]

    def bullets(title: str, items: list[str]):
        out.extend([f"## {title}", *([f"- {x}" for x in items] or ["- (not covered in the answers)"]), ""])

    bullets("Responsibilities", sk.get("responsibilities") or [])
    out.append("## Daily / weekly tasks (step by step)")
    if not sk.get("tasks"):
        out.append("- (not covered in the answers)")
    for t in sk.get("tasks") or []:
        out.append(f"### {t['title']}")
        if t.get("when"):
            out.append(f"When: {t['when']}")
        out.extend(f"{i}. {s}" for i, s in enumerate(t["steps"], 1))
        out.append("")
    if not sk.get("tasks"):
        out.append("")
    def rule(r: dict) -> str:
        when = re.sub(r"(?i)^if\s+", "", r["when"]).rstrip(" ,")
        return f"If {when}, then {r['then']}" if r.get("then") else r["when"]

    bullets("Decision rules (if X then Y)", [rule(r) for r in sk.get("decision_rules") or []])
    contacts = [f"{c['who']} — {c['for']}" if c.get("for") else c["who"] for c in sk.get("contacts") or []]
    systems = [s["name"] + (f" — {s['for']}" if s.get("for") else "") + (f" (access: {s['access']})" if s.get("access") else "")
               for s in sk.get("systems") or []]
    bullets("Contacts and systems", contacts + systems)
    bullets("Common problems and fixes", [f"{p['problem']} → {p['fix']}" if p.get("fix") else p["problem"] for p in sk.get("problems") or []])
    bullets("What to escalate to the owner", sk.get("escalate") or [])
    return "\n".join(out).rstrip() + "\n"


# ───────────────────────── SOPs from the skill ─────────────────────────
async def save_sops(ws: str, actor: str, person: dict, sk: dict, business_name: str) -> list[str]:
    """Each step-by-step task becomes an SOP in the SOP library (no AI). An SOP made earlier for the same task is left as it
    is (the owner may have edited it); new tasks get new SOPs."""
    from . import kits
    existing = {d["title"].lower(): d["_id"] async for d in db().kits.find({"ws": ws, "kind": "sop", "key_person_id": person["_id"]}, {"title": 1})}
    ids = list(existing.values())
    role = person.get("role") or "Team member"
    for t in sk.get("tasks") or []:
        if len(t["steps"]) < 2 or t["title"].lower() in existing:
            continue
        content = kits.shape(kits.KINDS["sop"], {
            "title": t["title"][:140], "purpose": f"How the {role} does this at {business_name}, written from {person.get('name', 'their')}'s own answers."[:500],
            "owner_role": role[:100], "when": (t.get("when") or "")[:300],
            "steps": [{"title": s[:160], "detail": ""} for s in t["steps"][:25]],
            "escalation": "; ".join(sk.get("escalate") or [])[:400]})
        doc = {"_id": new_id(), "ws": ws, "kind": "sop", "title": content["title"][:160], "content": content,
               "inputs": {"process": t["title"][:300], "notes": "", "mode": "process", "function": ""}, "created_by": actor,
               "created_at": now(), "updated_at": now(), "by_agent": "key_person", "key_person_id": person["_id"]}
        await db().kits.insert_one(doc)
        existing[t["title"].lower()] = doc["_id"]
        ids.append(doc["_id"])
    return ids


# ───────────────────────── the link ─────────────────────────
def new_link() -> dict:
    return {"token": secrets.token_urlsafe(24), "link_expires_at": now() + timedelta(days=LINK_DAYS)}


async def by_token(token: str) -> dict | None:
    token = str(token or "")[:80]
    if len(token) < 20:
        return None
    p = await db().key_people.find_one({"token": token})
    if not p or not p.get("link_expires_at") or p["link_expires_at"] <= now() or not p.get("questions"):
        return None
    return p


def clean_answers(questions: list[dict], raw: dict) -> list[dict]:
    """Answers keyed by question; only to the questions asked, each cleaned and cut to size."""
    out, total = [], 0
    for q in questions:
        a = scrub((raw or {}).get(q["key"], ""))
        total += len(a)
        if total > MAX_ANSWERS_TOTAL:
            a = a[:max(0, MAX_ANSWER - (total - MAX_ANSWERS_TOTAL))]
        out.append({"key": q["key"], "section": q["section"], "q": q["q"], "a": a})
    return out


# ───────────────────────── drawing: the questions PDF and the role card ─────────────────────────
def _font(bold: bool, size: int) -> ImageFont.FreeTypeFont:
    path = FONT_DIR / ("Mukta-700.ttf" if bold else "Mukta-400.ttf")   # Mukta draws English and Hindi
    try:
        return ImageFont.truetype(str(path), size, layout_engine=ImageFont.Layout.RAQM)
    except Exception:  # noqa: BLE001  no shaping library: still readable for English
        return ImageFont.truetype(str(path), size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    lines = []
    for para in str(text or "").split("\n"):
        words, line = para.split(), ""
        for w in words:
            trial = f"{line} {w}".strip()
            if draw.textlength(trial, font=font) <= width or not line:
                line = trial
            else:
                lines.append(line)
                line = w
        lines.append(line)
    return lines


def _hex(c: str) -> tuple:
    c = (c or "#1F4E79").lstrip("#")
    try:
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4)) if len(c) == 6 else (31, 78, 121)
    except ValueError:
        return (31, 78, 121)


def questions_pdf(person: dict, business_name: str, link: str = "", accent: str = "#1F4E79") -> bytes:
    """The questionnaire on A4 pages, for reading (answers are typed in the hub). Drawn as images so Hindi shows right."""
    W, H, M = 1240, 1754, 110                       # A4 at 150 dpi
    ink, muted, acc = (20, 23, 28), (91, 99, 112), _hex(accent)
    f_title, f_head, f_body, f_small = _font(True, 46), _font(True, 30), _font(False, 28), _font(False, 23)
    pages: list[Image.Image] = []
    page = draw = None
    y = 0

    def new_page():
        nonlocal page, draw, y
        page = Image.new("RGB", (W, H), "white")
        draw = ImageDraw.Draw(page)
        draw.rectangle([0, 0, W, 14], fill=acc)
        draw.text((M, H - 80), f"{business_name} · questions for {person.get('name', '')}", font=f_small, fill=muted)
        draw.text((W - M - 80, H - 80), f"{len(pages) + 1}", font=f_small, fill=muted)
        pages.append(page)
        y = M

    def block(lines: list[str], font, fill, gap: int, lh: int):
        nonlocal y
        for ln in lines:
            if y + lh > H - 130:
                new_page()
            draw.text((M, y), ln, font=font, fill=fill)
            y += lh
        y += gap

    new_page()
    first = (person.get("name") or "").split(" ")[0] or "you"
    block(_wrap(draw, business_name, f_small, W - 2 * M), f_small, muted, 6, 32)
    block(_wrap(draw, f"Questions for {first} — {person.get('role', '')}", f_title, W - 2 * M), f_title, ink, 10, 58)
    note = ("Your answers help the business write down how your work is done, so the team and an AI assistant can help "
            "with it. Write the way you'd explain it to a new colleague. Never write a password, PIN or bank detail.")
    block(_wrap(draw, note, f_body, W - 2 * M), f_body, muted, 8, 38)
    if link:
        block(_wrap(draw, f"Type your answers here: {link}", f_body, W - 2 * M), f_body, acc, 20, 38)
    section, n = None, 0
    for q in person.get("questions") or []:
        if q["section"] != section:
            section = q["section"]
            y += 10
            block([section], f_head, acc, 6, 42)
        n += 1
        block(_wrap(draw, f"{n}. {q['q']}", f_body, W - 2 * M), f_body, ink, 2, 38)
        if q.get("hint"):
            block(_wrap(draw, q["hint"], f_small, W - 2 * M - 30), f_small, muted, 0, 32)
        y += 18
    buf = io.BytesIO()
    pages[0].save(buf, "PDF", resolution=150, save_all=True, append_images=pages[1:])
    return buf.getvalue()


def card_png(person: dict, sk: dict, business_name: str, accent: str = "#1F4E79") -> bytes:
    """The agent role card: name, role, what it does, what it can take over, what it hands back. 1080×1350."""
    W, H, M = 1080, 1350, 80
    acc = _hex(accent)
    light = tuple(int(c + (255 - c) * 0.9) for c in acc)
    ink, muted = (20, 23, 28), (91, 99, 112)
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    card = sk.get("card") or {}
    d.rectangle([0, 0, W, 330], fill=acc)
    f_small, f_body, f_head, f_big = _font(False, 30), _font(False, 34), _font(True, 36), _font(True, 68)
    d.text((M, 70), "AI AGENT ROLE CARD", font=f_small, fill="white")
    y = 120
    for ln in _wrap(d, card.get("agent_name") or f"{person.get('role', 'Team')} Agent", f_big, W - 2 * M)[:2]:
        d.text((M, y), ln, font=f_big, fill="white")
        y += 82
    d.text((M, 280 if y < 280 else y), f"Role: {person.get('role', '')}"[:60], font=f_small, fill="white")
    y = 380
    for ln in _wrap(d, card.get("does") or sk.get("description", ""), f_body, W - 2 * M)[:4]:
        d.text((M, y), ln, font=f_body, fill=ink)
        y += 46
    y += 24

    def box(title: str, items: list[str], y: int) -> int:
        lines = []
        for it in items[:5]:
            w = _wrap(d, it, f_body, W - 2 * M - 90)[:2]
            lines.append(w)
        h = 70 + sum(len(w) * 44 + 10 for w in lines) + 20
        d.rounded_rectangle([M, y, W - M, y + h], radius=22, fill=light)
        d.text((M + 30, y + 22), title, font=f_head, fill=acc)
        yy = y + 76
        for w in lines:
            d.ellipse([M + 32, yy + 16, M + 44, yy + 28], fill=acc)
            for ln in w:
                d.text((M + 62, yy), ln, font=f_body, fill=ink)
                yy += 44
            yy += 10
        return y + h + 26

    y = box("Can take over", card.get("takes_over") or [], y)
    if y < H - 300:
        y = box("Hands back to you", card.get("hands_back") or [], y)
    d.text((M, H - 80), f"{business_name} · from {(person.get('name') or '').split(' ')[0]}'s own answers"[:70], font=f_small, fill=muted)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


async def ensure_indexes() -> None:
    await db().key_people.create_index([("ws", 1), ("created_at", -1)])
    await db().key_people.create_index("token", sparse=True)
