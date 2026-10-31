"""
Social media (v2.3, Brand engine): posts ready to post — an image drawn from the brand kit's templates (no AI, no image
generation) and a caption.

- Images a calendar month (India time): plans.monthly_images(owner) — 7 on the free plan and for event accounts, 30 for
  paying members. A counter shows what's left; at the limit, making another image is stopped with a plain message (and, for
  free and event accounts, the way to Membership). Downloading an image already made never counts again.
- The first time the page opens, 7 posts are made from what the hub already knows — the brand kit, the Knowledge Hub, their
  offers and prices exactly as written, an upcoming festival — with no AI token, and their images are drawn while images are
  left. (Day 3 of the event: the week's posts are already there.)
- "Write 7 new posts" is one AI token: a week of captions and image text from the Knowledge Hub, their website and brand kit.
  Lines with contact details, a rupee amount the owner didn't write, or the competitor's own words are dropped.
- Editing a post's image text makes its image stale: drawing it again counts as a new image. The caption can change freely.
- "Mark as posted" goes into the one posting log (routers/social.py, ref "sm:<post id>"), which the Dashboard's Social tile reads.

sm_posts {_id, owner_id, month, n, source starter|ai, template social_square|offer|festival, fields {..}, caption, created_at,
          image_file_id, image_at}
sm_usage {_id: "{ws}:{YYYY-MM}", owner_id, month, used}       images drawn this month
sm_state {_id: ws, starter_at}                                the 7 starter posts were made (once)
"""
import re
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

from .. import brand_kit as bk, designs, festivals, files, growth_context as gc, plans
from ..agents import rhythm
from ..ai import v23_sales_tasks as t
from ..competitor_watch import has_contact
from ..context import Ctx, feature
from ..db import db, now
from ..services import new_id, run_ai, track
from . import social
from .brandkit import _kit, _logo
from .hub import profile_of

router = APIRouter(prefix="/api/social-media")
read_ctx = feature("social_media")
write_ctx = feature("social_media", "manager")
TEMPLATES = ("social_square", "offer", "festival")
STARTER = 7


def month_now() -> str:
    return rhythm.today_ist().strftime("%Y-%m")


def next_month_start(t_: date) -> date:
    return (t_.replace(day=1) + timedelta(days=32)).replace(day=1)


async def images_status(owner: dict) -> dict:
    month = month_now()
    used = (await db().sm_usage.find_one({"_id": f"{owner['_id']}:{month}"}) or {}).get("used", 0)
    limit = plans.monthly_images(owner)
    paid = plans.is_paid(owner)
    return {"month": month, "used": used, "limit": limit, "left": max(limit - used, 0), "paid": paid,
            "resets_on": next_month_start(rhythm.today_ist()).isoformat(), "event": bool(plans.event_trial(owner))}


async def _take_image(owner: dict) -> bool:
    """One image from this month's allowance, safely when two requests come at once. False at the limit."""
    month, limit = month_now(), plans.monthly_images(owner)
    key = f"{owner['_id']}:{month}"
    try:
        await db().sm_usage.update_one({"_id": key}, {"$setOnInsert": {"owner_id": owner["_id"], "month": month, "used": 0}}, upsert=True)
    except DuplicateKeyError:
        pass
    return bool(await db().sm_usage.find_one_and_update({"_id": key, "used": {"$lt": limit}}, {"$inc": {"used": 1}}))


def limit_detail(st: dict) -> dict:
    nxt = date.fromisoformat(st["resets_on"]).strftime("%-d %B")
    msg = f"You've used this month's {st['limit']} images. They refill on {nxt}."
    if not st["paid"]:
        msg += " OwnerDesk Membership gives you 30 images a month."
    return {"code": "images_exhausted", "message": msg, "limit": st["limit"], "resets_on": st["resets_on"], "upgrade": not st["paid"]}


