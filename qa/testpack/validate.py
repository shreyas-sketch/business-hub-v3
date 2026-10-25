"""Feeds the test pack into a running local hub (mock AI) as a real owner would, and prints what each feature shows."""
import json
import os
import re
import sys
from datetime import timedelta

import httpx

sys.path.insert(0, os.path.dirname(__file__))
from data import BIZ, EXPENSES, FIXED_COSTS, FUNNEL, REVIEWS, TODAY, VENDORS, WINBACK  # noqa: E402

OUT = sys.argv[1]
B = "http://localhost:8000"


def login(email, pw):
    c = httpx.Client(base_url=B, timeout=120)
    r = c.post("/api/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return c


def show(label, r, keys=None, n=600):
    try:
        d = r.json()
    except Exception:  # noqa: BLE001
        d = r.text
    if keys and isinstance(d, dict):
        d = {k: d.get(k) for k in keys}
    print(f"\n== {label} [{r.status_code}]\n{json.dumps(d, default=str, ensure_ascii=False)[:n]}")
    return d


admin = login("admin@demo.hub", "demopass123")
c = httpx.Client(base_url=B, timeout=120)
r = c.post("/api/auth/register", json={"email": "rahul@deshmukhkitchens.example", "password": "testpass123", "name": BIZ["owner"], "phone": "9876543210"})
print("register:", r.status_code, r.text[:200])
if r.status_code != 200:
    c = login("rahul@deshmukhkitchens.example", "testpass123")
uid = c.get("/api/me").json()["user"]["id"]
admin.patch(f"/api/admin/users/{uid}", json={"plan": "growth"})

# 01 profile
prof = {k: BIZ[k] for k in ("name", "city", "industry", "offers", "ideal_customer", "why_us", "proof", "whatsapp", "email", "language", "payment_note")}
show("profile", c.put("/api/business", json=prof), ["name", "city"])
show("identity", c.put("/api/knowledge/identity", json={"entity": "pvt_ltd", "gst_registered": True, "gstin": BIZ["gstin"], "gst_scheme": "monthly",
                                                         "state": "Maharashtra", "staff": BIZ["staff"]}), n=200)
show("money", c.put("/api/knowledge/money", json={"payment_terms_days": BIZ["terms_days"], "bank_balance": 1842300,
                                                   "bank_balance_on": (TODAY - timedelta(days=1)).isoformat(), "gst_rate": 18, "safety_buffer": 200000,
                                                   "fixed_costs": [{"name": n, "amount": a, "day": d, "kind": k} for n, a, d, k in FIXED_COSTS]}), n=200)
show("targets", c.put("/api/knowledge/targets", json={"monthly_revenue": BIZ["monthly_target"]}), n=200)

# 02 funnel
f = FUNNEL
for label, vis in (("funnel A (with visitors)", f["visitors"]), ("funnel B (visitors 0)", 0)):
    d = show(label, c.put("/api/leaks", json={"numbers": {"visitors": vis, "inquiries": f["inquiries"], "meetings": f["meetings"], "proposals": f["proposals"],
                                                          "won": f["won"]}, "avg_sale": f["avg_sale"]}), n=1500)

# 06 sales register (xlsx) — once, feeds risk / paid / GST / Win-Back
with open(f"{OUT}/06_Sales_Register_Nov2025-Oct2026.xlsx", "rb") as fh:
    show("sales upload xlsx", c.post("/api/sales-upload", files={"file": ("06_Sales_Register_Nov2025-Oct2026.xlsx", fh.read(),
                                                                          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}), n=900)
risk = show("customer risk", c.get("/api/customer-risk"), ["line", "total_12m", "customers_12m", "top", "quiet"], n=2500)
paid = show("getting paid", c.get("/api/getting-paid"), n=2500)

# 04 expenses
cats = c.get("/api/expenses/settings").json()
print("\n== expense kind/categories:", cats.get("kind"), [x["label"] for x in cats.get("categories", [])][:30])
r = c.put("/api/expenses/settings", json={"custom": [{"label": "Hardware & fittings"}]})
print("custom category:", r.status_code, r.text[:200])
hw_key = next(x["key"] for x in c.get("/api/expenses/settings").json()["categories"] if x["label"] == "Hardware & fittings")
r = c.put("/api/expenses/settings", json={"budgets": {"purchases": 350000, hw_key: 60000, "marketing": 40000, "freight": 15000}})
print("budgets:", r.status_code, r.text[:200])
ids = []
for fname, ago, vkey, cat, taxable, gst, claim, paid_by, note in EXPENSES:
    vname, vgst = (VENDORS[vkey][0], VENDORS[vkey][2]) if vkey in VENDORS else (vkey, "")
    key = {"Raw material": "purchases", "Hardware & fittings": hw_key, "Marketing & ads": "marketing", "Electricity & power": "electricity",
           "Freight & courier": "freight"}[cat]
    r = c.post("/api/expenses", json={"date": (TODAY - timedelta(days=ago)).isoformat(), "amount": round(taxable + gst, 2), "gst": gst,
                                      "gst_claimable": claim, "category": key, "vendor": vname, "vendor_gstin": vgst,
                                      "paid_by": {"Bank transfer": "bank", "UPI": "upi", "Cash": "cash"}[paid_by], "note": note})
    assert r.status_code == 200, r.text
    eid = r.json()["id"]
    with open(f"{OUT}/04_Expense_Bills/{fname}", "rb") as fh:
        b = c.post(f"/api/expenses/{eid}/bill", files={"file": (fname, fh.read(), "application/pdf" if fname.endswith(".pdf") else "image/jpeg")})
    print(f"   expense {fname}: {r.status_code}, bill {b.status_code} {b.text[:120] if b.status_code != 200 else ''}")
show("expenses month", c.get(f"/api/expenses?month={TODAY.strftime('%Y-%m')}"), ["totals"], n=400)
show("gst set-aside", c.get("/api/gst-setaside"), n=2500)

# 05 bank statement: all three formats
for fname, pw in (("05_Bank_Statement_Jul-Sep2026.csv", ""), ("05_Bank_Statement_Jul-Sep2026.xlsx", ""),
                  ("05_Bank_Statement_Jul-Sep2026_password_DMK4417.pdf", "wrong"), ("05_Bank_Statement_Jul-Sep2026_password_DMK4417.pdf", "DMK4417")):
    with open(f"{OUT}/{fname}", "rb") as fh:
        r = c.post("/api/bank-statement", files={"file": (fname, fh.read())}, data={"password": pw})
    d = r.json()
    if r.status_code == 200:
        s = {k: d.get(k) for k in ("account_last4", "from", "to", "money_in", "money_out", "cash_withdrawn", "warnings", "not_in_expenses", "own_transfers", "counts")}
        print(f"\n== bank {fname} pw={pw!r} [200] keys={list(d)[:14]}\n{json.dumps(s, default=str)[:700]}")
        rec = d.get("recurring") or d.get("repeating")
        print("   repeating:", json.dumps(rec, default=str)[:600])
    else:
        print(f"\n== bank {fname} pw={pw!r} [{r.status_code}] {str(d)[:200]}")

# 10 reviews
for src, stars, who, ago, text, shows in REVIEWS[:1] + REVIEWS[3:4]:
    show(f"review {stars}*", c.post("/api/reputation/reviews", json={"platform": src, "rating": stars, "author": who, "text": text,
                                                                     "date": (TODAY - timedelta(days=ago)).isoformat()}), n=500)

# 11 Win-Back
show("win-back settings", c.put("/api/win-back/settings", json={k: WINBACK[k] for k in ("days", "min_purchases", "per_week", "offer")}), n=300)
show("win-back candidates", c.get("/api/win-back/candidates"), n=1200)
with open(f"{OUT}/11_Win-Back_Customer_List.xlsx", "rb") as fh:
    show("customers import", c.post("/api/customers/import", files={"file": ("11_Win-Back_Customer_List.xlsx", fh.read())}), n=300)
