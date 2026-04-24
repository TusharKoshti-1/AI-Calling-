"""
app.services.memory_service
───────────────────────────
Turns a raw customer_memory row into a compact "CUSTOMER CONTEXT" block
that can be appended to the system prompt, and (post-call) extracts
facts from a transcript and persists them back.

Why a separate service layer rather than inlining in the orchestrator:
  • The formatting rules (what to include, how to phrase it) are UX
    decisions — tweaking them shouldn't require touching the call-flow
    state machine.
  • The extractor is a self-contained LLM call; keeping it here makes
    it obvious that it's async and fire-and-forget from the caller's
    perspective.
"""
from __future__ import annotations

import json
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.repositories.customer_memory import CustomerMemoryRepository
from app.services.http_client import get_openai_client

log = get_logger(__name__)

# Cheap + fast model for the fact-extraction pass. We don't need the
# flagship model here — the input is short, the output is tiny JSON,
# and this runs AFTER the call has ended so latency is not user-visible.
_EXTRACTION_MODEL = "gpt-4o-mini"

# The extractor is deliberately VERTICAL-NEUTRAL. It doesn't assume the
# tenant is running a car-service bot or a real-estate bot — it extracts
# whatever facts the customer actually revealed, and leaves everything
# else null. The vertical-specific fields (car_model, service_status)
# are OPTIONAL — only filled when the call transcript actually mentions
# them, otherwise omitted.
_EXTRACTION_SYSTEM = """You extract structured facts from a phone call transcript between an AI phone assistant and a customer. The assistant might be calling for any kind of business — car service, real estate, clinic, gym, utility, sales follow-up, etc. Do not assume a specific vertical; extract whatever the customer actually said.

Output ONLY a compact JSON object, nothing else — no prose, no markdown.

Fields (ALL optional — omit any you can't fill confidently from the transcript):
  topic_summary      : string. One short phrase describing WHY the call happened,
                       as best you can tell (e.g. "car service reminder",
                       "property inquiry follow-up", "appointment confirmation").
  last_call_summary  : string. One SHORT sentence summarising how this call went.
  last_lead_status   : string. One of "hot", "warm", "cold".
                       hot  = customer agreed to book / take a next step.
                       warm = interested but not committing now.
                       cold = not interested / avoided the call.
  preferred_callback : string. e.g. "evenings", "after 6pm", "weekends", "next month".
  notes              : string. One short sentence with anything useful for the NEXT
                       call (e.g. "spouse handles this, call them instead",
                       "customer is abroad until March", "prefers WhatsApp"). Omit
                       if nothing notable.

Vertical-specific fields (ONLY fill if the customer explicitly said so —
otherwise OMIT the field entirely, do not guess):
  car_model          : string. Make + model if mentioned, e.g. "Nissan Patrol 2021".
                       Only for car-service calls. Leave out otherwise.
  service_status     : string. One of "due", "overdue", "done recently",
                       "scheduled", "not sure". Only for calls about servicing
                       or appointments. Leave out otherwise.

Rules:
  - Extract only what the customer actually said. Do NOT invent.
  - Omit a field entirely rather than guessing.
  - Keep strings short, plain text.
  - Return {} if there's nothing useful to extract."""


# Fields we're allowed to read out of the extractor's JSON. Any extra key
# the model hallucinates is silently dropped — defence in depth against
# prompt injection via the transcript.
_ALLOWED_EXTRACT_KEYS = frozenset({
    "topic_summary", "last_call_summary", "last_lead_status",
    "preferred_callback", "notes",
    # Vertical-specific fields (only filled when the call mentioned them)
    "car_model", "service_status",
})