# ───────────────────────── drawing ─────────────────────────
async def _draw(ws: str, post: dict) -> bytes:
    kit = await _kit(ws)
    b = await db().businesses.find_one({"owner_id": ws}) or {}
    site = await db().sites.find_one({"owner_id": ws}, {"accent": 1}) or {}
    design = kit.get("design") or bk.default_design(b.get("name", ""), site.get("accent"))
    img = designs.render(post["template"], post["fields"], design, b, await _logo({**kit, "_id": ws}))
    return designs.png(img)


async def make_image(owner: dict, post: dict) -> dict:
    """Draws the post's image, counting one image. Raises 429 at the month's limit."""
    if not await _take_image(owner):
        raise HTTPException(429, limit_detail(await images_status(owner)))
    try:
        data = await _draw(owner["_id"], post)
    except Exception:
        await db().sm_usage.update_one({"_id": f"{owner['_id']}:{month_now()}"}, {"$inc": {"used": -1}})   # not drawn: not counted
        raise
    f = await files.put(owner["_id"], data, f"post-{post['n']}.png", "image/png", "social_image", post_id=post["_id"])
    if post.get("image_file_id"):
        await files.delete(post["image_file_id"], owner["_id"])
    await db().sm_posts.update_one({"_id": post["_id"]}, {"$set": {"image_file_id": f["_id"], "image_at": now()}})
    return {**post, "image_file_id": f["_id"], "image_at": now()}


# ───────────────────────── the 7 starter posts (no AI) ─────────────────────────
def _lines(text: str) -> list[str]:
    return [x.strip(" •-–") for x in re.split(r"[\n;]+", str(text or "")) if x.strip(" •-–")]


