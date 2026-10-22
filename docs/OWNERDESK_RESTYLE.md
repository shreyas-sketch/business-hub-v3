# OwnerDesk v2.3: rename and restyle

**Base:** v2.2, commit `0acdc74`.

**What it does:** renames the product from "Business AI Action Hub" to **OwnerDesk** and restyles the whole app to the Akshat Dani design system (v1.0, Oct 2026).

**What it doesn't change:**

- No feature or behaviour changes.
- No database changes.
- No new Python packages.
- Nothing was deployed.

---

## 1. How to apply

Pick one of these:

- **A. Replace the code.** Use the full source in `OwnerDesk_v2.3_source.zip`.
- **B. Apply the patch on top of v2.2.** From the repo root:
  ```bash
  git checkout 0acdc74            # or your v2.2 branch
  git apply --binary OwnerDesk_v2.3.patch
  ```
  The patch includes the new logo, icon and font files.

Then:

```bash
cd frontend && npm ci && npm run build      # font packages changed, so npm ci is needed
cd ../backend && pytest -q                  # see section 6
```

**Production settings:**

- If production sets `PROGRAM_NAME=Business AI Action Hub`, change it to `PROGRAM_NAME=OwnerDesk`, or delete the line, because the default is now OwnerDesk.
- This name shows in two places: the footer of every page, and the "Built free with OwnerDesk" badge on free websites.
- `backend/.env.example` is already updated.

**Admin data (no code needed):**

- Plan names come from Admin → Features & plans. The demo uses "AI Advantage Plus".
- Rename them there if the plans should carry the OwnerDesk name.

---

## 2. The design rules, as applied to the hub

**Background.** Dark, never flat:

- Base `#14151A`.
- A soft radial lift toward `#1E2027`, plus a faint warm glow at the bottom right.
- A fine 48px grid at 5% opacity that fades out across the page.
- Dark only. There is no light mode.

**Text.** Warm ivory `#F2EEE6`. Secondary text is ivory at 70%, disabled text ivory at 35%.

**Gold `#C9A84C` is the signal.** It's used for:

- the one key phrase in each headline (`<em>`)
- every ₹ figure and hero number
- kickers
- the primary button
- the active menu item and tab
- counters and meters

**Teal `#2ED3B7` means AI, and only AI.** It's used for:

- every button that spends an AI run. These carry the class `ai` and a ✦ spark. A primary AI action is a solid teal fill.
- AI-written text (`.prompt`)
- AI output sections
- the AI "working" state: three pulsing teal dots on the button while it runs
- the Day-1 "writing" bar
- the AI tag on Workforce
- the explain box in Compliance
- bot messages

**Alert red `#E0533D`** is used only for form errors, error toasts and failed steps in a timeline. Warnings ("slightly behind") are gold-light, not red.

**Fonts:**

- Manrope everywhere. Headlines are ExtraBold 800, tracking −0.02em. Sub-lines are Light 300. Kickers are Medium 500 caps at 0.18em.
- JetBrains Mono only for prompts and code.
- Space Grotesk and Inter are removed.

**Shapes:**

- Cards and panels: radius 14.
- Device and preview frames, and modals: radius 18.
- Inputs: radius 12.
- Buttons, pills and tokens: fully rounded.
- Buttons are sentence case (no more all-caps).

**Components:**

- **Primary button:** gold fill with dark ExtraBold text.
- **Secondary button:** gold-deep outline with ivory text.
- **AI button:** teal outline, or a teal fill when it's the primary action.
- **Switch:** "on" is the OwnerDesk gold track with the ivory knob, the same as the logo.
- **Meters:** a gold gradient with a soft glow.
- **Stat tiles:** the hero number is gold.
- **Toasts:** a gold ✓, or a red ! for errors.
- **Footer rule:** gold-deep.

**Logo:** the logo kit's Horizontal Dark *Flat* file. It's shown under 64px, so the kit says to use the flat version.

