"""
app.services.telephony.twiml
────────────────────────────
Typed TwiML response builders. Centralising the XML here means the API
layer never hand-concatenates strings (which is how we used to ship bugs
like `<Redirect>{url without method}</Redirect>` mismatches).
"""
from __future__ import annotations

from xml.sax.saxutils import escape

from app.core.config import get_settings

# Twilio accepts either a number of seconds or the string "auto", which
# enables Twilio's adaptive endpointing. In practice "auto" ends the turn
# a few hundred milliseconds faster on most accents than a fixed 0.5s
# timeout would — and it adapts when the customer is obviously mid-sentence
# (trailing "and…"), so we get fewer premature cutoffs too.
SPEECH_TIMEOUT = "auto"
INPUT_TIMEOUT = 5


def _wrap(body: str) -> str:
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n{body}\n</Response>'


def _base_url() -> str:
    return get_settings().base_url


def listen_with_play(play_url: str) -> str:
    """Play an audio URL, then gather speech. Fallback: go back to greeting."""
    s = _base_url()
    return _wrap(
        f'  <Gather input="speech" action="{s}/webhooks/twilio/process-speech"'
        f' method="POST" speechTimeout="{SPEECH_TIMEOUT}" language="en-US">\n'
        f'    <Play>{escape(play_url)}</Play>\n'
        f'  </Gather>\n'
        f'  <Redirect method="POST">{s}/webhooks/twilio/greeting</Redirect>'
    )


def listen_silent(pause_seconds: int = 1) -> str:
    """Gather speech with a small pause — used when no speech was captured
    so we reprompt without playing anything new."""
    s = _base_url()
    return _wrap(
        f'  <Gather input="speech" action="{s}/webhooks/twilio/process-speech"'
        f' method="POST" speechTimeout="{SPEECH_TIMEOUT}" language="en-US"'
        f' timeout="{INPUT_TIMEOUT}">\n'
        f'    <Pause length="{pause_seconds}"/>\n'
        f'  </Gather>\n'
        f'  <Redirect method="POST">{s}/webhooks/twilio/process-speech</Redirect>'
    )


def play_and_hangup(play_url: str) -> str:
    """Play a final audio URL and then hang up."""
    return _wrap(
        f'  <Play>{escape(play_url)}</Play>\n'
        f'  <Pause length="1"/>\n'
        f'  <Hangup/>'
    )
