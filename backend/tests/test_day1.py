"""Day-1 report (the ₹99 webinar tool): the change spec's acceptance tests 1, 2, 8, 9 and 10 for the Action Hub, plus the
limits, the share page, sending, the admin library and the Score's six numbers. Every person here is made up."""
import base64
import csv
import io
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PIL import Image

from app import day1, day1_render, day1_runs
from app.config import settings
from app.db import db
from tests.conftest import new_owner
from tests.test_billing import KEY, SECRET, WH, rzp, sig  # noqa: F401  (rzp is a fixture: a fake Razorpay)

ADMIN = "9999900000"
ANSWERS_YES = {k: "yes" for k in ("focus", "margins", "sales", "payments", "offers", "reply", "followup", "content", "process", "numbers")}
NUMBERS = {"revenue_band": "50l_1cr", "team_size": "10_30", "enquiries": 40, "close_rate": "20", "aov": 85000, "reviews_12m": "none"}


# ───────────────────────── a small website to check ─────────────────────────
PAGES = {
    "/": b"""<html><head><title>Example Interiors</title><meta name="description" content="Kitchens and wardrobes in Pune">
<meta name="viewport" content="width=device-width"></head><body><h1>Example Interiors</h1><p>Modular kitchens, wardrobes.</p>
<p>Call 98765 43210</p><a href="/contact">Contact us</a></body></html>""",
    "/contact": b"""<html><head><title>Contact</title></head><body><p>Visit our studio in Pune.</p><p>hello@example-interiors.in</p></body></html>""",
}


