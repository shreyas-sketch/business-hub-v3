"""
The plan ladder and every feature the hub has, in one registry.

Defaults live in code. The admin can change any feature (which plan it is in, on/off for everyone, its name and
description, its place in the menu, whether a locked one shows as a teaser) and any plan (name, price, AI runs, team
size, how long it lasts) from Admin → Plans & features. Those changes are stored in the `config` collection and applied
here in place, so every `plans.has(...)`, `plans.PLANS[...]` and `plans.FEATURES[...]` sees them at once.

A plan can come from four places — the admin, a referral trial, the Membership subscription, or a one-time purchase —
and the highest one that is still valid wins. The admin can also give one owner a single feature until a date (a grant).

v2.3 (OwnerDesk): every feature also has an engine (the 6 engines of a business, plus start / more / account), a "show"
switch (in the menu or only working behind other pages), "paid_only" (closed during a free or event trial) and an optional
event day. The engine menu, the hidden features and the new prices are switched on together by the v2.3 setup
(app/v23.py), stored in the same `config` collection as any admin change — so the code defaults stay as they were.
During a live event, an attendee's engines open day by day: Lead and Sales on Day 1, Conversion and Operations on Day 2,
Brand and Data on Day 3 (Admin → Live event → the event day).
"""
import copy
import os
import time
from datetime import datetime

from .config import settings
from .db import db, now

TIERS = ["free", "lite", "program", "running", "growth", "office"]
GROUPS = ["Start", "Learn", "Structure", "Systems", "Scale", "AI team", "Grow"]
ROLES = ("staff", "manager", "owner")

# v2.3: the 6 engines, in menu order, with the event day each one opens on
ENGINES = [
    {"key": "lead", "label": "Lead", "day": 1, "blurb": "Get found, and turn visitors into inquiries."},
    {"key": "sales", "label": "Sales", "day": 1, "blurb": "Grow revenue from the customers you can reach, and get invoices out the same day."},
    {"key": "conversion", "label": "Conversion", "day": 2, "blurb": "Every inquiry caught, answered and tracked to a deal."},
    {"key": "operations", "label": "Operations", "day": 2, "blurb": "The money, deadlines and know-how that keep the business running without you."},
    {"key": "brand", "label": "Brand", "day": 3, "blurb": "One voice and one look everywhere, and a month of posts ready."},
    {"key": "data", "label": "Data", "day": 3, "blurb": "Know where the money goes, and keep what your key people know."},
]
ENGINE_KEYS = [e["key"] for e in ENGINES]
ENGINE_DAY = {e["key"]: e["day"] for e in ENGINES}
SECTIONS = ["start", *ENGINE_KEYS, "more", "account"]
SECTION_LABEL = {"start": "Start", **{e["key"]: f"{e['label']} engine" for e in ENGINES}, "more": "More", "account": "Account"}
EVENT_RUNS_DEFAULT = 50      # AI tokens an event account gets for the event (Admin → Live event can change it)
EVENT_IMAGES_DEFAULT = 7     # social media images on the free and event plans


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
for _t, _p in DEFAULT_PLANS.items():   # v2.3: social media images a month, extra AI tokens a month for paying members, a yearly price
    _p.update(images=EVENT_IMAGES_DEFAULT if _t == "free" else 30, extra_runs=0, price_year_minor=0)
