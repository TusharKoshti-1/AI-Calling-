"""
app.services.cr_handler
───────────────────────
ConversationRelay (CR) websocket handler.

This file replaces ~600 lines of v9 chunked-TwiML reply pipeline with
~400 lines of clean async websocket logic. Twilio handles all the audio
streaming, STT, TTS, barge-in, and turn-taking; we receive transcribed
prompts and respond with text tokens.

Twilio's websocket protocol (the messages we care about)
────────────────────────────────────────────────────────
INBOUND (Twilio → us):
  setup       Once at start. Contains callSid, accountSid, from, to,
              direction, customParameters (where our user_id lives).
  prompt      Customer finished speaking. {voicePrompt: text, lang, last}
              `last` is True only on the final delta of a turn.
  interrupt   Customer started talking over the AI mid-utterance.
              {utteranceUntilInterrupt, durationUntilInterruptMs}
              We MUST cancel any in-flight LLM stream when this fires —
              otherwise we'll keep generating tokens that get spoken
              after the customer's actual reply.
  dtmf        Keypad press. We don't currently use these.
  error       Twilio noticed something wrong on its end.

OUTBOUND (us → Twilio):
  text        {token, last, interruptible, preemptible}. Each one is
              streamed to TTS as soon as we send it. last=True signals
              end-of-turn so TTS knows to flush.
  end         {handoffData}. Closes the session. handoffData is JSON
              that comes back to us via the <Connect> action URL — used
              for live-agent handoff to pass "transfer to +971..." to
              the post-relay TwiML handler.

Why one handler per call (not per user_id)
──────────────────────────────────────────
Each Twilio call opens its own websocket. Since the customer's user_id
is in the customParameters of the setup message, one handler instance
serves one call cleanly. State for the call lives on the handler; when
the websocket closes, we tear down. No global call-state map needed
just for the websocket layer.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from app.core.logging import get_logger
from app.db.repositories.calls import CallsRepository
from app.db.repositories.messages import MessagesRepository
from app.services.llm import llm_registry
from app.services.llm.openai import OpenAIProvider
from app.services.settings_service import UserSettings, settings_service
from app.services.text_cleaner import clean_reply

log = get_logger(__name__)

# Soft max on conversation history length sent to LLM. ConversationRelay
# can run for a long time; we don't want token costs to balloon. 30
# turns ≈ 60 messages is a good ceiling — long enough that a 5-minute
# call still has plenty of context, short enough to keep prompts fast.
MAX_HISTORY_MESSAGES = 60

# Tag → action map. We do tag detection on the FULL post-stream reply
# (via clean_reply) rather than trying to pattern-match on streamed
# tokens — much more reliable, since gpt-4o-mini sometimes splits a
# tag across token boundaries.
HANDOFF_REASON_TRANSFER = "transfer"
HANDOFF_REASON_END = "end"


class CRHandler:
    """One-shot handler for a single Twilio ConversationRelay session.

    Lifecycle:
        1. Construct, await accept().
        2. handle_setup() runs once on the first message.
        3. Loop processing prompt/interrupt/etc messages until either
           the websocket closes or we send our own end message.
        4. _close() persists the transcript and tears down state.

    Only ever used as `await CRHandler(ws).run()`.
    """

    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws

        # Populated by handle_setup
        self.call_sid: str = ""
        self.user_id: str = ""
        self.from_number: str = ""
        self.to_number: str = ""
        self.session_id: str = ""
        self.us: UserSettings | None = None

        # Conversation history in the format the LLM expects.
        # Includes the welcome greeting as the first assistant turn so
        # the LLM has context for the customer's first reply.
        self.history: list[dict[str, str]] = []

        # The currently-in-flight reply task. Cancelled on barge-in OR
        # when a new prompt arrives before the previous one finished
        # (rare, but possible during low-quality audio).
        self.reply_task: asyncio.Task[None] | None = None

        # Whether handle_setup has run successfully.
        self._setup_ok = False

        # DB repos
        self._calls = CallsRepository()
        self._messages = MessagesRepository()

    # ────────────────────────────────────────────────────────
    # Public entrypoint
    # ────────────────────────────────────────────────────────
    async def run(self) -> None:
        await self.ws.accept()
        log.info("CR websocket accepted")
        try:
            async for raw in self.ws.iter_text():
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    log.warning("CR: ignoring non-JSON message: %r", raw[:100])
                    continue
                await self._dispatch(msg)
        except WebSocketDisconnect:
            log.info("[%s] CR websocket disconnected by client", self.call_sid)
        except Exception as exc:
            log.exception("[%s] CR websocket loop error: %s", self.call_sid, exc)
        finally:
            await self._close()

    # ────────────────────────────────────────────────────────
    # Message routing
    # ────────────────────────────────────────────────────────
    async def _dispatch(self, msg: dict[str, Any]) -> None:
        msg_type = msg.get("type")
        if msg_type == "setup":
            await self._handle_setup(msg)
        elif msg_type == "prompt":
            await self._handle_prompt(msg)
        elif msg_type == "interrupt":
            await self._handle_interrupt(msg)
        elif msg_type == "dtmf":
            log.info("[%s] DTMF %r (ignored)", self.call_sid, msg.get("digit"))
        elif msg_type == "error":
            log.error("[%s] CR error from Twilio: %s",
                      self.call_sid, msg.get("description"))
        else:
            log.debug("[%s] CR unknown msg type %r", self.call_sid, msg_type)

    # ────────────────────────────────────────────────────────
    # Setup (first message)
    # ────────────────────────────────────────────────────────
    async def _handle_setup(self, msg: dict[str, Any]) -> None:
        self.call_sid = msg.get("callSid") or ""
        self.session_id = msg.get("sessionId") or ""
        self.from_number = msg.get("from") or ""
        self.to_number = msg.get("to") or ""
        params = msg.get("customParameters") or {}
        self.user_id = (params.get("user_id") or "").strip()

        # Light authenticity check: the accountSid in the setup message
        # should match our configured Twilio account. This isn't full
        # signature verification — that requires inspecting the upgrade
        # request headers, which FastAPI's WS layer doesn't expose
        # cleanly — but it stops trivially-spoofed setup messages from
        # other Twilio accounts hitting our WS.
        from app.core.config import get_settings
        env = get_settings()
        msg_account = (msg.get("accountSid") or "").strip()
        if env.twilio_account_sid and msg_account and msg_account != env.twilio_account_sid:
            log.error(
                "[%s] CR setup: accountSid mismatch (%s vs configured %s) — rejecting",
                self.call_sid, msg_account, env.twilio_account_sid,
            )
            await self._send_end(
                handoff_data={"reasonCode": "auth-error",
                              "reason": "account mismatch"}
            )
            return

        if not self.call_sid or not self.user_id:
            log.error(
                "CR setup missing call_sid (%r) or user_id (%r) — ending",
                self.call_sid, self.user_id,
            )
            await self._send_end(
                handoff_data={"reasonCode": "config-error",
                              "reason": "missing call/user identification"}
            )
            return

        try:
            self.us = await settings_service.for_user(self.user_id)
        except Exception as exc:
            log.error("[%s] CR setup: settings load failed: %s",
                      self.call_sid, exc)
            await self._send_end(
                handoff_data={"reasonCode": "config-error",
                              "reason": "settings unavailable"}
            )
            return

        # Seed history with the welcome greeting that ConversationRelay
        # is going to speak. This is critical: when the customer replies
        # to the greeting, the LLM needs to know what was said so it can
        # respond contextually. Without this, the LLM thinks the customer
        # cold-opened with whatever they said.
        welcome = self._compute_welcome_greeting()
        self.history.append({"role": "assistant", "content": welcome})
        # Persist the welcome line as the first AI message in the
        # transcript so it shows up in the dashboard.
        asyncio.create_task(self._safe_insert_message("ai", welcome))

        # Mark the call as answered in the DB. We can't easily distinguish
        # "rang but no answer" from "answered" without the call-status
        # webhook, so we mark it answered here on the assumption that the
        # CR websocket only opens after pickup.
        asyncio.create_task(self._safe_call_update(status="answered"))

        self._setup_ok = True
        log.info("[%s] CR setup ok — user=%s welcome=%r",
                 self.call_sid, self.user_id, welcome[:60])

    def _compute_welcome_greeting(self) -> str:
        """Build the line we passed to TwiML's welcomeGreeting attr.

        IMPORTANT: this MUST exactly match what the cr-greeting endpoint
        returned to Twilio, otherwise the LLM history will be out of sync
        with what the customer actually heard. Both call sites use the
        same helper (settings_service + this format) so they stay
        consistent — see twilio_webhooks.py::cr_greeting().
        """
        return _compose_welcome(self.us)

    # ────────────────────────────────────────────────────────
    # Prompt: customer finished speaking
    # ────────────────────────────────────────────────────────
    async def _handle_prompt(self, msg: dict[str, Any]) -> None:
        if not self._setup_ok:
            log.warning("[%s] CR prompt before setup; ignoring", self.call_sid)
            return

        text = (msg.get("voicePrompt") or "").strip()
        is_last = bool(msg.get("last", True))

        # ConversationRelay can send interim (partial) prompts and one
        # final prompt with last=True. We only act on the final one —
        # acting on interim prompts would make us reply to half-finished
        # sentences. last defaults to True per the docs.
        if not is_last:
            return

        if not text:
            return  # nothing to act on

        log.info("[%s] customer: %r", self.call_sid, text)
        self.history.append({"role": "user", "content": text})
        asyncio.create_task(self._safe_insert_message("customer", text))

        # Cancel any prior reply still streaming (covers the case where
        # the customer talks again before the previous reply was fully
        # streamed — rare but real).
        if self.reply_task is not None and not self.reply_task.done():
            self.reply_task.cancel()

        self.reply_task = asyncio.create_task(self._run_reply(text))

    # ────────────────────────────────────────────────────────
    # Interrupt: customer barged in over AI's voice
    # ────────────────────────────────────────────────────────
    async def _handle_interrupt(self, msg: dict[str, Any]) -> None:
        # Twilio has already stopped the TTS playback by the time we
        # receive this — but we still need to cancel our LLM stream so
        # we don't keep sending tokens that would queue up for the next
        # turn. Without this cancel, after a barge-in you'd hear a few
        # "stale" words mixed into the AI's response to the new prompt.
        if self.reply_task is not None and not self.reply_task.done():
            self.reply_task.cancel()
        spoken_so_far = (msg.get("utteranceUntilInterrupt") or "").strip()
        # Replace what would have been the full assistant turn with what
        # the customer ACTUALLY heard before they interrupted. This keeps
        # the LLM's idea of the conversation accurate.
        if self.history and self.history[-1].get("role") == "assistant":
            self.history[-1]["content"] = spoken_so_far
            log.info("[%s] barge-in after %dms; AI heard saying %r",
                     self.call_sid,
                     int(msg.get("durationUntilInterruptMs") or 0),
                     spoken_so_far[:60])

    # ────────────────────────────────────────────────────────
    # Reply pipeline (one task per customer turn)
    # ────────────────────────────────────────────────────────
    async def _run_reply(self, customer_text: str) -> None:
        """Stream LLM tokens straight to ConversationRelay."""
        assert self.us is not None
        t0 = datetime.now(timezone.utc)

        provider_name = self.us.resolve_llm_provider()
        provider = llm_registry.get(provider_name)
        system_prompt = self.us.resolve_system_prompt()
        api_key = (
            self.us.get("openai_api_key") if provider_name == "openai" else None
        )
        model = (
            self.us.get("openai_model")
            if provider_name == "openai"
            else self.us.get("groq_model")
        )

        history_for_llm = self._trimmed_history()

        # Accumulate the full reply text so we can run clean_reply
        # afterwards to detect the END_CALL/HOT_LEAD/TRANSFER tags.
        # We DON'T look for tags in the stream itself — too unreliable
        # with gpt-4o-mini's irregular token boundaries.
        accumulated: list[str] = []
        first_token_t: datetime | None = None

        try:
            if isinstance(provider, OpenAIProvider):
                # Token-level streaming path (lowest latency).
                # The provider's stream_tokens char-by-char tag-buffer
                # already swallows control tags like [END_CALL] — they
                # never appear in the chunks yielded here.
                async for chunk, flags in provider.stream_tokens(
                    customer_text,
                    history=history_for_llm,
                    system_prompt=system_prompt,
                    model=model,
                    api_key=api_key,
                ):
                    if first_token_t is None and chunk:
                        first_token_t = datetime.now(timezone.utc)
                    if chunk:
                        accumulated.append(chunk)
                        await self._send_text(
                            chunk, last=flags.get("last", False),
                        )
                    elif flags.get("last"):
                        # Empty + last → just the close marker.
                        await self._send_text("", last=True)
            else:
                # Non-OpenAI provider (e.g. Groq): no token streaming
                # available, fall back to one-shot complete and send as
                # a single text message.
                full = await provider.complete(
                    customer_text, history=history_for_llm,
                    system_prompt=system_prompt, model=model, api_key=api_key,
                )
                accumulated.append(full)
                first_token_t = datetime.now(timezone.utc)
                await self._send_text(full, last=True)
        except asyncio.CancelledError:
            # Barge-in or new turn started. Don't send anything else.
            log.info("[%s] reply cancelled (barge-in/new turn)", self.call_sid)
            raise
        except Exception as exc:
            log.error("[%s] reply pipeline error: %s", self.call_sid, exc)
            try:
                await self._send_text(
                    "Sorry, I missed that — could you say that again?",
                    last=True,
                )
            except Exception:
                pass
            return

        full_text = "".join(accumulated).strip()
        if first_token_t:
            ttft_ms = int((first_token_t - t0).total_seconds() * 1000)
            total_ms = int(
                (datetime.now(timezone.utc) - t0).total_seconds() * 1000
            )
            log.info(
                "[%s] AI: %r  [ttft %dms, total %dms]",
                self.call_sid, full_text[:80], ttft_ms, total_ms,
            )

        # Update history with what we actually said.
        self.history.append({"role": "assistant", "content": full_text})
        asyncio.create_task(self._safe_insert_message("ai", full_text))

        # Tag detection on the FULL reply (not the stream).
        cleaned = clean_reply(full_text)

        if cleaned.hot_lead:
            asyncio.create_task(self._safe_call_update(hot_lead=True))

        if cleaned.transfer_call:
            number = (self.us.get("transfer_number") or "").strip()
            if number:
                log.info("[%s] AI signaled TRANSFER → %s",
                         self.call_sid, number)
                # Brief pause so Twilio finishes speaking the "connecting
                # you now" line before we hand the call back to TwiML.
                # ~1500ms is the empirical sweet spot — shorter and the
                # user hears their last syllable cut off.
                await asyncio.sleep(1.5)
                await self._send_end({
                    "reasonCode": HANDOFF_REASON_TRANSFER,
                    "transfer_number": number,
                })
            else:
                log.warning(
                    "[%s] TRANSFER requested but no transfer_number set",
                    self.call_sid,
                )
                await asyncio.sleep(1.0)
                await self._send_end({"reasonCode": HANDOFF_REASON_END})
        elif cleaned.end_call or cleaned.hot_lead:
            # End the call. Small pause so the closing line plays out.
            log.info("[%s] AI signaled END_CALL", self.call_sid)
            await asyncio.sleep(1.5)
            await self._send_end({"reasonCode": HANDOFF_REASON_END})

    def _trimmed_history(self) -> list[dict[str, str]]:
        """Return the history capped at MAX_HISTORY_MESSAGES.

        We always keep the LAST N messages (rolling window). The system
        prompt is injected separately by the LLM provider so it stays
        cheap (cached) regardless of history length.
        """
        if len(self.history) <= MAX_HISTORY_MESSAGES:
            return list(self.history)
        return list(self.history[-MAX_HISTORY_MESSAGES:])

    # ────────────────────────────────────────────────────────
    # Outbound websocket helpers
    # ────────────────────────────────────────────────────────
    async def _send_text(self, token: str, *, last: bool) -> None:
        """Send a text token for Twilio to TTS-stream to the caller."""
        try:
            await self.ws.send_text(json.dumps({
                "type": "text",
                "token": token,
                "last": last,
                # We mark each token interruptible so the customer can
                # always barge in. This is a per-message override; the
                # default from the TwiML attribute also applies.
                "interruptible": True,
            }))
        except Exception as exc:
            log.error("[%s] send_text failed: %s", self.call_sid, exc)
            raise

    async def _send_end(self, handoff_data: dict[str, Any]) -> None:
        """End the session and pass handoff_data through to the
        <Connect action> callback.

        The action URL receives this JSON as the HandoffData form field
        and decides what to do (transfer, hangup, etc.) — see
        twilio_webhooks.py::cr_action.
        """
        try:
            await self.ws.send_text(json.dumps({
                "type": "end",
                "handoffData": json.dumps(handoff_data),
            }))
        except Exception as exc:
            log.error("[%s] send_end failed: %s", self.call_sid, exc)

    # ────────────────────────────────────────────────────────
    # Cleanup
    # ────────────────────────────────────────────────────────
    async def _close(self) -> None:
        if self.reply_task is not None and not self.reply_task.done():
            self.reply_task.cancel()
        # Final transcript persist. Status comes via the call-status
        # webhook later, but we update the transcript field now so it's
        # available even if status fires before our DB writes drain.
        if self.call_sid and self.user_id:
            transcript = self._format_transcript()
            try:
                await self._calls.update_by_sid(
                    self.call_sid, transcript=transcript,
                )
            except Exception as exc:
                log.warning("[%s] final transcript update failed: %s",
                            self.call_sid, exc)
        log.info("[%s] CR session closed", self.call_sid)

    def _format_transcript(self) -> str:
        if self.us is None:
            agent = "Agent"
        else:
            agent = self.us.get("agent_name", "Agent") or "Agent"
        lines: list[str] = []
        for m in self.history:
            who = f"{agent} (AI)" if m["role"] == "assistant" else "Customer"
            lines.append(f"{who}: {m['content']}")
        return "\n".join(lines)

    async def _safe_insert_message(self, role: str, content: str) -> None:
        try:
            await self._messages.insert(
                call_sid=self.call_sid, user_id=self.user_id,
                role=role, content=content,
            )
        except Exception as exc:
            log.warning("[%s] message insert failed: %s",
                        self.call_sid, exc)

    async def _safe_call_update(self, **fields: Any) -> None:
        try:
            await self._calls.update_by_sid(self.call_sid, **fields)
        except Exception as exc:
            log.warning("[%s] call update failed: %s", self.call_sid, exc)


# ════════════════════════════════════════════════════════════
# Welcome-greeting helper
# ════════════════════════════════════════════════════════════
def _compose_welcome(us: UserSettings | None) -> str:
    """Build the opening line.

    Used in TWO places — the cr-greeting endpoint sends it via the
    welcomeGreeting attribute on the TwiML, and the websocket handler
    seeds history with the same text. Both must agree, which is why
    the format lives in this single function.
    """
    if us is None:
        return "Hello, thanks for picking up — is now a good time?"
    agent = us.get("agent_name", "Sara") or "Sara"
    agency = (us.get("agency_name") or "").strip()
    if agency:
        return (
            f"Hi, this is {agent} calling from {agency} regarding your "
            f"car service — is this a good time to talk?"
        )
    return (
        f"Hi, this is {agent} — is this a good time to talk?"
    )


def compose_welcome_for(us: UserSettings | None) -> str:
    """Public alias of _compose_welcome for use by the TwiML endpoint."""
    return _compose_welcome(us)
