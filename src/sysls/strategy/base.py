"""Strategy abstract base class and context for the sysls framework.

The Strategy ABC is the main user extension point. Users subclass it to
implement trading strategies. The strategy receives market data, manages
internal state, generates signals, and can request orders.

The StrategyContext provides strategies with access to framework services
(event bus, clock) and convenience methods for common operations.

Example usage::

    class MyStrategy(Strategy):
        async def on_market_data(self, event: MarketDataEvent) -> None:
            if some_condition(event):
                await self.emit_signal(
                    instrument=event.instrument,
                    direction=SignalDirection.LONG,
                    strength=0.8,
                )
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

import structlog

from sysls.core.events import (
    FillEvent,
    MarketDataEvent,
    OrderSubmitted,
    PositionEvent,
    SignalDirection,
    SignalEvent,
)
from sysls.core.types import (
    OrderRequest,
    OrderType,
    Side,
    TimeInForce,
    generate_order_id,
)

if TYPE_CHECKING:
    from decimal import Decimal

    from sysls.core.bus import EventBus
    from sysls.core.clock import Clock
    from sysls.core.types import Instrument

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


class StrategyContext:
    """Context provided to strategies for interacting with the framework.

    Provides access to the event bus, clock, and convenience methods
    for common operations like emitting signals and requesting orders.
    Strategies should use the context instead of directly accessing
    framework internals.

    Args:
        bus: The event bus for publishing/subscribing events.
        clock: The clock for getting current time.
    """

    def __init__(self, bus: EventBus, clock: Clock) -> None:
        self._bus = bus
        self._clock = clock

    @property
    def bus(self) -> EventBus:
        """The event bus."""
        return self._bus

    @property
    def clock(self) -> Clock:
        """The clock."""
        return self._clock

    async def emit_signal(
        self,
        strategy_id: str,
        instrument: Instrument,
        direction: SignalDirection,
        strength: float = 1.0,
        metadata: dict[str, str] | None = None,
    ) -> None:
        """Emit a SignalEvent on the bus.

        Convenience method that constructs and publishes a SignalEvent.

        Args:
            strategy_id: ID of the strategy emitting the signal.
            instrument: Target instrument for the signal.
            direction: Signal direction (LONG, SHORT, FLAT).
            strength: Signal strength/conviction, typically in [-1.0, 1.0].
            metadata: Optional key-value metadata.
        """
        event = SignalEvent(
            strategy_id=strategy_id,
            instrument=instrument,
            direction=direction,
            strength=strength,
            metadata=metadata or {},
            source=f"strategy:{strategy_id}",
        )
        await self._bus.publish(event)
        logger.debug(
            "signal_emitted",
            strategy_id=strategy_id,
            instrument=str(instrument),
            direction=direction.value,
            strength=strength,
        )

    async def request_order(
        self,
        instrument: Instrument,
        side: Side,
        quantity: Decimal,
        order_type: OrderType = OrderType.MARKET,
        price: Decimal | None = None,
        time_in_force: TimeInForce = TimeInForce.GTC,
    ) -> OrderRequest:
        """Create an OrderRequest and publish it as an event.

        Creates the OrderRequest, publishes an OrderSubmitted event on the bus,
        and returns the OrderRequest for tracking.

        NOTE: This does NOT submit through the OMS. The engine or an order
        manager subscribes to these events and routes them through the OMS.
        For Phase 3, this simply creates the request and emits an event.

        Args:
            instrument: The instrument to trade.
            side: Buy or sell.
            quantity: Order quantity (always positive).
            order_type: Market, limit, stop, etc.
            price: Limit price, required for LIMIT and STOP_LIMIT orders.
            time_in_force: How long the order remains active.

        Returns:
            The created OrderRequest for tracking.
        """
        order_id = generate_order_id()
        request = OrderRequest(
            order_id=order_id,
            instrument=instrument,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            time_in_force=time_in_force,
        )
        event = OrderSubmitted(
            order_id=order_id,
            instrument=instrument,
            side=side,
            quantity=quantity,
            price=price,
            source="strategy_context",
        )
        await self._bus.publish(event)
        logger.debug(
            "order_requested",
            order_id=order_id,
            instrument=str(instrument),
            side=side.value,
            quantity=str(quantity),
            order_type=order_type.value,
        )
        return request


class Strategy(ABC):
    """Abstract base class for trading strategies.

    Users subclass Strategy and implement the abstract methods to create
    trading strategies. The engine calls lifecycle methods in this order:

    1. ``__init__`` -- set up parameters (before engine start)
    2. ``on_start`` -- called once when the engine starts (bus is running)
    3. ``on_market_data`` -- called on every market data event for subscribed instruments
    4. ``on_fill`` -- called on every fill for orders this strategy submitted
    5. ``on_position`` -- called on every position change for relevant instruments
    6. ``on_stop`` -- called once when the engine stops

    Strategies access the event bus and clock through the StrategyContext
    provided at initialization.

    Args:
        strategy_id: Unique identifier for this strategy instance.
        context: StrategyContext providing bus, clock, and helper methods.
        instruments: List of instruments this strategy trades.
        params: Optional strategy-specific parameters dict.
    """

    def __init__(
        self,
        strategy_id: str,
        context: StrategyContext,
        instruments: list[Instrument],
        params: dict[str, Any] | None = None,
    ) -> None:
        self._strategy_id = strategy_id
        self._context = context
        self._instruments = list(instruments)
        self._params: dict[str, Any] = params if params is not None else {}
        self._log: structlog.stdlib.BoundLogger = structlog.get_logger(
            strategy_id=strategy_id,
        )

    # --- Properties ---

    @property
    def strategy_id(self) -> str:
        """The strategy's unique identifier."""
        return self._strategy_id

    @property
    def instruments(self) -> list[Instrument]:
        """Instruments this strategy is registered for."""
        return list(self._instruments)

    @property
    def params(self) -> dict[str, Any]:
        """Strategy parameters."""
        return dict(self._params)

    # --- Abstract methods (users MUST implement) ---

    @abstractmethod
    async def on_market_data(self, event: MarketDataEvent) -> None:
        """Called on every market data event for subscribed instruments.

        This is the main entry point for strategy logic. Analyze the
        incoming data and optionally emit signals or request orders.

        Args:
            event: The market data event to process.
        """

    # --- Optional lifecycle hooks (default no-op) ---

    async def on_start(self) -> None:  # noqa: B027
        """Called once when the engine starts. Override for initialization."""

    async def on_stop(self) -> None:  # noqa: B027
        """Called once when the engine stops. Override for cleanup."""

    async def on_fill(self, event: FillEvent) -> None:  # noqa: B027
        """Called on every fill for orders this strategy submitted.

        Args:
            event: The fill event to process.
        """

    async def on_position(self, event: PositionEvent) -> None:  # noqa: B027
        """Called on every position change for relevant instruments.

        Args:
            event: The position event to process.
        """

    # --- Concrete helper methods ---

    async def emit_signal(
        self,
        instrument: Instrument,
        direction: SignalDirection,
        strength: float = 1.0,
        metadata: dict[str, str] | None = None,
    ) -> None:
        """Emit a signal through the context.

        Convenience method that delegates to ``StrategyContext.emit_signal``.

        Args:
            instrument: Target instrument for the signal.
            direction: Signal direction (LONG, SHORT, FLAT).
            strength: Signal strength/conviction, typically in [-1.0, 1.0].
            metadata: Optional key-value metadata.
        """
        await self._context.emit_signal(
            strategy_id=self._strategy_id,
            instrument=instrument,
            direction=direction,
            strength=strength,
            metadata=metadata,
        )

    async def request_order(
        self,
        instrument: Instrument,
        side: Side,
        quantity: Decimal,
        order_type: OrderType = OrderType.MARKET,
        price: Decimal | None = None,
    ) -> OrderRequest:
        """Request an order through the context.

        Convenience method that delegates to ``StrategyContext.request_order``.

        Args:
            instrument: The instrument to trade.
            side: Buy or sell.
            quantity: Order quantity (always positive).
            order_type: Market, limit, stop, etc.
            price: Limit price, required for LIMIT and STOP_LIMIT orders.

        Returns:
            The created OrderRequest for tracking.
        """
        return await self._context.request_order(
            instrument=instrument,
            side=side,
            quantity=quantity,
            order_type=order_type,
            price=price,
        )
