"""
app.api.v1.endpoints.twilio_webhooks
────────────────────────────────────
All Twilio-facing endpoints. Thin HTTP shell — logic lives in the
CallOrchestrator, which resolves the owning user from the call SID.

Twilio signature verification guards all mutating webhooks.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form
from fastapi.responses import Response, StreamingResponse

from app.core.logging import get_logger
from app.core.security import verify_twilio_signature
from app.services.call_orchestrator import call_orchestrator

log = get_logger(__name__)

router = APIRouter(prefix="/webhooks/twilio", tags=["twilio"])


@router.post(
    "/greeting",
    dependencies=[Depends(verify_twilio_signature)],
)
async def twiml_greeting(
    CallSid: Annotated[str | None, Form()] = None,
    To:      Annotated[str | None, Form()] = None,
    From:    Annotated[str | None, Form()] = None,
) -> Response:
    xml = await call_orchestrator.handle_greeting(
        sid=CallSid or "", to_number=To or "", from_number=From or "",
    )
    return Response(content=xml, media_type="text/xml")


@router.post(
    "/process-speech",
    dependencies=[Depends(verify_twilio_signature)],
)
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


@router.get("/opening-audio")
async def opening_audio(sid: str = "") -> Response:
    audio = call_orchestrator.get_opening_audio(sid)
    if audio:
        return Response(
            content=audio,
            media_type="audio/wav",
            headers={"Cache-Control": "public, max-age=300"},
        )
    return Response(status_code=204)


@router.get("/reply-audio")
async def reply_audio(sid: str = "") -> Response:
    """Stream the AI's reply audio to Twilio as it's produced.

    Using a StreamingResponse here (rather than assembling a full WAV
    server-side and awaiting it) is the final latency win — Twilio's
    <Play> starts reading the body as soon as the first bytes arrive,
    so the customer hears sentence 1 while the LLM is still writing
    sentence 2.
    """
    generator = await call_orchestrator.stream_reply_audio(sid)
    if generator is None:
        return Response(status_code=204)
    return StreamingResponse(
        generator,
        media_type="audio/wav",
        headers={"Cache-Control": "no-store"},
    )


@router.post(
    "/post-reply-action",
    dependencies=[Depends(verify_twilio_signature)],
)
async def post_reply_action(
    CallSid: Annotated[str | None, Form()] = None,
) -> Response:
    """Twilio hits this AFTER the AI's reply audio finishes playing.

    The orchestrator decides what to do next: hang up, transfer, or
    resume listening. See call_orchestrator.handle_post_reply_action().
    Existence of this endpoint is what fixes the "AI says goodbye but
    call doesn't end" bug — previously the <Gather> swallowed the
    end-of-call signal.
    """
    xml = await call_orchestrator.handle_post_reply_action(sid=CallSid or "")
    return Response(content=xml, media_type="text/xml")


@router.post(
    "/transfer-status",
    dependencies=[Depends(verify_twilio_signature)],
)
async def transfer_status(
    sid:             str = "",                                 # query param
    CallSid:         Annotated[str | None, Form()] = None,     # form, also fine
    DialCallStatus:  Annotated[str | None, Form()] = None,
) -> Response:
    """Twilio hits this when a <Dial> finishes (success or failure).

    DialCallStatus tells us whether the transferee actually picked up.
    On success: hang up this leg cleanly. On failure: play the polite
    "experts are busy" line and hang up.
    """
    effective_sid = sid or CallSid or ""
    xml = await call_orchestrator.handle_transfer_status(
        sid=effective_sid,
        dial_call_status=DialCallStatus or "",
    )
    return Response(content=xml, media_type="text/xml")


@router.post(
    "/recording-status",
    dependencies=[Depends(verify_twilio_signature)],
)
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


@router.post(
    "/call-status",
    dependencies=[Depends(verify_twilio_signature)],
)
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


# ── Legacy aliases for existing Twilio number configurations ──
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
        SpeechResult=SpeechResult, CallSid=CallSid, To=To, From=From
    )


@legacy_router.get("/opening-audio")
async def legacy_opening_audio(sid: str = "") -> Response:
    return await opening_audio(sid=sid)


@legacy_router.get("/reply-audio")
async def legacy_reply_audio(sid: str = "") -> Response:
    return await reply_audio(sid=sid)


@legacy_router.post("/recording-status")
async def legacy_recording_status(
    CallSid:         Annotated[str | None, Form()] = None,
    RecordingUrl:    Annotated[str | None, Form()] = None,
    RecordingStatus: Annotated[str | None, Form()] = None,
) -> Response:
    return await recording_status(
        CallSid=CallSid, RecordingUrl=RecordingUrl, RecordingStatus=RecordingStatus
    )


@legacy_router.post("/call-status")
async def legacy_call_status(
    CallSid:      Annotated[str | None, Form()] = None,
    CallStatus:   Annotated[str | None, Form()] = None,
    CallDuration: Annotated[str | None, Form()] = None,
) -> Response:
    return await call_status(
        CallSid=CallSid, CallStatus=CallStatus, CallDuration=CallDuration
    )
