"""
app/services/storage/supabase.py
Supabase Storage — upload call recordings from Twilio → Supabase bucket.
"""
import httpx
from typing import Optional
from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

_STORAGE_BASE = f"{settings.SUPABASE_URL}/storage/v1"
_HEADERS = {
    "apikey":        settings.SUPABASE_KEY,
    "Authorization": f"Bearer {settings.SUPABASE_KEY}",
}


async def ensure_bucket() -> None:
    """Create the recordings bucket if it does not exist."""
    if not settings.SUPABASE_KEY:
        log.warning("SUPABASE_KEY not set — storage unavailable")
        return
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            f"{_STORAGE_BASE}/bucket/{settings.SUPABASE_BUCKET}",
            headers=_HEADERS,
        )
        if r.status_code == 200:
            return
        r = await client.post(
            f"{_STORAGE_BASE}/bucket",
            headers={**_HEADERS, "Content-Type": "application/json"},
            json={"id": settings.SUPABASE_BUCKET, "name": settings.SUPABASE_BUCKET, "public": True},
        )
        if r.status_code in (200, 201):
            log.info(f"✅ Storage bucket '{settings.SUPABASE_BUCKET}' created")
        else:
            log.warning(f"Bucket create: {r.status_code} {r.text[:200]}")


async def upload_recording(call_sid: str, twilio_recording_url: str) -> tuple[str, str]:
    """
    Download MP3 from Twilio → upload to Supabase Storage.
    Returns (public_url, storage_path).
    Falls back to Twilio URL on any failure.
    """
    if not settings.SUPABASE_KEY or not twilio_recording_url:
        return twilio_recording_url, ""

    mp3_url = (
        twilio_recording_url
        if twilio_recording_url.endswith(".mp3")
        else twilio_recording_url + ".mp3"
    )

    try:
        # 1. Download from Twilio
        async with httpx.AsyncClient(timeout=60) as client:
            dl = await client.get(
                mp3_url,
                auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN),
                follow_redirects=True,
            )
            if dl.status_code != 200:
                log.warning(f"Twilio download failed {dl.status_code} for {call_sid}")
                return mp3_url, ""
            audio_bytes = dl.content
            log.info(f"Downloaded recording: {len(audio_bytes)} bytes [{call_sid}]")

        # 2. Upload to Supabase Storage
        path = f"recordings/{call_sid}.mp3"
        async with httpx.AsyncClient(timeout=60) as client:
            up = await client.post(
                f"{_STORAGE_BASE}/object/{settings.SUPABASE_BUCKET}/{path}",
                headers={**_HEADERS, "Content-Type": "audio/mpeg", "x-upsert": "true"},
                content=audio_bytes,
            )
            if up.status_code in (200, 201):
                public_url = (
                    f"{settings.SUPABASE_URL}/storage/v1/object/public"
                    f"/{settings.SUPABASE_BUCKET}/{path}"
                )
                log.info(f"✅ Recording uploaded [{call_sid}]: {public_url}")
                return public_url, path
            log.error(f"Storage upload failed {up.status_code}: {up.text[:200]}")
            return mp3_url, ""

    except Exception as e:
        log.error(f"upload_recording error [{call_sid}]: {e}")
        return mp3_url, ""
