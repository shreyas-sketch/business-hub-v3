"""Browser checks for the workspace shell (12 checks): the top bar shows where you are, Go to finds pages by name and by what
they do and opens them from the keyboard, the account menu, and the phone's bottom bar. Run against a freshly seeded local
hub, as for e2e.py:  python3 qa/shell_checks.py
"""
import re
from playwright.sync_api import sync_playwright, expect

B = "http://localhost:8000"
results, problems = [], []


def check(name, fn):
    try:
        fn(); results.append(("PASS", name))
    except Exception as e:
        results.append(("FAIL", name)); problems.append(f"{name}: {type(e).__name__}: {str(e)[:300]}")


def login(page, phone):
    page.goto(f"{B}/login"); page.fill("input[inputmode=tel]", phone); page.click("button:has-text('Send code')")
    page.wait_for_selector(".prompt"); code = re.search(r"(\d{6})", page.inner_text(".prompt")).group(1)
    page.fill("input[autocomplete=one-time-code]", code); page.click("button:has-text('Log in')")


def watch(page, label):
    page.set_default_timeout(10000)
    page.on("pageerror", lambda e: problems.append(f"[{label}] page error: {e}"))
    page.on("response", lambda r: r.status >= 500 and problems.append(f"[{label}] {r.status} {r.url}"))


with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 900}); watch(pg, "desk")
    login(pg, "9820000007"); pg.wait_for_url("**/dashboard")

    def crumbs():
        pg.goto(f"{B}/quotes"); expect(pg.locator(".crumbs")).to_contain_text("Systems"); expect(pg.locator(".crumbs b")).to_have_text("Quotations & invoices")
        pg.goto(f"{B}/inquiries/new"); expect(pg.locator(".crumbs b")).to_have_text("Inquiries")
    check("top bar shows the section and page, for sub-pages too", crumbs)
    def theme():
        # the OwnerDesk design system: the logo in the menu, Manrope, the dark base, the gold signal
        expect(pg.locator(".rail .mark .logo, .rail .mark img").first).to_be_visible()
        font = pg.evaluate("getComputedStyle(document.body).fontFamily")
        assert "Manrope" in font, font
        gold = pg.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--gold').trim()")
        assert gold.upper() == "#C9A84C", gold
        pg.goto(f"{B}/"); pg.wait_for_timeout(600)
    check("the OwnerDesk look is on: logo, Manrope, gold", theme)
    def slash_opens():
        pg.goto(f"{B}/leads"); expect(pg.locator(".sheet h1")).to_be_visible(); pg.keyboard.press("/"); expect(pg.locator(".finder")).to_be_visible()
        expect(pg.locator(".finder input")).to_be_focused(); pg.keyboard.press("Escape"); expect(pg.locator(".finder")).to_have_count(0)
    check("/ opens Go to and Esc closes it", slash_opens)
    def ctrl_k():
        pg.keyboard.press("Control+k"); expect(pg.locator(".finder")).to_be_visible(); pg.keyboard.press("Escape")
    check("Ctrl K opens Go to", ctrl_k)
    def by_meaning():
        pg.click(".appbar .find"); pg.keyboard.type("gst")
        expect(pg.locator(".finder-item").first).to_contain_text("GST set-aside")       # its name starts with "GST"
        expect(pg.locator(".finder-list")).to_contain_text("Compliance calendar")       # found by what it does
        expect(pg.locator(".finder-list")).to_contain_text("Quotations & invoices")
        pg.keyboard.press("Enter"); pg.wait_for_url("**/gst"); expect(pg.locator(".finder")).to_have_count(0)
        pg.keyboard.press("/"); pg.keyboard.type("passbook")                             # a word the name doesn't say
        expect(pg.locator(".finder-item").first).to_contain_text("Bank statement reader"); pg.keyboard.press("Escape")
    check("typing “gst” finds GST set-aside and the compliance calendar; “passbook” finds the bank reader", by_meaning)
    def arrows():
        pg.keyboard.press("/"); pg.keyboard.type("money")
        second = pg.locator(".finder-item").nth(1).locator("b").inner_text()
        pg.keyboard.press("ArrowDown"); expect(pg.locator(".finder-item.on b")).to_have_text(second)
        pg.keyboard.press("Escape")
    check("arrow keys move through the matches", arrows)
    def no_match():
        pg.keyboard.press("/"); pg.keyboard.type("zzzqqq"); expect(pg.locator(".finder-empty")).to_contain_text("No page matches"); pg.keyboard.press("Escape")
    check("no match says what to try", no_match)
    def typing_slash():
        pg.goto(f"{B}/quotes"); pg.click("button:has-text('New quotation')"); pg.wait_for_timeout(400)
        box = pg.locator("textarea").first; box.click(); box.type("10/12 ft"); expect(pg.locator(".finder")).to_have_count(0)
        assert "10/12" in box.input_value()
    check("typing “/” in a form doesn't open Go to", typing_slash)
    def recent():
        pg.goto(f"{B}/cash-flow"); pg.wait_for_timeout(300); pg.keyboard.press("/")
        expect(pg.locator(".finder-sec").first).to_have_text("Recently opened"); expect(pg.locator(".finder-item").first).to_contain_text("Cash flow")
        pg.keyboard.press("Escape")
    check("Go to lists recently opened pages first", recent)
    def locked():
        pg.keyboard.press("/"); pg.keyboard.type("workforce"); expect(pg.locator(".finder-item .where.lock").first).to_contain_text("On ")
        pg.keyboard.press("Escape")
    check("a locked page shows which plan it's on", locked)
    def account():
        pg.click(".me-btn"); expect(pg.locator(".me-menu")).to_contain_text("AI runs this month")
        pg.mouse.click(700, 500); expect(pg.locator(".me-menu")).to_have_count(0)
        pg.click(".me-btn"); pg.click(".me-menu a:has-text('Plans & billing')"); pg.wait_for_url("**/plans")
    check("account menu opens, closes on an outside click, and links work", account)

    m = b.new_page(viewport={"width": 390, "height": 844}); watch(m, "phone")
    def phone_bar():
        login(m, "9820000006"); m.wait_for_url(re.compile(r"/(home|dashboard)"))
        expect(m.locator(".tabbar")).to_be_visible(); expect(m.locator(".appbar")).to_be_hidden()
        m.click(".tabbar a:has-text('Send list')"); m.wait_for_url("**/approvals")
        m.click(".tabbar button:has-text('Go to')"); expect(m.locator(".finder")).to_be_visible(); m.keyboard.type("chats"); m.keyboard.press("Enter")
        m.wait_for_url("**/chats")
        wide = m.evaluate("document.documentElement.scrollWidth - window.innerWidth"); assert wide <= 2, wide
    check("phone: the bottom bar and Go to work, nothing scrolls sideways", phone_bar)
    b.close()

print("\n".join(f"{s} {n}" for s, n in results))
print(f"{sum(s == 'PASS' for s, _ in results)}/{len(results)} passed")
if problems:
    print("PROBLEMS:"); print("\n".join(" - " + x for x in problems))
