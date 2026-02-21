"""Tests for the normalize module — schemas, validation, and conversion."""

from __future__ import annotations

from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from sysls.core.events import BarEvent, QuoteEvent, TradeEvent
from sysls.core.types import AssetClass, Instrument, Side, Venue
from sysls.data.normalize import (
    BAR_COLUMNS,
    BAR_DTYPES,
    QUOTE_COLUMNS,
    QUOTE_DTYPES,
    TRADE_COLUMNS,
    TRADE_DTYPES,
    bars_to_events,
    quotes_to_events,
    trades_to_events,
    validate_bar_dataframe,
    validate_quote_dataframe,
    validate_trade_dataframe,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def instrument() -> Instrument:
    return Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY, venue=Venue.PAPER)


@pytest.fixture
def bar_df() -> pd.DataFrame:
    """A valid 3-row bar DataFrame."""
    index = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"], utc=True)
    index.name = "timestamp"
    return pd.DataFrame(
        {
            "open": [150.0, 151.0, 149.0],
            "high": [155.0, 153.0, 152.0],
            "low": [149.0, 150.0, 148.0],
            "close": [154.0, 152.0, 151.0],
            "volume": [1_000_000.0, 900_000.0, 1_100_000.0],
            "vwap": [152.5, 151.5, 150.0],
            "trade_count": [5000, 4500, 5500],
        },
        index=index,
    )


@pytest.fixture
def trade_df() -> pd.DataFrame:
    """A valid 3-row trade DataFrame."""
    index = pd.to_datetime(
        ["2024-01-02 09:30:00", "2024-01-02 09:30:01", "2024-01-02 09:30:02"],
        utc=True,
    )
    index.name = "timestamp"
    return pd.DataFrame(
        {
            "price": [150.25, 150.30, 150.20],
            "size": [100.0, 200.0, 50.0],
            "side": ["BUY", "SELL", ""],
        },
        index=index,
    )


@pytest.fixture
def quote_df() -> pd.DataFrame:
    """A valid 3-row quote DataFrame."""
    index = pd.to_datetime(
        ["2024-01-02 09:30:00", "2024-01-02 09:30:01", "2024-01-02 09:30:02"],
        utc=True,
    )
    index.name = "timestamp"
    return pd.DataFrame(
        {
            "bid_price": [150.00, 150.05, 150.10],
            "bid_size": [500.0, 400.0, 600.0],
            "ask_price": [150.10, 150.15, 150.20],
            "ask_size": [300.0, 350.0, 250.0],
        },
        index=index,
    )


# ---------------------------------------------------------------------------
# Schema definition tests
# ---------------------------------------------------------------------------


class TestSchemaDefinitions:
    """Tests for the canonical schema constants."""

    def test_bar_columns_match_dtypes(self) -> None:
        assert list(BAR_DTYPES.keys()) == BAR_COLUMNS

    def test_trade_columns_match_dtypes(self) -> None:
        assert list(TRADE_DTYPES.keys()) == TRADE_COLUMNS

    def test_quote_columns_match_dtypes(self) -> None:
        assert list(QUOTE_DTYPES.keys()) == QUOTE_COLUMNS

    def test_bar_dtypes_are_numpy(self) -> None:
        for dtype in BAR_DTYPES.values():
            assert isinstance(dtype, np.dtype)

    def test_trade_dtypes_are_numpy(self) -> None:
        for dtype in TRADE_DTYPES.values():
            assert isinstance(dtype, np.dtype)

    def test_quote_dtypes_are_numpy(self) -> None:
        for dtype in QUOTE_DTYPES.values():
            assert isinstance(dtype, np.dtype)

    def test_bar_has_ohlcv_columns(self) -> None:
        for col in ["open", "high", "low", "close", "volume"]:
            assert col in BAR_COLUMNS

    def test_quote_has_bid_ask_columns(self) -> None:
        for col in ["bid_price", "bid_size", "ask_price", "ask_size"]:
            assert col in QUOTE_COLUMNS


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------


class TestValidation:
    """Tests for DataFrame validation functions."""

    def test_valid_bar_df_passes(self, bar_df: pd.DataFrame) -> None:
        validate_bar_dataframe(bar_df)  # should not raise

    def test_valid_trade_df_passes(self, trade_df: pd.DataFrame) -> None:
        validate_trade_dataframe(trade_df)

    def test_valid_quote_df_passes(self, quote_df: pd.DataFrame) -> None:
        validate_quote_dataframe(quote_df)

    def test_bar_df_wrong_index_type_raises(self) -> None:
        df = pd.DataFrame(
            {
                "open": [1.0],
                "high": [2.0],
                "low": [0.5],
                "close": [1.5],
                "volume": [100.0],
                "vwap": [1.2],
                "trade_count": [10],
            },
            index=[0],
        )
        with pytest.raises(ValueError, match="DatetimeIndex"):
            validate_bar_dataframe(df)

    def test_bar_df_missing_column_raises(self) -> None:
        index = pd.to_datetime(["2024-01-02"], utc=True)
        df = pd.DataFrame(
            {
                "open": [1.0],
                "high": [2.0],
                "low": [0.5],
                "close": [1.5],
                "volume": [100.0],
                "vwap": [1.2],
            },  # missing trade_count
            index=index,
        )
        with pytest.raises(ValueError, match="trade_count"):
            validate_bar_dataframe(df)

    def test_trade_df_missing_column_raises(self) -> None:
        index = pd.to_datetime(["2024-01-02"], utc=True)
        df = pd.DataFrame({"price": [1.0], "size": [100.0]}, index=index)
        with pytest.raises(ValueError, match="side"):
            validate_trade_dataframe(df)

    def test_quote_df_missing_column_raises(self) -> None:
        index = pd.to_datetime(["2024-01-02"], utc=True)
        df = pd.DataFrame({"bid_price": [1.0]}, index=index)
        with pytest.raises(ValueError, match="missing required columns"):
            validate_quote_dataframe(df)

    def test_extra_columns_allowed(self, bar_df: pd.DataFrame) -> None:
        bar_df["extra_col"] = 42.0
        validate_bar_dataframe(bar_df)  # extra columns should be fine