class _Site(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = PAGES.get(self.path.split("?")[0])
        self.send_response(200 if body else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body or b"not found")

    def log_message(self, *a):
        pass


@contextmanager
def site_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()


# ───────────────────────── helpers ─────────────────────────
@pytest.fixture
def setting():
    """Changes a setting for one test only."""
    old = {}

    def change(**kw):
        for k, v in kw.items():
            old.setdefault(k, getattr(settings, k))
            object.__setattr__(settings, k, v)
    yield change
    for k, v in old.items():
        object.__setattr__(settings, k, v)


async def webinar(code: str = "WEB-OCT-14") -> object:
    admin = await new_owner(ADMIN)
    r = await admin.post("/api/admin/cohorts", json={"code": code, "name": "₹99 webinar · 14 Oct", "workshop_date": "2026-10-14"})
    assert r.status_code == 200, r.text
    return admin


def body(phone: str, **over) -> dict:
    return {"cohort": "WEB-OCT-14", "phone": phone, "name": "Riya Sharma", "city": "Pune", "website": None, "answers": dict(ANSWERS_YES),
            "numbers": dict(NUMBERS), "consent": True, **over}


async def make_run(client, phone: str, **over) -> dict:
    r = await client.post("/api/day1/runs", json=body(phone, **over))
    assert r.status_code == 200, r.text
    return r.json()


# ───────────────────────── acceptance 1 ─────────────────────────
async def test_phone_only_run_is_saved_then_the_code_makes_the_account_and_a_payment_upgrades_it(client, rzp):  # noqa: F811
    await webinar()
    made = await make_run(client, "98330 11111")
    assert made["status"] == "queued" and len(made["token"]) >= 16 and made["eta_seconds"] >= 3
    run = await db().day1_runs.find_one({"token": made["token"]})
    assert run["phone"] == "+919833011111" and run["user_id"] is None and run["consent"]["text"] == day1.CONSENT_TEXT
    assert await db().users.count_documents({"phone": "+919833011111"}) == 0          # a saved run, no account

    building = (await client.get(f"/api/day1/runs/{made['token']}")).json()
    assert building["status"] == "queued" and building["eta_seconds"] >= 3
    assert await day1_runs.drain() == 1
    ready = (await client.get(f"/api/day1/runs/{made['token']}")).json()
    assert ready["status"] == "done" and ready["first_name"] == "Riya" and ready["score"] == 100 and not ready["saved"]
    assert "phone" not in json.dumps(ready) and "Pune" not in json.dumps(ready)       # first name only

    # one first run per number per webinar
    again = await client.post("/api/day1/runs", json=body("+91 98330-11111"))
    assert again.status_code == 409 and await db().day1_runs.count_documents({}) == 1

    # "Save this report" → the hub's own WhatsApp code → a Free account with the run attached
    code = await client.post(f"/api/day1/runs/{made['token']}/code")
    assert code.status_code == 200 and code.json()["dev_code"]
    assert (await client.post(f"/api/day1/runs/{made['token']}/verify", json={"code": "000000" if code.json()["dev_code"] != "000000" else "111111"})).status_code == 400
    code = (await client.post(f"/api/day1/runs/{made['token']}/code")).json()["dev_code"]
    v = await client.post(f"/api/day1/runs/{made['token']}/verify", json={"code": code})
    assert v.status_code == 200 and v.json() == {"ok": True, "new": True}
    user = await db().users.find_one({"phone": "+919833011111"})
    assert user["plan"] == "free" and user["cohort"] == "WEB-OCT-14" and user["name"] == "Riya"
    assert (await db().day1_runs.find_one({"token": made["token"]}))["user_id"] == user["_id"]
    assert await db().events.count_documents({"type": "signup", "meta.src": "webinar"}) == 1
    first = await db().scores.find_one({"ws": user["_id"]})
    assert first["source"] == "day1" and first["score"] == 100 and first["numbers"]["enquiries"] == 40   # their free first score
    mine = (await client.get("/api/day1/mine")).json()
    assert [m["token"] for m in mine] == [made["token"]] and mine[0]["png_url"].endswith(".png")
    full = (await client.get(f"/api/day1/runs/{made['token']}/full")).json()
    assert full["report"]["title"] == "Riya, here is where you stand"

    # a later Razorpay payment on the same number upgrades the same account; the run stays
    await client.put("/api/business", json={"name": "Example Interiors", "city": "Pune", "industry": "Interiors", "offers": [], "whatsapp": "",
                                            "email": "", "language": "English", "ideal_customer": "", "why_us": "", "proof": ""})
    order = (await client.post("/api/billing/checkout", json={"tier": "program"})).json()
    assert order["mode"] == "razorpay" and order["kind"] == "order"
    event = json.dumps({"event": "payment.captured", "payload": {"payment": {"entity": {
        "id": "pay_T1", "order_id": order["order_id"], "amount": order["amount"], "status": "captured"}}}}).encode()
    hook = await client.post("/api/razorpay/webhook", content=event, headers={"x-razorpay-signature": sig(WH, event)})
    assert hook.status_code == 200
    after = await db().users.find_one({"phone": "+919833011111"})
    assert after["_id"] == user["_id"] and (await client.get("/api/me")).json()["user"]["effective_plan"] == "program"
    assert (await client.get("/api/day1/mine")).json()[0]["token"] == made["token"]
    assert await db().users.count_documents({"phone": "+919833011111"}) == 1


async def test_logging_in_any_way_attaches_the_numbers_runs_and_never_overwrites_a_score(client):
    await webinar()
    made = await make_run(client, "9833022222")
    owner = await new_owner("9833022222")            # the ordinary login page, before the report is even built
    run = await db().day1_runs.find_one({"token": made["token"]})
    uid = run["user_id"]
    assert uid and (await owner.get("/api/day1/mine")).json()[0]["token"] == made["token"]
    await day1_runs.drain()
    # anyone can type a number, so a run only becomes someone's Score when they save that report with its code
    assert not await db().scores.find_one({"ws": uid}) and not await db().site_scans.find_one({"_id": uid})

    saver = await new_owner("9833022299")
    made_b = await make_run(client, "9833022288")
    code = (await saver.post(f"/api/day1/runs/{made_b['token']}/code")).json()["dev_code"]
    assert (await saver.post(f"/api/day1/runs/{made_b['token']}/verify", json={"code": code})).status_code == 200
    b_uid = (await db().day1_runs.find_one({"token": made_b["token"]}))["user_id"]
    assert not await db().scores.find_one({"ws": b_uid})
    await day1_runs.drain()
    assert (await db().scores.find_one({"ws": b_uid}))["source"] == "day1"   # saved while building: the score arrives when built

    # someone who already took the Score in the hub keeps theirs
    existing = await new_owner("9833033333")
    eid = (await existing.get("/api/me")).json()["user"]["id"]
    await existing.post("/api/score", json={"answers": {**ANSWERS_YES, "reply": "no"}})
    made2 = await make_run(client, "9833033333")
    await day1_runs.drain()
    other = await new_owner("9833033333")
    assert (await other.get("/api/day1/mine")).json()[0]["token"] == made2["token"]
    assert await db().scores.count_documents({"ws": eid}) == 1 and (await db().scores.find_one({"ws": eid}))["score"] == 90
    # someone else's report stays theirs
    assert (await owner.get(f"/api/day1/runs/{made2['token']}/full")).status_code == 403


# ───────────────────────── acceptance 2 ─────────────────────────
async def test_site_without_a_form_and_no_reviews_in_12_months_gives_those_findings_and_the_gap(client):
    await webinar()
    with site_server() as base:
        made = await make_run(client, "9833044444", website=base + "/")
        assert await day1_runs.drain() == 1
    run = await db().day1_runs.find_one({"token": made["token"]})
    assert run["site"]["read"] and not run["site"]["checks"]["form"] and not run["site"]["checks"]["proof"]
    labels = [f["label"] for f in run["report"]["findings"]]
    assert "Website isn't catching anyone" in labels and "Google profile is quiet" in labels
    assert run["finding_keys"][:2] == ["no_form", "quiet_google"]
    assert run["gap"]["gap_year"] == 40 * 0.25 * 0.20 * 85000 * 12 == 2_040_000
    assert run["report"]["missing"]["line"] == "About ₹20,40,000 a year in enquiries that go cold" and run["report"]["missing"]["tag"] == "Estimate"
    assert "about" in run["report"]["missing"]["math"] and "at your numbers" in run["report"]["missing"]["math"]
    assert len(run["report"]["imagine"]) == 5 and not any("tomorrow" in line.lower() for line in run["report"]["imagine"])
    assert run["report"]["bottom"]["program"] == "The 4-3-2-1 Action Program puts all five in place in the next 30 days"
    assert run["report"]["meta"].startswith("Riya · Pune · 127.0.0.1") and "40 enquiries a month" in run["report"]["meta"]

    # every finding comes from a rule on the inputs: reviews in the last year, and a site with a form, give neither
    cases = [({**NUMBERS, "reviews_12m": "6_20"}, "/"), ({**NUMBERS, "reviews_12m": "not_sure"}, None)]
    for i, (numbers, path) in enumerate(cases):
        with site_server() as base:
            if path:
                PAGES["/form"] = PAGES["/"].replace(b"</body>", b"<form><input name=phone></form></body>")
            made = await make_run(client, f"98330555{i:02d}", website=(base + "/form") if path else None, numbers=numbers)
            await day1_runs.drain()
        keys = (await db().day1_runs.find_one({"token": made["token"]}))["finding_keys"]
        assert "quiet_google" not in keys and "no_form" not in keys
    assert keys == ["no_website"]


async def test_the_gap_formula_for_other_numbers_and_with_no_enquiries():
    assert day1.gap({"enquiries": 120, "close_rate": "30", "aov": 4500}, 25)["gap_year"] == round(120 * 0.25 * 0.30 * 4500 * 12)
    assert day1.gap({"enquiries": 7, "close_rate": "40", "aov": 1999}, 25)["gap_year"] == round(7 * 0.25 * 0.40 * 1999 * 12)
    assert day1.gap({"enquiries": 40, "close_rate": "20", "aov": 85000}, 30)["gap_year"] == 2_448_000
    run = {**day1.sample_run(), "numbers": {**NUMBERS, "enquiries": 0}}
    report = day1.build(run, day1.score_for(run), day1.defaults())
    assert "₹0" not in report["missing"]["line"] and report["gap"]["gap_year"] == 0 and "no enquiries" in report["missing"]["line"].lower()


# ───────────────────────── acceptance 8 ─────────────────────────
async def test_500_queued_runs_complete_with_no_failures_and_no_ai(client):
    await webinar()
    for i in range(500):
        await day1_runs.create(phone=f"+9198{i:08d}", first_name="Asha", city="", website_url=None, answers=dict(ANSWERS_YES),
                               numbers=dict(NUMBERS), cohort="WEB-OCT-14")
    queued = await db().day1_runs.find_one({"phone": "+919800000499"})
    assert await day1_runs.eta(queued) > await day1_runs.eta(await db().day1_runs.find_one({"phone": "+919800000000"}))
    assert await day1_runs.drain(workers=8) == 500
    assert await db().day1_runs.count_documents({"status": "done"}) == 500
    assert await db().day1_runs.count_documents({"status": {"$ne": "done"}}) == 0
    assert await db().day1_files.count_documents({}) == 500
    # the public run makes no AI call, so no AI allowance or spend limit can stop it on a webinar evening
    assert await db().runs.count_documents({}) == 0 and await db().events.count_documents({"type": "limit_hit"}) == 0
    assert sum([r["ai_cost_inr"] async for r in db().day1_runs.find({}, {"ai_cost_inr": 1})]) == 0
    logs = (await (await new_owner(ADMIN)).get("/api/admin/day1/logs")).json()
    assert logs["queue"] == {"queued": 0, "building": 0, "failed": 0, "done_24h": 500} and logs["ai"]["cost_per_run_inr"] == 0


async def test_a_failing_build_retries_then_fails_and_the_admin_can_retry(client, monkeypatch):
    admin = await webinar()
    made = await make_run(client, "9833066666")
    real = day1_render.all_files
    monkeypatch.setattr(day1_render, "all_files", lambda *a: (_ for _ in ()).throw(RuntimeError("disk full")))
    for _ in range(3):
        await db().day1_runs.update_many({"status": "queued"}, {"$set": {"queued_at": day1_runs.now()}})
        await day1_runs.drain()
    run = await db().day1_runs.find_one({"token": made["token"]})
    assert run["status"] == "failed" and run["attempts"] == 3 and "disk full" in run["error"]
    assert (await client.get(f"/api/day1/runs/{made['token']}")).json()["error"]
    logs = (await admin.get("/api/admin/day1/logs")).json()
    assert logs["queue"]["failed"] == 1 and logs["failures"][0]["error"].startswith("RuntimeError")
    monkeypatch.setattr(day1_render, "all_files", real)
    assert (await admin.post(f"/api/admin/day1/runs/{run['_id']}/retry")).status_code == 200
    assert await day1_runs.drain() == 1 and (await db().day1_runs.find_one({"_id": run["_id"]}))["status"] == "done"
    assert (await admin.post(f"/api/admin/day1/runs/{run['_id']}/retry")).status_code == 400
    # a report left "building" by a restart goes back in the queue
    await db().day1_runs.update_one({"_id": run["_id"]}, {"$set": {"status": "building", "attempts": 1,
                                                                   "started_at": day1_runs.now() - day1_runs.timedelta(minutes=10)}})
    await day1_runs.requeue_stale()
    assert (await db().day1_runs.find_one({"_id": run["_id"]}))["status"] == "queued"


# ───────────────────────── acceptance 9 ─────────────────────────
async def test_cohort_csv_has_one_row_per_run_with_the_spec_columns(client):
    admin = await webinar()
    a = await make_run(client, "9833077771", name="Kiran", website=None)
    b = await make_run(client, "9833077772", name="Meera", numbers={**NUMBERS, "close_rate": "40", "enquiries": 12})
    c = await make_run(client, "9833077773", name="Vikram")
    await day1_runs.drain()
    await db().day1_runs.update_one({"token": c["token"]}, {"$set": {"status": "failed", "error": "x"}})
    saver = await new_owner("9833077772")                         # Meera saved hers
    await saver.put("/api/business", json={"name": "Meera Foods", "city": "Nagpur", "industry": "Food", "offers": [], "whatsapp": "",
                                           "email": "meera@example.in", "language": "English", "ideal_customer": "", "why_us": "", "proof": ""})
    run_a = await db().day1_runs.find_one({"token": a["token"]})
    assert (await admin.post(f"/api/admin/day1/runs/{run_a['_id']}/send")).status_code == 200
    await client.get(f"/r/{a['token']}", headers={"user-agent": "Mozilla/5.0 (Linux; Android 14)"})

    r = await admin.get("/api/admin/day1/cohorts/WEB-OCT-14/export.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv") and "attachment" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert r.text.splitlines()[0] == "phone,email,name,signed_up_at,tool_kind,completed,number_label,number_value,extra_json,report_sent_at,report_opened_at"
    assert len(rows) == 3 and [x["name"] for x in rows] == ["Kiran", "Meera", "Vikram"]
    k, m, v = rows
    assert k["phone"] == "+919833077771" and k["tool_kind"] == "action_hub_score" and k["completed"] == "true"
    assert k["number_label"] == "Business AI Score" and k["number_value"] == "100" and k["signed_up_at"] == "" and k["email"] == ""
    assert k["report_sent_at"].endswith("+05:30") and k["report_opened_at"].endswith("+05:30")
    assert m["signed_up_at"] and m["email"] == "meera@example.in" and m["report_sent_at"] == "" and m["report_opened_at"] == ""
    assert v["completed"] == "false" and v["number_value"] == ""
    extra = json.loads(m["extra_json"])
    assert extra["inputs"] == {"enquiries": 12, "close_rate": 40, "aov": 85000, "revenue_band": "50l_1cr", "team_size": "10_30",
                               "reviews_12m": "none", "city": "Pune", "website": None, "website_read": False}
    assert extra["findings"] == ["no_website", "quiet_google"] and extra["saved"] and extra["gap_year"] == round(12 * 0.25 * 0.40 * 85000 * 12)
    assert set(json.loads(k["extra_json"])["answers"]) == set(ANSWERS_YES)
    assert (await client.get("/api/admin/day1/cohorts/WEB-OCT-14/export.csv")).status_code in (401, 403)

    stats = next(x for x in (await admin.get("/api/admin/day1/cohorts")).json() if x["code"] == "WEB-OCT-14")
    assert {k: stats[k] for k in ("runs", "completed", "failed", "sent", "pending", "opened", "saved")} == \
        {"runs": 3, "completed": 2, "failed": 1, "sent": 1, "pending": 1, "opened": 1, "saved": 1}
    assert stats["link"].endswith("/report?c=WEB-OCT-14")


# ───────────────────────── acceptance 10 ─────────────────────────
async def test_png_is_1080x1920_under_1mb_in_the_brand_and_the_pdf_is_one_a4_page(client):
    await webinar()
    with site_server() as base:
        made = await make_run(client, "9833088888", website=base + "/")
        await day1_runs.drain()
    run = await db().day1_runs.find_one({"token": made["token"]})
    png = await client.get(day1_runs.media_url(run, "png").replace(settings.app_url, ""))
    assert png.status_code == 200 and png.headers["content-type"] == "image/png" and len(png.content) < 1_000_000
    img = Image.open(io.BytesIO(png.content)).convert("RGB")
    assert img.size == (1080, 1920)
    colours = {"#%02X%02X%02X" % c for _, c in img.getcolors(maxcolors=1_000_000)}
    assert img.getpixel((0, 0)) == (0x0A, 0x18, 0x26)                                    # ink
    for token in ("ink", "panel", "line", "text", "grey", "aqua", "amber"):
        assert day1_render.TOKENS[token] in colours, token

    pdf = await client.get(day1_runs.media_url(run, "pdf").replace(settings.app_url, ""))
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(pdf.content))
    assert len(reader.pages) == 1
    box = reader.pages[0].mediabox
    assert (round(float(box.width), 1), round(float(box.height), 1)) == (595.3, 841.9)   # A4
    text = reader.pages[0].extract_text()
    assert "Riya, here is where you stand" in text and "4-3-2-1 Action Program" in text and day1.CONSENT_TEXT[:40] in text.replace("\n", " ")
    fonts = {str(f.get_object()["/BaseFont"]).split("+")[-1] for f in reader.pages[0]["/Resources"]["/Font"].values()}
    assert fonts == {"SpaceGrotesk-SemiBold", "Inter-Regular", "Inter-Medium", "Inter-SemiBold", "Inter-Bold"}   # the brand's two families only

    preview = await client.get(f"/r/{made['token']}/preview.png")
    assert preview.status_code == 200 and Image.open(io.BytesIO(preview.content)).size == (1200, 630)
    assert (await client.get("/r/m/not-a-real-key-000.png")).status_code == 404


