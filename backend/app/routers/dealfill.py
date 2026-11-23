"""
Pipeline fills itself (v2.4, feature "deal_autofill"): a conversation becomes the deal's note, next step and follow-up date. The
work is in app/deal_fill.py.

  POST /api/crm/deals/{lead_id}/conversation          {text, source "paste"|"call", call_id?} → {update}   (Call coach calls this)
  POST /api/crm/deals/{lead_id}/conversation/upload   multipart `file`: a WhatsApp "Export chat" .txt or .zip, or a voice note
  POST /api/crm/conversation                          {text, name?, phone?, email?, company?}: no deal yet → a new-deal suggestion
                                                      (or, when the phone or email is a contact with an open deal, filed there)
  GET  /api/crm/deals/{lead_id}/updates               this deal's filed conversations, the ones waiting for a tap, the setting
  GET  /api/crm/updates                               new-contact suggestions waiting for a tap (no deal yet)
  POST /api/crm/updates/{id}/apply                    {fields?: ["pstage", "value", "status", "new_deal"]} — all when left out
  POST /api/crm/updates/{id}/dismiss
  POST /api/crm/updates/{id}/undo
  GET|PUT /api/crm/dealfill/settings                  {auto_forward}: "Move the stage forward by itself" (owner)
1 AI token per conversation. Nothing here messages a customer.
"""
import io
import zipfile
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.datastructures import UploadFile

from .. import crm, deal_fill, transcribe
from ..ai import dealfill_tasks as tasks
from ..context import Ctx, feature
from ..db import db
from ..services import limit_payload, runs_status

router = APIRouter(prefix="/api/crm")
TEXT_MAX_BYTES = 5 * 1024 * 1024          # an exported chat (.txt, or the .txt inside a .zip)
ZIP_MAX_BYTES = 25 * 1024 * 1024          # an export "with media" is bigger; only its .txt is read
VOICE_MAX_BYTES = 10 * 1024 * 1024
UPLOAD_MAX = ZIP_MAX_BYTES + 256 * 1024
AUDIO_EXT = set(transcribe.EXTENSIONS.values()) | {"mp4", "mpeg", "opus", "oga"}
NO_VOICE = "Voice notes aren't switched on on this hub yet. Paste or upload the conversation as text instead."


class ConversationIn(BaseModel):
    text: str = Field(min_length=10, max_length=60_000)
    source: Literal["paste", "call"] = "paste"
    call_id: str | None = Field(default=None, max_length=40)


class NewConversationIn(BaseModel):
    text: str = Field(min_length=10, max_length=60_000)
    name: str = Field(default="", max_length=80)
    phone: str = Field(default="", max_length=24)
    email: str = Field(default="", max_length=254)
    company: str = Field(default="", max_length=120)


class ApplyIn(BaseModel):
    fields: list[Literal["pstage", "value", "status", "new_deal"]] | None = None
    name: str | None = Field(default=None, max_length=80)        # a new contact: the name (and number) the owner typed
    phone: str | None = Field(default=None, max_length=24)


class SettingsIn(BaseModel):
    auto_forward: bool


async def _deal(ctx: Ctx, lead_id: str) -> dict:
    lead = await db().leads.find_one({"_id": lead_id, "owner_id": ctx.ws})
    if not lead:
        raise HTTPException(404, "Deal not found")
    return lead


def _who(ctx: Ctx) -> str:
    return ctx.user.get("name") or ("Owner" if ctx.role == "owner" else "Team")


async def _out(ctx: Ctx, u: dict) -> dict:
    return {"update": deal_fill.view(u, await deal_fill.stage_names(ctx.ws))}


@router.post("/deals/{lead_id}/conversation")
async def add_conversation(lead_id: str, body: ConversationIn, ctx: Ctx = Depends(feature("deal_autofill"))):
    lead = await _deal(ctx, lead_id)
    if body.source == "call":
        if not body.call_id:
            raise HTTPException(400, "Say which call this is.")
        if not await db().gym_sessions.find_one({"_id": body.call_id, "ws": ctx.ws}, {"_id": 1}):
            raise HTTPException(404, "That call isn't in your Call coach.")
        done = await db().deal_updates.find_one({"ws": ctx.ws, "lead_id": lead_id, "call_id": body.call_id, "undone_at": None})
        if done:          # "Add this call to the deal" pressed twice: the same filing, no second token
            return await _out(ctx, done)
    messages = tasks.to_messages(body.text, deal_fill.today())
    u = await deal_fill.file(ctx.owner, lead, messages, body.source, ctx.actor, _who(ctx), call_id=body.call_id if body.source == "call" else None)
    return await _out(ctx, u)


