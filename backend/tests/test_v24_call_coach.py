"""v2.4 Call coach: team names, a pasted call scored (1 AI token), a recording written out, labelled, checked and scored
(2 AI tokens, nothing of the audio kept), refunds, the monthly limit, each person's trend and the team's weak spots.
Every business, person, number and call here is invented."""
from datetime import timedelta

import pytest

from app import call_coach as cc
from app import growth_core, plans
from app import transcribe as speech
from app.ai import chat_tasks
from app.ai.provider import AIError
from app.db import db, month_key, now
from tests.v24_helpers import admin_client, owner_id, v24_member

PASTED = """Customer: Hello, I want a modular kitchen for my new flat. What is the price?
Ravi: Namaste sir, this is Ravi from Shree Ganesh Interiors. Our kitchens start at ₹1,85,000.
Customer: That is too costly. Another vendor quoted less.
Ravi: I can give you 10% discount if you book today.
Customer: Okay, I'll think about it."""

SPOKEN = ("Hello, Shree Ganesh Interiors, this is Ravi speaking. Hi, I need a wardrobe for my bedroom. How much will it cost? "
          "Sir, it depends on the size. What is the size of the wall? About ten feet. "
          "Our wardrobes include soft-close hinges and a five year warranty. Shall we book a site visit on Saturday at 11? "
          "Okay, Saturday works.")
AUDIO = b"ID3FAKEAUDIO-" + bytes(range(40))


@pytest.fixture
def setting():
    """Settings are frozen; a test changes one and it is put back afterwards."""
    changed = []

    def set_(name, value):
        changed.append((name, getattr(cc.settings, name)))
        object.__setattr__(cc.settings, name, value)
    yield set_
    for name, old in reversed(changed):
        object.__setattr__(cc.settings, name, old)


async def tokens(uid: str) -> int:
    return ((await db().usage.find_one({"user_id": uid, "month": month_key()})) or {}).get("used", 0)


async def calls_used(uid: str) -> int:
    return await growth_core.used(uid, "calls_scored")


async def setup(phone: str):
    c = await v24_member(phone)
    uid = await owner_id(c)
    ravi = (await c.post("/api/call-coach/members", json={"name": "Ravi Kumar"})).json()["member"]
    return c, uid, ravi


def no_audio_anywhere(doc, path="") -> None:
    """Nothing from the recording: no bytes of any kind, and not the fake audio's marker."""
    if isinstance(doc, (bytes, bytearray, memoryview)):
        raise AssertionError(f"bytes stored at {path}")
    if isinstance(doc, dict):
        for k, v in doc.items():
            no_audio_anywhere(v, f"{path}.{k}")
    elif isinstance(doc, (list, tuple)):
        for i, v in enumerate(doc):
            no_audio_anywhere(v, f"{path}[{i}]")
    elif isinstance(doc, str):
        assert "FAKEAUDIO" not in doc, path


