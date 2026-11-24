"""
Lead research (v2.4, feature "lead_brief", AI role "researcher"): a short brief on every new inquiry.

Sources, in this order, and nothing else (no search engine, no social profiles):
  1. the inquiry itself — name, business name, message;
  2. the lead's own business website, read live with fetch.read_site — the address they gave (a `website` field, or "our website
     is …" in their message), or the domain of a company email address. Free mail domains (Gmail, Yahoo, Outlook, Rediffmail …)
     are never treated as a website.
A private person is never researched: with no business name and no company website the brief uses only what they wrote and
says "Nothing public was looked up."

Automatic briefs (from agents/hooks.on_new_lead, in the background so a customer's form never waits) are written only when the
feature is in the plan, the Researcher is switched on, this month's `briefs` limit allows, and the owner has at least
TOKEN_FLOOR AI tokens left. "Research this lead" (the owner's own press) needs only a token: it works when the automatic briefs
are off or used up. 1 AI token a brief; when nothing useful is found the brief has the questions only and the token is
refunded (status "empty").

leads.brief {status done|empty|failed, who, asking, questions [3], first_line, sources [{label, url}], looked_up, at,
             by "auto"|<user id>, written_by ai|rules, tokens 0|1}
"""
import asyncio
import logging
import re
from datetime import timedelta
from urllib.parse import urlparse

from fastapi import HTTPException

from . import crm, fetch, growth_core, plans
from .agents import catalog
from .ai import brief_tasks
from .config import settings
from .db import db, now
from .services import run_ai, runs_status

log = logging.getLogger("lead_research")

ROLE = "researcher"
TOKEN_FLOOR = 20              # automatic briefs stop when fewer AI tokens than this are left
SITE_SECONDS = 25             # reading their website, all pages together
SAME_PERSON_DAYS = 7          # one automatic brief per person (phone or email) a week
BUSY_SECONDS = 90
_tasks: set = set()

FREE_MAIL = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.in", "yahoo.in", "ymail.com", "rocketmail.com", "outlook.com", "outlook.in",
    "hotmail.com", "hotmail.co.in", "live.com", "live.in", "msn.com", "rediffmail.com", "rediff.com", "icloud.com", "me.com", "mac.com",
    "proton.me", "protonmail.com", "pm.me", "aol.com", "aim.com", "zoho.com", "zohomail.com", "zohomail.in", "mail.com", "email.com",
    "gmx.com", "gmx.net", "yandex.com", "yandex.ru", "tutanota.com", "fastmail.com", "hey.com", "inbox.com", "sify.com", "vsnl.net",
    "vsnl.com", "bsnl.in", "airtelmail.in", "indiatimes.com", "in.com", "qq.com", "163.com", "126.com", "naver.com", "mail.ru",
}
FREE_PREFIX = ("yahoo.", "hotmail.", "live.", "outlook.", "gmail.", "googlemail.", "rediffmail.", "aol.", "gmx.", "yandex.")
# a link in a message to one of these is somebody else's page (a marketplace, a social profile, a map, a file), never their website
NOT_A_WEBSITE = ("facebook.com", "fb.com", "fb.me", "instagram.com", "linkedin.com", "twitter.com", "x.com", "youtube.com", "youtu.be",
                 "wa.me", "whatsapp.com", "google.com", "goo.gl", "g.co", "g.page", "bit.ly", "tinyurl.com", "t.me", "telegram.me",
                 "amazon.in", "amazon.com", "flipkart.com", "indiamart.com", "justdial.com", "meesho.com", "myntra.com", "houzz.in",
                 "houzz.com", "pinterest.com", "pin.it", "dropbox.com", "wetransfer.com", "sharepoint.com", "onedrive.live.com",
                 "threads.net", "snapchat.com", "quora.com", "reddit.com", "wikipedia.org", "zomato.com", "swiggy.com", "olx.in")
RESERVED = (".example", ".test", ".invalid", ".localhost", ".local", ".internal", ".lan", ".home.arpa")
SITE_IN_MESSAGE = re.compile(
    r"(?:\b(?:our|my)\s+(?:company(?:'s)?\s+|business\s+|shop(?:'s)?\s+)?(?:web\s?site|site|web\s?page|page)\s*(?:is|:|-|at|=)?\s*"
    r"|\bwebsite\s*[:=-]\s*|\bvisit\s+us\s+(?:at|on)\s+)"
    r"((?:https?://)?(?:www\.)?[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)+(?:/[^\s,;)]*)?)", re.I)


