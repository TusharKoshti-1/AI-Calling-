"""
app.services.telephony.twiml
────────────────────────────
Typed TwiML response builders.

Why this module looks the way it does
─────────────────────────────────────
Twilio's <Play> verb does NOT support HTTP chunked transfer encoding.
We learned this the painful way — earlier versions used a single big
StreamingResponse and Twilio would cut roughly half the calls because
the response arrived without a Content-Length and Twilio gave up.

The cure is "chunked TwiML" not "chunked HTTP":
  • Each individual <Play> URL points at a complete WAV with a real
    Content-Length header.
  • Multiple <Play> verbs in one TwiML response queue up — Twilio
    plays them seamlessly one after the other.
  • While the customer hears chunk N, the server has time to finish
    synthesising chunk N+1.

So a "streaming reply" looks like:
    <Response>
      <Play>/reply-audio?sid=...&part=0</Play>   ← first sentence
      <Play>/reply-audio?sid=...&part=1</Play>   ← second sentence
      <Gather ...>                               ← then listen
    </Response>

Each /reply-audio?part=N call returns one self-contained WAV with
Content-Length set. Twilio is happy. Customer hears continuous speech.

Barge-in
────────
Wrapping <Play> inside <Gather input="speech"> tells Twilio to STOP
playback the moment the customer starts speaking. That's how real
voice bots feel "alive" — you can interrupt them.
"""
from __future__ import annotations

from xml.sax.saxutils import escape

from app.core.config import get_settings

# Twilio accepts either a number of seconds or the string "auto".
# "auto" enables Twilio's adaptive endpointing — it detects when the
# customer has actually finished speaking instead of waiting a flat
# half-second. In practice this saves 200-500ms per turn.
SPEECH_TIMEOUT = "auto"
# How long Gather waits for the customer to START talking before giving
# up. We use a long-ish value during normal conversation so the call
# doesn't bail out if the customer pauses to think.
INPUT_TIMEOUT = 6


def _wrap(body: str) -> str:
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n{body}\n</Response>'


def _base_url() -> str:
    return get_settings().base_url


def _gather_attrs(barge_in: bool = True) -> str:
    """Common <Gather> attributes.

    bargeIn=true tells Twilio to stop the contained <Play> the instant
    speech is detected. That's the magic that makes the bot interruptible.
    """
    s = _base_url()
    barge = "true" if barge_in else "false"
    return (
        f'input="speech" '
        f'action="{s}/webhooks/twilio/process-speech" '
        f'method="POST" '
        f'speechTimeout="{SPEECH_TIMEOUT}" '
        f'language="en-US" '
        f'timeout="{INPUT_TIMEOUT}" '
        f'bargeIn="{barge}" '
        f'profanityFilter="false"'
    )


# ── Greeting / opening ──────────────────────────────────────────
def play_then_listen(play_url: str) -> str:
    """Play one audio file, then listen for the customer's reply.

    Used for the opening line. Single <Play> wrapped in <Gather> so
    if the customer starts talking before the greeting finishes we
    catch their interruption immediately.

    The trailing <Redirect> is a safety net: if Gather times out with
    no speech (long silence), we go back to greeting rather than
    leaving the call hung in a TwiML void.
    """
    s = _base_url()
    return _wrap(
        f'  <Gather {_gather_attrs(barge_in=True)}>\n'
        f'    <Play>{escape(play_url)}</Play>\n'
        f'  </Gather>\n'
        f'  <Redirect method="POST">{s}/webhooks/twilio/silence-prompt</Redirect>'
    )