async def test_team_members_are_added_renamed_and_removed_and_past_calls_stay(client):
    c, uid, ravi = await setup("9800410001")
    r = await c.post("/api/call-coach/members", json={"name": "  pooja   shah "})
    assert r.status_code == 200 and r.json()["member"]["name"] == "pooja shah"
    pooja = r.json()["member"]
    assert (await c.post("/api/call-coach/members", json={"name": "RAVI KUMAR"})).status_code == 409
    assert (await c.post("/api/call-coach/members", json={"name": "12"})).status_code == 400
    page = (await c.get("/api/call-coach")).json()
    assert [m["name"] for m in page["members"]] == ["Ravi Kumar", "pooja shah"]

    # a call by Pooja, then she is renamed: her past call follows the new spelling
    paste = PASTED.replace("Ravi:", "Pooja:")
    call = (await c.post("/api/call-coach/score", json={"member_id": pooja["id"], "text": paste})).json()["call"]
    assert call["member"] == "pooja shah"
    r = await c.patch(f"/api/call-coach/members/{pooja['id']}", json={"name": "Pooja Shah"})
    assert r.status_code == 200 and r.json()["member"]["name"] == "Pooja Shah"
    assert (await c.patch(f"/api/call-coach/members/{pooja['id']}", json={"name": "ravi kumar"})).status_code == 409
    assert (await db().gym_sessions.find_one({"_id": call["id"]}))["member"] == "Pooja Shah"

    # removed: off the list and can't be picked, but her call stays under her name
    assert (await c.delete(f"/api/call-coach/members/{pooja['id']}")).status_code == 200
    assert [m["name"] for m in (await c.get("/api/call-coach")).json()["members"]] == ["Ravi Kumar"]
    assert (await c.post("/api/call-coach/score", json={"member_id": pooja["id"], "text": paste})).status_code == 404
    ppl = {p["name"]: p for p in (await c.get("/api/call-coach/people")).json()["people"]}
    assert ppl["Pooja Shah"]["removed"] and ppl["Pooja Shah"]["count"] == 1 and ppl["Ravi Kumar"]["count"] == 0
    assert (await db().gym_sessions.find_one({"_id": call["id"]}))["member"] == "Pooja Shah"

    # added back: the same person, with her history
    back = (await c.post("/api/call-coach/members", json={"name": "Pooja Shah"})).json()["member"]
    assert back["id"] == pooja["id"]
    ppl = {p["name"]: p for p in (await c.get("/api/call-coach/people")).json()["people"]}
    assert not ppl["Pooja Shah"]["removed"] and ppl["Pooja Shah"]["count"] == 1

    # another business sees none of it
    other = await v24_member("9800410002")
    assert (await other.get("/api/call-coach")).json()["members"] == []
    assert (await other.patch(f"/api/call-coach/members/{ravi['id']}", json={"name": "X Y"})).status_code == 404
    assert (await other.get(f"/api/call-coach/calls/{call['id']}")).status_code == 404


async def test_a_pasted_call_is_scored_for_the_member_for_one_ai_token(client):
    c, uid, ravi = await setup("9800410003")
    before = await tokens(uid)
    r = await c.post("/api/call-coach/score", json={"member_id": ravi["id"], "text": PASTED})
    assert r.status_code == 200, r.text
    call = r.json()["call"]
    assert await tokens(uid) == before + 1 and await calls_used(uid) == 1
    # "Ravi:" (the member's first name) is the salesperson, the customer is the customer
    assert [l["role"] for l in call["lines"]] == ["customer", "salesperson", "customer", "salesperson", "customer"]
    assert call["text"].startswith("Customer: Hello, I want") and "\nSalesperson: Namaste sir" in call["text"]
    sc = call["score"]
    assert 0 <= sc["overall"] <= 10 and len(sc["scores"]) == 4 and len(sc["fixes"]) == 3 and sc["best"]
    # each fix carries a line from the call, word for word
    assert len(sc["moments"]) == 3 and all(sc["moments"])
    for m in sc["moments"]:
        hit = next(l for l in m["lines"] if l["hit"])
        assert hit["text"] in PASTED
    # saved where the Gym and the AI team page read it
    s = await db().gym_sessions.find_one({"_id": call["id"]})
    assert s["kind"] == "call" and s["source"] == "call_coach" and s["member"] == "Ravi Kumar" and s["member_id"] == ravi["id"]
    assert s["status"] == "scored" and s["scored_at"] and s["scenario"] == "call" and s["user_id"] == uid and s["input"] == "paste"
    assert s["score"]["overall"] == sc["overall"] and all(isinstance(f, str) for f in s["score"]["fixes"])
    log = await db().agent_log.find_one({"ws": uid, "agent": "call_coach"})
    assert log["text"] == f"Scored a call by Ravi Kumar: {cc.fmt(sc['overall'])}/10" and log["ref"] == f"call:{call['id']}"
    assert await growth_core.working_now(uid) == set()
    # the call's own page, and the history
    one = (await c.get(f"/api/call-coach/calls/{call['id']}")).json()["call"]
    assert one["score"]["overall"] == sc["overall"] and one["filed"] == [] and one["text"] == call["text"]
    hist = (await c.get("/api/call-coach/calls")).json()
    assert [h["id"] for h in hist["calls"]] == [call["id"]] and not hist["more"]
    assert (await c.get(f"/api/call-coach/calls?member_id={ravi['id']}")).json()["calls"][0]["member"] == "Ravi Kumar"
    page = (await c.get("/api/call-coach")).json()
    assert page["month"]["calls"] == 1 and page["month"]["average"] == sc["overall"]
    assert page["limit"]["used"] == 1 and page["limit"]["left"] == 19 and page["costs"] == {"paste": 1, "recording": 2}
    assert page["lines"]["allowed"] == "Only upload calls you are allowed to record."
    assert page["lines"]["not_kept"] == "The recording is not kept; only the written call is saved."

    # the old Gym page still shows it, and its own score-call still works (when the Gym is switched on)
    admin = await admin_client()
    assert (await admin.patch("/api/admin/config/features/training_gym", json={"on": True, "tier": "lite"})).status_code == 200
    await plans.load(force=True)
    gym = (await c.get("/api/gym")).json()
    assert any(g["id"] == call["id"] and g["score"]["overall"] == sc["overall"] for g in gym["sessions"])
    old = await c.post("/api/gym/score-call", json={"transcript": "Customer: Too costly\nMe: I can give 20% discount\nCustomer: ok"})
    assert old.status_code == 200 and old.json()["kind"] == "call" and "source" not in old.json()


