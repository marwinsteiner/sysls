"""Tests for sysls.backtest.metrics.

Tests cover all metric functions with known inputs/expected outputs,
edge cases (empty arrays, single element, all zeros, division by zero),
and the Pydantic model construction.
"""

from __future__ import annotations

import numpy as np
import pytest

from sysls.backtest.metrics import (
    BacktestResult,
    TradeRecord,
    annualized_return,
    annualized_volatility,
    calmar_ratio,
    compute_log_returns,
    compute_returns,
    drawdown_series,
    max_drawdown,
    profit_factor,
    sharpe_ratio,
    sortino_ratio,
    summarize_backtest,
    total_return,
    win_rate,
)
from sysls.core.types import Side

# ---------------------------------------------------------------------------
# TradeRecord model tests
# ---------------------------------------------------------------------------


class TestTradeRecord:
    """Tests for the TradeRecord Pydantic model."""

    def test_construction(self) -> None:
        """TradeRecord can be constructed with all required fields."""
        trade = TradeRecord(
            instrument="AAPL",
            side=Side.BUY,
            entry_price=150.0,
            exit_price=155.0,
            quantity=10.0,
            pnl=50.0,
            entry_index=0,
            exit_index=5,
        )
        assert trade.instrument == "AAPL"
        assert trade.side == "BUY"
        assert trade.pnl == 50.0

    def test_frozen(self) -> None:
        """TradeRecord is immutable."""
        trade = TradeRecord(
            instrument="AAPL",
            side=Side.BUY,
            entry_price=150.0,
            exit_price=155.0,
            quantity=10.0,
            pnl=50.0,
            entry_index=0,
            exit_index=5,
        )
        with pytest.raises(Exception):  # noqa: B017
            trade.pnl = 100.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# BacktestResult model tests
# ---------------------------------------------------------------------------


class TestBacktestResult:
    """Tests for the BacktestResult Pydantic model."""

    def test_construction(self) -> None:
        """BacktestResult can be constructed with all required fields."""
        result = BacktestResult(
            equity_curve=[100_000.0, 101_000.0],
            returns=[0.01],
            trades=[],
            total_return=0.01,
            sharpe_ratio=1.5,
            sortino_ratio=2.0,
            max_drawdown=0.05,
            calmar_ratio=0.3,
            annualized_return=0.1,
            annualized_volatility=0.15,
            win_rate=0.6,
            profit_factor=1.5,
            total_trades=0,
            initial_capital=100_000.0,
            final_equity=101_000.0,
        )
        assert result.total_return == 0.01
        assert result.total_trades == 0

    def test_frozen(self) -> None:
        """BacktestResult is immutable."""
        result = BacktestResult(
            equity_curve=[100_000.0],
            returns=[],
            total_return=0.0,
            sharpe_ratio=0.0,
            sortino_ratio=0.0,
            max_drawdown=0.0,
            calmar_ratio=0.0,
            annualized_return=0.0,
            annualized_volatility=0.0,
            win_rate=0.0,
            profit_factor=0.0,
            total_trades=0,
            initial_capital=100_000.0,
            final_equity=100_000.0,
        )
        with pytest.raises(Exception):  # noqa: B017
            result.total_return = 0.5  # type: ignore[misc]

    def test_with_trades(self) -> None:
        """BacktestResult stores a list of TradeRecord objects."""
        trade = TradeRecord(
            instrument="SPY",
            side=Side.BUY,
            entry_price=400.0,
            exit_price=410.0,
            quantity=5.0,
            pnl=50.0,
            entry_index=0,
            exit_index=10,
        )
        result = BacktestResult(
            equity_curve=[100_000.0, 100_050.0],
            returns=[0.0005],
            trades=[trade],
            total_return=0.0005,
            sharpe_ratio=0.5,
            sortino_ratio=0.7,
            max_drawdown=0.0,
            calmar_ratio=0.0,
            annualized_return=0.1,
            annualized_volatility=0.1,
            win_rate=1.0,
            profit_factor=0.0,
            total_trades=1,
            initial_capital=100_000.0,
            final_equity=100_050.0,
        )
        assert len(result.trades) == 1
        assert result.trades[0].pnl == 50.0


# ---------------------------------------------------------------------------
# compute_returns tests
# ---------------------------------------------------------------------------


