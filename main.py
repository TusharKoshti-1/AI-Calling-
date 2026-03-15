"""
CallSara — UAE Real Estate AI Calling Bot
FastAPI + Supabase PostgreSQL + Supabase Storage
"""
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

import httpx
import uvicorn
from fastapi import FastAPI, Form, Request, Query
from fastapi.responses import Response, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from config import (
    BASE_URL, PORT,
    TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM,
    INTRO_TEXT, AGENT_NAME, AGENCY_NAME,
)
from db.database import (
    init_db, close_db,
    upsert_call, update_call, finalize_call,
    insert_message, get_calls, get_call_messages,
    get_stats, get_total_count,
)
from services.groq     import get_reply
from services.cartesia import synthesize
from services.clean    import clean_reply
from services.storage  import ensure_bucket, upload_recording

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger(__name__)


# ── Lifespan ──────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting CallSara...")
    await init_db()
    await ensure_bucket()
    yield
    await close_db()
    log.info("CallSara stopped.")


app = FastAPI(title=f"{AGENCY_NAME} — AI Calling", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

# ── In-memory call state (live calls only) ────────────────────
# Cleared when call ends. DB is source of truth for history.
_call_state: dict = {}   # sid → {history, started_at}
_intro_cache: Optional[bytes] = None


# ══════════════════════════════════════════════════════════════
# FRONTEND
# ══════════════════════════════════════════════════════════════
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def index():
    return FileResponse("static/index.html")


# ══════════════════════════════════════════════════════════════
# API — dashboard
# ══════════════════════════════════════════════════════════════

@app.get("/api/status")
async def api_status():
    return {
        "agent":             AGENT_NAME,
        "agency":            AGENCY_NAME,
        "twilio_configured": bool(TWILIO_AUTH_TOKEN),
        "from_number":       TWILIO_FROM,
        "base_url":          BASE_URL,
    }


@app.get("/api/stats")
async def api_stats():
    try:
        stats = await get_stats()
        # Convert Decimal/int to plain types
        return {k: int(v) if v is not None else 0 for k, v in stats.items()}
    except Exception as e:
        log.error(f"Stats error: {e}")
        return {"total_calls":0,"hot_leads":0,"answered":0,"no_answer":0,"ringing":0,"avg_duration_sec":0,"calls_today":0,"hot_leads_today":0}


@app.get("/api/calls")
async def api_calls(
    limit:    int  = Query(50,  ge=1, le=200),
    offset:   int  = Query(0,   ge=0),
    status:   str  = Query("all"),
    hot_only: bool = Query(False),
    search:   str  = Query(""),
):
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
        # Serialize datetimes
        for c in calls:
            for k in ("started_at","ended_at","created_at"):
                if c.get(k) and hasattr(c[k], "isoformat"):
                    c[k] = c[k].isoformat()
            if c.get("id"):
                c["id"] = str(c["id"])
        return {"calls": calls, "total": total}
    except Exception as e:
        log.error(f"Get calls error: {e}")
        return {"calls": [], "total": 0}


@app.get("/api/calls/{call_sid}/messages")
async def api_messages(call_sid: str):
    try:
        msgs = await get_call_messages(call_sid)
        for m in msgs:
            if m.get("created_at") and hasattr(m["created_at"], "isoformat"):
                m["created_at"] = m["created_at"].isoformat()
        return {"messages": msgs}
    except Exception as e:
        log.error(f"Get messages error: {e}")
        return {"messages": []}


@app.post("/api/call")
async def api_call(request: Request):
    """Initiate outbound call from dashboard"""
    body  = await request.json()
    phone = (body.get("phone") or "").strip()
    if not phone:
        return JSONResponse({"success": False, "error": "Phone number required"}, status_code=400)
    if not phone.startswith("+"):
        phone = "+" + phone
    if not TWILIO_AUTH_TOKEN:
        return JSONResponse({"success": False, "error": "TWILIO_AUTH_TOKEN not configured"}, status_code=500)

    log.info(f"Dialing → {phone}")

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Calls.json",
            auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
            data={
                "To":                   phone,
                "From":                 TWILIO_FROM,
                "Url":                  f"{BASE_URL}/twiml-greeting",
                "Method":               "POST",
                "Record":               "true",
                "RecordingChannels":    "dual",
                "StatusCallback":       f"{BASE_URL}/call-status",
                "StatusCallbackMethod": "POST",
            },
        )

    if resp.status_code in (200, 201):
        data = resp.json()
        sid  = data.get("sid", "")
        # Save to DB immediately
        await upsert_call(sid, {
            "phone":       phone,
            "from_number": TWILIO_FROM,
            "status":      "ringing",
            "agent_name":  AGENT_NAME,
            "agency_name": AGENCY_NAME,
        })
        # Init in-memory state
        _call_state[sid] = {
            "history":    [],
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        log.info(f"Call started SID={sid}")
        return {"success": True, "sid": sid}

    try:
        err = resp.json().get("message", resp.text)
    except Exception:
        err = resp.text
    log.error(f"Twilio error {resp.status_code}: {err}")
    return JSONResponse({"success": False, "error": err}, status_code=400)


# ══════════════════════════════════════════════════════════════
# TWILIO WEBHOOKS
# ══════════════════════════════════════════════════════════════

@app.post("/twiml-greeting")
async def twiml_greeting():
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n'
        f'  <Gather input="speech" action="{BASE_URL}/process-speech"'
        f' method="POST" speechTimeout="1" language="en-US">\n'
        f'    <Play>{BASE_URL}/intro-audio</Play>\n'
        f'  </Gather>\n'
        f'  <Redirect>{BASE_URL}/twiml-greeting</Redirect>\n'
        '</Response>'
    )
    return Response(content=twiml, media_type="text/xml")


