"""The Employz.ai import (read-only): the client's limits and errors, every reader's pages, the full import into the CRM and
the Pipeline page, a second run writing nothing, resume after a crash, changes and deletes, a hub contact linked not copied,
a deal the hub pushed recognised, one run at a time, disconnect and purge, tenant isolation, and nothing personal in the log.
Employz.ai is played by a fake built from the documented response shapes. Every name and number here is invented."""
import asyncio
import json
import logging
import re
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app import connections, plans, employz_api as api, employz_import as imp
from app.db import db, now
from tests.test_v21_p0 import owner_on
from tests.v23_helpers import setup_v23

REAL_SLEEP = asyncio.sleep      # the fake fixture makes asyncio.sleep instant
LOC = "LocTest00000000000001"
TOKEN = "pit-test-0000-secret-token"


class FakeEmployz:
    """Pipelines, contacts and opportunities of one sub-account, served the way the LeadConnector API v2 does."""

    def __init__(self, contacts=150, deals=120):
        self.pipes = [{"id": "pipeA", "name": "Football Field", "stages": [
                           {"id": "sA1", "name": "Identification", "position": 0}, {"id": "sA2", "name": "Logic / reason", "position": 1},
                           {"id": "sA3", "name": "Pain / consequence", "position": 2}, {"id": "sA4", "name": "Solution / vision", "position": 3},
                           {"id": "sA5", "name": "Ask / close", "position": 4}]},
                      {"id": "pipeB", "name": "Service renewals", "stages": [{"id": "sB1", "name": "Due", "position": 0},
                                                                             {"id": "sB2", "name": "Renewed", "position": 1}]}]
        self.contacts = [{"id": f"c{i:04d}", "firstName": f"Test{i}", "lastName": "Person", "phone": f"+9198{i:08d}",
                          "email": f"test{i}@example.in", "companyName": "Invented Traders" if i % 3 == 0 else "",
                          "tags": ["imported"], "dateAdded": "2026-09-01T10:00:00.000Z", "dateUpdated": "2026-10-01T10:00:00.000Z",
                          "assignedTo": "u1", "customFields": [{"id": "cf1", "value": "Pune"}, {"id": "cfX", "value": "kept"}]}
                         for i in range(contacts)]
        self.deals = [{"id": f"o{i:04d}", "name": f"Deal {i}", "monetaryValue": 1000 * (i + 1), "pipelineId": "pipeA" if i % 4 else "pipeB",
                       "pipelineStageId": ("sA1", "sA2", "sA3", "sA4", "sA5")[i % 5] if i % 4 else "sB1",
                       "status": ("open", "open", "won", "lost", "abandoned")[i % 5], "source": "Website", "assignedTo": "u1",
                       "contactId": f"c{i:04d}", "contact": {"id": f"c{i:04d}", "name": f"Test{i} Person", "phone": f"+9198{i:08d}"},
                       "createdAt": "2026-10-01T10:00:00.000Z", "updatedAt": "2026-10-20T10:00:00.000Z",
                       "lastStatusChangeAt": "2026-10-20T10:00:00.000Z"} for i in range(deals)]
        self.calls: list[tuple[str, str]] = []
        self.fail_after: dict = {}      # path → raise on the nth call to it (a crash part-way)
        self.status: int | None = None   # force every answer to this status

    def page(self, items, q):
        limit = int(q.get("limit", ["100"])[0])
        after = q.get("startAfterId", [None])[0]
        start = next((i + 1 for i, x in enumerate(items) if x["id"] == after), 0) if after else 0
        chunk = items[start:start + limit]
        more = start + limit < len(items)
        meta = {"total": len(items), "startAfter": 1700000000000 + start + limit if more else None,
                "startAfterId": chunk[-1]["id"] if more and chunk else None, "nextPageUrl": None}
        return chunk, meta

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        assert request.method == "GET", f"the import must never write: {request.method} {request.url.path}"
        assert request.headers["Authorization"] == f"Bearer {TOKEN}" and request.headers["Version"] == api.VERSION
        if self.status:
            return httpx.Response(self.status, json={"message": "nope"})
        n = sum(1 for _, p in self.calls if p == request.url.path)
        if self.fail_after.get(request.url.path) == n:
            return httpx.Response(500, json={"message": "boom"})
        q = parse_qs(urlparse(str(request.url)).query)
        path = request.url.path
        if path == f"/locations/{LOC}":
            return httpx.Response(200, json={"location": {"id": LOC, "name": "Invented Test Business", "timezone": "Asia/Kolkata"}})
        if path == "/users/":
            return httpx.Response(200, json={"users": [{"id": "u1", "name": "Asha Owner"}]})
        if path == f"/locations/{LOC}/customFields":
            return httpx.Response(200, json={"customFields": [{"id": "cf1", "name": "City", "fieldKey": "contact.city", "dataType": "TEXT"}]})
        if path == "/opportunities/pipelines":
            return httpx.Response(200, json={"pipelines": self.pipes})
        if path == "/contacts/":
            chunk, meta = self.page(self.contacts, q)
            return httpx.Response(200, json={"contacts": chunk, "meta": meta})
        if path == "/opportunities/search":
            chunk, meta = self.page(self.deals, q)
            return httpx.Response(200, json={"opportunities": chunk, "meta": meta})
        return httpx.Response(404, json={"message": "not found"})


