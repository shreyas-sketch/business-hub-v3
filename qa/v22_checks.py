"""Browser QA of the v2.2 features, following the Feature Guide's "Show it live" steps and the run sheet's demos 10 and 11.
Run against a freshly seeded local hub (as for e2e.py), started with FETCH_ALLOW_PRIVATE=true so Competitor Watch can read a
website served by the hub itself:  python3 qa/v22_checks.py /tmp/shots
Re-seed before each run: the checks add expenses, reviews, competitors and settings.
"""
import re
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

B = "http://localhost:8000"
OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp"
ROOT = Path(__file__).resolve().parents[1]
LAUNCH, GROWTH, MEMBER, FREE, ADMIN, STAFF, MANAGER = "9820000007", "9820000006", "9820000002", "9820000004", "9999900000", "9820000012", "9820000011"
results, problems = [], []
PAGES = []   # every page opened, so a failed check can save what each one showed


def check(name, fn):
    try:
        fn(); results.append(("PASS", name)); print("PASS", name, flush=True)
    except Exception as e:  # noqa: BLE001 — every failure is reported, the run goes on
        results.append(("FAIL", name)); problems.append(f"{name}: {type(e).__name__}: {str(e)[:500]}"); print("FAIL", name, flush=True)
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower())[:40]
        for i, pg in enumerate(x for x in PAGES[-3:] if not x.is_closed()):
            try:
                pg.screenshot(path=f"{OUT}/fail_{slug}_{i}.png", full_page=True)
            except Exception:  # noqa: BLE001 — a screenshot is a bonus
                pass


def watch(page, label):
    PAGES.append(page)
    page.set_default_timeout(15000)
    page.on("pageerror", lambda e: problems.append(f"[{label}] page error: {e}"))
    page.on("console", lambda m: m.type == "error" and not re.search(r"status of (400|401|402|403|404|409|429)", m.text)
            and problems.append(f"[{label}] console: {m.text[:200]}"))
    page.on("response", lambda r: r.status >= 500 and problems.append(f"[{label}] {r.status} {r.url}"))
    page.on("dialog", lambda d: d.accept())


def login(b, phone, label, w=1366, h=900):
    ctx = b.new_context(viewport={"width": w, "height": h}, accept_downloads=True)
    # WhatsApp itself can't be reached from here: answer for it, so a "Send on WhatsApp" tab loads and its address can be read
    ctx.route(re.compile(r"https://(wa\.me|api\.whatsapp\.com)/.*"), lambda r: r.fulfill(body="WhatsApp stand-in", content_type="text/plain"))
    pg = ctx.new_page(); watch(pg, label)
    pg.goto(f"{B}/login"); pg.fill("input[inputmode=tel]", phone); pg.click("button:has-text('Send code')")
    pg.wait_for_selector(".prompt"); code = re.search(r"(\d{6})", pg.inner_text(".prompt")).group(1)
    pg.fill("input[autocomplete=one-time-code]", code); pg.click("button:has-text('Log in')")
    pg.wait_for_url(re.compile(r"/(home|dashboard|admin)"))
    return pg


def field(pg, label):
    return pg.locator(f"label.field:has(span.label:text-is('{label}'))").locator("input, select, textarea").first


