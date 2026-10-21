# AiSensy templates: ready to submit

Every WhatsApp message the hub starts is sent on a template approved by Meta. Create each one in AiSensy (Manage →
Template Message → New template), wait for approval, then make an **API campaign** on it and put the campaign's name in
the place shown. The variables must stay in this order: the hub fills `{{1}}`, `{{2}}`… in exactly this order.

Rules Meta applies, so these pass first time:
- A template can't start or end with a variable. Each body below starts and ends with fixed text.
- Each variable needs a sample value when you submit. Use the samples given.
- Don't add or remove variables. A template with a different count fails to send, and Admin → Logs says why.
- Submit the week before the event. Approval can take from minutes to a day or two.

Language: English for all. A Hindi or Marathi copy can be added later as a second language on the same template name.

---

## A. The hub's own number (set once, in Railway variables)

### 1. Login code: `AISENSY_OTP_CAMPAIGN`
Also used by the event link's "Send my code" and by the Day-1 report.

- **Name:** `hub_login_code`
- **Category:** Authentication
- **Body:** Meta fixes the text for this category. Pick the options in AiSensy:
  - "{{1}} is your verification code." (fixed)
  - Add the security line: "For your security, do not share this code." **On**
  - Expiry line: **10 minutes**. Codes in the hub last 10 minutes.
- **Button:** Copy code. Keep `AISENSY_OTP_BUTTON=true`.
- **Sample:** `{{1}}` = `482913`

### 2. New inquiry alert to the owner: `AISENSY_LEAD_ALERT_CAMPAIGN`
- **Name:** `hub_new_inquiry_alert`
- **Category:** Utility
- **Body:**
  ```
  New inquiry for {{1}} on your Business AI Action Hub.

  Name: {{2}}
  Phone: {{3}}
  Message: {{4}}

  Reply from your hub, or call them directly.
  ```
- **Samples:** `{{1}}` Shree Ganesh Interiors · `{{2}}` Neha Joshi · `{{3}}` +91 98330 00011 · `{{4}}` Need full interiors for a 2BHK in Thane.

### 3. Any other alert to the owner: `AISENSY_OWNER_ALERT_CAMPAIGN`
Used for compliance reminders, cash-flow warnings, the Monday CEO report, WhatsApp chats that need the owner, new email
inquiries and attachments.

- **Name:** `hub_owner_alert`
- **Category:** Utility
- **Body:**
  ```
  Update for {{1}} from your Business AI Action Hub:

  {{2}}

  Open your hub to see the details.
  ```
- **Samples:** `{{1}}` Shree Ganesh Interiors · `{{2}}` GSTR-3B for September is due on 20 Oct. Mark it done in your compliance calendar once it's filed.

### 4. Morning digest to the owner: `AISENSY_DIGEST_CAMPAIGN`
- **Name:** `hub_morning_digest`
- **Category:** Utility
- **Body:**
  ```
  Good morning. Here's today for {{1}}:

  {{2}}

  Open your hub to act on it.
  ```
- **Samples:** `{{1}}` Shree Ganesh Interiors · `{{2}}` 2 new inquiries, 4 messages in your Send list, ₹75,000 overdue from 2 customers.

### 5. Day-1 report to the webinar attendee: `AISENSY_REPORT_CAMPAIGN`
Only needed for the ₹99 webinar report (`/report`). See `docs/DAY1_REPORT.md`.

- **Name:** `hub_day1_report`
- **Category:** Utility
- **Header:** Image. Use Document instead to send the PDF, and set `AISENSY_REPORT_MEDIA=pdf`.
- **Body:**
  ```
  Hi {{1}}, your Business AI report is ready.

  {{2}}

  Tap the button below to see the full report.
  ```
- **Button:** Visit website, dynamic URL. Text `See the full report`, URL `https://hub.employz.ai/r/{{1}}`. Use your real hub
  address in place of `hub.employz.ai`.
  - The button's `{{1}}` is the report's link code, not the name.
  - Without a button: set `AISENSY_REPORT_LINK=text`, and use this body instead:
    `Hi {{1}}, your Business AI report is ready.` / `{{2}}` / `See the full report here: {{3}}` / `Thank you for joining the webinar.`
