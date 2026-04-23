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
from fastapi.responses import Response

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
    audio = await call_orchestrator.get_reply_audio(sid)
    if audio:
        return Response(
            content=audio,
            media_type="audio/wav",
            headers={"Cache-Control": "no-store"},
        )
    return Response(status_code=204)


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
