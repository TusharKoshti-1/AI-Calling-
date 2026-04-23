"""
app.api.v1.router
─────────────────
Aggregates every endpoint module into a single APIRouter.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import (
    auth,
    calls,
    health,
    settings as settings_ep,
    status,
    twilio_webhooks,
    voice,
)

api_router = APIRouter()

# Public
api_router.include_router(health.router)
api_router.include_router(auth.router)

# Twilio webhooks (HMAC-verified)
api_router.include_router(twilio_webhooks.router)
api_router.include_router(twilio_webhooks.legacy_router)

# Session-protected
api_router.include_router(status.router)
api_router.include_router(calls.router)
api_router.include_router(settings_ep.router)
api_router.include_router(voice.router)
