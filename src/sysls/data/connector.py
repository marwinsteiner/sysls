"""DataConnector abstract base class.

Defines the contract that all market data source implementations must follow.
Connectors provide two modes of data access:

1. **Historical** -- Fetch past data as pandas DataFrames with normalized
   column schemas defined in :mod:`sysls.data.normalize`.  Used for
   backtesting and analysis.
2. **Streaming** -- Subscribe to live data as async iterators of typed
   events from :mod:`sysls.core.events`.  Used for live trading.

All implementations must normalize output to the canonical schemas
regardless of the underlying source API format.

Example::

    connector = PolygonConnector(api_key="...")
    async with connector:
        bars = await connector.get_historical_bars(
            instrument=inst,
            start=datetime(2024, 1, 1),
            end=datetime(2024, 12, 31),
            timeframe=BarTimeframe.DAY_1,
        )
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum, unique
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from datetime import datetime

    import pandas as pd

    from sysls.core.events import BarEvent, QuoteEvent, TradeEvent
    from sysls.core.types import Instrument


@unique
class BarTimeframe(StrEnum):
    """Standard bar timeframes supported across data connectors.

    Values match common industry notation used by Polygon, DataBento,
    and other data providers.
    """

    SECOND_1 = "1s"
    MINUTE_1 = "1min"
    MINUTE_5 = "5min"
    MINUTE_15 = "15min"
    MINUTE_30 = "30min"
    HOUR_1 = "1h"
    HOUR_4 = "4h"
    DAY_1 = "1d"
    WEEK_1 = "1w"
    MONTH_1 = "1mo"


class DataConnector(ABC):
    """Abstract base class for market data connectors.

    Subclasses implement data retrieval from a specific source (Polygon,
    DataBento, ArcticDB, etc.) and normalize output to the canonical
    schemas defined in :mod:`sysls.data.normalize`.

    Connectors support the async context manager protocol for clean
    resource lifecycle management::

        async with PolygonConnector(api_key="...") as conn:
            bars = await conn.get_historical_bars(...)
    """

    # -- Lifecycle -----------------------------------------------------

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the data source.

        Raises:
            DataError: If the connection cannot be established.
        """

    @abstractmethod
    async def disconnect(self) -> None:
        """Cleanly release all resources and connections.

        Safe to call multiple times.  Must not raise if already
        disconnected.
        """

    async def __aenter__(self) -> DataConnector:
        """Enter async context manager -- calls :meth:`connect`."""
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        """Exit async context manager -- calls :meth:`disconnect`."""
        await self.disconnect()

    # -- Properties ----------------------------------------------------

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name of this data connector (e.g. ``'polygon'``)."""

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Whether the connector has an active connection."""

    # -- Historical data -----------------------------------------------

    @abstractmethod
    async def get_historical_bars(
        self,
        instrument: Instrument,
        start: datetime,
        end: datetime,
        timeframe: BarTimeframe = BarTimeframe.DAY_1,
    ) -> pd.DataFrame:
        """Fetch historical OHLCV bars as a normalized DataFrame.

        Returns a DataFrame conforming to
        :data:`sysls.data.normalize.BAR_DTYPES` with a ``DatetimeIndex``.

        Args:
            instrument: The instrument to fetch bars for.
            start: Start of the date range (inclusive).
            end: End of the date range (inclusive).
            timeframe: Bar period.  Defaults to daily.

        Returns:
            A pandas DataFrame with columns: ``open``, ``high``, ``low``,
            ``close``, ``volume``, ``vwap``, ``trade_count``.

        Raises:
            DataError: If the fetch fails.
            DataNotFoundError: If no data exists for the given parameters.
        """

    @abstractmethod
    async def get_historical_trades(
        self,
        instrument: Instrument,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        """Fetch historical trades as a normalized DataFrame.

        Returns a DataFrame conforming to
        :data:`sysls.data.normalize.TRADE_DTYPES` with a ``DatetimeIndex``.

        Args:
            instrument: The instrument to fetch trades for.
            start: Start of the date range (inclusive).
            end: End of the date range (inclusive).

        Returns:
            A pandas DataFrame with columns: ``price``, ``size``, ``side``.

        Raises:
            DataError: If the fetch fails.
            DataNotFoundError: If no data exists for the given parameters.
        """

    @abstractmethod
    async def get_historical_quotes(
        self,
        instrument: Instrument,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        """Fetch historical bid/ask quotes as a normalized DataFrame.

        Returns a DataFrame conforming to
        :data:`sysls.data.normalize.QUOTE_DTYPES` with a ``DatetimeIndex``.

        Args:
            instrument: The instrument to fetch quotes for.
            start: Start of the date range (inclusive).
            end: End of the date range (inclusive).

        Returns:
            A pandas DataFrame with columns: ``bid_price``, ``bid_size``,
            ``ask_price``, ``ask_size``.

        Raises:
            DataError: If the fetch fails.
            DataNotFoundError: If no data exists for the given parameters.
        """

    # -- Streaming data ------------------------------------------------

    @abstractmethod
    def stream_quotes(
        self,
        instruments: list[Instrument],
    ) -> AsyncIterator[QuoteEvent]:
        """Stream live bid/ask quote updates.

        Yields :class:`~sysls.core.events.QuoteEvent` instances as they
        arrive from the data source.  The iterator runs until cancelled
        or the connection drops.

        Args:
            instruments: Instruments to subscribe to.

        Yields:
            Normalized quote events.

        Raises:
            DataError: If subscription fails.
        """
        ...  # pragma: no cover

    @abstractmethod
    def stream_trades(
        self,
        instruments: list[Instrument],
    ) -> AsyncIterator[TradeEvent]:
        """Stream live trade/tick updates.

        Args:
            instruments: Instruments to subscribe to.

        Yields:
            Normalized trade events.

        Raises:
            DataError: If subscription fails.
        """
        ...  # pragma: no cover

    @abstractmethod
    def stream_bars(
        self,
        instruments: list[Instrument],
        timeframe: BarTimeframe = BarTimeframe.MINUTE_1,
    ) -> AsyncIterator[BarEvent]:
        """Stream live bar updates.

        Args:
            instruments: Instruments to subscribe to.
            timeframe: Bar period for aggregation.

        Yields:
            Normalized bar events.

        Raises:
            DataError: If subscription fails.
        """
        ...  # pragma: no cover
