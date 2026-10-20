"""Browser click-through of the free journey, the Business Score, plans and the referral loop (23 checks). Run against a freshly seeded local hub:
   cd backend && APP_ENV=development APP_URL=http://localhost:8000 AI_PROVIDER=mock python3 seed_demo.py
   uvicorn app.main:app --port 8000      (with frontend built: cd frontend && npm run build)
   pip install playwright && playwright install chromium && python3 qa/e2e.py /tmp/shots
   Re-seed before each run: the checks create owners and a cohort.
"""
import re, sys, traceback
from playwright.sync_api import sync_playwright, expect

BASE = "http://localhost:8000"; OUT = sys.argv[1]
DIALOGS, DISMISS = [], []
results = []; problems = []

def check(name, fn):
    try:
        fn(); results.append(("PASS", name))
    except Exception as e:
        results.append(("FAIL", name)); problems.append(f"{name}: {type(e).__name__}: {str(e)[:300]}")

def watch(page, label):
    page.on("pageerror", lambda e: problems.append(f"[{label}] page error: {e}"))
    # 401 before login, 400 for the deliberate bad inputs and 402 at the run limit are expected
    page.on("console", lambda m: m.type == "error" and not re.search(r"status of (400|401|402)", m.text) and problems.append(f"[{label}] console: {m.text[:200]}"))
    page.on("response", lambda r: r.status >= 500 and problems.append(f"[{label}] {r.status} {r.url}"))
    page.on("dialog", lambda d: (DIALOGS.append(d.message), d.dismiss() if DISMISS else d.accept()))

def go(page, label, scope=".nav"):
    """Click a menu link. On a long menu the groups start folded, so open any folded group first."""
    folded = page.locator(f"{scope} button.group[aria-expanded=false]")
    for _ in range(12):
        if not folded.count():
            break
        folded.first.click()
    page.locator(f"{scope} a:has-text('{label}')").first.click()