PLAN_EDITABLE = {"name": str, "short": str, "price_minor": int, "runs": int, "team_size": int, "access_days": int, "pitch": str, "promise": str,
                 "razorpay_plan_id": str, "images": int, "extra_runs": int, "price_year_minor": int}


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
    # v2.2 — the Data area of the Membership
    "expenses":        F("lite", "Structure", "Expenses", "Every company expense in one place: the bill, the GST you can claim back, and where the money went each month", "/expenses", "staff"),
    "bank_reader":     F("lite", "Structure", "Bank statement reader", "Upload a bank statement: money in and out, where it went, and the payments that repeat — into Expenses in one tap", "/bank-statement", "owner"),
    "gst_setaside":    F("lite", "Structure", "GST set-aside", "GST on your sales minus GST on your bills: about what to keep aside for the next GST payment", "/gst", "manager"),
    "customer_risk":   F("lite", "Structure", "Customer risk", "Who your sales depend on, and regular customers who have gone quiet — with the money at risk", "/customer-risk", "manager"),
    "getting_paid":    F("lite", "Structure", "Getting paid", "How long each customer really takes to pay, and the money stuck by how old it is", "/getting-paid", "manager"),
    # v2.2 agents in the Membership
    "competitor_watch": F("lite", "Scale", "Competitor Watch", "Every Monday: what changed on your competitors' websites and Google ratings, and what it means for you", "/competitors", "manager"),
    "reputation":      F("lite", "Scale", "Reviews & reputation", "Every review answered: replies drafted for you to approve, happy reviewers asked for a referral, happy customers asked for a review", "/reputation", "manager"),
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
    "win_back":        F("program", "AI team", "Win-Back", "Customers who haven't bought in 60 days or more get a personal message on WhatsApp or email — drafted for you, sent when you tap", "/win-back", "manager"),
    "market_research": F("program", "Scale", "Content & company research", "Post ideas from your competitor report, and a plan to find companies that fit your ideal customer — with ready search links, no scraping", "/research", "manager"),
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
    "lead_response":   F("growth", "AI team", "Lead Response", "Every new inquiry gets a first reply within 2 minutes, day or night, from your own WhatsApp number — with a call or visit booked. Prices and quotes come to you.", "/lead-response", "manager"),
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
    # ───────── v2.3: OwnerDesk (off until the v2.3 setup switches them on) ─────────
    "engine_audit":    F("free", "Start", "Engine Audit", "Check your business against the 6 engines: what each gap costs you in hours and rupees, and what fixing it unlocks", "/audit", "owner"),
    "lead_ideas":      F("lite", "Structure", "Ways to get more leads", "Lead ideas that fit your kind of business, each with a first step and the hub feature that does it", "/lead-ideas", "manager"),
    "crm":             F("lite", "Systems", "CRM", "Every contact and company with their deals, calls, notes and follow-up dates — import and export too", "/contacts"),
    "social_media":    F("lite", "Scale", "Social media", "Posts with images in your colours and a caption, ready to post — a counter shows the images left this month", "/social"),
    "brochures":       F("lite", "Structure", "Brochures", "A business brochure and a products and services brochure, written from your brand kit, website and competitor research", None, "owner"),
    "whatsapp_tool":   F("growth", "Systems", "WhatsApp tool", "Connect your own WhatsApp number through the WhatsApp service you use, so messages go from your number", None, "owner"),
    "key_person":      F("lite", "Structure", "Key-person knowledge", "What your key employee knows, written down as an AI agent skill, a role card and SOPs — so the business doesn't depend on one person", "/key-person", "owner"),
}
V23_NEW = ("engine_audit", "lead_ideas", "crm", "social_media", "brochures", "key_person")
# Each feature's engine. Anything not listed here sits under "More" (only seen when the admin switches it on).
DEFAULT_ENGINE = {
    "home": "start", "business_brain": "start", "engine_audit": "start",
    "website": "lead", "lead_magnet": "lead", "leads": "lead",
    "revenue_levers": "sales", "lead_ideas": "sales", "quotations": "sales", "gateway_invoicing": "sales", "quote_followup": "sales",
    "offer_ladder": "sales",
    "inquiry_desk": "conversion", "crm": "conversion", "pipeline": "conversion", "customers": "conversion", "follow_up_agent": "conversion",
    "crm_sync": "conversion", "funnel_leaks": "conversion", "approvals": "conversion", "whatsapp_ai": "conversion", "lead_response": "conversion",
    "smart_dashboard": "operations", "money_owed": "operations", "reminder_agent": "operations", "auto_reminders": "operations",
    "compliance_calendar": "operations", "sop_library": "operations", "voice_sop": "operations", "magic_number": "operations",
    "cash_flow": "operations",
    "brand_kit": "brand", "brand_message": "brand", "social_media": "brand", "content_calendar": "brand", "brochures": "brand",
    "launch_kit": "brand", "competitor_watch": "brand",
    "key_person": "data", "expenses": "data", "bank_reader": "data", "gst_setaside": "data", "customer_risk": "data", "getting_paid": "data",
    "whatsapp_tool": "account", "plans_billing": "account", "connections": "account", "recordings": "account", "member_call": "account", "invite": "account",
    "team_logins": "account",
}
for _i, (_k, _f) in enumerate(DEFAULT_FEATURES.items()):
    _f.update(key=_k, order=_i * 10, on=_k not in V23_NEW, teaser=True, sub="", engine=DEFAULT_ENGINE.get(_k, "more"), show=True,
              paid_only=False, day=-1)   # day -1: the engine's own day
