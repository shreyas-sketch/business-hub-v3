"""
Engine Audit (v2.3): the 4-3-2-1 audit, done on the hub — the Day 1 activity of the 3-day "your time back" event.

The owner checks their business against the 14 processes of the AI Advantage Day 1 audit ("A website that brings you
inquiries every week, without you chasing anyone"), one list. For each one the owner taps Have it or Missing — nothing to
type. A gap counts the hours a week a typical small business loses to it (est_hours, the admin can tune it), and the owner
sees straight away which OwnerDesk feature fixes it. (The API still takes a ₹ a month and an hours figure per gap; audits
saved before the page stopped asking hold them, and the ₹ figures show only when there are some.)

The arithmetic (kept simple and conservative; the admin can tune it in Admin → Engine Audit):
    growth unlocked a month = rupees it costs a month × the item's recovery rate (default 0.5)
    hours back a week       = hours it takes a week × the item's hours rate (default 0.5)
    when the owner's monthly sales are known, total growth unlocked is capped at cap_pct (default 50%) of them.
Every figure is labelled "Estimated from your own numbers." Nothing here calls the AI: the audit uses 0 tokens.

Items follow the feature registry: an item whose feature is switched off (or that the admin switches off) drops out.

config {_id "engine_audit", items_day1 {key: {statement, hint_cost, hint_hours, est_hours, rate, hours_rate, on}}, cap_pct, updated_at}
       (items: the admin's edits to the 16 statements used before the Day 1 audit's 14; no longer read)
engine_audits {_id, owner_id, answers [{item_key, engine, feature, status, monthly_cost, hours_week, growth_unlocked,
               hours_back}], totals {...}, revenue {month, source}, completed_at, by}
  (audits saved before "Not sure" was taken out may hold status "unsure" and cost_unsure / hours_unsure: read as Missing)
engine_audit_drafts {_id owner_id, answers {key: {...}}, revenue, updated_at}
"""
import io
import time
from datetime import datetime

from . import plans
from .db import IST, db, now

NOTE = "Estimated from your own numbers."
HOURS_NOTE = "Typical hours for a small business."
CAP_NOTE = "Capped at a conservative level"
DEFAULT_CAP_PCT = 50
DEFAULT_RATE = 0.5
MAX_COST = 1e9          # ₹ a month for one gap
MAX_HOURS = 1000        # hours a week (a whole team's)