async def test_a_long_report_still_fits_one_page():
    run = day1.sample_run()
    cfg = day1.defaults()
    for f in cfg["findings"].values():
        f["text"] = f["text"] + " " + f["text"]
    run["first_name"] = "Venkataraghavan"
    report = day1.build(run, day1.score_for(run), cfg)
    png = day1_render.png(report)
    assert Image.open(io.BytesIO(png)).size == (1080, 1920) and len(png) < 1_000_000
    from pypdf import PdfReader
    assert len(PdfReader(io.BytesIO(day1_render.pdf(report))).pages) == 1


# ───────────────────────── limits and consent ─────────────────────────
async def test_the_public_run_checks_consent_link_number_and_limits(client, setting):
    admin = await webinar()
    form = (await client.get("/api/day1/form?c=web-oct-14")).json()
    assert form["valid"] and form["open"] and form["consent"] == day1.CONSENT_TEXT and len(form["questions"]) == 10
    keys = [n["key"] for n in form["numbers"]]
    assert keys == ["revenue_band", "team_size", "enquiries", "close_rate", "aov", "reviews_12m"]
    assert not any("gst" in json.dumps(form).lower() for _ in [0])                      # never asks for GST
    assert [c["label"] for c in form["numbers"][0]["choices"]] == ["Under ₹10 L", "₹10–50 L", "₹50 L–1 Cr", "₹1–5 Cr", "₹5 Cr+"]
    assert [c["label"] for c in form["numbers"][1]["choices"]] == ["0–10", "10–30", "30–50", "50+"]
    assert [c["label"] for c in form["numbers"][3]["choices"]] == ["10%", "20%", "30%", "40%+"]
    assert not (await client.get("/api/day1/form?c=NOPE")).json()["valid"]

    bad = [({"consent": False}, 400, "tick the box"), ({"cohort": "NOPE"}, 404, "isn't valid"), ({"phone": "12345"}, 400, "valid"),
           ({"phone": "+14155550123"}, 400, "Indian"), ({"name": "١٢٣"}, 400, "English letters"), ({"website": "javascript:alert(1)"}, 400, "website"),
           ({"answers": {"focus": "yes"}}, 400, "Answer every question"), ({"numbers": {**NUMBERS, "aov": None}}, 400, "Average order value"),
           ({"numbers": {**NUMBERS, "close_rate": "55"}}, 400, "how many buy"), ({"numbers": {**NUMBERS, "enquiries": -3}}, 400, "Enquiries")]
    for over, status, words in bad:
        r = await client.post("/api/day1/runs", json={**body("9833099999"), **over})
        assert r.status_code == status and words.lower() in r.json()["detail"].lower(), (over, r.text)
    assert await db().day1_runs.count_documents({}) == 0

    assert (await admin.patch("/api/admin/day1/cohorts/WEB-OCT-14", json={"open": False})).status_code == 200
    assert (await client.post("/api/day1/runs", json=body("9833099999"))).status_code == 403
    await admin.patch("/api/admin/day1/cohorts/WEB-OCT-14", json={"open": True})

    setting(day1_runs_per_ip=2)
    assert (await client.post("/api/day1/runs", json=body("9833099901"))).status_code == 200
    assert (await client.post("/api/day1/runs", json=body("9833099902"))).status_code == 200
    r = await client.post("/api/day1/runs", json=body("9833099903"))
    assert r.status_code == 429 and "network" in r.json()["detail"]
    setting(day1_runs_per_ip=100, day1_daily_cap=3)
    assert (await client.post("/api/day1/runs", json=body("9833099904"))).status_code == 200
    r = await client.post("/api/day1/runs", json=body("9833099905"))
    assert r.status_code == 429 and "tomorrow" in r.json()["detail"] and await db().day1_runs.count_documents({}) == 3
    # a second run for the same number in another webinar is allowed
    await admin.post("/api/admin/cohorts", json={"code": "WEB-OCT-21", "name": "", "workshop_date": None})
    setting(day1_daily_cap=10)
    assert (await client.post("/api/day1/runs", json=body("9833099901", cohort="WEB-OCT-21"))).status_code == 200