class TestComputeReturns:
    """Tests for compute_returns."""

    def test_basic_returns(self) -> None:
        """Simple returns are computed correctly for a known sequence."""
        equity = np.array([100.0, 110.0, 105.0, 115.0])
        rets = compute_returns(equity)
        expected = np.array([0.10, -0.0454545454545, 0.0952380952381])
        np.testing.assert_allclose(rets, expected, rtol=1e-6)

    def test_empty_array(self) -> None:
        """Empty input returns empty output."""
        rets = compute_returns(np.array([]))
        assert rets.size == 0

    def test_single_element(self) -> None:
        """Single element returns empty output."""
        rets = compute_returns(np.array([100.0]))
        assert rets.size == 0

    def test_two_elements(self) -> None:
        """Two elements produce one return."""
        rets = compute_returns(np.array([100.0, 110.0]))
        np.testing.assert_allclose(rets, [0.10])

    def test_flat_equity(self) -> None:
        """Constant equity curve produces all-zero returns."""
        equity = np.array([100.0, 100.0, 100.0, 100.0])
        rets = compute_returns(equity)
        np.testing.assert_allclose(rets, [0.0, 0.0, 0.0])

    def test_zero_initial_equity(self) -> None:
        """Division by zero is handled gracefully when equity is zero."""
        equity = np.array([0.0, 100.0, 200.0])
        rets = compute_returns(equity)
        assert rets[0] == 0.0  # 0/0 case handled
        assert rets[1] == pytest.approx(1.0)  # 200/100 - 1


# ---------------------------------------------------------------------------
# compute_log_returns tests
# ---------------------------------------------------------------------------


class TestComputeLogReturns:
    """Tests for compute_log_returns."""

    def test_basic_log_returns(self) -> None:
        """Log returns match expected values for a known sequence."""
        equity = np.array([100.0, 110.0, 105.0])
        log_rets = compute_log_returns(equity)
        expected = np.log(np.array([110.0 / 100.0, 105.0 / 110.0]))
        np.testing.assert_allclose(log_rets, expected)

    def test_empty_array(self) -> None:
        """Empty input returns empty output."""
        assert compute_log_returns(np.array([])).size == 0

    def test_single_element(self) -> None:
        """Single element returns empty output."""
        assert compute_log_returns(np.array([100.0])).size == 0

    def test_handles_zero_equity(self) -> None:
        """Zero equity is clamped to avoid log(0)."""
        equity = np.array([0.0, 100.0])
        log_rets = compute_log_returns(equity)
        # Should not produce NaN or Inf
        assert np.all(np.isfinite(log_rets))


# ---------------------------------------------------------------------------
# sharpe_ratio tests
# ---------------------------------------------------------------------------


class TestSharpeRatio:
    """Tests for sharpe_ratio."""

    def test_empty_returns(self) -> None:
        """Empty returns produce Sharpe of 0.0."""
        assert sharpe_ratio(np.array([])) == 0.0

    def test_zero_std(self) -> None:
        """Constant positive returns produce Sharpe of 0.0 (no vol)."""
        # All the same return means std=0
        rets = np.array([0.01, 0.01, 0.01, 0.01])
        assert sharpe_ratio(rets) == 0.0

    def test_known_value(self) -> None:
        """Sharpe ratio for a known sequence matches manual calculation."""
        rets = np.array([0.01, 0.02, -0.005, 0.015, 0.008])
        mean_r = float(np.mean(rets))
        std_r = float(np.std(rets, ddof=1))
        expected = (mean_r / std_r) * np.sqrt(252)
        result = sharpe_ratio(rets, risk_free_rate=0.0, periods_per_year=252)
        assert result == pytest.approx(expected, rel=1e-10)

    def test_with_risk_free_rate(self) -> None:
        """Subtracting risk-free rate reduces the Sharpe ratio."""
        rets = np.array([0.01, 0.02, 0.015, 0.01, 0.012])
        sr_no_rf = sharpe_ratio(rets, risk_free_rate=0.0)
        sr_with_rf = sharpe_ratio(rets, risk_free_rate=0.005)
        assert sr_with_rf < sr_no_rf

    def test_single_return(self) -> None:
        """Single return cannot compute std; returns 0.0."""
        assert sharpe_ratio(np.array([0.01])) == 0.0

    def test_negative_returns(self) -> None:
        """All-negative returns produce a negative Sharpe ratio."""
        rets = np.array([-0.01, -0.02, -0.015, -0.005])
        assert sharpe_ratio(rets) < 0.0


