"""Polygon.io data connector implementation.

Wraps the ``polygon-api-client`` SDK to provide historical and streaming
market data through the :class:`~sysls.data.connector.DataConnector` ABC.

Polygon's REST client is synchronous -- all REST calls are executed via
:func:`asyncio.to_thread` to avoid blocking the event loop. Streaming
uses the Polygon WebSocketClient with an asyncio.Queue bridge.

Example::

    async with PolygonConnector(api_key="your_key") as conn:
        bars = await conn.get_historical_bars(
            instrument=inst,
            start=datetime(2024, 1, 1),
            end=datetime(2024, 12, 31),
            timeframe=BarTimeframe.DAY_1,
        )
"""

from __future__ import annotations

import asyncio
import contextlib
from decimal import Decimal
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import structlog

from sysls.core.exceptions import DataError, DataNotFoundError
from sysls.core.types import AssetClass, Side
from sysls.data.connector import BarTimeframe, DataConnector

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from datetime import datetime

    from polygon import RESTClient

    from sysls.core.events import BarEvent, QuoteEvent, TradeEvent
    from sysls.core.types import Instrument

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# BarTimeframe -> Polygon (multiplier, timespan) mapping
# ---------------------------------------------------------------------------

_TIMEFRAME_MAP: dict[BarTimeframe, tuple[int, str]] = {
    BarTimeframe.SECOND_1: (1, "second"),
    BarTimeframe.MINUTE_1: (1, "minute"),
    BarTimeframe.MINUTE_5: (5, "minute"),
    BarTimeframe.MINUTE_15: (15, "minute"),
    BarTimeframe.MINUTE_30: (30, "minute"),
    BarTimeframe.HOUR_1: (1, "hour"),
    BarTimeframe.HOUR_4: (4, "hour"),
    BarTimeframe.DAY_1: (1, "day"),
    BarTimeframe.WEEK_1: (1, "week"),
    BarTimeframe.MONTH_1: (1, "month"),
}


def _instrument_to_ticker(instrument: Instrument) -> str:
    """Map a sysls Instrument to a Polygon ticker string.

    Args:
        instrument: The instrument to convert.

    Returns:
        The Polygon-formatted ticker (e.g. ``"AAPL"`` for equities,
        ``"X:BTCUSD"`` for crypto).

    Raises:
        DataError: If the asset class is not supported by Polygon.
    """
    if instrument.asset_class in (
        AssetClass.CRYPTO_SPOT,
        AssetClass.CRYPTO_PERP,
        AssetClass.CRYPTO_FUTURE,
    ):
        # Polygon crypto tickers: "X:BTCUSD"
        # Strip common separators from the symbol to get base+quote
        symbol = instrument.symbol.replace("-", "").replace("/", "").replace("_", "")
        return f"X:{symbol}"
    if instrument.asset_class in (AssetClass.EQUITY, AssetClass.OPTION, AssetClass.FUTURE):
        return instrument.symbol
    raise DataError(f"Unsupported asset class for Polygon: {instrument.asset_class}")


