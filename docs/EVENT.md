# Live event link — the 3-day AI Advantage Membership event

What it is, how to run it, and what was tested. Not deployed.

## What an attendee does

1. Scans the QR on the projector (or opens `APP_URL/event?e=<CODE>`). No login.
2. Types their website and GST number, then three quick taps:
   - GST filed every month or every quarter;
   - how many people work with them;
   - only when the GST number can't tell (a firm, a company): partnership or LLP, private limited or one-person company.
3. Presses **Build my pack**. In about 5 seconds the first things are on screen; the rest is written over the next minute.
4. Presses **Save it to my hub**: name, email, WhatsApp number, the 6-digit code on WhatsApp. The account is created (or they
   are logged in) with everything inside, and Membership features stay open until the event's last day.

## What the pack holds

Made by rules, in seconds, no AI:

- **Compliance calendar**: every filing in the next 12 months from the rules table (the GST number gives the state and the
  business type; the taps give monthly or quarterly and staff). The dates still carry the "not reviewed by a CA yet" line until
  a CA stamps the rules.
- **Website check**: the 8 checks and the top 3 fixes.
- **Brand starter**: colours from their logo (or the website's theme colour), the logo itself, a WhatsApp Status poster and a
  social post drawn in those colours.
- **Knowledge Hub, started**: name, what they do, city, offers, contact, and the business identity.

Written by AI, when the event allows it (Admin → Live event → "AI before login", with a cap):

- the Knowledge Hub filled from the website's text;
- 3 brand messages to pick from (the poster follows the pick);
- a website draft (goes live with one tap after saving);
- 5–7 ways to get more leads.

If the website can't be opened, the page asks for the business in a line, and the AI writes from that (once per pack).

## Safety

- The link works only while the admin has it open. Each event has a cap on packs and on packs that get AI. At most 2 packs per
  GST number or website per event.
- No account pays for pre-login AI; each pack's AI cost is recorded and shown on the speaker screen.
- The GST number is only checked (format and check character) and read for its state and PAN type. No GST portal is called.
- Websites and logos are read with the hub's safe reader: public addresses only, every redirect checked, size and time limits,
  logos must really be PNG or JPEG and under 4 megapixels.
- A pack goes into an account only for the person holding its link who also proves a WhatsApp number with the code. Nothing of
  the attendee's (phone, email) is kept on a pack before it's saved.
- Nothing already in an account is overwritten: a business name, a website, a brand kit or a brand message that exists is
  kept, and the attendee is told which ones.
- A team member's login never gets a pack in the owner's hub.
- AI calls for packs are limited per server process (`EVENT_AI_CONCURRENCY`, 24), below the engine's own gate
  (`AI_CONCURRENCY`, 32), so attendees already in their hub always get a turn. A "too many requests" or "overloaded" answer
  from the AI engine is tried again (`AI_RETRIES`, 2) after the wait it asks for.

## Before the event (admin)

1. Admin → Live event → **Move them into Membership** (Lead magnet, Ways to get more leads, Pipeline, Quotations & invoices,
   Money owed). Undo per feature in Features & plans → Reset to default. The code's defaults are unchanged.
2. Admin → create the cohort (code, name, date). Admin → Live event → pick it:
   - **Packs, at most**: the room size plus half (300 for 200 people).
   - **Packs with AI, at most**: the same.
   - **Membership features open until**: the event's last day.
   - **Link open**: switch on when the session starts, off when it ends.
3. **Show the QR** puts the link on the projector. **Live numbers** is the speaker's screen (refreshes every 5 seconds).
4. Check the AI account's rate limit (output tokens a minute). See the test results below for what 200 people need.
5. Dry run the day before with 10 phones, end to end.

## What was tested (20 Oct 2026)

200 virtual attendees on one network address, the whole flow and every demo — see `qa/loadtest/README.md`. Measured on a
2-core machine with stand-ins for the AI engine, WhatsApp and the websites:

- **Generous AI limit, people arriving over 90 s:**
  - first things on screen in 6 s (95% within 12 s);
  - the whole pack in 71 s (95% within 101 s);
  - 0 errors in 12,953 requests.
- **Everyone pressing Build within 10 s, and an AI limit of 80,000 output tokens a minute:**
  - the pack takes about 5 minutes;
  - brand messages and lead ideas still reach everyone, but half the website drafts wait for after login;
  - most in-hub AI buttons say "busy".
  The AI account's limit is what decides the room's experience.

## Settings

| Setting | Default | What it does |
|---|---|---|
| `EVENT_WORKERS` | 24 | packs in phase 1 (website + rules) at once, per process |
| `EVENT_AI_WORKERS` | 40 | packs in phase 2 (AI) at once, per process |
| `EVENT_AI_CONCURRENCY` | 24 | AI calls for packs at once, per process |
| `EVENT_RUNS_PER_IP_PER_10MIN` | 600 | packs from one network address (a hall shares one) |
| `EVENT_SITE_SECONDS` | 20 | longest the website read may take |
| `EVENT_AI_SECONDS` | 150 | longest one AI step may take, waiting in line included |
| `AI_CONCURRENCY` | 32 | AI calls in flight per process, everything included |
| `AI_RETRIES` | 2 | retries of a 429 / 503 / 529 from the AI engine |

## Code

- `backend/app/event.py`: the GST reading, the two-phase queue, the pack view, saving into the account, the speaker's numbers.
- `backend/app/routers/event.py`: the public endpoints and Admin → Live event.
- `backend/app/routers/admin_config.py`: the Membership preset.
- `backend/app/ai/provider.py`: the AI gate and retries.
- `frontend/src/pages/Event.jsx`, `AdminEvent.jsx`, `event.css`.
- Demo hooks: Inquiries → **Make a quotation** (opens the quotation with the customer and their message filled in);
  Website → **Show the QR** (for "swap with your neighbour").
- Tests: `backend/tests/test_event.py`, `backend/tests/test_event_demos.py` (every demo, done by event accounts, then the
  event ending).
