"""Tests for sysls.backtest.vectorized.

Tests cover the vectorized backtester: position computation, equity
curves, trade extraction, cost modeling, and the top-level
``run_vectorized_backtest`` entry point.
"""

from __future__ import annotations

import numpy as np
import pytest

from sysls.backtest.metrics import BacktestResult
from sysls.backtest.vectorized import (
    compute_equity_curve,
    compute_positions,
    extract_trades,
    run_vectorized_backtest,
)

# ---------------------------------------------------------------------------
# compute_positions tests
# ---------------------------------------------------------------------------


class TestComputePositions:
    """Tests for compute_positions."""

    def test_basic_signals(self) -> None:
        """Signals of 1, -1, 0 are preserved."""
        signals = np.array([1, 0, -1, 1, 0])
        positions = compute_positions(signals)
        expected = np.array([1.0, 0.0, -1.0, 1.0, 0.0])
        np.testing.assert_array_equal(positions, expected)

    def test_clipping_out_of_range(self) -> None:
        """Values outside [-1, 1] are clipped."""
        signals = np.array([2.0, -3.0, 0.5, -0.5])
        positions = compute_positions(signals)
        expected = np.array([1.0, -1.0, 0.5, -0.5])
        np.testing.assert_array_equal(positions, expected)

    def test_empty_signals(self) -> None:
        """Empty input returns empty output."""
        positions = compute_positions(np.array([]))
        assert positions.size == 0

    def test_all_flat(self) -> None:
        """All-zero signals produce all-zero positions."""
        signals = np.zeros(5)
        positions = compute_positions(signals)
        np.testing.assert_array_equal(positions, np.zeros(5))

    def test_fractional_signals(self) -> None:
        """Fractional signal values within range are preserved."""
        signals = np.array([0.3, -0.7, 0.0, 1.0])
        positions = compute_positions(signals)
        np.testing.assert_allclose(positions, [0.3, -0.7, 0.0, 1.0])


# ---------------------------------------------------------------------------
# compute_equity_curve tests
# ---------------------------------------------------------------------------


