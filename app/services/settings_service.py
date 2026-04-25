"""
app.services.settings_service
─────────────────────────────
Per-user runtime settings, backed by the `settings` table.

Each signed-in user gets their own agent_name / agency_name / voice_id /
LLM provider / OpenAI key / system prompt. The orchestrator threads the
user_id through every call so the webhook→LLM→TTS path uses the right
tenant's settings.

Caching strategy:
  • In-memory cache keyed by user_id, short TTL (defaults flush on mutation).
  • DB is the source of truth — on cache miss we fetch + populate.
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.core.config import get_settings as get_env
from app.core.logging import get_logger
from app.db.repositories.settings import SettingsRepository
from app.services.prompts import render_default_prompt
from app.services.tts import DEFAULT_VOICE_ID

log = get_logger(__name__)

ALLOWED_SETTING_KEYS: frozenset[str] = frozenset({
    "agent_name", "agency_name", "system_prompt", "voice_id",
    "llm_provider", "openai_api_key", "openai_model", "groq_model",
    "transfer_number",
})


def _defaults_from_env() -> dict[str, str]:
    env = get_env()
    return {
        "agent_name":     env.agent_name,
        "agency_name":    env.agency_name,
        "system_prompt":  "default",
        "voice_id":       env.cartesia_voice_id or DEFAULT_VOICE_ID,
        "llm_provider":   env.llm_provider,
        "openai_api_key": "",
        "openai_model":   env.openai_model,
        "groq_model":     env.groq_model,
        # Empty by default — when blank, [TRANSFER_CALL] gracefully degrades
        # to ending the call rather than dialling nothing.
        "transfer_number": "",
    }


class UserSettings:
    """Read-only accessor over a single user's merged settings dict."""

    def __init__(self, user_id: str, data: dict[str, str]) -> None:
        self.user_id = user_id
        self._data = data

    def get(self, key: str, default: str = "") -> str:
        return self._data.get(key, default)

    def snapshot(self) -> dict[str, str]:
        return dict(self._data)

    def public_snapshot(self) -> dict[str, Any]:
        """Safe to return to the dashboard: the OpenAI key is redacted."""
        snap = self.snapshot()
        has_key = bool(snap.pop("openai_api_key", "").strip())
        snap["openai_api_key_present"] = has_key
        if (snap.get("system_prompt") or "").strip() in ("", "default"):
            snap["system_prompt"] = self.resolve_system_prompt()
        return snap

    def resolve_voice_id(self) -> str:
        v = (self._data.get("voice_id") or "").strip()
        return v or get_env().cartesia_voice_id or DEFAULT_VOICE_ID

    def resolve_system_prompt(self) -> str:
        sp = (self._data.get("system_prompt") or "").strip()
        if sp and sp != "default":
            return sp
        return render_default_prompt(
            agent_name=self.get("agent_name", get_env().agent_name),
            agency_name=self.get("agency_name", get_env().agency_name),
        )

    def resolve_llm_provider(self) -> str:
        return (self._data.get("llm_provider") or get_env().llm_provider).lower()


class SettingsService:
    def __init__(self, repo: SettingsRepository | None = None) -> None:
        self._repo = repo or SettingsRepository()
        self._cache: dict[str, dict[str, str]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, user_id: str) -> asyncio.Lock:
        if user_id not in self._locks:
            self._locks[user_id] = asyncio.Lock()
        return self._locks[user_id]

    async def for_user(self, user_id: str) -> UserSettings:
        """Return the effective settings for a user (cache-through)."""
        cached = self._cache.get(user_id)
        if cached is not None:
            return UserSettings(user_id, cached)

        async with self._lock_for(user_id):
            # Double-check inside the lock.
            cached = self._cache.get(user_id)
            if cached is not None:
                return UserSettings(user_id, cached)

            try:
                db_values = await self._repo.get_all_for_user(user_id)
            except Exception as exc:
                log.error("Settings load for %s failed: %s", user_id, exc)
                db_values = {}
            merged = {**_defaults_from_env(), **db_values}
            self._cache[user_id] = merged
            return UserSettings(user_id, merged)

    async def update_for_user(
        self, user_id: str, raw: dict[str, Any]
    ) -> dict[str, str]:
        """Persist a partial update. Returns the fields actually saved."""
        saved: dict[str, str] = {}
        for key, val in raw.items():
            if key not in ALLOWED_SETTING_KEYS or val is None:
                continue
            saved[key] = val.strip() if isinstance(val, str) else str(val)

        if not saved:
            return {}

        try:
            await self._repo.set_many_for_user(user_id, saved)
        except Exception as exc:
            log.error("Failed to persist settings for %s: %s", user_id, exc)
            raise

        # Invalidate cache — next read re-fetches from DB.
        async with self._lock_for(user_id):
            self._cache.pop(user_id, None)
        return saved

    def invalidate(self, user_id: str) -> None:
        self._cache.pop(user_id, None)


settings_service = SettingsService()
