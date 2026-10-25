"""Every screen a live-event participant or the admin sees, on the phones and laptop they'll use, with automatic layout checks.

  participant, on a 390 px phone (and 360 px for the checks): the event link → building → the pack → save with the code → the
  hub (every page in the menu, Membership with the event extras) → their own website;
  participant on a laptop (1440 px): the same hub pages; admin on a laptop: every admin page and the event QR.

Each screen is screenshotted, and checked for: anything wider than the screen (sideways scroll), text cut off inside its box,
content off the right edge, and (on phones) controls smaller than a finger. Run against a freshly seeded local hub (as for
e2e.py):  python3 qa/screen_audit.py /tmp/audit
"""
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

B = "http://localhost:8000"
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/audit")
OUT.mkdir(parents=True, exist_ok=True)
ADMIN = "9999900000"
CODE = "BAI-OCT-11"
issues: dict[str, list] = {}
shots: list[str] = []
errors: list[str] = []

CHECK = r"""(phone) => {
  const out = [], W = innerWidth;
  const sw = document.documentElement.scrollWidth;
  if (sw > W + 1) out.push(["sideways scroll", `page is ${sw - W}px wider than the screen`]);
  const scrollable = (el) => { for (let e = el.parentElement; e; e = e.parentElement) { const o = getComputedStyle(e).overflowX;
    if (o === "auto" || o === "scroll" || o === "hidden" || o === "clip") return true; } return false; };
  const visible = (el) => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 1 && r.height > 1 && cs.visibility !== "hidden" && +cs.opacity > 0; };   // 1px: screen-reader-only text
  const name = (el) => (el.tagName.toLowerCase() + (el.className && typeof el.className === "string" ? "." + el.className.trim().split(/\s+/).slice(0, 2).join(".") : ""))
    + " “" + (el.innerText || el.value || el.getAttribute("aria-label") || "").trim().slice(0, 40) + "”";
  for (const el of document.querySelectorAll("body *")) {
    if (!visible(el) || el.closest("svg, canvas, iframe, [aria-hidden=true], .finder, .toast")) continue;
    const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
    // off the right edge, and not inside something that scrolls or clips on purpose (tabs, tables, carousels)
    if (r.right > W + 1 && r.left < W && !scrollable(el) && cs.position !== "fixed") out.push(["off the edge", name(el) + ` right=${Math.round(r.right)}`]);
    // text cut off in its own box without an ellipsis
    const own = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
    if (own && (cs.overflowX === "hidden" || cs.overflow === "hidden") && el.scrollWidth > el.clientWidth + 2 && cs.textOverflow !== "ellipsis"
        && !cs.webkitLineClamp?.match(/\d/)) out.push(["text cut off", name(el)]);
    if (phone && el.matches("button, a.btn, [role=button], [role=tab], input:not([type=hidden]):not([type=checkbox]):not([type=radio]), select, textarea")
        && !el.closest("p, li p, .small, label.inline") && !el.disabled && (r.height < 32 || r.width < 32))
      out.push(["small to tap", name(el) + ` ${Math.round(r.width)}×${Math.round(r.height)}`]);
  }
  return out;
}"""


def note(label, page, phone):
    try:
        page.wait_for_timeout(350)
        found = page.evaluate(CHECK, phone)
    except Exception as e:  # noqa: BLE001
        found = [["check failed", str(e)[:120]]]
    seen = set()
    found = [x for x in found if not (tuple(x) in seen or seen.add(tuple(x)))]
    if found:
        issues[label] = found


