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

`attendees.py` refuses to run against a hub that sends real login codes. To test with the real AI engine:
1. Start the hub with `uvicorn app.main:app` and your key, instead of `lt_app:app`.
2. Keep it in development mode on localhost.
It then spends real AI money: about ₹2 a pack (an estimate).

## Results (20 Oct 2026, a 2-core machine running the load generator too, MongoDB 3.6)

These are in `results/`, measured with the stand-ins above:

| Run | Room | AI limit | First things | Whole pack | Errors |
|---|---|---|---|---|---|
| `run2_realistic` | arrive over 90 s | generous | 6 s middle, 12 s for 95% | 71 s middle, 101 s for 95% | 0 of 12,953 requests |
| `run4_burst_tight_wait` | all within 10 s | 80k output tokens a minute | 18 s / 29 s | 300 s / 416 s | in-hub AI demos refused ~70% "busy" |
| `run3_burst_tight` | all within 10 s | 80k, before the "wait your turn" fix | 18 s / 30 s | 61 s / 84 s, but most AI pieces missing | as above |
