"""
Files owners upload: lead magnet PDFs, compliance documents and receipts, brand logos.

Stored in the hub's own database (the `files` collection), one document per file, at most UPLOAD_MAX_MB (10 MB) — under
MongoDB's 16 MB document limit, so no extra service or account is needed (decision D6). The type is checked from the
file's first bytes, not its name. Files are only ever read back for their owner, or through a signed link that expires.
Moving to GridFS, S3 or R2 later only changes this module.
"""
import base64
import hashlib
import hmac
import time

from fastapi import HTTPException, UploadFile

from .config import settings
from .db import db, now
from .services import new_id

TYPES = {"pdf": ("application/pdf", b"%PDF-"), "png": ("image/png", b"\x89PNG\r\n\x1a\n"), "jpg": ("image/jpeg", b"\xff\xd8\xff")}
EXT = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg"}


def sniff(data: bytes) -> str | None:
    for ext, (mime, magic) in TYPES.items():
        if data.startswith(magic):
            return mime
    return None


async def read_upload(upload: UploadFile, allowed: tuple[str, ...], max_mb: int | None = None) -> tuple[bytes, str]:
    """Reads an upload, refusing anything bigger than the limit or not really one of the allowed types."""
    limit = (max_mb or settings.upload_max_mb) * 1024 * 1024
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"That file is bigger than {max_mb or settings.upload_max_mb} MB.")
    if not data:
        raise HTTPException(400, "That file is empty.")
    mime = sniff(data)
    if mime not in allowed:
        names = ", ".join(EXT[m].upper() for m in allowed)
        raise HTTPException(400, f"Upload a {names} file.")
    return data, mime


async def put(owner_id: str, data: bytes, filename: str, content_type: str, kind: str, **meta) -> dict:
    doc = {"_id": new_id(), "owner_id": owner_id, "filename": (filename or "file")[:120], "content_type": content_type, "size": len(data),
           "kind": kind, "data": data, "sha256": hashlib.sha256(data).hexdigest(), "meta": meta, "created_at": now()}
    await db().files.insert_one(doc)
    return {k: v for k, v in doc.items() if k != "data"}


async def get(file_id: str, owner_id: str | None = None) -> dict | None:
    q = {"_id": file_id}
    if owner_id is not None:
        q["owner_id"] = owner_id
    return await db().files.find_one(q)


async def delete(file_id: str, owner_id: str) -> None:
    await db().files.delete_one({"_id": file_id, "owner_id": owner_id})


def _secret() -> bytes:
    return (settings.jwt_secret or "development-only-secret-do-not-use-in-production-0000").encode()


def share_token(file_id: str, minutes: int = 60 * 24 * 7) -> str:
    """A link anyone can open until it expires (default 7 days). The file id and expiry are signed."""
    exp = int(time.time()) + minutes * 60
    msg = f"{file_id}.{exp}".encode()
    sig = base64.urlsafe_b64encode(hmac.new(_secret(), b"file:" + msg, hashlib.sha256).digest()[:18]).decode().rstrip("=")
    return f"{file_id}.{exp}.{sig}"


def check_token(token: str) -> str | None:
    try:
        file_id, exp, sig = token.split(".")
        msg = f"{file_id}.{exp}".encode()
        good = base64.urlsafe_b64encode(hmac.new(_secret(), b"file:" + msg, hashlib.sha256).digest()[:18]).decode().rstrip("=")
        if hmac.compare_digest(good, sig) and int(exp) > time.time():
            return file_id
    except (ValueError, TypeError):
        pass
    return None


def share_url(file_id: str, minutes: int = 60 * 24 * 7) -> str:
    return f"{settings.app_url}/api/files/s/{share_token(file_id, minutes)}"


async def ensure_indexes() -> None:
    await db().files.create_index([("owner_id", 1), ("kind", 1), ("created_at", -1)])
