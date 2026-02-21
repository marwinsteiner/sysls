"""Order Management System for order lifecycle tracking.

The OMS is the central coordinator for all order activity. It receives
OrderRequests, submits them to a venue adapter, tracks lifecycle state
transitions, maintains position state from fills, and emits events on the
bus for downstream consumers (risk engine, analytics, etc.).
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import TYPE_CHECKING

import structlog
from pydantic import BaseModel, Field

from sysls.core.events import (
    FillEvent,
    OrderAccepted,
    OrderCancelled,
    OrderRejected,
    OrderSubmitted,
    PositionEvent,
)
from sysls.core.exceptions import OrderError
from sysls.core.types import Instrument, OrderRequest, OrderStatus, Side

if TYPE_CHECKING:
    from sysls.core.bus import EventBus
    from sysls.execution.venues.base import VenueAdapter


def _now_ns() -> int:
    """Return current wall-clock time as nanoseconds since epoch."""
    return int(time.time() * 1_000_000_000)


class OrderState(BaseModel):
    """Internal OMS representation of an order's full lifecycle.

    Attributes:
        request: The original order request.
        status: Current lifecycle status.
        venue_order_id: Venue-assigned identifier (set after submission).
        filled_quantity: Cumulative filled quantity.
        avg_fill_price: Volume-weighted average fill price.
        created_at_ns: Order creation time (nanoseconds since epoch).
        updated_at_ns: Last state change time.
    """

    request: OrderRequest
    status: OrderStatus = OrderStatus.PENDING
    venue_order_id: str | None = None
    filled_quantity: Decimal = Decimal("0")
    avg_fill_price: Decimal | None = None
    created_at_ns: int = Field(default_factory=_now_ns)
    updated_at_ns: int = Field(default_factory=_now_ns)


class Position(BaseModel):
    """Net position in an instrument.

    Attributes:
        instrument: The instrument.
        quantity: Net quantity (positive=long, negative=short, zero=flat).
        avg_entry_price: Volume-weighted average entry price.
        realized_pnl: Cumulative realized PnL from closed portions.
    """

    instrument: Instrument
    quantity: Decimal = Decimal("0")
    avg_entry_price: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")


# Valid state transitions for the order state machine.
_VALID_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.PENDING: frozenset({OrderStatus.SUBMITTED, OrderStatus.REJECTED}),
    OrderStatus.SUBMITTED: frozenset(
        {
            OrderStatus.ACCEPTED,
            OrderStatus.REJECTED,
            OrderStatus.CANCELLED,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
        }
    ),
    OrderStatus.ACCEPTED: frozenset(
        {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
        }
    ),
    OrderStatus.PARTIALLY_FILLED: frozenset(
        {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
        }
    ),
    OrderStatus.FILLED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
    OrderStatus.REJECTED: frozenset(),
    OrderStatus.EXPIRED: frozenset(),
}


class OrderManagementSystem:
    """Tracks order lifecycle and manages position state.

    The OMS:
    - Receives OrderRequests, submits them to a venue adapter, and tracks lifecycle
    - Subscribes to FillEvent, OrderAccepted, OrderRejected, OrderCancelled on the bus
    - Updates internal OrderState on each event
    - Maintains Position state from fills
    - Emits PositionEvent on position changes, OrderSubmitted on submission

    Args:
        bus: The event bus for publishing/subscribing events.
        default_venue: Default venue adapter for order submission.
    """

    def __init__(self, bus: EventBus, default_venue: VenueAdapter) -> None:
        self._bus = bus
        self._default_venue = default_venue
        self._orders: dict[str, OrderState] = {}
        self._venue_to_order: dict[str, str] = {}
        self._positions: dict[Instrument, Position] = {}
        self._logger = structlog.get_logger(__name__)

    async def start(self) -> None:
        """Subscribe to order lifecycle events on the bus."""
        self._bus.subscribe(FillEvent, self._on_fill)
        self._bus.subscribe(OrderAccepted, self._on_order_accepted)
        self._bus.subscribe(OrderRejected, self._on_order_rejected)
        self._bus.subscribe(OrderCancelled, self._on_order_cancelled)
        self._logger.info("oms_started")

    async def submit_order(self, request: OrderRequest) -> str:
        """Submit an order through the venue adapter.

        Creates an OrderState with PENDING status, calls the venue adapter
        to submit the order, transitions to SUBMITTED, and emits an
        OrderSubmitted event on the bus.

        Args:
            request: The order request to submit.

        Returns:
            The order_id for tracking.

        Raises:
            OrderError: If submission fails at the venue.
        """
        order_id = request.order_id

        state = OrderState(request=request, status=OrderStatus.PENDING)
        self._orders[order_id] = state

        self._logger.info(
            "order_submitting",
            order_id=order_id,
            instrument=str(request.instrument),
            side=request.side.value,
            quantity=str(request.quantity),
        )

        try:
            venue_order_id = await self._default_venue.submit_order(request)
        except Exception as exc:
            # Submission failed: mark as REJECTED and re-raise as OrderError.
            state.status = OrderStatus.REJECTED
            state.updated_at_ns = _now_ns()
            self._logger.error(
                "order_submission_failed",
                order_id=order_id,
                error=str(exc),
            )
            if isinstance(exc, OrderError):
                raise
            raise OrderError(
                f"Failed to submit order {order_id}: {exc}",
                venue=self._default_venue.name,
            ) from exc

        # Submission succeeded: update state.
        state.venue_order_id = venue_order_id
        state.status = OrderStatus.SUBMITTED
        state.updated_at_ns = _now_ns()
        self._venue_to_order[venue_order_id] = order_id

        self._logger.info(
            "order_submitted",
            order_id=order_id,
            venue_order_id=venue_order_id,
        )

        await self._bus.publish(
            OrderSubmitted(
                order_id=order_id,
                instrument=request.instrument,
                side=request.side,
                quantity=request.quantity,
                price=request.price,
                source="oms",
            )
        )

        return order_id

    async def cancel_order(self, order_id: str) -> None:
        """Cancel an order.

        Looks up the OrderState and calls the venue adapter to cancel.
        The actual state transition happens when the OrderCancelled event
        arrives from the venue/bus.

        Args:
            order_id: The order to cancel.

        Raises:
            OrderError: If the order is not found or not in a cancellable state.
        """
        state = self._orders.get(order_id)
        if state is None:
            raise OrderError(
                f"Order {order_id} not found",
                venue=self._default_venue.name,
            )

        cancellable = {
            OrderStatus.SUBMITTED,
            OrderStatus.ACCEPTED,
            OrderStatus.PARTIALLY_FILLED,
        }
        if state.status not in cancellable:
            raise OrderError(
                f"Order {order_id} in status {state.status} cannot be cancelled",
                venue=self._default_venue.name,
            )

        if state.venue_order_id is None:
            raise OrderError(
                f"Order {order_id} has no venue_order_id",
                venue=self._default_venue.name,
            )

        self._logger.info(
            "order_cancelling",
            order_id=order_id,
            venue_order_id=state.venue_order_id,
        )

        await self._default_venue.cancel_order(state.venue_order_id, state.request.instrument)

    def get_order(self, order_id: str) -> OrderState | None:
        """Get the current state of an order.

        Args:
            order_id: The order identifier.

        Returns:
            The OrderState if found, None otherwise.
        """
        return self._orders.get(order_id)

    def get_position(self, instrument: Instrument) -> Position | None:
        """Get the current position for an instrument.

        Args:
            instrument: The instrument to query.

        Returns:
            The Position if one exists, None otherwise.
        """
        return self._positions.get(instrument)

    def get_all_orders(self) -> dict[str, OrderState]:
        """Get all tracked orders.

        Returns:
            Copy of the order_id to OrderState mapping.
        """
        return dict(self._orders)

    def get_all_positions(self) -> dict[Instrument, Position]:
        """Get all current positions.

        Returns:
            Copy of the instrument to Position mapping.
        """
        return dict(self._positions)

    # -- Internal event handlers -------------------------------------------

    async def _on_fill(self, event: FillEvent) -> None:
        """Handle a fill event.

        Updates order state (filled_quantity, avg_fill_price, status) and
        position state (quantity, avg_entry_price, realized_pnl). Emits a
        PositionEvent after each fill.

        Args:
            event: The fill event to process.
        """
        order_id = event.order_id
        state = self._orders.get(order_id)
        if state is None:
            self._logger.warning("fill_for_unknown_order", order_id=order_id)
            return

        # Update order fill state.
        old_filled = state.filled_quantity
        new_filled = old_filled + event.fill_quantity

        # Compute new VWAP fill price.
        if state.avg_fill_price is None or old_filled == Decimal("0"):
            new_avg_price = event.fill_price
        else:
            new_avg_price = (
                state.avg_fill_price * old_filled + event.fill_price * event.fill_quantity
            ) / new_filled

        state.filled_quantity = new_filled
        state.avg_fill_price = new_avg_price

        # Determine new order status from the fill event.
        new_status = event.order_status
        if self._is_valid_transition(state.status, new_status):
            state.status = new_status
        state.updated_at_ns = _now_ns()

        self._logger.info(
            "order_filled",
            order_id=order_id,
            fill_qty=str(event.fill_quantity),
            fill_price=str(event.fill_price),
            cumulative_qty=str(new_filled),
            status=state.status.value,
        )

        # Update position.
        position = self._update_position(
            event.instrument, event.side, event.fill_quantity, event.fill_price
        )

        # Emit PositionEvent.
        await self._bus.publish(
            PositionEvent(
                instrument=position.instrument,
                quantity=position.quantity,
                avg_price=position.avg_entry_price,
                realized_pnl=position.realized_pnl,
                source="oms",
            )
        )

    async def _on_order_accepted(self, event: OrderAccepted) -> None:
        """Handle order accepted: update status to ACCEPTED, store venue_order_id.

        Args:
            event: The order accepted event.
        """
        state = self._orders.get(event.order_id)
        if state is None:
            self._logger.warning("accepted_for_unknown_order", order_id=event.order_id)
            return

        if self._is_valid_transition(state.status, OrderStatus.ACCEPTED):
            state.status = OrderStatus.ACCEPTED
            state.updated_at_ns = _now_ns()

        if event.venue_order_id is not None and state.venue_order_id is None:
            state.venue_order_id = event.venue_order_id
            self._venue_to_order[event.venue_order_id] = event.order_id

        self._logger.info(
            "order_accepted",
            order_id=event.order_id,
            venue_order_id=event.venue_order_id,
        )

    async def _on_order_rejected(self, event: OrderRejected) -> None:
        """Handle order rejected: update status to REJECTED.

        Args:
            event: The order rejected event.
        """
        state = self._orders.get(event.order_id)
        if state is None:
            self._logger.warning("rejected_for_unknown_order", order_id=event.order_id)
            return

        if self._is_valid_transition(state.status, OrderStatus.REJECTED):
            state.status = OrderStatus.REJECTED
            state.updated_at_ns = _now_ns()

        self._logger.info(
            "order_rejected",
            order_id=event.order_id,
            reason=event.reason,
        )

    async def _on_order_cancelled(self, event: OrderCancelled) -> None:
        """Handle order cancelled: update status to CANCELLED.

        Args:
            event: The order cancelled event.
        """
        state = self._orders.get(event.order_id)
        if state is None:
            self._logger.warning("cancelled_for_unknown_order", order_id=event.order_id)
            return

        if self._is_valid_transition(state.status, OrderStatus.CANCELLED):
            state.status = OrderStatus.CANCELLED
            state.updated_at_ns = _now_ns()

        self._logger.info(
            "order_cancelled",
            order_id=event.order_id,
            reason=event.reason,
        )

    def _update_position(
        self,
        instrument: Instrument,
        side: Side,
        fill_qty: Decimal,
        fill_price: Decimal,
    ) -> Position:
        """Update position state from a fill.

        Handles four cases:
        - Opening a new position
        - Adding to an existing position (same direction)
        - Reducing a position (opposite direction, partial close)
        - Flipping a position (opposite direction, oversize)

        Realized PnL is computed on the closed portion:
        - Closing a long: (fill_price - avg_entry_price) * closed_qty
        - Closing a short: (avg_entry_price - fill_price) * closed_qty

        Args:
            instrument: The instrument being filled.
            side: Fill side (BUY or SELL).
            fill_qty: Fill quantity (always positive).
            fill_price: Fill execution price.

        Returns:
            The updated Position.
        """
        position = self._positions.get(instrument)
        if position is None:
            position = Position(instrument=instrument)
            self._positions[instrument] = position

        # Convert fill to a signed quantity: BUY is positive, SELL is negative.
        signed_qty = fill_qty if side == Side.BUY else -fill_qty
        old_qty = position.quantity

        new_qty = old_qty + signed_qty

        if old_qty == Decimal("0"):
            # Opening a fresh position.
            position.quantity = new_qty
            position.avg_entry_price = fill_price
        elif _same_sign(old_qty, signed_qty):
            # Adding to existing position (same direction).
            # New avg entry = VWAP of old position + new fill.
            total_cost = position.avg_entry_price * abs(old_qty) + fill_price * fill_qty
            position.quantity = new_qty
            position.avg_entry_price = total_cost / abs(new_qty)
        elif abs(signed_qty) <= abs(old_qty):
            # Reducing position (partial or full close, no flip).
            closed_qty = fill_qty
            if old_qty > Decimal("0"):
                # Closing a long.
                realized = (fill_price - position.avg_entry_price) * closed_qty
            else:
                # Closing a short.
                realized = (position.avg_entry_price - fill_price) * closed_qty
            position.realized_pnl += realized
            position.quantity = new_qty
            # avg_entry_price stays the same for remaining position.
            # If fully closed, reset avg_entry_price.
            if new_qty == Decimal("0"):
                position.avg_entry_price = Decimal("0")
        else:
            # Flipping position: close the full old position, then open new.
            closed_qty = abs(old_qty)
            if old_qty > Decimal("0"):
                realized = (fill_price - position.avg_entry_price) * closed_qty
            else:
                realized = (position.avg_entry_price - fill_price) * closed_qty

            position.realized_pnl += realized
            position.quantity = new_qty
            position.avg_entry_price = fill_price  # New position at fill price.

        self._logger.debug(
            "position_updated",
            instrument=str(instrument),
            quantity=str(position.quantity),
            avg_entry_price=str(position.avg_entry_price),
            realized_pnl=str(position.realized_pnl),
        )

        return position

    @staticmethod
    def _is_valid_transition(current: OrderStatus, target: OrderStatus) -> bool:
        """Check if a state transition is valid.

        Args:
            current: Current order status.
            target: Desired new status.

        Returns:
            True if the transition is allowed.
        """
        allowed = _VALID_TRANSITIONS.get(current, frozenset())
        return target in allowed


def _same_sign(a: Decimal, b: Decimal) -> bool:
    """Check if two Decimals have the same sign (both positive or both negative).

    Args:
        a: First decimal value.
        b: Second decimal value.

    Returns:
        True if both values have the same sign.
    """
    return (a > Decimal("0") and b > Decimal("0")) or (a < Decimal("0") and b < Decimal("0"))