class TestComputeEquityCurve:
    """Tests for compute_equity_curve."""

    def test_buy_and_hold(self) -> None:
        """Holding a long position tracks the underlying price."""
        prices = np.array([100.0, 110.0, 105.0, 115.0])
        positions = np.array([1.0, 1.0, 1.0, 1.0])
        equity = compute_equity_curve(prices, positions, initial_capital=100_000.0)
        # Position enters at bar 0. Returns: 0, +10%, -4.545%, +9.524%
        # Equity: 100k, 110k, 105k, 115k
        assert equity[0] == pytest.approx(100_000.0)
        assert equity[-1] == pytest.approx(115_000.0, rel=1e-6)

    def test_flat_position(self) -> None:
        """All-flat position keeps equity constant."""
        prices = np.array([100.0, 110.0, 90.0, 120.0])
        positions = np.zeros(4)
        equity = compute_equity_curve(prices, positions, initial_capital=50_000.0)
        np.testing.assert_allclose(equity, [50_000.0] * 4)

    def test_short_position(self) -> None:
        """Short position earns when price drops, loses when price rises."""
        prices = np.array([100.0, 90.0, 95.0])
        positions = np.array([-1.0, -1.0, -1.0])
        equity = compute_equity_curve(prices, positions, initial_capital=100_000.0)
        # Bar 0: equity = 100k (no return on first bar)
        # Bar 1: price drops 10% -> short earns 10% -> 110k
        # Bar 2: price rises 5.556% -> short loses -> 110k * (1 - 5/90)
        assert equity[0] == pytest.approx(100_000.0)
        assert equity[1] == pytest.approx(110_000.0, rel=1e-6)
        assert equity[2] < equity[1]  # Price went up, short loses

    def test_empty_prices(self) -> None:
        """Empty input returns empty output."""
        equity = compute_equity_curve(np.array([]), np.array([]))
        assert equity.size == 0

    def test_commission_reduces_equity(self) -> None:
        """Commissions reduce equity when positions change."""
        prices = np.array([100.0, 110.0, 105.0])
        positions = np.array([1.0, 1.0, 1.0])
        equity_no_cost = compute_equity_curve(prices, positions, initial_capital=100_000.0)
        equity_with_cost = compute_equity_curve(
            prices, positions, initial_capital=100_000.0, commission_rate=0.001
        )
        # Entry cost is deducted on bar 0
        assert equity_with_cost[0] < equity_no_cost[0]
        # Subsequent bars: no position change, no additional cost
        # But initial cost propagates through

    def test_slippage_reduces_equity(self) -> None:
        """Slippage reduces equity similar to commissions."""
        prices = np.array([100.0, 110.0, 105.0])
        positions = np.array([1.0, 0.0, 1.0])  # Trade on every bar
        equity_no_slip = compute_equity_curve(prices, positions, initial_capital=100_000.0)
        equity_with_slip = compute_equity_curve(
            prices, positions, initial_capital=100_000.0, slippage_rate=0.001
        )
        assert equity_with_slip[-1] < equity_no_slip[-1]

    def test_position_change_incurs_cost(self) -> None:
        """Costs are proportional to position change magnitude."""
        prices = np.array([100.0, 100.0, 100.0, 100.0])
        # Position goes: 0 -> 1 (change=1), 1 -> -1 (change=2), -1 -> 0 (change=1)
        positions = np.array([0.0, 1.0, -1.0, 0.0])
        equity = compute_equity_curve(
            prices, positions, initial_capital=100_000.0, commission_rate=0.01
        )
        # Flat prices so no P&L from returns, only costs
        # Bar 0: change=0 (enter flat), cost=0 -> 100k
        # Bar 1: change=1, cost=0.01 -> 100k * 0.99 = 99k
        # Bar 2: change=2, cost=0.02 -> 99k * 0.98
        # Bar 3: change=1, cost=0.01 -> 99k * 0.98 * 0.99
        assert equity[0] == pytest.approx(100_000.0)
        assert equity[1] == pytest.approx(99_000.0, rel=1e-6)
        assert equity[-1] < equity[1]  # More costs incurred

    def test_single_price(self) -> None:
        """Single price point returns initial capital."""
        equity = compute_equity_curve(np.array([100.0]), np.array([1.0]), initial_capital=50_000.0)
        assert equity.size == 1
        assert equity[0] == pytest.approx(50_000.0)


# ---------------------------------------------------------------------------
# extract_trades tests
# ---------------------------------------------------------------------------