- **Samples:** `{{1}}` Neha · `{{2}}` Your Business AI Score is 41/100. At your numbers, about ₹20,40,000 a year goes cold. · button `{{1}}` `a1b2c3d4`

---

## B. An owner's own number (Growth Mentorship and up)

Each owner creates these in **their own** AiSensy account and types each template's name in the hub: Connections →
WhatsApp — your own number. They're sent from the owner's number, so the customer sees the owner's business.

For 2, 5 and 6, the hub writes the whole message, starting "Hello <name>,", and sends it as `{{3}}`. So those templates
don't greet the customer again.

### 1. Instant reply to a new inquiry: Connections → "Template: instant reply"
- **Name:** `instant_reply`
- **Category:** Utility
- **Body:**
  ```
  Hello {{1}}, thank you for contacting {{2}}. We've got your message and will reply shortly.
  ```
- **Samples:** `{{1}}` Neha · `{{2}}` Shree Ganesh Interiors

### 2. Follow-up: "Template: follow-up"
Also used for inquiry proposals, quotation follow-ups and instructions from the AI office.

- **Name:** `follow_up`
- **Category:** Utility (Meta may move it to Marketing; that's fine, it still sends)
- **Body:**
  ```
  Message from {{2}} for {{1}}:

  {{3}}

  You can reply to this message directly.
  ```
- **Samples:** `{{1}}` Neha · `{{2}}` Shree Ganesh Interiors · `{{3}}` Hello Neha, just checking in on the 2BHK interiors you asked about. We can visit this Saturday to measure. Does 11 am work?

### 3. Payment reminder: "Template: payment reminder"
- **Name:** `payment_reminder`
- **Category:** Utility
- **Body:**
  ```
  Hello {{1}}, a gentle reminder from {{2}} that {{3}} was due on {{4}}.

  How to pay: {{5}}

  If you've already paid, please ignore this message. Thank you!
  ```
- **Samples:** `{{1}}` Joseph · `{{2}}` Shree Ganesh Interiors · `{{3}}` ₹35,000 · `{{4}}` 18 Oct · `{{5}}` UPI to shreeganesh@upi or bank transfer

### 4. Review request: "Template: review request"
- **Name:** `review_request`
- **Category:** Marketing
- **Body:**
  ```
  Hello {{1}}, thank you for choosing {{2}}! If you were happy with our work, could you leave us a quick review? It takes a minute and really helps us: {{3}}

  Thank you!
  ```
- **Samples:** `{{1}}` Neha · `{{2}}` Shree Ganesh Interiors · `{{3}}` https://g.page/r/shree-ganesh/review

### 5. Customer message: "Template: customer message"
Reorder reminders, referral requests, festival greetings.

- **Name:** `customer_message`
- **Category:** Marketing
- **Body:**
  ```
  Message from {{2}} for {{1}}:

  {{3}}

  Reply here if you'd like to know more.
  ```
- **Samples:** `{{1}}` Neha · `{{2}}` Shree Ganesh Interiors · `{{3}}` Hello Neha, wishing you a very happy Diwali from all of us at Shree Ganesh Interiors!

### 6. Money campaign: "Template: money campaign"
The monthly campaign (old-customer revival, festival offer, referral drive). A campaign can also name its own template.

- **Name:** `money_campaign`
- **Category:** Marketing
- **Body:**
  ```
  Message from {{2}} for {{1}}:

  {{3}}

  Reply to this message to book or ask a question.
  ```
- **Samples:** `{{1}}` Neha · `{{2}}` Shree Ganesh Interiors · `{{3}}` Hello Neha, this Diwali we're giving a free chimney with any kitchen booked this month. Shall we send you a quote?

---

## After approval
1. In AiSensy, create one **API campaign** per template, choosing that template.
2. Put the hub's five campaign names in Railway (section A), then redeploy.
3. Owners type their six campaign names in Connections (section B), then press **Test the connection**.
4. Test: log in with a real number (the code arrives), then send yourself an inquiry from a test owner's website (the
   alert arrives).
5. If a message doesn't arrive, Admin → Logs shows AiSensy's own reason: wrong campaign name, template not approved, or a
   low wallet balance.
