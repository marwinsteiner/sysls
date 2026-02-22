"""Tests for sysls.backtest.optimize.

Tests cover parameter grid generation, grid search optimization,
time-series cross-validation splits, and walk-forward analysis.
"""

from __future__ import annotations

import numpy as np
import pytest

from sysls.backtest.metrics import BacktestResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_trending_prices(n: int = 100, start: float = 100.0) -> np.ndarray:
    """Create a simple upward-trending price series for testing."""
    rng = np.random.default_rng(42)
    returns = 0.001 + rng.normal(0, 0.01, n)
    prices = start * np.cumprod(1 + returns)
    return prices


def _simple_signal_func(
    prices: np.ndarray,
    *,
    threshold: float = 0.0,
) -> np.ndarray:
    """Signal function: long when return > threshold, else flat."""
    signals = np.zeros(len(prices), dtype=np.float64)
    for i in range(1, len(prices)):
        ret = (prices[i] - prices[i - 1]) / prices[i - 1]
        signals[i] = 1.0 if ret > threshold else 0.0
    return signals


def _dual_param_signal(
    prices: np.ndarray,
    *,
    fast: int = 2,
    slow: int = 5,
) -> np.ndarray:
    """MA crossover signal: long when fast MA > slow MA."""
    signals = np.zeros(len(prices), dtype=np.float64)
    for i in range(slow, len(prices)):
        fast_ma = np.mean(prices[max(0, i - fast + 1) : i + 1])
        slow_ma = np.mean(prices[max(0, i - slow + 1) : i + 1])
        signals[i] = 1.0 if fast_ma > slow_ma else -1.0
    return signals


# ---------------------------------------------------------------------------
# ParameterGrid tests
# ---------------------------------------------------------------------------


class TestParameterGrid:
    """Tests for ParameterGrid."""

    def test_basic_grid(self) -> None:
        """Two-param grid produces correct Cartesian product."""
        from sysls.backtest.optimize import ParameterGrid

        grid = ParameterGrid({"fast": [2, 3], "slow": [5, 10]})
        combos = list(grid)
        assert len(combos) == 4
        assert {"fast": 2, "slow": 5} in combos
        assert {"fast": 2, "slow": 10} in combos
        assert {"fast": 3, "slow": 5} in combos
        assert {"fast": 3, "slow": 10} in combos

    def test_single_param(self) -> None:
        """Single-parameter grid yields one dict per value."""
        from sysls.backtest.optimize import ParameterGrid

        grid = ParameterGrid({"threshold": [0.01, 0.02, 0.03]})
        combos = list(grid)
        assert len(combos) == 3
        assert combos[0] == {"threshold": 0.01}
        assert combos[1] == {"threshold": 0.02}
        assert combos[2] == {"threshold": 0.03}

    def test_empty_grid(self) -> None:
        """Grid with no parameters yields one empty dict."""
        from sysls.backtest.optimize import ParameterGrid

        grid = ParameterGrid({})
        combos = list(grid)
        assert combos == [{}]

    def test_single_value_per_param(self) -> None:
        """Grid with one value per param yields exactly one combo."""
        from sysls.backtest.optimize import ParameterGrid

        grid = ParameterGrid({"a": [1], "b": [2]})
        combos = list(grid)
        assert combos == [{"a": 1, "b": 2}]

    def test_length(self) -> None:
        """__len__ matches number of yielded combinations."""
        from sysls.backtest.optimize import ParameterGrid

        grid = ParameterGrid({"a": [1, 2, 3], "b": [4, 5]})
        assert len(grid) == 6
        assert len(grid) == len(list(grid))

    def test_iteration_multiple_times(self) -> None:
        """Grid can be iterated multiple times."""
        from sysls.backtest.optimize import ParameterGrid

        grid = ParameterGrid({"x": [1, 2]})
        first = list(grid)
        second = list(grid)
        assert first == second


# ---------------------------------------------------------------------------
# Pydantic model tests
# ---------------------------------------------------------------------------


