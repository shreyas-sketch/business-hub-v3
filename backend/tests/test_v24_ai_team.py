"""v2.4 AI team page, Weekly growth line and Quick answers. Every count must equal a direct count of the saved records.

The other v2.4 features (prospecting, lead research, pipeline fills itself, call coach) are built in parallel, so their records
are inserted here directly, in the shapes in docs/V24_BUILD_CONTRACTS.md. Every business, person, phone number and amount
here is invented."""
from datetime import datetime, timedelta, timezone

import pytest

from app import ai_team, growth_core, plans, transcribe
from app.agents import catalog, jobs, scheduler
from app.config import settings
from app.db import IST, db, now
from app.services import runs_status
from tests.v24_helpers import admin_client, owner_id, v24_member

AT = datetime(2026, 11, 15, 12, 0, tzinfo=IST).astimezone(timezone.utc)        # "now" for the counting tests: Sunday 15 Nov, noon
TODAY = datetime(2026, 11, 15, 0, 5, tzinfo=IST).astimezone(timezone.utc)      # 5 minutes into today, India time (still 14 Nov in UTC)
YDAY = datetime(2026, 11, 14, 23, 55, tzinfo=IST).astimezone(timezone.utc)     # 10 minutes earlier: yesterday, the same UTC date
FIRST = datetime(2026, 11, 1, 0, 5, tzinfo=IST).astimezone(timezone.utc)       # the 1st of this month, India time (31 Oct in UTC)
LAST_MONTH = datetime(2026, 10, 31, 23, 55, tzinfo=IST).astimezone(timezone.utc)
ROLE_KEYS = ["prospector", "outreach_writer", "researcher", "pipeline_keeper", "call_coach", "followup", "payment_reminder"]


def ist(y, m, d, h=12, mi=0) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=IST).astimezone(timezone.utc)


@pytest.fixture
def setting():
    old = {}

    def change(**kw):
        for k, v in kw.items():
            old.setdefault(k, getattr(settings, k))
            object.__setattr__(settings, k, v)
    yield change
    for k, v in old.items():
        object.__setattr__(settings, k, v)


async def user(uid: str) -> dict:
    return await db().users.find_one({"_id": uid})


async def member(phone: str, **kw):
    c = await v24_member(phone, **kw)
    return c, await owner_id(c)


def by_key(rows: list[dict]) -> dict:
    return {r["key"]: r for r in rows}


def counts_of(role: dict) -> dict:
    return {c["key"]: (c["today"], c["month"]) for c in role["counts"]}


