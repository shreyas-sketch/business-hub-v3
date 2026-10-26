"""
AI jobs that write the hub's working documents: SOPs, hiring kits, decision logs, role clarity, culture charters,
competence development plans and monthly business reviews. Same rules as every other job: only the owner's facts,
plain language, the owner's preferred language. Each job has a deterministic draft for running without an AI key.
"""
from .provider import complete
from .tasks import RULES, _short, _v, profile_text

FUNCTIONS = ["Marketing", "Sales", "Operations", "Product development", "Accounts and finance", "People (HR)", "Management"]


def _lines(text: str, limit: int = 25) -> list[str]:
    return [l.strip(" -•\t") for l in (text or "").splitlines() if l.strip(" -•\t")][:limit]


# ───────────────────────── SOP ─────────────────────────
async def sop(p: dict, inputs: dict):
    if inputs.get("mode") == "scenario":
        return await scenario_sop(p, inputs)
    process, notes = inputs.get("process", "").strip(), inputs.get("notes", "").strip()

    def mock():
        steps = _lines(notes, 12) or ["Receive the request and note the customer's name, number and what they need",
                                      "Confirm the details and the timeline with the customer on WhatsApp",
                                      "Do the work and check it against the checklist below",
                                      "Hand over, collect payment and ask for feedback"]
        return {"title": process[:1].upper() + process[1:120], "purpose": f"So every {process.lower()} at {_v(p, 'name', 'our business')} is done the same, right way.",
                "owner_role": "", "when": f"Every time we handle {process.lower()}.",
                "steps": [{"title": s[:120], "detail": ""} for s in steps],
                "checklist": ["Customer details noted", "Timeline confirmed in writing", "Work checked before handover"],
                "mistakes": ["Starting before the customer has confirmed", "Skipping the final check"]}

    return await complete(RULES, f"""{profile_text(p)}

Write a Standard Operating Procedure (SOP) for this process: {process}
Owner's notes or voice-note transcript (use these facts; keep their order where it makes sense):
{notes or '(none)'}

A new team member must be able to follow it on day one. Steps are short, in order, one action each.
JSON only: {{"title": "...", "purpose": "one sentence", "owner_role": "who does it, if the notes say", "when": "when this SOP is used",
"steps": [{{"title": "short action", "detail": "how, in one or two sentences"}}], "checklist": ["..."], "mistakes": ["common mistakes to avoid"]}}
4–15 steps, up to 10 checklist items, up to 6 mistakes.""", mock, 3500)


async def scenario_sop(p: dict, inputs: dict):
    """A situation, not a routine job: what starts it, who owns it, the steps, the exact words to use, what done looks like
    and when to escalate."""
    situation, notes = inputs.get("process", "").strip(), inputs.get("notes", "").strip()

    def mock():
        name = _v(p, "name", "our business")
        return {"title": situation[:1].upper() + situation[1:120], "purpose": f"So everyone at {name} handles this the same calm, fair way.",
                "owner_role": "Whoever receives it first, then the owner", "when": f"When {situation.lower()}.", "trigger": situation[:1].upper() + situation[1:240],
                "function": inputs.get("function") or "Customer care",
                "steps": [{"title": "Listen and write down exactly what happened", "detail": "Name, number, order details and what they expect."},
                          {"title": "Say sorry for the trouble, without blaming anyone", "detail": ""},
                          {"title": "Check the facts before promising anything", "detail": "Look up the order, the dates and what was agreed."},
                          {"title": "Give a clear next step and a time", "detail": "Tell them what you will do and by when."},
                          {"title": "Follow up when you said you would", "detail": ""}] + [{"title": l[:120], "detail": ""} for l in _lines(notes, 6)],
                "scripts": [{"label": "First reply", "text": f"I'm sorry about this. Thank you for telling us. Let me check the details and come back to you by today evening. — {name}"},
                            {"label": "Follow-up", "text": "As promised, here is what we found and what we'll do next."}],
                "checklist": ["Details written down", "Next step and time given", "Followed up on time"],
                "mistakes": ["Arguing about who was wrong", "Promising a date before checking"],
                "done_when": "The customer has confirmed the problem is solved, and it's noted.",
                "escalation": "Bring in the owner when the customer asks for a refund or is still unhappy after one follow-up."}

    return await complete(RULES, f"""{profile_text(p)}

Write a scenario SOP for this situation: {situation}
Owner's notes (use these facts): {notes or '(none)'}
Function: {inputs.get('function') or 'whichever fits'}

JSON only: {{"title": "...", "purpose": "one sentence", "trigger": "what starts this", "owner_role": "who owns it", "when": "...",
"function": "Sales | Marketing | Operations/Delivery | Accounts | HR | Customer care",
"steps": [{{"title": "short action", "detail": "how"}}], "scripts": [{{"label": "e.g. First reply", "text": "the exact words or WhatsApp message"}}],
"checklist": ["..."], "mistakes": ["..."], "done_when": "what done looks like", "escalation": "who to bring in and when"}}
4–12 steps, 2–5 scripts. No prices, refunds or discounts that aren't in the profile.""", mock, 3500)