class PolygonConnector(DataConnector):
    """Polygon.io data connector using the polygon-api-client SDK.

    Provides historical OHLCV bars, trades, and quotes via Polygon's REST
    API, as well as real-time streaming via WebSocket. All REST calls are
    executed in a thread pool to avoid blocking the async event loop.

    Args:
        api_key: Polygon.io API key for authentication.
    """

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._rest_client: RESTClient | None = None
        self._connected: bool = False

    # -- Properties --------------------------------------------------------

    @property
    def name(self) -> str:
        """Human-readable name of this data connector."""
        return "polygon"

    @property
    def is_connected(self) -> bool:
        """Whether the connector has an active connection."""
        return self._connected

    # -- Lifecycle ---------------------------------------------------------

    async def connect(self) -> None:
        """Establish connection by creating the Polygon REST client.

        Raises:
            DataError: If the client cannot be created.
        """
        if self._connected:
            logger.warning("polygon_connector_already_connected")
            return

        try:
            from polygon import RESTClient as _RESTClient

            self._rest_client = _RESTClient(api_key=self._api_key)
            self._connected = True
            logger.info("polygon_connector_connected")
        except Exception as exc:
            raise DataError(f"Failed to create Polygon REST client: {exc}") from exc

    async def disconnect(self) -> None:
        """Release the REST client and mark as disconnected.

        Safe to call multiple times.
        """
        self._rest_client = None
        self._connected = False
        logger.info("polygon_connector_disconnected")

    # -- Historical data ---------------------------------------------------

    async def get_historical_bars(
        self,
        instrument: Instrument,
        start: datetime,
        end: datetime,
        timeframe: BarTimeframe = BarTimeframe.DAY_1,
    ) -> pd.DataFrame:
        """Fetch historical OHLCV bars as a normalized DataFrame.

        Args:
            instrument: The instrument to fetch bars for.
            start: Start of the date range (inclusive).
            end: End of the date range (inclusive).
            timeframe: Bar period. Defaults to daily.

        Returns:
            A pandas DataFrame conforming to the canonical bar schema with
            a ``DatetimeIndex`` named ``"timestamp"``.

        Raises:
            DataError: If the REST call fails or the connector is not connected.
            DataNotFoundError: If no data exists for the given parameters.
        """
        self._ensure_connected()
        assert self._rest_client is not None  # for type checker

        ticker = _instrument_to_ticker(instrument)
        multiplier, timespan = _TIMEFRAME_MAP[timeframe]
        start_str = start.strftime("%Y-%m-%d")
        end_str = end.strftime("%Y-%m-%d")

        logger.info(
            "polygon_fetch_bars",
            ticker=ticker,
            timeframe=str(timeframe),
            start=start_str,
            end=end_str,
        )

        try:
            aggs = await asyncio.to_thread(
                self._list_aggs,
                ticker,
                multiplier,
                timespan,
                start_str,
                end_str,
            )
        except DataError:
            raise
        except Exception as exc:
            raise DataError(f"Polygon bars request failed: {exc}") from exc

        if not aggs:
            raise DataNotFoundError(
                f"No bar data found for {ticker} from {start_str} to {end_str}"
            )

        records = []
        for agg in aggs:
            records.append(
                {
                    "timestamp": pd.Timestamp(agg.timestamp, unit="ms", tz="UTC"),
                    "open": float(agg.open) if agg.open is not None else np.nan,
                    "high": float(agg.high) if agg.high is not None else np.nan,
                    "low": float(agg.low) if agg.low is not None else np.nan,
                    "close": float(agg.close) if agg.close is not None else np.nan,
                    "volume": float(agg.volume) if agg.volume is not None else 0.0,
                    "vwap": float(agg.vwap) if agg.vwap is not None else np.nan,
                    "trade_count": int(agg.transactions) if agg.transactions is not None else 0,
                }
            )

        df = pd.DataFrame(records)
        df = df.set_index("timestamp")
        df.index.name = "timestamp"
        df = df.astype(
            {
                "open": np.float64,
                "high": np.float64,
                "low": np.float64,
                "close": np.float64,
                "volume": np.float64,
                "vwap": np.float64,
                "trade_count": np.int64,
            }
        )
        return df

    async def get_historical_trades(
        self,
        instrument: Instrument,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        """Fetch historical trades as a normalized DataFrame.

        Args:
            instrument: The instrument to fetch trades for.
            start: Start of the date range (inclusive).
            end: End of the date range (inclusive).

        Returns:
            A pandas DataFrame conforming to the canonical trade schema with
            a ``DatetimeIndex`` named ``"timestamp"``.

        Raises:
            DataError: If the REST call fails or the connector is not connected.
            DataNotFoundError: If no data exists for the given parameters.
        """
        self._ensure_connected()
        assert self._rest_client is not None

        ticker = _instrument_to_ticker(instrument)
        # Polygon expects nanosecond timestamps for trade/quote filtering
        start_ns = int(start.timestamp() * 1_000_000_000)
        end_ns = int(end.timestamp() * 1_000_000_000)

        logger.info(
            "polygon_fetch_trades",
            ticker=ticker,
            start=start.isoformat(),
            end=end.isoformat(),
        )

        try:
            trades = await asyncio.to_thread(
                self._list_trades,
                ticker,
                start_ns,
                end_ns,
            )
        except DataError:
            raise
        except Exception as exc:
            raise DataError(f"Polygon trades request failed: {exc}") from exc

        if not trades:
            raise DataNotFoundError(
                f"No trade data found for {ticker} from {start.isoformat()} to {end.isoformat()}"
            )

        records = []
        for trade in trades:
            # Use sip_timestamp (nanoseconds) as the canonical timestamp
            ts_ns = trade.sip_timestamp or trade.participant_timestamp or 0
            side = _infer_trade_side(trade.conditions)

            records.append(
                {
                    "timestamp": pd.Timestamp(ts_ns, unit="ns", tz="UTC"),
                    "price": float(trade.price) if trade.price is not None else np.nan,
                    "size": float(trade.size) if trade.size is not None else 0.0,
                    "side": side,
                }
            )

        df = pd.DataFrame(records)
        df = df.set_index("timestamp")
        df.index.name = "timestamp"
        df = df.astype(
            {
                "price": np.float64,
                "size": np.float64,
            }
        )
        return df

    async def get_historical_quotes(
        self,
        instrument: Instrument,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        """Fetch historical bid/ask quotes as a normalized DataFrame.

        Args:
            instrument: The instrument to fetch quotes for.
            start: Start of the date range (inclusive).
            end: End of the date range (inclusive).

        Returns:
            A pandas DataFrame conforming to the canonical quote schema with
            a ``DatetimeIndex`` named ``"timestamp"``.

        Raises:
            DataError: If the REST call fails or the connector is not connected.
            DataNotFoundError: If no data exists for the given parameters.
        """
        self._ensure_connected()
        assert self._rest_client is not None

        ticker = _instrument_to_ticker(instrument)
        start_ns = int(start.timestamp() * 1_000_000_000)
        end_ns = int(end.timestamp() * 1_000_000_000)

        logger.info(
            "polygon_fetch_quotes",
            ticker=ticker,
            start=start.isoformat(),
            end=end.isoformat(),
        )

        try:
            quotes = await asyncio.to_thread(
                self._list_quotes,
                ticker,
                start_ns,
                end_ns,
            )
        except DataError:
            raise
        except Exception as exc:
            raise DataError(f"Polygon quotes request failed: {exc}") from exc

        if not quotes:
            raise DataNotFoundError(
                f"No quote data found for {ticker} from {start.isoformat()} to {end.isoformat()}"
            )

        records = []
        for quote in quotes:
            ts_ns = quote.sip_timestamp or quote.participant_timestamp or 0
            records.append(
                {
                    "timestamp": pd.Timestamp(ts_ns, unit="ns", tz="UTC"),
                    "bid_price": float(quote.bid_price) if quote.bid_price is not None else np.nan,
                    "bid_size": float(quote.bid_size) if quote.bid_size is not None else 0.0,
                    "ask_price": float(quote.ask_price) if quote.ask_price is not None else np.nan,
                    "ask_size": float(quote.ask_size) if quote.ask_size is not None else 0.0,
                }
            )

        df = pd.DataFrame(records)
        df = df.set_index("timestamp")
        df.index.name = "timestamp"
        df = df.astype(
            {
                "bid_price": np.float64,
                "bid_size": np.float64,
                "ask_price": np.float64,
                "ask_size": np.float64,
            }
        )
        return df

    # -- Streaming data ----------------------------------------------------

    async def stream_quotes(
        self,
        instruments: list[Instrument],
    ) -> AsyncIterator[QuoteEvent]:
        """Stream live bid/ask quote updates via Polygon WebSocket.

        Subscribes to ``Q.{ticker}`` channels for equities and
        ``XQ.{ticker}`` for crypto, yielding normalized
        :class:`~sysls.core.events.QuoteEvent` instances.

        Args:
            instruments: Instruments to subscribe to.

        Yields:
            Normalized quote events as they arrive.

        Raises:
            DataError: If subscription fails or connector is not connected.
        """
        from sysls.core.events import QuoteEvent

        self._ensure_connected()
        queue: asyncio.Queue[QuoteEvent | None] = asyncio.Queue()

        subscriptions = []
        instrument_map: dict[str, Instrument] = {}
        for inst in instruments:
            ticker = _instrument_to_ticker(inst)
            is_crypto = ticker.startswith("X:")
            prefix = "XQ" if is_crypto else "Q"
            sub = f"{prefix}.{ticker}"
            subscriptions.append(sub)
            instrument_map[ticker] = inst

        async def _process_messages(msgs: list[object]) -> None:
            for msg in msgs:
                try:
                    instrument = _resolve_instrument_from_ws(msg, instrument_map)
                    if instrument is None:
                        continue
                    ts_ns = _extract_ws_timestamp_ns(msg)
                    event = QuoteEvent(
                        instrument=instrument,
                        bid_price=Decimal(str(getattr(msg, "bid_price", 0) or 0)),
                        bid_size=Decimal(str(getattr(msg, "bid_size", 0) or 0)),
                        ask_price=Decimal(str(getattr(msg, "ask_price", 0) or 0)),
                        ask_size=Decimal(str(getattr(msg, "ask_size", 0) or 0)),
                        timestamp_ns=ts_ns,
                        source="polygon",
                    )
                    await queue.put(event)
                except Exception:
                    logger.exception("polygon_ws_quote_parse_error")

        ws_task = asyncio.create_task(
            self._run_websocket(instruments, subscriptions, _process_messages)
        )

        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
        finally:
            ws_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ws_task

    async def stream_trades(
        self,
        instruments: list[Instrument],
    ) -> AsyncIterator[TradeEvent]:
        """Stream live trade/tick updates via Polygon WebSocket.

        Subscribes to ``T.{ticker}`` channels for equities and
        ``XT.{ticker}`` for crypto.

        Args:
            instruments: Instruments to subscribe to.

        Yields:
            Normalized trade events as they arrive.

        Raises:
            DataError: If subscription fails or connector is not connected.
        """
        from sysls.core.events import TradeEvent

        self._ensure_connected()
        queue: asyncio.Queue[TradeEvent | None] = asyncio.Queue()

        subscriptions = []
        instrument_map: dict[str, Instrument] = {}
        for inst in instruments:
            ticker = _instrument_to_ticker(inst)
            is_crypto = ticker.startswith("X:")
            prefix = "XT" if is_crypto else "T"
            sub = f"{prefix}.{ticker}"
            subscriptions.append(sub)
            instrument_map[ticker] = inst

        async def _process_messages(msgs: list[object]) -> None:
            for msg in msgs:
                try:
                    instrument = _resolve_instrument_from_ws(msg, instrument_map)
                    if instrument is None:
                        continue
                    ts_ns = _extract_ws_timestamp_ns(msg)
                    conditions = getattr(msg, "conditions", None)
                    side_str = _infer_trade_side(conditions)
                    side: Side | None = None
                    if side_str == "BUY":
                        side = Side.BUY
                    elif side_str == "SELL":
                        side = Side.SELL

                    event = TradeEvent(
                        instrument=instrument,
                        price=Decimal(str(getattr(msg, "price", 0) or 0)),
                        size=Decimal(str(getattr(msg, "size", 0) or 0)),
                        side=side,
                        timestamp_ns=ts_ns,
                        source="polygon",
                    )
                    await queue.put(event)
                except Exception:
                    logger.exception("polygon_ws_trade_parse_error")

        ws_task = asyncio.create_task(
            self._run_websocket(instruments, subscriptions, _process_messages)
        )

        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
        finally:
            ws_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ws_task

    async def stream_bars(
        self,
        instruments: list[Instrument],
        timeframe: BarTimeframe = BarTimeframe.MINUTE_1,
    ) -> AsyncIterator[BarEvent]:
        """Stream live bar updates via Polygon WebSocket.

        Subscribes to ``AM.{ticker}`` (minute aggregate) or ``A.{ticker}``
        (second aggregate) channels for equities, and ``XA.{ticker}`` /
        ``XAS.{ticker}`` for crypto.

        Args:
            instruments: Instruments to subscribe to.
            timeframe: Bar period for aggregation (only second and minute
                granularities are supported by Polygon streaming).

        Yields:
            Normalized bar events as they arrive.

        Raises:
            DataError: If subscription fails or connector is not connected.
        """
        from sysls.core.events import BarEvent

        self._ensure_connected()
        queue: asyncio.Queue[BarEvent | None] = asyncio.Queue()

        subscriptions = []
        instrument_map: dict[str, Instrument] = {}
        for inst in instruments:
            ticker = _instrument_to_ticker(inst)
            is_crypto = ticker.startswith("X:")
            if is_crypto:
                prefix = "XAS" if timeframe == BarTimeframe.SECOND_1 else "XA"
            else:
                prefix = "A" if timeframe == BarTimeframe.SECOND_1 else "AM"
            sub = f"{prefix}.{ticker}"
            subscriptions.append(sub)
            instrument_map[ticker] = inst

        async def _process_messages(msgs: list[object]) -> None:
            for msg in msgs:
                try:
                    instrument = _resolve_instrument_from_ws(msg, instrument_map)
                    if instrument is None:
                        continue
                    start_ns = (getattr(msg, "start_timestamp", 0) or 0) * 1_000_000
                    end_ns = (getattr(msg, "end_timestamp", 0) or 0) * 1_000_000
                    event = BarEvent(
                        instrument=instrument,
                        open=Decimal(str(getattr(msg, "open", 0) or 0)),
                        high=Decimal(str(getattr(msg, "high", 0) or 0)),
                        low=Decimal(str(getattr(msg, "low", 0) or 0)),
                        close=Decimal(str(getattr(msg, "close", 0) or 0)),
                        volume=Decimal(str(getattr(msg, "volume", 0) or 0)),
                        bar_start_ns=start_ns,
                        bar_end_ns=end_ns,
                        timestamp_ns=end_ns,
                        source="polygon",
                    )
                    await queue.put(event)
                except Exception:
                    logger.exception("polygon_ws_bar_parse_error")

        ws_task = asyncio.create_task(
            self._run_websocket(instruments, subscriptions, _process_messages)
        )

        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
        finally:
            ws_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ws_task

    # -- Private helpers ---------------------------------------------------

    def _ensure_connected(self) -> None:
        """Raise DataError if the connector is not connected."""
        if not self._connected or self._rest_client is None:
            raise DataError("PolygonConnector is not connected. Call connect() first.")

    def _list_aggs(
        self,
        ticker: str,
        multiplier: int,
        timespan: str,
        start: str,
        end: str,
    ) -> list[object]:
        """Synchronous wrapper around RESTClient.list_aggs.

        Intended to be called via ``asyncio.to_thread``.
        """
        assert self._rest_client is not None
        return list(
            self._rest_client.list_aggs(
                ticker=ticker,
                multiplier=multiplier,
                timespan=timespan,
                from_=start,
                to=end,
                limit=50000,
            )
        )

    def _list_trades(
        self,
        ticker: str,
        start_ns: int,
        end_ns: int,
    ) -> list[object]:
        """Synchronous wrapper around RESTClient.list_trades.

        Intended to be called via ``asyncio.to_thread``.
        """
        assert self._rest_client is not None
        return list(
            self._rest_client.list_trades(
                ticker=ticker,
                timestamp_gte=start_ns,
                timestamp_lte=end_ns,
                limit=50000,
                order="asc",
            )
        )

    def _list_quotes(
        self,
        ticker: str,
        start_ns: int,
        end_ns: int,
    ) -> list[object]:
        """Synchronous wrapper around RESTClient.list_quotes.

        Intended to be called via ``asyncio.to_thread``.
        """
        assert self._rest_client is not None
        return list(
            self._rest_client.list_quotes(
                ticker=ticker,
                timestamp_gte=start_ns,
                timestamp_lte=end_ns,
                limit=50000,
                order="asc",
            )
        )

    async def _run_websocket(
        self,
        instruments: list[Instrument],
        subscriptions: list[str],
        processor: object,
    ) -> None:
        """Run a Polygon WebSocket connection in a background thread.

        Creates a WebSocketClient, subscribes to the given channels, and
        runs the connection loop. The ``processor`` is an async callback
        that receives batches of messages.

        Args:
            instruments: The instruments being streamed.
            subscriptions: Polygon subscription strings (e.g. ``["Q.AAPL"]``).
            processor: Async callable that processes message batches.
        """
        from polygon import WebSocketClient
        from polygon.websocket.models.common import Market

        # Determine market from the first instrument
        first_ticker = _instrument_to_ticker(instruments[0])
        market = Market.Crypto if first_ticker.startswith("X:") else Market.Stocks

        ws_client = WebSocketClient(
            api_key=self._api_key,
            market=market,
            subscriptions=subscriptions,
        )

        try:
            await asyncio.to_thread(ws_client.run, processor)
        except asyncio.CancelledError:
            ws_client.close()
            raise
        except Exception:
            logger.exception("polygon_websocket_error")
            ws_client.close()


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

# Polygon trade condition codes that indicate the buy/sell aggressor side.
# See: https://polygon.io/glossary/us/stocks/conditions-indicators
_BUY_CONDITIONS: frozenset[int] = frozenset()
_SELL_CONDITIONS: frozenset[int] = frozenset()


def _infer_trade_side(conditions: list[int] | None) -> str:
    """Infer trade aggressor side from Polygon trade condition codes.

    Polygon does not directly provide a buy/sell side field. Some condition
    codes can hint at the aggressor, but reliable inference is limited.
    Returns ``"BUY"``, ``"SELL"``, or ``""`` (unknown).

    Args:
        conditions: List of Polygon condition code integers.

    Returns:
        One of ``"BUY"``, ``"SELL"``, or ``""`` (unknown).
    """
    if not conditions:
        return ""
    for code in conditions:
        if code in _BUY_CONDITIONS:
            return "BUY"
        if code in _SELL_CONDITIONS:
            return "SELL"
    return ""


def _resolve_instrument_from_ws(
    msg: object,
    instrument_map: dict[str, Instrument],
) -> Instrument | None:
    """Extract the instrument from a WebSocket message.

    Polygon WS messages have either a ``symbol`` field (equities) or
    ``pair`` field (crypto). We look these up in the instrument map.

    Args:
        msg: A polygon WebSocket model instance.
        instrument_map: Mapping from Polygon ticker to Instrument.

    Returns:
        The matched Instrument, or None if not found.
    """
    symbol = getattr(msg, "symbol", None) or getattr(msg, "pair", None)
    if symbol is None:
        return None
    # For equities, the symbol comes directly (e.g. "AAPL")
    if symbol in instrument_map:
        return instrument_map[symbol]
    # For crypto, try with "X:" prefix
    crypto_key = f"X:{symbol}"
    if crypto_key in instrument_map:
        return instrument_map[crypto_key]
    return None


def _extract_ws_timestamp_ns(msg: object) -> int:
    """Extract a nanosecond timestamp from a WebSocket message.

    Args:
        msg: A polygon WebSocket model instance.

    Returns:
        Timestamp in nanoseconds since epoch. Falls back to 0 if not available.
    """
    # WebSocket timestamps are in milliseconds
    ts = getattr(msg, "timestamp", None)
    if ts is not None:
        return int(ts) * 1_000_000
    return 0
