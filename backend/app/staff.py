"""
The 6 AI staff of Growth Mentorship. Each is a set of automations and tools with one switch, and goes live in a month of
the 6-month setup (when the team marks that step done, or when what they need is connected).
"""
STAFF = [
    {"key": "sales", "title": "Sales Executive", "month": 2, "step": "m2_sales", "needs": "whatsapp",
     "what": "Replies to every inquiry on your WhatsApp 24×7, qualifies the lead, books the meeting and follows up quotations.",
     "agents": ["instant_reply", "wa_sales", "followup", "quote_followup"], "tools": ["proposal"], "pages": ["/chats", "/pipeline"]},
    {"key": "telecaller", "title": "Telecaller", "month": 3, "step": "m3_voice", "needs": "voice",
     "what": "Calls every new lead within minutes and re-calls old ones with a natural voice; every call summarised on the lead.",
     "agents": ["telecaller", "recall"], "tools": ["telecalling"], "pages": ["/staff"]},
    {"key": "marketing", "title": "Marketing Executive", "month": 4, "step": "m4_content", "needs": None,
     "what": "A month of posts and reel scripts in your voice, ready to load into Employz.ai's social planner; turns happy customers into case studies.",
     "agents": ["week_plan", "calendar_auto"], "tools": ["content", "video", "case_study", "google"], "pages": ["/calendar"]},
    {"key": "accounts", "title": "Accounts Executive", "month": 5, "step": "m5_cash", "needs": None,
     "what": "Quotations, invoices and payment reminders — sent from your WhatsApp within the approval rules.",
     "agents": ["payment_reminder"], "tools": [], "pages": ["/quotes", "/money"]},
    {"key": "care", "title": "Customer Care Executive", "month": 5, "step": "m5_cash", "needs": None,
     "what": "Answers customer questions, asks for reviews and referrals, reminds customers to reorder and greets them on occasions.",
     "agents": ["review_request", "customer_desk"], "tools": [], "pages": ["/customers", "/chats"]},
    {"key": "chief", "title": "Chief of Staff", "month": 6, "step": "m6_team", "needs": None,
     "what": "Morning digest, pipeline analysis (stuck deals, conversion at each stage), meeting actions to the team, monthly review.",
     "agents": ["digest", "ceo_report", "monthly_review", "results"], "tools": [], "pages": ["/control", "/reviews"]},
]
BY_KEY = {s["key"]: s for s in STAFF}

# The 6-month setup, done for the owner by the team (Admin → owner → Setup)
SETUP_STEPS = [
    ("m1_employz", 1, "Employz.ai account: CRM, Football Field pipeline, team users and calendar"),
    ("m1_domain", 1, "Your own domain, with the multi-page website"),
    ("m1_whatsapp", 1, "Your WhatsApp number connected (WhatsApp tool), templates approved"),
    ("m1_brain", 1, "Company Brain loaded: price list, catalogue, FAQs, past quotations"),
    ("m1_customers", 1, "Old customer list imported, cleaned and sorted A, B and C"),
    ("m2_sales", 2, "Sales Executive live: replies on WhatsApp 24×7"),
    ("m3_voice", 3, "Telecaller live: ElevenLabs voice agent calling new leads"),
    ("m4_content", 4, "Marketing Executive live: the monthly content calendar loaded into Employz.ai's social planner"),
    ("m5_cash", 5, "Accounts and Customer Care live: reminders, reviews, reorders on WhatsApp"),
    ("m6_team", 6, "Team on PACE: roles, meetings and tasks; Chief of Staff live"),
]
