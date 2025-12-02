"""Twitter API error types."""

from __future__ import annotations

from enum import StrEnum
from typing import Self


class ErrorCode(StrEnum):
    """Standardized Twitter API error codes."""

    API_KEY_MISSING = "API_KEY_MISSING"
    RATE_LIMITED = "RATE_LIMITED"
    USER_NOT_FOUND = "USER_NOT_FOUND"
    USER_SUSPENDED = "USER_SUSPENDED"
    MAX_RETRIES_EXCEEDED = "MAX_RETRIES_EXCEEDED"
    NETWORK_ERROR = "NETWORK_ERROR"
    API_BOTTLENECK = "API_BOTTLENECK"
    INVALID_RESPONSE = "INVALID_RESPONSE"


class TwitterApiError(Exception):
    """Twitter API error with standardized error codes."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        http_status: int | None = None,
    ) -> None:
        """Initialize Twitter API error.

        Args:
            code: Standardized error code.
            message: Human-readable error message.
            http_status: Optional HTTP status code.
        """
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status

    def is_code(self, code: ErrorCode) -> bool:
        """Check if this error matches a specific code."""
        return self.code == code

    @classmethod
    def api_key_missing(cls) -> Self:
        """Create API key missing error."""
        return cls(
            ErrorCode.API_KEY_MISSING,
            "TWITTERX_APIKEY environment variable not set",
            http_status=401,
        )

    @classmethod
    def rate_limited(cls) -> Self:
        """Create rate limit error."""
        return cls(
            ErrorCode.RATE_LIMITED,
            "Twitter API rate limit exceeded",
            http_status=429,
        )

    @classmethod
    def user_not_found(cls, username: str) -> Self:
        """Create user not found error."""
        return cls(
            ErrorCode.USER_NOT_FOUND,
            f"User not found: {username}",
            http_status=404,
        )

    @classmethod
    def network_error(cls, details: str) -> Self:
        """Create network error."""
        return cls(
            ErrorCode.NETWORK_ERROR,
            f"Network error: {details}",
            http_status=502,
        )
