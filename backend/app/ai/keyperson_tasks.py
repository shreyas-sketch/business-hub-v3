"""
AI jobs for key-person knowledge capture (v2.3, Data engine): the owner's questions about one employee, that employee's
questionnaire, and the agent skill built from the answers. Same rules as every job: only the facts given, plain language,
the owner's preferred language. Never ask for passwords, PINs, bank details or anything personal — only work knowledge.
Each job has a deterministic draft for running without an AI key (tests, the demo).
"""
import re

from .provider import complete
from .tasks import RULES, _v, profile_text

SECTIONS = ["Daily and weekly work", "Step-by-step processes", "Decisions", "Customers, suppliers and contacts", "Tools and systems",
            "Problems and fixes", "Only they know"]
NEVER = ("Never ask for passwords, PINs, OTPs, bank or card details, or anything personal (health, family, salary, address). "
         "For tools and systems, ask WHERE access is kept and who else has it — never the password itself.")


def _who(person: dict) -> str:
    return f"{person.get('name', 'This employee')} ({person.get('role', 'team member')}" + (
        f", {person['department']})" if person.get("department") else ")")


# ───────────────────────── 1. questions to the owner ─────────────────────────
async def owner_questions(p: dict, person: dict):
    name, role = person.get("name", "this person").split(" ")[0] or "this person", person.get("role", "their role")

    def mock():
        return {"questions": [
            f"What does {name} handle as {role} that nobody else in the business can?",
            f"If {name} were away for two weeks, what would break first?",
            f"Which customers, suppliers or systems does {name} deal with?",
            f"Which decisions does {name} take without asking you?",
            f"What do you keep calling {name} to ask?",
        ]}

    return await complete(RULES, f"""{profile_text(p)}

The owner depends on one employee: {_who(person)}. We will turn what this person knows into written skills an AI agent and
the team can use, so the business doesn't stop when they're away or leave.

Write up to 5 short questions to ask the OWNER about this employee, shaped for the role "{role}", so we learn what to ask the
employee next. Cover: what only they handle, what breaks first without them, who and what they deal with, which decisions
they take alone, and what the owner keeps calling them about. One line each, simple English.
{NEVER}
JSON only: {{"questions": ["...", "..."]}}""", mock, 900)


# ───────────────────────── 2. the employee's questionnaire ─────────────────────────
async def questionnaire(p: dict, person: dict, owner_answers: list[dict]):
    name, role = person.get("name", "you").split(" ")[0] or "you", person.get("role", "your role")
    told = "\n".join(f"- {x.get('q', '')}\n  Owner: {x.get('a', '')}" for x in owner_answers if (x.get("a") or "").strip()) or "(nothing yet)"

    def mock():
        q = [
            ("Daily and weekly work", "What do you do every day, from the time you start? List the tasks in order.", "Short lines are fine."),
            ("Daily and weekly work", "What do you do once a week or once a month that nobody else does?", ""),
            ("Step-by-step processes", f"Pick the most important job you do as {role}. Write the steps one by one, as if teaching a new person.", "Step 1, step 2…"),
            ("Step-by-step processes", "What is the second job only you know how to do? Write its steps too.", ""),
            ("Step-by-step processes", "How do you check that the work is done right before it goes out?", ""),
            ("Decisions", "Which decisions do you take on your own? For each, how do you decide?", "If this happens, I do that."),
            ("Decisions", "When do you go to the owner instead of deciding yourself?", ""),
            ("Customers, suppliers and contacts", "Which customers or suppliers do you deal with most, and what for?", "Names of companies or roles; no phone numbers needed."),
            ("Customers, suppliers and contacts", "Who do you call when something goes wrong, and for what?", ""),
            ("Tools and systems", "Which software, websites, machines or registers do you use, and for what?", ""),
            ("Tools and systems", "Where are the logins for these kept, and who else has access? (Don't write any password here.)", "For example: in the office laptop's password book, with the owner."),
            ("Problems and fixes", "What usually goes wrong in your work, and how do you fix it?", ""),
            ("Problems and fixes", "What mistakes does a new person usually make in your job?", ""),
            ("Only they know", f"What do you know about this work that nobody else at {_v(p, 'name', 'the business')} knows?", ""),
            ("Only they know", "If you were away for two weeks, what would someone need to know on day one?", ""),
        ]
        return {"questions": [{"section": s, "q": t, "hint": h} for s, t, h in q]}

    return await complete(RULES, f"""{profile_text(p)}

Employee: {_who(person)}.
What the owner told us about them:
{told}

Write the questionnaire this employee fills in on their phone, so their know-how becomes written skills (an AI agent skill
and SOPs). 12 to 20 questions, tailored to the role and to what the owner said. Group them under these sections (use the
names exactly): {", ".join(SECTIONS)}. Ask for step-by-step answers where it helps ("write the steps one by one").
Speak to the employee as "you", warmly and simply. Each question can have a short hint (an example of a good answer).
{NEVER}
JSON only: {{"questions": [{{"section": "...", "q": "...", "hint": "..."}}]}}""", mock, 2500)


