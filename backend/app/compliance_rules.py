"""
The compliance rules table: Indian statutory filings and renewals, with when each applies and how its due date is worked
out. Dates come only from this table (and the admin's overrides) — never from the AI.

A CA must review the table before launch: Admin → Compliance rules records who reviewed it and when, and the owner's
page shows "Dates reviewed by a CA on <date>. Confirm with your CA." Until then it says the dates are not yet reviewed.

Each rule:
    key, title, category (tax | labour | licence | company | insurance | agreement | document | other), authority,
    applies  {entity: [...], gst: True/False, scheme: "monthly"/"qrmp", staff_min: n, states: [...], registrations: [...]}
             (every condition given must hold; a registration in `registrations` also switches it on, e.g. PF by choice)
    due      {"type": "monthly", "day": 20, "offset": 1, "exceptions": {"03": "04-30"}}   20th of the next month
             {"type": "month_end"}                                                       last day of the same month
             {"type": "quarterly", "day": 13, "offset": 1}                               13th of the month after each quarter
             {"type": "qrmp_payment", "day": 25}                                          25th, after months 1 and 2 of a quarter
             {"type": "dates", "dates": ["06-15", "09-15", ...], "labels": [...]}        fixed dates every year
             {"type": "owner_date"}                                                       a renewal: the owner enters the date
    plain, penalty_note, source_url
The admin can switch a rule off, edit it, add one, and move one due date when the government extends it.
"""
import calendar
import copy
from datetime import date, timedelta

GST_22 = ["Chhattisgarh", "Madhya Pradesh", "Gujarat", "Maharashtra", "Karnataka", "Goa", "Kerala", "Tamil Nadu", "Telangana", "Andhra Pradesh",
          "Dadra and Nagar Haveli and Daman and Diu", "Puducherry", "Andaman and Nicobar Islands", "Lakshadweep"]
COMPANY = ["pvt_ltd", "opc", "llp", "partnership"]
LATE = "Late filing costs a fee for every day it's late, plus interest on any tax paid late."

R = lambda key, title, category, authority, applies, due, plain, penalty, source: {  # noqa: E731
    "key": key, "title": title, "category": category, "authority": authority, "applies": applies, "due": due, "plain": plain,
    "penalty_note": penalty, "source_url": source, "on": True, "overrides": {}}

