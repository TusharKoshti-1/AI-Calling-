"""
LLM Router — picks Groq or OpenAI based on the runtime setting `llm_provider`.
Import get_reply from here instead of services.groq or services.openai directly.
"""
import logging

log = logging.getLogger(__name__)

# These are imported lazily so the router always reads the live setting.
async def get_reply(customer_text: str, history: list = None,
                    system_prompt: str = None,
                    provider: str = "groq") -> str:
    """
    Route the LLM call to Groq or OpenAI.

    Args:
        customer_text: Latest customer utterance.
        history:       Previous conversation turns (list of {role, content}).
        system_prompt: Override system prompt; None = use config default.
        provider:      "groq" | "openai"  (passed from _settings at call time).
    """
    provider = (provider or "groq").lower().strip()

    if provider == "openai":
        log.info("LLM Router → OpenAI")
        from services.openai import get_reply as _openai_reply
        return await _openai_reply(customer_text, history=history, system_prompt=system_prompt)

    # Default / fallback = Groq
    log.info("LLM Router → Groq")
    from services.groq import get_reply as _groq_reply
    return await _groq_reply(customer_text, history=history, system_prompt=system_prompt)
