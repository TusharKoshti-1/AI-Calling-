"""
app.services.llm.groq
─────────────────────
Groq Chat Completions provider (OpenAI-compatible endpoint).
"""
from __future__ import annotations

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
FALLBACK_REPLY = "Sorry, I missed that — could you say that again?"


class GroqProvider:
    name = "groq"

    async def complete(
        self,
        customer_text: str,
        *,
        history: list[dict[str, str]] | None = None,
        system_prompt: str,
        model: str | None = None,
    ) -> str:
        s = get_settings()
        if not s.groq_api_key:
            log.error("Groq API key is not configured.")
            return FALLBACK_REPLY

        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": customer_text})

        payload = {
            "model": model or s.groq_model,
            "messages": messages,
            "temperature": s.groq_temperature,
            "max_tokens": s.groq_max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {s.groq_api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(GROQ_URL, headers=headers, json=payload)
            if resp.status_code == 200:
                text = resp.json()["choices"][0]["message"]["content"]
                log.info("Groq reply: %s", text[:100])
                return text
            log.error("Groq %s: %s", resp.status_code, resp.text[:500])
        except Exception as exc:
            log.error("Groq request failed: %s", exc)

        return FALLBACK_REPLY
