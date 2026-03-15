"""
UAE Real Estate AI Calling Bot — FastAPI + Frontend
Sara | Prestige Properties Dubai
"""
import logging
from datetime import datetime
from typing import Optional

import httpx
import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import Response, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from config import (
    BASE_URL, PORT,
    TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM,
    INTRO_TEXT, AGENT_NAME, AGENCY_NAME,
)
from services.groq     import get_reply
from services.cartesia import synthesize
from services.clean    import clean_reply
from services.sheets   import save_row

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(title=f"{AGENCY_NAME} — AI Calling Bot")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── In-memory stores ──────────────────────────────────────────────────────────
# store keys per call_sid:
#   transcript_{sid}  — list of "Speaker: text" strings (for Sheets)
#   history_{sid}     — list of {role, content} dicts (for Groq multi-turn)
#   to_{sid}          — destination phone number
#   start_{sid}       — ISO timestamp of call start
#   pending_{sid}     — latest TTS text waiting to be fetched
store: dict    = {}
call_log: list = []
_intro_cache: Optional[bytes] = None


# ══════════════════════════════════════════════════════════════════════════════
# FRONTEND
# ══════════════════════════════════════════════════════════════════════════════
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def index():
    return FileResponse("static/index.html")


# ══════════════════════════════════════════════════════════════════════════════
# API — called by the dashboard frontend
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/call")
async def api_call(request: Request):
    """Start an outbound call from the dashboard"""
    body  = await request.json()
    phone = (body.get("phone") or "").strip()
    if not phone:
        return JSONResponse({"success": False, "error": "Phone number required"}, status_code=400)
    if not phone.startswith("+"):
        phone = "+" + phone

    log.info(f"Dialing → {phone}")

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
        call_log.insert(0, {
            "sid":        sid,
            "phone":      phone,
            "status":     "ringing",
            "hot_lead":   False,
            "duration":   "—",
            "started_at": datetime.utcnow().strftime("%d %b %Y %H:%M UTC"),
            "recording":  "",
            "transcript": [],
        })
        log.info(f"Call started SID={sid}")
        return {"success": True, "sid": sid}

    log.error(f"Twilio {resp.status_code}: {resp.text}")
    try:
        err = resp.json().get("message", resp.text)
    except Exception:
        err = resp.text
    return JSONResponse({"success": False, "error": err}, status_code=400)


@app.get("/api/calls")
async def api_calls():
    return {"calls": call_log}


@app.get("/api/status")
async def api_status():
    return {
        "agent":             AGENT_NAME,
        "agency":            AGENCY_NAME,
        "twilio_configured": bool(TWILIO_AUTH_TOKEN),
        "from_number":       TWILIO_FROM,
        "base_url":          BASE_URL,
    }


# ══════════════════════════════════════════════════════════════════════════════
# TWILIO WEBHOOKS
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/twiml-greeting")
async def twiml_greeting():
    """Called by Twilio when call is answered — plays intro, gathers speech"""
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n'
        f'  <Gather input="speech" action="{BASE_URL}/process-speech" method="POST"'
        f' speechTimeout="1" language="en-US">\n'
        f'    <Play>{BASE_URL}/intro-audio</Play>\n'
        f'  </Gather>\n'
        f'  <Redirect>{BASE_URL}/twiml-greeting</Redirect>\n'
        '</Response>'
    )
    return Response(content=twiml, media_type="text/xml")


