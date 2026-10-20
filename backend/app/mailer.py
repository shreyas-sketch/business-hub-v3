"""
Email to customers (app/mailer.py) — payment follow-ups, proposals and invoices (v2.1, decision D4: Amazon SES in Mumbai, ap-south-1).

- From: "<Business name>" <hub@EMAIL_FROM_DOMAIN> (the domain needs SPF, DKIM and DMARC). Reply-To: the owner's own email,
  plus the hub's reply address when inbound email is set up, so a customer's reply reaches the owner and stops reminders.
- Every email is logged in `email_log`; delivered / bounced / complained come back from SES through SNS
  (/api/hooks/email-events/<secret>). A bounced or complaining address is never emailed again by that owner.
- Limits: EMAILS_PER_OWNER_PER_DAY (300) per owner per India day, and the admin can pause one owner's automatic emails.
- EMAIL_PROVIDER=mock: written to the `outbox` collection instead of sent (only on a laptop or in tests; a real deployment
  with no provider sends nothing and says so).

SES is called directly over HTTPS with AWS Signature Version 4 (no SDK needed).
"""
import base64
import hashlib
import hmac
import json
import logging
import re
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from urllib.parse import quote, urlsplit

import httpx
from pymongo.errors import DuplicateKeyError

from .config import settings
from .db import IST, db, now
from .services import new_id

log = logging.getLogger("email")
EMAIL = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def valid(address: str | None) -> bool:
    return bool(address and EMAIL.match(address.strip()) and len(address) <= 254)


def configured() -> bool:
    return settings.email_provider == "ses" and bool(settings.aws_access_key_id and settings.aws_secret_access_key)


