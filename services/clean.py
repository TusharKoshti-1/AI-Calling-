import re
from config import END_PHRASES

def clean_reply(raw: str) -> tuple[str, bool, bool]:
    """Returns (cleaned_text, should_end_call, is_hot_lead)"""
    text = raw or "Thank you, have a great day!"
    should_end  = bool(re.search(r'\[END_CALL\]',  text, re.IGNORECASE))
    is_hot_lead = bool(re.search(r'\[HOT_LEAD\]',  text, re.IGNORECASE))
    if is_hot_lead:
        should_end = True

    text = re.sub(r'\[END_CALL\]',  '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[HOT_LEAD\]',  '', text, flags=re.IGNORECASE)
    text = re.sub(r'<think>[\s\S]*?</think>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'\*(.*?)\*',     r'\1', text)
    text = re.sub(r'#{1,6}\s', '', text)
    text = re.sub(r'[\U0001F300-\U0001FFFF\U00002600-\U000027BF]', '', text)
    text = re.sub(r'[\r\n]+', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()

    if not should_end:
        tl = text.lower()
        should_end = any(p in tl for p in END_PHRASES)

    text = text.replace('&', 'and').replace('<', '').replace('>', '').replace('"', "'")
    return text, should_end, is_hot_lead
