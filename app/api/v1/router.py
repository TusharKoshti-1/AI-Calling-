"""
app/api/v1/router.py
Two routers:
  api_router   — /api/v1/* (calls, settings, status) — versioned REST
  root_router  — /* (audio, webhooks) — Twilio needs plain root URLs
"""
from fastapi import APIRouter
from app.api.v1.endpoints import calls, settings, status, audio, webhooks

# Versioned REST API
api_router = APIRouter()
api_router.include_router(calls.router)
api_router.include_router(settings.router)
api_router.include_router(status.router)

# Root-level (no /api/v1 prefix — Twilio webhooks + audio served here)
root_router = APIRouter()
root_router.include_router(audio.router)
root_router.include_router(webhooks.router)