DEFAULT_FEATURES["business_brain"]["sub"] = "Business Brain"
# One dashboard: the Control Room is the Dashboard with the Growth tiles switched on, so it has no menu entry of its own
# (its Monday CEO report and /control stay).
DEFAULT_FEATURES["control_room"]["path"] = None

FEATURE_EDITABLE = {"tier": str, "group": str, "label": str, "what": str, "order": int, "on": bool, "teaser": bool,
                    "engine": str, "show": bool, "paid_only": bool, "day": int}

# Hub-wide settings outside the registry (the v2.3 setup turns the engine menu on; the admin edits the account links).
DEFAULT_UI = {"menu": "groups", "community_link": "", "support_email": "", "pay_monthly_link": "", "pay_yearly_link": "", "v23": None}
UI_EDITABLE = {"menu": str, "community_link": str, "support_email": str, "pay_monthly_link": str, "pay_yearly_link": str}

# Live values (defaults with the admin's changes applied). Mutated in place so imports stay valid.
PLANS: dict[str, dict] = copy.deepcopy(DEFAULT_PLANS)
META: dict[str, dict] = copy.deepcopy(DEFAULT_FEATURES)
FEATURES: dict[str, tuple] = {}          # key → (tier, group, label): the short form most of the app reads
AGENT_LEVEL: dict[str, str] = {}
UI: dict = dict(DEFAULT_UI)
EVENTS: dict[str, dict] = {}             # cohort code → {"day", "runs", "images"} for events that set a day (v2.3)
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
            if field == "engine" and patch[field] not in SECTIONS:
                continue
            if field == "day" and (isinstance(patch[field], bool) or not -1 <= patch[field] <= 3):
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
    apply_ui(await db().config.find_one({"_id": "ui"}))
    # the live events' day and allowances (small: only cohorts whose event has a day)
    events = {}
    q = {"$or": [{"event.day": {"$exists": True}}, {"event.runs": {"$exists": True}}, {"event.images": {"$exists": True}}]}
    async for c in db().cohorts.find(q, {"code": 1, "event.day": 1, "event.runs": 1, "event.images": 1}):
        e = c.get("event") or {}
        day = e.get("day")
        events[c["code"]] = {"day": day if isinstance(day, int) and not isinstance(day, bool) else None,
                             "runs": e.get("runs"), "images": e.get("images")}
    EVENTS.clear()
    EVENTS.update(events)


def apply_ui(doc: dict | None) -> None:
    UI.clear()
    UI.update(DEFAULT_UI)
    for field, kind in UI_EDITABLE.items():
        v = (doc or {}).get(field)
        if isinstance(v, kind):
            UI[field] = v
    if (doc or {}).get("v23"):
        UI["v23"] = doc["v23"]


def engines_on() -> bool:
    """True once the v2.3 setup (or the admin) has switched the hub to the engine menu."""
    return UI.get("menu") == "engines"


