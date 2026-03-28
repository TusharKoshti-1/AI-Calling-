"""
app/api/v1/endpoints/audio.py
Audio serving — intro and per-call reply WAV files.
"""
from fastapi import APIRouter, Query
from fastapi.responses import Response

from app.core.state import call_store
from app.core.logging import get_logger
from app.services.ai.tts import synthesize
from app.services import settings_service

log = get_logger(__name__)
router = APIRouter(prefix="/audio", tags=["audio"])


@router.get("/intro")
async def serve_intro():
    """
    GET /audio/intro — generate intro TTS (cached after first call).
    Invalidated when agent name/agency/intro text changes.
    """
    audio = call_store.get_intro_audio()
    if not audio:
        text  = settings_service.get_intro_text()
        log.info(f"Generating intro audio: {text[:80]}")
        audio = await synthesize(text)
        if audio:
            call_store.set_intro_audio(audio)

    if audio:
        return Response(
            content=audio,
            media_type="audio/wav",
            headers={"Cache-Control": "public, max-age=3600"},
        )
    log.error("Intro TTS generation failed")
    return Response(status_code=500)


@router.get("/reply")
async def serve_reply(sid: str = Query(..., description="Call SID")):
    """
    GET /audio/reply?sid=X — serve pre-generated reply for a call.
    Text was stored by process-speech webhook before Twilio fetches audio.
    """
    session = call_store.get(sid)
    if not session or not session.pending_tts:
        log.warning(f"No pending TTS for sid={sid}")
        return Response(status_code=204)   # 204 = Twilio skips gracefully

    text = session.pending_tts
    session.pending_tts = None   # consume it

    log.info(f"[{sid}] Synthesising reply: {text[:80]}")
    audio = await synthesize(text)

    if audio:
        return Response(
            content=audio,
            media_type="audio/wav",
            headers={"Cache-Control": "no-store"},
        )

    log.error(f"[{sid}] Reply TTS failed")
    return Response(status_code=204)
