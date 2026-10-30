"""
An owner's own domain for their website (Growth Mentorship and up, connected for them by the team).
Every website already lives at <name>.SITES_DOMAIN; this adds www.theirbusiness.in on top.

The admin adds the domain in Railway, pastes Railway's CNAME and TXT records into Admin → Own domains, the owner (or the
team, on a call) adds exactly those records at the domain provider and presses Check; once Railway has the certificate the
admin marks it live. CUSTOM_DOMAIN_TARGET switches to "direct" mode instead: owners point a CNAME at one fixed host and prove
the domain with our own TXT record.
"""
import logging
import re
import secrets
from datetime import timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from .. import plans
from ..config import settings
from ..context import Ctx, feature
from ..db import db, now
from ..security import rate_limit, require_admin
from ..services import notify, track
from ..sites import site_url

log = logging.getLogger("hub")
router = APIRouter(prefix="/api")


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
TLD = re.compile(r"^(?:[a-z]{2,63}|xn--[a-z0-9-]{1,59})$")
# Second-level suffixes where the registrable domain has three labels (mybiz.co.in)
TWO_LEVEL = {"co.in", "net.in", "org.in", "firm.in", "gen.in", "ind.in", "ac.in", "edu.in", "res.in", "gov.in", "nic.in",
             "co.uk", "org.uk", "me.uk", "com.au", "net.au", "org.au", "co.nz", "com.sg", "com.my", "co.za", "com.br", "co.jp", "com.np", "com.bd", "com.pk", "com.lk"}


class DomainIn(BaseModel):
    domain: str = Field(min_length=3, max_length=300)


class LiveIn(BaseModel):
    live: bool = True


class RecordsIn(BaseModel):
    """The records the hosting service shows when the admin adds the owner's domain there (Railway: a CNAME and a TXT)."""
    cname: str = Field(min_length=4, max_length=253)
    txt_name: str = Field(default="", max_length=253)
    txt_value: str = Field(default="", max_length=255)


def _bare(host: str) -> str:
    return re.sub(r"^[a-z][a-z0-9+.-]*://", "", (host or "").strip().lower()).split("/", 1)[0].split(":", 1)[0].rstrip(".")


def domain_target() -> str:
    """Where owners point their CNAME: CUSTOM_DOMAIN_TARGET, or the hub's own host when that isn't set."""
    return _bare(settings.custom_domain_target) or settings.app_host


def domain_mode() -> str:
    """direct  — CUSTOM_DOMAIN_TARGET is set: every owner points a CNAME there and proves the domain with our TXT record.
    hosted  — the default on Railway: the admin adds each domain in Railway and pastes the CNAME and TXT records Railway
              shows into Admin → Own domains; the owner then adds exactly those records."""
    return "direct" if _bare(settings.custom_domain_target) else "hosted"


def _full_host(name: str, domain: str) -> str:
    """A record name as shown by a DNS panel ('_railway-verify.www') or in full → the full host to look up."""
    name = _bare(name)
    root = registrable(domain)
    return name if name == root or name.endswith("." + root) else f"{name}.{root}"


def _relative(host: str, domain: str) -> str:
    root = registrable(domain)
    return "@" if host == root else host[: -len(root) - 1] if host.endswith("." + root) else host


def domain_records(d: dict) -> list[dict]:
    """The DNS records the owner adds, in order. Empty while a hosted domain waits for the admin's records."""
    name = d["name"]
    if domain_mode() == "direct":
        return [{"type": "CNAME", "name": record_name(name), "host": name, "value": domain_target()},
                {"type": "TXT", "name": proof_name(name), "host": f"_actionhub.{name}", "value": proof_value(d)}]
    hr = d.get("host_records") or {}
    if not hr.get("cname"):
        return []
    out = [{"type": "CNAME", "name": record_name(name), "host": name, "value": hr["cname"]}]
    if hr.get("txt_name") and hr.get("txt_value"):
        host = _full_host(hr["txt_name"], name)
        out.append({"type": "TXT", "name": _relative(host, name), "host": host, "value": hr["txt_value"]})
    return out


def registrable(host: str) -> str:
    labels = host.split(".")
    return ".".join(labels[-3:]) if len(labels) >= 3 and ".".join(labels[-2:]) in TWO_LEVEL else ".".join(labels[-2:])


def record_name(host: str) -> str:
    """What goes in the 'Name' / 'Host' box at the domain provider: 'www' for www.mybiz.in, '@' for mybiz.in."""
    root = registrable(host)
    return host[: -len(root) - 1] if host != root else "@"


