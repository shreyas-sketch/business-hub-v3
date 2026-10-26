"""Browser check of Razorpay invoices against qa/fake_razorpay.py on :8766 (invented data only).
Start the stand-in, then a seeded hub with RAZORPAY_API_BASE=http://127.0.0.1:8766/v1, then:
    python qa/razorpay_ui.py /tmp/rzp_shots 9820000007"""
import hashlib, hmac, json, re, sys, time
from pymongo import MongoClient
from playwright.sync_api import sync_playwright

B = "http://localhost:8000"; OUT = sys.argv[1]; PHONE = sys.argv[2] if len(sys.argv) > 2 else "9820000007"
SECRET = "whsec_qa_razorpay_1234"
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
def j(pg, path, method="GET", body=None):
    return pg.evaluate("""([p, m, b]) => fetch('/api' + p, {method: m, credentials: 'include', headers: {'content-type': 'application/json'},
        body: b ? JSON.stringify(b) : undefined}).then(async r => ({status: r.status, body: await r.json().catch(() => null)}))""", [path, method, body])
def wait_rzp(pg, inv_id, want):
    for _ in range(30):
        inv = next(x for x in j(pg, "/quotes?kind=invoice")["body"] if x["id"] == inv_id)
        if (inv.get("rzp") or {}).get("status") == want: return inv
        pg.wait_for_timeout(500)
    return inv