class TestExtractTrades:
    """Tests for extract_trades."""

    def test_single_round_trip(self) -> None:
        """A simple long entry and exit produces one trade."""
        prices = np.array([100.0, 105.0, 110.0, 108.0])
        positions = np.array([0.0, 1.0, 1.0, 0.0])
        trades = extract_trades(prices, positions, instrument="AAPL")
        assert len(trades) == 1
        trade = trades[0]
        assert trade.instrument == "AAPL"
        assert trade.side == "BUY"
        assert trade.entry_price == 105.0
        assert trade.exit_price == 108.0
        assert trade.entry_index == 1
        assert trade.exit_index == 3
        assert trade.pnl == pytest.approx(3.0)

    def test_short_round_trip(self) -> None:
        """A short entry and exit produces correct PnL."""
        prices = np.array([100.0, 105.0, 100.0, 95.0])
        positions = np.array([0.0, -1.0, -1.0, 0.0])
        trades = extract_trades(prices, positions)
        assert len(trades) == 1
        trade = trades[0]
        assert trade.side == "SELL"
        assert trade.entry_price == 105.0
        assert trade.exit_price == 95.0
        assert trade.pnl == pytest.approx(10.0)  # Profit: sold at 105, bought at 95

    def test_position_flip(self) -> None:
        """Position flip from long to short generates two trades."""
        prices = np.array([100.0, 105.0, 110.0, 108.0])
        positions = np.array([1.0, 1.0, -1.0, -1.0])
        trades = extract_trades(prices, positions)
        assert len(trades) == 2
        # First trade: long from 100, closed at 110
        assert trades[0].side == "BUY"
        assert trades[0].entry_price == 100.0
        assert trades[0].exit_price == 110.0
        assert trades[0].pnl == pytest.approx(10.0)
        # Second trade: short still open, closed at end
        assert trades[1].side == "SELL"
        assert trades[1].entry_price == 110.0
        assert trades[1].exit_price == 108.0
        assert trades[1].pnl == pytest.approx(2.0)

    def test_open_trade_closed_at_end(self) -> None:
        """A trade still open at the end is closed at the last price."""
        prices = np.array([100.0, 105.0, 110.0])
        positions = np.array([1.0, 1.0, 1.0])
        trades = extract_trades(prices, positions)
        assert len(trades) == 1
        trade = trades[0]
        assert trade.entry_index == 0
        assert trade.exit_index == 2
        assert trade.exit_price == 110.0
        assert trade.pnl == pytest.approx(10.0)

    def test_no_trades(self) -> None:
        """All-flat positions produce no trades."""
        prices = np.array([100.0, 105.0, 110.0])
        positions = np.zeros(3)
        trades = extract_trades(prices, positions)
        assert len(trades) == 0

    def test_empty_arrays(self) -> None:
        """Empty input produces empty trade list."""
        trades = extract_trades(np.array([]), np.array([]))
        assert len(trades) == 0

    def test_multiple_round_trips(self) -> None:
        """Multiple entries and exits produce multiple trades."""
        prices = np.array([100.0, 105.0, 110.0, 108.0, 112.0, 115.0])
        positions = np.array([1.0, 1.0, 0.0, 1.0, 1.0, 0.0])
        trades = extract_trades(prices, positions)
        assert len(trades) == 2
        # First trade: buy at 100, sell at 110
        assert trades[0].entry_price == 100.0
        assert trades[0].exit_price == 110.0
        # Second trade: buy at 108, sell at 115
        assert trades[1].entry_price == 108.0
        assert trades[1].exit_price == 115.0

    def test_losing_trade(self) -> None:
        """A losing long trade has negative PnL."""
        prices = np.array([100.0, 105.0, 95.0])
        positions = np.array([1.0, 1.0, 0.0])
        trades = extract_trades(prices, positions)
        assert len(trades) == 1
        assert trades[0].pnl == pytest.approx(-5.0)

    def test_losing_short_trade(self) -> None:
        """A losing short trade has negative PnL."""
        prices = np.array([100.0, 105.0, 110.0])
        positions = np.array([-1.0, -1.0, 0.0])
        trades = extract_trades(prices, positions)
        assert len(trades) == 1
        assert trades[0].pnl == pytest.approx(-10.0)  # Sold at 100, bought at 110


# ---------------------------------------------------------------------------
# run_vectorized_backtest tests
# ---------------------------------------------------------------------------


