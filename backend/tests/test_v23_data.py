"""v2.3 Data engine: key-person knowledge capture (owner questions → the employee's questionnaire on a no-login link →
typed answers, cleaned → the MD skill file, the role card, SOPs) and Expenses & bank statement (the hub's own analysis,
AI category suggestions that see only payee names, AI advice that sees only totals and never invents a number).
Every business, person, payee and number here is invented."""
from datetime import timedelta

import httpx
import pytest

from app import expense_ai as ea, keyperson as kp
from app.ai import expense_tasks
from app.ai.provider import Usage
from app.db import IST, db, now
from app.main import app
from tests.test_bankstatement import simple_csv
from tests.v23_helpers import owner_id, v23_member

STEEL = {"name": "Vrundavan Steel Works", "city": "Rajkot", "industry": "Steel fabrication and engineering works",
         "offers": [{"name": "MS fabrication", "price": "", "unit": ""}], "ideal_customer": "Factories and builders in Saurashtra",
         "why_us": "On-time delivery", "proof": "", "whatsapp": "98250 00000", "email": "hello@vsw.example", "language": "English"}


def anon() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://hub.example.in")


async def runs_used(c) -> int:
    return (await c.get("/api/me")).json()["runs"]["used"]


# ───────────────────────── key-person knowledge ─────────────────────────
async def test_key_person_from_owner_questions_to_skill_file_role_card_and_sops(client):
    o = await v23_member("9871000001", profile=STEEL)
    assert (await o.get("/api/me")).json()["features"]["key_person"]
    r = await o.post("/api/key-person", json={"name": "Ramesh Parmar", "role": "Dispatch supervisor", "department": "Operations"})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    assert r.json()["status"] == "new" and r.json()["costs"] == {"owner_questions": 1, "questionnaire": 1, "skill": 1}

    # the questionnaire waits for the owner's answers
    assert (await o.post(f"/api/key-person/{pid}/questionnaire")).status_code == 400
    used = await runs_used(o)
    p = (await o.post(f"/api/key-person/{pid}/owner-questions")).json()
    assert 2 <= len(p["owner_questions"]) <= 5 and "Ramesh" in p["owner_questions"][0]
    assert await runs_used(o) == used + 1
    p = (await o.put(f"/api/key-person/{pid}/owner-answers", json={"answers": ["Loading plans and transporter booking", "Trucks leave late"]})).json()
    assert p["status"] == "owner_answered" and p["owner_answers"][0]["a"] == "Loading plans and transporter booking"

    p = (await o.post(f"/api/key-person/{pid}/questionnaire")).json()
    assert 12 <= len(p["questions"]) <= 20 and p["status"] == "sent"
    assert p["link"].startswith("https://hub.example.in/k/") and not p["link_expired"]
    assert all(not kp._PERSONAL_Q.search(q["q"]) for q in p["questions"])
    secret_q = [q for q in p["questions"] if kp._SECRET_Q.search(q["q"])]
    assert secret_q and all("kept" in q["q"] and "Don't" in q["q"] for q in secret_q)   # where logins are kept, never the password
    token = p["link"].rsplit("/", 1)[1]
    doc = await db().key_people.find_one({"_id": pid})
    assert doc["link_expires_at"] - now() > timedelta(days=13)

    # the employee: no login
    e = anon()
    f = (await e.get(f"/api/kp/{token}")).json()
    assert f["first_name"] == "Ramesh" and f["business"] == "Vrundavan Steel Works" and len(f["questions"]) == len(p["questions"])
    assert "password" in f["note"] and not f["submitted"]
    keys = [q["key"] for q in f["questions"]]
    answers = {keys[0]: "Check the loading plan\nCall the transporter\nPrint the challan",
               keys[2]: "Step 1 weigh the bundles\nStep 2 tag each bundle\nStep 3 load heavy first",
               keys[5]: "If the truck is late then call the second transporter",
               keys[10]: "Tally login is with the owner. Password: Sunrise@123. Transporter Bharat Roadlines 9825012345"}
    r = await e.put(f"/api/kp/{token}", json={"answers": answers})
    assert r.status_code == 200 and not r.json()["submitted"]
    saved = {a["key"]: a["a"] for a in (await db().key_people.find_one({"_id": pid}))["answers"]}
    assert "Sunrise@123" not in saved[keys[10]] and "[removed]" in saved[keys[10]] and "9825012345" not in saved[keys[10]]
    assert (await e.get(f"/api/kp/{token}")).json()["answers"][keys[0]].startswith("Check the loading plan")
    pdf = await e.get(f"/api/kp/{token}/questions.pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    r = await e.put(f"/api/kp/{token}", json={"answers": answers, "submit": True})
    assert r.json()["submitted"]
    assert (await e.put(f"/api/kp/{token}", json={"answers": answers})).status_code == 409   # sent: closed
    notes = (await o.get("/api/me")).json()["notices"]
    assert any("Ramesh" in n["text"] for n in notes)

    p = (await o.get(f"/api/key-person/{pid}")).json()
    assert p["status"] == "answered" and p["submitted_by"] == "employee" and p["answered"] == 4

    used = await runs_used(o)
    p = (await o.post(f"/api/key-person/{pid}/skill")).json()
    assert await runs_used(o) == used + 1 and p["status"] == "skill"
    md = p["skill"]["md"]
    assert md.startswith("---\nname: dispatch-supervisor-skill\ndescription: ")
    for h in ("# Dispatch supervisor — skills", "## Responsibilities", "## Daily / weekly tasks (step by step)", "## Decision rules (if X then Y)",
              "## Contacts and systems", "## Common problems and fixes", "## What to escalate to the owner"):
        assert h in md
    assert "Sunrise@123" not in md and "9825012345" not in md
    assert "If the truck is late, then call the second transporter" in md
    card = p["skill"]["card"]
    assert card["agent_name"] and card["takes_over"] and card["hands_back"]
    assert p["sops"] and not p["can_add_to_agent"]
    sops = (await o.get("/api/kits/sop")).json()
    assert {s["id"] for s in p["sops"]} <= {s["id"] for s in (sops if isinstance(sops, list) else sops.get("items", []))}
    kit = await db().kits.find_one({"_id": p["sops"][0]["id"]})
    assert kit["kind"] == "sop" and kit["key_person_id"] == pid and len(kit["content"]["steps"]) >= 2
    # building again keeps the SOPs it made (the owner may have edited them)
    p2 = (await o.post(f"/api/key-person/{pid}/skill")).json()
    assert {s["id"] for s in p2["sops"]} == {s["id"] for s in p["sops"]}

    r = await o.get(f"/api/key-person/{pid}/skill.md")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown") and "dispatch-supervisor-skill.md" in r.headers["content-disposition"]
    r = await o.get(f"/api/key-person/{pid}/card.png")
    assert r.status_code == 200 and r.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert (await o.get(f"/api/key-person/{pid}/questions.pdf")).content[:4] == b"%PDF"
    # AI staff come with higher plans: here the owner downloads the file instead
    assert (await o.post(f"/api/key-person/{pid}/add-to-agent")).status_code == 403


async def test_key_person_link_expires_reopens_and_belongs_to_one_business(client):
    o = await v23_member("9871000002", profile=STEEL)
    pid = (await o.post("/api/key-person", json={"name": "Sunita Rao", "role": "Accounts executive"})).json()["id"]
    await o.post(f"/api/key-person/{pid}/owner-questions")
    await o.put(f"/api/key-person/{pid}/owner-answers", json={"answers": ["GST returns and vendor payments"]})
    p = (await o.post(f"/api/key-person/{pid}/questionnaire")).json()
    token = p["link"].rsplit("/", 1)[1]
    e = anon()
    assert (await e.get("/api/kp/not-a-real-token-at-all-xxxxxxxx")).status_code == 404
    # the owner can type the answers instead
    keys = [q["key"] for q in p["questions"]]
    r = await o.put(f"/api/key-person/{pid}/answers", json={"answers": {keys[0]: "Bank entries"}, "submit": True})
    assert r.status_code == 400   # at least 3 answers
    r = await o.put(f"/api/key-person/{pid}/answers", json={"answers": {k: "Reconcile the bank every Friday" for k in keys[:3]}, "submit": True})
    assert r.json()["submitted_by"] == "owner"
    assert (await e.put(f"/api/kp/{token}", json={"answers": {}})).status_code == 409
    p = (await o.post(f"/api/key-person/{pid}/reopen")).json()
    assert p["status"] == "sent"
    assert (await e.put(f"/api/kp/{token}", json={"answers": {keys[0]: "Updated by Sunita"}})).status_code == 200
    # a new link: the old one stops working
    p = (await o.post(f"/api/key-person/{pid}/link")).json()
    assert (await e.get(f"/api/kp/{token}")).status_code == 404
    token2 = p["link"].rsplit("/", 1)[1]
    assert (await e.get(f"/api/kp/{token2}")).status_code == 200
    # expired
    await db().key_people.update_one({"_id": pid}, {"$set": {"link_expires_at": now() - timedelta(minutes=1)}})
    assert (await e.get(f"/api/kp/{token2}")).status_code == 404
    # personal questions can't be added by hand either
    r = await o.put(f"/api/key-person/{pid}/questionnaire", json={"questions": [
        {"q": "What do you do first every morning?"}, {"q": "What is your monthly salary?"}, {"q": "Which suppliers do you call?"}]})
    assert r.status_code == 400
    # another business can't see this person
    other = await v23_member("9871000003", profile=STEEL)
    assert (await other.get(f"/api/key-person/{pid}")).status_code == 404


async def test_key_person_is_part_of_the_paid_plan_only_the_owner_and_safe_questions():
    assert kp.safe_question("What is your Tally password?") is None
    assert kp.safe_question("Which bank account number do you use?") is None
    q = kp.safe_question("Where are the logins for the GST portal kept, and who else has them?")
    assert q and q.endswith("(Don't write the password itself.)")
    assert kp.scrub("pin: 4455 and OTP is 991122") == "pin: [removed] and OTP is [removed]"
    with pytest.raises(ValueError):
        kp.shape_questions({"questions": [{"section": "Decisions", "q": "What is your salary?"}] * 12})


async def test_key_person_locked_on_the_free_plan(client):
    free = await v23_member("9871000004", paid=False, profile=STEEL)
    r = await free.get("/api/key-person")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "locked"


# ───────────────────────── expenses & bank statement ─────────────────────────
def ym(months_back: int, day: int) -> str:
    t = now().astimezone(IST).date().replace(day=1)
    for _ in range(months_back):
        t = (t - timedelta(days=1)).replace(day=1)
    return t.replace(day=day).isoformat()


def dmy(months_back: int, day: int) -> str:
    y, m, d = ym(months_back, day).split("-")
    return f"{d}/{m}/{y}"


async def add(c, months_back, day, amount, category, vendor=""):
    r = await c.post("/api/expenses", json={"date": ym(months_back, day), "amount": amount, "category": category, "vendor": vendor, "paid_by": "bank"})
    assert r.status_code == 200, r.text


async def books(c):
    for m in (3, 2, 1):
        await add(c, m, 5, 45000, "rent", "Shree Estates")
        await add(c, m, 7, 120000, "salaries", "Staff payroll")
        await add(c, m, 12, 60000 if m > 1 else 90000, "purchases", "Sharma Steel Traders")


async def test_analysis_by_month_category_payees_and_money_in_from_the_bank_statement(client):
    o = await v23_member("9871000010", profile=STEEL)
    await books(o)
    an = (await o.get("/api/expenses-ai/analysis?months=6")).json()
    assert len(an["months"]) == 6 and an["months"][-1]["partial"]
    assert an["total"] == 3 * 45000 + 3 * 120000 + 60000 * 2 + 90000
    cats = {c["key"]: c for c in an["categories"]}
    assert cats["salaries"]["salary"] and cats["salaries"]["amount"] == 360000
    assert cats["purchases"]["last_month"] == 90000 and cats["purchases"]["usual"] == 60000 and cats["purchases"]["last_vs_avg_pct"] == 50
    assert an["top_payees"][0]["payee"] == "Staff payroll" and an["top_payees"][0]["count"] == 3
    assert sum(c["share"] for c in an["categories"]) in (99, 100, 101)
    assert an["money"]["source"] is None
    # with a bank statement on the bank statement page: money in against money out
    csv = simple_csv([(dmy(1, 2), "NEFT CR-ZZBK0000001-MEHTA FABRICATORS-INV 4401", "", "180000.00", "680000.00"),
                      (dmy(1, 5), "UPI/DR/401122334455/SHREE ESTATES/ZZBK/zz.estates01@okzz/Rent", "45000.00", "", "635000.00")])
    assert (await o.post("/api/bank-statement", files={"file": ("s.csv", csv, "text/csv")})).status_code == 200
    an = (await o.get("/api/expenses-ai/analysis?months=3")).json()
    assert an["money"]["source"] == "bank" and an["money"]["months"][0]["in"] == 180000


async def test_ai_category_suggestions_see_only_payee_names_and_the_owner_accepts_them(client, monkeypatch):
    seen = []

    async def fake(system, prompt, mock, max_tokens=1200):
        seen.append(prompt)
        return mock(), Usage()
    monkeypatch.setattr(expense_tasks, "complete", fake)
    o = await v23_member("9871000011", profile=STEEL)
    csv = simple_csv([(dmy(1, 3), "UPI/DR/409988776655/KAVERI PACKWELL/ZZBK/kaveri.pack@okzz/Cartons", "23750.00", "", "476250.00"),
                      (dmy(1, 9), "UPI/DR/409988776611/KAVERI PACKWELL/ZZBK/kaveri.pack@okzz/Cartons", "18400.00", "", "457850.00"),
                      (dmy(1, 5), "UPI/DR/401122334455/SHREE ESTATES/ZZBK/zz.estates01@okzz/Rent", "45000.00", "", "412850.00")])
    s = (await o.post("/api/bank-statement", files={"file": ("s.csv", csv, "text/csv")})).json()
    other = [t for t in s["transactions"] if t["category"] == "other"]
    assert len(other) == 2
    v = (await o.get(f"/api/expenses-ai/statement/{s['id']}/suggestions")).json()
    assert v["unsorted"] == 2 and v["unsorted_payees"] == 1 and not v["suggestions"] and not v["asked"]
    used = await runs_used(o)
    v = (await o.post(f"/api/expenses-ai/statement/{s['id']}/suggest")).json()
    assert await runs_used(o) == used + 1
    assert {x["category"] for x in v["suggestions"].values()} == {"packing"} and set(v["suggestions"]) == {t["ref"] for t in other}
    prompt = seen[-1]
    assert "Kaveri Packwell".lower() in prompt.lower()
    for leak in ("23750", "23,750", "18400", "409988776655", "kaveri.pack@", "Shree Estates", "45000"):
        assert leak.lower() not in prompt.lower()
    # the owner accepts: remembered for this payee, and nothing is left to suggest
    ref = other[0]["ref"]
    r = await o.patch(f"/api/bank-statement/{s['id']}/transactions/{ref}", json={"category": "packing", "remember": True})
    assert all(t["category"] == "packing" for t in r.json()["transactions"] if "KAVERI" in t["narration"].upper())
    v = (await o.get(f"/api/expenses-ai/statement/{s['id']}/suggestions")).json()
    assert v["unsorted"] == 0 and not v["suggestions"]
    assert (await o.post(f"/api/expenses-ai/statement/{s['id']}/suggest")).status_code == 400


async def test_ai_advice_sees_totals_only_leaves_salaries_out_and_never_keeps_an_invented_number(client, monkeypatch):
    seen = []

    async def fake(system, prompt, mock, max_tokens=1200):
        seen.append(prompt)
        return mock(), Usage()
    monkeypatch.setattr(expense_tasks, "complete", fake)
    o = await v23_member("9871000012", profile=STEEL)
    assert (await o.post("/api/expenses-ai/advice", json={})).status_code == 400   # nothing to look at yet
    await books(o)
    used = await runs_used(o)
    r = await o.post("/api/expenses-ai/advice", json={"months": 6})
    assert r.status_code == 200, r.text
    adv = r.json()["advice"]
    assert await runs_used(o) == used + 1 and not adv["include_salaries"]
    assert adv["findings"] and adv["recommendations"]
    prompt = seen[-1]
    assert "Salaries" not in prompt.split("FIGURES")[1].split("Salaries are left out")[0]
    assert "₹45,000" in prompt and "Staff payroll" not in prompt and "₹1,20,000" not in prompt
    assert (await o.get("/api/expenses-ai/advice")).json()["advice"]["headline"] == adv["headline"]
    # with salaries ticked, they're in
    await o.post("/api/expenses-ai/advice", json={"months": 6, "include_salaries": True})
    assert "Salaries & wages" in seen[-1]

    # an answer with numbers the hub never gave: those sentences go; nothing usable left → the token comes back
    async def inventor(system, prompt, mock, max_tokens=1200):
        return {"headline": "Save ₹99,999 this year", "findings": [{"title": "Cut costs", "detail": "You can save ₹77,777 a month."}],
                "recommendations": [{"title": "Renegotiate rent to ₹31,500", "detail": "Ask for 30% off.", "why": ""}]}, Usage()
    monkeypatch.setattr(expense_tasks, "complete", inventor)
    used = await runs_used(o)
    assert (await o.post("/api/expenses-ai/advice", json={"months": 6})).status_code == 502
    assert await runs_used(o) == used


async def test_advice_and_suggestions_are_for_the_owner_and_the_number_check():
    allowed = {"rs": {45000, 135000, 90000}, "pct": {50, 38}, "other": {6, 2026}}
    assert ea.check_numbers("Rent was ₹45,000 a month. You could save ₹12,345 by moving.", allowed) == "Rent was ₹45,000 a month."
    assert ea.check_numbers("Purchases rose 50% to ₹90,000 in Oct 2026.", allowed) == "Purchases rose 50% to ₹90,000 in Oct 2026."
    assert ea.check_numbers("That's ₹1.35 lakh over 6 months.", allowed) == "That's ₹1.35 lakh over 6 months."
    assert ea.check_numbers("Cut 20% and pay 40000 less.", allowed) == ""
    assert ea.fmt(1234567) == "₹12,34,567"
