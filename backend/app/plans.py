"""
The plan ladder and every feature the hub has, in one registry.

Defaults live in code. The admin can change any feature (which plan it is in, on/off for everyone, its name and
description, its place in the menu, whether a locked one shows as a teaser) and any plan (name, price, AI runs, team
size, how long it lasts) from Admin → Plans & features. Those changes are stored in the `config` collection and applied
here in place, so every `plans.has(...)`, `plans.PLANS[...]` and `plans.FEATURES[...]` sees them at once.

A plan can come from four places — the admin, a referral trial, the Membership subscription, or a one-time purchase —
and the highest one that is still valid wins. The admin can also give one owner a single feature until a date (a grant).
"""
import copy
import os
import time
from datetime import datetime

from .db import db, now

TIERS = ["free", "lite", "program", "running", "growth", "office"]
GROUPS = ["Start", "Learn", "Structure", "Systems", "Scale", "AI team", "Grow"]
ROLES = ("staff", "manager", "owner")


def _days(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


DEFAULT_PLANS = {
    "free":    {"name": "Free", "price_minor": 0, "period": "", "runs": 10, "billing": "free", "team_size": 0,
                "access_days": None, "short": "Free", "pitch": "Given free at the 2-day Business AI workshop",
                "promise": "You're online today", "agent_level": "One-click drafts"},
    "lite":    {"name": "Membership", "price_minor": 199_900, "period": "month", "runs": 150, "billing": "subscription", "team_size": 0,
                "access_days": None, "short": "Membership", "pitch": "A weekly rhythm that keeps the business moving",
                "promise": "AI tools that already know your business", "agent_level": "A weekly plan made for you"},
    "program": {"name": "Action Program", "price_minor": 1_000_000, "period": "", "runs": 300, "billing": "one_time", "team_size": 0,
                "access_days": _days("ACCESS_DAYS_PROGRAM", 365), "short": "₹10k", "pitch": "The Action Program's frameworks, filled in with your own numbers",
                "promise": "The program, done with your own data", "agent_level": "Follow-ups and reviews written for you"},
    "running": {"name": "Running the Business", "price_minor": 2_500_000, "period": "", "runs": 400, "billing": "one_time", "team_size": 0,
                "access_days": _days("ACCESS_DAYS_RUNNING", 365), "short": "₹25k", "pitch": "Money and customers looked after every day",
                "promise": "Sales and cash on a system", "agent_level": "Money and customers looked after every morning"},
    "growth":  {"name": "Growth Mentorship", "price_minor": 15_000_000, "period": "6 months", "runs": 3000, "billing": "one_time", "team_size": 10,
                "access_days": _days("ACCESS_DAYS_GROWTH", 180), "short": "₹1.5L", "pitch": "Set up on Employz.ai, with 6 AI staff, in 6 months",
                "promise": "6 AI staff on your WhatsApp and Employz.ai", "agent_level": "6 AI staff on your WhatsApp and Employz.ai"},
    "office":  {"name": "LegacyWorkforce", "price_minor": 60_000_000, "period": "", "runs": 6000, "billing": "one_time", "team_size": 25,
                "access_days": _days("ACCESS_DAYS_OFFICE", 365), "short": "₹6L", "pitch": "A full AI workforce: 30 AI staff in 5 departments",
                "promise": "A 30-strong AI workforce that plans and acts", "agent_level": "A 30-strong AI workforce that plans and acts"},
}
PLAN_EDITABLE = {"name": str, "short": str, "price_minor": int, "runs": int, "team_size": int, "access_days": int, "pitch": str, "promise": str,
                 "razorpay_plan_id": str}


def F(tier, group, label, what, path=None, role="staff", core=False):
    return {"tier": tier, "group": group, "label": label, "what": what, "path": path, "role": role, "core": core}


# key → definition. Order here is the default order in the menu and on the plans page.
DEFAULT_FEATURES: dict[str, dict] = {
    # ───────── Free: given at the workshop ─────────
    "home":            F("free", "Start", "Home", "Your next steps and what's new", "/home", core=True),
    "recordings":      F("free", "Learn", "Recordings", "Workshop and program calls, with notes", "/recordings"),
    "business_brain":  F("free", "Structure", "Knowledge Hub", "Your Business Brain: fill it once, and every part of the hub and every AI task uses it", "/knowledge", "owner", core=True),
    "brand_message":   F("free", "Structure", "Brand message", "Your one-line brand message and a 30-second pitch", "/brand", "owner"),
    "business_score":  F("free", "Structure", "Business score", "Your Business AI Score out of 100, the top 3 fixes and a card to share", "/score", "owner"),
    "website":         F("free", "Systems", "Website", "Your website at name.employz.ai, with an inquiry form and a Chat on WhatsApp button", "/website", "owner"),
    "leads":           F("free", "Systems", "Leads", "Every inquiry in one place, with a WhatsApp alert to you", "/leads"),
    "ai_writer":       F("free", "Scale", "AI Writer", "A week of posts, replies to customers, answers about your business, and English polishing", "/writer"),
    "outputs":         F("free", "Scale", "My outputs", "Everything the AI has written for you, in one place", "/outputs"),
    "launch_kit":      F("free", "Grow", "Launch kit", "A WhatsApp Status poster, a digital visiting card and a QR stand, in your colours", "/launch-kit", "owner"),
    "invite":          F("free", "Grow", "Invite & earn", "Your invite link: rewards when friends go live, 30% commission when they pay", "/invite", "owner"),
    "plans_billing":   F("free", "Grow", "Plans & billing", "Your plan, payments and upgrades", "/plans", "owner", core=True),
    # ───────── Membership ─────────
    "week":            F("lite", "Start", "This week", "Monday: 7 posts and 3 actions. Every day: one 5-minute action and your streak. Friday: a 2-minute check-in and your weekly score", "/week", "manager"),
    "badge_off":       F("lite", "Systems", "No badge", "No “Built free” badge on your website"),
    "content_calendar": F("lite", "Scale", "Content calendar", "A month of posts with Indian festivals and occasions, ready in advance", "/calendar"),
    "score_recheck":   F("lite", "Structure", "Monthly score re-check", "Re-take your Business Score every month and see what moved"),
    "member_call":     F("lite", "Learn", "Member call", "The monthly member call: send your questions before, watch the recording after", "/member-call", "owner"),
    "cohort_board":    F("lite", "Grow", "Cohort board", "Streaks and the most consistent owners in your workshop batch", "/board", "owner"),
    "ai_tools":        F("lite", "Scale", "AI tools", "Ready-made AI tools that already know your business — no setting up", "/tools"),
    "tool_content":    F("lite", "Scale", "Content Agent", "A month of posts and reel scripts in your voice"),
    "tool_video":      F("lite", "Scale", "Video scripts", "Hook, script and captions, ready to shoot on a phone"),
    "tool_google":     F("lite", "Scale", "Google profile & reviews", "Google Business profile posts and replies to your Google reviews"),
    "tool_proposal":   F("lite", "Scale", "Proposal writer", "A proposal shaped to what this customer needs"),
    "tool_case_study": F("lite", "Scale", "Case study writer", "A happy customer's result turned into a story you can share"),
    "polish_voice":    F("lite", "Scale", "English polisher, full", "Polish voice notes too, and get letters and emails in the tone you pick"),
    "sop_library":     F("lite", "Systems", "SOP library", "Step-by-step SOPs written with AI and kept in one place", "/sops"),
    "magic_number":    F("lite", "Structure", "Magic Number", "Your revenue target worked back to customers, inquiries and daily activity — it tracks itself", "/magic", "manager"),
    "gaps_scan":       F("lite", "Structure", "Gaps scan", "12 questions that find the silent killers in your business, with this month's 3 fixes", "/gaps", "owner"),
    "funnel":          F("lite", "Systems", "Funnel & dashboard", "Visitors to inquiries to customers, week by week, and how fast leads get a reply", "/insights", "manager"),
    # v2.1 — the AI Advantage Membership launch (new features start in Membership; move them in Admin → Features & plans)
    "smart_dashboard": F("free", "Start", "Dashboard", "Your whole business on one screen — leads, money, posts, targets and what needs you today", "/dashboard", "manager"),
    "inquiry_desk":    F("lite", "Systems", "Inquiries", "Every inquiry from your website, email and WhatsApp in one place, answered with a proposal", "/inquiries"),
    "gateway_invoicing": F("lite", "Systems", "Automatic invoices", "Connect your payment gateway; every payment becomes a GST invoice on its own", None, "owner"),
    "auto_reminders":  F("lite", "AI team", "Automatic payment follow-ups", "Switch it on once: reminders go out by email as money falls due and goes overdue"),
    "compliance_calendar": F("lite", "Systems", "Compliance calendar", "Every filing, licence, renewal and document expiry in one calendar, with reminders", "/compliance", "manager"),
    "brand_kit":       F("lite", "Structure", "Brand kit", "Your brand message in every version, your design system and ready designs", "/brand-kit", "owner"),
    "cash_flow":       F("lite", "Structure", "Cash flow", "The next 30 days of money in and out, day by day — see a shortfall before it comes", "/cash-flow", "manager"),
    # ───────── Action Program ─────────
    "approvals":       F("program", "Start", "Send list", "Messages your automations wrote, ready to send from your WhatsApp with one tap", "/approvals", "manager"),
    "automations":     F("program", "AI team", "Automations", "What runs for you on its own, with a switch for each", "/agents", "manager"),
    "lead_magnet":     F("program", "Scale", "Lead magnet", "Writes your guide and publishes it at name.employz.ai/guide with a form that captures leads", "/lead-magnet", "owner"),
    "offer_ladder":    F("program", "Structure", "Offer ladder", "Entry, core and premium offers, shown as a section on your website", "/offers", "owner"),
    "goals":           F("program", "Structure", "Goals", "Financial, functional and learning goals with weekly check-ins", "/goals", "manager"),
    "revenue_levers":  F("program", "Structure", "Revenue levers", "Customers × average value × frequency, your 5-stage funnel and the 10×10×10 pledge", "/levers", "manager"),
    "customer_portfolio": F("program", "Structure", "Customer portfolio", "Your customer segments sorted Amazing, Breadwinning, Convenience and Dangerous, with the leads each needs", "/portfolio", "manager"),
    "pipeline":        F("program", "Systems", "Sales pipeline", "Leads on the Football Field — the 5 sales stages — and where deals stop", "/pipeline"),
    "funnel_leaks":    F("program", "Systems", "Funnel leak finder", "Your funnel numbers in; the biggest leak and the fixes out", "/leaks", "manager"),
    "end_goals":       F("program", "Structure", "4 end goals", "Identity, income, financial freedom and legacy, written down with dates", "/end-goals", "owner"),
    "tool_persona":    F("program", "Scale", "Persona builder", "Your ideal customer described from evidence: needs, desires and problems"),
    "tool_sales_roles": F("program", "Scale", "Sales role map", "Who opens doors and who closes deals, and what each needs"),
    "tool_telecalling": F("program", "Scale", "Telecalling script", "A calling script with objection branches, ready for a person or a voice agent"),
    "tool_marketing_plan": F("program", "Scale", "30-day marketing plan", "Content, strategies, channels and systems for the next 30 days"),
    "follow_up_agent": F("program", "AI team", "Follow-up automation", "Follow-ups on day 1, 3 and 7 after every inquiry, ready to send with one tap"),
    "review_agent":    F("program", "AI team", "Review requests", "A review request to every won customer, ready to send with one tap"),
    "digest_agent":    F("program", "AI team", "Morning digest", "One WhatsApp line each morning: new leads, replies waiting, money overdue, tasks due"),
    "decision_log":    F("program", "Structure", "Decision log", "List what's on your plate; the AI sorts it into automate, delegate or keep", "/decisions", "owner"),
    "voice_sop":       F("program", "Systems", "SOP from a voice note", "Record how a job is done; get a written SOP"),
    "hiring":          F("program", "Scale", "Hiring kits", "Job post, interview questions and a scorecard for any role", "/hiring", "manager"),
    # ───────── Running the Business ─────────
    "crm_sync":        F("running", "Systems", "Employz.ai CRM", "An Employz.ai account with your sales pipeline; every hub lead synced into it", "/crm", "owner"),
    "customers":       F("running", "Systems", "Customers", "Your customer list: what they bought, when to reorder, their occasions", "/customers", "manager"),
    "money_owed":      F("running", "Systems", "Money owed", "Every payment a customer still owes, what's overdue and what came in", "/money", "manager"),
    "reminder_agent":  F("running", "AI team", "Payment reminders", "Polite reminders for overdue money, prepared every morning, ready to send with one tap"),
    "quotations":      F("running", "Systems", "Quotations & invoices", "A voice note or a few lines turned into a GST quotation; invoices from accepted quotes", "/quotes", "manager"),
    "quote_followup":  F("running", "AI team", "Quote follow-ups", "A follow-up for every open quotation, ready to send with one tap"),
    "customer_desk":   F("running", "AI team", "Customer desk", "Referral requests, reorder reminders, quiet-customer alerts and occasion greetings, ready to send with one tap"),
    # ───────── Growth Mentorship ─────────
    "setup_tracker":   F("growth", "Start", "Your setup", "Your 6-month setup, done for you by the team: what's done and what's next", "/setup", "owner"),
    "control_room":    F("growth", "Start", "Control Room", "Target vs actual, pipeline, chats, calls and money on one screen; a CEO report every Monday", "/control", "manager"),
    "ai_staff":        F("growth", "AI team", "AI staff", "6 AI staff: Sales Executive, Telecaller, Marketing, Accounts, Customer Care and Chief of Staff", "/staff", "manager"),
    "whatsapp_ai":     F("growth", "Systems", "WhatsApp chats", "Your Sales Executive replies to every inquiry on your own WhatsApp number, 24×7; your team sees every chat", "/chats"),
    "voice_agent":     F("growth", "AI team", "AI Telecaller", "Calls new leads within minutes and re-calls old ones; every call summarised and scored"),
    # v2.1: from Membership, showing only the connections the plan unlocks (the payment gateway first; WhatsApp, voice… later plans)
    "connections":     F("lite", "Systems", "Connections", "Your own accounts — your payment gateway, and from higher plans your WhatsApp number, CRM and voice agent", "/connections", "owner"),
    "custom_domain":   F("growth", "Systems", "Own domain", "Your own domain with a multi-page website, connected for you"),
    "company_brain":   F("growth", "Structure", "Company Brain", "Price list, catalogue, FAQs and past quotations that every AI staff member answers from", "/company-brain", "manager"),
    "customer_import": F("growth", "Systems", "Customer import", "Your old customer list imported, cleaned and sorted A, B and C"),
    "campaigns":       F("growth", "Scale", "Money campaigns", "One campaign a month — old-customer revival, a festival offer or a referral drive. Approve once; it runs", "/campaigns", "manager"),
    "training_gym":    F("growth", "Scale", "Sales Training Gym", "Practise with an AI customer who haggles; every call scored with 3 fixes", "/gym"),
    "results_report":  F("growth", "AI team", "AI results", "Every month: hours saved, leads handled, calls made, money collected", "/results", "owner"),
    "team_logins":     F("growth", "Systems", "Team", "Team logins with roles for your managers and staff", "/team", "owner"),
    "role_clarity":    F("growth", "Structure", "Role clarity", "A one-page role clarity document for every team member", "/roles"),
    "culture_plan":    F("growth", "Structure", "Culture charter", "Our way and not our way, written with your team", "/culture"),
    "competence_plan": F("growth", "Structure", "Competence plans", "A competence development plan for every role", "/competence"),
    "meetings":        F("growth", "Scale", "Meetings", "Strategic and tactical meetings; notes become tasks with owners and dates", "/meetings"),
    "tasks":           F("growth", "Start", "Tasks", "Team tasks with owners and due dates", "/tasks"),
    "monthly_review":  F("growth", "Scale", "Monthly reviews", "On the 1st, an honest review of last month with next steps", "/reviews", "manager"),
    # ───────── LegacyWorkforce ─────────
    "workforce":       F("office", "AI team", "Workforce", "30 AI staff — 6 in each of 5 departments — picked from 82 roles; swap any time", "/workforce", "owner"),
    "agentic_office":  F("office", "AI team", "AI office", "Give one instruction; the Chief of Staff plans it across departments and the staff do it", "/office", "manager"),
    "agents_act":      F("office", "AI team", "Staff act on their own", "AI staff can act without waiting; money messages and messages with links always wait"),
    "more_connections": F("office", "Systems", "Tally, email and calendar", "Connections to Tally, email and your calendar"),
}
for _i, (_k, _f) in enumerate(DEFAULT_FEATURES.items()):
    _f.update(key=_k, order=_i * 10, on=True, teaser=True, sub="")
DEFAULT_FEATURES["business_brain"]["sub"] = "Business Brain"
# One dashboard: the Control Room is the Dashboard with the Growth tiles switched on, so it has no menu entry of its own
# (its Monday CEO report and /control stay).
DEFAULT_FEATURES["control_room"]["path"] = None

FEATURE_EDITABLE = {"tier": str, "group": str, "label": str, "what": str, "order": int, "on": bool, "teaser": bool}

# Live values (defaults with the admin's changes applied). Mutated in place so imports stay valid.
PLANS: dict[str, dict] = copy.deepcopy(DEFAULT_PLANS)
META: dict[str, dict] = copy.deepcopy(DEFAULT_FEATURES)
FEATURES: dict[str, tuple] = {}          # key → (tier, group, label): the short form most of the app reads
AGENT_LEVEL: dict[str, str] = {}
_state = {"loaded_at": 0.0, "stamp": None}


def _rebuild() -> None:
    FEATURES.clear()
    FEATURES.update({k: (m["tier"], m["group"], m["label"]) for k, m in META.items()})
    AGENT_LEVEL.clear()
    AGENT_LEVEL.update({t: PLANS[t]["agent_level"] for t in TIERS})


_rebuild()


def apply_overrides(features: dict | None, plan_over: dict | None) -> None:
    """Defaults + the admin's changes → the live registry. Unknown keys and bad values are ignored."""
    META.clear()
    META.update(copy.deepcopy(DEFAULT_FEATURES))
    for key, patch in (features or {}).items():
        if key not in META or not isinstance(patch, dict):
            continue
        for field, kind in FEATURE_EDITABLE.items():
            if field not in patch or not isinstance(patch[field], kind):
                continue
            if field == "tier" and patch[field] not in TIERS:
                continue
            if field == "group" and patch[field] not in GROUPS:
                continue
            if META[key]["core"] and field in ("tier", "on"):
                continue  # Home, Business Brain and Plans stay free and on: the hub can't work without them
            META[key][field] = patch[field]
    PLANS.clear()
    PLANS.update(copy.deepcopy(DEFAULT_PLANS))
    for tier, patch in (plan_over or {}).items():
        if tier not in PLANS or not isinstance(patch, dict):
            continue
        for field, kind in PLAN_EDITABLE.items():
            v = patch.get(field)
            if v is None or not isinstance(v, kind) or isinstance(v, bool):
                continue
            if tier == "free" and field == "price_minor":
                continue
            PLANS[tier][field] = v
    _rebuild()


async def load(force: bool = False) -> None:
    """Reads the admin's changes from the database. Cheap: two reads, at most every few seconds per worker."""
    if not force and time.monotonic() - _state["loaded_at"] < 5:
        return
    _state["loaded_at"] = time.monotonic()
    feats = await db().config.find_one({"_id": "features"}) or {}
    plan_doc = await db().config.find_one({"_id": "plans"}) or {}
    stamp = (feats.get("updated_at"), plan_doc.get("updated_at"))
    if force or stamp != _state["stamp"]:
        _state["stamp"] = stamp
        apply_overrides(feats.get("overrides"), plan_doc.get("overrides"))


def reset_cache() -> None:
    _state.update(loaded_at=0.0, stamp=None)
    apply_overrides(None, None)


# ───────────────────────── plans ─────────────────────────
def rank(tier: str) -> int:
    return TIERS.index(tier) if tier in TIERS else 0


def _valid(until, at: datetime) -> bool:
    return bool(until) and until > at


def plan_sources(user: dict, at: datetime | None = None) -> dict:
    """Every place a plan can come from, and whether it is valid right now."""
    at = at or now()
    out = {"admin": user.get("plan", "free")}
    trial = user.get("trial") or {}
    if _valid(trial.get("until"), at):
        out["trial"] = trial.get("plan", "free")
    sub = user.get("sub") or {}
    if sub.get("status") in ("active", "pending") or _valid(sub.get("paid_until"), at):
        out["subscription"] = "lite"
    best_paid = "free"
    for tier, until in (user.get("access") or {}).items():
        if tier in TIERS and _valid(until, at) and rank(tier) > rank(best_paid):
            best_paid = tier
    if best_paid != "free":
        out["purchase"] = best_paid
    return out


def effective_plan(user: dict, at: datetime | None = None) -> str:
    """The highest valid plan from any source. A trial or purchase never lowers a plan."""
    return max(plan_sources(user, at).values(), key=rank)


def plan_source(user: dict, at: datetime | None = None) -> str:
    """Where the plan in use comes from: purchase, subscription, trial or admin (the plan set on the account)."""
    sources = plan_sources(user, at)
    effective = max(sources.values(), key=rank)
    return next((k for k in ("purchase", "subscription", "trial", "admin") if sources.get(k) == effective), "admin")


# ───────────────────────── features ─────────────────────────
def granted(user: dict, feature: str, at: datetime | None = None) -> bool:
    until = (user.get("grants") or {}).get(feature)
    return isinstance(until, datetime) and until > (at or now())


def has(user: dict, feature: str) -> bool:
    """On for everyone (the admin's switch), and either in the owner's plan or granted to this owner by the admin."""
    m = META.get(feature)
    if not m or not m["on"]:
        return False
    return rank(effective_plan(user)) >= rank(m["tier"]) or granted(user, feature)


def tier_of(feature: str) -> str:
    return META[feature]["tier"] if feature in META else "office"


def label_of(feature: str) -> str:
    return META[feature]["label"] if feature in META else feature


def monthly_runs(user: dict) -> int:
    return PLANS[effective_plan(user)]["runs"]


def team_size(user: dict) -> int:
    return max((PLANS[t]["team_size"] for t in TIERS if rank(t) <= rank(effective_plan(user))), default=0)


def unlocking_tier(feature: str) -> dict:
    tier = tier_of(feature)
    return {"tier": tier, **PLANS[tier]}


def feature_view(key: str) -> dict:
    m = META[key]
    return {"key": key, "label": m["label"], "sub": m.get("sub", ""), "what": m["what"], "tier": m["tier"], "group": m["group"], "path": m["path"],
            "role": m["role"], "order": m["order"], "on": m["on"], "teaser": m["teaser"], "core": m["core"]}


def ladder() -> list[dict]:
    out = []
    for t in TIERS:
        unlocks = [{"key": k, "label": m["label"], "what": m["what"], "pillar": m["group"], "group": m["group"]}
                   for k, m in sorted(META.items(), key=lambda kv: (GROUPS.index(kv[1]["group"]), kv[1]["order"]))
                   if m["tier"] == t and m["on"]]
        out.append({"tier": t, **{k: v for k, v in PLANS[t].items() if k != "razorpay_plan_id"}, "unlocks": unlocks})
    return out


def menu_for(owner: dict, role: str) -> list[dict]:
    """The owner's menu: every page this role may see, grouped, in the admin's order. A locked page shows (with the plan
    that unlocks it) only when the admin lets it show as a teaser. Features switched off for everyone never show."""
    rank_role = ROLES.index(role)
    groups: dict[str, list] = {g: [] for g in GROUPS}
    for key, m in META.items():
        if not m["path"] or not m["on"] or ROLES.index(m["role"]) > rank_role:
            continue
        unlocked = has(owner, key)
        if not unlocked and not m["teaser"]:
            continue
        groups[m["group"]].append({"key": key, "path": m["path"], "label": m["label"], "sub": m.get("sub", ""), "locked": not unlocked,
                                   "tier": m["tier"], "tier_name": PLANS[m["tier"]]["name"], "tier_short": PLANS[m["tier"]]["short"],
                                   "order": m["order"]})
    return [{"group": g, "items": sorted(items, key=lambda i: i["order"])} for g, items in groups.items() if items]