class TestRunVectorizedBacktest:
    """Tests for the top-level run_vectorized_backtest function."""

    def test_basic_backtest(self) -> None:
        """A simple backtest produces a valid BacktestResult."""
        prices = np.array([100.0, 102.0, 101.0, 105.0, 103.0])
        signals = np.array([1, 1, -1, -1, 0])
        result = run_vectorized_backtest(prices, signals, initial_capital=100_000.0)

        assert isinstance(result, BacktestResult)
        assert result.initial_capital == 100_000.0
        assert len(result.equity_curve) == 5
        assert len(result.returns) == 4
        assert result.total_trades >= 1

    def test_buy_and_hold_returns(self) -> None:
        """Buy-and-hold produces expected total return."""
        prices = np.array([100.0, 110.0])
        signals = np.array([1, 1])
        result = run_vectorized_backtest(prices, signals, initial_capital=100_000.0)
        assert result.total_return == pytest.approx(0.10, rel=1e-6)
        assert result.final_equity == pytest.approx(110_000.0, rel=1e-6)

    def test_length_mismatch_raises(self) -> None:
        """Mismatched prices and signals raise ValueError."""
        prices = np.array([100.0, 110.0, 120.0])
        signals = np.array([1, 1])
        with pytest.raises(ValueError, match="same length"):
            run_vectorized_backtest(prices, signals)

    def test_empty_input_raises(self) -> None:
        """Empty arrays raise ValueError."""
        with pytest.raises(ValueError, match="must not be empty"):
            run_vectorized_backtest(np.array([]), np.array([]))

    def test_all_flat_preserves_capital(self) -> None:
        """No positions means equity stays at initial capital."""
        prices = np.array([100.0, 110.0, 90.0, 120.0])
        signals = np.zeros(4)
        result = run_vectorized_backtest(prices, signals, initial_capital=100_000.0)
        assert result.total_return == pytest.approx(0.0)
        assert result.final_equity == pytest.approx(100_000.0)
        assert result.total_trades == 0

    def test_with_commissions(self) -> None:
        """Commissions reduce final equity."""
        prices = np.array([100.0, 105.0, 110.0])
        signals = np.array([1, 1, 1])
        result_no_cost = run_vectorized_backtest(prices, signals, initial_capital=100_000.0)
        result_with_cost = run_vectorized_backtest(
            prices, signals, initial_capital=100_000.0, commission_rate=0.001
        )
        assert result_with_cost.final_equity < result_no_cost.final_equity

    def test_with_slippage(self) -> None:
        """Slippage reduces final equity."""
        prices = np.array([100.0, 105.0, 100.0, 105.0])
        signals = np.array([1, 0, 1, 0])  # Trade on/off
        result_no_slip = run_vectorized_backtest(prices, signals, initial_capital=100_000.0)
        result_with_slip = run_vectorized_backtest(
            prices, signals, initial_capital=100_000.0, slippage_rate=0.005
        )
        assert result_with_slip.final_equity < result_no_slip.final_equity

    def test_short_backtest(self) -> None:
        """Short-only strategy produces correct results."""
        prices = np.array([100.0, 95.0, 90.0, 85.0])
        signals = np.array([-1, -1, -1, -1])
        result = run_vectorized_backtest(prices, signals, initial_capital=100_000.0)
        # Price dropped 15% -> short earns ~15%
        assert result.total_return > 0.0
        assert result.final_equity > 100_000.0

    def test_custom_periods_per_year(self) -> None:
        """Custom periods_per_year is respected in annualization."""
        prices = np.array([100.0, 101.0, 102.0, 101.5, 103.0])
        signals = np.ones(5)
        result_daily = run_vectorized_backtest(prices, signals, periods_per_year=252)
        result_hourly = run_vectorized_backtest(prices, signals, periods_per_year=252 * 6)
        # Hourly annualization should amplify the annualized return
        # (same per-period return, more periods per year)
        assert result_hourly.annualized_return > result_daily.annualized_return

    def test_instrument_propagates(self) -> None:
        """Instrument name propagates to trade records."""
        prices = np.array([100.0, 110.0, 100.0])
        signals = np.array([1, 1, 0])
        result = run_vectorized_backtest(prices, signals, instrument="BTC-USDT")
        assert result.total_trades >= 1
        assert result.trades[0].instrument == "BTC-USDT"

    def test_metrics_are_populated(self) -> None:
        """All metric fields in BacktestResult are populated."""
        prices = np.array([100.0, 105.0, 98.0, 110.0, 107.0])
        signals = np.array([1, 1, -1, -1, 0])
        result = run_vectorized_backtest(prices, signals)

        # Just check that the fields are numeric (not NaN)
        assert np.isfinite(result.sharpe_ratio)
        assert np.isfinite(result.sortino_ratio)
        assert np.isfinite(result.max_drawdown)
        assert np.isfinite(result.calmar_ratio)
        assert np.isfinite(result.annualized_return)
        assert np.isfinite(result.annualized_volatility)
        assert np.isfinite(result.win_rate)
        assert np.isfinite(result.total_return)

    def test_serialization_round_trip(self) -> None:
        """BacktestResult from vectorized backtest can round-trip through JSON."""
        prices = np.array([100.0, 105.0, 103.0, 108.0])
        signals = np.array([1, 1, 0, 1])
        result = run_vectorized_backtest(prices, signals)
        json_str = result.model_dump_json()
        restored = BacktestResult.model_validate_json(json_str)
        assert restored.total_return == pytest.approx(result.total_return)
        assert restored.total_trades == result.total_trades

    def test_long_constant_signal(self) -> None:
        """Long signal on rising prices produces positive return."""
        # Simulate 50 bars of steady growth
        prices = 100.0 * np.cumprod(1.0 + np.full(50, 0.002))
        signals = np.ones(50)
        result = run_vectorized_backtest(prices, signals)
        assert result.total_return > 0.0
        assert result.sharpe_ratio > 0.0
        assert result.max_drawdown == pytest.approx(0.0)