@pytest.fixture
def fake(monkeypatch):
    f = FakeEmployz()
    monkeypatch.setattr(api, "_transport", lambda: httpx.MockTransport(f.handler))
    async def quick(_s):   # retries and the rate limit don't wait in tests
        return None
    monkeypatch.setattr(api.asyncio, "sleep", quick)
    api._windows.clear()
    return f


async def connected(phone: str):
    if not plans.engines_on():           # the CRM and the Pipeline page are v2.3 pages
        await setup_v23()
    c, uid, _ = await owner_on(phone, "running")
    assert (await c.put("/api/connections/employz", json={"values": {"provider": "employz", "location_id": LOC, "token": TOKEN,
                                                                       "app_link": "https://app.employz.ai/v2/location/x"}})).status_code == 200
    return c, uid


# ───────────────────────── the client ─────────────────────────
def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


def _client(monkeypatch, responses):
    calls = []

    def handler(req):
        calls.append(req)
        r = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(r, Exception):
            raise r
        return r
    monkeypatch.setattr(api, "_transport", lambda: httpx.MockTransport(handler))
    waits = []

    async def quick(s):
        waits.append(s)
    monkeypatch.setattr(api.asyncio, "sleep", quick)
    api._windows.clear()
    return calls, waits


CONN = {"location_id": LOC, "token": TOKEN}


def test_429_with_retry_after_then_success(monkeypatch):
    calls, waits = _client(monkeypatch, [httpx.Response(429, headers={"retry-after": "7"}), httpx.Response(200, json={"users": []})])
    assert asyncio.run(api.read_users(CONN)).records == [] and len(calls) == 2 and 7.0 in waits


def test_three_503s_then_success_and_a_fourth_gives_up(monkeypatch):
    calls, _ = _client(monkeypatch, [httpx.Response(503)] * 3 + [httpx.Response(200, json={"users": []})])
    asyncio.run(api.read_users(CONN))
    assert len(calls) == 4
    _client(monkeypatch, [httpx.Response(503)] * 5)
    with pytest.raises(api.Transient):
        asyncio.run(api.read_users(CONN))


def test_errors_401_404_envelope_oversized_timeout_and_half_a_cursor(monkeypatch):
    calls, _ = _client(monkeypatch, [httpx.Response(401, json={"message": "Invalid JWT"})])
    with pytest.raises(api.AuthError):
        asyncio.run(api.read_users(CONN))
    assert len(calls) == 1                                           # never retried
    _client(monkeypatch, [httpx.Response(200, json={"statusCode": 422, "message": "bad"})])
    with pytest.raises(api.Malformed):
        asyncio.run(api.read_users(CONN))
    _client(monkeypatch, [httpx.Response(200, content=b'{"users": [' + b'{"id":"x"},' * 200_000 + b'{"id":"y"}]}')])
    with pytest.raises(api.Malformed):
        asyncio.run(api.read_users(CONN))
    _client(monkeypatch, [httpx.ReadTimeout("slow")] * 5)
    with pytest.raises(api.Transient):
        asyncio.run(api.read_users(CONN))
    half = {"contacts": [{"id": f"c{i}"} for i in range(100)], "meta": {"startAfterId": "c99"}}
    _client(monkeypatch, [httpx.Response(200, json=half)])
    with pytest.raises(api.Malformed):
        asyncio.run(api.read_contacts(CONN))
    _client(monkeypatch, [httpx.Response(200, json={"contacts": [{"name": "no id"}]})])
    with pytest.raises(api.Malformed):
        asyncio.run(api.read_contacts(CONN))
    _client(monkeypatch, [httpx.Response(200, json={"contacts": [], "meta": {"total": 0}})])
    assert asyncio.run(api.read_contacts(CONN)).records == []


