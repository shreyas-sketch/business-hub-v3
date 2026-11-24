# OwnerDesk — Change Spec v2.4: the growth features

**From:** Harsh, 21 November 2026
**For:** the developer, and the AI coding agent working in the OwnerDesk repository
**Base:** OwnerDesk v2.3 (the 6-engine build handed over on 26 October 2026)
**What this is:** six growth features and four small extras, written so they can be built without more questions. Where something is not decided, a default is given. Section 12 lists what Harsh still has to decide.

**Status of this file:** it is a specification. Nothing in it has been built or tested yet.

---

## How to work on this

1. Read this whole file, then build in the order in section 13. Do not wait for approval between steps.
2. Where the code disagrees with this spec, pick the option that is safer for the business owner and their customers, write the reason in the handoff, and keep going.
3. **Reuse before rebuilding.** Section 2 lists what the code already has. Extend it. Do not write a second copy.
4. **No fake activity, fake numbers, fake receipts or "done" claims that are not proven.** Every number on a screen must be counted from a saved record.
5. **Never put real customer messages, names, phone numbers or emails into code, fixtures or screenshots.** Use invented data.
6. **Never change, weaken or delete existing tests or safety checks.** Add tests for every change. Keep the existing suite passing.
7. **No secrets in frontend code.** Never ask Harsh for passwords or API keys. New keys go in environment variables on the server.
8. **Do not deploy.** Hand over the source, a patch and a handoff note, as for v2.3.
9. Keep the hub's standing rules:
   - below ₹1.5 lakh, messages to customers are tap-to-send from the owner's own WhatsApp;
   - the hub's own WhatsApp number only messages owners;
   - the AI never invents prices, dates, names or facts;
   - every AI job has a rules version for tests and for when the AI answer cannot be used, and refunds the token on failure (`services.run_ai`).
10. Owners see "AI tokens", never "runs". Owners see category names, never software company names.

---

## 1. What is being added

| # | Feature | Menu place | Feature key |
|---|---|---|---|
| 1 | Find new customers | Lead engine | `prospects` |
| 2 | Outreach drafts, for approval | inside Find new customers | `prospects` |
| 3 | Lead research | inside Inquiries and Pipeline | `lead_brief` |
| 4 | Pipeline fills itself | inside Pipeline & Opportunities | `deal_autofill` |
| 5 | Call coach | Sales engine | `call_coach` |
| 6 | AI team (live view) | Start, after Engine Audit | `ai_team` |
| E1 | More like my best customers | inside Find new customers | `prospects` |
| E2 | Reply helper | inside Find new customers | `prospects` |
| E3 | Weekly growth line | automatic, to the owner | `growth_weekly` |
| E4 | Quick answers | Dashboard | `quick_answers` |

All are in the Membership plan (`lite`). `prospects` and `call_coach` are paid-only during the event, like Brochures.

Version label in the app becomes **2.4**.

---

## 2. What the code already has (reuse these)

| Need | Already in the code |
|---|---|
| Feature switches and menu | `backend/app/plans.py` (`DEFAULT_FEATURES`, `DEFAULT_ENGINE`), `backend/app/v23.py` (`MENU`, `BEHIND`, `PAID_ONLY`, `apply`, `apply_once`) |
| Spending and refunding a token | `backend/app/services.py` (`run_ai`, `spend_run`, `refund_run`) |
| Google Places search | `backend/app/competitor_watch.py` (`places_search`, `places_ready`, `PLACES_TRANSPORT` for tests), key `GOOGLE_PLACES_API_KEY` |
| Forgetting Google content | `backend/app/google_retention.py` |
| "Who to look for" plan | `competitor_watch.company_plan`, `plan_check`, `search_links` (feature `market_research`, switched off) |
| Reading a website safely | `backend/app/fetch.py` (`read_site`) |
| Tap-to-send messages | `backend/app/agents/approvals.py` (`create_approval`, `own_link`), pages `Approvals.jsx` |
| Agents, switches and the activity log | `backend/app/agents/catalog.py` (`register`), `agents/jobs.py` (`record`, `claim`, `schedule`), `GET /api/agents/activity`, page `Agents.jsx` |
| New-lead hook | `backend/app/agents/hooks.py` (`on_new_lead`), `routers/public.py` (`save_lead`) |
| Contacts, deals, notes, follow-ups | `backend/app/crm.py` (`upsert_contact`, `add_activity`, `set_reminder`, `stages_of`), `routers/crm.py` |
| Scoring a call from a transcript | `POST /api/growth/gym/score-call`, `ai/chat_tasks.py` (`score_conversation`, `score_shape`, `call_lines`), page `Gym.jsx` (feature `training_gym`, switched off) |
| Voice note to text | `backend/app/transcribe.py` (`transcribe`) |
| Monthly results and minutes saved | `backend/app/agents/growth.py` (`results_data`, `MINUTES`), page `Results.jsx` |
| Email that arrives at the hub | `backend/app/inbound.py` |
| WhatsApp messages in | `routers/hooks.py` (`wa_inbound`), `chat_ai.py` (`wa_threads`, `wa_messages`) |
| Best customers by sales | `backend/app/salesbook.py` (`history`, `group_customers`) |
| File uploads | `backend/app/files.py` (`read_upload`, `put`) |
| Test helpers | `backend/tests/v23_helpers.py`, `qa/screens_v23.py`, `qa/pipeline_v23.py` |

