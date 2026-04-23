"""
app.main
────────
FastAPI app factory and process entry point.

Production launch:
    uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 2

Single-worker dev:
    python -m app.main
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.logging import configure_logging, get_logger
from app.db.session import close_pool, init_pool
from app.services.settings_service import settings_service
from app.services.storage import storage


# ── Paths ────────────────────────────────────────────────────────
_STATIC_DIR = Path(__file__).parent / "static"


# ═══════════════════════════════════════════════════════════════
# Lifespan
# ═══════════════════════════════════════════════════════════════
@asynccontextmanager
async def lifespan(app: FastAPI):
    log = get_logger(__name__)
    log.info("Starting CallSara…")
    try:
        await init_pool()
        await settings_service.load()
        await storage.ensure_bucket()
    except Exception as exc:
        # Don't crash the process on boot — log loudly, still start the
        # HTTP server so /health can report degraded state for ops alerting.
        log.error("Startup warning: %s", exc)
    yield
    try:
        await close_pool()
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
# App factory
# ═══════════════════════════════════════════════════════════════
def create_app() -> FastAPI:
    s = get_settings()
    configure_logging(s.log_level)

    app = FastAPI(
        title="CallSara — AI Calling",
        version="0.2.0",
        lifespan=lifespan,
        # In production, hide docs unless explicitly opened up.
        docs_url="/docs" if not s.is_production else None,
        redoc_url=None,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Global exception handler ──────────────────────────────
    @app.exception_handler(AppError)
    async def _app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.message, "code": exc.code},
        )

    # ── API routes ────────────────────────────────────────────
    app.include_router(api_router)

    # ── Static frontend (SPA) ─────────────────────────────────
    # Mount /static/{css,js,assets,…} directly.
    app.mount(
        "/static",
        StaticFiles(directory=str(_STATIC_DIR)),
        name="static",
    )

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(str(_STATIC_DIR / "index.html"))

    return app


app = create_app()


if __name__ == "__main__":
    s = get_settings()
    uvicorn.run("app.main:app", host="0.0.0.0", port=s.port, reload=False)
