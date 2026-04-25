"""
app.services.text_cleaner
─────────────────────────
Normalises raw LLM output for TTS and extracts control tags:

    [HOT_LEAD]      → mark the call as a hot lead
    [END_CALL]      → hang up after this turn
    [TRANSFER_CALL] → after this turn, dial the user's configured
                      transfer_number; if no answer, fall back to a
                      polite "experts are busy" line and end the call

Bug fix vs. the legacy version:
    The old END_PHRASES matcher used a plain substring check, so "take care"
    would trigger on "take careful" or "have a good daybreak". We now match
    on word boundaries to eliminate that whole class of false positive.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Words/phrases that unambiguously signal the LLM is wrapping up, even
# when it forgets to emit [END_CALL]. Matched as whole phrases, not substrings.
#
# These are intentionally biased toward "definitely ending" — false
# positives here mean the call ends one turn too early, which is FAR
# better than the alternative (call sits open in silence forever after
# the AI says "have a good day"). gpt-4o-mini is the worst offender at
# forgetting [END_CALL], so we cast a wide net.
END_PHRASES: tuple[str, ...] = (
    # Standard wrap-ups
    "have a good day", "have a great day", "have a wonderful day",
    "have a nice day", "have a lovely day", "have a good one",
    "take care", "good day", "goodbye", "bye for now",
    "talk soon", "talk to you later", "talk to you soon",
    "all the best", "wish you the best", "best of luck",
    # Service-context closers
    "follow up in a few months", "call you back later", "call back later",
    "i'll call you back", "i'll reach back out",
    "reach out to you", "consultant will call", "team will contact",
    "team will reach out", "someone will call you",
    "experts will call", "expert will call", "be in touch",
    # Customer-not-interested closers
    "not interested", "won't bother you", "won't disturb you",
    "i'll let you go", "let you go now",
    # Driving / busy closers
    "drive safe", "drive safely",
    # Hindi / Hinglish wrap-ups
    "dhanyavaad", "shukriya", "baad mein baat karte", "thodi der baad call",
    "apna khayal rakhna", "badhai ho", "future mein zaroor", "phir milte hain",
    "alvida", "namaste",
    # Arabic wrap-ups
    "مع السلامة", "في أمان الله", "إلى اللقاء", "يوم سعيد",
    "سنتواصل معك", "شكراً جزيلاً",
)

# Pre-compile once. Word boundaries only apply to ASCII phrases; Arabic/Hindi
# strings are treated as literal substrings because \b would be meaningless
# for them — but because they're long multi-word phrases, false positives
# are effectively impossible.
_ASCII_PATTERNS: list[re.Pattern[str]] = [
    re.compile(rf"\b{re.escape(p)}\b", re.IGNORECASE)
    for p in END_PHRASES
    if all(ord(c) < 128 for c in p)
]
_NON_ASCII_PHRASES: list[str] = [
    p for p in END_PHRASES if any(ord(c) >= 128 for c in p)
]

_TAG_END = re.compile(r"\[END_CALL\]", re.IGNORECASE)
_TAG_HOT = re.compile(r"\[HOT_LEAD\]", re.IGNORECASE)
_TAG_TRANSFER = re.compile(r"\[TRANSFER_CALL\]", re.IGNORECASE)
_THINK_BLOCK = re.compile(r"<think>[\s\S]*?</think>", re.IGNORECASE)
_BOLD = re.compile(r"\*\*(.*?)\*\*")
_ITALIC = re.compile(r"\*(.*?)\*")
_HEADING = re.compile(r"#{1,6}\s")
_EMOJI = re.compile(r"[\U0001F300-\U0001FFFF\U00002600-\U000027BF]")
_NEWLINES = re.compile(r"[\r\n]+")
_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class CleanedReply:
    text: str
    end_call: bool
    hot_lead: bool
    transfer_call: bool = False


def clean_reply(raw: str) -> CleanedReply:
    """Strip control tags + markdown, detect end-of-call, hot-lead, and transfer signals."""
    text = raw or "Thank you, have a great day!"

    end_call = bool(_TAG_END.search(text))
    hot_lead = bool(_TAG_HOT.search(text))
    transfer_call = bool(_TAG_TRANSFER.search(text))
    if hot_lead:
        end_call = True  # Hot leads always end the call.
    if transfer_call:
        # When transferring we DON'T want the orchestrator to also hang up
        # before the dial happens — the transfer flow handles its own
        # cleanup. So clear end_call here even if the model also emitted
        # [END_CALL] alongside [TRANSFER_CALL].
        end_call = False

    # Strip tags + markdown + emoji
    text = _TAG_END.sub("", text)
    text = _TAG_HOT.sub("", text)
    text = _TAG_TRANSFER.sub("", text)
    text = _THINK_BLOCK.sub("", text)
    text = _BOLD.sub(r"\1", text)
    text = _ITALIC.sub(r"\1", text)
    text = _HEADING.sub("", text)
    text = _EMOJI.sub("", text)
    text = _NEWLINES.sub(" ", text)
    text = _WS.sub(" ", text).strip()

    # Post-hoc end-phrase detection if no explicit [END_CALL] tag.
    # Only check this when not transferring — transfer has its own audio.
    if not end_call and not transfer_call:
        lowered = text.lower()
        if any(p.search(lowered) for p in _ASCII_PATTERNS):
            end_call = True
        elif any(p in text for p in _NON_ASCII_PHRASES):
            end_call = True

    # Sanitise a few characters that can confuse downstream consumers.
    text = (
        text.replace("&", "and")
            .replace("<", "")
            .replace(">", "")
            .replace('"', "'")
    )

    return CleanedReply(
        text=text,
        end_call=end_call,
        hot_lead=hot_lead,
        transfer_call=transfer_call,
    )