- In the side menu, at the top above the business name.
- In the phone top bar, next to the business name.
- On the landing page, the login page, onboarding and the live-event screen.

**Favicon, app icons and manifest:** from the logo kit. The theme colour is `#14151A`.

The old token names (`--ink`, `--aqua`, `--amber`…) still exist as aliases that point at the new colours. That's why the 40+ page stylesheets work without a rewrite. New code should use the new names (`--bg`, `--gold`, `--teal`…).

---

## 3. What changed, file by file

**Name**

- `frontend/index.html` (title, theme colour, favicon)
- `frontend/public/manifest.webmanifest`
- `App.jsx` (side menu and top bar)
- `ui.jsx`:
  - new `Logo` component
  - footer default name
  - `Busy` button now adds `is-busy` while it runs, so AI buttons can show their working state
- `Landing.jsx`, `Login.jsx`, `Onboarding.jsx`, `Event.jsx`: the logo instead of the text name
- `Invite.jsx`: the share message
- `Day1.jsx`: one line
- `Website.jsx`: the badge text
- `backend/app/config.py`: `PROGRAM_NAME` default
- `backend/app/main.py`
- `backend/app/day1_render.py`: the PDF "creator" field only
- `backend/app/ai/tasks.py` and `ai/team_tasks.py`: "You work inside OwnerDesk, the business hub…"
- `frontend/package.json`: the package name is now `ownerdesk`
- `README.md`

**Look**

- `frontend/src/styles.css`: the design system tokens and all shared components, rewritten.
- `frontend/src/v2.css`: pills, kanban, chat bubbles, scores, drag rows, AI output.
- `frontend/src/pages/*.css`:
  - Headline-style text moved from weight 300/400 to 700/800.
  - Old aqua and amber RGBA values moved to gold.
  - 79 boxed components got rounded corners.
  - The AI elements listed in section 2 are now teal.
- `frontend/src/main.jsx` and `package.json`:
  - `@fontsource/manrope` and `@fontsource/jetbrains-mono` added.
  - `@fontsource/inter` and `@fontsource/space-grotesk` removed.
- `frontend/src/pages/LaunchKit.jsx`: the WhatsApp Status poster now draws in Manrope.
- `frontend/src/billing.js`: the Razorpay checkout colour is now gold.
- `backend/app/templates/score.html`: the Business Score share page, in OwnerDesk colours and Manrope.
- `backend/app/templates/not_found.html`: the same.
- `backend/app/templates/site.html` and `card.html`: only the "Built free with OwnerDesk" badge and the hub's preview bar. The owner's own colours are untouched.
- `backend/app/routers/hub.py` and `event.py`: QR codes in `#14151A`.

**New files**

- `frontend/public/brand/ownerdesk-logo.svg` (Horizontal Dark Flat)
- `frontend/public/brand/ownerdesk-logo-large.svg` (Horizontal Dark)
- `frontend/public/brand/ownerdesk-mark.svg`
- `frontend/public/favicon.ico`, `favicon-32.png`, `apple-touch-icon.png`
- `frontend/public/icon-192.png` and `icon-512.png` (replaced)
- `frontend/public/fonts/manrope-latin-{400,700,800}-normal.woff2`, used by the server-rendered score and not-found pages
- `docs/OWNERDESK_RESTYLE.md` (this file)

---

## 4. Buttons marked as AI (teal)

Every control that spends an AI run was traced from the button to the API route, and on to `run_ai`/`spend_run` in the backend.

61 controls in 30 files:

- **Writing and content:**
  - Writer: posts, reply, ask, polish
  - Calendar: write/rewrite the month
  - Research: post ideas, research plan
  - Competitors: Check now
  - Reputation: draft a reply (×2)
  - Lead magnet: Write my guide
  - Campaigns: Write it for me
  - Brand: write options, write my brand message
  - Brand kit: write, rewrite from Knowledge Hub
  - Offers: Build my offer ladder
  - Website: Build my website, Show me the change, Rewrite with AI
