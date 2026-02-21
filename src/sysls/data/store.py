"""Time-series storage interface.

Defines the abstract interface for persisting and retrieving normalized
market data.  The primary implementation uses ArcticDB with an LMDB
backend for local development and S3 for production deployments.

Storage conventions:

* Data is keyed by **symbol** strings.  The recommended format is
  ``"{ticker}/{timeframe}/{source}"`` (e.g. ``"AAPL/1d/polygon"``).
* DataFrames MUST have a ``DatetimeIndex`` named ``"timestamp"`` in UTC.
* Schemas must conform to the canonical dtypes in
  :mod:`sysls.data.normalize`.
* Implementations handle versioning, deduplication, and append
  semantics internally.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    import pandas as pd

    from sysls.core.types import Instrument
    from sysls.data.connector import BarTimeframe


def make_symbol_key(
    instrument: Instrument,
    timeframe: BarTimeframe | None = None,
    *,
    data_type: str = "bars",
) -> str:
    """Build a canonical storage symbol key.

    Args:
        instrument: The instrument to key on.
        timeframe: Bar timeframe (required for bar data, ignored otherwise).
        data_type: One of ``"bars"``, ``"trades"``, ``"quotes"``.

    Returns:
        A string like ``"AAPL/1d/bars"`` or ``"BTC-USDT-PERP/trades"``.
    """
    parts = [instrument.symbol]
    if timeframe is not None:
        parts.append(timeframe.value)
    parts.append(data_type)
    return "/".join(parts)


class TimeSeriesStore(ABC):
    """Abstract interface for time-series data storage.

    Implementations handle versioning, deduplication, and efficient
    columnar storage for normalized market data.

    All methods are async to support non-blocking I/O in the event loop.
    Sync backends (e.g. ArcticDB) should use ``asyncio.to_thread`` in
    their implementations.
    """

    # -- Write ---------------------------------------------------------

    @abstractmethod
    async def write(
        self,
        symbol: str,
        data: pd.DataFrame,
        metadata: dict[str, str] | None = None,
    ) -> None:
        """Write (or overwrite) data for a symbol.

        Args:
            symbol: Storage key (see :func:`make_symbol_key`).
            data: DataFrame with ``DatetimeIndex`` and normalized schema.
            metadata: Optional key-value metadata to store alongside.

        Raises:
            DataError: If the write fails.
        """

    @abstractmethod
    async def append(
        self,
        symbol: str,
        data: pd.DataFrame,
    ) -> None:
        """Append rows to an existing symbol.

        New rows must have timestamps strictly after the last existing
        row.  Implementations should handle deduplication of overlapping
        data gracefully.

        Args:
            symbol: Storage key.
            data: New rows to append.

        Raises:
            DataError: If the append fails.
            DataNotFoundError: If the symbol does not exist.
        """

    # -- Read ----------------------------------------------------------

    @abstractmethod
    async def read(
        self,
        symbol: str,
        start: datetime | None = None,
        end: datetime | None = None,
        columns: list[str] | None = None,
    ) -> pd.DataFrame:
        """Read data for a symbol, optionally filtered by date range.

        Args:
            symbol: Storage key.
            start: Inclusive start of date range filter.  ``None`` means
                no lower bound.
            end: Inclusive end of date range filter.  ``None`` means
                no upper bound.
            columns: Subset of columns to return.  ``None`` returns all.

        Returns:
            A DataFrame with ``DatetimeIndex``.  Empty DataFrame if
            the date range contains no data.

        Raises:
            DataNotFoundError: If the symbol does not exist.
        """

    # -- Metadata / management -----------------------------------------

    @abstractmethod
    async def list_symbols(self) -> list[str]:
        """List all symbols present in the store.

        Returns:
            Sorted list of symbol keys.
        """

    @abstractmethod
    async def has_symbol(self, symbol: str) -> bool:
        """Check whether a symbol exists in the store.

        Args:
            symbol: Storage key to check.

        Returns:
            ``True`` if the symbol exists, ``False`` otherwise.
        """

    @abstractmethod
    async def delete(self, symbol: str) -> None:
        """Delete all data and metadata for a symbol.

        Safe to call for non-existent symbols (no-op).

        Args:
            symbol: Storage key to delete.
        """