# ───────────────────────── where to look ─────────────────────────
def _host(url_or_domain: str) -> str:
    s = str(url_or_domain or "").strip().lower()
    if "://" not in s:
        s = "https://" + s
    try:
        return (urlparse(s).hostname or "").rstrip(".").removeprefix("www.")
    except ValueError:
        return ""


def _is(host: str, domains) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains if d)


def free_mail(domain: str) -> bool:
    d = (domain or "").lower().strip()
    return d in FREE_MAIL or d.startswith(FREE_PREFIX)


def email_domain(email: str) -> str:
    e = str(email or "").strip().lower()
    return e.rsplit("@", 1)[1].strip(".") if "@" in e else ""


def _public_host(host: str, owner_domains: set) -> bool:
    return bool(host) and "." in host and not host.endswith(RESERVED) and not _is(host, owner_domains) and not _is(host, NOT_A_WEBSITE)


def website_of(lead: dict, owner_domains: set) -> tuple[str | None, str]:
    """The lead's own business website and where it came from ("gave" | "email"), or (None, why not)."""
    given = str(lead.get("website") or "").strip()
    if given:
        host = _host(given)
        if _public_host(host, owner_domains) and not free_mail(host):
            return (given if "://" in given else f"https://{given}"), "gave"
    m = SITE_IN_MESSAGE.search(str(lead.get("message") or ""))
    if m:
        url = m.group(1).rstrip(".")
        host = _host(url)
        if _public_host(host, owner_domains) and not free_mail(host):
            return (url if "://" in url else f"https://{url}"), "gave"
    dom = email_domain(lead.get("email", ""))
    if dom and not free_mail(dom) and _public_host(dom, owner_domains):
        return f"https://{dom}", "email"
    return None, "free_mail" if dom and free_mail(dom) else "none"


async def owner_domains(ws: str, business: dict) -> set:
    """The owner's own domains (his email, his websites) and the hub's: a lead from there is not someone to research."""
    out = {email_domain(business.get("email", "")), settings.email_from_domain.lower(), (settings.inbound_email_domain or "").lower(),
           _host(settings.app_url)}
    owner = await db().users.find_one({"_id": ws}, {"email": 1}) or {}
    out.add(email_domain(owner.get("email", "")))
    async for s in db().sites.find({"owner_id": ws}, {"slug": 1, "domain": 1}).limit(5):
        dom = s.get("domain")
        if isinstance(dom, dict) and dom.get("name"):
            out.add(_host(dom["name"]))
        if s.get("slug") and settings.sites_domain:
            out.add(f"{s['slug']}.{settings.sites_domain}")
    if business.get("website"):
        out.add(_host(business["website"]))
    return {d for d in out if d and not free_mail(d)}


async def read_their_site(url: str) -> tuple[dict | None, str]:
    """Their website, read live (public addresses only, size and time limits — see fetch.py). (facts, "") or (None, why)."""
    try:
        f = await asyncio.wait_for(fetch.read_site(url), SITE_SECONDS)
    except fetch.FetchError as e:
        return None, str(e)
    except (asyncio.TimeoutError, TimeoutError):
        return None, "Their website took too long to open."
    except Exception as e:  # noqa: BLE001
        log.info("reading %s for a lead brief failed: %s", url, str(e)[:200])
        return None, "Their website didn't open."
    text = (f.text or "").strip()
    if not (f.title or f.description or text):
        return None, "Their website had nothing to read."
    return {"url": f.url, "title": f.title[:200], "description": f.description[:400], "headings": f.headings[:8], "text": text[:6000]}, ""


# ───────────────────────── the brief ─────────────────────────
NUM = re.compile(r"\d[\d,.]{2,}")


def _grounded(who: str, site: dict | None, lead: dict) -> bool:
    """Every number in `who` must be in what they wrote or on their website (the AI must not add years, sizes or prices)."""
    seen = " ".join([str(lead.get("message") or ""), str(lead.get("company") or ""), *(str((site or {}).get(k) or "") for k in ("title", "description", "text")),
                     " ".join((site or {}).get("headings") or [])])
    flat = re.sub(r"[,\s]", "", seen)
    return all(re.sub(r"[,\s]", "", n).rstrip(".") in flat for n in NUM.findall(who or ""))


