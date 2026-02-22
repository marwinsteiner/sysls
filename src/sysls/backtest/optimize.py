"""Walk-forward analysis, grid search, and parameter optimization.

Provides tools for systematic parameter optimization of trading strategies
using the vectorized backtester.  All functions are pure computation --
no async code, no I/O.

Typical usage::

    import numpy as np
    from sysls.backtest.optimize import (
        ParameterGrid,
        grid_search,
        walk_forward,
    )

    prices = np.array([100.0, 102.0, 101.0, 105.0, 103.0, 107.0])

    def my_signal(prices, fast=2, slow=5):
        # simple moving-average crossover signal generator
        ...
        return signals

    param_grid = ParameterGrid({"fast": [2, 3], "slow": [5, 10]})
    result = grid_search(prices, my_signal, param_grid)
    print(result.best_params, result.best_score)
"""

from __future__ import annotations

import itertools
import math
from typing import TYPE_CHECKING, Any

import structlog
from pydantic import BaseModel, ConfigDict

from sysls.backtest.metrics import BacktestResult  # noqa: TC001 (Pydantic field type)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    import numpy as np

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# ParameterGrid
# ---------------------------------------------------------------------------


class ParameterGrid:
    """Generates all Cartesian-product combinations of parameter values.

    Takes a dictionary mapping parameter names to lists of candidate values
    and yields every combination as a ``dict[str, Any]``.

    Args:
        param_dict: Mapping of parameter names to lists of candidate values.

    Example::

        grid = ParameterGrid({"fast": [2, 3], "slow": [5, 10]})
        for combo in grid:
            print(combo)
        # {"fast": 2, "slow": 5}
        # {"fast": 2, "slow": 10}
        # {"fast": 3, "slow": 5}
        # {"fast": 3, "slow": 10}
    """

    def __init__(self, param_dict: dict[str, list[Any]]) -> None:
        self._keys = list(param_dict.keys())
        self._values = [list(v) for v in param_dict.values()]

    def __iter__(self) -> Iterator[dict[str, Any]]:
        """Yield each parameter combination as a dictionary."""
        if not self._keys:
            yield {}
            return
        for combo in itertools.product(*self._values):
            yield dict(zip(self._keys, combo, strict=True))

    def __len__(self) -> int:
        """Return the total number of parameter combinations."""
        if not self._keys:
            return 1
        return math.prod(len(v) for v in self._values)


# ---------------------------------------------------------------------------
# Pydantic result models (frozen)
# ---------------------------------------------------------------------------


class GridSearchResult(BaseModel, frozen=True):
    """Result of a grid search optimization.

    Attributes:
        best_params: Parameter combination that produced the best score.
        best_score: Value of the optimization metric for the best params.
        all_results: List of ``(params, BacktestResult)`` for every
            combination evaluated, sorted by score (best first).
    """

    model_config = ConfigDict(ser_json_inf_nan="constants")

    best_params: dict[str, Any]
    best_score: float
    all_results: list[tuple[dict[str, Any], BacktestResult]]


class WalkForwardSplit(BaseModel, frozen=True):
    """Result for a single walk-forward split.

    Attributes:
        split_index: Zero-based index of this split.
        train_start: Start index of the training window (inclusive).
        train_end: End index of the training window (exclusive).
        oos_start: Start index of the out-of-sample window (inclusive).
        oos_end: End index of the out-of-sample window (exclusive).
        best_params: Best parameters found during in-sample optimization.
        oos_result: Backtest result on the out-of-sample data.
    """

    split_index: int
    train_start: int
    train_end: int
    oos_start: int
    oos_end: int
    best_params: dict[str, Any]
    oos_result: BacktestResult


class WalkForwardResult(BaseModel, frozen=True):
    """Aggregated result of a walk-forward analysis.

    Attributes:
        splits: Per-split results with train/OOS boundaries and metrics.
        combined_oos_equity: Concatenated out-of-sample equity curves
            across all splits.
        combined_metrics: Performance metrics computed over the combined
            out-of-sample equity curve.
    """

    model_config = ConfigDict(ser_json_inf_nan="constants")

    splits: list[WalkForwardSplit]
    combined_oos_equity: list[float]
    combined_metrics: BacktestResult


