"""v2.3 Engine Audit: the 4-3-2-1 audit on the hub. Have it / Missing / Not sure for each engine's accomplishments, a rupee
cost a month and hours a week for each gap, growth unlocked and hours back (× the item's rate, capped at half a month's
sales), the fix for each gap (open, opening on an event day, or in the plan), history with the change since last time, the
PDF and the share card, and Admin → Engine Audit. 0 AI tokens. Every business, person and number here is invented."""
from datetime import timedelta

from app import plans
from app.db import db, now
from tests.conftest import new_owner
from tests.v23_helpers import admin_client, owner_id, setup_v23, v23_member

ALL_KEYS = ["lead_website", "lead_magnet", "sales_levers", "sales_leads", "sales_invoicing", "conv_inquiries", "conv_pipeline",
            "ops_dashboard", "ops_payments", "ops_compliance", "ops_sops", "brand_kit", "brand_social", "brand_brochure",
            "data_keyperson", "data_money"]


def all_have(**changes) -> dict:
    out = {k: {"status": "have"} for k in ALL_KEYS}
    out.update(changes)
    return out


async def test_audit_is_off_until_the_v23_setup(client):
    o = await new_owner("9811000001", name="Owner Test")
    r = await o.get("/api/audit")
    assert r.status_code == 403
    await setup_v23()
    assert (await o.get("/api/audit")).status_code == 200       # a free account can take it too (Start section)


async def test_items_by_engine_with_their_fixes(client):
    m = await v23_member("9811000002")
    a = (await m.get("/api/audit")).json()
    assert [i["key"] for i in a["items"]] == ALL_KEYS
    assert [e["key"] for e in a["engines"]] == ["lead", "sales", "conversion", "operations", "brand", "data"]
    fixes = {i["key"]: i["fix"] for i in a["items"]}
    assert fixes["lead_website"] == {"state": "open", "label": "Website", "path": "/website"}
    assert fixes["sales_levers"]["label"] == "Revenue Multiplier" and fixes["sales_levers"]["state"] == "open"
    assert fixes["brand_brochure"]["state"] == "open" and fixes["brand_brochure"]["path"].startswith("/brand-kit")
    assert all(i["hint_cost"] and i["hint_hours"] and i["rate"] == 0.5 and i["hours_rate"] == 0.5 for i in a["items"])
    assert a["latest"] is None and a["history"] == [] and a["note"] == "Estimated from your own numbers." and a["cap_pct"] == 50

    free = await v23_member("9811000003", paid=False)
    fx = {i["key"]: i["fix"] for i in (await free.get("/api/audit")).json()["items"]}
    assert fx["lead_website"]["state"] == "plan" and fx["lead_website"]["path"] == "/plans"
    assert fx["lead_website"]["tier_name"] == "OwnerDesk Membership"


async def test_totals_growth_hours_and_top_gaps_with_no_tokens(client):
    m = await v23_member("9811000004")
    before = (await m.get("/api/me")).json()["runs"]["used"]
    r = await m.post("/api/audit", json={"answers": {"lead_website": {"status": "missing"}}})
    assert r.status_code == 400 and "still to go" in r.json()["detail"]
    answers = all_have(
        lead_magnet={"status": "missing", "monthly_cost": 40000, "hours_week": 4},
        ops_payments={"status": "missing", "monthly_cost": 90000, "hours_week": 6},
        brand_social={"status": "unsure", "monthly_cost": 15000, "hours_week": 3, "cost_unsure": True},
        ops_sops={"status": "missing", "monthly_cost": 25000, "hours_week": 10},
    )
    res = (await m.post("/api/audit", json={"answers": answers})).json()
    t = res["totals"]
    assert t["in_place"] == 12 and t["total"] == 16 and t["gaps"] == 4
    assert t["cost_month"] == 155000                       # the "not sure" ₹ counts as ₹0 and is flagged
    assert t["growth_month"] == 77500 and t["hours_week"] == 23 and t["hours_back_week"] == 11.5
    assert t["unsure_amounts"] == 1 and not t["capped"]
    rows = {x["item_key"]: x for x in res["answers"]}
    assert rows["brand_social"]["cost_unsure"] and rows["brand_social"]["monthly_cost"] == 0 and rows["brand_social"]["hours_week"] == 3
    assert rows["ops_payments"]["growth_unlocked"] == 45000 and rows["ops_payments"]["hours_back"] == 3
    assert res["top_gaps"] == ["ops_payments", "lead_magnet", "ops_sops"]
    ops = next(e for e in res["engines"] if e["key"] == "operations")
    assert ops["in_place"] == 2 and ops["total"] == 4 and ops["cost_month"] == 115000 and ops["hours_week"] == 16
    assert res["change"] is None and res["note"] == "Estimated from your own numbers."
    assert (await m.get("/api/me")).json()["runs"]["used"] == before          # 0 AI tokens

    got = (await m.get("/api/audit")).json()
    assert got["latest"]["id"] == res["id"] and len(got["history"]) == 1 and got["draft"] is None


