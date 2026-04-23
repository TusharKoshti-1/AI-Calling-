"""Twilio HMAC signature verification — canonicalisation + matching."""
from __future__ import annotations

import base64
import hashlib
import hmac

from app.core.security import _compute_twilio_signature


def test_twilio_signature_matches_algorithm_spec():
    """The algorithm per Twilio's docs:

        base64( HMAC-SHA1( auth_token,  url + "".join(k+v for k,v in sorted(params))) )

    We recompute the expected value by hand rather than hard-coding a magic
    string — that way the test self-describes the algorithm.
    """
    auth_token = "12345"
    url = "https://mycompany.com/myapp.php?foo=1&bar=2"
    params = {
        "Digits": "1234",
        "To": "+18005551212",
        "From": "+14158675310",
        "Caller": "+14158675310",
        "CallSid": "CA1234567890ABCDE",
    }

    data = url + "".join(f"{k}{v}" for k, v in sorted(params.items()))
    expected = base64.b64encode(
        hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest()
    ).decode()

    assert _compute_twilio_signature(auth_token, url, params) == expected


def test_twilio_signature_changes_when_param_changes():
    url = "https://example.com/webhook"
    a = _compute_twilio_signature("token", url, {"x": "1"})
    b = _compute_twilio_signature("token", url, {"x": "2"})
    assert a != b


def test_twilio_signature_stable_for_same_inputs():
    """Deterministic — same inputs must yield same output."""
    params = {"B": "2", "A": "1", "C": "3"}
    url = "https://example.com/w"
    a = _compute_twilio_signature("tok", url, params)
    b = _compute_twilio_signature("tok", url, dict(params))
    assert a == b
