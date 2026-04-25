"""
app.api.v1.endpoints.twilio_webhooks
────────────────────────────────────
Twilio webhook surface. Thin HTTP shell — all logic lives in the
CallOrchestrator.

Notable endpoints
─────────────────
GET  /reply-audio?sid=...&turn=N&part=K
    Serves ONE chunk of the AI's reply for a specific turn. Returns
    a complete WAV with Content-Length set so Twilio's <Play> can
    reliably download it. Idempotent — multiple fetches of the same
    coordinates return the same bytes (Twilio sometimes retries).

POST /silence-prompt
    Triggered when a <Gather> times out with no speech. We respond
    with "are you still there?" once, then hang up if silence
    continues — keeps the call from sitting open indefinitely.

POST /post-reply-action
    Legacy endpoint kept for any in-flight calls still using the
    earlier TwiML pattern. New flow doesn't call it.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form
from fastapi.responses import Response

from app.core.logging import get_logger
from app.core.security import verify_twilio_signature
from app.services.call_orchestrator import call_orchestrator

log = get_logger(__name__)

router = APIRouter(prefix="/webhooks/twilio", tags=["twilio"])


# ── TwiML webhooks ──────────────────────────────────────────────
@router.post("/greeting", dependencies=[Depends(verify_twilio_signature)])
async def twiml_greeting(
    CallSid: Annotated[str | None, Form()] = None,
    To:      Annotated[str | None, Form()] = None,
    From:    Annotated[str | None, Form()] = None,
) -> Response:
    xml = await call_orchestrator.handle_greeting(
        sid=CallSid or "", to_number=To or "", from_number=From or "",
    )
    return Response(content=xml, media_type="text/xml")


@router.post("/process-speech", dependencies=[Depends(verify_twilio_signature)])
async def process_speech(
    SpeechResult: Annotated[str | None, Form()] = None,
    CallSid:      Annotated[str | None, Form()] = None,
    To:           Annotated[str | None, Form()] = None,
    From:         Annotated[str | None, Form()] = None,
) -> Response:
    xml = await call_orchestrator.handle_speech(
        sid=CallSid or "", to_number=To or "", from_number=From or "",
        speech=(SpeechResult or "").strip(),
    )
    return Response(content=xml, media_type="text/xml")


@router.post("/silence-prompt", dependencies=[Depends(verify_twilio_signature)])
async def silence_prompt(
    CallSid: Annotated[str | None, Form()] = None,
) -> Response:
    """Gather timed out with no speech — handle the silence."""
    xml = await call_orchestrator.handle_silence_prompt(sid=CallSid or "")
    return Response(content=xml, media_type="text/xml")


@router.post("/post-reply-action", dependencies=[Depends(verify_twilio_signature)])
async def post_reply_action(
    CallSid: Annotated[str | None, Form()] = None,
) -> Response:
    """Legacy endpoint — kept so in-flight calls don't break on deploy."""
    xml = await call_orchestrator.handle_post_reply_action(sid=CallSid or "")
    return Response(content=xml, media_type="text/xml")


@router.post("/transfer-status", dependencies=[Depends(verify_twilio_signature)])
async def transfer_status(
    sid:             str = "",
    CallSid:         Annotated[str | None, Form()] = None,
    DialCallStatus:  Annotated[str | None, Form()] = None,
) -> Response:
    effective_sid = sid or CallSid or ""
    xml = await call_orchestrator.handle_transfer_status(
        sid=effective_sid,
        dial_call_status=DialCallStatus or "",
    )
    return Response(content=xml, media_type="text/xml")


@router.post("/recording-status", dependencies=[Depends(verify_twilio_signature)])
async def recording_status(
    CallSid:         Annotated[str | None, Form()] = None,
    RecordingUrl:    Annotated[str | None, Form()] = None,
    RecordingStatus: Annotated[str | None, Form()] = None,
) -> Response:
    await call_orchestrator.handle_recording_status(
        sid=CallSid or "",
        recording_url=RecordingUrl or "",
        recording_status=RecordingStatus or "",
    )
    return Response(content="OK", media_type="text/plain")