def png_bytes():
    import struct, zlib
    w, h = 60, 40
    rows = b"".join(b"\x00" + bytes([200, 80, 40]) * w for _ in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def go(pg, path):
    pg.goto(f"{B}{path}"); expect(pg.locator(".sheet h1")).to_be_visible()


with sync_playwright() as p:
    b = p.chromium.launch()
    k = login(b, LAUNCH, "kaveri")

    # ── DATA · Expenses (DT-04): add today's bill with a photo, see where it went, download for the CA ──
    def expenses_add():
        go(k, "/expenses"); expect(k.locator(".sheet h1")).to_contain_text("money")
        k.click("button:has-text('Add an expense')")
        field(k, "Amount (₹)").fill("2360")
        paid_to = field(k, "Paid to"); paid_to.fill("MSEDCL electricity")
        cat = field(k, "Category")
        options = cat.locator("option").all_inner_texts()
        pick = next((o for o in options if "lectric" in o), options[1] if len(options) > 1 else options[0])
        cat.select_option(label=pick)
        k.locator("input[type=file][accept*='image']").set_input_files(files=[{"name": "bill.png", "mimeType": "image/png", "buffer": png_bytes()}])
        k.locator("form button:has-text('Save'), .panel button:has-text('Save')").first.click()
        expect(k.locator("text=MSEDCL electricity").first).to_be_visible()
    check("Expenses: add today's electricity bill with a photo", expenses_add)

    def expenses_where():
        k.click(".tabs button:has-text('Where it went')"); expect(k.locator(".sheet")).to_contain_text("budget")
    check("Expenses: Where it went shows the month by head, against budget", expenses_where)

    def expenses_csv():
        k.click(".tabs button:has-text('For your CA')")
        from datetime import date
        t = date.today(); field(k, "From").fill(t.replace(day=1).isoformat()); field(k, "To").fill(t.isoformat())  # it opens on last month
        with k.expect_download() as dl:
            k.locator("a:has-text('Download'), button:has-text('Download')").first.click()
        path = dl.value.path(); text = Path(path).read_text(encoding="utf-8-sig")
        assert "MSEDCL electricity" in text and "GST" in text.splitlines()[0], text[:300]
    check("Expenses: Download for your CA (CSV) has the bill and a GST column", expenses_csv)

    # ── DATA · Bank statement reader (DT-05): upload the demo statement, where it went, repeating, add all to Expenses ──
    def bank_read():
        go(k, "/bank-statement")
        k.locator("input[type=file]").set_input_files(str(ROOT / "docs/demo/kaveri-bank-statement.csv"))
        k.click("button:has-text('Read my statement')")
        expect(k.locator(".tabs button:has-text('Where it went')")).to_be_visible(timeout=60000)
        expect(k.locator(".sheet")).to_contain_text("Money in")
    check("Bank statement: the demo statement is read", bank_read)

    def bank_repeating():
        k.click(".tabs button:has-text('Repeating')"); expect(k.locator(".sheet")).to_contain_text(re.compile("rent|Rent|EMI"))
    check("Bank statement: Repeating shows rent and the EMI", bank_repeating)

    def bank_add_all():
        months = ("2026-07", "2026-08", "2026-09")        # the demo statement's three months
        count = lambda: sum(k.request.get(f"{B}/api/expenses?month={m}").json()["totals"]["count"] for m in months)
        before = count()
        k.click(".tabs button:has-text('The payments')")
        k.locator("button:has-text('Add all')").first.click()
        k.wait_for_timeout(1500)
        after = count()
        assert after > before, (before, after)
        k.locator("button:has-text('Add all')").first.click() if k.locator("button:has-text('Add all')").count() else None
        k.wait_for_timeout(1000)
        assert count() == after, "the same payments were added twice"
    check("Bank statement: Add all to Expenses adds the payments", bank_add_all)

    def bank_delete():
        k.locator("button:has-text('Delete it now')").first.click()
        expect(k.locator("button:has-text('Read my statement')")).to_be_visible()
    check("Bank statement: Delete it now removes the list", bank_delete)

    # ── DATA · GST set-aside (DT-06): last month's estimate and due date, how it's worked out, count it in cash flow ──
    def gst():
        go(k, "/gst"); expect(k.locator(".sheet h1")).to_contain_text("GST")
        expect(k.locator(".sheet")).to_contain_text("GSTR-3B")
        k.locator("summary:has-text('How this is worked out')").first.click()
        expect(k.locator("details[open]").first).to_be_visible()
        btn = k.locator("button:has-text('Count it in my cash flow')")
        if btn.count():
            btn.first.click(); expect(k.locator("button:has-text('Remove from cash flow')").first).to_be_visible()
        else:
            expect(k.locator("button:has-text('Remove from cash flow')").first).to_be_visible()
    check("GST set-aside: estimate, due date, how it's worked out, counted in cash flow", gst)

    # ── DATA · Customer risk (DT-07): the top line, gone quiet, check in on WhatsApp ──
    def risk():
        go(k, "/customer-risk"); expect(k.locator(".sheet")).to_contain_text(re.compile(r"\d+%"))
        tab = k.locator(".tabs button:has-text('Gone quiet')")
        if tab.count(): tab.first.click()
        link = k.locator("a:has-text('Check in on WhatsApp')").first
        expect(link).to_be_visible(); assert re.match(r"https://(wa\.me|api\.whatsapp\.com)/", link.get_attribute("href") or ""), link.get_attribute("href")
    check("Customer risk: top line, Gone quiet, Check in on WhatsApp opens WhatsApp", risk)

    # ── DATA · Getting paid (DT-08): the days you give and the days you wait, the late buckets, chase them ──
    def paid():
        go(k, "/getting-paid"); expect(k.locator(".sheet")).to_contain_text(re.compile(r"You give \d+ days"))
        expect(k.locator(".sheet")).to_contain_text("days late")
        k.click("a:has-text('Chase these in Money owed'), button:has-text('Chase these in Money owed')"); k.wait_for_url("**/money**")
    check("Getting paid: terms vs real wait, late buckets, Chase these opens Money owed", paid)

    # ── CONVERSION · Demo 10, find your biggest leak (CV-03): numbers with visitors at 0, the leak and the rupees, get fixes ──
    def leak():
        go(k, "/leaks")
        for label, v in (("Website visitors", "0"), ("Inquiries", "40"), ("Meetings or site visits", "10"),
                         ("Proposals or quotations", "9"), ("Customers won", "4"), ("Average sale (₹)", "36000")):
            field(k, label).fill(v)
        k.click("button:has-text('Find my biggest leak')")
        expect(k.locator(".sheet")).to_contain_text("No website visitors given")
        expect(k.locator(".sheet")).to_contain_text(re.compile(r"Biggest leak", re.I))
        expect(k.locator(".sheet")).to_contain_text("₹")
    check("Demo 10: visitors at 0, Find my biggest leak shows the leak and what fixing it is worth", leak)

    def leak_fixes():
        k.click("button:has-text('Get fixes')")
        fixes = k.locator(".panel:has(span.label:text-is('Fixes for your leak')) li")
        k.wait_for_function("() => document.querySelectorAll('li').length >= 3", timeout=30000)
        assert 3 <= fixes.count() <= 5, fixes.count()
    check("Demo 10: Get fixes writes three to five actions (1 AI run)", leak_fixes)

    def leak_saved():
        go(k, "/leaks"); expect(k.locator(".sheet")).to_contain_text("Saved")
        assert field(k, "Inquiries").input_value() == "40"
    check("Demo 10: the numbers are saved and come back", leak_saved)

    # ── CONVERSION · Demo 11, your follow-ups (CV-04): follow-up 1 of 3 is ready, Send on WhatsApp opens WhatsApp ──
    def followup_ready():
        go(k, "/inquiries")
        sw = k.locator("label:has-text('Follow up on every inquiry') input")
        assert sw.is_checked(), "follow-ups are off on the demo account"
        expect(k.locator("text=/Follow-up 1 of 3 is ready/").first).to_be_visible()
    check("Demo 11: Inquiries shows Follow-up 1 of 3 is ready, with follow-ups switched on", followup_ready)

    def followup_send():
        card = k.locator(".panel:has-text('is ready')").first
        with k.expect_popup() as pop:
            card.locator("a:has-text('Send on WhatsApp'), button:has-text('Send on WhatsApp')").last.click()
        pop.value.wait_for_url(re.compile(r"https://(wa\.me|api\.whatsapp\.com)/"), timeout=15000)
        assert "text=" in pop.value.url, pop.value.url
        pop.value.close(); k.wait_for_timeout(800)
        sent = k.request.get(f"{B}/api/approvals?status=done").json()
        items = sent if isinstance(sent, list) else sent.get("items", sent.get("approvals", []))
        assert any("ollow" in (a.get("title") or "") for a in items), [a.get("title") for a in items][:5]
    check("Demo 11: Send on WhatsApp opens WhatsApp with the text, and the follow-up counts as sent", followup_send)

    def followup_switch():
        sw = k.locator("label:has-text('Follow up on every inquiry') input")
        sw.click(force=True); k.wait_for_timeout(600); go(k, "/inquiries")
        assert not k.locator("label:has-text('Follow up on every inquiry') input").is_checked()
        k.locator("label:has-text('Follow up on every inquiry') input").click(force=True); k.wait_for_timeout(600)
        go(k, "/inquiries"); assert k.locator("label:has-text('Follow up on every inquiry') input").is_checked()
    check("Demo 11: the owner can switch follow-ups off and back on, and it's kept", followup_switch)

    # ── AI AGENT · Competitor Watch (AG-01): add a competitor with a website, Check now, see their running ads ──
    def competitor():
        go(k, "/competitors")
        k.locator("button:has-text('Add'), button:has-text('Add your first competitor')").first.click()
        field(k, "Their business name").fill("Patel Pumps (test)")
        field(k, "Their website").fill(f"{B}/s/shree-ganesh-interiors")
        k.click("button:has-text('Add competitor')")
        expect(k.locator("text=Patel Pumps (test)")).to_be_visible()
        k.click("button:has-text('Check now')")
        expect(k.locator(".sheet")).not_to_contain_text("No report yet", timeout=90000)
    check("Competitor Watch: add a competitor's website and Check now saves a first look", competitor)

    def competitor_ads():
        row = k.locator(":is(li, .panel, div):has-text('Patel Pumps (test)')").last
        link = k.locator("a:has-text('Ads'), a:has-text('See their running ads')").first
        href = link.get_attribute("href") or ""
        assert "facebook.com/ads/library" in href, href
    check("Competitor Watch: See their running ads links to Meta's public Ad Library", competitor_ads)

    # ── AI AGENT · Reviews & reputation (AG-02): paste a 2-star review, approve the draft, post it; ask a 5-star for a referral ──
    def review_two_star():
        go(k, "/reputation")
        k.click("button:has-text('Paste a review')")
        field(k, "Their name, as shown").fill("Sunil Jadhav")
        k.locator("[aria-label=Stars] button:has-text('2★')").click()
        field(k, "What they wrote").fill("Technician came two days late and the chimney still makes noise. Not happy.")
        k.click("button:has-text('Save review')")
        expect(k.locator("text=Sunil Jadhav").first).to_be_visible()
        card = k.locator(".panel:has-text('Sunil Jadhav')").first
        expect(card.locator("textarea")).not_to_have_value("", timeout=30000)
        draft = card.locator("textarea").input_value()
        assert "₹" not in draft and len(draft) > 40, draft
        card.locator("button:has-text('Approve')").click()
        k.click(".tabs button:has-text('To post')")
        card = k.locator(".panel:has-text('Sunil Jadhav')").first
        card.locator("button:has-text(\"I've posted it\")").click()
        k.click(".tabs button:has-text('Posted')"); expect(k.locator("text=Sunil Jadhav").first).to_be_visible()
    check("Reviews: paste a 2-star review, a calm draft with no prices, approve, I've posted it", review_two_star)

    def review_referral():
        k.click(".tabs button:has-text('To reply')")
        k.click("button:has-text('Paste a review')")
        field(k, "Their name, as shown").fill("Rohan Kulkarni")
        k.locator("[aria-label=Stars] button:has-text('5★')").click()
        field(k, "What they wrote").fill("Same-day chimney installation, very neat work. Highly recommend Kaveri!")
        k.click("button:has-text('Save review')")
        card = k.locator(".panel:has-text('Rohan Kulkarni')").first
        expect(card.locator("button:has-text('Ask for a referral')")).to_be_visible(timeout=30000)
    check("Reviews: a 5-star review offers Ask for a referral", review_referral)

    # ── AI AGENT · Win-Back (AG-03, Action Program): read the sample, switch it on, run now, send one ──
    g = login(b, GROWTH, "sharma")

    def winback():
        go(k, "/win-back")
        expect(k.locator(".sheet")).to_contain_text("Hello")                       # the sample message
        k.locator("label:has-text(\"I've read this message\") input").check(force=True)
        k.click("button:has-text('Switch Win-Back on')")
        expect(k.locator("button:has-text('Switch off')")).to_be_visible()
        k.click("button:has-text('Run now')")
        expect(k.locator("a:has-text('Send on WhatsApp'), button:has-text('Send on WhatsApp')").first).to_be_visible(timeout=30000)
        with k.expect_popup() as pop:
            k.locator("a:has-text('Send on WhatsApp'), button:has-text('Send on WhatsApp')").first.click()
        pop.value.wait_for_url(re.compile(r"https://(wa\.me|api\.whatsapp\.com)/"), timeout=15000); pop.value.close()
    check("Win-Back: read the sample, switch it on, Run now, Send on WhatsApp opens WhatsApp", winback)

    # ── AI AGENT · Content & company research (AG-04, Action Program): 5 post ideas, a research plan, a search link ──
    def research():
        go(g, "/research")
        g.click("button:has-text('Write 5 post ideas'), button:has-text('Write 5 fresh ideas')")
        g.wait_for_function("() => document.querySelectorAll('.sheet h3').length >= 5", timeout=30000)
        field(g, "What kind of company buys from you?").fill("restaurants and cloud kitchens")
        field(g, "City or area").fill("Pune")
        g.click("button:has-text('Make my research plan')")
        link = g.locator("a[href^='https://www.google.com/search'], a[href*='google.com/maps'], a[href*='linkedin.com']").first
        expect(link).to_be_visible(timeout=30000)
        expect(g.locator("button:has-text('Download the CSV template')")).to_be_visible()
    check("Research: Write 5 post ideas, Make my research plan with search links and a CSV template", research)

    # ── AI AGENT · Lead Response (AG-05, Growth): test send, it's on, a website inquiry gets the first reply on WhatsApp ──
    def lead_response():
        go(g, "/lead-response")
        expect(g.locator(".sheet")).to_contain_text("Your WhatsApp number is connected")
        expect(g.locator(".sheet")).to_contain_text("We look forward to speaking with you")     # the wording Meta accepts
        g.click("button:has-text('Send it to my WhatsApp')"); expect(g.locator(".toast")).to_be_visible()
        if g.locator("button:has-text('Switch Lead Response on')").count():
            g.locator("label:has-text(\"I've read this first reply\") input").check(force=True)
            g.click("button:has-text('Switch Lead Response on')")
        expect(g.locator("button:has-text('Switch off')")).to_be_visible()
        slug = g.request.get(f"{B}/api/site").json()["slug"]
        v = b.new_context(viewport={"width": 390, "height": 844}).new_page(); watch(v, "visitor")
        v.goto(f"{B}/s/{slug}"); v.fill("input[name=name]", "Meera Desai"); v.fill("input[name=phone]", "98301 45678")
        v.fill("textarea[name=message]", "Need a modular kitchen for a 2BHK in Kothrud"); v.click("button:has-text('Send')")
        expect(v.locator(".ok")).to_be_visible()
        g.wait_for_timeout(2500); go(g, "/chats")
        expect(g.locator(".sheet")).to_contain_text("Meera", timeout=15000)
        thread = g.locator(":is(button, a, .listbox button):has-text('Meera')").first; thread.click()
        expect(g.locator(".sheet")).to_contain_text("thank you for contacting", timeout=10000)
    check("Lead Response: test send, switched on, a website inquiry gets the first reply with times in WhatsApp chats", lead_response)

    # ── Who can do what ──
    def staff_expense():
        s = login(b, STAFF, "staff")
        go(s, "/expenses"); s.click("button:has-text('Add an expense')")
        field(s, "Amount (₹)").fill("640"); field(s, "Paid to").fill("Site tea and snacks")
        cat = field(s, "Category"); cat.select_option(index=1)
        s.locator("form button:has-text('Save'), .panel button:has-text('Save')").first.click()
        expect(s.locator(".sheet")).to_contain_text("Site tea and snacks")
        mine = s.request.get(f"{B}/api/expenses").json()["items"]
        row = next(i for i in mine if i.get("vendor") == "Site tea and snacks" or "Site tea" in str(i))
        assert row["status"] != "approved", row["status"]
        s.goto(f"{B}/bank-statement"); s.wait_for_timeout(1200)
        assert s.locator("button:has-text('Read my statement')").count() == 0, "staff can open the bank statement reader"
        s.context.close()
        m = login(b, MANAGER, "manager")
        go(m, "/expenses"); m.click("button:has-text('To approve')")
        card = m.locator(":is(.panel, li, div):has-text('Site tea and snacks')").last
        row = m.locator("xpath=//*[contains(., 'Site tea and snacks')][.//button[normalize-space()='Approve']]").last
        row.locator("button:has-text('Approve')").first.click(); m.wait_for_timeout(800)
        items = m.request.get(f"{B}/api/expenses").json()["items"]
        assert next(i for i in items if "Site tea" in str(i))["status"] == "approved"
        m.context.close()
    check("Staff add their own bill, it waits for a manager, the manager approves; staff can't open the bank statement", staff_expense)

    def free_locked():
        f = login(b, FREE, "free")
        for path in ("/expenses", "/leaks", "/competitors", "/lead-response"):
            f.goto(f"{B}{path}"); f.wait_for_timeout(900)
            expect(f.locator(".sheet")).to_contain_text(re.compile(r"Unlock|unlocks with", re.I))
        f.context.close()
    check("A Free owner sees each v2.2 page locked, with the plan that unlocks it", free_locked)

    def d1():
        mem = login(b, MEMBER, "member")
        mem.goto(f"{B}/leaks"); mem.wait_for_timeout(900)
        expect(mem.locator(".sheet")).to_contain_text(re.compile(r"Unlock", re.I))          # before D1: still the Action Program
        a = login(b, ADMIN, "admin")
        go(a, "/admin/event"); a.click("button:has-text('Move them into Membership')"); expect(a.locator(".toast")).to_contain_text("Membership")
        mem.goto(f"{B}/leaks"); expect(mem.locator("button:has-text('Find my biggest leak')")).to_be_visible()
        mem.goto(f"{B}/inquiries"); expect(mem.locator("label:has-text('Follow up on every inquiry')")).to_be_visible()
        assert not mem.locator("label:has-text('Follow up on every inquiry') input").is_checked(), "follow-ups should start off on the Membership"
        a.context.close(); mem.context.close()
    check("D1: Move them into Membership opens the leak finder and follow-ups (off until the owner switches them on)", d1)

    def event_account():
        """The hand-merged part of event.py: an event account starts with follow-ups on (v2.2), and an email another account
        already uses doesn't stop the save (the duplicate-email fix)."""
        a = login(b, ADMIN, "admin-event")
        from datetime import date, timedelta
        until = (date.today() + timedelta(days=3)).isoformat()   # "Membership features open until": the event's last day
        r = a.request.put(f"{B}/api/admin/event/BAI-OCT-11", data={"open": True, "ai": True, "pack_cap": 400, "ai_cap": 300, "trial_until": until})
        assert r.ok, r.text()
        e = b.new_context(viewport={"width": 390, "height": 844}).new_page(); watch(e, "attendee")
        run = e.request.post(f"{B}/api/event/runs", data={"event": "BAI-OCT-11", "website": None, "business_name": "Joshi Caterers QA",
                                                          "about": "Wedding and corporate catering in Pune", "city": "Pune", "gstin": None,
                                                          "state": "Maharashtra", "gst_scheme": "monthly", "staff": 6, "entity": "proprietor", "consent": True})
        assert run.ok, run.text()
        token = run.json()["token"]
        for _ in range(60):
            if e.request.get(f"{B}/api/event/runs/{token}").json()["status"] in ("done", "failed"):
                break
            e.wait_for_timeout(1000)
        code = e.request.post(f"{B}/api/event/runs/{token}/code", data={"phone": "9830166666"}).json()["dev_code"]
        taken = "hello@kaveri-appliances.example"   # Kaveri's email: already on another account
        saved = e.request.post(f"{B}/api/event/runs/{token}/verify", data={"phone": "9830166666", "code": code, "email": taken, "name": "Priya"})
        assert saved.ok, saved.text()
        e.goto(f"{B}/inquiries"); expect(e.locator("label:has-text('Follow up on every inquiry')")).to_be_visible()
        assert e.locator("label:has-text('Follow up on every inquiry') input").is_checked(), "event accounts should start with follow-ups on"
        e.context.close(); a.context.close()
    check("Event account: follow-ups start on, and an email already in use doesn't stop the save", event_account)

    b.close()

print("\n".join(f"{s} {n}" for s, n in results))
print(f"{sum(s == 'PASS' for s, _ in results)}/{len(results)} passed")
if problems:
    print("PROBLEMS:"); print("\n".join(" - " + x for x in problems))
