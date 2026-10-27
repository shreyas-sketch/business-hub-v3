# 200 people at once — the live event load test

The full event flow, run by 200 virtual attendees on one network address:
- they scan the link;
- type their website and GST number;
- watch the pack build;
- save it with the WhatsApp code.

Then the speaker's demos run, with the whole room on the same demo within a minute:
- website live and the QR;
- swap with your neighbour;
- be your own customer;
- inquiry to quotation;
- brand kit;
- cash flow with a what-if;
- the daily number;
- the score twice;
- the Dashboard.

Every person, number, GST number and website is made up.

## What's real and what's a stand-in

- **Real:** the hub's own code, two server processes, and MongoDB.
- **Stand-ins (`lt_app.py`):**
  - The AI engine. `limiter.py` plays the provider's limits: output tokens a minute (`LIMIT_OTPM`) and calls in flight. It gives
    each call an answer time of about 8 s in the middle, with a slower tail up to about 25 s.
  - WhatsApp: the login code takes 0.3–1.5 s to send.
  - The attendees' own websites (`fakeweb.py`): most open in 0.3–4 s, 12% take 8–15 s, 4% never answer in time, and 4% are
    broken.

## Running it

```
python fakeweb.py 8850 &
LIMIT_OTPM=400000 python limiter.py &          # 80000 for the "tight AI limit" run
DB_NAME=loadtest1 ./start_hub.sh &             # MONGO_URL=… for another MongoDB
python attendees.py --n 200 --arrive 90 --typing 20,45 --reading 10,30 --out results.json
# everyone at once:  --arrive 10 --typing 0,1 --reading 0,5 --stage-limit 300
```

`attendees.py` refuses to run against a hub that sends real login codes.

**The real AI account** (`real_api_probe.py`, no hub or database needed): prints each key's limits as the provider reports
them (requests, input and output tokens a minute, and the organisation, so you can see whether two keys are two accounts),
then with `--burst 200` sends 200 real website drafts at once through the hub's own engine pool and reports answered,
waits, "busy", output tokens a minute reached and cost. From `backend/`: `python ../qa/loadtest/real_api_probe.py --burst 200`.

**A live hub** (`live_event_load.py`): paced packs through the public event flow with real owner and competitor websites
and the server's own AI key, plus a monitor timing `/api/health` every 2 s. Needs an open event code; nothing is saved to an
account. `python live_event_load.py --base https://ownerdesk.io --sites sites.json --stages CODE:5:2,CODE:10:3,CODE:15:2`.

**The engine pool in the simulation:** each key is its own account at `limiter.py` (`LIMIT_SHARED=1` puts them all on one,
as keys of one Anthropic organisation are). E.g. `ANTHROPIC_API_KEYS=sim-2 ./start_hub.sh` for two accounts, or
`AI_FALLBACK=gemini GEMINI_API_KEY=sim-g ./start_hub.sh` for a backup engine. To test with the real AI engine:
1. Start the hub with `uvicorn app.main:app` and your key, instead of `lt_app:app`.
2. Keep it in development mode on localhost.
It then spends real AI money: about ₹2 a pack (an estimate).

## Results (20 Oct 2026, a 2-core machine running the load generator too)

The database was a real MongoDB server, but an old one: 3.6.8, the only server build this sandbox could install. It was reached
with PyMongo 4.9.2 / Motor 3.6, the newest drivers that still talk to 3.6. Production uses MongoDB 7+ with the pinned driver,
so treat the database side of these numbers as indicative.

These are in `results/`, measured with the stand-ins above:

| Run | Room | AI limit | First things | Whole pack | Errors |
|---|---|---|---|---|---|
| `run2_realistic` | arrive over 90 s | generous | 6 s middle, 12 s for 95% | 71 s middle, 101 s for 95% | 0 of 12,953 requests |
| `run4_burst_tight_wait` | all within 10 s | 80k output tokens a minute | 18 s / 29 s | 300 s / 416 s | in-hub AI demos refused ~70% "busy" |
| `run3_burst_tight` | all within 10 s | 80k, before the "wait your turn" fix | 18 s / 30 s | 61 s / 84 s, but most AI pieces missing | as above |

## Results (25 Oct 2026): the event flow plus the twelve v2.2 features, with the event extras in Membership

Run with `--pack` (the test pack in `qa/testpack`), so after the demos above each attendee also does, with real files: the leak
finder's fixes, a year of sales from Tally (customer risk, getting paid, GST set-aside), two bills with their PDF/photo, the bank
statement (70% CSV, 30% the locked PDF), a review reply, Competitor Watch (two competitors, Check now), Win-Back (switch on,
Run now) and research (post ideas, research plan) — with Win-Back, research, WhatsApp chats and Lead Response moved into
Membership as for the live event. 200 attendees, one network address, MongoDB 8, a 4-core machine running the load generator too.

| Run | AI limit | Whole pack | AI demos refused "busy" | Errors |
|---|---|---|---|---|
| `v23_realistic_with_v22_features` | generous (400k output tokens a minute) | 68 s middle, 103 s for 95% | none | 0 of 18,257 requests |
| `v23_tight_ai` | 80k a minute | 258 s / 361 s; 95 websites written only after saving | ~60% of brand kit, quotation and research | none besides those |
| `v23_tight_ai_retries6` | 80k, `AI_RETRIES=6` | 263 s / 367 s | ~57% — waiting longer doesn't add tokens | as above |

- Every v2.2 feature held up with 200 at once: uploads and pages answer in well under a second (bank statement: CSV 0.2 s,
  locked PDF 1 s in the middle), AI features 8–16 s like the rest.
- `bank_burst.py`: all 200 upload their statement within 5 seconds — all read on the first try, the slowest after 21 s (the
  reader's limit is 60 s), with 2 server processes as on Railway.
- With a tight AI limit nothing breaks and no run is lost (a refused AI step is refunded), but the room waits: the AI account's
  output-tokens-a-minute limit decides the day. Check it in the provider's console before the event.
