"""Prepares a freshly seeded local hub for qa/screens_v23.py and qa/pipeline_v23.py: the event open on Day 1, the demo member
(9820000007) paid for a year, and an event attendee (9820000101) who built and saved a pack on the event link.

    python seed_demo.py            # (in backend/, local only) then start the hub: the v2.3 setup applies itself
    python3 qa/prep_v23.py         # DB_NAME as the hub's (default qa_mc)
"""
import os
import re
from datetime import date, timedelta
from pymongo import MongoClient
from playwright.sync_api import expect, sync_playwright

B = "http://localhost:8000"; CODE = "BAI-OCT-11"


def gstin():
    cs = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"; first = "27AAKFS4410C1Z"
    t = sum((cs.index(ch) * (2 if i % 2 else 1)) // 36 + (cs.index(ch) * (2 if i % 2 else 1)) % 36 for i, ch in enumerate(first))
    return first + cs[(36 - t % 36) % 36]


def login(pg, phone):
    pg.goto(f"{B}/login"); pg.fill("input[inputmode=tel]", phone); pg.click("button:has-text('Send code')")
    pg.wait_for_selector(".prompt"); code = re.search(r"(\d{6})", pg.inner_text(".prompt")).group(1)
    pg.fill("input[autocomplete=one-time-code]", code); pg.click("button:has-text('Log in')"); pg.wait_for_timeout(1500)


with sync_playwright() as p:
    b = p.chromium.launch()
    a = b.new_page(); login(a, "9999900000")
    r = a.request.put(f"{B}/api/admin/event/{CODE}", data={"open": True, "ai": True, "pack_cap": 2000, "ai_cap": 2000,
                                                           "trial_until": (date.today() + timedelta(days=3)).isoformat()})
    print("event", r.status, "day", a.request.post(f"{B}/api/admin/event/{CODE}/day", data={"day": 1}).status)
    uid = MongoClient()[os.getenv("DB_NAME", "qa_mc")].users.find_one({"phone": "+919820000007"})["_id"]
    print("member paid", a.request.post(f"{B}/api/admin/onboarding/{uid}/paid", data={"period": "year", "ref": "QA"}).status)
    ctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    ctx.route(re.compile(r"https://(wa\.me|api\.whatsapp\.com)/.*"), lambda r: r.fulfill(body="stand-in", content_type="text/plain"))
    e = ctx.new_page()
    e.goto(f"{B}/event?e={CODE}"); expect(e.locator("h1")).to_contain_text("minute")
    e.locator("text=I don't have a website").click()
    e.fill("input[placeholder='Riya Tiffins']", "Annapurna Home Tiffins"); e.fill("input[placeholder='Pune']", "Pune")
    e.fill("textarea", "Home-cooked lunch tiffins delivered to offices in Baner, Aundh and Hinjewadi")
    e.fill("input[placeholder='27ABCDE1234F1Z5']", gstin()); e.wait_for_timeout(900)
    seg = e.locator("[role=group][aria-label='Type of business'] button")
    if seg.count():
        seg.first.click()
    e.fill("input[aria-label='People who work with you']", "6")
    e.locator(".d1-consent").last.click(); e.click("button:has-text('Build my pack')")
    e.wait_for_url("**/event/r/**"); expect(e.locator("h1")).to_contain_text("built", timeout=120000)
    e.locator("button:has-text('Save it to my hub')").first.click(); e.wait_for_timeout(600)
    e.fill("input[placeholder='Asha']", "Meena"); e.fill("input[placeholder='you@yourbusiness.in']", "meena@annapurna.example")
    e.fill("input[placeholder='98200 00000']", "98200 00101")
    e.click("button:has-text('Send my code')"); e.wait_for_selector(".prompt")
    code = re.search(r"(\d{6})", e.inner_text(".prompt")).group(1); e.fill("input[aria-label='6-digit code']", code)
    e.click("button:has-text('Save to my hub')"); expect(e.locator("h2:has-text('Saved')")).to_be_visible()
    print("attendee saved")
    b.close()