with sync_playwright() as p:
    b = p.chromium.launch()
    a = b.new_page(); login(a, "9999900000")
    db = MongoClient().qa_mc
    uid = db.users.find_one({"phone": f"+91{PHONE}"})["_id"]
    print("plan", a.request.patch(f"{B}/api/admin/users/{uid}", data={"plan": "running"}).status)
    ctx = b.new_context(viewport={"width": 1366, "height": 900}); pg = ctx.new_page(); watch(pg, "desk"); login(pg, PHONE)
    r = j(pg, "/connections/razorpay", "PUT", {"values": {"key_id": "rzp_test_abc123", "key_secret": "ks_test", "webhook_secret": SECRET}})
    check(r["status"] == 200 and r["body"]["connected"], "Razorpay keys saved")
    token = r["body"]["webhook"].rsplit("/", 1)[1]
    # 1. an unpaid invoice → issued Razorpay invoice with a pay link
    inv = j(pg, "/invoices", "POST", {"customer": {"name": "Rohit Shah", "phone": "98511 30001", "email": "rohit@shah.example"},
                                       "items": [{"name": "Steel brackets", "qty": 3, "unit": "nos", "rate": 1000, "gst": 18}], "due_in_days": 7})["body"]
    inv = wait_rzp(pg, inv["id"], "issued")
    check(inv["rzp"]["status"] == "issued" and inv["rzp"]["pay_url"], f"issued Razorpay invoice {inv['rzp'].get('invoice_id')}")
    check(f"Pay online: {inv['rzp']['pay_url']}" in inv["share_text"], "WhatsApp text carries the pay link")
    pg.goto(f"{B}/quotes"); pg.wait_for_timeout(1500)
    pg.locator("[role=tab]:has-text('Invoices'), button:has-text('Invoices')").first.click(); pg.wait_for_timeout(800)
    check(pg.locator(".inv-rzp:has-text('Awaiting payment')").count() >= 1, "invoice card shows Razorpay · Awaiting payment")
    check(pg.locator(".inv-rzp button:has-text('Copy pay link')").count() >= 1, "copy pay link button")
    shot(pg, "desk_invoice_issued")
    # 2. the customer pays through it (signed webhook, as Razorpay sends it)
    paise = round(inv["totals"]["total"] * 100)
    payload = {"entity": "event", "event": "payment.captured", "id": "evt_qa1", "payload": {"payment": {"entity": {
        "id": "pay_qa_inv1", "amount": paise, "currency": "INR", "email": "rohit@shah.example", "contact": "+919851130001",
        "invoice_id": inv["rzp"]["invoice_id"], "notes": {}, "created_at": int(time.time())}}}}
    body = json.dumps(payload).encode()
    res = a.request.post(f"{B}/api/hooks/payments/{token}", data=body, headers={"content-type": "application/json",
                         "x-razorpay-signature": hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()})
    check(res.status == 200 and res.json().get("result") == "due_paid", f"payment through it settles the invoice ({res.json().get('result')})")
    inv = next(x for x in j(pg, "/quotes?kind=invoice")["body"] if x["id"] == inv["id"])
    check(inv["status"] == "paid" and inv["rzp"]["status"] == "paid" and not inv["rzp"]["pay_url"], "invoice paid, pay link gone")
    # 3. money another way (a payment link): GST invoice + record-only draft
    payload2 = {"entity": "event", "event": "payment.captured", "id": "evt_qa2", "payload": {"payment": {"entity": {
        "id": "pay_qa_link1", "amount": 59000, "currency": "INR", "email": "neha@example.in", "contact": "+919833000011", "notes": {},
        "created_at": int(time.time())}}}}
    body2 = json.dumps(payload2).encode()
    res2 = a.request.post(f"{B}/api/hooks/payments/{token}", data=body2, headers={"content-type": "application/json",
                          "x-razorpay-signature": hmac.new(SECRET.encode(), body2, hashlib.sha256).hexdigest()})
    check(res2.json().get("result") == "invoiced", "payment by link becomes a hub GST invoice")
    rec = wait_rzp(pg, res2.json()["invoice_id"], "draft")
    check(rec["rzp"]["mode"] == "record" and rec["rzp"]["status"] == "draft" and not rec["rzp"]["pay_url"], "…and a record-only draft in Razorpay")
    # 4. paid in cash in Money owed → its issued Razorpay invoice is cancelled
    inv3 = j(pg, "/invoices", "POST", {"customer": {"name": "Cash Customer", "phone": "98200 44444"},
                                        "items": [{"name": "Repair", "qty": 1, "unit": "job", "rate": 2000, "gst": 18}], "due_in_days": 3})["body"]
    inv3 = wait_rzp(pg, inv3["id"], "issued")
    check(j(pg, f"/money/{inv3['due_id']}", "PATCH", {"status": "paid"})["status"] == 200, "marked paid in Money owed (cash)")
    inv3 = wait_rzp(pg, inv3["id"], "cancelled")
    check(inv3["rzp"]["status"] == "cancelled", "its Razorpay invoice is cancelled")
    pg.reload(); pg.wait_for_timeout(1500)
    pg.locator("[role=tab]:has-text('Invoices'), button:has-text('Invoices')").first.click(); pg.wait_for_timeout(800)
    check(pg.locator(".inv-rzp:has-text('Record')").count() >= 1 and pg.locator(".inv-rzp:has-text('Cancelled')").count() >= 1, "cards show Record and Cancelled")
    shot(pg, "desk_invoices_all")
    # 5. the Razorpay invoices tab
    pg.locator("[role=tab]:has-text('Razorpay invoices'), button:has-text('Razorpay invoices')").first.click(); pg.wait_for_timeout(600)
    pg.locator("button:has-text('Read now')").click(); pg.wait_for_timeout(1500)
    rows = pg.locator("table.t tbody tr").count()
    check(rows == 3, f"Razorpay invoices tab lists 3 ({rows})")
    check(pg.locator("table.t:has-text('Record')").count() == 1 and pg.locator("table.t:has-text('Cancelled')").count() == 1, "statuses shown (the stand-in never marks its own invoice paid)")
    shot(pg, "desk_razorpay_tab")
    ctx.close()
    ph = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True); pp = ph.new_page(); watch(pp, "phone"); login(pp, PHONE)
    pp.goto(f"{B}/quotes"); pp.wait_for_timeout(1500)
    pp.locator("[role=tab]:has-text('Razorpay invoices'), button:has-text('Razorpay invoices')").first.click(); pp.wait_for_timeout(1200)
    shot(pp, "phone_razorpay_tab")
    pp.locator("[role=tab]:has-text('Invoices'), button:has-text('Invoices')").first.click(); pp.wait_for_timeout(800)
    shot(pp, "phone_invoices")
    b.close()
print(f"\n{len(passed)} passed, {len(problems)} problems")
for x in problems: print(" -", x)