def reset_cache() -> None:
    _state.update(loaded_at=0.0, stamp=None)
    apply_overrides(None, None)
    apply_ui(None)
    EVENTS.clear()


# ───────────────────────── plans ─────────────────────────
def rank(tier: str) -> int:
    return TIERS.index(tier) if tier in TIERS else 0


def _valid(until, at: datetime) -> bool:
    return bool(until) and until > at


def plan_sources(user: dict, at: datetime | None = None) -> dict:
    """Every place a plan can come from, and whether it is valid right now."""
    at = at or now()
    out = {"admin": user.get("plan", "free")}
    if user.get("phone") in settings.admin_phones and not user.get("team_of"):
        out["hub_admin"] = TIERS[-1]  # admin numbers (ADMIN_PHONES) have every feature unlocked, for as long as they're on the list
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
    return next((k for k in ("hub_admin", "purchase", "subscription", "trial", "admin") if sources.get(k) == effective), "admin")


# ───────────────────────── features ─────────────────────────
def granted(user: dict, feature: str, at: datetime | None = None) -> bool:
    until = (user.get("grants") or {}).get(feature)
    return isinstance(until, datetime) and until > (at or now())


def has(user: dict, feature: str) -> bool:
    """On for everyone (the admin's switch), and either in the owner's plan or granted to this owner by the admin.
    v2.3: a paid-only feature stays closed during a free or event trial, and during a live event a menu feature opens on
    its engine's day."""
    m = META.get(feature)
    if not m or not m["on"]:
        return False
    if granted(user, feature):
        return True
    if rank(effective_plan(user)) < rank(m["tier"]):
        return False
    if m.get("paid_only") and not is_paid(user):
        return False
    return not day_locked(user, feature)


def is_paid(user: dict) -> bool:
    """The member pays (or the team has set the plan by hand): a Membership-or-higher plan that isn't only a trial."""
    sources = plan_sources(user)
    return max((rank(t) for k, t in sources.items() if k != "trial"), default=0) >= rank("lite")


def event_trial(user: dict) -> dict | None:
    """The live-event trial, when that is what the owner's plan comes from right now (a paid plan always wins)."""
    t = user.get("trial") or {}
    if t.get("reason") != "event" or not _valid(t.get("until"), now()):
        return None
    if is_paid(user) or rank(effective_plan(user)) > rank(t.get("plan", "free")):
        return None
    return t


def event_day(user: dict) -> int | None:
    """Which day of the live event the owner is on (None: not on an event trial, or the event doesn't set days)."""
    t = event_trial(user)
    if not t:
        return None
    ev = EVENTS.get(t.get("cohort") or "")
    return ev.get("day") if ev else None


def feature_day(feature: str) -> int:
    m = META.get(feature) or {}
    d = m.get("day", -1)
    return d if isinstance(d, int) and d >= 0 else ENGINE_DAY.get(m.get("engine", ""), 0)


def day_locked(user: dict, feature: str) -> bool:
    """A menu feature whose engine opens on a later day of the live event. Features that only work behind other pages
    (show off) follow the plan, so a Day-1 page never breaks because a helper belongs to a later engine."""
    m = META.get(feature)
    if not m or not m.get("show", True):
        return False
    day = event_day(user)
    return day is not None and feature_day(feature) > day


def tier_of(feature: str) -> str:
    return META[feature]["tier"] if feature in META else "office"


def label_of(feature: str) -> str:
    return META[feature]["label"] if feature in META else feature


def monthly_runs(user: dict) -> int:
    """AI tokens a month (the code says runs). v2.3: an event account gets the event's own allowance; a paying member gets
    the plan's tokens plus the plan's extra tokens."""
    t = event_trial(user) if engines_on() else None
    if t:
        ev = EVENTS.get(t.get("cohort") or "") or {}
        return int(ev.get("runs") or EVENT_RUNS_DEFAULT)
    p = PLANS[effective_plan(user)]
    return p["runs"] + (p.get("extra_runs", 0) if is_paid(user) else 0)