DEFAULT_RULES = [
    # ── GST ──
    R("gstr1", "GSTR-1 (sales return)", "tax", "GST", {"gst": True, "scheme": "monthly"}, {"type": "monthly", "day": 11, "offset": 1},
      "Every sale invoice you raised last month, reported to GST.", LATE, "https://www.gst.gov.in"),
    R("gstr3b", "GSTR-3B (GST return and payment)", "tax", "GST", {"gst": True, "scheme": "monthly"}, {"type": "monthly", "day": 20, "offset": 1},
      "Your GST payable for last month: tax on sales minus input credit, paid and filed.", LATE, "https://www.gst.gov.in"),
    R("gstr1_qrmp", "GSTR-1 (quarterly, QRMP)", "tax", "GST", {"gst": True, "scheme": "qrmp"}, {"type": "quarterly", "day": 13, "offset": 1},
      "Every sale invoice of the last quarter, reported to GST.", LATE, "https://www.gst.gov.in"),
    R("gstr3b_qrmp_22", "GSTR-3B (quarterly, QRMP)", "tax", "GST", {"gst": True, "scheme": "qrmp", "states": GST_22},
      {"type": "quarterly", "day": 22, "offset": 1}, "Your GST return for the last quarter.", LATE, "https://www.gst.gov.in"),
    R("gstr3b_qrmp_24", "GSTR-3B (quarterly, QRMP)", "tax", "GST", {"gst": True, "scheme": "qrmp", "states_not": GST_22},
      {"type": "quarterly", "day": 24, "offset": 1}, "Your GST return for the last quarter.", LATE, "https://www.gst.gov.in"),
    R("pmt06", "GST payment (PMT-06, QRMP)", "tax", "GST", {"gst": True, "scheme": "qrmp"}, {"type": "qrmp_payment", "day": 25},
      "GST for the first and second month of the quarter, paid by challan.", "Interest is charged on GST paid late.", "https://www.gst.gov.in"),
    # ── Income tax and TDS ──
    R("tds_payment", "TDS deposit", "tax", "Income Tax", {"entity": COMPANY}, {"type": "monthly", "day": 7, "offset": 1, "exceptions": {"03": "04-30"}},
      "Tax you deducted last month (salaries above the limit, contractors, rent, professional fees), deposited to the government.",
      "Interest of 1.5% a month on TDS deposited late.", "https://www.incometax.gov.in"),
    R("tds_return", "TDS return (24Q / 26Q)", "tax", "Income Tax", {"entity": COMPANY},
      {"type": "dates", "dates": ["07-31", "10-31", "01-31", "05-31"], "labels": ["Apr–Jun", "Jul–Sep", "Oct–Dec", "Jan–Mar"]},
      "The quarterly statement of the TDS you deducted and deposited.", "A late fee of ₹200 a day, up to the TDS amount.", "https://www.tdscpc.gov.in"),
    R("advance_tax", "Advance tax instalment", "tax", "Income Tax", {},
      {"type": "dates", "dates": ["06-15", "09-15", "12-15", "03-15"], "labels": ["15% of the year's tax", "45% of the year's tax", "75% of the year's tax",
                                                                                "100% of the year's tax"]},
      "If the year's tax is above ₹10,000, it's paid in four instalments during the year.", "Interest of 1% a month on instalments paid short or late.",
      "https://www.incometax.gov.in"),
    R("itr_july", "Income-tax return", "tax", "Income Tax", {"entity": ["proprietor", "partnership", "other"]}, {"type": "dates", "dates": ["07-31"], "for": "previous_fy"},
      "Last financial year's income-tax return (31 October instead if your accounts are audited).", "A late fee of up to ₹5,000, plus interest.",
      "https://www.incometax.gov.in"),
    R("itr_october", "Income-tax return", "tax", "Income Tax", {"entity": ["pvt_ltd", "opc", "llp"]}, {"type": "dates", "dates": ["10-31"], "for": "previous_fy"},
      "The company's or LLP's income-tax return for last financial year, with the audit report.", "A late fee of up to ₹5,000, plus interest.",
      "https://www.incometax.gov.in"),
    # ── Staff ──
    R("pf", "PF contribution and ECR", "labour", "EPFO", {"staff_min": 20, "or_registration": "pf"}, {"type": "monthly", "day": 15, "offset": 1},
      "Provident Fund for your staff for last month, paid and filed (applies from 20 staff).", "Damages and 12% interest on PF paid late.",
      "https://www.epfindia.gov.in"),
    R("esi", "ESI contribution", "labour", "ESIC", {"staff_min": 10, "or_registration": "esi"}, {"type": "monthly", "day": 15, "offset": 1},
      "ESI for staff earning up to the wage limit, for last month (applies from 10 staff).", "Interest of 12% a year on ESI paid late.",
      "https://www.esic.gov.in"),
    R("pt_mh", "Professional tax (employer)", "labour", "Maharashtra GST Department", {"states": ["Maharashtra"], "staff_min": 1},
      {"type": "month_end"}, "Professional tax deducted from salaries this month (yearly by 31 March if last year's total was under ₹50,000).",
      "Interest and a penalty on tax paid late.", "https://mahagst.gov.in"),
    R("ptec_mh", "Professional tax (business, PTEC)", "labour", "Maharashtra GST Department", {"states": ["Maharashtra"]},
      {"type": "dates", "dates": ["06-30"]}, "The business's own yearly professional tax.", "Interest and a penalty on tax paid late.", "https://mahagst.gov.in"),
    R("pt_ka", "Professional tax (employer)", "labour", "Karnataka Commercial Taxes", {"states": ["Karnataka"], "staff_min": 1},
      {"type": "monthly", "day": 20, "offset": 1}, "Professional tax deducted from last month's salaries.", "Interest and a penalty on tax paid late.",
      "https://pt.kar.nic.in"),
    R("pt_gj", "Professional tax (employer)", "labour", "Gujarat local authority", {"states": ["Gujarat"], "staff_min": 1},
      {"type": "monthly", "day": 15, "offset": 1}, "Professional tax deducted from last month's salaries.", "Interest and a penalty on tax paid late.",
      "https://commercialtax.gujarat.gov.in"),
    R("pt_tg", "Professional tax (employer)", "labour", "Telangana Commercial Taxes", {"states": ["Telangana"], "staff_min": 1},
      {"type": "monthly", "day": 10, "offset": 1}, "Professional tax deducted from last month's salaries.", "Interest and a penalty on tax paid late.",
      "https://www.tgct.gov.in"),
    R("pt_wb", "Professional tax (employer)", "labour", "West Bengal Professional Tax Directorate", {"states": ["West Bengal"], "staff_min": 1},
      {"type": "monthly", "day": 21, "offset": 1}, "Professional tax deducted from last month's salaries.", "Interest and a penalty on tax paid late.",
      "https://egov.wbcomtax.gov.in"),
    # ── Company and LLP (Ministry of Corporate Affairs) ──
    R("aoc4", "AOC-4 (financial statements to ROC)", "company", "MCA", {"entity": ["pvt_ltd"]}, {"type": "dates", "dates": ["10-30"], "for": "previous_fy"},
      "The audited accounts, filed within 30 days of the AGM (AGM by 30 September).", "An additional fee of ₹100 a day.", "https://www.mca.gov.in"),
    R("mgt7", "MGT-7 (annual return to ROC)", "company", "MCA", {"entity": ["pvt_ltd"]}, {"type": "dates", "dates": ["11-29"], "for": "previous_fy"},
      "The company's annual return, filed within 60 days of the AGM.", "An additional fee of ₹100 a day.", "https://www.mca.gov.in"),
    R("aoc4_opc", "AOC-4 (financial statements, OPC)", "company", "MCA", {"entity": ["opc"]}, {"type": "dates", "dates": ["09-27"], "for": "previous_fy"},
      "A one-person company's accounts, within 180 days of the year end.", "An additional fee of ₹100 a day.", "https://www.mca.gov.in"),
    R("llp_form11", "LLP Form 11 (annual return)", "company", "MCA", {"entity": ["llp"]}, {"type": "dates", "dates": ["05-30"], "for": "previous_fy"},
      "The LLP's annual return for last financial year.", "An additional fee of ₹100 a day.", "https://www.mca.gov.in"),
    R("llp_form8", "LLP Form 8 (accounts and solvency)", "company", "MCA", {"entity": ["llp"]}, {"type": "dates", "dates": ["10-30"], "for": "previous_fy"},
      "The LLP's statement of accounts and solvency.", "An additional fee of ₹100 a day.", "https://www.mca.gov.in"),
    # ── Licences and registrations (renewal dates come from the owner's own licence) ──
    R("fssai", "FSSAI licence renewal", "licence", "FSSAI", {"registration": "fssai"}, {"type": "owner_date"},
      "Your food licence. Apply for renewal before it expires.", "Food business without a valid licence can be stopped and fined.",
      "https://foscos.fssai.gov.in"),
    R("shops", "Shops & Establishment renewal", "licence", "Local labour department", {"registration": "shops"}, {"type": "owner_date"},
      "Your shop or office registration, if your state needs it renewed.", "A fine for operating without a valid registration.", ""),
    R("trade_licence", "Trade licence renewal", "licence", "Municipal corporation", {"registration": "trade_licence"}, {"type": "owner_date"},
      "Your municipal trade licence.", "A fine, and the premises can be sealed.", ""),
    R("iec_update", "IEC yearly update", "licence", "DGFT", {"registration": "iec"}, {"type": "dates", "dates": ["06-30"]},
      "Confirm your Import Export Code details every year between April and June, even if nothing changed.",
      "An IEC not updated is de-activated.", "https://www.dgft.gov.in"),
    R("fire_noc", "Fire NOC renewal", "licence", "Fire department", {"registration": "fire_noc"}, {"type": "owner_date"},
      "Your fire safety certificate.", "Premises without a valid fire NOC can be sealed.", ""),
    R("pollution", "Pollution consent renewal", "licence", "State Pollution Control Board", {"registration": "pollution"}, {"type": "owner_date"},
      "Your consent to operate.", "Operating without consent can lead to closure.", ""),
    R("drug_licence", "Drug licence renewal", "licence", "State drug controller", {"registration": "drug_licence"}, {"type": "owner_date"},
      "Your drug licence.", "Selling without a valid licence is an offence.", ""),
    R("trademark", "Trademark renewal", "licence", "Trade Marks Registry", {"registration": "trademark"}, {"type": "owner_date"},
      "Your trademark lasts 10 years from the application date. Renew it in the last 6 months.", "A lapsed mark can be removed from the register.",
      "https://ipindia.gov.in"),
]
DEFAULT_KEYS = [r["key"] for r in DEFAULT_RULES]
DUE_TYPES = ("monthly", "month_end", "quarterly", "qrmp_payment", "dates", "owner_date")