PROCESS_MAPS = {
    "trading": ["Sales: Taking an order from a dealer", "Sales: Sending a price list and quotation", "Sales: Following up an open quotation",
                "Marketing: Posting new stock on WhatsApp and social media", "Marketing: Getting new dealers", "Operations/Delivery: Receiving stock from a supplier",
                "Operations/Delivery: Checking stock levels and reordering", "Operations/Delivery: Packing and dispatching an order",
                "Operations/Delivery: Handling a damaged or wrong delivery", "Accounts: Raising a GST invoice", "Accounts: Collecting payments and following up dues",
                "Accounts: Paying suppliers", "Accounts: Monthly GST and books with the accountant", "HR: Hiring a salesperson", "HR: A new staff member's first week",
                "Customer care: Handling a complaint", "Customer care: Returns and replacements", "Customer care: Asking happy customers for referrals"],
    "clinic": ["Sales: Answering an appointment enquiry", "Sales: Explaining treatment options and costs", "Marketing: Posting patient education content",
               "Marketing: Getting Google reviews from happy patients", "Operations/Delivery: Booking and confirming appointments",
               "Operations/Delivery: Patient check-in and records", "Operations/Delivery: Sterilisation and room preparation", "Operations/Delivery: Follow-up after treatment",
               "Accounts: Billing and receipts", "Accounts: Insurance and pending payments", "Accounts: Monthly accounts with the CA", "HR: Hiring an assistant",
               "HR: A new staff member's first week", "Customer care: Handling a patient complaint", "Customer care: Missed appointments and reminders",
               "Customer care: Recall for check-ups"],
    "food": ["Sales: Taking a phone or WhatsApp order", "Sales: Bulk and event orders", "Marketing: Posting the daily special", "Marketing: Festival offers",
             "Operations/Delivery: Opening checklist", "Operations/Delivery: Closing checklist", "Operations/Delivery: Receiving and storing raw material",
             "Operations/Delivery: Hygiene and FSSAI checks", "Operations/Delivery: Packing and delivery", "Accounts: Daily cash and UPI tally",
             "Accounts: Paying suppliers", "HR: Hiring kitchen staff", "HR: A new staff member's first day", "Customer care: Handling a complaint about food",
             "Customer care: Asking for reviews"],
    "services": ["Sales: Answering a new enquiry", "Sales: Site visit or first meeting", "Sales: Sending a quotation", "Sales: Following up a quotation",
                 "Marketing: Posting finished work", "Marketing: Asking for referrals", "Marketing: Google profile and reviews", "Operations/Delivery: Starting a new job",
                 "Operations/Delivery: Buying material for a job", "Operations/Delivery: Daily progress updates to the customer", "Operations/Delivery: Quality check before handover",
                 "Operations/Delivery: Handover and sign-off", "Accounts: Taking an advance", "Accounts: Raising the invoice", "Accounts: Following up payments",
                 "Accounts: Paying contractors and suppliers", "HR: Hiring a site supervisor", "HR: A new staff member's first week", "Customer care: Handling a complaint",
                 "Customer care: After-sales service visit"],
    "manufacturing": ["Sales: Handling an enquiry from a buyer", "Sales: Costing and quotation", "Sales: Sample approval", "Marketing: Catalogue and exhibitions",
                      "Operations/Delivery: Purchase of raw material", "Operations/Delivery: Production planning", "Operations/Delivery: Quality inspection",
                      "Operations/Delivery: Machine maintenance", "Operations/Delivery: Packing and dispatch", "Accounts: GST invoice and e-way bill",
                      "Accounts: Collections", "Accounts: Paying vendors", "HR: Hiring operators", "HR: Safety induction", "Customer care: Handling a quality complaint",
                      "Customer care: Replacement and credit notes"],
}


