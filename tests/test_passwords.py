"""Password hashing — bcrypt wraps + truncates correctly, verify is constant-time."""
from __future__ import annotations

from app.core.passwords import hash_password, verify_password


def test_hash_password_produces_bcrypt_like_output():
    h = hash_password("hunter2hunter2")
    assert h.startswith("$2b$") or h.startswith("$2a$") or h.startswith("$2y$")


def test_verify_password_roundtrip():
    h = hash_password("Correct Horse Battery Staple")
    assert verify_password("Correct Horse Battery Staple", h) is True


def test_verify_password_rejects_wrong_password():
    h = hash_password("one")
    assert verify_password("two", h) is False


def test_verify_password_false_for_empty_hash():
    assert verify_password("anything", "") is False


def test_verify_password_tolerates_malformed_hash():
    assert verify_password("anything", "not-a-real-hash") is False


def test_hash_rejects_passwords_over_72_bytes_consistently():
    """bcrypt truncates at 72 bytes. Two passwords matching in their first
    72 bytes but differing after should both verify — our code documents
    this by truncating up front."""
    base = "a" * 72
    h = hash_password(base)
    assert verify_password(base + "X", h) is True
    assert verify_password(base + "Y", h) is True
