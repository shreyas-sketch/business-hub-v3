"""Fixes from the 28 Oct audit: the CRM (emails, notes, stage ids), the SOP overview, GST month warnings, agent names,
WhatsApp messages sent from the owner's own phone, and ₹0 quotations. Every business, person and number here is invented."""
from datetime import date

from app.db import db, now
from app.routers import gstsetaside
from app.services import new_id
from tests.v23_helpers import owner_id, v23_member


async def test_a_bad_email_on_a_new_opportunity_is_refused_not_dropped(client):
    c = await v23_member("9800930001")
    r = await c.post("/api/crm/deals", json={"name": "Ravi Shah", "phone": "98200 93001", "email": "ravi@@example"})
    assert r.status_code == 400 and "email address doesn't look right" in r.text
    assert await db().leads.count_documents({"name": "Ravi Shah"}) == 0
    ok = (await c.post("/api/crm/deals", json={"name": "Ravi Shah", "phone": "98200 93001", "email": " Ravi@Example.in "})).json()
    assert ok["email"] == "ravi@example.in"
    assert (await c.post("/api/crm/deals", json={"name": "Meena Iyer", "phone": "98200 93002", "email": ""})).status_code == 200


async def test_new_stages_with_look_alike_names_get_their_own_ids(client):
    c = await v23_member("9800930002")
    stages = (await c.get("/api/crm/pipeline")).json()["stages"]
    body = {"stages": [{"id": s["id"], "name": s["name"], "ff": s["ff"], "hint": ""} for s in stages]
            + [{"name": "Quote Sent", "ff": "pain", "hint": ""}, {"name": "Quote-Sent", "ff": "pain", "hint": ""},
               {"name": "quote sent!", "ff": "vision", "hint": ""}], "lost_reasons": []}
    r = await c.put("/api/crm/pipeline/stages", json=body)
    assert r.status_code == 200, r.text
    ids = [s["id"] for s in r.json()["stages"]]
    assert len(ids) == len(set(ids)) and ids[-3:] == ["quote-sent", "quote-sent-2", "quote-sent-3"]
    # saving again keeps every id as it is
    again = (await c.put("/api/crm/pipeline/stages", json={"stages": r.json()["stages"], "lost_reasons": []})).json()
    assert [s["id"] for s in again["stages"]] == ids


async def test_a_note_on_an_opportunity_without_phone_or_email_is_kept(client):
    c = await v23_member("9800930003")
    d = (await c.post("/api/crm/deals", json={"name": "Walk-in customer"})).json()
    assert d["contact"] is None
    r = await c.patch(f"/api/crm/deals/{d['id']}", json={"note": "Wants a quote by Friday"})
    assert r.status_code == 200
    notes = [a["text"] for a in r.json()["activities"] if a["kind"] == "note"]
    assert notes == ["Wants a quote by Friday"]
    # with a contact, the note is on the contact's history too, as before
    d2 = (await c.post("/api/crm/deals", json={"name": "Asha Rao", "phone": "98200 93003"})).json()
    await c.patch(f"/api/crm/deals/{d2['id']}", json={"note": "Called back"})
    contact = (await c.get(f"/api/crm/contacts/{d2['contact']['id']}")).json()
    assert any(a["text"] == "Called back" for a in contact["activities"])


async def test_a_deleted_sop_is_no_longer_written_in_the_overview(client):
    c = await v23_member("9800930004")
    ws = await owner_id(c)
    await db().kits.insert_many([{"_id": "sop-a", "ws": ws, "kind": "sop", "title": "Dispatch", "content": {}, "created_at": now(), "updated_at": now()},
                                 {"_id": "sop-b", "ws": ws, "kind": "sop", "title": "Billing", "content": {}, "created_at": now(), "updated_at": now()}])
    await db().sop_maps.insert_one({"_id": ws, "generated_at": now(), "processes": [
        {"key": "dispatch", "function": "Operations/Delivery", "title": "Dispatch", "selected": True, "sop_id": "sop-a", "status": "written"},
        {"key": "billing", "function": "Finance", "title": "Billing", "selected": True, "sop_id": "sop-b", "status": "written"},
        {"key": "orphan", "function": "Finance", "title": "Payroll", "selected": True, "sop_id": "deleted-long-ago", "status": "written"}]})
    assert (await c.delete("/api/kits/sop/sop-a")).status_code == 200
    v = (await c.get("/api/sop-set")).json()
    by = {p["key"]: p for p in v["processes"]}
    assert by["dispatch"]["sop_id"] is None and by["dispatch"]["status"] is None
    assert by["orphan"]["sop_id"] is None                      # deleted before this fix: put right too
    assert by["billing"]["sop_id"] == "sop-b" and by["billing"]["status"] == "written"
    assert v["without_sop"] == 2                               # they can be written again
    stored = {p["key"]: p for p in (await db().sop_maps.find_one({"_id": ws}))["processes"]}
    assert stored["dispatch"]["sop_id"] is None and stored["billing"]["sop_id"] == "sop-b"