async def test_the_share_page_shows_the_first_name_only_and_counts_real_opens(client):
    await webinar()
    made = await make_run(client, "9833012121", name="riya sharma", city="Nashik")
    page = await client.get(f"/r/{made['token']}")
    assert page.status_code == 200 and "being built" in page.text            # opening it before it's built isn't an open
    await day1_runs.drain()
    page = await client.get(f"/r/{made['token']}", headers={"user-agent": "WhatsApp/2.23.20.0"})   # the link preview
    assert "Riya, here is where you stand" in page.text and "Save with your WhatsApp code" in page.text
    assert f"/report/save/{made['token']}" in page.text and f"/r/{made['token']}/preview.png" in page.text
    for private in ("Nashik", "Sharma", "98330", "12121", "+91"):
        assert private not in page.text, private
    assert '<span class="money">₹20,40,000</span>' in page.text and "linear-gradient" not in page.text
    run = await db().day1_runs.find_one({"token": made["token"]})
    assert run["opened_at"] is None                                   # a crawler is not an open
    await client.get(f"/r/{made['token']}", headers={"user-agent": "Mozilla/5.0 (iPhone)"})
    assert (await db().day1_runs.find_one({"token": made["token"]}))["opened_at"] is not None
    assert (await client.get("/r/nope")).status_code == 404