@app.get("/intro-audio")
async def intro_audio():
    global _intro_cache
    if not _intro_cache:
        log.info("Generating intro audio...")
        _intro_cache = await synthesize(INTRO_TEXT)
    if _intro_cache:
        return Response(content=_intro_cache, media_type="audio/wav")
    return Response(status_code=500)


@app.post("/process-speech")
async def process_speech(
    SpeechResult: Optional[str] = Form(None),
    CallSid:      Optional[str] = Form(None),
    To:           Optional[str] = Form(None),
    From:         Optional[str] = Form(None),
):
    call_sid = CallSid or ""
    to_num   = To or ""
    speech   = (SpeechResult or "").strip()
    log.info(f"[{call_sid}] Speech: '{speech}'")

    # Init state if first exchange
    if call_sid not in _call_state:
        _call_state[call_sid] = {
            "history":    [],
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        await upsert_call(call_sid, {
            "phone":       to_num,
            "from_number": From or "",
            "status":      "answered",
            "agent_name":  AGENT_NAME,
            "agency_name": AGENCY_NAME,
        })
    else:
        await update_call(call_sid, status="answered")

    customer_text = speech if speech else "hello"

    # Save customer message to DB
    if speech:
        await insert_message(call_sid, "customer", speech)

    # Groq with full history
    state   = _call_state[call_sid]
    history = state["history"]
    history.append({"role": "user", "content": customer_text})

    raw_reply = await get_reply(customer_text, history=history[:-1])
    reply_text, end_call, is_hot_lead = clean_reply(raw_reply)
    log.info(f"[{call_sid}] Reply='{reply_text}' end={end_call} hot={is_hot_lead}")

    # Append AI reply to history
    history.append({"role": "assistant", "content": reply_text})

    # Save AI message to DB
    await insert_message(call_sid, "ai", reply_text)

    # Update hot lead flag if needed
    if is_hot_lead:
        await update_call(call_sid, hot_lead=True)

    # Store pending TTS text
    state["pending"] = reply_text

    audio_url = f"{BASE_URL}/reply-audio?sid={call_sid}"

    if end_call:
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n'
            f'  <Play>{audio_url}</Play>\n'
            '  <Pause length="1"/>\n'
            '  <Hangup/>\n'
            '</Response>'
        )
    else:
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n'
            f'  <Gather input="speech" action="{BASE_URL}/process-speech"'
            f' method="POST" speechTimeout="3" language="en-US">\n'
            f'    <Play>{audio_url}</Play>\n'
            f'  </Gather>\n'
            f'  <Redirect>{BASE_URL}/twiml-greeting</Redirect>\n'
            '</Response>'
        )
    return Response(content=twiml, media_type="text/xml")


@app.get("/reply-audio")
async def reply_audio(sid: str = ""):
    state = _call_state.get(sid, {})
    text  = state.pop("pending", None) or "Thank you, have a great day!"
    audio = await synthesize(text)
    if audio:
        return Response(content=audio, media_type="audio/wav")
    return Response(status_code=500)


@app.post("/call-status")
async def call_status(
    CallSid:      Optional[str] = Form(None),
    CallStatus:   Optional[str] = Form(None),
    CallDuration: Optional[str] = Form(None),
    RecordingUrl: Optional[str] = Form(None),
    To:           Optional[str] = Form(None),
):
    call_sid  = CallSid      or ""
    status    = CallStatus   or ""
    duration  = int(CallDuration or 0)
    recording = RecordingUrl or ""

    log.info(f"[{call_sid}] Status={status} Duration={duration}s Recording={recording}")

    # Build transcript from DB messages
    msgs = await get_call_messages(call_sid)
    transcript_lines = []
    for m in msgs:
        prefix = f"{AGENT_NAME} (AI)" if m["role"] == "ai" else "Customer"
        transcript_lines.append(f"{prefix}: {m['content']}")
    transcript = "\n".join(transcript_lines)

    # Upload recording to Supabase Storage
    rec_url, rec_path = "", ""
    if recording and status in ("completed",):
        rec_url, rec_path = await upload_recording(call_sid, recording)

    if not rec_url and recording:
        rec_url = recording + ".mp3"

    # Finalize in DB
    await finalize_call(
        sid=call_sid,
        status=status,
        duration_sec=duration,
        recording_url=rec_url,
        recording_path=rec_path,
        transcript=transcript,
    )

    # Clean up in-memory state
    _call_state.pop(call_sid, None)

    return Response(content="OK", media_type="text/plain")


# ══════════════════════════════════════════════════════════════
# HEALTH
# ══════════════════════════════════════════════════════════════

@app.get("/health")
async def health():
    try:
        from db.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        db_ok = True
    except Exception:
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "db": db_ok}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=False)
