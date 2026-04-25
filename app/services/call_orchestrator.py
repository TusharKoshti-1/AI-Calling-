"""
app.services.call_orchestrator
──────────────────────────────
Per-call lifecycle orchestrator (post-ConversationRelay).

In v9 this file held the chunked-TwiML reply pipeline, audio cache,
silence-prompt logic, and TTS chunk synthesis. In v10 ConversationRelay
takes care of all that — the orchestrator's job shrinks to:

  • Recording outbound call attempts (DB row, "ringing" status).
  • Updating call rows on Twilio status webhooks.
  • Persisting recording files to Supabase Storage.
  • Discarding state on call delete (called from the delete endpoint).

The actual conversation logic now lives in app.services.cr_handler —
the ConversationRelay websocket handler.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.core.logging import get_logger
from app.db.repositories.calls import CallsRepository
from app.services.settings_service import settings_service
from app.services.storage import storage

log = get_logger(__name__)


def _spawn(coro) -> None:
    async def _runner():
        try:
            await coro
        except Exception as exc:
            log.error("Background task failed: %s", exc)
    asyncio.create_task(_runner())


class CallOrchestrator:
    def __init__(
        self,
        *,
        calls_repo: CallsRepository | None = None,
    ) -> None:
        self._calls = calls_repo or CallsRepository()

    # ────────────────────────────────────────────────────────
    # Outbound lifecycle
    # ────────────────────────────────────────────────────────
    async def register_outbound(
        self, sid: str, user_id: str, phone: str
    ) -> None:
        """Record a fresh outbound call attempt.

        The upsert is AWAITED (not fire-and-forget) because Twilio's
        /greeting webhook fires very quickly — sometimes within 500 ms
        of register_outbound returning. If we let the upsert run in the
        background, the greeting handler can race ahead, fail to look
        up the user_id by sid, and hang up the call.

        The latency cost of the await is hidden inside Twilio's own
        dial setup time (the customer's phone is still ringing), so it
        adds zero perceived delay.

        Lightweight in v10 — the websocket handler does the rest of the
        legwork (history, transcript, etc.) once the call is answered.
        """
        us = await settings_service.for_user(user_id)
        try:
            await self._calls.upsert(sid, user_id, {
                "phone": phone,
                "from_number": "",
                "status": "ringing",
                "agent_name": us.get("agent_name"),
                "agency_name": us.get("agency_name"),
                "started_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as exc:
            # If the DB write fails, log and continue. The greeting
            # webhook will hit get_user_for_sid → None and hang up
            # gracefully, which is the right failure mode (better than
            # blowing up the API call to /api/call).
            log.error("[%s] register_outbound DB upsert failed: %s",
                      sid, exc)

    async def get_user_for_sid(self, sid: str) -> str | None:
        """Look up the owning user for a call SID. Used by webhooks
        that arrive without our customParameters context (e.g.
        recording-status, call-status)."""
        try:
            return await self._calls.get_user_for_sid(sid)
        except Exception as exc:
            log.warning("get_user_for_sid(%s) failed: %s", sid, exc)
            return None

    # ────────────────────────────────────────────────────────
    # Status + recording webhooks (unchanged from v9)
    # ────────────────────────────────────────────────────────
    async def handle_call_status(
        self, sid: str, status: str, duration: int,
    ) -> None:
        log.info("[%s] Status=%s Duration=%ss", sid, status, duration)
        if status in ("completed", "failed", "no-answer", "busy", "canceled"):
            # The transcript was already persisted by the CR handler
            # at session close; we just update status + duration.
            _spawn(self._calls.finalize_by_sid(
                sid, status, duration, "", "", "",
            ))
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
                await self._calls.set_recording_by_sid(
                    sid, recording_url + ".mp3", "",
                )
            except Exception:
                pass

    # ────────────────────────────────────────────────────────
    # Delete-call hook (kept from v9 for the dashboard delete feature)
    # ────────────────────────────────────────────────────────
    def discard_state(self, sid: str) -> None:
        """No-op in v10 — there's no orchestrator-owned in-memory state
        to discard. WebSocket handlers own their own state and tear
        down on disconnect.

        Kept as a method (rather than removed) so the delete endpoint's
        call site doesn't have to change. The endpoint expects this to
        exist and tolerates it being a no-op.
        """
        return


call_orchestrator = CallOrchestrator()
