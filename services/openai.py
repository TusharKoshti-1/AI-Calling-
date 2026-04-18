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
        if not OPENAI_API_KEY or not OPENAI_API_KEY.startswith("sk-"):
            log.error(f"OpenAI API key is missing or invalid (starts with: '{OPENAI_API_KEY[:8] if OPENAI_API_KEY else 'EMPTY'}'). Set it in Settings → LLM Provider.")
            return "Sorry, I missed that — could you say that again?"
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
            # Log the full error so we can diagnose API/model issues
            log.error(f"OpenAI API error {resp.status_code}: {resp.text[:500]}")
    except Exception as e:
        log.error(f"OpenAI request failed: {e}")
    # Neutral fallback — does NOT contain any end-call phrases
    return "Sorry,?"
