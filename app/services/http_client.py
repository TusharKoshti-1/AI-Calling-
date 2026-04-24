"""
app.services.http_client
────────────────────────
Process-wide httpx.AsyncClient singletons for outbound provider calls.

Why this exists
───────────────
Creating an httpx.AsyncClient per request forces a fresh TCP + TLS
handshake to api.openai.com / api.cartesia.ai on every call turn — that
adds roughly 100–300 ms per provider per turn on a real-time voice path
where every millisecond is audible silence for the caller.

A module-level client reuses HTTP/2 connections across requests, which
turns the "first request to this host" handshake into a one-time startup
cost rather than a per-turn cost.

We expose:
  • get_openai_client()   → long timeout; used for both streaming and non-streaming
  • get_cartesia_client() → bounded timeout; TTS must fail fast

Both clients are created lazily on first use so module import stays cheap
and unit tests that never hit the network don't open sockets.
"""
from __future__ import annotations

import httpx

_openai_client: httpx.AsyncClient | None = None
_cartesia_client: httpx.AsyncClient | None = None


def get_openai_client() -> httpx.AsyncClient:
    """Shared client for api.openai.com.

    Uses a generous read timeout because streaming completions can legitimately
    sit idle between chunks. Connect timeout is short — if we can't open a
    socket in 5s the network is broken, no point waiting.
    """
    global _openai_client
    if _openai_client is None:
        _openai_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0),
            limits=httpx.Limits(
                max_connections=50,
                max_keepalive_connections=20,
                keepalive_expiry=30.0,
            ),
            http2=False,  # OpenAI speaks HTTP/1.1 cleanly; h2 adds no value here
        )
    return _openai_client


def get_cartesia_client() -> httpx.AsyncClient:
    """Shared client for api.cartesia.ai.

    TTS latency is on the critical path, so keep the timeout tight — if
    Cartesia hasn't started responding in ~8s, something is wrong and we
    want to fall back rather than hang the call.
    """
    global _cartesia_client
    if _cartesia_client is None:
        _cartesia_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0),
            limits=httpx.Limits(
                max_connections=30,
                max_keepalive_connections=10,
                keepalive_expiry=30.0,
            ),
        )
    return _cartesia_client


async def close_all() -> None:
    """Close both clients on app shutdown."""
    global _openai_client, _cartesia_client
    if _openai_client is not None:
        try:
            await _openai_client.aclose()
        finally:
            _openai_client = None
    if _cartesia_client is not None:
        try:
            await _cartesia_client.aclose()
        finally:
            _cartesia_client = None
