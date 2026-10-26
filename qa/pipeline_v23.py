"""v2.3 Pipeline & Opportunities (GHL-style) interaction check, on a desktop and on a phone. Drives the page the way an owner
would — builds the pipeline from the 4 questions, drags a card, undoes it, moves one from its ⋯ menu, edits one in the dialog
(value, follow-up, a note, Won), adds one from a column's +, filters, the list view, and reorders a stage — and checks
each step on the server's data. Every name here is invented.

Run against a seeded hub on localhost:8000 (v2.3 setup applied; a member with open opportunities and no pipeline yet):
    python3 qa/pipeline_v23.py /tmp/pp_shots 9820000007
"""
import json
import re
import sys

from playwright.sync_api import sync_playwright

BASE = "http://localhost:8000"
OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/pp_shots"
PHONE = sys.argv[2] if len(sys.argv) > 2 else "9820000007"
problems: list[str] = []
passed: list[str] = []


def check(ok, what):
    (passed if ok else problems).append(what)
    print(("  ok  " if ok else "  FAIL ") + what)


def watch(page, label):
    page.on("pageerror", lambda e: problems.append(f"[{label}] page error: {e}"))
    page.on("console", lambda m: m.type == "error" and not re.search(r"status of (400|401|402|403|404|409|429)|fonts\.g", m.text)
            and problems.append(f"[{label}] console: {m.text[:200]}"))
    page.on("response", lambda r: r.status >= 500 and problems.append(f"[{label}] {r.status} {r.url}"))


def login(page, phone):
    page.goto(f"{BASE}/login")
    page.fill("input[inputmode=tel]", phone)
    page.click("button:has-text('Send code')")
    page.wait_for_selector(".prompt")
    code = re.search(r"(\d{6})", page.inner_text(".prompt")).group(1)
    page.fill("input[autocomplete=one-time-code]", code)
    page.click("button:has-text('Log in')")
    page.wait_for_timeout(1500)


def deals(page):
    return page.evaluate("() => fetch('/api/crm/deals', {credentials: 'include'}).then(r => r.json())")


def shot(page, name):
    page.wait_for_timeout(350)
    wide = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
    if wide > 1:
        problems.append(f"{name}: the page scrolls sideways by {wide}px")
    page.screenshot(path=f"{OUT}/{name}.png", full_page=False)


def col_of(page, name):
    return page.evaluate("""(n) => { for (const c of document.querySelectorAll('.pp-col')) {
        for (const b of c.querySelectorAll('.pp-card-name b')) if (b.textContent.trim() === n) return c.getAttribute('data-col'); } return null; }""", name)


def center(page, sel):
    b = page.locator(sel).first.bounding_box()
    return b["x"] + b["width"] / 2, b["y"] + min(b["height"] / 2, 40)