# ───────────────────────── AWS Signature Version 4 ─────────────────────────
def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def sigv4(method: str, url: str, headers: dict, body: bytes, *, region: str, service: str, access_key: str, secret_key: str,
          at: datetime) -> dict:
    """Returns the headers to send: the ones given plus host, x-amz-date and Authorization."""
    parts = urlsplit(url)
    amz_date, day = at.strftime("%Y%m%dT%H%M%SZ"), at.strftime("%Y%m%d")
    signed_headers = {k.lower(): str(v).strip() for k, v in headers.items()}
    signed_headers.update(host=parts.netloc, **{"x-amz-date": amz_date})
    names = sorted(signed_headers)
    query = "&".join(f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in sorted(
        (kv.split("=", 1) + [""])[:2] for kv in parts.query.split("&") if kv)) if parts.query else ""
    canonical = "\n".join([method, quote(parts.path or "/", safe="/-_.~"), query,
                           "".join(f"{n}:{signed_headers[n]}\n" for n in names), ";".join(names), hashlib.sha256(body).hexdigest()])
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    key = _hmac(_hmac(_hmac(_hmac(("AWS4" + secret_key).encode(), day), region), service), "aws4_request")
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return {**headers, "host": parts.netloc, "x-amz-date": amz_date,
            "Authorization": f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={';'.join(names)}, Signature={signature}"}


def http_client() -> httpx.AsyncClient:
    """One place to build the HTTP client, so tests can put a fake SES behind it."""
    return httpx.AsyncClient(timeout=20)


async def ses(method: str, path: str, payload: dict | None = None) -> dict:
    url = f"https://email.{settings.aws_region}.amazonaws.com{path}"
    body = json.dumps(payload).encode() if payload is not None else b""
    headers = sigv4(method, url, {"content-type": "application/json"}, body, region=settings.aws_region, service="ses",
                    access_key=settings.aws_access_key_id, secret_key=settings.aws_secret_access_key, at=datetime.now(timezone.utc))
    async with http_client() as client:
        r = await client.request(method, url, content=body, headers=headers)
    if r.status_code >= 300:
        raise RuntimeError(f"SES {r.status_code}: {r.text[:200]}")
    return r.json() if r.content else {}


# ───────────────────────── an inbound email stored in S3 by SES ─────────────────────────
class TooLarge(Exception):
    pass


S3_KEY = re.compile(r"^[A-Za-z0-9!_.*'()/+=-]{1,1024}$")


async def s3_get(bucket: str, key: str) -> bytes:
    """One stored email from the inbound bucket (SES's S3 action), read with the hub's AWS key (it needs s3:GetObject on that
    bucket only). Refuses a key with odd characters and anything over EMAIL_INBOUND_MAX_MB."""
    if not S3_KEY.match(key or ""):
        raise ValueError("unexpected S3 key")
    region = settings.email_inbound_bucket_region or settings.aws_region
    # a bucket name with dots doesn't match S3's wildcard certificate as a subdomain: those are read path-style
    url = f"https://s3.{region}.amazonaws.com/{bucket}/{key}" if "." in bucket else f"https://{bucket}.s3.{region}.amazonaws.com/{key}"
    empty = hashlib.sha256(b"").hexdigest()
    headers = sigv4("GET", url, {"x-amz-content-sha256": empty}, b"", region=region, service="s3",
                    access_key=settings.aws_access_key_id, secret_key=settings.aws_secret_access_key, at=datetime.now(timezone.utc))
    limit = settings.email_inbound_max_mb * 1024 * 1024
    async with http_client() as client:
        async with client.stream("GET", url, headers=headers) as r:
            if r.status_code != 200:
                raise RuntimeError(f"S3 {r.status_code}")
            if int(r.headers.get("content-length") or 0) > limit:
                raise TooLarge()
            body = bytearray()
            async for chunk in r.aiter_bytes():
                body += chunk
                if len(body) > limit:
                    raise TooLarge()
    return bytes(body)


# ───────────────────────── building a message ─────────────────────────
def build(*, from_name: str, to: str, subject: str, text: str, html: str | None, reply_to: list[str],
          attachments: list[tuple[str, bytes, str]] | None, headers: dict | None = None) -> EmailMessage:
    m = EmailMessage()
    m["From"] = formataddr((re.sub(r"[\r\n<>\"]", "", from_name)[:60] or "Business AI", f"hub@{settings.email_from_domain}"))
    m["To"] = to
    m["Subject"] = re.sub(r"[\r\n]", " ", subject)[:180]
    if reply_to:
        m["Reply-To"] = ", ".join(reply_to)
    m["Message-ID"] = make_msgid(domain=settings.email_from_domain)
    m["Auto-Submitted"] = "auto-generated"     # RFC 3834: out-of-office robots don't answer the hub's mail
    for k, v in (headers or {}).items():
        m[k] = v
    m.set_content(text)
    if html:
        m.add_alternative(html, subtype="html")
    for name, data, mime in attachments or []:
        main, sub = mime.split("/", 1)
        m.add_attachment(data, maintype=main, subtype=sub, filename=name)
    return m


# ───────────────────────── sending ─────────────────────────
def _day() -> str:
    return now().astimezone(IST).strftime("%Y-%m-%d")


async def _take_slot(owner_id: str) -> bool:
    key = f"{owner_id}:{_day()}"
    try:
        await db().email_counters.update_one({"_id": key}, {"$setOnInsert": {"n": 0, "at": now()}}, upsert=True)
    except DuplicateKeyError:
        pass
    return bool(await db().email_counters.find_one_and_update({"_id": key, "n": {"$lt": settings.emails_per_owner_per_day}}, {"$inc": {"n": 1}}))


async def suppressed(owner_id: str, address: str) -> bool:
    return bool(await db().email_suppress.find_one({"_id": f"{owner_id}:{address.strip().lower()}"}))


INBOUND_READY = True    # replies to r+<thread>@INBOUND_EMAIL_DOMAIN are read by routers/inquiries.py (/api/hooks/email-in)


def reply_address(token: str) -> str | None:
    """The hub's own reply address for one thread, when inbound email is set up and read (replies reach the owner and the hub).
    Until the inbound handler exists, customers reply to the owner's own email only — never to an inbox nobody reads."""
    return f"r+{token}@{settings.inbound_email_domain}" if settings.inbound_email_domain and INBOUND_READY else None


async def send(*, owner: dict, to: str, subject: str, text: str, kind: str, from_name: str, reply_to: str | None = None,
               html: str | None = None, attachments: list[tuple[str, bytes, str]] | None = None, ref: dict | None = None,
               thread: str | None = None) -> dict:
    """One email to one customer. Never raises: {"sent": bool, "via": str, "id": log id, "error"?: str}."""
    owner_id = owner["_id"]
    to = (to or "").strip()
    if not valid(to):
        return {"sent": False, "via": "no-email", "error": "No valid email address."}
    if owner.get("email_paused"):
        return {"sent": False, "via": "paused", "error": "Automatic emails are paused for this account."}
    if await suppressed(owner_id, to):
        return {"sent": False, "via": "suppressed", "error": "This address bounced or complained before, so it isn't emailed again."}
    if not configured() and not settings.local_dev:
        log.warning("email %s not sent: EMAIL_PROVIDER isn't set up", kind)
        return {"sent": False, "via": "email-not-configured", "error": "Email isn't set up on this hub yet."}
    if not await _take_slot(owner_id):
        return {"sent": False, "via": "daily-limit", "error": "Today's email limit for this account is reached."}
    thread = thread or new_id()[:16]
    replies = [a for a in (reply_to if valid(reply_to) else None, reply_address(thread)) if a]
    msg = build(from_name=from_name, to=to, subject=subject, text=text, html=html, reply_to=replies, attachments=attachments,
                headers={"X-Hub-Thread": thread})
    entry = {"_id": new_id(), "owner_id": owner_id, "to": to.lower(), "subject": msg["Subject"], "kind": kind, "ref": ref or {}, "thread": thread,
             "rfc_id": str(msg["Message-ID"]),
             "message_id": None, "status": "sending", "at": now(), "attachments": [a[0] for a in attachments or []]}
    await db().email_log.insert_one(entry)
    try:
        if configured():
            payload = {"FromEmailAddress": msg["From"], "Destination": {"ToAddresses": [to]},
                       "Content": {"Raw": {"Data": base64.b64encode(msg.as_bytes()).decode()}},
                       "EmailTags": [{"Name": "kind", "Value": re.sub(r"[^A-Za-z0-9_-]", "_", kind)[:50]}]}
            if settings.ses_configuration_set:
                payload["ConfigurationSetName"] = settings.ses_configuration_set
            out = await ses("POST", "/v2/email/outbound-emails", payload)
            message_id, via = out.get("MessageId"), "ses"
        else:
            await db().outbox.insert_one({"channel": "email", "to": to, "kind": kind, "subject": msg["Subject"], "text": text, "reply_to": replies,
                                          "from": msg["From"], "attachments": [{"name": a[0], "size": len(a[1]), "type": a[2]} for a in attachments or []],
                                          "ref": ref or {}, "thread": thread, "at": now()})
            log.info("DEV email → %s [%s] %s", to, kind, msg["Subject"])
            message_id, via = f"dev-{entry['_id']}", "dev-email"
    except Exception as e:  # noqa: BLE001
        log.warning("email %s failed: %s", kind, str(e)[:200])
        await db().email_log.update_one({"_id": entry["_id"]}, {"$set": {"status": "failed", "error": str(e)[:200]}})
        await db().email_counters.update_one({"_id": f"{owner_id}:{_day()}"}, {"$inc": {"n": -1}})
        return {"sent": False, "via": "email", "error": "The email couldn't be sent just now.", "id": entry["_id"]}
    await db().email_log.update_one({"_id": entry["_id"]}, {"$set": {"status": "sent", "message_id": message_id, "via": via}})
    return {"sent": True, "via": via, "id": entry["_id"], "thread": thread, "message_id": message_id}


# ───────────────────────── delivery events (SES → SNS) ─────────────────────────
async def record_event(event: dict) -> bool:
    """One SES event (Delivery, Bounce, Complaint…). A hard bounce or a complaint stops that owner emailing the address."""
    kind = str(event.get("eventType") or event.get("notificationType") or "").lower()
    mail = event.get("mail") if isinstance(event.get("mail"), dict) else {}
    mid = mail.get("messageId")
    if not mid:
        return False
    entry = await db().email_log.find_one({"message_id": mid})
    if not entry:
        return False
    status = {"delivery": "delivered", "bounce": "bounced", "complaint": "complained", "reject": "failed", "send": "sent",
              "open": "opened", "deliverydelay": "delayed"}.get(kind)
    if not status:
        return False
    patch = {f"{status}_at": now()}
    if status in ("delivered", "bounced", "complained", "failed") or entry.get("status") in ("sent", "delayed"):
        patch["status"] = status
    await db().email_log.update_one({"_id": entry["_id"]}, {"$set": patch})
    permanent = status == "complained" or (status == "bounced" and str((event.get("bounce") or {}).get("bounceType")) == "Permanent")
    if permanent:
        await db().email_suppress.update_one({"_id": f"{entry['owner_id']}:{entry['to']}"},
                                             {"$set": {"owner_id": entry["owner_id"], "address": entry["to"], "reason": status, "at": now()}}, upsert=True)
    return True


# ───────────────────────── SNS (event and inbound notifications) ─────────────────────────
SNS_HOST = re.compile(r"^sns\.[a-z0-9-]+\.amazonaws\.com$")
_certs: dict[str, bytes] = {}
SNS_FIELDS = {"Notification": ("Message", "MessageId", "Subject", "Timestamp", "TopicArn", "Type"),
              "SubscriptionConfirmation": ("Message", "MessageId", "SubscribeURL", "Timestamp", "Token", "TopicArn", "Type"),
              "UnsubscribeConfirmation": ("Message", "MessageId", "SubscribeURL", "Timestamp", "Token", "TopicArn", "Type")}


def sns_url_ok(url: str | None, suffix: str = "") -> bool:
    """Only Amazon SNS's own HTTPS hosts — never a URL an attacker put in a message."""
    try:
        u = urlsplit(url or "")
    except ValueError:
        return False
    return u.scheme == "https" and bool(SNS_HOST.match(u.hostname or "")) and (not suffix or u.path.endswith(suffix))


def sns_string(msg: dict) -> bytes:
    out = ""
    for k in SNS_FIELDS.get(msg.get("Type", ""), ()):
        if k in msg and msg[k] is not None:
            out += f"{k}\n{msg[k]}\n"
    return out.encode()


async def sns_verified(msg: dict) -> bool:
    """Checks the SNS message signature against Amazon's signing certificate (SignatureVersion 1 = SHA1, 2 = SHA256)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    url = msg.get("SigningCertURL") or msg.get("SigningCertUrl")
    if not sns_url_ok(url, ".pem") or msg.get("Type") not in SNS_FIELDS:
        return False
    try:
        if url not in _certs:
            async with http_client() as c:
                r = await c.get(url)
                r.raise_for_status()
                _certs[url] = r.content
        cert = x509.load_pem_x509_certificate(_certs[url])
        algo = hashes.SHA256() if str(msg.get("SignatureVersion")) == "2" else hashes.SHA1()
        cert.public_key().verify(base64.b64decode(msg.get("Signature", "")), sns_string(msg), padding.PKCS1v15(), algo)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("SNS signature check failed: %s", str(e)[:200])
        return False


async def sns_confirm(msg: dict) -> bool:
    if not sns_url_ok(msg.get("SubscribeURL")):
        return False
    async with http_client() as c:
        r = await c.get(msg["SubscribeURL"])
    return r.status_code == 200


async def rates(days: int = 30) -> list[dict]:
    """Admin → Email: per owner over the last N days — sent, bounced, complained, and the rates."""
    from datetime import timedelta
    since = now() - timedelta(days=days)
    by: dict[str, dict] = {}
    async for e in db().email_log.find({"at": {"$gte": since}, "status": {"$ne": "sending"}}, {"owner_id": 1, "status": 1}).limit(200_000):
        row = by.setdefault(e["owner_id"], {"owner_id": e["owner_id"], "sent": 0, "bounced": 0, "complained": 0, "failed": 0})
        st = e.get("status")
        if st == "failed":
            row["failed"] += 1
            continue
        row["sent"] += 1
        row["bounced"] += st == "bounced"
        row["complained"] += st == "complained"
    for row in by.values():
        row["bounce_rate"] = round(row["bounced"] / row["sent"] * 100, 2) if row["sent"] else 0
        row["complaint_rate"] = round(row["complained"] / row["sent"] * 100, 3) if row["sent"] else 0
        row["warning"] = row["bounce_rate"] >= 5 or row["complaint_rate"] >= 0.1
    return sorted(by.values(), key=lambda r: (-r["bounce_rate"], -r["sent"]))


async def health() -> dict:
    """Admin → Email: is the sending domain set up (SPF, DMARC from DNS; DKIM and verification from SES)."""
    out = {"provider": settings.email_provider, "configured": configured(), "domain": settings.email_from_domain,
           "inbound_domain": settings.inbound_email_domain or None, "spf": None, "dmarc": None, "dkim": None, "verified": None}
    try:
        import dns.asyncresolver
        txt = [b"".join(r.strings).decode() for r in await dns.asyncresolver.resolve(settings.email_from_domain, "TXT", lifetime=3)]
        out["spf"] = any(t.startswith("v=spf1") and "amazonses.com" in t for t in txt)
    except Exception:  # noqa: BLE001
        out["spf"] = False
    try:
        import dns.asyncresolver
        txt = [b"".join(r.strings).decode() for r in await dns.asyncresolver.resolve(f"_dmarc.{settings.email_from_domain}", "TXT", lifetime=3)]
        out["dmarc"] = any(t.startswith("v=DMARC1") for t in txt)
    except Exception:  # noqa: BLE001
        out["dmarc"] = False
    if configured():
        try:
            ident = await ses("GET", f"/v2/email/identities/{quote(settings.email_from_domain)}")
            out["dkim"] = (ident.get("DkimAttributes") or {}).get("Status") == "SUCCESS"
            out["verified"] = bool(ident.get("VerifiedForSendingStatus"))
        except Exception as e:  # noqa: BLE001
            out["error"] = str(e)[:200]
    return out


async def ensure_indexes() -> None:
    d = db()
    await d.email_log.create_index([("owner_id", 1), ("at", -1)])
    await d.email_log.create_index("message_id", unique=True, partialFilterExpression={"message_id": {"$type": "string"}})
    await d.email_log.create_index("thread")
    await d.email_log.create_index("rfc_id")
    await d.email_log.create_index([("owner_id", 1), ("to", 1), ("kind", 1), ("at", -1)])