# (key, engine, statement, feature that fixes it, hint for the ₹ question, what the hours go on, hours a week a typical small
# business loses to the gap): the 14 processes of the AI Advantage Day 1 audit, in its order, with its words
DEFAULT_ITEMS = [
    ("lead_website", "lead", "A website that brings you inquiries every week, without you chasing anyone", "website",
     "Inquiries you miss a month × your average order", "Hours spent explaining your business and sending details again and again", 3),
    ("lead_magnet", "lead", "A free guide or checklist that collects contacts of people interested in what you sell", "lead_magnet",
     "Leads you could collect a month × your close rate × your average order", "Hours spent finding new people to talk to", 3),
    ("sales_invoicing", "sales", "Quotations and invoices that go out the same day, without you sitting to make them", "quotations",
     "Staff hours a month × hourly cost, plus payments delayed by late invoices", "Hours spent making, checking and resending quotes and bills", 5),
    ("sales_levers", "sales", "A clear plan to grow revenue: more customers, bigger orders, more repeat buying", "revenue_levers",
     "Growth you're leaving on the table a month", "Hours spent on growth ideas that go nowhere", 2),
    ("conv_inquiries", "conversion", "Every inquiry, from WhatsApp, email, calls and website, in one place and answered fast",
     "inquiry_desk", "Inquiries a month that never get a reply × your close rate × your average order",
     "Hours spent searching chats and replying late", 5),
    ("conv_pipeline", "conversion", "Every lead tracked stage by stage, so none is forgotten", "pipeline",
     "Deals a month lost to forgotten follow-ups × your average order", "Hours spent remembering who to call back", 3),
    ("conv_deal_value", "conversion", "You know exactly how much money is sitting in open deals right now", "crm",
     "Deals a month that slip because nobody was watching them × your average order", "Hours spent asking your team for updates", 2),
    ("ops_compliance", "operations", "Every GST, TDS, licence and renewal date tracked, and reminded before it's due",
     "compliance_calendar", "Penalties and late fees in a typical year ÷ 12", "Hours spent chasing your CA and fixing late filings", 2),
    ("ops_sops", "operations", "Your key processes written down, so your team doesn't need to ask you", "sop_library",
     "Your hours a month spent explaining and fixing mistakes × what an hour of your time is worth",
     "Hours spent answering the same questions", 5),
    ("ops_payments", "operations", "Pending payments followed up on time, without you chasing", "money_owed",
     "Money stuck overdue a month (the interest on it, plus your time)", "Hours spent calling customers for money", 4),
    ("brand_kit", "brand", "One brand message and voice, the same on your website, WhatsApp and sales calls", "brand_kit",
     "Business lost to looking smaller or less trustworthy than competitors", "Hours spent rewriting how you describe your business", 1.5),
    ("brand_social", "brand", "Posting on social media every week, with content ready in advance", "social_media",
     "Inquiries a month you'd get from regular posting × your close rate × your average order",
     "Hours spent thinking what to post, or not posting at all", 4),
    ("data_money", "data", "Knowing exactly where your money goes every month", "expenses",
     "Spending a month you'd cut if you could see it", "Hours spent going through bank statements and bills", 3),
    ("data_keyperson", "data", "If your most important employee left tomorrow, their know-how stays in the business", "key_person",
     "What a month of disruption would cost if that person left", "Hours spent covering for, or depending on, one person", 3),
]
ITEM_KEYS = [i[0] for i in DEFAULT_ITEMS]
# features without a page of their own: where the fix lives
FIX_PATH = {"brochures": "/brand-kit?tab=brochures"}
# where the admin's edits are kept: a new field for the 14 Day 1 questions, so edits made to the earlier 16 don't put old
# wording on the new questions
ITEMS_FIELD = "items_day1"
EDITABLE = {"statement": str, "hint_cost": str, "hint_hours": str, "est_hours": (int, float), "rate": (int, float),
            "hours_rate": (int, float), "on": bool}


def defaults() -> dict[str, dict]:
    return {k: {"key": k, "engine": e, "statement": s, "feature": f, "hint_cost": hc, "hint_hours": hh, "est_hours": eh,
                "rate": DEFAULT_RATE, "hours_rate": DEFAULT_RATE, "on": True, "order": n}
            for n, (k, e, s, f, hc, hh, eh) in enumerate(DEFAULT_ITEMS)}


# ───────────────────────── the admin's settings (cached a few seconds: 300 phones at once) ─────────────────────────
_cache: dict = {"at": 0.0, "cfg": None}


async def load_config(fresh: bool = False) -> dict:
    if not fresh and _cache["cfg"] is not None and time.monotonic() - _cache["at"] < 5:
        return _cache["cfg"]
    doc = await db().config.find_one({"_id": "engine_audit"}) or {}
    items = defaults()
    for key, patch in (doc.get(ITEMS_FIELD) or {}).items():
        if key not in items or not isinstance(patch, dict):
            continue
        for field, kind in EDITABLE.items():
            v = patch.get(field)
            if v is None or not isinstance(v, kind) or (field != "on" and isinstance(v, bool)):
                continue
            items[key][field] = v
    cap = doc.get("cap_pct")
    cfg = {"items": items, "cap_pct": cap if isinstance(cap, int) and not isinstance(cap, bool) and 0 < cap <= 100 else DEFAULT_CAP_PCT,
           "overrides": doc.get(ITEMS_FIELD) or {}}
    _cache.update(at=time.monotonic(), cfg=cfg)
    return cfg


def forget_cache() -> None:
    _cache.update(at=0.0, cfg=None)


def active_items(cfg: dict) -> list[dict]:
    """The items in the audit right now: switched on here, and their feature on in the registry."""
    out = []
    for it in sorted(cfg["items"].values(), key=lambda i: (plans.ENGINE_KEYS.index(i["engine"]), i["order"])):
        m = plans.META.get(it["feature"])
        if it["on"] and m and m["on"]:
            out.append(it)
    return out