class TestPydanticModels:
    """Tests for GridSearchResult, WalkForwardSplit, WalkForwardResult."""

    @staticmethod
    def _make_backtest_result() -> BacktestResult:
        """Create a minimal BacktestResult for model tests."""
        return BacktestResult(
            equity_curve=[100_000.0, 101_000.0, 102_000.0],
            returns=[0.01, 0.0099],
            trades=[],
            total_return=0.02,
            sharpe_ratio=1.5,
            sortino_ratio=2.0,
            max_drawdown=0.01,
            calmar_ratio=2.0,
            annualized_return=0.15,
            annualized_volatility=0.10,
            win_rate=0.0,
            profit_factor=0.0,
            total_trades=0,
            initial_capital=100_000.0,
            final_equity=102_000.0,
        )

    def test_grid_search_result_construction(self) -> None:
        """GridSearchResult can be constructed with valid data."""
        from sysls.backtest.optimize import GridSearchResult

        br = self._make_backtest_result()
        gsr = GridSearchResult(
            best_params={"fast": 2},
            best_score=1.5,
            all_results=[({"fast": 2}, br)],
        )
        assert gsr.best_params == {"fast": 2}
        assert gsr.best_score == 1.5
        assert len(gsr.all_results) == 1

    def test_grid_search_result_frozen(self) -> None:
        """GridSearchResult is immutable."""
        from sysls.backtest.optimize import GridSearchResult

        br = self._make_backtest_result()
        gsr = GridSearchResult(
            best_params={"fast": 2},
            best_score=1.5,
            all_results=[({"fast": 2}, br)],
        )
        with pytest.raises(Exception):  # noqa: B017
            gsr.best_score = 2.0  # type: ignore[misc]

    def test_walk_forward_split_construction(self) -> None:
        """WalkForwardSplit can be constructed with valid data."""
        from sysls.backtest.optimize import WalkForwardSplit

        br = self._make_backtest_result()
        wfs = WalkForwardSplit(
            split_index=0,
            train_start=0,
            train_end=70,
            oos_start=70,
            oos_end=100,
            best_params={"threshold": 0.01},
            oos_result=br,
        )
        assert wfs.split_index == 0
        assert wfs.train_end == 70
        assert wfs.oos_start == 70

    def test_walk_forward_result_construction(self) -> None:
        """WalkForwardResult can be constructed with valid data."""
        from sysls.backtest.optimize import WalkForwardResult

        br = self._make_backtest_result()
        wfr = WalkForwardResult(
            splits=[],
            combined_oos_equity=[100_000.0, 101_000.0],
            combined_metrics=br,
        )
        assert len(wfr.splits) == 0
        assert len(wfr.combined_oos_equity) == 2

    def test_serialization_round_trip(self) -> None:
        """Models survive JSON serialization round-trip."""
        from sysls.backtest.optimize import GridSearchResult

        br = self._make_backtest_result()
        gsr = GridSearchResult(
            best_params={"fast": 2, "slow": 5},
            best_score=1.5,
            all_results=[({"fast": 2, "slow": 5}, br)],
        )
        json_str = gsr.model_dump_json()
        reconstructed = GridSearchResult.model_validate_json(json_str)
        assert reconstructed.best_params == gsr.best_params
        assert reconstructed.best_score == gsr.best_score
        assert len(reconstructed.all_results) == len(gsr.all_results)


# ---------------------------------------------------------------------------
# grid_search tests
# ---------------------------------------------------------------------------


