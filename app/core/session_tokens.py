"""
app.core.session_tokens
───────────────────────
Session token issuance and parsing.

Design:
  • Token format: JWT-ish — `{session_id}.{hmac}` base64-urlencoded.
  • Server-side `sessions` row is the source of truth (so signout revokes
    immediately). The cryptographic HMAC just prevents tampering on the
    wire — it's not the authority.
  • Never store the raw token in the DB; we store SHA-256(token) so a
    DB leak does not let an attacker reuse sessions.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass


@dataclass(frozen=True)
class IssuedToken:
    token: str          # hand this to the client as a cookie
    token_hash: str     # SHA-256 of the token — persist this in sessions.token_hash


def hash_token(token: str) -> str:
    """SHA-256 hex digest of the raw token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_token(secret: str) -> IssuedToken:
    """Generate a new random session token + its hash for DB storage.

    The token itself is a 32-byte URL-safe random string. We additionally
    carry an HMAC so even *before* hitting the DB we can reject obviously
    forged tokens — cheap defence in depth.
    """
    raw = secrets.token_urlsafe(32)
    mac = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()[:16]
    token = f"{raw}.{mac}"
    return IssuedToken(token=token, token_hash=hash_token(token))


def verify_token_shape(token: str, secret: str) -> bool:
    """Quick check: is this token well-formed + correctly HMAC'd?

    A `True` return does NOT mean the session is live — the caller must
    still look it up in the `sessions` table and check `expires_at` and
    `revoked_at`. `False` means skip the DB lookup entirely.
    """
    if not token or "." not in token:
        return False
    raw, _, mac = token.rpartition(".")
    if not raw or not mac:
        return False
    expected = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()[:16]
    return hmac.compare_digest(mac, expected)
