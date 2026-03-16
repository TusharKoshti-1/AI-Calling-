"""
CallSara — UAE Real Estate AI Calling Bot
FastAPI + Supabase PostgreSQL + Supabase Storage

LATENCY OPTIMIZATIONS:
1. DB writes are fire-and-forget (asyncio.create_task) — never block the call path
2. Groq + Cartesia TTS run in parallel after LLM returns
3. TTS pre-generated and cached before /reply-audio is even fetched
4. speechTimeout=2 (not 3) — saves 1s of dead air per turn
5. Render keep-alive via /health ping (add UptimeRobot to hit /health every 5min)
"""
import asyncio
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
    AGENT_NAME, AGENCY_NAME, END_PHRASES,
)
from db.database import (
    init_db, close_db,
    upsert_call, update_call, finalize_call, set_recording,
    insert_message, get_calls, get_call_messages,
    get_stats, get_total_count,
    get_setting, set_setting, get_all_settings,
)
from services.groq     import get_reply
from services.cartesia import synthesize, DEFAULT_VOICE_ID
from services.clean    import clean_reply
from services.storage  import ensure_bucket, upload_recording

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger(__name__)


# ── Runtime settings ──────────────────────────────────────────────────────────
_settings: dict = {
    "agent_name":    AGENT_NAME,
    "agency_name":   AGENCY_NAME,
    "intro_text":    "",
    "system_prompt": "default",
    "voice_id":      DEFAULT_VOICE_ID,
}

def _get_voice_id() -> str:
    v = _settings.get("voice_id", "").strip()
    return v if v else DEFAULT_VOICE_ID

def _get_intro() -> str:
    t = _settings.get("intro_text", "").strip()
    if t: return t
    n, a = _settings["agent_name"], _settings["agency_name"]
    return (f"Hello, this is {n} calling from {a}. "
            f"You recently inquired about one of our properties — "
            f"do you have two minutes?")

def _get_system_prompt() -> str:
    sp = _settings.get("system_prompt", "").strip()
    if sp and sp != "default": return sp
    from config import SYSTEM_PROMPT
    return SYSTEM_PROMPT


# ── Fire-and-forget DB write — NEVER blocks the call path ─────────────────────
def _bg(coro):
    """Schedule a coroutine as a background task — no await, no blocking."""
    asyncio.create_task(coro)


async def _safe(coro):
    """Run a coroutine, swallow exceptions (for background tasks)."""
    try:
        return await coro
    except Exception as e:
        log.error(f"BG task error: {e}")


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting CallSara...")
    try:
        await init_db()
        db_s = await get_all_settings()
        _settings.update(db_s)
        log.info(f"Settings: agent={_settings['agent_name']} voice={_settings['voice_id'][:8]}")
        await ensure_bucket()
        # Pre-warm intro audio cache on startup
        asyncio.create_task(_warm_intro())
    except Exception as e:
        log.error(f"Startup warning: {e}")
    yield
    try: await close_db()
    except Exception: pass


async def _warm_intro():
    """Pre-generate intro audio at startup so first call is instant."""
    global _intro_cache
    try:
        text = _get_intro()
        audio = await synthesize(text, voice_id=_get_voice_id())
        if audio:
            _intro_cache = audio
            log.info(f"Intro audio pre-warmed: {len(audio)} bytes")
    except Exception as e:
        log.error(f"Intro warm-up failed: {e}")