class TestGridSearch:
    """Tests for grid_search."""

    def test_basic_search(self) -> None:
        """Grid search returns valid result with correct structure."""
        from sysls.backtest.optimize import GridSearchResult, ParameterGrid, grid_search

        prices = _make_trending_prices(50)
        param_grid = ParameterGrid({"threshold": [0.0, 0.005, 0.01]})
        result = grid_search(prices, _simple_signal_func, param_grid)

        assert isinstance(result, GridSearchResult)
        assert "threshold" in result.best_params
        assert isinstance(result.best_score, float)
        assert len(result.all_results) == 3

    def test_best_params_highest_metric(self) -> None:
        """Best params correspond to the highest metric value."""
        from sysls.backtest.optimize import ParameterGrid, grid_search

        prices = _make_trending_prices(50)
        param_grid = ParameterGrid({"threshold": [0.0, 0.005, 0.01]})
        result = grid_search(prices, _simple_signal_func, param_grid, metric="sharpe_ratio")

        # Best should have highest sharpe among all results
        all_sharpes = [r.sharpe_ratio for _, r in result.all_results]
        assert result.best_score == all_sharpes[0]  # first after sort
        # Verify descending order
        for i in range(len(all_sharpes) - 1):
            assert all_sharpes[i] >= all_sharpes[i + 1]

    def test_max_drawdown_ascending(self) -> None:
        """When metric is max_drawdown, lower is better."""
        from sysls.backtest.optimize import ParameterGrid, grid_search

        prices = _make_trending_prices(50)
        param_grid = ParameterGrid({"threshold": [0.0, 0.005, 0.01]})
        result = grid_search(prices, _simple_signal_func, param_grid, metric="max_drawdown")

        # Sorted ascending: lowest drawdown first
        all_dd = [r.max_drawdown for _, r in result.all_results]
        for i in range(len(all_dd) - 1):
            assert all_dd[i] <= all_dd[i + 1]

    def test_single_param_grid(self) -> None:
        """Grid search works with a single parameter."""
        from sysls.backtest.optimize import ParameterGrid, grid_search

        prices = _make_trending_prices(50)
        param_grid = ParameterGrid({"threshold": [0.005]})
        result = grid_search(prices, _simple_signal_func, param_grid)

        assert len(result.all_results) == 1
        assert result.best_params == {"threshold": 0.005}

    def test_all_results_populated(self) -> None:
        """All parameter combinations appear in all_results."""
        from sysls.backtest.optimize import ParameterGrid, grid_search

        prices = _make_trending_prices(50)
        param_grid = ParameterGrid({"threshold": [0.0, 0.005, 0.01, 0.02]})
        result = grid_search(prices, _simple_signal_func, param_grid)

        result_params = [p for p, _ in result.all_results]
        for expected in [{"threshold": t} for t in [0.0, 0.005, 0.01, 0.02]]:
            assert expected in result_params

    def test_invalid_metric_raises(self) -> None:
        """Invalid metric name raises ValueError."""
        from sysls.backtest.optimize import ParameterGrid, grid_search

        prices = _make_trending_prices(50)
        param_grid = ParameterGrid({"threshold": [0.0]})
        with pytest.raises(ValueError, match="Invalid metric"):
            grid_search(prices, _simple_signal_func, param_grid, metric="not_a_metric")


# ---------------------------------------------------------------------------
# TimeSeriesSplit tests
# ---------------------------------------------------------------------------


class TestTimeSeriesSplit:
    """Tests for TimeSeriesSplit."""

    def test_basic_splits(self) -> None:
        """Splits generate correct expanding windows."""
        from sysls.backtest.optimize import TimeSeriesSplit

        splitter = TimeSeriesSplit(n_samples=100, n_splits=3, train_ratio=0.7)
        splits = list(splitter)
        assert len(splits) == 3

        # Each split is a 4-tuple of ints
        for train_start, train_end, oos_start, oos_end in splits:
            assert isinstance(train_start, int)
            assert isinstance(train_end, int)
            assert isinstance(oos_start, int)
            assert isinstance(oos_end, int)

    def test_expanding_window(self) -> None:
        """Training window starts at 0 and grows each split."""
        from sysls.backtest.optimize import TimeSeriesSplit

        splitter = TimeSeriesSplit(n_samples=100, n_splits=3, train_ratio=0.7)
        splits = list(splitter)

        # All training windows start at 0
        for train_start, _, _, _ in splits:
            assert train_start == 0

        # Training end grows monotonically
        train_ends = [te for _, te, _, _ in splits]
        for i in range(len(train_ends) - 1):
            assert train_ends[i] < train_ends[i + 1]

    def test_no_overlap(self) -> None:
        """Training and OOS windows do not overlap."""
        from sysls.backtest.optimize import TimeSeriesSplit

        splitter = TimeSeriesSplit(n_samples=100, n_splits=3, train_ratio=0.7)
        for _, train_end, oos_start, _ in splitter:
            assert train_end == oos_start  # contiguous, no gap/overlap

    def test_length(self) -> None:
        """__len__ returns the configured number of splits."""
        from sysls.backtest.optimize import TimeSeriesSplit

        splitter = TimeSeriesSplit(n_samples=100, n_splits=5, train_ratio=0.5)
        assert len(splitter) == 5

    def test_single_split(self) -> None:
        """A single split covers training + OOS correctly."""
        from sysls.backtest.optimize import TimeSeriesSplit

        splitter = TimeSeriesSplit(n_samples=100, n_splits=1, train_ratio=0.7)
        splits = list(splitter)
        assert len(splits) == 1

        train_start, train_end, oos_start, oos_end = splits[0]
        assert train_start == 0
        assert train_end == 70
        assert oos_start == 70
        assert oos_end == 100

    def test_coverage(self) -> None:
        """Last split's OOS extends to the end of the data."""
        from sysls.backtest.optimize import TimeSeriesSplit

        splitter = TimeSeriesSplit(n_samples=100, n_splits=3, train_ratio=0.7)
        splits = list(splitter)

        # Last split's OOS end must be n_samples
        _, _, _, last_oos_end = splits[-1]
        assert last_oos_end == 100

        # OOS windows are contiguous across splits
        for i in range(len(splits) - 1):
            _, _, _, oos_end_i = splits[i]
            _, _, oos_start_next, _ = splits[i + 1]
            assert oos_end_i == oos_start_next

    def test_too_short_data_raises(self) -> None:
        """Data too short for requested splits raises ValueError."""
        from sysls.backtest.optimize import TimeSeriesSplit

        with pytest.raises(ValueError, match="Data too short"):
            TimeSeriesSplit(n_samples=10, n_splits=10, train_ratio=0.9)