def fix_for(owner: dict, feature: str) -> dict:
    """The deliverable that fixes a gap, as this owner can reach it: open now, opens on a day of the event, or in the plan."""
    m = plans.META.get(feature) or {}
    path = m.get("path") or FIX_PATH.get(feature) or "/plans"
    if plans.has(owner, feature):
        return {"state": "open", "label": m.get("label", feature), "path": path}
    if plans.day_locked(owner, feature):
        return {"state": "day", "label": m.get("label", feature), "path": path, "opens_day": plans.feature_day(feature)}
    return {"state": "plan", "label": m.get("label", feature), "path": "/plans", "tier_name": plans.PLANS[m.get("tier", "lite")]["name"]}


def item_view(owner: dict, it: dict) -> dict:
    return {"key": it["key"], "engine": it["engine"], "statement": it["statement"], "feature": it["feature"],
            "hint_cost": it["hint_cost"], "hint_hours": it["hint_hours"], "est_hours": it["est_hours"], "rate": it["rate"],
            "hours_rate": it["hours_rate"],
            "fix": fix_for(owner, it["feature"])}


# ───────────────────────── the owner's sales a month (for the cap) ─────────────────────────
async def revenue_hint(ws: str) -> dict:
    """The owner's sales in a typical month, if the hub knows them: Revenue Multiplier's numbers (a year ÷ 12), then the
    Knowledge Hub's monthly revenue target. None when neither is filled in."""
    lv = await db().levers.find_one({"_id": ws}, {"result.revenue": 1}) or {}
    year = ((lv.get("result") or {}).get("revenue")) or 0
    if year and year > 0:
        return {"month": round(year / 12), "source": "your Revenue Multiplier numbers"}
    b = await db().businesses.find_one({"owner_id": ws}, {"targets.monthly_revenue": 1}) or {}
    target = (b.get("targets") or {}).get("monthly_revenue") or 0
    if target and target > 0:
        return {"month": round(float(target)), "source": "your monthly revenue target in the Knowledge Hub"}
    return {"month": None, "source": None}


# ───────────────────────── the arithmetic ─────────────────────────
def _num(v, top: float) -> float:
    try:
        x = float(v or 0)
    except (TypeError, ValueError):
        return 0.0
    if x != x or x < 0:      # NaN or below zero
        return 0.0
    return min(x, top)


def compute(items: list[dict], answers: dict, revenue_month: float | None, cap_pct: int) -> dict:
    """answers: {item_key: {status: have|missing, monthly_cost, hours_week}}. A gap with no hours given counts the item's
    estimate (est_hours: what a typical small business loses to it a week); the page sends only Have it / Missing.
    Returns the per-item rows, per-engine totals, overall totals and the top 3 gaps. The page does the same sums live."""
    rows = []
    for it in items:
        a = answers.get(it["key"]) or {}
        status = {"have": "have", "missing": "missing", "unsure": "missing"}.get(a.get("status"))   # "unsure": an older draft
        gap = status == "missing"
        cost = _num(a.get("monthly_cost"), MAX_COST) if gap else 0.0
        hours = (_num(a.get("hours_week"), MAX_HOURS) if a.get("hours_week") is not None else float(it["est_hours"])) if gap else 0.0
        rows.append({"item_key": it["key"], "engine": it["engine"], "feature": it["feature"], "statement": it["statement"],
                     "status": status, "monthly_cost": round(cost), "hours_week": round(hours, 1),
                     "growth_unlocked": cost * float(it["rate"]), "hours_back": hours * float(it["hours_rate"])})
    growth_total = sum(r["growth_unlocked"] for r in rows)
    cap_amount = round(revenue_month * cap_pct / 100) if revenue_month and revenue_month > 0 else None
    capped = bool(cap_amount is not None and growth_total > cap_amount)
    factor = (cap_amount / growth_total) if capped and growth_total else 1.0
    for r in rows:
        r["growth_unlocked"] = round(r["growth_unlocked"] * factor)
        r["hours_back"] = round(r["hours_back"], 1)

    def sums(rs: list[dict]) -> dict:
        return {"in_place": sum(1 for r in rs if r["status"] == "have"), "total": len(rs),
                "answered": sum(1 for r in rs if r["status"]), "gaps": sum(1 for r in rs if r["status"] == "missing"),
                "cost_month": round(sum(r["monthly_cost"] for r in rs)), "growth_month": round(sum(r["growth_unlocked"] for r in rs)),
                "hours_week": round(sum(r["hours_week"] for r in rs), 1), "hours_back_week": round(sum(r["hours_back"] for r in rs), 1)}

    engines = []
    for e in plans.ENGINES:
        rs = [r for r in rows if r["engine"] == e["key"]]
        if rs:
            engines.append({"key": e["key"], "label": e["label"], **sums(rs)})
    gaps = sorted((r for r in rows if r["status"] == "missing"), key=lambda r: (-r["monthly_cost"], -r["hours_week"]))
    return {"rows": rows, "engines": engines, "totals": {**sums(rows), "capped": capped, "cap_amount": cap_amount, "cap_pct": cap_pct},
            "top_gaps": [r["item_key"] for r in gaps[:3] if r["monthly_cost"] or r["hours_week"]] or [r["item_key"] for r in gaps[:3]],
            "note": NOTE}