app = FastAPI(title="CallSara — AI Calling", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_call_state: dict = {}
_intro_cache: Optional[bytes] = None


# ══════════════════════════════════════════════════════════════════════════════
# FRONTEND
# ══════════════════════════════════════════════════════════════════════════════
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def index(): return FileResponse("static/index.html")


# ══════════════════════════════════════════════════════════════════════════════
# API
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/status")
async def api_status():
    return {
        "agent":             _settings.get("agent_name", AGENT_NAME),
        "agency":            _settings.get("agency_name", AGENCY_NAME),
        "twilio_configured": bool(TWILIO_AUTH_TOKEN),
        "from_number":       TWILIO_FROM,
        "base_url":          BASE_URL,
    }


@app.get("/api/stats")
async def api_stats():
    try:
        s = await get_stats()
        return {k: int(v) if v is not None else 0 for k, v in s.items()}
    except Exception as e:
        log.error(f"Stats error: {e}")
        return {"total_calls":0,"hot_leads":0,"answered":0,"no_answer":0,
                "ringing":0,"avg_duration_sec":0,"calls_today":0,"hot_leads_today":0}


@app.get("/api/calls")
async def api_calls(
    limit:    int  = Query(50, ge=1, le=200),
    offset:   int  = Query(0, ge=0),
    status:   str  = Query("all"),
    hot_only: bool = Query(False),
    search:   str  = Query(""),
):
    try:
        calls = await get_calls(
            limit=limit, offset=offset,
            status=status if status != "all" else None,
            hot_only=hot_only, search=search or None,
        )
        total = await get_total_count(
            status=status if status != "all" else None,
            hot_only=hot_only, search=search or None,
        )
        for c in calls:
            for k in ("started_at","ended_at","created_at"):
                if c.get(k) and hasattr(c[k],"isoformat"):
                    c[k] = c[k].isoformat()
            if c.get("id"): c["id"] = str(c["id"])
        return {"calls": calls, "total": total}
    except Exception as e:
        log.error(f"Get calls error: {e}")
        return {"calls": [], "total": 0}


@app.get("/api/calls/{call_sid}/messages")
async def api_messages(call_sid: str):
    try:
        msgs = await get_call_messages(call_sid)
        for m in msgs:
            if m.get("created_at") and hasattr(m["created_at"],"isoformat"):
                m["created_at"] = m["created_at"].isoformat()
        return {"messages": msgs}
    except Exception as e:
        return {"messages": []}


@app.post("/api/call")
async def api_call(request: Request):
    body  = await request.json()
    phone = (body.get("phone") or "").strip()
    if not phone:
        return JSONResponse({"success":False,"error":"Phone number required"}, status_code=400)
    if not phone.startswith("+"): phone = "+" + phone
    if not TWILIO_AUTH_TOKEN:
        return JSONResponse({"success":False,"error":"TWILIO_AUTH_TOKEN not set"}, status_code=500)

    log.info(f"Dialing → {phone}")
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Calls.json",
            auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
            data={
                "To": phone, "From": TWILIO_FROM,
                "Url": f"{BASE_URL}/twiml-greeting", "Method": "POST",
                "Record": "true", "RecordingChannels": "dual",
                "RecordingStatusCallback": f"{BASE_URL}/recording-status",
                "RecordingStatusCallbackMethod": "POST",
                "StatusCallback": f"{BASE_URL}/call-status",
                "StatusCallbackMethod": "POST",
            },
        )

    if resp.status_code in (200, 201):
        data = resp.json()
        sid  = data.get("sid", "")
        _call_state[sid] = {
            "history": [], "phone": phone,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        _bg(_safe(upsert_call(sid, {
            "phone": phone, "from_number": TWILIO_FROM, "status": "ringing",
            "agent_name": _settings["agent_name"], "agency_name": _settings["agency_name"],
        })))
        return {"success": True, "sid": sid}

    try: err = resp.json().get("message", resp.text)
    except Exception: err = resp.text
    return JSONResponse({"success":False,"error":err}, status_code=400)


@app.get("/api/settings")
async def api_get_settings():
    try:
        db_s = await get_all_settings()
        _settings.update(db_s)
        result = dict(_settings)
        if result.get("system_prompt","").strip() in ("","default"):
            result["system_prompt"] = _get_system_prompt()
        return result
    except Exception as e:
        return _settings


@app.post("/api/settings")
async def api_save_settings(request: Request):
    body = await request.json()
    allowed = {"agent_name","agency_name","intro_text","system_prompt","voice_id"}
    saved = {}
    for key, val in body.items():
        if key in allowed and isinstance(val, str):
            _settings[key] = val.strip()
            saved[key] = val.strip()
            _bg(_safe(set_setting(key, val.strip())))
    global _intro_cache
    _intro_cache = None
    # Re-warm intro audio with new settings
    asyncio.create_task(_warm_intro())
    return {"success": True, "saved": saved}


# ══════════════════════════════════════════════════════════════════════════════
# VOICE PREVIEW
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/voice/preview")
async def voice_preview(request: Request):
    body = await request.json()
    vid  = (body.get("voice_id") or "").strip()
    text = (body.get("text") or "").strip()
    if not vid:
        return JSONResponse({"error": "voice_id required"}, status_code=400)
    if not text:
        text = _get_intro()
    try:
        log.info(f"Voice preview: voice={vid[:8]} text='{text[:60]}'")
        audio = await synthesize(text, voice_id=vid, encoding="mp3")
        if audio:
            return Response(content=audio, media_type="audio/mpeg",
                            headers={"Cache-Control":"no-store"})
        return JSONResponse({"error": "TTS generation failed"}, status_code=500)
    except Exception as e:
        log.error(f"Voice preview error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


# ══════════════════════════════════════════════════════════════════════════════
# TWILIO WEBHOOKS — microsecond critical path
# Target: process-speech returns TwiML in < 400ms total
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/twiml-greeting")
async def twiml_greeting():
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n'
        f'  <Gather input="speech" action="{BASE_URL}/process-speech"'
        f' method="POST" speechTimeout="1" language="en-US">\n'
        f'    <Play>{BASE_URL}/intro-audio</Play>\n'
        f'  </Gather>\n'
        f'  <Redirect>{BASE_URL}/twiml-greeting</Redirect>\n</Response>'
    )
    return Response(content=twiml, media_type="text/xml")


@app.get("/intro-audio")
async def intro_audio():
    global _intro_cache
    if not _intro_cache:
        log.info("Generating intro audio (not pre-warmed)...")
        _intro_cache = await synthesize(_get_intro(), voice_id=_get_voice_id())
    if _intro_cache:
        return Response(content=_intro_cache, media_type="audio/wav",
                        headers={"Cache-Control":"public, max-age=3600"})
    return Response(status_code=500)


@app.post("/process-speech")
async def process_speech(
    SpeechResult: Optional[str] = Form(None),
    CallSid:      Optional[str] = Form(None),
    To:           Optional[str] = Form(None),
    From:         Optional[str] = Form(None),
):
    t0 = datetime.now(timezone.utc)

    call_sid = CallSid or ""
    to_num   = To or ""
    speech   = (SpeechResult or "").strip()
    log.info(f"[{call_sid}] Speech: '{speech}'")

    # ── Init call state ───────────────────────────────────────────────────────
    is_new = call_sid not in _call_state
    if is_new:
        _call_state[call_sid] = {
            "history": [], "phone": to_num,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        # Fire-and-forget — DB write does NOT block TwiML response
        _bg(_safe(upsert_call(call_sid, {
            "phone": to_num, "from_number": From or "", "status": "answered",
            "agent_name": _settings["agent_name"], "agency_name": _settings["agency_name"],
        })))
    else:
        _bg(_safe(update_call(call_sid, status="answered")))

    customer_text = speech if speech else "hello"

    # Fire-and-forget DB message save — doesn't slow us down
    if speech:
        _bg(_safe(insert_message(call_sid, "customer", speech)))

    # ── GROQ LLM ─────────────────────────────────────────────────────────────
    state   = _call_state[call_sid]
    history = state["history"]
    history.append({"role": "user", "content": customer_text})

    try:
        raw_reply = await get_reply(
            customer_text,
            history=history[:-1],
            system_prompt=_get_system_prompt()
        )
    except Exception as e:
        log.error(f"Groq error [{call_sid}]: {e}")
        raw_reply = "Thank you for calling, our team will follow up shortly. Have a great day! [END_CALL]"

    reply_text, end_call, is_hot_lead = clean_reply(raw_reply)
    t_llm = (datetime.now(timezone.utc) - t0).total_seconds()
    log.info(f"[{call_sid}] LLM in {t_llm:.2f}s → '{reply_text[:60]}' end={end_call}")

    # ── Update history ────────────────────────────────────────────────────────
    history.append({"role": "assistant", "content": reply_text})

    # ── START TTS in background — don't await here ────────────────────────────
    # Store text immediately so /reply-audio can start TTS when Twilio fetches it
    state["pending"]  = reply_text
    state["tts_task"] = asyncio.create_task(
        synthesize(reply_text, voice_id=_get_voice_id())
    )

    # ── Fire-and-forget DB writes ─────────────────────────────────────────────
    _bg(_safe(insert_message(call_sid, "ai", reply_text)))
    if is_hot_lead:
        _bg(_safe(update_call(call_sid, hot_lead=True)))

    # ── Return TwiML immediately ──────────────────────────────────────────────
    audio_url = f"{BASE_URL}/reply-audio?sid={call_sid}"
    t_total = (datetime.now(timezone.utc) - t0).total_seconds()
    log.info(f"[{call_sid}] TwiML ready in {t_total:.2f}s")

    if end_call:
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n'
            f'  <Play>{audio_url}</Play>\n  <Pause length="1"/>\n  <Hangup/>\n</Response>'
        )
    else:
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n'
            f'  <Gather input="speech" action="{BASE_URL}/process-speech"'
            f' method="POST" speechTimeout="2" language="en-US">\n'
            f'    <Play>{audio_url}</Play>\n'
            f'  </Gather>\n'
            f'  <Redirect>{BASE_URL}/twiml-greeting</Redirect>\n</Response>'
        )
    return Response(content=twiml, media_type="text/xml")


