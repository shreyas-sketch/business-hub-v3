"""Invoicing: the owner's own invoice as a reference for AI drafts (PDF with text, a photo, a scanned PDF), the customer's
PDF link in the WhatsApp message, and the Revenue Multiplier keeping the earlier numbers. Every name here is invented."""
import io
from datetime import timedelta

import httpx
from PIL import Image

from app import invoice_pdf, invoice_style as ist
from app.ai import desk_tasks, provider
from app.config import settings
from app.db import db, now
from tests.test_v21_p0 import owner_on, used_runs

SAMPLE = {"kind": "invoice", "number": "SGI/24-25/017", "gst_mode": "intra", "created_at": now(), "due_date": "2026-11-05",
          "customer": {"name": "Rohit Shah", "phone": "+919851130001"},
          "items": [{"name": "Modular kitchen - L shape (10 ft)", "qty": 1, "unit": "set", "rate": 185000, "gst": 18, "amount": 185000},
                    {"name": "Wardrobe - sliding (7 ft)", "qty": 2, "unit": "nos", "rate": 60000, "gst": 18, "amount": 120000}],
          "totals": {"subtotal": 305000, "gst": 54900, "cgst": 27450, "sgst": 27450, "igst": 0, "total": 359900},
          "terms": "Goods once sold will not be taken back. 50% advance."}


def sample_pdf() -> bytes:
    return invoice_pdf.render(SAMPLE, {"name": "Shree Ganesh Interiors", "city": "Thane"}, {"gstin": "27AAAPZ9999Q1Z9", "payment": "UPI sgi@okicici"})


async def add(c, data: bytes, name: str, ctype: str):
    return await c.post("/api/quotes/style", files={"file": (name, data, ctype)})


# ───────────────────────── the owner's invoice as a reference ─────────────────────────
async def test_a_pdf_invoice_teaches_the_drafts_and_fills_empty_settings(client, monkeypatch):
    c, uid, _ = await owner_on("9872000001", "running")
    seen: list[str] = []
    real = desk_tasks.complete

    async def spy(system, prompt, mock, max_tokens=1200, **kw):
        seen.append(prompt)
        return await real(system, prompt, mock, max_tokens, **kw)
    monkeypatch.setattr(desk_tasks, "complete", spy)

    before = await used_runs(uid)
    r = await add(c, sample_pdf(), "SGI-017.pdf", "application/pdf")
    assert r.status_code == 200, r.text
    d = r.json()
    assert await used_runs(uid) == before + 1                                  # one AI token to read it
    assert [s["kind"] for s in d["samples"]] == ["pdf"] and d["style"]["gst_rates"] == [18] and "terms" in d["filled"]
    assert (await db().quote_settings.find_one({"_id": uid}))["terms"].startswith("Goods once sold")   # empty → from their invoice
    stored = (await db().invoice_styles.find_one({"_id": uid}))["samples"][0]
    assert set(stored) == {"id", "name", "kind", "pages", "at", "style"}           # only what was learned; never the file

    # the learned style goes into every AI quotation draft
    await db().invoice_styles.update_one({"_id": uid}, {"$set": {"style.item_naming": "Product - variant (size)",
                                                                 "style.items": [{"name": "Wardrobe - sliding (7 ft)", "unit": "nos", "rate": 60000, "gst": 18}]}})
    assert (await c.post("/api/quotes/draft", json={"brief": "Two sliding wardrobes for Mr Shah"})).status_code == 200
    assert any("HOW THIS BUSINESS WRITES ITS OWN QUOTATIONS" in p and "Wardrobe - sliding (7 ft) (nos) ₹60000" in p for p in seen)

    # remove it: the drafts stop using it
    sid = d["samples"][0]["id"]
    assert (await c.delete(f"/api/quotes/style/{sid}")).json()["samples"] == []
    seen.clear()
    await c.post("/api/quotes/draft", json={"brief": "Two sliding wardrobes for Mr Shah"})
    assert not any("HOW THIS BUSINESS WRITES" in p for p in seen)


async def test_a_photo_and_a_scan_go_to_the_ai_as_images_and_a_typed_setting_is_never_overwritten(client, monkeypatch):
    c, uid, _ = await owner_on("9872000002", "running")
    await c.put("/api/quotes/settings", json={"gstin": "", "address": "", "payment": "", "terms": "My own terms"})
    got: list = []

    async def fake_learn(sample):
        got.append(sample)
        return {"summary": "A handwritten bill", "terms": "Their printed terms", "payment": "UPI x@okaxis", "items": []}, provider.Usage()
    monkeypatch.setattr(ist, "learn", fake_learn)
    img = Image.new("RGB", (900, 1200), "white")
    buf = io.BytesIO(); img.save(buf, "PNG")
    r = await add(c, buf.getvalue(), "bill.png", "image/png")
    assert r.status_code == 200, r.text
    assert got[0]["kind"] == "image" and got[0]["images"][0][0] == "image/jpeg" and not got[0]["text"]
    assert r.json()["filled"] == ["payment"]                                    # terms were typed by the owner: kept
    qs = await db().quote_settings.find_one({"_id": uid})
    assert qs["terms"] == "My own terms" and qs["payment"] == "UPI x@okaxis"
    # a scanned PDF (no text in it) is drawn as pictures
    scan = io.BytesIO(); Image.new("RGB", (800, 1100), "white").save(scan, "PDF")
    assert (await add(c, scan.getvalue(), "scan.pdf", "application/pdf")).status_code == 200
    assert got[1]["kind"] == "pdf" and got[1]["images"] and not got[1]["text"]
    # limits: 3 invoices, a file that's neither, an empty file
    assert (await add(c, buf.getvalue(), "b3.png", "image/png")).status_code == 200
    assert (await add(c, buf.getvalue(), "b4.png", "image/png")).status_code == 400
    assert (await c.delete(f"/api/quotes/style/{r.json()['samples'][0]['id']}")).status_code == 200
    assert (await add(c, b"hello", "notes.txt", "text/plain")).status_code == 400
    assert (await add(c, b"", "empty.pdf", "application/pdf")).status_code == 400


