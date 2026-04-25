"""
app.services.call_orchestrator
──────────────────────────────
Per-call state machine, multi-tenant aware.

Each call is attached to a `user_id` when `/api/call` is invoked. That
user_id is stored both in the DB (calls.user_id) and in the in-process
state map. When Twilio hits a webhook we look up the user_id from the
SID so every downstream action uses that user's settings.

Latency design — streaming everything
─────────────────────────────────────
When the customer finishes a turn we want the first syllable of the AI's
reply in the caller's ear as fast as possible. The classic approach
(await full LLM → await full TTS → hand WAV to Twilio) stacks those
latencies serially and is painful.

Instead we run three stages in a pipeline:
  1. LLM streaming completion — tokens arrive ~300 ms after the first
     fetch; we watch for sentence boundaries and emit speakable chunks.
  2. Parallel TTS — each sentence is synthesised by Cartesia concurrently
     with the LLM still generating the next sentence.
  3. Streaming HTTP response — /reply-audio is an async generator.
     It awaits the FIRST WAV chunk and flushes its header + body to
     Twilio immediately; subsequent chunks' audio bodies are appended
     live. Twilio's <Play> starts playing as soon as it has enough
     header + samples, so the caller hears sentence 1 while the LLM is
     still writing sentence 2.

Result: the customer typically hears the first word ~800–1200 ms sooner
than on a non-streaming pipeline, and the rest of the reply lands
without a visible seam.

In-call memory
──────────────
The LLM remembers everything from the CURRENT call: every customer
turn and every AI reply is appended to `state.history`, and the full
history is replayed to the LLM on every new reply. This is plain
conversation context — no DB, no extraction, no persistence.

When the call ends, `state` is discarded and the conversation memory
goes with it. There is intentionally NO cross-call memory.

Scaling:
  • The in-memory `_state` map works for one worker. To run multiple
    workers, replace _CallStateStore with a Redis implementation —
    the public orchestrator API does not change.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
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

    # Opening-line audio is generated once at the greeting webhook and
    # cached here so the /opening-audio endpoint can serve it instantly.
    opening_audio: bytes | None = None

    # Streaming reply pipeline:
    #   reply_audio_queue — async queue the background producer pushes
    #                       TTS chunks into; /reply-audio drains it as
    #                       a streaming response body. None = sentinel
    #                       for end-of-stream. This is what lets the
    #                       first sentence reach Twilio before the LLM
    #                       has finished writing the second.
    #   producer_task     — the asyncio task feeding that queue. Held
    #                       so we can wait for it to finish and then
    #                       read the full post-cleaning reply text.
    #   pending_text      — the full text the LLM produced (post tag
    #                       cleaning). Used as a fallback if the
    #                       streaming TTS path fails and we need to
    #                       synthesise from scratch.
    reply_audio_queue: asyncio.Queue[bytes | None] | None = None
    producer_task: asyncio.Task[None] | None = None
    pending_text: str = ""

    # Post-reply control flags. The producer sets these when it detects
    # an [END_CALL] or [TRANSFER_CALL] tag in the streamed LLM output.
    # /post-reply-action reads them after the reply audio finishes
    # playing and decides hangup / transfer / continue.
    #
    # We use a separate post-play webhook (rather than embedding the
    # decision in /process-speech) so the call ends correctly even if
    # the customer doesn't speak again — which is the whole point of
    # [END_CALL].
    pending_hangup: bool = False
    pending_transfer: bool = False


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
# WAV concatenation helper
# ═══════════════════════════════════════════════════════════════
# Cartesia returns a standalone WAV (RIFF header + fmt chunk + data chunk)
# per TTS call. When we stream several per turn we stitch them into one
# WAV so Twilio's <Play> sees a single valid file.
#
# We do this in pure Python to avoid a dependency on wave/soundfile —
# keeping it tight and allocation-light enough for the hot path.


def _concat_wavs(chunks: list[bytes]) -> bytes:
    """Concatenate multiple WAV blobs into one by merging their data chunks.

    All inputs must share format (sample rate, channels, bit depth) — which
    they do, because we always call Cartesia with the same output_format.

    If parsing fails for any chunk, we fall back to returning just the first
    chunk so the customer still hears something rather than silence.
    """
    if not chunks:
        return b""
    if len(chunks) == 1:
        return chunks[0]

    try:
        first = chunks[0]
        # Locate the "data" subchunk in the first WAV — headers can have
        # optional chunks before "data" (e.g. "LIST" metadata) so we scan
        # rather than assuming a 44-byte header.
        data_idx = first.find(b"data")
        if data_idx < 0 or data_idx + 8 > len(first):
            return first
        header_end = data_idx + 8  # "data" + 4-byte size field
        merged_body = bytearray(first[header_end:])

        for c in chunks[1:]:
            idx = c.find(b"data")
            if idx < 0 or idx + 8 > len(c):
                continue
            merged_body.extend(c[idx + 8:])

        # Patch sizes in the copied header.
        header = bytearray(first[:header_end])
        # Bytes 4–7: RIFF chunk size = total file size - 8.
        total_size = header_end + len(merged_body) - 8
        header[4:8] = total_size.to_bytes(4, "little")
        # Data-chunk size lives in the 4 bytes right before our body.
        header[header_end - 4:header_end] = len(merged_body).to_bytes(4, "little")

        return bytes(header) + bytes(merged_body)
    except Exception as exc:
        log.error("WAV concat failed (%s) — using first chunk only", exc)
        return chunks[0]


def _split_wav(blob: bytes) -> tuple[bytes, bytes]:
    """Return (header_bytes, audio_body_bytes) for a WAV.

    Header = everything up to and including the "data" chunk's size field.
    Body   = the raw audio samples after that.
    Returns (b"", blob) if the blob doesn't look like a WAV — callers
    can still yield the body, we just can't rewrite header sizes.
    """
    if not blob or len(blob) < 12 or blob[:4] != b"RIFF":
        return b"", blob
    idx = blob.find(b"data")
    if idx < 0 or idx + 8 > len(blob):
        return b"", blob
    end = idx + 8
    return blob[:end], blob[end:]


def _rewrite_sizes_unknown(header: bytes) -> bytes:
    """Rewrite a WAV header's size fields to 0xFFFFFFFF ("unknown").

    When we're streaming audio to Twilio we don't know the total size
    in advance — the LLM might still be generating sentence 3. Twilio
    and most WAV players treat 0xFFFFFFFF in the RIFF and data size
    fields as "read until EOF" rather than trusting a byte count that
    would otherwise be too small and cause playback to cut off early.
    """
    if len(header) < 8:
        return header
    out = bytearray(header)
    # RIFF chunk size at bytes 4..7
    out[4:8] = b"\xff\xff\xff\xff"
    # Data chunk size = last 4 bytes of our "header slice"
    out[-4:] = b"\xff\xff\xff\xff"
    return bytes(out)


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

        _spawn(
            self._calls.upsert(sid, user_id, {
                "phone": phone,
                "from_number": "",
                "status": "ringing",
                "agent_name": us.get("agent_name"),
                "agency_name": us.get("agency_name"),
            })
        )

    async def _resolve_user_for_sid(self, sid: str) -> str | None:
        """Find the owning user for a given call SID.

        First consults the in-memory state (fast path for active calls),
        then falls back to the DB (needed across process restarts).
        """
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
            # Cannot resolve — just hang up politely.
            return twiml.play_and_hangup("about:blank")

        state = self._state.get(sid)
        if state is None:
            state = CallState(
                sid=sid,
                user_id=user_id,
                phone=to_number,
                started_at=datetime.now(timezone.utc).isoformat(),
            )
            self._state.put(state)

        us = await settings_service.for_user(user_id)
        _spawn(
            self._calls.upsert(sid, user_id, {
                "phone": to_number,
                "from_number": from_number,
                "status": "answered",
                "agent_name": us.get("agent_name"),
                "agency_name": us.get("agency_name"),
            })
        )

        from app.core.config import get_settings
        base = get_settings().base_url

        if state.opening_audio is not None:
            return twiml.listen_with_play(
                f"{base}/webhooks/twilio/opening-audio?sid={sid}"
            )

        opening_text = await self._generate_opening_line(sid, us)
        state.history.append({"role": "assistant", "content": opening_text})
        _spawn(self._messages.insert(
            call_sid=sid, user_id=user_id, role="ai", content=opening_text,
        ))

        audio = await tts_provider.synthesize(
            opening_text, voice_id=us.resolve_voice_id()
        )
        if audio:
            state.opening_audio = audio

        return twiml.listen_with_play(
            f"{base}/webhooks/twilio/opening-audio?sid={sid}"
        )

    async def _generate_opening_line(self, sid: str, us: UserSettings) -> str:
        provider = llm_registry.get(us.resolve_llm_provider())
        base_prompt = us.resolve_system_prompt()

        augmented = (
            base_prompt
            + "\n\n---\n[SYSTEM]: The call just connected. Deliver your opening line now. "
              "Do not include [END_CALL] or [HOT_LEAD]. One or two sentences only."
        )
        try:
            raw = await provider.complete(
                "begin",
                history=[],
                system_prompt=augmented,
                model=(
                    us.get("openai_model")
                    if us.resolve_llm_provider() == "openai"
                    else us.get("groq_model")
                ),
                api_key=(
                    us.get("openai_api_key")
                    if us.resolve_llm_provider() == "openai"
                    else None
                ),
            )
        except Exception as exc:
            log.error("[%s] Opening-line LLM error: %s", sid, exc)
            agent = us.get("agent_name", "Sara")
            agency = (us.get("agency_name") or "").strip()
            # Fallback opener. Only mentions the agency if the tenant has
            # actually set one — otherwise we'd say "calling from ." which
            # sounds broken.
            if agency:
                raw = (
                    f"Hi, this is {agent} calling from {agency} — "
                    f"is this a good time to speak for a minute?"
                )
            else:
                raw = (
                    f"Hi, this is {agent} calling — "
                    f"is this a good time to speak for a minute?"
                )
        cleaned = clean_reply(raw)
        log.info("[%s] AI opening: %s", sid, cleaned.text[:80])
        return cleaned.text

    def get_opening_audio(self, sid: str) -> bytes | None:
        state = self._state.get(sid)
        return state.opening_audio if state else None

    # ── Speech turn (streaming pipeline) ──────────────────────
    async def handle_speech(
        self, sid: str, to_number: str, from_number: str, speech: str
    ) -> str:
        """Handle one customer utterance.

        This method returns TwiML as fast as possible — the heavy lifting
        (streaming LLM + parallel TTS) runs as a background task, and the
        audio is served from /reply-audio when Twilio comes asking.
        """
        from app.core.config import get_settings
        base = get_settings().base_url
        t0 = datetime.now(timezone.utc)

        user_id = await self._resolve_user_for_sid(sid)
        if user_id is None:
            log.warning("[%s] process-speech: unknown SID", sid)
            return twiml.play_and_hangup("about:blank")

        us = await settings_service.for_user(user_id)
        state = self._state.get(sid)
        if state is None:
            state = CallState(
                sid=sid,
                user_id=user_id,
                phone=to_number,
                started_at=datetime.now(timezone.utc).isoformat(),
            )
            self._state.put(state)
            _spawn(self._calls.upsert(sid, user_id, {
                "phone": to_number, "from_number": from_number,
                "status": "answered",
                "agent_name": us.get("agent_name"),
                "agency_name": us.get("agency_name"),
            }))
        else:
            _spawn(self._calls.update_by_sid(sid, status="answered"))

        if not speech:
            return twiml.listen_silent()

        _spawn(self._messages.insert(
            call_sid=sid, user_id=user_id, role="customer", content=speech,
        ))
        state.history.append({"role": "user", "content": speech})

        # Kick off the streaming reply pipeline. We do NOT await it here —
        # we return TwiML immediately and let Twilio pull the audio from
        # /reply-audio, which drains the queue as a streaming response.
        history_snapshot = list(state.history[:-1])  # immutable copy for the task
        provider_name = us.resolve_llm_provider()

        # Fresh queue per turn. Old one (if any) gets GC'd — any leftover
        # /reply-audio consumer on it is from a prior turn and already
        # finished.
        state.reply_audio_queue = asyncio.Queue(maxsize=32)
        state.producer_task = asyncio.create_task(
            self._stream_reply_audio(
                sid=sid,
                user_id=user_id,
                us=us,
                provider_name=provider_name,
                customer_text=speech,
                history=history_snapshot,
                t0=t0,
            )
        )

        audio_url = f"{base}/webhooks/twilio/reply-audio?sid={sid}"
        return twiml.listen_with_play(audio_url)

    async def _stream_reply_audio(
        self,
        *,
        sid: str,
        user_id: str,
        us: UserSettings,
        provider_name: str,
        customer_text: str,
        history: list[dict[str, str]],
        t0: datetime,
    ) -> None:
        """Producer: stream LLM → fire TTS in parallel → push WAV chunks into queue.

        The audio HTTP response (/reply-audio) acts as the consumer. The
        queue decouples them so the first TTS chunk can be flushed to
        Twilio while the LLM is still generating the second sentence.

        Pushes None as a sentinel when done. Errors always ensure a
        terminating None is pushed so the consumer doesn't hang.
        """
        state = self._state.get(sid)
        queue = state.reply_audio_queue if state else None

        async def _put(chunk: bytes | None) -> None:
            if queue is not None:
                await queue.put(chunk)

        try:
            system_prompt = us.resolve_system_prompt()

            voice_id = us.resolve_voice_id()
            model = (
                us.get("openai_model") if provider_name == "openai"
                else us.get("groq_model")
            )
            api_key = us.get("openai_api_key") if provider_name == "openai" else None
            provider = llm_registry.get(provider_name)

            # ── Path A: non-streaming provider (Groq) ──────────────
            if not isinstance(provider, OpenAIProvider):
                try:
                    raw_reply = await provider.complete(
                        customer_text,
                        history=history,
                        system_prompt=system_prompt,
                        model=model,
                        api_key=api_key,
                    )
                except Exception as exc:
                    log.error("[%s] LLM error: %s", sid, exc)
                    raw_reply = "Sorry, I missed that — could you say that again?"

                cleaned = clean_reply(raw_reply)
                self._commit_reply_text(sid, user_id, cleaned)
                audio = await tts_provider.synthesize(cleaned.text, voice_id=voice_id)
                await _put(audio)
                t_total = (datetime.now(timezone.utc) - t0).total_seconds()
                log.info("[%s] non-stream reply total %.2fs", sid, t_total)
                return

            # ── Path B: streaming provider (OpenAI) ─────────────────
            # We spawn a TTS task per LLM sentence, then drain them IN
            # ORDER and push into the audio queue as soon as each is
            # ready. That preserves playback order while still letting
            # sentence 2's TTS overlap with sentence 1 playing.
            tts_tasks: list[asyncio.Task[bytes | None]] = []
            full_text_parts: list[str] = []
            first_chunk_at: datetime | None = None

            async def _drain_ready_tts() -> None:
                """Push any completed TTS chunks at the head of the queue.

                We only push tasks from the FRONT of `tts_tasks` to keep
                ordering correct. If task 0 isn't done yet, we can't push
                task 1 even if it finished — otherwise the caller would
                hear sentence 2 before sentence 1.
                """
                while tts_tasks and tts_tasks[0].done():
                    task = tts_tasks.pop(0)
                    try:
                        audio_bytes = task.result()
                    except Exception as exc:
                        log.error("[%s] TTS chunk error: %s", sid, exc)
                        audio_bytes = None
                    if audio_bytes:
                        await _put(audio_bytes)

            try:
                async for chunk in provider.stream_sentences(
                    customer_text,
                    history=history,
                    system_prompt=system_prompt,
                    model=model,
                    api_key=api_key,
                ):
                    if not chunk.strip():
                        continue
                    if first_chunk_at is None:
                        first_chunk_at = datetime.now(timezone.utc)
                        log.info(
                            "[%s] first LLM chunk in %.2fs: '%s'",
                            sid,
                            (first_chunk_at - t0).total_seconds(),
                            chunk[:60],
                        )
                    full_text_parts.append(chunk)
                    tts_tasks.append(asyncio.create_task(
                        tts_provider.synthesize(chunk, voice_id=voice_id)
                    ))
                    # Opportunistic drain — keeps latency-to-first-audio low
                    # without waiting for the LLM to finish.
                    await _drain_ready_tts()
            except Exception as exc:
                log.error("[%s] LLM stream error: %s", sid, exc)

            # Fallback: stream produced nothing. Do one non-streaming
            # completion so the call doesn't go silent.
            if not full_text_parts:
                log.warning("[%s] empty stream, falling back to non-streaming", sid)
                try:
                    raw = await provider.complete(
                        customer_text,
                        history=history,
                        system_prompt=system_prompt,
                        model=model,
                        api_key=api_key,
                    )
                except Exception as exc:
                    log.error("[%s] fallback LLM error: %s", sid, exc)
                    raw = "Sorry, I missed that — could you say that again?"
                cleaned = clean_reply(raw)
                self._commit_reply_text(sid, user_id, cleaned)
                audio = await tts_provider.synthesize(cleaned.text, voice_id=voice_id)
                await _put(audio)
                return

            # LLM done — now drain any remaining TTS tasks in order.
            chunk_count = 0
            for task in tts_tasks:
                try:
                    audio_bytes = await asyncio.wait_for(task, timeout=10.0)
                except asyncio.TimeoutError:
                    log.error("[%s] TTS chunk timed out", sid)
                    audio_bytes = None
                except Exception as exc:
                    log.error("[%s] TTS chunk error: %s", sid, exc)
                    audio_bytes = None
                if audio_bytes:
                    await _put(audio_bytes)
                    chunk_count += 1

            # Commit the full post-clean text (for end-call detection,
            # DB, history). The chunks shipped to the caller already had
            # [TAG] markers stripped; clean_reply on the joined text
            # picks up end-phrase / hot-lead flags.
            full_raw = " ".join(full_text_parts)
            cleaned = clean_reply(full_raw)
            self._commit_reply_text(sid, user_id, cleaned)

            t_total = (datetime.now(timezone.utc) - t0).total_seconds()
            log.info(
                "[%s] streamed reply: %d chunks, total %.2fs, end=%s hot=%s",
                sid, chunk_count, t_total,
                cleaned.end_call, cleaned.hot_lead,
            )
        except Exception as exc:
            log.error("[%s] producer crashed: %s", sid, exc)
        finally:
            # Always terminate the queue so the consumer exits.
            await _put(None)

    def _commit_reply_text(
        self, sid: str, user_id: str, cleaned: "CleanedReply"
    ) -> None:
        """Persist reply text + set post-play control flags.

        Called from both streaming and non-streaming paths after
        clean_reply() has parsed the LLM output. The cleaned object's
        flags determine whether /post-reply-action will continue
        listening, hang up, or transfer.
        """
        state = self._state.get(sid)
        if state is not None:
            state.history.append({"role": "assistant", "content": cleaned.text})
            state.pending_text = cleaned.text
            # Set the control flags exactly once per turn. Producer
            # writes them; /post-reply-action reads + clears them.
            if cleaned.transfer_call:
                state.pending_transfer = True
            elif cleaned.end_call:
                state.pending_hangup = True
        _spawn(self._messages.insert(
            call_sid=sid, user_id=user_id, role="ai", content=cleaned.text,
        ))
        if cleaned.hot_lead:
            _spawn(self._calls.update_by_sid(sid, hot_lead=True))

    # ── Post-reply branching (the call-ending fix) ───────────
    async def handle_post_reply_action(self, sid: str) -> str:
        """Decide what Twilio should do after the AI's reply audio finishes.

        Three possible outcomes:
          • pending_hangup   → return <Hangup/> so the call ends.
          • pending_transfer → dial the user's transfer_number; if the
                               dial fails or no one picks up, Twilio
                               falls through to /transfer-status which
                               plays a polite fallback line and hangs up.
          • neither flag set → resume listening for the next utterance.

        Flags are cleared after read so a stale state can't end the call
        on the next turn by accident.
        """
        state = self._state.get(sid)
        if state is None:
            # Unknown SID — safest is to hang up rather than loop forever.
            log.warning("[%s] post-reply-action for unknown SID — hanging up", sid)
            return twiml.hangup()

        # Read + clear in one step. If we crash mid-way the worst case is
        # a redundant continue-listening, not a stuck call.
        hangup_now = state.pending_hangup
        transfer_now = state.pending_transfer
        state.pending_hangup = False
        state.pending_transfer = False

        if transfer_now:
            us = await settings_service.for_user(state.user_id)
            transfer_number = (us.get("transfer_number") or "").strip()
            if not transfer_number:
                log.warning(
                    "[%s] [TRANSFER_CALL] but no transfer_number configured — "
                    "ending call gracefully", sid,
                )
                return twiml.hangup()
            log.info("[%s] transferring to %s", sid, transfer_number)
            return twiml.transfer_call(transfer_number, sid)

        if hangup_now:
            log.info("[%s] ending call (end_call flag was set)", sid)
            return twiml.hangup()

        # No control flag — resume the conversation.
        return twiml.listen_for_speech()

    # ── Transfer status callback ─────────────────────────────
    async def handle_transfer_status(
        self, sid: str, dial_call_status: str
    ) -> str:
        """Called by Twilio after the <Dial> finishes.

        Twilio sets DialCallStatus to one of:
          completed           — the transferee picked up and the bridged
                                call ended normally. Nothing more to do.
          answered            — same as completed on some accounts.
          no-answer / busy /
          failed / canceled   — nobody picked up. We play a polite
                                "experts are busy" line and hang up.

        The behaviour you asked for: when the transfer doesn't connect,
        the AI lets the customer know our experts are busy and someone
        will call back, then ends the call warmly.
        """
        normalised = (dial_call_status or "").lower()
        log.info("[%s] transfer status = %s", sid, normalised)

        if normalised in ("completed", "answered"):
            # Bridge succeeded and is now over — end this leg quietly.
            return twiml.hangup()

        # Failure path — synthesise the apology line on demand and play it.
        state = self._state.get(sid)
        if state is None:
            return twiml.hangup()

        us = await settings_service.for_user(state.user_id)
        # Phrase agreed with the operator. Kept short so it fits the
        # standard 2-sentence cap without sounding rushed.
        apology = (
            "Looks like our experts are busy at the moment — they'll "
            "call you back as soon as they're available. Thank you for "
            "understanding, and have a great day!"
        )
        from app.core.config import get_settings
        base = get_settings().base_url

        # Synthesise + cache the audio in state so /reply-audio can serve
        # it. We reuse the pending_text + producer-queue pathway so we
        # don't have to invent a second audio-serving endpoint.
        audio = await tts_provider.synthesize(
            apology, voice_id=us.resolve_voice_id()
        )
        if not audio:
            # If TTS failed, end the call rather than hang silent.
            log.error("[%s] transfer-failed apology TTS failed", sid)
            return twiml.hangup()

        # Stash in a fresh queue with a single chunk so /reply-audio
        # streams it the same way it streams normal replies.
        state.reply_audio_queue = asyncio.Queue(maxsize=2)
        await state.reply_audio_queue.put(audio)
        await state.reply_audio_queue.put(None)
        state.pending_hangup = True   # play it, then post-reply will hang up

        audio_url = f"{base}/webhooks/twilio/reply-audio?sid={sid}"
        return twiml.listen_with_play(audio_url)

    # ── Serve reply audio ────────────────────────────────────
    async def stream_reply_audio(self, sid: str) -> AsyncIterator[bytes] | None:
        """Yield WAV bytes as they become available.

        Returns None if the SID is unknown — caller should send a 404
        in that case. Otherwise returns an async generator that:
          1. Awaits the first WAV chunk (typically ~1 s after the
             customer finished speaking).
          2. Yields its full RIFF header + data body immediately.
          3. For each subsequent chunk, yields only the PCM/mu-law body
             (without a fresh RIFF header) so Twilio sees one continuous
             stream of samples.

        Twilio's <Play> tolerates this: the header at the start declares
        a size field we write as "unknown / streaming" (0xFFFFFFFF) so
        Twilio doesn't stop early when it hits the declared length.
        """
        state = self._state.get(sid)
        if state is None:
            log.warning("[%s] reply-audio requested for unknown SID", sid)
            return None

        queue = state.reply_audio_queue
        # Consume the queue reference so a late retry doesn't re-drain.
        state.reply_audio_queue = None
        producer = state.producer_task
        state.producer_task = None

        if queue is None:
            # No producer was started for this SID — fall back to text.
            return self._fallback_audio_generator(sid)

        return self._wav_stream_generator(sid, queue, producer)

    async def _wav_stream_generator(
        self,
        sid: str,
        queue: asyncio.Queue[bytes | None],
        producer: asyncio.Task[None] | None,
    ) -> AsyncIterator[bytes]:
        """Async generator that yields WAV bytes from the TTS queue.

        See stream_reply_audio() for the overall design.
        """
        try:
            # Await first chunk — this is the latency-defining await.
            # If the producer is very slow, time out rather than hanging
            # Twilio forever.
            try:
                first = await asyncio.wait_for(queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                log.error("[%s] timed out waiting for first audio chunk", sid)
                return

            if first is None:
                # Producer died before yielding anything.
                log.warning("[%s] producer yielded no audio", sid)
                return

            # Yield the first chunk's header with an "unknown size" marker
            # so Twilio doesn't stop at the declared data length.
            # RIFF size field (bytes 4-7) and data-chunk size (4 bytes
            # immediately after 'data') both get 0xFFFFFFFF.
            header_bytes, body_bytes = _split_wav(first)
            if header_bytes:
                yield _rewrite_sizes_unknown(header_bytes)
            if body_bytes:
                yield body_bytes

            # Subsequent chunks: strip the RIFF header, yield only audio body.
            while True:
                next_chunk = await queue.get()
                if next_chunk is None:
                    break
                _h, b = _split_wav(next_chunk)
                if b:
                    yield b
        finally:
            # Ensure the producer task is awaited (prevents "never retrieved"
            # warnings) and any lingering queue state is dropped.
            if producer is not None and not producer.done():
                try:
                    await asyncio.wait_for(producer, timeout=2.0)
                except Exception:
                    producer.cancel()

    async def _fallback_audio_generator(
        self, sid: str
    ) -> AsyncIterator[bytes]:
        """Last-resort fallback when there's no producer queue.

        Synthesises state.pending_text from scratch and yields the
        whole WAV as a single chunk. Keeps the call from going silent.
        """
        state = self._state.get(sid)
        if state is None:
            return
        us = await settings_service.for_user(state.user_id)
        fallback_text = state.pending_text or "Thank you, have a great day!"
        log.warning("[%s] reply-audio fallback path taken", sid)
        audio = await tts_provider.synthesize(
            fallback_text, voice_id=us.resolve_voice_id()
        )
        if audio:
            yield audio

    # ── Status + recording webhooks ──────────────────────────
    async def handle_call_status(
        self, sid: str, status: str, duration: int
    ) -> None:
        log.info("[%s] Status=%s Duration=%ss", sid, status, duration)

        state = self._state.get(sid)
        transcript = ""
        if state is not None:
            us = await settings_service.for_user(state.user_id)
            agent = us.get("agent_name", "Agent")
            lines: list[str] = []
            for msg in state.history:
                prefix = f"{agent} (AI)" if msg.get("role") == "assistant" else "Customer"
                lines.append(f"{prefix}: {msg.get('content', '')}")
            transcript = "\n".join(lines)

        if status in ("completed", "failed", "no-answer", "busy", "canceled"):
            _spawn(
                self._calls.finalize_by_sid(sid, status, duration, "", "", transcript)
            )
            # Conversation memory lives on `state` only — discarding it
            # here intentionally drops everything the AI knew about this
            # caller. Transcript is still saved on the calls row above
            # for human review, but the AI starts fresh next time.
            self._state.discard(sid)
        elif status == "in-progress":
            _spawn(self._calls.update_by_sid(sid, status="answered"))

    async def handle_recording_status(
        self, sid: str, recording_url: str, recording_status: str
    ) -> None:
        if recording_status != "completed" or not recording_url or not sid:
            return
        _spawn(self._persist_recording(sid, recording_url))

    async def _persist_recording(self, sid: str, recording_url: str) -> None:
        try:
            public_url, path = await storage.upload_recording(sid, recording_url)
            target = public_url or (recording_url + ".mp3")
            await self._calls.set_recording_by_sid(sid, target, path)
            log.info("[%s] Recording saved → %s", sid, target)
        except Exception as exc:
            log.error("[%s] Recording upload error: %s", sid, exc)
            try:
                await self._calls.set_recording_by_sid(sid, recording_url + ".mp3", "")
            except Exception:
                pass


call_orchestrator = CallOrchestrator()
