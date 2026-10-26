# OwnerDesk v2.3: the 6-engine build

**Base:** the OwnerDesk rename and restyle handed over on 22 Oct (commit `f18e91e`), which sits on v2.2 (`0acdc74`).
"v2.3" in the hub now means both: the rename/restyle, and this build on top of it.

**What it is:** the hub as it's sold from the 3-day "your time back" event. A menu of the 6 engines of a business, only the
features the partners agreed, the engines opening day by day at the event, and one paid plan.

**Nothing was deployed.** No new Python or npm packages. No manual database migration: the setup applies itself once,
on the first start (section 3).

---

## 1. What the owner sees

The left menu is **Start**, the **6 engines** and **Account**. Each engine heading opens a page for that engine.

**Start**
- Knowledge Hub. Its first step now asks, once: their website, one competitor's website, and 4 questions if they
  have no website. Answers already known are filled in for the owner to confirm.
- Engine Audit (new). One line per feature: Have it / Missing / Not sure. For a gap: rupees a month and hours a week.
  Running totals of cost, growth unlocked, hours lost and hours back. Growth is capped at 50% of a month's sales.
  Every figure says "Estimated from your own numbers". PDF and share image. No AI tokens.

**Lead engine** (Day 1)
- Website: from their own site, or from the 4 answers. Preview at desktop and phone width before publishing.
- Lead magnet: 8 ideas → pick one → a 6–12 page PDF with a cover in their brand, plus a landing page with a form →
  live on their site. Every download becomes a lead.

**Sales engine** (Day 1)
- Revenue Multiplier (renamed from Revenue levers): the calculator, plus 45 ideas (15 per lever) built from their
  Knowledge Hub, website, lead magnet and competitor. No rupee promises.
- Ways to get more leads: its own page now.
- Invoicing (Quotations & invoices).

**Conversion engine** (Day 2)
- Inquiries: as before. Every inquiry now also creates a contact and a deal.
- CRM (new): contacts, companies, history, follow-up dates with reminders, CSV and Excel import and export.
- Pipeline & Opportunities (one page), laid out like GoHighLevel's Opportunities page:
  - Two tabs: **Opportunities** and **Pipeline**.
  - Toolbar: the pipeline, board/list switch, "+ Add opportunity"; then search, Open / Won / Lost / All, Filters (source,
    value from, follow-up due) and Sort.
  - One column per stage: a coloured top line, "N opportunities · ₹ total", and a + that adds straight into that stage.
  - Cards: initials, name, business, value, source, owner; Call, WhatsApp, notes count (from the saved history),
    quotation number, follow-up date; a ⋯ menu with Open, Move to a stage, Mark won, Mark lost.
  - Drag and drop between stages (mouse, or press and hold on a phone), with an Undo bar for 6 seconds.
  - A card opens an "Edit opportunity" dialog: details on the left, Notes / Quotation & invoice on the right,
    Cancel / Update. Update sends only what changed. Lost needs a reason.
  - Pipeline tab: the stages in order (drag the handle, or the arrows), add or delete stages, lost reasons, and
    "Build it again from the 4 questions". On a first visit with no stages, the 4 questions (Chirag's 10, merged)
    come first.
  - On a computer the page uses the full width; on a phone one column at a time, and the dialog fills the screen.

**Operations engine** (Day 2)
- Dashboard: the home page, open from Day 1.
- Payment follow-up.
- Compliance calendar: a pop-up asks for the GST number and TAN. TDS dates follow the TAN. A company with no TAN is
  told to check with its CA.
- SOP Writer.

**Brand engine** (Day 3)
- Brand kit: plus a Brochures tab with two text brochures (business, and products and services). Members only.
- Social media (new): 7 posts with images ready on first open. A counter shows images left: 7 on the free or event
  access, 30 a month for members.

**Data engine** (Day 3)
- Key-person knowledge (new): the owner's 5 questions → a questionnaire the employee types into on their phone (link,
  no login) → an AI agent skill file (.md), a role card and SOPs.
- Expenses & bank statement: plus an Analysis tab, AI category suggestions for unsorted rows, and AI advice that only
  sees category totals (salaries only if ticked).

**Account**
- Plans & billing: ₹1,999 a month or ₹19,999 a year, with your Razorpay links.
- Connections, named by category, not by the software company:
  - WhatsApp tool (was AiSensy; Wati, Interakt and Meta are choices inside it)
  - CRM (was Employz.ai)
  - Payment gateway (was Razorpay)
  - AI voice agent (was ElevenLabs)
  - Accounting software (was Tally)
  - Calendar (help text no longer names Google)
  - The company's name shows only as the choice inside each section's picker ("Your CRM: Employz.ai"). Where only one
    works so far, it's already picked. More can be added to the picker later without renaming anything.
- Recordings.
- The WhatsApp community link (members only) and the support email.

**Everything else** the hub has is switched off for everyone. The code and data stay. Admin → Features & plans
switches any of it back on, and it then shows under "More".

**Words:** owners see "AI tokens", never "runs", and category names ("WhatsApp tool", "CRM", "Payment gateway"), never
the software company. Page headings use the engine names.

---

## 2. Access and plans

**One paid plan:** "OwnerDesk Membership", tier `lite`.
- ₹1,999 a month or ₹19,999 a year.
- 150 AI tokens a month, plus "Extra AI tokens a month" (set in Admin → Features & plans → Plans; 0 until you decide).
- 30 social images a month.

**Payment** happens outside the hub, on Razorpay. The team records it in **Admin → Onboarding → Mark paid** (a month or
a year). The plan opens at once. A second payment adds on from the current paid date.

