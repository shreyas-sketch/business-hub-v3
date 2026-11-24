"""v2.4 (OwnerDesk growth) check: uses each new feature the way an owner would, on a desktop (1366 px) and a phone (390 px),
checks the saved data after each step, and takes screenshots. Invented data only (seed_demo.py, qa/fake_places.py).

    # 1. a throwaway database with the demo seed, the v2.3 and v2.4 setup applied (see docs/V24_HANDOFF.md, section QA)
    # 2. python3 qa/fake_places.py 8098
    # 3. the hub on :8000 with GOOGLE_PLACES_API_KEY=qa-fake GOOGLE_PLACES_API_BASE=http://127.0.0.1:8098/v1 FETCH_ALLOW_PRIVATE=true
    #    APP_ENV=development AI_PROVIDER=mock
    QA_DB=qa24 python3 qa/screens_v24.py /tmp/shots24

Prints each check, and writes report.json (checks, problems, screenshots) next to the screenshots.
"""
import json
import os
import re
import sys

from playwright.sync_api import sync_playwright

BASE = "http://localhost:8000"
OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/shots24"
QA_DB = os.environ.get("QA_DB", "")    # optional: the seeded database, to check what is stored (e.g. QA_DB=qa24)
QA_MONGO = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017")
MEMBER = "9820000007"          # Kaveri Home Appliances in seed_demo.py (invented), on Membership
SIZES = {"desktop": {"width": 1366, "height": 900}, "phone": {"width": 390, "height": 844}}
passed: list[str] = []
problems: list[str] = []
shots: list[str] = []


def check(ok, what):
    (passed if ok else problems).append(what)
    print(("  ok   " if ok else "  FAIL ") + what)


def watch(page, label):
    page.on("pageerror", lambda e: problems.append(f"[{label}] page error: {e}"))
    page.on("console", lambda m: m.type == "error" and not re.search(r"status of (400|401|402|403|404|409|429)|fonts\.g", m.text)
            and problems.append(f"[{label}] console: {m.text[:200]}"))
    page.on("response", lambda r: r.status >= 500 and problems.append(f"[{label}] {r.status} {r.url}"))
    page.on("dialog", lambda d: d.accept())


def login(page, phone):
    page.goto(f"{BASE}/login")
    page.fill("input[inputmode=tel]", phone)
    page.click("button:has-text('Send code')")
    page.wait_for_selector(".prompt")
    code = re.search(r"(\d{6})", page.inner_text(".prompt")).group(1)
    page.fill("input[autocomplete=one-time-code]", code)
    page.click("button:has-text('Log in')")
    page.wait_for_timeout(1500)


def shot(page, size, name, full=True):
    page.wait_for_timeout(400)
    wide = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
    if wide > 1:
        problems.append(f"[{size}] {name}: the page scrolls sideways by {wide}px")
    fn = f"{size}_{name}.png"
    page.screenshot(path=f"{OUT}/{fn}", full_page=full)
    shots.append(fn)


def api(page, method, path, body=None):
    return page.evaluate("""([m, p, b]) => fetch('/api' + p, {method: m, credentials: 'include',
        headers: b ? {'content-type': 'application/json'} : {}, body: b ? JSON.stringify(b) : undefined})
        .then(async r => ({status: r.status, body: await r.json().catch(() => null)}))""", [method, path, body])


def step(name, fn):
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        problems.append(f"{name}: {type(e).__name__}: {str(e)[:300]}")
        print(f"  FAIL {name}: {type(e).__name__}: {str(e)[:200]}")


