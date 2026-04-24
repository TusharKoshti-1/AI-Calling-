"""
app.services.tts.cartesia
─────────────────────────
Cartesia TTS adapter. Selects the right model per voice (Arabic voices
require `sonic-3`, everything else uses the configured default).

Uses a process-wide httpx.AsyncClient so we don't pay TLS handshake cost
on every TTS call — that alone shaves ~100–200 ms off reply latency on a
real-time voice path.
"""
from __future__ import annotations

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.http_client import get_cartesia_client
from app.services.tts.base import AudioEncoding

log = get_logger(__name__)

DEFAULT_VOICE_ID = "95d51f79-c397-46f9-b49a-23763d3eaa2d"  # Indian / Hinglish

# Arabic voices require sonic-3 instead of sonic-turbo
ARABIC_VOICE_IDS: frozenset[str] = frozenset({
    "002622d8-19d0-4567-a16a-f99c7397c062",  # Huda
    "fc923f89-1de5-4ddf-b93c-6da2ba63428a",  # Nour
    "f1cdfb4a-bf7d-4e83-916e-8f0802278315",  # Walid
    "664aec8a-64a4-4437-8a0b-a61aa4f51fe6",  # Hassan
    "b0aa4612-81d2-4df3-9730-3fc064754b1f",  # Khalid
})


class CartesiaProvider:
    name = "cartesia"

    def _model_for(self, voice_id: str) -> str:
        if voice_id in ARABIC_VOICE_IDS:
            return "sonic-3"
        return get_settings().cartesia_model

    async def synthesize(
        self,
        text: str,
        *,
        voice_id: str,
        encoding: AudioEncoding = "pcm_mulaw",
        sample_rate: int = 8000,
    ) -> bytes | None:
        if not text or not text.strip():
            return None

        s = get_settings()
        if not s.cartesia_api_key:
            log.error("Cartesia API key is not configured.")
            return None

        vid = voice_id or s.cartesia_voice_id or DEFAULT_VOICE_ID
        model = self._model_for(vid)

        if encoding == "mp3":
            output_format = {"container": "mp3", "encoding": "mp3", "sample_rate": 44100}
        else:
            output_format = {
                "container": "wav",
                "encoding": "pcm_mulaw",
                "sample_rate": sample_rate,
            }

        payload = {
            "model_id": model,
            "transcript": text,
            "voice": {"mode": "id", "id": vid},
            "output_format": output_format,
        }
        headers = {
            "Cartesia-Version": s.cartesia_version,
            "X-API-Key": s.cartesia_api_key,
            "Content-Type": "application/json",
        }

        log.info("TTS → model=%s voice=%s… enc=%s", model, vid[:8], encoding)
        try:
            client = get_cartesia_client()
            resp = await client.post(
                "https://api.cartesia.ai/tts/bytes",
                headers=headers,
                json=payload,
            )
            if resp.status_code == 200:
                log.info("TTS ok: %d bytes", len(resp.content))
                return resp.content
            log.error("Cartesia %s: %s", resp.status_code, resp.text[:400])
        except Exception as exc:
            log.error("Cartesia request failed: %s", exc)
        return None
