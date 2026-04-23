"""
app.services.llm.registry
─────────────────────────
Returns the LLM provider for a given name. Providers are stateless — the
caller supplies credentials per call.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.services.llm.base import LLMProvider
from app.services.llm.groq import GroqProvider
from app.services.llm.openai import OpenAIProvider

log = get_logger(__name__)


class LLMRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, LLMProvider] = {
            "groq": GroqProvider(),
            "openai": OpenAIProvider(),
        }

    def get(self, name: str) -> LLMProvider:
        key = (name or "groq").lower().strip()
        provider = self._providers.get(key)
        if provider is None:
            log.warning("Unknown LLM provider '%s' — falling back to groq.", name)
            return self._providers["groq"]
        return provider


llm_registry = LLMRegistry()
