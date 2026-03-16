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

# Indian accent — default voice for Hinglish calls
DEFAULT_VOICE_ID = "95d51f79-c397-46f9-b49a-23763d3eaa2d"


async def synthesize(
    text: str,
    voice_id: str = None,
    encoding: str = "pcm_mulaw",   # "pcm_mulaw" for Twilio, "mp3" for browser preview
    sample_rate: int = 8000,
) -> Optional[bytes]:
    if not text or not text.strip():
        return None

    vid = voice_id or DEFAULT_VOICE_ID

    # Cartesia output format spec:
    # - Telephony (Twilio): container=wav, encoding=pcm_mulaw, sample_rate=8000
    # - Browser preview:    container=mp3, encoding=mp3, sample_rate=44100
    # NOTE: Cartesia mp3 format — container AND encoding must both be "mp3"
    #       bit_rate is NOT a valid field — Cartesia ignores/rejects it
    if encoding == "mp3":
        output_format = {
            "container":   "mp3",
            "encoding":    "mp3",
            "sample_rate": 44100,
        }
        media_type = "audio/mpeg"
    else:
        output_format = {
            "container":   "wav",
            "encoding":    "pcm_mulaw",
            "sample_rate": sample_rate,
        }
        media_type = "audio/wav"

    payload = {
        "model_id":      CARTESIA_MODEL,
        "transcript":    text,
        "voice":         {"mode": "id", "id": vid},
        "output_format": output_format,
    }

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                "https://api.cartesia.ai/tts/bytes",
                headers=_HEADERS,
                json=payload,
            )
            if resp.status_code == 200:
                log.info(f"TTS ok: {len(resp.content)} bytes voice={vid[:8]} enc={encoding}")
                return resp.content
            log.error(f"Cartesia {resp.status_code}: {resp.text[:300]}")
    except Exception as e:
        log.error(f"Cartesia error: {e}")
    return None
