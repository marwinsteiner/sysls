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

from sysls.backtest.metrics import BacktestResult
from sysls.backtest.vectorized import run_vectorized_backtest

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
    import numpy as np

    # Validate metric name against BacktestResult fields.
    if metric not in BacktestResult.model_fields:
        raise ValueError(
            f"Invalid metric {metric!r}. Must be one of: {sorted(BacktestResult.model_fields)}"
        )

    prices_arr = np.asarray(prices, dtype=np.float64)
    all_results: list[tuple[dict[str, Any], BacktestResult]] = []

    for params in param_grid:
        signals = signal_func(prices_arr, **params)
        result = run_vectorized_backtest(
            prices_arr,
            signals,
            initial_capital=initial_capital,
            commission_rate=commission_rate,
            slippage_rate=slippage_rate,
            periods_per_year=periods_per_year,
        )
        all_results.append((params, result))
        logger.debug("grid_search.evaluated", params=params, score=getattr(result, metric))

    # Sort: for max_drawdown lower is better, otherwise higher is better.
    reverse = metric != "max_drawdown"
    all_results.sort(key=lambda x: getattr(x[1], metric), reverse=reverse)

    best_params, best_result = all_results[0]
    best_score = float(getattr(best_result, metric))

    logger.info(
        "grid_search.complete",
        n_combos=len(all_results),
        best_params=best_params,
        best_score=best_score,
    )

    return GridSearchResult(
        best_params=best_params,
        best_score=best_score,
        all_results=all_results,
    )


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
        if n_splits < 1:
            raise ValueError(f"n_splits must be >= 1, got {n_splits}")
        if not 0.0 < train_ratio < 1.0:
            raise ValueError(f"train_ratio must be in (0, 1), got {train_ratio}")
        self._n_samples = n_samples
        self._n_splits = n_splits
        self._train_ratio = train_ratio

        # First train window ends at train_ratio of total data.
        # Remaining data is divided into n_splits equal OOS segments.
        self._first_train_end = int(n_samples * train_ratio)
        remaining = n_samples - self._first_train_end
        self._oos_step = remaining // n_splits

        if self._oos_step < 1:
            raise ValueError(
                f"Data too short for {n_splits} splits with "
                f"train_ratio={train_ratio}. Need at least "
                f"{self._first_train_end + n_splits} samples, got {n_samples}."
            )

    def __iter__(
        self,
    ) -> Iterator[tuple[int, int, int, int]]:
        """Yield ``(train_start, train_end, oos_start, oos_end)`` tuples."""
        for i in range(self._n_splits):
            train_start = 0
            train_end = self._first_train_end + i * self._oos_step
            oos_start = train_end
            # Last split absorbs any remainder from integer division.
            oos_end = self._n_samples if i == self._n_splits - 1 else oos_start + self._oos_step
            yield (train_start, train_end, oos_start, oos_end)

    def __len__(self) -> int:
        """Return the number of splits."""
        return self._n_splits


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
    import numpy as np

    from sysls.backtest.metrics import summarize_backtest

    prices_arr = np.asarray(prices, dtype=np.float64)
    n_samples = prices_arr.size

    splitter = TimeSeriesSplit(
        n_samples=n_samples,
        n_splits=n_splits,
        train_ratio=train_ratio,
    )

    splits: list[WalkForwardSplit] = []
    oos_equity_segments: list[np.ndarray] = []

    for idx, (train_start, train_end, oos_start, oos_end) in enumerate(splitter):
        train_prices = prices_arr[train_start:train_end]
        oos_prices = prices_arr[oos_start:oos_end]

        # Grid search on training data to find best params.
        train_result = grid_search(
            train_prices,
            signal_func,
            param_grid,
            initial_capital=initial_capital,
            commission_rate=commission_rate,
            slippage_rate=slippage_rate,
            metric=metric,
            periods_per_year=periods_per_year,
        )

        best_params = train_result.best_params

        # Backtest OOS data with the best params from training.
        oos_signals = signal_func(oos_prices, **best_params)
        oos_result = run_vectorized_backtest(
            oos_prices,
            oos_signals,
            initial_capital=initial_capital,
            commission_rate=commission_rate,
            slippage_rate=slippage_rate,
            periods_per_year=periods_per_year,
        )

        oos_equity_segments.append(np.array(oos_result.equity_curve, dtype=np.float64))

        splits.append(
            WalkForwardSplit(
                split_index=idx,
                train_start=train_start,
                train_end=train_end,
                oos_start=oos_start,
                oos_end=oos_end,
                best_params=best_params,
                oos_result=oos_result,
            )
        )

        logger.debug(
            "walk_forward.split_complete",
            split=idx,
            best_params=best_params,
            oos_return=oos_result.total_return,
        )

    # Concatenate OOS equity curves, chaining each segment's end
    # value as the next segment's starting capital.
    combined_equity_list: list[float] = []
    current_capital = initial_capital
    for segment in oos_equity_segments:
        if segment.size == 0:
            continue
        # Scale segment so it starts at current_capital.
        scale = current_capital / segment[0] if segment[0] != 0.0 else 1.0
        scaled = segment * scale
        combined_equity_list.extend(scaled.tolist())
        current_capital = float(scaled[-1])

    combined_equity_arr = np.array(combined_equity_list, dtype=np.float64)

    combined_metrics = summarize_backtest(
        equity_curve=combined_equity_arr,
        trades=[],  # Individual trades not tracked across splits
        initial_capital=initial_capital,
        periods_per_year=periods_per_year,
    )

    logger.info(
        "walk_forward.complete",
        n_splits=n_splits,
        combined_return=combined_metrics.total_return,
        combined_sharpe=combined_metrics.sharpe_ratio,
    )

    return WalkForwardResult(
        splits=splits,
        combined_oos_equity=combined_equity_list,
        combined_metrics=combined_metrics,
    )
