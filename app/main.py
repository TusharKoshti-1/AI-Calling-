"""
app.main
────────
FastAPI app factory and process entry point.

Page routing:
  /              → dashboard (if signed in) or signin (if not)
  /signin        → signin.html
  /signup        → signup.html
  /dashboard     → dashboard.html  (auth-gated)
  /calls         → calls.html      (auth-gated)
  /hot           → hot.html        (auth-gated)
  /dialer        → dialer.html     (auth-gated)
  /settings      → settings.html   (auth-gated)
  /voice         → voice.html      (auth-gated)

Auth-gating on HTML pages is a belt-and-suspenders redirect — every API
call still enforces auth independently via `get_current_user`.

Sliding-session middleware:
  After any authenticated request, if the session has <session_refresh_within_hours
  remaining we extend the DB expiry and reissue the cookie. This keeps active
  SaaS users signed in indefinitely without forcing re-login every 30 days.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import uvicorn
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.logging import configure_logging, get_logger
from app.core.security import AuthUser, get_optional_user, session_expiry
from app.db.session import close_pool, init_pool
from app.services.storage import storage

_STATIC_DIR = Path(__file__).parent / "static"
_PAGES_DIR = _STATIC_DIR / "pages"


@asynccontextmanager
async def lifespan(app: FastAPI):
    log = get_logger(__name__)
    log.info("Starting CallSara…")
    try:
        await init_pool()
        await storage.ensure_bucket()
    except Exception as exc:
        log.error("Startup warning: %s", exc)
    yield
    try:
        await close_pool()
    except Exception:
        pass


def create_app() -> FastAPI:
    s = get_settings()
    configure_logging(s.log_level)
    log = get_logger(__name__)

    app = FastAPI(
        title="CallSara — AI Calling",
        version="0.3.1",
        lifespan=lifespan,
        docs_url="/docs" if not s.is_production else None,
        redoc_url=None,
    )

    # CORS — only used by non-browser API consumers. Browser dashboards
    # live on the same origin so CORS doesn't affect them.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,   # required so cookies flow with CORS
    )

    # ── Sliding-session refresh middleware ────────────────────
    # When a request was authenticated (i.e. the auth dependency
    # populated request.state.session_token) and the session is
    # about to expire, we bump its expiry forward and reissue the
    # cookie. This is the "keep me signed in while I'm active"
    # behaviour users expect from a SaaS.
    @app.middleware("http")
    async def sliding_session_refresh(request: Request, call_next):
        response = await call_next(request)
        try:
            token: str | None = getattr(request.state, "session_token", None)
            current_exp: datetime | None = getattr(
                request.state, "session_expires_at", None
            )
            if not token or current_exp is None:
                return response

            # Normalise tz
            if current_exp.tzinfo is None:
                current_exp = current_exp.replace(tzinfo=timezone.utc)

            now = datetime.now(timezone.utc)
            remaining = (current_exp - now).total_seconds() / 3600.0

            # If still has plenty of life left, do nothing (avoid a DB
            # write on every single request).
            if remaining >= s.session_refresh_within_hours:
                return response

            # Extend session in DB and reissue cookie with full TTL.
            new_exp = session_expiry(s.session_ttl_hours)
            from app.core.session_tokens import hash_token
            from app.db.repositories.sessions import SessionsRepository
            try:
                await SessionsRepository().extend(hash_token(token), new_exp)
            except Exception as exc:
                # Best-effort — never fail the request because of a refresh hiccup.
                log.debug("Sliding refresh DB extend failed: %s", exc)
                return response

            response.set_cookie(
                key=s.session_cookie_name,
                value=token,
                max_age=s.session_ttl_hours * 3600,
                httponly=True,
                secure=s.effective_cookie_secure,
                samesite=s.session_cookie_samesite,
                path="/",
            )
        except Exception as exc:
            # Never let the refresh path break a response.
            log.debug("Sliding refresh skipped: %s", exc)
        return response

    @app.exception_handler(AppError)
    async def _app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.message, "code": exc.code},
        )

    # ── API routes ────────────────────────────────────────────
    app.include_router(api_router)

    # ── Static assets ─────────────────────────────────────────
    app.mount(
        "/static",
        StaticFiles(directory=str(_STATIC_DIR)),
        name="static",
    )

    # ── Page routes ───────────────────────────────────────────
    def _page(filename: str) -> FileResponse:
        return FileResponse(str(_PAGES_DIR / filename))

    async def _gated(
        filename: str,
        user: AuthUser | None,
        request: Request,
    ):
        """Redirect to /signin if not logged in, otherwise serve the page.

        Preserves the original path+query as `?next=` so signin can bounce
        the user back to where they were trying to go."""
        if user is None:
            next_path = request.url.path
            if request.url.query:
                next_path = f"{next_path}?{request.url.query}"
            from urllib.parse import quote
            return RedirectResponse(
                url=f"/signin?next={quote(next_path, safe='')}",
                status_code=303,
            )
        return _page(filename)

    @app.get("/", include_in_schema=False)
    async def root(
        request: Request,
        user: Annotated[AuthUser | None, Depends(get_optional_user)],
    ):
        if user is None:
            return RedirectResponse(url="/signin", status_code=303)
        return RedirectResponse(url="/dashboard", status_code=303)

    @app.get("/signin", include_in_schema=False)
    async def page_signin(
        request: Request,
        user: Annotated[AuthUser | None, Depends(get_optional_user)],
    ):
        if user is not None:
            return RedirectResponse(url="/dashboard", status_code=303)
        return _page("signin.html")

    @app.get("/signup", include_in_schema=False)
    async def page_signup(
        request: Request,
        user: Annotated[AuthUser | None, Depends(get_optional_user)],
    ):
        if user is not None:
            return RedirectResponse(url="/dashboard", status_code=303)
        return _page("signup.html")

    @app.get("/dashboard", include_in_schema=False)
    async def page_dashboard(
        request: Request,
        user: Annotated[AuthUser | None, Depends(get_optional_user)],
    ):
        return await _gated("dashboard.html", user, request)

    @app.get("/calls", include_in_schema=False)
    async def page_calls(
        request: Request,
        user: Annotated[AuthUser | None, Depends(get_optional_user)],
    ):
        return await _gated("calls.html", user, request)

    @app.get("/hot", include_in_schema=False)
    async def page_hot(
        request: Request,
        user: Annotated[AuthUser | None, Depends(get_optional_user)],
    ):
        return await _gated("hot.html", user, request)

    @app.get("/dialer", include_in_schema=False)
    async def page_dialer(
        request: Request,
        user: Annotated[AuthUser | None, Depends(get_optional_user)],
    ):
        return await _gated("dialer.html", user, request)

    @app.get("/settings", include_in_schema=False)
    async def page_settings(
        request: Request,
        user: Annotated[AuthUser | None, Depends(get_optional_user)],
    ):
        return await _gated("settings.html", user, request)

    @app.get("/voice", include_in_schema=False)
    async def page_voice(
        request: Request,
        user: Annotated[AuthUser | None, Depends(get_optional_user)],
    ):
        return await _gated("voice.html", user, request)

    return app


app = create_app()


if __name__ == "__main__":
    s = get_settings()
    uvicorn.run("app.main:app", host="0.0.0.0", port=s.port, reload=False)
