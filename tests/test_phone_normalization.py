"""Phone-number normalisation in TwilioClient."""
from __future__ import annotations

import pytest

from app.core.exceptions import ValidationError
from app.services.telephony.twilio_client import TwilioClient


class TestPhoneNormalization:
    def test_adds_plus_prefix(self):
        assert TwilioClient._normalize_phone("971501234567") == "+971501234567"

    def test_keeps_existing_plus(self):
        assert TwilioClient._normalize_phone("+971501234567") == "+971501234567"

    def test_strips_spaces_and_dashes(self):
        assert TwilioClient._normalize_phone("+971-50 123 4567") == "+971501234567"

    def test_empty_raises(self):
        with pytest.raises(ValidationError):
            TwilioClient._normalize_phone("")

    def test_non_digit_raises(self):
        with pytest.raises(ValidationError):
            TwilioClient._normalize_phone("+971ABCDE")

    def test_too_short_raises(self):
        with pytest.raises(ValidationError):
            TwilioClient._normalize_phone("+12")

    def test_too_long_raises(self):
        with pytest.raises(ValidationError):
            TwilioClient._normalize_phone("+1234567890123456")
