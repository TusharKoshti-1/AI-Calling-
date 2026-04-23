"""
app.services.telephony.twilio_client
────────────────────────────────────
Thin async Twilio REST wrapper for the one call we actually make
(initiating an outbound call). Also centralises TwiML generation so the
API layer doesn't hand-roll XML strings.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.core.config import get_settings
from app.core.exceptions import ConfigurationError, UpstreamError, ValidationError
from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class DialResult:
    sid: str
    phone: str


class TwilioClient:
    async def initiate_call(self, phone: str) -> DialResult:
        """Place an outbound call via Twilio. Returns the new call SID."""
        phone = self._normalize_phone(phone)
        s = get_settings()

        if not s.twilio_account_sid or not s.twilio_auth_token:
            raise ConfigurationError("Twilio credentials are not configured.")
        if not s.twilio_from:
            raise ConfigurationError("TWILIO_FROM (caller ID) is not configured.")
        if phone == s.twilio_from:
            raise ValidationError("Refusing to dial the Twilio caller-ID number.")

        url = (
            f"https://api.twilio.com/2010-04-01/Accounts/"
            f"{s.twilio_account_sid}/Calls.json"
        )
        data = {
            "To": phone,
            "From": s.twilio_from,
            "Url": f"{s.base_url}/webhooks/twilio/greeting",
            "Method": "POST",
            "Record": "true",
            "RecordingChannels": "dual",
            "RecordingStatusCallback": f"{s.base_url}/webhooks/twilio/recording-status",
            "RecordingStatusCallbackMethod": "POST",
            "StatusCallback": f"{s.base_url}/webhooks/twilio/call-status",
            "StatusCallbackMethod": "POST",
        }

        log.info("Dialing → %s", phone)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                url,
                auth=(s.twilio_account_sid, s.twilio_auth_token),
                data=data,
            )

        if resp.status_code in (200, 201):
            sid = resp.json().get("sid", "")
            return DialResult(sid=sid, phone=phone)

        # Twilio returns a `message` field on error — bubble it up cleanly.
        try:
            err = resp.json().get("message", resp.text)
        except Exception:
            err = resp.text
        raise UpstreamError(f"Twilio dial failed: {err}")

    @staticmethod
    def _normalize_phone(phone: str) -> str:
        phone = (phone or "").strip().replace(" ", "").replace("-", "")
        if not phone:
            raise ValidationError("Phone number is required.")
        if not phone.startswith("+"):
            phone = "+" + phone
        # Minimal sanity: must be +, digits, and reasonable length.
        digits = phone[1:]
        if not digits.isdigit() or not (7 <= len(digits) <= 15):
            raise ValidationError(f"Invalid phone number format: {phone!r}")
        return phone


twilio_client = TwilioClient()
