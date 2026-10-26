"""Browser check of the Employz.ai import against qa/fake_employz.py on :8765 (invented data only).
   Run the stand-in (APP_ENV=development APP_URL=http://localhost:8000 python qa/fake_employz.py), start a seeded hub with
   EMPLOYZ_API_BASE=http://127.0.0.1:8765, then: python qa/employz_ui.py /tmp/ez_shots 9820000007"""
import re, sys, time
from pymongo import MongoClient
from playwright.sync_api import sync_playwright

B = "http://localhost:8000"; OUT = sys.argv[1]; PHONE = sys.argv[2] if len(sys.argv) > 2 else "9820000007"
problems, passed = [], []
def check(ok, what):
    (passed if ok else problems).append(what); print(("  ok  " if ok else "  FAIL ") + what, flush=True)
def watch(pg, label):
    pg.on("pageerror", lambda e: problems.append(f"[{label}] page error: {e}"))
    pg.on("console", lambda m: m.type == "error" and not re.search(r"status of (400|401|402|403|404|409|429)|fonts\.g", m.text) and problems.append(f"[{label}] console: {m.text[:200]}"))
    pg.on("response", lambda r: r.status >= 500 and problems.append(f"[{label}] {r.status} {r.url}"))
def login(pg, phone):
    pg.goto(f"{B}/login"); pg.fill("input[inputmode=tel]", phone); pg.click("button:has-text('Send code')")
    pg.wait_for_selector(".prompt"); code = re.search(r"(\d{6})", pg.inner_text(".prompt")).group(1)
    pg.fill("input[autocomplete=one-time-code]", code); pg.click("button:has-text('Log in')"); pg.wait_for_timeout(1500)
def shot(pg, name):
    pg.wait_for_timeout(400)
    wide = pg.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
    if wide > 1: problems.append(f"{name}: scrolls sideways by {wide}px")
    pg.screenshot(path=f"{OUT}/{name}.png", full_page=False)

