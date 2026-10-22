# Action Hub v2.2 — Conversion, Data and the 5 AI agents (21 Oct 2026)

Not deployed. Code commits after 89e2f7d: 252a81b, f343db3, 009df01, b2efa6f, 8a5cb4d, fd7413f, and the last commit (Lead Response safety fix, docs).

## What's in which plan

**AI Advantage Membership (₹1,999 a month)**
- Conversion:
  - Funnel leak finder.
  - Follow-ups on day 1, 3 and 7.
  - Both reach the Membership with the admin's one-click preset (Admin → Live event → **Move them into Membership**).
    In the code they still sit in the Action Program, so nothing changes for anyone until it's pressed.
- Data (in the Membership in the code already):
  - Expense tracker.
  - Bank statement reader.
  - GST set-aside.
  - Customer risk.
  - Getting paid.
  - The sales upload (Tally or Excel) that feeds them.
- AI agents:
  - Competitor Watch (agent 2).
  - Reviews & reputation (agent 4).

**Action Program**
- Win-Back (agent 3).
- Content & company research (agent 5).

**Growth Mentorship**
- Lead Response (agent 1).

The admin can move any of these in Admin → Features & plans.

## The expense tracker, built for your customers

No customer file was attached, so I built it from the customer profile in this project's strategy docs: owner-run Indian
businesses with staff, GST and a CA.

- The categories follow the kind of business, picked from the Knowledge Hub's industry:
  - manufacturing: raw material, job work, consumables;
  - construction and interiors: site material, site labour, subcontractors;
  - trading: stock purchases, godown;
  - and services, logistics, food, wellness and consulting.
- Every business also gets the everyday heads: rent, salaries, electricity, fuel, marketing, and so on. Up to 10 of their own.
- Staff add their own bills (with a photo or PDF), and a manager approves them.
- The GST on each bill, and whether it can be claimed, feeds the GST set-aside.
- A monthly budget per head; repeating payments into Cash flow; a CSV for the CA.
- The bank statement reader fills it in one tap.

## Decisions I made, and why (the safer choice each time)

1. **Agent plans.**
   - Lead Response is in Growth: it sends from the owner's own WhatsApp number, which only Growth has.
   - Win-Back and company research are in the Action Program.
   - Competitor Watch and Reviews are in the Membership, as you asked.
2. **Follow-ups on the Membership are off until the owner switches them on.**
   - Each inquiry costs 3 AI runs, so the owner chooses it.
   - Accounts made at the live event start with them on, for the Day 2 demo.
   - From the Action Program up they stay on by default, as before.
3. **Competitors' ads.**
   - Meta's Ad Library API only returns political and social-issue ads in India.
   - So the hub gives a one-tap link to Meta's public Ad Library, and never reads Instagram or Facebook.
4. **"Lead prospecting" became company research.**
   - The hub makes a plan: kinds of companies, filters, questions to qualify them, search links and a CSV template.
   - It never scrapes or stores people's details, and never runs the searches itself.
5. **Instagram and Facebook messages for Lead Response aren't built.** They need the owner's Employz.ai inbox connected. The
   page says so.
6. **Reviews.**
   - The hub never posts a reply for the owner: approve, copy, open Google, "I've posted it".
   - Review requests offer nothing in return, as Google requires.
   - A referral ask goes only to a reviewer matched exactly to a customer.
7. **Bank statements.**
   - The file is never stored, and the PDF password is used once and never stored.
   - Long numbers, e-mails and UPI IDs are masked. The list is deleted after 24 hours. Owner only, and no AI reads it.
   - Each file is read in a separate process with memory and time limits, so a huge or broken file can't slow the hub.
   - Salaries go into Expenses only when the owner ticks them, because managers can see Expenses.
   - A row where the statement doesn't say money in or out is marked "Check" and added only if the owner picks it.
8. **GST set-aside is an estimate, and says so.**
   - Due dates come from the compliance rules, never the AI.
   - It never overwrites an amount the owner typed, unless they press **Replace it**.
   - When the bills' GST is bigger, nothing is carried forward. Keeping more aside is the safer mistake.
9. **The demo seed refuses to wipe a real database.** It runs only in local development, with no WhatsApp, SMS, e-mail or
   payment key set, on an empty database or one it made — unless `SEED_DEMO_WIPE` names that database on purpose.

