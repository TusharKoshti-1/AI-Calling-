"""
app.api.v1.endpoints.calls
──────────────────────────
User-scoped: every call here resolves the signed-in user via the
`get_current_user` dependency and scopes queries to that user_id.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.logging import get_logger
from app.core.security import AuthUser, get_current_user
from app.db.repositories.calls import CallsRepository
from app.db.repositories.messages import MessagesRepository
from app.schemas.calls import DialRequest, DialResponse
from app.services.call_orchestrator import call_orchestrator
from app.services.storage import storage
from app.services.telephony import twilio_client

log = get_logger(__name__)

router = APIRouter(tags=["calls"])
_calls = CallsRepository()
_messages = MessagesRepository()


def _serialize_call(c: dict) -> dict:
    for key in ("started_at", "ended_at", "created_at"):
        val = c.get(key)
        if val is not None and hasattr(val, "isoformat"):
            c[key] = val.isoformat()
    if c.get("id") is not None:
        c["id"] = str(c["id"])
    return c


@router.get("/api/stats")
async def api_stats(
    user: Annotated[AuthUser, Depends(get_current_user)],
) -> dict:
    try:
        s = await _calls.stats(user.id)
        return {k: int(v) if v is not None else 0 for k, v in s.items()}
    except Exception as exc:
        log.error("Stats error: %s", exc)
        return {
            "total_calls": 0, "hot_leads": 0, "answered": 0, "no_answer": 0,
            "ringing": 0, "avg_duration_sec": 0, "calls_today": 0,
            "hot_leads_today": 0,
        }


@router.get("/api/calls")
async def api_calls(
    user: Annotated[AuthUser, Depends(get_current_user)],
    limit:    Annotated[int, Query(ge=1, le=200)] = 50,
    offset:   Annotated[int, Query(ge=0)] = 0,
    status:   Annotated[str, Query()] = "all",
    hot_only: Annotated[bool, Query()] = False,
    search:   Annotated[str, Query()] = "",
) -> dict:
    try:
        calls = await _calls.list(
            user_id=user.id,
            limit=limit, offset=offset,
            status=status if status != "all" else None,
            hot_only=hot_only, search=search or None,
        )
        total = await _calls.count(
            user_id=user.id,
            status=status if status != "all" else None,
            hot_only=hot_only, search=search or None,
        )
        return {"calls": [_serialize_call(c) for c in calls], "total": total}
    except Exception as exc:
        log.error("Get calls error: %s", exc)
        return {"calls": [], "total": 0}


@router.get("/api/calls/{call_sid}/messages")
async def api_messages(
    call_sid: str,
    user: Annotated[AuthUser, Depends(get_current_user)],
) -> dict:
    try:
        msgs = await _messages.list_for_call(call_sid=call_sid, user_id=user.id)
        for m in msgs:
            ca = m.get("created_at")
            if ca is not None and hasattr(ca, "isoformat"):
                m["created_at"] = ca.isoformat()
        return {"messages": msgs}
    except Exception as exc:
        log.error("Get messages error: %s", exc)
        return {"messages": []}


@router.post("/api/call", response_model=DialResponse)
async def api_call(
    body: DialRequest,
    user: Annotated[AuthUser, Depends(get_current_user)],
) -> DialResponse:
    try:
        result = await twilio_client.initiate_call(body.phone)
    except Exception as exc:
        log.warning("Dial failed: %s", exc)
        return DialResponse(success=False, error=str(exc))

    await call_orchestrator.register_outbound(
        sid=result.sid, user_id=user.id, phone=result.phone,
    )
    return DialResponse(success=True, sid=result.sid)


@router.delete("/api/calls/{call_id}")
async def api_delete_call(
    call_id: str,
    user: Annotated[AuthUser, Depends(get_current_user)],
) -> dict:
    """Permanently delete a call and everything attached to it.

    Cleanup performed:
      1. DB row in `calls` (the WHERE clause is scoped by user_id, so
         users can only delete their own calls — see CallsRepository).
      2. All `messages` rows for the call — happens automatically via
         the ON DELETE CASCADE on messages.call_id.
      3. The recording audio file in Supabase Storage, if any.
      4. Any in-memory CallState (in case the call is still active in
         this worker) — prevents a dangling conversation if a user
         deletes a call that's mid-flight in another tab.

    The DB delete is awaited (so the user gets an accurate response
    about whether anything was actually removed). The storage delete
    is fire-and-forget — its outcome doesn't change what the dashboard
    shows, and we don't want to make the user wait on object storage
    when the row is already gone.
    """
    # Validate UUID shape before touching the DB. asyncpg would raise
    # a DataError on non-UUID input which we'd catch as 500 — but a
    # malformed id from the frontend is really a "not found" case
    # from the user's perspective, so we surface it as 404.
    try:
        uuid.UUID(call_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=404, detail="call not found") from None

    try:
        deleted = await _calls.delete_by_id(call_id, user.id)
    except Exception as exc:
        log.error("Delete call %s failed: %s", call_id, exc)
        raise HTTPException(status_code=500, detail="delete failed") from exc

    if deleted is None:
        # Either the id doesn't exist OR it belongs to another user.
        # Same response either way — never leak the existence of other
        # tenants' calls via differential 404 vs 403.
        raise HTTPException(status_code=404, detail="call not found")

    # Drop in-memory state for this SID so any straggling webhooks
    # don't try to keep talking to a deleted call.
    sid = deleted.get("sid", "")
    if sid:
        try:
            call_orchestrator.discard_state(sid)
        except Exception as exc:
            log.warning("discard_state(%s) failed: %s", sid, exc)

    # Fire off the storage cleanup without blocking the response.
    rec_path = (deleted.get("recording_path") or "").strip()
    if rec_path:
        async def _cleanup() -> None:
            try:
                await storage.delete_recording(rec_path)
            except Exception as exc:
                # Already logged inside delete_recording; this is a
                # belt-and-braces guard so the task can never crash
                # the event loop.
                log.error("Recording cleanup task failed: %s", exc)
        asyncio.create_task(_cleanup())

    return {"success": True, "sid": sid}
