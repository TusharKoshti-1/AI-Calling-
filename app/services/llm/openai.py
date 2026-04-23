"""
app.services.llm.openai
───────────────────────
OpenAI Chat Completions provider.

In SaaS mode the OpenAI API key is per-user — passed in on each call
rather than held in a process-wide slot. The provider is stateless.
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

    async def complete(
        self,
        customer_text: str,
        *,
        history: list[dict[str, str]] | None = None,
        system_prompt: str,
        model: str | None = None,
        api_key: str | None = None,
    ) -> str:
        s = get_settings()
        effective_key = (api_key or s.openai_api_key or "").strip()
        chosen_model = (model or s.openai_model).strip() or "gpt-4o-mini"

        if not effective_key or not effective_key.startswith("sk-"):
            log.error("OpenAI API key missing or invalid (no per-user key, no env).")
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
            "Authorization": f"Bearer {effective_key}",
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
