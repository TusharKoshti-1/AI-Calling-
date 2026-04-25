"""
app.api.v1.endpoints.twilio_webhooks
────────────────────────────────────
Twilio webhook surface for ConversationRelay.

Six endpoints. That's it.

  POST /webhooks/twilio/greeting
      Twilio fetches this on call-answer. We return TwiML that opens a
      ConversationRelay websocket back to us.

  WS   /webhooks/twilio/cr
      The websocket endpoint Twilio connects to. ConversationRelay
      sends us prompt/interrupt/dtmf events; we send back text tokens.
      All conversation logic lives in CRHandler.

  POST /webhooks/twilio/cr-action
      Action callback fired when the <Connect> verb ends — i.e. when
      the websocket session closes. Used to handle live-agent transfer:
      our CRHandler closes the websocket with handoffData={"reasonCode":
      "transfer", "transfer_number": "+971..."}, Twilio POSTs that here,
      we return <Dial> TwiML.

  POST /webhooks/twilio/transfer-status
      Fired after a <Dial> finishes. Distinguishes "human picked up"
      from "no answer / busy / failed" so we can play the polite
      fallback line on failure.

  POST /webhooks/twilio/recording-status
      Twilio's recording-completed callback. Triggers Supabase upload.

  POST /webhooks/twilio/call-status
      Twilio's call-lifecycle callback. Updates the calls row status.
"""
from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Form, WebSocket
from fastapi.responses import Response

from app.core.logging import get_logger
from app.core.security import verify_twilio_signature
from app.services.call_orchestrator import call_orchestrator
from app.services.cr_handler import CRHandler, compose_welcome_for
from app.services.settings_service import settings_service
from app.services.telephony import twiml as twiml_helpers

log = get_logger(__name__)

router = APIRouter(prefix="/webhooks/twilio", tags=["twilio"])


# ────────────────────────────────────────────────────────────
# 1. Greeting — Twilio fetches this on call-answer
# ────────────────────────────────────────────────────────────
@router.post("/greeting", dependencies=[Depends(verify_twilio_signature)])
async def cr_greeting(
    CallSid: Annotated[str | None, Form()] = None,
    To:      Annotated[str | None, Form()] = None,
    From:    Annotated[str | None, Form()] = None,
) -> Response:
    """Return TwiML that opens a ConversationRelay session.

    The TwiML includes a welcomeGreeting attribute which Twilio speaks
    to the customer immediately. We compute that greeting here using
    the user's settings, and the SAME function is used inside the
    websocket handler to seed conversation history — keeps both sides
    consistent so the LLM knows what was actually said to the caller.

    User resolution: outbound calls have a row in `calls` (from
    register_outbound) with the user_id; we look it up by SID. Inbound
    calls (if you wire any up later) would need a different mapping
    strategy — currently we just hang up on inbound with no user.
    """
    sid = (CallSid or "").strip()
    if not sid:
        log.warning("cr_greeting: no CallSid")
        return Response(content=twiml_helpers.hangup(), media_type="text/xml")

    user_id = await call_orchestrator.get_user_for_sid(sid)
    if not user_id:
        log.warning("[%s] cr_greeting: no owning user, hanging up", sid)
        return Response(content=twiml_helpers.hangup(), media_type="text/xml")

    us = await settings_service.for_user(user_id)
    welcome = compose_welcome_for(us)
    voice_id = us.resolve_voice_id() or "UgBBYS2sOqTuMpoF3BR0"  # ElevenLabs default

    # Hints help Deepgram correctly transcribe domain words. We pass
    # the agency name so STT recognises it instead of guessing similar
    # phonetics — important if the agency has a non-dictionary name.
    agency = (us.get("agency_name") or "").strip()
    hints = agency or ""

    xml = twiml_helpers.connect_conversation_relay(
        user_id=user_id,
        welcome_greeting=welcome,
        voice_id=voice_id,
        # ElevenLabs is the cleanest option for natural conversation;
        # voice IDs from the Cartesia world don't apply here. The voice
        # ID stored in settings is whatever the user picked in the
        # ConversationRelay voice picker on the Twilio docs page.
        tts_provider="ElevenLabs",
        # en-US is the safe default. For multi-language calls (Hindi /
        # Arabic / English mixed), the operator should set the user's
        # `language` setting to "multi" — see settings_service.
        language=us.get("language", "en-US") or "en-US",
        transcription_provider="Deepgram",
        speech_model="nova-3-general",
        hints=hints,
    )
    return Response(content=xml, media_type="text/xml")