def prospects(page, size):
    page.goto(f"{BASE}/prospects")
    page.wait_for_timeout(1500)
    if page.locator("button[role=tab]:has-text('Find')").count():
        page.click("button[role=tab]:has-text('Find')")
        page.wait_for_timeout(500)
    shot(page, size, "01_find")
    if page.locator("button:has-text('Find businesses')").is_disabled():       # a first visit: no kinds yet
        page.fill("input[aria-label='Kind of business']", "builders")
        page.click("button:text-is('Add')")
        page.wait_for_timeout(800)
        page.click("button:has-text('Suggest with AI')")
        page.wait_for_timeout(2500)
        shot(page, size, "01b_kinds")
    kinds = (api(page, "GET", "/prospects")["body"] or {}).get("kinds") or []
    check(len(kinds) >= 2 and "builders" in kinds, f"[{size}] who to look for keeps the kind typed and adds the AI ones ({kinds})")
    before = api(page, "GET", "/prospects")["body"]
    page.click("button:has-text('Find businesses')")
    page.wait_for_timeout(2500)
    shot(page, size, "02_results")
    boxes = page.locator("label.pr-row input[type=checkbox]:not([disabled])")
    n = min(2, boxes.count())
    for i in range(n):
        boxes.nth(i).check()
    check(n >= 1, f"[{size}] a search shows businesses to keep ({boxes.count()} can be ticked)")
    page.click("button:has-text('Keep ')")
    page.wait_for_timeout(2000)
    shot(page, size, "03_kept")
    lst = api(page, "GET", "/prospects/list")["body"]
    items = (lst or {}).get("items") or []
    google = [x for x in items if x.get("from_google")]
    check(len(google) >= n, f"[{size}] kept businesses are on the list ({len(google)} from Google Maps)")
    check(all(x.get("name") for x in google), f"[{size}] Google businesses show their name (fetched live)")
    if QA_DB:   # and nothing but the place ID is stored for them
        from pymongo import MongoClient
        stored = list(MongoClient(QA_MONGO)[QA_DB].prospects.find({"source": "google"}))
        leaked = [k for doc in stored for k in ("name", "address", "phone", "website", "email", "location", "lat", "lng") if doc.get(k)]
        check(stored and not leaked, f"[{size}] Google businesses are stored as place IDs only ({len(stored)} stored, extra fields: {leaked or 'none'})")
    after = api(page, "GET", "/prospects")["body"]
    try:
        used = after["limits"]["searches"]["used"] - before["limits"]["searches"]["used"]
        check(used == 1, f"[{size}] one search counted against the month ({used})")
    except (KeyError, TypeError):
        problems.append(f"[{size}] could not read the search limit from /api/prospects")
    page.click("button[role=tab]:has-text('My list')")
    page.wait_for_timeout(1500)
    boxes = page.locator(".pr-row input[type=checkbox]:not([disabled])")
    for i in range(min(2, boxes.count())):
        boxes.nth(i).check()
    page.click("button:has-text('Write first messages')")
    page.wait_for_timeout(5000)
    shot(page, size, "04_list_drafted")
    page.locator(".pr-open").first.click()
    page.wait_for_timeout(2500)
    shot(page, size, "05_prospect")
    page.goto(f"{BASE}/approvals")
    page.wait_for_timeout(2000)
    shot(page, size, "06_send_list")
    pend = api(page, "GET", "/approvals")["body"] or []
    rows = pend if isinstance(pend, list) else (pend.get("items") or [])
    outreach = [a for a in rows if a.get("kind") == "outreach"]
    check(bool(outreach), f"[{size}] first messages wait in the Send list ({len(outreach)})")
    if outreach:
        r = api(page, "POST", f"/approvals/{outreach[0]['id']}/send")
        check(r["status"] == 400, f"[{size}] an outreach message can't be sent by OwnerDesk itself (status {r['status']})")


def call_coach(page, size):
    data = api(page, "GET", "/call-coach")["body"] or {}
    members = data.get("members") or []
    if not members:
        api(page, "POST", "/call-coach/members", {"name": "Invented Salesperson"})
    page.goto(f"{BASE}/call-coach")
    page.wait_for_timeout(1500)
    shot(page, size, "07_call_coach")
    sel = page.locator(".cc-score select")
    if sel.count():
        sel.select_option(index=1 if sel.locator("option").count() > 1 else 0)
    page.fill(".cc-score textarea", "Customer: Do you install chimneys on the same day?\nMe: Yes sir. Which brand and what size is the kitchen?\n"
                                     "Customer: Small kitchen, 60 cm. How much?\nMe: It includes installation and a 1 year warranty. "
                                     "Shall I book the visit for Saturday at 11?\nCustomer: Okay, Saturday.")
    page.click("button:has-text('Check who said what first')")
    page.wait_for_timeout(800)
    shot(page, size, "08_call_check")
    page.click(".cc-review button:has-text('Score this call')")
    page.wait_for_timeout(1800)
    shot(page, size, "09_call_scored")
    calls = api(page, "GET", "/call-coach/calls")["body"] or {}
    lst = calls.get("calls") or calls.get("items") or []
    check(bool(lst), f"[{size}] the scored call is saved ({len(lst)} calls)")