# ───────────────────────── WhatsApp ─────────────────────────
async def test_send_report_send_all_pending_resend_and_delivery_status(client, setting):
    admin = await webinar()
    tokens = [(await make_run(client, f"98330300{i:02d}", name=n))["token"] for i, n in enumerate(["Asha", "Bina", "Chetan"])]
    await day1_runs.drain()
    await db().outbox.delete_many({})
    r = await admin.post("/api/admin/day1/cohorts/WEB-OCT-14/send-pending")
    assert r.json() == {"queued": 3}
    await day1_runs.settle()
    sent = [m async for m in db().outbox.find({})]
    assert len(sent) == 3 and {m["kind"] for m in sent} == {"day1_report"}                 # nothing but the report
    asha = next(m for m in sent if m["to"] == "+919833030000")
    run = await db().day1_runs.find_one({"token": tokens[0]})
    assert asha["params"] == ["Asha", "Your Business AI Score is 100/100. At your numbers, about ₹20,40,000 a year goes cold."]
    assert asha["media"]["url"] == f"{settings.app_url}/r/m/{run['media_key']}.png"
    assert asha["buttons"][0]["parameters"][0]["text"] == tokens[0]
    assert run["sent_at"] and run["send_count"] == 1
    assert (await admin.post("/api/admin/day1/cohorts/WEB-OCT-14/send-pending")).json() == {"queued": 0}

    first_sent = run["sent_at"]
    assert (await admin.post(f"/api/admin/day1/runs/{run['_id']}/send")).status_code == 200      # resend
    again = await db().day1_runs.find_one({"_id": run["_id"]})
    assert again["send_count"] == 2 and again["sent_at"] == first_sent and await db().outbox.count_documents({}) == 4

    setting(aisensy_report_link="text", aisensy_report_media="pdf")
    bina = await db().day1_runs.find_one({"token": tokens[1]})
    await admin.post(f"/api/admin/day1/runs/{bina['_id']}/send")
    last = await db().outbox.find_one({"to": "+919833030001"}, sort=[("at", -1)])
    assert last["params"][2] == f"{settings.app_url}/r/{tokens[1]}" and last["media"]["url"].endswith(".pdf") and "buttons" not in last

    # sent / delivered / read come back from AiSensy
    assert (await client.post("/api/hooks/day1-status/anything", json={})).status_code == 404     # off until a secret is set
    setting(day1_status_secret="s3cret-hook-0000")
    assert (await client.post("/api/hooks/day1-status/wrong", json={})).status_code == 404
    meta = {"entry": [{"changes": [{"value": {"statuses": [{"recipient_id": "919833030000", "status": "delivered"}]}}]}]}
    assert (await client.post("/api/hooks/day1-status/s3cret-hook-0000", json=meta)).json()["matched"] == 1
    flat = {"data": {"phone": "9833030000", "status": "read"}}
    assert (await client.post("/api/hooks/day1-status/s3cret-hook-0000", json=flat)).json()["matched"] == 1
    run = await db().day1_runs.find_one({"token": tokens[0]})
    assert run["delivered_at"] and run["read_at"]
    assert (await client.post("/api/hooks/day1-status/s3cret-hook-0000", json={"phone": "9000000000", "status": "read"})).json()["matched"] == 0

    # not configured on a real deployment: nothing is sent and the admin is told
    setting(env="production")
    assert not settings.local_dev
    r = await admin.post(f"/api/admin/day1/runs/{bina['_id']}/send")
    assert r.status_code == 502 and "AiSensy" in r.json()["detail"]


