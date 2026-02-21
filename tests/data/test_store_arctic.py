"""Tests for the ArcticStore ArcticDB-backed TimeSeriesStore implementation.

Tests that require ``arcticdb`` are skipped when the package is not
installed (e.g., on Windows where no wheels are available).  The
importability and error-handling tests at the bottom of this module run
unconditionally on all platforms.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from sysls.core.exceptions import DataNotFoundError
from sysls.data.normalize import BAR_COLUMNS
from sysls.data.store_arctic import _HAS_ARCTICDB, ArcticStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bar_df(
    dates: list[str],
    *,
    open_start: float = 100.0,
) -> pd.DataFrame:
    """Build a minimal bar DataFrame conforming to the canonical schema."""
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


# ===========================================================================
# Tests that ALWAYS run (no arcticdb dependency)
# ===========================================================================


class TestArcticStoreImportability:
    """Tests that the store_arctic module is usable without arcticdb installed."""

    def test_module_is_importable(self) -> None:
        """store_arctic module should be importable regardless of arcticdb availability."""
        # If we got here, the import at the top of this file succeeded.
        from sysls.data import store_arctic

        assert hasattr(store_arctic, "ArcticStore")
        assert hasattr(store_arctic, "_HAS_ARCTICDB")

    def test_has_arcticdb_is_bool(self) -> None:
        """_HAS_ARCTICDB should be a boolean flag."""
        assert isinstance(_HAS_ARCTICDB, bool)

    def test_instantiation_without_arcticdb_raises(self) -> None:
        """Instantiating ArcticStore without arcticdb should raise ImportError."""
        if _HAS_ARCTICDB:
            pytest.skip("arcticdb is installed; cannot test missing-import path")

        with pytest.raises(ImportError, match="arcticdb is required"):
            ArcticStore(uri="lmdb://test", library_name="test")


# ===========================================================================
# Tests that require arcticdb (skipped on platforms without it)
# ===========================================================================


@pytest.fixture
def arctic_store(tmp_path: object) -> ArcticStore:
    """Create an ArcticStore backed by a temporary LMDB directory.

    Skipped automatically if arcticdb is not installed.
    """
    if not _HAS_ARCTICDB:
        pytest.skip("arcticdb is not installed")
    uri = f"lmdb://{tmp_path}/test_arctic"
    return ArcticStore(uri=uri, library_name="test")


@pytest.fixture
def bar_df() -> pd.DataFrame:
    """Return a 5-day bar DataFrame."""
    return _make_bar_df(["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"])


class TestArcticStoreWriteRead:
    """Write/read round-trip tests for ArcticStore."""

    @pytest.mark.asyncio
    async def test_write_and_read_round_trip(
        self, arctic_store: ArcticStore, bar_df: pd.DataFrame
    ) -> None:
        """Written data should be retrievable via read."""
        await arctic_store.write("AAPL/1d/bars", bar_df)
        result = await arctic_store.read("AAPL/1d/bars")

        pd.testing.assert_frame_equal(result, bar_df)

    @pytest.mark.asyncio
    async def test_write_overwrites(self, arctic_store: ArcticStore) -> None:
        """A second write should replace the first."""
        df1 = _make_bar_df(["2024-01-01", "2024-01-02"], open_start=100.0)
        df2 = _make_bar_df(["2024-06-01", "2024-06-02"], open_start=200.0)

        await arctic_store.write("AAPL/1d/bars", df1)
        await arctic_store.write("AAPL/1d/bars", df2)

        result = await arctic_store.read("AAPL/1d/bars")
        pd.testing.assert_frame_equal(result, df2)


class TestArcticStoreAppend:
    """Append tests for ArcticStore."""

    @pytest.mark.asyncio
    async def test_append_adds_rows(self, arctic_store: ArcticStore) -> None:
        """Appended rows should appear after existing data."""
        initial = _make_bar_df(["2024-01-01", "2024-01-02"], open_start=100.0)
        extra = _make_bar_df(["2024-01-03", "2024-01-04"], open_start=102.0)

        await arctic_store.write("AAPL/1d/bars", initial)
        await arctic_store.append("AAPL/1d/bars", extra)

        result = await arctic_store.read("AAPL/1d/bars")
        assert len(result) == 4

    @pytest.mark.asyncio
    async def test_append_to_nonexistent_raises(self, arctic_store: ArcticStore) -> None:
        """Appending to a non-existent symbol should raise DataNotFoundError."""
        df = _make_bar_df(["2024-01-01"])
        with pytest.raises(DataNotFoundError, match="Symbol not found"):
            await arctic_store.append("MISSING/1d/bars", df)


class TestArcticStoreFiltering:
    """Date range filtering tests for ArcticStore."""

    @pytest.mark.asyncio
    async def test_read_with_date_range(
        self, arctic_store: ArcticStore, bar_df: pd.DataFrame
    ) -> None:
        """Reading with date range should filter results."""
        await arctic_store.write("AAPL/1d/bars", bar_df)
        start = datetime(2024, 1, 2, tzinfo=UTC)
        end = datetime(2024, 1, 4, tzinfo=UTC)
        result = await arctic_store.read("AAPL/1d/bars", start=start, end=end)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_read_nonexistent_raises(self, arctic_store: ArcticStore) -> None:
        """Reading a non-existent symbol should raise DataNotFoundError."""
        with pytest.raises(DataNotFoundError, match="Symbol not found"):
            await arctic_store.read("MISSING/1d/bars")


class TestArcticStoreSymbolManagement:
    """List/has/delete tests for ArcticStore."""

    @pytest.mark.asyncio
    async def test_list_symbols_sorted(self, arctic_store: ArcticStore) -> None:
        """list_symbols should return sorted keys."""
        df = _make_bar_df(["2024-01-01"])
        await arctic_store.write("MSFT/1d/bars", df)
        await arctic_store.write("AAPL/1d/bars", df)

        result = await arctic_store.list_symbols()
        assert result == ["AAPL/1d/bars", "MSFT/1d/bars"]

    @pytest.mark.asyncio
    async def test_has_symbol(self, arctic_store: ArcticStore, bar_df: pd.DataFrame) -> None:
        """has_symbol should return True for existing, False for non-existing."""
        await arctic_store.write("AAPL/1d/bars", bar_df)
        assert await arctic_store.has_symbol("AAPL/1d/bars") is True
        assert await arctic_store.has_symbol("MISSING") is False

    @pytest.mark.asyncio
    async def test_delete_existing(self, arctic_store: ArcticStore, bar_df: pd.DataFrame) -> None:
        """Deleting an existing symbol should remove it."""
        await arctic_store.write("AAPL/1d/bars", bar_df)
        await arctic_store.delete("AAPL/1d/bars")
        assert await arctic_store.has_symbol("AAPL/1d/bars") is False

    @pytest.mark.asyncio
    async def test_delete_nonexistent_is_noop(self, arctic_store: ArcticStore) -> None:
        """Deleting a non-existent symbol should not raise."""
        await arctic_store.delete("MISSING/1d/bars")  # Should not raise
