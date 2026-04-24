"""
app.services.llm.groq
─────────────────────
Groq Chat Completions provider. Stateless — caller passes model and
(optional) api_key per call.

Uses the same shared httpx client as OpenAI for connection pooling; when
users switch provider at runtime the cost is one fresh TLS handshake,
not one per turn.
"""
from __future__ import annotations

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.http_client import get_openai_client  # reused: same transport shape

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
        api_key: str | None = None,
    ) -> str:
        s = get_settings()
        effective_key = (api_key or s.groq_api_key or "").strip()
        if not effective_key:
            log.error("Groq API key is not configured.")
            return FALLBACK_REPLY

        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": customer_text})

        payload = {
            "model": (model or s.groq_model),
            "messages": messages,
            "temperature": s.groq_temperature,
            "max_tokens": s.groq_max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {effective_key}",
            "Content-Type": "application/json",
        }

        try:
            client = get_openai_client()
            resp = await client.post(GROQ_URL, headers=headers, json=payload)
            if resp.status_code == 200:
                text = resp.json()["choices"][0]["message"]["content"]
                log.info("Groq reply: %s", text[:100])
                return text
            log.error("Groq %s: %s", resp.status_code, resp.text[:500])
        except Exception as exc:
            log.error("Groq request failed: %s", exc)

        return FALLBACK_REPLY