# ---------------------------------------------------------------------------
# walk_forward tests
# ---------------------------------------------------------------------------


class TestWalkForward:
    """Tests for walk_forward."""

    def test_basic_walk_forward(self) -> None:
        """Walk-forward produces valid result with correct split count."""
        from sysls.backtest.optimize import (
            ParameterGrid,
            WalkForwardResult,
            walk_forward,
        )

        prices = _make_trending_prices(100)
        param_grid = ParameterGrid({"threshold": [0.0, 0.005]})
        result = walk_forward(
            prices,
            _simple_signal_func,
            param_grid,
            n_splits=3,
            train_ratio=0.7,
        )

        assert isinstance(result, WalkForwardResult)
        assert len(result.splits) == 3
        assert len(result.combined_oos_equity) > 0

    def test_oos_equity_concatenation(self) -> None:
        """Combined OOS equity has entries from all splits."""
        from sysls.backtest.optimize import ParameterGrid, walk_forward

        prices = _make_trending_prices(100)
        param_grid = ParameterGrid({"threshold": [0.0, 0.005]})
        result = walk_forward(
            prices,
            _simple_signal_func,
            param_grid,
            n_splits=3,
            train_ratio=0.7,
        )

        # Total OOS equity points should equal sum of per-split equity lengths
        total_oos_points = sum(len(s.oos_result.equity_curve) for s in result.splits)
        assert len(result.combined_oos_equity) == total_oos_points

    def test_combined_metrics_populated(self) -> None:
        """Combined metrics are computed over the full OOS equity."""
        from sysls.backtest.optimize import ParameterGrid, walk_forward

        prices = _make_trending_prices(100)
        param_grid = ParameterGrid({"threshold": [0.0]})
        result = walk_forward(
            prices,
            _simple_signal_func,
            param_grid,
            n_splits=2,
            train_ratio=0.7,
        )

        metrics = result.combined_metrics
        assert metrics.initial_capital == 100_000.0
        assert len(metrics.equity_curve) == len(result.combined_oos_equity)
        assert isinstance(metrics.sharpe_ratio, float)

    def test_split_params_may_differ(self) -> None:
        """Different splits can select different best params."""
        from sysls.backtest.optimize import ParameterGrid, walk_forward

        # Use a signal that behaves differently on different data slices
        prices = _make_trending_prices(200)
        param_grid = ParameterGrid({"threshold": [0.0, 0.005, 0.01]})
        result = walk_forward(
            prices,
            _simple_signal_func,
            param_grid,
            n_splits=3,
            train_ratio=0.5,
        )

        # Each split has best_params (they may be same or different)
        for split in result.splits:
            assert "threshold" in split.best_params

    def test_invalid_n_splits_raises(self) -> None:
        """n_splits < 1 raises ValueError."""
        from sysls.backtest.optimize import ParameterGrid, walk_forward

        prices = _make_trending_prices(100)
        param_grid = ParameterGrid({"threshold": [0.0]})
        with pytest.raises(ValueError, match="n_splits must be >= 1"):
            walk_forward(
                prices,
                _simple_signal_func,
                param_grid,
                n_splits=0,
            )

    def test_single_split_walk_forward(self) -> None:
        """Walk-forward with a single split still works."""
        from sysls.backtest.optimize import ParameterGrid, walk_forward

        prices = _make_trending_prices(100)
        param_grid = ParameterGrid({"threshold": [0.0]})
        result = walk_forward(
            prices,
            _simple_signal_func,
            param_grid,
            n_splits=1,
            train_ratio=0.7,
        )
        assert len(result.splits) == 1
        assert result.splits[0].train_start == 0
        assert result.splits[0].oos_end == 100