with sync_playwright() as p:
    b = p.chromium.launch()
    a = b.new_page(); login(a, "9999900000")
    uid = MongoClient().qa_mc.users.find_one({"phone": f"+91{PHONE}"})["_id"]
    print("plan", a.request.patch(f"{B}/api/admin/users/{uid}", data={"plan": "running"}).status)
    for label, vp in (("desk", {"width": 1366, "height": 860}), ("phone", {"width": 390, "height": 844})):
        ctx = b.new_context(viewport=vp, is_mobile=label == "phone", has_touch=label == "phone")
        pg = ctx.new_page(); watch(pg, label); login(pg, PHONE)
        if label == "desk":
            pg.goto(f"{B}/connections"); pg.wait_for_timeout(1200)
            panel = pg.locator(".panel", has=pg.locator("h3", has_text=re.compile("^CRM$"))).first
            check(panel.count() == 1, "CRM panel on Connections")
            check(panel.locator("text=Stage id").count() == 0 and panel.locator("text=Pipeline id").count() == 0, "no manual pipeline / stage id fields")
            panel.locator("label:has-text('ub-account') input, label:has-text('ocation') input").first.fill("LocTest00000000000001")
            panel.locator("label:has-text('Link to open') input").fill("https://app.employz.ai/v2/location/LocTest00000000000001")
            panel.locator("input[type=password]").first.fill("pit-test-0000-secret-token")
            panel.locator("button:has-text('Save')").click(); pg.wait_for_timeout(2500)
            shot(pg, f"{label}_connections_saved")
            sel = pg.locator("select[aria-label='Pipeline for new leads']")
            check(sel.count() == 1, "pipeline picker shows after saving")
            sel.select_option("pipeA"); pg.wait_for_timeout(300)
            check(pg.locator("text=Identification → Logic / reason").count() > 0, "picked pipeline's stages listed")
            pg.locator("button:has-text('Use this pipeline')").click(); pg.wait_for_timeout(1500)
            conn = MongoClient().qa_mc.connections.find_one({"_id": uid}) or {}
            ez = conn.get("employz") or {}
            check(ez.get("pipeline_id") == "pipeA" and ez.get("stage_identification") == "sA1" and ez.get("stage_close") == "sA5", f"5 stage ids saved by name ({ {k: v for k, v in ez.items() if k.startswith(('stage_','pipeline'))} })")
            pg.locator("button:has-text('Import now')").click()
            for _ in range(40):
                pg.wait_for_timeout(1000)
                if pg.locator("text=Up to date").count(): break
            shot(pg, f"{label}_connections_imported")
            check(pg.locator("text=Up to date").count() > 0, "import finishes: Up to date")
            st = pg.evaluate("() => fetch('/api/employz/import').then(r => r.json())")
            check(st["in_hub"] == {"pipelines": 2, "contacts": 150, "opportunities": 120}, f"in hub {st['in_hub']}")
        pg.goto(f"{B}/pipeline"); pg.wait_for_timeout(1800)
        pg.locator("[role=tab]:has-text('Opportunities')").click(); pg.wait_for_timeout(600)
        sw = pg.locator("select[aria-label='Pipeline']")
        check(sw.count() == 1, f"[{label}] pipeline switcher on the board")
        hub_add = pg.locator("button:has-text('+ Add opportunity')").count()
        sw.select_option("employz:pipeA"); pg.wait_for_timeout(1800)
        check("pipeline=employz" in pg.url, f"[{label}] url keeps the pipeline")
        check(pg.locator("text=From Employz.ai · view only.").count() == 1, f"[{label}] view-only line with last sync")
        check(pg.locator("button:has-text('+ Add opportunity')").count() == 0 and pg.locator(".pp-add").count() == 0, f"[{label}] no add buttons (hub had {hub_add})")
        cols = pg.locator(".pp-col-head b").all_inner_texts()
        check(cols[:2] == ["Identification", "Logic / reason"], f"[{label}] Employz.ai stages as columns {cols}")
        shot(pg, f"{label}_board_employz")
        card = pg.locator(".pp-card").first
        name = card.locator(".pp-card-name b").inner_text()
        if label == "desk":   # a drag does nothing
            box = card.bounding_box(); target = pg.locator(".pp-col").nth(2).bounding_box()
            pg.mouse.move(box["x"] + 20, box["y"] + 20); pg.mouse.down(); pg.mouse.move(box["x"] + 60, box["y"] + 40, steps=4)
            pg.mouse.move(target["x"] + 40, target["y"] + 120, steps=8); pg.mouse.up(); pg.wait_for_timeout(600)
            check(pg.locator(".pp-modal").count() == 0 or True, "drag attempted")
            if pg.locator(".pp-modal").count(): pg.keyboard.press("Escape")
            check(pg.locator(".pp-undo").count() == 0, "a drag doesn't move an Employz.ai card")
        (card.locator(".pp-dots").tap() if label == "phone" else card.locator(".pp-dots").click()); pg.wait_for_timeout(300)
        items = pg.locator(".pp-menu [role=menuitem]").all_inner_texts()
        check(items == ["View", "Open in Employz.ai"], f"[{label}] ⋯ menu is view-only {items}")
        pg.keyboard.press("Escape")
        pg.locator(".pp-card").first.click(); pg.wait_for_timeout(600)
        check(pg.locator(".pp-modal .label:has-text('From Employz.ai')").count() == 1, f"[{label}] read-only dialog opens")
        check(pg.locator(".pp-modal button:has-text('Update')").count() == 0 and pg.locator(".pp-modal input").count() == 0, f"[{label}] dialog has nothing to edit")
        shot(pg, f"{label}_deal_view")
        pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
        sw.select_option("hub"); pg.wait_for_timeout(1500)
        check(pg.locator("button:has-text('+ Add opportunity')").count() == hub_add and "pipeline=" not in pg.url, f"[{label}] back on the hub pipeline")
        pg.goto(f"{B}/contacts"); pg.wait_for_timeout(1500)
        check(pg.locator(".crm-ext").count() > 0, f"[{label}] contacts show From Employz.ai")
        shot(pg, f"{label}_contacts")
        pg.locator(".crm-row", has=pg.locator(".crm-ext")).first.click(); pg.wait_for_timeout(1200)
        check(pg.locator(".crm-ext-fields dt:has-text('City')").count() == 1, f"[{label}] contact shows custom fields")
        shot(pg, f"{label}_contact")
        ctx.close()
    b.close()
print(f"\n{len(passed)} passed, {len(problems)} problems")
for x in problems: print(" -", x)
