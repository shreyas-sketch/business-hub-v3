"""v2.3 (OwnerDesk) screenshot sweep: every engine page for a paying member, the event view on Day 1, and the admin pages,
on a phone and on a desktop. Collects page errors, console errors, server errors and any page that scrolls sideways.
Run against a seeded hub on localhost:8000 (v2.3 setup applied):  python3 qa/screens_v23.py /tmp/shots23
"""
import json
import re
import sys

from playwright.sync_api import sync_playwright

BASE = "http://localhost:8000"
OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/shots23"
MEMBER = ["dashboard", "knowledge", "audit", "website", "lead-magnet", "levers", "lead-ideas", "quotes", "inquiries", "contacts", "pipeline",
          "money", "compliance", "sops", "brand-kit", "brand-kit?tab=brochures", "social", "key-person", "expenses", "expenses?tab=analysis",
          "bank-statement", "plans", "connections", "recordings", "engine/lead", "engine/sales", "engine/conversion", "engine/operations",
          "engine/brand", "engine/data"]
EVENT = ["dashboard", "engine/lead", "engine/conversion", "contacts", "social", "brand-kit?tab=brochures", "plans", "audit", "website", "levers"]
ADMIN = ["admin/onboarding", "admin/event", "admin/features", "admin/audit"]
PEOPLE = [("member", "9820000007", MEMBER), ("event", "9820000101", EVENT), ("admin", "9999900000", ADMIN)]
SIZES = {"phone": {"width": 390, "height": 844}, "desktop": {"width": 1366, "height": 900}}
problems: list[str] = []


def watch(page, label):
    page.on("pageerror", lambda e: problems.append(f"[{label}] page error: {e}"))
    page.on("console", lambda m: m.type == "error" and not re.search(r"status of (400|401|402|403|404|409|429)", m.text)
            and problems.append(f"[{label}] console: {m.text[:200]}"))
    page.on("response", lambda r: r.status >= 500 and problems.append(f"[{label}] {r.status} {r.url}"))
    page.on("dialog", lambda d: d.dismiss())


def login(page, phone):
    page.goto(f"{BASE}/login")
    page.fill("input[inputmode=tel]", phone)
    page.click("button:has-text('Send code')")
    page.wait_for_selector(".prompt")
    code = re.search(r"(\d{6})", page.inner_text(".prompt")).group(1)
    page.fill("input[autocomplete=one-time-code]", code)
    page.click("button:has-text('Log in')")
    page.wait_for_timeout(1500)


def main():
    shots = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for who, phone, pages in PEOPLE:
            for size, vp in SIZES.items():
                ctx = browser.new_context(viewport=vp, device_scale_factor=1)
                page = ctx.new_page()
                label = f"{who}/{size}"
                watch(page, label)
                login(page, phone)
                for path in pages:
                    page.goto(f"{BASE}/{path}")
                    page.wait_for_timeout(1600)
                    wide = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
                    if wide > 1:
                        problems.append(f"[{label}] /{path} scrolls sideways by {wide}px")
                    name = f"{who}_{size}_{re.sub(r'[^a-z0-9]+', '-', path)}.png"
                    page.screenshot(path=f"{OUT}/{name}", full_page=True, clip=None)
                    shots.append(name)
                ctx.close()
        browser.close()
    json.dump({"shots": shots, "problems": problems}, open(f"{OUT}/report.json", "w"), indent=1)
    print(f"{len(shots)} screenshots, {len(problems)} problems")
    for x in problems:
        print(" ", x)


if __name__ == "__main__":
    main()
