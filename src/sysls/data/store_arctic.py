"""ArcticDB-backed time-series store for production data persistence.

Provides an :class:`ArcticStore` implementation of
:class:`~sysls.data.store.TimeSeriesStore` using ArcticDB for versioned,
columnar time-series storage.

ArcticDB is an **optional** dependency.  This module is importable
regardless of whether ``arcticdb`` is installed, but instantiating
:class:`ArcticStore` without it will raise :class:`ImportError`.

Typical usage::

    store = ArcticStore(uri="lmdb://data/arctic")
    await store.write("AAPL/1d/bars", bars_df, metadata={"source": "polygon"})
    df = await store.read("AAPL/1d/bars", start=dt1, end=dt2)

Install with::

    pip install 'sysls[arctic]'
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from sysls.core.exceptions import DataError, DataNotFoundError
from sysls.data.store import TimeSeriesStore

if TYPE_CHECKING:
    from datetime import datetime

    import pandas as pd

# Graceful import -- module is importable even without arcticdb installed.
try:
    import arcticdb

    _HAS_ARCTICDB = True
except ImportError:
    arcticdb = None  # type: ignore[assignment]
    _HAS_ARCTICDB = False


class ArcticStore(TimeSeriesStore):
    """ArcticDB-backed time-series store.

    Uses ArcticDB with an LMDB backend for local development or S3 for
    production.  All ArcticDB calls are synchronous and are wrapped
    in ``asyncio.to_thread`` for async compatibility.

    Args:
        uri: ArcticDB connection URI (e.g., ``"lmdb://data/arctic"``).
        library_name: Name of the ArcticDB library.  Defaults to
            ``"market_data"``.

    Raises:
        ImportError: If ``arcticdb`` is not installed.
    """

    def __init__(self, uri: str, library_name: str = "market_data") -> None:
        if not _HAS_ARCTICDB:
            msg = (
                "arcticdb is required for ArcticStore. "
                "Install it with: pip install 'sysls[arctic]'"
            )
            raise ImportError(msg)
        self._store = arcticdb.Arctic(uri)
        self._lib = self._store.get_library(library_name, create_if_missing=True)

    # -- Write ---------------------------------------------------------

    async def write(
        self,
        symbol: str,
        data: pd.DataFrame,
        metadata: dict[str, str] | None = None,
    ) -> None:
        """Write (or overwrite) data for a symbol via ArcticDB.

        Args:
            symbol: Storage key (see :func:`~sysls.data.store.make_symbol_key`).
            data: DataFrame with ``DatetimeIndex`` and normalized schema.
            metadata: Optional key-value metadata to store alongside.

        Raises:
            DataError: If the write fails.
        """
        try:
            await asyncio.to_thread(self._lib.write, symbol, data, metadata=metadata)
        except Exception as exc:
            raise DataError(f"Failed to write symbol {symbol!r}: {exc}") from exc

    # -- Read ----------------------------------------------------------

    async def read(
        self,
        symbol: str,
        start: datetime | None = None,
        end: datetime | None = None,
        columns: list[str] | None = None,
    ) -> pd.DataFrame:
        """Read data for a symbol from ArcticDB, optionally filtered.

        Args:
            symbol: Storage key.
            start: Inclusive start of date range filter.  ``None`` means
                no lower bound.
            end: Inclusive end of date range filter.  ``None`` means
                no upper bound.
            columns: Subset of columns to return.  ``None`` returns all.

        Returns:
            A DataFrame with ``DatetimeIndex``.

        Raises:
            DataNotFoundError: If the symbol does not exist.
            DataError: If the read fails for another reason.
        """
        if not await self.has_symbol(symbol):
            raise DataNotFoundError(f"Symbol not found: {symbol}")

        try:
            # Build optional date_range for ArcticDB query
            date_range = None
            if start is not None or end is not None:
                date_range = (start, end)

            versioned_item = await asyncio.to_thread(
                self._lib.read,
                symbol,
                date_range=date_range,
                columns=columns,
            )
            return versioned_item.data
        except Exception as exc:
            raise DataError(f"Failed to read symbol {symbol!r}: {exc}") from exc

    # -- Append --------------------------------------------------------

    async def append(
        self,
        symbol: str,
        data: pd.DataFrame,
    ) -> None:
        """Append rows to an existing symbol in ArcticDB.

        Args:
            symbol: Storage key.
            data: New rows to append.

        Raises:
            DataNotFoundError: If the symbol does not exist.
            DataError: If the append fails.
        """
        if not await self.has_symbol(symbol):
            raise DataNotFoundError(f"Symbol not found: {symbol}")

        try:
            await asyncio.to_thread(self._lib.append, symbol, data)
        except Exception as exc:
            raise DataError(f"Failed to append to symbol {symbol!r}: {exc}") from exc

    # -- Metadata / management -----------------------------------------

    async def list_symbols(self) -> list[str]:
        """List all symbols present in the ArcticDB library.

        Returns:
            Sorted list of symbol keys.
        """
        symbols = await asyncio.to_thread(self._lib.list_symbols)
        return sorted(symbols)

    async def has_symbol(self, symbol: str) -> bool:
        """Check whether a symbol exists in the ArcticDB library.

        Args:
            symbol: Storage key to check.

        Returns:
            ``True`` if the symbol exists, ``False`` otherwise.
        """
        return await asyncio.to_thread(self._lib.has_symbol, symbol)

    async def delete(self, symbol: str) -> None:
        """Delete all data and metadata for a symbol from ArcticDB.

        Safe to call for non-existent symbols (no-op).

        Args:
            symbol: Storage key to delete.
        """
        if not await self.has_symbol(symbol):
            return

        try:
            await asyncio.to_thread(self._lib.delete, symbol)
        except Exception as exc:
            raise DataError(f"Failed to delete symbol {symbol!r}: {exc}") from exc