---

## 3. Setup and switches

- Add the six new feature keys to `DEFAULT_FEATURES`, off by default, the same way `V23_NEW` works.
- Add `backend/app/v24.py`, modelled on `v23.py`: it writes admin config overrides, runs once at first start (`apply_once`, stamp `ui.v24`), and can be re-applied from Admin → Features & plans.
- It must not undo an admin's later changes, and it must leave the v2.3 setup as it is.
- Add `POST /api/admin/config/presets/v24`, next to the v2.3 one.
- Monthly limits below are admin-editable per plan, next to tokens and images.

**Default monthly limits for a member** (untested; set to keep cost low):

| Limit | Default |
|---|---|
| Business searches | 10 |
| Prospects kept | 100 |
| First messages sent per day | 20 |
| Lead briefs written automatically | 40 |
| Call recordings scored | 20, each up to 15 minutes |

---

## 4. Feature 1 — Find new customers

**What the owner does**

1. Opens **Lead engine → Find new customers**.
2. Sees "Who to look for": 3 to 6 kinds of businesses, filled from the Knowledge Hub's ideal customer. He can edit them or type his own.
3. Picks the area: his city, or "within X km of my business" (5 to 50 km).
4. Presses **Find**. A list of real businesses appears: name, kind, area, and a link to open it on Google Maps.
5. Ticks the ones worth contacting and presses **Keep**.

**How it works**

- "Who to look for" reuses `company_plan` (1 token) or the owner's own typed kinds (0 tokens).
- The search is Google Places Text Search (New), one call per kind, 20 results a call. Use `locationBias` with a circle for "within X km" (the radius limit is 50,000 m).
- Ask the search for name, address, type, business status and Maps link only. Fetch the phone number and website with a Place Details call **only for prospects the owner keeps**. This keeps the cost down.
- Leave out: businesses marked closed, the owner's own business, and anyone already in Contacts or Customers (match by phone, then by name).
- Each search counts against the monthly search limit. Show "7 of 10 searches left this month".

**Google's rules (important)**

Google's Places API policies allow storing place IDs for ever. They do not allow copying and saving business names, addresses or phone numbers into your own database, or exporting them.

So:

- Save only: the place ID, the owner's own status and notes, and dates.
- Show name, address, phone and website by fetching them again when the list opens.
- Show the Google Maps attribution on the results list and on a prospect's page.
- No CSV export of search results.
- Add the new collection to the `google_retention.py` sweep.

**A second way in: the owner's own list**

- The owner can upload his own list (Excel or CSV): an exhibition list, an association directory, leads he bought.
- That data is his, so it is saved normally, de-duplicated against Contacts, and goes through the same Keep → Draft → Contact steps.

**Statuses of a prospect**

Found → Kept → Drafted → Contacted → Replied → Became a deal · Not interested · Do not contact

"Do not contact" is permanent for that business (by place ID and by phone).

**Data**

- `prospect_searches` {ws, kinds, area, radius_km, at, count}
- `prospects` {ws, place_id or own-list fields, status, notes, kept_at, contacted_at, replied_at, lead_id}

---

## 5. Feature 2 — Outreach drafts, for approval