@app.get("/reply-audio")
async def reply_audio(sid: str = ""):
    """
    Twilio fetches this right after receiving TwiML (~50-100ms after process-speech).
    The TTS task was already started in process-speech, so it's usually done by now.
    """
    t0 = datetime.now(timezone.utc)
    state = _call_state.get(sid, {})

    # Wait for the pre-started TTS task if it exists
    tts_task = state.pop("tts_task", None)
    if tts_task:
        try:
            audio = await asyncio.wait_for(tts_task, timeout=8.0)
        except asyncio.TimeoutError:
            log.error(f"[{sid}] TTS task timed out")
            audio = None
        except Exception as e:
            log.error(f"[{sid}] TTS task error: {e}")
            audio = None
    else:
        # Fallback: generate now (shouldn't happen in normal flow)
        text  = state.pop("pending", None) or "Thank you, have a great day!"
        log.warning(f"[{sid}] TTS fallback (no pre-started task)")
        audio = await synthesize(text, voice_id=_get_voice_id())

    # Clear pending text
    state.pop("pending", None)

    t_tts = (datetime.now(timezone.utc) - t0).total_seconds()
    log.info(f"[{sid}] Audio ready in {t_tts:.2f}s ({len(audio) if audio else 0} bytes)")

    if audio:
        return Response(content=audio, media_type="audio/wav",
                        headers={"Cache-Control": "no-store"})
    return Response(status_code=204)