def applies(rule: dict, identity: dict) -> bool:
    """Does this rule apply to a business with this identity? Every condition given must hold."""
    if not rule.get("on", True):
        return False
    a = rule.get("applies") or {}
    regs = set(identity.get("registrations") or [])
    if a.get("registration"):
        return a["registration"] in regs
    if a.get("or_registration") and a["or_registration"] in regs:
        return True
    if a.get("entity") and identity.get("entity") not in a["entity"]:
        return False
    if "gst" in a and bool(identity.get("gst_registered")) != a["gst"]:
        return False
    if a.get("scheme") and (identity.get("gst_scheme") or "monthly") != a["scheme"]:
        return False
    if a.get("staff_min") and int(identity.get("staff") or 0) < a["staff_min"]:
        return False
    if a.get("states") and identity.get("state") not in a["states"]:
        return False
    if a.get("states_not") and identity.get("state") in a["states_not"]:
        return False
    return True


def _add_months(year: int, month: int, n: int) -> tuple[int, int]:
    m = year * 12 + (month - 1) + n
    return m // 12, m % 12 + 1


def _on(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def fy(d: date) -> str:
    start = d.year if d.month >= 4 else d.year - 1
    return f"FY {start}-{str(start + 1)[2:]}"


def due_dates(rule: dict, start: date, end: date) -> list[tuple[str, date]]:
    """(period label, due date) for every due date of this rule between start and end (inclusive), with overrides applied."""
    due = rule.get("due") or {}
    kind = due.get("type")
    out: list[tuple[str, date]] = []
    if kind in ("monthly", "month_end"):
        offset = int(due.get("offset", 1)) if kind == "monthly" else 0
        y, m = _add_months(start.year, start.month, -offset - 1)
        while True:
            dy, dm = _add_months(y, m, offset)
            exc = (due.get("exceptions") or {}).get(f"{m:02d}") if kind == "monthly" else None
            if exc:   # e.g. TDS for March is due on 30 April, not 7 April
                when = _on(dy, int(exc[:2]), int(exc[3:]))
            else:
                when = _on(dy, dm, int(due.get("day", 1))) if kind == "monthly" else _on(y, m, 31)
            if when > end:
                break
            if when >= start:
                out.append((date(y, m, 1).strftime("%B %Y"), when))
            y, m = _add_months(y, m, 1)
    elif kind in ("quarterly", "qrmp_payment"):
        y, m = _add_months(start.year, start.month, -4)
        while True:
            q_end = m in (3, 6, 9, 12)
            take = q_end if kind == "quarterly" else not q_end
            dy, dm = _add_months(y, m, int(due.get("offset", 1)))
            when = _on(dy, dm, int(due.get("day", 1)))
            if when > end:
                break
            if take and when >= start:
                if kind == "quarterly":
                    qs = date(*_add_months(y, m, -2), 1)
                    label = f"{qs.strftime('%b')}–{date(y, m, 1).strftime('%b %Y')}"
                else:
                    label = date(y, m, 1).strftime("%B %Y")
                out.append((label, when))
            y, m = _add_months(y, m, 1)
    elif kind == "dates":
        labels = due.get("labels") or []
        for year in range(start.year - 1, end.year + 2):
            for i, md in enumerate(due.get("dates") or []):
                when = _on(year, int(md[:2]), int(md[3:]))
                if start <= when <= end:
                    if i < len(labels):
                        label = f"{labels[i]} · {fy(when - timedelta(days=70))}"
                    elif due.get("for") == "previous_fy":
                        label = fy(when - timedelta(days=365))
                    else:
                        label = fy(when)
                    out.append((label, when))
    overrides = rule.get("overrides") or {}
    moved = []
    for label, when in out:
        new = overrides.get(when.isoformat())
        moved.append((label, date.fromisoformat(new) if new else when))
    return sorted(moved, key=lambda x: x[1])


def merged(saved: list[dict]) -> list[dict]:
    """The defaults with the admin's saved versions on top, plus rules the admin added."""
    by_key = {r["key"]: copy.deepcopy(r) for r in DEFAULT_RULES}
    for r in saved:
        k = r.get("key")
        if not k:
            continue
        base = by_key.get(k, {"key": k, "on": True, "overrides": {}, "added": True})
        base.update({f: v for f, v in r.items() if f != "_id"})
        by_key[k] = base
    order = {k: i for i, k in enumerate(DEFAULT_KEYS)}
    return sorted(by_key.values(), key=lambda r: (order.get(r["key"], 999), r["key"]))