# ───────────────────────── Admin → Day-1 reports ─────────────────────────
async def test_admin_edits_the_library_and_constants_previews_and_resets(client):
    admin = await webinar()
    assert (await client.get("/api/admin/day1/config")).status_code in (401, 403)
    cfg = (await admin.get("/api/admin/day1/config")).json()
    no_form = next(f for f in cfg["findings"] if f["key"] == "no_form")
    assert no_form["label"] == "Website isn't catching anyone" and no_form["rule"] == "The website has no enquiry form"
    assert cfg["constants"] == {"cold_rate_pct": 25} and "gap_year" in cfg["placeholders"]

    edit = {"findings": {"quiet_google": {**{k: next(f for f in cfg["findings"] if f["key"] == "quiet_google")[k] for k in ("on", "imagine")},
                                          "priority": 1, "label": "Nobody has reviewed you lately", "text": "No reviews for {first_name} in a year."}},
            "constants": {"cold_rate_pct": 30}, "templates": {"bottom_picture": "In a month: {enquiries} enquiries a month, none lost."}}
    bad = await admin.put("/api/admin/day1/config", json={"templates": {"title": "Hi {phone}"}})
    assert bad.status_code == 400 and "{phone}" in bad.json()["detail"]
    assert (await admin.put("/api/admin/day1/config", json={"constants": {"cold_rate_pct": 95}})).status_code == 400

    pv = (await admin.post("/api/admin/day1/preview", json={"config": edit, "sample": {"first_name": "Sam", "numbers": {"enquiries": 100}}})).json()
    assert pv["report"]["findings"][0]["label"] == "Nobody has reviewed you lately" and pv["report"]["gap"]["gap_year"] == round(100 * 0.30 * 0.20 * 85000 * 12)
    img = Image.open(io.BytesIO(base64.b64decode(pv["png"].split(",", 1)[1])))
    assert img.size == (1080, 1920) and pv["png_bytes"] < 1_000_000
    assert (await db().day1_config.count_documents({})) == 0                  # a preview saves nothing

    saved = await admin.put("/api/admin/day1/config", json=edit)
    assert saved.status_code == 200 and saved.json()["constants"]["cold_rate_pct"] == 30
    made = await make_run(client, "9833014141")
    await day1_runs.drain()
    run = await db().day1_runs.find_one({"token": made["token"]})
    assert run["report"]["findings"][0] == {"key": "quiet_google", "label": "Nobody has reviewed you lately", "text": "No reviews for Riya in a year."}
    assert run["gap"]["gap_year"] == round(40 * 0.30 * 0.20 * 85000 * 12) and run["report"]["bottom"]["picture"] == "In a month: 40 enquiries a month, none lost."
    assert await db().day1_config_log.count_documents({}) == 1

    reset = (await admin.post("/api/admin/day1/config/reset")).json()
    assert reset["constants"]["cold_rate_pct"] == 25 and next(f for f in reset["findings"] if f["key"] == "quiet_google")["label"] == "Google profile is quiet"


