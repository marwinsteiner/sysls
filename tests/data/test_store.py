"""Tests for the TimeSeriesStore interface and symbol key helper."""

from __future__ import annotations

import pytest

from sysls.core.types import AssetClass, Instrument, Venue
from sysls.data.connector import BarTimeframe
from sysls.data.store import TimeSeriesStore, make_symbol_key

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def instrument() -> Instrument:
    return Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY, venue=Venue.PAPER)


# ---------------------------------------------------------------------------
# make_symbol_key tests
# ---------------------------------------------------------------------------


class TestMakeSymbolKey:
    """Tests for the make_symbol_key helper."""

    def test_bars_with_timeframe(self, instrument: Instrument) -> None:
        key = make_symbol_key(instrument, BarTimeframe.DAY_1, data_type="bars")
        assert key == "AAPL/1d/bars"

    def test_bars_minute(self, instrument: Instrument) -> None:
        key = make_symbol_key(instrument, BarTimeframe.MINUTE_1, data_type="bars")
        assert key == "AAPL/1min/bars"

    def test_trades_no_timeframe(self, instrument: Instrument) -> None:
        key = make_symbol_key(instrument, data_type="trades")
        assert key == "AAPL/trades"

    def test_quotes_no_timeframe(self, instrument: Instrument) -> None:
        key = make_symbol_key(instrument, data_type="quotes")
        assert key == "AAPL/quotes"

    def test_default_data_type_is_bars(self, instrument: Instrument) -> None:
        key = make_symbol_key(instrument, BarTimeframe.HOUR_1)
        assert key == "AAPL/1h/bars"

    def test_crypto_symbol(self) -> None:
        inst = Instrument(
            symbol="BTC-USDT-PERP",
            asset_class=AssetClass.CRYPTO_PERP,
            venue=Venue.CCXT,
        )
        key = make_symbol_key(inst, BarTimeframe.MINUTE_5, data_type="bars")
        assert key == "BTC-USDT-PERP/5min/bars"


# ---------------------------------------------------------------------------
# TimeSeriesStore ABC tests
# ---------------------------------------------------------------------------


class TestTimeSeriesStoreABC:
    """Tests for the TimeSeriesStore abstract base class."""

    def test_cannot_instantiate_directly(self) -> None:
        with pytest.raises(TypeError):
            TimeSeriesStore()  # type: ignore[abstract]
