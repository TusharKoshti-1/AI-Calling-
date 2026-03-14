import re
from config import END_PHRASES

def clean_reply(raw: str) -> tuple[str, bool]:
    text = raw or "Koi baat nahi sir, dhanyavaad."
    should_end = bool(re.search(r'\[END_CALL\]', text, re.IGNORECASE))
    text = re.sub(r'\[END_CALL\]', '', text, flags=re.IGNORECASE).strip()
    text = re.sub(r'<think>[\s\S]*?</think>', '', text, flags=re.IGNORECASE).strip()
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'\*(.*?)\*',     r'\1', text)
    text = re.sub(r'#{1,6}\s', '', text)
    text = re.sub(r'[\U0001F300-\U0001FFFF\U00002600-\U000027BF]', '', text)
    text = re.sub(r'[\r\n]+', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    if not should_end:
        tl = text.lower()
        should_end = any(p in tl for p in END_PHRASES)
    text = text.replace('&','aur').replace('<','').replace('>','').replace('"',"'")
    return text, should_end
