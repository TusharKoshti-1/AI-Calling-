"""
app.services.tts.base
─────────────────────
Protocol every TTS provider must implement.
"""
from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

AudioEncoding = Literal["pcm_mulaw", "mp3"]


@runtime_checkable
class TTSProvider(Protocol):
    name: str

    async def synthesize(
        self,
        text: str,
        *,
        voice_id: str,
        encoding: AudioEncoding = "pcm_mulaw",
        sample_rate: int = 8000,
    ) -> bytes | None: ...