# ───────────────────────── invented records, one set per role ─────────────────────────
async def seed(ws: str, other: str = "ws-someone-else") -> None:
    """Records at the India-time edges: TODAY and YDAY are on the same UTC date but different India days; FIRST is in this
    month in India time but in October in UTC; LAST_MONTH is the evening before. Another business's records never count."""
    d = db()
    await d.prospect_searches.insert_many([
        {"_id": "s1", "ws": ws, "kinds": ["Interior designers"], "area": "Invented Nagar", "radius_km": 5, "found": 12, "new": 9, "at": TODAY, "by": ws},
        {"_id": "s2", "ws": ws, "kinds": ["Architects"], "area": "Invented Nagar", "radius_km": 5, "found": 8, "new": 6, "at": YDAY, "by": ws},
        {"_id": "s3", "ws": ws, "kinds": ["Builders"], "area": "Sample Road", "radius_km": 3, "found": 5, "new": 5, "at": FIRST, "by": ws},
        {"_id": "s4", "ws": ws, "kinds": ["Builders"], "area": "Sample Road", "radius_km": 3, "found": 100, "new": 90, "at": LAST_MONTH, "by": ws},
        {"_id": "s5", "ws": other, "kinds": ["Builders"], "area": "Elsewhere", "radius_km": 3, "found": 50, "new": 50, "at": TODAY, "by": other},
    ])

    def prospect(pid, wsid, status, kept_at, replied_at=None):
        return {"_id": pid, "ws": wsid, "source": "list", "place_id": None, "kind": "Interior designers", "name": f"Invented Firm {pid}",
                "phone": "+919800000000", "email": None, "website": None, "city": "Invented City", "status": status, "notes": "",
                "kept_at": kept_at, "drafted_at": None, "contacted_at": None, "replied_at": replied_at, "closed_at": None, "lead_id": None,
                "followups_sent": 0, "next_followup_on": None, "updated_at": kept_at}
    await d.prospects.insert_many([
        prospect("p1", ws, "kept", TODAY), prospect("p2", ws, "drafted", TODAY), prospect("p3", ws, "replied", YDAY, replied_at=TODAY),
        prospect("p4", ws, "contacted", LAST_MONTH), prospect("p5", ws, "deal", LAST_MONTH, replied_at=FIRST),
        prospect("p6", ws, "kept", FIRST), prospect("p9", other, "kept", TODAY, replied_at=TODAY)])

    def approval(aid, wsid, agent, kind, status, created_at, decided_at=None, via=None, decided_by=None):
        return {"_id": aid, "ws": wsid, "agent": agent, "kind": kind, "title": "Invented", "to_name": "Invented Person",
                "to_phone": "+919800000001", "text": "Hello, an invented message.", "params": [], "status": status, "via": via,
                "created_at": created_at, "decided_at": decided_at, "decided_by": decided_by, "lead_id": None, "due_id": None}
    await d.approvals.insert_many([
        # outreach: drafts by created_at; "messages you sent" = sent via own by decided_at; pending = waiting
        approval("a1", ws, "outreach_writer", "outreach", "pending", TODAY),
        approval("a2", ws, "outreach_writer", "outreach", "sent", TODAY, TODAY, "own", ws),
        approval("a3", ws, "outreach_writer", "outreach", "sent", FIRST, YDAY, "own", ws),
        approval("a4", ws, "outreach_writer", "outreach", "skipped", TODAY, TODAY, None, ws),
        approval("a5", ws, "outreach_writer", "outreach", "sent", YDAY, TODAY, "owner-whatsapp", ws),   # not "own": never counts as sent by you
        approval("a6", ws, "outreach_writer", "outreach", "sent", LAST_MONTH, LAST_MONTH, "own", ws),
        approval("a7", ws, "outreach_writer", "outreach", "sent", LAST_MONTH, TODAY, "own", ws),       # written in October, sent today
        approval("a9", other, "outreach_writer", "outreach", "pending", TODAY),
        # follow-ups: "sent by you" leaves out the ones the agent sent on its own
        approval("f1", ws, "followup", "followup", "pending", TODAY),
        approval("f2", ws, "followup", "followup", "sent", TODAY, TODAY, "own", ws),
        approval("f3", ws, "followup", "followup", "sent", YDAY, YDAY, "owner-whatsapp", "agent"),
        approval("f4", ws, "followup", "followup", "sent", FIRST, FIRST, "owner-whatsapp", ws),
        approval("f5", ws, "followup", "followup", "expired", LAST_MONTH, TODAY, None, "agent"),
        # payment reminders
        approval("r1", ws, "payment_reminder", "reminder", "pending", TODAY),
        approval("r2", ws, "payment_reminder", "reminder", "sent", YDAY, YDAY, "own", ws),
        approval("r3", ws, "payment_reminder", "reminder", "pending", LAST_MONTH),
        approval("r9", other, "payment_reminder", "reminder", "pending", TODAY),
    ])

    def lead(lid, wsid, brief=None, **extra):
        return {"_id": lid, "owner_id": wsid, "name": f"Invented Lead {lid}", "phone": "+919800000002", "status": "new", "stage": "identification",
                "source": "website", "created_at": LAST_MONTH, **({"brief": brief} if brief else {}), **extra}

    def brief(status, at):
        return {"status": status, "who": "An invented business", "asking": "A quote", "questions": ["Q1?", "Q2?", "Q3?"], "first_line": "Hello",
                "sources": [], "looked_up": status == "done", "at": at, "by": "auto"}
    await d.leads.insert_many([
        lead("l1", ws, brief("done", TODAY)), lead("l2", ws, brief("done", FIRST)), lead("l3", ws, brief("empty", TODAY)),
        lead("l4", ws, brief("done", LAST_MONTH)), lead("l5", ws, brief("failed", YDAY)), lead("l9", other, brief("done", TODAY))])

    def update(uid, wsid, state, at, applied_at=None):
        return {"_id": uid, "ws": wsid, "lead_id": "l1", "contact_id": None, "source": "paste", "call_id": None, "summary": "Invented",
                "needs": "", "next_step": "", "follow_up_on": None, "applied": {"note": True, "follow_up_on": None}, "suggested": {},
                "state": state, "undo": {}, "undone_at": None, "at": at, "applied_at": applied_at, "by": ws}
    await d.deal_updates.insert_many([
        update("u1", ws, "pending", TODAY), update("u2", ws, "applied", TODAY, TODAY), update("u3", ws, "applied", YDAY, TODAY),
        update("u4", ws, "dismissed", FIRST), update("u5", ws, "applied", LAST_MONTH, FIRST), update("u6", ws, "done", LAST_MONTH),
        update("u9", other, "pending", TODAY)])

    def session(sid, wsid, scored_at, overall, source="call_coach", kind="call"):
        return {"_id": sid, "ws": wsid, "user_id": wsid, "kind": kind, "scenario": "call", "title": "An invented call", "messages": [],
                "status": "scored", "member": "Invented Salesperson", "source": source,
                "score": {"scores": {}, "overall": overall, "fixes": ["a", "b", "c"], "best": "x"}, "created_at": scored_at, "scored_at": scored_at}
    await d.gym_sessions.insert_many([
        session("g1", ws, TODAY, 6), session("g2", ws, TODAY, 8), session("g3", ws, FIRST, 4),
        session("g4", ws, TODAY, 10, source=None),                     # a Sales Training Gym call, not the Call coach's
        session("g5", ws, LAST_MONTH, 1), session("g9", other, TODAY, 2)])

    def due(did, wsid, status, amount, paid_at=None):
        return {"_id": did, "ws": wsid, "customer": "Invented Customer", "phone": "+919800000003", "amount": amount, "due_date": "2026-11-01",
                "note": "", "invoice_no": "", "status": status, "paid_at": paid_at, "reminders_sent": 0, "created_at": LAST_MONTH}
    await d.dues.insert_many([
        due("d1", ws, "paid", 500000, TODAY), due("d2", ws, "paid", 1234550, YDAY), due("d3", ws, "paid", 999900, LAST_MONTH),
        due("d4", ws, "due", 100000), due("d9", other, "paid", 700000, TODAY)])


# what the records above must count to — (today, this month)
EXPECTED = {
    "prospector": {"found": (12, 25), "kept": (2, 4)},
    "outreach_writer": {"drafts": (3, 5), "sent": (2, 3), "replies": (1, 2)},
    "researcher": {"researched": (1, 2)},
    "pipeline_keeper": {"filed": (2, 4), "applied": (2, 3)},
    "call_coach": {"scored": (2, 3), "average": (7.0, 6.0)},
    "followup": {"drafts": (2, 4), "sent": (1, 2)},
    "payment_reminder": {"prepared": (1, 2), "received": (5000.0, 17345.5)},
}
WAITING = {"prospector": 2, "outreach_writer": 1, "researcher": 0, "pipeline_keeper": 1, "call_coach": 0, "followup": 1, "payment_reminder": 2}


