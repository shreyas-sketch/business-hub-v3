"""Engine Audit, hours only: one list of the 14 processes, Have it or Missing and nothing to type. A gap counts the hours a
week a typical small business loses to it (the admin can change that figure); no rupee figures appear unless sent."""
from tests.test_v23_audit import ALL_KEYS, all_have
from tests.v23_helpers import admin_client, v23_member


async def test_missing_counts_the_typical_hours_with_nothing_typed(client):
    m = await v23_member("9811100001")
    items = {i["key"]: i for i in (await m.get("/api/audit")).json()["items"]}
    assert [k for k in items] == ALL_KEYS and all(i["est_hours"] > 0 for i in items.values())
    res = (await m.post("/api/audit", json={"answers": all_have(sales_invoicing={"status": "missing"},
                                                                 ops_payments={"status": "missing"})})).json()
    t = res["totals"]
    assert t["hours_week"] == items["sales_invoicing"]["est_hours"] + items["ops_payments"]["est_hours"] == 9
    assert t["hours_back_week"] == 4.5 and t["cost_month"] == 0 and t["growth_month"] == 0 and t["gaps"] == 2
    assert res["top_gaps"] == ["sales_invoicing", "ops_payments"]                    # most hours first
    for path in ("pdf", "card.png"):                                                 # an hours-only result still renders
        assert (await m.get(f"/api/audit/{res['id']}/{path}")).status_code == 200


async def test_the_admin_changes_the_typical_hours(client):
    m = await v23_member("9811100002")
    admin = await admin_client()
    r = await admin.put("/api/admin/audit/items/brand_social", json={"est_hours": 6})
    got = next(i for i in r.json()["items"] if i["key"] == "brand_social")
    assert got["est_hours"] == 6 and got["default"]["est_hours"] == 4 and got["changed"]
    res = (await m.post("/api/audit", json={"answers": all_have(brand_social={"status": "missing"})})).json()
    assert res["totals"]["hours_week"] == 6
    assert (await admin.put("/api/admin/audit/items/brand_social", json={"est_hours": 101})).status_code == 422