async def test_growth_is_capped_at_half_a_months_sales(client):
    m = await v23_member("9811000005")
    answers = all_have(lead_website={"status": "missing", "monthly_cost": 300000, "hours_week": 2},
                       conv_inquiries={"status": "missing", "monthly_cost": 100000, "hours_week": 2})
    res = (await m.post("/api/audit", json={"answers": answers, "revenue_month": 100000})).json()
    t = res["totals"]
    assert t["capped"] and t["cap_amount"] == 50000 and t["growth_month"] == 50000 and t["cost_month"] == 400000
    rows = {x["item_key"]: x for x in res["answers"]}
    assert rows["lead_website"]["growth_unlocked"] == 37500 and rows["conv_inquiries"]["growth_unlocked"] == 12500
    assert res["revenue"] == {"month": 100000, "source": "the sales a month you typed"}

    # with no sales typed, the Knowledge Hub's monthly revenue target is used
    assert (await m.put("/api/knowledge/targets", json={"monthly_revenue": 600000})).status_code == 200
    got = (await m.get("/api/audit")).json()
    assert got["revenue"]["month"] == 600000 and "Knowledge Hub" in got["revenue"]["source"]
    res2 = (await m.post("/api/audit", json={"answers": answers})).json()
    assert not res2["totals"]["capped"] and res2["totals"]["growth_month"] == 200000
    assert res2["change"]["in_place"] == 0 and res2["change"]["cost_month"] == 0


