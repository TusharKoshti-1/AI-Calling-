import logging
import httpx
from config import GROQ_API_KEY, GROQ_MODEL, GROQ_TEMP, GROQ_MAX_TOKENS, SYSTEM_PROMPT

log = logging.getLogger(__name__)

async def get_reply(customer_text: str, history: list = None) -> str:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": customer_text})

    payload = {
        "model":       GROQ_MODEL,
        "messages":    messages,
        "temperature": GROQ_TEMP,
        "max_tokens":  GROQ_MAX_TOKENS,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                json=payload,
            )
            if resp.status_code == 200:
                text = resp.json()["choices"][0]["message"]["content"]
                log.info(f"Groq: {text[:100]}")
                return text
            log.error(f"Groq {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        log.error(f"Groq error: {e}")
    return "Thank you, have a great day!"