def fit(text: str, limit: int) -> str:
    """Whole sentences that fit in `limit` characters (an image has room for a few lines); else whole words, never half."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) <= limit:
        return text
    out = ""
    for sent in re.split(r"(?<=[.!?])\s+", text):
        if len(out) + len(sent) + 1 > limit:
            break
        out = f"{out} {sent}".strip()
    if out:
        return out
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:-–— ")


def _lower_first(x: str) -> str:
    x = x.strip().rstrip(".")
    return x[0].lower() + x[1:] if len(x) > 1 and x[0].isupper() and x[1].islower() else x


def starter_posts(p: dict, msgs: dict, site_content: dict, today: date) -> list[dict]:
    """7 posts from the owner's own words only: the brand message, their offers (prices exactly as written), why customers
    choose them, who they help, their proof, an upcoming festival, and their hooks. Nothing invented."""
    core, web, mk, so = (msgs.get(k) or {} for k in ("core", "website", "marketing", "social"))
    name, city = p.get("name", ""), p.get("city", "")
    cta = ((web.get("ctas") or [""])[0] or site_content.get("cta") or "Message us")[:40]
    brand = p.get("brand") or {}
    who = re.sub(r"\s+", " ", str(p.get("ideal_customer") or "")).strip().rstrip(".")
    out: list[dict] = []

    def add(template, fields, caption):
        if template == "social_square" and len(fields.get("headline") or "") > 45:
            fields["body"] = fit(fields.get("body", ""), 110)      # a 3-line headline leaves room for less text
        clean = {k: str(v or "").strip() for k, v in fields.items()}
        cap = re.sub(r"\s+", " ", caption).strip().lower()
        same = any((x["template"] == template and x["fields"] == clean) or re.sub(r"\s+", " ", x["caption"]).strip().lower() == cap for x in out)
        if any(clean.values()) and not same:      # never the same post twice
            out.append({"template": template, "fields": clean, "caption": caption.strip()})

    one = core.get("one_line") or brand.get("message") or web.get("headline") or site_content.get("headline") or name
    pitch = core.get("pitch") or brand.get("pitch") or web.get("subheadline") or site_content.get("subheadline") or p.get("industry", "")
    short_pitch = fit(pitch, 150)
    add("social_square", {"headline": fit(one, 90), "body": short_pitch, "cta": cta}, f"{one}\n\n{pitch}\n\n{name}{', ' + city if city else ''}")
    for o in [o for o in p.get("offers") or [] if o.get("name")][:3]:
        price = " ".join(x for x in (o.get("price", ""), o.get("unit", "")) if x)
        detail = f"For {_lower_first(who)}" if who and len(who) <= 90 else fit(web.get("subheadline") or "", 120)
        add("offer", {"title": o["name"], "detail": detail, "price": price, "cta": cta},
            f"{o['name']}{' — ' + price if price else ''}\n\nMessage us to know more. {name}{', ' + city if city else ''}")
    why = _lines(p.get("why_us"))
    if why:
        add("social_square", {"headline": f"Why customers choose {name}", "body": fit(" · ".join(why[:3]), 150), "cta": cta},
            f"Why customers choose {name}:\n" + "\n".join(f"• {x}" for x in why[:4]))
    if who:
        add("social_square", {"headline": "Who we work with", "body": fit(who + ".", 150), "cta": cta},
            f"We work with {_lower_first(who)}.\n\n{pitch}")
    proof = _lines(p.get("proof")) or [x for x in core.get("proof") or [] if x]
    if proof:
        add("social_square", {"headline": "What we've done", "body": fit(proof[0], 150), "cta": cta}, "\n".join(proof[:2]))
    fest = next((f for f in festivals.upcoming(today, 3) if date.fromisoformat(f["date"]) <= today + timedelta(days=30)), None)
    if fest:
        line = mk.get("festival_line") or f"Warm wishes from all of us at {name}."
        add("festival", {"greeting": f"Happy {fest['name']}", "message": fit(line, 160)}, f"Happy {fest['name']}!\n\n{line}\n\n— {name}")
    for h in (so.get("hooks") or []) + (mk.get("ad_headlines") or []):
        if len(out) >= STARTER:
            break
        add("social_square", {"headline": fit(h, 90), "body": short_pitch, "cta": cta}, f"{h}\n\n{pitch}")
    for st in (site_content.get("steps") or []):
        if len(out) >= STARTER:
            break
        if isinstance(st, dict) and st.get("title"):
            add("social_square", {"headline": fit(st["title"], 90), "body": fit(st.get("text", ""), 150), "cta": cta}, f"{st['title']}\n\n{st.get('text', '')}")
    for q in (site_content.get("faq") or []):
        if len(out) >= STARTER:
            break
        if isinstance(q, dict) and q.get("q"):
            add("social_square", {"headline": fit(q["q"], 90), "body": fit(q.get("a", ""), 150), "cta": cta}, f"{q['q']}\n\n{q.get('a', '')}")
    # A short profile: who they are, said plainly, a few different ways. Fewer than 7 posts beats the same post repeated.
    industry = p.get("industry", "")
    for headline, body, caption in ((name, fit(industry, 150), f"{name} — {industry}{', ' + city if city else ''}"),
                                    (f"{name} in {city}" if city else "", fit(industry, 150), f"Based in {city}, {name} is here for you.\n\n{industry}"),
                                    (cta, short_pitch, f"{cta}\n\n{pitch}\n\n{name}")):
        if len(out) >= STARTER:
            break
        if headline and body:
            add("social_square", {"headline": fit(headline, 90), "body": body, "cta": cta}, caption)
    return out[:STARTER]


async def _insert(ws: str, posts: list[dict], source: str, actor: str) -> list[dict]:
    month = month_now()
    # after the highest number, not the count: a deleted post would otherwise hand its number out twice
    last = await db().sm_posts.find_one({"owner_id": ws}, {"n": 1}, sort=[("n", -1)])
    start = (last or {}).get("n") or 0
    docs = []
    for i, x in enumerate(posts):
        docs.append({"_id": new_id(), "owner_id": ws, "month": month, "n": start + i + 1, "source": source, "template": x["template"],
                     "fields": x["fields"], "caption": x["caption"][:2200], "created_at": now(), "by": actor, "image_file_id": None, "image_at": None})
    if docs:
        await db().sm_posts.insert_many(docs)
    return docs


async def _draw_while_left(owner: dict, docs: list[dict]) -> int:
    made = 0
    for d in docs:
        try:
            await make_image(owner, d)
            made += 1
        except HTTPException as e:
            if e.status_code == 429:
                break
            raise
    return made


async def ensure_starter(ctx: Ctx) -> None:
    """The first visit: 7 posts from the hub's own data, with their images (while images are left). Once per business."""
    if await db().sm_state.find_one({"_id": ctx.ws}, {"_id": 1}):
        return
    p = await db().businesses.find_one({"owner_id": ctx.ws}) or {}
    if not p.get("name") or not p.get("industry"):
        return     # nothing to write from yet: the Knowledge Hub comes first
    try:
        await db().sm_state.insert_one({"_id": ctx.ws, "starter_at": now()})
    except DuplicateKeyError:
        return     # another tab got here first
    kit = await _kit(ctx.ws)
    site = await db().sites.find_one({"owner_id": ctx.ws}, {"content": 1}) or {}
    docs = await _insert(ctx.ws, starter_posts(p, kit.get("messages") or {}, site.get("content") or {}, rhythm.today_ist()), "starter", ctx.actor)
    await _draw_while_left(ctx.owner, docs)
    await track("social_starter", ctx.ws, posts=len(docs))


