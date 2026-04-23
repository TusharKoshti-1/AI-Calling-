"""
app.core.passwords
──────────────────
Password hashing using bcrypt. Keep this isolated from everything else
so it's trivial to swap to argon2 later without touching auth flow.
"""
from __future__ import annotations

import bcrypt


# bcrypt's maximum input size is 72 bytes. Silently truncate longer inputs
# to match library behavior explicitly rather than letting users hit errors.
_MAX_PW_BYTES = 72


def _truncate(pw: str) -> bytes:
    return pw.encode("utf-8")[:_MAX_PW_BYTES]


def hash_password(plain: str) -> str:
    """Return a bcrypt-hashed password (cost 12)."""
    return bcrypt.hashpw(_truncate(plain), bcrypt.gensalt(rounds=12)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time check. Returns False if hashed is malformed."""
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(_truncate(plain), hashed.encode())
    except (ValueError, TypeError):
        return False
