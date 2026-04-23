"""
app.services.llm.registry
─────────────────────────
Central registry that returns the LLM provider to use for a given request.
The dashboard setting `llm_provider` drives selection at call time.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.services.llm.base import LLMProvider
from app.services.llm.groq import GroqProvider
from app.services.llm.openai import OpenAIProvider

log = get_logger(__name__)


class LLMRegistry:
    """Owns singleton instances of every provider. Cheap to keep around —
    providers are just thin HTTP wrappers."""

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

    @property
    def openai(self) -> OpenAIProvider:
        # Typed accessor for dashboard key injection.
        return self._providers["openai"]  # type: ignore[return-value]


# Module-level singleton — safe because provider instances are stateless
# except for OpenAI's runtime-key slot, which is threaded through once.
llm_registry = LLMRegistry()