## Google reviews: needs your decision

- **What Google's terms say** (checked 21 Oct 2026):
  - They expressly allow keeping place IDs, and latitude and longitude for 30 days.
  - They don't mention review words, stars or ratings.
  - The general terms say Maps content isn't cached except where expressly allowed.
- **What the hub does once `GOOGLE_PLACES_API_KEY` is set:**
  - It keeps a Google review's words, stars and author, the daily rating, and competitors' Google ratings.
  - It keeps each for at most 30 days, then clears it. A sweep checks every hour.
- **Today the key is blank, so this is off.**
  - Owners paste reviews, and replies are drafted the same way.
  - Competitor Watch reads websites only.
- **Your options:**
  1. Leave it off (the safest).
  2. Get legal advice or Google's confirmation, then set the key.
  3. Later: the Google Business Profile API. The owner signs in with Google, and the hub can list their own reviews and post
     replies. Not built.

## Tested

**Measured:**
- Full test suite: 713 passed on the in-memory database. The one known failing test was left out (see below).
- The 368 new v2.2 tests also passed on a real MongoDB server (3.6).
- Every new page was checked at desktop and phone width: no errors, nothing wider than the screen.
- 200 virtual attendees, people arriving over 90 s, final code:
  - First things on screen: 6 s for the middle person, 12 s for 95 in 100.
  - The whole pack: 72 s, and 106 s for 95 in 100.
  - 0 errors in 13,982 requests.
  - All 200 found their biggest leak.
  - Next morning, 400 follow-ups fell due at the same moment. All were written within 7.5 minutes (with a stand-in AI).
  - All 200 had a follow-up ready, and all 200 sent one.
- 200 at once, tight AI limit (80,000 tokens a minute), on the build just before the last review fixes:
  - 197 of 200 packs. 3 connections dropped with the test machine at 100%.
  - The whole pack took about 5 minutes.
  - All 197 found their leak.
  - 295 follow-ups were written within 5.5 minutes of falling due.

**Estimated:**
- The bank statement reader can use up to about 3 GB at the worst moment, so give the Railway service at least 4 GB.

**Untested:**
- The real AI engine and its limit.
- Real WhatsApp templates on owners' own numbers (Lead Response).
- Google Places with a real key. The code was tested against a stand-in.
- Real bank statements. The reader is tested on invented samples in HDFC, SBI, ICICI, Axis and Kotak layouts.
- Real competitors' websites from the venue.
- Railway and Atlas.

## Fixed along the way

- **Lead Response with nothing to send from.**
  - It can now be switched on only once the owner's number and the first-reply template are set.
  - If the template is removed later, the plain instant reply answers instead, and the owner is told.
  - So a customer is never left without a first reply.

- **PDFs drawn at the same moment.** Reports and invoices drawn at once in different threads could break each other's fonts.
  The full suite caught it once. Now one PDF is drawn at a time (a few milliseconds each).
- **The website reader.** Pages now have an overall time limit, and packed pages can't grow past the size limit. Shared and
  documentation address ranges count as private.

## Known limits

- Scanned PDF bank statements have no text to read. The page asks for Excel or CSV.
- Google shares only its 5 "most relevant" reviews per place.
- Websites built mostly in JavaScript show little text to Competitor Watch.
- Follow-ups, Competitor Watch, the review check, Win-Back and the hourly Google clean-up need `RUN_SCHEDULER=true`.
- The known failing test, `test_agents.py::test_monthly_review_on_the_first_with_last_months_data`:
  - its own data inserts two scores without a token;
  - unchanged, because tests change only with your permission.
- `test_day1::test_send_all_pending_twice_at_once_sends_each_report_once` is sometimes flaky on the in-memory database. It was
  flaky before v2.2.

## The files

- `Action Hub v2.2 - Feature Guide.pdf`: the 12 new features, what each does on its own, and how to show it live.
- `Action Hub - Live Event Run Sheet v2.2.pdf`:
  - the run sheet with demos 10 and 11 (leak finder, follow-ups);
  - the swap moved to Day 1;
  - the speaker demos for the data features and agents;
  - the v2.2 load test.
- `docs/DEPLOY_CHECKLIST.md`: section 0.5 and the steps marked v2.2.
- `action-hub-v2.2.zip`: the code.
- `action-hub-v2.2.patch`: everything since 89e2f7d.
