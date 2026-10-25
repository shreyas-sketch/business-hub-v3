"""
Membership's Magic Number and Gaps scan, and the Action Program's tools, each done with the owner's own data:
lead magnet (published on their website), offer ladder (shown on their website), revenue levers and the 10×10×10 pledge,
customer portfolio (Amazing / Breadwinning / Convenience / Dangerous), funnel leak finder and the 4 end goals.
All the maths is done here; the AI only writes words.
"""
import math
import statistics
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import sales
from ..agents import rhythm
from ..ai import program_tasks
from ..context import Ctx, feature
from ..db import db, now, public
from ..services import new_id, run_ai, track
from ..sites import site_url
from .hub import profile_of
from .membership import ist_midnight, ist_today

router = APIRouter(prefix="/api")


def _ist_month_start() -> date:
    return ist_today().replace(day=1)


# ═════════════════════════ Magic Number (Membership) ═════════════════════════
class MagicIn(BaseModel):
    monthly_target: float = Field(gt=0, le=1e11)        # ₹ of sales a month
    avg_sale: float = Field(gt=0, le=1e10)              # ₹ per customer
    close_rate: float = Field(gt=0, le=100)             # % of inquiries that buy
    meeting_rate: float = Field(default=0, ge=0, le=100)


async def magic_progress(ws: str, result: dict) -> dict:
    first = _ist_month_start()
    since = ist_midnight(first)
    t = ist_today()
    nxt = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    elapsed = (t - first).days + 1
    total = (nxt - first).days
    inquiries = await db().leads.count_documents({"owner_id": ws, "created_at": {"$gte": since}})
    won = await db().leads.count_documents({"owner_id": ws, "status": "won", "$or": [{"won_at": {"$gte": since}},
                                                                                     {"won_at": {"$exists": False}, "updated_at": {"$gte": since}}]})
    # sales: the same count as "Am I on target?" — invoices (plus won leads without one), or payments when counted when paid
    m = await db().magic.find_one({"_id": ws}, {"count_when": 1}) or {}
    value = (await sales.month_sales(ws, m.get("count_when") or "invoiced", t))["sales"]
    expected = round(elapsed / total * 100)
    def pct(a, b):
        return round(a / b * 100) if b else 0
    return {"inquiries": inquiries, "customers": won, "sales": value, "expected_pct": expected,
            "inquiries_pct": pct(inquiries, result.get("inquiries_month", 0)), "customers_pct": pct(won, result.get("customers_month", 0)),
            "days_left": (nxt - t).days - 1}


@router.get("/magic")
async def get_magic(ctx: Ctx = Depends(feature("magic_number", "manager"))):
    m = await db().magic.find_one({"_id": ctx.ws})
    if not m or not m.get("inputs"):
        b = await db().businesses.find_one({"owner_id": ctx.ws}, {"targets": 1}) or {}
        return {"inputs": None, "result": None, "progress": None, "brain_target": (b.get("targets") or {}).get("monthly_revenue")}
    return {"inputs": m["inputs"], "result": m["result"], "progress": await magic_progress(ctx.ws, m["result"]),
            "updated_at": m["updated_at"].isoformat(), "target": await sales.target_status(ctx.ws), "count_when": m.get("count_when") or "invoiced"}


@router.put("/magic")
async def save_magic(body: MagicIn, ctx: Ctx = Depends(feature("magic_number", "manager"))):
    inputs = body.model_dump()
    result = rhythm.magic_result(inputs)
    await db().magic.replace_one({"_id": ctx.ws}, {"_id": ctx.ws, "inputs": inputs, "result": result, "updated_at": now(), "by": ctx.actor}, upsert=True)
    await db().businesses.update_one({"owner_id": ctx.ws}, {"$set": {"targets.monthly_revenue": inputs["monthly_target"]}})  # the Knowledge Hub target
    await track("magic_number", ctx.ws)
    return {"inputs": inputs, "result": result, "progress": await magic_progress(ctx.ws, result), "updated_at": now().isoformat(),
            "target": await sales.target_status(ctx.ws)}