async def test_a_short_statement_warns_only_about_its_own_month(client):
    periods = gstsetaside._periods(date(2026, 10, 28))
    await db().bank_months.insert_one({"_id": "w-gst:-:2026-09", "ws": "w-gst", "period": "2026-09", "receipts": 100000, "count": 2,
                                       "start": "2026-09-15", "end": "2026-09-30"})
    out, _ = await gstsetaside._numbers("w-gst", periods)
    assert out["2026-09"]["bank_short"] == ["15–30 Sep"] and out["2026-10"]["bank_short"] == []


async def test_documents_name_the_writer_in_words(client):
    c = await v23_member("9800930005")
    ws = await owner_id(c)
    await db().kits.insert_one({"_id": "sop-w", "ws": ws, "kind": "sop", "title": "Dispatch", "content": {}, "by_agent": "sop_set",
                                "created_at": now(), "updated_at": now()})
    assert (await c.get("/api/kits/sop")).json()[0]["by_agent"] == "SOP writer"
    assert (await c.get("/api/kits/sop/sop-w")).json()["by_agent"] == "SOP writer"


async def test_opening_whatsapp_is_not_sending(client):
    c = await v23_member("9800930006")
    ws = await owner_id(c)
    lead = (await c.post("/api/crm/deals", json={"name": "Neha Joshi", "phone": "98340 93006"})).json()
    aid = new_id()
    await db().approvals.insert_one({"_id": aid, "ws": ws, "agent": "followup", "kind": "followup", "status": "pending", "title": "Follow-up 1 of 3",
                                     "to_name": "Neha Joshi", "to_phone": "+919834093006", "text": "Hello Neha, any questions on the quote?",
                                     "lead_id": lead["id"], "n": 1, "created_at": now()})
    r = await c.post(f"/api/approvals/{aid}/opened")
    assert r.status_code == 200 and r.json()["link"].startswith("https://wa.me/919834093006?text=Hello%20Neha")
    a = await db().approvals.find_one({"_id": aid})
    assert a["status"] == "pending" and a["opened_at"]
    assert (await db().leads.find_one({"_id": lead["id"]}))["status"] == "new"            # nothing counted as sent yet
    assert any(x["id"] == aid and x["opened_at"] for x in (await c.get("/api/approvals")).json())
    assert (await c.post(f"/api/approvals/{aid}/opened")).status_code == 200              # can be opened again
    r = await c.post(f"/api/approvals/{aid}/own")                                         # "Yes, it's sent"
    assert r.status_code == 200 and r.json()["approval"]["status"] == "sent"
    assert (await db().leads.find_one({"_id": lead["id"]}))["status"] == "contacted"
    assert (await c.post(f"/api/approvals/{aid}/opened")).status_code == 409
    assert (await c.post("/api/approvals/nope/opened")).status_code == 404


async def test_a_zero_rupee_quotation_cant_be_shared_or_sent(client):
    c = await v23_member("9800930007")
    q = (await c.post("/api/quotes", json={"customer": {"name": "Meena Iyer", "phone": "98200 93007"},
                                          "items": [{"name": "Modular kitchen", "qty": 1, "rate": 0}]})).json()
    assert q["needs_price"] and q["pdf_url"] is None and q["whatsapp_link"] is None and q["share_text"] is None
    assert not (await db().quotes.find_one({"_id": q["id"]})).get("share_token")         # no customer link was made
    r = await c.post(f"/api/quotes/{q['id']}/status", json={"status": "sent"})
    assert r.status_code == 400 and "₹0" in r.text
    priced = (await c.put(f"/api/quotes/{q['id']}", json={"customer": {"name": "Meena Iyer", "phone": "98200 93007"},
                                                         "items": [{"name": "Modular kitchen", "qty": 1, "rate": 185000},
                                                                   {"name": "Free design visit", "qty": 1, "rate": 0}]})).json()
    assert not priced.get("needs_price") and priced["pdf_url"] and priced["whatsapp_link"]
    assert (await c.post(f"/api/quotes/{q['id']}/status", json={"status": "sent"})).status_code == 200
