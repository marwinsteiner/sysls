"""Risk engine for pre-trade and post-trade limit enforcement.

The risk engine maintains a set of configurable risk limits and checks
proposed orders against them synchronously. Per the CLAUDE.md design
decision: "Risk engine is synchronous on the hot path. Pre-trade risk
checks must not add latency."

The engine also subscribes to position events on the event bus to
maintain internal state for position-aware checks (e.g. max position,
drawdown tracking).

Example usage::

    engine = RiskEngine(bus=bus, limits=[
        MaxPositionLimit(name="max_pos_NVDA", max_quantity=Decimal("100")),
        MaxDrawdownLimit(name="max_dd", max_drawdown_pct=0.05),
    ])
    await engine.start()

    violations = engine.check_order(order, current_price=Decimal("150.00"))
    if violations:
        # Order violates risk limits — do not submit
        ...
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import structlog
from pydantic import BaseModel

from sysls.core.events import PositionEvent, RiskEvent, RiskSeverity
from sysls.core.types import Instrument, Side

if TYPE_CHECKING:
    from sysls.core.bus import EventBus
    from sysls.core.types import OrderRequest


class RiskLimit(BaseModel, frozen=True):
    """Base class for all risk limits.

    Attributes:
        name: Human-readable limit name (e.g. "max_position_NVDA").
        enabled: Whether this limit is active.
    """

    name: str
    enabled: bool = True


class MaxPositionLimit(RiskLimit, frozen=True):
    """Limit on the absolute quantity of a position in an instrument.

    Attributes:
        instrument: The instrument this limit applies to (None = all instruments).
        max_quantity: Maximum absolute position quantity allowed.
    """

    instrument: Instrument | None = None
    max_quantity: Decimal


class MaxNotionalLimit(RiskLimit, frozen=True):
    """Limit on the notional value of a single order.

    Attributes:
        max_notional: Maximum notional value (quantity * price) allowed per order.
        currency: Currency for the notional limit.
    """

    max_notional: Decimal
    currency: str = "USD"


class MaxOrderSizeLimit(RiskLimit, frozen=True):
    """Limit on the quantity of a single order.

    Attributes:
        instrument: The instrument this limit applies to (None = all).
        max_quantity: Maximum order quantity allowed.
    """

    instrument: Instrument | None = None
    max_quantity: Decimal


class MaxDrawdownLimit(RiskLimit, frozen=True):
    """Limit on the maximum drawdown from peak portfolio value.

    Attributes:
        max_drawdown_pct: Maximum allowed drawdown as a fraction (e.g. 0.05 = 5%).
    """

    max_drawdown_pct: float


class MaxOpenOrdersLimit(RiskLimit, frozen=True):
    """Limit on the total number of open (unfilled) orders.

    Attributes:
        max_orders: Maximum number of open orders allowed.
    """

    max_orders: int


class RiskEngine:
    """Pre-trade and post-trade risk enforcement.

    The risk engine maintains risk limits and checks proposed orders
    against them. Pre-trade checks are synchronous (no async) for
    zero-latency enforcement. The engine also subscribes to position
    and fill events on the bus to maintain internal state for drawdown
    tracking.

    Args:
        bus: EventBus for subscribing to position/fill events and emitting RiskEvents.
        limits: Initial set of risk limits.
    """

    def __init__(
        self,
        bus: EventBus,
        limits: list[RiskLimit] | None = None,
    ) -> None:
        self._bus = bus
        self._limits: list[RiskLimit] = list(limits or [])
        self._positions: dict[Instrument, Decimal] = {}
        self._open_order_count: int = 0
        self._peak_value: Decimal = Decimal("0")
        self._current_value: Decimal = Decimal("0")
        self._realized_pnl: Decimal = Decimal("0")
        self._logger = structlog.get_logger(__name__)

    async def start(self) -> None:
        """Subscribe to position and fill events on the bus."""
        self._bus.subscribe(PositionEvent, self._on_position)

    def add_limit(self, limit: RiskLimit) -> None:
        """Add a new risk limit.

        Args:
            limit: The risk limit to add.
        """
        self._limits.append(limit)
        self._logger.info("risk_limit_added", limit_name=limit.name)

    def remove_limit(self, name: str) -> bool:
        """Remove a limit by name.

        Args:
            name: The name of the limit to remove.

        Returns:
            True if the limit was found and removed, False otherwise.
        """
        for i, limit in enumerate(self._limits):
            if limit.name == name:
                self._limits.pop(i)
                self._logger.info("risk_limit_removed", limit_name=name)
                return True
        return False

    def get_limits(self) -> list[RiskLimit]:
        """Return all configured limits.

        Returns:
            A copy of the current list of risk limits.
        """
        return list(self._limits)

    def check_order(
        self, order: OrderRequest, current_price: Decimal | None = None
    ) -> list[RiskEvent]:
        """Run all pre-trade risk checks against a proposed order.

        This is SYNCHRONOUS -- it does not await anything. It checks the
        order against all enabled limits and returns a list of RiskEvents
        for any violations. An empty list means the order passes.

        Args:
            order: The proposed order to check.
            current_price: Current market price (needed for notional checks).
                If None and order has no price, notional checks are skipped.

        Returns:
            List of RiskEvents. Empty = order passes all checks.
        """
        violations: list[RiskEvent] = []

        for limit in self._limits:
            if not limit.enabled:
                continue

            if isinstance(limit, MaxPositionLimit):
                violation = self._check_max_position(order, limit)
                if violation is not None:
                    violations.append(violation)

            elif isinstance(limit, MaxNotionalLimit):
                violation = self._check_max_notional(order, limit, current_price)
                if violation is not None:
                    violations.append(violation)

            elif isinstance(limit, MaxOrderSizeLimit):
                violation = self._check_max_order_size(order, limit)
                if violation is not None:
                    violations.append(violation)

            elif isinstance(limit, MaxDrawdownLimit):
                violation = self._check_max_drawdown(limit)
                if violation is not None:
                    violations.append(violation)

            elif isinstance(limit, MaxOpenOrdersLimit):
                violation = self._check_max_open_orders(limit)
                if violation is not None:
                    violations.append(violation)

        if violations:
            self._logger.warning(
                "risk_check_failed",
                order_id=order.order_id,
                instrument=str(order.instrument),
                violation_count=len(violations),
            )

        return violations

    async def on_order_submitted(self) -> None:
        """Called when an order is submitted (increments open order count)."""
        self._open_order_count += 1

    async def on_order_completed(self) -> None:
        """Called when an order is filled/cancelled/rejected (decrements open order count)."""
        self._open_order_count = max(0, self._open_order_count - 1)

    def update_portfolio_value(self, value: Decimal) -> None:
        """Update the current portfolio value for drawdown tracking.

        Args:
            value: Current total portfolio value.
        """
        self._current_value = value
        if value > self._peak_value:
            self._peak_value = value

    @property
    def current_drawdown_pct(self) -> float:
        """Current drawdown as a fraction (0.0 = no drawdown)."""
        if self._peak_value <= Decimal("0"):
            return 0.0
        return float((self._peak_value - self._current_value) / self._peak_value)

    # -- Internal event handlers ---

    async def _on_position(self, event: PositionEvent) -> None:
        """Track position changes for limit enforcement.

        Args:
            event: The position event with updated position information.
        """
        self._positions[event.instrument] = event.quantity
        self._realized_pnl += event.realized_pnl

    # -- Internal limit check methods ---

    def _check_max_position(
        self, order: OrderRequest, limit: MaxPositionLimit
    ) -> RiskEvent | None:
        """Check if the order would breach the max position limit.

        Args:
            order: The proposed order.
            limit: The max position limit to check against.

        Returns:
            A RiskEvent if the limit is breached, None otherwise.
        """
        # If limit is instrument-specific, only check matching instruments
        if limit.instrument is not None and order.instrument != limit.instrument:
            return None

        current_qty = self._positions.get(order.instrument, Decimal("0"))

        if order.side == Side.BUY:
            new_qty = current_qty + order.quantity
        else:
            new_qty = current_qty - order.quantity

        if abs(new_qty) > limit.max_quantity:
            return RiskEvent(
                severity=RiskSeverity.BREACH,
                rule_name=limit.name,
                message=(
                    f"Order would result in position {new_qty} "
                    f"exceeding max {limit.max_quantity} "
                    f"for {order.instrument.symbol}"
                ),
                instrument=order.instrument,
                current_value=float(abs(new_qty)),
                limit_value=float(limit.max_quantity),
                source="risk_engine",
            )
        return None

    def _check_max_notional(
        self,
        order: OrderRequest,
        limit: MaxNotionalLimit,
        current_price: Decimal | None,
    ) -> RiskEvent | None:
        """Check if the order notional exceeds the limit.

        Args:
            order: The proposed order.
            limit: The max notional limit to check against.
            current_price: Current market price for notional calculation.

        Returns:
            A RiskEvent if the limit is breached, None otherwise.
        """
        price = order.price or current_price
        if price is None:
            # Cannot check notional without a price
            return None

        notional = order.quantity * price

        if notional > limit.max_notional:
            return RiskEvent(
                severity=RiskSeverity.BREACH,
                rule_name=limit.name,
                message=(
                    f"Order notional {notional} exceeds max {limit.max_notional} {limit.currency}"
                ),
                instrument=order.instrument,
                current_value=float(notional),
                limit_value=float(limit.max_notional),
                source="risk_engine",
            )
        return None

    def _check_max_order_size(
        self, order: OrderRequest, limit: MaxOrderSizeLimit
    ) -> RiskEvent | None:
        """Check if the order quantity exceeds the max order size.

        Args:
            order: The proposed order.
            limit: The max order size limit to check against.

        Returns:
            A RiskEvent if the limit is breached, None otherwise.
        """
        # If limit is instrument-specific, only check matching instruments
        if limit.instrument is not None and order.instrument != limit.instrument:
            return None

        if order.quantity > limit.max_quantity:
            return RiskEvent(
                severity=RiskSeverity.BREACH,
                rule_name=limit.name,
                message=(
                    f"Order quantity {order.quantity} exceeds max "
                    f"{limit.max_quantity} for {order.instrument.symbol}"
                ),
                instrument=order.instrument,
                current_value=float(order.quantity),
                limit_value=float(limit.max_quantity),
                source="risk_engine",
            )
        return None

    def _check_max_drawdown(self, limit: MaxDrawdownLimit) -> RiskEvent | None:
        """Check if the current drawdown exceeds the limit.

        Args:
            limit: The max drawdown limit to check against.

        Returns:
            A RiskEvent if the limit is breached, None otherwise.
        """
        dd = self.current_drawdown_pct

        if dd > limit.max_drawdown_pct:
            return RiskEvent(
                severity=RiskSeverity.BREACH,
                rule_name=limit.name,
                message=(f"Current drawdown {dd:.2%} exceeds max {limit.max_drawdown_pct:.2%}"),
                current_value=dd,
                limit_value=limit.max_drawdown_pct,
                source="risk_engine",
            )
        return None

    def _check_max_open_orders(self, limit: MaxOpenOrdersLimit) -> RiskEvent | None:
        """Check if the open order count exceeds the limit.

        Args:
            limit: The max open orders limit to check against.

        Returns:
            A RiskEvent if the limit is breached, None otherwise.
        """
        if self._open_order_count >= limit.max_orders:
            return RiskEvent(
                severity=RiskSeverity.BREACH,
                rule_name=limit.name,
                message=(
                    f"Open order count {self._open_order_count} "
                    f"meets or exceeds max {limit.max_orders}"
                ),
                current_value=float(self._open_order_count),
                limit_value=float(limit.max_orders),
                source="risk_engine",
            )
        return None
