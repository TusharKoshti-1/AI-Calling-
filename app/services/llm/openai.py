"""
app.services.llm.openai
───────────────────────
OpenAI Chat Completions provider.

Two entry points:

  complete(...)         → legacy non-streaming. Returns the full reply string.
                          Still used by the opening-line generator where we
                          have no streaming advantage (we need the full line
                          before we can synthesise audio anyway, because the
                          opening is cached).

  stream_sentences(...) → async iterator of clean sentence fragments suitable
                          for TTS. Used by the real-time reply path so we can
                          start speaking while the LLM is still generating.
                          Tag prefixes like [HOT_LEAD]/[END_CALL] are buffered
                          out so they never leak to the speaker.

Key latency optimisations:
  • Persistent httpx client (no per-request TCP+TLS handshake)
  • Streaming completions (first audible audio ~800 ms sooner on a 2 s reply)
  • Prompt caching: system prompt is placed first and kept stable so OpenAI's
    automatic prompt cache hits on every turn after the first (cuts prefill
    latency + cost by ~50% on cached tokens)

In SaaS mode the OpenAI API key is per-user — passed in on each call rather
than held in a process-wide slot. The provider is stateless.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.http_client import get_openai_client

log = get_logger(__name__)

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
FALLBACK_REPLY = "Sorry, I missed that — could you say that again?"

# Boundary characters that end a "speakable" chunk. We stream tokens until we
# hit one of these, then yield the chunk to TTS. Including commas + dashes
# lets us kick off TTS after the first clause, not the first full sentence —
# big latency win because real human-style replies use them constantly.
_SENTENCE_ENDERS = frozenset(".!?…,—\n")
# Minimum chars before we'll emit a SUBSEQUENT chunk. Smaller is faster but
# choppier; 40 is a good balance for Cartesia.
_MIN_CHUNK_CHARS = 40
# For the FIRST chunk we lower the threshold drastically — getting audio
# playing fast is more important than chunk size on the very first audio
# the customer hears (every ms of dead air feels long). 6 chars catches
# casual short clauses like "Got it," / "Sure," / "Hi there," — exactly
# the conversational starters our prompt encourages.
_FIRST_CHUNK_MIN_CHARS = 6


def _build_messages(
    system_prompt: str,
    history: list[dict[str, str]] | None,
    customer_text: str,
) -> list[dict[str, str]]:
    """Assemble the messages array with the system prompt first.

    Keeping the system prompt as the first message (and stable across turns)
    is what lets OpenAI's automatic prompt cache kick in — cached prefixes
    are both cheaper and measurably faster to prefill.
    """
    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": customer_text})
    return messages


class OpenAIProvider:
    name = "openai"

    async def complete(
        self,
        customer_text: str,
        *,
        history: list[dict[str, str]] | None = None,
        system_prompt: str,
        model: str | None = None,
        api_key: str | None = None,
    ) -> str:
        """Non-streaming completion. Returns the full reply as one string."""
        s = get_settings()
        effective_key = (api_key or s.openai_api_key or "").strip()
        chosen_model = (model or s.openai_model).strip() or "gpt-4o-mini"

        if not effective_key or not effective_key.startswith("sk-"):
            log.error("OpenAI API key missing or invalid (no per-user key, no env).")
            return FALLBACK_REPLY

        payload = {
            "model": chosen_model,
            "messages": _build_messages(system_prompt, history, customer_text),
            "temperature": s.openai_temperature,
            "max_completion_tokens": s.openai_max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {effective_key}",
            "Content-Type": "application/json",
        }

        try:
            client = get_openai_client()
            resp = await client.post(OPENAI_URL, headers=headers, json=payload)
            if resp.status_code == 200:
                text = resp.json()["choices"][0]["message"]["content"]
                log.info("OpenAI reply: %s", text[:100])
                return text
            log.error("OpenAI %s: %s", resp.status_code, resp.text[:500])
        except Exception as exc:
            log.error("OpenAI request failed: %s", exc)

        return FALLBACK_REPLY

    async def stream_sentences(
        self,
        customer_text: str,
        *,
        history: list[dict[str, str]] | None = None,
        system_prompt: str,
        model: str | None = None,
        api_key: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream the completion and yield speakable chunks as they're ready.

        Yields cleaned text fragments — control tags like [HOT_LEAD] /
        [END_CALL] are held back until the tag closes, then dropped from
        the yielded stream. This guarantees TTS never speaks the literal
        characters "[HOT_LEAD]" at the customer.

        Consumers should concatenate all yielded chunks to get the full
        reply text, then run the result through `clean_reply()` for tag
        detection (end_call / hot_lead flags).
        """
        s = get_settings()
        effective_key = (api_key or s.openai_api_key or "").strip()
        chosen_model = (model or s.openai_model).strip() or "gpt-4o-mini"

        if not effective_key or not effective_key.startswith("sk-"):
            log.error("OpenAI API key missing or invalid (streaming).")
            yield FALLBACK_REPLY
            return

        payload = {
            "model": chosen_model,
            "messages": _build_messages(system_prompt, history, customer_text),
            "temperature": s.openai_temperature,
            "max_completion_tokens": s.openai_max_tokens,
            "stream": True,
        }
        headers = {
            "Authorization": f"Bearer {effective_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

        client = get_openai_client()
        buffer = ""           # not-yet-yielded text (post tag-strip)
        inside_tag = False    # are we mid-"[HOT_LEAD]" / "[END_CALL]"?
        tag_buffer = ""       # raw chars while inside a tag
        got_anything = False
        chunks_emitted = 0    # used to apply the smaller first-chunk threshold

        try:
            async with client.stream(
                "POST", OPENAI_URL, headers=headers, json=payload
            ) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", "replace")[:400]
                    log.error("OpenAI stream %s: %s", resp.status_code, body)
                    yield FALLBACK_REPLY
                    return

                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    try:
                        delta = obj["choices"][0].get("delta", {}).get("content")
                    except (KeyError, IndexError):
                        delta = None
                    if not delta:
                        continue

                    got_anything = True
                    # Char-by-char so we can detect tag boundaries robustly.
                    for ch in delta:
                        if inside_tag:
                            tag_buffer += ch
                            if ch == "]":
                                # Tag closed — drop it entirely from TTS output.
                                # (clean_reply on the joined full-text will still
                                # detect the tag for end_call / hot_lead flags.)
                                inside_tag = False
                                tag_buffer = ""
                            elif len(tag_buffer) > 32:
                                # Not actually a tag — emit what we swallowed.
                                buffer += tag_buffer
                                inside_tag = False
                                tag_buffer = ""
                            continue

                        if ch == "[":
                            inside_tag = True
                            tag_buffer = "["
                            continue

                        buffer += ch

                        # Emit on a clause/sentence boundary. The first
                        # chunk uses a smaller threshold so audio starts
                        # playing fast — the customer hears something
                        # within ~600-800ms of finishing their turn
                        # instead of waiting for a full sentence to
                        # synthesise.
                        threshold = (
                            _FIRST_CHUNK_MIN_CHARS
                            if chunks_emitted == 0
                            else _MIN_CHUNK_CHARS
                        )
                        if ch in _SENTENCE_ENDERS and len(buffer) >= threshold:
                            chunk = buffer.strip()
                            if chunk:
                                yield chunk
                                chunks_emitted += 1
                            buffer = ""

                # Flush any remaining non-tag buffer.
                if tag_buffer and not inside_tag:
                    buffer += tag_buffer
                tail = buffer.strip()
                if tail:
                    yield tail

        except Exception as exc:
            log.error("OpenAI stream failed: %s", exc)
            if not got_anything:
                yield FALLBACK_REPLY