async def test_findings_are_chosen_by_rule_and_priority_never_invented():
    run = day1.sample_run()
    cfg = day1.defaults()
    keys = day1.triggered(run["answers"], run["site"], run["numbers"])
    assert keys[:3] == ["no_form", "quiet_google", "slow_reply"] and "no_website" not in keys
    cfg["findings"]["no_form"]["on"] = False
    chosen, imagine = day1.choose(cfg, keys)
    assert chosen == ["quiet_google", "slow_reply", "no_followup"] and len(imagine) == 5
    lines = {line for k in chosen for line in cfg["findings"][k]["imagine"]}
    assert all(line in lines for line in imagine)                              # each line answers a finding above
    unread = day1.triggered(run["answers"], {"host": "x.in", "read": False, "checks": {}}, run["numbers"])
    assert not {"no_form", "no_whatsapp", "no_proof", "not_mobile", "no_website"} & set(unread)   # an unreadable site is never guessed at
    strong = day1.triggered(ANSWERS_YES, {"host": "x.in", "read": True, "checks": {k: True for k in ("form", "whatsapp", "proof", "mobile")}},
                            {**NUMBERS, "reviews_12m": "20_plus"})
    assert strong == []
    chosen, imagine = day1.choose(cfg, strong)
    assert chosen == [] and imagine == day1.DEFAULT_IMAGINE[:5]


