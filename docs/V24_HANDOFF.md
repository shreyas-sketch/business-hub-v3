# OwnerDesk v2.4 — the growth features: handoff

21 November 2026. Base: OwnerDesk v2.3 (commit `dafc7ac` in this repo). The spec is `docs/V24_SPEC.md`.

Nothing here was deployed. Nothing was tried with a real AI key, a real Google Places key, a real WhatsApp account or a
real phone call. Everything was checked with the test AI writer (`AI_PROVIDER=mock`), a fake Google Maps server and
invented businesses.

---

## 1. What is new

**On the menu**

- **Start → AI team.** One card per AI role: what it did today and this month, counted from the owner's own records. A
  switch per role. The latest 30 actions, each linked to what was done. "Hours saved" is marked Estimated.
- **Lead engine → Find new customers.** Businesses on Google Maps that fit, or the owner's own list. The hub writes a
  first message, an email and a call opener for each. **The owner sends every message from his own WhatsApp or email.
  The hub never sends them.** Day 3 and day 7 follow-ups wait in the Send list. Reply helper. Do not contact. Make this a
  deal. "More like my best customers" fills who to look for from his top customers.
- **Sales engine → Call coach.** Paste a call or upload a recording. Score out of 10, 3 fixes, each with the line from
  the call. Per-person trend. "Add this call to the deal".

**Behind other pages**

