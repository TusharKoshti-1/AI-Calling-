"""
app.api.v1.endpoints.voice
──────────────────────────
Voice-preview endpoint for the Settings page.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response

from app.core.logging import get_logger
from app.core.security import require_admin_api_key
from app.schemas.settings import VoicePreviewRequest
from app.services.settings_service import settings_service
from app.services.tts import tts_provider

log = get_logger(__name__)

router = APIRouter(
    tags=["voice"],
    dependencies=[Depends(require_admin_api_key)],
)


@router.post("/api/voice/preview")
async def voice_preview(body: VoicePreviewRequest) -> Response:
    voice_id = body.voice_id.strip()
    text = (body.text or "").strip()

    if not text:
        agent = settings_service.get("agent_name", "Sara")
        agency = settings_service.get("agency_name", "our agency")
        text = (
            f"Hello, I'm {agent} from {agency}. "
            f"Are you looking to invest in a property, or is this somewhere "
            f"you'd like to live?"
        )

    try:
        log.info("Voice preview: voice=%s text='%s'", voice_id[:8], text[:60])
        audio = await tts_provider.synthesize(
            text, voice_id=voice_id, encoding="mp3"
        )
        if audio:
            return Response(
                content=audio,
                media_type="audio/mpeg",
                headers={"Cache-Control": "no-store"},
            )
        return JSONResponse({"error": "TTS generation failed"}, status_code=500)
    except Exception as exc:
        log.error("Voice preview error: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)