# ────────────────────────────────────────────────────────────
# 2. The websocket itself
# ────────────────────────────────────────────────────────────
@router.websocket("/cr")
async def cr_websocket(websocket: WebSocket) -> None:
    """Twilio opens this when the <Connect><ConversationRelay> TwiML
    runs. One websocket per call.

    NOTE: Twilio strips query strings from websocket URLs, so we can't
    pass user_id via the path. Instead, the user_id arrives in the
    setup message's customParameters (configured via <Parameter> in
    the TwiML). The handler reads it from there.

    NOTE: Twilio docs recommend X-Twilio-Signature validation on the
    websocket too. FastAPI's websocket layer doesn't run our HTTP
    middleware, so we'd need a manual check. For now we trust the
    websocket URL — if you expose the server publicly without auth,
    the worst an attacker could do is run up your LLM bill by faking
    calls; they couldn't reach actual call audio. Adding signature
    validation is a TODO.
    """
    handler = CRHandler(websocket)
    await handler.run()


# ────────────────────────────────────────────────────────────
# 3. Post-relay action — fired when the websocket session ends
# ────────────────────────────────────────────────────────────
@router.post("/cr-action", dependencies=[Depends(verify_twilio_signature)])
async def cr_action(
    CallSid:        Annotated[str | None, Form()] = None,
    SessionStatus:  Annotated[str | None, Form()] = None,
    HandoffData:    Annotated[str | None, Form()] = None,
    ErrorCode:      Annotated[str | None, Form()] = None,
    ErrorMessage:   Annotated[str | None, Form()] = None,
) -> Response:
    """Twilio fires this after our <Connect><ConversationRelay> ends.

    SessionStatus is one of: ended, completed, failed.
    HandoffData is the JSON string we sent in our end-message.

    Branches:
      • Transfer requested → return <Dial> TwiML to bridge to a human.
      • Otherwise (normal end / error) → hang up.
    """
    sid = CallSid or ""
    log.info(
        "[%s] cr-action: status=%s err=%s handoff=%r",
        sid, SessionStatus, ErrorCode, HandoffData,
    )

    if HandoffData:
        try:
            data = json.loads(HandoffData)
        except json.JSONDecodeError:
            log.warning("[%s] cr-action: handoff JSON parse failed", sid)
            data = {}
        reason = data.get("reasonCode")
        if reason == "transfer":
            number = (data.get("transfer_number") or "").strip()
            if number:
                return Response(
                    content=twiml_helpers.dial_transfer_number(number, sid),
                    media_type="text/xml",
                )

    # Default: just hang up cleanly.
    return Response(content=twiml_helpers.hangup(), media_type="text/xml")


# ────────────────────────────────────────────────────────────
# 4. Transfer-status — fired after the <Dial> finishes
# ────────────────────────────────────────────────────────────
@router.post(
    "/transfer-status",
    dependencies=[Depends(verify_twilio_signature)],
)
async def transfer_status(
    sid:             str = "",
    CallSid:         Annotated[str | None, Form()] = None,
    DialCallStatus:  Annotated[str | None, Form()] = None,
) -> Response:
    effective_sid = sid or CallSid or ""
    status = (DialCallStatus or "").lower()
    log.info("[%s] transfer status = %s", effective_sid, status)
    if status in ("completed", "answered"):
        return Response(content=twiml_helpers.hangup(), media_type="text/xml")

    msg = (
        "Looks like our experts are busy at the moment — they'll call "
        "you back as soon as they're available. Thank you for "
        "understanding, and have a great day!"
    )
    return Response(
        content=twiml_helpers.transfer_failed(msg),
        media_type="text/xml",
    )


# ────────────────────────────────────────────────────────────
# 5. Recording-status callback (unchanged from v9)
# ────────────────────────────────────────────────────────────
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


# ────────────────────────────────────────────────────────────
# 6. Call-status callback (unchanged from v9)
# ────────────────────────────────────────────────────────────
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


# ────────────────────────────────────────────────────────────
# Legacy aliases — for any phone numbers still configured to hit
# old endpoints. They no-op except for keeping calls from erroring.
# ────────────────────────────────────────────────────────────
legacy_router = APIRouter(tags=["twilio-legacy"])


@legacy_router.post("/twiml-greeting")
async def legacy_greeting(
    CallSid: Annotated[str | None, Form()] = None,
    To:      Annotated[str | None, Form()] = None,
    From:    Annotated[str | None, Form()] = None,
) -> Response:
    return await cr_greeting(CallSid=CallSid, To=To, From=From)


@legacy_router.post("/recording-status")
async def legacy_recording_status(
    CallSid:         Annotated[str | None, Form()] = None,
    RecordingUrl:    Annotated[str | None, Form()] = None,
    RecordingStatus: Annotated[str | None, Form()] = None,
) -> Response:
    return await recording_status(
        CallSid=CallSid, RecordingUrl=RecordingUrl,
        RecordingStatus=RecordingStatus,
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