# ───────────────────────── views ─────────────────────────
async def view(ctx: Ctx) -> dict:
    posts = [x async for x in db().sm_posts.find({"owner_id": ctx.ws}).sort("n", -1).limit(60)]
    posted = {p["post_ref"]: p async for p in db().social_posts.find({"owner_id": ctx.ws, "post_ref": {"$regex": "^sm:"}})}
    items = []
    for x in posts:
        mark = posted.get(f"sm:{x['_id']}")
        items.append({"id": x["_id"], "n": x["n"], "month": x["month"], "source": x["source"], "template": x["template"], "fields": x["fields"],
                      "caption": x["caption"], "image": bool(x.get("image_file_id")),
                      "image_url": f"/api/social-media/posts/{x['_id']}/image.png?v={int(x['image_at'].timestamp())}" if x.get("image_at") else None,
                      "posted": bool(mark), "platform": (mark or {}).get("platform", ""), "created_at": x["created_at"].isoformat()})
    return {"posts": items, "images": await images_status(ctx.owner), "tokens_to_write": 1, "platforms": social.PLATFORMS,
            "templates": {k: [{"key": f.key, "label": f.label, "max": f.max, "lines": f.lines} for f in designs.TEMPLATES[k].fields] for k in TEMPLATES},
            "has_profile": bool((await db().businesses.find_one({"owner_id": ctx.ws}, {"name": 1, "industry": 1}) or {}).get("industry"))}


@router.get("")
async def get_page(ctx: Ctx = Depends(read_ctx)):
    await ensure_starter(ctx)
    return await view(ctx)


async def _post(ctx: Ctx, post_id: str) -> dict:
    p = await db().sm_posts.find_one({"_id": post_id, "owner_id": ctx.ws})
    if not p:
        raise HTTPException(404, "That post isn't in your hub any more.")
    return p


@router.post("/posts/{post_id}/image")
async def draw(post_id: str, ctx: Ctx = Depends(read_ctx)):
    """Draws (or draws again) the post's image: one image from this month's allowance."""
    p = await _post(ctx, post_id)
    await make_image(ctx.owner, p)
    await track("social_image", ctx.ws)
    return await view(ctx)


@router.get("/posts/{post_id}/image.png")
async def image(post_id: str, ctx: Ctx = Depends(read_ctx)):
    p = await _post(ctx, post_id)
    f = await files.get(p["image_file_id"], ctx.ws) if p.get("image_file_id") else None
    if not f:
        raise HTTPException(404, "Make the image first.")
    return Response(bytes(f["data"]), media_type="image/png",
                    headers={"Content-Disposition": f'inline; filename="post-{p["n"]}.png"', "Cache-Control": "private, max-age=86400"})


class PostIn(BaseModel):
    caption: str = Field(default="", max_length=2200)
    fields: dict[str, str] = Field(default_factory=dict)


