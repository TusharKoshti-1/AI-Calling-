"""
app.api.v1.router
─────────────────
Aggregates every endpoint module into a single APIRouter so main.py only
mounts one thing. New endpoints are added by creating a module in
`endpoints/` and including its router here.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import (
    calls,
    health,
    settings as settings_ep,
    status,
    twilio_webhooks,
    voice,
)

api_router = APIRouter()

# Unauthenticated / public
api_router.include_router(health.router)
api_router.include_router(status.router)

# Twilio webhooks (HMAC-verified, not API-key guarded)
api_router.include_router(twilio_webhooks.router)
api_router.include_router(twilio_webhooks.legacy_router)

# Admin-guarded dashboard endpoints
api_router.include_router(calls.router)
api_router.include_router(settings_ep.router)
api_router.include_router(voice.router)
