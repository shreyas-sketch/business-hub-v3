# The OwnerDesk test pack

One invented business (Deshmukh Modular Kitchens, Pune) with a document or file for each of the twelve v2.2 features: the
business profile, a funnel review, inquiries, bills (PDF and a phone photo), the bank statement (CSV, Excel, a locked PDF), a
year of sales from Tally, competitors, reviews, Win-Back settings, a research brief and the Lead Response set-up. Every name,
number, GSTIN, phone and account is invented. Dates are worked out from `TODAY` in `data.py`: change it and rebuild.

```
python gen.py <out folder>          # builds the pack (reportlab, openpyxl, Pillow, pypdf)
python validate.py <out folder>     # feeds it into a local hub on :8000 (mock AI) and prints what each feature shows
```

The load test uses it too: `python ../loadtest/attendees.py --pack <out folder>` adds the twelve features as demos.