def _shape(r: dict) -> dict:
    qs = [brief_tasks._clean(q, 160) for q in (r.get("questions") or []) if isinstance(q, str) and q.strip()][:3]
    return {"who": str(r.get("who") or "").strip()[:500], "asking": str(r.get("asking") or "").strip()[:400], "questions": qs,
            "first_line": str(r.get("first_line") or "").strip()[:280], "useful": r.get("useful") is not False}


def view(brief: dict | None) -> dict | None:
    if not brief:
        return None
    return {"status": brief.get("status"), "who": brief.get("who", ""), "asking": brief.get("asking", ""), "questions": brief.get("questions") or [],
            "first_line": brief.get("first_line", ""), "sources": brief.get("sources") or [], "looked_up": bool(brief.get("looked_up")),
            "note": brief.get("note", ""), "at": crm.iso(brief.get("at")), "by": "auto" if brief.get("by") == "auto" else "you",
            "written_by": brief.get("written_by", "rules"), "tokens": int(brief.get("tokens") or 0)}


async def _hold(lead_id: str) -> bool:
    """One brief at a time per lead (a double press or the automatic brief and a press at once spend one token, not two)."""
    r = await db().leads.update_one({"_id": lead_id, "$or": [{"brief_busy": {"$exists": False}}, {"brief_busy": {"$lt": now()}}]},
                                    {"$set": {"brief_busy": now() + timedelta(seconds=BUSY_SECONDS)}})
    return bool(r.modified_count)


async def write(owner: dict, lead: dict, by: str = "auto") -> dict:
    """Writes the brief for this lead and saves it on the lead. 1 AI token when the AI wrote a useful brief; 0 otherwise.
    Raises 402 when the owner has no tokens left (an automatic brief checks that first)."""
    if not await _hold(lead["_id"]):
        raise HTTPException(409, "This lead is being researched right now. Look again in a minute.")
    try:
        return await _write(owner, lead, by)
    finally:
        await db().leads.update_one({"_id": lead["_id"]}, {"$unset": {"brief_busy": ""}})


async def _write(owner: dict, lead: dict, by: str) -> dict:
    ws = owner["_id"]
    business = await db().businesses.find_one({"owner_id": ws}) or {}
    url, how = website_of(lead, await owner_domains(ws, business))
    site, why = (None, "")
    async with growth_core.working(ws, ROLE):
        if url:
            site, why = await read_their_site(url)
        fallback = brief_tasks.rules_brief(business, lead, site)
        written_by, tokens = "rules", 0
        if brief_tasks.nothing_to_go_on(lead, site):
            out, status = fallback, "empty"
        else:
            seen: dict = {}

            def check(r: dict) -> dict:
                out = _shape(r if isinstance(r, dict) else {})
                seen.update(out)
                if len(out["questions"]) < 3 or not out["first_line"]:
                    raise ValueError("incomplete brief")
                if not out["useful"] or not out["who"] or not out["asking"]:
                    raise ValueError("nothing useful found")
                if not site or not _grounded(out["who"], site, lead):
                    out["who"] = fallback["who"]     # no website read: only what they wrote, never a guess about the person
                return out
            try:
                out = await run_ai(owner, "lead_brief", brief_tasks.brief(business, lead, site), f"Lead brief — {lead.get('name') or 'inquiry'}",
                                   save_output=False, check=check)
                status, written_by, tokens = "done", "ai", 1
            except HTTPException as e:
                if e.status_code == 402:
                    raise
                if seen and not seen.get("useful", True):          # the AI found nothing useful: questions only, token refunded
                    out = {**fallback, "questions": seen["questions"] if len(seen.get("questions") or []) == 3 else fallback["questions"]}
                    status = "empty"
                else:                                               # the AI's answer couldn't be used: the rules version, no token
                    out = fallback
                    status = "done" if fallback["useful"] else "empty"
    brief = {"status": status, "who": out["who"] if status == "done" else fallback["who"], "asking": out["asking"] if status == "done" else fallback["asking"],
             "questions": out["questions"][:3], "first_line": out["first_line"],
             "sources": [{"label": "Their website", "url": site["url"]}] if site else [], "looked_up": bool(site),
             "note": (f"Their website ({_host(url)}) didn't open, so nothing public was looked up." if url and not site else ""),
             "at": now(), "by": by, "written_by": written_by, "tokens": tokens}
    if status == "empty" and not site and brief_tasks.NOTHING_LOOKED_UP not in brief["who"]:
        brief["who"] = brief_tasks.who_from_message(lead, lead.get("source") or "")
    await db().leads.update_one({"_id": lead["_id"]}, {"$set": {"brief": brief}})
    name = lead.get("name") or "a new inquiry"
    cid = lead.get("contact_id") or (await db().leads.find_one({"_id": lead["_id"]}, {"contact_id": 1}) or {}).get("contact_id")
    if not cid:
        c = await crm.upsert_contact(ws, lead.get("name", ""), lead.get("phone"), lead.get("email"), lead.get("company", ""), lead.get("source", ""))
        cid = c["_id"] if c else None
    if cid:
        await crm.add_activity(ws, cid, lead["_id"], "research",
                               "Lead researched" + (f": {brief['sources'][0]['url']}" if site else "") if status == "done"
                               else "Lead research: nothing useful to add, 3 questions are ready", "ownerdesk", "OwnerDesk")
    await growth_core.log_action(ws, ROLE, (f"Wrote a brief on {name}" + (" from their website" if site else "") if status == "done"
                                            else f"Wrote 3 first-call questions for {name} (nothing public to add)"), ref=f"lead:{lead['_id']}")
    return brief


