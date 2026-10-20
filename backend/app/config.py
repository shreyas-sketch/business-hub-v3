"""All settings come from environment variables. Nothing here needs editing to deploy."""
import os
from dataclasses import dataclass, field


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # production unless you explicitly say otherwise; "development" only takes effect on a localhost APP_URL
    env: str = os.getenv("APP_ENV", "production").strip().lower()        # production | development | test
    app_url: str = os.getenv("APP_URL", "http://localhost:8000").rstrip("/")
    mongo_url: str = os.getenv("MONGO_URL", "mongodb://127.0.0.1:27017")
    db_name: str = os.getenv("DB_NAME", "action_hub")
    jwt_secret: str = os.getenv("JWT_SECRET", "")
    admin_phones: tuple = tuple(p.strip() for p in os.getenv("ADMIN_PHONES", "").split(",") if p.strip())

    # Login and abuse limits
    otp_country_codes: tuple = tuple(c.strip().lstrip("+") for c in os.getenv("OTP_COUNTRY_CODES", "91").split(",") if c.strip())
    otp_per_ip_per_hour: int = _int("OTP_PER_IP_PER_HOUR", 1500)   # a whole workshop room shares one Wi-Fi address
    inquiries_per_site_per_hour: int = _int("INQUIRIES_PER_SITE_PER_HOUR", 120)
    trusted_proxy_hops: int = _int("TRUSTED_PROXY_HOPS", 1)        # Railway = 1; add 1 for each proxy in front (e.g. Cloudflare = 2)

    # Program identity (footer rail + public pages)
    program_name: str = os.getenv("PROGRAM_NAME", "Business AI Action Hub")
    speaker_line: str = os.getenv("SPEAKER_LINE", "Akshat Dani · Chirag J.")

    # AI engine: anthropic | gemini | openai | mock
    ai_provider: str = os.getenv("AI_PROVIDER", "mock").lower()
    ai_model: str = os.getenv("AI_MODEL", "")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    # Rupee cost per million tokens, used only for the cost estimate on Pulse
    ai_cost_in_per_m: float = _float("AI_COST_INR_PER_M_INPUT", 85.0)
    ai_cost_out_per_m: float = _float("AI_COST_INR_PER_M_OUTPUT", 425.0)
    ai_concurrency: int = _int("AI_CONCURRENCY", 32)        # AI calls in flight at once, per server process; the rest wait their turn
    ai_retries: int = _int("AI_RETRIES", 2)                 # a "too many requests" / "overloaded" answer is tried again this many times

    # Messaging: WhatsApp via AiSensy (the hub's own number), SMS fallback via MSG91. Empty = development mode (logged, not sent)
    aisensy_api_key: str = os.getenv("AISENSY_API_KEY", "")
    aisensy_otp_campaign: str = os.getenv("AISENSY_OTP_CAMPAIGN", "")
    aisensy_otp_button: bool = os.getenv("AISENSY_OTP_BUTTON", "true").lower() in ("1", "true", "yes")
    # The hub's own number only ever messages owners (never their customers): login codes, new-inquiry alerts, the digest
    # and other owner alerts — and a webinar attendee their own Day-1 report. Customer messages go from each owner's own
    # number (Growth Mentorship) or their own phone.
    aisensy_lead_alert_campaign: str = os.getenv("AISENSY_LEAD_ALERT_CAMPAIGN", "")  # to the owner: {{1}} business, {{2}} name, {{3}} phone, {{4}} message
    aisensy_digest_campaign: str = os.getenv("AISENSY_DIGEST_CAMPAIGN", "")          # to the owner: {{1}} business, {{2}} summary
    # Day-1 report (₹99 webinar): one approved AiSensy media template, sent from the hub's own number like the owner alerts.
    # Template: the report as an image (or PDF) header, body "{{1}}, your Business AI report is ready. {{2}}", and a URL button
    # "See the full report" → APP_URL/r/{{1}} (the button's {{1}} is the report's share token).
    aisensy_report_campaign: str = os.getenv("AISENSY_REPORT_CAMPAIGN", "")
    aisensy_report_media: str = os.getenv("AISENSY_REPORT_MEDIA", "image").lower()      # image | pdf
    aisensy_report_link: str = os.getenv("AISENSY_REPORT_LINK", "button").lower()       # button | text (the link as {{3}})
    msg91_auth_key: str = os.getenv("MSG91_AUTH_KEY", "")
    msg91_otp_template_id: str = os.getenv("MSG91_OTP_TEMPLATE_ID", "")

    # Payments (Razorpay). Without keys: test checkout on a local machine, "talk to us" on a real deployment.
    razorpay_key_id: str = os.getenv("RAZORPAY_KEY_ID", "")
    razorpay_key_secret: str = os.getenv("RAZORPAY_KEY_SECRET", "")
    razorpay_webhook_secret: str = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
    razorpay_plan_id_lite: str = os.getenv("RAZORPAY_PLAN_ID_LITE", "")       # the ₹1,999/month plan created in the Razorpay dashboard
    commission_hold_days: int = _int("COMMISSION_HOLD_DAYS", 7)                # refund window before a commission can be approved
    commission_months: int = _int("REFERRAL_COMMISSION_MONTHS", 12)            # pay commission on a friend's payments for this long

    # Agents
    run_scheduler: bool = os.getenv("RUN_SCHEDULER", "true").lower() in ("1", "true", "yes")
    followup_days: tuple = tuple(int(d) for d in os.getenv("FOLLOWUP_DAYS", "1,3,7").split(",") if d.strip().isdigit())
    review_delay_days: int = _int("REVIEW_REQUEST_DELAY_DAYS", 2)
    reminder_gap_days: int = _int("PAYMENT_REMINDER_GAP_DAYS", 3)

    # Own domains for member websites: owners point a CNAME here
    custom_domain_target: str = os.getenv("CUSTOM_DOMAIN_TARGET", "")
    # Every member website at <name>.SITES_DOMAIN (one wildcard DNS record). Empty: websites live at APP_URL/s/<name>.
    sites_domain: str = os.getenv("SITES_DOMAIN", "").strip().lower().lstrip("*").strip(".")
    reserved_subdomains: tuple = tuple(s.strip().lower() for s in os.getenv(
        "RESERVED_SUBDOMAINS",
        "app,hub,www,api,admin,mail,email,webmail,login,auth,pay,payments,help,support,docs,blog,status,cdn,static,assets,ftp,smtp,"
        "imap,pop,ns1,ns2,dev,staging,test,beta,portal,dashboard,account,accounts,billing,crm,link,links,go,m,mobile,shop,store,"
        "agency,partners,affiliates,secure,services,app2,leadconnector,msgsndr,api2,sites,site,join,s").split(",") if s.strip())

    # Connections to each owner's own accounts (Growth Mentorship and up). Secrets are encrypted with this key.
    connections_key: str = os.getenv("CONNECTIONS_KEY", "")
    aisensy_project_api_base: str = os.getenv("AISENSY_PROJECT_API_BASE", "https://apis.aisensy.com/project-apis/v1").rstrip("/")
    whatsapp_graph_base: str = os.getenv("WHATSAPP_GRAPH_BASE", "https://graph.facebook.com/v21.0").rstrip("/")
    employz_api_base: str = os.getenv("EMPLOYZ_API_BASE", "https://services.leadconnectorhq.com").rstrip("/")
    employz_app_url: str = os.getenv("EMPLOYZ_APP_URL", "").rstrip("/")
    elevenlabs_api_base: str = os.getenv("ELEVENLABS_API_BASE", "https://api.elevenlabs.io").rstrip("/")
    aisensy_owner_alert_campaign: str = os.getenv("AISENSY_OWNER_ALERT_CAMPAIGN", "")  # to the owner: {{1}} business, {{2}} the message
    campaign_daily_cap: int = _int("CAMPAIGN_DAILY_CAP", 200)          # money-campaign messages per owner per day
    ai_replies_per_chat_per_day: int = _int("AI_REPLIES_PER_CHAT_PER_DAY", 20)
    calls_per_owner_per_day: int = _int("CALLS_PER_OWNER_PER_DAY", 60)

    # Day-1 report: the no-login run during the ₹99 webinar (Admin → Day-1 reports)
    day1_daily_cap: int = _int("DAY1_DAILY_CAP", 5000)                 # public runs a day (India time), all webinars together
    day1_runs_per_ip: int = _int("DAY1_RUNS_PER_IP_PER_10MIN", 30)     # per network address, every 10 minutes
    day1_workers: int = _int("DAY1_WORKERS", 8)                        # reports built at the same time, per server process
    day1_site_seconds: int = _int("DAY1_SITE_SECONDS", 20)             # longest the website check may take on a run
    day1_status_secret: str = os.getenv("DAY1_STATUS_WEBHOOK_SECRET", "")  # AiSensy sent/delivered/read webhook: /api/hooks/day1-status/<secret>

    # Live event link (Admin → Live event): website + GST number → the event pack, before login
    event_workers: int = _int("EVENT_WORKERS", 24)                    # packs in phase 1 (website + rules) at once, per server process
    event_ai_workers: int = _int("EVENT_AI_WORKERS", 40)              # packs in phase 2 (AI) at once, per server process
    event_runs_per_ip: int = _int("EVENT_RUNS_PER_IP_PER_10MIN", 600)  # per network address: a whole hall shares one Wi-Fi address
    event_site_seconds: int = _int("EVENT_SITE_SECONDS", 20)          # longest the website read may take
    event_ai_seconds: int = _int("EVENT_AI_SECONDS", 150)             # longest one AI step may take, waiting in line included

    # v2.1: email to customers (payment follow-ups, proposals, invoices). mock = written to the outbox, like WhatsApp in development
    email_provider: str = os.getenv("EMAIL_PROVIDER", "mock").lower()          # ses | mock
    aws_region: str = os.getenv("AWS_REGION", "ap-south-1")
    aws_access_key_id: str = os.getenv("AWS_ACCESS_KEY_ID", "")
    aws_secret_access_key: str = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    ses_configuration_set: str = os.getenv("SES_CONFIGURATION_SET", "")       # sends delivery/bounce/complaint events to the webhook
    email_from_domain: str = os.getenv("EMAIL_FROM_DOMAIN", "mail.employz.ai")  # SPF, DKIM and DMARC records required
    inbound_email_domain: str = os.getenv("INBOUND_EMAIL_DOMAIN", "")         # e.g. in.employz.ai (MX → SES inbound); empty = no inbound
    email_events_secret: str = os.getenv("EMAIL_EVENTS_SECRET", "")           # SNS → APP_URL/api/hooks/email-events/<secret>
    email_events_topic_arn: str = os.getenv("EMAIL_EVENTS_TOPIC_ARN", "")     # only this SNS topic's events are accepted (recommended)
    email_inbound_secret: str = os.getenv("EMAIL_INBOUND_SECRET", "")         # SNS → APP_URL/api/hooks/email-in/<secret>
    email_inbound_topic_arn: str = os.getenv("EMAIL_INBOUND_TOPIC_ARN", "")   # only this SNS topic's mail is accepted (recommended)
    proposals_per_owner_per_day: int = _int("PROPOSALS_PER_OWNER_PER_DAY", 100)
    emails_per_owner_per_day: int = _int("EMAILS_PER_OWNER_PER_DAY", 300)
    upload_max_mb: int = _int("UPLOAD_MAX_MB", 10)
    compliance_reminder_days: tuple = tuple(int(d) for d in os.getenv("COMPLIANCE_REMINDER_DAYS", "30,7,1,0").split(",") if d.strip().isdigit())
    cashflow_alert_days: int = _int("CASHFLOW_ALERT_DAYS", 10)
    dashboard_cache_seconds: int = _int("DASHBOARD_CACHE_SECONDS", 60)

    # Upgrade destinations (used only when Razorpay is not configured)
    upgrade_urls: dict = field(default_factory=lambda: {
        "lite": os.getenv("UPGRADE_URL_LITE", ""),
        "program": os.getenv("UPGRADE_URL_PROGRAM", ""),
        "running": os.getenv("UPGRADE_URL_RUNNING", ""),
        "growth": os.getenv("UPGRADE_URL_GROWTH", ""),
        "office": os.getenv("UPGRADE_URL_OFFICE", ""),
    })

    # Referral rewards (Bullzeye pattern: incentive-only, rewarded on activation, not on signup)
    friend_trial_days: int = _int("REFERRAL_FRIEND_TRIAL_DAYS", 30)
    referrer_first_reward_days: int = _int("REFERRAL_FIRST_FRIEND_REWARD_DAYS", 30)
    referrer_bonus_runs: int = _int("REFERRAL_BONUS_RUNS_PER_LIVE_FRIEND", 10)
    commission_percent: int = _int("REFERRAL_COMMISSION_PERCENT", 30)

    @property
    def production(self) -> bool:
        return not self.local_dev

    @property
    def local_dev(self) -> bool:
        """Development conveniences (codes shown on screen, messages logged instead of sent) only run
        in tests or on a localhost APP_URL — never on a public deployment, even if APP_ENV is left unset."""
        if self.env == "test":
            return True
        hosted = any(os.getenv(k) for k in ("RAILWAY_ENVIRONMENT", "RAILWAY_PROJECT_ID", "RENDER", "FLY_APP_NAME", "DYNO", "K_SERVICE"))
        return self.env == "development" and self.app_host in ("localhost", "127.0.0.1") and not hosted

    @property
    def fetch_private_ok(self) -> bool:
        """Reading a website on a private address (127.0.0.1, 10.x…) is allowed only in tests and on a laptop."""
        return self.local_dev and os.getenv("FETCH_ALLOW_PRIVATE", "true" if self.env == "test" else "false").lower() in ("1", "true", "yes")

    @property
    def app_host(self) -> str:
        return self.app_url.split("//", 1)[-1].split("/", 1)[0].split(":", 1)[0]

    @property
    def secure_cookies(self) -> bool:
        return self.app_url.startswith("https://")


settings = Settings()

if settings.production:
    if len(settings.jwt_secret) < 32:
        raise RuntimeError("JWT_SECRET must be at least 32 characters on any non-local deployment "
                           "(for local development set APP_ENV=development and APP_URL=http://localhost:8000)")
    if settings.app_host in ("localhost", "127.0.0.1") or not settings.app_url.startswith("https://"):
        raise RuntimeError("APP_URL must be the public https address of the hub, e.g. https://hub.example.com")
