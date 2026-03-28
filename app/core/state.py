"""
app/core/state.py
Runtime in-memory state for live calls.
DB is source of truth for history; this holds ephemeral live-call data.
"""
from typing import Dict, Optional
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class CallSession:
    """Single live call session — held in RAM during the call."""
    sid: str
    phone: str
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    history: list = field(default_factory=list)   # Groq message history
    pending_tts: Optional[str] = None             # Text waiting to be synthesised
    agent_name: str = "Sara"
    agency_name: str = ""
    system_prompt: str = ""


class CallStateStore:
    """Thread-safe (asyncio single-thread) in-memory call store."""

    def __init__(self):
        self._sessions: Dict[str, CallSession] = {}
        self._intro_cache: Optional[bytes] = None

    def create(self, sid: str, phone: str, agent_name: str = "Sara",
               agency_name: str = "", system_prompt: str = "") -> CallSession:
        session = CallSession(
            sid=sid, phone=phone,
            agent_name=agent_name, agency_name=agency_name,
            system_prompt=system_prompt,
        )
        self._sessions[sid] = session
        return session

    def get(self, sid: str) -> Optional[CallSession]:
        return self._sessions.get(sid)

    def remove(self, sid: str) -> Optional[CallSession]:
        return self._sessions.pop(sid, None)

    def exists(self, sid: str) -> bool:
        return sid in self._sessions

    def all_sids(self) -> list:
        return list(self._sessions.keys())

    # ── Intro audio cache ────────────────────────────────────
    def get_intro_audio(self) -> Optional[bytes]:
        return self._intro_cache

    def set_intro_audio(self, audio: bytes) -> None:
        self._intro_cache = audio

    def clear_intro_cache(self) -> None:
        self._intro_cache = None


# Singleton store — imported by routes
call_store = CallStateStore()