async def _same_person_recently(ws: str, lead: dict) -> bool:
    ors = [{"phone": lead["phone"]}] if lead.get("phone") else []
    if lead.get("email"):
        ors.append({"email": lead["email"]})
    if not ors:
        return False
    return bool(await db().leads.find_one({"owner_id": ws, "_id": {"$ne": lead["_id"]}, "$or": ors, "brief.by": "auto",
                                           "brief.at": {"$gte": now() - timedelta(days=SAME_PERSON_DAYS)}}, {"_id": 1}))


async def auto(owner: dict, lead: dict) -> dict | None:
    """The automatic brief on a new lead, when everything allows it. Never raises."""
    ws = owner["_id"]
    name = lead.get("name") or "a new inquiry"
    try:
        if not plans.has(owner, "lead_brief") or not await growth_core.role_on(ws, ROLE):
            return None
        fresh = await db().leads.find_one({"_id": lead["_id"]}) or lead
        if fresh.get("brief") or await _same_person_recently(ws, fresh):
            return None
        if (await runs_status(owner))["left"] < TOKEN_FLOOR:
            await growth_core.log_action(ws, ROLE, f"No automatic brief on {name}: fewer than {TOKEN_FLOOR} AI tokens are left. "
                                                   "You can still press Research this lead.", ref=f"lead:{lead['_id']}", status="skipped")
            return None
        try:
            await growth_core.take(owner, "briefs")
        except HTTPException:
            await growth_core.log_action(ws, ROLE, f"No automatic brief on {name}: this month's automatic briefs are used. "
                                                   "You can still press Research this lead.", ref=f"lead:{lead['_id']}", status="skipped")
            return None
        try:
            brief = await write(owner, fresh, "auto")
        except Exception:
            await growth_core.give_back(owner, "briefs")
            raise
        if not brief.get("tokens"):
            await growth_core.give_back(owner, "briefs")      # no token was used: it doesn't count against the month
        return brief
    except HTTPException as e:
        log.info("automatic brief for lead %s not written: %s", lead.get("_id"), getattr(e, "detail", e))
        return None
    except Exception:  # noqa: BLE001
        log.exception("automatic brief for lead %s failed", lead.get("_id"))
        await db().leads.update_one({"_id": lead["_id"], "brief": {"$exists": False}}, {"$set": {"brief": {"status": "failed", "at": now(), "by": "auto"}}})
        await growth_core.log_action(ws, ROLE, f"Couldn't write the brief on {name}. Press Research this lead to try again.",
                                     ref=f"lead:{lead['_id']}", status="failed")
        return None


def after_lead(owner: dict, lead: dict) -> None:
    """Runs in the background, so a customer's form never waits for the website or the AI."""
    async def run():
        await auto(owner, lead)
    task = asyncio.create_task(run())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def settle() -> None:
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


async def ensure_indexes() -> None:
    await db().leads.create_index([("owner_id", 1), ("brief.at", -1)])


catalog.register(ROLE, {
    "name": "Researcher", "feature": "lead_brief", "trigger": "lead",
    "what": "Writes a short brief on every new inquiry: who they are (from their own business website, never a private person), "
            "what they are probably asking for, 3 questions for the first call and a first line.",
    "when": f"On every new inquiry, while you have {TOKEN_FLOOR} or more AI tokens and this month's automatic briefs last"})
