"""
app.services.llm.openai
───────────────────────
OpenAI Chat Completions provider. The API key can be supplied:
  1. Via the OPENAI_API_KEY env var (baseline), or
  2. Via the dashboard Settings page (stored in DB, injected per-call).

The runtime key takes precedence when both are present.
"""
from __future__ import annotations

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
FALLBACK_REPLY = "Sorry, I missed that — could you say that again?"


class OpenAIProvider:
    name = "openai"

    def __init__(self) -> None:
        # Runtime override — set by SettingsService when the user saves a
        # key in the dashboard. Checked before falling back to env config.
        self._runtime_api_key: str = ""
        self._runtime_model: str = ""

    def set_runtime_overrides(self, *, api_key: str = "", model: str = "") -> None:
        """Inject dashboard-configured credentials without touching env vars."""
        if api_key:
            self._runtime_api_key = api_key.strip()
        if model:
            self._runtime_model = model.strip()

    def _resolve_api_key(self) -> str:
        return self._runtime_api_key or get_settings().openai_api_key

    def _resolve_model(self, model: str | None) -> str:
        if model:
            return model
        return self._runtime_model or get_settings().openai_model

    async def complete(
        self,
        customer_text: str,
        *,
        history: list[dict[str, str]] | None = None,
        system_prompt: str,
        model: str | None = None,
    ) -> str:
        s = get_settings()
        api_key = self._resolve_api_key()
        chosen_model = self._resolve_model(model)

        if not api_key or not api_key.startswith("sk-"):
            log.error(
                "OpenAI API key missing or invalid. "
                "Set it in Settings → LLM Provider, or OPENAI_API_KEY env."
            )
            return FALLBACK_REPLY

        log.info("OpenAI → model=%s", chosen_model)

        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": customer_text})

        payload = {
            "model": chosen_model,
            "messages": messages,
            "temperature": s.openai_temperature,
            "max_completion_tokens": s.openai_max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(OPENAI_URL, headers=headers, json=payload)
            if resp.status_code == 200:
                text = resp.json()["choices"][0]["message"]["content"]
                log.info("OpenAI reply: %s", text[:100])
                return text
            log.error("OpenAI %s: %s", resp.status_code, resp.text[:500])
        except Exception as exc:
            log.error("OpenAI request failed: %s", exc)

        return FALLBACK_REPLY