# ---------------------------------------------------------------------------
# Bar conversion tests
# ---------------------------------------------------------------------------


class TestBarsToEvents:
    """Tests for bars_to_events conversion."""

    def test_returns_bar_events(self, bar_df: pd.DataFrame, instrument: Instrument) -> None:
        events = bars_to_events(bar_df, instrument)
        assert len(events) == 3
        assert all(isinstance(e, BarEvent) for e in events)

    def test_event_instrument_matches(self, bar_df: pd.DataFrame, instrument: Instrument) -> None:
        events = bars_to_events(bar_df, instrument)
        for event in events:
            assert event.instrument == instrument

    def test_event_prices_are_decimal(self, bar_df: pd.DataFrame, instrument: Instrument) -> None:
        events = bars_to_events(bar_df, instrument)
        first = events[0]
        assert isinstance(first.open, Decimal)
        assert isinstance(first.high, Decimal)
        assert isinstance(first.low, Decimal)
        assert isinstance(first.close, Decimal)
        assert isinstance(first.volume, Decimal)

    def test_event_values_match_dataframe(
        self, bar_df: pd.DataFrame, instrument: Instrument
    ) -> None:
        events = bars_to_events(bar_df, instrument)
        first = events[0]
        assert first.open == Decimal("150.0")
        assert first.high == Decimal("155.0")
        assert first.low == Decimal("149.0")
        assert first.close == Decimal("154.0")

    def test_event_source_propagated(self, bar_df: pd.DataFrame, instrument: Instrument) -> None:
        events = bars_to_events(bar_df, instrument, source="polygon")
        for event in events:
            assert event.source == "polygon"

    def test_event_timestamps_are_nanoseconds(
        self, bar_df: pd.DataFrame, instrument: Instrument
    ) -> None:
        events = bars_to_events(bar_df, instrument)
        for event in events:
            assert isinstance(event.timestamp_ns, int)
            assert event.timestamp_ns > 0

    def test_invalid_df_raises(self, instrument: Instrument) -> None:
        bad_df = pd.DataFrame({"x": [1]}, index=[0])
        with pytest.raises(ValueError):
            bars_to_events(bad_df, instrument)

    def test_empty_df_returns_empty_list(self, instrument: Instrument) -> None:
        index = pd.DatetimeIndex([], dtype="datetime64[ns, UTC]", name="timestamp")
        df = pd.DataFrame(
            {col: pd.Series([], dtype=dtype) for col, dtype in BAR_DTYPES.items()},
            index=index,
        )
        events = bars_to_events(df, instrument)
        assert events == []


# ---------------------------------------------------------------------------
# Trade conversion tests
# ---------------------------------------------------------------------------


class TestTradesToEvents:
    """Tests for trades_to_events conversion."""

    def test_returns_trade_events(self, trade_df: pd.DataFrame, instrument: Instrument) -> None:
        events = trades_to_events(trade_df, instrument)
        assert len(events) == 3
        assert all(isinstance(e, TradeEvent) for e in events)

    def test_side_mapping(self, trade_df: pd.DataFrame, instrument: Instrument) -> None:
        events = trades_to_events(trade_df, instrument)
        assert events[0].side is Side.BUY
        assert events[1].side is Side.SELL
        assert events[2].side is None  # empty string → None

    def test_prices_are_decimal(self, trade_df: pd.DataFrame, instrument: Instrument) -> None:
        events = trades_to_events(trade_df, instrument)
        assert isinstance(events[0].price, Decimal)
        assert isinstance(events[0].size, Decimal)

    def test_values_match_dataframe(self, trade_df: pd.DataFrame, instrument: Instrument) -> None:
        events = trades_to_events(trade_df, instrument)
        assert events[0].price == Decimal("150.25")
        assert events[1].size == Decimal("200.0")


# ---------------------------------------------------------------------------
# Quote conversion tests
# ---------------------------------------------------------------------------


class TestQuotesToEvents:
    """Tests for quotes_to_events conversion."""

    def test_returns_quote_events(self, quote_df: pd.DataFrame, instrument: Instrument) -> None:
        events = quotes_to_events(quote_df, instrument)
        assert len(events) == 3
        assert all(isinstance(e, QuoteEvent) for e in events)

    def test_prices_are_decimal(self, quote_df: pd.DataFrame, instrument: Instrument) -> None:
        events = quotes_to_events(quote_df, instrument)
        first = events[0]
        assert isinstance(first.bid_price, Decimal)
        assert isinstance(first.ask_price, Decimal)
        assert isinstance(first.bid_size, Decimal)
        assert isinstance(first.ask_size, Decimal)

    def test_values_match_dataframe(self, quote_df: pd.DataFrame, instrument: Instrument) -> None:
        events = quotes_to_events(quote_df, instrument)
        first = events[0]
        assert first.bid_price == Decimal("150.0")
        assert first.ask_price == Decimal("150.1")
        assert first.bid_size == Decimal("500.0")
        assert first.ask_size == Decimal("300.0")

    def test_event_source_propagated(self, quote_df: pd.DataFrame, instrument: Instrument) -> None:
        events = quotes_to_events(quote_df, instrument, source="databento")
        for event in events:
            assert event.source == "databento"
