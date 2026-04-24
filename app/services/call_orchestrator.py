"""
app.services.call_orchestrator
──────────────────────────────
Per-call state machine, multi-tenant aware.

Each call is attached to a `user_id` when `/api/call` is invoked. That
user_id is stored both in the DB (calls.user_id) and in the in-process
state map. When Twilio hits a webhook we look up the user_id from the
SID so every downstream action uses that user's settings.

Latency design — streaming reply pipeline
─────────────────────────────────────────
When the customer finishes a turn we want the caller to hear the first
syllable of the AI's reply as fast as possible. The classic approach
(await full LLM → await full TTS → hand WAV to Twilio) stacks those
latencies serially: ~1–2 s LLM + ~0.5–1 s TTS + transport = painful.

Instead we do:
  1. Kick off a streaming LLM completion (for OpenAI — Groq stays on
     the classic single-shot path since the SDK is non-streaming here).
  2. As soon as the stream yields a speakable sentence fragment, fire
     a TTS request for that fragment in parallel with the LLM still
     generating the next sentence.
  3. The /reply-audio endpoint awaits the background task, which in
     turn concatenates every TTS chunk (in order) into one WAV Twilio
     can <Play> seamlessly.

Result: the customer typically hears the first word ~800–1200 ms sooner
than before, and the rest of the sentence lands without a visible seam.

Scaling:
  • The in-memory `_state` map works for one worker. To run multiple
    workers, replace _CallStateStore with a Redis implementation —
    the public orchestrator API does not change.
"""
from __future__ import annotations

import asyncio
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
from app.services.text_cleaner import clean_reply
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
    #   reply_audio_task — background task that will resolve to the
    #                      concatenated WAV bytes for this turn. The
    #                      /reply-audio endpoint awaits it.
    #   pending_text     — the full text the LLM produced (post tag
    #                      cleaning). Used as a fallback if the
    #                      streaming TTS path fails and we need to
    #                      synthesise from scratch.
    reply_audio_task: asyncio.Task[bytes | None] | None = None
    pending_text: str = ""


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
            agency = us.get("agency_name", "our agency")
            raw = (
                f"Hi, this is {agent} calling from {agency} — "
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
        # /reply-audio. That endpoint will await the background task.
        history_snapshot = list(state.history[:-1])  # immutable copy for the task
        provider_name = us.resolve_llm_provider()
        state.reply_audio_task = asyncio.create_task(
            self._produce_reply_audio(
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

    async def _produce_reply_audio(
        self,
        *,
        sid: str,
        user_id: str,
        us: UserSettings,
        provider_name: str,
        customer_text: str,
        history: list[dict[str, str]],
        t0: datetime,
    ) -> bytes | None:
        """Stream LLM → fire TTS per sentence in parallel → return WAV bytes.

        Streaming path is used only for OpenAI; Groq callers fall back to
        the non-streaming single-shot path transparently.
        """
        system_prompt = us.resolve_system_prompt()
        voice_id = us.resolve_voice_id()
        model = (
            us.get("openai_model") if provider_name == "openai"
            else us.get("groq_model")
        )
        api_key = us.get("openai_api_key") if provider_name == "openai" else None

        provider = llm_registry.get(provider_name)

        # ── Path A: non-streaming provider (Groq) ──────────────────
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
            self._commit_reply_text(sid, user_id, cleaned.text, cleaned.hot_lead)
            audio = await tts_provider.synthesize(cleaned.text, voice_id=voice_id)
            t_total = (datetime.now(timezone.utc) - t0).total_seconds()
            log.info("[%s] non-stream reply total %.2fs", sid, t_total)
            return audio

        # ── Path B: streaming provider (OpenAI) ────────────────────
        # Fire a TTS task per chunk as the LLM stream emits sentences.
        # Collect them in order at the end and concatenate the WAVs.
        tts_tasks: list[asyncio.Task[bytes | None]] = []
        full_text_parts: list[str] = []
        first_chunk_at: datetime | None = None

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
        except Exception as exc:
            log.error("[%s] LLM stream error: %s", sid, exc)

        # If the stream produced nothing (rare — usually a bad API key),
        # fall back to one non-streaming completion so the call doesn't
        # go silent.
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
            self._commit_reply_text(sid, user_id, cleaned.text, cleaned.hot_lead)
            return await tts_provider.synthesize(cleaned.text, voice_id=voice_id)

        # Clean the full text for DB + end-call detection. The streamed
        # chunks already had [TAG] markers stripped, but end-phrase
        # detection runs on the joined text.
        full_raw = " ".join(full_text_parts)
        cleaned = clean_reply(full_raw)
        self._commit_reply_text(sid, user_id, cleaned.text, cleaned.hot_lead)

        # Collect all TTS chunks (in order).
        chunks: list[bytes] = []
        for task in tts_tasks:
            try:
                result = await asyncio.wait_for(task, timeout=10.0)
            except asyncio.TimeoutError:
                log.error("[%s] TTS chunk timed out", sid)
                result = None
            except Exception as exc:
                log.error("[%s] TTS chunk error: %s", sid, exc)
                result = None
            if result:
                chunks.append(result)

        if not chunks:
            # Every TTS call failed — try one last synthesis of the full text.
            log.warning("[%s] all streaming TTS failed, fallback synthesize", sid)
            return await tts_provider.synthesize(cleaned.text, voice_id=voice_id)

        merged = _concat_wavs(chunks)
        t_total = (datetime.now(timezone.utc) - t0).total_seconds()
        log.info(
            "[%s] streamed reply: %d chunks, %d bytes, total %.2fs, end=%s hot=%s",
            sid, len(chunks), len(merged), t_total,
            cleaned.end_call, cleaned.hot_lead,
        )
        return merged

    def _commit_reply_text(
        self, sid: str, user_id: str, text: str, hot_lead: bool
    ) -> None:
        """Persist the AI's reply text to in-memory history + DB. Fire-and-forget."""
        state = self._state.get(sid)
        if state is not None:
            state.history.append({"role": "assistant", "content": text})
            state.pending_text = text
        _spawn(self._messages.insert(
            call_sid=sid, user_id=user_id, role="ai", content=text,
        ))
        if hot_lead:
            _spawn(self._calls.update_by_sid(sid, hot_lead=True))

    # ── Serve reply audio ────────────────────────────────────
    async def get_reply_audio(self, sid: str) -> bytes | None:
        state = self._state.get(sid)
        if state is None:
            log.warning("[%s] reply-audio requested for unknown SID", sid)
            return None

        task = state.reply_audio_task
        state.reply_audio_task = None
        pending = state.pending_text

        if task is not None:
            try:
                return await asyncio.wait_for(task, timeout=15.0)
            except asyncio.TimeoutError:
                log.error("[%s] reply audio task timed out", sid)
            except Exception as exc:
                log.error("[%s] reply audio task error: %s", sid, exc)

        user_id = state.user_id
        us = await settings_service.for_user(user_id)
        fallback_text = pending or "Thank you, have a great day!"
        log.warning("[%s] reply-audio fallback path taken", sid)
        return await tts_provider.synthesize(
            fallback_text, voice_id=us.resolve_voice_id()
        )

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