def pipeline(page, size, deal_name):
    if not (api(page, "GET", "/crm/pipeline")["body"] or {}).get("pipeline"):
        api(page, "POST", "/crm/pipeline/build", {"answers": {"sell": "Kitchen appliances", "customers": "Families", "sources": ["website"],
                                                             "who": "owner", "after_lead": "", "path": ["call", "quote"], "before_pay": "",
                                                             "reasons": []}, "use_ai": False})
    page.goto(f"{BASE}/pipeline")
    page.wait_for_selector(".pp-board")
    page.wait_for_timeout(800)
    card = page.locator(f".pp-card:has(.pp-card-name b:text-is('{deal_name}')) .pp-card-name").first
    if not card.count():
        card = page.locator(".pp-card .pp-card-name").first
    card.click()
    page.wait_for_selector(".pp-modal-body")
    page.wait_for_timeout(600)
    page.click(".pp-modal-side .tabs button:has-text('Conversation')")
    page.fill(".pp-modal-side textarea", "Customer: We need a chimney and a hob for our new flat.\nMe: Sure ji, sending the quotation now.\n"
                                          "Customer: Budget is ₹42,000. Please call me tomorrow evening.")
    page.click(".pp-modal-side button:has-text('File this conversation')")
    page.wait_for_selector(".pp-suggest", timeout=10000)
    page.wait_for_timeout(500)
    shot(page, size, "10_deal_suggested", full=False)
    items = page.locator(".pp-suggest-item").all_inner_texts()
    check(bool(items), f"[{size}] filing a chat suggests deal updates that wait for a tap ({len(items)})")
    page.click(".pp-modal-side .tabs button:has-text('Brief')")
    page.wait_for_timeout(400)
    if page.locator(".pp-modal-side button:has-text('Research')").count():
        page.click(".pp-modal-side button:has-text('Research')")
        page.wait_for_timeout(1500)
    shot(page, size, "11_deal_brief", full=False)
    check(page.locator(".pp-brief").count() == 1, f"[{size}] the deal has a lead brief")
    page.click(".pp-modal-foot button:has-text('Cancel')")
    page.wait_for_timeout(400)


def ai_team_and_dashboard(page, size):
    page.goto(f"{BASE}/ai-team")
    page.wait_for_timeout(1800)
    shot(page, size, "12_ai_team")
    team = api(page, "GET", "/ai-team")["body"] or {}
    feed = team.get("feed") or []
    check(len(feed) >= 3, f"[{size}] the AI team feed lists the actions just done ({len(feed)} lines)")
    check(all(x.get("link") for x in feed), f"[{size}] every feed line links to its record")
    page.goto(f"{BASE}/dashboard")
    page.wait_for_timeout(1800)
    q = page.locator("button.qa-q").first
    if q.count():
        q.click()
        page.wait_for_timeout(900)
    check(q.count() == 1, f"[{size}] Quick answers show on the dashboard")
    shot(page, size, "13_dashboard")


def main():
    deals = {"desktop": "Kunal Mehta", "phone": "Farhan Shaikh"}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for size, vp in SIZES.items():
            ctx = browser.new_context(viewport=vp, device_scale_factor=1)
            page = ctx.new_page()
            watch(page, size)
            login(page, MEMBER)
            step(f"[{size}] find new customers", lambda: prospects(page, size))
            step(f"[{size}] call coach", lambda: call_coach(page, size))
            step(f"[{size}] pipeline fills itself + lead brief", lambda: pipeline(page, size, deals[size]))
            step(f"[{size}] AI team + dashboard", lambda: ai_team_and_dashboard(page, size))
            ctx.close()
        browser.close()
    json.dump({"passed": passed, "problems": problems, "shots": shots}, open(f"{OUT}/report.json", "w"), indent=1)
    print(f"{len(passed)} checks passed, {len(problems)} problems, {len(shots)} screenshots")
    for x in problems:
        print(" ", x)


if __name__ == "__main__":
    main()