def monthly_images(user: dict) -> int:
    """Social media images a calendar month: the event's (or the free plan's) number until the member pays."""
    t = event_trial(user)
    if t:
        ev = EVENTS.get(t.get("cohort") or "") or {}
        return int(ev.get("images") or PLANS["free"].get("images", EVENT_IMAGES_DEFAULT))
    if not is_paid(user):
        return int(PLANS["free"].get("images", EVENT_IMAGES_DEFAULT))
    return int(PLANS[effective_plan(user)].get("images", 30))


def team_size(user: dict) -> int:
    return max((PLANS[t]["team_size"] for t in TIERS if rank(t) <= rank(effective_plan(user))), default=0)


def unlocking_tier(feature: str) -> dict:
    tier = tier_of(feature)
    return {"tier": tier, **PLANS[tier]}


def feature_view(key: str) -> dict:
    m = META[key]
    return {"key": key, "label": m["label"], "sub": m.get("sub", ""), "what": m["what"], "tier": m["tier"], "group": m["group"], "path": m["path"],
            "role": m["role"], "order": m["order"], "on": m["on"], "teaser": m["teaser"], "core": m["core"],
            "engine": m.get("engine", "more"), "show": m.get("show", True), "paid_only": m.get("paid_only", False), "day": m.get("day", -1)}


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
    if engines_on():
        return engine_menu(owner, role)
    rank_role = ROLES.index(role)
    groups: dict[str, list] = {g: [] for g in GROUPS}
    for key, m in META.items():
        if not m["path"] or not m["on"] or ROLES.index(m["role"]) > rank_role:
            continue
        unlocked = has(owner, key)
        if not unlocked and not m["teaser"]:
            continue
        groups[m["group"]].append({"key": key, "path": m["path"], "label": m["label"], "sub": m.get("sub", ""), "what": m.get("what", ""), "locked": not unlocked,
                                   "tier": m["tier"], "tier_name": PLANS[m["tier"]]["name"], "tier_short": PLANS[m["tier"]]["short"],
                                   "order": m["order"]})
    return [{"group": g, "items": sorted(items, key=lambda i: i["order"])} for g, items in groups.items() if items]


def engine_menu(owner: dict, role: str) -> list[dict]:
    """v2.3: the menu as Start, the 6 engines, More (anything the admin switched back on) and Account. Only features with
    "show" on appear. During a live event, an engine that opens on a later day shows its pages locked, with the day."""
    rank_role = ROLES.index(role)
    sections: dict[str, list] = {s: [] for s in SECTIONS}
    for key, m in META.items():
        if not m["path"] or not m["on"] or not m.get("show", True) or ROLES.index(m["role"]) > rank_role:
            continue
        unlocked = has(owner, key)
        by_day = not unlocked and day_locked(owner, key)
        if not unlocked and not by_day and not m["teaser"]:
            continue
        sections[m.get("engine") if m.get("engine") in sections else "more"].append({
            "key": key, "path": m["path"], "label": m["label"], "sub": m.get("sub", ""), "locked": not unlocked,
            "lock": "day" if by_day else ("plan" if not unlocked else None), "opens_day": feature_day(key) if by_day else None,
            "tier": m["tier"], "tier_name": PLANS[m["tier"]]["name"], "tier_short": PLANS[m["tier"]]["short"], "order": m["order"]})
    return [{"group": SECTION_LABEL[s], "section": s, "engine": s in ENGINE_DAY, "day": ENGINE_DAY.get(s),
             "items": sorted(items, key=lambda i: i["order"])} for s, items in sections.items() if items]


def engines_for(owner: dict) -> list[dict]:
    """The 6 engines as this owner sees them: open, or the event day they open on."""
    day = event_day(owner)
    return [{**e, "open": day is None or e["day"] <= day} for e in ENGINES]
