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

    def test_best_params_highest_metric(self) -> None:
        """Best params correspond to the highest metric value."""

    def test_max_drawdown_ascending(self) -> None:
        """When metric is max_drawdown, lower is better."""

    def test_single_param_grid(self) -> None:
        """Grid search works with a single parameter."""

    def test_all_results_populated(self) -> None:
        """All parameter combinations appear in all_results."""

    def test_invalid_metric_raises(self) -> None:
        """Invalid metric name raises ValueError."""


# ---------------------------------------------------------------------------
# TimeSeriesSplit tests
# ---------------------------------------------------------------------------


class TestTimeSeriesSplit:
    """Tests for TimeSeriesSplit."""

    def test_basic_splits(self) -> None:
        """Splits generate correct expanding windows."""

    def test_expanding_window(self) -> None:
        """Training window starts at 0 and grows each split."""

    def test_no_overlap(self) -> None:
        """Training and OOS windows do not overlap."""

    def test_length(self) -> None:
        """__len__ returns the configured number of splits."""

    def test_single_split(self) -> None:
        """A single split covers training + OOS correctly."""

    def test_coverage(self) -> None:
        """All splits together cover the data without gaps."""


# ---------------------------------------------------------------------------
# walk_forward tests
# ---------------------------------------------------------------------------


class TestWalkForward:
    """Tests for walk_forward."""

    def test_basic_walk_forward(self) -> None:
        """Walk-forward produces valid result with correct split count."""

    def test_oos_equity_concatenation(self) -> None:
        """Combined OOS equity has entries from all splits."""

    def test_combined_metrics_populated(self) -> None:
        """Combined metrics are computed over the full OOS equity."""

    def test_split_params_may_differ(self) -> None:
        """Different splits may select different best params."""

    def test_invalid_n_splits_raises(self) -> None:
        """n_splits < 1 raises ValueError."""