def test_the_code_never_writes_to_employz():
    root = Path(__file__).resolve().parents[1] / "app"
    for f in ("employz_api.py", "employz_import.py", "routers/employz.py"):
        src = (root / f).read_text()
        assert not re.search(r"\.(post|put|patch|delete)\(|\"(POST|PUT|PATCH|DELETE)\"", src.replace("@router.post", "")), f


# ───────────────────────── the import ─────────────────────────
async def test_check_lists_what_the_token_can_read_and_refuses_another_sub_account(client, fake):
    c, uid = await connected("9873000001")
    d = (await c.get("/api/employz/check")).json()
    assert d["ok"] and d["location"]["name"] == "Invented Test Business" and all(t["readable"] for t in d["types"])
    stored = (await db().connections.find_one({"_id": uid}))["employz"]["token"]
    assert TOKEN not in json.dumps(stored, default=str)                 # encrypted at rest
    fake.status = 401
    d = (await c.get("/api/employz/check")).json()
    assert not d["ok"] and "refused" in d["error"]
    fake.status = None
    conn = {"location_id": "SomeoneElse0000000000", "token": TOKEN}
    with pytest.raises(api.AuthError):
        await api.read_location(conn)


async def test_full_import_then_nothing_written_twice_then_changes_and_deletes(client, fake):
    c, uid = await connected("9873000002")
    assert (await imp.status(uid))["state"] == "never"      # saved, nothing imported yet: never "up to date"
    st = await imp.run(uid, "initial")
    assert st["state"] == "idle" and st["in_hub"] == {"pipelines": 2, "contacts": 150, "opportunities": 120}
    assert st["coverage"]["contacts"] == {"source": 150, "imported": 150, "rejected": 0, "ready": True}
    assert st["coverage"]["opportunities"]["ready"] and st["counts"]["contacts"]["added"] == 150
    one = await db().contacts.find_one({"ws": uid, "ext.ids": "c0003"})
    assert one["name"] == "Test3 Person" and one["company"] == "Invented Traders" and one["ext"]["fields"] == {"City": "Pune", "cfX": "kept"}
    assert one["ext"]["owner"] == "Asha Owner"
    # the second run writes nothing
    st = await imp.run(uid, "scheduled")
    assert st["counts"]["contacts"] == {"added": 0, "updated": 0, "unchanged": 150, "rejected": 0}
    assert st["counts"]["opportunities"]["unchanged"] == 120 and st["counts"]["pipelines"]["unchanged"] == 2
    # a changed contact updates once; a deleted one is marked deleted, not removed
    fake.contacts[5]["companyName"] = "Renamed Co"
    del fake.contacts[7]
    fake.deals = [d for d in fake.deals if d["id"] != "o0010"]
    st = await imp.run(uid, "scheduled")
    assert st["counts"]["contacts"]["updated"] == 1 and st["in_hub"]["contacts"] == 149 and st["in_hub"]["opportunities"] == 119
    assert (await db().contacts.find_one({"ws": uid, "ext.ids": "c0005"}))["company"] == "Renamed Co"
    gone = await db().contacts.find_one({"ws": uid, "ext.ids": "c0007"})
    assert gone and gone["ext"]["deleted_at"]
    assert (await db().ext_deals.find_one({"_id": f"{uid}:o0010"}))["deleted_at"]
    # the CRM no longer lists the deleted contact
    r = await c.get("/api/crm/contacts")
    assert r.status_code == 200, r.text
    names = [x["name"] for x in r.json()["items"]]
    assert "Test7 Person" not in names and "Test8 Person" in names


async def test_a_sync_stopped_part_way_carries_on_from_its_page_with_exact_counts(client, fake, monkeypatch):
    c, uid = await connected("9873000003")
    original = fake.handler
    tries = {"n": 0}

    def flaky(req):
        if req.url.path == "/contacts/" and "startAfterId" in str(req.url) and tries["n"] < 4:
            tries["n"] += 1
            return httpx.Response(503)
        return original(req)
    monkeypatch.setattr(api, "_transport", lambda: httpx.MockTransport(flaky))   # the 2nd contacts page fails all 4 tries
    st = await imp.run(uid, "initial")
    assert st["state"] == "failed" and st["step"] == "contacts" and st["pages"]["contacts"] == 1
    assert await db().contacts.count_documents({"ws": uid, "ext.src": "employz"}) == 100
    st = await imp.run(uid, "scheduled")               # carries on from page 2
    assert st["state"] == "idle" and st["in_hub"]["contacts"] == 150
    assert st["counts"]["contacts"]["added"] == 150 and st["coverage"]["contacts"]["ready"]