**Event access.** Saving an event pack gives the attendee the event's access until the event's last day.
- Engines open by **event day**: Day 1 Lead and Sales, Day 2 adds Conversion and Operations, Day 3 adds Brand and Data.
- Knowledge Hub, Engine Audit, Dashboard and Account are open from the start.
- Event accounts get 50 AI tokens and 7 social images for the event (both editable per event).
- Brochures, the WhatsApp tool and the CRM sync wait until they pay.
- Paying members have everything, whatever the day.

**When access ends** (End event access, or the last day passes) without paying:
- Everything is locked except Knowledge Hub, Engine Audit and Plans & billing.
- They land on Plans & billing.
- Nothing is deleted.

**Switch off** (Admin → Onboarding) blocks the login completely. **Switch on** brings it back.

---

## 3. How to deploy

1. Deploy as usual (DEPLOY_CHECKLIST.md). Use the full source, or apply the patch:
   - on top of the 22 Oct restyle: `OwnerDesk_v2.3_engines.patch`;
   - straight from v2.2: `OwnerDesk_v2.3_from_v2.2.patch`.
2. **The first start applies the v2.3 setup by itself**, once (`app/v23.py`). It writes into the same `config`
   collection as admin changes. After that it never runs again by itself, so your later admin changes stay.
   - To re-apply it: Admin → Features & plans → "Apply again".
3. **New settings** (all optional; nothing changes if they're left out) are in `.env.example` and in section 4.5 of the
   checklist:
   - `WHATSAPP_PROVIDER`: aisensy by default, or wati, interakt, meta.
   - The `WATI_*` and `INTERAKT_*` settings.
   - `WHATSAPP_TEMPLATE_LANGUAGE`.
   - `WHATSAPP_HUB_PHONE_NUMBER_ID` and `WHATSAPP_HUB_ACCESS_TOKEN`.
4. New collections (indexes are created at start-up): `engine_audits`, `engine_audit_drafts`, `onboarding`,
   `contacts`, `companies`, `crm_activities`, `pipelines`, `competitor_summaries`, `magnet_ideas`, `growth_ideas`,
   `brochures`, `sm_posts`, `sm_usage`, `sm_state`, `key_people`, `expense_advice`.
   - Existing collections gain fields: `businesses.inputs`, `businesses.identity.tan/has_tan/pan`, lead CRM fields,
     `magnet_files` AI guides, `sites.built_from`.

---

## 4. The team's checklist for the event

**Before Day 1**
1. Admin → Features & plans: the "OwnerDesk v2.3 setup" panel says **Applied**.
2. Admin → Onboarding → **Links members see**: the WhatsApp community link, support email, and both Razorpay links.
3. Admin → Live event, for the event's cohort:
   - open the link;
   - set the last day;
   - AI tokens per attendee (50) and social images (7);
   - press **Day 1**.
4. Set the real AI key (checklist section 9). With the test writer, AI text is placeholder text.

**During the event**
- Day 2 morning: press **Day 2**.
- Day 3 morning: press **Day 3**.

**After the event**
- End of Day 3: **End event access**. It closes the link and ends access for everyone who hasn't paid.
- Every payment: Admin → Onboarding → **Mark paid**.
- Onboarding also holds:
  - each member's setup checklist, worked out by the hub;
  - call date, who's handling them, stage and notes;
  - extra tokens;
  - switch off and on.

---

## 5. Tests

**Measured on this build**, MongoDB 3.6 locally:
- Full suite: **772 passed, 6 failed** (26 Oct). The 6 are the same ones that fail on the untouched base (below).
- 64 new tests in 8 files (`tests/test_v23_*.py`), all passing. `test_v23_names_and_cards.py` checks that no
  connection names a software company outside its picker, and the cards' note and call counts.
- No old test was changed.

**Six old tests also fail on the untouched base** (commit `f18e91e`). They depend on today's date, a Monday near the end
of the month:
- 4 in `test_agents.py`;
- the cash-flow step of `test_event_demos.py`;
- the Tuesday agent in `test_winback.py`.

They were not changed, because tests are only changed with permission.

**Pipeline page, driven like an owner would** (`qa/pipeline_v23.py`, desktop 1366 px and phone 390 px, invented data):
24 of 24 checks pass. It builds the pipeline from the questions, drags a card and undoes it, moves one from its ⋯ menu,
edits one (value, follow-up, a note, Won) in one Update, blocks Lost without a reason, adds from a column's +, filters,
searches, switches to the list, reorders a stage, and on the phone drags with press-and-hold (Chrome's touch
simulation) and checks the dialog and menu fit the screen. Each step is checked against the saved data, not only the
screen.

**Screens:** `qa/screens_v23.py` takes every engine page for a member, the event view on Day 1, and the admin pages, at
390 px and 1366 px. Result: 88 screenshots, no page errors, nothing scrolls sideways. The only console errors were the
owner site's Google Fonts, which this workspace can't reach.

---

## 6. Not tested yet

- The real AI's wording and length: lead magnet pages, 45 ideas, brochures, skill files. Only the test writer was used.
- Wati, Interakt and Meta-direct WhatsApp. They are built from the public API documents and need a real account. Send
  yourself a login code and an owner alert before switching a live hub.
- Drag and drop on a real iPhone or Android phone. It was tested with Chrome's touch simulation only.
- 300 people doing the Engine Audit at once. It is light (one small write per save, no AI), but load wasn't measured.
- Owner websites now load the brand kit's fonts from Google Fonts. This is a new outside request; it can be reverted in
  `templates/site.html` and `public._extras`.

## 7. Still open

- Chirag's two GHL screenshots from the meeting chat (…191102.jpg, …193321.jpg) never arrived. The Pipeline page
  follows GHL's Opportunities layout as described in section 1; if the screenshots differ, match them.
- The extra-tokens bonus number: set it in Admin → Features & plans → Plans → OwnerDesk Membership.