def test_images_reach_each_ai_engine(monkeypatch):
    import asyncio
    sent = []

    class Fake(httpx.AsyncClient):
        async def post(self, url, json=None, headers=None):
            sent.append((url, json))
            if "anthropic" in url:
                return httpx.Response(200, json={"content": [{"type": "text", "text": "{}"}], "usage": {}, "stop_reason": "end_turn"})
            if "googleapis" in url:
                return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})
            return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})
    monkeypatch.setattr(provider.httpx, "AsyncClient", lambda *a, **k: Fake())
    pic = [("image/jpeg", b"\xff\xd8jpeg")]
    for name in ("anthropic", "gemini", "openai"):
        asyncio.run(provider._call_once(name, "sys", "read this", None, 100, images=pic))
    a, g, o = (j for _, j in sent)
    assert a["messages"][0]["content"][0]["type"] == "image" and a["messages"][0]["content"][1]["text"] == "read this"
    assert g["contents"][0]["parts"][0]["inline_data"]["mime_type"] == "image/jpeg"
    assert o["messages"][1]["content"][0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    sent.clear()
    asyncio.run(provider._call_once("anthropic", "sys", "text only", None, 100))
    assert sent[0][1]["messages"][0]["content"] == "text only"               # without images: as before


# ───────────────────────── the PDF link in the WhatsApp message ─────────────────────────
async def test_the_whatsapp_message_carries_the_pdf_link_and_it_opens_without_a_login(client):
    c, uid, _ = await owner_on("9872000003", "running")
    q = (await c.post("/api/quotes", json={"customer": {"name": "Rohit Shah", "phone": "98511 30001"},
                                           "items": [{"name": "Wardrobe", "qty": 1, "rate": 60000}]})).json()
    url = q["pdf_url"]
    assert url.startswith(f"{settings.app_url}/i/") and "share_token" not in q
    assert q["share_text"].endswith(f"See the quotation as a PDF: {url}")
    assert httpx.URL(q["whatsapp_link"]).params["text"].endswith(url)
    path = url.replace(settings.app_url, "")
    r = await client.get(path)                                                   # the customer: no login
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content.startswith(b"%PDF")
    # the same link every time; the invoice made from it gets its own
    again = next(x for x in (await c.get("/api/quotes?kind=quote")).json() if x["id"] == q["id"])
    assert again["pdf_url"] == url
    await c.post(f"/api/quotes/{q['id']}/status", json={"status": "accepted"})
    # Invoice on yes: accepting it made the invoice
    inv = next(x for x in (await c.get("/api/quotes", params={"kind": "invoice"})).json() if x.get("quote_id") == q["id"])
    assert inv["pdf_url"] != url and "See the invoice as a PDF:" in inv["share_text"]
    assert (await client.get(inv["pdf_url"].replace(settings.app_url, ""))).status_code == 200
    # a made-up or short token: not found
    assert (await client.get("/i/" + "x" * 22)).status_code == 404
    assert (await client.get("/i/abc")).status_code == 404


# ───────────────────────── Revenue Multiplier: earlier numbers are kept ─────────────────────────
async def test_levers_keep_the_earlier_numbers_once_per_sitting(client):
    c, uid, _ = await owner_on("9872000004", "program")
    base = {"customers": 100, "avg_value": 50000, "frequency": 1.2, "grow_customers": 10, "grow_value": 10, "grow_frequency": 10,
            "funnel": {}, "pledge": ""}
    assert (await c.put("/api/levers", json=base)).status_code == 200
    # typing in the same sitting (saved as they type): no history yet
    for n in (110, 120):
        await c.put("/api/levers", json={**base, "customers": n})
    lv = (await c.get("/api/levers")).json()["levers"]
    assert lv["customers"] == 120 and lv["history"] == []
    # a later sitting: the numbers from before it are kept
    await db().levers.update_one({"_id": uid}, {"$set": {"updated_at": now() - timedelta(hours=2)}})
    await c.put("/api/levers", json={**base, "customers": 150})
    lv = (await c.get("/api/levers")).json()["levers"]
    assert lv["customers"] == 150 and [h["customers"] for h in lv["history"]] == [120]
    assert lv["history"][0]["revenue"] == 120 * 50000 * 1.2
    # only the pledge changed: no new history
    await db().levers.update_one({"_id": uid}, {"$set": {"updated_at": now() - timedelta(hours=2)}})
    await c.put("/api/levers", json={**base, "customers": 150, "pledge": "10×10×10 by March"})
    assert len((await c.get("/api/levers")).json()["levers"]["history"]) == 1
