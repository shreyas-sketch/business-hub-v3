# Deployment checklist — Action Hub v2.1 + the live event link

Railway (Singapore) · MongoDB Atlas M10 · wildcard `*.employz.ai` · AiSensy · MSG91 · Razorpay · Amazon SES (sending and inbound).

Nothing here has been deployed. Every step is for your team to do, in this order. Each variable named here is in
`backend/.env.example`, with a one-line note.

Facts about outside services were checked on 20 Oct 2026 against their own docs (links at the end). Prices and plan limits
change, so recheck before you buy.

---

## 0. Before you start

1. Decide D1 (the Membership feature placement). It's one button — Admin → Live event → **Move them into Membership** — and it
   can be undone feature by feature. Until it's pressed, Membership members can connect Razorpay (Connections) and their gateway
   invoices are made, but they can't open the list, because **Quotations & invoices** is a Running the Business page.
2. The CA review of the compliance rules: Admin → Compliance rules → stamp "reviewed". Until then every date shows "not
   reviewed by a CA yet".
3. Generate the secrets once and keep them in a password manager, never in code:
   - `JWT_SECRET`: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`
   - `CONNECTIONS_KEY`: `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
     — never change it later (owners' stored keys become unreadable).
   - `EMAIL_EVENTS_SECRET`, `EMAIL_INBOUND_SECRET`, `DAY1_STATUS_WEBHOOK_SECRET`: 40+ random characters each.

## 1. MongoDB Atlas M10

1. Create the cluster on **AWS, Singapore (ap-southeast-1)** — the same city as the Railway servers, so every database call
   stays inside Singapore. MongoDB 7.0 or newer (the hub's driver needs 4.4+).
2. Turn on Cloud Backup (M10 and up).
3. Database user: password login, role `readWrite` on the database `action_hub` only.
4. Network access: add Railway's static outbound IPs (step 2.4) — not `0.0.0.0/0`. Railway says these IPs can be shared with
   other Railway customers, so the strong password still matters.
5. `MONGO_URL` = the `mongodb+srv://…` string; `DB_NAME=action_hub`. The hub creates its indexes itself on first start.
6. Throwaway database for the test suite (step 10): a second database name on the same cluster, e.g. `action_hub_tests` — the
   test run makes a random `action_hub_test_xxxx` database and empties it as it goes.

## 2. Railway (Singapore)

1. Plan: **Pro**. You need it for static outbound IPs (for Atlas) and for more than 2 custom domains on the service (Pro allows
   20, more on request; Hobby allows 2) — every Growth owner's own domain is one more.
2. New project → deploy from the repo. The `Dockerfile` at the root builds the frontend and runs the API.
   - Region: **Southeast Asia (Singapore)**. In config-as-code its id is `asia-southeast1-eqsg3a`.
   - `WEB_CONCURRENCY=2` (two server processes — what the 200-person test ran on). The event queue and the automations run
     inside these processes; nothing else to deploy.
3. Variables: everything from `.env.example`, with:
   - `APP_ENV=production`, `APP_URL=https://hub.employz.ai`, `SITES_DOMAIN=employz.ai`, `TRUSTED_PROXY_HOPS=1` (2 if Cloudflare
     proxies in front);
   - `ADMIN_PHONES` = your admins' numbers with +91;
   - `RUN_SCHEDULER=true`.
4. Settings → Networking → **Enable Static IPs**. You get 3 IPv4 addresses. Add all 3 to Atlas (step 1.4) **before** you deploy.
   If you ever move the service to another region, the IPs change.

## 3. Domains: `hub.employz.ai` and `*.employz.ai`

1. In Railway, add the custom domain `hub.employz.ai`, then the wildcard `*.employz.ai`.
2. For the wildcard, Railway shows **two CNAMEs and one TXT**: the wildcard CNAME, an `_acme-challenge` CNAME (needed for the
   certificate) and a TXT (wildcards don't verify without it). Add all three at your DNS provider.
3. On Cloudflare: don't proxy the `_acme-challenge` record or the `authorize.railwaydns.net` CNAME. Turn Universal SSL on and set
   SSL/TLS to **Full**.
4. These names must **not** be covered by the wildcard. Each has its own records (steps 7–8), which take precedence over the
   wildcard:
   - `mail.employz.ai` (sending);
   - `in.employz.ai` (inbound mail).
   They're already reserved, so no owner can take them as a website name.
5. Check: `https://hub.employz.ai` loads, and a test owner's website opens at `https://<name>.employz.ai`.

## 4. AiSensy — the hub's own WhatsApp number

Templates to submit to Meta through AiSensy. Approval can take a while, so do it the week before, not event week. Then make an
API campaign on each one and put its name in the variable:

| Variable | Category | Content |
|---|---|---|
| `AISENSY_OTP_CAMPAIGN` | Authentication | `{{1}}` = the code, with the copy-code button (`AISENSY_OTP_BUTTON=true`) |
| `AISENSY_LEAD_ALERT_CAMPAIGN` | Utility | to the owner: `{{1}}` business, `{{2}}` customer name, `{{3}}` customer phone, `{{4}}` message |
| `AISENSY_OWNER_ALERT_CAMPAIGN` | Utility | to the owner: `{{1}}` business, `{{2}}` the message |
| `AISENSY_DIGEST_CAMPAIGN` | Utility | morning digest: `{{1}}` business, `{{2}}` summary |
| `AISENSY_REPORT_CAMPAIGN` | Utility (media) | Day-1 report — see `docs/DAY1_REPORT.md` |

**The event depends on the number's messaging limit.**
- Since October 2025, Meta counts the limit per business portfolio. It counts unique people messaged first by the business in
  24 hours, and login codes count.
- A new portfolio starts at **250**. Business verification moves it to **2,000**.
- 200 attendees getting codes and alerts, plus your existing members, can come close to 250 in one day.
- Before the event, check the portfolio's current tier in AiSensy / Meta Business Manager. You want 2,000 or more.
- (Secondary source, see the end; check Meta's own page before you rely on it.)

## 5. MSG91 — SMS when WhatsApp can't deliver the login code

1. DLT registration (entity, sender ID, OTP template), then the OTP template in MSG91.
2. `MSG91_AUTH_KEY`, `MSG91_OTP_TEMPLATE_ID`.
3. The hub tries WhatsApp first, then SMS.
4. Test: send a code to a number that isn't on WhatsApp. It should arrive by SMS.

## 6. Razorpay

**The hub's own account (Membership and the programs):**
1. Live keys: `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`.
2. Subscriptions → Plans: a ₹1,999 monthly plan → `RAZORPAY_PLAN_ID_LITE` (or set it in Admin → Plans & features → Membership).
3. Webhooks: `https://hub.employz.ai/api/razorpay/webhook` with `payment.captured` and the `subscription.*` events, and a
   secret → `RAZORPAY_WEBHOOK_SECRET`.
4. Test mode first: a test subscription should upgrade a test account.

**Each owner's own Razorpay (automatic invoices):**
1. The owner connects it in Connections.
2. The hub shows the owner a webhook address with its own token, for `payment.captured` and `refund.processed`.
3. New in this build: every payer is matched to the owner's Customers list by phone, then email, or added. Each payment counts
   once.

## 7. Amazon SES — sending (Mumbai, `ap-south-1`)

1. SES → Identities → domain `mail.employz.ai`, with Easy DKIM. Add the 3 DKIM CNAMEs, an SPF TXT and a DMARC TXT
   (`_dmarc.mail.employz.ai`). Admin → Email shows anything still missing.
2. Request **production access** (out of the sandbox), or mail only reaches verified addresses.
3. Configuration set → event destination: SNS topic `hub-email-events` (Delivery, Bounce, Complaint) → HTTPS subscription
   `https://hub.employz.ai/api/hooks/email-events/<EMAIL_EVENTS_SECRET>`. The hub confirms the subscription itself.
4. Variables:
   - `EMAIL_PROVIDER=ses`, `AWS_REGION=ap-south-1`, `EMAIL_FROM_DOMAIN=mail.employz.ai`;
   - `SES_CONFIGURATION_SET`;
   - `EMAIL_EVENTS_SECRET`, `EMAIL_EVENTS_TOPIC_ARN` (the ARN of `hub-email-events`).
5. IAM user for the hub, allowed only:
   - `ses:SendEmail`, `ses:SendRawEmail`, `ses:GetEmailIdentity`;
   - `s3:GetObject` on `arn:aws:s3:::<inbound bucket>/inbound/*` (step 8).
   Its keys → `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`.
6. Test: send to `bounce@simulator.amazonses.com`. It should show as bounced in Admin → Email.

## 8. Amazon SES — inbound (inquiry addresses and replies), with S3 so big emails don't bounce

SES email receiving is available in Mumbai (`ap-south-1`). The SNS action alone bounces any email over 150 KB, so attachments
would bounce. This build reads each email from S3 instead.

1. SES → Identities → verify the domain `in.employz.ai`.
2. DNS: `MX in.employz.ai → 10 inbound-smtp.ap-south-1.amazonaws.com`.
3. S3 bucket in `ap-south-1`, e.g. `employz-hub-inbound`. A name without dots is simplest; one with dots works too, read
   path-style:
   - **Block all public access**: on.
   - **Default encryption**: SSE-S3. If you choose SSE-KMS instead, the hub's IAM user also needs `kms:Decrypt`, and SES needs
     `kms:GenerateDataKey` on that key. Don't turn on the S3 action's own "encrypt message" option: that encrypts on the client
     side, and the hub can't read it.
   - **Lifecycle rule**: delete objects under `inbound/` after 30 days. The hub keeps what it needs: the text, and PDF/JPG/PNG
     attachments, on the inquiry.
   - **Bucket policy**: allow `ses.amazonaws.com` to `s3:PutObject` on `arn:aws:s3:::employz-hub-inbound/inbound/*`, with
     conditions `aws:SourceAccount` = your account id and `aws:SourceArn` = your receipt rule's ARN.
4. SNS topic `hub-inbound`:
   - **Access policy**: allow `ses.amazonaws.com` to `sns:Publish`, with the same `aws:SourceAccount` condition.
   - **HTTPS subscription**: `https://hub.employz.ai/api/hooks/email-in/<EMAIL_INBOUND_SECRET>`. The hub confirms it itself.
5. Receipt rule set (make it **active**) → rule:
   - **Recipients**: `in.employz.ai`.
   - **Spam and virus scanning**: on.
   - **One action, S3**: bucket `employz-hub-inbound`, object key prefix `inbound/`, **SNS topic** `hub-inbound`.
   - Don't add a separate SNS action: that's the one that bounces mail over 150 KB.
6. Variables:
   - `INBOUND_EMAIL_DOMAIN=in.employz.ai`, `EMAIL_INBOUND_SECRET`;
   - `EMAIL_INBOUND_TOPIC_ARN` (the ARN of `hub-inbound`; required — anything from another topic is refused);
   - `EMAIL_INBOUND_BUCKET=employz-hub-inbound`;
   - `EMAIL_INBOUND_BUCKET_REGION` (blank = `AWS_REGION`), `EMAIL_INBOUND_MAX_MB=30`.
7. Test: from a Gmail account, email an owner's inquiry address twice:
   - once with a one-line message;
   - once with a 2 MB PDF.
   Both should become inquiries. The PDF should show under the second one, and only that owner can download it.
   - Attachments are kept only when the sender's address is proven (DMARC passes, as it does for Gmail).
   - Each business can keep up to 50 MB of them a day.

## 9. AI engine

1. `AI_PROVIDER=anthropic`, `ANTHROPIC_API_KEY`, `AI_MODEL` (blank = the built-in default).
2. Check the account's rate limits, especially **output tokens per minute**. In our 200-person test (simulated engine):
   - the event packs plus three in-hub AI demos made about 1,400 AI calls in roughly 10 minutes;
   - at our assumed answer sizes, that was about 1.5 million output tokens.
   With a limit of 80,000 output tokens a minute:
   - packs took about 5 minutes instead of about 1;
   - half the website drafts were left for after login;
   - 6 to 8 in 10 of the in-hub AI demo buttons said "busy, try again" (no run was used).

   See the event run sheet and `qa/loadtest/`. These are estimates until you run it with the real engine.
3. `AI_CONCURRENCY=32`, `AI_RETRIES=2`, `EVENT_AI_CONCURRENCY=24`: the tested defaults.

## 10. After the deploy (smoke test, about 30 minutes)

1. Log in with a WhatsApp code. Log in again on a number that isn't on WhatsApp (SMS).
2. Make a test owner:
   - website live at `<name>.employz.ai`;
   - fill its form; the lead and the WhatsApp lead alert should arrive.
3. The inbound email tests (step 8.7). The bounce test (step 7.6).
4. Razorpay test mode:
   - a payment to a test owner's connected account → an invoice, and the payer on Customers;
   - a refund → a credit note.
5. Admin → Live event: make a test cohort, open the link, build a pack on your phone, save it, close the link.
6. The test suite against the throwaway Atlas database (run from a laptop whose IP is allowed in Atlas):

   ```
   cd backend
   pip install -r requirements.txt -r requirements-dev.txt
   MONGO_URL='mongodb+srv://<test-user>:<password>@<cluster>/' python -m pytest -q
   ```

   Expected: everything passes (365 tests on MongoDB 8.3 here; not run on Atlas yet).
   - `test_agents.py::test_monthly_review_on_the_first_with_last_months_data` used to fail: its data had two Business Scores
     without a share token, and the `scores.token` unique index refused the second. Its scores now have tokens.
   - The tests make their own database (`action_hub_test_<random>`), so a throwaway cluster user is enough.

## 11. If something goes wrong

- Roll back: Railway → Deployments → redeploy the previous one. The database needs no rollback; nothing in this build changes
  existing data.
- The event link: Admin → Live event → switch **Link open** off. Packs already built stay.

---

Sources checked on 20 Oct 2026:
- [Amazon SES endpoints and quotas — email receiving regions](https://docs.aws.amazon.com/general/latest/gr/ses.html)
- [Railway — working with domains (wildcards)](https://docs.railway.com/networking/domains/working-with-domains)
- [Railway — deployment regions](https://docs.railway.com/reference/deployment-regions)
- [Railway — static outbound IPs](https://docs.railway.com/networking/static-outbound-ips)
- [AiSensy — WhatsApp message limits in 2026 (secondary source)](https://m.aisensy.com/blog/whatsapp-message-limits-guide/)
