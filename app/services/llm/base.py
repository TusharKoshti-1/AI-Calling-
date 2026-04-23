"""
app.services.llm.base
─────────────────────
Protocol every LLM provider must implement. Providers are stateless —
credentials and model come in on each call so multi-tenant callers can
thread the right user's settings through.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    async def complete(
        self,
        customer_text: str,
        *,
        history: list[dict[str, str]] | None = None,
        system_prompt: str,
        model: str | None = None,
        api_key: str | None = None,
    ) -> str: ...
