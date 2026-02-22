"""Performance metrics computation for backtesting.

All metric functions are pure, stateless, and operate on numpy arrays
for maximum performance. No Python loops on the hot path.

Typical usage::

    import numpy as np
    from sysls.backtest.metrics import (
        compute_returns,
        sharpe_ratio,
        max_drawdown,
        summarize_backtest,
    )

    equity = np.array([100_000, 101_000, 99_500, 102_000, 103_500])
    returns = compute_returns(equity)
    sr = sharpe_ratio(returns)
    mdd = max_drawdown(equity)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    import numpy as np

# ---------------------------------------------------------------------------
# Trade / result models (Pydantic v2, frozen)
# ---------------------------------------------------------------------------


class TradeRecord(BaseModel, frozen=True):
    """Record of a completed round-trip trade.

    Attributes:
        instrument: Symbol string identifying the traded instrument.
        side: Entry side, either ``"BUY"`` or ``"SELL"``.
        entry_price: Price at which the position was entered.
        exit_price: Price at which the position was exited.
        quantity: Absolute size of the position.
        pnl: Realized profit/loss for this round-trip.
        entry_index: Bar index at which the entry occurred.
        exit_index: Bar index at which the exit occurred.
    """

    instrument: str
    side: str
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    entry_index: int
    exit_index: int


class BacktestResult(BaseModel, frozen=True):
    """Aggregated backtest results.

    Contains the full equity curve, trade log, and all computed
    performance metrics from a single backtest run.

    Attributes:
        equity_curve: Equity value at each time step.
        returns: Simple period returns at each time step.
        trades: List of completed round-trip trades.
        total_return: Cumulative return over the backtest period.
        sharpe_ratio: Annualized Sharpe ratio.
        sortino_ratio: Annualized Sortino ratio (downside deviation).
        max_drawdown: Maximum peak-to-trough drawdown as a positive fraction.
        calmar_ratio: Annualized return divided by maximum drawdown.
        annualized_return: Geometric annualized return.
        annualized_volatility: Annualized return volatility.
        win_rate: Fraction of trades with positive PnL.
        profit_factor: Gross profit divided by gross loss (``inf`` if no losses).
        total_trades: Number of completed round-trip trades.
        initial_capital: Starting capital.
        final_equity: Ending equity value.
    """

    model_config = ConfigDict(ser_json_inf_nan="constants")

    equity_curve: list[float]
    returns: list[float]
    trades: list[TradeRecord] = Field(default_factory=list)
    total_return: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: float
    calmar_ratio: float
    annualized_return: float
    annualized_volatility: float
    win_rate: float
    profit_factor: float
    total_trades: int
    initial_capital: float
    final_equity: float


# ---------------------------------------------------------------------------
# Return computation
# ---------------------------------------------------------------------------


def compute_returns(equity: np.ndarray) -> np.ndarray:
    """Compute simple period returns from an equity curve.

    Args:
        equity: 1-D array of equity values (must have length >= 1).

    Returns:
        Array of simple returns with length ``len(equity) - 1``.
        Returns an empty array if *equity* has fewer than 2 elements.
    """
    import numpy as np

    equity = np.asarray(equity, dtype=np.float64)
    if equity.size < 2:
        return np.empty(0, dtype=np.float64)
    # Avoid division by zero: where previous equity is 0, return 0.0
    prev = equity[:-1]
    safe_prev = np.where(prev == 0.0, 1.0, prev)
    rets = (equity[1:] - prev) / safe_prev
    rets = np.where(prev == 0.0, 0.0, rets)
    return rets


def compute_log_returns(equity: np.ndarray) -> np.ndarray:
    """Compute logarithmic period returns from an equity curve.

    Args:
        equity: 1-D array of equity values (must have length >= 1).

    Returns:
        Array of log returns with length ``len(equity) - 1``.
        Returns an empty array if *equity* has fewer than 2 elements.
    """
    import numpy as np

    equity = np.asarray(equity, dtype=np.float64)
    if equity.size < 2:
        return np.empty(0, dtype=np.float64)
    # Clamp to small positive to avoid log(0) or log(negative)
    safe = np.clip(equity, 1e-15, None)
    return np.log(safe[1:] / safe[:-1])


# ---------------------------------------------------------------------------
# Risk-adjusted return ratios
# ---------------------------------------------------------------------------


def sharpe_ratio(
    returns: np.ndarray,
    risk_free_rate: float = 0.0,
    periods_per_year: int = 252,
) -> float:
    """Compute the annualized Sharpe ratio.

    Args:
        returns: 1-D array of period returns.
        risk_free_rate: Risk-free rate per period (default 0).
        periods_per_year: Number of periods in a trading year.

    Returns:
        Annualized Sharpe ratio. Returns ``0.0`` if the standard
        deviation of excess returns is zero or if *returns* is empty.
    """
    import numpy as np

    returns = np.asarray(returns, dtype=np.float64)
    if returns.size == 0:
        return 0.0
    excess = returns - risk_free_rate
    std = float(np.std(excess, ddof=1)) if returns.size > 1 else 0.0
    if std == 0.0:
        return 0.0
    mean_excess = float(np.mean(excess))
    return (mean_excess / std) * np.sqrt(periods_per_year)


def sortino_ratio(
    returns: np.ndarray,
    risk_free_rate: float = 0.0,
    periods_per_year: int = 252,
) -> float:
    """Compute the annualized Sortino ratio.

    Uses downside deviation (root-mean-square of negative excess
    returns) instead of total volatility. This uses the population-
    based downside deviation (no Bessel correction), which is the
    standard convention for Sortino ratios.

    Args:
        returns: 1-D array of period returns.
        risk_free_rate: Risk-free rate per period (default 0).
        periods_per_year: Number of periods in a trading year.

    Returns:
        Annualized Sortino ratio. Returns ``0.0`` if the downside
        deviation is zero or if *returns* is empty.
    """
    import numpy as np

    returns = np.asarray(returns, dtype=np.float64)
    if returns.size == 0:
        return 0.0
    excess = returns - risk_free_rate
    downside = np.minimum(excess, 0.0)
    downside_std = float(np.sqrt(np.mean(downside**2)))
    if downside_std == 0.0:
        return 0.0
    mean_excess = float(np.mean(excess))
    return (mean_excess / downside_std) * np.sqrt(periods_per_year)


def calmar_ratio(
    returns: np.ndarray,
    equity: np.ndarray,
    periods_per_year: int = 252,
) -> float:
    """Compute the Calmar ratio (annualized return / max drawdown).

    Args:
        returns: 1-D array of period returns.
        equity: 1-D array of equity values.
        periods_per_year: Number of periods in a trading year.

    Returns:
        Calmar ratio. Returns ``0.0`` if max drawdown is zero or if
        either input is empty.
    """
    ann_ret = annualized_return(returns, periods_per_year=periods_per_year)
    mdd = max_drawdown(equity)
    if mdd == 0.0:
        return 0.0
    return ann_ret / mdd


# ---------------------------------------------------------------------------
# Drawdown metrics
# ---------------------------------------------------------------------------


def max_drawdown(equity: np.ndarray) -> float:
    """Compute the maximum peak-to-trough drawdown.

    Args:
        equity: 1-D array of equity values.

    Returns:
        Maximum drawdown as a positive fraction (e.g. ``0.1`` means 10%
        drawdown). Returns ``0.0`` for empty or single-element arrays.
    """
    dd = drawdown_series(equity)
    if dd.size == 0:
        return 0.0
    return float(dd.max())


def drawdown_series(equity: np.ndarray) -> np.ndarray:
    """Compute the running drawdown at each point.

    Drawdown is defined as ``(peak - equity) / peak`` so the values are
    non-negative fractions.

    Args:
        equity: 1-D array of equity values.

    Returns:
        Array of drawdown fractions, same length as *equity*.
        Returns an empty array for empty input.
    """
    import numpy as np

    equity = np.asarray(equity, dtype=np.float64)
    if equity.size == 0:
        return np.empty(0, dtype=np.float64)
    running_max = np.maximum.accumulate(equity)
    # Avoid division by zero where running_max is 0
    safe_max = np.where(running_max == 0.0, 1.0, running_max)
    dd = (running_max - equity) / safe_max
    dd = np.where(running_max == 0.0, 0.0, dd)
    return dd


# ---------------------------------------------------------------------------
# Return / volatility metrics
# ---------------------------------------------------------------------------


def total_return(equity: np.ndarray) -> float:
    """Compute total return over the entire equity curve.

    Args:
        equity: 1-D array of equity values.

    Returns:
        Total return as a fraction (e.g. ``0.15`` means 15% return).
        Returns ``0.0`` for empty or single-element arrays, or if
        initial equity is zero.
    """
    import numpy as np

    equity = np.asarray(equity, dtype=np.float64)
    if equity.size < 2:
        return 0.0
    initial = float(equity[0])
    if initial == 0.0:
        return 0.0
    return (float(equity[-1]) - initial) / initial


def annualized_return(
    returns: np.ndarray,
    periods_per_year: int = 252,
) -> float:
    """Compute the geometric annualized return.

    Args:
        returns: 1-D array of period returns.
        periods_per_year: Number of periods in a trading year.

    Returns:
        Annualized return as a fraction. Returns ``0.0`` for empty arrays.
    """
    import numpy as np

    returns = np.asarray(returns, dtype=np.float64)
    if returns.size == 0:
        return 0.0
    cumulative = float(np.prod(1.0 + returns))
    if cumulative <= 0.0:
        # Total loss scenario: cannot annualize meaningfully
        return -1.0
    n_periods = returns.size
    return cumulative ** (periods_per_year / n_periods) - 1.0


def annualized_volatility(
    returns: np.ndarray,
    periods_per_year: int = 252,
) -> float:
    """Compute annualized volatility of returns.

    Args:
        returns: 1-D array of period returns.
        periods_per_year: Number of periods in a trading year.

    Returns:
        Annualized volatility. Returns ``0.0`` for arrays with fewer
        than 2 elements.
    """
    import numpy as np

    returns = np.asarray(returns, dtype=np.float64)
    if returns.size < 2:
        return 0.0
    return float(np.std(returns, ddof=1)) * np.sqrt(periods_per_year)


# ---------------------------------------------------------------------------
# Trade-level metrics
# ---------------------------------------------------------------------------


def win_rate(pnl_per_trade: np.ndarray) -> float:
    """Compute the fraction of trades with positive PnL.

    Args:
        pnl_per_trade: 1-D array of PnL values per trade.

    Returns:
        Win rate as a fraction in [0.0, 1.0]. Returns ``0.0`` for empty
        arrays.
    """
    import numpy as np

    pnl = np.asarray(pnl_per_trade, dtype=np.float64)
    if pnl.size == 0:
        return 0.0
    return float(np.sum(pnl > 0.0)) / pnl.size


def profit_factor(pnl_per_trade: np.ndarray) -> float:
    """Compute the profit factor (gross profit / gross loss).

    Args:
        pnl_per_trade: 1-D array of PnL values per trade.

    Returns:
        Profit factor. Returns ``float('inf')`` when there are winning
        trades but no losing trades. Returns ``0.0`` for empty arrays
        or arrays with no winning trades.
    """
    import numpy as np

    pnl = np.asarray(pnl_per_trade, dtype=np.float64)
    if pnl.size == 0:
        return 0.0
    gross_profit = float(np.sum(pnl[pnl > 0.0]))
    gross_loss = float(np.abs(np.sum(pnl[pnl < 0.0])))
    if gross_loss == 0.0:
        return float("inf") if gross_profit > 0.0 else 0.0
    return gross_profit / gross_loss


# ---------------------------------------------------------------------------
# Convenience: build BacktestResult from arrays
# ---------------------------------------------------------------------------


def _safe_float(value: float, default: float = 0.0) -> float:
    """Sanitize a float value for JSON-safe storage.

    Replaces ``NaN`` and ``Inf`` with a finite default, since JSON
    cannot represent these values natively.

    Args:
        value: The float value to sanitize.
        default: Replacement for non-finite values.

    Returns:
        The original value if finite, otherwise *default*.
    """
    import math

    return value if math.isfinite(value) else default


def summarize_backtest(
    equity_curve: np.ndarray,
    trades: list[TradeRecord],
    initial_capital: float,
    periods_per_year: int = 252,
) -> BacktestResult:
    """Build a complete :class:`BacktestResult` from raw arrays and trades.

    Computes all performance metrics from the equity curve and trade log.

    Args:
        equity_curve: 1-D array of equity values at each time step.
        trades: List of completed round-trip trades.
        initial_capital: Starting capital.
        periods_per_year: Number of trading periods per year for
            annualization.

    Returns:
        A fully populated :class:`BacktestResult` instance.
    """
    import numpy as np

    equity_arr = np.asarray(equity_curve, dtype=np.float64)
    rets = compute_returns(equity_arr)

    pnl_arr = (
        np.array([t.pnl for t in trades], dtype=np.float64)
        if trades
        else np.empty(0, dtype=np.float64)
    )

    return BacktestResult(
        equity_curve=equity_arr.tolist(),
        returns=rets.tolist(),
        trades=trades,
        total_return=_safe_float(total_return(equity_arr)),
        sharpe_ratio=_safe_float(sharpe_ratio(rets, periods_per_year=periods_per_year)),
        sortino_ratio=_safe_float(sortino_ratio(rets, periods_per_year=periods_per_year)),
        max_drawdown=_safe_float(max_drawdown(equity_arr)),
        calmar_ratio=_safe_float(
            calmar_ratio(rets, equity_arr, periods_per_year=periods_per_year)
        ),
        annualized_return=_safe_float(annualized_return(rets, periods_per_year=periods_per_year)),
        annualized_volatility=_safe_float(
            annualized_volatility(rets, periods_per_year=periods_per_year)
        ),
        win_rate=_safe_float(win_rate(pnl_arr)),
        profit_factor=_safe_float(profit_factor(pnl_arr)),
        total_trades=len(trades),
        initial_capital=initial_capital,
        final_equity=float(equity_arr[-1]) if equity_arr.size > 0 else initial_capital,
    )
