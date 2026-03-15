import re
from config import END_PHRASES

def clean_reply(raw: str) -> tuple[str, bool, bool]:
    """
    Returns (cleaned_text, should_end_call, is_hot_lead)
    Handles [END_CALL] and [HOT_LEAD] tags.
    """
    text = raw or "Thank you, have a great day!"

    # Detect control tags
    should_end  = bool(re.search(r'\[END_CALL\]',  text, re.IGNORECASE))
    is_hot_lead = bool(re.search(r'\[HOT_LEAD\]',  text, re.IGNORECASE))

    # Hot lead always ends the call
    if is_hot_lead:
        should_end = True

    # Remove control tags
    text = re.sub(r'\[END_CALL\]',  '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[HOT_LEAD\]',  '', text, flags=re.IGNORECASE)

    # Strip <think> tags (reasoning models)
    text = re.sub(r'<think>[\s\S]*?</think>', '', text, flags=re.IGNORECASE)

    # Strip markdown
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'\*(.*?)\*',     r'\1', text)
    text = re.sub(r'#{1,6}\s', '', text)

    # Strip emojis
    text = re.sub(r'[\U0001F300-\U0001FFFF\U00002600-\U000027BF]', '', text)

    # Normalize whitespace
    text = re.sub(r'[\r\n]+', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()

    # Fallback: detect closing phrases if model forgot [END_CALL]
    if not should_end:
        tl = text.lower()
        should_end = any(p in tl for p in END_PHRASES)

    # Escape XML special chars (safe for TwiML)
    text = text.replace('&', 'and').replace('<', '').replace('>', '').replace('"', "'")

    return text, should_end, is_hot_lead