**What the owner does**

1. On the Kept list, presses **Write first messages**.
2. Gets one draft per prospect: a short WhatsApp message, an email version and a call opener.
3. Edits if he wants, then taps **Send from my WhatsApp**. It opens his own WhatsApp with the message typed. He presses send himself.
4. Marks it Sent. Follow-up drafts appear on day 3 and day 7 if there is no reply.

**Rules**

- One AI job writes drafts for up to 5 prospects (1 token).
- The draft is written from two things only: the owner's Knowledge Hub, and the prospect's **own website**, read live with `fetch.read_site`. If the prospect has no website, the draft uses the Knowledge Hub and the kind of business only.
- No invented facts about the prospect. No prices unless the owner wrote them. No claims of an earlier relationship.
- Every first message says who is writing and why, and ends with an easy way to say no.
- Drafts go to the Send list through `create_approval` with a new kind `outreach`.
- An `outreach` message is **never** sent by the hub: not from the hub's number, not from the owner's connected WhatsApp tool, not by the hub's email. It only opens the owner's own WhatsApp (`own_link`) or his own mail app. Even on a plan where staff may act on their own, `outreach` always waits.
- At most 20 first messages a day. Show why: sending many cold messages can get a WhatsApp number blocked.
- Show one line on the page: "You decide who to contact. Do not message or call people who asked not to be contacted."
- Follow-ups stop when the owner marks Replied, Not interested or Do not contact.

---

## 6. Feature 3 — Lead research

**What the owner sees**

A short brief on every new inquiry, in Inquiries and in the "Edit opportunity" box (a new **Brief** tab):

- who they are, in 2 or 3 lines, with the link it came from;
- what they are probably asking for, from their own message;
- 3 questions to ask on the first call;
- a suggested first line.

**How it works**

- Register an agent with trigger `lead` (`agents/catalog.register`), feature `lead_brief`, with a switch on the AI team page. On by default for members.
- Sources, in this order:
  1. the inquiry itself (name, business name, message);
  2. the business's website, if the lead gave one or wrote from a company email address. Read it with `fetch.read_site`.
- Do not use Gmail, Yahoo, Outlook and other free mail domains as a website.
- **Never research a private person.** If the lead gave no business name and no company website, the brief uses only what they wrote, and says "Nothing public was looked up."
- If nothing useful is found, write the questions only and refund the token.
- 1 token each. Stop writing briefs automatically when the monthly limit is reached, or when fewer than 20 tokens are left. The owner can still press **Research this lead**.
- Save on the lead as `brief` {text parts, sources, at}. Add an activity line "Lead researched".

---

## 7. Feature 4 — Pipeline fills itself

**What the owner sees**

After a conversation, the deal updates itself: a summary in Notes, the next step, the follow-up date, and a suggested stage and value to accept with one tap.

**Where conversations come from**

1. **Paste or upload**, inside the "Edit opportunity" box: a pasted chat, a WhatsApp "Export chat" file (.txt or .zip), or an email thread.
2. **Voice note after a call**: the owner or staff member speaks for 30 seconds. `transcribe.py` turns it into text.
3. **A scored call** from Call coach (section 8).
4. **Email**: a reply that arrives at the hub's inquiry address from a known contact is added to that contact's open deal, instead of making a new inquiry.
5. **Connected WhatsApp tool** (members who connected their number): when a chat with a known contact has been quiet for 30 minutes, at most once a day per chat. `chat_ai.inbound` already saves every incoming message to `wa_messages` before it checks the plan, and it does not reply without `whatsapp_ai`. Read from there. Do not switch on `whatsapp_ai` for this.

**What the AI job returns** (1 token per conversation)

- a 3-line summary;
- what the customer needs;
- next step, and a follow-up date **only if a date was said**;
- a value **only if an amount was said**;
- a suggested pipeline stage, from this business's own stages;
- a lost reason, only if the customer clearly said no.

**What is applied by itself, and what waits**

| Applied by itself | Waits for one tap |
|---|---|
| Summary as a note (`crm.add_activity`) | Stage change |
| Next step and follow-up date (`crm.set_reminder`) | Value |
| | Won or Lost |
| | A new contact and deal, when the person is not in the CRM |