async def test_check_who_said_what_is_free_and_unclear_names_are_asked_about(client):
    c, uid, ravi = await setup("9800410004")
    before = await tokens(uid)
    r = (await c.post("/api/call-coach/read", json={"member_id": ravi["id"], "text": PASTED})).json()
    assert [l["role"] for l in r["lines"]] == ["customer", "salesperson", "customer", "salesperson", "customer"] and not r["unsure"]
    # time stamps from a chat export and unlabelled lines carry on the last speaker's turn
    stamped = "21/11/2026, 10:30 am - Customer: Is the shop open on Sunday?\n[10:31] Me: Yes, 10 to 2.\nAnd we deliver too."
    r = (await c.post("/api/call-coach/read", json={"member_id": ravi["id"], "text": stamped})).json()
    assert r["lines"] == [{"role": "customer", "text": "Is the shop open on Sunday?"}, {"role": "salesperson", "text": "Yes, 10 to 2. And we deliver too."}]
    # a name that isn't the member's, next to "Customer:", is the salesperson; next to "Me:", the customer
    r = (await c.post("/api/call-coach/read", json={"member_id": ravi["id"], "text": "Customer: Hi\nSuresh: Hello sir"})).json()
    assert [l["role"] for l in r["lines"]] == ["customer", "salesperson"]
    r = (await c.post("/api/call-coach/read", json={"member_id": ravi["id"], "text": "Me: Hello\nMr Mehta: Hi, how much?"})).json()
    assert [l["role"] for l in r["lines"]] == ["salesperson", "customer"]
    # two names, neither on the team list: a guess the owner must check — scoring asks instead of guessing
    two = "Anil: Hello, kitchens?\nMr Mehta: Yes, how much?\nAnil: It depends on the size."
    r = (await c.post("/api/call-coach/read", json={"member_id": ravi["id"], "text": two})).json()
    assert r["unsure"] and r["labels"] == ["Anil", "Mr Mehta"]
    r = await c.post("/api/call-coach/score", json={"member_id": ravi["id"], "text": two})
    assert r.status_code == 400 and "“Anil” and “Mr Mehta”" in r.json()["detail"]
    # only one side: refused before any AI token
    r = await c.post("/api/call-coach/score", json={"member_id": ravi["id"], "text": "Me: Hello\nMe: Anyone there?"})
    assert r.status_code == 400 and "customer's" in r.json()["detail"]
    assert await tokens(uid) == before and await calls_used(uid) == 0
    # the checked lines are scored as they are
    lines = [{"role": "salesperson", "text": "Hello, kitchens?"}, {"role": "customer", "text": "Yes, how much?"},
             {"role": "salesperson", "text": "It depends on the size."}]
    r = await c.post("/api/call-coach/score", json={"member_id": ravi["id"], "lines": lines})
    assert r.status_code == 200 and r.json()["call"]["input"] == "paste" and await tokens(uid) == before + 1


