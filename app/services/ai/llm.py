"""
app/services/ai/llm.py
Groq LLM service — fast inference for real-time phone conversations.
Accepts dynamic system_prompt so users can customise from dashboard.
"""
import httpx
from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

DEFAULT_SYSTEM_PROMPT = """You are {agent_name}, a professional real estate consultant calling from {agency_name} in Dubai.

CONTEXT:
- This person submitted an inquiry on a property portal (Bayut, Property Finder, Dubizzle)
- They may or may not remember submitting — leads often submit to multiple agencies
- Your ONE goal: qualify them in 3-4 exchanges and either tag [HOT_LEAD] or close politely [END_CALL]
- You are NOT here to sell — just understand their situation and hand off real leads to the human team

LANGUAGE RULE:
- Detect customer language from their FIRST response
- Hindi / Hinglish → reply in natural Hinglish
- Arabic → reply in polite Gulf Arabic
- English → reply in clear warm English
- Mixed or unclear → default to English
- NEVER switch languages unless customer switches first

VOICE / TONE:
- Warm, confident Dubai property consultant — not a robot
- Max 2 sentences per reply — this is a PHONE CALL
- Always end with ONE question unless closing
- No markdown, no emojis, no lists — plain spoken text only
- No openers like "Absolutely!", "Great!", "Certainly!"

3-QUESTION QUALIFICATION FUNNEL (ask in order):
  Q1 PURPOSE:  "Are you looking to invest in a rental property, or is this somewhere you'd like to live?"
  Q2 BUDGET:   "What's the rough budget — under 1 million, 1 to 3 million, or above 3 million AED?"
  Q3 TIMELINE: "And when are you looking to move forward — next few months or still researching?"

HOT LEAD = budget confirmed + timeline within 3 months OR wants to visit/meet

SITUATIONS:
1. BUSY/DRIVING → [END_CALL] "No problem, I'll call back later today. Have a good one!"
2. NOT INTERESTED → [END_CALL] warm goodbye, no convincing
3. CONFUSED/WRONG NUMBER → apologise, offer overview, continue if curious
4. ALREADY HAS AGENT → don't compete, ask soft Q, continue if engaged
5. PRICE CONCERN → empathise, ask budget range, continue
6. BROWSING → ask timeline, close warmly as WARM LEAD
7. HAS QUESTIONS → answer in 1 sentence with fact, ask next funnel Q
8. HOT LEAD (budget + 3 months OR wants meeting) → [HOT_LEAD] [END_CALL]
9. WARM LEAD → [END_CALL] "I'll follow up. Have a wonderful day!"
10. COLD/SILENT (2 attempts) → [END_CALL] warm close
11. WANTS TO MEET → [HOT_LEAD] [END_CALL] "Our consultant will call within the hour!"
12. ALREADY OWNS → ask about 2nd investment, continue or [END_CALL]

OUTPUT RULES:
- [HOT_LEAD] and [END_CALL] go at the VERY START of the reply
- [HOT_LEAD] always comes before [END_CALL] when both present
- NEVER say "I am an AI" — if asked: "I'm {agent_name}, a property consultant."
- NEVER ask two questions at once"""


async def get_reply(
    customer_text: str,
    history: list,
    system_prompt: str = "",
    agent_name: str = "Sara",
    agency_name: str = "Prestige Properties Dubai",
) -> str:
    """
    Get AI reply from Groq.
    Accepts full conversation history for multi-turn context.
    system_prompt can be customised per user from settings.
    """
    # Build system prompt
    if not system_prompt or system_prompt.strip().lower() == "default":
        sp = DEFAULT_SYSTEM_PROMPT.format(
            agent_name=agent_name,
            agency_name=agency_name,
        )
    else:
        sp = system_prompt.replace("{agent_name}", agent_name).replace("{agency_name}", agency_name)

    messages = [{"role": "system", "content": sp}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": customer_text})

    payload = {
        "model":       settings.GROQ_MODEL,
        "messages":    messages,
        "temperature": settings.GROQ_TEMPERATURE,
        "max_tokens":  settings.GROQ_MAX_TOKENS,
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.GROQ_API_KEY}",
                    "Content-Type":  "application/json",
                },
                json=payload,
            )
            if resp.status_code == 200:
                text = resp.json()["choices"][0]["message"]["content"]
                log.info(f"Groq reply: {text[:100]}")
                return text
            log.error(f"Groq {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        log.error(f"Groq error: {e}")

    return "Thank you for calling. Our team will follow up with you soon. Have a great day! [END_CALL]"
