import logging
import httpx
from typing import Optional
from config import CARTESIA_API_KEY, CARTESIA_MODEL, CARTESIA_VERSION

log = logging.getLogger(__name__)

_HEADERS = {
    "Cartesia-Version": CARTESIA_VERSION,
    "X-API-Key":        CARTESIA_API_KEY,
    "Content-Type":     "application/json",
}

# Default voice (Indian accent — existing working voice)
DEFAULT_VOICE_ID = "95d51f79-c397-46f9-b49a-23763d3eaa2d"


async def synthesize(text: str, voice_id: str = None,
                     encoding: str = "pcm_mulaw",
                     sample_rate: int = 8000) -> Optional[bytes]:
    """
    Generate TTS audio bytes.
    voice_id: overrides default. Pass None to use runtime setting.
    encoding: pcm_mulaw (telephony, 8kHz) or mp3 (preview, browser playback)
    """
    if not text or not text.strip():
        return None

    vid = voice_id or DEFAULT_VOICE_ID

    # mp3 for browser preview, wav/mulaw for Twilio
    if encoding == "mp3":
        output_format = {
            "container":   "mp3",
            "encoding":    "mp3",
            "sample_rate": 44100,
            "bit_rate":    128000,
        }
    else:
        output_format = {
            "container":   "wav",
            "encoding":    "pcm_mulaw",
            "sample_rate": sample_rate,
        }

    payload = {
        "model_id":   CARTESIA_MODEL,
        "transcript": text,
        "voice":      {"mode": "id", "id": vid},
        "output_format": output_format,
    }

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                "https://api.cartesia.ai/tts/bytes",
                headers=_HEADERS, json=payload
            )
            if resp.status_code == 200:
                log.info(f"TTS ok: {len(resp.content)} bytes | voice={vid[:8]}...")
                return resp.content
            log.error(f"Cartesia {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        log.error(f"Cartesia error: {e}")
    return None
