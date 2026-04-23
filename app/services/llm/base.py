"""
app.services.llm.base
─────────────────────
Protocol that every LLM provider must implement. Add a new provider by
creating a new file that exports a class implementing `LLMProvider`.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    """Minimal async contract: given a system prompt, history and the latest
    customer utterance, return the assistant's raw reply text."""

    name: str

    async def complete(
        self,
        customer_text: str,
        *,
        history: list[dict[str, str]] | None = None,
        system_prompt: str,
        model: str | None = None,
    ) -> str: ...
