"""
The Employz.ai import: a business's sub-account (pipelines, contacts, opportunities, and the users and custom fields they
refer to) read into the hub and kept in step, read-only (see app/employz_api.py: GET only).

Where it goes
    contacts      the hub's own CRM contacts. Matched by Employz.ai id, then phone, then email, so a person who is already in
                  the hub is linked, not copied. contacts.ext = {src "employz", ids [..], loc, created (made by the import),
                  hash {id: fingerprint}, fields {label: value}, owner, seen (the last full read that saw it), deleted_at}.
                  A contact the import made takes Employz.ai's name, phone, email, company and tags; one that was already in
                  the hub only gets the fields it had empty (an owner's own edits are never overwritten).
    ext_pipelines {_id "<ws>:<id>", ws, ext_id, name, stages [{id, name, position}], hash, seen, deleted_at}
    ext_deals     {_id "<ws>:<id>", ws, ext_id, pipeline_id, stage_id, name, value, status open|won|lost|abandoned, source,
                   assignee, contact_ext, contact_id (the hub contact), contact {name, phone, email, company}, fields,
                   created_at, updated_at, status_at, hub_lead_id (a deal the hub itself sent to Employz.ai), hash, seen,
                   deleted_at}
                  Kept apart from the hub's leads on purpose: an imported deal never triggers the hub's inquiry
                  automations (instant reply, follow-ups, alerts, the push back to Employz.ai) and never lands in Inquiries.
    ext_sync      {_id ws, state idle|running|failed|needs_attention|disconnected, lock_until, scan {id, started_at, step,
                   cursors {contacts, opportunities}, pages {..}}, counts {type: {added, updated, unchanged, rejected}},
                   coverage {type: {source, imported, rejected, ready}}, rejected [{type, id, reason}] (the last 100),
                   location {id, name, timezone}, users {id: name}, last_ok_at, last_error, fails, disconnected_at}

How it runs
    A sync reads everything (pages of 100) and writes only what changed (a fingerprint per record). The cursor is saved
    after every page, so a sync stopped part-way (a deploy, an error) carries on from the next page. When a full read of a
    kind finishes, a record it didn't see was deleted in Employz.ai: it's marked deleted (soft), never removed outright.
    One sync per business at a time (a lock that expires). The scheduler starts one every SYNC_MINUTES; "Import now" too.
    Disconnecting stops it at once; the imported data is removed PURGE_DAYS later (unless they connect again).

Nothing personal (names, phones, emails, tokens) is ever written to the log.
"""
import asyncio
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone

from pymongo.errors import DuplicateKeyError

from . import connections, crm, employz_api as api, plans
from .db import db, now
from .services import new_id, notify

log = logging.getLogger("employz_import")

SYNC_MINUTES = 15
RETRY_MINUTES = 5
LOCK = timedelta(minutes=10)
PURGE_DAYS = 7
KEEP_REJECTED = 100
STATUS = {"open": "open", "won": "won", "lost": "lost", "abandoned": "abandoned"}
_running: set[str] = set()      # this worker's syncs in progress (the lock in the database guards across workers)


def _aware(at):
    if isinstance(at, datetime) and at.tzinfo is None:
        return at.replace(tzinfo=timezone.utc)
    return at


