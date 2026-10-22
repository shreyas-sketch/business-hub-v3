# OwnerDesk · v2.3 (formerly Business AI Action Hub)

> v2.3 is the OwnerDesk rename and restyle to the Akshat Dani design system: see `docs/OWNERDESK_RESTYLE.md`.

The hub every owner leaves the 2-day Business AI workshop with. It is free on day one, and the same hub grows with the owner through six plans: from a live website and an AI Writer, to a weekly rhythm, the Action Program's frameworks filled in with their own numbers, sales and cash on a system, 6 AI staff on their own WhatsApp and Employz.ai, and finally a 30-strong AI workforce.

## The six plans

| Plan | Price | AI runs/month | In one line |
|---|---|---|---|
| Free | ₹0 | 10 | Website at `name.employz.ai`, Business Brain from a website / voice note / typed lines, Business AI Score, launch kit, AI Writer |
| Membership | ₹1,999/month (autopay) | 150 | The weekly rhythm (Monday plan, daily 5-minute action and streak, Friday score), festival calendar, member call, cohort board, AI tools, Magic Number, Gaps scan |
| Action Program | ₹10,000 once (1 year) | 300 | Lead magnet at `/guide`, offer ladder on the site, revenue levers and the 10×10×10 pledge, customer portfolio (A/B/C/D), Football Field pipeline, leak finder, 4 end goals, Send list with follow-ups and reviews |
| Running the Business | ₹25,000 once (1 year) | 400 | Employz.ai CRM starter (sync), customers A/B/C, GST quotations and invoices, money owed, payment reminders, quote follow-ups, customer desk |
| Growth Mentorship | ₹1,50,000 once (6 months) | 3,000 | 6 AI staff on the owner's own WhatsApp (AiSensy) and Employz.ai, done-for-you setup, Company Brain, money campaigns, Control Room, Sales Training Gym, AI results, team of 10 |
| LegacyWorkforce | ₹6,00,000 once (1 year) | 6,000 | 30 AI staff (6 in each of 5 departments, from 82 roles), the AI office, staff that act on their own, Tally/email/calendar, team of 25 |

Each plan includes everything below it. **Every feature is managed from Admin → Features & plans**: move it to another plan (drag and drop), switch it off for everyone, rename it, change its description and menu order, choose whether locked pages show as a teaser, edit each plan's price and limits, give one owner a feature without changing their plan, and undo any change from the log. Defaults live in `backend/app/plans.py`; the admin's changes are stored in the database and apply within seconds. `docs/PHASES.md` lists every feature by plan, and `docs/Action Hub v2 - Feature List by Plan.pdf` is the same list for sharing.

**Three rules hold everywhere:**

1. The hub's central WhatsApp number only ever messages **owners** (login codes, new-inquiry alerts, the digest and owner alerts) — and a ₹99 webinar attendee their own Day-1 report.
2. Customer messages below ₹1.5L are **tap-to-send** from the owner's own WhatsApp (the hub prepares them in the Send list). From Growth Mentorship they can go from the owner's **own connected number** (AiSensy). Money messages (reminders, quotations) always wait for a person; only LegacyWorkforce staff may act on their own, and never with money or links.
3. Below ₹1.5L owners get **AI tools**; **AI staff** start at Growth Mentorship.

## Day-1 report (the ₹99 webinar)

During the webinar each attendee opens `/report?c=<webinar code>`, types their WhatsApp number, first name and website, answers the Score's 10 questions and 6 numbers, and gets a one-page report in the Blueprint brand: a 1080×1920 image for WhatsApp and an A4 PDF. No login and no AI. "Save with WhatsApp code" uses the hub's own login code, makes a Free account and attaches the report; any later payment upgrades that same account. The team sends the reports and exports the cohort for the Performance Hub from **Admin → Day-1 reports**. Setup and the WhatsApp template to submit: `docs/DAY1_REPORT.md`.

## v2.1 — the subscription model