# ═════════════════════════ Gaps scan (Membership) ═════════════════════════
# (key, area, statement, fix, the hub feature that helps)
GAPS = [
    ("focus1", "Strategic focus", "We know which customers and offers to focus on — and we say no to the rest.",
     "List your customer segments and sort them by return and effort; aim most of your effort at the best one.", "customer_portfolio"),
    ("focus2", "Strategic focus", "Every month we pick our top 3 priorities and stick to them.",
     "Every Monday, write the 3 things that matter most this week, and check them on Friday.", "week"),
    ("margin1", "Margins", "We know the profit on each product or service we sell.",
     "Work out the margin on your top 5 products. Re-price or drop the ones that don't pay.", "revenue_levers"),
    ("margin2", "Margins", "We review our prices at least once a year.",
     "Compare your prices with three competitors and decide one price change this month.", "revenue_levers"),
    ("sales1", "Sales consistency", "Inquiries come in every week, from more than one channel.",
     "Add a second channel: publish a lead magnet, or ask every happy customer for one referral.", "lead_magnet"),
    ("sales2", "Sales consistency", "Every lead is followed up until we get a clear yes or no.",
     "Follow up on day 1, 3 and 7. Put every lead on the pipeline so none is forgotten.", "follow_up_agent"),
    ("pay1", "Payments", "Customers pay on time, without us chasing them.",
     "Send a polite reminder on the due date, then every 3 days — the hub can prepare them.", "money_owed"),
    ("pay2", "Payments", "We take an advance and put a due date on every bill.",
     "Make an advance part of every quotation, and put the due date on every bill.", "quotations"),
    ("customer", "Customer strategy", "We know who our best customers are and what they value.",
     "Describe your best customers from real examples: their needs, desires and problems.", "tool_persona"),
    ("offer", "Offer architecture", "We have an easy first offer, a main offer and a premium offer.",
     "Build an offer ladder and show it on your website.", "offer_ladder"),
    ("reach", "Reach", "People who need us can find us online — website, Google and social media.",
     "Post three times a week, keep your Google profile fresh and share your website link in every reply.", "ai_writer"),
    ("systems", "Systems", "The business runs for a week without me in every decision.",
     "Write your three most common jobs as SOPs and hand one to someone this week.", "sop_library"),
]
AREAS = ["Strategic focus", "Margins", "Sales consistency", "Payments", "Customer strategy", "Offer architecture", "Reach", "Systems"]


class GapsIn(BaseModel):
    answers: dict[str, int]


@router.get("/gaps")
async def get_gaps(ctx: Ctx = Depends(feature("gaps_scan", "owner"))):
    history = [public(g) async for g in db().gap_scans.find({"ws": ctx.ws}).sort("at", -1).limit(12)]
    return {"questions": [{"key": k, "area": a, "q": q} for k, a, q, *_ in GAPS], "latest": history[0] if history else None,
            "history": [{"at": h["at"], "score": h["score"]} for h in history]}


@router.post("/gaps")
async def take_gaps(body: GapsIn, ctx: Ctx = Depends(feature("gaps_scan", "owner"))):
    if any(not isinstance(body.answers.get(k), int) or not 1 <= body.answers[k] <= 5 for k, *_ in GAPS):
        raise HTTPException(400, "Rate every line from 1 (never) to 5 (always).")
    ans = {k: body.answers[k] for k, *_ in GAPS}
    areas = {}
    for a in AREAS:
        vals = [ans[k] for k, area, *_ in GAPS if area == a]
        areas[a] = round((sum(vals) / len(vals) - 1) / 4 * 100)
    score = round((sum(ans.values()) / len(ans) - 1) / 4 * 100)
    ranked = sorted(GAPS, key=lambda g: ans[g[0]])
    fixes = [{"area": a, "fix": fix, "feature": feat, "rating": ans[k]} for k, a, _q, fix, feat in ranked if ans[k] < 4][:3]
    prev = await db().gap_scans.find_one({"ws": ctx.ws}, sort=[("at", -1)])
    doc = {"_id": new_id(), "ws": ctx.ws, "at": now(), "answers": ans, "areas": areas, "score": score,
           "fixes": fixes, "change": score - prev["score"] if prev else None,
           "silent_killers": {a: areas[a] for a in ("Strategic focus", "Margins", "Sales consistency", "Payments")}}
    await db().gap_scans.insert_one(doc)
    await track("gaps_scan", ctx.ws, score=score)
    return public(doc)