async def test_a_recording_is_written_out_labelled_checked_and_scored_for_two_tokens(client, monkeypatch):
    c, uid, ravi = await setup("9800410005")
    heard = {}

    async def fake_transcribe(data, filename, content_type):
        heard.update(data=data, filename=filename, content_type=content_type)
        return SPOKEN
    monkeypatch.setattr(speech, "transcribe", fake_transcribe)
    before = await tokens(uid)
    r = await c.post(f"/api/call-coach/recording?member_id={ravi['id']}", content=AUDIO, headers={"content-type": "audio/mpeg"})
    assert r.status_code == 200, r.text
    draft = r.json()["draft"]
    assert heard == {"data": AUDIO, "filename": "call.mp3", "content_type": "audio/mpeg"}
    assert await tokens(uid) == before + 1 and await calls_used(uid) == 0
    lines = draft["lines"]
    assert len(lines) == 9 and lines[0] == {"role": "salesperson", "text": "Hello, Shree Ganesh Interiors, this is Ravi speaking."}
    assert lines[1]["role"] == "customer" and lines[2]["role"] == "customer" and lines[7]["role"] == "salesperson"
    assert draft["member"] == "Ravi Kumar" and draft["labelled_by"] == "rules"
    # nothing of the recording is kept anywhere: no file, no bytes, only the written call
    assert await db().files.count_documents({}) == 0
    for name in await db().list_collection_names():
        async for doc in db()[name].find({}):
            no_audio_anywhere(doc, name)
    saved = await db().call_drafts.find_one({"_id": draft["id"]})
    assert set(saved) == {"_id", "ws", "member_id", "member", "lines", "labelled_by", "at", "by"}
    # waiting on the page, and opened again
    assert (await c.get("/api/call-coach")).json()["drafts"][0]["id"] == draft["id"]
    assert (await c.get(f"/api/call-coach/drafts/{draft['id']}")).json()["draft"]["lines"] == lines

    # the owner fixes two labels and a word, then scores
    lines[4]["role"], lines[5]["role"] = "salesperson", "customer"
    lines[5]["text"] = "About ten feet wide."
    r = await c.post("/api/call-coach/score", json={"member_id": ravi["id"], "lines": lines, "draft_id": draft["id"]})
    assert r.status_code == 200, r.text
    call = r.json()["call"]
    assert call["input"] == "recording" and await tokens(uid) == before + 2 and await calls_used(uid) == 1
    assert "Customer: About ten feet wide." in call["text"] and "Salesperson: Sir, it depends on the size. What is the size of the wall?" in call["text"]
    assert await db().call_drafts.count_documents({}) == 0
    s = await db().gym_sessions.find_one({"_id": call["id"]})
    assert s["input"] == "recording" and s["source"] == "call_coach"
    no_audio_anywhere(s)
    # scored once only
    assert (await c.post("/api/call-coach/score", json={"member_id": ravi["id"], "lines": lines, "draft_id": draft["id"]})).status_code == 404


async def test_recordings_are_checked_before_any_token(client, monkeypatch, setting):
    c, uid, ravi = await setup("9800410006")
    called = []

    async def fake_transcribe(data, filename, content_type):
        called.append(content_type)
        return "   "
    monkeypatch.setattr(speech, "transcribe", fake_transcribe)
    before = await tokens(uid)
    url = f"/api/call-coach/recording?member_id={ravi['id']}"
    assert (await c.post(url, content=AUDIO, headers={"content-type": "image/png"})).status_code == 415
    assert (await c.post(url, content=b"", headers={"content-type": "audio/mpeg"})).status_code == 400
    assert (await c.post("/api/call-coach/recording?member_id=nobody", content=AUDIO, headers={"content-type": "audio/mpeg"})).status_code == 404
    real_max = cc.max_bytes
    monkeypatch.setattr(cc, "max_bytes", lambda: 20)
    r = await c.post(url, content=AUDIO, headers={"content-type": "audio/mpeg"})
    assert r.status_code == 413 and "about 15 minutes" in r.json()["detail"]
    monkeypatch.setattr(cc, "max_bytes", real_max)
    assert called == []
    # a file sent without a type is read by its ending; silence gives the token back
    r = await c.post(url + "&ext=m4a", content=AUDIO, headers={"content-type": "application/octet-stream"})
    assert r.status_code == 400 and "couldn't hear" in r.json()["detail"] and called == ["audio/mp4"]
    assert await tokens(uid) == before
    # the 25 MB limit, and 13 MB when the only speech engine takes the file inside the request
    assert cc.max_bytes() == 25 * cc.MB
    setting("openai_api_key", "")
    setting("gemini_api_key", "test-key-not-real")
    assert cc.max_bytes() == 13 * cc.MB