- **Sales:**
  - Quotes: Draft the quotation, Fill the invoice
  - Inquiries: Add inquiry (writes the proposal), Write one, Write again
  - Leads: Draft reply
  - Gym: Say it, Finish and score, Score this call (×2), Start the role-play
- **Operations:**
  - Kits: the shared "New" button (SOP, hiring kit, role, charter, plan, Sort my tasks)
  - SOP set: Map my processes, Map again, Write N SOPs, Write this month's review now
  - Office: standup, Give instruction
  - Workforce: Start the job
  - Tools: Write it for me
  - Meetings: Pull out actions
  - Compliance: Explain for my business
- **Data and planning:**
  - Week: Plan my week, Rewrite my week
  - Goals: actions
  - Levers: Suggest ideas
  - Leaks: Get fixes
  - Knowledge Hub: Update my hub, Refresh
  - Company Brain: Ask
  - Win-Back: Run now
- **Onboarding:**
  - Read my website
  - Fill my Business Brain (×2)
- **Voice notes:** record, stop and use, upload (VoiceNote and Writer). A voice note is transcribed by the AI.

**Calls you may want to check:**

- "Check now" (Competitors) only spends a run when a website changed.
- "Run now" (Win-Back) spends at most one run a week.

**Left plain on purpose:**

- "Save review" only spends a run when its switch is on, and marking it would need a conditional class.
- Settings switches that turn on later AI work.
- Hub-calculated features (Business Score, bank statement reader, levers maths).

---

## 5. Left as it was, on purpose

1. **The Day-1 report (PNG, PDF, preview image and share page) keeps the old Blueprint look.**
   - The acceptance test `test_day1.py::test_png_is_1080x1920_under_1mb_in_the_brand_and_the_pdf_is_one_a4_page` checks the exact old colours and fonts.
   - Tests are only changed with the owner's permission.
   - To restyle it:
     1. Update that test's colour and font lines.
     2. Swap `TOKENS` and `FONTS` in `day1_render.py` to the new palette.
     3. Add Manrope TTF files to `backend/app/fonts`.
2. **The CRM and WhatsApp source labels stay as they were:** `"Action Hub · website"`, `"Action Hub · lead_magnet"` and `"action-hub"`.
   - They're data sent to each owner's Employz.ai or AiSensy account, and owners may filter on them.
   - Two tests check them.
   - Renaming them is a product decision.
3. **Owners' own outputs keep the owner's colours:** websites, visiting cards, quotations, invoices, SOP prints, brand-kit designs, and the brand-kit default accent `#F2A93B`.
4. **The footer speaker line** ("Akshat Dani · Chirag J.") comes from the program settings and is unchanged.
5. **Old documents** in `docs/` (v2.0–v2.2 handoffs and PDFs) still say Action Hub. They are history.

---

## 6. Tested

**Measured:**

- Backend: the full suite on the in-memory database. **713 passed.**
  - `test_agents.py::test_monthly_review_on_the_first_with_last_months_data` was left out. It already fails on v2.2 (its own data inserts two scores without a token). See `V22_HANDOFF.md`.
  - No test was changed.
- Frontend: `vite build` succeeds.
- Browser click-through (`qa/e2e.py`, on a freshly seeded demo with the built app): **16 of 18 checks pass.**
  - These 2 fail on unchanged v2.2 too, with the same timeouts:
    - "Business Brain can be edited later": the check still expects the old `/start` page.
    - "admin: cohort…": the check still expects admins to land on `/home`.
  - The click-through script is out of date, not the app. Both are left as they were.
- Every menu page (45) was opened at desktop width (1440×900).
- 13 key pages were opened at phone width (390×844), including the landing and login pages.
- No page errors.
- Screenshots are in `screens/`.

**Untested:**

- iOS Safari, and Android Chrome on a real device. Manrope loads from the app bundle, so it doesn't depend on Google Fonts.