# ═════════════════════════ Lead magnet (Action Program) ═════════════════════════
class MagnetIn(BaseModel):
    problem: str = Field(default="", max_length=300)
    format: str = Field(default="checklist", pattern="^(checklist|guide|price guide|mistakes list)$")


class MagnetContent(BaseModel):
    content: dict


class PublishIn(BaseModel):
    published: bool


def _magnet_shape(raw: dict) -> dict:
    if not isinstance(raw, dict) or not str(raw.get("title") or "").strip():
        raise ValueError("no title")
    secs = []
    for s in (raw.get("sections") or [])[:10]:
        if isinstance(s, dict) and str(s.get("heading") or "").strip():
            secs.append({"heading": str(s["heading"])[:140], "text": str(s.get("text") or "")[:800],
                         "points": [str(p)[:300] for p in (s.get("points") or []) if p][:8]})
    if len(secs) < 2:
        raise ValueError("too short")
    return {"title": str(raw["title"])[:140], "subtitle": str(raw.get("subtitle") or "")[:200], "promise": str(raw.get("promise") or "")[:200],
            "intro": str(raw.get("intro") or "")[:800], "sections": secs, "cta": str(raw.get("cta") or "")[:400]}


async def _site_for(ws: str) -> dict | None:
    return await db().sites.find_one({"owner_id": ws}, {"slug": 1, "status": 1})


async def _magnet_view(ws: str, g: dict | None) -> dict:
    site = await _site_for(ws)
    stats = {"views": 0, "leads": 0}
    async for v in db().site_views.find({"ws": ws}, {"guide_views": 1, "guide_leads": 1}):
        stats["views"] += int(v.get("guide_views") or 0)
        stats["leads"] += int(v.get("guide_leads") or 0)
    return {"guide": public(g) if g else None, "url": site_url(site["slug"], "/guide") if site else None,
            "site_live": bool(site and site.get("status") == "live"), "stats": stats,
            "preview_url": f"/s/{site['slug']}/guide?preview=1" if site and g else None}


@router.get("/lead-magnet")
async def get_magnet(ctx: Ctx = Depends(feature("lead_magnet", "owner"))):
    return await _magnet_view(ctx.ws, await db().lead_magnets.find_one({"_id": ctx.ws}))


@router.post("/lead-magnet/generate")
async def make_magnet(body: MagnetIn, ctx: Ctx = Depends(feature("lead_magnet", "owner"))):
    p = await profile_of(ctx.ws)
    content = await run_ai(ctx.owner, "lead_magnet", program_tasks.lead_magnet(p, body.model_dump()), "Lead magnet guide", check=_magnet_shape)
    await db().lead_magnets.update_one({"_id": ctx.ws}, {"$set": {"content": content, "inputs": body.model_dump(), "updated_at": now()},
                                                         "$setOnInsert": {"published": False, "created_at": now()}}, upsert=True)
    return await _magnet_view(ctx.ws, await db().lead_magnets.find_one({"_id": ctx.ws}))


