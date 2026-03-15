import logging
import httpx
from config import GROQ_API_KEY, GROQ_MODEL, GROQ_TEMP, GROQ_MAX_TOKENS, SYSTEM_PROMPT

log = logging.getLogger(__name__)

async def get_reply(customer_text: str, history: list = None) -> str:
    """
    Call Groq LLM with full conversation history for multi-turn context.
    history: list of {"role": "user"/"assistant", "content": "..."} dicts
    """
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Inject prior conversation turns so the model has context
    if history:
        messages.extend(history)

    # Add current customer message
    messages.append({"role": "user", "content": customer_text})

    payload = {
        "model":       GROQ_MODEL,
        "messages":    messages,
        "temperature": GROQ_TEMP,
        "max_tokens":  GROQ_MAX_TOKENS,
    }
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type":  "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers, json=payload,
            )
            if resp.status_code == 200:
                text = resp.json()["choices"][0]["message"]["content"]
                log.info(f"Groq: {text[:120]}")
                return text
            log.error(f"Groq {resp.status_code}: {resp.text}")
    except Exception as e:
        log.error(f"Groq error: {e}")
    return "Thank you, have a great day!"