# ───────────────────────── 3. the agent skill ─────────────────────────
async def skill(p: dict, person: dict, answers: list[dict], owner_answers: list[dict]):
    role = person.get("role", "Team member")
    qa = "\n".join(f"[{x.get('section', '')}] Q: {x.get('q', '')}\nA: {x.get('a', '')}" for x in answers if (x.get("a") or "").strip())
    told = "\n".join(f"- {x.get('q', '')} → {x.get('a', '')}" for x in owner_answers if (x.get("a") or "").strip())

    def mock():
        by = {}
        for x in answers:
            a = (x.get("a") or "").strip()
            if a:
                by.setdefault(x.get("section", ""), []).append(a)
        lines = lambda sec: [l.strip(" -•\t") for a in by.get(sec, []) for l in a.splitlines() if l.strip(" -•\t")]   # noqa: E731
        steps = lines("Step-by-step processes")
        tasks = []
        if steps:
            tasks.append({"title": f"Main {role.lower()} job", "when": "Every time this job comes up", "steps": steps[:12]})
        daily = lines("Daily and weekly work")
        if daily:
            tasks.append({"title": "Daily and weekly routine", "when": "Every working day", "steps": daily[:12]})
        rules = []
        for l in lines("Decisions"):
            parts = re.split(r"\s+then\s+", l, maxsplit=1, flags=re.I)
            if len(parts) == 2:
                rules.append({"when": re.sub(r"(?i)^if\s+", "", parts[0]).strip(" ,"), "then": parts[1].strip()})
        return {
            "description": f"Does the work of the {role} at {_v(p, 'name', 'the business')}, the way {person.get('name', 'they').split(' ')[0]} does it.",
            "responsibilities": daily[:6] or [f"The work of the {role}"],
            "tasks": tasks,
            "decision_rules": rules[:10],
            "contacts": [{"who": l, "for": ""} for l in lines("Customers, suppliers and contacts")[:10]],
            "systems": [{"name": l, "for": ""} for l in lines("Tools and systems")[:10]],
            "problems": [{"problem": l, "fix": ""} for l in lines("Problems and fixes")[:10]],
            "escalate": ["Anything about money, prices or discounts", "A customer who is unhappy or threatens to leave"] + lines("Only they know")[:3],
            "card": {"agent_name": f"{role} Agent", "does": f"Handles the {role.lower()} work the way {person.get('name', 'your employee').split(' ')[0]} does.",
                     "takes_over": [t["title"] for t in tasks][:6] or ["Routine work of this role"],
                     "hands_back": ["Money decisions and discounts", "Unhappy customers", "Anything not covered by these steps"]},
        }

    return await complete(RULES, f"""{profile_text(p)}

Employee: {_who(person)}. Below are their own answers about how they do their work, and what the owner said about them.
Turn them into an AI agent skill for this role. Use ONLY what they wrote: do not add steps, contacts, tools, prices, numbers or
rules that aren't in the answers. Keep their wording where you can. Leave a list empty if the answers don't cover it.
Never include a password, PIN, phone number, account number or anything personal, even if the answers have one.

Owner said:
{told or '(nothing)'}

Employee's answers:
{qa}

JSON only: {{"description": "one sentence: what this agent does and when to use it",
"responsibilities": ["..."],
"tasks": [{{"title": "short name of the job", "when": "when it is done", "steps": ["step 1", "step 2"]}}],
"decision_rules": [{{"when": "if this happens", "then": "do this"}}],
"contacts": [{{"who": "company or role", "for": "what for"}}],
"systems": [{{"name": "tool or system", "for": "what it is used for", "access": "where access is kept (never the password)"}}],
"problems": [{{"problem": "...", "fix": "..."}}],
"escalate": ["what to hand back to the owner"],
"card": {{"agent_name": "short name, e.g. Dispatch Agent", "does": "one line", "takes_over": ["tasks it can take over"], "hands_back": ["what it hands back to the owner"]}}}}
Up to 8 tasks (each 3–15 steps), 12 decision rules, 12 contacts, 10 systems, 10 problems, 8 escalations.""", mock, 5000)
