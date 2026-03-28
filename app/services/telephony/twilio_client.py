"""
app/services/telephony/twilio_client.py
Twilio API calls — initiating outbound calls only.
Webhook handling is in the API routes (not here).
"""
import httpx
from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)


async def make_call(
    to: str,
    from_number: str = None,
    webhook_base: str = None,
) -> dict:
    """
    Initiate an outbound call via Twilio.
    Returns dict with {success, sid, status} or {success, error}.
    """
    from_num = from_number or settings.TWILIO_FROM
    base     = webhook_base or settings.BASE_URL

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{settings.TWILIO_ACCOUNT_SID}/Calls.json",
            auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN),
            data={
                "To":                            to,
                "From":                          from_num,
                "Url":                           f"{base}/webhooks/twilio/greeting",
                "Method":                        "POST",
                "Record":                        "true",
                "RecordingChannels":             "dual",
                "RecordingStatusCallback":       f"{base}/webhooks/twilio/recording-status",
                "RecordingStatusCallbackMethod": "POST",
                "StatusCallback":                f"{base}/webhooks/twilio/call-status",
                "StatusCallbackMethod":          "POST",
            },
        )

    if resp.status_code in (200, 201):
        data = resp.json()
        log.info(f"Twilio call initiated: SID={data.get('sid')} → {to}")
        return {"success": True, "sid": data.get("sid"), "status": data.get("status")}

    try:
        err = resp.json().get("message", resp.text)
    except Exception:
        err = resp.text
    log.error(f"Twilio error {resp.status_code}: {err}")
    return {"success": False, "error": err}


def build_twiml_greeting(base_url: str, started: bool = False) -> str:
    """
    Build TwiML for the greeting webhook.
    Only plays intro audio on first hit (started=False).
    On redirect loop (started=True), just listens — prevents intro repeating.
    """
    gather_open = (
        f'<Gather input="speech" action="{base_url}/webhooks/twilio/process-speech"'
        f' method="POST" speechTimeout="3" language="en-US">'
    )
    intro = f'  <Play>{base_url}/audio/intro</Play>' if not started else ''

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Response>\n'
        f'  {gather_open}\n'
        f'{intro}\n'
        '  </Gather>\n'
        f'  <Redirect method="POST">{base_url}/webhooks/twilio/greeting?started=1</Redirect>\n'
        '</Response>'
    )


def build_twiml_reply(audio_url: str, process_url: str, greeting_url: str,
                      end_call: bool) -> str:
    """Build TwiML that plays AI reply then either hangs up or gathers next speech."""
    if end_call:
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Response>\n'
            f'  <Play>{audio_url}</Play>\n'
            '  <Pause length="1"/>\n'
            '  <Hangup/>\n'
            '</Response>'
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Response>\n'
        f'  <Gather input="speech" action="{process_url}"'
        f' method="POST" speechTimeout="3" language="en-US">\n'
        f'    <Play>{audio_url}</Play>\n'
        '  </Gather>\n'
        f'  <Redirect method="POST">{greeting_url}?started=1</Redirect>\n'
        '</Response>'
    )
