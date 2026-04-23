"""
app.core.exceptions
───────────────────
Custom exception hierarchy. HTTP layer translates these to responses.
"""
from __future__ import annotations


class AppError(Exception):
    """Base class for application-level errors."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code


class ValidationError(AppError):
    status_code = 400
    code = "validation_error"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class AuthError(AppError):
    status_code = 401
    code = "unauthorized"


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"


class UpstreamError(AppError):
    """External provider (Twilio / Groq / OpenAI / Cartesia) failed."""

    status_code = 502
    code = "upstream_error"


class ConfigurationError(AppError):
    status_code = 500
    code = "configuration_error"