async def direct_counts(ws: str, start: datetime, end: datetime) -> dict:
    """The same numbers, counted straight from the collections as the contracts define them (independent of ai_team)."""
    d = db()
    r = {"$gte": start, "$lt": end}
    found = sum([s["found"] async for s in d.prospect_searches.find({"ws": ws, "at": r})])
    scores = [s["score"]["overall"] async for s in d.gym_sessions.find({"ws": ws, "kind": "call", "source": "call_coach", "scored_at": r})]
    paid = sum([x["amount"] async for x in d.dues.find({"ws": ws, "status": "paid", "paid_at": r})])
    return {
        "prospector": {"found": found, "kept": await d.prospects.count_documents({"ws": ws, "kept_at": r})},
        "outreach_writer": {"drafts": await d.approvals.count_documents({"ws": ws, "kind": "outreach", "created_at": r}),
                            "sent": await d.approvals.count_documents({"ws": ws, "kind": "outreach", "status": "sent", "via": "own", "decided_at": r}),
                            "replies": await d.prospects.count_documents({"ws": ws, "replied_at": r})},
        "researcher": {"researched": await d.leads.count_documents({"owner_id": ws, "brief.status": "done", "brief.at": r})},
        "pipeline_keeper": {"filed": await d.deal_updates.count_documents({"ws": ws, "at": r}),
                            "applied": await d.deal_updates.count_documents({"ws": ws, "state": "applied", "applied_at": r})},
        "call_coach": {"scored": len(scores), "average": round(sum(scores) / len(scores), 1) if scores else None},
        "followup": {"drafts": await d.approvals.count_documents({"ws": ws, "agent": "followup", "created_at": r}),
                     "sent": await d.approvals.count_documents({"ws": ws, "agent": "followup", "status": "sent",
                                                                "decided_by": {"$nin": ["agent", "auto-approved"]}, "decided_at": r})},
        "payment_reminder": {"prepared": await d.approvals.count_documents({"ws": ws, "kind": "reminder", "created_at": r}),
                             "received": round(paid / 100, 2)},
    }


# ═════════════════════════ the AI team page ═════════════════════════
async def test_every_count_equals_a_direct_count_today_and_this_month_in_india_time(client, monkeypatch):
    m, uid = await member("9800440001")
    await seed(uid)
    monkeypatch.setattr(ai_team, "now", lambda: AT)
    r = await m.get("/api/ai-team")
    assert r.status_code == 200, r.text
    page = r.json()
    roles = by_key(page["roles"])
    assert list(roles) == ROLE_KEYS                       # every role, in the order of growth_core.ROLES
    day = await direct_counts(uid, *ai_team.day_bounds(AT))
    month = await direct_counts(uid, *ai_team.month_bounds(AT))
    for role, want in EXPECTED.items():
        got = counts_of(roles[role])
        assert got == want, role
        for key, (t, mo) in got.items():
            assert (t, mo) == (day[role][key], month[role][key]), (role, key)
    # the India-time edges: 5 minutes past midnight is today; 10 minutes before it is not (both are 14 Nov in UTC)
    assert ai_team.day_bounds(AT) == (ist(2026, 11, 15, 0, 0), ist(2026, 11, 16, 0, 0))
    assert ai_team.month_bounds(AT) == (ist(2026, 11, 1, 0, 0), ist(2026, 12, 1, 0, 0))
    assert TODAY.date() == YDAY.date() and FIRST.month == 10
    # the same numbers straight from the counting module, for each role
    for role in ROLE_KEYS:
        assert {c["key"]: (c["today"], c["month"]) for c in await ai_team.role_counts(uid, role, AT)} == EXPECTED[role]
    # waiting for you: drafts and suggestions not acted on yet
    assert {k: roles[k]["waiting"] for k in ROLE_KEYS} == WAITING
    assert roles["prospector"]["waiting_text"] == "2 businesses kept, waiting for a first message"
    assert roles["pipeline_keeper"]["waiting_text"] == "1 suggested change waiting for your tap"
    assert roles["outreach_writer"]["waiting_link"] == "/approvals"
    assert page["refresh_seconds"] == 15


async def test_the_top_strip_and_hours_saved_count_this_month(client, monkeypatch):
    m, uid = await member("9800440002")
    await seed(uid)
    await db().leads.insert_many([   # deals from new customers: leads from a prospect, with the value the owner entered (or none)
        {"_id": "n1", "owner_id": uid, "name": "Invented Deal One", "source": "prospecting", "prospect_id": "p3", "status": "new",
         "value": 85000, "created_at": TODAY},
        {"_id": "n2", "owner_id": uid, "name": "Invented Deal Two", "source": "prospecting", "prospect_id": "p5", "status": "new",
         "value": None, "created_at": FIRST},
        {"_id": "n3", "owner_id": uid, "name": "Invented Old Deal", "source": "prospecting", "prospect_id": "p4", "status": "won",
         "value": 40000, "created_at": LAST_MONTH},
        {"_id": "n4", "owner_id": uid, "name": "Invented Website Deal", "source": "website", "status": "new", "value": 99000, "created_at": TODAY},
    ])
    monkeypatch.setattr(ai_team, "now", lambda: AT)
    page = (await m.get("/api/ai-team")).json()
    assert page["strip"] == {"found": 25, "sent": 3, "replies": 2, "deals": 2, "value": 85000.0}
    # hours saved: once, for the month, estimated, with the minutes per action listed
    h = page["hours"]
    assert h["estimated"] is True
    rows = {r["key"]: (r["n"], r["minutes"]) for r in h["rows"]}
    searches = await db().prospect_searches.count_documents({"ws": uid, "at": {"$gte": ist(2026, 11, 1, 0, 0), "$lt": ist(2026, 12, 1, 0, 0)}})
    assert rows == {"search": (searches, 20), "outreach_draft": (5, 5), "brief": (2, 10), "filed": (4, 5), "call": (3, 15),
                    "followup": (4, 5), "reminder": (2, 4)}
    assert searches == 3
    total = 3 * 20 + 5 * 5 + 2 * 10 + 4 * 5 + 3 * 15 + 4 * 5 + 2 * 4
    assert h["minutes"] == total and h["hours"] == round(total / 60, 1)
    assert all(r["what"] for r in h["rows"])


