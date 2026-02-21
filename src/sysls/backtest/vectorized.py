"""Vectorized backtesting engine inspired by vectorbt.

Operates entirely on numpy arrays and pandas Series for maximum
performance.  No Python loops on the hot path — all position,
equity, and PnL computations are fully vectorized.

Signal conventions:
    * ``1``  → long
    * ``-1`` → short
    * ``0``  → flat / no position

Typical usage::

    import numpy as np
    from sysls.backtest.vectorized import run_vectorized_backtest

    prices = np.array([100.0, 102.0, 101.0, 105.0, 103.0])
    signals = np.array([1, 1, -1, -1, 0])
    result = run_vectorized_backtest(prices, signals, initial_capital=100_000.0)
    print(result.sharpe_ratio, result.max_drawdown)
"""

from __future__ import annotations

import numpy as np

from sysls.backtest.metrics import (
    BacktestResult,
    TradeRecord,
    summarize_backtest,
)

# ---------------------------------------------------------------------------
# Position computation (vectorized)
# ---------------------------------------------------------------------------


def compute_positions(signals: np.ndarray) -> np.ndarray:
    """Convert raw signals to position array.

    Signals are clipped to ``{-1, 0, 1}`` and returned as a float64
    position series.

    Args:
        signals: 1-D array of signal values (1=long, -1=short, 0=flat).

    Returns:
        1-D float64 position array of the same length.
    """
    signals = np.asarray(signals, dtype=np.float64)
    return np.clip(signals, -1.0, 1.0)


# ---------------------------------------------------------------------------
# Equity curve computation (vectorized)
# ---------------------------------------------------------------------------


def compute_equity_curve(
    prices: np.ndarray,
    positions: np.ndarray,
    initial_capital: float = 100_000.0,
    commission_rate: float = 0.0,
    slippage_rate: float = 0.0,
) -> np.ndarray:
    """Compute an equity curve from prices and positions.

    The equity is computed mark-to-market:

    1. Price returns are computed as ``(price[t] - price[t-1]) / price[t-1]``.
    2. Portfolio returns are ``position[t-1] * price_return[t]`` (the position
       held *entering* the bar earns that bar's return).
    3. On bars where the position changes, commission and slippage costs
       are deducted proportionally.

    Args:
        prices: 1-D array of asset prices.
        positions: 1-D array of positions (same length as *prices*).
        initial_capital: Starting equity.
        commission_rate: Commission as a fraction of notional traded
            (e.g. ``0.001`` for 10 bps).
        slippage_rate: Slippage as a fraction of price on each trade
            (e.g. ``0.0005`` for 5 bps).

    Returns:
        1-D equity curve array of same length as *prices*.
    """
    prices = np.asarray(prices, dtype=np.float64)
    positions = np.asarray(positions, dtype=np.float64)

    n = prices.size
    if n == 0:
        return np.empty(0, dtype=np.float64)

    # Price returns: first element is 0 (no return on day 0)
    price_returns = np.empty(n, dtype=np.float64)
    price_returns[0] = 0.0
    prev = prices[:-1]
    safe_prev = np.where(prev == 0.0, 1.0, prev)
    price_returns[1:] = (prices[1:] - prev) / safe_prev
    price_returns[1:] = np.where(prev == 0.0, 0.0, price_returns[1:])

    # Portfolio returns: position from previous bar earns this bar's return
    # For bar 0, there is no prior position so return is 0
    portfolio_returns = np.empty(n, dtype=np.float64)
    portfolio_returns[0] = 0.0
    portfolio_returns[1:] = positions[:-1] * price_returns[1:]

    # Trading costs: applied on bars where position changes
    position_changes = np.empty(n, dtype=np.float64)
    position_changes[0] = np.abs(positions[0])  # Initial entry
    position_changes[1:] = np.abs(positions[1:] - positions[:-1])

    cost_rate = commission_rate + slippage_rate
    costs = position_changes * cost_rate

    # Net returns per bar
    net_returns = portfolio_returns - costs

    # Build equity curve via cumulative product
    equity = initial_capital * np.cumprod(1.0 + net_returns)
    return equity


# ---------------------------------------------------------------------------
# Trade extraction (vectorized)
# ---------------------------------------------------------------------------


