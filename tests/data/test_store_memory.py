"""Tests for the MemoryStore in-memory TimeSeriesStore implementation."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from sysls.core.exceptions import DataNotFoundError
from sysls.data.normalize import BAR_COLUMNS, QUOTE_COLUMNS, TRADE_COLUMNS
from sysls.data.store_memory import MemoryStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bar_df(
    dates: list[str],
    *,
    open_start: float = 100.0,
) -> pd.DataFrame:
    """Build a minimal bar DataFrame conforming to the canonical schema.

    Args:
        dates: ISO date strings for the DatetimeIndex.
        open_start: Starting open price; other OHLC values derived from it.

    Returns:
        A DataFrame with BAR_COLUMNS and a UTC DatetimeIndex named
        ``"timestamp"``.
    """
    n = len(dates)
    idx = pd.DatetimeIndex(dates, name="timestamp", tz="UTC")
    data = {
        "open": np.arange(open_start, open_start + n, dtype="float64"),
        "high": np.arange(open_start + 1, open_start + n + 1, dtype="float64"),
        "low": np.arange(open_start - 1, open_start + n - 1, dtype="float64"),
        "close": np.arange(open_start + 0.5, open_start + n + 0.5, dtype="float64"),
        "volume": np.full(n, 1_000_000.0, dtype="float64"),
        "vwap": np.arange(open_start + 0.25, open_start + n + 0.25, dtype="float64"),
        "trade_count": np.full(n, 5000, dtype="int64"),
    }
    return pd.DataFrame(data, index=idx, columns=BAR_COLUMNS)


def _make_trade_df(dates: list[str]) -> pd.DataFrame:
    """Build a minimal trade DataFrame conforming to the canonical schema."""
    n = len(dates)
    idx = pd.DatetimeIndex(dates, name="timestamp", tz="UTC")
    data = {
        "price": np.arange(50.0, 50.0 + n, dtype="float64"),
        "size": np.full(n, 100.0, dtype="float64"),
        "side": ["BUY"] * n,
    }
    return pd.DataFrame(data, index=idx, columns=TRADE_COLUMNS)


def _make_quote_df(dates: list[str]) -> pd.DataFrame:
    """Build a minimal quote DataFrame conforming to the canonical schema."""
    n = len(dates)
    idx = pd.DatetimeIndex(dates, name="timestamp", tz="UTC")
    data = {
        "bid_price": np.arange(99.0, 99.0 + n, dtype="float64"),
        "bid_size": np.full(n, 500.0, dtype="float64"),
        "ask_price": np.arange(101.0, 101.0 + n, dtype="float64"),
        "ask_size": np.full(n, 500.0, dtype="float64"),
    }
    return pd.DataFrame(data, index=idx, columns=QUOTE_COLUMNS)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store() -> MemoryStore:
    """Return a fresh MemoryStore instance."""
    return MemoryStore()


@pytest.fixture
def bar_df() -> pd.DataFrame:
    """Return a 5-day bar DataFrame."""
    return _make_bar_df(["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"])


# ---------------------------------------------------------------------------
# Write / read round-trip
# ---------------------------------------------------------------------------


class TestWriteRead:
    """Tests for write and read round-trip behavior."""

    @pytest.mark.asyncio
    async def test_write_and_read_round_trip(
        self, store: MemoryStore, bar_df: pd.DataFrame
    ) -> None:
        """Written data should be retrievable via read."""
        await store.write("AAPL/1d/bars", bar_df)
        result = await store.read("AAPL/1d/bars")

        pd.testing.assert_frame_equal(result, bar_df)

    @pytest.mark.asyncio
    async def test_write_overwrites_existing(self, store: MemoryStore) -> None:
        """A second write to the same symbol replaces the first."""
        df1 = _make_bar_df(["2024-01-01", "2024-01-02"], open_start=100.0)
        df2 = _make_bar_df(["2024-06-01", "2024-06-02"], open_start=200.0)

        await store.write("AAPL/1d/bars", df1)
        await store.write("AAPL/1d/bars", df2)

        result = await store.read("AAPL/1d/bars")
        pd.testing.assert_frame_equal(result, df2)

    @pytest.mark.asyncio
    async def test_write_with_trade_schema(self, store: MemoryStore) -> None:
        """Write and read back trade-schema DataFrames."""
        df = _make_trade_df(["2024-01-01T10:00:00", "2024-01-01T10:00:01"])
        await store.write("AAPL/trades", df)
        result = await store.read("AAPL/trades")
        pd.testing.assert_frame_equal(result, df)

    @pytest.mark.asyncio
    async def test_write_with_quote_schema(self, store: MemoryStore) -> None:
        """Write and read back quote-schema DataFrames."""
        df = _make_quote_df(["2024-01-01T10:00:00", "2024-01-01T10:00:01"])
        await store.write("AAPL/quotes", df)
        result = await store.read("AAPL/quotes")
        pd.testing.assert_frame_equal(result, df)


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


class TestMetadata:
    """Tests for metadata storage alongside DataFrames."""

    @pytest.mark.asyncio
    async def test_metadata_stored(self, store: MemoryStore, bar_df: pd.DataFrame) -> None:
        """Metadata dict should be stored with the symbol."""
        meta = {"source": "polygon", "timeframe": "1d"}
        await store.write("AAPL/1d/bars", bar_df, metadata=meta)

        assert store.get_metadata("AAPL/1d/bars") == meta

    @pytest.mark.asyncio
    async def test_metadata_cleared_on_overwrite_without_meta(
        self, store: MemoryStore, bar_df: pd.DataFrame
    ) -> None:
        """Overwriting a symbol without metadata should clear old metadata."""
        await store.write("AAPL/1d/bars", bar_df, metadata={"source": "polygon"})
        await store.write("AAPL/1d/bars", bar_df)

        assert store.get_metadata("AAPL/1d/bars") is None

    @pytest.mark.asyncio
    async def test_metadata_replaced_on_overwrite(
        self, store: MemoryStore, bar_df: pd.DataFrame
    ) -> None:
        """Overwriting a symbol with new metadata should replace old metadata."""
        await store.write("AAPL/1d/bars", bar_df, metadata={"source": "polygon"})
        await store.write("AAPL/1d/bars", bar_df, metadata={"source": "databento"})

        assert store.get_metadata("AAPL/1d/bars") == {"source": "databento"}

    @pytest.mark.asyncio
    async def test_metadata_none_for_unknown_symbol(self, store: MemoryStore) -> None:
        """get_metadata returns None for symbols that do not exist."""
        assert store.get_metadata("UNKNOWN") is None


# ---------------------------------------------------------------------------
# Append
# ---------------------------------------------------------------------------


class TestAppend:
    """Tests for the append method."""

    @pytest.mark.asyncio
    async def test_append_adds_rows(self, store: MemoryStore) -> None:
        """Appended rows should appear after existing data."""
        initial = _make_bar_df(["2024-01-01", "2024-01-02"], open_start=100.0)
        extra = _make_bar_df(["2024-01-03", "2024-01-04"], open_start=102.0)

        await store.write("AAPL/1d/bars", initial)
        await store.append("AAPL/1d/bars", extra)

        result = await store.read("AAPL/1d/bars")
        assert len(result) == 4
        # First two rows match initial, last two match extra
        pd.testing.assert_frame_equal(result.iloc[:2], initial)
        pd.testing.assert_frame_equal(result.iloc[2:], extra)

    @pytest.mark.asyncio
    async def test_append_sorts_by_index(self, store: MemoryStore) -> None:
        """Appended data should be sorted by timestamp index."""
        initial = _make_bar_df(["2024-01-03", "2024-01-05"], open_start=100.0)
        extra = _make_bar_df(["2024-01-01", "2024-01-04"], open_start=200.0)

        await store.write("AAPL/1d/bars", initial)
        await store.append("AAPL/1d/bars", extra)

        result = await store.read("AAPL/1d/bars")
        assert result.index.is_monotonic_increasing

    @pytest.mark.asyncio
    async def test_append_to_nonexistent_raises(self, store: MemoryStore) -> None:
        """Appending to a non-existent symbol should raise DataNotFoundError."""
        df = _make_bar_df(["2024-01-01"])
        with pytest.raises(DataNotFoundError, match="Symbol not found"):
            await store.append("MISSING/1d/bars", df)


# ---------------------------------------------------------------------------
# Read with filtering
# ---------------------------------------------------------------------------


class TestReadFiltering:
    """Tests for date range and column subset filtering on read."""

    @pytest.mark.asyncio
    async def test_read_with_start_filter(self, store: MemoryStore, bar_df: pd.DataFrame) -> None:
        """Reading with a start date should exclude earlier rows."""
        await store.write("AAPL/1d/bars", bar_df)

        start = datetime(2024, 1, 3, tzinfo=UTC)
        result = await store.read("AAPL/1d/bars", start=start)
        assert len(result) == 3
        assert result.index.min() >= pd.Timestamp(start)

    @pytest.mark.asyncio
    async def test_read_with_end_filter(self, store: MemoryStore, bar_df: pd.DataFrame) -> None:
        """Reading with an end date should exclude later rows."""
        await store.write("AAPL/1d/bars", bar_df)

        end = datetime(2024, 1, 3, tzinfo=UTC)
        result = await store.read("AAPL/1d/bars", end=end)
        assert len(result) == 3
        assert result.index.max() <= pd.Timestamp(end)

    @pytest.mark.asyncio
    async def test_read_with_start_and_end_filter(
        self, store: MemoryStore, bar_df: pd.DataFrame
    ) -> None:
        """Reading with both start and end should return the intersection."""
        await store.write("AAPL/1d/bars", bar_df)

        start = datetime(2024, 1, 2, tzinfo=UTC)
        end = datetime(2024, 1, 4, tzinfo=UTC)
        result = await store.read("AAPL/1d/bars", start=start, end=end)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_read_with_column_subset(self, store: MemoryStore, bar_df: pd.DataFrame) -> None:
        """Reading with columns should return only those columns."""
        await store.write("AAPL/1d/bars", bar_df)
        result = await store.read("AAPL/1d/bars", columns=["open", "close"])
        assert list(result.columns) == ["open", "close"]
        assert len(result) == len(bar_df)

    @pytest.mark.asyncio
    async def test_read_with_date_range_and_columns(
        self, store: MemoryStore, bar_df: pd.DataFrame
    ) -> None:
        """Date range and column filtering should compose correctly."""
        await store.write("AAPL/1d/bars", bar_df)

        start = datetime(2024, 1, 2, tzinfo=UTC)
        end = datetime(2024, 1, 3, tzinfo=UTC)
        result = await store.read(
            "AAPL/1d/bars", start=start, end=end, columns=["close", "volume"]
        )
        assert len(result) == 2
        assert list(result.columns) == ["close", "volume"]

    @pytest.mark.asyncio
    async def test_read_empty_range_returns_empty_df(
        self, store: MemoryStore, bar_df: pd.DataFrame
    ) -> None:
        """A date range with no matching data should return an empty DataFrame."""
        await store.write("AAPL/1d/bars", bar_df)

        start = datetime(2025, 1, 1, tzinfo=UTC)
        result = await store.read("AAPL/1d/bars", start=start)
        assert len(result) == 0
        assert isinstance(result.index, pd.DatetimeIndex)

    @pytest.mark.asyncio
    async def test_read_nonexistent_raises(self, store: MemoryStore) -> None:
        """Reading a non-existent symbol should raise DataNotFoundError."""
        with pytest.raises(DataNotFoundError, match="Symbol not found"):
            await store.read("MISSING/1d/bars")


# ---------------------------------------------------------------------------
# List / has / delete
# ---------------------------------------------------------------------------


class TestSymbolManagement:
    """Tests for list_symbols, has_symbol, and delete."""

    @pytest.mark.asyncio
    async def test_list_symbols_sorted(self, store: MemoryStore) -> None:
        """list_symbols should return all keys in sorted order."""
        df = _make_bar_df(["2024-01-01"])
        await store.write("MSFT/1d/bars", df)
        await store.write("AAPL/1d/bars", df)
        await store.write("GOOG/1d/bars", df)

        result = await store.list_symbols()
        assert result == ["AAPL/1d/bars", "GOOG/1d/bars", "MSFT/1d/bars"]

    @pytest.mark.asyncio
    async def test_has_symbol_true(self, store: MemoryStore, bar_df: pd.DataFrame) -> None:
        """has_symbol should return True for existing symbols."""
        await store.write("AAPL/1d/bars", bar_df)
        assert await store.has_symbol("AAPL/1d/bars") is True

    @pytest.mark.asyncio
    async def test_has_symbol_false(self, store: MemoryStore) -> None:
        """has_symbol should return False for non-existing symbols."""
        assert await store.has_symbol("MISSING") is False

    @pytest.mark.asyncio
    async def test_delete_existing(self, store: MemoryStore, bar_df: pd.DataFrame) -> None:
        """Deleting an existing symbol should remove it from the store."""
        await store.write("AAPL/1d/bars", bar_df, metadata={"source": "test"})
        await store.delete("AAPL/1d/bars")

        assert await store.has_symbol("AAPL/1d/bars") is False
        assert store.get_metadata("AAPL/1d/bars") is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_is_noop(self, store: MemoryStore) -> None:
        """Deleting a non-existent symbol should not raise an error."""
        await store.delete("MISSING/1d/bars")  # Should not raise

    @pytest.mark.asyncio
    async def test_delete_then_read_raises(self, store: MemoryStore, bar_df: pd.DataFrame) -> None:
        """Reading after delete should raise DataNotFoundError."""
        await store.write("AAPL/1d/bars", bar_df)
        await store.delete("AAPL/1d/bars")

        with pytest.raises(DataNotFoundError):
            await store.read("AAPL/1d/bars")


# ---------------------------------------------------------------------------
# Empty store
# ---------------------------------------------------------------------------


class TestEmptyStore:
    """Tests for behavior of an empty store."""

    @pytest.mark.asyncio
    async def test_list_symbols_empty(self, store: MemoryStore) -> None:
        """Empty store should return an empty list."""
        assert await store.list_symbols() == []

    @pytest.mark.asyncio
    async def test_has_symbol_empty(self, store: MemoryStore) -> None:
        """Empty store should return False for any symbol."""
        assert await store.has_symbol("ANYTHING") is False


# ---------------------------------------------------------------------------
# Defensive copying
# ---------------------------------------------------------------------------


class TestDefensiveCopy:
    """Tests verifying that MemoryStore uses defensive copies."""

    @pytest.mark.asyncio
    async def test_write_is_defensive(self, store: MemoryStore) -> None:
        """Mutating the original DataFrame after write should not affect the store."""
        df = _make_bar_df(["2024-01-01", "2024-01-02"])
        await store.write("AAPL/1d/bars", df)

        # Mutate the original
        df.iloc[0, 0] = -999.0

        result = await store.read("AAPL/1d/bars")
        assert result.iloc[0, 0] != -999.0

    @pytest.mark.asyncio
    async def test_read_is_defensive(self, store: MemoryStore) -> None:
        """Mutating a read result should not affect the store."""
        df = _make_bar_df(["2024-01-01", "2024-01-02"])
        await store.write("AAPL/1d/bars", df)

        result = await store.read("AAPL/1d/bars")
        result.iloc[0, 0] = -999.0

        # Read again to verify store is unchanged
        result2 = await store.read("AAPL/1d/bars")
        assert result2.iloc[0, 0] != -999.0

    @pytest.mark.asyncio
    async def test_append_is_defensive(self, store: MemoryStore) -> None:
        """Mutating the appended DataFrame after append should not affect the store."""
        initial = _make_bar_df(["2024-01-01"], open_start=100.0)
        extra = _make_bar_df(["2024-01-02"], open_start=200.0)

        await store.write("AAPL/1d/bars", initial)
        await store.append("AAPL/1d/bars", extra)

        # Mutate the extra df
        extra.iloc[0, 0] = -999.0

        result = await store.read("AAPL/1d/bars")
        assert result.iloc[1, 0] != -999.0