async def test_a_new_owner_sees_zeros_each_with_one_next_step(client):
    m, uid = await member("9800440003")
    page = (await m.get("/api/ai-team")).json()
    for role in page["roles"]:
        assert all(c["today"] in (0, None) and c["month"] in (0, None) for c in role["counts"]), role["key"]
        assert role["next"]["text"] and role["next"]["path"].startswith("/"), role["key"]
        assert role["last"] is None and role["waiting"] == 0
    assert by_key(page["roles"])["call_coach"]["counts"][1]["month"] is None     # no calls: no average (not a made-up zero)
    assert page["strip"]["found"] == 0 and page["strip"]["next"]["path"] == "/prospects"
    assert page["hours"]["hours"] == 0 and page["feed"] == []
    assert page["weekly"]["line"] is None


async def test_states_are_true_working_only_while_a_job_runs(client, setting):
    m, uid = await member("9800440004")
    setting(google_places_api_key="")

    async def roles():
        return by_key((await m.get("/api/ai-team")).json()["roles"])
    r = await roles()
    assert r["researcher"]["state"] == "idle" and r["researcher"]["state_label"] == "Idle"
    # Needs setup: no Places key on the server
    assert r["prospector"]["state"] == "setup" and r["prospector"]["setup"] == "Search needs a server setting. Your own list still works."
    # follow-ups on Membership wait for the owner's own switch (as on the Agents page), so they are honestly off
    assert r["followup"]["state"] == "off" and r["followup"]["on"] is False

    async with growth_core.working(uid, "researcher"):
        assert (await roles())["researcher"]["state"] == "working"
        assert (await roles())["researcher"]["state_label"] == "Working"
        assert (await roles())["pipeline_keeper"]["state"] == "idle"
    assert (await roles())["researcher"]["state"] == "idle"

    # Waiting for you, with how many — and Working comes first while a job runs
    await db().deal_updates.insert_one({"_id": "u1", "ws": uid, "lead_id": None, "state": "pending", "at": now(), "applied_at": None})
    r = await roles()
    assert r["pipeline_keeper"]["state"] == "waiting" and r["pipeline_keeper"]["waiting"] == 1
    async with growth_core.working(uid, "pipeline_keeper"):
        assert (await roles())["pipeline_keeper"]["state"] == "working"
    assert (await roles())["pipeline_keeper"]["state"] == "waiting"
    # a mark left by a process that died expires on its own
    await db().growth_working.insert_one({"_id": f"{uid}:call_coach", "ws": uid, "role": "call_coach", "since": now() - timedelta(minutes=10),
                                          "until": now() - timedelta(minutes=5), "n": 1})
    assert (await roles())["call_coach"]["state"] == "idle"


async def test_a_switched_off_role_says_so_and_its_automatic_work_stops(client):
    m, uid = await member("9800440005")
    for role in ("researcher", "pipeline_keeper", "prospector", "outreach_writer", "call_coach"):
        assert await growth_core.role_on(uid, role)
        r = await m.patch(f"/api/ai-team/roles/{role}", json={"on": False})
        assert r.status_code == 200, r.text
        assert r.json()["role"]["state"] == "off" and r.json()["role"]["state_label"] == "Switched off"
        assert not await growth_core.role_on(uid, role)          # what automatic work checks before it runs
    roles = by_key((await m.get("/api/ai-team")).json()["roles"])
    assert roles["researcher"]["state"] == "off" and roles["researcher"]["on"] is False
    # back on
    r = await m.patch("/api/ai-team/roles/researcher", json={"on": True})
    assert r.json()["role"]["state"] == "idle" and await growth_core.role_on(uid, "researcher")
    # follow-ups: the switch is the owner's own choice, so the agent really runs for a Membership owner
    owner = await user(uid)
    assert not await jobs.is_on_for(owner, "followup")
    r = await m.patch("/api/ai-team/roles/followup", json={"on": True})
    assert r.json()["role"]["on"] is True and await jobs.is_on_for(owner, "followup")
    await m.patch("/api/ai-team/roles/followup", json={"on": False})
    assert not await jobs.is_on_for(owner, "followup")
    # the Weekly growth line is on by default, and switched here
    assert await jobs.is_on_for(owner, "growth_weekly")
    r = await m.patch("/api/ai-team/roles/growth_weekly", json={"on": False})
    assert r.status_code == 200 and r.json()["weekly"]["on"] is False and not await jobs.is_on_for(owner, "growth_weekly")
    assert (await m.patch("/api/ai-team/roles/nobody", json={"on": False})).status_code == 404


