"""Engine Audit, hours only: one list of the 9 Day 1 lines, Have it or Missing and nothing to type. A gap counts the hours a
week a typical small business loses to it (the admin can change that figure); no rupee figures appear unless sent."""
from tests.test_v23_audit import ALL_KEYS, all_have
from tests.v23_helpers import admin_client, v23_member


async def test_missing_counts_the_typical_hours_with_nothing_typed(client):
    m = await v23_member("9811100001")
    items = {i["key"]: i for i in (await m.get("/api/audit")).json()["items"]}
    assert [k for k in items] == ALL_KEYS and all(i["est_hours"] > 0 for i in items.values())
    res = (await m.post("/api/audit", json={"answers": all_have(ops_team={"status": "missing"},
                                                                 ops_written={"status": "missing"})})).json()
    t = res["totals"]
    assert t["hours_week"] == items["ops_team"]["est_hours"] + items["ops_written"]["est_hours"] == 9
    assert t["hours_back_week"] == 4.5 and t["cost_month"] == 0 and t["growth_month"] == 0 and t["gaps"] == 2
    assert res["top_gaps"] == ["ops_team", "ops_written"]                    # most hours first
    for path in ("pdf", "card.png"):                                                 # an hours-only result still renders
        assert (await m.get(f"/api/audit/{res['id']}/{path}")).status_code == 200


async def test_the_admin_changes_the_typical_hours(client):
    m = await v23_member("9811100002")
    admin = await admin_client()
    r = await admin.put("/api/admin/audit/items/brand_weekly", json={"est_hours": 6})
    got = next(i for i in r.json()["items"] if i["key"] == "brand_weekly")
    assert got["est_hours"] == 6 and got["default"]["est_hours"] == 3 and got["changed"]
    res = (await m.post("/api/audit", json={"answers": all_have(brand_weekly={"status": "missing"})})).json()
    assert res["totals"]["hours_week"] == 6
    assert (await admin.put("/api/admin/audit/items/brand_weekly", json={"est_hours": 101})).status_code == 422
