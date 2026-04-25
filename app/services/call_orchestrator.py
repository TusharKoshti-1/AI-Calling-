"""
app.services.call_orchestrator
──────────────────────────────
Per-call state machine, multi-tenant aware.

Each call is attached to a `user_id` when `/api/call` is invoked. That
user_id is stored both in the DB (calls.user_id) and in the in-process
state map. When Twilio hits a webhook we look up the user_id from the
SID so every downstream action uses that user's settings.

Reply flow (the redesign)
─────────────────────────
The previous version tried to stream audio over a single chunked HTTP
response. Twilio's <Play> doesn't tolerate chunked transfer well —
about half the calls cut after the first sentence. The new design:

  1. /process-speech receives the customer's utterance.
  2. handle_speech() spawns a background task that streams the LLM
     reply, splits it into N speakable chunks, and synthesises each
     one to its own self-contained WAV. Chunks are stored in
     state.audio_parts as a list[bytes].
  3. Once the FIRST chunk is ready (typically ~700-1200 ms), we return
     TwiML with one <Play> per chunk URL — even chunks 2 and 3 that
     aren't ready yet are referenced by URL.
  4. Twilio fetches /reply-audio?sid=...&part=0 first; that endpoint
     waits for chunk 0 to be ready and returns it with Content-Length.
  5. While chunk 0 plays, the background task is finishing chunks 1-N.
     By the time Twilio asks for chunk 1, it's usually already there.
  6. When the customer replies (or barges in), Gather hands control
     back to /process-speech for the next turn.

This achieves the same "first audio fast" behaviour without using
HTTP chunked transfer. Each chunk is a complete WAV, so Twilio
gets a Content-Length and never cuts the call mid-sentence.

End-of-call is handled in the SAME TwiML response, not a follow-up
webhook — when the reply contains [END_CALL]/[HOT_LEAD], we use
play_chunks_then_hangup() instead of play_chunks_then_listen().
This eliminates the "AI says goodbye but call doesn't end" race.

Barge-in
────────
The Gather wrapping our <Play>s has bargeIn=true. When the customer
starts speaking mid-sentence, Twilio stops playback and posts the
detected speech to /process-speech. Short backchannel utterances
(uh-huh, yeah, mm-hmm alone) are filtered out so they don't trigger
a real reply turn.

Multi-tenant in-memory state
────────────────────────────
The `_state` map is in-process. For multi-worker deployments swap it
for Redis. The orchestrator's public API stays the same.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.core.logging import get_logger
from app.db.repositories.calls import CallsRepository
from app.db.repositories.messages import MessagesRepository
from app.services.llm import llm_registry
from app.services.llm.openai import OpenAIProvider
from app.services.settings_service import UserSettings, settings_service
from app.services.storage import storage
from app.services.telephony import twiml
from app.services.text_cleaner import CleanedReply, clean_reply
from app.services.tts import tts_provider

log = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════
# Per-call state (in-memory)
# ═══════════════════════════════════════════════════════════════
@dataclass
class CallState:
    sid: str
    user_id: str
    phone: str = ""
    history: list[dict[str, str]] = field(default_factory=list)
    started_at: str = ""

    # Cached opening-line audio. Generated once in handle_greeting and
    # served from /opening-audio. Cached so Twilio retries return
    # identical bytes.
    opening_audio: bytes | None = None

    # ── Reply pipeline (turn-scoped) ──────────────────────────
    # Reset on every customer turn.
    #
    # turn_id          monotonically increasing counter, used in audio
    #                  URLs as a cache-buster so chunk 0 of turn 5 is
    #                  never confused with chunk 0 of turn 4.
    # audio_parts      list of WAV bytes ready to be served by part.
    #                  Index = part number. Entries are filled as
    #                  TTS finishes each chunk; ready_events[i] is set
    #                  when audio_parts[i] is available.
    # ready_events     parallel list of asyncio.Event; ready_events[i]
    #                  is set the moment audio_parts[i] gets populated.
    # producer_done    Set when the background producer task has
    #                  finished. /reply-audio uses this to detect
    #                  "no more chunks coming" reliably.
    # producer_task    The background asyncio.Task itself.
    turn_id: int = 0
    audio_parts: list[bytes | None] = field(default_factory=list)
    ready_events: list[asyncio.Event] = field(default_factory=list)
    producer_done: asyncio.Event = field(default_factory=asyncio.Event)
    producer_task: asyncio.Task[None] | None = None

    # The full text of the most recent AI reply (post-cleaning).
    # Used for repetition detection on the next turn.
    pending_text: str = ""
    last_ai_reply: str = ""


class _CallStateStore:
    def __init__(self) -> None:
        self._state: dict[str, CallState] = {}

    def put(self, state: CallState) -> None:
        self._state[state.sid] = state

    def get(self, sid: str) -> CallState | None:
        return self._state.get(sid)

    def discard(self, sid: str) -> None:
        self._state.pop(sid, None)


def _spawn(coro) -> None:
    async def _runner():
        try:
            await coro
        except Exception as exc:
            log.error("Background task failed: %s", exc)
    asyncio.create_task(_runner())


# ═══════════════════════════════════════════════════════════════
# Backchannel filter (keeps barge-in feeling natural)
# ═══════════════════════════════════════════════════════════════
# When bargeIn is on, Twilio's STT picks up everything — including
# tiny acknowledgment noises ("uh-huh", "yeah", coughs). If we send
# every one of those to the LLM as a real turn, the conversation falls
# apart: AI keeps re-replying to noise.
#
# This filter discards utterances that are:
#   • shorter than 3 characters after stripping, OR
#   • match a known backchannel/filler word
#
# Real-world threshold: the SHORTEST meaningful customer turn is
# usually "yes" / "no" / "ok" — those are 2-3 chars. We let those
# through. We block "uh", "um", "hm", "mm", and pure punctuation.

_BACKCHANNELS = frozenset({
    "uh", "um", "hm", "mm", "mhm", "mmhm", "uhuh", "uhhuh",
    "ah", "oh", "eh", "huh", "haan",  # "haan" is Hindi for "yes" but very short — let it through actually
})

# Override: these short tokens ARE meaningful, don't filter them.
_MEANINGFUL_SHORT = frozenset({
    "yes", "no", "ok", "okay", "sure", "yep", "nope", "yeah",
    "haan", "nahi", "ji", "han",   # Hindi
    "naam", "nahin",
    "نعم", "لا",                   # Arabic yes/no
})


def _is_backchannel(text: str) -> bool:
    """Return True if the utterance is too tiny or filler-y to act on."""
    s = text.strip().lower()
    # Strip all non-word characters (handles "uh-huh", "uh.", "um!" etc.).
    # We compare AFTER normalisation against both the meaningful and
    # backchannel sets, so "uh-huh" → "uhhuh" → blocked.
    normalised = re.sub(r"[^\w]+", "", s)
    if not normalised:
        return True
    if normalised in _MEANINGFUL_SHORT:
        return False
    if normalised in _BACKCHANNELS:
        return True
    # Anything 1-2 chars that isn't in MEANINGFUL_SHORT is filler.
    if len(normalised) < 3:
        return True
    return False


# ═══════════════════════════════════════════════════════════════
# Sentence chunker for streaming TTS
# ═══════════════════════════════════════════════════════════════
# We split the LLM's reply into TTS-friendly chunks. Smaller chunks =
# faster first audio, but too small sounds choppy. Around 40-120 chars
# per chunk is the sweet spot — that's roughly one short sentence or
# one clause.

_SENTENCE_BOUNDARY = re.compile(r"([.!?…]+|\n+)")
_MIN_CHUNK = 35
_MAX_CHUNK = 220


def _chunk_text_for_tts(text: str) -> list[str]:
    """Split text into TTS chunks at sentence boundaries.

    Keeps the closing punctuation with each chunk so it sounds natural.
    Merges very short trailing fragments into the previous chunk so we
    don't TTS something tiny like "Ok." on its own (sounds clipped).
    """
    if not text or not text.strip():
        return []
    parts = _SENTENCE_BOUNDARY.split(text)
    # split() with a capturing group gives [text, sep, text, sep, ...]
    # Reassemble (text + sep) pairs back into chunks.
    chunks: list[str] = []
    buf = ""
    i = 0
    while i < len(parts):
        piece = parts[i] or ""
        sep = parts[i + 1] if i + 1 < len(parts) else ""
        i += 2
        candidate = (buf + piece + sep).strip()
        if not candidate:
            continue
        if len(candidate) >= _MIN_CHUNK or i >= len(parts):
            # Long enough OR we're at the end — emit and reset.
            if len(candidate) > _MAX_CHUNK:
                # Hard-split very long blobs at the nearest space so a
                # single TTS request doesn't take forever.
                while len(candidate) > _MAX_CHUNK:
                    cut = candidate.rfind(" ", _MIN_CHUNK, _MAX_CHUNK)
                    if cut < 0:
                        cut = _MAX_CHUNK
                    chunks.append(candidate[:cut].strip())
                    candidate = candidate[cut:].strip()
                if candidate:
                    chunks.append(candidate)
            else:
                chunks.append(candidate)
            buf = ""
        else:
            buf = candidate + " "
    if buf.strip():
        # Couldn't reach min length — append to previous chunk if any.
        if chunks:
            chunks[-1] = (chunks[-1] + " " + buf.strip()).strip()
        else:
            chunks.append(buf.strip())
    return chunks


# ═══════════════════════════════════════════════════════════════
# Orchestrator
# ═══════════════════════════════════════════════════════════════
class CallOrchestrator:
    def __init__(
        self,
        *,
        calls_repo: CallsRepository | None = None,
        messages_repo: MessagesRepository | None = None,
    ) -> None:
        self._calls = calls_repo or CallsRepository()
        self._messages = messages_repo or MessagesRepository()
        self._state = _CallStateStore()

    def discard_state(self, sid: str) -> None:
        """Drop in-memory state for a SID. Used by the delete endpoint."""
        self._state.discard(sid)

    # ── Lifecycle ─────────────────────────────────────────────
    async def register_outbound(
        self, sid: str, user_id: str, phone: str
    ) -> None:
        """Record a call we just placed via Twilio."""
        state = CallState(
            sid=sid,
            user_id=user_id,
            phone=phone,
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        self._state.put(state)
        us = await settings_service.for_user(user_id)
        _spawn(self._calls.upsert(sid, user_id, {
            "phone": phone, "from_number": "", "status": "ringing",
            "agent_name": us.get("agent_name"),
            "agency_name": us.get("agency_name"),
        }))

    async def _resolve_user_for_sid(self, sid: str) -> str | None:
        state = self._state.get(sid)
        if state is not None:
            return state.user_id
        return await self._calls.get_user_for_sid(sid)

    # ── Greeting / opening line ───────────────────────────────
    async def handle_greeting(
        self, sid: str, to_number: str, from_number: str
    ) -> str:
        user_id = await self._resolve_user_for_sid(sid)
        if user_id is None:
            log.warning("[%s] greeting received but no owning user found", sid)
            return twiml.hangup_clean()

        state = self._state.get(sid)
        if state is None:
            state = CallState(
                sid=sid, user_id=user_id, phone=to_number,
                started_at=datetime.now(timezone.utc).isoformat(),
            )
            self._state.put(state)

        us = await settings_service.for_user(user_id)
        _spawn(self._calls.upsert(sid, user_id, {
            "phone": to_number, "from_number": from_number,
            "status": "answered",
            "agent_name": us.get("agent_name"),
            "agency_name": us.get("agency_name"),
        }))

        from app.core.config import get_settings
        base = get_settings().base_url

        # Cached path — Twilio retried the greeting webhook (it does
        # this sometimes during connection setup). Don't regenerate.
        if state.opening_audio is not None:
            return twiml.play_then_listen(
                f"{base}/webhooks/twilio/opening-audio?sid={sid}"
            )

        opening_text = await self._generate_opening_line(sid, us)
        state.history.append({"role": "assistant", "content": opening_text})
        state.last_ai_reply = opening_text
        _spawn(self._messages.insert(
            call_sid=sid, user_id=user_id, role="ai", content=opening_text,
        ))

        audio = await tts_provider.synthesize(
            opening_text, voice_id=us.resolve_voice_id()
        )
        if audio:
            state.opening_audio = audio
        else:
            log.error("[%s] opening TTS failed", sid)

        return twiml.play_then_listen(
            f"{base}/webhooks/twilio/opening-audio?sid={sid}"
        )

    async def _generate_opening_line(self, sid: str, us: UserSettings) -> str:
        provider = llm_registry.get(us.resolve_llm_provider())
        base_prompt = us.resolve_system_prompt()
        # Mini gets confused if the prompt has multiple "system" sections.
        # Keep the directive as a SHORT user-style nudge after the prompt.
        augmented = (
            base_prompt
            + "\n\nThe phone has just connected. Say your opening line now — "
              "ONE warm sentence ending with a question. No tags."
        )
        try:
            raw = await provider.complete(
                "begin",
                history=[],
                system_prompt=augmented,
                model=us.get("openai_model") if us.resolve_llm_provider() == "openai" else us.get("groq_model"),
                api_key=us.get("openai_api_key") if us.resolve_llm_provider() == "openai" else None,
            )
        except Exception as exc:
            log.error("[%s] opening LLM error: %s", sid, exc)
            agent = us.get("agent_name", "Sara")
            agency = (us.get("agency_name") or "").strip()
            raw = (
                f"Hi, this is {agent} calling from {agency} — is this a good time to talk?"
                if agency else
                f"Hi, this is {agent} calling — is this a good time to talk?"
            )
        cleaned = clean_reply(raw)
        log.info("[%s] AI opening: %s", sid, cleaned.text[:80])
        return cleaned.text

    def get_opening_audio(self, sid: str) -> bytes | None:
        state = self._state.get(sid)
        return state.opening_audio if state else None

    # ── Customer speech turn ─────────────────────────────────
    async def handle_speech(
        self, sid: str, to_number: str, from_number: str, speech: str,
    ) -> str:
        """Process one customer utterance and return TwiML for the AI's reply.

        The TwiML returned here references the audio chunks via URL.
        Twilio fetches each chunk from /reply-audio?sid=...&part=N
        as it plays them.
        """
        from app.core.config import get_settings
        base = get_settings().base_url
        t0 = datetime.now(timezone.utc)

        user_id = await self._resolve_user_for_sid(sid)
        if user_id is None:
            log.warning("[%s] process-speech: unknown SID — hanging up", sid)
            return twiml.hangup_clean()

        us = await settings_service.for_user(user_id)
        state = self._state.get(sid)
        if state is None:
            state = CallState(
                sid=sid, user_id=user_id, phone=to_number,
                started_at=datetime.now(timezone.utc).isoformat(),
            )
            self._state.put(state)
            _spawn(self._calls.upsert(sid, user_id, {
                "phone": to_number, "from_number": from_number,
                "status": "answered",
                "agent_name": us.get("agent_name"),
                "agency_name": us.get("agency_name"),
            }))

        # Empty speech → reprompt with silent gather. Twilio will end up
        # back here with another empty body if the caller stays silent;
        # silence-prompt handler has the actual "are you there?" logic.
        if not speech:
            return twiml.listen_silent()

        # Backchannel filter — short fillers shouldn't drive a new turn.
        if _is_backchannel(speech):
            log.info("[%s] backchannel ignored: %r", sid, speech)
            return twiml.listen_silent()

        # Cancel any prior reply still in flight (caller barged in
        # before the previous reply finished). This prevents the
        # outdated reply audio from continuing in the background.
        if state.producer_task is not None and not state.producer_task.done():
            state.producer_task.cancel()

        _spawn(self._messages.insert(
            call_sid=sid, user_id=user_id, role="customer", content=speech,
        ))
        state.history.append({"role": "user", "content": speech})

        # Begin a fresh turn. Bump turn_id so the audio URLs for THIS
        # turn don't collide with cached chunks from a previous turn.
        state.turn_id += 1
        state.audio_parts = []
        state.ready_events = []
        state.producer_done = asyncio.Event()

        history_snapshot = list(state.history[:-1])
        provider_name = us.resolve_llm_provider()
        state.producer_task = asyncio.create_task(
            self._produce_reply(
                sid=sid, user_id=user_id, us=us,
                provider_name=provider_name,
                customer_text=speech,
                history=history_snapshot,
                turn_id=state.turn_id,
                t0=t0,
            )
        )

        # Wait until the producer has prepared the FIRST chunk + told us
        # how many chunks there are total. Bounded wait — if the LLM is
        # slow, we time out and play a "one moment" line instead of
        # making the customer hear silence.
        try:
            await asyncio.wait_for(self._first_chunk_ready(state), timeout=12.0)
        except asyncio.TimeoutError:
            log.error("[%s] first-chunk timeout — using silence-prompt", sid)
            return twiml.listen_silent()

        # Decide TwiML shape based on the cleaned reply's flags.
        # The producer sets these on state via _commit_reply_text.
        reply_text = state.pending_text or ""
        end_call = getattr(state, "_pending_end_call", False)
        transfer_call = getattr(state, "_pending_transfer", False)

        # Build URLs for every chunk the producer has scheduled.
        # ready_events tells us how many chunks total — we URL them all
        # even if some are still being synthesised. /reply-audio?part=N
        # waits for that chunk to be ready before responding.
        urls = [
            f"{base}/webhooks/twilio/reply-audio?sid={sid}&turn={state.turn_id}&part={i}"
            for i in range(len(state.ready_events))
        ]

        log.info(
            "[%s] turn %d: %d chunks, end=%s transfer=%s reply=%r",
            sid, state.turn_id, len(urls), end_call, transfer_call,
            reply_text[:80],
        )

        if transfer_call:
            transfer_number = (us.get("transfer_number") or "").strip()
            if transfer_number:
                return twiml.play_chunks_then_transfer(
                    urls, transfer_number, sid,
                )
            log.warning("[%s] transfer requested but no number set — ending", sid)
            return twiml.play_chunks_then_hangup(urls)

        if end_call:
            return twiml.play_chunks_then_hangup(urls)

        return twiml.play_chunks_then_listen(urls)

    async def _first_chunk_ready(self, state: CallState) -> None:
        """Wait until ready_events[0] is set OR the producer signals done.

        We can't simply do `await state.ready_events[0]` because at the
        moment we're called, ready_events may still be empty (the
        producer hasn't even computed how many chunks there'll be yet).
        So we poll briefly until it shows up.
        """
        # Wait for the producer to populate ready_events at all.
        while not state.ready_events:
            if state.producer_done.is_set():
                return  # producer finished without producing chunks
            await asyncio.sleep(0.05)
        await state.ready_events[0].wait()

    # ── The producer: LLM stream → chunked TTS → state.audio_parts ──
    async def _produce_reply(
        self, *,
        sid: str, user_id: str, us: UserSettings, provider_name: str,
        customer_text: str, history: list[dict[str, str]],
        turn_id: int, t0: datetime,
    ) -> None:
        """Generate the AI reply and synthesise its audio chunks.

        Writes results into the call's `audio_parts` list and sets
        `ready_events[i]` as each chunk completes.
        """
        state = self._state.get(sid)
        if state is None or state.turn_id != turn_id:
            return  # call ended or new turn already started

        try:
            system_prompt = us.resolve_system_prompt()
            voice_id = us.resolve_voice_id()
            model = (
                us.get("openai_model") if provider_name == "openai"
                else us.get("groq_model")
            )
            api_key = us.get("openai_api_key") if provider_name == "openai" else None
            provider = llm_registry.get(provider_name)

            # Get the full reply text first. We use the streaming endpoint
            # to keep first-token-fast, but accumulate text rather than
            # streaming each chunk straight to TTS — because we need to
            # split intelligently at sentence boundaries AFTER the model
            # is done so we don't end up with weird mid-clause splits.
            full_text = await self._collect_full_reply(
                provider, customer_text, history,
                system_prompt, model, api_key,
            )

            cleaned = clean_reply(full_text)

            # Repetition guard: if mini is about to say the same thing
            # again, push it forward instead.
            if state.last_ai_reply and self._is_repeat(cleaned.text, state.last_ai_reply):
                log.warning("[%s] repetition detected — substituting wrap-up", sid)
                cleaned = clean_reply(
                    "Sorry, looks like we may be going in circles — let me have "
                    "the team follow up with you on this. Have a great day!"
                )

            self._commit_reply_text(sid, user_id, cleaned)

            # Split into TTS chunks AFTER the full text is in. Splitting
            # before adds nothing because we still have to wait for the
            # LLM to be done before we know the FULL text was clean.
            chunks = _chunk_text_for_tts(cleaned.text)
            if not chunks:
                chunks = [cleaned.text or "Thank you, have a great day!"]

            # Pre-allocate slots so we know how many parts up-front.
            for _ in chunks:
                state.audio_parts.append(None)
                state.ready_events.append(asyncio.Event())

            log.info(
                "[%s] turn %d producer: %d chunks total, first chunk = %r",
                sid, turn_id, len(chunks), chunks[0][:60],
            )

            # Synthesise chunks in PARALLEL (so chunk 1 can be ready
            # before chunk 0 finishes if Cartesia is faster on the
            # second one — uncommon but possible). We still SET each
            # ready_event individually so the consumer doesn't wait
            # for chunks it hasn't asked for yet.
            await asyncio.gather(*[
                self._synthesise_chunk(state, idx, chunks[idx], voice_id, sid)
                for idx in range(len(chunks))
            ], return_exceptions=True)

            t_total = (datetime.now(timezone.utc) - t0).total_seconds()
            ok_count = sum(1 for p in state.audio_parts if p)
            log.info(
                "[%s] turn %d done: %d/%d chunks ok, %.2fs",
                sid, turn_id, ok_count, len(chunks), t_total,
            )
        except asyncio.CancelledError:
            log.info("[%s] turn %d cancelled (barge-in)", sid, turn_id)
            raise
        except Exception as exc:
            log.error("[%s] producer crashed: %s", sid, exc)
        finally:
            # Always set ALL ready_events so the consumer doesn't hang.
            for ev in state.ready_events:
                if not ev.is_set():
                    ev.set()
            state.producer_done.set()

    async def _collect_full_reply(
        self, provider, customer_text, history, system_prompt, model, api_key,
    ) -> str:
        """Run the LLM and return the complete reply as one string.

        Prefers streaming for first-token latency, but accumulates the
        full text — we don't speak partial sentences. This keeps the
        chunking step deterministic and avoids ugly mid-clause cuts.
        """
        if isinstance(provider, OpenAIProvider):
            try:
                parts: list[str] = []
                async for piece in provider.stream_sentences(
                    customer_text, history=history,
                    system_prompt=system_prompt, model=model, api_key=api_key,
                ):
                    parts.append(piece)
                if parts:
                    return " ".join(parts)
            except Exception as exc:
                log.error("LLM stream error: %s — falling back", exc)
        # Non-streaming fallback (Groq path or stream failure)
        try:
            return await provider.complete(
                customer_text, history=history,
                system_prompt=system_prompt, model=model, api_key=api_key,
            )
        except Exception as exc:
            log.error("LLM complete error: %s", exc)
            return "Sorry, I didn't catch that — could you say it again?"

    async def _synthesise_chunk(
        self, state: CallState, idx: int, text: str, voice_id: str, sid: str,
    ) -> None:
        """TTS one chunk and store the bytes in state.audio_parts[idx]."""
        try:
            audio = await asyncio.wait_for(
                tts_provider.synthesize(text, voice_id=voice_id),
                timeout=10.0,
            )
        except asyncio.TimeoutError:
            log.error("[%s] TTS timeout on chunk %d", sid, idx)
            audio = None
        except Exception as exc:
            log.error("[%s] TTS error chunk %d: %s", sid, idx, exc)
            audio = None
        # Even on failure mark ready, otherwise consumer waits forever.
        state.audio_parts[idx] = audio
        state.ready_events[idx].set()

    @staticmethod
    def _is_repeat(new_text: str, last_text: str, *, threshold: float = 0.85) -> bool:
        """Return True if `new_text` is essentially a repeat of `last_text`.

        We do a cheap word-overlap ratio rather than a full Levenshtein
        compare — fast and good enough to catch mini's "stuck in a loop"
        mode where it parrots its own previous reply.
        """
        if not new_text or not last_text:
            return False
        new_words = set(new_text.lower().split())
        last_words = set(last_text.lower().split())
        if not new_words or not last_words:
            return False
        overlap = len(new_words & last_words) / max(len(new_words), len(last_words))
        return overlap >= threshold

    def _commit_reply_text(
        self, sid: str, user_id: str, cleaned: CleanedReply,
    ) -> None:
        """Persist reply text + control flags onto state."""
        state = self._state.get(sid)
        if state is not None:
            state.history.append({"role": "assistant", "content": cleaned.text})
            state.pending_text = cleaned.text
            state.last_ai_reply = cleaned.text
            state._pending_end_call = cleaned.end_call
            state._pending_transfer = cleaned.transfer_call
        _spawn(self._messages.insert(
            call_sid=sid, user_id=user_id, role="ai", content=cleaned.text,
        ))
        if cleaned.hot_lead:
            _spawn(self._calls.update_by_sid(sid, hot_lead=True))

    # ── Serve a specific reply-audio chunk (Twilio fetches these) ──
    async def serve_reply_chunk(
        self, sid: str, turn: int, part: int,
    ) -> bytes | None:
        """Return the WAV bytes for a specific (sid, turn, part) tuple.

        Idempotent — Twilio sometimes retries chunk fetches; we always
        return the same bytes for the same coordinates. If the chunk
        has been GC'd or never existed, returns None.
        """
        state = self._state.get(sid)
        if state is None:
            log.warning("[%s] reply-chunk: unknown SID", sid)
            return None
        if state.turn_id != turn:
            # Stale request from a previous turn. We DON'T return None
            # because Twilio may have queued the URL and is now fetching
            # it after the next turn started. Better to send silence
            # (technically empty bytes) and let the conversation flow.
            log.info("[%s] reply-chunk: stale turn %d (current %d)",
                     sid, turn, state.turn_id)
            return None
        if part < 0 or part >= len(state.ready_events):
            log.warning(
                "[%s] reply-chunk: out-of-range part %d (have %d)",
                sid, part, len(state.ready_events),
            )
            return None

        # Wait for this specific chunk to be ready. Bounded — we don't
        # want a stuck synthesise to stall Twilio forever.
        try:
            await asyncio.wait_for(state.ready_events[part].wait(), timeout=12.0)
        except asyncio.TimeoutError:
            log.error("[%s] reply-chunk %d timeout", sid, part)
            return None
        return state.audio_parts[part] if part < len(state.audio_parts) else None

    # ── Post-reply / transfer / status ───────────────────────
    async def handle_post_reply_action(self, sid: str) -> str:
        """Legacy hook — keep for back-compat. New flow doesn't use it.

        End-of-call decisions are now baked into the TwiML returned by
        handle_speech() (play_chunks_then_hangup vs play_chunks_then_listen),
        so /post-reply-action is no longer in the critical path. We
        keep the endpoint to gracefully handle any in-flight Twilio
        call still using the old TwiML pattern.
        """
        log.info("[%s] legacy post-reply-action hit — listening", sid)
        return twiml.listen_silent()

    async def handle_silence_prompt(self, sid: str) -> str:
        """Called when a Gather timed out with no speech.

        Real humans pause. But after two consecutive long silences in
        a row we end the call rather than loop forever. Twilio's
        Gather timeout is INPUT_TIMEOUT (6s by default), so two of
        those = ~12 seconds of dead air, which is too much.
        """
        state = self._state.get(sid)
        if state is None:
            return twiml.hangup_clean()
        # Track silence count on the state object.
        state._silence_count = getattr(state, "_silence_count", 0) + 1
        if state._silence_count >= 2:
            log.info("[%s] hanging up after 2 silences", sid)
            return twiml.hangup_clean()
        # First silence — try once with "are you there?".
        from app.core.config import get_settings
        base = get_settings().base_url
        us = await settings_service.for_user(state.user_id)
        prompt = "Hello — are you still there?"
        audio = await tts_provider.synthesize(prompt, voice_id=us.resolve_voice_id())
        if not audio:
            return twiml.listen_silent()
        # Cache as a one-off audio chunk and return TwiML that plays it.
        state.turn_id += 1
        state.audio_parts = [audio]
        state.ready_events = [asyncio.Event()]
        state.ready_events[0].set()
        state.producer_done.set()
        url = f"{base}/webhooks/twilio/reply-audio?sid={sid}&turn={state.turn_id}&part=0"
        return twiml.play_chunks_then_listen([url])

    async def handle_transfer_status(
        self, sid: str, dial_call_status: str,
    ) -> str:
        normalised = (dial_call_status or "").lower()
        log.info("[%s] transfer status = %s", sid, normalised)
        if normalised in ("completed", "answered"):
            return twiml.hangup_clean()

        state = self._state.get(sid)
        if state is None:
            return twiml.hangup_clean()
        us = await settings_service.for_user(state.user_id)
        apology = (
            "Looks like our experts are busy at the moment — they'll call "
            "you back as soon as they're available. Thank you for "
            "understanding, and have a great day!"
        )
        audio = await tts_provider.synthesize(apology, voice_id=us.resolve_voice_id())
        if not audio:
            return twiml.hangup_clean()

        from app.core.config import get_settings
        base = get_settings().base_url
        state.turn_id += 1
        state.audio_parts = [audio]
        state.ready_events = [asyncio.Event()]
        state.ready_events[0].set()
        state.producer_done.set()
        url = f"{base}/webhooks/twilio/reply-audio?sid={sid}&turn={state.turn_id}&part=0"
        return twiml.play_chunks_then_hangup([url])

    async def handle_call_status(
        self, sid: str, status: str, duration: int,
    ) -> None:
        log.info("[%s] Status=%s Duration=%ss", sid, status, duration)
        state = self._state.get(sid)
        transcript = ""
        if state is not None:
            us = await settings_service.for_user(state.user_id)
            agent = us.get("agent_name", "Agent")
            lines = [
                f"{agent + ' (AI)' if m['role'] == 'assistant' else 'Customer'}: {m['content']}"
                for m in state.history
            ]
            transcript = "\n".join(lines)

        if status in ("completed", "failed", "no-answer", "busy", "canceled"):
            _spawn(self._calls.finalize_by_sid(
                sid, status, duration, "", "", transcript,
            ))
            self._state.discard(sid)
        elif status == "in-progress":
            _spawn(self._calls.update_by_sid(sid, status="answered"))

    async def handle_recording_status(
        self, sid: str, recording_url: str, recording_status: str,
    ) -> None:
        if recording_status != "completed" or not recording_url or not sid:
            return
        _spawn(self._persist_recording(sid, recording_url))

    async def _persist_recording(self, sid: str, recording_url: str) -> None:
        try:
            public_url, path = await storage.upload_recording(sid, recording_url)
            target = public_url or (recording_url + ".mp3")
            await self._calls.set_recording_by_sid(sid, target, path)
        except Exception as exc:
            log.error("[%s] recording upload error: %s", sid, exc)
            try:
                await self._calls.set_recording_by_sid(sid, recording_url + ".mp3", "")
            except Exception:
                pass


call_orchestrator = CallOrchestrator()