def _when(v) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def fingerprint(d: dict) -> str:
    return hashlib.sha1(json.dumps(d, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def _fields(rec: dict, defs: dict) -> dict:
    """Custom field values by their label. A field the hub doesn't know the definition of is kept under its id."""
    out = {}
    for f in rec.get("customFields") or rec.get("customField") or []:
        if not isinstance(f, dict) or not f.get("id"):
            continue
        v = f.get("value", f.get("fieldValue"))
        if v in (None, "", []):
            continue
        out[str(defs.get(f["id"]) or f["id"])[:80]] = v if isinstance(v, (int, float, bool)) else str(v)[:500]
    return out


# ───────────────────────── shaping one record ─────────────────────────
def contact_of(c: dict, defs: dict, users: dict) -> dict:
    first, last = str(c.get("firstName") or "").strip(), str(c.get("lastName") or "").strip()
    name = str(c.get("contactName") or c.get("name") or f"{first} {last}").strip() or str(c.get("companyName") or "").strip()
    return {"name": name[:80], "phone": crm.norm_phone(c.get("phone")), "email": crm.norm_email(c.get("email")),
            "company": str(c.get("companyName") or "").strip()[:120], "tags": crm.clean_tags(c.get("tags") or []),
            "source": str(c.get("source") or "").strip()[:60], "owner": users.get(c.get("assignedTo") or "", ""),
            "fields": _fields(c, defs), "updated": str(c.get("dateUpdated") or c.get("dateAdded") or "")}


def deal_of(o: dict, defs: dict, users: dict) -> dict:
    if not isinstance(o.get("pipelineId"), str) or not o.get("pipelineId") or not isinstance(o.get("pipelineStageId"), str) or not o.get("pipelineStageId"):
        raise ValueError("no pipeline or stage")
    status = STATUS.get(str(o.get("status") or "open").lower())
    if not status:
        raise ValueError("unknown status")
    try:
        value = float(o["monetaryValue"]) if o.get("monetaryValue") not in (None, "") else None
    except (TypeError, ValueError):
        raise ValueError("value is not a number")
    ct = o.get("contact") if isinstance(o.get("contact"), dict) else {}
    return {"name": str(o.get("name") or ct.get("name") or "Opportunity").strip()[:160], "value": value,
            "pipeline_id": o["pipelineId"], "stage_id": o["pipelineStageId"], "status": status,
            "source": str(o.get("source") or "").strip()[:60], "assignee": users.get(o.get("assignedTo") or "", ""),
            "contact_ext": str(o.get("contactId") or ct.get("id") or ""),
            "contact": {"name": str(ct.get("name") or "").strip()[:80], "phone": crm.norm_phone(ct.get("phone")),
                        "email": crm.norm_email(ct.get("email")), "company": str(ct.get("companyName") or "").strip()[:120]},
            "fields": _fields(o, defs), "created": str(o.get("createdAt") or ""), "updated": str(o.get("updatedAt") or ""),
            "status_at": str(o.get("lastStatusChangeAt") or "")}


# ───────────────────────── writing ─────────────────────────
async def _upsert_contact(ws: str, loc: str, ext_id: str, c: dict, scan: str) -> str:
    """added | updated | unchanged"""
    h = fingerprint(c)
    d = db().contacts
    existing = await d.find_one({"ws": ws, "ext.ids": ext_id})
    if existing:
        ext = existing.get("ext") or {}
        if (ext.get("hash") or {}).get(ext_id) == h and not ext.get("deleted_at"):
            await d.update_one({"_id": existing["_id"]}, {"$set": {"ext.seen": scan}})
            return "unchanged"
        patch = _contact_patch(existing, c, own=ext.get("created"))
        patch.update({f"ext.hash.{ext_id}": h, "ext.fields": c["fields"], "ext.owner": c["owner"], "ext.seen": scan, "updated_at": now()})
        await d.update_one({"_id": existing["_id"]}, {"$set": patch, "$unset": {"ext.deleted_at": ""}})
        return "updated"
    for _ in range(2):
        match = await crm.find_contact(ws, c["phone"], c["email"])
        if match:                 # already in the hub (or another Employz.ai record of the same person): link it
            patch = _contact_patch(match, c, own=(match.get("ext") or {}).get("created"))
            patch.update({f"ext.hash.{ext_id}": h, "ext.src": "employz", "ext.loc": loc, "ext.fields": c["fields"],
                          "ext.owner": c["owner"], "ext.seen": scan, "updated_at": now()})
            if not match.get("ext"):
                patch["ext.created"] = False
            await d.update_one({"_id": match["_id"]}, {"$set": patch, "$addToSet": {"ext.ids": ext_id}, "$unset": {"ext.deleted_at": ""}})
            return "updated"
        doc = {"_id": new_id(), "ws": ws, "name": c["name"] or "Customer", "phone": c["phone"], "email": c["email"],
               "company": c["company"], "company_id": await crm.company_for(ws, c["company"]) if c["company"] else None,
               "source": "employz", "tags": c["tags"], "notes": "", "follow_up_on": None, "follow_up_note": "", "assignee_id": None,
               "created_at": now(), "updated_at": now(), "last_activity_at": now(),
               "ext": {"src": "employz", "loc": loc, "ids": [ext_id], "created": True, "hash": {ext_id: h}, "fields": c["fields"],
                       "owner": c["owner"], "seen": scan}}
        try:
            await d.insert_one(doc)
            return "added"
        except DuplicateKeyError:
            continue          # the same phone or email arrived a moment ago: link to that one
    raise ValueError("could not be matched or added")


def _contact_patch(existing: dict, c: dict, own: bool) -> dict:
    """A contact the import made follows Employz.ai; one the hub had keeps its own values and only gets empty ones filled."""
    patch = {}
    for k in ("name", "phone", "email", "company"):
        v = c[k]
        if not v:
            continue
        if own or not existing.get(k) or (k == "name" and existing.get("name") in ("Customer", "WhatsApp customer")):
            if k in ("phone", "email") and existing.get(k) and existing.get(k) != v and not own:
                continue
            patch[k] = v
    if own and c["tags"]:
        patch["tags"] = c["tags"]
    return patch


async def _upsert_pipeline(ws: str, p: dict, scan: str) -> str:
    stages = sorted(({"id": s["id"], "name": str(s.get("name") or "Stage").strip()[:60], "position": s.get("position") or 0}
                     for s in p.get("stages") or []), key=lambda s: (s["position"], s["name"]))
    body = {"name": str(p.get("name") or "Pipeline").strip()[:80], "stages": stages}
    h = fingerprint(body)
    _id = f"{ws}:{p['id']}"
    old = await db().ext_pipelines.find_one({"_id": _id}, {"hash": 1, "deleted_at": 1})
    if old and old.get("hash") == h and not old.get("deleted_at"):
        await db().ext_pipelines.update_one({"_id": _id}, {"$set": {"seen": scan}})
        return "unchanged"
    await db().ext_pipelines.update_one({"_id": _id}, {"$set": {"ws": ws, "ext_id": p["id"], **body, "hash": h, "seen": scan, "updated_at": now()},
                                                       "$unset": {"deleted_at": ""}}, upsert=True)
    return "updated" if old else "added"


async def _upsert_deal(ws: str, ext_id: str, dl: dict, scan: str) -> str:
    h = fingerprint(dl)
    _id = f"{ws}:{ext_id}"
    old = await db().ext_deals.find_one({"_id": _id}, {"hash": 1, "deleted_at": 1})
    if old and old.get("hash") == h and not old.get("deleted_at"):
        await db().ext_deals.update_one({"_id": _id}, {"$set": {"seen": scan}})
        return "unchanged"
    contact = await db().contacts.find_one({"ws": ws, "ext.ids": dl["contact_ext"]}, {"_id": 1}) if dl["contact_ext"] else None
    pushed = await db().leads.find_one({"owner_id": ws, "crm.opportunity_id": ext_id}, {"_id": 1})
    doc = {"ws": ws, "ext_id": ext_id, **{k: v for k, v in dl.items() if k not in ("created", "updated", "status_at")},
           "created_at": _when(dl["created"]), "updated_at": _when(dl["updated"]), "status_at": _when(dl["status_at"]),
           "contact_id": contact["_id"] if contact else None, "hub_lead_id": pushed["_id"] if pushed else None,
           "hash": h, "seen": scan, "synced_at": now()}
    await db().ext_deals.update_one({"_id": _id}, {"$set": doc, "$unset": {"deleted_at": ""}}, upsert=True)
    return "updated" if old else "added"


async def _gone(ws: str, kind: str, scan: str) -> int:
    """After a complete read: what it didn't see was deleted in Employz.ai. Marked, never removed (soft delete)."""
    at = now()
    if kind == "contacts":
        r = await db().contacts.update_many({"ws": ws, "ext.src": "employz", "ext.seen": {"$ne": scan}, "ext.deleted_at": {"$exists": False}},
                                            {"$set": {"ext.deleted_at": at}})
    else:
        coll = db().ext_pipelines if kind == "pipelines" else db().ext_deals
        r = await coll.update_many({"ws": ws, "seen": {"$ne": scan}, "deleted_at": {"$exists": False}}, {"$set": {"deleted_at": at}})
    return r.modified_count


# ───────────────────────── one sync ─────────────────────────
async def _lock(ws: str) -> bool:
    t = now()
    try:
        await db().ext_sync.insert_one({"_id": ws, "state": "idle", "fails": 0, "created_at": t})
    except DuplicateKeyError:
        pass
    r = await db().ext_sync.update_one({"_id": ws, "$or": [{"lock_until": {"$exists": False}}, {"lock_until": None}, {"lock_until": {"$lt": t}}],
                                        "disconnected_at": {"$exists": False}},
                                       {"$set": {"lock_until": t + LOCK, "state": "running", "run_started_at": t}})
    return bool(r.modified_count)


async def _beat(ws: str, fields: dict | None = None) -> None:
    await db().ext_sync.update_one({"_id": ws}, {"$set": {"lock_until": now() + LOCK, **(fields or {})}})


async def _reject(ws: str, kind: str, ext_id: str, reason: str) -> None:
    await db().ext_sync.update_one({"_id": ws}, {"$push": {"rejected": {"$each": [{"type": kind, "id": ext_id[:40], "reason": reason[:80], "at": now()}],
                                                                          "$slice": -KEEP_REJECTED}}})


async def run(ws: str, trigger: str = "scheduled") -> dict:
    """One sync of this business's sub-account. Returns the sync's state. Safe to call twice at once: one runs."""
    if not await _lock(ws):
        return await status(ws)
    try:
        conn = await connections.get(ws, "employz")
        if not connections.ready("employz", conn):
            await db().ext_sync.update_one({"_id": ws}, {"$set": {"state": "not_connected", "lock_until": None}})
            return await status(ws)
        await _sync(ws, conn, trigger)
        await db().ext_sync.update_one({"_id": ws}, {"$set": {"state": "idle", "lock_until": None, "last_ok_at": now(), "fails": 0,
                                                              "last_error": None}, "$unset": {"scan": ""}})
    except api.AuthError as e:
        first = (await db().ext_sync.find_one({"_id": ws}, {"state": 1}) or {}).get("state") != "needs_attention"
        await db().ext_sync.update_one({"_id": ws}, {"$set": {"state": "needs_attention", "lock_until": None,
                                                              "last_error": {"code": e.code, "message": e.message, "request_id": e.request_id, "at": now()}}})
        if first:
            await notify(ws, "Your Employz.ai connection needs attention: " + e.message)
        log.warning("employz import %s: %s (%s)", ws, e.code, e.detail)
    except api.EmployzError as e:
        s = await db().ext_sync.find_one_and_update({"_id": ws}, {"$set": {"state": "failed", "lock_until": None,
                                                                            "last_error": {"code": e.code, "message": e.message, "request_id": e.request_id, "at": now()}},
                                                                   "$inc": {"fails": 1}}, return_document=True)
        if (s or {}).get("fails") == 3:
            await notify(ws, "The Employz.ai import failed 3 times in a row: " + e.message)
        log.warning("employz import %s: %s (%s)", ws, e.code, e.detail)
    except Exception:
        await db().ext_sync.update_one({"_id": ws}, {"$set": {"state": "failed", "lock_until": None,
                                                              "last_error": {"code": "error", "message": api.EmployzError.message, "at": now()}},
                                                     "$inc": {"fails": 1}})
        log.exception("employz import %s failed", ws)
    return await status(ws)


async def _sync(ws: str, conn: dict, trigger: str) -> None:
    s = await db().ext_sync.find_one({"_id": ws}) or {}
    scan = s.get("scan") or {}
    if scan.get("id"):              # an unfinished read carries on from its saved page, with its counts so far
        counts, coverage = s.get("counts") or {}, s.get("coverage") or {}
    else:
        scan = {"id": new_id(), "started_at": now(), "trigger": trigger, "step": "location", "cursors": {}, "pages": {}}
        counts, coverage = {}, {}
        await _beat(ws, {"scan": scan, "counts": counts, "coverage": coverage})
    sid, loc = scan["id"], conn["location_id"]

    def tally(kind: str, what: str) -> None:
        c = counts.setdefault(kind, {"added": 0, "updated": 0, "unchanged": 0, "rejected": 0})
        c[what] = c.get(what, 0) + 1

    location = await api.read_location(conn)
    users = {u["id"]: str(u.get("name") or f"{u.get('firstName', '')} {u.get('lastName', '')}").strip()[:80]
             for u in (await api.read_users(conn)).records}
    defs = {f["id"]: str(f.get("name") or f.get("fieldKey") or f["id"])[:80] for f in (await api.read_custom_fields(conn)).records}
    await _beat(ws, {"location": location, "users": users})

    order = ["pipelines", "contacts", "opportunities"]
    for kind in order[order.index(scan["step"]) if scan["step"] in order else 0:]:
        await _beat(ws, {"scan.step": kind})
        if kind == "pipelines":
            page = await api.read_pipelines(conn)
            for p in page.records:
                tally(kind, await _upsert_pipeline(ws, p, sid))
            await _gone(ws, kind, sid)
            coverage[kind] = {"source": len(page.records), "imported": len(page.records), "rejected": 0, "ready": True}
            await _beat(ws, {"counts": counts, "coverage": coverage})
            continue
        reader = api.read_contacts if kind == "contacts" else api.read_opportunities
        cursor = (scan.get("cursors") or {}).get(kind)
        pages = (scan.get("pages") or {}).get(kind, 0)
        total = None
        while True:
            page = await reader(conn, cursor)
            total = page.total if page.total is not None else total
            for rec in page.records:
                try:
                    if kind == "contacts":
                        tally(kind, await _upsert_contact(ws, loc, rec["id"], contact_of(rec, defs, users), sid))
                    else:
                        tally(kind, await _upsert_deal(ws, rec["id"], deal_of(rec, defs, users), sid))
                except ValueError as e:      # a bad record is set aside with its reason; the rest of the page goes in
                    tally(kind, "rejected")
                    await _reject(ws, kind, rec["id"], str(e))
            pages += 1
            cursor = page.cursor
            await _beat(ws, {f"scan.cursors.{kind}": cursor, f"scan.pages.{kind}": pages, "counts": counts})
            if not cursor:
                break
        await _gone(ws, kind, sid)
        c = counts.get(kind) or {}
        imported = c.get("added", 0) + c.get("updated", 0) + c.get("unchanged", 0)
        coverage[kind] = {"source": total, "imported": imported, "rejected": c.get("rejected", 0),
                          "ready": total is None or imported + c.get("rejected", 0) >= total}
        await _beat(ws, {"coverage": coverage, "scan.cursors": {**(scan.get("cursors") or {}), kind: None}})
    await _beat(ws, {"last_full_at": now(), "trigger": trigger})


# ───────────────────────── status, disconnect, the clock ─────────────────────────
async def status(ws: str) -> dict:
    s = await db().ext_sync.find_one({"_id": ws}) or {}
    pipes = await db().ext_pipelines.count_documents({"ws": ws, "deleted_at": {"$exists": False}})
    deals = await db().ext_deals.count_documents({"ws": ws, "deleted_at": {"$exists": False}})
    contacts = await db().contacts.count_documents({"ws": ws, "ext.src": "employz", "ext.deleted_at": {"$exists": False}})
    iso = lambda v: _aware(v).isoformat() if isinstance(v, datetime) else None   # noqa: E731
    err = s.get("last_error") or None
    scan = s.get("scan") or {}
    state = s.get("state") or "never"
    if state == "idle" and not s.get("last_ok_at"):      # connected, nothing imported yet
        state = "never"
    return {"state": state, "running": s.get("state") == "running" and bool(s.get("lock_until")),
            "location": s.get("location"), "last_ok_at": iso(s.get("last_ok_at")), "last_full_at": iso(s.get("last_full_at")),
            "step": scan.get("step"), "pages": scan.get("pages") or {},
            "counts": s.get("counts") or {}, "coverage": s.get("coverage") or {},
            "rejected": [{"type": r["type"], "reason": r["reason"]} for r in (s.get("rejected") or [])[-20:]],
            "error": {"code": err.get("code"), "message": err.get("message")} if err else None,
            "in_hub": {"pipelines": pipes, "contacts": contacts, "opportunities": deals},
            "every_minutes": SYNC_MINUTES, "disconnected_at": iso(s.get("disconnected_at"))}


async def on_disconnect(ws: str) -> None:
    await db().ext_sync.update_one({"_id": ws}, {"$set": {"state": "disconnected", "disconnected_at": now(), "lock_until": None},
                                                 "$unset": {"scan": ""}}, upsert=True)


async def on_connect(ws: str) -> None:
    """Saved again: syncing resumes, nothing is purged."""
    await db().ext_sync.update_one({"_id": ws, "disconnected_at": {"$exists": True}},      # a running import keeps its lock
                                   {"$unset": {"disconnected_at": "", "lock_until": ""}, "$set": {"state": "idle"}})


async def purge(ws: str) -> dict:
    """The imported data of a disconnected business: deals and pipelines removed; contacts the import made removed unless the
    hub has history on them (then kept as the hub's own); contacts that were already in the hub just lose the link."""
    deals = (await db().ext_deals.delete_many({"ws": ws})).deleted_count
    pipes = (await db().ext_pipelines.delete_many({"ws": ws})).deleted_count
    removed = 0
    async for c in db().contacts.find({"ws": ws, "ext.src": "employz"}, {"ext.created": 1}):
        used = await db().crm_activities.count_documents({"ws": ws, "contact_id": c["_id"]}, limit=1) or \
            await db().leads.count_documents({"owner_id": ws, "contact_id": c["_id"]}, limit=1)
        if (c.get("ext") or {}).get("created") and not used:
            await db().contacts.delete_one({"_id": c["_id"]})
            removed += 1
        else:
            await db().contacts.update_one({"_id": c["_id"]}, {"$unset": {"ext": ""}})
    await db().ext_sync.delete_one({"_id": ws})
    return {"opportunities": deals, "pipelines": pipes, "contacts_removed": removed}


def _due(s: dict, at: datetime) -> bool:
    if s.get("disconnected_at") or (s.get("lock_until") and _aware(s["lock_until"]) > at):
        return False
    if s.get("state") == "needs_attention":
        return False                         # waits for the owner to save the connection again
    if s.get("state") == "failed" or s.get("scan"):
        last = _aware(s.get("last_error", {}).get("at") if isinstance(s.get("last_error"), dict) else None) or _aware(s.get("run_started_at"))
        return not last or at - last >= timedelta(minutes=RETRY_MINUTES)
    last = _aware(s.get("last_ok_at"))
    return not last or at - last >= timedelta(minutes=SYNC_MINUTES)


async def tick(at: datetime | None = None, wait: bool = False) -> int:
    """The scheduler's minute: start the syncs that are due (a few at a time per worker) and purge old disconnections."""
    at = at or now()
    started = 0
    for s in [x async for x in db().ext_sync.find({"disconnected_at": {"$lt": at - timedelta(days=PURGE_DAYS)}}, {"_id": 1}).limit(50)]:
        await purge(s["_id"])
    async for doc in db().connections.find({"employz.location_id": {"$exists": True, "$ne": ""}}, {"_id": 1}).limit(2000):
        ws = doc["_id"]
        if ws in _running or len(_running) >= 3:
            continue
        s = await db().ext_sync.find_one({"_id": ws}) or {}
        if not _due(s, at):
            continue
        owner = await db().users.find_one({"_id": ws})
        if not owner or owner.get("disabled") or not plans.has(owner, "crm_sync"):
            continue
        started += 1
        if wait:
            await run(ws, "scheduled")
        else:
            _running.add(ws)
            task = asyncio.create_task(run(ws, "scheduled"))
            task.add_done_callback(lambda _t, w=ws: _running.discard(w))
    return started


async def ensure_indexes() -> None:
    d = db()
    await d.contacts.create_index([("ws", 1), ("ext.ids", 1)], sparse=True)
    await d.ext_pipelines.create_index([("ws", 1)])
    await d.ext_deals.create_index([("ws", 1), ("pipeline_id", 1), ("status", 1)])
    await d.ext_deals.create_index([("ws", 1), ("seen", 1)])