- **Lead research** (Inquiries and the deal's Brief tab): a short brief on every new inquiry, with 3 questions to ask.
  It reads only a business website. A private person is never looked up.
- **Pipeline fills itself** (Pipeline): paste a chat, upload a WhatsApp export, or record a voice note. The note and
  follow-up date are filed by themselves. A new stage or value waits for a tap. Every OwnerDesk change can be undone.
- **Weekly growth line**: Monday 09:00 India time, one line on the owner's WhatsApp, only when there is something to say.
- **Quick answers** (Dashboard tile): 6 fixed questions answered from records by code. No AI, no tokens.

**What uses AI tokens**

| Action | Tokens |
|---|---|
| Suggest who to look for | 1 |
| First messages | 1 for up to 5 businesses |
| Reply helper | 1 |
| Lead brief | 1 |
| Filing a conversation | 1 |
| Scoring a pasted call | 1 |
| Scoring a recording | 2 (writing it out, then scoring) |
| Follow-ups to businesses, AI team, Quick answers, Weekly line | 0 |

**Monthly limits per plan** (admin can change them in Features & plans → Plans):

| | Membership, Action Program | Running the Business | Growth Mentorship | LegacyWorkforce |
|---|---|---|---|---|
| Google Maps searches | 10 | 20 | 40 | 100 |
| Businesses kept | 100 | 200 | 400 | 1,000 |
| First messages a day | 20 | 40 | 80 | 200 |
| Automatic lead briefs | 40 | 80 | 160 | 400 |
| Calls scored | 20 | 40 | 80 | 200 |

Free owners see the locked page. These numbers are starting defaults, not measured.

---

## 2. How it switches on

- On the first server start with this code, the v2.4 setup applies itself once (after the v2.3 setup). It only touches
  its own 7 features. Admin → Features & plans can apply it again (`POST /api/admin/config/presets/v24`).
- All 7 are on the Membership plan and up, so ₹1.5 lakh and ₹6 lakh members get them too. The ₹1.5 lakh install spec
  says "no lead generation in the 6 months". Whether those members should see Find new customers is Harsh's decision.
  To hide it for them, change the feature in Admin → Features & plans.

**Server settings it needs** (no new ones were added):

- `GOOGLE_PLACES_API_KEY` for Find new customers. Without it, the page says Google Maps search isn't switched on, and the
  owner's own list still works.
- `OPENAI_API_KEY` or `GEMINI_API_KEY` for call recordings and voice notes. Without one, recordings say so and pasted
  calls still work.
- The web server in front of the app must accept **25 MB request bodies** on `/api/call-coach/recording` (and voice
  notes on `/api/crm/deals/*/conversation/upload`).

---

## 3. Safety rules, and where they are enforced

- **Outreach is never sent by the hub.** Approvals of kind `outreach` can only be sent from the owner's own WhatsApp
  (`agents/approvals.py`, `OWN_ONLY`). "Send" through the hub's WhatsApp returns 400. LegacyWorkforce's "act on its own"
  setting cannot override it. Tested.
- **Google Maps data:** only the place ID is stored. Name, address, phone and website are fetched live each time and shown
  with "Google Maps" attribution. No export. Drafts never contain the Google name. Do not contact keeps the place ID and a
  keyed one-way hash of the phone. A test scans the database for Google fields; the QA run checks it too.
- **A deal made from Find new customers gets no automatic contact.** No day 1/3/7 lead follow-ups, no instant reply, no
  Lead Response, no AI Telecaller call, no automatic proposal email (`agents/hooks.py`). Reason: the business never
  contacted the business number, and an AI call would fall under TRAI's September 2026 rules. The brief and CRM still
  get it. Tested.
- **Pipeline fills itself never sets a value the conversation didn't state**, never marks Won or Lost, and never moves to
  the last stage by itself. An email is filed into a deal only when the sender's address passed DMARC.
- **Call recordings are never stored.** Only the written-out lines, kept up to 7 days as a draft until scored.
- **No fake numbers.** Every count on the AI team page and Quick answers is read from saved records. Hours saved is the
  only estimate and is labelled so.

---

## 4. Tests

- **New:** 87 tests in `backend/tests/test_v24_*.py` (foundation, prospects, lead research, deal fill, call coach, AI
  team, integration). All pass.
- **Full suite:** 860 passed, 5 failed (section 7). The v2.3 baseline run this morning: 773 passed, 5 failed.
- **No existing test was changed, weakened or deleted.**
- **Five old tests fail, on untouched v2.3 code too, at the same hour.** I ran the three new-looking ones on a clean
  v2.3 checkout at 13:50 UTC on 21 November and they failed there as well. They depend on the clock: on a Saturday evening
  in India they fast-forward into Monday 10:00, when the week-plan agent spends one extra AI token.
  - `test_agents.py::test_followup_drafted_then_tapped_to_send_or_sent_from_the_owners_number`
  - `test_agents.py::test_followup_skipped_when_lead_already_closed_or_deleted`
  - `test_agents.py::test_busy_ai_retries_the_job_later`
  - `test_agents.py::test_monthly_review_on_the_first_with_last_months_data` (a duplicate key on `scores.token`; failed
    in the v2.3 baseline run this morning)
  - `test_reputation.py::test_google_search_settings_and_the_daily_watch` (date-dependent; failed in the baseline)

**Browser checks** (desktop 1366 px and phone 390 px, invented data, test AI writer, fake Google Maps):

- `qa/screens_v24.py`: uses each new feature like an owner and checks the saved data after each step. **28 of 28 pass**,
  no page errors, nothing scrolls sideways. 27 screenshots.
- `qa/pipeline_v23.py` (the v2.3 Pipeline check) on v2.4: **24 of 24 pass**.

How to run them:

```bash
cd backend
APP_ENV=development APP_URL=http://localhost:8000 DB_NAME=qa24 AI_PROVIDER=mock RUN_SCHEDULER=false \
  ADMIN_PHONES=+919999900000 python3 ../qa/seed_v24.py            # empties qa24 and seeds one invented member
python3 ../qa/fake_places.py 8098 &                               # fake Google Maps + fake business websites
APP_ENV=development APP_URL=http://localhost:8000 DB_NAME=qa24 AI_PROVIDER=mock RUN_SCHEDULER=false \
  ADMIN_PHONES=+919999900000 GOOGLE_PLACES_API_KEY=qa-fake GOOGLE_PLACES_API_BASE=http://127.0.0.1:8098/v1 \
  FETCH_ALLOW_PRIVATE=true uvicorn app.main:app --port 8000 &
cd .. && (cd frontend && npm run build) && QA_DB=qa24 python3 qa/screens_v24.py /tmp/shots24
```

`seed_demo.py` stops at its team step on v2.3 and v2.4, because the v2.3 setup hides the team pages it calls. That was
already so on v2.3 and was left alone; `qa/seed_v24.py` is used instead.

---

## 5. Not tested yet

- The real AI's wording: first messages, briefs, call scores and filed conversations. The screenshots show the test
  writer's fixed text (for example, its "kinds of business" are the ideal-customer sentence).
- The real Google Places API: field masks, India coverage, cost per search.
- Call recordings with a real speech engine, Hindi and Hinglish calls, and how long a recording can be (25 MB is
  enforced; "about 15 minutes" is a guess).
- The scorer reads only the last 40 turns of a call, so the opening of a long call isn't scored.
- WhatsApp chats filed once a day at 7:30 pm India time; not run against real chats.
- The Weekly growth line on a real WhatsApp number.
- Load: many owners searching at once.

---

## 6. Decisions for Harsh

1. **Google's terms.** Drafts leave out the Google business name, and only place IDs are stored. "Make this a deal" saves
   the name and number the owner confirms into his contacts. Check this reading with someone who knows Google's terms.
2. **₹1.5 lakh members and Find new customers** (section 2).
3. **Limits** (section 1). Starting defaults.
4. **The Pipeline's "Move the stage forward by itself"** is off by default. It never moves into the last step.

---

## 7. Changes made while joining the four parts

- `app/crm.py`: "Find new customers" as the source name for these deals.
- `app/agents/hooks.py`: no automatic contact for deals from Find new customers (section 3).
- `app/ai_team.py`: an undone pipeline update no longer counts as applied; a scheduled run with nothing to do no longer
  fills the Latest actions list.
- `AiTeam.jsx`: "Skipped" and "Not done" instead of raw status words.
- `routers/prospects.py` and `Prospects.jsx`: "Suggest with AI" keeps the kinds the owner typed and adds to them, instead
  of replacing them.
- `tests/test_v24_integration.py`: tests for the above, and for Call coach's "Add this call to the deal" through the
  Pipeline's endpoint.

**Last full run** (21 Nov 2026, about 14:20 UTC, MongoDB 3.6): **860 passed, 5 failed.** The 5 are the old, clock-dependent
ones listed in section 4.