# ---------------------------------------------------------------------------
# sortino_ratio tests
# ---------------------------------------------------------------------------


class TestSortinoRatio:
    """Tests for sortino_ratio."""

    def test_empty_returns(self) -> None:
        """Empty returns produce Sortino of 0.0."""
        assert sortino_ratio(np.array([])) == 0.0

    def test_all_positive_returns(self) -> None:
        """All positive returns produce Sortino of 0.0 (no downside)."""
        rets = np.array([0.01, 0.02, 0.015, 0.008])
        assert sortino_ratio(rets) == 0.0

    def test_known_value(self) -> None:
        """Sortino ratio for a known sequence matches manual calculation."""
        rets = np.array([0.01, -0.005, 0.02, -0.01, 0.015])
        excess = rets  # rf=0
        downside = np.minimum(excess, 0.0)
        downside_std = float(np.sqrt(np.mean(downside**2)))
        expected = (float(np.mean(excess)) / downside_std) * np.sqrt(252)
        result = sortino_ratio(rets)
        assert result == pytest.approx(expected, rel=1e-10)

    def test_sortino_ge_sharpe_for_mixed_returns(self) -> None:
        """Sortino is typically >= Sharpe for distributions with positive skew."""
        rets = np.array([0.01, 0.02, 0.03, -0.005, 0.015])
        sr = sharpe_ratio(rets)
        so = sortino_ratio(rets)
        # Sortino should be higher since we only penalize downside
        assert so >= sr


# ---------------------------------------------------------------------------
# max_drawdown / drawdown_series tests
# ---------------------------------------------------------------------------


class TestDrawdown:
    """Tests for max_drawdown and drawdown_series."""

    def test_no_drawdown(self) -> None:
        """Monotonically increasing equity has zero drawdown."""
        equity = np.array([100.0, 110.0, 120.0, 130.0])
        assert max_drawdown(equity) == 0.0

    def test_known_drawdown(self) -> None:
        """Drawdown for a known sequence matches manual calculation."""
        # Peak=120, trough=96, dd = (120-96)/120 = 0.2
        equity = np.array([100.0, 120.0, 96.0, 110.0])
        assert max_drawdown(equity) == pytest.approx(0.2, rel=1e-10)

    def test_drawdown_series_shape(self) -> None:
        """Drawdown series has same length as input."""
        equity = np.array([100.0, 110.0, 95.0, 105.0])
        dd = drawdown_series(equity)
        assert dd.shape == equity.shape

    def test_drawdown_series_values(self) -> None:
        """Drawdown series values are correct at each point."""
        equity = np.array([100.0, 110.0, 99.0, 115.0])
        dd = drawdown_series(equity)
        # Point 0: peak=100, dd=0
        # Point 1: peak=110, dd=0
        # Point 2: peak=110, dd=(110-99)/110 = 0.1
        # Point 3: peak=115, dd=0
        expected = np.array([0.0, 0.0, 11.0 / 110.0, 0.0])
        np.testing.assert_allclose(dd, expected)

    def test_empty_equity(self) -> None:
        """Empty equity produces zero max drawdown and empty series."""
        assert max_drawdown(np.array([])) == 0.0
        assert drawdown_series(np.array([])).size == 0

    def test_single_element(self) -> None:
        """Single element produces zero drawdown."""
        assert max_drawdown(np.array([100.0])) == 0.0

    def test_all_declining(self) -> None:
        """Strictly declining equity has drawdown at the last point."""
        equity = np.array([100.0, 90.0, 80.0, 70.0])
        expected_dd = (100.0 - 70.0) / 100.0
        assert max_drawdown(equity) == pytest.approx(expected_dd)


# ---------------------------------------------------------------------------
# total_return tests
# ---------------------------------------------------------------------------


