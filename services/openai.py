import logging
import httpx
import config as cfg

log = logging.getLogger(__name__)

async def get_reply(customer_text: str, history: list = None,
                    system_prompt: str = None) -> str:
    if system_prompt is None:
        system_prompt = cfg.SYSTEM_PROMPT

    # Read all values at call time (not import time) so that runtime overrides
    # from _apply_runtime_llm_settings() in main.py are always picked up.
    api_key = cfg.OPENAI_API_KEY
    model   = cfg.OPENAI_MODEL
    temp    = cfg.OPENAI_TEMP
    max_tok = cfg.OPENAI_MAX_TOKENS

    log.info(f"OpenAI → model={model}")

    messages = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": customer_text})

    payload = {
        "model": model, "messages": messages,
        "temperature": temp, "max_completion_tokens": max_tok,
    }
    try:
        if not api_key or not api_key.startswith("sk-"):
            log.error(
                f"OpenAI API key missing/invalid "
                f"('{api_key[:8] if api_key else 'EMPTY'}'). "
                f"Set it in Settings → LLM Provider."
            )
            return "Sorry, I missed that — could you say that again?"

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                json=payload,
            )
            if resp.status_code == 200:
                text = resp.json()["choices"][0]["message"]["content"]
                log.info(f"OpenAI reply: {text[:100]}")
                return text
            log.error(f"OpenAI API error {resp.status_code}: {resp.text[:500]}")
    except Exception as e:
        log.error(f"OpenAI request failed: {e}")

    return "Sorry,?"
