"""Pluggable TTS providers."""
from app.services.tts.base import TTSProvider
from app.services.tts.cartesia import DEFAULT_VOICE_ID, CartesiaProvider

# Module-level singleton — stateless HTTP wrapper.
tts_provider: TTSProvider = CartesiaProvider()

__all__ = ["TTSProvider", "tts_provider", "DEFAULT_VOICE_ID"]