- Show the waiting items as a "Suggested updates" card at the top of the deal, with **Apply** and **Dismiss**.
- A setting, off by default: "Move the stage forward by itself". It never moves a deal to Won or Lost and never changes the value.
- Every change made by the AI is marked "by OwnerDesk" in the history and can be undone.
- The stored conversation text stays inside that business's data. Do not send it anywhere except the AI call.

---

## 8. Feature 5 — Call coach

**What the owner does**

1. Opens **Sales engine → Call coach**.
2. Uploads a call recording, or pastes the call as text.
3. Picks which team member made the call (a simple list of names he types once).
4. Gets a score out of 10, what went well, and 3 fixes with the exact moment quoted.
5. Sees each person's scores over time.

**How it works**

- Reuse the existing scorer: `score_conversation`, `score_shape` and the `score-call` endpoint. Open them to the new feature key `call_coach`. Leave `training_gym` as it is.
- Recording → text with `transcribe.transcribe`. Accept the audio types it already supports. Limit: 15 minutes and 25 MB a call.
- A recording has no speaker labels. Add one AI step that marks each line Customer or Salesperson. Show the text before scoring so the owner can fix the labels.
- 1 token per call scored. Refund on failure.
- The recording file is deleted after the text is made. Say so on the page.
- One line on the page: "Only upload calls you are allowed to record."
- After scoring, offer **Add this call to the deal**, which runs the section 7 job on the same text.
- Data: reuse `gym_sessions` with `kind: "call"`, adding `member` (the name picked).

---

## 9. Feature 6 — AI team (live view)

**What the owner sees**

A page at **Start → AI team**:

- one card per AI role, each with one job;
- the role's state;
- real counts for today and this month;
- its last action, with the time and a link to the record;
- a switch to turn it off;
- a feed of the latest actions.

**The roles** (role names, no human names)

| Role | Job | Counts shown |
|---|---|---|
| Prospector | Finds businesses that fit | businesses found, kept |
| Outreach writer | Writes first messages and follow-ups | drafts written, messages you sent, replies |
| Researcher | Writes a brief on each new lead | leads researched |
| Pipeline keeper | Files conversations into deals | conversations filed, updates applied |
| Call coach | Scores calls | calls scored, average score |
| Follow-up writer | Day 1, 3, 7 follow-ups (already built) | drafts written, sent by you |
| Collections | Payment reminders (already built) | reminders prepared, money received |

Show a role only when its feature is on for this owner.

**States** (each must be true, not decoration)

- **Working**: a job for this role is running right now (`jobs.claim`).
- **Waiting for you**: it has drafts or suggestions not yet acted on. Show how many.
- **Idle**: nothing to do.
- **Needs setup**: something is missing. Say what.
- **Switched off**.

**Rules**

- Every count is counted from the saved records for that period. No counters that can drift, no made-up activity, no animation of work that is not happening.
- Every line in the feed links to its record (the prospect, the brief, the deal, the scored call).
- Use `jobs.record` for the feed, with a reference to the record. The page reads `GET /api/agents/activity`.
- Refresh every 15 seconds while the page is open.
- "Hours saved" may be shown once, for the month, labelled **Estimated**, with the minutes per action listed on the page. Reuse `MINUTES` and add entries for the new actions.
- A zero is shown as zero, with one line on what to do next.
- Top of the page, this month: businesses found · messages you sent · replies · deals from new customers · value of those deals. The value is the sum of deal values the owner entered.

---

## 10. The four extras

**E1. More like my best customers**
- One button on Find new customers.
- Takes the owner's top 5 customers by sales (`salesbook`) and what kind of business each is (ask the owner once per customer if not known), and fills "Who to look for" from that.
- 0 tokens if the kinds are known, 1 token to word them.

**E2. Reply helper**
- When a prospect replies, the owner pastes the reply.
- He gets a suggested answer and one button: **Make this a deal**. That creates the contact and a deal at the first stage with source "prospecting".
- 1 token.

**E3. Weekly growth line**
- Every Monday morning, one WhatsApp line to the owner from the hub's number, and the same line on the Dashboard:
  "Last week: 40 businesses found, 12 messages sent by you, 3 replies, 1 new deal worth ₹85,000."
- Counted from records. Sent only when at least one number is above zero. A switch on the AI team page.
- Reuse the digest agent's sending path.