class MemoryService:
    def __init__(self, repo: CustomerMemoryRepository | None = None) -> None:
        self._repo = repo or CustomerMemoryRepository()

    # ── Read path (pre-call) ───────────────────────────────────
    async def load_context_block(self, user_id: str, phone: str) -> str:
        """Return a short, neutral context block to inject into the prompt.

        Returns an empty string when there's no memory yet — the caller
        can then just skip the block entirely.
        """
        try:
            row = await self._repo.get(user_id, phone)
        except Exception as exc:
            log.error("memory load failed for %s: %s", phone, exc)
            return ""
        return self._format_for_prompt(row) if row else ""

    @staticmethod
    def _format_for_prompt(row: dict[str, Any]) -> str:
        """Render a memory row as a compact, natural-sounding prompt block.

        The block is INFORMATION for the AI, not a script to read aloud.
        We explicitly DON'T instruct it to reference this verbatim on
        every call — otherwise the agent would open with creepy "I see
        you have a Toyota Camry" lines every time.

        Fields are rendered only when populated. A brand-new memory row
        (e.g. bumped call_count but no extracted facts yet) produces an
        empty block so the prompt stays clean.
        """
        lines: list[str] = []

        # Generic fields
        topic = (row.get("topic_summary") or "").strip()
        last_summary = (row.get("last_call_summary") or "").strip()
        last_lead = (row.get("last_lead_status") or "").strip()
        pref = (row.get("preferred_callback") or "").strip()
        notes = (row.get("notes") or "").strip()
        # Vertical-specific fields (may be absent / empty in many deployments)
        car = (row.get("car_model") or "").strip()
        status = (row.get("service_status") or "").strip()
        call_count = row.get("call_count") or 0

        if call_count and call_count > 1:
            # call_count is bumped at call start, so "> 1" means there was
            # at least one prior call before this one.
            lines.append(
                f"You have spoken to this customer {call_count - 1} time(s) before."
            )
        if topic:
            lines.append(f"Known interest / reason for calling: {topic}.")
        if car:
            lines.append(f"Car on file: {car}.")
        if status:
            lines.append(f"Last known service status: {status}.")
        if last_lead:
            lines.append(f"Previous call classified them as: {last_lead} lead.")
        if pref:
            lines.append(f"They prefer callbacks: {pref}.")
        if last_summary:
            lines.append(f"Summary of last call: {last_summary}")
        if notes:
            lines.append(f"Notes: {notes}")

        if not lines:
            return ""

        header = (
            "\n\n---\nCUSTOMER CONTEXT (internal — use naturally, do not read out "
            "verbatim, do not mention that you have notes):\n"
        )
        return header + "\n".join(f"  • {line}" for line in lines)

    # ── Write path (post-call) ─────────────────────────────────
    async def record_call_start(self, user_id: str, phone: str) -> None:
        """Called when a call begins — bumps call count + last_seen."""
        try:
            await self._repo.bump_call_count(user_id, phone)
        except Exception as exc:
            log.error("bump_call_count failed: %s", exc)

    async def extract_and_save(
        self,
        user_id: str,
        phone: str,
        transcript: str,
        api_key: str | None,
    ) -> None:
        """Run the extractor LLM on the transcript and upsert facts.

        Safe to call with a weak/failing API key — logs the error and
        returns silently rather than raising. The call has already ended
        so there's no user-visible failure mode.
        """
        if not transcript.strip() or not phone:
            return

        s = get_settings()
        effective_key = (api_key or s.openai_api_key or "").strip()
        if not effective_key or not effective_key.startswith("sk-"):
            log.info("extract skipped (no OpenAI key) for %s", phone)
            return

        payload = {
            "model": _EXTRACTION_MODEL,
            "messages": [
                {"role": "system", "content": _EXTRACTION_SYSTEM},
                {"role": "user", "content": f"TRANSCRIPT:\n{transcript}\n\nJSON:"},
            ],
            "temperature": 0.0,          # deterministic — we want facts, not flavour
            "response_format": {"type": "json_object"},
            "max_completion_tokens": 200,
        }
        headers = {
            "Authorization": f"Bearer {effective_key}",
            "Content-Type": "application/json",
        }

        try:
            client = get_openai_client()
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers, json=payload,
            )
            if resp.status_code != 200:
                log.error("extractor %s: %s", resp.status_code, resp.text[:300])
                return
            content = resp.json()["choices"][0]["message"]["content"]
        except Exception as exc:
            log.error("extractor request failed: %s", exc)
            return

        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            log.error("extractor returned non-JSON: %s", content[:200])
            return
        if not isinstance(data, dict):
            return

        # Whitelist — drop anything unexpected.
        facts = {k: v for k, v in data.items() if k in _ALLOWED_EXTRACT_KEYS}
        # Normalise types — the LLM sometimes returns a list or int where
        # we asked for a string; coerce or drop.
        cleaned: dict[str, str | None] = {}
        for k, v in facts.items():
            if v is None or v == "":
                continue
            if isinstance(v, (list, dict)):
                v = json.dumps(v, ensure_ascii=False)
            if not isinstance(v, str):
                v = str(v)
            v = v.strip()[:500]  # cap length defensively
            cleaned[k] = v or None

        if not cleaned:
            log.info("extractor returned no useful facts for %s", phone)
            return

        try:
            await self._repo.upsert_facts(user_id, phone, **cleaned)
            log.info("memory updated for %s: %s", phone, list(cleaned.keys()))
        except Exception as exc:
            log.error("memory upsert failed for %s: %s", phone, exc)


memory_service = MemoryService()
