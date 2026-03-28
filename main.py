"""
main.py — CallSara SaaS Entry Point
"""
from contextlib import asynccontextmanager
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.core.config import settings
from app.core.logging import setup_logging, get_logger
from app.api.v1.router import api_router, root_router
from db.database import init_db, close_db
from app.services import settings_service
from app.services.storage.supabase import ensure_bucket

setup_logging("DEBUG" if settings.DEBUG else "INFO")
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info(f"🚀 Starting {settings.APP_NAME} v{settings.APP_VERSION} [{settings.ENVIRONMENT}]")
    try:
        await init_db()
    except Exception as e:
        log.error(f"DB init failed (running without DB): {e}")
    await settings_service.load_from_db()
    try:
        await ensure_bucket()
    except Exception as e:
        log.warning(f"Storage bucket check failed: {e}")
    log.info("✅ CallSara ready")
    yield
    try:
        await close_db()
    except Exception:
        pass
    log.info("CallSara stopped.")


app = FastAPI(
    title=f"{settings.APP_NAME} API",
    version=settings.APP_VERSION,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── API routes (/api/v1/*) ─────────────────────────────────────────────────────
app.include_router(api_router, prefix="/api/v1")

# ── Root routes (audio + webhooks, no prefix) ──────────────────────────────────
app.include_router(root_router)

# ── Static assets ──────────────────────────────────────────────────────────────
app.mount("/assets", StaticFiles(directory="frontend/assets"), name="assets")

# ── Page routes ────────────────────────────────────────────────────────────────
@app.get("/")
async def page_dashboard():
    return FileResponse("frontend/pages/dashboard.html")

@app.get("/calls")
async def page_calls():
    return FileResponse("frontend/pages/calls.html")

@app.get("/hot-leads")
async def page_hot_leads():
    return FileResponse("frontend/pages/hot_leads.html")

@app.get("/dialer")
async def page_dialer():
    return FileResponse("frontend/pages/dialer.html")

@app.get("/settings")
async def page_settings():
    return FileResponse("frontend/pages/settings.html")

# ── Health ─────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    db_ok = False
    try:
        from db.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        db_ok = True
    except Exception:
        pass
    return {"status": "ok" if db_ok else "degraded", "db": db_ok, "version": settings.APP_VERSION}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=settings.PORT, reload=settings.DEBUG)
