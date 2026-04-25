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
    """Play an audio URL, then go through the post-reply-action endpoint.

    The post-reply-action endpoint decides what to do next:
      • If the AI's last reply ended with [END_CALL] → <Hangup/>
      • If it ended with [TRANSFER_CALL]            → <Dial> the transfer number
      • Otherwise                                    → resume <Gather> for next turn

    Doing this server-side (rather than baking <Gather> directly inside
    the audio playback step) is what fixes the "AI says goodbye but call
    keeps listening" bug — the orchestrator now gets a chance to act on
    the end_call/transfer flag AFTER the reply audio finishes playing.
    """
    s = _base_url()
    # We use <Play> followed by a <Redirect> rather than wrapping <Play>
    # inside <Gather>, because <Gather> swallows control flow and we
    # need an unambiguous post-play hook to decide hang up vs continue.
    return _wrap(
        f'  <Play>{escape(play_url)}</Play>\n'
        f'  <Redirect method="POST">{s}/webhooks/twilio/post-reply-action</Redirect>'
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


def listen_for_speech() -> str:
    """Open a fresh <Gather> for the customer's next utterance.

    Used by /post-reply-action when the call should continue (no end,
    no transfer). Kept as a separate builder so we don't accidentally
    replay any audio.
    """
    s = _base_url()
    return _wrap(
        f'  <Gather input="speech" action="{s}/webhooks/twilio/process-speech"'
        f' method="POST" speechTimeout="{SPEECH_TIMEOUT}" language="en-US"'
        f' timeout="{INPUT_TIMEOUT}">\n'
        f'    <Pause length="1"/>\n'
        f'  </Gather>\n'
        f'  <Redirect method="POST">{s}/webhooks/twilio/process-speech</Redirect>'
    )


def hangup() -> str:
    """Politely end the call. A short pause first lets the last word
    of the AI's audio actually reach the caller's ear before we cut."""
    return _wrap(
        f'  <Pause length="1"/>\n'
        f'  <Hangup/>'
    )


def transfer_call(
    transfer_number: str,
    sid: str,
    *,
    timeout_seconds: int = 25,
) -> str:
    """Dial the configured transfer number and bridge the caller in.

    If the dial fails (busy / no answer / declined), Twilio falls
    through to the next verb — we send it to /transfer-no-answer
    which plays a polite "experts are busy" line and hangs up.

    `timeout_seconds` is how long to ring the transfer target before
    giving up. 25 s is a sensible default — long enough that a human
    can pick up after a few rings, short enough that the customer
    isn't kept waiting forever in silence.
    """
    s = _base_url()
    # callerId="" tells Twilio to forward the original caller's number
    # as the caller-ID, which is usually what dispatchers want to see.
    # The action= param fires when the dial completes (either picked up
    # and ended, OR didn't pick up at all). We branch on DialCallStatus
    # in the handler.
    return _wrap(
        f'  <Dial timeout="{timeout_seconds}" '
        f'action="{s}/webhooks/twilio/transfer-status?sid={escape(sid)}" '
        f'method="POST">\n'
        f'    {escape(transfer_number)}\n'
        f'  </Dial>'
    )


def transfer_failed_message(audio_url: str) -> str:
    """When the transfer didn't connect, play a polite fallback line and hang up."""
    return _wrap(
        f'  <Play>{escape(audio_url)}</Play>\n'
        f'  <Pause length="1"/>\n'
        f'  <Hangup/>'
    )


def play_and_hangup(play_url: str) -> str:
    """Play a final audio URL and then hang up."""
    return _wrap(
        f'  <Play>{escape(play_url)}</Play>\n'
        f'  <Pause length="1"/>\n'
        f'  <Hangup/>'
    )
