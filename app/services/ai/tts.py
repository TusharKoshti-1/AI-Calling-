"""
app/services/ai/tts.py
Cartesia TTS — converts AI reply text to WAV audio for telephony.
Output: pcm_mulaw 8kHz WAV (Twilio/Telnyx compatible).
"""
import httpx
from typing import Optional
from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)


async def synthesize(text: str) -> Optional[bytes]:
    """
    Convert text to WAV audio bytes.
    Returns None on failure — callers must handle gracefully.
    """
    if not text or not text.strip():
        return None

    payload = {
        "model_id":   settings.CARTESIA_MODEL,
        "transcript": text,
        "voice":      {"mode": "id", "id": settings.CARTESIA_VOICE_ID},
        "output_format": {
            "container":   "wav",
            "encoding":    "pcm_mulaw",
            "sample_rate": 8000,
        },
    }
    headers = {
        "Cartesia-Version": settings.CARTESIA_VERSION,
        "X-API-Key":        settings.CARTESIA_API_KEY,
        "Content-Type":     "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.cartesia.ai/tts/bytes",
                headers=headers,
                json=payload,
            )
            if resp.status_code == 200:
                log.info(f"TTS: {len(resp.content)} bytes for '{text[:60]}'")
                return resp.content
            log.error(f"Cartesia {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        log.error(f"Cartesia error: {e}")

    return None
