import logging
import httpx
from typing import Optional
from config import CARTESIA_API_KEY, CARTESIA_MODEL

log = logging.getLogger(__name__)

# Indian accent — sonic-turbo (English/Hinglish, fastest ~40ms)
DEFAULT_VOICE_ID = "95d51f79-c397-46f9-b49a-23763d3eaa2d"

# Arabic voices — require sonic-3 (not sonic-turbo, not sonic-multilingual)
ARABIC_VOICE_IDS = {
    "002622d8-19d0-4567-a16a-f99c7397c062",  # Huda
    "fc923f89-1de5-4ddf-b93c-6da2ba63428a",  # Nour
    "f1cdfb4a-bf7d-4e83-916e-8f0802278315",  # Walid
    "664aec8a-64a4-4437-8a0b-a61aa4f51fe6",  # Hassan
    "b0aa4612-81d2-4df3-9730-3fc064754b1f",  # Khalid
}

def _model_for(voice_id: str) -> str:
    if voice_id in ARABIC_VOICE_IDS:
        return "sonic-3"    # Arabic requires sonic-3
    return CARTESIA_MODEL   # sonic-turbo for Indian/English


async def synthesize(
    text: str,
    voice_id: str = None,
    encoding: str = "pcm_mulaw",
    sample_rate: int = 8000,
) -> Optional[bytes]:
    if not text or not text.strip():
        return None

    vid   = voice_id or DEFAULT_VOICE_ID
    model = _model_for(vid)

    # Use updated API version — 2024-06-10 is old and rejects some voice IDs
    headers = {
        "Cartesia-Version": "2024-11-13",
        "X-API-Key":        CARTESIA_API_KEY,
        "Content-Type":     "application/json",
    }

    if encoding == "mp3":
        output_format = {"container": "mp3", "encoding": "mp3", "sample_rate": 44100}
    else:
        output_format = {"container": "wav", "encoding": "pcm_mulaw", "sample_rate": sample_rate}

    payload = {
        "model_id":      model,
        "transcript":    text,
        "voice":         {"mode": "id", "id": vid},
        "output_format": output_format,
    }

    log.info(f"TTS → model={model} voice={vid[:8]}... enc={encoding}")

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                "https://api.cartesia.ai/tts/bytes",
                headers=headers,
                json=payload,
            )
            if resp.status_code == 200:
                log.info(f"TTS ok: {len(resp.content)} bytes")
                return resp.content
            log.error(f"Cartesia {resp.status_code}: {resp.text}")
    except Exception as e:
        log.error(f"Cartesia error: {e}")
    return None