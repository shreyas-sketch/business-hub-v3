"""Browser checks for what the v2.1 + live event merge touched (20 checks): email and code login, the folding menu with the
admin pages, website photos and the QR, the live event link end to end on a phone, and the Membership preset (D1).
Run against a freshly seeded local hub, as for e2e.py, with demo email logins on:
   cd backend && APP_ENV=development APP_URL=http://localhost:8000 AI_PROVIDER=mock python3 seed_demo.py
   DEMO_PASSWORD=demopass123 python3 seed_demo.py --demo-logins && uvicorn app.main:app --port 8000
   python3 qa/merge_checks.py /tmp/shots
Re-seed before each run: the checks make accounts and open the event link.
"""
import re, sys, struct, zlib
from datetime import date, timedelta
from playwright.sync_api import sync_playwright, expect

B = "http://localhost:8000"; OUT = sys.argv[1]
results, problems = [], []
CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def gstin(first14="27ABCPX1234Q1Z"):
    t = 0
    for i, ch in enumerate(first14):
        p = CHARSET.index(ch) * (2 if i % 2 else 1); t += p // 36 + p % 36
    return first14 + CHARSET[(36 - t % 36) % 36]


def png(w=640, h=420):
    rows = b"".join(b"\x00" + b"".join(bytes([(x + y) % 256, (x * 2) % 256, 140]) for x in range(w)) for y in range(h))
    def chunk(t, d): return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def check(name, fn):
    try:
        fn(); results.append(("PASS", name)); print("PASS", name, flush=True)
    except Exception as e:
        results.append(("FAIL", name)); problems.append(f"{name}: {type(e).__name__}: {str(e)[:400]}"); print("FAIL", name, str(e)[:300], flush=True)


def watch(page, label):
    page.set_default_timeout(15000)
    page.on("pageerror", lambda e: problems.append(f"[{label}] page error: {e}"))
    page.on("console", lambda m: m.type == "error" and not re.search(r"status of (400|401|402|403|404|409|429)", m.text) and problems.append(f"[{label}] console: {m.text[:200]}"))
    page.on("response", lambda r: r.status >= 500 and problems.append(f"[{label}] {r.status} {r.url}"))
    page.on("dialog", lambda d: d.accept())


def otp_login(page, phone, url="/login"):
    page.goto(f"{B}{url}"); page.fill("input[inputmode=tel]", phone); page.click("button:has-text('Send code')")
    page.wait_for_selector(".prompt"); code = re.search(r"(\d{6})", page.inner_text(".prompt")).group(1)
    page.fill("input[autocomplete=one-time-code]", code); page.click("button:has-text('Log in')")


def new_page(b, label, w=1366, h=900):
    pg = b.new_context(viewport={"width": w, "height": h}).new_page(); watch(pg, label); return pg


