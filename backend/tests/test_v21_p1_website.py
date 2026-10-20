"""v2.1 P1 — 3.3 Website with lead magnet: edit the website by prompt (preview, Apply / Discard / Undo, new facts go to the
Knowledge Hub first) and uploaded lead magnets whose PDF downloads only after the form is filled. Every name, phone and
email here is invented."""
import io
import time

import httpx
from PIL import Image

from app import integrations
from app.db import db
from app.routers import sitemagnets
from tests.conftest import PROFILE, go_live, new_owner

ADMIN = "9999900000"
PDF = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"


async def owner_on(phone: str, plan: str = "program", profile: dict | None = None):
    c = await new_owner(phone)
    site = await go_live(c, profile)
    uid = (await c.get("/api/me")).json()["user"]["id"]
    if plan != "free":
        admin = await new_owner(ADMIN)
        assert (await admin.patch(f"/api/admin/users/{uid}", json={"plan": plan})).status_code == 200
    return c, uid, site


async def upload(c, title="Kitchen planning checklist", description="12 things to decide before you order", data=PDF, cover=None, name="checklist.pdf"):
    files = {"file": (name, data, "application/pdf")}
    if cover:
        files["cover"] = ("cover.png", cover, "image/png")
    return await c.post("/api/lead-magnets", data={"title": title, "description": description}, files=files)


# ───────────────────────── edit by prompt ─────────────────────────
async def test_a_prompt_edit_changes_only_its_section_and_discard_keeps_the_page(client):
    c, uid, site = await owner_on("9852000001", "free")
    before = (await c.get("/api/site")).json()["content"]
    r = await c.post("/api/site/prompt", json={"instruction": "Add a section about our 15 years in the trade"})
    assert r.status_code == 200, r.text
    pv = r.json()
    assert pv["changed"] == ["about"] and set(pv["after"]) == {"about"} and pv["before"]["about"] == before["about"]
    assert "15 years" not in pv["after"]["about"]                  # not in the Brain, so not on the page
    assert pv["new_facts"] == ["15 years in the trade"]
    assert (await c.get("/api/site")).json()["content"] == before   # a preview changes nothing
    assert (await c.get("/api/site/prompt")).json()["preview"]["changed"] == ["about"]
    await c.post("/api/site/prompt/discard")
    assert (await c.get("/api/site")).json()["content"] == before
    assert (await c.get("/api/site/prompt")).json()["preview"] is None
    assert (await c.post("/api/site/prompt/apply")).status_code == 400


async def test_apply_changes_only_that_section_and_undo_restores_it(client):
    c, uid, site = await owner_on("9852000002", "free")
    before = (await c.get("/api/site")).json()["content"]
    await c.post("/api/site/prompt", json={"instruction": "Make the about text warmer"})
    after = (await c.post("/api/site/prompt/apply")).json()["content"]
    assert after["about"] != before["about"] and {k: v for k, v in after.items() if k != "about"} == {k: v for k, v in before.items() if k != "about"}
    page = (await client.get(f"/s/{site['slug']}")).text
    assert "What our customers value most" in page
    # an applied prompt edit is the owner's edit: a Knowledge Hub refresh keeps it (banner, not overwrite)
    s = await db().sites.find_one({"owner_id": uid})
    assert s["content"] != s["generated_content"]
    assert (await c.get("/api/site/prompt")).json()["can_undo"] is True
    # a hand edit elsewhere since the apply survives Undo; only the prompt's own section goes back
    s2 = (await c.get("/api/site")).json()
    assert (await c.put("/api/site", json={"content": {**s2["content"], "cta": "Book a visit"}, "accent": s2["accent"]})).status_code == 200
    undone = (await c.post("/api/site/prompt/undo")).json()["content"]
    assert undone == {**before, "cta": "Book a visit"}
    assert (await c.post("/api/site/prompt/undo")).status_code == 400