def _under(host: str, parent: str) -> bool:
    return bool(parent) and (host == parent or host.endswith("." + parent))


def clean_domain(raw: str) -> str:
    """'https://WWW.MyBiz.in/' → 'www.mybiz.in'. Refuses paths, ports, IP addresses and the hub's own addresses."""
    s = (raw or "").strip().lower()
    s = re.sub(r"^[a-z][a-z0-9+.-]*://", "", s).rstrip("/").rstrip(".")
    if not s or re.search(r"[/?#@:\\\s]", s):
        raise HTTPException(400, "Enter only the domain, like www.yourbusiness.in — nothing before or after it.")
    try:
        s = s.encode("idna").decode("ascii")
    except UnicodeError:
        raise HTTPException(400, "That doesn't look like a domain. Enter it like www.yourbusiness.in")
    labels = s.split(".")
    if len(s) > 253 or len(labels) < 2 or not all(LABEL.match(l) for l in labels) or not TLD.match(labels[-1]):
        raise HTTPException(400, "That doesn't look like a domain. Enter it like www.yourbusiness.in")
    hub = settings.app_host
    if (s == "localhost" or _under(s, hub) or _under(s, registrable(hub)) or _under(s, domain_target())
            or (settings.sites_domain and _under(s, settings.sites_domain))):
        raise HTTPException(400, "That address belongs to the hub. Enter a domain you bought for your business.")
    return s


def domain_view(site: dict) -> dict | None:
    d = site.get("domain")
    if not d:
        return None
    return {"name": d["name"], "status": d.get("status", "pending"), "note": d.get("note", ""),
            "added_at": _iso(d.get("added_at")), "checked_at": _iso(d.get("checked_at")), "live_at": _iso(d.get("live_at")),
            "url": f"https://{d['name']}", "apex": record_name(d["name"]) == "@", "mode": domain_mode(),
            "records": domain_records(d)}


def proof_name(host: str) -> str:
    """The TXT record that proves the owner controls the domain: '_actionhub.www' for www.mybiz.in, '_actionhub' for mybiz.in."""
    rel = record_name(host)
    return "_actionhub" if rel == "@" else f"_actionhub.{rel}"


def proof_value(d: dict) -> str:
    return f"actionhub-verify={d.get('token', '')}"


async def _my_site(ctx: Ctx) -> dict:
    site = await db().sites.find_one({"owner_id": ctx.ws})
    if not site:
        raise HTTPException(400, "Create your website first")
    return site


class DnsUnavailable(Exception):
    pass


async def resolve(name: str, rdtype: str) -> list[str]:
    """DNS answers for a name, lower-cased without the trailing dot. [] when there is no such record.
    Tests replace this function, so the check never touches the network."""
    import dns.asyncresolver
    import dns.exception
    import dns.resolver
    try:
        answer = await dns.asyncresolver.resolve(name, rdtype, lifetime=6)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers):
        return []
    except (dns.exception.DNSException, OSError) as e:
        raise DnsUnavailable(str(e)) from e
    return sorted({r.to_text().rstrip(".").lower() for r in answer})


async def dns_verdict(d: dict) -> tuple[bool | None, str]:
    """(True, note) when the domain's DNS is right, (False, what to fix) when not, (None, note) when there's nothing to check yet."""
    name = d["name"]
    records = domain_records(d)
    if not records:
        return None, "We're preparing your domain. The records to add will appear here, usually within a working day."
    cname = next(r for r in records if r["type"] == "CNAME")
    for txt in (r for r in records if r["type"] == "TXT"):
        found = [t.strip('"') for t in await resolve(txt["host"], "TXT")]
        if txt["value"] not in found:
            return False, ("We can't see your TXT record yet. Add the TXT record shown here exactly, then check again. "
                           "New records can take a few hours to show.")
    target = cname["value"].lower()
    cnames = await resolve(name, "CNAME")
    if target in cnames:
        return True, f"{name} points to the hub."
    mine = set(await resolve(name, "A"))
    if mine and mine == set(await resolve(target, "A")):
        return True, f"{name} points to the hub's server."
    if cnames:
        return False, f"{name} points to {cnames[0]}. Change that CNAME record so it points to {target}, then check again."
    if mine:
        return False, (f"{name} points to another server ({', '.join(sorted(mine)[:3])}). Delete that record, add the CNAME record "
                       "shown here, then check again.")
    return False, (f"We can't see a record for {name} yet. After you add it, it can take from a few minutes to a few hours "
                   "to show. Check again later.")


