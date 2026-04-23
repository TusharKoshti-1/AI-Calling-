"""Session token issuance + verification."""
from __future__ import annotations

from app.core.session_tokens import hash_token, issue_token, verify_token_shape


def test_issued_token_has_random_hmac_suffix():
    t1 = issue_token("secret")
    t2 = issue_token("secret")
    assert t1.token != t2.token
    assert t1.token_hash != t2.token_hash


def test_issued_token_is_valid_shape():
    t = issue_token("secret")
    assert verify_token_shape(t.token, "secret")


def test_token_shape_rejects_wrong_secret():
    t = issue_token("correct")
    assert verify_token_shape(t.token, "wrong") is False


def test_token_shape_rejects_tampered_token():
    t = issue_token("secret")
    # Flip a char in the payload
    bad = "X" + t.token[1:]
    assert verify_token_shape(bad, "secret") is False


def test_token_shape_rejects_malformed():
    assert verify_token_shape("", "secret") is False
    assert verify_token_shape("nodot", "secret") is False
    assert verify_token_shape(".trailing", "secret") is False
    assert verify_token_shape("leading.", "secret") is False


def test_hash_token_is_deterministic_and_sha256():
    assert hash_token("abc") == hash_token("abc")
    assert len(hash_token("abc")) == 64  # hex SHA-256