async def test_draft_history_change_pdf_and_card(client):
    m = await v23_member("9811000006")
    draft = {"answers": {"lead_website": {"status": "missing", "monthly_cost": 5000}}, "revenue_month": 80000}
    assert (await m.put("/api/audit/draft", json=draft)).status_code == 200
    got = (await m.get("/api/audit")).json()
    assert got["draft"]["answers"]["lead_website"]["monthly_cost"] == 5000 and got["draft"]["revenue_month"] == 80000

    first = (await m.post("/api/audit", json={"answers": all_have(lead_website={"status": "missing", "monthly_cost": 20000,
                                                                                   "hours_week": 5},
                                                                     data_money={"status": "missing", "monthly_cost": 8000})})).json()
    second = (await m.post("/api/audit", json={"answers": all_have(data_money={"status": "missing", "monthly_cost": 8000})})).json()
    assert second["change"] == {"since": first["completed_at"], "in_place": 1, "cost_month": -20000, "hours_week": -5}
    got = (await m.get("/api/audit")).json()
    assert [h["id"] for h in got["history"]] == [second["id"], first["id"]] and got["draft"] is None

    r = await m.get(f"/api/audit/{first['id']}/pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content[:4] == b"%PDF"
    assert "Engine-Audit-Shree-Ganesh-Interiors.pdf" in r.headers["content-disposition"]
    r = await m.get(f"/api/audit/{first['id']}/card.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content[:8] == b"\x89PNG\r\n\x1a\n"

    other = await v23_member("9811000007")
    assert (await other.get(f"/api/audit/{first['id']}/pdf")).status_code == 404
    assert (await other.get(f"/api/audit/{first['id']}/card.png")).status_code == 404


async def test_an_item_drops_out_when_its_feature_is_switched_off(client):
    m = await v23_member("9811000008")
    adm = await admin_client()
    assert (await adm.patch("/api/admin/config/features/social_media", json={"on": False})).status_code == 200
    keys = [i["key"] for i in (await m.get("/api/audit")).json()["items"]]
    assert "brand_social" not in keys and len(keys) == 15
    res = (await m.post("/api/audit", json={"answers": {k: {"status": "have"} for k in keys}})).json()
    assert res["totals"]["total"] == 15


async def test_event_day_shows_when_a_fix_opens(client):
    await setup_v23()
    m = await v23_member("9811000009", paid=False)
    uid = await owner_id(m)
    await db().cohorts.insert_one({"_id": "AUDITDAY", "code": "AUDITDAY", "name": "Event", "created_at": now(),
                                   "event": {"open": True, "day": 1}})
    await db().users.update_one({"_id": uid}, {"$set": {"trial": {"plan": "lite", "until": now() + timedelta(days=3), "reason": "event",
                                                                 "cohort": "AUDITDAY"}}})
    await plans.load(force=True)
    fx = {i["key"]: i["fix"] for i in (await m.get("/api/audit")).json()["items"]}
    assert fx["lead_website"]["state"] == "open" and fx["sales_invoicing"]["state"] == "open"
    assert fx["conv_inquiries"] == {"state": "day", "label": "Inquiries", "path": "/inquiries", "opens_day": 2}
    assert fx["data_money"]["state"] == "day" and fx["data_money"]["opens_day"] == 3
    assert fx["ops_dashboard"]["state"] == "open"                  # the Dashboard is open from Day 1
    assert fx["brand_brochure"]["state"] == "plan"                 # brochures open once the member pays


async def test_admin_edits_items_rates_and_the_cap(client):
    m = await v23_member("9811000010")
    adm = await admin_client()
    view = (await adm.get("/api/admin/audit")).json()
    assert len(view["items"]) == 16 and view["cap_pct"] == 50 and view["stats"]["audits"] == 0
    r = await adm.put("/api/admin/audit/items/lead_magnet", json={"statement": "A free guide on my website collects leads every week",
                                                                   "rate": 0.25, "hours_rate": 1})
    assert r.status_code == 200
    item = next(i for i in r.json()["items"] if i["key"] == "lead_magnet")
    assert item["changed"] and item["rate"] == 0.25 and item["default"]["rate"] == 0.5
    assert (await adm.put("/api/admin/audit/items/ops_compliance", json={"on": False})).status_code == 200
    assert (await adm.put("/api/admin/audit/settings", json={"cap_pct": 20})).json()["cap_pct"] == 20
    assert (await adm.put("/api/admin/audit/items/nope", json={"on": False})).status_code == 404
    assert (await adm.put("/api/admin/audit/items/lead_magnet", json={"rate": 2})).status_code == 422

    a = (await m.get("/api/audit")).json()
    items = {i["key"]: i for i in a["items"]}
    assert "ops_compliance" not in items and items["lead_magnet"]["statement"].startswith("A free guide on my website")
    assert a["cap_pct"] == 20
    answers = {k: {"status": "have"} for k in items}
    answers["lead_magnet"] = {"status": "missing", "monthly_cost": 40000, "hours_week": 4}
    res = (await m.post("/api/audit", json={"answers": answers, "revenue_month": 1000000})).json()
    assert res["totals"]["growth_month"] == 10000 and res["totals"]["hours_back_week"] == 4 and not res["totals"]["capped"]

    r = (await adm.post("/api/admin/audit/items/lead_magnet/reset")).json()
    item = next(i for i in r["items"] if i["key"] == "lead_magnet")
    assert not item["changed"] and item["rate"] == 0.5 and r["stats"]["audits"] == 1 and r["stats"]["owners"] == 1
    assert (await m.get("/api/admin/audit")).status_code == 403