def shot(page, label, phone=False, full=True):
    path = OUT / (re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") + ".png")
    page.screenshot(path=str(path), full_page=full)
    shots.append(path.name)
    note(label, page, phone)


def watch(page, label):
    page.set_default_timeout(20000)
    page.on("pageerror", lambda e: errors.append(f"[{label}] page error: {e}"))
    page.on("response", lambda r: r.status >= 500 and errors.append(f"[{label}] {r.status} {r.url}"))
    page.on("dialog", lambda d: d.accept())


def login(b, phone, w, h, label, mobile=False):
    ctx = b.new_context(viewport={"width": w, "height": h}, is_mobile=mobile, has_touch=mobile, device_scale_factor=2 if mobile else 1)
    ctx.route(re.compile(r"https://(wa\.me|api\.whatsapp\.com)/.*"), lambda r: r.fulfill(body="WhatsApp stand-in", content_type="text/plain"))
    pg = ctx.new_page(); watch(pg, label)
    pg.goto(f"{B}/login"); pg.fill("input[inputmode=tel]", phone); pg.click("button:has-text('Send code')")
    pg.wait_for_selector(".prompt"); code = re.search(r"(\d{6})", pg.inner_text(".prompt")).group(1)
    pg.fill("input[autocomplete=one-time-code]", code); pg.click("button:has-text('Log in')")
    pg.wait_for_url(re.compile(r"/(home|dashboard|admin|start)"))
    return pg


def pages_of(pg):
    me = pg.evaluate("fetch('/api/me').then(r => r.json())")
    return [(g["group"], it["label"], it["path"]) for g in me["menu"] for it in g["items"] if not it.get("locked")]


def gstin():
    cs = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    first = "27AAKFS4410C1Z"
    t = sum((cs.index(ch) * (2 if i % 2 else 1)) // 36 + (cs.index(ch) * (2 if i % 2 else 1)) % 36 for i, ch in enumerate(first))
    return first + cs[(36 - t % 36) % 36]


with sync_playwright() as p:
    b = p.chromium.launch()

    # ── admin, on a laptop ──
    a = login(b, ADMIN, 1440, 900, "admin")
    a.request.post(f"{B}/api/admin/config/presets/membership-v22")
    a.request.post(f"{B}/api/admin/config/presets/event-extras")
    a.request.put(f"{B}/api/admin/event/{CODE}", data={"open": True, "ai": True, "pack_cap": 2000, "ai_cap": 2000,
                                                       "trial_until": (date.today() + timedelta(days=3)).isoformat()})
    for path in ["/admin", "/admin/features", "/admin/event", "/admin/day1", "/admin/calls", "/admin/recordings", "/admin/compliance", "/admin/email"]:
        a.goto(B + path); a.wait_for_load_state("networkidle")
        shot(a, f"admin {path}")
    a.goto(B + "/admin/event"); a.wait_for_load_state("networkidle")
    a.click("button:has-text('Show the QR')"); a.wait_for_timeout(600)
    shot(a, "admin event QR on the projector", full=False)

    # ── participant, on a phone: the event link to the hub ──
    ctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, device_scale_factor=2)
    ctx.route(re.compile(r"https://(wa\.me|api\.whatsapp\.com)/.*"), lambda r: r.fulfill(body="WhatsApp stand-in", content_type="text/plain"))
    e = ctx.new_page(); watch(e, "participant")
    e.goto(f"{B}/event?e={CODE}"); expect(e.locator("h1")).to_contain_text("minute")
    shot(e, "phone event 1 the form", True)
    e.locator("text=I don't have a website").click()
    e.fill("input[placeholder='Riya Tiffins']", "Annapurna Home Tiffins"); e.fill("input[placeholder='Pune']", "Pune")
    e.fill("textarea", "Home-cooked lunch tiffins delivered to offices in Baner, Aundh and Hinjewadi")
    e.fill("input[placeholder='27ABCDE1234F1Z5']", gstin()); e.wait_for_timeout(900)
    seg = e.locator("[role=group][aria-label='Type of business'] button")
    if seg.count():
        seg.first.click()
    e.fill("input[aria-label='People who work with you']", "6")
    shot(e, "phone event 2 the form filled", True)
    e.locator(".d1-consent").last.click(); e.click("button:has-text('Build my pack')")
    e.wait_for_url("**/event/r/**"); e.wait_for_timeout(1200)
    shot(e, "phone event 3 building", True)
    expect(e.locator("h1")).to_contain_text("built", timeout=90000)
    shot(e, "phone event 4 the pack is ready", True)
    e.locator("button:has-text('Save it to my hub')").first.click(); e.wait_for_timeout(600)
    shot(e, "phone event 5 save it", True)
    e.fill("input[placeholder='Asha']", "Meena"); e.fill("input[placeholder='you@yourbusiness.in']", "meena@annapurna.example")
    e.fill("input[placeholder='98200 00000']", "98301 66666")
    e.click("button:has-text('Send my code')"); e.wait_for_selector(".prompt")
    code = re.search(r"(\d{6})", e.inner_text(".prompt")).group(1); e.fill("input[aria-label='6-digit code']", code)
    e.click("button:has-text('Save to my hub')"); expect(e.locator("h2:has-text('Saved')")).to_be_visible()
    shot(e, "phone event 6 saved", True)
    e.click("button:has-text('Open my hub')"); e.wait_for_url(re.compile(r"/(home|start|knowledge|dashboard)"))
    shot(e, "phone hub landing", True)

    hub_pages = pages_of(e)
    for group, label, path in hub_pages:
        e.goto(B + path); e.wait_for_load_state("networkidle")
        shot(e, f"phone {group} {label}", True)
    e.goto(B + "/website"); e.wait_for_load_state("networkidle")
    site = e.evaluate("fetch('/api/site').then(r => r.json())")
    if site.get("slug"):
        if site.get("status") != "live":
            e.request.post(f"{B}/api/site/publish")
        e.goto(f"{B}/s/{site['slug']}"); e.wait_for_load_state("networkidle")
        shot(e, "phone participant's own website", True)
    menu = e.locator(".topbar button:has-text('Menu')")
    e.goto(B + "/home")
    if menu.count():
        menu.click(); e.wait_for_timeout(400); shot(e, "phone the menu open", True, full=False)

    # the same screens on a small Android phone, checks only
    small = b.new_context(viewport={"width": 360, "height": 780}, is_mobile=True, has_touch=True, storage_state=ctx.storage_state())
    s = small.new_page(); watch(s, "participant-360")
    for group, label, path in hub_pages + [("Website", "own website", f"/s/{site.get('slug')}")]:
        s.goto(B + path); s.wait_for_load_state("networkidle")
        note(f"360px {group} {label}", s, True)

    # the participant on a laptop
    lap = b.new_context(viewport={"width": 1440, "height": 900}, storage_state=ctx.storage_state())
    l = lap.new_page(); watch(l, "participant-laptop")
    for group, label, path in hub_pages:
        l.goto(B + path); l.wait_for_load_state("networkidle")
        shot(l, f"laptop {group} {label}")
    b.close()

(OUT / "issues.json").write_text(json.dumps({"issues": issues, "errors": errors, "screens": shots}, indent=1, ensure_ascii=False))
print(f"{len(shots)} screens; {len(issues)} with layout issues; {len(errors)} errors")
for k, v in issues.items():
    print(k)
    for x in v[:6]:
        print("   ", x[0], "—", x[1])
for x in errors[:20]:
    print("ERROR", x)
