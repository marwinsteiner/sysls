"""In-memory time-series store for testing and development.

Provides a :class:`MemoryStore` implementation of
:class:`~sysls.data.store.TimeSeriesStore` backed by plain Python
dictionaries.  All data lives in process memory and does not persist
across restarts.

Typical usage::

    store = MemoryStore()
    await store.write("AAPL/1d/bars", bars_df, metadata={"source": "polygon"})
    df = await store.read("AAPL/1d/bars", start=dt1, end=dt2)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

from sysls.core.exceptions import DataNotFoundError
from sysls.data.store import TimeSeriesStore

if TYPE_CHECKING:
    from datetime import datetime


class MemoryStore(TimeSeriesStore):
    """In-memory time-series store backed by a dict of DataFrames.

    Suitable for testing, development, and small datasets that fit
    in memory.  Data does not persist across process restarts.

    All methods return **copies** of stored data to prevent accidental
    mutation of the internal state.
    """

    def __init__(self) -> None:
        self._data: dict[str, pd.DataFrame] = {}
        self._metadata: dict[str, dict[str, str]] = {}

    # -- Write ---------------------------------------------------------

    async def write(
        self,
        symbol: str,
        data: pd.DataFrame,
        metadata: dict[str, str] | None = None,
    ) -> None:
        """Write (or overwrite) data for a symbol.

        Stores a defensive copy of the provided DataFrame so that later
        mutations by the caller do not affect stored data.

        Args:
            symbol: Storage key (see :func:`~sysls.data.store.make_symbol_key`).
            data: DataFrame with ``DatetimeIndex`` and normalized schema.
            metadata: Optional key-value metadata to store alongside.
        """
        self._data[symbol] = data.copy()
        if metadata is not None:
            self._metadata[symbol] = dict(metadata)
        elif symbol in self._metadata:
            # Overwrite clears previous metadata when none is provided
            del self._metadata[symbol]

    # -- Read ----------------------------------------------------------

    async def read(
        self,
        symbol: str,
        start: datetime | None = None,
        end: datetime | None = None,
        columns: list[str] | None = None,
    ) -> pd.DataFrame:
        """Read data for a symbol, optionally filtered by date range.

        Returns a defensive copy so that the caller cannot mutate the
        internal store state.

        Args:
            symbol: Storage key.
            start: Inclusive start of date range filter.  ``None`` means
                no lower bound.
            end: Inclusive end of date range filter.  ``None`` means
                no upper bound.
            columns: Subset of columns to return.  ``None`` returns all.

        Returns:
            A DataFrame with ``DatetimeIndex``.  Empty DataFrame if the
            date range contains no data.

        Raises:
            DataNotFoundError: If the symbol does not exist.
        """
        if symbol not in self._data:
            raise DataNotFoundError(f"Symbol not found: {symbol}")

        df = self._data[symbol]

        # Apply date range filter
        if start is not None:
            df = df[df.index >= pd.Timestamp(start)]
        if end is not None:
            df = df[df.index <= pd.Timestamp(end)]

        # Apply column subset
        if columns is not None:
            df = df[columns]

        return df.copy()

    # -- Append --------------------------------------------------------

    async def append(
        self,
        symbol: str,
        data: pd.DataFrame,
    ) -> None:
        """Append rows to an existing symbol.

        Concatenates new rows after existing data and sorts by index to
        maintain time ordering.

        Args:
            symbol: Storage key.
            data: New rows to append.

        Raises:
            DataNotFoundError: If the symbol does not exist.
        """
        if symbol not in self._data:
            raise DataNotFoundError(f"Symbol not found: {symbol}")

        self._data[symbol] = pd.concat([self._data[symbol], data.copy()]).sort_index()

    # -- Metadata / management -----------------------------------------

    async def list_symbols(self) -> list[str]:
        """List all symbols present in the store.

        Returns:
            Sorted list of symbol keys.
        """
        return sorted(self._data.keys())

    async def has_symbol(self, symbol: str) -> bool:
        """Check whether a symbol exists in the store.

        Args:
            symbol: Storage key to check.

        Returns:
            ``True`` if the symbol exists, ``False`` otherwise.
        """
        return symbol in self._data

    async def delete(self, symbol: str) -> None:
        """Delete all data and metadata for a symbol.

        Safe to call for non-existent symbols (no-op).

        Args:
            symbol: Storage key to delete.
        """
        self._data.pop(symbol, None)
        self._metadata.pop(symbol, None)

    # -- Extra helpers (not part of ABC) --------------------------------

    def get_metadata(self, symbol: str) -> dict[str, str] | None:
        """Return metadata for a symbol, or ``None`` if not set.

        This is a synchronous convenience method not part of the
        :class:`~sysls.data.store.TimeSeriesStore` ABC.

        Args:
            symbol: Storage key.

        Returns:
            Metadata dict, or ``None`` if the symbol has no metadata.
        """
        return self._metadata.get(symbol)
