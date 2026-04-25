"""
app.services.storage.supabase_storage
─────────────────────────────────────
Bucket management + call-recording uploads. Downloads the MP3 from Twilio,
pushes it to Supabase Storage, returns a public URL.
"""
from __future__ import annotations

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)


class SupabaseStorage:
    def _base_url(self) -> str:
        return f"{get_settings().supabase_url}/storage/v1"

    def _headers(self) -> dict[str, str]:
        key = get_settings().supabase_service_key
        return {"apikey": key, "Authorization": f"Bearer {key}"}

    async def ensure_bucket(self) -> None:
        """Create the recordings bucket if missing. No-op if Supabase not set."""
        s = get_settings()
        if not s.supabase_service_key or not s.supabase_url:
            log.warning("Supabase not configured — skipping bucket bootstrap.")
            return

        base = self._base_url()
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{base}/bucket/{s.supabase_bucket}",
                headers=self._headers(),
            )
            if resp.status_code == 200:
                return
            resp = await client.post(
                f"{base}/bucket",
                headers={**self._headers(), "Content-Type": "application/json"},
                json={"id": s.supabase_bucket, "name": s.supabase_bucket, "public": True},
            )
            if resp.status_code in (200, 201):
                log.info("✅ Storage bucket '%s' created", s.supabase_bucket)
            else:
                log.warning("Bucket create returned %s: %s", resp.status_code, resp.text)

    async def upload_recording(
        self, call_sid: str, recording_twilio_url: str
    ) -> tuple[str, str]:
        """Download the recording from Twilio and upload it to Supabase.

        Returns:
            (public_url, storage_path) — or (fallback_twilio_mp3_url, "") on error.
        """
        s = get_settings()
        if not s.supabase_service_key or not recording_twilio_url:
            return recording_twilio_url, ""

        mp3_url = (
            recording_twilio_url
            if recording_twilio_url.endswith(".mp3")
            else recording_twilio_url + ".mp3"
        )

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                dl = await client.get(
                    mp3_url,
                    auth=(s.twilio_account_sid, s.twilio_auth_token),
                    follow_redirects=True,
                )
            if dl.status_code != 200:
                log.warning("Twilio recording download failed: %s", dl.status_code)
                return mp3_url, ""
            audio_bytes = dl.content
            log.info("Downloaded recording: %d bytes", len(audio_bytes))

            path = f"recordings/{call_sid}.mp3"
            upload_headers = {
                **self._headers(),
                "Content-Type": "audio/mpeg",
                "x-upsert": "true",
            }
            async with httpx.AsyncClient(timeout=30) as client:
                up = await client.post(
                    f"{self._base_url()}/object/{s.supabase_bucket}/{path}",
                    headers=upload_headers,
                    content=audio_bytes,
                )
            if up.status_code in (200, 201):
                public_url = (
                    f"{s.supabase_url}/storage/v1/object/public/"
                    f"{s.supabase_bucket}/{path}"
                )
                log.info("✅ Recording uploaded: %s", public_url)
                return public_url, path

            log.error("Storage upload failed: %s %s", up.status_code, up.text[:400])
            return mp3_url, ""
        except Exception as exc:
            log.error("Recording upload error: %s", exc)
            return mp3_url, ""

    async def get_signed_url(self, path: str, expires_in: int = 3600) -> str:
        """Return a signed URL for a private recording. Empty string on failure."""
        if not path:
            return ""
        s = get_settings()
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{self._base_url()}/object/sign/{s.supabase_bucket}/{path}",
                headers={**self._headers(), "Content-Type": "application/json"},
                json={"expiresIn": expires_in},
            )
            if resp.status_code == 200:
                return resp.json().get("signedURL", "")
        return ""

    async def delete_recording(self, path: str) -> bool:
        """Delete a recording object from the bucket.

        Idempotent and forgiving:
          • Empty path → no-op, returns True.
          • Supabase not configured → no-op, returns True.
          • File not found (404) → treated as success (already gone).
          • Network/storage error → returns False; caller logs it but
            the call row will already be deleted from the DB by then,
            so an orphaned object is the worst case.

        Why we tolerate failures rather than throwing:
        the user clicked "delete" and the DB row is the source of truth
        for what they see. A storage cleanup hiccup shouldn't surface as
        an error in their face — we'd rather succeed loudly and reconcile
        orphaned files later if needed.
        """
        if not path:
            return True
        s = get_settings()
        if not s.supabase_service_key or not s.supabase_url:
            log.warning("Supabase not configured — skipping storage delete.")
            return True

        url = f"{self._base_url()}/object/{s.supabase_bucket}/{path}"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.delete(url, headers=self._headers())
            if resp.status_code in (200, 204):
                log.info("✅ Recording deleted: %s", path)
                return True
            if resp.status_code == 404:
                log.info("Recording already missing in storage: %s", path)
                return True
            log.error(
                "Storage delete failed (%s): %s — %s",
                path, resp.status_code, resp.text[:300],
            )
            return False
        except Exception as exc:
            log.error("Recording delete error for %s: %s", path, exc)
            return False


storage = SupabaseStorage()
