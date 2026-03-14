"""
TVS iQube AI Calling Bot — FastAPI + Frontend
"""
import logging
from datetime import datetime
from typing import Optional

import httpx
import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from config import BASE_URL, PORT, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM, INTRO_TEXT
from services.groq     import get_reply
from services.cartesia import synthesize
from services.clean    import clean_reply
from services.sheets   import save_row

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(title="TVS iQube AI Calling Bot")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── In-memory stores ──────────────────────────────────────────────────────────
store: dict = {}          # call state during live calls
call_log: list = []       # completed call records shown in UI
_intro_cache: Optional[bytes] = None


# ══════════════════════════════════════════════════════════════════════════════
# FRONTEND
# ══════════════════════════════════════════════════════════════════════════════

app.mount("/static", StaticFiles(directory="static"), name="static")

from fastapi.responses import FileResponse

@app.get("/")
async def index():
    return FileResponse("static/index.html")


# ══════════════════════════════════════════════════════════════════════════════
# API — used by frontend
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/call")
async def api_call(request: Request):
    """Frontend calls this to start a new outbound call"""
    body = await request.json()
    phone = (body.get("phone") or "").strip()
    if not phone:
        return JSONResponse({"success": False, "error": "Phone number required"}, status_code=400)
    if not phone.startswith("+"):
        phone = "+" + phone

    log.info(f"API call request → {phone}")

    async with httpx.AsyncClient(timeout=10) as client:
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
        # Add to live call log immediately as "ringing"
        call_log.insert(0, {
            "sid":        sid,
            "phone":      phone,
            "status":     "ringing",
            "duration":   "—",
            "started_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
            "recording":  "",
            "transcript": [],
        })
        log.info(f"Call started: SID={sid}")
        return {"success": True, "sid": sid}

    log.error(f"Twilio error {resp.status_code}: {resp.text}")
    try:
        err = resp.json().get("message", resp.text)
    except Exception:
        err = resp.text
    return JSONResponse({"success": False, "error": err}, status_code=400)


@app.get("/api/calls")
async def api_calls():
    """Return all call records for the dashboard"""
    return {"calls": call_log}


@app.get("/api/status")
async def api_status():
    """Return config status so frontend can show what's configured"""
    return {
        "twilio_configured": bool(TWILIO_AUTH_TOKEN),
        "from_number": TWILIO_FROM,
        "base_url": BASE_URL,
    }


# ══════════════════════════════════════════════════════════════════════════════
# TWILIO WEBHOOKS
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/twiml-greeting")
async def twiml_greeting():
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n'
        f'  <Gather input="speech" action="{BASE_URL}/process-speech" method="POST" speechTimeout="1" language="hi-IN">\n'
        f'    <Play>{BASE_URL}/intro-audio</Play>\n'
        f'  </Gather>\n'
        f'  <Redirect>{BASE_URL}/twiml-greeting</Redirect>\n</Response>'
    )
    return Response(content=twiml, media_type="text/xml")


@app.get("/intro-audio")
async def intro_audio():
    global _intro_cache
    if not _intro_cache:
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

    # Init store
    if f"transcript_{call_sid}" not in store:
        store[f"transcript_{call_sid}"] = []
        store[f"to_{call_sid}"]    = to_num
        store[f"start_{call_sid}"] = datetime.utcnow().isoformat()
        # Update call log status to answered
        _update_log(call_sid, status="answered")

    if speech:
        store[f"transcript_{call_sid}"].append(f"Customer: {speech}")
        _update_log(call_sid, append_transcript=f"Customer: {speech}")

    customer_text = speech or "hello"

    raw_reply              = await get_reply(customer_text)
    reply_text, end_call   = clean_reply(raw_reply)
    log.info(f"[{call_sid}] Reply: '{reply_text}' end={end_call}")

    store[f"transcript_{call_sid}"].append(f"Priya (AI): {reply_text}")
    store[f"pending_{call_sid}"]   = reply_text
    _update_log(call_sid, append_transcript=f"Priya (AI): {reply_text}")

    audio_url = f"{BASE_URL}/reply-audio?sid={call_sid}"

    if end_call:
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n'
            f'  <Play>{audio_url}</Play>\n  <Pause length="1"/>\n  <Hangup/>\n</Response>'
        )
    else:
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n'
            f'  <Gather input="speech" action="{BASE_URL}/process-speech" method="POST" speechTimeout="3" language="hi-IN">\n'
            f'    <Play>{audio_url}</Play>\n'
            f'  </Gather>\n'
            f'  <Redirect>{BASE_URL}/twiml-greeting</Redirect>\n</Response>'
        )
    return Response(content=twiml, media_type="text/xml")


@app.get("/reply-audio")
async def reply_audio(sid: str = ""):
    text  = store.pop(f"pending_{sid}", "Haan sir, koi baat nahi.")
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
    duration  = CallDuration or "0"
    recording = RecordingUrl or ""

    log.info(f"[{call_sid}] Status={status} Duration={duration}s")

    transcript_list = store.pop(f"transcript_{call_sid}", [])
    transcript      = "\n".join(transcript_list)
    stored_to       = store.pop(f"to_{call_sid}",    To or "")
    call_start      = store.pop(f"start_{call_sid}", datetime.utcnow().isoformat())
    store.pop(f"pending_{call_sid}", None)

    # Update call log with final status
    _update_log(call_sid,
        status=status,
        duration=duration + "s",
        recording=(recording + ".mp3") if recording else "",
    )

    if status in ("completed","failed","no-answer","busy"):
        row = {
            "Date & Time":             call_start,
            "Phone Number":            stored_to or To or "",
            "Status":                  status,
            "Duration (sec)":          duration,
            "Recording Link":          (recording + ".mp3") if recording else "No recording",
            "Conversation Transcript": transcript or "No transcript",
        }
        await save_row(row)

    return Response(content="OK", media_type="text/plain")


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _update_log(sid: str, status: str = None, duration: str = None,
                recording: str = None, append_transcript: str = None):
    """Update an existing call_log entry by SID"""
    for entry in call_log:
        if entry["sid"] == sid:
            if status:
                entry["status"] = status
            if duration:
                entry["duration"] = duration
            if recording is not None:
                entry["recording"] = recording
            if append_transcript:
                entry["transcript"].append(append_transcript)
            return
    # SID not found (call-status arrived before api/call) — create it
    call_log.insert(0, {
        "sid":        sid,
        "phone":      "",
        "status":     status or "unknown",
        "duration":   duration or "—",
        "started_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "recording":  recording or "",
        "transcript": [append_transcript] if append_transcript else [],
    })


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=False)