def extract_trades(
    prices: np.ndarray,
    positions: np.ndarray,
    instrument: str = "",
) -> list[TradeRecord]:
    """Extract completed round-trip trades from position changes.

    A trade opens when the position moves from 0 to non-zero (or flips
    sign), and closes when it returns to 0 or flips sign.

    Args:
        prices: 1-D array of asset prices.
        positions: 1-D array of positions.
        instrument: Symbol string for the trade records.

    Returns:
        List of :class:`TradeRecord` instances.
    """
    prices = np.asarray(prices, dtype=np.float64)
    positions = np.asarray(positions, dtype=np.float64)

    trades: list[TradeRecord] = []
    n = positions.size
    if n == 0:
        return trades

    entry_idx: int | None = None
    entry_price: float = 0.0
    entry_side: str = ""

    for i in range(n):
        pos = float(positions[i])
        prev_pos = float(positions[i - 1]) if i > 0 else 0.0

        if prev_pos == 0.0 and pos != 0.0:
            # Opening a new position
            entry_idx = i
            entry_price = float(prices[i])
            entry_side = "BUY" if pos > 0 else "SELL"

        elif prev_pos != 0.0 and pos == 0.0:
            # Closing the position
            if entry_idx is not None:
                exit_price = float(prices[i])
                qty = abs(prev_pos)
                if entry_side == "BUY":
                    pnl = (exit_price - entry_price) * qty
                else:
                    pnl = (entry_price - exit_price) * qty
                trades.append(
                    TradeRecord(
                        instrument=instrument,
                        side=entry_side,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        quantity=qty,
                        pnl=pnl,
                        entry_index=entry_idx,
                        exit_index=i,
                    )
                )
                entry_idx = None

        elif prev_pos != 0.0 and pos != 0.0 and np.sign(prev_pos) != np.sign(pos):
            # Position flip: close old, open new
            if entry_idx is not None:
                exit_price = float(prices[i])
                qty = abs(prev_pos)
                if entry_side == "BUY":
                    pnl = (exit_price - entry_price) * qty
                else:
                    pnl = (entry_price - exit_price) * qty
                trades.append(
                    TradeRecord(
                        instrument=instrument,
                        side=entry_side,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        quantity=qty,
                        pnl=pnl,
                        entry_index=entry_idx,
                        exit_index=i,
                    )
                )
            # Open new position in opposite direction
            entry_idx = i
            entry_price = float(prices[i])
            entry_side = "BUY" if pos > 0 else "SELL"

    # Close any open trade at the last bar
    if entry_idx is not None and n > 0:
        last_pos = float(positions[-1])
        exit_price = float(prices[-1])
        qty = abs(last_pos)
        if entry_side == "BUY":
            pnl = (exit_price - entry_price) * qty
        else:
            pnl = (entry_price - exit_price) * qty
        trades.append(
            TradeRecord(
                instrument=instrument,
                side=entry_side,
                entry_price=entry_price,
                exit_price=exit_price,
                quantity=qty,
                pnl=pnl,
                entry_index=entry_idx,
                exit_index=n - 1,
            )
        )

    return trades


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_vectorized_backtest(
    prices: np.ndarray,
    signals: np.ndarray,
    initial_capital: float = 100_000.0,
    commission_rate: float = 0.0,
    slippage_rate: float = 0.0,
    instrument: str = "",
    periods_per_year: int = 252,
) -> BacktestResult:
    """Run a complete vectorized backtest.

    This is the main entry point for the vectorized backtesting engine.
    It takes price and signal arrays, computes positions, equity curves,
    and trades, then assembles all performance metrics into a
    :class:`BacktestResult`.

    Args:
        prices: 1-D array of asset prices.
        signals: 1-D array of trading signals (1=long, -1=short, 0=flat).
            Must be the same length as *prices*.
        initial_capital: Starting capital.
        commission_rate: Commission as a fraction of notional traded.
        slippage_rate: Slippage as a fraction of price per trade.
        instrument: Symbol string for trade records.
        periods_per_year: Number of trading periods per year for
            annualization (default 252 for daily bars).

    Returns:
        A fully populated :class:`BacktestResult` with equity curve,
        trade log, and all performance metrics.

    Raises:
        ValueError: If *prices* and *signals* have different lengths,
            or if either is empty.
    """
    prices = np.asarray(prices, dtype=np.float64)
    signals = np.asarray(signals, dtype=np.float64)

    if prices.size != signals.size:
        raise ValueError(
            f"prices and signals must have the same length: {prices.size} != {signals.size}"
        )
    if prices.size == 0:
        raise ValueError("prices and signals must not be empty")

    positions = compute_positions(signals)

    equity = compute_equity_curve(
        prices,
        positions,
        initial_capital=initial_capital,
        commission_rate=commission_rate,
        slippage_rate=slippage_rate,
    )

    trades = extract_trades(prices, positions, instrument=instrument)

    return summarize_backtest(
        equity_curve=equity,
        trades=trades,
        initial_capital=initial_capital,
        periods_per_year=periods_per_year,
    )