def login(page, phone):
    page.goto(f"{BASE}/login") if "/login" not in page.url else None
    page.fill("input[inputmode=tel]", phone); page.click("button:has-text('Send code')")
    page.wait_for_selector(".prompt"); code = re.search(r"(\d{6})", page.inner_text(".prompt")).group(1)
    page.fill("input[autocomplete=one-time-code]", code); page.click("button:has-text('Log in')")

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1366, "height": 900}); ctx.grant_permissions(["clipboard-read", "clipboard-write"])
    pg = ctx.new_page(); watch(pg, "owner")

    # ── Workshop attendee arrives through the cohort link ──
    def cohort_banner():
        pg.goto(f"{BASE}/join?c=BAI-OCT-11"); expect(pg.locator(".invite-banner")).to_contain_text("Business AI workshop")
    check("cohort link shows workshop welcome", cohort_banner)
    def bad_phone():
        pg.click("text=Start now"); pg.fill("input[inputmode=tel]", "12345"); pg.click("button:has-text('Send code')")
        expect(pg.locator(".toast")).to_contain_text("valid")
    check("invalid phone shows a clear error", bad_phone)
    def wrong_code():
        pg.fill("input[inputmode=tel]", "9830000001"); pg.click("button:has-text('Send code')"); pg.wait_for_selector(".prompt")
        pg.fill("input[autocomplete=one-time-code]", "000001"); pg.click("button:has-text('Log in')")
        expect(pg.locator(".toast")).to_contain_text("not right")
        code = re.search(r"(\d{6})", pg.inner_text(".prompt")).group(1)
        pg.fill("input[autocomplete=one-time-code]", code); pg.click("button:has-text('Log in')"); pg.wait_for_url("**/start")
    check("wrong code refused, right code lands on setup", wrong_code)
    def three_ways():
        expect(pg.locator("text=I have a website")).to_be_visible(); expect(pg.locator("text=Tell us by voice")).to_be_visible()
        pg.click("text=Type it")
        pg.fill("textarea", "My business is Patel Pumps & Motors in Ahmedabad. We rewind motors and repair industrial pumps for factories.")
        pg.click("button:has-text('Fill my Business Brain')")
        expect(pg.locator("input[placeholder='Shree Ganesh Interiors']")).to_have_value(re.compile("Patel Pumps"), timeout=8000)
        expect(pg.locator(".notice")).to_contain_text("from your words")
    check("Business Brain: three ways in; typed words fill a draft to check", three_ways)
    def onboarding():
        pg.fill("input[placeholder='Shree Ganesh Interiors']", "")
        expect(pg.locator("button:has-text('Next')")).to_be_disabled()
        pg.fill("input[placeholder='Shree Ganesh Interiors']", "Patel Pumps & Motors"); pg.fill("input[placeholder='Thane, Mumbai']", "Ahmedabad")
        pg.fill("input[placeholder^='Home and office']", "Industrial pumps and motor rewinding"); pg.click("button:has-text('Next')")
        pg.fill("input[aria-label='Product or service']", "Motor rewinding"); pg.fill("input[aria-label='Price in rupees']", "₹2,500"); pg.click("button:has-text('Next')")
        pg.fill("textarea", "Same-day pickup\nGenuine copper winding"); pg.click("button:has-text('Next')")
        expect(pg.locator("button:has-text('Write my brand message')")).to_be_disabled()
        pg.fill("input[placeholder='98200 00000']", "98300 00001"); pg.click("button:has-text('Write my brand message')")
        pg.wait_for_url("**/brand**"); expect(pg.locator(".grid3 .panel")).to_have_count(3, timeout=8000)
    check("4-step Business Brain then 3 brand options appear", onboarding)
    def brand():
        pg.locator(".grid3 .panel").nth(1).click(); expect(pg.locator(".grid3 .panel.active")).to_have_count(1)
        pg.click("button:has-text('Use this and build my website')"); pg.wait_for_url("**/website")
    check("pick a brand message → website", brand)
    def build_site():
        pg.click("button:has-text('Build my website')"); expect(pg.locator("text=Check it, then go")).to_be_visible(timeout=8000)
        pg.locator("label:has-text('Headline') input").fill("Motors rewound and back the same day")
        pg.click("button:has-text('Save')"); expect(pg.locator(".toast")).to_contain_text("Saved")
        frame = pg.frame_locator("iframe.preview-frame"); expect(frame.locator("h1")).to_have_text("Motors rewound and back the same day")
        pg.click("button:has-text('Publish')"); expect(pg.locator("h1")).to_contain_text("live", timeout=8000)
        expect(pg.locator(".panel.active .mono").first).to_contain_text("/s/patel-pumps-motors")
        pg.click("button:has-text('Copy link')"); expect(pg.locator(".toast")).to_contain_text("Copied")
    check("build, edit, save, preview, publish, copy link", build_site)
    def slug_rules():
        pg.locator("label:has-text('Web address') input").fill("admin"); pg.click("button:has-text('Save')")
        expect(pg.locator(".toast.err")).to_contain_text("web address"); pg.locator("label:has-text('Web address') input").fill("patel-pumps-motors")
    check("reserved web address refused", slug_rules)
    pg.screenshot(path=f"{OUT}/e2e_website.png")

    # ── A customer uses the public website ──
    cust = b.new_context(viewport={"width": 390, "height": 844}).new_page(); watch(cust, "customer")
    def inquiry():
        cust.goto(f"{BASE}/s/patel-pumps-motors"); expect(cust.locator("h1")).to_have_text("Motors rewound and back the same day")
        expect(cust.locator(".badge")).to_be_visible()
        cust.fill("input[name=name]", "Ramesh Shah"); cust.fill("input[name=phone]", "98250 12345"); cust.fill("textarea[name=message]", "5 HP motor burnt, need rewinding")
        cust.click("button:has-text('Send inquiry')"); expect(cust.locator(".ok")).to_contain_text("reached Patel Pumps")
    check("customer sends inquiry from phone, sees thank-you", inquiry)

    def leads():
        go(pg, "Leads"); expect(pg.locator("table.t tbody tr")).to_have_count(1)
        expect(pg.locator("h1")).to_contain_text("1 lead waiting")
        pg.click("button:has-text('Draft reply')"); expect(pg.locator(".modal textarea")).to_have_value(re.compile("Ramesh"))
        expect(pg.locator(".modal a:has-text('Open in WhatsApp')")).to_have_attribute("href", re.compile(r"wa\.me/919825012345\?text="))
        pg.click(".modal button:has-text('Close')"); pg.locator("table.t select").select_option("won")
        expect(pg.locator("h1")).to_contain_text("Every lead has a reply")
    check("lead arrives, reply drafted with WhatsApp link, status set", leads)
    def writer():
        go(pg, "AI Writer"); expect(pg.locator("text=Your writing assistant")).to_be_visible()
        pg.click("button:has-text(\"Write this week's posts\")"); expect(pg.locator("text=Copy").first).to_be_visible(timeout=8000)
        pg.click("button[role=tab]:has-text('Reply to a customer')"); pg.locator("textarea").last.fill("Do you repair submersible pumps?"); pg.click("button:has-text('Draft my reply')")
        expect(pg.locator(".prompt").first).to_contain_text("Patel Pumps")
        pg.click("button[role=tab]:has-text('Ask')"); pg.locator("textarea").last.fill("How do I get more factory customers?"); pg.click("button:has-text('Get my answer')")
        expect(pg.locator(".panel:has-text('Answer')").last).to_contain_text("Structure")
        pg.click("button[role=tab]:has-text('English polisher')"); expect(pg.locator("text=Membership").first).to_be_visible()
    check("AI Writer: explained, posts, reply, ask, polisher shows what's locked", writer)
    def score():
        pg.goto(f"{BASE}/score"); expect(pg.locator(".fr-seg")).to_have_count(10)
        for grp in pg.locator(".fr-seg").all(): grp.locator("button").first.click()
        pg.click("button:has-text('Work out my score')"); expect(pg.locator(".bigscore")).to_be_visible(timeout=8000)
        expect(pg.locator("input[aria-label='Your score card link']")).to_have_value(re.compile(r"/score/"))
    check("Business Score: 10 questions → score and a share card", score)
    def outputs():
        go(pg, "My outputs"); expect(pg.locator(".stack > .panel")).to_have_count(6)
        pg.locator(".stack > .panel").first.locator("button:has-text('Show')").click(); expect(pg.locator(".prompt").first).to_be_visible()
        pg.locator(".stack > .panel").first.locator("button:has-text('Delete')").click(); expect(pg.locator(".stack > .panel")).to_have_count(5)
    check("My outputs: 6 saved, show and delete", outputs)
    def home_progress():
        go(pg, "Home"); expect(pg.locator("h1")).to_contain_text("working"); expect(pg.locator(".progress .done")).to_have_count(5)
    check("home shows all five steps done", home_progress)
    def edit_brain():
        go(pg, "Knowledge Hub"); pg.locator("a:has-text('Edit')").first.click(); pg.wait_for_url("**/start")
        expect(pg.locator("input[placeholder='Shree Ganesh Interiors']")).to_have_value("Patel Pumps & Motors")
        for _ in range(3): pg.click("button:has-text('Next')")
        pg.click("button:has-text('Save')"); pg.wait_for_url("**/home")
    check("Business Brain can be edited later", edit_brain)
    def plans():
        go(pg, "Plans & billing"); pg.wait_for_url("**/plans")
        expect(pg.locator(".panel.active").first).to_contain_text("Free")
        expect(pg.locator("[id^=tier-]")).to_have_count(6)
        DISMISS.append(1); DIALOGS.clear()
        pg.locator("#tier-lite button:has-text('Start Membership')").click(); pg.wait_for_timeout(600)
        DISMISS.clear()
        assert DIALOGS and "Test payment of ₹1,999" in DIALOGS[0], DIALOGS
        expect(pg.locator("#tier-office")).to_contain_text("6,00,000")
    check("plans ladder and upgrade request", plans)

    # ── Viral loop: an owner clicks the badge on that website ──
    f = b.new_context(viewport={"width": 1366, "height": 900}).new_page(); watch(f, "friend")
    def badge_join():
        f.goto(f"{BASE}/s/patel-pumps-motors")
        with f.context.expect_page() as newp: f.click(".badge")
        j = newp.value; watch(j, "friend-join"); j.wait_for_load_state()
        expect(j.locator(".invite-banner")).to_contain_text("Patel Pumps & Motors invited you")
        j.click("text=Claim it"); login(j, "9830000002"); j.wait_for_url("**/start"); j.click("text=Fill the form myself")
        j.fill("input[placeholder='Shree Ganesh Interiors']", "Joshi Sweets"); j.fill("input[placeholder^='Home and office']", "Mithai and farsan shop")
        for _ in range(2): j.click("button:has-text('Next')")
        j.click("button:has-text('Next')"); j.fill("input[placeholder='98200 00000']", "98300 00002"); j.click("button:has-text('Write my brand message')")
        expect(j.locator(".grid3 .panel")).to_have_count(3, timeout=8000); j.locator(".grid3 .panel").first.click()
        j.click("button:has-text('Use this and build my website')"); j.click("button:has-text('Build my website')")
        j.click("button:has-text('Publish')", timeout=10000); expect(j.locator("h1")).to_contain_text("live", timeout=8000)
        go(j, "Plans & billing"); expect(j.locator(".panel.active").first).to_contain_text("Membership")
    check("badge → join with invite → friend gets Membership trial → goes live", badge_join)
    def referrer_rewarded():
        go(pg, "Home"); pg.reload(); expect(pg.locator(".notice")).to_contain_text("Joshi Sweets just went live")
        pg.click(".notice button"); expect(pg.locator(".notice")).to_have_count(0)
        go(pg, "Invite & earn"); expect(pg.locator("table.t")).to_contain_text("Joshi Sweets")
    check("referrer sees reward notice and friend in invite list", referrer_rewarded)

    # ── Free owner runs out of AI runs ──
    def limit():
        r = b.new_context(viewport={"width": 1366, "height": 900}).new_page(); watch(r, "limit")
        r.goto(f"{BASE}/login"); login(r, "9820000004"); r.wait_for_url("**/home"); r.goto(f"{BASE}/writer")
        for _ in range(12):
            r.click("button:has-text(\"Write this week's posts\")"); r.wait_for_timeout(250)
            if r.locator(".modal").count(): break
        expect(r.locator(".modal")).to_contain_text("used this month")
        expect(r.locator(".modal input")).to_have_value(re.compile(r"/join\?ref="))
    check("free owner at the limit sees invite-or-upgrade", limit)

    # ── Mobile menu ──
    def mobile():
        m = b.new_context(viewport={"width": 390, "height": 844}).new_page(); watch(m, "mobile")
        m.goto(f"{BASE}/login"); login(m, "9830000001"); m.wait_for_url("**/home")
        m.click("button:has-text('Menu')"); go(m, "Leads", ".rail.open"); m.wait_for_url("**/leads")
        expect(m.locator(".rail.open")).to_have_count(0); expect(m.locator("button:has-text('Draft reply')").first).to_be_visible()
        expect(m.locator("table.t select").first).to_be_visible(); m.screenshot(path=f"{OUT}/e2e_m_leads.png", full_page=True)
        m.goto(f"{BASE}/website"); m.wait_for_timeout(800); m.screenshot(path=f"{OUT}/e2e_m_website.png")
    check("mobile: menu works, leads keep status + reply action", mobile)

    # ── Admin ──
    def admin():
        a = b.new_context(viewport={"width": 1366, "height": 900}).new_page(); watch(a, "admin")
        a.goto(f"{BASE}/login"); login(a, "9999900000"); a.wait_for_url("**/admin"); a.goto(f"{BASE}/admin")
        expect(a.locator("h1")).to_contain_text("referral")
        a.fill("input[placeholder='BAI-OCT-11']", "BAI-NOV-08"); a.fill("input[placeholder^='Business AI workshop']", "Workshop 8 Nov"); a.click("button:has-text('Create')")
        expect(a.locator(".toast")).to_contain_text("join?c=BAI-NOV-08"); expect(a.locator("table.t:has(th:has-text('Signups'))")).to_contain_text("BAI-NOV-08")
        a.fill("input[placeholder^='Search']", "9830000001"); a.wait_for_timeout(600)
        row = a.locator("table.t:has(th:has-text('Bonus runs'))").locator("tbody tr").first; expect(row).to_contain_text("Patel Pumps")
        row.locator("select").select_option("lite"); expect(a.locator(".toast")).to_contain_text("Plan updated")
        row.locator("button:has-text('+10')").click(); expect(row).to_contain_text("10")
        a.fill("input[placeholder^='Search']", "9820000004"); a.wait_for_timeout(600)
        mrow = a.locator("table.t:has(th:has-text('Bonus runs'))").locator("tbody tr").first; mrow.locator("button:has-text('Pause')").click()
        expect(a.locator(".toast")).to_contain_text("Paused"); expect(mrow).to_contain_text("paused")
        assert a.request.get(f"{BASE}/s/mehta-engineering-works").status == 404
        mrow.locator("button:has-text('Resume')").click(); expect(a.locator(".toast")).to_contain_text("Resumed")
        assert a.request.get(f"{BASE}/s/mehta-engineering-works").status == 200
        a.fill("input[placeholder^='Search']", "9830000001"); a.wait_for_timeout(600)
        a.screenshot(path=f"{OUT}/e2e_admin.png", full_page=True)
        a.set_viewport_size({"width": 390, "height": 844}); a.fill("input[placeholder^='Search']", ""); a.wait_for_timeout(600)
        expect(a.locator("table.t:has(th:has-text('Bonus runs'))").locator("button:has-text('+10')").first).to_be_visible(); a.screenshot(path=f"{OUT}/e2e_m_admin.png", full_page=True)
    check("admin: cohort, search, plan change, bonus runs, pause/resume", admin)
    def paid_owner_effects():
        cust.goto(f"{BASE}/s/patel-pumps-motors"); expect(cust.locator(".badge")).to_have_count(0)
        cust.fill("input[name=name]", "Kiran Patel"); cust.fill("input[name=phone]", "9825099999"); cust.click("button:has-text('Send inquiry')")
        go(pg, "Leads"); pg.reload(); expect(pg.locator("table.t")).to_contain_text("Kiran Patel")
        expect(pg.locator("table.t")).not_to_contain_text("Auto-replied")   # below ₹1.5L nothing goes to customers on its own
        pg.goto(f"{BASE}/week"); expect(pg.locator("text=Plan my week")).to_be_visible()
    check("after upgrade: badge gone, lead arrives, no auto-message, weekly plan opens", paid_owner_effects)
    def logout():
        pg.click(".rail >> button:has-text('Log out')"); pg.wait_for_url(BASE + "/"); expect(pg.locator("h1")).to_contain_text("live")
    check("log out returns to the public page", logout)
    b.close()

for s, n in results: print(s, n)
print("\nPROBLEMS:" if problems else "\nNo console errors, page errors or server errors.")
for x in problems: print(" -", x)