# ───────────────────────── the PDF and the share card ─────────────────────────
C = {"bg": "#14151A", "panel": "#1E2027", "line": "#2A2D36", "ivory": "#F2EEE6", "ivory70": "#B6B3AD", "ivory35": "#6E6D6B",
     "gold": "#C9A84C", "gold_light": "#F0D98A"}


def inr(x: float) -> str:
    """₹1,23,456 — Indian grouping."""
    n = int(round(x or 0))
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    return ("-₹" if n < 0 else "₹") + s


def hours(x: float) -> str:
    v = round(float(x or 0), 1)
    return f"{int(v)}" if v == int(v) else f"{v}"


STATUS_WORD = {"have": "In place", "missing": "Missing", "unsure": "Missing", None: "Not answered"}   # "unsure": an older audit


def pdf(audit: dict, business: str) -> bytes:
    """A4, the OwnerDesk dark look: the totals, the engine-by-engine scorecard with every statement, and the top 3 gaps."""
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    from .day1_render import PDF_LOCK, _register_pdf_fonts
    _register_pdf_fonts()
    W, H = A4
    M = 40
    buf = io.BytesIO()
    t = audit["totals"]
    rows = {r["item_key"]: r for r in audit["answers"]}
    when = audit["completed_at"].astimezone(IST).strftime("%d %b %Y") if isinstance(audit.get("completed_at"), datetime) else ""
    with PDF_LOCK:
        c = canvas.Canvas(buf, pagesize=A4, pageCompression=1)
        c.setTitle("Engine Audit")
        c.setAuthor("OwnerDesk")
        c.setCreator("OwnerDesk")

        def page_bg():
            c.setFillColor(HexColor(C["bg"]))
            c.rect(0, 0, W, H, stroke=0, fill=1)
            c.setFillColor(HexColor(C["ivory35"]))
            c.setFont("day1-body", 8)
            c.drawString(M, 22, f"OwnerDesk · Engine Audit · {business}"[:110])
            c.drawRightString(W - M, 22, f"{NOTE if t['cost_month'] else HOURS_NOTE} Not a promise or a guarantee.")

        def wrap(text: str, font: str, size: float, width: float) -> list[str]:
            from reportlab.pdfbase.pdfmetrics import stringWidth
            words, lines, cur = str(text).split(), [], ""
            for w in words:
                trial = f"{cur} {w}".strip()
                if stringWidth(trial, font, size) <= width or not cur:
                    cur = trial
                else:
                    lines.append(cur)
                    cur = w
            if cur:
                lines.append(cur)
            return lines

        page_bg()
        y = H - M - 6
        c.setFillColor(HexColor(C["gold"]))
        c.setFont("day1-body_semi", 9)
        c.drawString(M, y, "ENGINE AUDIT" + (f"  ·  {when}" if when else ""))
        y -= 30
        c.setFillColor(HexColor(C["ivory"]))
        c.setFont("day1-head_bold", 24)
        for line in wrap(business or "Your business", "day1-head_bold", 24, W - 2 * M)[:2]:
            c.drawString(M, y, line)
            y -= 28
        c.setFont("day1-body", 10)
        c.setFillColor(HexColor(C["ivory70"]))
        c.drawString(M, y, f"{t['in_place']} of {t['total']} accomplishments in place across the 6 engines.")
        y -= 24
        # four tiles
        money = bool(t["cost_month"])     # audits from the page are hours only; earlier ones may hold ₹ figures
        tiles = ([("Cost of gaps / month", inr(t["cost_month"])), ("Growth unlocked / month", inr(t["growth_month"]))] if money else []) + \
            [("Hours lost / week", hours(t["hours_week"])), ("Hours you get back / week", hours(t["hours_back_week"])),
             ("Hours you get back / year", hours(t["hours_back_week"] * 52))][:2 if money else 3]
        tw = (W - 2 * M - (len(tiles) - 1) * 10) / len(tiles)
        for i, (label, value) in enumerate(tiles):
            x = M + i * (tw + 10)
            c.setFillColor(HexColor(C["panel"]))
            c.roundRect(x, y - 62, tw, 62, 8, stroke=0, fill=1)
            c.setFillColor(HexColor(C["ivory70"]))
            c.setFont("day1-body", 8)
            c.drawString(x + 10, y - 16, label)
            c.setFillColor(HexColor(C["gold"]))
            c.setFont("day1-head_bold", 18)
            c.drawString(x + 10, y - 44, value)
        y -= 78
        c.setFont("day1-body", 8.5)
        c.setFillColor(HexColor(C["ivory70"]))
        note = (NOTE + (f" Growth is {CAP_NOTE.lower()}: {t['cap_pct']}% of your sales in a month ({inr(t['cap_amount'])})."
                        if t.get("capped") else "") + " A year: " + inr(t["growth_month"] * 12) + " of growth and " +
                hours(t["hours_back_week"] * 52) + " hours back.") if money else \
            f"{HOURS_NOTE} A year: {hours(t['hours_week'] * 52)} hours lost, {hours(t['hours_back_week'] * 52)} back."
        for line in wrap(note, "day1-body", 8.5, W - 2 * M):
            c.drawString(M, y, line)
            y -= 12
        y -= 8

        def need(h: float):
            nonlocal y
            if y - h < 50:
                c.showPage()
                page_bg()
                y = H - M

        # top 3 gaps
        if audit.get("top_gaps"):
            need(30 + 34 * len(audit["top_gaps"]))
            c.setFillColor(HexColor(C["gold"]))
            c.setFont("day1-body_semi", 9)
            c.drawString(M, y, "YOUR TOP 3 GAPS")
            y -= 18
            for k in audit["top_gaps"]:
                r = rows.get(k)
                if not r:
                    continue
                c.setFillColor(HexColor(C["ivory"]))
                c.setFont("day1-body_semi", 10.5)
                c.drawString(M, y, r["statement"][:95])
                y -= 14
                c.setFillColor(HexColor(C["ivory70"]))
                c.setFont("day1-body", 9)
                cost = f"Costs about {inr(r['monthly_cost'])} a month and " if r["monthly_cost"] else "Takes about "
                c.drawString(M, y, f"{cost}{hours(r['hours_week'])} hours a week · Fixed by {plans.label_of(r['feature'])}"[:120])
                y -= 20
            y -= 6
        # scorecard, engine by engine
        for e in audit["engines"]:
            erows = [r for r in audit["answers"] if r["engine"] == e["key"]]
            need(40 + 30 * len(erows))
            c.setStrokeColor(HexColor(C["line"]))
            c.line(M, y + 6, W - M, y + 6)
            y -= 12
            c.setFillColor(HexColor(C["ivory"]))
            c.setFont("day1-head_bold", 14)
            c.drawString(M, y, f"{e['label']} engine")
            c.setFillColor(HexColor(C["gold"]))
            c.setFont("day1-body_semi", 10)
            c.drawRightString(W - M, y, f"{e['in_place']} of {e['total']} in place")
            y -= 18
            for r in erows:
                lines = wrap(r["statement"], "day1-body", 10, W - 2 * M - 110)
                need(14 * len(lines) + 16)
                c.setFillColor(HexColor(C["ivory"]))
                c.setFont("day1-body", 10)
                for j, line in enumerate(lines):
                    c.drawString(M, y - j * 13, line)
                c.setFillColor(HexColor(C["gold"] if r["status"] == "have" else C["ivory70"]))
                c.setFont("day1-body_semi", 9.5)
                c.drawRightString(W - M, y, STATUS_WORD.get(r["status"], ""))
                y -= 13 * len(lines)
                if r["status"] in ("missing", "unsure"):
                    c.setFillColor(HexColor(C["ivory70"]))
                    c.setFont("day1-body", 8.5)
                    cost = f"{inr(r['monthly_cost'])} a month · " if r["monthly_cost"] else ""
                    c.drawString(M, y, f"{cost}{hours(r['hours_week'])} hours a week · Fixed by {plans.label_of(r['feature'])}"[:130])
                    y -= 12
                y -= 6
        c.showPage()
        c.save()
    return buf.getvalue()