def business_type(p: dict) -> str:
    text = f"{p.get('industry', '')} {' '.join(o.get('name', '') for o in p.get('offers') or [])}".lower()
    for kind, words in (("clinic", ("clinic", "dental", "doctor", "hospital", "physio", "diagnostic", "skin", "eye care")),
                        ("food", ("restaurant", "cafe", "café", "food", "sweets", "bakery", "cake", "patisserie", "catering", "farsan",
                                  "kitchen and dining", "cloud kitchen")),
                        # no bare "plant": a "plant-based" bakery is not a factory
                        ("manufacturing", ("manufactur", "factory", "fabrication", "machining", "production")),
                        ("trading", ("trader", "trading", "wholesale", "distributor", "dealer", "supplier", "retail", "shop", "store"))):
        if any(w in text for w in words):
            return kind
    return "services"


async def process_map(p: dict):
    kind = business_type(p)

    def mock():
        return {"processes": [{"function": x.split(": ", 1)[0], "title": x.split(": ", 1)[1], "why": ""} for x in PROCESS_MAPS[kind]]}

    return await complete(RULES, f"""{profile_text(p)}

List the business processes this kind of business runs, grouped by function, so each can get an SOP.
Functions: Sales, Marketing, Operations/Delivery, Accounts, HR, Customer care. 15–30 processes, specific to THIS business type.
JSON only: {{"processes": [{{"function": "...", "title": "short name of the process", "why": "one line on why it matters here"}}]}}""", mock, 3000)


async def sop_scripts(p: dict, sop: dict):
    """The second pass on a written SOP: the exact words to use, what done looks like, and when to escalate."""
    def mock():
        return {"scripts": [{"label": "Message to the customer", "text": f"Hello, this is {_v(p, 'name', 'us')}. Here is what happens next: "
                                                                           f"{(sop.get('steps') or [{'title': 'we will update you'}])[0]['title'].lower()}."}],
                "done_when": f"{sop.get('title', 'This job')} is complete and noted, and the customer knows what happens next.",
                "escalation": "Bring in the owner if the customer is unhappy, or anything costs more than agreed."}

    steps = "\n".join(f"- {s.get('title')}" for s in sop.get("steps") or [])
    return await complete(RULES, f"""{profile_text(p)}

This SOP is written: {sop.get('title')}
Steps:
{steps}

Add the words to use (WhatsApp messages or what to say), what "done" looks like, and when to escalate.
JSON only: {{"scripts": [{{"label": "...", "text": "..."}}], "done_when": "...", "escalation": "..."}}. 1–4 scripts.""", mock, 1500)


