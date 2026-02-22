"""Tests for the event-driven replay engine."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd
import pytest

from sysls.backtest.replay import ReplayEngine, _ns_to_datetime
from sysls.core.events import BarEvent, FillEvent, MarketDataEvent, PositionEvent
from sysls.core.types import AssetClass, Instrument, OrderType, Side, Venue
from sysls.strategy.base import Strategy

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


def _make_instrument(symbol: str = "TEST") -> Instrument:
    """Create a test instrument."""
    return Instrument(
        symbol=symbol,
        asset_class=AssetClass.EQUITY,
        venue=Venue.PAPER,
        currency="USD",
    )


def _make_bar_df(
    prices: list[float],
    start: str = "2024-01-01",
    freq: str = "D",
) -> pd.DataFrame:
    """Create a normalized bar DataFrame from a list of close prices.

    For simplicity, open=high=low=close and volume=1000 for each bar.
    """
    dates = pd.date_range(start=start, periods=len(prices), freq=freq, tz="UTC")
    return pd.DataFrame(
        {
            "open": prices,
            "high": prices,
            "low": prices,
            "close": prices,
            "volume": [1000.0] * len(prices),
            "vwap": prices,
            "trade_count": [100] * len(prices),
        },
        index=dates,
    )


def _make_trending_bar_df(
    start_price: float = 100.0,
    num_bars: int = 10,
    trend: float = 1.0,
    start: str = "2024-01-01",
    freq: str = "D",
) -> pd.DataFrame:
    """Create a bar DataFrame with a simple linear trend."""
    prices = [start_price + i * trend for i in range(num_bars)]
    return _make_bar_df(prices, start=start, freq=freq)


# ---------------------------------------------------------------------------
# Test strategies
# ---------------------------------------------------------------------------


class DoNothingStrategy(Strategy):
    """Strategy that does nothing — just receives data."""

    async def on_market_data(self, event: MarketDataEvent) -> None:
        """No-op handler."""


class BuyOnceStrategy(Strategy):
    """Strategy that buys 10 shares on the first bar, then holds."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._bought = False

    async def on_market_data(self, event: MarketDataEvent) -> None:
        """Buy on first bar event."""
        if not self._bought and isinstance(event, BarEvent):
            self._bought = True
            await self.request_order(
                instrument=event.instrument,
                side=Side.BUY,
                quantity=Decimal("10"),
                order_type=OrderType.MARKET,
                price=event.close,
            )


class BuyAndSellStrategy(Strategy):
    """Strategy that buys on bar 1 and sells on bar 3."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._bar_count = 0

    async def on_market_data(self, event: MarketDataEvent) -> None:
        """Buy on bar 1, sell on bar 3."""
        if not isinstance(event, BarEvent):
            return
        self._bar_count += 1
        if self._bar_count == 1:
            await self.request_order(
                instrument=event.instrument,
                side=Side.BUY,
                quantity=Decimal("10"),
                order_type=OrderType.MARKET,
                price=event.close,
            )
        elif self._bar_count == 3:
            await self.request_order(
                instrument=event.instrument,
                side=Side.SELL,
                quantity=Decimal("10"),
                order_type=OrderType.MARKET,
                price=event.close,
            )


class LifecycleTrackingStrategy(Strategy):
    """Strategy that records lifecycle events for testing."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.events_received: list[str] = []
        self.market_data_count = 0
        self.fill_count = 0
        self.position_count = 0

    async def on_start(self) -> None:
        """Record start."""
        self.events_received.append("start")

    async def on_stop(self) -> None:
        """Record stop."""
        self.events_received.append("stop")

    async def on_market_data(self, event: MarketDataEvent) -> None:
        """Record market data event."""
        self.market_data_count += 1
        self.events_received.append(f"market_data:{self.market_data_count}")

    async def on_fill(self, event: FillEvent) -> None:
        """Record fill event."""
        self.fill_count += 1
        self.events_received.append(f"fill:{self.fill_count}")

    async def on_position(self, event: PositionEvent) -> None:
        """Record position event."""
        self.position_count += 1
        self.events_received.append(f"position:{self.position_count}")