# ---------------------------------------------------------------------------
# Grid search
# ---------------------------------------------------------------------------


def grid_search(
    prices: np.ndarray,
    signal_func: Callable[..., np.ndarray],
    param_grid: ParameterGrid,
    initial_capital: float = 100_000.0,
    commission_rate: float = 0.0,
    slippage_rate: float = 0.0,
    metric: str = "sharpe_ratio",
    periods_per_year: int = 252,
) -> GridSearchResult:
    """Run a grid search over parameter combinations.

    Evaluates every combination in *param_grid* by calling *signal_func*
    with the candidate parameters, running a vectorized backtest, and
    ranking by the chosen *metric*.

    Args:
        prices: 1-D array of asset prices.
        signal_func: Callable with signature
            ``signal_func(prices, **params) -> signals``.
        param_grid: :class:`ParameterGrid` of candidate parameter values.
        initial_capital: Starting capital for each backtest.
        commission_rate: Commission rate per trade.
        slippage_rate: Slippage rate per trade.
        metric: Name of the :class:`BacktestResult` attribute to optimize.
            For ``"max_drawdown"``, lower is better; for all other metrics,
            higher is better.
        periods_per_year: Annualization factor.

    Returns:
        A :class:`GridSearchResult` with the best parameters, best score,
        and all evaluated results sorted by score.

    Raises:
        ValueError: If *param_grid* is empty or *metric* is not a valid
            :class:`BacktestResult` attribute.
    """
    raise NotImplementedError


# ---------------------------------------------------------------------------
# Time-series cross-validation splits
# ---------------------------------------------------------------------------


class TimeSeriesSplit:
    """Generate expanding-window time-series train/test splits.

    The training window always starts at index 0 and expands with each
    split.  The out-of-sample (test) window immediately follows the
    training window.

    Args:
        n_samples: Total number of data points.
        n_splits: Number of train/test splits to generate.
        train_ratio: Fraction of the total data allocated to training
            in the *first* split. The training window grows with each
            subsequent split.
    """

    def __init__(
        self,
        n_samples: int,
        n_splits: int,
        train_ratio: float = 0.7,
    ) -> None:
        raise NotImplementedError

    def __iter__(
        self,
    ) -> Iterator[tuple[int, int, int, int]]:
        """Yield ``(train_start, train_end, oos_start, oos_end)`` tuples."""
        raise NotImplementedError

    def __len__(self) -> int:
        """Return the number of splits."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Walk-forward analysis
# ---------------------------------------------------------------------------


def walk_forward(
    prices: np.ndarray,
    signal_func: Callable[..., np.ndarray],
    param_grid: ParameterGrid,
    n_splits: int = 5,
    train_ratio: float = 0.7,
    initial_capital: float = 100_000.0,
    commission_rate: float = 0.0,
    slippage_rate: float = 0.0,
    metric: str = "sharpe_ratio",
    periods_per_year: int = 252,
) -> WalkForwardResult:
    """Run walk-forward analysis with expanding training windows.

    For each split the data is divided into a training (in-sample) window
    and an out-of-sample (OOS) window.  A grid search is performed on the
    training data to find the best parameters, which are then used to
    backtest the OOS window.  The OOS equity curves are concatenated to
    produce a combined performance estimate.

    Args:
        prices: 1-D array of asset prices.
        signal_func: Callable with signature
            ``signal_func(prices, **params) -> signals``.
        param_grid: :class:`ParameterGrid` of candidate parameter values.
        n_splits: Number of expanding-window splits.
        train_ratio: Fraction of total data for training in the first
            split.
        initial_capital: Starting capital for each backtest.
        commission_rate: Commission rate per trade.
        slippage_rate: Slippage rate per trade.
        metric: :class:`BacktestResult` attribute to optimize.
        periods_per_year: Annualization factor.

    Returns:
        A :class:`WalkForwardResult` with per-split results, concatenated
        OOS equity, and combined metrics.

    Raises:
        ValueError: If *n_splits* < 1 or data is too short to split.
    """
    raise NotImplementedError