# ───────────────────────── Hiring kit ─────────────────────────
async def jd(p: dict, inputs: dict):
    role, notes = inputs.get("role", "").strip(), inputs.get("notes", "").strip()

    def mock():
        return {"role": role, "summary": f"{_v(p, 'name', 'We')} is hiring a {role} in {_v(p, 'city', 'our city')}. You will help us serve customers who come to us for {_short(_v(p, 'industry', 'our work')).lower()}.",
                "responsibilities": [f"Handle day-to-day {role.lower()} work to our standards", "Keep customers updated on WhatsApp", "Report progress every week"],
                "requirements": ["Reliable and on time", "Comfortable using WhatsApp and a phone for work"] + _lines(notes, 4),
                "kpis": ["Work completed on time", "Customer feedback"],
                "interview_questions": [{"q": f"Tell me about a time you handled a difficult customer as a {role}.", "look_for": "Stays calm, solves the problem, follows up"},
                                        {"q": "How do you plan your day when everything is urgent?", "look_for": "A clear way to prioritise"},
                                        {"q": "Why do you want to work with a business like ours?", "look_for": "Has read about us; wants to stay"}],
                "scorecard": [{"criterion": "Relevant experience", "weight": 3}, {"criterion": "Attitude and reliability", "weight": 5}, {"criterion": "Communication", "weight": 4}],
                "salary_note": ""}

    return await complete(RULES, f"""{profile_text(p)}

Write a hiring kit for this role: {role}
Owner's notes: {notes or '(none)'}

Do NOT invent a salary; leave salary_note empty unless the notes give one.
JSON only: {{"role": "...", "summary": "2–3 sentences for the job post", "responsibilities": ["..."], "requirements": ["..."], "kpis": ["how success is measured"],
"interview_questions": [{{"q": "...", "look_for": "what a good answer shows"}}], "scorecard": [{{"criterion": "...", "weight": 1-5}}], "salary_note": ""}}
5–8 responsibilities, 4–8 requirements, 3–5 KPIs, 6–10 interview questions, 4–6 scorecard rows.""", mock, 3000)


# ───────────────────────── Decision log ─────────────────────────
async def decision(p: dict, inputs: dict):
    tasks_in = _lines(inputs.get("tasks", ""), 25)

    def mock():
        def verdict(t: str) -> tuple[str, str, str]:
            low = t.lower()
            if any(w in low for w in ("post", "reply", "remind", "invoice", "follow", "report", "whatsapp", "message", "data")):
                return "automate", "It repeats the same way every time.", "Hand it to a hub agent or an AI draft."
            if any(w in low for w in ("strategy", "pricing", "hire", "key client", "partner", "vision", "bank")):
                return "keep", "Only the owner should decide this.", "Block time for it every week."
            return "delegate", "Someone on the team can do it with a clear SOP.", "Write an SOP and assign it."
        out = []
        for t in tasks_in:
            v, why, nxt = verdict(t)
            out.append({"task": t[:200], "verdict": v, "reason": why, "next_step": nxt, "status": "todo"})
        return {"items": out}

    return await complete(RULES, f"""{profile_text(p)}

The owner does these tasks personally today (one per line):
{chr(10).join('- ' + t for t in tasks_in)}

For each task decide: automate (software or an AI agent can do it), delegate (a team member can do it with an SOP) or keep
(only the owner should). Be practical for a small Indian business.
JSON only: {{"items": [{{"task": "as written", "verdict": "automate|delegate|keep", "reason": "one sentence", "next_step": "one concrete next step", "status": "todo"}}]}}
Return every task, in the same order.""", mock, 3000)


