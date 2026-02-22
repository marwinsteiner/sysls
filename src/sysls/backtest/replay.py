"""Event-driven historical replay engine for backtesting.

The replay engine creates a sandboxed environment with a SimulatedClock,
EventBus, OMS, and PaperVenue, then feeds historical data through the full
live stack for realistic simulation. Strategies receive events through the
standard ``on_market_data`` lifecycle, just as they would in production.

Usage::

    engine = ReplayEngine(initial_capital=Decimal("100000"))
    result = await engine.run(
        strategy_cls=MyStrategy,
        data={instrument: bars_df},
    )
    print(result["final_equity"])
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
import structlog

from sysls.core.bus import EventBus
from sysls.core.clock import SimulatedClock
from sysls.core.events import (
    BarEvent,
    FillEvent,
    MarketDataEvent,
    OrderSubmitted,
    PositionEvent,
    TradeEvent,
)
from sysls.core.types import OrderRequest, OrderType
from sysls.data.normalize import bars_to_events, trades_to_events
from sysls.execution.oms import OrderManagementSystem
from sysls.execution.paper import PaperVenue
from sysls.strategy.base import Strategy, StrategyContext

if TYPE_CHECKING:
    from datetime import datetime

    from sysls.core.types import Instrument

logger = structlog.get_logger(__name__)


def _ns_to_datetime(ns: int) -> datetime:
    """Convert nanoseconds since epoch to a timezone-aware UTC datetime.

    Args:
        ns: Nanoseconds since Unix epoch.

    Returns:
        UTC datetime.
    """
    return pd.Timestamp(ns, unit="ns", tz="UTC").to_pydatetime()


class ReplayEngine:
    """Event-driven historical replay engine for backtesting.

    Creates a sandboxed environment with SimulatedClock, EventBus, OMS,
    and PaperVenue to replay historical data through a Strategy. The
    strategy receives events through the standard lifecycle, identical
    to live execution.

    Args:
        initial_capital: Starting capital in the account currency.
        commission_rate: Per-trade commission as a fraction of notional
            (e.g. ``Decimal("0.001")`` for 10 bps).
    """

    def __init__(
        self,
        initial_capital: Decimal = Decimal("100000"),
        commission_rate: Decimal = Decimal("0"),
    ) -> None:
        self._initial_capital = initial_capital
        self._commission_rate = commission_rate

    async def run(
        self,
        strategy_cls: type[Strategy],
        data: dict[Instrument, pd.DataFrame],
        strategy_params: dict[str, Any] | None = None,
        data_type: str = "bar",
    ) -> dict[str, Any]:
        """Run an event-driven backtest.

        Sets up a complete execution environment and feeds historical data
        through it chronologically. The strategy receives events, generates
        orders, and the OMS + PaperVenue simulate fills.

        Args:
            strategy_cls: The Strategy subclass to instantiate and run.
            data: Mapping from Instrument to normalized DataFrames.
                Must conform to bar or trade schemas from
                :mod:`sysls.data.normalize`.
            strategy_params: Optional parameters dict passed to the strategy
                constructor.
            data_type: Type of data being fed. ``"bar"`` (default) or
                ``"trade"``. Determines which converter is used.

        Returns:
            Dict containing:
                - ``equity_curve``: numpy array of equity values per bar.
                - ``timestamps``: numpy array of datetime timestamps.
                - ``trades``: list of trade dicts (timestamp, instrument,
                  side, price, quantity, commission).
                - ``positions``: final positions dict.
                - ``initial_capital``: starting capital as float.
                - ``final_equity``: ending equity as float.

        Raises:
            ValueError: If *data* is empty or *data_type* is unsupported.
        """
        if not data:
            raise ValueError("data must contain at least one instrument DataFrame")
        if data_type not in ("bar", "trade"):
            raise ValueError(f"Unsupported data_type: {data_type!r}. Use 'bar' or 'trade'.")

        # -- 1. Convert DataFrames to events -----------------------------------
        all_events = self._build_event_stream(data, data_type)
        if not all_events:
            raise ValueError("No events generated from the provided data")

        # -- 2. Set up sandboxed environment -----------------------------------
        start_time = _ns_to_datetime(all_events[0].timestamp_ns)
        clock = SimulatedClock(start=start_time)
        bus = EventBus()
        paper = PaperVenue(
            bus=bus,
            initial_balances={"USD": self._initial_capital},
        )
        oms = OrderManagementSystem(bus=bus, default_venue=paper)
        ctx = StrategyContext(bus=bus, clock=clock)

        instruments = list(data.keys())
        strategy = strategy_cls(
            strategy_id="replay",
            context=ctx,
            instruments=instruments,
            params=strategy_params,
        )

        # -- 3. Wire event subscriptions ---------------------------------------
        latest_prices: dict[Instrument, Decimal] = {}
        trade_log: list[dict[str, Any]] = []
        equity_snapshots: list[tuple[datetime, float]] = []

        # Strategy receives market data, fills, and position events.
        bus.subscribe(MarketDataEvent, strategy.on_market_data)
        bus.subscribe(FillEvent, strategy.on_fill)
        bus.subscribe(PositionEvent, strategy.on_position)

        # Order router: strategy OrderSubmitted -> OMS submit.
        async def _route_order(event: OrderSubmitted) -> None:
            """Route strategy-originated orders through the OMS."""
            if event.source == "oms":
                return
            price = event.price
            if price is None:
                price = latest_prices.get(event.instrument)
            request = OrderRequest(
                order_id=event.order_id,
                instrument=event.instrument,
                side=event.side,
                order_type=OrderType.MARKET,
                quantity=event.quantity,
                price=price,
            )
            await oms.submit_order(request)

        bus.subscribe(OrderSubmitted, _route_order)

        # Trade logger: record every fill for the trade log.
        async def _record_fill(event: FillEvent) -> None:
            """Record fill details for the trade log."""
            commission = Decimal("0")
            if self._commission_rate > 0:
                commission = event.fill_price * event.fill_quantity * self._commission_rate
            trade_log.append(
                {
                    "timestamp": clock.now(),
                    "instrument": str(event.instrument),
                    "side": event.side.value,
                    "price": float(event.fill_price),
                    "quantity": float(event.fill_quantity),
                    "commission": float(commission),
                }
            )

        bus.subscribe(FillEvent, _record_fill)

        # -- 4. Start components -----------------------------------------------
        await bus.start()
        await paper.connect()
        await oms.start()
        await strategy.on_start()

        logger.info(
            "replay_started",
            strategy=strategy_cls.__name__,
            instruments=len(instruments),
            events=len(all_events),
            initial_capital=str(self._initial_capital),
        )

        # Record initial equity.
        equity_snapshots.append((clock.now(), float(self._initial_capital)))

        # -- 5. Feed events chronologically ------------------------------------
        for event in all_events:
            # Advance simulated clock to event timestamp.
            event_time = _ns_to_datetime(event.timestamp_ns)
            await clock.advance_to(event_time)

            # Update latest market price for fill pricing.
            if isinstance(event, BarEvent):
                latest_prices[event.instrument] = event.close
            elif isinstance(event, TradeEvent):
                latest_prices[event.instrument] = event.price

            # Publish event and drain the bus to ensure all cascading
            # events (orders, fills, position updates) complete before
            # advancing to the next data point.
            await bus.publish(event)
            await bus._queue.join()

            # Snapshot equity after processing this event.
            equity = self._compute_equity(oms, latest_prices)
            equity_snapshots.append((event_time, equity))

        # -- 6. Teardown -------------------------------------------------------
        await strategy.on_stop()
        await bus.stop()
        await paper.disconnect()

        # -- 7. Build results --------------------------------------------------
        timestamps = np.array(
            [pd.Timestamp(ts) for ts, _ in equity_snapshots],
            dtype="datetime64[ns]",
        )
        equity_curve = np.array([eq for _, eq in equity_snapshots], dtype=np.float64)

        final_positions: dict[str, dict[str, Any]] = {}
        for inst, pos in oms.get_all_positions().items():
            final_positions[str(inst)] = {
                "quantity": float(pos.quantity),
                "avg_entry_price": float(pos.avg_entry_price),
                "realized_pnl": float(pos.realized_pnl),
            }

        result = {
            "equity_curve": equity_curve,
            "timestamps": timestamps,
            "trades": trade_log,
            "positions": final_positions,
            "initial_capital": float(self._initial_capital),
            "final_equity": (
                float(equity_curve[-1]) if len(equity_curve) > 0 else float(self._initial_capital)
            ),
        }

        logger.info(
            "replay_complete",
            final_equity=result["final_equity"],
            total_trades=len(trade_log),
            total_bars=len(all_events),
        )

        return result

    def _build_event_stream(
        self,
        data: dict[Instrument, pd.DataFrame],
        data_type: str,
    ) -> list[MarketDataEvent]:
        """Convert instrument DataFrames into a sorted event stream.

        Args:
            data: Mapping from Instrument to normalized DataFrames.
            data_type: ``"bar"`` or ``"trade"``.

        Returns:
            Chronologically sorted list of MarketDataEvents.
        """
        all_events: list[MarketDataEvent] = []
        for instrument, df in data.items():
            if data_type == "bar":
                events = bars_to_events(df, instrument, source="replay")
            else:
                events = trades_to_events(df, instrument, source="replay")
            all_events.extend(events)

        all_events.sort(key=lambda e: e.timestamp_ns)
        return all_events

    def _compute_equity(
        self,
        oms: OrderManagementSystem,
        latest_prices: dict[Instrument, Decimal],
    ) -> float:
        """Compute current portfolio equity.

        Equity = initial_capital + realized_pnl + unrealized_pnl - commissions.

        Unrealized PnL is computed by marking open positions to the latest
        known price.

        Args:
            oms: The OMS with current position state.
            latest_prices: Latest known prices per instrument.

        Returns:
            Current total equity as float.
        """
        total_realized = Decimal("0")
        total_unrealized = Decimal("0")

        for instrument, position in oms.get_all_positions().items():
            total_realized += position.realized_pnl

            if position.quantity != Decimal("0"):
                mark_price = latest_prices.get(instrument)
                if mark_price is not None:
                    if position.quantity > Decimal("0"):
                        unrealized = (mark_price - position.avg_entry_price) * position.quantity
                    else:
                        unrealized = (position.avg_entry_price - mark_price) * abs(
                            position.quantity
                        )
                    total_unrealized += unrealized

        equity = self._initial_capital + total_realized + total_unrealized
        return float(equity)
