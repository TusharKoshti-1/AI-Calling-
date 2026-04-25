"""
app.services.tts.elevenlabs
───────────────────────────
ElevenLabs TTS adapter. Implements the same TTSProvider Protocol as the
Cartesia adapter so callers can use either provider interchangeably.

Output format choices
─────────────────────
ElevenLabs supports many output formats. We pick:

  • "ulaw_8000" for the call audio path (Twilio <Play> with raw mu-law
    8 kHz wraps cleanly into the WAV envelope our orchestrator sends to
    Twilio). This matches Cartesia's output exactly so downstream code
    doesn't need to know which provider produced the audio.

  • "mp3_44100_128" for the dashboard preview path (the browser's
    <audio> element handles MP3 natively).

The "container=wav" wrapping happens server-side: ElevenLabs returns
raw mu-law bytes; we write a 44-byte WAV header on top in
_wrap_mulaw_in_wav() so the result is a valid WAV file with
Content-Length, just like Cartesia returns.

Model choice
────────────
`eleven_flash_v2_5` is ElevenLabs' lowest-latency model (sub-300ms),
phone-call optimised, and supports 32 languages including good Hindi
and Indian English. It's also half the credit cost of the multilingual
v2 model, which matters because each character billed counts against
your Starter plan's 30k/month credit pool.

If you want fuller-quality voice (richer prosody, slightly more natural
inflection) at higher latency and 2x cost, override `elevenlabs_model`
to `eleven_multilingual_v2` via env var.
"""
from __future__ import annotations

import struct

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.http_client import get_elevenlabs_client
from app.services.tts.base import AudioEncoding

log = get_logger(__name__)

# Default voice if no per-user voice_id is set and no env override either.
# UgBBYS2sOqTuMpoF3BR0 is Twilio's published default voice for en-US in
# their ConversationRelay docs — a warm professional female voice that
# works well for outbound calling. Source:
# https://www.twilio.com/docs/voice/conversationrelay/voice-configuration
DEFAULT_VOICE_ID = "UgBBYS2sOqTuMpoF3BR0"


def _wrap_mulaw_in_wav(mulaw_bytes: bytes, sample_rate: int = 8000) -> bytes:
    """Wrap raw mu-law audio in a minimal WAV container.

    Twilio's <Play> verb plays raw bytes from a URL but expects the
    response to look like a real audio file. Cartesia returns a WAV
    container with mu-law inside it; ElevenLabs gives us raw mu-law.
    Adding the 44-byte WAV header ourselves makes the two providers'
    outputs interchangeable for downstream code.

    The header is for: PCM mu-law (audio format 7), mono, 8 kHz, 8-bit.
    """
    if not mulaw_bytes:
        return b""

    data_size = len(mulaw_bytes)
    file_size = 36 + data_size  # 44-byte header minus the 8-byte RIFF prefix

    # RIFF chunk descriptor
    header = b"RIFF"
    header += struct.pack("<I", file_size)
    header += b"WAVE"

    # fmt sub-chunk: format=7 (mu-law), channels=1, samples=8000Hz,
    # bytes-per-sec=8000, block_align=1, bits=8
    header += b"fmt "
    header += struct.pack("<I", 16)        # Subchunk1Size
    header += struct.pack("<H", 7)         # AudioFormat = mu-law
    header += struct.pack("<H", 1)         # NumChannels = mono
    header += struct.pack("<I", sample_rate)
    header += struct.pack("<I", sample_rate)  # ByteRate
    header += struct.pack("<H", 1)         # BlockAlign
    header += struct.pack("<H", 8)         # BitsPerSample

    # data sub-chunk
    header += b"data"
    header += struct.pack("<I", data_size)

    return header + mulaw_bytes


class ElevenLabsProvider:
    name = "elevenlabs"

    async def synthesize(
        self,
        text: str,
        *,
        voice_id: str,
        encoding: AudioEncoding = "pcm_mulaw",
        sample_rate: int = 8000,
    ) -> bytes | None:
        """Synthesise `text` to bytes using ElevenLabs.

        Returns audio bytes ready to serve directly to Twilio's <Play>
        (when encoding="pcm_mulaw") or to a browser <audio> element
        (when encoding="mp3"). Returns None on any failure — the caller
        is expected to handle a None and either retry or play a
        fallback.
        """
        if not text or not text.strip():
            return None

        s = get_settings()
        if not s.elevenlabs_api_key:
            log.error(
                "ElevenLabs API key is not configured. "
                "Set ELEVENLABS_API_KEY in your environment."
            )
            return None

        vid = voice_id or s.elevenlabs_voice_id or DEFAULT_VOICE_ID

        # Output-format selection. ElevenLabs uses URL query strings for
        # this rather than a JSON body field — so it goes in the URL.
        # ulaw_8000 = raw mu-law 8 kHz, ideal for telephony (Twilio).
        # mp3_44100_128 = standard streaming MP3, ideal for browser preview.
        if encoding == "mp3":
            output_format = "mp3_44100_128"
            content_type = "audio/mpeg"
        else:
            output_format = "ulaw_8000"
            content_type = "audio/basic"  # mu-law

        url = (
            f"https://api.elevenlabs.io/v1/text-to-speech/{vid}"
            f"?output_format={output_format}"
        )
        payload = {
            "text": text,
            "model_id": s.elevenlabs_model,
            # voice_settings: defaults are fine for most voices. We could
            # expose stability/similarity/speed per-voice but the picker
            # currently uses one set of defaults, which sounds natural.
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
                "style": 0.0,
                "use_speaker_boost": True,
            },
        }
        headers = {
            "xi-api-key": s.elevenlabs_api_key,
            "Content-Type": "application/json",
            "Accept": content_type,
        }

        log.info("TTS → eleven model=%s voice=%s… enc=%s",
                 s.elevenlabs_model, vid[:8], encoding)
        try:
            client = get_elevenlabs_client()
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code == 200:
                if encoding == "mp3":
                    log.info("TTS ok (eleven mp3): %d bytes", len(resp.content))
                    return resp.content
                # mu-law → wrap in WAV so Twilio's <Play> sees a valid file
                wav = _wrap_mulaw_in_wav(resp.content, sample_rate=sample_rate)
                log.info(
                    "TTS ok (eleven mulaw): %d raw → %d wav",
                    len(resp.content), len(wav),
                )
                return wav
            # Body has the JSON error from ElevenLabs — useful for debugging.
            log.error(
                "ElevenLabs %s: %s",
                resp.status_code, resp.text[:400],
            )
        except Exception as exc:
            log.error("ElevenLabs request failed: %s", exc)
        return None