# ───────────────────────── the Score asks the six numbers too ─────────────────────────
async def test_the_logged_in_score_takes_the_six_numbers_and_shows_the_gap(client):
    c = await new_owner("9833015151")
    q = (await c.get("/api/score")).json()
    assert len(q["questions"]) == 10 and [n["key"] for n in q["numbers"]] == day1.NUMBER_KEYS
    r = await c.post("/api/score", json={"answers": ANSWERS_YES, "numbers": {**NUMBERS, "close_rate": "bad"}})
    assert r.status_code == 400
    s = (await c.post("/api/score", json={"answers": ANSWERS_YES, "numbers": NUMBERS})).json()
    assert s["score"] == 100 and s["numbers"]["aov"] == 85000 and s["gap"]["year"] == 2_040_000
    assert (await c.get("/api/score")).json()["latest"]["gap"]["line"].startswith("About ₹20,40,000 a year")


# ───────────────────────── from the independent review ─────────────────────────
async def test_report_files_download_for_any_latin_name(client):
    await webinar()
    made = await make_run(client, "9833016161", name="Łukasz Șerban")
    await day1_runs.drain()
    run = await db().day1_runs.find_one({"token": made["token"]})
    assert run["first_name"] == "Łukasz"
    for ext in ("png", "pdf"):
        r = await client.get(day1_runs.media_url(run, ext).replace(settings.app_url, ""))
        assert r.status_code == 200 and "filename*=UTF-8''" in r.headers["content-disposition"]


async def test_send_all_pending_twice_at_once_sends_each_report_once(client):
    admin = await webinar()
    for i in range(6):
        await make_run(client, f"98330170{i:02d}", name="Neha")
    await day1_runs.drain()
    await db().outbox.delete_many({})
    first = await admin.post("/api/admin/day1/cohorts/WEB-OCT-14/send-pending")
    second = await admin.post("/api/admin/day1/cohorts/WEB-OCT-14/send-pending")
    assert first.json()["queued"] == 6 and second.json()["queued"] in (0, 6)
    await day1_runs.settle()
    assert await db().outbox.count_documents({"kind": "day1_report"}) == 6
    assert await db().day1_runs.count_documents({"send_count": 1}) == 6 and await db().day1_runs.count_documents({"sending_at": {"$ne": None}}) == 0


async def test_share_link_texts_may_only_name_the_first_name(client):
    admin = await webinar()
    for body_ in ({"templates": {"title": "{first_name} from {city}, here is where you stand"}},
                  {"templates": {"eyebrow": "{program} · {site}"}}):
        r = await admin.put("/api/admin/day1/config", json=body_)
        assert r.status_code == 400 and "share link" in r.json()["detail"]
    cfg = (await admin.get("/api/admin/day1/config")).json()
    f = next(x for x in cfg["findings"] if x["key"] == "no_form")
    r = await admin.put("/api/admin/day1/config", json={"findings": {"no_form": {**{k: f[k] for k in ("on", "priority", "text", "imagine")},
                                                                                 "label": "{site} isn't catching anyone"}}})
    assert r.status_code == 400
    # the full report (sent only to their WhatsApp) may use them
    assert (await admin.put("/api/admin/day1/config", json={"templates": {"bottom_picture": "In {city}, a month from now: every enquiry caught."}})).status_code == 200


async def test_login_still_works_if_attaching_reports_fails(client, monkeypatch):
    async def broken(user):
        raise RuntimeError("database blip")
    monkeypatch.setattr(day1_runs, "attach_runs", broken)
    c = await new_owner("9833018181")
    assert (await c.get("/api/me")).status_code == 200


async def test_small_enquiry_counts_never_read_about_zero_and_ticks_match_the_message_id(client, setting):
    run = {**day1.sample_run(), "numbers": {**NUMBERS, "enquiries": 2}}
    report = day1.build(run, day1.score_for(run), day1.defaults())
    assert "about 0.5 a month" in report["missing"]["math"] and "about 0 a month" not in report["missing"]["math"]

    await webinar()
    tokens = [(await make_run(client, f"98330190{i:02d}"))["token"] for i in range(2)]
    await day1_runs.drain()
    await day1_runs.send_many([r["_id"] async for r in db().day1_runs.find({})])
    await db().day1_runs.update_one({"token": tokens[0]}, {"$set": {"wa_message_id": "wamid.ABC"}})
    setting(day1_status_secret="s3cret-hook-0000")
    meta = {"entry": [{"changes": [{"value": {"statuses": [{"id": "wamid.ABC", "recipient_id": "919833019000", "status": "read"}]}}]}]}
    assert (await client.post("/api/hooks/day1-status/s3cret-hook-0000", json=meta)).json()["matched"] == 1
    assert (await db().day1_runs.find_one({"token": tokens[0]}))["read_at"]
    # a status for a report that never went out is not counted
    await db().day1_runs.update_one({"token": tokens[1]}, {"$set": {"last_send.ok": False}})
    assert (await client.post("/api/hooks/day1-status/s3cret-hook-0000", json={"phone": "9833019001", "status": "read"})).json()["matched"] == 0