async def test_a_failed_ai_job_gives_back_the_tokens_and_the_call(client, monkeypatch):
    c, uid, ravi = await setup("9800410007")
    before = await tokens(uid)

    async def thin(system, prompt, mock, max_tokens=900):
        return {"scores": [], "fixes": []}, None
    monkeypatch.setattr(chat_tasks, "complete", thin)
    r = await c.post("/api/call-coach/score", json={"member_id": ravi["id"], "text": PASTED})
    assert r.status_code == 502 and "not used" in r.json()["detail"]

    async def down(system, prompt, mock, max_tokens=900):
        raise AIError("engine down")
    monkeypatch.setattr(chat_tasks, "complete", down)
    assert (await c.post("/api/call-coach/score", json={"member_id": ravi["id"], "text": PASTED})).status_code == 502
    assert await tokens(uid) == before and await calls_used(uid) == 0
    assert await db().gym_sessions.count_documents({}) == 0 and await db().agent_log.count_documents({"agent": "call_coach"}) == 0
    assert await growth_core.working_now(uid) == set()

    # the speech engine failing gives the write-out token back
    async def broken(data, filename, content_type):
        raise speech.TranscribeError("speech engine 500")
    monkeypatch.setattr(speech, "transcribe", broken)
    r = await c.post(f"/api/call-coach/recording?member_id={ravi['id']}", content=AUDIO, headers={"content-type": "audio/ogg"})
    assert r.status_code == 502 and "AI token was not used" in r.json()["detail"]
    assert await tokens(uid) == before and await db().call_drafts.count_documents({}) == 0

    # labels the AI got wrong (one short): the call is still written out, labelled by the simple rules
    async def heard(data, filename, content_type):
        return SPOKEN
    monkeypatch.setattr(speech, "transcribe", heard)
    from app.ai import callcoach_tasks

    async def short(system, prompt, mock, max_tokens=900):
        return {"who": ["S", "C"]}, None
    monkeypatch.setattr(callcoach_tasks, "complete", short)
    r = await c.post(f"/api/call-coach/recording?member_id={ravi['id']}", content=AUDIO, headers={"content-type": "audio/ogg"})
    assert r.status_code == 200 and r.json()["draft"]["labelled_by"] == "rules" and len(r.json()["draft"]["lines"]) == 9
    assert await tokens(uid) == before + 1


async def test_the_21st_call_in_a_month_is_refused(client, monkeypatch):
    c, uid, ravi = await setup("9800410008")
    user = await db().users.find_one({"_id": uid})
    for _ in range(19):
        await growth_core.take(user, "calls_scored")
    assert (await c.post("/api/call-coach/score", json={"member_id": ravi["id"], "text": PASTED})).status_code == 200
    assert await calls_used(uid) == 20
    before = await tokens(uid)
    r = await c.post("/api/call-coach/score", json={"member_id": ravi["id"], "text": PASTED})
    assert r.status_code == 429 and "20 calls scored" in r.json()["detail"]
    called = []

    async def fake_transcribe(data, filename, content_type):
        called.append(1)
        return SPOKEN
    monkeypatch.setattr(speech, "transcribe", fake_transcribe)
    r = await c.post(f"/api/call-coach/recording?member_id={ravi['id']}", content=AUDIO, headers={"content-type": "audio/mpeg"})
    assert r.status_code == 429 and called == []
    assert await tokens(uid) == before and await calls_used(uid) == 20
    assert (await c.get("/api/call-coach")).json()["limit"]["left"] == 0