class TestTotalReturn:
    """Tests for total_return."""

    def test_positive_return(self) -> None:
        """Positive total return."""
        equity = np.array([100.0, 115.0])
        assert total_return(equity) == pytest.approx(0.15)

    def test_negative_return(self) -> None:
        """Negative total return."""
        equity = np.array([100.0, 85.0])
        assert total_return(equity) == pytest.approx(-0.15)

    def test_zero_return(self) -> None:
        """Zero total return when equity unchanged."""
        equity = np.array([100.0, 100.0])
        assert total_return(equity) == pytest.approx(0.0)

    def test_empty_equity(self) -> None:
        """Empty equity returns 0.0."""
        assert total_return(np.array([])) == 0.0

    def test_single_element(self) -> None:
        """Single element returns 0.0."""
        assert total_return(np.array([100.0])) == 0.0

    def test_zero_initial(self) -> None:
        """Zero initial equity returns 0.0 to avoid division by zero."""
        equity = np.array([0.0, 100.0])
        assert total_return(equity) == 0.0


# ---------------------------------------------------------------------------
# annualized_return tests
# ---------------------------------------------------------------------------


class TestAnnualizedReturn:
    """Tests for annualized_return."""

    def test_empty_returns(self) -> None:
        """Empty returns produce 0.0."""
        assert annualized_return(np.array([])) == 0.0

    def test_known_value(self) -> None:
        """Annualized return for a known daily-return sequence."""
        # 252 days of 0.1% return each
        daily_ret = 0.001
        rets = np.full(252, daily_ret)
        # Cumulative = (1.001)^252, annualized over 1 year = same
        expected = (1.0 + daily_ret) ** 252 - 1.0
        result = annualized_return(rets, periods_per_year=252)
        assert result == pytest.approx(expected, rel=1e-8)

    def test_half_year_data(self) -> None:
        """Annualized return extrapolates correctly for partial year."""
        # 126 days of 0.04% return
        daily_ret = 0.0004
        rets = np.full(126, daily_ret)
        cumulative = (1.0 + daily_ret) ** 126
        expected = cumulative ** (252 / 126) - 1.0
        result = annualized_return(rets, periods_per_year=252)
        assert result == pytest.approx(expected, rel=1e-8)

    def test_total_loss(self) -> None:
        """Returns exceeding -100% produce annualized return of -1.0."""
        # Leveraged loss: cumulative product of (1 + r) <= 0
        rets = np.array([-1.5])  # Single period with -150% return
        result = annualized_return(rets, periods_per_year=252)
        assert result == -1.0


# ---------------------------------------------------------------------------
# annualized_volatility tests
# ---------------------------------------------------------------------------


class TestAnnualizedVolatility:
    """Tests for annualized_volatility."""

    def test_empty_returns(self) -> None:
        """Empty returns produce 0.0."""
        assert annualized_volatility(np.array([])) == 0.0

    def test_single_return(self) -> None:
        """Single return produces 0.0 (cannot compute std with ddof=1)."""
        assert annualized_volatility(np.array([0.01])) == 0.0

    def test_known_value(self) -> None:
        """Annualized volatility matches manual calculation."""
        rets = np.array([0.01, -0.005, 0.02, 0.0, -0.01])
        expected = float(np.std(rets, ddof=1)) * np.sqrt(252)
        result = annualized_volatility(rets)
        assert result == pytest.approx(expected, rel=1e-10)

    def test_zero_returns(self) -> None:
        """All-zero returns produce zero volatility."""
        rets = np.zeros(10)
        assert annualized_volatility(rets) == 0.0


# ---------------------------------------------------------------------------
# calmar_ratio tests
# ---------------------------------------------------------------------------


class TestCalmarRatio:
    """Tests for calmar_ratio."""

    def test_known_value(self) -> None:
        """Calmar ratio for a known scenario."""
        equity = np.array([100.0, 120.0, 96.0, 110.0])
        rets = compute_returns(equity)
        ann_ret = annualized_return(rets)
        mdd = max_drawdown(equity)
        expected = ann_ret / mdd
        result = calmar_ratio(rets, equity)
        assert result == pytest.approx(expected, rel=1e-10)

    def test_no_drawdown(self) -> None:
        """Zero drawdown returns 0.0 Calmar (avoid division by zero)."""
        equity = np.array([100.0, 110.0, 120.0])
        rets = compute_returns(equity)
        assert calmar_ratio(rets, equity) == 0.0

    def test_empty_inputs(self) -> None:
        """Empty inputs produce 0.0."""
        assert calmar_ratio(np.array([]), np.array([])) == 0.0