# ── Multi-chunk reply (the streaming flow) ──────────────────────
def play_chunks_then_listen(play_urls: list[str]) -> str:
    """Play a sequence of audio chunks, then listen.

    This is the workhorse of the conversational loop. Each url in
    `play_urls` is a self-contained WAV (with Content-Length). They
    play seamlessly in order; <Gather> wraps them so the customer
    can barge in at any moment.

    Why we don't use one big <Play>: chunking lets the FIRST chunk
    start playing while the LATER chunks are still being synthesised
    server-side. That's how we keep first-audio latency low without
    using HTTP chunked transfer (which Twilio doesn't support reliably
    for <Play>).
    """
    if not play_urls:
        return listen_silent()
    s = _base_url()
    plays = "\n".join(
        f'    <Play>{escape(url)}</Play>' for url in play_urls
    )
    return _wrap(
        f'  <Gather {_gather_attrs(barge_in=True)}>\n'
        f'{plays}\n'
        f'  </Gather>\n'
        f'  <Redirect method="POST">{s}/webhooks/twilio/silence-prompt</Redirect>'
    )


def play_chunks_then_hangup(play_urls: list[str]) -> str:
    """Play final audio chunks and hang up. No further input.

    Used when the AI's reply contained [END_CALL] or [HOT_LEAD] —
    we want the customer to hear the closing line in full and then
    the call ends cleanly with no awkward silence after.

    Note: bargeIn is OFF here because there's no point — even if
    the customer interrupts, we're hanging up next anyway, and a
    truncated goodbye sounds worse than a complete one.
    """
    if not play_urls:
        return hangup_clean()
    plays = "\n".join(
        f'  <Play>{escape(url)}</Play>' for url in play_urls
    )
    return _wrap(
        f'{plays}\n'
        f'  <Pause length="1"/>\n'
        f'  <Hangup/>'
    )


def play_chunks_then_transfer(
    play_urls: list[str], transfer_number: str, sid: str,
    timeout_seconds: int = 25,
) -> str:
    """Play 'connecting you now' lines, then dial a human.

    If the dial fails or no one picks up, Twilio fires the action URL
    with DialCallStatus, which then plays the polite fallback line.
    """
    s = _base_url()
    plays = "\n".join(
        f'  <Play>{escape(url)}</Play>' for url in play_urls
    )
    return _wrap(
        f'{plays}\n'
        f'  <Dial timeout="{timeout_seconds}" '
        f'action="{s}/webhooks/twilio/transfer-status?sid={escape(sid)}" '
        f'method="POST">\n'
        f'    {escape(transfer_number)}\n'
        f'  </Dial>'
    )


# ── Listening / silence handling ────────────────────────────────
def listen_silent() -> str:
    """Open a fresh <Gather> with no audio.

    Used when:
      • The customer's previous turn was empty (mis-detection).
      • A <Gather> timed out — we give them one more chance to speak.
    """
    s = _base_url()
    return _wrap(
        f'  <Gather {_gather_attrs(barge_in=False)}>\n'
        f'    <Pause length="1"/>\n'
        f'  </Gather>\n'
        f'  <Redirect method="POST">{s}/webhooks/twilio/silence-prompt</Redirect>'
    )


# ── Hangup ──────────────────────────────────────────────────────
def hangup_clean() -> str:
    """Just hang up. Pause first so any in-flight audio gets a chance
    to actually reach the caller's ear before we cut the line."""
    return _wrap(
        f'  <Pause length="1"/>\n'
        f'  <Hangup/>'
    )


# ── Legacy helpers kept for back-compat with code that still calls them ─
def listen_with_play(play_url: str) -> str:
    """DEPRECATED: legacy name. Routes to play_then_listen."""
    return play_then_listen(play_url)


def listen_for_speech() -> str:
    """DEPRECATED: legacy name. Routes to listen_silent."""
    return listen_silent()


def hangup() -> str:
    """DEPRECATED: legacy name. Routes to hangup_clean."""
    return hangup_clean()


def play_and_hangup(play_url: str) -> str:
    """DEPRECATED: legacy name. Single-chunk hangup version."""
    return play_chunks_then_hangup([play_url])


def transfer_call(transfer_number: str, sid: str, *, timeout_seconds: int = 25) -> str:
    """DEPRECATED: legacy name. Transfer with no audio prelude."""
    return play_chunks_then_transfer([], transfer_number, sid, timeout_seconds)


def transfer_failed_message(audio_url: str) -> str:
    """DEPRECATED: the new transfer-status flow uses play_chunks_then_hangup."""
    return play_chunks_then_hangup([audio_url])
