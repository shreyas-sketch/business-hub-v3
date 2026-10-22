"""
The meeting actions agent: reads the notes a team took in its strategic or tactical meeting and pulls out
the decisions and the action items, each with an owner from the team and a due date when the notes give one.
Same rules as every job: only what the notes say; never invent tasks, names, numbers or dates.
A deterministic draft (`mock`) reads the notes line by line, so the hub works without an AI key.
"""
import re
from datetime import date, timedelta

from .provider import complete

RULES = """You work inside OwnerDesk, the business hub, for an Indian small business.
You read the notes the team wrote during its own meeting.
Rules you never break:
1. Use ONLY what the notes say. Never invent tasks, decisions, names, numbers, amounts or dates.
2. A decision is something the team agreed. An action is a concrete job someone will do after the meeting.
3. Short, plain language. Keep the language the notes are written in (English, Hinglish, Hindi, Gujarati…). No emojis."""

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
DECISION = re.compile(r"^(?:we\s+decided|we\s+agreed|decided|decision|agreed)\b(?:\s+to\b)?[\s:–-]*", re.I)
ACTION = re.compile(r"^(?:action(?:\s+item)?|todo|to\s+do|task|next\s+step)\b[\s:–-]*", re.I)
BULLET = re.compile(r"^\s*(?:[-•*·]+|\d+[.)])\s*")


def _clean(text: str) -> str:
    return " ".join(str(text or "").split())


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


TITLES = re.compile(r"\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|Shri|Smt|No|Rs|vs|etc|approx|e\.g|i\.e)\.$", re.I)


def sentences(line: str) -> list[str]:
    """Splits after . ! ? before a capital letter, but never after Mr. / Dr. / Rs. and the like."""
    out: list[str] = []
    for part in re.split(r"(?<=[.!?])\s+(?=[A-Z])", line):
        if out and TITLES.search(out[-1]):
            out[-1] = f"{out[-1]} {part}"
        else:
            out.append(part)
    return [s for s in out if s.strip()]


def _lines(text: str) -> list[str]:
    out = []
    for raw in re.split(r"[\n;]+", text or ""):
        line = _clean(BULLET.sub("", raw))
        if len(line) >= 3:
            out.append(line)
    return out


def due_from(text: str, base: date) -> str:
    """'by Friday', 'tomorrow', 'next week' or a written date → YYYY-MM-DD, counted from the meeting date. Else ''."""
    m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if m:
        try:
            return date.fromisoformat(m.group(1)).isoformat()
        except ValueError:
            pass
    low = text.lower()
    if re.search(r"\btoday\b", low):
        return base.isoformat()
    if re.search(r"\btomorrow\b", low):
        return (base + timedelta(days=1)).isoformat()
    if re.search(r"\bnext week\b", low):
        return (base + timedelta(days=7)).isoformat()
    for i, name in enumerate(WEEKDAYS):
        if re.search(rf"\b{name}\b", low):
            return (base + timedelta(days=(i - base.weekday()) % 7 or 7)).isoformat()
    return ""


def _first(name: str) -> str:
    return (name or "").split()[0].lower() if (name or "").split() else ""


def person_in(line: str, people: list[dict]) -> dict | None:
    """The first team member named in a line of notes (by first name), if any."""
    low = line.lower()
    for p in people:
        first = _first(p["name"])
        if first and re.search(rf"\b{re.escape(first)}\b", low):
            return p
    return None


def match_person(name: str, people: list[dict]) -> dict | None:
    """An owner name from the AI → one person in the workspace. Ambiguous or unknown names stay unassigned."""
    n = _clean(name).lower().strip(".")
    if not n:
        return None
    if n in ("owner", "the owner", "business owner", "boss", "me", "myself"):
        return next((p for p in people if p.get("role") == "owner"), None)
    for test in (lambda p: p["name"].lower() == n,
                 lambda p: _first(p["name"]) == n.split()[0],
                 lambda p: n in p["name"].lower() or p["name"].lower() in n):
        found = [p for p in people if p["name"] and test(p)]
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            return None
    return None


def shape_actions(result) -> dict:
    """Keeps the AI answer to the shape we store; raises if it is not usable (the run is then refunded)."""
    if not isinstance(result, dict) or not isinstance(result.get("decisions", []), list) or not isinstance(result.get("actions", []), list):
        raise ValueError("unexpected shape")
    decisions = [_clean(d)[:300] for d in result.get("decisions") or [] if isinstance(d, str) and _clean(d)][:15]
    actions = []
    for a in result.get("actions") or []:
        if isinstance(a, str):
            a = {"task": a}
        if not isinstance(a, dict):
            continue
        task = _clean(a.get("task"))[:200]
        if task:
            actions.append({"task": task, "owner": _clean(a.get("owner"))[:80], "due": _clean(a.get("due"))[:10]})
    return {"decisions": decisions, "actions": actions[:20]}


async def meeting_actions(meeting: dict, people: list[dict], business: str = ""):
    try:
        base = date.fromisoformat(meeting.get("date", ""))
    except ValueError:
        base = date.today()
    sections = [(s.get("title", ""), (s.get("notes") or "").strip()) for s in meeting.get("sections", []) if (s.get("notes") or "").strip()]

    def mock():
        decisions, actions = [], []
        for _, notes in sections:
            for line in (sent for l in _lines(notes) for sent in sentences(l)):
                if DECISION.match(line):
                    decisions.append(_cap(DECISION.sub("", line)))
                    continue
                who = person_in(line, people)
                padded = f" {line.lower()} "
                if ACTION.match(line) or (who and (" will " in padded or padded.startswith(f" {_first(who['name'])} to "))):
                    task = ACTION.sub("", line)
                    if who:
                        task = re.sub(rf"^{re.escape(_first(who['name']))}\w*\s+(?:will|to|should|must)\s+", "", task, flags=re.I)
                    actions.append({"task": _cap(task)[:200], "owner": who["name"] if who else "", "due": due_from(line, base)})
        if not decisions and not actions:  # nothing marked: the goals sections become unassigned actions to review
            for title, notes in sections:
                if "goal" in title.lower() or "plan" in title.lower():
                    actions += [{"task": _cap(l)[:200], "owner": "", "due": ""} for l in _lines(notes)[:3]]
        return {"decisions": decisions[:15], "actions": actions[:20]}

    roster = "\n".join(f"- {p['name']} ({p.get('role', 'staff')})" for p in people) or "- (no names)"
    notes = "\n\n".join(f"## {title}\n{text[:4000]}" for title, text in sections)
    kind = "strategic (monthly or quarterly)" if meeting.get("type") == "strategic" else "tactical (weekly)"
    return await complete(RULES, f"""Business: {business or '(not given)'}
Meeting: {meeting.get('title', '')} — a {kind} team meeting held on {base.isoformat()} ({WEEKDAYS[base.weekday()].title()}).

People in this business (use these exact names as owners):
{roster}

The meeting notes, section by section:
{notes}

Pull out:
- "decisions": what the team agreed, one short line each (up to 15).
- "actions": jobs to be done after the meeting (up to 20). For each:
  "task": starts with a verb, under 20 words, keeps any number exactly as written in the notes;
  "owner": the exact name from the list above when the notes say who will do it, else "";
  "due": YYYY-MM-DD only when the notes give or clearly imply a date (work out "by Friday" from the meeting date), else "".
If there are no decisions or no actions, return empty lists.
JSON: {{"decisions": ["..."], "actions": [{{"task": "...", "owner": "...", "due": ""}}]}}""", mock, 2500)
