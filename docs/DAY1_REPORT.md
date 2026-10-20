# Day-1 report — Business AI Action Hub

What was built from "Program hubs — Day-1 tool and report: change spec" (14 Oct 2026), for the Business AI hub only. No plan, price, credit or other feature changed.

## The flow

1. **Before the webinar.** In Admin → Day-1 reports → Webinars, add the webinar (it is a cohort, e.g. `WEB-OCT-14`). Its link is `APP_URL/report?c=WEB-OCT-14`.
2. **In the webinar.** The attendee opens the link (no login) and fills four short steps:
   - WhatsApp number, first name, optional city, and the consent box;
   - website, or "I don't have a website";
   - the Score's 10 questions;
   - 6 numbers: revenue band, team size, enquiries a month, close rate, average order value, and Google reviews in the last 12 months. No GST.
3. **The queue builds the report.** The page shows "Building your report… about N seconds". The queue then:
   - reads the website (the Score's 8 checks);
   - works out the Business AI Score;
   - picks the findings and "imagine" lines by rule;
   - works out the gap line;
   - draws the PNG, the PDF and a link-preview image.

   No AI is called. The run is in the database, so closing the tab loses nothing.
4. **The attendee sees a summary.** Their screen shows first name, score, the gap line, the three finding labels, and "See the full report now".
5. **Sending.** The team presses **Send all pending** for the cohort, or **Send report** for one person. The image goes from the hub's own AiSensy number, with a "See the full report" button to `/r/<token>`.
6. **Saving.** `/r/<token>` shows the first name only, plus "Save with your WhatsApp code". The hub's own login code creates a Free account (source `webinar`, cohort set) and attaches the report.
   - Saved this way, it also becomes their first Business AI Score, unless they already had one.
   - Logging in any other way with that number makes the report openable from the account, but never turns it into their Score: anyone can type a number, so only the person who saves that report with its code adopts it.
   - A later payment in the hub upgrades that same account. The hub has one account per number.
7. **Export.** Admin → Day-1 reports → Webinars → **Export cohort CSV** gives the file the Performance Hub imports.

## WhatsApp template to submit to Meta now (not in webinar week)

Create one template in AiSensy:

- **Header:** image. To send the PDF instead, use a document header and set `AISENSY_REPORT_MEDIA=pdf`.
- **Body:** `{{1}}, your Business AI report is ready. {{2}}`
  - `{{1}}` is the first name.
  - `{{2}}` is a line such as "Your Business AI Score is 41/100. At your numbers, about ₹20,40,000 a year goes cold." The wording is in Admin → Library → "WhatsApp message — line 2".
- **Button:** URL "See the full report" → `https://<your hub>/r/{{1}}`. The button's `{{1}}` is the report's token.
- **Campaign:** create an API campaign on the template and put its name in `AISENSY_REPORT_CAMPAIGN`.
- **Category:** Meta may class it as Marketing, since it names the 4-3-2-1 Action Program.

If the template has no button, set `AISENSY_REPORT_LINK=text` and add `{{3}}` to the body for the link.

Delivery and read ticks:

1. Set `DAY1_STATUS_WEBHOOK_SECRET`.
2. Point AiSensy's message-status webhook at `https://<your hub>/api/hooks/day1-status/<secret>`.

The webhook accepts Meta's `statuses` shape and a flat `{phone, status}` shape.

- When AiSensy returned a message id on sending, the status is matched by that id.
- Otherwise it marks the latest report successfully sent to that number in the last 14 days. This is approximate: a login-code message to the same number in that time could also be counted.

## Settings (see `backend/.env.example`)

| Setting | Default | What it does |
|---|---|---|
| `AISENSY_REPORT_CAMPAIGN` | none | The approved media template's API campaign. Without it a real deployment sends nothing, and Admin → Logs says so. |
| `AISENSY_REPORT_MEDIA` | `image` | `image` or `pdf` in the header. |
| `AISENSY_REPORT_LINK` | `button` | `button` or `text` (`{{3}}`). |
| `DAY1_STATUS_WEBHOOK_SECRET` | none | Turns on the sent/delivered/read webhook. |
| `DAY1_DAILY_CAP` | 5000 | Public reports a day (India time), all webinars together. |
| `DAY1_RUNS_PER_IP_PER_10MIN` | 30 | Per network address. |
| `DAY1_WORKERS` | 8 | Reports built at once in each server process. |
| `DAY1_SITE_SECONDS` | 20 | The longest the website check may take. |

## Limits and safety

- **One report per number per webinar.** A second try is told the report comes on WhatsApp. It never shows the earlier report, so typing someone else's number shows nothing.
- **Caps.** There is a daily cap, a per-network limit, and the consent box (the spec's exact sentence).
- **What anyone with the link sees.** The share page and its preview image show the first name only: no number, city or website.
  - The texts that appear there (eyebrow, title, the score's label, the gap line and the finding labels) can't be edited to use `{city}`, `{site}` or the business inputs.
- **Files.** The image and PDF addresses use a second random key, separate from the share link.
- **Opens.** Link previews by WhatsApp and other bots are not counted as opens.
- **No AI and no web research on the public run.** The only outside request is reading the website the attendee typed, through the hub's existing SSRF-guarded reader.
  - A site that can't be opened gives no website findings: nothing is guessed.
  - The page text isn't kept, only the 8 yes/no checks. There are no uploads for Business AI.
- **The AI spend limit.** The Action Hub has no hub-wide monthly AI spend limit (AI use is limited per owner by runs), and the Day-1 run uses no AI. So nothing can stop the hub on a webinar evening. The 500-report test checks that no AI run is recorded.

## The report

- **Order:** blocks run top to bottom as the spec lists them.
- **Tiles:** the score, then "Systems in place n of 10", then "Website checks n of 8".
- **Gap line:** enquiries a month × 25% × close rate × average order × 12 → "About ₹X a year", tagged *Estimate*.
- **Findings:** three, chosen by rule in the admin's priority order. For example:
  - no enquiry form on the site → "Website isn't catching anyone";
  - "None" for Google reviews in the last 12 months → "Google profile is quiet".
- **Imagine lines:** five. The first lines of the three findings, then the second lines of findings 1 and 2.
- **Editing:** every word, the order, on/off and the 25% are in Admin → Day-1 reports → Library. Preview shows a made-up attendee with unsaved edits.
- **Brand:** Blueprint tokens; Space Grotesk and Inter are bundled in `backend/app/fonts` under the SIL Open Font License.
  - aqua marks systems words, written `[like this]`;
  - amber marks rupee amounts;
  - no icons, photos or gradients.
- **Sizes:** the PNG is 1080×1920 and about 240 KB; the PDF is one A4 page. Long text shrinks to fit.

## Reused from the hub

- The Business AI Score (questions, website checks, scoring).
- The website reader.
- The WhatsApp login code (OTP, rate limits, 5 tries).
- Account creation on the Free plan.
- Cohorts and their codes.
- The AiSensy sender, which now also carries a media header and a URL button.
- The share-card pattern.
- Razorpay checkout and webhook.
- The rate limiter.
- The Blueprint design system.

## Decisions to check

- **The six numbers sit beside the Score's 10 questions, not in place of them.** The score stays comparable month to month. They are required on the webinar tool and optional on the in-hub Score, where they add the gap line.
- **Google reviews are asked, not looked up.** That keeps the rule's input honest without web research before the code.
- **Sending is manual (per person or "Send all pending").** The spec makes delivery timing the team's call.
  - Each send claims the report first, so pressing the button twice never sends a report twice.
- **Anyone can type someone else's number.** That person then gets one report on WhatsApp (only if the team sends it), and the number's one report for that webinar is used up. The per-network and daily limits bound this.
- **Revenue band and team size** appear in the meta line, the "It all runs through you" finding and the export. The spec's gap formula itself uses enquiries, close rate and order value.
- **Not matched by phone: payments taken outside the hub.** A Razorpay payment page or link isn't matched, because the hub can't tell which plan such a page sells. Payments in the hub's own checkout upgrade the account as before.

## Tests

`backend/tests/test_day1.py` covers acceptance tests 1, 2, 8, 9 and 10 plus:

- limits and consent;
- the share page;
- sending, resending and delivery status;
- the admin library and preview;
- rule choice;
- the in-hub Score's numbers.

The 500-report test takes about a minute.