with sync_playwright() as p:
    b = p.chromium.launch()

    # ── Login: main's email login, the manager's "/" landing ──
    pg = new_page(b, "email")
    def email_login():
        pg.goto(f"{B}/login"); pg.click("[role=tab]:has-text('Email')")
        pg.fill("input[type=email]", "legacy@demo.hub"); pg.fill("input[type=password]", "demopass123"); pg.click("button:has-text('Log in')")
        pg.wait_for_url("**/home"); expect(pg.locator(".nav")).to_be_visible()
    check("email + password login lands on the owner's home", email_login)
    def email_wrong():
        c = new_page(b, "email-wrong"); c.goto(f"{B}/login?way=email")
        c.fill("input[type=email]", "legacy@demo.hub"); c.fill("input[type=password]", "wrong-password"); c.click("button:has-text('Log in')")
        expect(c.locator(".toast.err")).to_be_visible(); assert "/login" in c.url
    check("wrong password is refused", email_wrong)
    def email_register():
        c = new_page(b, "register"); c.goto(f"{B}/login?way=email&signup=1")
        c.fill("input[autocomplete=name]", "Neha QA"); c.fill("input[type=email]", "neha.qa@example.in"); c.fill("input[type=password]", "longpassword1")
        c.fill("input[autocomplete=tel]", "98301 23456"); c.click("button:has-text('Create my free hub')")
        c.wait_for_url(re.compile(r"/(start|home)"));
    check("email sign-up creates an account and goes in", email_register)
    def demo_otp():
        c = new_page(b, "demo-otp"); otp_login(c, "9820000002"); c.wait_for_url("**/home")
    check("demo number code login (shown on screen) lands on home", demo_otp)
    def next_param():
        c = new_page(b, "next"); otp_login(c, "9820000004", "/login?next=/leads"); c.wait_for_url("**/leads")
        d = new_page(b, "next-evil"); otp_login(d, "9820000003", "/login?next=//evil.example"); d.wait_for_url("**/home"); assert d.url.startswith(B)
    check("login honours ?next= inside the hub", next_param)
    def control_redirect():
        pg.goto(f"{B}/control"); pg.wait_for_url("**/dashboard")
    check("/control redirects to the dashboard", control_redirect)

    # ── Menu: main's foldable menu with the manager's admin pages ──
    a = new_page(b, "admin")
    def admin_menu():
        otp_login(a, "9999900000"); a.wait_for_url("**/admin")
        for label, path in [("Live event", "/admin/event"), ("Day-1 reports", "/admin/day1"), ("Compliance rules", "/admin/compliance"),
                            ("Email", "/admin/email"), ("Features & plans", "/admin/features"), ("Manage member calls", "/admin/calls")]:
            a.locator(f".nav a:has-text('{label}')").first.click(); a.wait_for_url(f"**{path}"); expect(a.locator("h1")).to_be_visible()
    check("admin menu: every admin page opens", admin_menu)
    def fold():
        c = new_page(b, "fold"); otp_login(c, "9820000001"); c.wait_for_url("**/home")
        g = c.locator(".nav button.group[aria-expanded=false]").first; name = g.inner_text().split("\n")[0]
        g.click(); expect(c.locator(f".nav button.group:has-text('{name}')")).to_have_attribute("aria-expanded", "true")
        c.reload(); expect(c.locator(f".nav button.group:has-text('{name}')")).to_have_attribute("aria-expanded", "true")
    check("long menu folds, a group opens and stays open after reload", fold)
    def subtitle():
        c = new_page(b, "sub"); otp_login(c, "9820000004"); c.wait_for_url("**/home")
        expect(c.locator(".nav a:has-text('Knowledge Hub') .nav-sub")).to_have_text("Business Brain")
    check("menu item subtitle (Knowledge Hub · Business Brain) shows", subtitle)

    # ── Website: main's photos + the manager's QR ──
    w = new_page(b, "website")
    def photo_upload():
        otp_login(w, "9820000007"); w.wait_for_url("**/dashboard"); w.goto(f"{B}/website")
        w.locator("label:has-text('Upload main photo') input[type=file]").set_input_files(files=[{"name": "shop.png", "mimeType": "image/png", "buffer": png()}])
        expect(w.locator("label:has-text('Replace main photo')")).to_be_visible()
        w.wait_for_timeout(1500)
        r = w.request.get(f"{B}/api/site"); site = r.json(); hero = (site.get("photos") or {}).get("hero")
        assert hero and hero.get("kind") == "upload", site.get("photos")
    check("upload a main photo on the website page", photo_upload)
    def photo_public():
        c = new_page(b, "site-visitor", 390, 844); c.goto(f"{B}/s/kaveri-home-appliances")
        imgs = c.locator("img[src*='/photo/']"); expect(imgs.first).to_be_visible()
        ok = c.evaluate("[...document.querySelectorAll(\"img[src*='/photo/']\")].every(i => i.complete && i.naturalWidth > 0)"); assert ok, "photo didn't load"
        c.fill("input[name=name]", "QA Visitor"); c.fill("input[name=phone]", "98250 99999"); c.fill("textarea[name=message]", "Need a quote for a chimney")
        c.click("button:has-text('Send')"); expect(c.locator(".ok")).to_be_visible()
    check("public website shows the uploaded photo and takes an inquiry", photo_public)
    def qr():
        w.click("button:has-text('Show the QR')"); img = w.locator(".modal img, [role=dialog] img").first; expect(img).to_be_visible()
        w.wait_for_function("() => { const i = document.querySelector('.modal img, [role=dialog] img'); return i && i.complete && i.naturalWidth > 100 }")
        w.screenshot(path=f"{OUT}/qr.png"); w.keyboard.press("Escape")
    check("website QR code opens and loads", qr)
    def photo_remove():
        w.reload(); w.wait_for_timeout(1000)
        before = (w.request.get(f"{B}/api/site").json().get("photos") or {}).get("hero")
        r = w.request.post(f"{B}/api/site/photos/remove", data={"slot": "hero", "index": 0}); assert r.ok, r.text()
        assert not (r.json().get("photos") or {}).get("hero") and before
    check("remove the main photo", photo_remove)

    # ── The live event link, end to end on a phone ──
    def open_link():
        a.goto(f"{B}/admin/event"); expect(a.locator("h1")).to_contain_text("live")
        a.locator("text=Link open").click()
        a.fill("input[type=date]", (date.today() + timedelta(days=3)).isoformat()); a.click("button:has-text('Save')")
        a.wait_for_timeout(800); a.reload()
        r = a.request.get(f"{B}/api/event/form?e=BAI-OCT-11").json(); assert r.get("open") is not False and r.get("valid"), r
    check("admin opens the event link and sets the trial end", open_link)
    e = new_page(b, "attendee", 390, 844); token = {}
    def build_pack():
        e.goto(f"{B}/event?e=BAI-OCT-11"); expect(e.locator("h1")).to_contain_text("minute")
        e.locator("text=I don't have a website").click()
        e.fill("input[placeholder='Riya Tiffins']", "Sai QA Tiffins"); e.fill("input[placeholder='Pune']", "Pune")
        e.fill("textarea", "Home-cooked lunch tiffins delivered to offices in Baner and Aundh")
        e.fill("input[placeholder='27ABCDE1234F1Z5']", gstin()); e.wait_for_timeout(800)
        seg = e.locator("[role=group][aria-label='Type of business'] button")
        if seg.count(): seg.first.click()
        e.fill("input[aria-label='People who work with you']", "3")
        e.locator(".d1-consent").last.click(); e.click("button:has-text('Build my pack')")
        e.wait_for_url("**/event/r/**"); token["t"] = e.url.rsplit("/", 1)[1]
        expect(e.locator("h1")).to_contain_text("built", timeout=90000)
        e.screenshot(path=f"{OUT}/event_pack.png", full_page=True)
        wide = e.evaluate("document.documentElement.scrollWidth - window.innerWidth"); assert wide <= 2, f"scrolls sideways by {wide}px"
    check("attendee builds a pack on a phone (no website, GST number)", build_pack)
    def save_pack():
        e.locator("button:has-text('Save it to my hub')").first.click()
        e.fill("input[placeholder='Asha']", "Sai QA"); e.fill("input[placeholder='you@yourbusiness.in']", "sai.qa@example.in"); e.fill("input[placeholder='98200 00000']", "98301 77777")
        e.click("button:has-text('Send my code')"); e.wait_for_selector(".prompt")
        code = re.search(r"(\d{6})", e.inner_text(".prompt")).group(1); e.fill("input[aria-label='6-digit code']", code)
        e.click("button:has-text('Save to my hub')"); expect(e.locator("h2:has-text('Saved')")).to_be_visible()
        e.click("button:has-text('Open my hub')"); e.wait_for_url(re.compile(r"/(home|start|knowledge|dashboard)"))
        me = e.request.get(f"{B}/api/me").json(); assert me["user"].get("phone", "").endswith("9830177777"), me["user"]
        assert any(not i.get("locked") and i["label"] == "Compliance calendar" for g in me["menu"] for i in g["items"]), "Membership trial not open"
        e.screenshot(path=f"{OUT}/event_hub.png")
    check("attendee saves with the WhatsApp code and lands in a hub with Membership open", save_pack)
    def pack_in_hub():
        for path, text in [("/knowledge", "Sai QA Tiffins"), ("/compliance", "GST")]:
            e.goto(f"{B}{path}"); expect(e.locator("main, .main").first).to_contain_text(text, timeout=15000)
    check("the pack is inside the hub (Knowledge Hub, compliance calendar)", pack_in_hub)
    def admin_live():
        a.goto(f"{B}/admin/event"); expect(a.locator("text=Saved to a hub").locator("..")).to_contain_text("1", timeout=12000)
    check("admin live numbers count the saved pack", admin_live)
    def reuse_token():
        c = new_page(b, "replay", 390, 844); r = c.request.post(f"{B}/api/event/runs/{token['t']}/code", data={"phone": "9830188888"})
        assert r.status in (400, 403, 409, 410), (r.status, r.text())
    check("a saved pack can't be claimed again by another number", reuse_token)

    # ── D1: move the guide's features into Membership ──
    def d1():
        a.goto(f"{B}/admin/event"); a.click("button:has-text('Move them into Membership')"); expect(a.locator(".toast")).to_contain_text("Membership")
        m = new_page(b, "member-after-d1"); otp_login(m, "9820000002"); m.wait_for_url("**/home")
        me = m.request.get(f"{B}/api/me").json()
        open_ = {i["label"] for g in me["menu"] for i in g["items"] if not i.get("locked")}
        for f in ["Quotations & invoices", "Sales pipeline", "Lead magnet", "Revenue levers", "Money owed"]:
            assert f in open_, f"{f} still locked"
        m.goto(f"{B}/quotes"); expect(m.locator("h1")).to_be_visible(); m.screenshot(path=f"{OUT}/member_quotes.png")
    check("D1 button opens the 5 features for Membership members", d1)

    b.close()

print("\n".join(f"{s} {n}" for s, n in results))
print(f"{sum(s == 'PASS' for s, _ in results)}/{len(results)} passed")
if problems:
    print("PROBLEMS:"); print("\n".join(" - " + x for x in problems))