# ───────────────────────── Role clarity ─────────────────────────
async def role(p: dict, inputs: dict):
    title, person, function, notes = (inputs.get(k, "").strip() for k in ("role", "person_name", "function", "notes"))

    def mock():
        return {"title": title, "person_name": person, "function": function or "Operations", "level": "doer",
                "definition": f"The {title} makes sure {_short(_v(p, 'industry', 'our work')).lower()} is delivered on time and to standard, so customers come back and refer others.",
                "responsibilities": [{"name": "Deliver the work", "tasks": ["Check today's jobs list every morning", "Prepare what is needed before starting", "Do the work using the SOP", "Update the job status on WhatsApp"]},
                                     {"name": "Keep customers informed", "tasks": ["Confirm the timeline with the customer", "Share progress at each stage", "Tell the manager at once if anything will be late"]}],
                "metrics": ["Jobs completed on time (%)", "Customer complaints per month"], "reports_to": "Owner", "version": 1}

    return await complete(RULES, f"""{profile_text(p)}

Write a one-page ROLE CLARITY document for this role: {title}
Person in the role (may be empty): {person or '(not given)'}
Business function: {function or '(choose the best fit)'} — the seven functions are {', '.join(FUNCTIONS)}.
Owner's notes: {notes or '(none)'}

Test: if someone woke this person at 3am and asked "what is your role?", they could answer from this page.
- definition: 2–3 lines, what the role is responsible for.
- responsibilities: no more than 4. For EACH, the tasks and activities as step-by-step actions in the order they happen (the most important part).
- metrics: how the role's effectiveness is measured.
- level: creator (sets goals and strategy), manager (plans and reviews) or doer (executes).
JSON only: {{"title": "...", "person_name": "...", "function": "one of the seven", "level": "creator|manager|doer", "definition": "...",
"responsibilities": [{{"name": "...", "tasks": ["step 1", "step 2"]}}], "metrics": ["..."], "reports_to": "...", "version": 1}}""", mock, 3500)


# ───────────────────────── Culture charter ─────────────────────────
HEADINGS = {"relationships": "When relationships turn resistant", "energy": "When the team's energy goes down",
            "commitment": "When commitment becomes conditional", "performance": "When performance is full of reasons"}


async def culture(p: dict, inputs: dict):
    notes = inputs.get("notes", "").strip()

    def mock():
        name = _v(p, "name", "Our")
        return {"name": f"The {name} Way", "purpose": "We care for each other and for the goal: no blame, no excuses, and we hold each other to high standards.",
                "situations": [
                    {"heading": "relationships", "situation": "Someone talks about a colleague behind their back",
                     "our_way": "Speak to the person directly first. If it doesn't resolve, involve the manager together.", "not_our_way": "Gossip, complain to others, take sides"},
                    {"heading": "energy", "situation": "The team is overloaded in a busy week",
                     "our_way": "Raise it early in the tactical meeting and re-plan together.", "not_our_way": "Silently miss deadlines or quietly stop caring"},
                    {"heading": "commitment", "situation": "Work is unevenly shared",
                     "our_way": "Agree who owns what in writing; review it every month.", "not_our_way": "\"That's not my job\" or doing the minimum"},
                    {"heading": "performance", "situation": "A target is missed",
                     "our_way": "Own it, find the root cause, agree the next action and date.", "not_our_way": "Blame customers, the market or each other"}]
                + [{"heading": "relationships", "situation": s[:200], "our_way": "", "not_our_way": ""} for s in _lines(notes, 6)]}

    return await complete(RULES, f"""{profile_text(p)}

Write a CULTURE CHARTER for this team. Culture rests on relationships: responsive relationships (trust, respect, care, connection)
create a resourceful state of energy, unconditional commitment, performance beyond reasons and extraordinary results; resistant
relationships (blame, ego, groupism, conflict between people) create the opposite. The team must be responsive to people AND to the goal
(an army, not a comfort-zone family and not a fear factory).

Situations the owner has seen (may be empty):
{notes or '(none)'}

List real workplace situations under four headings: relationships (turn resistant), energy (goes down), commitment (becomes conditional),
performance (filled with reasons). For each: our_way (how we handle it) and not_our_way.
JSON only: {{"name": "The <business> Way", "purpose": "one line", "situations": [{{"heading": "relationships|energy|commitment|performance", "situation": "...", "our_way": "...", "not_our_way": "..."}}]}}
3–5 situations per heading. Include the owner's situations.""", mock, 4000)


