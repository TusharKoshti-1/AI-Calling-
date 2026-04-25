"""Pluggable TTS providers.

This module exports `tts_provider` — the single object that the rest of
the app imports and calls `synthesize()` on. In v12 it became a ROUTER:
it inspects the voice_id you pass in and dispatches the call to the
right backend (Cartesia or ElevenLabs).

Why route by voice ID instead of a separate provider setting
────────────────────────────────────────────────────────────
Cartesia and ElevenLabs voice IDs have completely different shapes:

  • Cartesia:    36-char UUID with dashes → "95d51f79-c397-46f9-b49a-23763d3eaa2d"
  • ElevenLabs:  20-char alphanumeric, no dashes → "UgBBYS2sOqTuMpoF3BR0"

Detecting the provider from the ID itself is unambiguous and means we
never have to keep a separate "provider" field in sync with the voice
field. Saves the user from a class of "I picked an ElevenLabs voice
but my provider setting still says Cartesia" bugs.

Per-tenant behaviour
────────────────────
Each user's saved `voice_id` setting independently determines which
provider their calls use. Switching providers is the same UX as
switching voices — just click "Use This Voice" on a voice card.
"""
from app.services.tts.base import TTSProvider
from app.services.tts.cartesia import (
    DEFAULT_VOICE_ID as CARTESIA_DEFAULT_VOICE_ID,
    CartesiaProvider,
)
from app.services.tts.elevenlabs import (
    DEFAULT_VOICE_ID as ELEVENLABS_DEFAULT_VOICE_ID,
    ElevenLabsProvider,
)

# Stateless backend instances. They share no state, so one of each is fine.
_cartesia = CartesiaProvider()
_elevenlabs = ElevenLabsProvider()


def looks_like_elevenlabs_id(voice_id: str) -> bool:
    """Return True if voice_id looks like an ElevenLabs voice ID.

    ElevenLabs IDs are 20-character alphanumeric without dashes.
    Cartesia IDs are 36-character UUIDs with dashes.

    A simple "no dashes" check is reliable because UUIDs always
    contain dashes when serialised in canonical form (8-4-4-4-12).
    """
    vid = (voice_id or "").strip()
    if not vid:
        return False
    return "-" not in vid


def provider_for(voice_id: str) -> TTSProvider:
    """Pick the TTS backend that owns this voice_id.

    Default (empty voice_id, or unrecognised shape) → Cartesia, because
    that's the historical default and what existing users had configured
    before ElevenLabs was an option. New users get an ElevenLabs default
    voice via `_defaults_from_env()` in settings_service, so they'll be
    routed to ElevenLabs naturally.
    """
    if looks_like_elevenlabs_id(voice_id):
        return _elevenlabs
    return _cartesia


class _TTSRouter:
    """Drop-in replacement for the previous `tts_provider` singleton.

    Same Protocol surface (`name` + `async synthesize(...)`), but the
    actual work gets dispatched to the right backend based on voice_id.
    The `name` of the router itself is "router" — the actual provider's
    name is logged inside synthesize().
    """

    name = "router"

    async def synthesize(
        self,
        text: str,
        *,
        voice_id: str,
        encoding: str = "pcm_mulaw",
        sample_rate: int = 8000,
    ) -> bytes | None:
        backend = provider_for(voice_id)
        return await backend.synthesize(
            text,
            voice_id=voice_id,
            encoding=encoding,
            sample_rate=sample_rate,
        )


tts_provider: TTSProvider = _TTSRouter()

# Backwards-compatible alias so existing imports of DEFAULT_VOICE_ID
# (e.g. settings_service.py) keep working. Points at the Cartesia
# default because that was the original meaning, and existing users
# may rely on it as a fallback when nothing is set.
DEFAULT_VOICE_ID = CARTESIA_DEFAULT_VOICE_ID

__all__ = [
    "TTSProvider",
    "tts_provider",
    "DEFAULT_VOICE_ID",
    "CARTESIA_DEFAULT_VOICE_ID",
    "ELEVENLABS_DEFAULT_VOICE_ID",
    "looks_like_elevenlabs_id",
    "provider_for",
]