def desktop(browser):
    ctx = browser.new_context(viewport={"width": 1366, "height": 900}, device_scale_factor=1)
    page = ctx.new_page()
    watch(page, "desktop")
    login(page, PHONE)
    page.goto(f"{BASE}/pipeline")
    page.wait_for_selector(".pp-subnav")
    page.wait_for_timeout(800)
    first = page.locator(".pp-subnav button.on").inner_text()
    check(first == "Pipeline" and page.locator("text=Build without AI (free)").count() == 1,
          "first visit with no stages: the Pipeline tab and the 4 questions come first")
    shot(page, "d01_questions")
    page.click("text=Build without AI (free)")
    page.wait_for_selector(".pp-board", timeout=15000)
    page.wait_for_timeout(800)
    check(page.locator(".pp-subnav button.on").inner_text() == "Opportunities", "after building, it opens the Opportunities board")
    cols = page.locator(".pp-col")
    n = cols.count()
    check(n >= 3, f"the board shows a column per stage ({n})")
    page.evaluate("() => window.scrollTo(0, 0)")
    shot(page, "d02_board")

    data = deals(page)
    stages = [s["id"] for s in data["stages"]]
    open_ = [d for d in data["deals"] if d["status"] in ("new", "contacted")]
    heads_ok = all(re.search(r"\d+ opportunit(y|ies)", cols.nth(i).locator(".pp-col-sum").inner_text()) for i in range(n))
    check(heads_ok, "each column shows how many opportunities and the ₹ total")

    # drag a card one column to the right
    d0 = next(d for d in open_ if d["pstage"] in stages[:-1])
    src = d0["pstage"]
    dst = stages[stages.index(src) + 1]
    card = f".pp-col[data-col='{src}'] .pp-card:has(.pp-card-name b:text-is('{d0['name']}'))"
    x, y = center(page, card)
    tx, ty = center(page, f".pp-col[data-col='{dst}'] .pp-col-body")
    page.mouse.move(x, y)
    page.mouse.down()
    for i in range(1, 16):
        page.mouse.move(x + (tx - x) * i / 15, y + (ty - y) * i / 15)
        page.wait_for_timeout(16)
    page.mouse.up()
    page.wait_for_timeout(900)
    after = {d["id"]: d for d in deals(page)["deals"]}
    check(after[d0["id"]]["pstage"] == dst and col_of(page, d0["name"]) == dst, f"drag and drop moves {d0['name']} to the next stage, and it's saved")
    check(page.locator(".pp-undo").count() == 1, "an Undo bar shows after the move")
    shot(page, "d03_after_drag_undo")
    page.click(".pp-undo button:has-text('Undo')")
    page.wait_for_timeout(900)
    after = {d["id"]: d for d in deals(page)["deals"]}
    check(after[d0["id"]]["pstage"] == src and col_of(page, d0["name"]) == src, "Undo puts it back, and that's saved too")

    # the ⋯ menu: move to another stage
    d1 = next(d for d in open_ if d["id"] != d0["id"])
    target = next(s for s in data["stages"] if s["id"] != d1["pstage"])
    page.locator(f".pp-card:has(.pp-card-name b:text-is('{d1['name']}')) .pp-dots").click()
    page.wait_for_selector(".pp-menu")
    shot(page, "d04_card_menu")
    page.locator(f".pp-menu button:text-is('{target['name']}')").click()
    page.wait_for_timeout(900)
    after = {d["id"]: d for d in deals(page)["deals"]}
    check(after[d1["id"]]["pstage"] == target["id"], f"the ⋯ menu moves {d1['name']} to {target['name']}")
    page.keyboard.press("Escape")

    # the edit dialog: value, follow-up, a note, then Won
    d2 = next(d for d in open_ if d["id"] not in (d0["id"], d1["id"]))
    page.locator(f".pp-card:has(.pp-card-name b:text-is('{d2['name']}')) .pp-card-name").click()
    page.wait_for_selector(".pp-modal-body")
    page.wait_for_timeout(500)
    shot(page, "d05_edit_dialog")
    page.fill(".pp-modal-main label:has-text('Opportunity value') input", "275000")
    page.fill(".pp-modal-main label:has-text('Follow up on') input", "2026-11-03")
    page.fill(".pp-modal-side textarea", "Asked for a sample box in two sizes")
    page.click(".pp-modal-side button:has-text('Add')")
    page.wait_for_timeout(900)
    check(page.locator(".pp-modal-main label:has-text('Opportunity value') input").input_value() == "275000",
          "adding a note keeps what was typed on the left, not saved yet")
    check(page.locator(".pp-modal-side .crm-ev:has-text('Asked for a sample box')").count() >= 1, "the note shows in the history at once")
    page.select_option(".pp-modal-main label:has-text('Status') select", "won")
    page.click(".pp-modal-foot button:has-text('Update')")
    page.wait_for_timeout(1000)
    after = {d["id"]: d for d in deals(page)["deals"]}
    x2 = after[d2["id"]]
    check(page.locator(".pp-modal").count() == 0, "Update closes the dialog")
    check(x2["value"] == 275000 and x2["follow_up_on"] == "2026-11-03" and x2["status"] == "won", "Update saves the value, the follow-up date and Won together")
    check(x2["counts"]["note"] >= 1, "the card's note count comes from the saved history")

    # mark lost from the menu needs a reason
    d3 = next(d for d in open_ if d["id"] not in (d0["id"], d1["id"], d2["id"]))
    page.locator(f".pp-card:has(.pp-card-name b:text-is('{d3['name']}')) .pp-dots").click()
    page.locator(".pp-menu button:has-text('Mark lost')").click()
    page.wait_for_selector(".pp-modal-body")
    page.wait_for_timeout(400)
    page.click(".pp-modal-foot button:has-text('Update')")
    page.wait_for_timeout(500)
    check(page.locator(".pp-modal-foot .form-err").count() == 1, "Lost without a reason is stopped, with a message")
    page.locator(".pp-modal-main .crm-chips .crm-chip").first.click()
    page.click(".pp-modal-foot button:has-text('Update')")
    page.wait_for_timeout(900)
    x3 = {d["id"]: d for d in deals(page)["deals"]}[d3["id"]]
    check(x3["status"] == "lost" and x3["lost_reason"], "with a reason picked, it's saved as Lost")

    # + on a column adds an opportunity in that stage
    last = stages[-1]
    page.locator(f".pp-col[data-col='{last}'] .pp-add").click()
    page.wait_for_selector(".modal")
    page.fill(".modal label:has-text('Contact name') input", "Riddhi Traders")
    page.fill(".modal label:has-text('Opportunity value') input", "56000")
    page.click(".modal button:has-text('Add opportunity')")
    page.wait_for_timeout(1000)
    check(col_of(page, "Riddhi Traders") == last, "a column's + adds the opportunity straight into that stage")

    # status filter, filters, search, list
    page.click(".pp-tb-right .crm-seg button:has-text('Won')")
    page.wait_for_timeout(400)
    names = page.locator(".pp-card-name b").all_inner_texts()
    won = {d["name"] for d in deals(page)["deals"] if d["status"] == "won"}
    check(names and set(names) <= won, "the Won filter shows only won opportunities")
    shot(page, "d06_filter_won")
    page.click(".pp-tb-right .crm-seg button:has-text('Open')")
    page.click(".pp-pop-wrap > button")
    page.wait_for_selector(".pp-pop")
    shot(page, "d07_filters")
    page.click(".pp-pop button:has-text('Done')")
    page.fill(".pp-search", "Riddhi")
    page.wait_for_timeout(300)
    check(page.locator(".pp-card").count() == 1, "search narrows the board")
    page.fill(".pp-search", "")
    page.click(".pp-viewtoggle button[aria-label='List']")
    page.wait_for_timeout(400)
    check(page.locator(".pp-tr:not(.head)").count() >= 3, "the list view shows a row per opportunity")
    shot(page, "d08_list")
    page.click(".pp-viewtoggle button[aria-label='Board']")

    # Pipeline tab: reorder a stage with the arrows and save
    page.click(".pp-subnav button:has-text('Pipeline')")
    page.wait_for_selector(".pp-stages")
    shot(page, "d09_pipeline_tab")
    first_name = page.locator(".pp-stage input").first.input_value()
    page.locator(".pp-stage").first.locator("button[aria-label='Move down']").click()
    page.click("button:has-text('Save pipeline')")
    page.wait_for_timeout(900)
    order = [s["name"] for s in deals(page)["stages"]]
    check(order[1] == first_name, f"moving a stage down and saving changes the order ({first_name} is now 2nd)")
    ctx.close()


