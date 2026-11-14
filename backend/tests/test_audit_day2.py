"""The Day 2 Sales and Conversion audits join the Engine Audit below the Day 1 lines. An owner who finished the 9 Day 1 lines
keeps those answers exactly as they were: the finished audit is untouched, a half-done draft keeps its answers, and taking the
audit again starts from the Day 1 answers with only the 6 new lines left. Every business, person and number is invented."""
from datetime import timedelta

from app import engine_audit as ea
from app.db import db, now
from tests.test_v23_audit import ALL_KEYS, DAY1_KEYS
from tests.v23_helpers import owner_id, v23_member

SALES = ["sales_followup", "sales_quote_chase", "sales_pay_remind"]
CONVERSION = ["conv_fast_reply", "conv_quote_sameday", "conv_one_place"]


def test_the_six_new_lines_are_the_slides_words_below_day_1():
    items = ea.active_items({"items": ea.defaults(), "cap_pct": 50, "overrides": {}})
    assert [i["key"] for i in items] == ALL_KEYS == DAY1_KEYS + SALES + CONVERSION
    words = {i["key"]: i["statement"] for i in items}
    assert words["sales_followup"] == "Every enquiry followed up until there is a yes or a no"
    assert words["sales_quote_chase"] == "Every quotation chased, without you having to remember it"
    assert words["sales_pay_remind"] == "Payment reminders that go out on their own"
    assert words["conv_fast_reply"] == "Every enquiry answered within minutes, day or night"
    assert words["conv_quote_sameday"] == "Quotations that go out the same day"
    assert words["conv_one_place"] == "Every enquiry in one place, not spread across phones and notebooks"
    assert {i["engine"] for i in items if i["key"] in SALES} == {"sales"}
    assert {i["engine"] for i in items if i["key"] in CONVERSION} == {"conversion"}


async def test_yesterdays_answers_stay_as_they_were(client):
    m = await v23_member("9811200001")
    uid = await owner_id(m)
    day1 = {k: {"status": "missing" if k in ("lead_site", "ops_team") else "have"} for k in DAY1_KEYS}
    # yesterday's finished audit of the 9 Day 1 lines, as it was saved then
    rows = [{"item_key": k, "engine": k.split("_")[0].replace("ops", "operations"), "feature": "website", "statement": k,
             "status": a["status"], "monthly_cost": 0, "hours_week": 3 if a["status"] == "missing" else 0,
             "growth_unlocked": 0, "hours_back": 0} for k, a in day1.items()]
    old = {"_id": "day1-audit", "owner_id": uid, "by": uid, "completed_at": now() - timedelta(days=1), "answers": rows,
           "engines": [], "top_gaps": [], "revenue": {}, "change": None,
           "totals": {"in_place": 7, "total": 9, "answered": 9, "gaps": 2, "cost_month": 0, "growth_month": 0,
                      "hours_week": 6, "hours_back_week": 3}}
    await db().engine_audits.insert_one(dict(old))

    got = (await m.get("/api/audit")).json()
    assert [i["key"] for i in got["items"]] == ALL_KEYS                      # 15 lines, the 6 new ones last
    assert got["latest"]["id"] == "day1-audit" and got["latest"]["totals"]["total"] == 9
    assert {r["item_key"]: r["status"] for r in got["latest"]["answers"]} == {k: a["status"] for k, a in day1.items()}

    # the audit again: the page starts from yesterday's answers, so only the 6 new lines need a tap
    r = await m.post("/api/audit", json={"answers": day1})
    assert r.status_code == 400 and "6 still to go" in r.json()["detail"]
    new = {k: {"status": "have"} for k in SALES + CONVERSION}
    new["sales_pay_remind"] = {"status": "missing"}
    res = (await m.post("/api/audit", json={"answers": {**day1, **new}})).json()
    status = {r["item_key"]: r["status"] for r in res["answers"]}
    assert all(status[k] == a["status"] for k, a in day1.items())           # Day 1 answers carried over unchanged
    assert status["sales_pay_remind"] == "missing" and res["totals"]["total"] == 15 and res["totals"]["in_place"] == 12
    assert [e["key"] for e in res["engines"]] == ["lead", "operations", "brand", "sales", "conversion"]

    # yesterday's audit is still there, untouched, and in the history
    kept = await db().engine_audits.find_one({"_id": "day1-audit"})
    assert kept["answers"] == old["answers"] and kept["totals"] == old["totals"]
    hist = (await m.get("/api/audit")).json()["history"]
    assert [h["total"] for h in hist] == [15, 9]


async def test_a_half_done_draft_keeps_its_answers(client):
    m = await v23_member("9811200002")
    uid = await owner_id(m)
    await db().engine_audit_drafts.insert_one({"_id": uid, "answers": {"lead_site": {"status": "have"},
                                                                       "ops_team": {"status": "missing"}}, "updated_at": now()})
    got = (await m.get("/api/audit")).json()
    assert got["draft"]["answers"] == {"lead_site": {"status": "have"}, "ops_team": {"status": "missing"}}
