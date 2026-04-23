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
"""
from __future__ import annotations

from contextlib import asynccontextmanager
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
from app.core.security import AuthUser, get_optional_user
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

    app = FastAPI(
        title="CallSara — AI Calling",
        version="0.3.0",
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
    ):
        """Redirect to /signin if not logged in, otherwise serve the page."""
        if user is None:
            return RedirectResponse(url="/signin", status_code=303)
        return _page(filename)

    @app.get("/", include_in_schema=False)
    async def root(
        user: Annotated[AuthUser | None, Depends(get_optional_user)],
    ):
        if user is None:
            return RedirectResponse(url="/signin", status_code=303)
        return RedirectResponse(url="/dashboard", status_code=303)

    @app.get("/signin", include_in_schema=False)
    async def page_signin(
        user: Annotated[AuthUser | None, Depends(get_optional_user)],
    ):
        if user is not None:
            return RedirectResponse(url="/dashboard", status_code=303)
        return _page("signin.html")

    @app.get("/signup", include_in_schema=False)
    async def page_signup(
        user: Annotated[AuthUser | None, Depends(get_optional_user)],
    ):
        if user is not None:
            return RedirectResponse(url="/dashboard", status_code=303)
        return _page("signup.html")

    @app.get("/dashboard", include_in_schema=False)
    async def page_dashboard(
        user: Annotated[AuthUser | None, Depends(get_optional_user)],
    ):
        return await _gated("dashboard.html", user)

    @app.get("/calls", include_in_schema=False)
    async def page_calls(
        user: Annotated[AuthUser | None, Depends(get_optional_user)],
    ):
        return await _gated("calls.html", user)

    @app.get("/hot", include_in_schema=False)
    async def page_hot(
        user: Annotated[AuthUser | None, Depends(get_optional_user)],
    ):
        return await _gated("hot.html", user)

    @app.get("/dialer", include_in_schema=False)
    async def page_dialer(
        user: Annotated[AuthUser | None, Depends(get_optional_user)],
    ):
        return await _gated("dialer.html", user)

    @app.get("/settings", include_in_schema=False)
    async def page_settings(
        user: Annotated[AuthUser | None, Depends(get_optional_user)],
    ):
        return await _gated("settings.html", user)

    @app.get("/voice", include_in_schema=False)
    async def page_voice(
        user: Annotated[AuthUser | None, Depends(get_optional_user)],
    ):
        return await _gated("voice.html", user)

    return app


app = create_app()


if __name__ == "__main__":
    s = get_settings()
    uvicorn.run("app.main:app", host="0.0.0.0", port=s.port, reload=False)