def card_png(audit: dict, business: str) -> bytes:
    """A 1080×1350 image for WhatsApp Status or a post: growth unlocked, hours back, and the 6 engines."""
    from PIL import Image, ImageDraw

    from .day1_render import PilMeasure
    f = PilMeasure.font
    W, H = 1080, 1350
    img = Image.new("RGB", (W, H), C["bg"])
    d = ImageDraw.Draw(img)
    t = audit["totals"]
    M = 80

    def fit(text: str, key: str, size: int, width: int) -> tuple[str, int]:
        while size > 18 and f(key, size).getlength(text) > width:
            size -= 2
        return text, size

    d.rounded_rectangle((40, 40, W - 40, H - 40), radius=28, outline=C["line"], width=2)
    d.text((M, 100), "ENGINE AUDIT", font=f("body_semi", 30), fill=C["gold"])
    name, size = fit(business or "My business", "head_bold", 60, W - 2 * M)
    d.text((M, 150), name, font=f("head_bold", size), fill=C["ivory"])
    if t["cost_month"]:
        d.text((M, 250), "Growth I can unlock a month", font=f("body", 34), fill=C["ivory70"])
        value, size = fit(inr(t["growth_month"]), "head_bold", 120, W - 2 * M)
        d.text((M, 295), value, font=f("head_bold", size), fill=C["gold"])
        d.text((M, 450), f"+ {hours(t['hours_back_week'])} hours a week back", font=f("head", 46), fill=C["ivory"])
    else:   # an hours-only audit
        d.text((M, 250), "Hours I can get back a year", font=f("body", 34), fill=C["ivory70"])
        value, size = fit(hours(t["hours_back_week"] * 52), "head_bold", 120, W - 2 * M)
        d.text((M, 295), value, font=f("head_bold", size), fill=C["gold"])
        d.text((M, 450), f"{hours(t['hours_back_week'])} hours a week back", font=f("head", 46), fill=C["ivory"])
    y = 560
    for e in audit["engines"]:
        d.text((M, y), f"{e['label']}", font=f("body_semi", 34), fill=C["ivory"])
        label = f"{e['in_place']} of {e['total']} in place"
        d.text((W - M - f('body', 30).getlength(label), y + 3), label, font=f("body", 30), fill=C["ivory70"])
        bx, by, bw = M, y + 52, W - 2 * M
        d.rounded_rectangle((bx, by, bx + bw, by + 14), radius=7, fill=C["panel"])
        if e["total"]:
            filled = int(bw * e["in_place"] / e["total"])
            if filled > 0:
                d.rounded_rectangle((bx, by, bx + max(filled, 14), by + 14), radius=7, fill=C["gold"])
        y += 98
    d.text((M, H - 150), NOTE if t["cost_month"] else HOURS_NOTE, font=f("body", 28), fill=C["ivory70"])
    d.text((M, H - 108), "Made with OwnerDesk", font=f("body_semi", 28), fill=C["gold"])
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()
