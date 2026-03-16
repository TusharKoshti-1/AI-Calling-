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
from services.cartesia import synthesize
from services.clean    import clean_reply
from services.storage  import ensure_bucket, upload_recording

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger(__name__)


# ── Runtime settings cache (updated from DB on startup + via API) ─────────────
_settings: dict = {
    "agent_name":    AGENT_NAME,
    "agency_name":   AGENCY_NAME,
    "intro_text":    "",
    "system_prompt": "default",
}

def _get_intro() -> str:
    t = _settings.get("intro_text","").strip()
    if t: return t
    n, a = _settings["agent_name"], _settings["agency_name"]
    return (f"Hello, this is {n} calling from {a}. "
            f"You recently inquired about one of our properties — "
            f"I just wanted to follow up quickly. Do you have two minutes?")

def _get_system_prompt() -> str:
    sp = _settings.get("system_prompt","").strip()
    if sp and sp != "default": return sp
    n, a = _settings["agent_name"], _settings["agency_name"]
    from config import SYSTEM_PROMPT
    return SYSTEM_PROMPT


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting CallSara...")
    try:
        await init_db()
        # Load settings from DB into runtime cache
        db_settings = await get_all_settings()
        _settings.update(db_settings)
        log.info(f"Settings loaded: agent={_settings['agent_name']} agency={_settings['agency_name']}")
        await ensure_bucket()
    except Exception as e:
        log.error(f"Startup warning: {e}")
        log.warning("App starting with defaults — DB features may be unavailable")
    # Invalidate intro audio cache when settings change
    global _intro_cache
    _intro_cache = None
    yield
    try: await close_db()
    except Exception: pass
    log.info("CallSara stopped.")