async def test_the_feed_links_every_line_to_its_record(client):
    m, uid = await member("9800440006")
    t0 = now() - timedelta(hours=2)
    lines = [("prospector", "search:s1", "Found 12 businesses near Invented Nagar."),
             ("prospector", "prospect:p1", "Kept Invented Firm."),
             ("outreach_writer", "prospect:p2", "Wrote a first message to Invented Firm."),
             ("researcher", "lead:l1", "Wrote a brief on Invented Lead."),
             ("researcher", None, "A line with no reference."),
             ("pipeline_keeper", "lead:l2", "Filed a conversation into Invented Deal."),
             ("pipeline_keeper", "update:u1", "Suggested a new deal."),
             ("call_coach", "call:g1", "Scored a call: 7 out of 10."),
             ("followup", "abc123", "Follow-up 1 to Invented Lead is ready in your Send list."),
             ("payment_reminder", "approval:r1", "1 payment reminder ready in your Send list.")]
    for i, (agent, ref, text) in enumerate(lines):
        await db().agent_log.insert_one({"_id": f"log{i}", "ws": uid, "agent": agent, "text": text, "status": "done", "at": t0 + timedelta(minutes=i),
                                         "ref": ref})
    await db().agent_log.insert_many([
        {"_id": "x1", "ws": uid, "agent": "digest", "text": "Not an AI team role.", "status": "done", "at": now(), "ref": None},
        {"_id": "x2", "ws": "ws-someone-else", "agent": "researcher", "text": "Another business.", "status": "done", "at": now(), "ref": "lead:zz"},
        {"_id": "x3", "ws": uid, "agent": "followup", "text": "Invented Lead is already won, so no follow-up was needed.", "status": "skipped",
         "at": t0 + timedelta(minutes=30), "ref": "lead-x"}])
    page = (await m.get("/api/ai-team")).json()
    feed = page["feed"]
    assert [f["id"] for f in feed] == ["x3"] + [f"log{i}" for i in reversed(range(len(lines)))]   # newest first, only this hub's roles
    links = {f["id"]: f["link"] for f in feed}
    assert links == {"x3": "/approvals", "log0": "/prospects", "log1": "/prospects/p1", "log2": "/prospects/p2", "log3": "/pipeline?deal=l1",
                     "log4": "/inquiries", "log5": "/pipeline?deal=l2", "log6": "/pipeline", "log7": "/call-coach?call=g1", "log8": "/approvals",
                     "log9": "/approvals"}
    assert all(f["at"] and f["role_title"] for f in feed)
    roles = by_key(page["roles"])
    # each card's last action: the newest finished one, with its time and link (a skipped run is not an action)
    assert roles["prospector"]["last"]["text"] == "Kept Invented Firm." and roles["prospector"]["last"]["link"] == "/prospects/p1"
    assert roles["followup"]["last"]["id"] == "log8" and roles["call_coach"]["last"]["link"] == "/call-coach?call=g1"
    # at most 30 lines
    await db().agent_log.insert_many([{"_id": f"many{i}", "ws": uid, "agent": "researcher", "text": "Wrote a brief.", "status": "done",
                                       "at": now() + timedelta(seconds=i), "ref": f"lead:m{i}"} for i in range(40)])
    feed = (await m.get("/api/ai-team")).json()["feed"]
    assert len(feed) == 30 and feed[0]["id"] == "many39" and feed[-1]["id"] == "many10"


async def test_needs_setup_for_recordings_without_a_transcription_key(client, monkeypatch, setting):
    m, uid = await member("9800440007")
    setting(google_places_api_key="test-places-key")
    roles = by_key((await m.get("/api/ai-team")).json()["roles"])
    assert roles["prospector"]["state"] == "idle" and roles["prospector"]["setup"] is None
    assert roles["call_coach"]["state"] == "idle"
    monkeypatch.setattr(transcribe, "available", lambda: False)
    roles = by_key((await m.get("/api/ai-team")).json()["roles"])
    assert roles["call_coach"]["state"] == "setup" and roles["call_coach"]["setup"] == "Recordings need a server setting. Pasted calls work."


async def test_roles_show_only_when_their_feature_is_on(client):
    # a trial owner (not paying): Find new customers and Call coach are paid-only, so those roles don't show
    t, tid = await member("9800440008", paid=False)
    await db().users.update_one({"_id": tid}, {"$set": {"trial": {"plan": "lite", "until": now() + timedelta(days=10), "reason": "referral"}}})
    page = (await t.get("/api/ai-team")).json()
    keys = [r["key"] for r in page["roles"]]
    assert keys == ["researcher", "pipeline_keeper", "followup", "payment_reminder"]
    assert page["strip"] is None                                     # its numbers come from Find new customers
    assert (await t.patch("/api/ai-team/roles/prospector", json={"on": False})).status_code == 403
    # the admin switches a feature off for everyone: its role goes
    m, uid = await member("9800440009")
    admin = await admin_client()
    assert (await admin.patch("/api/admin/config/features/deal_autofill", json={"on": False})).status_code == 200
    await plans.load(force=True)
    keys = [r["key"] for r in (await m.get("/api/ai-team")).json()["roles"]]
    assert "pipeline_keeper" not in keys and "researcher" in keys and "prospector" in keys
    assert (await m.patch("/api/ai-team/roles/pipeline_keeper", json={"on": False})).status_code == 403


async def test_a_free_owner_is_refused(client):
    f, _ = await member("9800440010", paid=False)
    r = await f.get("/api/ai-team")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "locked"
    assert (await f.get("/api/ai-team/answers/stuck")).status_code == 403
    assert (await f.patch("/api/ai-team/roles/researcher", json={"on": False})).status_code == 403


# ═════════════════════════ the weekly growth line ═════════════════════════
MONDAY = ist(2026, 11, 23, 9, 5)    # last week = Mon 16 Nov 00:00 → Mon 23 Nov 00:00, India time


async def seed_week(ws: str) -> None:
    inside1, inside2 = ist(2026, 11, 16, 0, 5), ist(2026, 11, 22, 23, 55)
    before, after = ist(2026, 11, 15, 23, 55), ist(2026, 11, 23, 0, 5)
    d = db()
    await d.prospect_searches.insert_many([{"_id": f"w{i}", "ws": ws, "found": n, "new": n, "at": at, "kinds": [], "area": "Invented"}
                                           for i, (n, at) in enumerate([(25, inside1), (15, inside2), (7, before), (9, after)])])
    await d.approvals.insert_many([{"_id": f"wa{i}", "ws": ws, "agent": "outreach_writer", "kind": "outreach", "status": "sent", "via": via,
                                    "created_at": before, "decided_at": at}
                                   for i, (via, at) in enumerate([("own", inside1), ("own", inside2), ("own", before), ("owner-whatsapp", inside1),
                                                                  ("own", after)] + [("own", inside1)] * 10)])
    await d.prospects.insert_many([{"_id": f"wp{i}", "ws": ws, "status": "replied", "kept_at": before, "replied_at": at}
                                   for i, at in enumerate([inside1, inside2, inside2, before])])
    await d.leads.insert_many([{"_id": "wl1", "owner_id": ws, "source": "prospecting", "status": "new", "value": 85000, "created_at": inside2},
                               {"_id": "wl2", "owner_id": ws, "source": "prospecting", "status": "new", "value": 60000, "created_at": after},
                               {"_id": "wl3", "owner_id": ws, "source": "website", "status": "new", "value": 10000, "created_at": inside2}])