@router.put("/posts/{post_id}")
async def edit(post_id: str, body: PostIn, ctx: Ctx = Depends(read_ctx)):
    """The caption changes freely. New image text makes the image stale: drawing it again is a new image."""
    p = await _post(ctx, post_id)
    allowed = {f.key: f.max for f in designs.TEMPLATES[p["template"]].fields}
    fields = {**p["fields"], **{k: str(v)[:allowed[k]] for k, v in body.fields.items() if k in allowed}}
    patch = {"caption": body.caption.strip()[:2200] or p["caption"], "fields": fields}
    if fields != p["fields"] and p.get("image_file_id"):
        await files.delete(p["image_file_id"], ctx.ws)
        patch.update(image_file_id=None, image_at=None)
    await db().sm_posts.update_one({"_id": p["_id"]}, {"$set": patch})
    return await view(ctx)


@router.delete("/posts/{post_id}")
async def remove(post_id: str, ctx: Ctx = Depends(write_ctx)):
    p = await _post(ctx, post_id)
    if p.get("image_file_id"):
        await files.delete(p["image_file_id"], ctx.ws)
    await db().sm_posts.delete_one({"_id": p["_id"]})
    await social.unmark(ctx.ws, f"sm:{p['_id']}")
    return await view(ctx)


def shape_week(raw: dict, ctx: dict, fests: list[dict]) -> list[dict]:
    p = ctx.get("profile") or {}
    allowed, own_phone, comp = gc.owner_amounts(p), p.get("whatsapp", ""), ctx.get("competitor_shingles") or set()
    names = {f["name"].lower(): f["name"] for f in fests}

    def ok(s: str) -> bool:
        return not has_contact(s, own_phone) and not gc.has_foreign_amount(s, allowed) and not gc.copies(s, comp)
    out = []
    for x in (raw.get("posts") or [])[:7] if isinstance(raw, dict) else []:
        if not isinstance(x, dict):
            continue
        head, body, cta = (re.sub(r"\s+", " ", str(x.get(k) or "")).strip() for k in ("headline", "body", "cta"))
        caption = str(x.get("caption") or "").strip()
        if not head or not ok(f"{head} {body} {cta}"):
            continue
        caption = "\n".join(line for line in caption.splitlines() if ok(line)) or f"{head}\n\n{body}"
        fest = names.get(str(x.get("festival") or "").strip().lower())
        if fest:
            out.append({"template": "festival", "fields": {"greeting": f"Happy {fest}", "message": body or head}, "caption": caption})
        else:
            out.append({"template": "social_square", "fields": {"headline": head, "body": body, "cta": cta or "Message us"}, "caption": caption})
    if len(out) < 4:
        raise ValueError("too few usable posts")
    return out


@router.post("/write")
async def write_week(ctx: Ctx = Depends(write_ctx)):
    """7 new posts with AI (1 token). Their images are drawn while this month's images last."""
    await profile_of(ctx.ws)
    g = await gc.gather(ctx.ws)
    today = rhythm.today_ist()
    fests = [f for f in festivals.upcoming(today, 4) if date.fromisoformat(f["date"]) <= today + timedelta(days=8)]
    posts = await run_ai(ctx.owner, "social_week", t.social_week(g, fests), "Social media — a week of posts", check=lambda r: shape_week(r, g, fests))
    docs = await _insert(ctx.ws, posts, "ai", ctx.actor)
    made = await _draw_while_left(ctx.owner, docs)
    await track("social_week", ctx.ws, posts=len(docs), images=made)
    return {**await view(ctx), "made": len(docs), "images_made": made}


class PostedIn(BaseModel):
    platform: str = Field(default="", max_length=30)
    posted: bool = True


@router.post("/posts/{post_id}/posted")
async def posted(post_id: str, body: PostedIn, ctx: Ctx = Depends(read_ctx)):
    p = await _post(ctx, post_id)
    if body.platform and body.platform not in social.PLATFORMS:
        raise HTTPException(400, f"Platform is one of: {', '.join(social.PLATFORMS)}")
    if body.posted:
        await social.mark(ctx.ws, ctx.actor, f"sm:{p['_id']}", body.platform)
    else:
        await social.unmark(ctx.ws, f"sm:{p['_id']}")
    return await view(ctx)


async def ensure_indexes() -> None:
    await db().sm_posts.create_index([("owner_id", 1), ("n", -1)])