async def test_a_hub_contact_is_linked_not_copied_and_a_pushed_deal_is_recognised(client, fake):
    c, uid = await connected("9873000004")
    await db().contacts.insert_one({"_id": "hub-contact-1", "ws": uid, "name": "Asha Kulkarni (my name)", "phone": "+919800000001",
                                    "email": None, "company": "", "tags": [], "created_at": now(), "updated_at": now(), "last_activity_at": now()})
    await db().leads.insert_one({"_id": "hub-lead-1", "owner_id": uid, "name": "Pushed lead", "status": "new", "created_at": now(),
                                 "crm": {"opportunity_id": "o0001", "contact_id": "c0001"}})
    await imp.run(uid, "initial")
    merged = await db().contacts.find_one({"_id": "hub-contact-1"})
    assert merged["name"] == "Asha Kulkarni (my name)" and "c0001" in merged["ext"]["ids"] and merged["ext"]["created"] is False
    assert await db().contacts.count_documents({"ws": uid, "phone": "+919800000001"}) == 1
    assert (await db().ext_deals.find_one({"_id": f"{uid}:o0001"}))["hub_lead_id"] == "hub-lead-1"


async def test_the_board_shows_each_employz_pipeline_read_only_and_only_to_its_business(client, fake):
    c, uid = await connected("9873000005")
    await imp.run(uid, "initial")
    hub = (await c.get("/api/crm/deals")).json()
    keys = [p["key"] for p in hub["pipelines"]]
    assert keys[0] == "hub" and "employz:pipeA" in keys and "employz:pipeB" in keys and hub["readonly"] is False
    b = (await c.get("/api/crm/deals", params={"pipeline": "employz:pipeA"})).json()
    assert b["readonly"] and b["pipeline_name"] == "Football Field" and [s["name"] for s in b["stages"]][:2] == ["Identification", "Logic / reason"]
    assert len(b["deals"]) == 90 and all(d["readonly"] and d["id"].startswith("x:") for d in b["deals"])
    assert sum(s["count"] for s in b["stages"]) == b["totals"]["open"] == sum(1 for d in b["deals"] if d["status"] == "new")
    assert b["open_url"] == "https://app.employz.ai/v2/location/x"
    # another business sees none of it
    other, _, _ = await owner_on("9873000006", "running")
    assert [p["key"] for p in (await other.get("/api/crm/deals")).json()["pipelines"]] == ["hub"]
    assert (await other.get("/api/crm/deals", params={"pipeline": "employz:pipeA"})).status_code == 404
    other_contacts = (await other.get("/api/crm/contacts")).json()
    assert "Test3 Person" not in json.dumps(other_contacts)


async def test_pick_the_pipeline_for_new_hub_leads_fills_the_5_stage_ids(client, fake):
    c, uid = await connected("9873000007")
    d = (await c.get("/api/employz/remote-pipelines")).json()
    assert [p["name"] for p in d["pipelines"]] == ["Football Field", "Service renewals"]
    r = (await c.post("/api/employz/push-pipeline", json={"pipeline_id": "pipeA"})).json()
    assert r["missing"] == [] and r["stages"]["close"] == "Ask / close"
    conn = await connections.get(uid, "employz")
    assert conn["pipeline_id"] == "pipeA" and conn["stage_identification"] == "sA1" and conn["stage_close"] == "sA5"
    assert (await c.post("/api/employz/push-pipeline", json={"pipeline_id": "nopeXYZ"})).status_code == 404
    # a pipeline whose stage names don't match: the owner picks a stage for each step; won/lost are never suggested
    fake.pipes.append({"id": "pipeC", "name": "Sales coordinator", "stages": [{"id": f"sC{i}", "name": n, "position": i} for i, n in enumerate(
        ["New Lead", "Customer Contacted", "Lead Qualified", "Site Visit Booked", "Follow Up", "Lead Closed Lost", "Lead Closed Won"])]})
    d = (await c.get("/api/employz/remote-pipelines")).json()
    assert d["saved"]["close"] == "sA5" and list(d["steps"]) == ["identification", "logic", "pain", "vision", "close"]
    pc = next(p for p in d["pipelines"] if p["id"] == "pipeC")
    assert pc["suggested"] == {"identification": "sC0"}                      # not "Lead Closed Lost" or "Won" for Close
    r = await c.post("/api/employz/push-pipeline", json={"pipeline_id": "pipeC", "stages": {"identification": "sC0"}})
    assert r.status_code == 400                                              # all 5 steps, or nothing is saved
    assert (await c.post("/api/employz/push-pipeline", json={"pipeline_id": "pipeC", "stages": {
        "identification": "sC0", "logic": "sC1", "pain": "sC2", "vision": "sC3", "close": "sA5"}})).status_code == 400   # another pipeline's stage
    mine = {"identification": "sC0", "logic": "sC1", "pain": "sC2", "vision": "sC3", "close": "sC4"}
    r = (await c.post("/api/employz/push-pipeline", json={"pipeline_id": "pipeC", "stages": mine})).json()
    assert r["missing"] == [] and r["stages"]["close"] == "Follow Up"
    conn = await connections.get(uid, "employz")
    assert conn["pipeline_id"] == "pipeC" and {k: conn[f"stage_{k}"] for k in mine} == mine
    # matched by name only (no choice sent): what didn't match is cleared, never left from the old pipeline
    await c.post("/api/employz/push-pipeline", json={"pipeline_id": "pipeB"})
    conn = await connections.get(uid, "employz")
    assert conn["pipeline_id"] == "pipeB" and conn["stage_logic"] == "" and conn["stage_close"] == ""