app = FastAPI(title="CallSara — AI Calling", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_call_state: dict = {}
_intro_cache: Optional[bytes] = None


# ── Safe DB wrappers — call flow NEVER crashes on DB error ────────────────────
async def _db(fn, *args, **kwargs):
    try: return await fn(*args, **kwargs)
    except Exception as e: log.error(f"DB error in {fn.__name__}: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# FRONTEND
# ══════════════════════════════════════════════════════════════════════════════
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def index(): return FileResponse("static/index.html")


# ══════════════════════════════════════════════════════════════════════════════
# API — STATUS & STATS
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


# ══════════════════════════════════════════════════════════════════════════════
# API — CALLS
# ══════════════════════════════════════════════════════════════════════════════

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
        log.error(f"Messages error: {e}")
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
        sid  = data.get("sid","")
        _call_state[sid] = {
            "history": [], "phone": phone,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        await _db(upsert_call, sid, {
            "phone": phone, "from_number": TWILIO_FROM, "status": "ringing",
            "agent_name": _settings["agent_name"], "agency_name": _settings["agency_name"],
        })
        log.info(f"Call started SID={sid}")
        return {"success": True, "sid": sid}

    try: err = resp.json().get("message", resp.text)
    except Exception: err = resp.text
    return JSONResponse({"success":False,"error":err}, status_code=400)


# ══════════════════════════════════════════════════════════════════════════════
# API — SETTINGS
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/settings")
async def api_get_settings():
    try:
        db_settings = await get_all_settings()
        _settings.update(db_settings)
        # Return with default system prompt text if set to "default"
        result = dict(_settings)
        if result.get("system_prompt","").strip() in ("","default"):
            result["system_prompt"] = _get_system_prompt()
        return result
    except Exception as e:
        log.error(f"Get settings error: {e}")
        return _settings


@app.post("/api/settings")
async def api_save_settings(request: Request):
    body = await request.json()
    allowed = {"agent_name","agency_name","intro_text","system_prompt"}
    saved = {}
    for key, val in body.items():
        if key in allowed and isinstance(val, str):
            await _db(set_setting, key, val.strip())
            _settings[key] = val.strip()
            saved[key] = val.strip()
    # Invalidate intro audio cache so next call re-generates it
    global _intro_cache
    _intro_cache = None
    log.info(f"Settings updated: {list(saved.keys())}")
    return {"success": True, "saved": saved}


# ══════════════════════════════════════════════════════════════════════════════
# TWILIO WEBHOOKS
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
        text = _get_intro()
        log.info(f"Generating intro: {text[:80]}")
        _intro_cache = await synthesize(text)
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
    call_sid = CallSid or ""
    to_num   = To or ""
    speech   = (SpeechResult or "").strip()
    log.info(f"[{call_sid}] Speech: '{speech}'")

    if call_sid not in _call_state:
        _call_state[call_sid] = {
            "history": [], "phone": to_num,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        await _db(upsert_call, call_sid, {
            "phone": to_num, "from_number": From or "", "status": "answered",
            "agent_name": _settings["agent_name"], "agency_name": _settings["agency_name"],
        })
    else:
        await _db(update_call, call_sid, status="answered")

    customer_text = speech if speech else "hello"
    if speech:
        await _db(insert_message, call_sid, "customer", speech)

    state   = _call_state[call_sid]
    history = state["history"]
    history.append({"role":"user","content":customer_text})

    try:
        system_prompt = _get_system_prompt()
        raw_reply = await get_reply(customer_text, history=history[:-1],
                                    system_prompt=system_prompt)
    except Exception as e:
        log.error(f"Groq error [{call_sid}]: {e}")
        raw_reply = "Thank you for calling, our team will follow up with you. Have a great day! [END_CALL]"

    reply_text, end_call, is_hot_lead = clean_reply(raw_reply)
    log.info(f"[{call_sid}] → '{reply_text}' end={end_call} hot={is_hot_lead}")

    history.append({"role":"assistant","content":reply_text})
    await _db(insert_message, call_sid, "ai", reply_text)
    if is_hot_lead:
        await _db(update_call, call_sid, hot_lead=True)

    state["pending"] = reply_text
    audio_url = f"{BASE_URL}/reply-audio?sid={call_sid}"

    if end_call:
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n'
            f'  <Play>{audio_url}</Play>\n  <Pause length="1"/>\n  <Hangup/>\n</Response>'
        )
    else:
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n'
            f'  <Gather input="speech" action="{BASE_URL}/process-speech"'
            f' method="POST" speechTimeout="3" language="en-US">\n'
            f'    <Play>{audio_url}</Play>\n'
            f'  </Gather>\n'
            f'  <Redirect>{BASE_URL}/twiml-greeting</Redirect>\n</Response>'
        )
    return Response(content=twiml, media_type="text/xml")


@app.get("/reply-audio")
async def reply_audio(sid: str = ""):
    state = _call_state.get(sid, {})
    text  = state.pop("pending", None) or "Thank you, have a great day!"
    log.info(f"[{sid}] TTS: '{text[:80]}'")
    try:
        audio = await synthesize(text)
    except Exception as e:
        log.error(f"TTS error [{sid}]: {e}")
        audio = None
    if audio:
        return Response(content=audio, media_type="audio/wav",
                        headers={"Cache-Control":"no-store"})
    return Response(status_code=204)


@app.post("/recording-status")
async def recording_status(
    CallSid:      Optional[str] = Form(None),
    RecordingUrl: Optional[str] = Form(None),
    RecordingSid: Optional[str] = Form(None),
    RecordingStatus: Optional[str] = Form(None),
):
    """
    Twilio fires this specifically when a recording is ready.
    This is separate from call-status and is the CORRECT place to upload.
    """
    call_sid  = CallSid      or ""
    rec_url   = RecordingUrl or ""
    rec_status= RecordingStatus or ""

    log.info(f"[{call_sid}] RecordingStatus={rec_status} URL={rec_url}")

    if rec_status == "completed" and rec_url and call_sid:
        try:
            public_url, path = await upload_recording(call_sid, rec_url)
            if public_url:
                await _db(set_recording, call_sid, public_url, path)
                log.info(f"[{call_sid}] Recording saved → {public_url}")
            else:
                # Fallback: store Twilio URL directly
                await _db(set_recording, call_sid, rec_url+".mp3", "")
        except Exception as e:
            log.error(f"Recording upload error [{call_sid}]: {e}")
            await _db(set_recording, call_sid, rec_url+".mp3", "")

    return Response(content="OK", media_type="text/plain")


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

    # Build transcript from in-memory history
    state   = _call_state.get(call_sid, {})
    history = state.get("history", [])
    agent   = _settings.get("agent_name", AGENT_NAME)
    lines   = []
    for msg in history:
        prefix = f"{agent} (AI)" if msg["role"] == "assistant" else "Customer"
        lines.append(f"{prefix}: {msg['content']}")
    transcript = "\n".join(lines)

    if status in ("completed","failed","no-answer","busy","canceled"):
        # Recording URL is handled by /recording-status webhook
        # Just finalize call status + transcript here
        await _db(finalize_call, call_sid, status, duration, "", "", transcript)
        _call_state.pop(call_sid, None)
    elif status in ("in-progress",):
        await _db(update_call, call_sid, status="answered")

    return Response(content="OK", media_type="text/plain")


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
    return {"status":"ok","db":db_ok,"agent":_settings.get("agent_name",AGENT_NAME)}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=False)
