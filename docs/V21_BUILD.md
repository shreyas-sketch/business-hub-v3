# Action Hub v2.1 — Subscription Model build (P0 + P1)

Built from "Action Hub v2.1 Change Spec — Subscription Model Plan", October 2026. Code: the `action-hub` repo, commits 15b09d7 → 256db59 (on top of the Day-1 report, 58a3854). Not deployed.

## What was built

### P0 (launch demos)

- **Knowledge Hub** (Business Brain): identity, money basics and targets, a completeness meter. Every output built from it refreshes when it changes. Anything the owner edited is kept, with a "your Knowledge Hub changed" banner.
- **Smart Dashboard**: one widget registry with 13 tiles, cached for 60 seconds. It is the landing page once the Knowledge Hub is 60% complete. The owner picks the layout; the admin switches tiles on or off and sets their order. It replaces the Control Room.
- **Compliance calendar**: dates come only from the rules table, which the admin edits and a CA stamps as reviewed. The owner is reminded 30, 7 and 1 days before, and on the day. Documents are kept with each item. Licences ask for their expiry date. Extensions move the date.
- **Cash flow, next 30 days**: the balance is rolled forward from its date. Late dues aren't counted until paid. Shows the shortfall and the dues to collect first, with a what-if.
- **Am I on target**: invoiced or paid, projection, gap and the pace needed each day. Alerts on the 10th and 20th.
- **Automatic payment follow-ups**: by email after the owner approves the wording once. A wording change pauses them. They stop on payment or a reply. WhatsApp sends automatically only from Growth Mentorship.
- **Brand kit**: message system, design tokens (which reach the website, launch kit and invoices) and 11 server-drawn designs, Hindi included.
- **SOP set**: 2 AI runs per SOP, with refunds, versions, assignment and print.
- **Mark as posted**, with a posting log.
- **Ways to get more leads.**
- **Launch demo account**: 9820000007.

### P1

- **Inquiries inbox**: an email address for each owner (Amazon SES → SNS), plus pasted or shared WhatsApp inquiries. Every inquiry gets an automatic proposal.
- **Email replies**: replies to the hub's emails stop the follow-ups and alert the owner.
- **Invoicing**: direct invoices and billing entry. The owner's Razorpay turns each payment into an invoice (or settles the matching due), and each refund into a credit note.
- **Website**: edit by prompt, and uploaded lead-magnet PDFs that are only released after the form is filled.

## Decisions (D1–D6 and the safer choices)

**D1–D6 as built**
- **D1**: new features are in Membership. The Dashboard is in Free, because acceptance requires Free owners to see their own tiles.
- **D2**: approve once, then email goes automatically; WhatsApp is automatic from Growth only.
- **D4**: SES ap-south-1.
- **D5**: an admin rename.
- **D6**: files are kept in a plain `files` collection, not GridFS.

**Safer choices**
- Every new automation only ever messages the owner, apart from the approved emails.
- Proposals never contain a number, discount or date that isn't in the Brain.
- A WhatsApp proposal always waits for a tap.
- Email auto-replies go only to senders authenticated by DMARC.
- Website inquiries get a proposal only when the owner switches it on.

**Spec gaps left open**
- Emails over 150 KB need the SES S3 action.
- Gateway customers don't become Customers yet.

## Checks (measured)

- 316 backend tests: 315 pass. 1 failure is pre-existing; it comes from the in-memory test database.
- Screenshot sweep of every page, for every plan and role, desktop and phone.
- Two independent code reviews: P0 found 20 issues and P1 found 17. All fixed with tests, except the spec gaps above.

## Before go-live

- A CA reviews the compliance rules.
- Management confirms which features move into Membership.
- SES setup: sending domain (SPF, DKIM, DMARC), inbound domain MX and receipt rule, SNS topics and their ARNs.
- Each owner connects their own Razorpay in test mode first.
- Seed the demo with a real AI key.
