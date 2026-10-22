"""Browser QA of OwnerDesk v2.3 (the rename and the design system) together with the hub's navigation: the brand files, the
server-rendered pages in the new colours, the badge, the QR codes, the AI buttons (teal, busy while they work), the Launch Kit
poster, the Day-1 report end to end, and a crawl of every page and link in the menu, watching for a missing file or a crash.
Run against a freshly seeded local hub (as for e2e.py):  python3 qa/v23_checks.py /tmp/shots
"""
import re
import sys
import time
import urllib.request

from playwright.sync_api import expect, sync_playwright

B = "http://localhost:8000"
OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp"
OFFICE, GROWTH, LAUNCH, FREE, ADMIN = "9820000001", "9820000006", "9820000007", "9820000004", "9999900000"
OLD = re.compile(r"Business AI Action Hub|#0A1826|#3FD6C9|#EEF4F6", re.I)   # the v2.2 name and colours
TEAL = "rgb(46, 211, 183)"
results, problems = [], []


def check(name, fn):
    try:
        fn(); results.append(("PASS", name)); print("PASS", name, flush=True)
    except Exception as e:  # noqa: BLE001 — every failure is reported, the run goes on
        results.append(("FAIL", name)); problems.append(f"{name}: {type(e).__name__}: {str(e)[:500]}"); print("FAIL", name, flush=True)


