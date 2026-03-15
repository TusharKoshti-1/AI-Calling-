import logging
import httpx
from typing import Optional
from config import CARTESIA_API_KEY, CARTESIA_VOICE_ID, CARTESIA_MODEL, CARTESIA_VERSION

log = logging.getLogger(__name__)

_HEADERS = {
    "Cartesia-Version": CARTESIA_VERSION,
    "X-API-Key":        CARTESIA_API_KEY,
    "Content-Type":     "application/json",
}

async def synthesize(text: str) -> Optional[bytes]:
    if not text or not text.strip():
        return None
    payload = {
        "model_id":   CARTESIA_MODEL,
        "transcript": text,
        "voice":      {"mode": "id", "id": CARTESIA_VOICE_ID},
        "output_format": {"container": "wav", "encoding": "pcm_mulaw", "sample_rate": 8000},
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post("https://api.cartesia.ai/tts/bytes", headers=_HEADERS, json=payload)
            if resp.status_code == 200:
                return resp.content
            log.error(f"Cartesia {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        log.error(f"Cartesia error: {e}")
    return None
