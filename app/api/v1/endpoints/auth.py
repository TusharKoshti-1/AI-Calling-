"""
app.api.v1.endpoints.auth
─────────────────────────
Signup / signin / signout / me.

Session cookies are HttpOnly — JavaScript on the dashboard never touches
them directly. Signout revokes the server-side session immediately.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from app.core.config import Settings, get_settings
from app.core.exceptions import AuthError, ValidationError
from app.core.logging import get_logger
from app.core.passwords import hash_password, verify_password
from app.core.security import AuthUser, get_current_user, session_expiry
from app.core.session_tokens import hash_token, issue_token
from app.db.repositories.sessions import SessionsRepository
from app.db.repositories.users import UsersRepository
from app.schemas.auth import (
    AuthResponse,
    AuthUserOut,
    SigninRequest,
    SignupRequest,
)

log = get_logger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])

_users = UsersRepository()
_sessions = SessionsRepository()


def _set_session_cookie(
    response: Response, token: str, settings: Settings
) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=settings.session_cookie_secure or settings.is_production,
        samesite=settings.session_cookie_samesite,
        path="/",
    )


def _clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        httponly=True,
        samesite=settings.session_cookie_samesite,
    )


@router.post("/signup", response_model=AuthResponse)
async def signup(
    body: SignupRequest,
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthResponse:
    if not settings.allow_public_signup:
        # First-user bootstrap is still allowed when signup is closed.
        if await _users.count() > 0:
            raise AuthError("Signup is currently closed.")

    existing = await _users.get_by_email(body.email)
    if existing:
        raise ValidationError("An account with that email already exists.")

    # First user in the system becomes an admin automatically.
    is_first = await _users.count() == 0

    pwd_hash = hash_password(body.password)
    user = await _users.create(
        email=body.email,
        password_hash=pwd_hash,
        full_name=body.full_name,
        is_admin=is_first,
    )

    # Immediately sign the user in.
    issued = issue_token(settings.session_secret)
    await _sessions.create(
        user_id=str(user["id"]),
        token_hash=issued.token_hash,
        expires_at=session_expiry(settings.session_ttl_hours),
        user_agent=request.headers.get("user-agent", "")[:400],
        ip=request.client.host if request.client else None,
    )
    _set_session_cookie(response, issued.token, settings)

    log.info("signup %s (admin=%s)", user["email"], is_first)
    return AuthResponse(user=AuthUserOut(
        id=str(user["id"]),
        email=user["email"],
        full_name=user.get("full_name"),
        is_admin=bool(user.get("is_admin", False)),
    ))


@router.post("/signin", response_model=AuthResponse)
async def signin(
    body: SigninRequest,
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthResponse:
    row = await _users.get_by_email(body.email)
    if row is None or not verify_password(body.password, row["password_hash"]):
        raise AuthError("Invalid email or password.")
    if not row.get("is_active", True):
        raise AuthError("This account has been disabled.")

    issued = issue_token(settings.session_secret)
    await _sessions.create(
        user_id=str(row["id"]),
        token_hash=issued.token_hash,
        expires_at=session_expiry(settings.session_ttl_hours),
        user_agent=request.headers.get("user-agent", "")[:400],
        ip=request.client.host if request.client else None,
    )
    await _users.touch_login(str(row["id"]))
    _set_session_cookie(response, issued.token, settings)

    log.info("signin %s", row["email"])
    return AuthResponse(user=AuthUserOut(
        id=str(row["id"]),
        email=row["email"],
        full_name=row.get("full_name"),
        is_admin=bool(row.get("is_admin", False)),
    ))


@router.post("/signout")
async def signout(
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    token = request.cookies.get(settings.session_cookie_name)
    if token:
        try:
            await _sessions.revoke(hash_token(token))
        except Exception as exc:
            # Signout always succeeds from the client's perspective.
            log.warning("Failed to revoke session on signout: %s", exc)
    _clear_session_cookie(response, settings)
    return {"success": True}


@router.get("/me", response_model=AuthUserOut)
async def me(
    user: Annotated[AuthUser, Depends(get_current_user)],
) -> AuthUserOut:
    return AuthUserOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        is_admin=user.is_admin,
    )