async def test_undo_refuses_when_the_owner_changed_that_section_since(client):
    c, uid, site = await owner_on("9852000007", "free")
    await c.post("/api/site/prompt", json={"instruction": "Make the about text warmer"})
    applied = (await c.post("/api/site/prompt/apply")).json()
    await c.put("/api/site", json={"content": {**applied["content"], "about": "Our own words, typed by hand."}, "accent": applied["accent"]})
    r = await c.post("/api/site/prompt/undo")
    assert r.status_code == 409 and "About us" in r.json()["detail"]
    assert (await c.get("/api/site")).json()["content"]["about"] == "Our own words, typed by hand."


async def test_a_stale_preview_isnt_applied_over_newer_edits(client):
    c, uid, site = await owner_on("9852000003", "free")
    await c.post("/api/site/prompt", json={"instruction": "Change the headline to sound more premium"})
    s = (await c.get("/api/site")).json()
    assert (await c.put("/api/site", json={"content": {**s["content"], "cta": "Book a visit"}, "accent": s["accent"]})).status_code == 200
    assert (await c.post("/api/site/prompt/apply")).status_code == 409
    assert (await c.get("/api/site")).json()["content"]["cta"] == "Book a visit"


async def test_numbers_not_in_the_brain_never_reach_the_page(client, monkeypatch):
    from app.ai import site_tasks
    c, uid, site = await owner_on("9852000004", "free")

    async def eager(p, content, instruction):   # an AI that ignores the rule, and claims its own numbers as "new facts"
        return {"patch": {"about": "Over 500 kitchens delivered since 2009.", "cta": "Get a free quote",
                          "nope": "x", "faq": [{"q": "Price?", "a": "Kitchens from ₹1,85,000 onwards."}]},
                "new_facts": ["500 kitchens delivered", "since 2009"], "notes": []}, None
    monkeypatch.setattr(site_tasks, "site_patch", eager)
    pv = (await c.post("/api/site/prompt", json={"instruction": "Make it more premium"})).json()
    assert "about" not in pv["changed"] and "nope" not in pv["changed"]
    assert "cta" in pv["changed"] and "faq" in pv["changed"]        # ₹1,85,000 is the Brain's own price
    # the owner never said 500 or 2009: nothing is offered for the Knowledge Hub, and the preview says a number was left out
    assert pv["new_facts"] == [] and pv["invented_left_out"] is True
    assert pv["left_out"] == [{"section": "about", "reason": "invented", "numbers": ["2009", "500"]}]


async def test_only_the_owners_own_new_facts_are_offered(client, monkeypatch):
    from app.ai import site_tasks
    c, uid, site = await owner_on("9852000006", "free")

    async def writes_it(p, content, instruction):
        return {"patch": {"about": "We have delivered 500 kitchens across Thane, with 12 carpenters."}, "new_facts": [], "notes": []}, None
    monkeypatch.setattr(site_tasks, "site_patch", writes_it)
    pv = (await c.post("/api/site/prompt", json={"instruction": "Say we have delivered 500 kitchens"})).json()
    assert pv["changed"] == [] and pv["new_facts"] == ["500 kitchens"]
    assert pv["left_out"] == [{"section": "about", "reason": "invented", "numbers": ["12"]}]   # 12 was the AI's: left out, not offered
    assert pv["invented_left_out"] is True


async def test_a_new_fact_goes_into_the_knowledge_hub(client):
    c, uid, site = await owner_on("9852000005", "free")
    r = await c.post("/api/knowledge/facts", json={"text": "15 years in the trade"})
    assert r.status_code == 200 and "15 years in the trade" in r.json()["proof"]
    await c.post("/api/knowledge/facts", json={"text": "15 years in the trade."})          # once only
    assert (await db().businesses.find_one({"owner_id": uid}))["proof"].count("15 years") == 1
    pv = (await c.post("/api/site/prompt", json={"instruction": "Add a section about our 15 years in the trade"})).json()
    assert "15 years in the trade" in pv["after"]["about"] and pv["new_facts"] == []
    from app import knowledge
    await knowledge.settle()