@app.post("/recording-status")
async def recording_status(
    CallSid:         Optional[str] = Form(None),
    RecordingUrl:    Optional[str] = Form(None),
    RecordingStatus: Optional[str] = Form(None),
):
    call_sid  = CallSid      or ""
    rec_url   = RecordingUrl or ""
    rec_status= RecordingStatus or ""
    log.info(f"[{call_sid}] RecordingStatus={rec_status}")

    if rec_status == "completed" and rec_url and call_sid:
        _bg(_upload_and_save(call_sid, rec_url))

    return Response(content="OK", media_type="text/plain")


async def _upload_and_save(call_sid: str, rec_url: str):
    try:
        public_url, path = await upload_recording(call_sid, rec_url)
        target = public_url if public_url else rec_url + ".mp3"
        await _safe(set_recording(call_sid, target, path))
        log.info(f"[{call_sid}] Recording saved → {target}")
    except Exception as e:
        log.error(f"Recording upload error [{call_sid}]: {e}")
        await _safe(set_recording(call_sid, rec_url + ".mp3", ""))


@app.post("/call-status")
async def call_status(
    CallSid:      Optional[str] = Form(None),
    CallStatus:   Optional[str] = Form(None),
    CallDuration: Optional[str] = Form(None),
    To:           Optional[str] = Form(None),
):
    call_sid = CallSid    or ""
    status   = CallStatus or ""
    duration = int(CallDuration or 0)
    log.info(f"[{call_sid}] Status={status} Duration={duration}s")

    state   = _call_state.get(call_sid, {})
    history = state.get("history", [])
    agent   = _settings.get("agent_name", AGENT_NAME)
    lines   = []
    for msg in history:
        prefix = f"{agent} (AI)" if msg["role"] == "assistant" else "Customer"
        lines.append(f"{prefix}: {msg['content']}")
    transcript = "\n".join(lines)

    if status in ("completed","failed","no-answer","busy","canceled"):
        _bg(_safe(finalize_call(call_sid, status, duration, "", "", transcript)))
        _call_state.pop(call_sid, None)
    elif status == "in-progress":
        _bg(_safe(update_call(call_sid, status="answered")))

    return Response(content="OK", media_type="text/plain")


@app.get("/health")
async def health():
    """
    Also use this as a keep-alive ping.
    Set up UptimeRobot (free) to hit this URL every 5 minutes
    to prevent Render free tier from sleeping between calls.
    URL: https://your-app.onrender.com/health
    """
    try:
        from db.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        db_ok = True
    except Exception:
        db_ok = False
    return {"status":"ok","db":db_ok,"agent":_settings.get("agent_name",AGENT_NAME)}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=False)