async def test_the_weekly_line_counts_last_monday_to_sunday_and_goes_to_the_owner(client, monkeypatch):
    m, uid = await member("9800440011")
    await seed_week(uid)
    line = "Last week: 40 businesses found, 12 messages sent by you, 3 replies, 1 new deal worth ₹85,000."
    assert ai_team.last_week(MONDAY) == (ist(2026, 11, 16, 0, 0), ist(2026, 11, 23, 0, 0))
    spec = catalog.AGENTS["growth_weekly"]
    assert spec.feature == "growth_weekly" and spec.group == "alerts" and spec.trigger == "schedule"
    slot = catalog.due_slot(spec.schedule, MONDAY)
    assert slot is not None
    assert catalog.due_slot(spec.schedule, ist(2026, 11, 23, 8, 55)) is None and catalog.due_slot(spec.schedule, ist(2026, 11, 22, 9, 5)) is None
    assert catalog.last_occurrence(spec.schedule, MONDAY) == ist(2026, 11, 23, 9, 0)
    owner = await user(uid)
    assert await scheduler.run_slot(spec, [owner], slot[0], MONDAY) == 1
    [wa] = [o async for o in db().outbox.find({"kind": "growth_weekly"})]
    assert wa["channel"] == "whatsapp" and wa["to"] == owner["phone"] and wa["params"][1] == line   # the owner's own phone, from the hub
    notes = [n["text"] async for n in db().notices.find({"user_id": uid})]
    assert line in notes
    log = await db().agent_log.find_one({"ws": uid, "agent": "growth_weekly"})
    assert log["status"] == "done" and log["text"].startswith(line)
    # once per week, however often the clock ticks
    assert await scheduler.run_slot(spec, [owner], slot[0], MONDAY + timedelta(minutes=1)) == 0
    assert await db().outbox.count_documents({"kind": "growth_weekly"}) == 1
    # the same line on the AI team page (counted from the records) and on the Dashboard
    monkeypatch.setattr(ai_team, "now", lambda: MONDAY + timedelta(hours=1))
    w = (await m.get("/api/ai-team")).json()["weekly"]
    assert w["line"] == line and w["on"] is True and w["from"] == "2026-11-16" and w["to"] == "2026-11-22" and w["sent"]["at"]
    tiles = {t["key"]: t for t in (await m.get("/api/dashboard")).json()["tiles"]}
    assert tiles["quick_answers"]["data"]["weekly"] == line


async def test_nothing_is_sent_when_every_number_is_zero_or_the_switch_is_off(client):
    m, uid = await member("9800440012")
    spec = catalog.AGENTS["growth_weekly"]
    slot = catalog.due_slot(spec.schedule, MONDAY)[0]
    # has used Find new customers before (so it is checked), but nothing happened last week
    await db().prospect_searches.insert_one({"_id": "old", "ws": uid, "found": 30, "new": 30, "at": ist(2026, 11, 2, 11), "kinds": [], "area": "x"})
    owner = await user(uid)
    assert await scheduler.run_slot(spec, [owner], slot, MONDAY) == 1
    assert await db().outbox.count_documents({"kind": "growth_weekly"}) == 0
    assert not [n async for n in db().notices.find({"user_id": uid, "text": {"$regex": "^Last week"}})]
    log = await db().agent_log.find_one({"ws": uid, "agent": "growth_weekly"})
    assert log["status"] == "skipped"
    assert ai_team.worth_saying(await ai_team.numbers_for(uid, *ai_team.last_week(MONDAY))) is False
    # an owner who never used Find new customers isn't even checked (no "nothing to report" line every Monday)
    m2, uid2 = await member("9800440013")
    assert await scheduler.run_slot(spec, [await user(uid2)], slot, MONDAY) == 0
    assert await db().agent_log.count_documents({"ws": uid2}) == 0
    # switched off on the AI team page: never sent, even with numbers
    m3, uid3 = await member("9800440014")
    await seed_week(uid3)
    assert (await m3.patch("/api/ai-team/roles/growth_weekly", json={"on": False})).status_code == 200
    assert await scheduler.run_slot(spec, [await user(uid3)], slot, MONDAY) == 0
    assert await db().outbox.count_documents({"kind": "growth_weekly"}) == 0


# ═════════════════════════ quick answers ═════════════════════════
async def answer(c, key: str) -> dict:
    r = await c.get(f"/api/ai-team/answers/{key}")
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["tokens"] == 0 and a["question"] and a["rule"] and a["line"]
    return a


async def test_which_deals_are_stuck(client):
    m, uid = await member("9800440020")
    t = now()
    ago = lambda days: t - timedelta(days=days)   # noqa: E731
    base = {"owner_id": uid, "phone": "", "stage": "identification", "source": "website"}
    await db().leads.insert_many([
        {**base, "_id": "s1", "name": "Invented Kitchen Job", "status": "new", "value": 85000, "created_at": ago(30), "stage_at": ago(30)},
        {**base, "_id": "s2", "name": "Invented Office Job", "status": "contacted", "value": 20000, "created_at": ago(30)},   # a call 2 days ago
        {**base, "_id": "s3", "name": "Invented Shop Job", "status": "new", "created_at": ago(15), "updated_at": ago(11)},
        {**base, "_id": "s4", "name": "Invented Won Job", "status": "won", "value": 50000, "created_at": ago(40)},
        {**base, "_id": "s5", "name": "Invented New Job", "status": "new", "created_at": ago(3)},
        {**base, "_id": "s6", "name": "Invented Followed Job", "status": "new", "created_at": ago(20), "last_followup_at": ago(5)},
        {**base, "_id": "s7", "name": "Invented Moved Job", "status": "new", "created_at": ago(25), "stage_at": ago(9)},
        {**base, "_id": "s8", "owner_id": "ws-someone-else", "name": "Not mine", "status": "new", "created_at": ago(50)},
    ])
    await db().crm_activities.insert_one({"_id": "act1", "ws": uid, "contact_id": None, "lead_id": "s2", "kind": "call", "text": "Called", "at": ago(2)})
    before = await runs_status(await user(uid))
    a = await answer(m, "stuck")
    assert a["numbers"] == {"open": 6, "stuck": 2, "value": 85000.0}
    assert [r["id"] for r in a["rows"]] == ["s1", "s3"]           # biggest value first
    assert a["rows"][0]["days"] == 30 and a["rows"][1]["days"] == 11 and a["rows"][0]["link"] == "/pipeline?deal=s1"
    assert a["line"] == "2 open deals had nothing happen for 10 days or more — worth ₹85,000 (value you entered)."
    assert "10 days" in a["rule"]
    assert (await runs_status(await user(uid)))["used"] == before["used"] and await db().runs.count_documents({}) == 0