# ---------------------------------------------------------------------------
# win_rate tests
# ---------------------------------------------------------------------------


class TestWinRate:
    """Tests for win_rate."""

    def test_all_winners(self) -> None:
        """All positive PnL gives win rate 1.0."""
        pnl = np.array([10.0, 20.0, 5.0])
        assert win_rate(pnl) == pytest.approx(1.0)

    def test_all_losers(self) -> None:
        """All negative PnL gives win rate 0.0."""
        pnl = np.array([-10.0, -20.0, -5.0])
        assert win_rate(pnl) == pytest.approx(0.0)

    def test_mixed(self) -> None:
        """Mixed PnL computes correct fraction."""
        pnl = np.array([10.0, -5.0, 20.0, -15.0])
        assert win_rate(pnl) == pytest.approx(0.5)

    def test_empty(self) -> None:
        """Empty array returns 0.0."""
        assert win_rate(np.array([])) == 0.0

    def test_zero_pnl_trades(self) -> None:
        """Zero PnL trades are not counted as wins."""
        pnl = np.array([0.0, 0.0, 10.0])
        assert win_rate(pnl) == pytest.approx(1.0 / 3.0)


# ---------------------------------------------------------------------------
# profit_factor tests
# ---------------------------------------------------------------------------


class TestProfitFactor:
    """Tests for profit_factor."""

    def test_known_value(self) -> None:
        """Profit factor for a known sequence."""
        pnl = np.array([100.0, -50.0, 200.0, -25.0])
        # Gross profit = 300, gross loss = 75
        assert profit_factor(pnl) == pytest.approx(300.0 / 75.0)

    def test_no_losses(self) -> None:
        """All winners with no losses returns infinity."""
        pnl = np.array([10.0, 20.0, 30.0])
        assert profit_factor(pnl) == float("inf")

    def test_no_wins(self) -> None:
        """No wins returns 0.0."""
        pnl = np.array([-10.0, -20.0])
        assert profit_factor(pnl) == 0.0

    def test_empty(self) -> None:
        """Empty array returns 0.0."""
        assert profit_factor(np.array([])) == 0.0


# ---------------------------------------------------------------------------
# summarize_backtest tests
# ---------------------------------------------------------------------------


class TestSummarizeBacktest:
    """Tests for the summarize_backtest convenience function."""

    def test_basic_summary(self) -> None:
        """Summarize produces a complete BacktestResult."""
        equity = np.array([100_000.0, 101_000.0, 100_500.0, 102_000.0])
        trades = [
            TradeRecord(
                instrument="SPY",
                side=Side.BUY,
                entry_price=400.0,
                exit_price=410.0,
                quantity=5.0,
                pnl=50.0,
                entry_index=0,
                exit_index=2,
            ),
        ]
        result = summarize_backtest(equity, trades, initial_capital=100_000.0)

        assert isinstance(result, BacktestResult)
        assert result.total_trades == 1
        assert result.initial_capital == 100_000.0
        assert result.final_equity == 102_000.0
        assert result.total_return == pytest.approx(0.02)
        assert result.win_rate == pytest.approx(1.0)
        assert len(result.equity_curve) == 4
        assert len(result.returns) == 3

    def test_empty_trades(self) -> None:
        """Summary with no trades produces valid metrics."""
        equity = np.array([100_000.0, 100_000.0])
        result = summarize_backtest(equity, [], initial_capital=100_000.0)
        assert result.total_trades == 0
        assert result.win_rate == 0.0
        assert result.profit_factor == 0.0

    def test_empty_equity_curve(self) -> None:
        """Summarize handles an empty equity curve gracefully."""
        equity = np.array([])
        result = summarize_backtest(equity, [], initial_capital=100_000.0)
        assert result.total_trades == 0
        assert result.total_return == 0.0
        assert result.max_drawdown == 0.0
        assert result.final_equity == 100_000.0

    def test_serialization_round_trip(self) -> None:
        """BacktestResult can be serialized to JSON and back."""
        equity = np.array([100_000.0, 101_000.0])
        result = summarize_backtest(equity, [], initial_capital=100_000.0)
        json_str = result.model_dump_json()
        restored = BacktestResult.model_validate_json(json_str)
        assert restored.total_return == result.total_return
        assert restored.equity_curve == result.equity_curve