@router.put("/lead-magnet")
async def edit_magnet(body: MagnetContent, ctx: Ctx = Depends(feature("lead_magnet", "owner"))):
    try:
        content = _magnet_shape(body.content)
    except ValueError:
        raise HTTPException(400, "The guide needs a title and at least two sections with headings.")
    r = await db().lead_magnets.update_one({"_id": ctx.ws}, {"$set": {"content": content, "updated_at": now()}})
    if not r.matched_count:
        raise HTTPException(404, "Write the guide first.")
    return await _magnet_view(ctx.ws, await db().lead_magnets.find_one({"_id": ctx.ws}))


@router.post("/lead-magnet/publish")
async def publish_magnet(body: PublishIn, ctx: Ctx = Depends(feature("lead_magnet", "owner"))):
    g = await db().lead_magnets.find_one({"_id": ctx.ws})
    if not g:
        raise HTTPException(404, "Write the guide first.")
    site = await _site_for(ctx.ws)
    if body.published and not (site and site.get("status") == "live"):
        raise HTTPException(400, "Publish your website first — the guide lives on it.")
    await db().lead_magnets.update_one({"_id": ctx.ws}, {"$set": {"published": body.published, "published_at": now() if body.published else None}})
    if body.published:
        await track("lead_magnet_live", ctx.ws)
    return await _magnet_view(ctx.ws, await db().lead_magnets.find_one({"_id": ctx.ws}))


# ═════════════════════════ Offer ladder (Action Program) ═════════════════════════
class LadderIn(BaseModel):
    notes: str = Field(default="", max_length=1000)
    entry: str = Field(default="", max_length=120)


class LadderContent(BaseModel):
    content: dict


class ShowIn(BaseModel):
    show: bool