async def test_who_owes_me_the_most(client):
    m, uid = await member("9800440021")
    t = now().astimezone(IST).date()
    day = lambda n: (t + timedelta(days=n)).isoformat()   # noqa: E731

    def due(i, name, phone, rupees, on, status="due"):
        return {"_id": f"o{i}", "ws": uid, "customer": name, "phone": phone, "amount": rupees * 100, "due_date": on, "status": status,
                "paid_at": None, "created_at": now()}
    await db().dues.insert_many([
        due(1, "Invented Traders", "+919800011111", 30000, day(-10)), due(2, "Invented Traders", "+919800011111", 20000, day(5)),
        due(3, "Sample Stores", "+919800022222", 70000, day(20)), due(4, "Demo Mart", "", 10000, day(3)), due(5, "Example Agencies", "", 5000, day(1)),
        due(6, "Test Brothers", "", 4000, day(2)), due(7, "Mock Enterprises", "", 3000, day(4)),
        due(8, "Paid Person", "", 999999, day(-1), status="paid"), {**due(9, "Other Hub", "", 500000, day(1)), "ws": "ws-someone-else"}])
    a = await answer(m, "owes")
    assert a["numbers"]["customers"] == 6 and a["numbers"]["total"] == 142000 and a["numbers"]["overdue"] == 30000
    assert [r["label"] for r in a["rows"]] == ["Sample Stores", "Invented Traders", "Demo Mart", "Example Agencies", "Test Brothers"]
    assert a["numbers"]["top"] == [70000, 50000, 10000, 5000, 4000]
    assert a["rows"][1]["value"] == "₹50,000" and a["rows"][1]["sub"].startswith("2 payments · oldest due") and "10 days late" in a["rows"][1]["sub"]
    assert a["line"] == "₹1,42,000 is owed to you by 6 customers. ₹30,000 of it is past its due date."
    # nobody owes anything
    m2, _ = await member("9800440022")
    a = await answer(m2, "owes")
    assert a["empty"] and a["numbers"]["total"] == 0 and a["next"]["path"] == "/money"


async def test_which_source_brings_the_most_deals(client):
    m, uid = await member("9800440023")
    t = now()

    def lead(i, source, status, created_days, won_days=None, value=None):
        return {"_id": f"q{i}", "owner_id": uid, "name": f"Invented {i}", "source": source, "status": status, "value": value,
                "created_at": t - timedelta(days=created_days), **({"won_at": t - timedelta(days=won_days)} if won_days is not None else {})}
    await db().leads.insert_many([
        lead(1, "referral", "won", 60, 10, 50000), lead(2, "referral", "won", 30, 5, 30000), lead(3, "referral", "new", 20),
        lead(4, "website", "won", 40, 20, 100000), lead(5, "website", "new", 10),
        lead(6, "whatsapp", "won", 200, 100, 90000),                 # won 100 days ago: outside the 90 days
        lead(7, "marketplace", "lost", 15),
        {**lead(8, "referral", "won", 5, 1, 70000), "owner_id": "ws-someone-else"}])
    a = await answer(m, "sources")
    by = a["numbers"]["by_source"]
    assert by["Referral"] == {"won": 2, "value": 80000.0, "came_in": 3}
    assert by["Website"] == {"won": 1, "value": 100000.0, "came_in": 2}
    assert by["Online marketplace"] == {"won": 0, "value": 0.0, "came_in": 1}         # a category name, not a company's
    assert "WhatsApp" not in by and a["numbers"]["won"] == 3 and a["numbers"]["value"] == 180000
    assert [r["label"] for r in a["rows"]] == ["Referral", "Website", "Online marketplace"]
    assert a["line"] == "Referral brought the most won deals in the last 90 days: 2 deals worth ₹80,000."
    m2, _ = await member("9800440024")
    a = await answer(m2, "sources")
    assert a["empty"] and a["numbers"]["won"] == 0


async def test_who_has_gone_quiet(client):
    m, uid = await member("9800440025")
    t = now().astimezone(IST).date()

    def sale(i, name, days_ago, rupees):
        return {"_id": f"sr{i}", "ws": uid, "batch_id": "b1", "date": (t - timedelta(days=days_ago)).isoformat(), "customer": name, "phone": "",
                "amount": rupees * 100, "gst": None, "invoice_no": "", "due_date": "", "paid_on": "", "created_at": now()}
    rows = [sale(i, "Invented Traders", d, 50000) for i, d in enumerate([240, 210, 180, 150, 120])]          # every 30 days, last 120 days ago
    rows += [sale(10 + i, "Sample Stores", d, 20000) for i, d in enumerate([45, 35, 25, 15, 5])]          # still buying
    rows += [sale(20 + i, "Example Agencies", d, 10000) for i, d in enumerate([200, 180, 160])]          # every 20 days, last 160 days ago
    rows += [sale(30 + i, "Demo Mart", d, 5000) for i, d in enumerate([300, 100])]                       # bought twice: not a regular
    await db().sales_rows.insert_many(rows)
    a = await answer(m, "quiet")
    assert a["enough"] is True
    assert a["numbers"]["names"] == ["Invented Traders", "Example Agencies"] and a["numbers"]["quiet"] == 2
    # the yearly value: their sales over the time since their first purchase, as on the Customer risk page
    assert a["rows"][0]["amount"] == round(250000 / 240 * 365) and a["rows"][1]["amount"] == round(30000 / 200 * 365)
    assert a["rows"][0]["sub"].endswith("usually every 30 days") and a["rows"][0]["wa"].startswith("https://wa.me/")
    m2, _ = await member("9800440026")
    a = await answer(m2, "quiet")
    assert a["enough"] is False and a["line"].startswith("Not enough sales history yet")