@router.get("/site/domain")
async def get_domain(ctx: Ctx = Depends(feature("custom_domain", "owner"))):
    site = await db().sites.find_one({"owner_id": ctx.ws})
    return {"domain": domain_view(site) if site else None, "target": domain_target(),
            "site": {"exists": bool(site), "live": bool(site and site.get("status") == "live")}}


@router.post("/site/domain")
async def add_domain(body: DomainIn, ctx: Ctx = Depends(feature("custom_domain", "owner"))):
    site = await _my_site(ctx)
    name = clean_domain(body.domain)
    if (site.get("domain") or {}).get("name") == name:
        return {"domain": domain_view(site), "target": domain_target()}
    other = await db().sites.find_one({"domain.name": name, "_id": {"$ne": site["_id"]}}, {"_id": 1, "domain": 1})
    if other:
        stale = other["domain"].get("status") in ("pending", "setup") and other["domain"].get("added_at") and other["domain"]["added_at"] < now() - timedelta(days=7)
        if not stale:
            raise HTTPException(409, "This domain is already connected to another website on the hub.")
        await db().sites.update_one({"_id": other["_id"], "domain.name": name}, {"$unset": {"domain": ""}})  # an unproven claim expires after 7 days
    domain = {"name": name, "status": "pending" if domain_mode() == "direct" else "setup", "token": secrets.token_hex(12),
              "added_at": now(), "checked_at": None, "note": ""}
    await db().sites.update_one({"_id": site["_id"]}, {"$set": {"domain": domain}})
    await track("domain_added", ctx.ws, domain=name)
    return {"domain": domain_view({"domain": domain}), "target": domain_target()}


@router.post("/site/domain/check")
async def check_domain(ctx: Ctx = Depends(feature("custom_domain", "owner"))):
    site = await _my_site(ctx)
    d = site.get("domain")
    if not d:
        raise HTTPException(400, "Add your domain first.")
    await rate_limit(f"dns:{site['_id']}", 30, 600, "That's a lot of checks. DNS changes take time — please try again in a few minutes.")
    status = d.get("status", "pending")
    try:
        ok, note = await dns_verdict(d)
    except DnsUnavailable:
        ok, note = None, "We couldn't check right now. Please try again in a few minutes."
    if ok and status == "pending":
        status = "verified"
        await track("domain_verified", ctx.ws, domain=d["name"])
    elif ok is False and status == "verified":
        status = "pending"
    if ok and status == "verified":
        note = "Connected. We're now switching on the secure (https) address for it — this usually takes up to a day."
    elif ok and status == "live":
        note = "Live. Customers can open your website on this address."
    await db().sites.update_one({"_id": site["_id"]}, {"$set": {"domain.status": status, "domain.note": note, "domain.checked_at": now()}})
    return {"domain": domain_view(await db().sites.find_one({"_id": site["_id"]})), "target": domain_target()}


@router.delete("/site/domain")
async def remove_domain(ctx: Ctx = Depends(feature("custom_domain", "owner"))):
    site = await _my_site(ctx)
    await db().sites.update_one({"_id": site["_id"]}, {"$unset": {"domain": ""}})
    d = site.get("domain")
    if d:
        await track("domain_removed", ctx.ws, domain=d["name"])
        if d.get("status") in ("verified", "live"):  # it may still be attached in Railway: the admin removes it there
            await db().released_domains.update_one({"_id": d["name"]}, {"$set": {"site_id": site["_id"], "owner_id": ctx.ws, "was": d.get("status"),
                                                                                 "released_at": now()}}, upsert=True)
    return {"ok": True}


@router.get("/admin/domains")
async def admin_domains(_: dict = Depends(require_admin)):
    sites = [s async for s in db().sites.find({"domain.name": {"$exists": True}}).limit(1000)]
    ids = [s["owner_id"] for s in sites]
    names = {b["owner_id"]: b.get("name", "") async for b in db().businesses.find({"owner_id": {"$in": ids}}, {"owner_id": 1, "name": 1})}
    phones = {u["_id"]: u.get("phone", "") async for u in db().users.find({"_id": {"$in": ids}}, {"phone": 1})}
    order = {"setup": -2, "verified": 0, "pending": 1, "live": 2}
    rows = [{"site_id": s["_id"], "slug": s["slug"], "site_status": s.get("status"), "business": names.get(s["owner_id"], ""),
             "phone": phones.get(s["owner_id"], ""), "hub_url": site_url(s["slug"]), **domain_view(s)} for s in sites]
    claimed = {r["name"] for r in rows}
    async for rel in db().released_domains.find({}).limit(500):
        if rel["_id"] in claimed:
            continue
        rows.append({"site_id": rel.get("site_id"), "slug": "", "site_status": None, "business": names.get(rel.get("owner_id"), ""),
                     "phone": phones.get(rel.get("owner_id"), ""), "hub_url": "", "name": rel["_id"], "status": "released",
                     "note": "Removed by the owner — remove it from Railway's custom domains.", "added_at": None,
                     "checked_at": None, "live_at": None, "released_at": _iso(rel.get("released_at"))})
    order["released"] = -1
    return sorted(rows, key=lambda r: (order.get(r["status"], 3), r["added_at"] or ""))


