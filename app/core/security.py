"""
app.core.security
─────────────────
Authentication dependencies:

  • `get_current_user`     — returns the logged-in user, or raises AuthError.
  • `get_optional_user`    — returns the user or None (for pages that render
                             sign-in links vs. dashboard).
  • `verify_twilio_signature` — HMAC-verify Twilio webhook bodies.

Session cookies are opaque tokens; the DB is the source of truth.

Sliding-session refresh:
  When we successfully resolve a user from a session, we stash the raw
  token + its DB expiry on `request.state`. A response middleware (see
  `app.main`) then decides whether to bump the row's expiry and reissue
  the cookie with a fresh Max-Age. Doing it here instead of inside the
  dependency keeps read paths side-effect-free.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Cookie, Depends, Request

from app.core.config import Settings, get_settings
from app.core.exceptions import AuthError
from app.core.session_tokens import hash_token, verify_token_shape


# ═══════════════════════════════════════════════════════════════
# Auth principal
# ═══════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class AuthUser:
    id: str
    email: str
    full_name: str | None
    is_admin: bool


# ═══════════════════════════════════════════════════════════════
# Current-user resolution
# ═══════════════════════════════════════════════════════════════
async def _lookup_user_by_session(
    token: str | None,
    secret: str,
    request: Request | None = None,
) -> AuthUser | None:
    """Shared helper — returns AuthUser if the session is valid & live,
    else None. Never raises; caller decides what to do with None.

    When `request` is provided and resolution succeeds, the raw token
    and session's current DB expiry are stored on `request.state` so
    the sliding-refresh middleware can decide whether to extend."""
    if not token or not verify_token_shape(token, secret):
        return None

    # Late import — the DB pool is optional during module import.
    from app.db.repositories.sessions import SessionsRepository
    from app.db.repositories.users import UsersRepository

    row = await SessionsRepository().find_live(hash_token(token))
    if row is None:
        return None

    user = await UsersRepository().get_by_id(row["user_id"])
    if user is None or not user.get("is_active", True):
        return None

    if request is not None:
        # Make info available to the sliding-refresh middleware.
        request.state.session_token = token
        request.state.session_expires_at = row["expires_at"]

    return AuthUser(
        id=str(user["id"]),
        email=user["email"],
        full_name=user.get("full_name"),
        is_admin=bool(user.get("is_admin", False)),
    )


async def get_current_user(
    settings: Annotated[Settings, Depends(get_settings)],
    request: Request,
    session_cookie: Annotated[str | None, Cookie(alias="callsara_session")] = None,
) -> AuthUser:
    """FastAPI dependency — 401s if the caller isn't signed in."""
    # Allow the cookie name to be overridden via settings; FastAPI's Cookie
    # binding is static so we fall back to manually reading the header.
    token = session_cookie or request.cookies.get(settings.session_cookie_name)
    user = await _lookup_user_by_session(token, settings.session_secret, request)
    if user is None:
        raise AuthError("Not authenticated.")
    return user


async def get_optional_user(
    settings: Annotated[Settings, Depends(get_settings)],
    request: Request,
    session_cookie: Annotated[str | None, Cookie(alias="callsara_session")] = None,
) -> AuthUser | None:
    """Like get_current_user, but returns None instead of raising."""
    token = session_cookie or request.cookies.get(settings.session_cookie_name)
    return await _lookup_user_by_session(token, settings.session_secret, request)


async def require_admin(
    user: Annotated[AuthUser, Depends(get_current_user)],
) -> AuthUser:
    """Dependency that requires an admin user."""
    if not user.is_admin:
        raise AuthError("Admin privileges required.")
    return user


# ═══════════════════════════════════════════════════════════════
# Twilio signature verification
# ═══════════════════════════════════════════════════════════════
def _compute_twilio_signature(auth_token: str, url: str, params: dict[str, str]) -> str:
    """Per Twilio: base64( HMAC-SHA1( auth_token, url + sorted(k+v for params) ))"""
    data = url + "".join(f"{k}{v}" for k, v in sorted(params.items()))
    mac = hmac.new(auth_token.encode("utf-8"), data.encode("utf-8"), hashlib.sha1)
    return base64.b64encode(mac.digest()).decode("utf-8")


async def verify_twilio_signature(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Reject webhooks that don't carry a valid X-Twilio-Signature."""
    if not settings.verify_twilio_signature:
        return
    if not settings.twilio_auth_token:
        raise AuthError("Twilio signature verification enabled but auth token missing.")

    signature = request.headers.get("X-Twilio-Signature", "")
    if not signature:
        raise AuthError("Missing X-Twilio-Signature header.")

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


# ═══════════════════════════════════════════════════════════════
# Utility for auth endpoints
# ═══════════════════════════════════════════════════════════════
def session_expiry(hours: int) -> datetime:
    from datetime import timedelta
    return datetime.now(timezone.utc) + timedelta(hours=hours)
