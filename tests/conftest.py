"""Test bootstrap — sets safe env vars before app modules load."""
from __future__ import annotations

import os

# Set required env BEFORE any `from app...` imports run.
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("ADMIN_API_KEY", "")
os.environ.setdefault("VERIFY_TWILIO_SIGNATURE", "false")
os.environ.setdefault("BASE_URL", "http://localhost:8000")

# Wipe any settings singleton from previous imports so tests
# see the test env.
from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()
