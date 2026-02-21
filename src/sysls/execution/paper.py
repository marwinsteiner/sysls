"""Paper trading venue adapter with simulated fills.

The PaperVenue simulates a trading venue locally without connecting to any
external service. It supports market and limit orders, optional fill latency,
and configurable partial fill probability. All order lifecycle events are
emitted on the event bus, making it indistinguishable from a real venue
adapter from the OMS's perspective.
"""

from __future__ import annotations

import asyncio
import random
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import uuid4

import structlog
from pydantic import BaseModel

from sysls.core.events import FillEvent, OrderAccepted, OrderCancelled
from sysls.core.exceptions import OrderError
from sysls.core.types import Instrument, OrderRequest, OrderStatus, OrderType, Side
from sysls.execution.venues.base import VenueAdapter

if TYPE_CHECKING:
    from sysls.core.bus import EventBus


class _PaperOrder(BaseModel):
    """Internal tracking model for a paper order.

    Attributes:
        request: The original order request.
        venue_order_id: Paper-generated venue order ID.
        status: Current order lifecycle status.
        filled_quantity: Cumulative filled quantity so far.
    """

    request: OrderRequest
    venue_order_id: str
    status: OrderStatus = OrderStatus.ACCEPTED
    filled_quantity: Decimal = Decimal("0")


