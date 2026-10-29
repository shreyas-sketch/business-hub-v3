"""A grants_only feature (the tender finder, while it's tried out with one account): open only to the owners the admin has
granted it to, whatever their plan, and out of everyone else's menu. No database: plans.has and the menus read the user."""
from datetime import timedelta

from app import plans
from app.db import now


def keys(menu: list[dict]) -> set[str]:
    return {i["key"] for g in menu for i in g["items"]}


def test_the_tender_finder_opens_only_where_it_is_granted():
    plans.reset_cache()
    assert plans.META["tender_finder"]["grants_only"]
    top_plan = {"_id": "o1", "plan": "office"}
    granted = {"_id": "o2", "plan": "free", "grants": {"tender_finder": now() + timedelta(days=365)}}
    lapsed = {"_id": "o3", "plan": "office", "grants": {"tender_finder": now() - timedelta(days=1)}}
    assert not plans.has(top_plan, "tender_finder") and not plans.has(lapsed, "tender_finder")
    assert plans.has(granted, "tender_finder")
    assert plans.has(top_plan, "expenses") and not plans.has(granted, "expenses")   # every other feature still follows the plan
    for menu in (plans.menu_for, plans.engine_menu):
        assert "tender_finder" in keys(menu(granted, "manager"))
        assert "tender_finder" not in keys(menu(top_plan, "owner"))                  # not even as a locked teaser
        assert "tender_finder" not in keys(menu(granted, "staff"))                   # still owner and managers only


def test_the_admin_can_open_a_grants_only_feature_by_plan_again():
    plans.reset_cache()
    try:
        plans.apply_overrides({"tender_finder": {"grants_only": False}}, None)
        assert plans.has({"_id": "o1", "plan": "lite"}, "tender_finder")
        assert not plans.has({"_id": "o2", "plan": "free"}, "tender_finder")
    finally:
        plans.reset_cache()
