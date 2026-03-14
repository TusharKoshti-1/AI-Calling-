import logging
import httpx
from config import GROQ_API_KEY, GROQ_MODEL, GROQ_TEMP, GROQ_MAX_TOKENS, SYSTEM_PROMPT

log = logging.getLogger(__name__)

async def get_reply(customer_text: str) -> str:
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": customer_text},
        ],
        "temperature": GROQ_TEMP,
        "max_tokens":  GROQ_MAX_TOKENS,
    }
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers, json=payload,
            )
            if resp.status_code == 200:
                text = resp.json()["choices"][0]["message"]["content"]
                log.info(f"Groq: {text}")
                return text
            log.error(f"Groq {resp.status_code}: {resp.text}")
    except Exception as e:
        log.error(f"Groq error: {e}")
    return "Koi baat nahi sir, dhanyavaad."
