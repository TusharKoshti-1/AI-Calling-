"""
app.services.settings_service
─────────────────────────────
Runtime application settings (agent name, active voice, LLM provider,
system prompt override) backed by the `settings` DB table.

Responsibilities:
  • Cache settings in-memory for fast lookup on the TwiML critical path.
  • Persist updates to Postgres and refresh the cache.
  • Inject OpenAI runtime overrides into the LLM registry.
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.core.config import get_settings as get_env
from app.core.logging import get_logger
from app.db.repositories.settings import SettingsRepository
from app.services.llm import llm_registry
from app.services.prompts import render_default_prompt
from app.services.tts import DEFAULT_VOICE_ID

log = get_logger(__name__)

# Keys the dashboard is allowed to write via /api/settings.
ALLOWED_SETTING_KEYS: frozenset[str] = frozenset({
    "agent_name", "agency_name", "system_prompt", "voice_id",
    "llm_provider", "openai_api_key", "openai_model", "groq_model",
})


class SettingsService:
    def __init__(self, repo: SettingsRepository | None = None) -> None:
        self._repo = repo or SettingsRepository()
        self._cache: dict[str, str] = {}
        self._lock = asyncio.Lock()

    # ── Boot / refresh ────────────────────────────────────────
    async def load(self) -> None:
        """Populate the in-memory cache from the DB. Called once at startup
        (and optionally from /api/settings GET for fresh reads)."""
        env = get_env()
        # Seed with env defaults, so brand-new deployments just work.
        defaults: dict[str, str] = {
            "agent_name":     env.agent_name,
            "agency_name":    env.agency_name,
            "system_prompt":  "default",
            "voice_id":       env.cartesia_voice_id or DEFAULT_VOICE_ID,
            "llm_provider":   env.llm_provider,
            "openai_api_key": "",
            "openai_model":   env.openai_model,
            "groq_model":     env.groq_model,
        }
        try:
            db_values = await self._repo.get_all()
        except Exception as exc:
            log.error("Settings load failed, using env defaults: %s", exc)
            db_values = {}
        merged = {**defaults, **{k: v for k, v in db_values.items() if v is not None}}
        async with self._lock:
            self._cache = merged
        self._apply_runtime_overrides()
        log.info(
            "Settings loaded: agent=%s voice=%s provider=%s",
            merged.get("agent_name"),
            (merged.get("voice_id") or "")[:8],
            merged.get("llm_provider"),
        )

    # ── Reads ─────────────────────────────────────────────────
    def get(self, key: str, default: str = "") -> str:
        return self._cache.get(key, default)

    def snapshot(self) -> dict[str, str]:
        """Return a copy of the cache (safe for JSON serialisation)."""
        return dict(self._cache)

    def public_snapshot(self) -> dict[str, Any]:
        """Snapshot safe to return to the dashboard.
        The OpenAI API key is NEVER returned — only a boolean flag."""
        snap = self.snapshot()
        has_key = bool(snap.pop("openai_api_key", "").strip())
        snap["openai_api_key_present"] = has_key
        # Resolve "default" → the actual rendered prompt for the dashboard.
        if (snap.get("system_prompt") or "").strip() in ("", "default"):
            snap["system_prompt"] = self.resolve_system_prompt()
        return snap

    def resolve_voice_id(self) -> str:
        v = (self._cache.get("voice_id") or "").strip()
        return v or get_env().cartesia_voice_id or DEFAULT_VOICE_ID

    def resolve_system_prompt(self) -> str:
        sp = (self._cache.get("system_prompt") or "").strip()
        if sp and sp != "default":
            return sp
        return render_default_prompt(
            agent_name=self.get("agent_name", get_env().agent_name),
            agency_name=self.get("agency_name", get_env().agency_name),
        )

    def resolve_llm_provider(self) -> str:
        return (self._cache.get("llm_provider") or get_env().llm_provider).lower()

    # ── Writes ────────────────────────────────────────────────
    async def update(self, raw: dict[str, Any]) -> dict[str, str]:
        """Persist a partial update. Returns only the fields actually saved."""
        saved: dict[str, str] = {}
        for key, val in raw.items():
            if key not in ALLOWED_SETTING_KEYS:
                continue
            if val is None:
                continue
            # Coerce numbers/booleans to string — DB column is TEXT.
            str_val = val.strip() if isinstance(val, str) else str(val)
            saved[key] = str_val

        if not saved:
            return {}

        try:
            await self._repo.set_many(saved)
        except Exception as exc:
            log.error("Failed to persist settings: %s", exc)
            raise

        async with self._lock:
            self._cache.update(saved)
        self._apply_runtime_overrides()
        return saved

    # ── Internals ─────────────────────────────────────────────
    def _apply_runtime_overrides(self) -> None:
        """Push DB-stored OpenAI credentials into the LLM registry so calls
        use the per-deployment key without restarting the process."""
        llm_registry.openai.set_runtime_overrides(
            api_key=self._cache.get("openai_api_key", ""),
            model=self._cache.get("openai_model", ""),
        )


# Module singleton — loaded on app startup via lifespan.
settings_service = SettingsService()
