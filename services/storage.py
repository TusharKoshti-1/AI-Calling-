"""
services/storage.py — Supabase Storage
Uploads call recordings (MP3 from Twilio) to Supabase Storage bucket
Returns public URL stored in the calls table
"""
import logging
import httpx
from config import SUPABASE_URL, SUPABASE_KEY, SUPABASE_BUCKET

log = logging.getLogger(__name__)

STORAGE_BASE = f"{SUPABASE_URL}/storage/v1"
HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
}


async def ensure_bucket():
    """Create recordings bucket if it doesn't exist"""
    if not SUPABASE_KEY:
        return
    async with httpx.AsyncClient(timeout=10) as client:
        # Check if bucket exists
        r = await client.get(f"{STORAGE_BASE}/bucket/{SUPABASE_BUCKET}", headers=HEADERS)
        if r.status_code == 200:
            return  # already exists
        # Create bucket (public so recording links work without auth)
        r = await client.post(
            f"{STORAGE_BASE}/bucket",
            headers={**HEADERS, "Content-Type": "application/json"},
            json={"id": SUPABASE_BUCKET, "name": SUPABASE_BUCKET, "public": True}
        )
        if r.status_code in (200, 201):
            log.info(f"✅ Storage bucket '{SUPABASE_BUCKET}' created")
        else:
            log.warning(f"Bucket create: {r.status_code} {r.text}")


async def upload_recording(call_sid: str, recording_twilio_url: str) -> tuple[str, str]:
    """
    Download recording from Twilio, upload to Supabase Storage.
    Returns (public_url, storage_path) or ("", "") on failure.
    """
    if not SUPABASE_KEY or not recording_twilio_url:
        return recording_twilio_url, ""

    from config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN

    try:
        # Download from Twilio (MP3)
        mp3_url = recording_twilio_url if recording_twilio_url.endswith(".mp3") else recording_twilio_url + ".mp3"
        async with httpx.AsyncClient(timeout=30) as client:
            dl = await client.get(
                mp3_url,
                auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
                follow_redirects=True
            )
            if dl.status_code != 200:
                log.warning(f"Twilio recording download failed: {dl.status_code}")
                return mp3_url, ""

            audio_bytes = dl.content
            log.info(f"Downloaded recording: {len(audio_bytes)} bytes")

        # Upload to Supabase Storage
        path = f"recordings/{call_sid}.mp3"
        upload_headers = {
            **HEADERS,
            "Content-Type": "audio/mpeg",
            "x-upsert": "true",
        }
        async with httpx.AsyncClient(timeout=30) as client:
            up = await client.post(
                f"{STORAGE_BASE}/object/{SUPABASE_BUCKET}/{path}",
                headers=upload_headers,
                content=audio_bytes,
            )
            if up.status_code in (200, 201):
                public_url = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET}/{path}"
                log.info(f"✅ Recording uploaded: {public_url}")
                return public_url, path
            else:
                log.error(f"Storage upload failed: {up.status_code} {up.text}")
                return mp3_url, ""

    except Exception as e:
        log.error(f"Recording upload error: {e}")
        return recording_twilio_url + ".mp3", ""


async def get_signed_url(path: str, expires_in: int = 3600) -> str:
    """Get a temporary signed URL for a private recording"""
    if not path:
        return ""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            f"{STORAGE_BASE}/object/sign/{SUPABASE_BUCKET}/{path}",
            headers={**HEADERS, "Content-Type": "application/json"},
            json={"expiresIn": expires_in}
        )
        if r.status_code == 200:
            return r.json().get("signedURL", "")
    return ""