@router.post("/call-status", dependencies=[Depends(verify_twilio_signature)])
async def call_status(
    CallSid:      Annotated[str | None, Form()] = None,
    CallStatus:   Annotated[str | None, Form()] = None,
    CallDuration: Annotated[str | None, Form()] = None,
) -> Response:
    try:
        duration = int(CallDuration or 0)
    except ValueError:
        duration = 0
    await call_orchestrator.handle_call_status(
        sid=CallSid or "",
        status=CallStatus or "",
        duration=duration,
    )
    return Response(content="OK", media_type="text/plain")


# ── Audio endpoints (no signature check — Twilio fetches via plain GET) ──
@router.get("/opening-audio")
async def opening_audio(sid: str = "") -> Response:
    audio = call_orchestrator.get_opening_audio(sid)
    if audio is None:
        return Response(status_code=204)
    # Explicit Content-Length is what makes <Play> reliable.
    return Response(
        content=audio,
        media_type="audio/wav",
        headers={
            "Content-Length": str(len(audio)),
            "Cache-Control": "public, max-age=300",
            "Accept-Ranges": "bytes",
        },
    )


@router.get("/reply-audio")
async def reply_audio(
    sid: str = "",
    turn: int = 0,
    part: int = 0,
) -> Response:
    """Serve ONE WAV chunk (one sentence/clause) of the AI's reply.

    The orchestrator schedules N chunks per reply. Twilio's TwiML has
    one <Play> per chunk URL. Each fetch lands here with a different
    `part` number; we wait for that chunk's audio to finish synthesising
    and return it.

    Crucially we set Content-Length so Twilio doesn't have to guess
    when the body ends — that was the source of the "calls cut after
    intro" bug we had on the streaming-response version.
    """
    audio = await call_orchestrator.serve_reply_chunk(sid=sid, turn=turn, part=part)
    if audio is None:
        # Empty 204 — Twilio's <Play> handles a missing chunk by skipping
        # to the next verb instead of erroring out, so the call continues.
        return Response(status_code=204)
    return Response(
        content=audio,
        media_type="audio/wav",
        headers={
            "Content-Length": str(len(audio)),
            "Cache-Control": "no-store",
            "Accept-Ranges": "bytes",
        },
    )


# ── Legacy aliases for old Twilio number configurations ────────
legacy_router = APIRouter(tags=["twilio-legacy"])


@legacy_router.post("/twiml-greeting")
async def legacy_greeting(
    CallSid: Annotated[str | None, Form()] = None,
    To:      Annotated[str | None, Form()] = None,
    From:    Annotated[str | None, Form()] = None,
) -> Response:
    return await twiml_greeting(CallSid=CallSid, To=To, From=From)


@legacy_router.post("/process-speech")
async def legacy_process_speech(
    SpeechResult: Annotated[str | None, Form()] = None,
    CallSid:      Annotated[str | None, Form()] = None,
    To:           Annotated[str | None, Form()] = None,
    From:         Annotated[str | None, Form()] = None,
) -> Response:
    return await process_speech(
        SpeechResult=SpeechResult, CallSid=CallSid, To=To, From=From,
    )


@legacy_router.get("/opening-audio")
async def legacy_opening_audio(sid: str = "") -> Response:
    return await opening_audio(sid=sid)


@legacy_router.get("/reply-audio")
async def legacy_reply_audio(sid: str = "", turn: int = 0, part: int = 0) -> Response:
    return await reply_audio(sid=sid, turn=turn, part=part)


@legacy_router.post("/recording-status")
async def legacy_recording_status(
    CallSid:         Annotated[str | None, Form()] = None,
    RecordingUrl:    Annotated[str | None, Form()] = None,
    RecordingStatus: Annotated[str | None, Form()] = None,
) -> Response:
    return await recording_status(
        CallSid=CallSid, RecordingUrl=RecordingUrl, RecordingStatus=RecordingStatus,
    )


@legacy_router.post("/call-status")
async def legacy_call_status(
    CallSid:      Annotated[str | None, Form()] = None,
    CallStatus:   Annotated[str | None, Form()] = None,
    CallDuration: Annotated[str | None, Form()] = None,
) -> Response:
    return await call_status(
        CallSid=CallSid, CallStatus=CallStatus, CallDuration=CallDuration,
    )