P1 adds the Inquiries inbox with automatic proposals (email in through Amazon SES), Razorpay → automatic GST invoices and credit notes, direct invoices, website edit by prompt and uploaded lead magnets. P0: the Knowledge Hub (Business Brain with identity, money and targets), the Smart Dashboard (the owner's landing page), the compliance calendar, a 30-day cash-flow forecast, "Am I on target?", automatic payment follow-ups by email (approve once), the brand kit with designs, the whole-business SOP set, Mark as posted and ways to get more leads. Decisions and scope: `docs/V2_PLAN.md` (v2.1 section). Email needs Amazon SES (see `backend/.env.example`). Demo login for the launch: 9820000007.

## v2.2 — Conversion, Data and five AI agents

In the ₹1,999 Membership: the funnel leak finder and follow-ups on day 1, 3 and 7 (moved in by the admin's one-click preset), the expense tracker, the bank statement reader, GST set-aside, customer risk and getting paid (fed by a Tally/Excel sales upload), Competitor Watch and Reviews & reputation. Win-Back and Content & company research are in the Action Program; Lead Response is in Growth Mentorship. What was decided and why, what was tested and what needs a decision (Google reviews): `docs/V22_HANDOFF.md`. Deploying: `docs/DEPLOY_CHECKLIST.md` (section 0.5). The demo bank statement (invented): `docs/demo/kaveri-bank-statement.csv`.

## What's inside

```
backend/     FastAPI + MongoDB (Motor)
  app/plans.py              the six plans, 79 features, the admin's overrides, the menu
  app/routers/admin_config.py   Admin → Features & plans: move, switch, rename, menu order, plan settings, grants, change log + undo
  app/day1.py, day1_runs.py, day1_render.py, routers/day1.py   the ₹99 webinar's Day-1 report: library and rules, queue, PNG/PDF, send, export
  app/sites.py              websites at <name>.SITES_DOMAIN (wildcard), /s/<name>, and own domains
  app/fetch.py, onboard.py  reading an owner's existing website safely (SSRF-guarded), voice/typed onboarding, Business AI Score
  app/agents/rhythm.py      Membership's week, streak, weekly score, festival calendar, Magic Number
  app/routers/program.py    lead magnet, offer ladder, levers, portfolio, leak finder, end goals, gaps scan
  app/routers/desk.py       customers A/B/C + import, GST quotations and invoices, Employz.ai CRM
  app/agents/desk.py        quote follow-ups, customer desk, CRM sync
  app/connections.py        each owner's own accounts (WhatsApp/AiSensy, Employz.ai, ElevenLabs, Tally…) — encrypted
  app/integrations.py       AiSensy (campaign + project API), Meta Cloud API, GoHighLevel API v2, ElevenLabs
  app/chat_ai.py            WhatsApp chats: AI replies, hand-over to a person, 24-hour window
  app/agents/growth.py      instant reply, AI Telecaller, re-calls, Control Room, Monday CEO report, AI results
  app/campaigns.py          money campaigns: approved once, sent 10am–7pm, daily cap
  app/company_brain.py      price lists, FAQs, past quotations the AI staff answer from
  app/roles_catalog.py, tools.py   the 82 roles and the ready-made AI tools
  app/agents/               scheduler, Send list (approvals), automations, the AI office
  app/knowledge.py, routers/knowledge.py   v2.1 Knowledge Hub: sections, completeness, refresh-or-flag when it changes
  app/dashboard_widgets.py, routers/dashboard.py   v2.1 Smart Dashboard: one entry per tile
  app/compliance_rules.py, routers/compliance.py   v2.1 compliance calendar: the rules table (CA-reviewed), items, reminders, documents
  app/cashflow.py, app/sales.py, routers/cashflow.py   v2.1 cash flow forecast and "Am I on target?"
  app/followups.py, app/mailer.py, routers/followups.py   v2.1 automatic payment follow-ups; email through Amazon SES
  app/brand_kit.py, app/designs.py, routers/brandkit.py   v2.1 brand kit: messages, design tokens, server-drawn designs
  routers/sopset.py, routers/social.py, routers/leadideas.py   v2.1 SOP set, posting log, ways to get more leads
  app/files.py              small files (≤10 MB) in MongoDB: documents, logos, lead magnet PDFs
  routers/inquiries.py, app/inbound.py, ai/proposal_tasks.py   v2.1 P1 inquiries, email in (SES → SNS), automatic proposal
  routers/gateway.py, payments_in/   v2.1 P1 direct invoices, Razorpay → invoices and credit notes
  routers/sitemagnets.py, ai/site_tasks.py   v2.1 P1 website edit by prompt, uploaded lead magnets
  tests/                    316 tests — run before every deploy (the Day-1 500-report load test takes about a minute)
  seed_demo.py              a demo hub: an owner on every plan with every v2 feature filled in
frontend/    React + Vite, Blueprint design system; the menu comes from the server (the admin's order and names)
docs/        PHASES.md · V2_PLAN.md · the three PDFs (developer guide, brochure, feature list by plan)
qa/e2e.py    browser click-through: onboarding, score, writer, website, leads, plans, referral loop, admin (23 checks)
qa/screens.py  screenshot sweep of every page for every plan and role, desktop and phone, flags sideways scrolling
```

## Run it on your laptop

You need Python 3.11+, Node 20+, and MongoDB (`docker run -p 27017:27017 mongo:7`, or a free Atlas cluster).

```bash
# backend
cd backend
pip install -r requirements-dev.txt
export APP_ENV=development APP_URL=http://localhost:8000 MONGO_URL=mongodb://127.0.0.1:27017 AI_PROVIDER=mock ADMIN_PHONES=+919999900000
python3 seed_demo.py                 # optional: demo data (logins below)
uvicorn app.main:app --reload --port 8000

# frontend (second terminal)
cd frontend
npm install
npm run dev                          # http://localhost:5173 — proxies /api and /s/ to :8000
```

Demo logins after `seed_demo.py` (the login code is shown on screen in development):

| Number | Who |
|---|---|
| 9820000001 | Shree Ganesh Interiors — LegacyWorkforce (30 AI staff, AI office, team, every module) |
| 9820000011 / 9820000012 | Priya Nair (manager) / Suresh Yadav (staff) on that team |
| 9820000006 | Sharma Modular Kitchens — Growth Mentorship (WhatsApp chats, Telecaller call, campaign, Company Brain, setup) |
| 9820000005 | Patel Sweets & Farsan — Running the Business (customers, GST quotations, invoice, Send list) |
| 9820000003 | Arora Traders — Action Program |
| 9820000002 | Kapoor Dental Clinic — Membership (week, calendar, score, gaps, member call question) |
| 9820000004 | Mehta Engineering Works — Free |
| 9999900000 | Admin (Pulse, Features & plans, member calls, payments, domains, recordings) |

In development, with no WhatsApp or SMS keys set, the login code is shown on the login screen — only when `APP_ENV=development` **and** `APP_URL` is localhost. Demo WhatsApp and voice connections (provider `demo`) write to the `outbox` collection instead of sending. `AI_PROVIDER=mock` returns fixed demo text, so everything can be clicked through without an AI key.

## Tests

```bash
cd backend && pytest -q        # needs MongoDB at MONGO_URL; creates and wipes a throwaway database
```

237 tests, including the v2 suites:

- **Admin:** moving, switching off, renaming and undoing features; core features protected; plan prices (Membership needs a new Razorpay plan id); grants.
- **Free:** reading a website (redirects, sub-pages, contact details, 8 checks) and refusing private, odd or non-http addresses; voice/typed onboarding; the Business Score and share card; websites at `name.employz.ai`; visiting card; English polisher by plan.
- **Membership and Action Program:** the week, streak and weekly score; festival calendar; member call questions; cohort board; tools by plan; Magic Number; Gaps scan; lead magnet bringing leads; offer ladder on the site; levers; portfolio letters; leak finder; Football Field stages; goal kinds.
- **Running the Business:** A/B/C sorting; Excel/CSV import with merging; GST maths (CGST/SGST/IGST), invoices into Money owed; quote follow-ups on day 2/5/10 that never go out alone; customer desk; Employz.ai sync and webhook against a fake server.
- **Day-1 report:** a phone-only run saved with no account, the code attaching it, a Razorpay payment upgrading the same account; findings by rule and the gap formula; 500 queued reports with no failures and no AI; the cohort CSV columns; the PNG (1080×1920, under 1 MB, brand colours) and the one-page A4 PDF; limits, the share page, sending and delivery status, the admin library.
- **Growth and LegacyWorkforce:** encrypted connections; AiSensy, Meta and ElevenLabs request shapes; WhatsApp AI replies, hand-over, the 24-hour window, daily caps, Meta signatures; Telecaller in calling hours and the signed post-call webhook; Company Brain; money campaigns in hours with a daily cap; Control Room, Gym, results; 6-per-department workforce.

## Deploy (Railway + MongoDB Atlas)

Everything — the wildcard domain for `name.employz.ai`, the hub's WhatsApp templates, each Growth owner's AiSensy, Employz.ai and ElevenLabs connections, Razorpay — is in **docs/Action Hub v2 - Developer Guide.pdf**. In short:

1. MongoDB Atlas M10 and Railway, both in Singapore; deploy this repo (the `Dockerfile` is picked up).
2. Variables from `backend/.env.example`: at minimum `APP_ENV=production`, `APP_URL`, `MONGO_URL`, `JWT_SECRET`, `CONNECTIONS_KEY`, `ADMIN_PHONES`, an AI key, a login channel (AiSensy and/or MSG91), and `SITES_DOMAIN=employz.ai` with the wildcard record `*.employz.ai` added to Railway.
3. Payments: Razorpay keys, the ₹1,999 monthly plan id and the webhook.
4. Check `GET /api/health`, log in with an admin number, review **Admin → Features & plans**, and create the workshop cohort.

The app refuses to start in production with a weak `JWT_SECRET` or a non-https `APP_URL`.

## Notes for the developer

- **Database:** MongoDB. Attempt limits, run counting and exactly-once jobs rely on unique ids and single-document atomic updates.
- **Scheduler:** automations run from a loop inside each web worker (`RUN_SCHEDULER=true`); every job is claimed first, so several workers never send twice. Money campaigns tick in the same loop.
- **Owners' accounts:** API keys and tokens are encrypted with `CONNECTIONS_KEY` and never sent back to the browser. Each connection has its own secret webhook address. Set `CONNECTIONS_KEY` once and keep it — without it, keys are derived from `JWT_SECRET`.
- **Reading websites:** the address an owner types is untrusted. Only http(s) on ports 80/443, every host is resolved and refused if private, and the connection is pinned to the checked address; 2 MB and 4 redirects at most.
- **Payments:** Razorpay is the source of truth; refunds remove access and void commissions.
- **Disabling:** removing a number from `ADMIN_PHONES` revokes admin access at once; pausing an owner logs them out and takes their website offline; switching a feature off in Admin hides it for everyone within seconds.