def _ladder_shape(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("not an object")
    levels = []
    for i, l in enumerate((raw.get("levels") or [])[:3]):
        if isinstance(l, dict) and str(l.get("name") or "").strip():
            levels.append({"level": str(l.get("level") or ["Easy first step", "Main offer", "Premium"][i])[:40], "name": str(l["name"])[:80],
                           "for_whom": str(l.get("for_whom") or "")[:200], "includes": [str(x)[:120] for x in (l.get("includes") or []) if x][:5],
                           "price": str(l.get("price") or "")[:60], "why": str(l.get("why") or "")[:200]})
    if len(levels) != 3:
        raise ValueError("needs 3 levels")
    return {"heading": str(raw.get("heading") or "Ways to work with us")[:80], "intro": str(raw.get("intro") or "")[:240], "levels": levels}


async def _ladder_view(ws: str) -> dict:
    d = await db().offer_ladders.find_one({"_id": ws})
    site = await _site_for(ws)
    return {"ladder": public(d) if d else None, "site_url": site_url(site["slug"]) + "#ways" if site else None}


@router.get("/offers")
async def get_ladder(ctx: Ctx = Depends(feature("offer_ladder", "owner"))):
    return await _ladder_view(ctx.ws)


@router.post("/offers/generate")
async def make_ladder(body: LadderIn, ctx: Ctx = Depends(feature("offer_ladder", "owner"))):
    p = await profile_of(ctx.ws)
    content = await run_ai(ctx.owner, "offer_ladder", program_tasks.offer_ladder(p, body.model_dump()), "Offer ladder", check=_ladder_shape)
    await db().offer_ladders.update_one({"_id": ctx.ws}, {"$set": {"content": content, "updated_at": now()},
                                                          "$setOnInsert": {"show": False, "created_at": now()}}, upsert=True)
    return await _ladder_view(ctx.ws)


@router.put("/offers")
async def edit_ladder(body: LadderContent, ctx: Ctx = Depends(feature("offer_ladder", "owner"))):
    try:
        content = _ladder_shape(body.content)
    except ValueError:
        raise HTTPException(400, "Keep three levels, each with a name.")
    r = await db().offer_ladders.update_one({"_id": ctx.ws}, {"$set": {"content": content, "updated_at": now()}})
    if not r.matched_count:
        raise HTTPException(404, "Build the ladder first.")
    return await _ladder_view(ctx.ws)


@router.post("/offers/show")
async def show_ladder(body: ShowIn, ctx: Ctx = Depends(feature("offer_ladder", "owner"))):
    r = await db().offer_ladders.update_one({"_id": ctx.ws}, {"$set": {"show": body.show}})
    if not r.matched_count:
        raise HTTPException(404, "Build the ladder first.")
    return await _ladder_view(ctx.ws)


# ═════════════════════════ Revenue levers + the 10×10×10 pledge (Action Program) ═════════════════════════
STAGES5 = [("leads", "Leads — getting found"), ("conversion", "Sales — turning inquiries into customers"),
           ("value", "Value — a bigger average sale"), ("retention", "Retention — customers buying again"),
           ("referral", "Referral and reactivation — customers bringing customers")]


class LeversIn(BaseModel):
    customers: float = Field(ge=0, le=1e9)            # customers a year
    avg_value: float = Field(ge=0, le=1e10)           # ₹ per purchase
    frequency: float = Field(ge=0, le=1000)           # purchases per customer a year
    grow_customers: float = Field(default=10, ge=0, le=500)
    grow_value: float = Field(default=10, ge=0, le=500)
    grow_frequency: float = Field(default=10, ge=0, le=500)
    funnel: dict[str, str] = Field(default_factory=dict)   # stage → what we'll do (and which AI tool)
    pledge: str = Field(default="", max_length=600)


def levers_result(i: dict) -> dict:
    now_rev = i["customers"] * i["avg_value"] * i["frequency"]
    new_rev = (i["customers"] * (1 + i["grow_customers"] / 100) * i["avg_value"] * (1 + i["grow_value"] / 100)
               * i["frequency"] * (1 + i["grow_frequency"] / 100))
    mult = (1 + i["grow_customers"] / 100) * (1 + i["grow_value"] / 100) * (1 + i["grow_frequency"] / 100)
    # the compounding, lever by lever, in the owner's own rupees: 10% on each → ×1.331, so +33.1%
    steps, running = [], now_rev
    for key, label in (("grow_customers", "customers"), ("grow_value", "average sale"), ("grow_frequency", "times they buy")):
        after = running * (1 + i[key] / 100)
        steps.append({"lever": label, "pct": i[key], "before": round(running), "after": round(after), "adds": round(after - running)})
        running = after
    return {"revenue": round(now_rev), "new_revenue": round(new_rev), "growth_pct": round((new_rev / now_rev - 1) * 100, 1) if now_rev else None,
            "extra": round(new_rev - now_rev), "multiplier": round(mult, 4), "steps": steps,
            "simple_sum_pct": round(i["grow_customers"] + i["grow_value"] + i["grow_frequency"], 1)}


@router.get("/levers")
async def get_levers(ctx: Ctx = Depends(feature("revenue_levers", "manager"))):
    d = await db().levers.find_one({"_id": ctx.ws})
    if d:
        d["result"] = levers_result({**d, **{k: d.get(k, 10) for k in ("grow_customers", "grow_value", "grow_frequency")}})
    return {"levers": public(d) if d else None, "stages": [{"key": k, "label": l} for k, l in STAGES5]}


@router.put("/levers")
async def save_levers(body: LeversIn, ctx: Ctx = Depends(feature("revenue_levers", "manager"))):
    inputs = body.model_dump()
    inputs["funnel"] = {k: str(v)[:400] for k, v in inputs["funnel"].items() if k in dict(STAGES5)}
    prev = await db().levers.find_one({"_id": ctx.ws}) or {}
    pledged_at = prev.get("pledged_at")
    if inputs["pledge"].strip() and inputs["pledge"].strip() != prev.get("pledge", ""):
        pledged_at = now()
    await db().levers.replace_one({"_id": ctx.ws}, {"_id": ctx.ws, **inputs, "result": levers_result(inputs), "pledged_at": pledged_at,
                                                    "updated_at": now(), "by": ctx.actor}, upsert=True)
    return {"levers": public(await db().levers.find_one({"_id": ctx.ws})), "stages": [{"key": k, "label": l} for k, l in STAGES5]}


# ═════════════════════════ Customer portfolio (Action Program) ═════════════════════════
LETTERS = {"A": "Amazing", "B": "Breadwinning", "C": "Convenience", "D": "Dangerous"}
RULES = {"A": "Focus here: low effort, high return. Aim 70–80% of your goal at them.",
         "B": "Pursue them too: high return, more effort. Aim 20–30% of your goal at them.",
         "C": "If they come, they come — never say no, but build nothing just for them.",
         "D": "Stay away, even when they come to you: high effort, low return."}


class Segment(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    kind: str = Field(default="B2C", pattern="^(B2B|B2C|B2Ch)$")
    ticket: float = Field(ge=0, le=1e10)               # ₹ a year per customer
    margin: float = Field(ge=0, le=100)                # gross profit %
    repeat: str = Field(default="maybe", pattern="^(yes|no|maybe)$")
    collect_days: int = Field(default=30, ge=0, le=720)
    effort: int = Field(ge=1, le=10)
    conversion: float = Field(default=20, gt=0, le=100)
    letter: str = Field(default="", pattern="^(|A|B|C|D)$")      # the owner may overrule the hub


class PortfolioIn(BaseModel):
    goal: float = Field(default=0, ge=0, le=1e12)      # aspiration goal, ₹ a year
    amazing_share: int = Field(default=75, ge=50, le=90)
    segments: list[Segment] = Field(default_factory=list, max_length=20)


def portfolio_result(goal: float, share: int, segments: list[dict]) -> list[dict]:
    def ret(s):
        r = s["ticket"] * s["margin"] / 100 * {"yes": 1.5, "maybe": 1.2, "no": 1.0}[s["repeat"]]
        return r * (0.8 if s["collect_days"] > 60 else 1.0)
    returns = [ret(s) for s in segments]
    mid = statistics.median(returns) if returns else 0
    out = []
    for s, r in zip(segments, returns):
        high_return = r >= mid and r > 0
        high_effort = s["effort"] >= 6
        letter = s.get("letter") or ("A" if high_return and not high_effort else "B" if high_return else "C" if not high_effort else "D")
        out.append({**s, "return_per_customer": round(r), "letter": letter, "type": LETTERS[letter], "rule": RULES[letter], "auto": not s.get("letter")})
    for letter, pct in (("A", share), ("B", 100 - share)):
        group = [x for x in out if x["letter"] == letter]
        for x in group:
            target = goal * pct / 100 / len(group) if goal and group else 0
            x["target"] = round(target)
            x["customers_needed"] = math.ceil(target / x["ticket"]) if target and x["ticket"] else None
            x["leads_needed"] = math.ceil(x["customers_needed"] / (x["conversion"] / 100)) if x.get("customers_needed") else None
    return out


@router.get("/portfolio")
async def get_portfolio(ctx: Ctx = Depends(feature("customer_portfolio", "manager"))):
    d = await db().portfolios.find_one({"_id": ctx.ws})
    return {"portfolio": public(d) if d else None, "letters": LETTERS, "rules": RULES}


@router.put("/portfolio")
async def save_portfolio(body: PortfolioIn, ctx: Ctx = Depends(feature("customer_portfolio", "manager"))):
    segs = [s.model_dump() for s in body.segments]
    result = portfolio_result(body.goal, body.amazing_share, segs)
    await db().portfolios.replace_one({"_id": ctx.ws}, {"_id": ctx.ws, "goal": body.goal, "amazing_share": body.amazing_share,
                                                        "segments": result, "updated_at": now(), "by": ctx.actor}, upsert=True)
    return {"portfolio": public(await db().portfolios.find_one({"_id": ctx.ws})), "letters": LETTERS, "rules": RULES}


# ═════════════════════════ Funnel leak finder (Action Program) ═════════════════════════
FUNNEL = [("visitors", "Website visitors"), ("inquiries", "Inquiries"), ("meetings", "Meetings or site visits"),
          ("proposals", "Proposals or quotations"), ("won", "Customers won")]
DEFAULT_TARGETS = {"inquiries": 3, "meetings": 50, "proposals": 70, "won": 30}


class LeaksIn(BaseModel):
    numbers: dict[str, float]
    targets: dict[str, float] = Field(default_factory=dict)
    avg_sale: float = Field(default=0, ge=0, le=1e10)


def leaks_result(numbers: dict, targets: dict, avg_sale: float) -> dict:
    steps, worst = [], None
    # Most owners don't know their website visitors: with none given, the funnel starts at inquiries (that first step is
    # left out, not counted as a 0% leak)
    from_inquiries = float(numbers.get("visitors") or 0) <= 0 and float(numbers.get("inquiries") or 0) > 0
    for i, (k, label) in enumerate(FUNNEL):
        n = float(numbers.get(k) or 0)
        step = {"key": k, "label": label, "n": n}
        if i == 1 and from_inquiries:
            steps.append({**step, "skipped": True})
            continue
        if i:
            prev = float(numbers.get(FUNNEL[i - 1][0]) or 0)
            rate = round(n / prev * 100, 1) if prev else 0
            target = float(targets.get(k) or DEFAULT_TARGETS[k])
            gap = (target - rate) / target if target else 0
            step.update(rate=rate, target=target, gap=round(gap * 100))
            if not prev:
                step["no_data"] = True        # nothing came into this step, so there's nothing to judge it on (all zeros: no leak)
            elif worst is None or gap > worst["gap_raw"]:
                worst = {**step, "gap_raw": gap}
        steps.append(step)
    lost = None
    if worst and worst["gap_raw"] > 0:
        i = [k for k, _ in FUNNEL].index(worst["key"])
        prev = float(numbers.get(FUNNEL[i - 1][0]) or 0)
        extra_here = prev * (worst["target"] - worst["rate"]) / 100
        downstream = 1.0
        for k, _ in FUNNEL[i + 1:]:
            t = steps[[s["key"] for s in steps].index(k)].get("rate", 0) / 100
            downstream *= t
        lost = {"extra_customers": round(extra_here * downstream, 1), "extra_sales": round(extra_here * downstream * avg_sale)}
    if worst:
        worst.pop("gap_raw", None)
    return {"steps": steps, "biggest_leak": worst if worst and worst.get("gap", 0) > 0 else None, "if_fixed": lost,
            "starts_at": "inquiries" if from_inquiries else "visitors"}


async def funnel_from_hub(ws: str) -> dict:
    first = ist_today() - timedelta(days=29)
    since = ist_midnight(first)
    views = 0
    async for v in db().site_views.find({"ws": ws, "day": {"$gte": first.isoformat()}}, {"n": 1}):
        views += int(v.get("n") or 0)
    leads = await db().leads.count_documents({"owner_id": ws, "created_at": {"$gte": since}})
    meetings = await db().leads.count_documents({"owner_id": ws, "created_at": {"$gte": since},
                                                 "$or": [{"stage": {"$in": ["pain", "vision", "close"]}}, {"status": "won"}]})
    proposals = await db().leads.count_documents({"owner_id": ws, "created_at": {"$gte": since},
                                                  "$or": [{"stage": {"$in": ["vision", "close"]}}, {"status": "won"}]})
    won = await db().leads.count_documents({"owner_id": ws, "created_at": {"$gte": since}, "status": "won"})
    return {"visitors": views, "inquiries": leads, "meetings": meetings, "proposals": proposals, "won": won}


@router.get("/leaks")
async def get_leaks(ctx: Ctx = Depends(feature("funnel_leaks", "manager"))):
    d = await db().leaks.find_one({"_id": ctx.ws})
    if d and "starts_at" not in (d.get("result") or {}):      # saved before v2.2: worked out again with today's rules
        d["result"] = leaks_result(d.get("numbers") or {}, d.get("targets") or {}, float(d.get("avg_sale") or 0))
    return {"saved": public(d) if d else None, "from_hub": await funnel_from_hub(ctx.ws), "stages": [{"key": k, "label": l} for k, l in FUNNEL],
            "default_targets": DEFAULT_TARGETS}


@router.put("/leaks")
async def save_leaks(body: LeaksIn, ctx: Ctx = Depends(feature("funnel_leaks", "manager"))):
    nums = {k: max(0.0, min(float(body.numbers.get(k) or 0), 1e9)) for k, _ in FUNNEL}
    targets = {k: max(0.1, min(float(body.targets.get(k) or DEFAULT_TARGETS[k]), 100)) for k in DEFAULT_TARGETS}
    result = leaks_result(nums, targets, body.avg_sale)
    await db().leaks.replace_one({"_id": ctx.ws}, {"_id": ctx.ws, "numbers": nums, "targets": targets, "avg_sale": body.avg_sale,
                                                   "result": result, "updated_at": now()}, upsert=True)
    return await get_leaks(ctx)


@router.post("/leaks/fixes")
async def leak_fixes(ctx: Ctx = Depends(feature("funnel_leaks", "manager"))):
    d = await db().leaks.find_one({"_id": ctx.ws})
    if not d:
        raise HTTPException(400, "Save your funnel numbers first.")
    p = await profile_of(ctx.ws)

    def shape(r):
        fixes = [{"stage": str(f.get("stage", ""))[:60], "action": str(f.get("action", ""))[:300], "why": str(f.get("why", ""))[:200]}
                 for f in (r.get("fixes") or []) if isinstance(f, dict) and f.get("action")][:6]
        if not fixes:
            raise ValueError("no fixes")
        return {"summary": str(r.get("summary", ""))[:400], "fixes": fixes}
    fixes = await run_ai(ctx.owner, "leaks", program_tasks.leak_fixes(p, d["result"]), "Funnel leak fixes", check=shape)
    await db().leaks.update_one({"_id": ctx.ws}, {"$set": {"fixes": fixes, "fixes_at": now()}})
    return await get_leaks(ctx)


# ═════════════════════════ The 4 end goals (Action Program) ═════════════════════════
class Goal4(BaseModel):
    text: str = Field(default="", max_length=800)
    by: str = Field(default="", max_length=20)


class EndGoalsIn(BaseModel):
    identity: Goal4 = Field(default_factory=Goal4)
    income: Goal4 = Field(default_factory=Goal4)
    freedom: Goal4 = Field(default_factory=Goal4)
    legacy: Goal4 = Field(default_factory=Goal4)


END_GOALS = [("identity", "Identity", "Who do you want to be known as — in your industry, your city, your family?"),
             ("income", "Income", "What income do you want the business to give you every month?"),
             ("freedom", "Financial freedom", "What would let you stop worrying about money — the number, and by when?"),
             ("legacy", "Legacy", "What should the business be, and do, long after you step back?")]


@router.get("/end-goals")
async def get_end_goals(ctx: Ctx = Depends(feature("end_goals", "owner"))):
    d = await db().end_goals.find_one({"_id": ctx.ws})
    return {"goals": public(d) if d else None, "prompts": [{"key": k, "label": l, "q": q} for k, l, q in END_GOALS]}


@router.put("/end-goals")
async def save_end_goals(body: EndGoalsIn, ctx: Ctx = Depends(feature("end_goals", "owner"))):
    await db().end_goals.replace_one({"_id": ctx.ws}, {"_id": ctx.ws, **body.model_dump(), "updated_at": now()}, upsert=True)
    return await get_end_goals(ctx)


async def ensure_indexes() -> None:
    await db().gap_scans.create_index([("ws", 1), ("at", -1)])