class PaperVenue(VenueAdapter):
    """Paper trading venue adapter with simulated fills.

    Simulates a trading venue entirely in-memory. Market orders are filled
    immediately (with optional latency). Limit orders are accepted and stored
    but not filled until matching market data arrives (Phase 3+).

    All order lifecycle events (OrderAccepted, FillEvent, OrderCancelled) are
    emitted on the event bus, making the PaperVenue indistinguishable from a
    real venue from the OMS's perspective.

    Args:
        bus: EventBus for emitting order lifecycle events.
        initial_balances: Starting balances by currency code.
            Defaults to ``{"USD": Decimal("100000")}``.
        fill_latency_ms: Simulated latency in milliseconds before fills.
            0 means instant fills.
        partial_fill_probability: Probability in [0, 1] that a market order
            receives a partial fill (50% of quantity) before the remaining
            fill. Defaults to 0.0 (always full fill).
    """

    _DEFAULT_FILL_PRICE = Decimal("100")

    def __init__(
        self,
        bus: EventBus,
        initial_balances: dict[str, Decimal] | None = None,
        fill_latency_ms: int = 0,
        partial_fill_probability: float = 0.0,
    ) -> None:
        self._bus = bus
        self._orders: dict[str, _PaperOrder] = {}
        self._positions: dict[Instrument, Decimal] = {}
        self._balances: dict[str, Decimal] = dict(
            initial_balances if initial_balances is not None else {"USD": Decimal("100000")}
        )
        self._fill_latency_ms = fill_latency_ms
        self._partial_fill_probability = partial_fill_probability
        self._connected = False
        self._logger = structlog.get_logger(__name__)

    # -- Lifecycle ---------------------------------------------------------

    async def connect(self) -> None:
        """Establish simulated connection (sets internal flag)."""
        self._connected = True
        self._logger.info("paper_venue_connected")

    async def disconnect(self) -> None:
        """Release simulated connection (clears internal flag)."""
        self._connected = False
        self._logger.info("paper_venue_disconnected")

    # -- Properties --------------------------------------------------------

    @property
    def name(self) -> str:
        """Human-readable venue name."""
        return "paper"

    @property
    def is_connected(self) -> bool:
        """Whether the adapter is connected."""
        return self._connected

    @property
    def supported_order_types(self) -> list[OrderType]:
        """All order types are supported by the paper venue."""
        return [OrderType.MARKET, OrderType.LIMIT, OrderType.STOP, OrderType.STOP_LIMIT]

    # -- Order operations --------------------------------------------------

    async def submit_order(self, order: OrderRequest) -> str:
        """Submit an order to the paper venue.

        Generates a venue order ID, stores the order, emits OrderAccepted,
        and immediately fills market orders.

        Args:
            order: Normalized order request.

        Returns:
            Paper-generated venue order ID.
        """
        venue_order_id = f"PAPER-{uuid4().hex[:12]}"

        paper_order = _PaperOrder(
            request=order,
            venue_order_id=venue_order_id,
        )
        self._orders[venue_order_id] = paper_order

        self._logger.info(
            "paper_order_accepted",
            venue_order_id=venue_order_id,
            order_id=order.order_id,
            side=order.side.value,
            order_type=order.order_type.value,
            quantity=str(order.quantity),
        )

        await self._bus.publish(
            OrderAccepted(
                order_id=order.order_id,
                instrument=order.instrument,
                venue_order_id=venue_order_id,
                source="paper",
            )
        )

        if order.order_type == OrderType.MARKET:
            await self._execute_fill(paper_order)

        return venue_order_id

    async def cancel_order(self, venue_order_id: str, instrument: Instrument) -> None:
        """Cancel a paper order.

        Args:
            venue_order_id: The paper venue order ID.
            instrument: The instrument (used for event emission).

        Raises:
            OrderError: If the order is not found.
        """
        paper_order = self._orders.get(venue_order_id)
        if paper_order is None:
            raise OrderError(
                f"Paper order {venue_order_id} not found",
                venue="paper",
            )

        paper_order.status = OrderStatus.CANCELLED

        self._logger.info(
            "paper_order_cancelled",
            venue_order_id=venue_order_id,
            order_id=paper_order.request.order_id,
        )

        await self._bus.publish(
            OrderCancelled(
                order_id=paper_order.request.order_id,
                instrument=paper_order.request.instrument,
                reason="Cancelled by user",
                source="paper",
            )
        )

    async def get_order_status(self, venue_order_id: str, instrument: Instrument) -> OrderStatus:
        """Query current status of a paper order.

        Args:
            venue_order_id: The paper venue order ID.
            instrument: The instrument.

        Returns:
            Current order status.

        Raises:
            OrderError: If the order is not found.
        """
        paper_order = self._orders.get(venue_order_id)
        if paper_order is None:
            raise OrderError(
                f"Paper order {venue_order_id} not found",
                venue="paper",
            )
        return paper_order.status

    # -- Position / balance queries ----------------------------------------

    async def get_positions(self) -> dict[Instrument, Decimal]:
        """Get all current paper positions.

        Returns:
            Copy of the instrument to net quantity mapping.
        """
        return dict(self._positions)

    async def get_balances(self) -> dict[str, Decimal]:
        """Get account balances by currency.

        Returns:
            Copy of the currency to balance mapping.
        """
        return dict(self._balances)

    # -- Private fill execution --------------------------------------------

    async def _execute_fill(self, paper_order: _PaperOrder) -> None:
        """Execute a simulated fill for a paper order.

        Handles optional fill latency and partial fill probability.
        Updates internal positions and balances, and emits FillEvent(s)
        on the bus.

        Args:
            paper_order: The paper order to fill.
        """
        if self._fill_latency_ms > 0:
            await asyncio.sleep(self._fill_latency_ms / 1000)

        request = paper_order.request
        fill_price = self._determine_fill_price(request)
        total_quantity = request.quantity

        is_partial = (
            self._partial_fill_probability > 0 and random.random() < self._partial_fill_probability
        )

        if is_partial:
            first_qty = total_quantity / Decimal("2")
            remaining_qty = total_quantity - first_qty

            # First partial fill.
            paper_order.filled_quantity = first_qty
            paper_order.status = OrderStatus.PARTIALLY_FILLED

            await self._emit_fill(
                paper_order=paper_order,
                fill_price=fill_price,
                fill_quantity=first_qty,
                cumulative_quantity=first_qty,
                order_status=OrderStatus.PARTIALLY_FILLED,
            )

            # Second fill completes the order.
            paper_order.filled_quantity = total_quantity
            paper_order.status = OrderStatus.FILLED

            await self._emit_fill(
                paper_order=paper_order,
                fill_price=fill_price,
                fill_quantity=remaining_qty,
                cumulative_quantity=total_quantity,
                order_status=OrderStatus.FILLED,
            )
        else:
            # Full fill in one go.
            paper_order.filled_quantity = total_quantity
            paper_order.status = OrderStatus.FILLED

            await self._emit_fill(
                paper_order=paper_order,
                fill_price=fill_price,
                fill_quantity=total_quantity,
                cumulative_quantity=total_quantity,
                order_status=OrderStatus.FILLED,
            )

        # Update internal position and balance tracking.
        self._update_position(request.instrument, request.side, total_quantity)
        self._update_balance(request.instrument, request.side, total_quantity, fill_price)

    async def _emit_fill(
        self,
        paper_order: _PaperOrder,
        fill_price: Decimal,
        fill_quantity: Decimal,
        cumulative_quantity: Decimal,
        order_status: OrderStatus,
    ) -> None:
        """Emit a FillEvent on the bus.

        Args:
            paper_order: The paper order being filled.
            fill_price: Execution price.
            fill_quantity: Quantity for this individual fill.
            cumulative_quantity: Total filled quantity so far.
            order_status: Order status after this fill.
        """
        fill_id = f"PAPER-FILL-{uuid4().hex[:12]}"
        request = paper_order.request

        await self._bus.publish(
            FillEvent(
                order_id=request.order_id,
                instrument=request.instrument,
                side=request.side,
                fill_price=fill_price,
                fill_quantity=fill_quantity,
                cumulative_quantity=cumulative_quantity,
                order_status=order_status,
                venue_fill_id=fill_id,
                source="paper",
            )
        )

        self._logger.info(
            "paper_fill_emitted",
            order_id=request.order_id,
            venue_order_id=paper_order.venue_order_id,
            fill_price=str(fill_price),
            fill_quantity=str(fill_quantity),
            cumulative_quantity=str(cumulative_quantity),
            status=order_status.value,
        )

    @staticmethod
    def _determine_fill_price(request: OrderRequest) -> Decimal:
        """Determine the fill price for a paper order.

        Uses the order's limit price if available, otherwise falls back
        to a default price of 100.

        Args:
            request: The order request.

        Returns:
            The simulated fill price.
        """
        if request.price is not None:
            return request.price
        return PaperVenue._DEFAULT_FILL_PRICE

    def _update_position(self, instrument: Instrument, side: Side, quantity: Decimal) -> None:
        """Update internal position tracking after a fill.

        Args:
            instrument: The instrument filled.
            side: Fill side.
            quantity: Fill quantity (always positive).
        """
        current = self._positions.get(instrument, Decimal("0"))
        signed_qty = quantity if side == Side.BUY else -quantity
        self._positions[instrument] = current + signed_qty

    def _update_balance(
        self,
        instrument: Instrument,
        side: Side,
        quantity: Decimal,
        price: Decimal,
    ) -> None:
        """Update internal balance tracking after a fill.

        Deducts (for buys) or credits (for sells) the notional value
        from the instrument's quote currency balance.

        Args:
            instrument: The instrument filled.
            side: Fill side.
            quantity: Fill quantity.
            price: Execution price.
        """
        currency = instrument.currency
        notional = quantity * price
        current_balance = self._balances.get(currency, Decimal("0"))

        if side == Side.BUY:
            self._balances[currency] = current_balance - notional
        else:
            self._balances[currency] = current_balance + notional
