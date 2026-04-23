"""
app.services.call_orchestrator
──────────────────────────────
In-process per-call state machine. Handles:

  • Tracking conversation history for an active call
  • Pre-emptively starting TTS so Twilio's fetch of /reply-audio doesn't wait
  • Caching the AI-generated opening line per call SID
  • Firing DB persistence off the critical path (fire-and-forget)

Scaling note:
    This orchestrator keeps per-call state in process memory. That is fine
    for a single worker (the common Render setup). To scale to N workers,
    swap `_CallStateStore` out for a Redis-backed implementation — the
    public API of this class doesn't change. This is intentionally the
    only place that knows about state locality.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.repositories.calls import CallsRepository
from app.db.repositories.messages import MessagesRepository
from app.services.llm import llm_registry
from app.services.settings_service import settings_service
from app.services.storage import storage
from app.services.telephony import twiml
from app.services.text_cleaner import clean_reply
from app.services.tts import tts_provider

log = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════
# Per-call state (in-memory; swap for Redis for multi-worker)
# ═══════════════════════════════════════════════════════════════
@dataclass
class CallState:
    sid: str
    phone: str = ""
    history: list[dict[str, str]] = field(default_factory=list)
    started_at: str = ""
    # Pre-generated TTS that /reply-audio will pick up:
    tts_task: asyncio.Task[bytes | None] | None = None
    pending_text: str = ""
    # AI-generated opening-line audio, cached while the call is active:
    opening_audio: bytes | None = None


class _CallStateStore:
    """Thread-safe dict wrapper for per-call state."""

    def __init__(self) -> None:
        self._state: dict[str, CallState] = {}

    def get_or_create(self, sid: str, *, phone: str = "") -> tuple[CallState, bool]:
        existing = self._state.get(sid)
        if existing:
            return existing, False
        created = CallState(
            sid=sid,
            phone=phone,
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        self._state[sid] = created
        return created, True

    def get(self, sid: str) -> CallState | None:
        return self._state.get(sid)

    def discard(self, sid: str) -> None:
        self._state.pop(sid, None)


# ═══════════════════════════════════════════════════════════════
# Fire-and-forget helpers
# ═══════════════════════════════════════════════════════════════
def _spawn(coro) -> None:
    """Run `coro` as a background task without blocking the caller.
    Exceptions are logged, never re-raised (DB lag must not drop calls)."""

    async def _runner():
        try:
            await coro
        except Exception as exc:
            log.error("Background task failed: %s", exc)

    asyncio.create_task(_runner())


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
    async def register_outbound(self, sid: str, phone: str) -> None:
        """Record a call we just placed via Twilio."""
        state, _ = self._state.get_or_create(sid, phone=phone)
        state.phone = phone
        _spawn(
            self._calls.upsert(
                sid,
                {
                    "phone": phone,
                    "from_number": "",
                    "status": "ringing",
                    "agent_name": settings_service.get("agent_name"),
                    "agency_name": settings_service.get("agency_name"),
                },
            )
        )

    # ── Greeting / opening line ───────────────────────────────
    async def handle_greeting(self, sid: str, to_number: str, from_number: str) -> str:
        """Produce the TwiML that plays the AI-generated opening line."""
        state, is_new = self._state.get_or_create(sid, phone=to_number)
        if is_new:
            _spawn(
                self._calls.upsert(
                    sid,
                    {
                        "phone": to_number,
                        "from_number": from_number,
                        "status": "answered",
                        "agent_name": settings_service.get("agent_name"),
                        "agency_name": settings_service.get("agency_name"),
                    },
                )
            )

        from app.core.config import get_settings
        base = get_settings().base_url

        # If we already generated the opening audio, just replay it.
        if state.opening_audio is not None:
            return twiml.listen_with_play(
                f"{base}/webhooks/twilio/opening-audio?sid={sid}"
            )

        opening_text = await self._generate_opening_line(sid)
        state.history.append({"role": "assistant", "content": opening_text})
        _spawn(self._messages.insert(sid, "ai", opening_text))

        # Synthesize now so the <Play> fetches audio that's already ready.
        audio = await tts_provider.synthesize(
            opening_text, voice_id=settings_service.resolve_voice_id()
        )
        if audio:
            state.opening_audio = audio

        return twiml.listen_with_play(
            f"{base}/webhooks/twilio/opening-audio?sid={sid}"
        )

    async def _generate_opening_line(self, sid: str) -> str:
        """Ask the configured LLM to produce the first line of the call."""
        provider_name = settings_service.resolve_llm_provider()
        provider = llm_registry.get(provider_name)
        base_prompt = settings_service.resolve_system_prompt()
        augmented = (
            base_prompt
            + "\n\n---\n[SYSTEM]: The call just connected. Deliver your opening line now. "
              "Do not include [END_CALL] or [HOT_LEAD]. One or two sentences only."
        )
        try:
            raw = await provider.complete(
                "begin", history=[], system_prompt=augmented
            )
        except Exception as exc:
            log.error("[%s] Opening-line LLM error: %s", sid, exc)
            agent = settings_service.get("agent_name", "Sara")
            agency = settings_service.get("agency_name", "our agency")
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

    # ── Speech turn ───────────────────────────────────────────
    async def handle_speech(
        self, sid: str, to_number: str, from_number: str, speech: str
    ) -> str:
        """Process a single customer utterance and return TwiML for the reply."""
        from app.core.config import get_settings
        base = get_settings().base_url
        t0 = datetime.now(timezone.utc)

        state, is_new = self._state.get_or_create(sid, phone=to_number)
        if is_new:
            _spawn(
                self._calls.upsert(
                    sid,
                    {
                        "phone": to_number,
                        "from_number": from_number,
                        "status": "answered",
                        "agent_name": settings_service.get("agent_name"),
                        "agency_name": settings_service.get("agency_name"),
                    },
                )
            )
        else:
            _spawn(self._calls.update(sid, status="answered"))

        # Silence handling: reprompt if no speech came through.
        if not speech:
            return twiml.listen_silent()

        _spawn(self._messages.insert(sid, "customer", speech))
        state.history.append({"role": "user", "content": speech})

        # ── LLM call ──────────────────────────────────────────
        provider_name = settings_service.resolve_llm_provider()
        provider = llm_registry.get(provider_name)
        system_prompt = settings_service.resolve_system_prompt()
        try:
            raw_reply = await provider.complete(
                speech,
                history=state.history[:-1],
                system_prompt=system_prompt,
            )
        except Exception as exc:
            log.error("[%s] LLM error: %s", sid, exc)
            raw_reply = "Sorry, I missed that — could you say that again?"

        cleaned = clean_reply(raw_reply)
        t_llm = (datetime.now(timezone.utc) - t0).total_seconds()
        log.info(
            "[%s] LLM %.2fs → '%s' end=%s hot=%s",
            sid, t_llm, cleaned.text[:60], cleaned.end_call, cleaned.hot_lead,
        )

        state.history.append({"role": "assistant", "content": cleaned.text})

        # Start TTS in the background; /reply-audio will await the task.
        state.pending_text = cleaned.text
        state.tts_task = asyncio.create_task(
            tts_provider.synthesize(
                cleaned.text, voice_id=settings_service.resolve_voice_id()
            )
        )

        _spawn(self._messages.insert(sid, "ai", cleaned.text))
        if cleaned.hot_lead:
            _spawn(self._calls.update(sid, hot_lead=True))

        audio_url = f"{base}/webhooks/twilio/reply-audio?sid={sid}"
        if cleaned.end_call:
            return twiml.play_and_hangup(audio_url)
        return twiml.listen_with_play(audio_url)

    # ── Serving the pre-generated reply audio ────────────────
    async def get_reply_audio(self, sid: str) -> bytes | None:
        """Called by Twilio fetching the <Play> URL. Waits on the TTS task
        that `handle_speech` kicked off."""
        state = self._state.get(sid)
        if state is None:
            log.warning("[%s] reply-audio requested for unknown SID", sid)
            return None

        task = state.tts_task
        state.tts_task = None  # consume
        pending = state.pending_text
        state.pending_text = ""

        if task is not None:
            try:
                return await asyncio.wait_for(task, timeout=8.0)
            except asyncio.TimeoutError:
                log.error("[%s] TTS task timed out", sid)
            except Exception as exc:
                log.error("[%s] TTS task error: %s", sid, exc)

        # Fallback: synthesize now if no task was started.
        fallback_text = pending or "Thank you, have a great day!"
        log.warning("[%s] TTS fallback path taken", sid)
        return await tts_provider.synthesize(
            fallback_text, voice_id=settings_service.resolve_voice_id()
        )

    # ── Status + recording webhooks ──────────────────────────
    async def handle_call_status(
        self, sid: str, status: str, duration: int
    ) -> None:
        log.info("[%s] Status=%s Duration=%ss", sid, status, duration)

        state = self._state.get(sid)
        transcript = self._format_transcript(state.history if state else [])

        if status in ("completed", "failed", "no-answer", "busy", "canceled"):
            _spawn(
                self._calls.finalize(sid, status, duration, "", "", transcript)
            )
            self._state.discard(sid)
        elif status == "in-progress":
            _spawn(self._calls.update(sid, status="answered"))

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
            await self._calls.set_recording(sid, target, path)
            log.info("[%s] Recording saved → %s", sid, target)
        except Exception as exc:
            log.error("[%s] Recording upload error: %s", sid, exc)
            try:
                await self._calls.set_recording(sid, recording_url + ".mp3", "")
            except Exception:
                pass

    # ── Helpers ───────────────────────────────────────────────
    def _format_transcript(self, history: list[dict[str, str]]) -> str:
        agent = settings_service.get("agent_name", "Agent")
        lines: list[str] = []
        for msg in history:
            prefix = f"{agent} (AI)" if msg.get("role") == "assistant" else "Customer"
            lines.append(f"{prefix}: {msg.get('content', '')}")
        return "\n".join(lines)


# Module-level singleton — safe in single-worker deployments.
call_orchestrator = CallOrchestrator()