def fetch(path):
    try:
        with urllib.request.urlopen(B + path) as r:
            return r.status, r.headers.get("content-type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("content-type", ""), e.read()


def watch(page, label):
    page.set_default_timeout(15000)
    page.on("pageerror", lambda e: problems.append(f"[{label}] page error: {e}"))
    page.on("console", lambda m: m.type == "error" and not re.search(r"status of (400|401|402|403|404|409|429)", m.text)
            and problems.append(f"[{label}] console: {m.text[:200]}"))
    # Any file the app itself needs (not an API answer) that is missing is a bug; so is any server error
    page.on("response", lambda r: (r.status >= 500 or (r.status >= 400 and r.url.startswith(B) and "/api/" not in r.url))
            and problems.append(f"[{label}] {r.status} {r.url}"))
    page.on("dialog", lambda d: d.accept())


def login(b, phone, label, w=1366, h=900):
    ctx = b.new_context(viewport={"width": w, "height": h}, accept_downloads=True)
    ctx.route(re.compile(r"https://(wa\.me|api\.whatsapp\.com)/.*"), lambda r: r.fulfill(body="WhatsApp stand-in", content_type="text/plain"))
    pg = ctx.new_page(); watch(pg, label)
    pg.goto(f"{B}/login"); pg.fill("input[inputmode=tel]", phone); pg.click("button:has-text('Send code')")
    pg.wait_for_selector(".prompt"); code = re.search(r"(\d{6})", pg.inner_text(".prompt")).group(1)
    pg.fill("input[autocomplete=one-time-code]", code); pg.click("button:has-text('Log in')")
    pg.wait_for_url(re.compile(r"/(home|dashboard|admin)"))
    return pg


# ───────────────────────── files and pages served without the app ─────────────────────────
def assets():
    want = {"/brand/ownerdesk-logo.svg": "image/svg", "/brand/ownerdesk-logo-large.svg": "image/svg", "/brand/ownerdesk-mark.svg": "image/svg",
            "/favicon.ico": "image/", "/favicon-32.png": "image/png", "/apple-touch-icon.png": "image/png", "/icon-192.png": "image/png",
            "/icon-512.png": "image/png", "/fonts/manrope-latin-400-normal.woff2": "font/woff2",
            "/fonts/manrope-latin-700-normal.woff2": "font/woff2", "/fonts/manrope-latin-800-normal.woff2": "font/woff2"}
    bad = []
    for path, kind in want.items():
        st, ct, body = fetch(path)
        if st != 200 or kind not in ct or len(body) < 200:
            bad.append(f"{path}: {st} {ct} {len(body)}B")
        if path.endswith(".png") and body[:8] != b"\x89PNG\r\n\x1a\n":
            bad.append(f"{path}: not a PNG")
    assert not bad, bad


def manifest():
    import json
    st, ct, body = fetch("/manifest.webmanifest")
    m = json.loads(body)
    assert st == 200 and m["name"] == "OwnerDesk" and m["theme_color"].upper() == "#14151A", m
    for i in m["icons"]:
        assert fetch(i["src"])[0] == 200, i
    st, _, html = fetch("/")
    html = html.decode()
    assert "<title>OwnerDesk</title>" in html and 'content="#14151A"' in html and "/favicon.ico" in html, html[:600]


def badge():
    _, _, free = fetch("/s/mehta-engineering-works")                  # a Free owner: the badge, with the new name
    _, _, paid = fetch("/s/shree-ganesh-interiors")                   # Office: no badge
    free, paid = free.decode(), paid.decode()
    assert "Built free with <b>OwnerDesk</b>" in free, "no OwnerDesk badge on a Free site"
    assert "Built free with" not in paid, "a paid site shows the badge"
    assert not OLD.search(free) and not OLD.search(paid), OLD.search(free + paid).group(0)


def public_pages():
    seen = {}
    for path in ["/s/mehta-engineering-works/card", "/s/shree-ganesh-interiors/card", "/s/kapoor-dental-clinic/card"]:
        st, ct, body = fetch(path)
        seen[path] = st
        assert st == 200 and "text/html" in ct, (path, st)
        assert not OLD.search(body.decode()), (path, OLD.search(body.decode()).group(0))
    guides = {slug: fetch(f"/s/{slug}/guide") for slug in ["shree-ganesh-interiors", "sharma-modular-kitchens", "mehta-engineering-works"]}
    for slug, (st, _, body) in guides.items():   # a guide is there only when the owner has switched it on
        assert st in (200, 404) and not OLD.search(body.decode()), (slug, st)
    st, ct, body = fetch("/s/mehta-engineering-works/card.vcf")
    assert st == 200 and b"BEGIN:VCARD" in body, (st, body[:80])
    st, _, body = fetch("/s/no-such-business")
    body = body.decode()
    assert st == 404 and "#14151A" in body and "Manrope" in body and "isn't live" in body, (st, body[:300])


def score_page():
    from pymongo import MongoClient
    import os
    tok = MongoClient()[os.getenv("DB_NAME", "qa_mc")].scores.find_one({}, {"token": 1})["token"]
    st, _, body = fetch(f"/score/{tok}")
    body = body.decode()
    assert st == 200 and "#C9A84C" in body and "Manrope" in body and not OLD.search(body), (st, body[:300])
    assert fetch("/score/not-a-token")[0] == 404


def run(b):
    check("brand files: logo, mark, favicons, app icons and Manrope fonts are served", assets)
    check("app manifest and page head say OwnerDesk, in #14151A", manifest)
    check("a Free owner's website shows “Built free with OwnerDesk”; a paid one shows no badge", badge)
    check("visiting card, guide, vCard and the not-found page are in the OwnerDesk colours", public_pages)
    check("a shared Business Score page is in the OwnerDesk colours", score_page)

    # ───────────────────────── inside the hub ─────────────────────────
    pg = login(b, OFFICE, "office")

    def program():
        me = pg.evaluate("fetch('/api/me').then(r => r.json())")
        assert me["program"]["name"] == "OwnerDesk", me["program"]
        pg.goto(f"{B}/home"); pg.wait_for_selector(".sheet-foot")
        assert "ownerdesk" in pg.inner_text(".sheet-foot").lower(), pg.inner_text(".sheet-foot")
        assert pg.evaluate("document.fonts.ready.then(() => document.fonts.check('800 20px Manrope') && document.fonts.check('400 16px Manrope'))"), "Manrope isn't loaded"
        assert "Manrope" in pg.evaluate("getComputedStyle(document.body).fontFamily")
        logo = pg.locator(".rail img.logo:visible").first
        expect(logo).to_be_visible()
        assert pg.evaluate("(i) => i.complete && i.naturalWidth > 0", logo.element_handle()), "the logo didn't load"
    check("inside the hub: OwnerDesk in the footer, Manrope loaded, the logo shows", program)

    def qr():
        r = pg.request.get(f"{B}/api/site/qr.png")
        assert r.status == 200 and r.body()[:4] == b"\x89PNG", r.status
    check("the website QR code is a PNG", qr)

    def crawl():
        """Every menu page, then every link inside them: each opens without an error, and none falls back to Home unasked."""
        me = pg.evaluate("fetch('/api/me').then(r => r.json())")
        menu = [it["path"] for g in me["menu"] for it in g["items"] if not it.get("locked")]
        todo, done, bounced, links = list(dict.fromkeys(menu)), set(), [], set()
        api4 = []   # an API refusal while a page simply opens is worth a look (a plan lock, 402/403, is expected)
        pg.on("response", lambda r: "/api/" in r.url and r.status in (400, 404, 405, 422) and api4.append(f"{r.status} {r.url[len(B):]}"))
        while todo:
            path = todo.pop(0)
            if path in done:
                continue
            done.add(path)
            pg.goto(f"{B}{path}"); pg.wait_for_load_state("networkidle")
            here = pg.evaluate("location.pathname")
            if here != path.split("?")[0] and here in ("/home", "/"):
                bounced.append(path)
            if pg.locator(".err, [role=alert]", has_text="Something went wrong").count():
                problems.append(f"[crawl] {path}: Something went wrong")
            if path in menu:
                for h in pg.eval_on_selector_all("main a[href^='/'], .desk a[href^='/']", "as => as.map(a => a.getAttribute('href'))"):
                    h = h.split("#")[0]
                    if h and not h.startswith(("/api/", "/s/", "/r/", "/score/")) and h not in done:
                        links.add(h); todo.append(h)
        assert len(done) > 30, len(done)
        assert not bounced, f"these links land on Home: {bounced}"
        print(f"     crawled {len(done)} pages ({len(links)} found inside pages)")
        for x in sorted(set(api4)):
            print("     api refusal on open:", x)
    check("crawl: every menu page and every link inside them opens, none bounces to Home", crawl)
    pg.context.close()

    # ───────────────────────── AI buttons ─────────────────────────
    pg = login(b, GROWTH, "growth")

    def ai_button():
        pg.goto(f"{B}/week"); btn = pg.locator("button.ai", has_text="Plan my week")
        if not btn.count():
            pg.goto(f"{B}/offers"); btn = pg.locator("button.ai").first
        expect(btn).to_be_visible()
        assert pg.evaluate("(e) => getComputedStyle(e).backgroundColor", btn.element_handle()) == TEAL or \
            pg.evaluate("(e) => getComputedStyle(e).color", btn.element_handle()) == TEAL, "the AI button isn't teal"
        assert pg.evaluate("(e) => getComputedStyle(e, '::before').content", btn.element_handle()) == '"✦"'
        held = []   # the AI call is held here (not answered) until the busy state has been seen
        pg.route(re.compile(r".*/api/(week|offers|offer).*"), lambda r: held.append(r) if r.request.method == "POST" else r.continue_())
        btn.click()
        expect(pg.locator("button.ai.is-busy")).to_have_count(1)
        assert pg.evaluate("document.querySelector('button.ai.is-busy').disabled")
        for _ in range(100):
            if held:
                break
            pg.wait_for_timeout(50)
        assert held, "the AI button sent nothing"
        for r in held:
            r.continue_()
        expect(pg.locator("button.ai.is-busy")).to_have_count(0, timeout=20000)
        pg.unroute(re.compile(r".*/api/(week|offers|offer).*"))
    check("an AI button is teal with ✦, shows the busy state while it works, and comes back", ai_button)

    def plain_buttons_gold():
        pg.goto(f"{B}/home")
        prim = pg.locator(".btn.primary:not(.ai)").first
        expect(prim).to_be_visible()
        assert pg.evaluate("(e) => getComputedStyle(e).backgroundColor", prim.element_handle()) == "rgb(201, 168, 76)"
    check("a plain main button is gold, not teal", plain_buttons_gold)
    pg.context.close()

    # ───────────────────────── Launch Kit poster ─────────────────────────
    pg = login(b, LAUNCH, "launch")

    def poster():
        pg.goto(f"{B}/launch-kit"); pg.wait_for_selector("canvas.fr-poster")
        pg.wait_for_timeout(800)
        with pg.expect_download() as d:
            pg.click("button:has-text('Download PNG')")
        f = d.value.path()
        data = open(f, "rb").read()
        assert data[:4] == b"\x89PNG" and len(data) > 20000, len(data)
        assert d.value.suggested_filename.endswith("-status.png"), d.value.suggested_filename
        # The canvas is drawn in Manrope once it has loaded
        assert pg.evaluate("document.fonts.check('500 100px Manrope')")
    check("Launch Kit: the WhatsApp Status poster downloads as a PNG, drawn in Manrope", poster)
    pg.context.close()

    # ───────────────────────── Day-1 report (admin opens it, an attendee fills it) ─────────────────────────
    adm = login(b, ADMIN, "admin")

    def day1_open():
        adm.goto(f"{B}/admin/day1"); adm.wait_for_selector("text=BAI-OCT-11")
        panel = adm.locator(".panel", has_text="BAI-OCT-11").first
        sw = panel.locator("[role=switch], input[type=checkbox]").first
        if "Closed" in panel.inner_text():
            sw.click(); expect(panel).to_contain_text("Open")
        q = adm.request.get(f"{B}/api/admin/day1/cohorts/BAI-OCT-11/qr.png")
        if q.status != 404:
            assert q.status == 200 and q.body()[:4] == b"\x89PNG", q.status
    check("Admin: the Day-1 webinar opens for reports", day1_open)

    def day1_fill():
        ctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        p = ctx.new_page(); watch(p, "day1")
        p.goto(f"{B}/report?c=BAI-OCT-11"); p.wait_for_selector("input[inputmode=tel]")
        p.fill("input[inputmode=tel]", "9876500011"); p.fill("input[autocomplete=given-name]", "Riya"); p.fill("input[autocomplete=address-level2]", "Pune")
        for c in p.locator("input[type=checkbox]").all():
            c.check()
        p.click("button:has-text('Next')")
        p.locator("label:has-text(\"don't have a website\") input, input[type=checkbox]").first.check()
        p.click("button:has-text('Next')")
        for g in p.locator(".d1-seg").all():
            g.locator("button").first.click()
        p.click("button:has-text('Next')")
        for g in p.locator(".d1-seg").all():
            g.locator("button").first.click()
        for n in p.locator("input.d1-num").all():
            n.fill(n.get_attribute("min") or "10") if (n.get_attribute("min") or "0") != "0" else n.fill("10")
        p.click("button:has-text('Make my report')")
        p.wait_for_url(re.compile(r"/report/r/[^/?]+$"))
        expect(p.locator("text=here is where you")).to_be_visible(timeout=60000)
        assert re.search(r"\d+", p.inner_text(".fr-score"))
        assert p.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), "the report scrolls sideways on a phone"
        token = p.evaluate("location.pathname").split("/")[-1]
        r = p.request.get(f"{B}/r/{token}/preview.png")
        assert r.status in (200, 404), r.status
        p.screenshot(path=f"{OUT}/v23_day1_report.png", full_page=True)
        ctx.close()
    check("Day-1: an attendee fills the four steps on a phone and gets their score and findings", day1_fill)

    def admin_event_qr():
        adm.goto(f"{B}/admin/event"); adm.wait_for_load_state("networkidle")
        img = adm.locator("img[src*='qr']").first
        if img.count():
            assert adm.evaluate("(i) => i.complete && i.naturalWidth > 0", img.element_handle()), "the event QR didn't load"
    check("Admin: the live event QR loads", admin_event_qr)
    adm.context.close()

    # ───────────────────────── a Free owner's hub, on a phone ─────────────────────────
    pg = login(b, FREE, "free-phone", 390, 844)

    def phone():
        pg.goto(f"{B}/home"); pg.wait_for_selector(".tabbar")
        img = pg.locator("img.logo:visible").first
        expect(img).to_be_visible()
        for path in ["/home", "/website", "/inquiries", "/plans", "/launch-kit"]:
            pg.goto(f"{B}{path}"); pg.wait_for_load_state("networkidle")
            assert pg.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), f"{path} scrolls sideways"
            assert not OLD.search(pg.content()), (path, OLD.search(pg.content()).group(0))
    check("phone: the logo shows, and Free pages neither scroll sideways nor carry the old name", phone)
    pg.context.close()


with sync_playwright() as p:
    br = p.chromium.launch()
    run(br)
    br.close()

print()
print(f"{sum(1 for r, _ in results if r == 'PASS')}/{len(results)} passed")
for x in problems:
    print("PROBLEM", x)
sys.exit(1 if problems or any(r == "FAIL" for r, _ in results) else 0)
