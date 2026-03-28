"""
app/api/v1/endpoints/webhooks.py
Twilio webhook handlers — the core call conversation loop.
These MUST never return HTTP 500 or Twilio will drop the call.
"""
from fastapi import APIRouter, Form, Query
from fastapi.responses import Response
from typing import Optional
from datetime import datetime, timezone

from app.core.config import settings
from app.core.state import call_store
from app.core.logging import get_logger
from app.services.ai.llm import get_reply
from app.services.ai.reply_parser import parse_reply
from app.services.storage.supabase import upload_recording
from app.services.telephony.twilio_client import (
    build_twiml_greeting,
    build_twiml_reply,
)
from app.services import settings_service
from db.repositories.calls import (
    upsert_call, update_call, finalize_call,
    set_recording, insert_message,
)

log = get_logger(__name__)
router = APIRouter(prefix="/webhooks/twilio", tags=["webhooks"])

BASE = settings.BASE_URL


async def _db(fn, *args, **kwargs):
    """Safe DB call — logs error but never raises (call flow must not break)."""
    try:
        return await fn(*args, **kwargs)
    except Exception as e:
        log.error(f"DB error in {fn.__name__}: {e}")


# ══════════════════════════════════════════════════════════════
# 1. GREETING — called when call is answered
# ══════════════════════════════════════════════════════════════

@router.post("/greeting")
async def twilio_greeting(started: Optional[str] = Query(None)):
    """
    Twilio calls this when the call is answered.
    - First hit (started=None): plays intro audio, gathers speech
    - Redirect loops (started=1): just gathers speech (NO intro repeat)
    """
    twiml = build_twiml_greeting(BASE, started=bool(started))
    return Response(content=twiml, media_type="text/xml")


# ══════════════════════════════════════════════════════════════
# 2. PROCESS SPEECH — main AI conversation loop
# ══════════════════════════════════════════════════════════════

@router.post("/process-speech")
async def twilio_process_speech(
    SpeechResult: Optional[str] = Form(None),
    CallSid:      Optional[str] = Form(None),
    To:           Optional[str] = Form(None),
    From:         Optional[str] = Form(None),
):
    call_sid = CallSid or ""
    to_num   = To or ""
    speech   = (SpeechResult or "").strip()
    log.info(f"[{call_sid}] SpeechResult: '{speech}'")

    # ── Init session on first exchange ────────────────────────
    session = call_store.get(call_sid)
    if not session:
        session = call_store.create(
            sid=call_sid,
            phone=to_num,
            agent_name=settings_service.get_agent_name(),
            agency_name=settings_service.get_agency_name(),
            system_prompt=settings_service.get_system_prompt(),
        )
        await _db(upsert_call, call_sid, {
            "phone":       to_num,
            "from_number": From or "",
            "status":      "answered",
            "agent_name":  session.agent_name,
            "agency_name": session.agency_name,
            "provider":    settings.TELEPHONY_PROVIDER,
        })
    else:
        await _db(update_call, call_sid, status="answered")

    customer_text = speech if speech else "hello"

    # Save customer speech to DB
    if speech:
        await _db(insert_message, call_sid, "customer", speech)

    # Update conversation history
    session.history.append({"role": "user", "content": customer_text})

    # ── Groq LLM ──────────────────────────────────────────────
    try:
        raw_reply = await get_reply(
            customer_text=customer_text,
            history=session.history[:-1],     # exclude latest user msg
            system_prompt=session.system_prompt,
            agent_name=session.agent_name,
            agency_name=session.agency_name,
        )
    except Exception as e:
        log.error(f"LLM error [{call_sid}]: {e}")
        raw_reply = (
            "Thank you for your time. Our team will follow up with you very soon. "
            "Have a wonderful day! [END_CALL]"
        )

    # ── Parse reply ───────────────────────────────────────────
    parsed = parse_reply(raw_reply)
    log.info(
        f"[{call_sid}] Reply: '{parsed.text[:80]}' "
        f"end={parsed.end_call} hot={parsed.is_hot_lead}"
    )

    # Update history with AI reply
    session.history.append({"role": "assistant", "content": parsed.text})

    # Persist AI message and flags to DB
    await _db(insert_message, call_sid, "ai", parsed.text)
    if parsed.is_hot_lead:
        await _db(update_call, call_sid, hot_lead=True)

    # Store reply text for /audio/reply to synthesise
    session.pending_tts = parsed.text

    # ── Build TwiML ───────────────────────────────────────────
    audio_url   = f"{BASE}/audio/reply?sid={call_sid}"
    process_url = f"{BASE}/webhooks/twilio/process-speech"
    greeting_url= f"{BASE}/webhooks/twilio/greeting"

    twiml = build_twiml_reply(
        audio_url=audio_url,
        process_url=process_url,
        greeting_url=greeting_url,
        end_call=parsed.end_call,
    )
    return Response(content=twiml, media_type="text/xml")


# ══════════════════════════════════════════════════════════════
# 3. RECORDING STATUS — fires when Twilio recording is ready
# ══════════════════════════════════════════════════════════════

@router.post("/recording-status")
async def twilio_recording_status(
    CallSid:         Optional[str] = Form(None),
    RecordingUrl:    Optional[str] = Form(None),
    RecordingStatus: Optional[str] = Form(None),
):
    """
    Twilio fires this SEPARATELY when recording is ready (after call ends).
    This is the correct place to upload — not in call-status.
    """
    call_sid   = CallSid         or ""
    rec_url    = RecordingUrl    or ""
    rec_status = RecordingStatus or ""

    log.info(f"[{call_sid}] RecordingStatus={rec_status} URL={rec_url}")

    if rec_status == "completed" and rec_url:
        public_url, path = await upload_recording(call_sid, rec_url)
        fallback = rec_url if rec_url.endswith(".mp3") else rec_url + ".mp3"
        await _db(set_recording, call_sid, public_url or fallback, path)

    return Response(content="OK", media_type="text/plain")


# ══════════════════════════════════════════════════════════════
# 4. CALL STATUS — fires for every call status change
# ══════════════════════════════════════════════════════════════

@router.post("/call-status")
async def twilio_call_status(
    CallSid:      Optional[str] = Form(None),
    CallStatus:   Optional[str] = Form(None),
    CallDuration: Optional[str] = Form(None),
):
    call_sid = CallSid    or ""
    status   = CallStatus or ""
    duration = int(CallDuration or 0)

    log.info(f"[{call_sid}] CallStatus={status} Duration={duration}s")

    terminal = {"completed", "failed", "no-answer", "busy", "canceled"}

    if status in terminal:
        # Build transcript from in-memory history (always available)
        session = call_store.get(call_sid)
        transcript = ""
        if session:
            lines = []
            agent = session.agent_name
            for msg in session.history:
                prefix = f"{agent} (AI)" if msg["role"] == "assistant" else "Customer"
                lines.append(f"{prefix}: {msg['content']}")
            transcript = "\n".join(lines)

        await _db(finalize_call, call_sid, status, duration, transcript)
        call_store.remove(call_sid)

    elif status == "in-progress":
        await _db(update_call, call_sid, status="answered")

    return Response(content="OK", media_type="text/plain")
