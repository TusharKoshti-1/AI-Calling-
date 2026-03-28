"""
app/api/v1/endpoints/calls.py
REST API — call management (initiate, list, detail, messages).
"""
from fastapi import APIRouter, Request, Query
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.state import call_store, CallSession
from app.core.logging import get_logger
from app.services import settings_service
from app.services.telephony.twilio_client import make_call
from db.repositories.calls import (
    get_calls, get_call_messages, get_stats, get_total_count,
    upsert_call,
)

log = get_logger(__name__)
router = APIRouter(prefix="/calls", tags=["calls"])


@router.post("")
async def initiate_call(request: Request):
    """POST /api/v1/calls — dial a number."""
    body  = await request.json()
    phone = (body.get("phone") or "").strip()

    if not phone:
        return JSONResponse({"success": False, "error": "Phone number required"}, status_code=400)
    if not phone.startswith("+"):
        phone = "+" + phone
    if not settings.TWILIO_AUTH_TOKEN:
        return JSONResponse({"success": False, "error": "TWILIO_AUTH_TOKEN not configured"}, status_code=500)

    result = await make_call(to=phone)

    if result["success"]:
        sid = result["sid"]
        # Create in-memory session
        call_store.create(
            sid=sid,
            phone=phone,
            agent_name=settings_service.get_agent_name(),
            agency_name=settings_service.get_agency_name(),
            system_prompt=settings_service.get_system_prompt(),
        )
        # Persist to DB
        try:
            await upsert_call(sid, {
                "phone":       phone,
                "from_number": settings.TWILIO_FROM,
                "status":      "ringing",
                "agent_name":  settings_service.get_agent_name(),
                "agency_name": settings_service.get_agency_name(),
                "provider":    settings.TELEPHONY_PROVIDER,
            })
        except Exception as e:
            log.error(f"DB upsert error on initiate [{sid}]: {e}")

        log.info(f"Call started SID={sid} → {phone}")
        return {"success": True, "sid": sid, "status": result.get("status")}

    return JSONResponse({"success": False, "error": result.get("error")}, status_code=400)


@router.get("")
async def list_calls(
    limit:    int  = Query(50,  ge=1, le=200),
    offset:   int  = Query(0,   ge=0),
    status:   str  = Query("all"),
    hot_only: bool = Query(False),
    search:   str  = Query(""),
):
    """GET /api/v1/calls — paginated call list."""
    try:
        calls = await get_calls(
            limit=limit, offset=offset,
            status=status if status != "all" else None,
            hot_only=hot_only,
            search=search or None,
        )
        total = await get_total_count(
            status=status if status != "all" else None,
            hot_only=hot_only,
            search=search or None,
        )
        for c in calls:
            for k in ("started_at", "ended_at", "created_at"):
                if c.get(k) and hasattr(c[k], "isoformat"):
                    c[k] = c[k].isoformat()
            if c.get("id"):
                c["id"] = str(c["id"])
        return {"calls": calls, "total": total, "limit": limit, "offset": offset}
    except Exception as e:
        log.error(f"list_calls error: {e}")
        return {"calls": [], "total": 0}


@router.get("/stats")
async def call_stats():
    """GET /api/v1/calls/stats — dashboard stat cards."""
    try:
        s = await get_stats()
        return {k: int(v) if v is not None else 0 for k, v in s.items()}
    except Exception as e:
        log.error(f"call_stats error: {e}")
        return {
            "total_calls": 0, "hot_leads": 0, "answered": 0,
            "no_answer": 0, "ringing": 0, "avg_duration_sec": 0,
            "calls_today": 0, "hot_leads_today": 0,
        }


@router.get("/{call_sid}/messages")
async def call_messages(call_sid: str):
    """GET /api/v1/calls/{sid}/messages — full transcript."""
    try:
        msgs = await get_call_messages(call_sid)
        for m in msgs:
            if m.get("created_at") and hasattr(m["created_at"], "isoformat"):
                m["created_at"] = m["created_at"].isoformat()
        return {"messages": msgs, "call_sid": call_sid}
    except Exception as e:
        log.error(f"call_messages error [{call_sid}]: {e}")
        return {"messages": [], "call_sid": call_sid}