async def test_how_fast_we_reply_to_new_inquiries(client):
    m, uid = await member("9800440027")
    t = now()

    def lead(i, created_days, reply_minutes=None, **extra):
        created = t - timedelta(days=created_days)
        return {"_id": f"r{i}", "owner_id": uid, "name": f"Invented {i}", "source": "website", "status": "new" if reply_minutes is None else "contacted",
                "created_at": created, **({"first_action_at": created + timedelta(minutes=reply_minutes)} if reply_minutes is not None else {}), **extra}
    await db().leads.insert_many([
        lead(1, 10, 30), lead(2, 9, 120), lead(3, 8, 26 * 60), lead(4, 2),
        lead(5, 5, 1, created_by=uid),                     # a deal the owner added himself: no reply to time
        lead(6, 40, 5),                                    # older than 30 days
        lead(7, 3, 2, source="prospecting")])
    a = await answer(m, "reply")
    assert a["enough"] is True
    assert a["numbers"] == {"inquiries": 4, "replied": 3, "waiting": 1, "median_minutes": 120.0, "within_hour": 1, "within_day": 1}
    assert a["line"] == "Half of your inquiries get a first reply within 2 hours (the median, over 3 replies)."
    assert [r["n"] for r in a["rows"]] == [1, 1, 1, 1]
    # too few replies recorded: "Not enough data", never a guess
    m2, uid2 = await member("9800440028")
    await db().leads.insert_many([{**lead(10, 3, 15), "owner_id": uid2}, {**lead(11, 2), "owner_id": uid2}])
    a = await answer(m2, "reply")
    assert a["enough"] is False and a["numbers"]["median_minutes"] is None and a["line"].startswith("Not enough data: 1 of the 2 inquiries")


async def test_what_is_due_this_week(client):
    m, uid = await member("9800440029")
    t = now().astimezone(IST).date()
    day = lambda n: (t + timedelta(days=n)).isoformat()   # noqa: E731
    await db().leads.insert_many([
        {"_id": "dl1", "owner_id": uid, "name": "Invented Deal", "status": "new", "follow_up_on": day(2), "follow_up_note": "Send the drawings",
         "created_at": now()},
        {"_id": "dl2", "owner_id": uid, "name": "Invented Late Deal", "status": "contacted", "follow_up_on": day(-3), "created_at": now()},
        {"_id": "dl3", "owner_id": uid, "name": "Invented Won Deal", "status": "won", "follow_up_on": day(1), "created_at": now()}])
    await db().contacts.insert_many([
        {"_id": "dc1", "ws": uid, "name": "Invented Contact", "phone": "+919800033333", "email": None, "follow_up_on": day(0), "follow_up_note": ""},
        {"_id": "dc2", "ws": uid, "name": "Invented Later Contact", "phone": "+919800044444", "email": None, "follow_up_on": day(10)}])

    def due(i, rupees, on, status="due"):
        return {"_id": f"dd{i}", "ws": uid, "customer": f"Invented Customer {i}", "phone": "", "amount": rupees * 100, "due_date": on,
                "status": status, "paid_at": None}
    await db().dues.insert_many([due(1, 15000, day(3)), due(2, 5000, day(0)), due(3, 9000, day(9)), due(4, 7000, day(-2)),
                                 due(5, 8000, day(1), status="paid"), due(6, 1000, "")])
    a = await answer(m, "due")
    assert a["numbers"] == {"followups": 2, "followups_late": 1, "money_due": 20000, "money_due_n": 2, "money_late": 7000, "money_late_n": 1}
    assert {r["label"] for r in a["rows"] if r["group"] == "Follow-ups"} == {"Invented Deal", "Invented Contact"}
    assert [r["label"] for r in a["rows"] if r["group"] == "Money due"] == ["Invented Customer 2", "Invented Customer 1"]
    assert a["line"] == "In the next 7 days: 2 follow-ups and ₹20,000 due from 2 payments. Already late: 1 follow-up and ₹7,000 owed."


async def test_quick_answers_tile_on_the_dashboard_and_no_tokens(client):
    m, uid = await member("9800440030")
    d = (await m.get("/api/dashboard")).json()
    tiles = {t["key"]: t for t in d["tiles"]}
    q = tiles["quick_answers"]
    assert q["state"] == "on" and [x["key"] for x in q["data"]["questions"]] == ["stuck", "owes", "sources", "quiet", "reply", "due"]
    assert q["data"]["weekly"] is None                                   # nothing happened last week
    assert (await m.get("/api/ai-team/answers")).json()["questions"][0] == {"key": "stuck", "question": "Which deals are stuck?"}
    before = await runs_status(await user(uid))
    for key, _ in ai_team.QUESTIONS:
        a = await answer(m, key)
        assert a["key"] == key and a["tokens"] == 0
    assert (await runs_status(await user(uid)))["used"] == before["used"] and await db().runs.count_documents({"user_id": uid}) == 0
    assert (await m.get("/api/ai-team/answers/anything")).status_code == 404
    # a free owner sees the tile as a teaser, and can't open an answer
    f, _ = await member("9800440031", paid=False)
    r = await f.get("/api/dashboard")
    tiles = {t["key"]: t for t in r.json()["tiles"]} if r.status_code == 200 else {}
    assert "quick_answers" not in tiles or tiles["quick_answers"]["state"] == "teaser"
    assert (await f.get("/api/ai-team/answers/owes")).status_code == 403
