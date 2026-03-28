"""
app/services/ai/reply_parser.py
Parses raw LLM output — extracts control tags and cleans text for TTS.
Single responsibility: takes raw string, returns structured result.
"""
import re
from dataclasses import dataclass

END_PHRASES = [
    # English
    "have a good day", "have a great day", "have a wonderful day", "take care",
    "good day", "goodbye", "talk soon", "all the best", "call you back later",
    "call back later", "reach out to you", "consultant will call", "not interested",
    "won't bother you", "i'll let you go", "best of luck", "follow up in a few months",
    # Hindi
    "dhanyavaad", "shukriya", "baad mein baat karte", "thodi der baad call",
    "apna khayal rakhna", "badhai ho", "future mein zaroor", "phir milte hain",
    # Arabic
    "مع السلامة", "في أمان الله", "إلى اللقاء", "يوم سعيد",
    "سنتواصل معك", "شكراً جزيلاً",
]


@dataclass
class ParsedReply:
    text: str        # Clean text safe for TTS and TwiML
    end_call: bool   # Should the call end after this reply?
    is_hot_lead: bool  # Tag this as a hot lead?


def parse_reply(raw: str) -> ParsedReply:
    """
    Parse raw LLM output into structured reply.
    Handles [END_CALL], [HOT_LEAD], markdown, emojis, XML chars.
    """
    text = raw or "Thank you, have a great day!"

    # Detect control tags (case-insensitive, anywhere in text)
    is_hot_lead = bool(re.search(r'\[HOT_LEAD\]', text, re.IGNORECASE))
    end_call    = bool(re.search(r'\[END_CALL\]',  text, re.IGNORECASE))

    # Hot lead always ends the call
    if is_hot_lead:
        end_call = True

    # Remove control tags
    text = re.sub(r'\[END_CALL\]',  '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[HOT_LEAD\]',  '', text, flags=re.IGNORECASE)

    # Strip <think> reasoning tags (some models include them)
    text = re.sub(r'<think>[\s\S]*?</think>', '', text, flags=re.IGNORECASE)

    # Strip markdown
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'\*(.*?)\*',     r'\1', text)
    text = re.sub(r'#{1,6}\s+',     '',    text)

    # Strip emojis
    text = re.sub(r'[\U0001F300-\U0001FFFF\U00002600-\U000027BF]', '', text)

    # Normalise whitespace
    text = re.sub(r'[\r\n]+', ' ', text)
    text = re.sub(r'\s+',     ' ', text).strip()

    # Fallback end detection — if model forgot to add [END_CALL]
    if not end_call:
        tl = text.lower()
        end_call = any(phrase in tl for phrase in END_PHRASES)

    # Escape XML characters for TwiML safety
    text = (
        text
        .replace('&', 'and')
        .replace('<', '')
        .replace('>', '')
        .replace('"', "'")
    )

    return ParsedReply(text=text, end_call=end_call, is_hot_lead=is_hot_lead)