# ───────────────────────── uploaded lead magnets ─────────────────────────
async def test_an_uploaded_pdf_downloads_only_after_the_form(client):
    c, uid, site = await owner_on("9852000010", "program")
    r = await upload(c)
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["slug"] == "kitchen-planning-checklist" and m["published"] and m["cover_generated"] and m["url"].endswith("/guide/kitchen-planning-checklist")
    cover = await c.get(m["cover_url"])
    assert cover.status_code == 200 and Image.open(io.BytesIO(cover.content)).size == (1080, 1080)
    page = f"/s/{site['slug']}/guide/{m['slug']}"
    form = await client.get(page)
    assert form.status_code == 200 and "Kitchen planning checklist" in form.text and 'name="email"' in form.text

    # no link, a forged link and an expired link: no PDF, back to the form
    for t in ("", "123.abc", sitemagnets.download_token("other-magnet")):
        r = await client.get(f"{page}/file", params={"t": t} if t else None, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith(page) and r.headers.get("content-type", "") != "application/pdf"
    exp = int(time.time()) - 10
    r = await client.get(f"{page}/file", params={"t": f"{exp}.{sitemagnets._sig(m['id'], exp)}"}, follow_redirects=False)
    assert r.status_code == 303 and "err=expired" in r.headers["location"]

    # the bot that fills the hidden field gets nothing
    r = await client.post(page, data={"name": "Bot", "phone": "98520 00099", "website": "spam"}, follow_redirects=False)
    assert r.status_code == 303 and not await db().leads.count_documents({"owner_id": uid})
    assert (await client.post(page, data={"name": "Neha Joshi", "phone": "123"}, follow_redirects=False)).headers["location"].endswith("err=phone#get")
    assert (await client.post(page, data={"name": "Neha Joshi", "phone": "98520 00011", "email": "not-an-email"},
                              follow_redirects=False)).headers["location"].endswith("err=email#get")

    done = await client.post(page, data={"name": "Neha Joshi", "phone": "98520 00011", "email": "neha@example.com"})
    assert done.status_code == 200 and "Download the PDF" in done.text and "emailed the link to neha@example.com" in done.text
    link = next(x for x in done.text.split('"') if "/file?t=" in x)
    pdf = await client.get(link.replace("&amp;", "&"))
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf" and pdf.content == PDF
    assert "attachment" in pdf.headers["content-disposition"]

    lead = await db().leads.find_one({"owner_id": uid})
    assert lead["source"] == "lead_magnet" and lead["magnet_id"] == m["id"] and lead["email"] == "neha@example.com" and lead["stage"] == "identification"
    owner_phone = (await db().users.find_one({"_id": uid}))["phone"]
    alert = await db().outbox.find_one({"channel": "whatsapp", "kind": "lead_alert"})
    assert alert and alert["to"] == owner_phone                                  # the owner's alert; never a WhatsApp to the visitor
    assert not await db().outbox.count_documents({"channel": "whatsapp", "to": lead["phone"]})
    mail = await db().outbox.find_one({"channel": "email", "kind": "lead_magnet"})
    assert mail["to"] == "neha@example.com" and "/file?t=" in mail["text"]
    mine = (await c.get("/api/lead-magnets")).json()["items"][0]
    assert mine["downloads"] == 1 and mine["leads"] == 1
    leads = (await c.get("/api/leads")).json()
    assert any(x.get("source") == "lead_magnet" for x in (leads if isinstance(leads, list) else leads.get("items", [])))


async def test_lead_magnet_limits_types_and_switches(client):
    c, uid, site = await owner_on("9852000011", "program")
    assert (await upload(c, data=b"\x89PNG\r\n\x1a\nnot a pdf", name="fake.pdf")).status_code == 400
    assert (await upload(c, title="ab")).status_code == 400
    png = io.BytesIO()
    Image.new("RGB", (400, 400), "#1F4E79").save(png, "PNG")
    first = (await upload(c, cover=png.getvalue())).json()
    assert first["cover_generated"] is False
    for i in range(4):
        assert (await upload(c, title="Kitchen planning checklist")).status_code == 200
    r = await upload(c, title="One too many")
    assert r.status_code == 400 and "5" in r.json()["detail"]
    slugs = [m["slug"] for m in (await c.get("/api/lead-magnets")).json()["items"]]
    assert len(set(slugs)) == 5
    # unpublished: the public page and the file are gone
    assert (await c.patch(f"/api/lead-magnets/{first['id']}", json={"published": False})).json()["published"] is False
    assert (await client.get(f"/s/{site['slug']}/guide/{first['slug']}")).status_code == 404
    token = sitemagnets.download_token(first["id"])
    assert (await client.get(f"/s/{site['slug']}/guide/{first['slug']}/file", params={"t": token})).status_code == 404
    assert (await c.delete(f"/api/lead-magnets/{first['id']}")).json() == {"ok": True}
    assert not await db().files.find_one({"_id": first["file_id"]})
    # another owner can't touch it
    other, _, _ = await owner_on("9852000012", "program")
    second = (await c.get("/api/lead-magnets")).json()["items"][0]
    assert (await other.patch(f"/api/lead-magnets/{second['id']}", json={"published": False})).status_code == 404
    assert (await other.get(f"/api/lead-magnets/{second['id']}/file")).status_code == 404
    # below the Action Program it's locked
    free, _, _ = await owner_on("9852000013", "free")
    assert (await upload(free)).status_code == 403


async def test_guide_page_offers_uploads_and_the_ai_guide(client):
    c, uid, site = await owner_on("9852000014", "program")
    m = (await upload(c)).json()
    r = await client.get(f"/s/{site['slug']}/guide", follow_redirects=False)          # no AI guide yet: /guide opens the upload
    assert r.status_code == 303 and r.headers["location"].endswith(f"/guide/{m['slug']}")
    await c.post("/api/lead-magnet/generate", json={"problem": "Choosing a modular kitchen", "format": "checklist"})
    await c.post("/api/lead-magnet/publish", json={"published": True})
    page = await client.get(f"/s/{site['slug']}/guide")
    assert page.status_code == 200 and "More free downloads" in page.text and f"/guide/{m['slug']}" in page.text


async def test_a_lead_magnet_lead_reaches_employz_when_crm_sync_is_on(client, monkeypatch):
    from tests.test_v2_running import FakeEmployz
    fake = FakeEmployz()
    monkeypatch.setattr(integrations, "TRANSPORT", httpx.MockTransport(fake))
    c, uid, site = await owner_on("9852000015", "running")
    admin = await new_owner(ADMIN)
    stages = {f"stage_{k}": f"st_{k}" for k in ("identification", "logic", "pain", "vision", "close")}
    r = await admin.put(f"/api/admin/owners/{uid}/connections/employz",
                        json={"values": {"location_id": "loc_9", "token": "pit-secret", "pipeline_id": "pipe_1", **stages}})
    assert r.status_code == 200, r.text
    m = (await upload(c)).json()
    await client.post(f"/s/{site['slug']}/guide/{m['slug']}", data={"name": "Kavita Rao", "phone": "98520 00021"})
    lead = await db().leads.find_one({"owner_id": uid})
    assert lead["source"] == "lead_magnet" and lead["crm"]["contact_id"] == "ct_1" and lead["crm"]["opportunity_id"] == "op_1"
    import json
    assert json.loads(fake.calls[0].content)["source"] == "Action Hub · lead_magnet"


async def test_lead_magnet_pages_work_on_the_websites_own_address(client):
    """name.employz.ai/guide/<magnet>, its download and its cover answer on the website's own host."""
    from app.config import settings
    old = settings.sites_domain
    object.__setattr__(settings, "sites_domain", "employz.ai")
    try:
        c, uid, site = await owner_on("9852000019", "program")
        m = (await upload(c)).json()
        assert m["url"] == f"https://{site['slug']}.employz.ai/guide/{m['slug']}"
        host = f"https://{site['slug']}.employz.ai"
        assert (await client.get(f"{host}/guide/{m['slug']}")).status_code == 200
        assert (await client.get(f"{host}/guide/{m['slug']}/cover.png")).status_code == 200
        r = await client.get(f"{host}/guide/{m['slug']}/file", follow_redirects=False)
        assert r.status_code == 303 and r.headers.get("content-type", "") != "application/pdf"
        assert (await client.get(f"{host}/guide/{m['slug']}/../../api/me")).status_code == 404
    finally:
        object.__setattr__(settings, "sites_domain", old)


async def test_form_fills_and_downloads_are_counted_separately(client):
    c, uid, site = await owner_on("9852000016", "program")
    m = (await upload(c)).json()
    page = f"/s/{site['slug']}/guide/{m['slug']}"
    done = await client.post(page, data={"name": "Neha Joshi", "phone": "98520 00031"})
    mine = (await c.get("/api/lead-magnets")).json()["items"][0]
    assert mine["form_fills"] == 1 and mine["downloads"] == 0           # filled the form, didn't download yet
    link = next(x for x in done.text.split('"') if "/file?t=" in x)
    await client.get(link.replace("&amp;", "&"))
    await client.get(link.replace("&amp;", "&"))
    mine = (await c.get("/api/lead-magnets")).json()["items"][0]
    assert mine["form_fills"] == 1 and mine["downloads"] == 2


async def test_two_uploads_at_once_never_make_a_sixth_and_failures_leave_no_files(client):
    import asyncio
    c, uid, site = await owner_on("9852000017", "program")
    for i in range(4):
        assert (await upload(c, title=f"Checklist number {i}")).status_code == 200
    stored = await db().files.count_documents({"owner_id": uid})
    a, b = await asyncio.gather(upload(c, title="Fifth one"), upload(c, title="Sixth one"))
    assert sorted([a.status_code, b.status_code]) == [200, 400]
    assert await db().magnet_files.count_documents({"owner_id": uid}) == 5
    assert await db().files.count_documents({"owner_id": uid}) == stored + 2          # the refused upload's PDF and cover are gone
    # the race itself, made certain: another upload lands after this one's check but before its insert
    await c.delete(f"/api/lead-magnets/{(await c.get('/api/lead-magnets')).json()['items'][0]['id']}")
    from app.db import now as _now
    real, sneaked = sitemagnets._unique_slug, {"done": False}

    async def sneaky(ws, title):
        if not sneaked["done"]:
            sneaked["done"] = True
            from datetime import timedelta
            await db().magnet_files.insert_one({"_id": "sneak", "owner_id": ws, "slug": "sneak", "title": "Sneak",
                                                "created_at": _now() - timedelta(seconds=1)})   # it landed first
        return await real(ws, title)
    import pytest
    mp = pytest.MonkeyPatch()
    mp.setattr(sitemagnets, "_unique_slug", sneaky)
    files_before = await db().files.count_documents({"owner_id": uid})
    r = await upload(c, title="Late one")
    mp.undo()
    assert r.status_code == 400 and await db().magnet_files.count_documents({"owner_id": uid}) == 5
    assert not await db().magnet_files.find_one({"owner_id": uid, "title": "Late one"})
    assert await db().files.count_documents({"owner_id": uid}) == files_before
    await db().magnet_files.delete_one({"_id": "sneak"})
    # a broken cover: refused, and its PDF isn't left behind
    await c.delete(f"/api/lead-magnets/{(await c.get('/api/lead-magnets')).json()['items'][0]['id']}")
    before = await db().files.count_documents({"owner_id": uid})
    r = await upload(c, title="Bad cover", cover=b"\x89PNG\r\n\x1a\nbroken")
    assert r.status_code == 400 and await db().files.count_documents({"owner_id": uid}) == before


async def test_oversized_requests_are_refused_before_reading(client, monkeypatch):
    import dataclasses
    from app.config import settings
    c, uid, site = await owner_on("9852000018", "program")
    m = (await upload(c)).json()
    big = await client.post(f"/s/{site['slug']}/guide/{m['slug']}", data={"name": "Neha Joshi", "phone": "98520 00041", "message": "x" * 25000})
    assert big.status_code == 413 and not await db().leads.count_documents({"owner_id": uid})
    monkeypatch.setattr(sitemagnets, "settings", dataclasses.replace(settings, upload_max_mb=0))    # limit: 0 + 1 MB
    r = await upload(c, title="Too big", data=PDF + b"0" * (1024 * 1024 + 10))
    assert r.status_code == 413 and await db().magnet_files.count_documents({"owner_id": uid}) == 1
