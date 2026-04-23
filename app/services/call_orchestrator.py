"""
app.services.call_orchestrator
──────────────────────────────
Per-call state machine, multi-tenant aware.

Each call is attached to a `user_id` when `/api/call` is invoked. That
user_id is stored both in the DB (calls.user_id) and in the in-process
state map. When Twilio hits a webhook we look up the user_id from the
SID so every downstream action uses that user's settings.

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
    tts_task: asyncio.Task[bytes | None] | None = None
    pending_text: str = ""
    opening_audio: bytes | None = None


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

    # ── Speech turn ───────────────────────────────────────────
    async def handle_speech(
        self, sid: str, to_number: str, from_number: str, speech: str
    ) -> str:
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

        # ── LLM call with this user's provider & credentials ─────
        provider = llm_registry.get(us.resolve_llm_provider())
        provider_name = us.resolve_llm_provider()
        system_prompt = us.resolve_system_prompt()
        try:
            raw_reply = await provider.complete(
                speech,
                history=state.history[:-1],
                system_prompt=system_prompt,
                model=(
                    us.get("openai_model") if provider_name == "openai"
                    else us.get("groq_model")
                ),
                api_key=(
                    us.get("openai_api_key") if provider_name == "openai" else None
                ),
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
        state.pending_text = cleaned.text
        state.tts_task = asyncio.create_task(
            tts_provider.synthesize(cleaned.text, voice_id=us.resolve_voice_id())
        )

        _spawn(self._messages.insert(
            call_sid=sid, user_id=user_id, role="ai", content=cleaned.text,
        ))
        if cleaned.hot_lead:
            _spawn(self._calls.update_by_sid(sid, hot_lead=True))

        audio_url = f"{base}/webhooks/twilio/reply-audio?sid={sid}"
        if cleaned.end_call:
            return twiml.play_and_hangup(audio_url)
        return twiml.listen_with_play(audio_url)

    # ── Serve reply audio ────────────────────────────────────
    async def get_reply_audio(self, sid: str) -> bytes | None:
        state = self._state.get(sid)
        if state is None:
            log.warning("[%s] reply-audio requested for unknown SID", sid)
            return None

        task = state.tts_task
        state.tts_task = None
        pending = state.pending_text
        state.pending_text = ""

        if task is not None:
            try:
                return await asyncio.wait_for(task, timeout=8.0)
            except asyncio.TimeoutError:
                log.error("[%s] TTS task timed out", sid)
            except Exception as exc:
                log.error("[%s] TTS task error: %s", sid, exc)

        user_id = state.user_id
        us = await settings_service.for_user(user_id)
        fallback_text = pending or "Thank you, have a great day!"
        log.warning("[%s] TTS fallback path taken", sid)
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