# ───────────────────────── Competence development plan ─────────────────────────
async def competence(p: dict, inputs: dict):
    title, notes = inputs.get("role", "").strip(), inputs.get("notes", "").strip()

    def mock():
        return {"role": title,
                "attributes": {"skills": ["Explaining options clearly to customers"], "knowledge": [f"Our products and prices for {_short(_v(p, 'industry', 'our work')).lower()}"],
                               "self_image": ["Sees themselves as a professional who solves problems"], "traits": ["Patient", "Organised"],
                               "motives": ["Wants to grow into a senior role"]},
                "plan": [{"attribute": "Explaining options clearly to customers", "people": "Sit in on the owner's customer calls for two weeks",
                          "resources": "Our product sheet and FAQ", "experiences": "Handle five customer calls with the owner listening", "by_when": "4 weeks"}]}

    return await complete(RULES, f"""{profile_text(p)}

Write a COMPETENCE DEVELOPMENT PLAN for this role: {title}
Owner's notes: {notes or '(none)'}

Competence has five parts: skills, knowledge, self-image, traits and motives. List the attributes the role needs under each.
Then a plan for the most important gaps using P-R-E: People to learn from, Resources to learn from, Experiences to learn from, with a by-when.
JSON only: {{"role": "...", "attributes": {{"skills": [], "knowledge": [], "self_image": [], "traits": [], "motives": []}},
"plan": [{{"attribute": "...", "people": "...", "resources": "...", "experiences": "...", "by_when": "..."}}]}}
2–5 items per attribute, 3–6 plan rows.""", mock, 3000)


# ───────────────────────── Monthly business review ─────────────────────────
async def review(p: dict, inputs: dict):
    data = inputs.get("data") or {}
    period = inputs.get("period", "")

    def mock():
        n = data.get("numbers", {})
        leads, won = n.get("leads", 0), n.get("won", 0)
        def pl(k, one, many):
            return f"{k} {one if k == 1 else many}"
        wins = [f"{pl(won, 'new customer', 'new customers')} won from {pl(leads, 'inquiry', 'inquiries')}"] if won else []
        concerns = [] if leads else ["No website inquiries this month — share your website link more often"]
        if n.get("waiting", 0):
            concerns.append(f"{n['waiting']} lead{' is' if n['waiting'] == 1 else 's are'} still waiting for a first reply")
        if n.get("overdue_amount"):
            concerns.append(f"Money owed and overdue: {n['overdue_amount']}")
        labels = {"leads": "Inquiries", "won": "Customers won", "lost": "Lost", "waiting": "Waiting for a first reply",
                  "overdue_amount": "Money overdue", "collected": "Collected", "site_views": "Website views"}
        return {"period": period, "headline": f"{period}: {pl(leads, 'inquiry', 'inquiries')}, {won} won.",
                "numbers": {labels.get(k, k.replace("_", " ").capitalize()): str(v) for k, v in n.items()},
                "wins": wins or ["The hub is set up and your website is live"],
                "concerns": concerns or ["Nothing urgent — keep the weekly rhythm going"],
                "recommendations": [{"action": "Reply to every new lead within an hour", "why": "Fast replies win more customers"},
                                    {"action": "Post on WhatsApp Status three times this week", "why": "More visitors means more inquiries"}],
                "focus_next_month": "Follow up every open lead and ask every happy customer for a review."}

    import json
    return await complete(RULES + "\nYou are the owner's business advisor writing an honest monthly review. Use ONLY the numbers given.", f"""{profile_text(p)}

MONTH: {period}
DATA FROM THE HUB (JSON): {json.dumps(data, default=str)[:6000]}

Write the monthly business review: what went well, what needs attention, what to do next month. Be specific to these numbers; never invent any.
JSON only: {{"period": "{period}", "headline": "one line", "numbers": {{"label": "value"}}, "wins": ["..."], "concerns": ["..."],
"recommendations": [{{"action": "...", "why": "..."}}], "focus_next_month": "one sentence"}}
2–5 wins, 2–5 concerns, 3–5 recommendations.""", mock, 3000)