# ---------------------------------------------------------------------------
# Edge case tests
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge case tests for the optimize module."""

    def test_grid_search_with_multi_param(self) -> None:
        """Grid search works with multiple parameters (MA crossover)."""
        from sysls.backtest.optimize import ParameterGrid, grid_search

        prices = _make_trending_prices(100)
        param_grid = ParameterGrid({"fast": [2, 3], "slow": [5, 10]})
        result = grid_search(prices, _dual_param_signal, param_grid)

        assert len(result.all_results) == 4
        assert "fast" in result.best_params
        assert "slow" in result.best_params

    def test_grid_search_total_return_metric(self) -> None:
        """Grid search can optimize on total_return metric."""
        from sysls.backtest.optimize import ParameterGrid, grid_search

        prices = _make_trending_prices(50)
        param_grid = ParameterGrid({"threshold": [0.0, 0.005, 0.01]})
        result = grid_search(prices, _simple_signal_func, param_grid, metric="total_return")

        all_returns = [r.total_return for _, r in result.all_results]
        # Sorted descending
        for i in range(len(all_returns) - 1):
            assert all_returns[i] >= all_returns[i + 1]

    def test_grid_search_with_costs(self) -> None:
        """Grid search passes commission and slippage through correctly."""
        from sysls.backtest.optimize import ParameterGrid, grid_search

        prices = _make_trending_prices(50)
        param_grid = ParameterGrid({"threshold": [0.0]})

        result_no_cost = grid_search(prices, _simple_signal_func, param_grid)
        result_with_cost = grid_search(
            prices,
            _simple_signal_func,
            param_grid,
            commission_rate=0.01,
            slippage_rate=0.005,
        )

        # With costs, final equity should be lower
        no_cost_equity = result_no_cost.all_results[0][1].final_equity
        with_cost_equity = result_with_cost.all_results[0][1].final_equity
        assert with_cost_equity < no_cost_equity

    def test_parameter_grid_three_params(self) -> None:
        """Grid with 3 parameters produces correct product."""
        from sysls.backtest.optimize import ParameterGrid

        grid = ParameterGrid({"a": [1, 2], "b": [3, 4], "c": [5, 6]})
        assert len(grid) == 8
        combos = list(grid)
        assert len(combos) == 8
        # Check one specific combo exists
        assert {"a": 1, "b": 3, "c": 5} in combos

    def test_time_series_split_invalid_train_ratio(self) -> None:
        """Invalid train_ratio raises ValueError."""
        from sysls.backtest.optimize import TimeSeriesSplit

        with pytest.raises(ValueError, match="train_ratio"):
            TimeSeriesSplit(n_samples=100, n_splits=3, train_ratio=0.0)
        with pytest.raises(ValueError, match="train_ratio"):
            TimeSeriesSplit(n_samples=100, n_splits=3, train_ratio=1.0)

    def test_walk_forward_equity_continuity(self) -> None:
        """Combined OOS equity is scaled so segments chain smoothly."""
        from sysls.backtest.optimize import ParameterGrid, walk_forward

        prices = _make_trending_prices(100)
        param_grid = ParameterGrid({"threshold": [0.0]})
        result = walk_forward(
            prices,
            _simple_signal_func,
            param_grid,
            n_splits=2,
            train_ratio=0.7,
        )

        # First point should equal initial_capital
        assert result.combined_oos_equity[0] == pytest.approx(100_000.0)

        # At the boundary between splits, equity should be continuous
        first_split_len = len(result.splits[0].oos_result.equity_curve)
        if first_split_len < len(result.combined_oos_equity):
            end_of_first = result.combined_oos_equity[first_split_len - 1]
            start_of_second = result.combined_oos_equity[first_split_len]
            # Should be approximately equal (scaled to chain)
            assert start_of_second == pytest.approx(end_of_first, rel=0.01)

    def test_walk_forward_combined_metrics_has_correct_length(self) -> None:
        """Combined metrics equity curve matches combined_oos_equity."""
        from sysls.backtest.optimize import ParameterGrid, walk_forward

        prices = _make_trending_prices(100)
        param_grid = ParameterGrid({"threshold": [0.0, 0.005]})
        result = walk_forward(
            prices,
            _simple_signal_func,
            param_grid,
            n_splits=3,
            train_ratio=0.7,
        )

        assert len(result.combined_metrics.equity_curve) == len(result.combined_oos_equity)