def _kind(name: str, ctype: str, data: bytes) -> str:
    ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""
    if data[:4] == b"PK\x03\x04" or ext == "zip":
        return "zip"
    if ctype.startswith("audio/") or ctype == "video/webm" or ext in AUDIO_EXT:
        return "voice"
    if ext == "txt" or ctype.startswith("text/"):
        return "text"
    return "unknown"


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def chat_from_zip(data: bytes) -> str:
    """The chat inside a WhatsApp export .zip: only its .txt is read (photos and voice notes in it are never opened)."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise HTTPException(400, "That .zip file couldn't be opened. Export the chat again and upload it.")
    with zf:
        texts = [i for i in zf.infolist() if not i.is_dir() and i.filename.lower().endswith(".txt") and not i.filename.startswith("__MACOSX")
                 and not i.filename.rsplit("/", 1)[-1].startswith(".")]
        if not texts:
            raise HTTPException(400, "There's no chat (.txt file) in that .zip. In WhatsApp choose Export chat, then upload the file it makes.")
        best = sorted(texts, key=lambda i: (not i.filename.rsplit("/", 1)[-1].lower().startswith(("_chat", "whatsapp chat")), -i.file_size))[0]
        if best.file_size > TEXT_MAX_BYTES:
            raise HTTPException(413, "That chat is too long to read. Export a shorter chat, or paste the last part of it.")
        with zf.open(best) as f:
            raw = f.read(TEXT_MAX_BYTES + 1)
    if len(raw) > TEXT_MAX_BYTES:
        raise HTTPException(413, "That chat is too long to read. Export a shorter chat, or paste the last part of it.")
    return _decode(raw)


@router.post("/deals/{lead_id}/conversation/upload")
async def upload_conversation(lead_id: str, request: Request, ctx: Ctx = Depends(feature("deal_autofill"))):
    """A WhatsApp "Export chat" file (.txt, or the .zip with the chat inside), or a voice note recorded after a call."""
    lead = await _deal(ctx, lead_id)
    try:   # the size must be declared up front, so nothing big is ever read or buffered
        declared = int(request.headers.get("content-length") or "")
    except ValueError:
        raise HTTPException(411, "Please upload the file again.")
    if declared > UPLOAD_MAX:
        raise HTTPException(413, "That file is too big. A chat export without media, or a voice note under 10 MB, works best.")
    try:
        form = await request.form()
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "Please send the file again.")
    try:
        f = form.get("file")
        if not isinstance(f, UploadFile):
            raise HTTPException(400, "Choose a chat export or a voice note to upload.")
        data = await f.read(ZIP_MAX_BYTES + 1)
        name, ctype = f.filename or "upload", (f.content_type or "").split(";")[0].strip().lower()
    finally:
        await form.close()
    if not data:
        raise HTTPException(400, "That file is empty.")
    kind = _kind(name, ctype, data)
    if kind == "unknown":
        raise HTTPException(415, "Upload a WhatsApp chat export (.txt or .zip) or a voice note.")
    if kind == "voice":
        if len(data) > VOICE_MAX_BYTES:
            raise HTTPException(413, "That voice note is too big. Please keep it under 10 MB.")
        if not transcribe.available():
            raise HTTPException(503, NO_VOICE)
        if (await runs_status(ctx.owner))["left"] < 1:
            raise HTTPException(402, limit_payload(ctx.owner))
        await deal_fill.take_voice(ctx.ws)
        try:
            text = await transcribe.transcribe(data, name, ctype or "audio/webm")
        except transcribe.TranscribeError:
            raise HTTPException(502, "We couldn't read that voice note right now. No AI token was used — please try again.")
        except HTTPException as e:
            if e.status_code == 503:
                raise HTTPException(503, NO_VOICE)
            raise
        text = (text or "").strip()[:20000]
        if len(text) < 5:
            raise HTTPException(400, "We couldn't hear anything in that voice note. Try again a little closer to the phone.")
        messages = [{"day": deal_fill.today(), "who": _who(ctx), "text": text}]
        source = "voice"
    else:
        if kind == "zip":
            raw = chat_from_zip(data)
        else:
            if len(data) > TEXT_MAX_BYTES:
                raise HTTPException(413, "That chat is too long to read. Export a shorter chat, or paste the last part of it.")
            raw = _decode(data)
        messages = tasks.parse_chat(raw) or tasks.to_messages(raw[-deal_fill.MAX_TEXT:], deal_fill.today())
        if not messages:
            raise HTTPException(400, "There's nothing to read in that file.")
        source = "upload"
    u = await deal_fill.file(ctx.owner, lead, messages, source, ctx.actor, _who(ctx))
    out = await _out(ctx, u)
    if source == "voice":
        out["transcript"] = text[:4000]
    return out


@router.post("/conversation")
async def new_conversation(body: NewConversationIn, ctx: Ctx = Depends(feature("deal_autofill"))):
    """A conversation with someone who has no deal yet: a suggestion to add them as a contact and a deal, for one tap. When the
    number or email given is already a contact with an open deal, it is filed into that deal instead."""
    phone, email = crm.norm_phone(body.phone), crm.norm_email(body.email)
    if body.phone.strip() and not phone:
        raise HTTPException(400, "Enter a valid 10-digit mobile number, or leave it empty.")
    messages = tasks.to_messages(body.text, deal_fill.today())
    contact = await crm.find_contact(ctx.ws, phone, email) if (phone or email) else None
    lead = await deal_fill.open_deal_of(ctx.ws, contact) if contact else None
    u = await deal_fill.file(ctx.owner, lead, messages, "paste", ctx.actor, _who(ctx),
                             new_contact=None if lead else {"name": body.name.strip(), "phone": phone or "", "email": email or "",
                                                            "company": body.company.strip()})
    return await _out(ctx, u)


@router.get("/deals/{lead_id}/updates")
async def deal_updates(lead_id: str, ctx: Ctx = Depends(feature("deal_autofill"))):
    await _deal(ctx, lead_id)
    names = await deal_fill.stage_names(ctx.ws)
    items = [deal_fill.view(u, names) async for u in db().deal_updates.find({"ws": ctx.ws, "lead_id": lead_id}).sort("at", -1).limit(30)]
    return {"updates": items, "pending": [u for u in items if u["state"] == "pending"], "settings": await deal_fill.settings_of(ctx.ws),
            "can_change_settings": ctx.role == "owner", "voice": transcribe.available(), "token_cost": 1}


@router.get("/updates")
async def new_deal_suggestions(ctx: Ctx = Depends(feature("deal_autofill"))):
    names = await deal_fill.stage_names(ctx.ws)
    items = [deal_fill.view(u, names) async for u in db().deal_updates.find({"ws": ctx.ws, "lead_id": None, "state": "pending"}).sort("at", -1).limit(20)]
    return {"pending": items}


@router.post("/updates/{uid}/apply")
async def apply_update(uid: str, body: ApplyIn | None = None, ctx: Ctx = Depends(feature("deal_autofill"))):
    body = body or ApplyIn()
    return {"update": await deal_fill.apply(ctx, uid, body.fields, name=body.name, phone=body.phone)}


@router.post("/updates/{uid}/dismiss")
async def dismiss_update(uid: str, ctx: Ctx = Depends(feature("deal_autofill"))):
    return {"update": await deal_fill.dismiss(ctx, uid)}


@router.post("/updates/{uid}/undo")
async def undo_update(uid: str, ctx: Ctx = Depends(feature("deal_autofill"))):
    return await deal_fill.undo(ctx, uid)


@router.get("/dealfill/settings")
async def get_settings(ctx: Ctx = Depends(feature("deal_autofill"))):
    return {**await deal_fill.settings_of(ctx.ws), "can_change": ctx.role == "owner"}


@router.put("/dealfill/settings")
async def put_settings(body: SettingsIn, ctx: Ctx = Depends(feature("deal_autofill", "owner"))):
    return {**await deal_fill.save_settings(ctx.ws, body.auto_forward, ctx.actor), "can_change": True}


async def ensure_indexes() -> None:
    await deal_fill.ensure_indexes()