async def test_one_run_at_a_time_a_refused_token_needs_attention_and_disconnect_purges_later(client, fake, caplog):
    c, uid = await connected("9873000008")
    with caplog.at_level(logging.INFO):
        a, b = await asyncio.gather(imp.run(uid, "manual"), imp.run(uid, "manual"))
    assert await db().ext_deals.count_documents({"ws": uid}) == 120
    assert sum(1 for m, p in fake.calls if p == f"/locations/{LOC}") == 1          # only one of the two ran
    for secret in (TOKEN, "+919800000003", "test3@example.in", "Test3 Person"):
        assert secret not in caplog.text
    # a refused token: needs attention, the owner is told once, the clock waits for them
    fake.status = 401
    st = await imp.run(uid, "scheduled")
    assert st["state"] == "needs_attention" and "refused" in st["error"]["message"]
    assert await db().notices.count_documents({"user_id": uid, "text": {"$regex": "Employz.ai connection needs attention"}}) == 1
    assert not imp._due(await db().ext_sync.find_one({"_id": uid}), now())
    fake.status = None
    # disconnect: syncing stops at once; the imported data goes after the grace period
    assert (await c.delete("/api/connections/employz")).status_code == 200
    assert (await imp.status(uid))["state"] == "disconnected"
    assert await imp.tick(now(), wait=True) == 0 and await db().ext_deals.count_documents({"ws": uid}) == 120
    await imp.tick(now() + timedelta(days=imp.PURGE_DAYS, minutes=1), wait=True)
    assert await db().ext_deals.count_documents({"ws": uid}) == 0 and await db().ext_pipelines.count_documents({"ws": uid}) == 0
    assert await db().contacts.count_documents({"ws": uid, "ext.src": "employz"}) == 0


async def test_import_now_and_the_scheduler_start_a_sync_that_is_due(client, fake):
    c, uid = await connected("9873000009")
    assert (await c.post("/api/employz/import")).json()["running"] is True
    for _ in range(50):
        await REAL_SLEEP(0.05)
        if (await imp.status(uid))["state"] == "idle":
            break
    st = (await c.get("/api/employz/import")).json()
    assert st["state"] == "idle" and st["in_hub"]["opportunities"] == 120 and st["last_ok_at"]
    assert await imp.tick(now(), wait=True) == 0                                    # just synced: not due
    assert await imp.tick(now() + timedelta(minutes=imp.SYNC_MINUTES + 1), wait=True) == 1


async def test_saving_the_connection_again_never_breaks_a_running_import(client, fake):
    c, uid = await connected("9873000010")
    await imp.run(uid, "initial")
    until = now() + timedelta(minutes=5)
    await db().ext_sync.update_one({"_id": uid}, {"$set": {"state": "running", "lock_until": until}})
    await imp.on_connect(uid)                                   # the owner saves the CRM details while it runs
    s = await db().ext_sync.find_one({"_id": uid})
    assert s["state"] == "running" and s["lock_until"]
    assert (await c.get("/api/employz/import")).json()["running"] is True