**E4. Quick answers (instead of open chat with data)**
- A Dashboard tile with fixed questions the owner taps:
  - Which deals are stuck?
  - Who owes me the most?
  - Which source brings the most deals?
  - Who has gone quiet?
  - How fast do we reply to new inquiries?
  - What is due this week?
- Every number is computed by code from the records. The AI is not asked to calculate or to search the data.
- 0 tokens.
- Do not build an open "ask anything" chat over the owner's data in this version. Wrong answers there look right, and the owner cannot tell.

---

## 11. Acceptance checks

- [ ] v2.4 setup applies once at first start, shows "Applied" in Admin, and does not undo admin changes or the v2.3 setup.
- [ ] Find new customers: a search with a fake Places server returns a list; closed businesses and existing contacts are left out; the 11th search in a month is blocked with a clear message.
- [ ] After a search, the database holds place IDs and the owner's status and notes only. No Google name, address or phone is stored. The attribution shows.
- [ ] An uploaded own list is saved, de-duplicated, and can be kept and drafted.
- [ ] Outreach: 5 prospects → 5 drafts for 1 token; an `outreach` message cannot be sent by the hub by any route, on any plan; the 21st first message in a day is blocked; follow-ups stop on Replied, Not interested and Do not contact; a "Do not contact" business never gets another draft.
- [ ] A draft never contains text copied from the prospect's website (test with a fake site carrying a unique phrase) and never an invented fact.
- [ ] Lead research: a new inquiry with a company website gets a brief with its source link; an inquiry from a free mail address with no business name gets "Nothing public was looked up"; an empty result refunds the token.
- [ ] Pipeline fills itself: a pasted conversation adds a note and a follow-up date when a date was said; stage, value, Won and Lost wait for a tap; Dismiss leaves the deal unchanged; every AI change can be undone.
- [ ] A conversation with no amount in it never produces a value.
- [ ] Call coach: a pasted call is scored; an uploaded recording is transcribed, labelled, shown for correction, then scored; the recording file is gone afterwards; a failed job refunds the token.
- [ ] AI team: every count matches a direct count of the records in a test; a role with no activity shows zero; "Working" shows only while a job runs; every feed line opens its record.
- [ ] Quick answers: each answer matches the records in a test, and uses 0 tokens.
- [ ] No software company name is visible to owners. Owners see "AI tokens".
- [ ] All existing tests pass, plus the new ones. The screenshot sweep on phone and desktop shows no page errors and no sideways scroll, using invented data only.

---

## 12. For Harsh to decide

| # | Decision | Default until decided |
|---|---|---|
| 1 | **Google's terms.** Is "place IDs only, details fetched live" acceptable, after a check of Google's Maps Platform terms? | Build it that way. Do not store Google's business details. |
| 2 | **A paid business-data provider** for names and phone numbers that may be stored. | Not included. The owner's own uploaded list covers this for now. |
| 3 | **Cost.** Google bills each search and each details call. Transcription is billed per minute. | Keep the limits in section 3. Measure real cost on 10 members before raising them. |
| 4 | **Event access.** Should event attendees try Find new customers? | No. Members only. |
| 5 | **WhatsApp chats filling the pipeline** needs the member's own WhatsApp tool connected. | Build paste, upload, voice note and email first. WhatsApp last. |
| 6 | **Named roles.** Role names (Prospector, Researcher) or human names? | Role names. |

---

## 13. Order of work

1. `v24.py`, the feature keys, limits and the admin preset (section 3).
2. Lead research (section 6). It is the smallest and proves the agent, token and feed path.
3. Pipeline fills itself: paste, upload and voice note (section 7, sources 1 and 2).
4. Call coach (section 8), then "Add this call to the deal".
5. Find new customers with the owner's own list first, then Google search (section 4).
6. Outreach drafts and follow-ups (section 5), then the Reply helper (E2).
7. AI team page (section 9), then the Weekly growth line (E3).
8. Quick answers (E4) and More like my best customers (E1).
9. Pipeline fills itself from email and from the connected WhatsApp tool (section 7, sources 4 and 5).
10. Tests, the screenshot sweep, the handoff note, the source zip and the patch. Do not deploy.

**Handoff note must say:** what was built, what was not, every place this spec was not followed and why, what was measured, and what is still untested with real accounts (Google Places, transcription, the real AI's wording).