@app.get("/intro-audio")
async def intro_audio():
    """Generate intro TTS once, cache and serve as WAV"""
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
    """Main AI loop — speech → Groq → TTS → TwiML"""
    call_sid = CallSid or ""
    to_num   = To or ""
    speech   = (SpeechResult or "").strip()
    log.info(f"[{call_sid}] Speech: '{speech}'")

    # ── Init call state on first exchange ────────────────────────────────
    if f"transcript_{call_sid}" not in store:
        store[f"transcript_{call_sid}"] = []
        store[f"history_{call_sid}"]    = []   # ← multi-turn history
        store[f"to_{call_sid}"]         = to_num
        store[f"start_{call_sid}"]      = datetime.utcnow().isoformat()
        _update_log(call_sid, status="answered")

    customer_text = speech if speech else "hello"

    # Append customer speech to transcript & history
    if speech:
        store[f"transcript_{call_sid}"].append(f"Customer: {speech}")
        _update_log(call_sid, append_transcript=f"Customer: {speech}")
    store[f"history_{call_sid}"].append({"role": "user", "content": customer_text})

    # ── Groq with full conversation history ──────────────────────────────
    # Pass all prior turns except the one we just appended
    prior_history = store[f"history_{call_sid}"][:-1]
    raw_reply = await get_reply(customer_text, history=prior_history)

    # ── Parse and clean reply ─────────────────────────────────────────────
    reply_text, end_call, is_hot_lead = clean_reply(raw_reply)
    log.info(f"[{call_sid}] → '{reply_text}' end={end_call} hot={is_hot_lead}")

    # Append AI reply to transcript & history
    store[f"transcript_{call_sid}"].append(f"{AGENT_NAME} (AI): {reply_text}")
    store[f"history_{call_sid}"].append({"role": "assistant", "content": reply_text})
    store[f"pending_{call_sid}"] = reply_text
    _update_log(call_sid, append_transcript=f"{AGENT_NAME} (AI): {reply_text}")
    if is_hot_lead:
        _update_log(call_sid, hot_lead=True)

    # ── Build TwiML response ──────────────────────────────────────────────
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
            f'  <Gather input="speech" action="{BASE_URL}/process-speech" method="POST"'
            f' speechTimeout="3" language="en-US">\n'
            f'    <Play>{audio_url}</Play>\n'
            f'  </Gather>\n'
            f'  <Redirect>{BASE_URL}/twiml-greeting</Redirect>\n'
            '</Response>'
        )
    return Response(content=twiml, media_type="text/xml")


@app.get("/reply-audio")
async def reply_audio(sid: str = ""):
    """Fetch pending reply text, generate TTS, return WAV"""
    text  = store.pop(f"pending_{sid}", "Thank you, have a great day!")
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
    """Twilio final status callback — update log and save to Sheets"""
    call_sid  = CallSid      or ""
    status    = CallStatus   or ""
    duration  = CallDuration or "0"
    recording = RecordingUrl or ""

    log.info(f"[{call_sid}] Status={status} Duration={duration}s")

    transcript_list = store.pop(f"transcript_{call_sid}", [])
    transcript      = "\n".join(transcript_list)
    stored_to       = store.pop(f"to_{call_sid}",    To or "")
    call_start      = store.pop(f"start_{call_sid}", datetime.utcnow().isoformat())
    store.pop(f"history_{call_sid}",  None)
    store.pop(f"pending_{call_sid}",  None)

    _update_log(call_sid,
        status=status,
        duration=duration + "s",
        recording=(recording + ".mp3") if recording else "",
    )

    if status in ("completed", "failed", "no-answer", "busy"):
        await save_row({
            "Date & Time":             call_start,
            "Phone Number":            stored_to or To or "",
            "Status":                  status,
            "Duration (sec)":          duration,
            "Recording Link":          (recording + ".mp3") if recording else "No recording",
            "Conversation Transcript": transcript or "No transcript",
        })

    return Response(content="OK", media_type="text/plain")


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _update_log(sid: str, status: str = None, duration: str = None,
                recording: str = None, append_transcript: str = None,
                hot_lead: bool = False):
    for entry in call_log:
        if entry["sid"] == sid:
            if status:                    entry["status"]   = status
            if duration:                  entry["duration"] = duration
            if recording is not None:     entry["recording"] = recording
            if hot_lead:                  entry["hot_lead"] = True
            if append_transcript:         entry["transcript"].append(append_transcript)
            return
    # Not found — create placeholder (status callback before api/call)
    call_log.insert(0, {
        "sid":        sid,
        "phone":      "",
        "status":     status or "unknown",
        "hot_lead":   hot_lead,
        "duration":   duration or "—",
        "started_at": datetime.utcnow().strftime("%d %b %Y %H:%M UTC"),
        "recording":  recording or "",
        "transcript": [append_transcript] if append_transcript else [],
    })


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=False)