@router.put("/admin/domains/{site_id}/records")
async def admin_domain_records(site_id: str, body: RecordsIn, admin: dict = Depends(require_admin)):
    """Hosted mode: after adding the owner's domain in Railway, the admin pastes the records Railway shows."""
    site = await db().sites.find_one({"_id": site_id})
    if not site or not site.get("domain"):
        raise HTTPException(404, "No domain on this website")
    cname = _bare(body.cname)
    if not re.fullmatch(r"(?:[a-z0-9-]{1,63}\.)+[a-z]{2,63}", cname):
        raise HTTPException(400, "Paste the CNAME value Railway shows, like abc123.up.railway.app")
    txt_name, txt_value = _bare(body.txt_name), body.txt_value.strip().strip('"')
    if bool(txt_name) != bool(txt_value):
        raise HTTPException(400, "Paste both the TXT name and its value, or neither.")
    d = site["domain"]
    patch = {"domain.host_records": {"cname": cname, "txt_name": txt_name, "txt_value": txt_value, "set_at": now(), "by": admin["_id"]},
             "domain.note": "Add the records shown here, then press Check now."}
    if d.get("status") == "setup":
        patch["domain.status"] = "pending"
    await db().sites.update_one({"_id": site_id}, {"$set": patch})
    if d.get("status") == "setup":
        await notify(site["owner_id"], f"Your domain {d['name']} is ready to connect: add the records shown on your Website page.")
    await track("domain_records", site["owner_id"], domain=d["name"], by=admin["_id"])
    return domain_view(await db().sites.find_one({"_id": site_id}))


@router.delete("/admin/domains/released/{name}")
async def admin_domain_released_done(name: str, _: dict = Depends(require_admin)):
    await db().released_domains.delete_one({"_id": name.lower()[:253]})
    return {"ok": True}


@router.post("/admin/domains/{site_id}/live")
async def admin_domain_live(site_id: str, body: LiveIn | None = Body(default=None), admin: dict = Depends(require_admin)):
    live = body.live if body else True
    site = await db().sites.find_one({"_id": site_id})
    if not site or not site.get("domain"):
        raise HTTPException(404, "No domain on this website")
    name = site["domain"]["name"]
    if live:
        patch = {"domain.status": "live", "domain.live_at": now(), "domain.note": "Live. Customers can open your website on this address."}
    else:
        patch = {"domain.status": "verified", "domain.note": "Connected. We're switching on the secure (https) address — usually within a day."}
    await db().sites.update_one({"_id": site_id}, {"$set": patch})
    await track("domain_live" if live else "domain_unlive", site["owner_id"], domain=name, by=admin["_id"])
    if live and site["domain"].get("status") != "live":
        await notify(site["owner_id"], f"Your website is now live on https://{name}")
    return domain_view(await db().sites.find_one({"_id": site_id}))


async def own_domain(request: Request, host: str):
    """For a request on a host that isn't the hub's: the website slug to answer with, a response (redirect or not found),
    or None when no website has claimed this host (the hub then behaves as usual)."""
    host = (host or "").strip().rstrip(".").lower()
    if not host or host == settings.app_host:
        return None
    site = await db().sites.find_one({"domain.name": host})
    if not site:
        return None
    from . import public as pages
    ready = site["domain"].get("status") == "live" and site.get("status") == "live"
    if not ready:
        return pages.not_found()
    owner = await db().users.find_one({"_id": site["owner_id"]}, {"plan": 1, "trial": 1, "sub": 1, "access": 1, "grants": 1, "phone": 1, "welcome": 1})
    if owner and not plans.has(owner, "custom_domain"):  # plan lapsed: keep customers flowing to the free address
        return RedirectResponse(site_url(site["slug"]), status_code=302)
    return site["slug"]


async def ensure_indexes() -> None:
    try:  # looked up on every request that arrives on a non-hub host
        await db().sites.create_index("domain.name")
    except Exception as e:  # noqa: BLE001
        log.warning("domain index not created: %s", e)
