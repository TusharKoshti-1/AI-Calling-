"""
app.core.security
─────────────────
Authentication & webhook signature verification.

Two layers of protection:
  • Admin API endpoints are guarded by a shared API key (header or query).
  • Twilio webhooks are verified via X-Twilio-Signature HMAC.

For true multi-tenant SaaS auth, replace `admin_api_key_dep` with a real
JWT / Supabase Auth dependency. The surface area is already abstracted
behind a single dependency so the swap is mechanical.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Annotated

from fastapi import Depends, Header, Query, Request

from app.core.config import Settings, get_settings
from app.core.exceptions import AuthError


# ═══════════════════════════════════════════════════════════════
# Admin API key
# ═══════════════════════════════════════════════════════════════
async def require_admin_api_key(
    settings: Annotated[Settings, Depends(get_settings)],
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    api_key: Annotated[str | None, Query(alias="api_key")] = None,
) -> None:
    """FastAPI dependency that enforces a valid admin API key.

    In non-production environments with no key configured, access is open
    (so local dev doesn't need extra setup). In production, a key MUST be set.
    """
    expected = (settings.admin_api_key or "").strip()
    if not expected:
        if settings.is_production:
            raise AuthError(
                "ADMIN_API_KEY is not configured — refusing to serve admin API "
                "in production without auth."
            )
        return  # open in dev

    provided = (x_api_key or api_key or "").strip()
    if not provided or not hmac.compare_digest(provided, expected):
        raise AuthError("Invalid or missing API key.")


# ═══════════════════════════════════════════════════════════════
# Twilio signature verification
# ═══════════════════════════════════════════════════════════════
def _compute_twilio_signature(auth_token: str, url: str, params: dict[str, str]) -> str:
    """Reproduce Twilio's signing algorithm:
      sorted(params) concatenated onto the URL, HMAC-SHA1, base64-encoded.
    """
    data = url + "".join(f"{k}{v}" for k, v in sorted(params.items()))
    mac = hmac.new(auth_token.encode("utf-8"), data.encode("utf-8"), hashlib.sha1)
    return base64.b64encode(mac.digest()).decode("utf-8")


async def verify_twilio_signature(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """FastAPI dependency that verifies a request originated from Twilio.

    Can be disabled via `VERIFY_TWILIO_SIGNATURE=false` (useful for local dev
    with a plain HTTP tunnel that mangles the URL).
    """
    if not settings.verify_twilio_signature:
        return
    if not settings.twilio_auth_token:
        # Can't verify without the token — treat as misconfiguration
        raise AuthError("Twilio signature verification enabled but auth token missing.")

    signature = request.headers.get("X-Twilio-Signature", "")
    if not signature:
        raise AuthError("Missing X-Twilio-Signature header.")

    # Twilio signs the full URL as the caller sees it. Respect proxy headers.
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host") or request.url.netloc
    url = f"{proto}://{host}{request.url.path}"
    if request.url.query:
        url += f"?{request.url.query}"

    form = await request.form()
    params = {k: str(v) for k, v in form.items()}
    expected = _compute_twilio_signature(settings.twilio_auth_token, url, params)

    if not hmac.compare_digest(expected, signature):
        raise AuthError("Twilio signature mismatch.")
