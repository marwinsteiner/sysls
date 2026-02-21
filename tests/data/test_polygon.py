"""Tests for the Polygon.io data connector."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from sysls.core.events import BarEvent, QuoteEvent, TradeEvent
from sysls.core.exceptions import DataError, DataNotFoundError
from sysls.core.types import AssetClass, Instrument, Venue
from sysls.data.connector import BarTimeframe
from sysls.data.polygon import (
    _TIMEFRAME_MAP,
    PolygonConnector,
    _extract_ws_timestamp_ns,
    _infer_trade_side,
    _instrument_to_ticker,
    _resolve_instrument_from_ws,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def api_key() -> str:
    """Dummy Polygon API key for testing."""
    return "test_api_key_12345"


@pytest.fixture
def connector(api_key: str) -> PolygonConnector:
    """Create a PolygonConnector instance for testing."""
    return PolygonConnector(api_key=api_key)


@pytest.fixture
def equity_instrument() -> Instrument:
    """AAPL equity instrument for testing."""
    return Instrument(
        symbol="AAPL",
        asset_class=AssetClass.EQUITY,
        venue=Venue.IBKR,
        currency="USD",
    )


@pytest.fixture
def crypto_instrument() -> Instrument:
    """BTC-USD crypto instrument for testing."""
    return Instrument(
        symbol="BTC-USD",
        asset_class=AssetClass.CRYPTO_SPOT,
        venue=Venue.CCXT,
        currency="USD",
    )


@pytest.fixture
def mock_aggs() -> list[MagicMock]:
    """Create mock Polygon Agg objects."""
    agg1 = MagicMock()
    agg1.timestamp = 1704067200000  # 2024-01-01 00:00:00 UTC in ms
    agg1.open = 150.0
    agg1.high = 155.0
    agg1.low = 149.0
    agg1.close = 154.0
    agg1.volume = 1000000.0
    agg1.vwap = 152.5
    agg1.transactions = 50000

    agg2 = MagicMock()
    agg2.timestamp = 1704153600000  # 2024-01-02 00:00:00 UTC in ms
    agg2.open = 154.0
    agg2.high = 158.0
    agg2.low = 153.0
    agg2.close = 157.0
    agg2.volume = 1200000.0
    agg2.vwap = 155.5
    agg2.transactions = 60000

    return [agg1, agg2]


@pytest.fixture
def mock_trades() -> list[MagicMock]:
    """Create mock Polygon Trade objects."""
    trade1 = MagicMock()
    trade1.sip_timestamp = 1704067200000000000  # nanoseconds
    trade1.participant_timestamp = 1704067200000000000
    trade1.price = 150.25
    trade1.size = 100.0
    trade1.conditions = None

    trade2 = MagicMock()
    trade2.sip_timestamp = 1704067201000000000
    trade2.participant_timestamp = 1704067201000000000
    trade2.price = 150.50
    trade2.size = 200.0
    trade2.conditions = [1]

    return [trade1, trade2]


@pytest.fixture
def mock_quotes() -> list[MagicMock]:
    """Create mock Polygon Quote objects."""
    quote1 = MagicMock()
    quote1.sip_timestamp = 1704067200000000000
    quote1.participant_timestamp = 1704067200000000000
    quote1.bid_price = 150.00
    quote1.bid_size = 500.0
    quote1.ask_price = 150.05
    quote1.ask_size = 300.0

    quote2 = MagicMock()
    quote2.sip_timestamp = 1704067201000000000
    quote2.participant_timestamp = 1704067201000000000
    quote2.bid_price = 150.10
    quote2.bid_size = 400.0
    quote2.ask_price = 150.15
    quote2.ask_size = 350.0

    return [quote1, quote2]


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


class TestPolygonConnectorLifecycle:
    """Tests for connect, disconnect, and context manager."""

    def test_name_property(self, connector: PolygonConnector) -> None:
        """Connector name should be 'polygon'."""
        assert connector.name == "polygon"

    def test_is_connected_initially_false(self, connector: PolygonConnector) -> None:
        """New connector should not be connected."""
        assert connector.is_connected is False

    @pytest.mark.asyncio
    async def test_connect(self, connector: PolygonConnector) -> None:
        """Connect should create REST client and set connected flag."""
        with patch("polygon.RESTClient") as mock_rest:
            mock_rest.return_value = MagicMock()
            await connector.connect()
            assert connector.is_connected is True

    @pytest.mark.asyncio
    async def test_connect_idempotent(self, connector: PolygonConnector) -> None:
        """Calling connect() when already connected should be a no-op."""
        with patch("polygon.RESTClient"):
            await connector.connect()
            await connector.connect()  # Should not raise
            assert connector.is_connected is True

    @pytest.mark.asyncio
    async def test_disconnect(self, connector: PolygonConnector) -> None:
        """Disconnect should clear the REST client and connected flag."""
        with patch("polygon.RESTClient"):
            await connector.connect()
            assert connector.is_connected is True
            await connector.disconnect()
            assert connector.is_connected is False

    @pytest.mark.asyncio
    async def test_disconnect_idempotent(self, connector: PolygonConnector) -> None:
        """Calling disconnect() multiple times should not raise."""
        await connector.disconnect()
        await connector.disconnect()
        assert connector.is_connected is False

    @pytest.mark.asyncio
    async def test_context_manager(self, api_key: str) -> None:
        """Async context manager should connect and disconnect."""
        with patch("polygon.RESTClient"):
            async with PolygonConnector(api_key=api_key) as conn:
                assert conn.is_connected is True
                assert conn.name == "polygon"
            assert conn.is_connected is False

    @pytest.mark.asyncio
    async def test_connect_failure_raises_data_error(self, api_key: str) -> None:
        """If RESTClient creation fails, DataError should be raised."""
        connector = PolygonConnector(api_key=api_key)
        with (
            patch(
                "polygon.RESTClient",
                side_effect=RuntimeError("auth failed"),
            ),
            pytest.raises(DataError, match="Failed to create Polygon REST client"),
        ):
            await connector.connect()


# ---------------------------------------------------------------------------
# Instrument to ticker mapping tests
# ---------------------------------------------------------------------------


class TestInstrumentToTicker:
    """Tests for _instrument_to_ticker mapping."""

    def test_equity_ticker(self, equity_instrument: Instrument) -> None:
        """Equity instruments should map to plain symbol."""
        assert _instrument_to_ticker(equity_instrument) == "AAPL"

    def test_crypto_ticker(self, crypto_instrument: Instrument) -> None:
        """Crypto instruments should map to X:SYMBOL format."""
        assert _instrument_to_ticker(crypto_instrument) == "X:BTCUSD"

    def test_crypto_with_slashes(self) -> None:
        """Crypto symbols with slashes should have separators stripped."""
        inst = Instrument(
            symbol="ETH/USD",
            asset_class=AssetClass.CRYPTO_SPOT,
            venue=Venue.CCXT,
        )
        assert _instrument_to_ticker(inst) == "X:ETHUSD"

    def test_crypto_with_underscores(self) -> None:
        """Crypto symbols with underscores should have separators stripped."""
        inst = Instrument(
            symbol="SOL_USDT",
            asset_class=AssetClass.CRYPTO_PERP,
            venue=Venue.CCXT,
        )
        assert _instrument_to_ticker(inst) == "X:SOLUSDT"

    def test_option_ticker(self) -> None:
        """Option instruments should map to plain symbol."""
        inst = Instrument(
            symbol="O:AAPL240119C00190000",
            asset_class=AssetClass.OPTION,
            venue=Venue.IBKR,
        )
        assert _instrument_to_ticker(inst) == "O:AAPL240119C00190000"

    def test_future_ticker(self) -> None:
        """Future instruments should map to plain symbol."""
        inst = Instrument(
            symbol="ESH24",
            asset_class=AssetClass.FUTURE,
            venue=Venue.IBKR,
        )
        assert _instrument_to_ticker(inst) == "ESH24"

    def test_unsupported_asset_class_raises(self) -> None:
        """Event contracts should raise DataError."""
        inst = Instrument(
            symbol="WILL_BTC_HIT_100K",
            asset_class=AssetClass.EVENT,
            venue=Venue.POLYMARKET,
        )
        with pytest.raises(DataError, match="Unsupported asset class"):
            _instrument_to_ticker(inst)


# ---------------------------------------------------------------------------
# Timeframe mapping tests
# ---------------------------------------------------------------------------


class TestTimeframeMapping:
    """Tests for BarTimeframe -> Polygon mapping."""

    def test_all_timeframes_mapped(self) -> None:
        """Every BarTimeframe value should have a mapping."""
        for tf in BarTimeframe:
            assert tf in _TIMEFRAME_MAP, f"Missing mapping for {tf}"

    def test_day_1_mapping(self) -> None:
        """DAY_1 should map to (1, 'day')."""
        assert _TIMEFRAME_MAP[BarTimeframe.DAY_1] == (1, "day")

    def test_minute_5_mapping(self) -> None:
        """MINUTE_5 should map to (5, 'minute')."""
        assert _TIMEFRAME_MAP[BarTimeframe.MINUTE_5] == (5, "minute")

    def test_hour_4_mapping(self) -> None:
        """HOUR_4 should map to (4, 'hour')."""
        assert _TIMEFRAME_MAP[BarTimeframe.HOUR_4] == (4, "hour")

    def test_second_1_mapping(self) -> None:
        """SECOND_1 should map to (1, 'second')."""
        assert _TIMEFRAME_MAP[BarTimeframe.SECOND_1] == (1, "second")


# ---------------------------------------------------------------------------
# Historical bars tests
# ---------------------------------------------------------------------------


class TestGetHistoricalBars:
    """Tests for get_historical_bars method."""

    @pytest.mark.asyncio
    async def test_not_connected_raises(
        self,
        connector: PolygonConnector,
        equity_instrument: Instrument,
    ) -> None:
        """Calling without connecting should raise DataError."""
        with pytest.raises(DataError, match="not connected"):
            await connector.get_historical_bars(
                instrument=equity_instrument,
                start=datetime(2024, 1, 1, tzinfo=UTC),
                end=datetime(2024, 1, 31, tzinfo=UTC),
            )

    @pytest.mark.asyncio
    async def test_returns_normalized_dataframe(
        self,
        connector: PolygonConnector,
        equity_instrument: Instrument,
        mock_aggs: list[MagicMock],
    ) -> None:
        """Should return DataFrame with correct schema and dtypes."""
        connector._connected = True
        connector._rest_client = MagicMock()

        with patch.object(connector, "_list_aggs", return_value=mock_aggs):
            df = await connector.get_historical_bars(
                instrument=equity_instrument,
                start=datetime(2024, 1, 1, tzinfo=UTC),
                end=datetime(2024, 1, 31, tzinfo=UTC),
                timeframe=BarTimeframe.DAY_1,
            )

        # Check index
        assert isinstance(df.index, pd.DatetimeIndex)
        assert df.index.name == "timestamp"
        assert len(df) == 2

        # Check columns
        expected_columns = {"open", "high", "low", "close", "volume", "vwap", "trade_count"}
        assert set(df.columns) == expected_columns

        # Check dtypes
        assert df["open"].dtype == np.float64
        assert df["high"].dtype == np.float64
        assert df["low"].dtype == np.float64
        assert df["close"].dtype == np.float64
        assert df["volume"].dtype == np.float64
        assert df["vwap"].dtype == np.float64
        assert df["trade_count"].dtype == np.int64

        # Check values
        assert df.iloc[0]["open"] == 150.0
        assert df.iloc[0]["close"] == 154.0
        assert df.iloc[0]["trade_count"] == 50000

    @pytest.mark.asyncio
    async def test_empty_results_raises_not_found(
        self,
        connector: PolygonConnector,
        equity_instrument: Instrument,
    ) -> None:
        """Empty aggs should raise DataNotFoundError."""
        connector._connected = True
        connector._rest_client = MagicMock()

        with (
            patch.object(connector, "_list_aggs", return_value=[]),
            pytest.raises(DataNotFoundError, match="No bar data found"),
        ):
            await connector.get_historical_bars(
                instrument=equity_instrument,
                start=datetime(2024, 1, 1, tzinfo=UTC),
                end=datetime(2024, 1, 31, tzinfo=UTC),
            )

    @pytest.mark.asyncio
    async def test_api_error_raises_data_error(
        self,
        connector: PolygonConnector,
        equity_instrument: Instrument,
    ) -> None:
        """REST client errors should be wrapped in DataError."""
        connector._connected = True
        connector._rest_client = MagicMock()

        with (
            patch.object(
                connector,
                "_list_aggs",
                side_effect=RuntimeError("API rate limit"),
            ),
            pytest.raises(DataError, match="Polygon bars request failed"),
        ):
            await connector.get_historical_bars(
                instrument=equity_instrument,
                start=datetime(2024, 1, 1, tzinfo=UTC),
                end=datetime(2024, 1, 31, tzinfo=UTC),
            )

    @pytest.mark.asyncio
    async def test_handles_none_values_in_aggs(
        self,
        connector: PolygonConnector,
        equity_instrument: Instrument,
    ) -> None:
        """Agg fields with None should be replaced with NaN or 0."""
        agg = MagicMock()
        agg.timestamp = 1704067200000
        agg.open = None
        agg.high = 155.0
        agg.low = 149.0
        agg.close = None
        agg.volume = None
        agg.vwap = None
        agg.transactions = None

        connector._connected = True
        connector._rest_client = MagicMock()

        with patch.object(connector, "_list_aggs", return_value=[agg]):
            df = await connector.get_historical_bars(
                instrument=equity_instrument,
                start=datetime(2024, 1, 1, tzinfo=UTC),
                end=datetime(2024, 1, 31, tzinfo=UTC),
            )

        assert np.isnan(df.iloc[0]["open"])
        assert np.isnan(df.iloc[0]["close"])
        assert df.iloc[0]["volume"] == 0.0
        assert np.isnan(df.iloc[0]["vwap"])
        assert df.iloc[0]["trade_count"] == 0

    @pytest.mark.asyncio
    async def test_crypto_bars(
        self,
        connector: PolygonConnector,
        crypto_instrument: Instrument,
        mock_aggs: list[MagicMock],
    ) -> None:
        """Crypto instruments should use X:SYMBOL ticker format."""
        connector._connected = True
        connector._rest_client = MagicMock()

        with patch.object(connector, "_list_aggs", return_value=mock_aggs) as mock_call:
            await connector.get_historical_bars(
                instrument=crypto_instrument,
                start=datetime(2024, 1, 1, tzinfo=UTC),
                end=datetime(2024, 1, 31, tzinfo=UTC),
            )
            # Verify the ticker used was crypto format
            mock_call.assert_called_once()
            call_args = mock_call.call_args
            assert call_args[0][0] == "X:BTCUSD"


# ---------------------------------------------------------------------------
# Historical trades tests
# ---------------------------------------------------------------------------


class TestGetHistoricalTrades:
    """Tests for get_historical_trades method."""

    @pytest.mark.asyncio
    async def test_not_connected_raises(
        self,
        connector: PolygonConnector,
        equity_instrument: Instrument,
    ) -> None:
        """Calling without connecting should raise DataError."""
        with pytest.raises(DataError, match="not connected"):
            await connector.get_historical_trades(
                instrument=equity_instrument,
                start=datetime(2024, 1, 1, tzinfo=UTC),
                end=datetime(2024, 1, 2, tzinfo=UTC),
            )

    @pytest.mark.asyncio
    async def test_returns_normalized_dataframe(
        self,
        connector: PolygonConnector,
        equity_instrument: Instrument,
        mock_trades: list[MagicMock],
    ) -> None:
        """Should return DataFrame with correct schema and dtypes."""
        connector._connected = True
        connector._rest_client = MagicMock()

        with patch.object(connector, "_list_trades", return_value=mock_trades):
            df = await connector.get_historical_trades(
                instrument=equity_instrument,
                start=datetime(2024, 1, 1, tzinfo=UTC),
                end=datetime(2024, 1, 2, tzinfo=UTC),
            )

        assert isinstance(df.index, pd.DatetimeIndex)
        assert df.index.name == "timestamp"
        assert len(df) == 2

        expected_columns = {"price", "size", "side"}
        assert set(df.columns) == expected_columns

        assert df["price"].dtype == np.float64
        assert df["size"].dtype == np.float64

        assert df.iloc[0]["price"] == 150.25
        assert df.iloc[0]["size"] == 100.0

    @pytest.mark.asyncio
    async def test_empty_results_raises_not_found(
        self,
        connector: PolygonConnector,
        equity_instrument: Instrument,
    ) -> None:
        """Empty trades should raise DataNotFoundError."""
        connector._connected = True
        connector._rest_client = MagicMock()

        with (
            patch.object(connector, "_list_trades", return_value=[]),
            pytest.raises(DataNotFoundError, match="No trade data found"),
        ):
            await connector.get_historical_trades(
                instrument=equity_instrument,
                start=datetime(2024, 1, 1, tzinfo=UTC),
                end=datetime(2024, 1, 2, tzinfo=UTC),
            )

    @pytest.mark.asyncio
    async def test_api_error_raises_data_error(
        self,
        connector: PolygonConnector,
        equity_instrument: Instrument,
    ) -> None:
        """REST client errors should be wrapped in DataError."""
        connector._connected = True
        connector._rest_client = MagicMock()

        with (
            patch.object(
                connector,
                "_list_trades",
                side_effect=RuntimeError("timeout"),
            ),
            pytest.raises(DataError, match="Polygon trades request failed"),
        ):
            await connector.get_historical_trades(
                instrument=equity_instrument,
                start=datetime(2024, 1, 1, tzinfo=UTC),
                end=datetime(2024, 1, 2, tzinfo=UTC),
            )

    @pytest.mark.asyncio
    async def test_fallback_to_participant_timestamp(
        self,
        connector: PolygonConnector,
        equity_instrument: Instrument,
    ) -> None:
        """If sip_timestamp is None, participant_timestamp should be used."""
        trade = MagicMock()
        trade.sip_timestamp = None
        trade.participant_timestamp = 1704067200000000000
        trade.price = 150.0
        trade.size = 50.0
        trade.conditions = None

        connector._connected = True
        connector._rest_client = MagicMock()

        with patch.object(connector, "_list_trades", return_value=[trade]):
            df = await connector.get_historical_trades(
                instrument=equity_instrument,
                start=datetime(2024, 1, 1, tzinfo=UTC),
                end=datetime(2024, 1, 2, tzinfo=UTC),
            )

        assert len(df) == 1
        assert df.iloc[0]["price"] == 150.0


# ---------------------------------------------------------------------------
# Historical quotes tests
# ---------------------------------------------------------------------------


class TestGetHistoricalQuotes:
    """Tests for get_historical_quotes method."""

    @pytest.mark.asyncio
    async def test_not_connected_raises(
        self,
        connector: PolygonConnector,
        equity_instrument: Instrument,
    ) -> None:
        """Calling without connecting should raise DataError."""
        with pytest.raises(DataError, match="not connected"):
            await connector.get_historical_quotes(
                instrument=equity_instrument,
                start=datetime(2024, 1, 1, tzinfo=UTC),
                end=datetime(2024, 1, 2, tzinfo=UTC),
            )

    @pytest.mark.asyncio
    async def test_returns_normalized_dataframe(
        self,
        connector: PolygonConnector,
        equity_instrument: Instrument,
        mock_quotes: list[MagicMock],
    ) -> None:
        """Should return DataFrame with correct schema and dtypes."""
        connector._connected = True
        connector._rest_client = MagicMock()

        with patch.object(connector, "_list_quotes", return_value=mock_quotes):
            df = await connector.get_historical_quotes(
                instrument=equity_instrument,
                start=datetime(2024, 1, 1, tzinfo=UTC),
                end=datetime(2024, 1, 2, tzinfo=UTC),
            )

        assert isinstance(df.index, pd.DatetimeIndex)
        assert df.index.name == "timestamp"
        assert len(df) == 2

        expected_columns = {"bid_price", "bid_size", "ask_price", "ask_size"}
        assert set(df.columns) == expected_columns

        assert df["bid_price"].dtype == np.float64
        assert df["bid_size"].dtype == np.float64
        assert df["ask_price"].dtype == np.float64
        assert df["ask_size"].dtype == np.float64

        assert df.iloc[0]["bid_price"] == 150.00
        assert df.iloc[0]["ask_price"] == 150.05
        assert df.iloc[1]["bid_size"] == 400.0

    @pytest.mark.asyncio
    async def test_empty_results_raises_not_found(
        self,
        connector: PolygonConnector,
        equity_instrument: Instrument,
    ) -> None:
        """Empty quotes should raise DataNotFoundError."""
        connector._connected = True
        connector._rest_client = MagicMock()

        with (
            patch.object(connector, "_list_quotes", return_value=[]),
            pytest.raises(DataNotFoundError, match="No quote data found"),
        ):
            await connector.get_historical_quotes(
                instrument=equity_instrument,
                start=datetime(2024, 1, 1, tzinfo=UTC),
                end=datetime(2024, 1, 2, tzinfo=UTC),
            )

    @pytest.mark.asyncio
    async def test_api_error_raises_data_error(
        self,
        connector: PolygonConnector,
        equity_instrument: Instrument,
    ) -> None:
        """REST client errors should be wrapped in DataError."""
        connector._connected = True
        connector._rest_client = MagicMock()

        with (
            patch.object(
                connector,
                "_list_quotes",
                side_effect=RuntimeError("network error"),
            ),
            pytest.raises(DataError, match="Polygon quotes request failed"),
        ):
            await connector.get_historical_quotes(
                instrument=equity_instrument,
                start=datetime(2024, 1, 1, tzinfo=UTC),
                end=datetime(2024, 1, 2, tzinfo=UTC),
            )

    @pytest.mark.asyncio
    async def test_handles_none_values_in_quotes(
        self,
        connector: PolygonConnector,
        equity_instrument: Instrument,
    ) -> None:
        """Quote fields with None should be replaced with NaN or 0."""
        quote = MagicMock()
        quote.sip_timestamp = 1704067200000000000
        quote.participant_timestamp = 1704067200000000000
        quote.bid_price = None
        quote.bid_size = None
        quote.ask_price = 150.05
        quote.ask_size = None

        connector._connected = True
        connector._rest_client = MagicMock()

        with patch.object(connector, "_list_quotes", return_value=[quote]):
            df = await connector.get_historical_quotes(
                instrument=equity_instrument,
                start=datetime(2024, 1, 1, tzinfo=UTC),
                end=datetime(2024, 1, 2, tzinfo=UTC),
            )

        assert np.isnan(df.iloc[0]["bid_price"])
        assert df.iloc[0]["bid_size"] == 0.0
        assert df.iloc[0]["ask_price"] == 150.05
        assert df.iloc[0]["ask_size"] == 0.0


# ---------------------------------------------------------------------------
# Trade side inference tests
# ---------------------------------------------------------------------------


class TestInferTradeSide:
    """Tests for _infer_trade_side helper."""

    def test_none_conditions_returns_empty(self) -> None:
        """None conditions should return empty string."""
        assert _infer_trade_side(None) == ""

    def test_empty_conditions_returns_empty(self) -> None:
        """Empty conditions list should return empty string."""
        assert _infer_trade_side([]) == ""

    def test_unknown_conditions_returns_empty(self) -> None:
        """Unknown condition codes should return empty string."""
        assert _infer_trade_side([1, 2, 3]) == ""


# ---------------------------------------------------------------------------
# WebSocket helper tests
# ---------------------------------------------------------------------------


class TestResolveInstrumentFromWs:
    """Tests for _resolve_instrument_from_ws helper."""

    def test_equity_symbol_match(self, equity_instrument: Instrument) -> None:
        """Should resolve equity instrument from symbol field."""
        msg = MagicMock()
        msg.symbol = "AAPL"
        msg.pair = None
        instrument_map = {"AAPL": equity_instrument}
        result = _resolve_instrument_from_ws(msg, instrument_map)
        assert result == equity_instrument

    def test_crypto_pair_match(self, crypto_instrument: Instrument) -> None:
        """Should resolve crypto instrument from pair field."""
        msg = MagicMock()
        msg.symbol = None
        msg.pair = "BTCUSD"
        instrument_map = {"X:BTCUSD": crypto_instrument}
        result = _resolve_instrument_from_ws(msg, instrument_map)
        assert result == crypto_instrument

    def test_no_match_returns_none(self) -> None:
        """Should return None if no matching instrument found."""
        msg = MagicMock()
        msg.symbol = "UNKNOWN"
        msg.pair = None
        result = _resolve_instrument_from_ws(msg, {})
        assert result is None

    def test_no_symbol_or_pair_returns_none(self) -> None:
        """Should return None if message has no symbol or pair."""
        msg = MagicMock(spec=[])  # No attributes
        result = _resolve_instrument_from_ws(msg, {})
        assert result is None


class TestExtractWsTimestampNs:
    """Tests for _extract_ws_timestamp_ns helper."""

    def test_extracts_timestamp_ms_to_ns(self) -> None:
        """Should convert millisecond timestamp to nanoseconds."""
        msg = MagicMock()
        msg.timestamp = 1704067200000  # ms
        result = _extract_ws_timestamp_ns(msg)
        assert result == 1704067200000 * 1_000_000

    def test_no_timestamp_returns_zero(self) -> None:
        """Should return 0 if no timestamp attribute."""
        msg = MagicMock(spec=[])  # No attributes
        result = _extract_ws_timestamp_ns(msg)
        assert result == 0


# ---------------------------------------------------------------------------
# Streaming tests
# ---------------------------------------------------------------------------


class TestStreamQuotes:
    """Tests for stream_quotes method."""

    @pytest.mark.asyncio
    async def test_not_connected_raises(
        self,
        connector: PolygonConnector,
        equity_instrument: Instrument,
    ) -> None:
        """Calling without connecting should raise DataError."""
        with pytest.raises(DataError, match="not connected"):
            async for _ in connector.stream_quotes([equity_instrument]):
                pass

    @pytest.mark.asyncio
    async def test_yields_quote_events(
        self,
        connector: PolygonConnector,
        equity_instrument: Instrument,
    ) -> None:
        """Should yield QuoteEvent instances from WebSocket messages."""
        connector._connected = True
        connector._rest_client = MagicMock()

        async def fake_run_websocket(
            instruments: list[Instrument],
            subscriptions: list[str],
            processor: object,
        ) -> None:
            """Simulate WS messages by calling the processor directly."""
            msg = MagicMock()
            msg.symbol = "AAPL"
            msg.pair = None
            msg.bid_price = 150.0
            msg.bid_size = 500.0
            msg.ask_price = 150.05
            msg.ask_size = 300.0
            msg.timestamp = 1704067200000
            await processor([msg])
            # Send None sentinel to stop the iterator
            # We need to get the queue from the closure
            # Instead, just cancel after processing

        with patch.object(connector, "_run_websocket", side_effect=fake_run_websocket):
            collected = []
            async for event in connector.stream_quotes([equity_instrument]):
                collected.append(event)
                break  # Stop after first event

        assert len(collected) == 1
        event = collected[0]
        assert isinstance(event, QuoteEvent)
        assert event.bid_price == Decimal("150.0")
        assert event.ask_price == Decimal("150.05")
        assert event.source == "polygon"

    @pytest.mark.asyncio
    async def test_equity_subscription_prefix(
        self,
        connector: PolygonConnector,
        equity_instrument: Instrument,
    ) -> None:
        """Equity instruments should use Q.TICKER subscription."""
        connector._connected = True
        connector._rest_client = MagicMock()

        captured_subs: list[str] = []

        async def capture_subs(instruments, subscriptions, processor):
            captured_subs.extend(subscriptions)
            # Don't produce any events; the generator will be cancelled

        with patch.object(connector, "_run_websocket", side_effect=capture_subs):
            gen = connector.stream_quotes([equity_instrument])
            task = asyncio.create_task(gen.__anext__())
            await asyncio.sleep(0.05)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                await task

        assert "Q.AAPL" in captured_subs

    @pytest.mark.asyncio
    async def test_crypto_subscription_prefix(
        self,
        connector: PolygonConnector,
        crypto_instrument: Instrument,
    ) -> None:
        """Crypto instruments should use XQ.X:TICKER subscription."""
        connector._connected = True
        connector._rest_client = MagicMock()

        captured_subs: list[str] = []

        async def capture_subs(instruments, subscriptions, processor):
            captured_subs.extend(subscriptions)

        with patch.object(connector, "_run_websocket", side_effect=capture_subs):
            gen = connector.stream_quotes([crypto_instrument])
            task = asyncio.create_task(gen.__anext__())
            await asyncio.sleep(0.05)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                await task

        assert "XQ.X:BTCUSD" in captured_subs


class TestStreamTrades:
    """Tests for stream_trades method."""

    @pytest.mark.asyncio
    async def test_not_connected_raises(
        self,
        connector: PolygonConnector,
        equity_instrument: Instrument,
    ) -> None:
        """Calling without connecting should raise DataError."""
        with pytest.raises(DataError, match="not connected"):
            async for _ in connector.stream_trades([equity_instrument]):
                pass

    @pytest.mark.asyncio
    async def test_yields_trade_events(
        self,
        connector: PolygonConnector,
        equity_instrument: Instrument,
    ) -> None:
        """Should yield TradeEvent instances from WebSocket messages."""
        connector._connected = True
        connector._rest_client = MagicMock()

        async def fake_run_websocket(instruments, subscriptions, processor):
            msg = MagicMock()
            msg.symbol = "AAPL"
            msg.pair = None
            msg.price = 150.25
            msg.size = 100.0
            msg.conditions = None
            msg.timestamp = 1704067200000
            await processor([msg])

        with patch.object(connector, "_run_websocket", side_effect=fake_run_websocket):
            collected = []
            async for event in connector.stream_trades([equity_instrument]):
                collected.append(event)
                break

        assert len(collected) == 1
        event = collected[0]
        assert isinstance(event, TradeEvent)
        assert event.price == Decimal("150.25")
        assert event.size == Decimal("100.0")
        assert event.source == "polygon"

    @pytest.mark.asyncio
    async def test_equity_subscription_prefix(
        self,
        connector: PolygonConnector,
        equity_instrument: Instrument,
    ) -> None:
        """Equity instruments should use T.TICKER subscription."""
        connector._connected = True
        connector._rest_client = MagicMock()

        captured_subs: list[str] = []

        async def capture_subs(instruments, subscriptions, processor):
            captured_subs.extend(subscriptions)

        with patch.object(connector, "_run_websocket", side_effect=capture_subs):
            gen = connector.stream_trades([equity_instrument])
            task = asyncio.create_task(gen.__anext__())
            await asyncio.sleep(0.05)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                await task

        assert "T.AAPL" in captured_subs


class TestStreamBars:
    """Tests for stream_bars method."""

    @pytest.mark.asyncio
    async def test_not_connected_raises(
        self,
        connector: PolygonConnector,
        equity_instrument: Instrument,
    ) -> None:
        """Calling without connecting should raise DataError."""
        with pytest.raises(DataError, match="not connected"):
            async for _ in connector.stream_bars([equity_instrument]):
                pass

    @pytest.mark.asyncio
    async def test_yields_bar_events(
        self,
        connector: PolygonConnector,
        equity_instrument: Instrument,
    ) -> None:
        """Should yield BarEvent instances from WebSocket messages."""
        connector._connected = True
        connector._rest_client = MagicMock()

        async def fake_run_websocket(instruments, subscriptions, processor):
            msg = MagicMock()
            msg.symbol = "AAPL"
            msg.pair = None
            msg.open = 150.0
            msg.high = 155.0
            msg.low = 149.0
            msg.close = 154.0
            msg.volume = 1000000.0
            msg.start_timestamp = 1704067200000
            msg.end_timestamp = 1704067260000
            msg.timestamp = 1704067260000
            await processor([msg])

        with patch.object(connector, "_run_websocket", side_effect=fake_run_websocket):
            collected = []
            async for event in connector.stream_bars([equity_instrument]):
                collected.append(event)
                break

        assert len(collected) == 1
        event = collected[0]
        assert isinstance(event, BarEvent)
        assert event.open == Decimal("150.0")
        assert event.close == Decimal("154.0")
        assert event.source == "polygon"

    @pytest.mark.asyncio
    async def test_minute_subscription_prefix(
        self,
        connector: PolygonConnector,
        equity_instrument: Instrument,
    ) -> None:
        """Minute bars for equities should use AM.TICKER subscription."""
        connector._connected = True
        connector._rest_client = MagicMock()

        captured_subs: list[str] = []

        async def capture_subs(instruments, subscriptions, processor):
            captured_subs.extend(subscriptions)

        with patch.object(connector, "_run_websocket", side_effect=capture_subs):
            gen = connector.stream_bars([equity_instrument], timeframe=BarTimeframe.MINUTE_1)
            task = asyncio.create_task(gen.__anext__())
            await asyncio.sleep(0.05)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                await task

        assert "AM.AAPL" in captured_subs

    @pytest.mark.asyncio
    async def test_second_subscription_prefix(
        self,
        connector: PolygonConnector,
        equity_instrument: Instrument,
    ) -> None:
        """Second bars for equities should use A.TICKER subscription."""
        connector._connected = True
        connector._rest_client = MagicMock()

        captured_subs: list[str] = []

        async def capture_subs(instruments, subscriptions, processor):
            captured_subs.extend(subscriptions)

        with patch.object(connector, "_run_websocket", side_effect=capture_subs):
            gen = connector.stream_bars([equity_instrument], timeframe=BarTimeframe.SECOND_1)
            task = asyncio.create_task(gen.__anext__())
            await asyncio.sleep(0.05)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                await task

        assert "A.AAPL" in captured_subs

    @pytest.mark.asyncio
    async def test_crypto_minute_subscription(
        self,
        connector: PolygonConnector,
        crypto_instrument: Instrument,
    ) -> None:
        """Crypto minute bars should use XA.X:TICKER subscription."""
        connector._connected = True
        connector._rest_client = MagicMock()

        captured_subs: list[str] = []

        async def capture_subs(instruments, subscriptions, processor):
            captured_subs.extend(subscriptions)

        with patch.object(connector, "_run_websocket", side_effect=capture_subs):
            gen = connector.stream_bars([crypto_instrument], timeframe=BarTimeframe.MINUTE_1)
            task = asyncio.create_task(gen.__anext__())
            await asyncio.sleep(0.05)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                await task

        assert "XA.X:BTCUSD" in captured_subs

    @pytest.mark.asyncio
    async def test_crypto_second_subscription(
        self,
        connector: PolygonConnector,
        crypto_instrument: Instrument,
    ) -> None:
        """Crypto second bars should use XAS.X:TICKER subscription."""
        connector._connected = True
        connector._rest_client = MagicMock()

        captured_subs: list[str] = []

        async def capture_subs(instruments, subscriptions, processor):
            captured_subs.extend(subscriptions)

        with patch.object(connector, "_run_websocket", side_effect=capture_subs):
            gen = connector.stream_bars([crypto_instrument], timeframe=BarTimeframe.SECOND_1)
            task = asyncio.create_task(gen.__anext__())
            await asyncio.sleep(0.05)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                await task

        assert "XAS.X:BTCUSD" in captured_subs
