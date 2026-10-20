# Action Hub — live event build and v2.1 follow-ups (20 Oct 2026)

Not deployed. Code commits: dd5b748, 2be2ebb, 0a50dcc, 0c49f9f (all after beaa085).

## The live event link

- The attendee scans one QR, types their website and GST number (plus two taps), and presses **Build my pack**.
- Before any login, the hub builds:
  - by rules: the compliance calendar, the website check, the brand starter and the Knowledge Hub, started;
  - by AI (when switched on for the event): 3 brand messages, a website draft and ways to get more leads.
- **Save it to my hub**: name, email, WhatsApp number and the 6-digit code. The account is made with everything inside.
  Membership features stay open until the event's last day.
- Admin → Live event: the link on/off switch, caps on packs and on AI, the QR, the speaker screen (live numbers per area)
  and the runs list (retry a failed pack).
- One-click preset: "Move them into Membership" (Lead magnet, Ways to get more leads, Pipeline, Quotations & invoices,
  Money owed). It can be undone per feature. It waits on decision D1.
- All 9 demos are tested end to end with event accounts, including the event ending:
  1. Website live, with the QR.
  2. Swap with your neighbour.
  3. Be your own customer.
  4. Inquiry to quotation.
  5. Brand kit.
  6. Cash flow, with a what-if.
  7. Your daily number.
  8. The score, on Day 1 and Day 3.
  9. The Dashboard at 60%.
- The run sheet PDF ("Action Hub - Live Event Run Sheet") covers setup, the day, a proposed 3-day plan and each demo.

## Load test with 200 virtual attendees

Stand-ins played the AI engine, WhatsApp and the attendees' websites. The hub's code, 2 server processes and MongoDB 3.6
were real, on a 2-core machine.

**Measured, people arriving over 90 s, generous AI limit:**
- First things on screen: 6 s for the middle person, 12 s for 95 in 100.
- Whole pack: 71 s for the middle person, 101 s for 95 in 100.
- 0 errors in 12,953 requests.

**Measured, everyone within 10 s, AI limit of 80,000 output tokens a minute:**
- First things: 18 s.
- Whole pack: about 5 minutes.
- 103 of 200 website drafts were left for after login.
- In-hub AI buttons were mostly "busy". No runs were used.

**Estimated:**
- About ₹2 of AI a pack.
- About 1.5 million output tokens for 200 people.

**Untested:**
- The real AI engine and its limit.
- Real WhatsApp codes at 200 at once.
- Venue Wi-Fi.
- Railway and Atlas.
- Real phones.

## The 9 follow-ups

1. **Menu.** Inquiries, Gateway invoicing and Connections are on for Membership. The Razorpay invoices are listed under
   Quotations & invoices, which stays a Running the Business page until D1.
2. **Growth WhatsApp chat.** It still auto-replies 24×7, tested. Only the written proposals wait for Send.
3. **Knowledge Hub.** Saving uses no AI runs. "Update my hub" shows the AI-run cost first. Owner edits are kept.
4. **Demo seed.** Website-inquiry proposals are on in `seed_demo.py`. The re-seed with the real AI key is yours to run:
   `AI_PROVIDER=anthropic ANTHROPIC_API_KEY=… MONGO_URL=… DB_NAME=… python seed_demo.py`.
5. **Inquiry emails over 150 KB.** These go through the SES S3 action. PDF, PNG and JPEG attachments are kept, only from
   proven senders, up to 50 MB a day per business.
6. **Razorpay payers.** Each payer becomes a Customer: matched by phone, then email, otherwise created. Once per payment.
7. **iPhone.** "Paste a WhatsApp inquiry" is the main option, with a "Paste the message" button.
8. **Atlas test run.** Not run on Atlas: this workspace can't reach Atlas, and it needs your connection string. The
   command is in DEPLOY_CHECKLIST.md. The suite was run on a real MongoDB server (3.6) instead.
   - The one failing test (`test_monthly_review_on_the_first_with_last_months_data`) fails there too.
   - The cause: it inserts two score records without a token, and the unique index on `scores.token` rejects the second.
   - So it is expected to fail on Atlas as well (not run there). The test isn't changed, because tests are only changed
     with permission.
9. **Deployment checklist.** See `docs/DEPLOY_CHECKLIST.md`. It covers Railway Singapore, Atlas M10, `*.employz.ai`,
   AiSensy templates and the messaging limit, MSG91, Razorpay, and SES sending and inbound (`EMAIL_INBOUND_TOPIC_ARN`,
   `EMAIL_INBOUND_BUCKET`).

## Still open

- D1: where the Membership features sit.
- The CA review of the compliance rules.
- The 3-day plan in the run sheet is a proposal.
- Known limits:
  - A won lead later paid through Razorpay can count twice in a Customer's total.
  - Part payments count as purchases.
  - Refunds don't reduce the total.