def phone(browser):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, has_touch=True, is_mobile=True)
    page = ctx.new_page()
    watch(page, "phone")
    login(page, PHONE)
    page.goto(f"{BASE}/pipeline")
    page.wait_for_selector(".pp-board")
    page.wait_for_timeout(900)
    shot(page, "p01_board")
    w = page.locator(".pp-col").first.bounding_box()["width"]
    check(300 <= w <= 340, f"on a phone a column is about 84% of the screen ({round(w)}px)")
    page.evaluate("() => window.scrollTo(0, 0)")

    # long press and drag with a finger (Chrome's touch events)
    data = deals(page)
    stages = [s["id"] for s in data["stages"]]
    d0 = next(d for d in data["deals"] if d["status"] in ("new", "contacted") and d["pstage"] == stages[0])
    card = page.locator(f".pp-col[data-col='{stages[0]}'] .pp-card:has(.pp-card-name b:text-is('{d0['name']}'))").first
    card.scroll_into_view_if_needed()
    b = card.bounding_box()
    x, y = b["x"] + b["width"] / 2, b["y"] + 30
    cdp = ctx.new_cdp_session(page)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
    page.wait_for_timeout(450)
    for i in range(1, 21):
        cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x + 6 * i, "y": y + 2}]})
        page.wait_for_timeout(30)
    # hold it near the right edge so the board scrolls to the next stage, then let go in the middle of the screen
    cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": 372, "y": y + 2}]})
    page.wait_for_timeout(450)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": 200, "y": y + 2}]})
    page.wait_for_timeout(200)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    page.wait_for_timeout(1000)
    moved = {d["id"]: d for d in deals(page)["deals"]}[d0["id"]]["pstage"]
    check(moved != stages[0], f"on a phone, press-and-hold then drag moves {d0['name']} to another stage")

    page.locator(".pp-card .pp-card-name").first.click()
    page.wait_for_selector(".pp-modal-body")
    page.wait_for_timeout(500)
    mb = page.locator(".pp-modal").bounding_box()
    check(mb["width"] >= 389, "on a phone the Edit dialog fills the screen")
    shot(page, "p02_edit_dialog")
    page.click(".pp-modal-foot button:has-text('Cancel')")
    page.locator(".pp-card .pp-dots").first.click()
    page.wait_for_selector(".pp-menu")
    mm = page.locator(".pp-menu").bounding_box()
    check(mm["x"] >= 0 and mm["x"] + mm["width"] <= 390, "the ⋯ menu stays on the screen")
    shot(page, "p03_menu")
    page.keyboard.press("Escape")
    page.click(".pp-viewtoggle button[aria-label='List']")
    page.wait_for_timeout(400)
    shot(page, "p04_list")
    page.click(".pp-subnav button:has-text('Pipeline')")
    page.wait_for_selector(".pp-stages")
    shot(page, "p05_pipeline_tab")
    ctx.close()


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        print("desktop")
        desktop(browser)
        print("phone")
        phone(browser)
        browser.close()
    json.dump({"passed": passed, "problems": problems}, open(f"{OUT}/report.json", "w"), indent=1)
    print(f"{len(passed)} checks passed, {len(problems)} problems")
    for x in problems:
        print(" ", x)


if __name__ == "__main__":
    main()
