"""Custom exception hierarchy for sysls.

All sysls exceptions inherit from ``SyslsError`` so callers can catch the
entire hierarchy with a single except clause when appropriate.
"""

from __future__ import annotations


class SyslsError(Exception):
    """Base exception for all sysls errors."""


# ---------------------------------------------------------------------------
# Configuration errors
# ---------------------------------------------------------------------------


class ConfigError(SyslsError):
    """Raised when configuration is invalid or cannot be loaded."""


# ---------------------------------------------------------------------------
# Venue / execution errors
# ---------------------------------------------------------------------------


class VenueError(SyslsError):
    """Raised when a venue adapter encounters an error.

    Attributes:
        venue: Name of the venue that produced the error.
    """

    def __init__(self, message: str, *, venue: str) -> None:
        self.venue = venue
        super().__init__(f"[{venue}] {message}")


class OrderError(VenueError):
    """Raised when an order operation fails at the venue level."""


class ConnectionError(VenueError):  # noqa: A001
    """Raised when a venue connection fails or is lost."""


# ---------------------------------------------------------------------------
# Data errors
# ---------------------------------------------------------------------------


class DataError(SyslsError):
    """Raised when a data connector encounters an error."""


class DataNotFoundError(DataError):
    """Raised when requested data is not available."""


# ---------------------------------------------------------------------------
# Strategy errors
# ---------------------------------------------------------------------------


class StrategyError(SyslsError):
    """Raised when a strategy encounters an unrecoverable error."""


# ---------------------------------------------------------------------------
# Risk errors
# ---------------------------------------------------------------------------


class RiskLimitError(SyslsError):
    """Raised when a risk limit is breached.

    Attributes:
        limit_name: Identifier for the limit that was breached.
    """

    def __init__(self, message: str, *, limit_name: str) -> None:
        self.limit_name = limit_name
        super().__init__(f"[{limit_name}] {message}")


# ---------------------------------------------------------------------------
# Event bus errors
# ---------------------------------------------------------------------------


class EventBusError(SyslsError):
    """Raised when the event bus encounters an error."""
