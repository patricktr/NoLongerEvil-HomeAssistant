"""Exceptions for the No Longer Evil integration."""

from __future__ import annotations


class NLEError(Exception):
    """Base exception for No Longer Evil errors."""


class NLEAPIError(NLEError):
    """Exception for API errors."""


class NLEAuthenticationError(NLEError):
    """Exception for authentication errors."""


class NLEConnectionError(NLEError):
    """Exception for connection errors."""


class NLERateLimitError(NLEError):
    """Exception for rate limit errors."""

    def __init__(self, message: str, retry_after: str | None = None) -> None:
        """Initialize the exception."""
        super().__init__(message)
        self.retry_after = retry_after