async def test_each_persons_trend_and_the_teams_weak_spots_this_month(client):
    c, uid, ravi = await setup("9800410009")
    pooja = (await c.post("/api/call-coach/members", json={"name": "Pooja"})).json()["member"]

    def call(member, overall, days_ago, next_step=3, fixes=("End every call with a clear next step and a date.",)):
        at = now() - timedelta(days=days_ago, minutes=1)
        return {"_id": f"t{member['id'][:6]}{days_ago}", "ws": uid, "kind": "call", "source": "call_coach", "status": "scored",
                "member": member["name"], "member_id": member["id"], "scored_at": at, "created_at": at, "messages": [],
                "score": {"overall": overall, "best": "", "fixes": list(fixes),
                          "scores": [{"criterion": "Clear next step", "score": next_step, "note": ""},
                                     {"criterion": "Listening and questions", "score": 8, "note": ""}]}}
    # Ravi: 8 calls, newest first 8, 8, 7, 7, 8 · then 5, 6, 4
    for i, o in enumerate([8, 8, 7, 7, 8, 5, 6, 4]):
        await db().gym_sessions.insert_one(call(ravi, o, i))
    await db().gym_sessions.insert_one(call(pooja, 6, 0, next_step=6, fixes=("Ask at least three questions first.",)))
    # an old Gym role-play and someone else's call don't count
    await db().gym_sessions.insert_one({"_id": "roleplay", "ws": uid, "kind": "role-play", "status": "scored", "scored_at": now(),
                                        "score": {"overall": 1, "scores": [], "fixes": []}})
    ppl = (await c.get("/api/call-coach/people")).json()
    by = {p["name"]: p for p in ppl["people"]}
    r = by["Ravi Kumar"]
    assert r["count"] == 8 and r["last5"] == 7.6 and r["last_n"] == 5 and r["prev5"] == 5.0 and r["prev_n"] == 3 and r["trend"] == "up"
    assert [x["overall"] for x in r["calls"]] == [8, 8, 7, 7, 8, 5, 6, 4]
    p = by["Pooja"]
    assert p["count"] == 1 and p["last5"] == 6 and p["prev5"] is None and p["trend"] is None
    # this month (calls up to 7 days back fall in this month or last; count only those since the 1st)
    start = cc.month_start()
    this_month = [o for i, o in enumerate([8, 8, 7, 7, 8, 5, 6, 4]) if now() - timedelta(days=i, minutes=1) >= start] + [6]
    team = ppl["team"]
    assert team["calls"] == len(this_month) and team["average"] == round(sum(this_month) / len(this_month), 1)
    rows = team["criteria"]
    assert rows[0]["criterion"] == "Clear next step" and rows[1]["criterion"] == "Listening and questions"
    ravi_now = len(this_month) - 1
    assert rows[0]["fixes"] == ravi_now and rows[1]["fixes"] == 1 and rows[1]["average"] == 8
    assert rows[0]["average"] == round((3 * ravi_now + 6) / len(this_month), 1)


async def test_recordings_without_a_speech_engine_say_so_and_pasted_calls_still_work(client, monkeypatch):
    c, uid, ravi = await setup("9800410010")
    monkeypatch.setattr(speech, "available", lambda: False)
    page = (await c.get("/api/call-coach")).json()
    assert page["recording"]["on"] is False and "server setting" in page["recording"]["note"]
    r = await c.post(f"/api/call-coach/recording?member_id={ravi['id']}", content=AUDIO, headers={"content-type": "audio/mpeg"})
    assert r.status_code == 503 and "server setting" in r.json()["detail"] and "Pasted calls still work" in r.json()["detail"]
    assert "OpenAI" not in r.json()["detail"] and "Gemini" not in r.json()["detail"]
    assert (await c.post("/api/call-coach/score", json={"member_id": ravi["id"], "text": PASTED})).status_code == 200


async def test_free_owners_wait_for_membership(client):
    f = await v24_member("9800410011", paid=False)
    for method, url, body in (("get", "/api/call-coach", None), ("post", "/api/call-coach/members", {"name": "Ravi"}),
                              ("post", "/api/call-coach/score", {"member_id": "x", "text": PASTED}), ("get", "/api/call-coach/people", None)):
        r = await (f.get(url) if method == "get" else f.post(url, json=body))
        assert r.status_code == 403, url
    r = await f.post("/api/call-coach/recording?member_id=x", content=AUDIO, headers={"content-type": "audio/mpeg"})
    assert r.status_code == 403
    assert await db().gym_sessions.count_documents({}) == 0