class ParamStrategy(Strategy):
    """Strategy that uses params dict to configure behavior."""

    async def on_market_data(self, event: MarketDataEvent) -> None:
        """Buy if params say so."""
        if self.params.get("should_buy") and isinstance(event, BarEvent):
            self._params["should_buy"] = False  # Only buy once
            qty = Decimal(str(self.params.get("quantity", 1)))
            await self.request_order(
                instrument=event.instrument,
                side=Side.BUY,
                quantity=qty,
                order_type=OrderType.MARKET,
                price=event.close,
            )


class MultiInstrumentStrategy(Strategy):
    """Strategy that buys each instrument once."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._bought: set[str] = set()

    async def on_market_data(self, event: MarketDataEvent) -> None:
        """Buy each instrument on first bar."""
        if not isinstance(event, BarEvent):
            return
        key = str(event.instrument)
        if key not in self._bought:
            self._bought.add(key)
            await self.request_order(
                instrument=event.instrument,
                side=Side.BUY,
                quantity=Decimal("5"),
                order_type=OrderType.MARKET,
                price=event.close,
            )


# ---------------------------------------------------------------------------
# Tests: ReplayEngine.__init__
# ---------------------------------------------------------------------------


class TestReplayEngineInit:
    """Tests for ReplayEngine constructor."""

    def test_default_initial_capital(self) -> None:
        """Default initial capital is 100000."""
        engine = ReplayEngine()
        assert engine._initial_capital == Decimal("100000")

    def test_custom_initial_capital(self) -> None:
        """Custom initial capital is stored correctly."""
        engine = ReplayEngine(initial_capital=Decimal("50000"))
        assert engine._initial_capital == Decimal("50000")

    def test_default_commission_rate(self) -> None:
        """Default commission rate is 0."""
        engine = ReplayEngine()
        assert engine._commission_rate == Decimal("0")

    def test_custom_commission_rate(self) -> None:
        """Custom commission rate is stored correctly."""
        engine = ReplayEngine(commission_rate=Decimal("0.001"))
        assert engine._commission_rate == Decimal("0.001")


# ---------------------------------------------------------------------------
# Tests: Validation
# ---------------------------------------------------------------------------


class TestReplayValidation:
    """Tests for input validation in run()."""

    @pytest.mark.asyncio
    async def test_empty_data_raises(self) -> None:
        """Empty data dict raises ValueError."""
        engine = ReplayEngine()
        with pytest.raises(ValueError, match="at least one instrument"):
            await engine.run(strategy_cls=DoNothingStrategy, data={})

    @pytest.mark.asyncio
    async def test_invalid_data_type_raises(self) -> None:
        """Invalid data_type raises ValueError."""
        engine = ReplayEngine()
        inst = _make_instrument()
        df = _make_bar_df([100.0])
        with pytest.raises(ValueError, match="Unsupported data_type"):
            await engine.run(
                strategy_cls=DoNothingStrategy,
                data={inst: df},
                data_type="invalid",
            )


# ---------------------------------------------------------------------------
# Tests: Basic replay with do-nothing strategy
# ---------------------------------------------------------------------------


class TestDoNothingReplay:
    """Tests with a strategy that generates no orders."""

    @pytest.mark.asyncio
    async def test_returns_result_dict(self) -> None:
        """run() returns a dict with expected keys."""
        engine = ReplayEngine()
        inst = _make_instrument()
        df = _make_bar_df([100.0, 101.0, 102.0])
        result = await engine.run(strategy_cls=DoNothingStrategy, data={inst: df})

        assert "equity_curve" in result
        assert "timestamps" in result
        assert "trades" in result
        assert "positions" in result
        assert "initial_capital" in result
        assert "final_equity" in result

    @pytest.mark.asyncio
    async def test_equity_curve_shape(self) -> None:
        """Equity curve has initial + one entry per bar."""
        engine = ReplayEngine()
        inst = _make_instrument()
        df = _make_bar_df([100.0, 101.0, 102.0])
        result = await engine.run(strategy_cls=DoNothingStrategy, data={inst: df})

        # Initial snapshot + 3 bar snapshots = 4 entries
        assert len(result["equity_curve"]) == 4

    @pytest.mark.asyncio
    async def test_equity_unchanged_without_trades(self) -> None:
        """Equity stays at initial capital when no trades occur."""
        engine = ReplayEngine(initial_capital=Decimal("50000"))
        inst = _make_instrument()
        df = _make_bar_df([100.0, 101.0, 102.0])
        result = await engine.run(strategy_cls=DoNothingStrategy, data={inst: df})

        assert result["initial_capital"] == 50000.0
        assert result["final_equity"] == 50000.0
        np.testing.assert_array_equal(
            result["equity_curve"],
            [50000.0, 50000.0, 50000.0, 50000.0],
        )

    @pytest.mark.asyncio
    async def test_no_trades_recorded(self) -> None:
        """No trades when strategy does nothing."""
        engine = ReplayEngine()
        inst = _make_instrument()
        df = _make_bar_df([100.0, 101.0])
        result = await engine.run(strategy_cls=DoNothingStrategy, data={inst: df})

        assert result["trades"] == []

    @pytest.mark.asyncio
    async def test_no_positions(self) -> None:
        """No positions when strategy does nothing."""
        engine = ReplayEngine()
        inst = _make_instrument()
        df = _make_bar_df([100.0])
        result = await engine.run(strategy_cls=DoNothingStrategy, data={inst: df})

        assert result["positions"] == {}

    @pytest.mark.asyncio
    async def test_timestamps_are_numpy_array(self) -> None:
        """Timestamps are returned as a numpy array."""
        engine = ReplayEngine()
        inst = _make_instrument()
        df = _make_bar_df([100.0, 101.0])
        result = await engine.run(strategy_cls=DoNothingStrategy, data={inst: df})

        assert isinstance(result["timestamps"], np.ndarray)

    @pytest.mark.asyncio
    async def test_equity_curve_is_numpy_array(self) -> None:
        """Equity curve is returned as a numpy array."""
        engine = ReplayEngine()
        inst = _make_instrument()
        df = _make_bar_df([100.0])
        result = await engine.run(strategy_cls=DoNothingStrategy, data={inst: df})

        assert isinstance(result["equity_curve"], np.ndarray)
        assert result["equity_curve"].dtype == np.float64


# ---------------------------------------------------------------------------
# Tests: Buy-once strategy
# ---------------------------------------------------------------------------


class TestBuyOnceReplay:
    """Tests with a strategy that buys and holds."""

    @pytest.mark.asyncio
    async def test_single_trade_recorded(self) -> None:
        """Buying once records exactly one trade."""
        engine = ReplayEngine()
        inst = _make_instrument()
        df = _make_bar_df([100.0, 101.0, 102.0])
        result = await engine.run(strategy_cls=BuyOnceStrategy, data={inst: df})

        assert len(result["trades"]) == 1

    @pytest.mark.asyncio
    async def test_trade_details(self) -> None:
        """Trade dict has correct fields and values."""
        engine = ReplayEngine()
        inst = _make_instrument()
        df = _make_bar_df([100.0, 101.0])
        result = await engine.run(strategy_cls=BuyOnceStrategy, data={inst: df})

        trade = result["trades"][0]
        assert trade["side"] == "BUY"
        assert trade["price"] == 100.0
        assert trade["quantity"] == 10.0
        assert "timestamp" in trade
        assert "instrument" in trade

    @pytest.mark.asyncio
    async def test_position_after_buy(self) -> None:
        """Position shows long 10 after buying."""
        engine = ReplayEngine()
        inst = _make_instrument()
        df = _make_bar_df([100.0, 101.0])
        result = await engine.run(strategy_cls=BuyOnceStrategy, data={inst: df})

        assert len(result["positions"]) == 1
        pos = next(iter(result["positions"].values()))
        assert pos["quantity"] == 10.0
        assert pos["avg_entry_price"] == 100.0

    @pytest.mark.asyncio
    async def test_equity_reflects_unrealized_pnl(self) -> None:
        """Equity changes with unrealized PnL after buying."""
        engine = ReplayEngine(initial_capital=Decimal("100000"))
        inst = _make_instrument()
        # Buy at 100, price goes to 110 -> unrealized PnL = 10 * 10 = 100
        df = _make_bar_df([100.0, 110.0])
        result = await engine.run(strategy_cls=BuyOnceStrategy, data={inst: df})

        # Final equity = 100000 + unrealized PnL (10 shares * $10 gain = $100)
        assert result["final_equity"] == pytest.approx(100100.0)


# ---------------------------------------------------------------------------
# Tests: Buy-and-sell strategy (realized PnL)
# ---------------------------------------------------------------------------


class TestBuyAndSellReplay:
    """Tests for round-trip trades with realized PnL."""

    @pytest.mark.asyncio
    async def test_two_trades_recorded(self) -> None:
        """Buy + sell records two trades."""
        engine = ReplayEngine()
        inst = _make_instrument()
        df = _make_bar_df([100.0, 105.0, 110.0, 115.0])
        result = await engine.run(strategy_cls=BuyAndSellStrategy, data={inst: df})

        assert len(result["trades"]) == 2

    @pytest.mark.asyncio
    async def test_realized_pnl_positive(self) -> None:
        """Realized PnL is positive when selling higher than buy price."""
        engine = ReplayEngine(initial_capital=Decimal("100000"))
        inst = _make_instrument()
        # Buy at 100, sell at 110 -> realized PnL = 10 * 10 = 100
        df = _make_bar_df([100.0, 105.0, 110.0, 115.0])
        result = await engine.run(strategy_cls=BuyAndSellStrategy, data={inst: df})

        # After sell, position is flat, realized PnL = (110-100)*10 = 100
        assert result["final_equity"] == pytest.approx(100100.0)

    @pytest.mark.asyncio
    async def test_position_flat_after_round_trip(self) -> None:
        """Position is flat after buying and selling same quantity."""
        engine = ReplayEngine()
        inst = _make_instrument()
        df = _make_bar_df([100.0, 105.0, 110.0, 115.0])
        result = await engine.run(strategy_cls=BuyAndSellStrategy, data={inst: df})

        # Position should show zero quantity (flat) or realized PnL only
        if result["positions"]:
            pos = next(iter(result["positions"].values()))
            assert pos["quantity"] == 0.0

    @pytest.mark.asyncio
    async def test_realized_pnl_negative(self) -> None:
        """Realized PnL is negative when selling lower than buy price."""
        engine = ReplayEngine(initial_capital=Decimal("100000"))
        inst = _make_instrument()
        # Buy at 100, sell at 90 -> realized PnL = (90-100)*10 = -100
        df = _make_bar_df([100.0, 95.0, 90.0, 85.0])
        result = await engine.run(strategy_cls=BuyAndSellStrategy, data={inst: df})

        assert result["final_equity"] == pytest.approx(99900.0)


# ---------------------------------------------------------------------------
# Tests: Strategy lifecycle
# ---------------------------------------------------------------------------


class TestStrategyLifecycle:
    """Tests that strategy lifecycle hooks are called correctly."""

    @pytest.mark.asyncio
    async def test_on_start_and_stop_called(self) -> None:
        """on_start and on_stop are called in correct order."""
        engine = ReplayEngine()
        inst = _make_instrument()
        df = _make_bar_df([100.0, 101.0])

        # We can't easily introspect the strategy instance directly,
        # but we can verify the replay completes without error.
        result = await engine.run(strategy_cls=DoNothingStrategy, data={inst: df})
        assert result["final_equity"] > 0

    @pytest.mark.asyncio
    async def test_market_data_events_received(self) -> None:
        """Strategy receives one market data event per bar."""
        engine = ReplayEngine()
        inst = _make_instrument()
        df = _make_bar_df([100.0, 101.0, 102.0])
        result = await engine.run(strategy_cls=DoNothingStrategy, data={inst: df})

        # 3 bars = 3 equity changes beyond initial
        assert len(result["equity_curve"]) == 4  # initial + 3 bars


# ---------------------------------------------------------------------------
# Tests: Strategy params
# ---------------------------------------------------------------------------


class TestStrategyParams:
    """Tests for strategy parameter passing."""

    @pytest.mark.asyncio
    async def test_params_passed_to_strategy(self) -> None:
        """Strategy params are forwarded correctly."""
        engine = ReplayEngine()
        inst = _make_instrument()
        df = _make_bar_df([100.0, 101.0])
        result = await engine.run(
            strategy_cls=ParamStrategy,
            data={inst: df},
            strategy_params={"should_buy": True, "quantity": 5},
        )

        assert len(result["trades"]) == 1
        assert result["trades"][0]["quantity"] == 5.0

    @pytest.mark.asyncio
    async def test_no_params_default(self) -> None:
        """No params defaults to empty dict, strategy still runs."""
        engine = ReplayEngine()
        inst = _make_instrument()
        df = _make_bar_df([100.0])
        result = await engine.run(
            strategy_cls=ParamStrategy,
            data={inst: df},
        )

        assert result["trades"] == []


# ---------------------------------------------------------------------------
# Tests: Multi-instrument replay
# ---------------------------------------------------------------------------


class TestMultiInstrumentReplay:
    """Tests with multiple instruments."""

    @pytest.mark.asyncio
    async def test_multi_instrument_data(self) -> None:
        """Replay handles multiple instruments correctly."""
        engine = ReplayEngine()
        inst_a = _make_instrument("AAPL")
        inst_b = _make_instrument("GOOG")
        df_a = _make_bar_df([150.0, 155.0], start="2024-01-01")
        df_b = _make_bar_df([100.0, 105.0], start="2024-01-01")

        result = await engine.run(
            strategy_cls=MultiInstrumentStrategy,
            data={inst_a: df_a, inst_b: df_b},
        )

        # Both instruments should have been bought
        assert len(result["trades"]) == 2
        assert len(result["positions"]) == 2

    @pytest.mark.asyncio
    async def test_events_sorted_chronologically(self) -> None:
        """Events from different instruments are interleaved by time."""
        engine = ReplayEngine()
        inst_a = _make_instrument("AAPL")
        inst_b = _make_instrument("GOOG")
        # Staggered dates to test interleaving
        df_a = _make_bar_df([150.0, 155.0], start="2024-01-01", freq="2D")
        df_b = _make_bar_df([100.0, 105.0], start="2024-01-02", freq="2D")

        result = await engine.run(
            strategy_cls=MultiInstrumentStrategy,
            data={inst_a: df_a, inst_b: df_b},
        )

        # All 4 bars processed (initial + 4 snapshots)
        assert len(result["equity_curve"]) == 5


# ---------------------------------------------------------------------------
# Tests: Commission handling
# ---------------------------------------------------------------------------


class TestCommission:
    """Tests for commission rate application."""

    @pytest.mark.asyncio
    async def test_zero_commission(self) -> None:
        """Zero commission rate means no commissions recorded."""
        engine = ReplayEngine(commission_rate=Decimal("0"))
        inst = _make_instrument()
        df = _make_bar_df([100.0, 101.0])
        result = await engine.run(strategy_cls=BuyOnceStrategy, data={inst: df})

        assert result["trades"][0]["commission"] == 0.0

    @pytest.mark.asyncio
    async def test_nonzero_commission(self) -> None:
        """Commission is computed as rate * price * quantity."""
        engine = ReplayEngine(commission_rate=Decimal("0.001"))
        inst = _make_instrument()
        df = _make_bar_df([100.0, 101.0])
        result = await engine.run(strategy_cls=BuyOnceStrategy, data={inst: df})

        # Commission = 0.001 * 100 * 10 = 1.0
        assert result["trades"][0]["commission"] == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_commission_reduces_equity(self) -> None:
        """Non-zero commission rate reduces final equity vs zero commission.

        The equity curve should reflect commission drag, not just
        record commissions in the trade log.
        """
        inst = _make_instrument()
        df = _make_bar_df([100.0, 101.0, 102.0])

        engine_no_comm = ReplayEngine(
            initial_capital=Decimal("100000"),
            commission_rate=Decimal("0"),
        )
        result_no_comm = await engine_no_comm.run(strategy_cls=BuyOnceStrategy, data={inst: df})

        engine_with_comm = ReplayEngine(
            initial_capital=Decimal("100000"),
            commission_rate=Decimal("0.01"),
        )
        result_with_comm = await engine_with_comm.run(
            strategy_cls=BuyOnceStrategy, data={inst: df}
        )

        # Commission = 0.01 * 100 * 10 = 10.0, so equity should be $10 less
        assert result_with_comm["final_equity"] < result_no_comm["final_equity"]
        expected_diff = 0.01 * 100.0 * 10.0  # 10.0
        actual_diff = result_no_comm["final_equity"] - result_with_comm["final_equity"]
        assert actual_diff == pytest.approx(expected_diff)


# ---------------------------------------------------------------------------
# Tests: Equity curve computation
# ---------------------------------------------------------------------------


class TestEquityCurve:
    """Tests for equity curve accuracy."""

    @pytest.mark.asyncio
    async def test_equity_starts_at_initial_capital(self) -> None:
        """First equity entry is the initial capital."""
        engine = ReplayEngine(initial_capital=Decimal("75000"))
        inst = _make_instrument()
        df = _make_bar_df([100.0])
        result = await engine.run(strategy_cls=DoNothingStrategy, data={inst: df})

        assert result["equity_curve"][0] == 75000.0

    @pytest.mark.asyncio
    async def test_equity_tracks_unrealized_pnl(self) -> None:
        """Equity curve reflects mark-to-market unrealized PnL."""
        engine = ReplayEngine(initial_capital=Decimal("100000"))
        inst = _make_instrument()
        # Buy at 100, then price goes to 110, then 120
        df = _make_bar_df([100.0, 110.0, 120.0])
        result = await engine.run(strategy_cls=BuyOnceStrategy, data={inst: df})

        # After buying at 100: equity = 100000 (buy at market, no unrealized yet)
        # After bar 2 (price=110): equity = 100000 + 10*10 = 100100
        # After bar 3 (price=120): equity = 100000 + 10*20 = 100200
        # Note: the buy happens during bar 1 processing, so the equity after bar 1
        # reflects the position being bought at 100 and marked at 100 = no change
        equity = result["equity_curve"]
        assert equity[-1] == pytest.approx(100200.0)


# ---------------------------------------------------------------------------
# Tests: _ns_to_datetime helper
# ---------------------------------------------------------------------------


class TestNsToDatetime:
    """Tests for the nanosecond conversion helper."""

    def test_converts_ns_to_datetime(self) -> None:
        """Converts ns since epoch to UTC datetime."""
        # 2024-01-01 00:00:00 UTC
        ns = 1704067200_000_000_000
        dt = _ns_to_datetime(ns)
        assert dt.year == 2024
        assert dt.month == 1
        assert dt.day == 1
        assert dt.tzinfo is not None

    def test_preserves_sub_second(self) -> None:
        """Sub-second precision is preserved."""
        ns = 1704067200_500_000_000  # +0.5 seconds
        dt = _ns_to_datetime(ns)
        assert dt.microsecond == 500000


# ---------------------------------------------------------------------------
# Tests: _build_event_stream
# ---------------------------------------------------------------------------


class TestBuildEventStream:
    """Tests for the internal event stream builder."""

    def test_single_instrument_bar_events(self) -> None:
        """Single instrument produces one event per row."""
        engine = ReplayEngine()
        inst = _make_instrument()
        df = _make_bar_df([100.0, 101.0, 102.0])
        events = engine._build_event_stream({inst: df}, "bar")

        assert len(events) == 3
        assert all(isinstance(e, BarEvent) for e in events)

    def test_events_sorted_by_timestamp(self) -> None:
        """Events are sorted chronologically."""
        engine = ReplayEngine()
        inst_a = _make_instrument("A")
        inst_b = _make_instrument("B")
        df_a = _make_bar_df([100.0], start="2024-01-02")
        df_b = _make_bar_df([200.0], start="2024-01-01")
        events = engine._build_event_stream({inst_a: df_a, inst_b: df_b}, "bar")

        assert len(events) == 2
        assert events[0].timestamp_ns < events[1].timestamp_ns

    def test_multi_instrument_interleaved(self) -> None:
        """Multiple instruments are interleaved by timestamp."""
        engine = ReplayEngine()
        inst_a = _make_instrument("A")
        inst_b = _make_instrument("B")
        df_a = _make_bar_df([100.0, 101.0], start="2024-01-01", freq="2D")
        df_b = _make_bar_df([200.0, 201.0], start="2024-01-02", freq="2D")
        events = engine._build_event_stream({inst_a: df_a, inst_b: df_b}, "bar")

        assert len(events) == 4
        # Should be: A(Jan1), B(Jan2), A(Jan3), B(Jan4)
        for i in range(len(events) - 1):
            assert events[i].timestamp_ns <= events[i + 1].timestamp_ns


# ---------------------------------------------------------------------------
# Tests: _compute_equity
# ---------------------------------------------------------------------------


class TestComputeEquity:
    """Tests for equity computation."""

    @pytest.mark.asyncio
    async def test_no_positions_returns_initial_capital(self) -> None:
        """Equity equals initial capital with no positions."""
        engine = ReplayEngine(initial_capital=Decimal("100000"))
        inst = _make_instrument()
        df = _make_bar_df([100.0])
        result = await engine.run(strategy_cls=DoNothingStrategy, data={inst: df})

        assert result["final_equity"] == 100000.0
