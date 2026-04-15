import logging
import httpx
from config import OPENAI_API_KEY, OPENAI_MODEL, OPENAI_TEMP, OPENAI_MAX_TOKENS

log = logging.getLogger(__name__)

async def get_reply(customer_text: str, history: list = None,
                    system_prompt: str = None) -> str:
    if system_prompt is None:
        from config import SYSTEM_PROMPT
        system_prompt = SYSTEM_PROMPT

    messages = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": customer_text})

    payload = {
        "model": OPENAI_MODEL, "messages": messages,
        "temperature": OPENAI_TEMP, "max_tokens": OPENAI_MAX_TOKENS,
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}",
                         "Content-Type": "application/json"},
                json=payload,
            )
            if resp.status_code == 200:
                text = resp.json()["choices"][0]["message"]["content"]
                log.info(f"OpenAI: {text[:100]}")
                return text
            log.error(f"OpenAI {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        log.error(f"OpenAI error: {e}")
    return "Thank you, have a great day!"
