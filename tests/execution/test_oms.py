"""Tests for the Order Management System."""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
import pytest_asyncio

from sysls.core.bus import EventBus
from sysls.core.events import (
    FillEvent,
    OrderAccepted,
    OrderCancelled,
    OrderRejected,
    OrderSubmitted,
    PositionEvent,
)
from sysls.core.exceptions import OrderError
from sysls.core.types import (
    AssetClass,
    Instrument,
    OrderRequest,
    OrderStatus,
    OrderType,
    Side,
    TimeInForce,
    Venue,
)
from sysls.execution.oms import OrderManagementSystem, OrderState, Position
from tests.execution.test_base import MockVenueAdapter

# -- Helpers ---------------------------------------------------------------


def _make_instrument(
    symbol: str = "AAPL",
    venue: Venue = Venue.PAPER,
) -> Instrument:
    """Create a test instrument."""
    return Instrument(
        symbol=symbol,
        asset_class=AssetClass.EQUITY,
        venue=venue,
    )


def _make_order_request(
    instrument: Instrument | None = None,
    side: Side = Side.BUY,
    quantity: Decimal = Decimal("10"),
    price: Decimal | None = None,
    order_type: OrderType = OrderType.MARKET,
) -> OrderRequest:
    """Create a test order request."""
    if instrument is None:
        instrument = _make_instrument()
    return OrderRequest(
        instrument=instrument,
        side=side,
        order_type=order_type,
        quantity=quantity,
        price=price,
        time_in_force=TimeInForce.GTC,
    )


def _make_fill_event(
    order_id: str,
    instrument: Instrument,
    side: Side,
    fill_price: Decimal,
    fill_quantity: Decimal,
    cumulative_quantity: Decimal,
    order_status: OrderStatus = OrderStatus.FILLED,
) -> FillEvent:
    """Create a test fill event."""
    return FillEvent(
        order_id=order_id,
        instrument=instrument,
        side=side,
        fill_price=fill_price,
        fill_quantity=fill_quantity,
        cumulative_quantity=cumulative_quantity,
        order_status=order_status,
        source="test",
    )


# -- Model Tests -----------------------------------------------------------


class TestOrderState:
    """Tests for the OrderState model."""

    def test_default_values(self) -> None:
        """OrderState should have sensible defaults."""
        request = _make_order_request()
        state = OrderState(request=request)

        assert state.status == OrderStatus.PENDING
        assert state.venue_order_id is None
        assert state.filled_quantity == Decimal("0")
        assert state.avg_fill_price is None
        assert state.created_at_ns > 0
        assert state.updated_at_ns > 0


class TestPosition:
    """Tests for the Position model."""

    def test_default_values(self) -> None:
        """Position should have zero defaults."""
        instrument = _make_instrument()
        position = Position(instrument=instrument)

        assert position.quantity == Decimal("0")
        assert position.avg_entry_price == Decimal("0")
        assert position.realized_pnl == Decimal("0")


# -- OMS Tests (require event bus) -----------------------------------------


@pytest_asyncio.fixture
async def bus() -> EventBus:  # type: ignore[misc]
    """Create and start an event bus for testing."""
    event_bus = EventBus()
    await event_bus.start()
    yield event_bus  # type: ignore[misc]
    await event_bus.stop()


@pytest.fixture
def venue() -> MockVenueAdapter:
    """Create a mock venue adapter."""
    return MockVenueAdapter(name="PAPER")


@pytest_asyncio.fixture
async def oms(bus: EventBus, venue: MockVenueAdapter) -> OrderManagementSystem:  # type: ignore[misc]
    """Create and start an OMS connected to the bus and venue."""
    system = OrderManagementSystem(bus=bus, default_venue=venue)
    await system.start()
    return system


class TestOMSSubmission:
    """Tests for order submission through the OMS."""

    @pytest.mark.asyncio
    async def test_submit_order_creates_state(
        self, oms: OrderManagementSystem, venue: MockVenueAdapter
    ) -> None:
        """submit_order should create OrderState and track it."""
        request = _make_order_request()
        order_id = await oms.submit_order(request)

        state = oms.get_order(order_id)
        assert state is not None
        assert state.request is request
        assert state.status == OrderStatus.SUBMITTED
        assert state.venue_order_id == "MOCK-1"

    @pytest.mark.asyncio
    async def test_submit_order_calls_venue(
        self, oms: OrderManagementSystem, venue: MockVenueAdapter
    ) -> None:
        """submit_order should forward the request to the venue adapter."""
        request = _make_order_request()
        await oms.submit_order(request)

        assert len(venue.submitted_orders) == 1
        assert venue.submitted_orders[0] is request

    @pytest.mark.asyncio
    async def test_submit_order_emits_order_submitted(
        self, oms: OrderManagementSystem, bus: EventBus
    ) -> None:
        """submit_order should emit an OrderSubmitted event."""
        captured: list[OrderSubmitted] = []

        async def handler(event: OrderSubmitted) -> None:
            captured.append(event)

        bus.subscribe(OrderSubmitted, handler)

        request = _make_order_request()
        order_id = await oms.submit_order(request)

        # Let the bus process the event.
        await asyncio.sleep(0.05)

        assert len(captured) == 1
        assert captured[0].order_id == order_id
        assert captured[0].side == request.side
        assert captured[0].quantity == request.quantity

    @pytest.mark.asyncio
    async def test_submit_order_returns_order_id(self, oms: OrderManagementSystem) -> None:
        """submit_order should return the order's order_id."""
        request = _make_order_request()
        order_id = await oms.submit_order(request)

        assert order_id == request.order_id

    @pytest.mark.asyncio
    async def test_submit_order_venue_error_raises(
        self, oms: OrderManagementSystem, venue: MockVenueAdapter
    ) -> None:
        """submit_order should raise OrderError when venue fails."""
        venue.set_submit_error(RuntimeError("connection lost"))

        request = _make_order_request()
        with pytest.raises(OrderError, match="Failed to submit order"):
            await oms.submit_order(request)

        # State should be REJECTED.
        state = oms.get_order(request.order_id)
        assert state is not None
        assert state.status == OrderStatus.REJECTED

    @pytest.mark.asyncio
    async def test_submit_multiple_orders(
        self, oms: OrderManagementSystem, venue: MockVenueAdapter
    ) -> None:
        """Multiple orders should be tracked independently."""
        r1 = _make_order_request(quantity=Decimal("10"))
        r2 = _make_order_request(quantity=Decimal("20"))

        id1 = await oms.submit_order(r1)
        id2 = await oms.submit_order(r2)

        assert id1 != id2
        assert oms.get_order(id1) is not None
        assert oms.get_order(id2) is not None
        assert len(venue.submitted_orders) == 2


class TestOMSOrderLifecycle:
    """Tests for order lifecycle state transitions."""

    @pytest.mark.asyncio
    async def test_order_accepted(self, oms: OrderManagementSystem, bus: EventBus) -> None:
        """OrderAccepted event should update status to ACCEPTED."""
        request = _make_order_request()
        order_id = await oms.submit_order(request)

        accepted = OrderAccepted(
            order_id=order_id,
            instrument=request.instrument,
            venue_order_id="VENUE-123",
            source="test",
        )
        await bus.publish(accepted)
        await asyncio.sleep(0.05)

        state = oms.get_order(order_id)
        assert state is not None
        assert state.status == OrderStatus.ACCEPTED

    @pytest.mark.asyncio
    async def test_order_rejected(self, oms: OrderManagementSystem, bus: EventBus) -> None:
        """OrderRejected event should update status to REJECTED."""
        request = _make_order_request()
        order_id = await oms.submit_order(request)

        rejected = OrderRejected(
            order_id=order_id,
            instrument=request.instrument,
            reason="insufficient funds",
            source="test",
        )
        await bus.publish(rejected)
        await asyncio.sleep(0.05)

        state = oms.get_order(order_id)
        assert state is not None
        assert state.status == OrderStatus.REJECTED

    @pytest.mark.asyncio
    async def test_order_cancelled(self, oms: OrderManagementSystem, bus: EventBus) -> None:
        """OrderCancelled event should update status to CANCELLED."""
        request = _make_order_request()
        order_id = await oms.submit_order(request)

        cancelled = OrderCancelled(
            order_id=order_id,
            instrument=request.instrument,
            reason="user requested",
            source="test",
        )
        await bus.publish(cancelled)
        await asyncio.sleep(0.05)

        state = oms.get_order(order_id)
        assert state is not None
        assert state.status == OrderStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_order_calls_venue(
        self, oms: OrderManagementSystem, venue: MockVenueAdapter
    ) -> None:
        """cancel_order should call venue.cancel_order."""
        request = _make_order_request()
        order_id = await oms.submit_order(request)

        await oms.cancel_order(order_id)

        assert "MOCK-1" in venue.cancelled_orders

    @pytest.mark.asyncio
    async def test_cancel_order_unknown_raises(self, oms: OrderManagementSystem) -> None:
        """cancel_order on an unknown order_id should raise OrderError."""
        with pytest.raises(OrderError, match="not found"):
            await oms.cancel_order("nonexistent-id")

    @pytest.mark.asyncio
    async def test_cancel_filled_order_raises(
        self, oms: OrderManagementSystem, bus: EventBus
    ) -> None:
        """cancel_order on a FILLED order should raise OrderError."""
        instrument = _make_instrument()
        request = _make_order_request(instrument=instrument)
        order_id = await oms.submit_order(request)

        # Fill the order completely.
        fill = _make_fill_event(
            order_id=order_id,
            instrument=instrument,
            side=Side.BUY,
            fill_price=Decimal("150"),
            fill_quantity=Decimal("10"),
            cumulative_quantity=Decimal("10"),
            order_status=OrderStatus.FILLED,
        )
        await bus.publish(fill)
        await asyncio.sleep(0.05)

        with pytest.raises(OrderError, match="cannot be cancelled"):
            await oms.cancel_order(order_id)


class TestOMSFillHandling:
    """Tests for fill processing and position tracking."""

    @pytest.mark.asyncio
    async def test_buy_fill_opens_long_position(
        self, oms: OrderManagementSystem, bus: EventBus
    ) -> None:
        """A BUY fill should create a long position."""
        instrument = _make_instrument()
        request = _make_order_request(instrument=instrument, side=Side.BUY, quantity=Decimal("10"))
        order_id = await oms.submit_order(request)

        fill = _make_fill_event(
            order_id=order_id,
            instrument=instrument,
            side=Side.BUY,
            fill_price=Decimal("150"),
            fill_quantity=Decimal("10"),
            cumulative_quantity=Decimal("10"),
        )
        await bus.publish(fill)
        await asyncio.sleep(0.05)

        position = oms.get_position(instrument)
        assert position is not None
        assert position.quantity == Decimal("10")
        assert position.avg_entry_price == Decimal("150")
        assert position.realized_pnl == Decimal("0")

    @pytest.mark.asyncio
    async def test_sell_fill_opens_short_position(
        self, oms: OrderManagementSystem, bus: EventBus
    ) -> None:
        """A SELL fill should create a short position."""
        instrument = _make_instrument()
        request = _make_order_request(instrument=instrument, side=Side.SELL, quantity=Decimal("5"))
        order_id = await oms.submit_order(request)

        fill = _make_fill_event(
            order_id=order_id,
            instrument=instrument,
            side=Side.SELL,
            fill_price=Decimal("200"),
            fill_quantity=Decimal("5"),
            cumulative_quantity=Decimal("5"),
        )
        await bus.publish(fill)
        await asyncio.sleep(0.05)

        position = oms.get_position(instrument)
        assert position is not None
        assert position.quantity == Decimal("-5")
        assert position.avg_entry_price == Decimal("200")

    @pytest.mark.asyncio
    async def test_partial_fills(self, oms: OrderManagementSystem, bus: EventBus) -> None:
        """Two partial fills should accumulate correctly."""
        instrument = _make_instrument()
        request = _make_order_request(instrument=instrument, side=Side.BUY, quantity=Decimal("10"))
        order_id = await oms.submit_order(request)

        # First partial fill: 6 @ 150.
        fill1 = _make_fill_event(
            order_id=order_id,
            instrument=instrument,
            side=Side.BUY,
            fill_price=Decimal("150"),
            fill_quantity=Decimal("6"),
            cumulative_quantity=Decimal("6"),
            order_status=OrderStatus.PARTIALLY_FILLED,
        )
        await bus.publish(fill1)
        await asyncio.sleep(0.05)

        state = oms.get_order(order_id)
        assert state is not None
        assert state.status == OrderStatus.PARTIALLY_FILLED
        assert state.filled_quantity == Decimal("6")

        # Second partial fill: 4 @ 152 -> order fully filled.
        fill2 = _make_fill_event(
            order_id=order_id,
            instrument=instrument,
            side=Side.BUY,
            fill_price=Decimal("152"),
            fill_quantity=Decimal("4"),
            cumulative_quantity=Decimal("10"),
            order_status=OrderStatus.FILLED,
        )
        await bus.publish(fill2)
        await asyncio.sleep(0.05)

        state = oms.get_order(order_id)
        assert state is not None
        assert state.status == OrderStatus.FILLED
        assert state.filled_quantity == Decimal("10")
        # VWAP: (150*6 + 152*4) / 10 = (900 + 608) / 10 = 150.8
        assert state.avg_fill_price == Decimal("150.8")

        # Position should reflect both fills.
        position = oms.get_position(instrument)
        assert position is not None
        assert position.quantity == Decimal("10")
        # Position VWAP: (150*6 + 152*4) / 10 = 150.8
        assert position.avg_entry_price == Decimal("150.8")

    @pytest.mark.asyncio
    async def test_close_long_with_realized_pnl(
        self, oms: OrderManagementSystem, bus: EventBus
    ) -> None:
        """Closing a long position should compute realized PnL correctly."""
        instrument = _make_instrument()

        # Open long: buy 10 @ 100.
        buy_request = _make_order_request(
            instrument=instrument, side=Side.BUY, quantity=Decimal("10")
        )
        buy_id = await oms.submit_order(buy_request)

        buy_fill = _make_fill_event(
            order_id=buy_id,
            instrument=instrument,
            side=Side.BUY,
            fill_price=Decimal("100"),
            fill_quantity=Decimal("10"),
            cumulative_quantity=Decimal("10"),
        )
        await bus.publish(buy_fill)
        await asyncio.sleep(0.05)

        # Close long: sell 10 @ 120.
        sell_request = _make_order_request(
            instrument=instrument, side=Side.SELL, quantity=Decimal("10")
        )
        sell_id = await oms.submit_order(sell_request)

        sell_fill = _make_fill_event(
            order_id=sell_id,
            instrument=instrument,
            side=Side.SELL,
            fill_price=Decimal("120"),
            fill_quantity=Decimal("10"),
            cumulative_quantity=Decimal("10"),
        )
        await bus.publish(sell_fill)
        await asyncio.sleep(0.05)

        position = oms.get_position(instrument)
        assert position is not None
        assert position.quantity == Decimal("0")
        # Realized PnL: (120 - 100) * 10 = 200
        assert position.realized_pnl == Decimal("200")
        # When fully closed, avg entry resets to 0.
        assert position.avg_entry_price == Decimal("0")

    @pytest.mark.asyncio
    async def test_close_short_with_realized_pnl(
        self, oms: OrderManagementSystem, bus: EventBus
    ) -> None:
        """Closing a short position should compute realized PnL correctly."""
        instrument = _make_instrument()

        # Open short: sell 5 @ 200.
        sell_request = _make_order_request(
            instrument=instrument, side=Side.SELL, quantity=Decimal("5")
        )
        sell_id = await oms.submit_order(sell_request)

        sell_fill = _make_fill_event(
            order_id=sell_id,
            instrument=instrument,
            side=Side.SELL,
            fill_price=Decimal("200"),
            fill_quantity=Decimal("5"),
            cumulative_quantity=Decimal("5"),
        )
        await bus.publish(sell_fill)
        await asyncio.sleep(0.05)

        # Close short: buy 5 @ 180.
        buy_request = _make_order_request(
            instrument=instrument, side=Side.BUY, quantity=Decimal("5")
        )
        buy_id = await oms.submit_order(buy_request)

        buy_fill = _make_fill_event(
            order_id=buy_id,
            instrument=instrument,
            side=Side.BUY,
            fill_price=Decimal("180"),
            fill_quantity=Decimal("5"),
            cumulative_quantity=Decimal("5"),
        )
        await bus.publish(buy_fill)
        await asyncio.sleep(0.05)

        position = oms.get_position(instrument)
        assert position is not None
        assert position.quantity == Decimal("0")
        # Short PnL: (200 - 180) * 5 = 100
        assert position.realized_pnl == Decimal("100")

    @pytest.mark.asyncio
    async def test_position_flip_long_to_short(
        self, oms: OrderManagementSystem, bus: EventBus
    ) -> None:
        """Selling more than current long position should flip to short."""
        instrument = _make_instrument()

        # Open long: buy 10 @ 100.
        buy_request = _make_order_request(
            instrument=instrument, side=Side.BUY, quantity=Decimal("10")
        )
        buy_id = await oms.submit_order(buy_request)

        buy_fill = _make_fill_event(
            order_id=buy_id,
            instrument=instrument,
            side=Side.BUY,
            fill_price=Decimal("100"),
            fill_quantity=Decimal("10"),
            cumulative_quantity=Decimal("10"),
        )
        await bus.publish(buy_fill)
        await asyncio.sleep(0.05)

        # Flip: sell 15 @ 110 (close 10 long, open 5 short).
        sell_request = _make_order_request(
            instrument=instrument, side=Side.SELL, quantity=Decimal("15")
        )
        sell_id = await oms.submit_order(sell_request)

        sell_fill = _make_fill_event(
            order_id=sell_id,
            instrument=instrument,
            side=Side.SELL,
            fill_price=Decimal("110"),
            fill_quantity=Decimal("15"),
            cumulative_quantity=Decimal("15"),
        )
        await bus.publish(sell_fill)
        await asyncio.sleep(0.05)

        position = oms.get_position(instrument)
        assert position is not None
        # 10 - 15 = -5 (short 5)
        assert position.quantity == Decimal("-5")
        # Realized PnL from closing long: (110 - 100) * 10 = 100
        assert position.realized_pnl == Decimal("100")
        # New position avg entry is the flip fill price.
        assert position.avg_entry_price == Decimal("110")

    @pytest.mark.asyncio
    async def test_position_event_emitted_on_fill(
        self, oms: OrderManagementSystem, bus: EventBus
    ) -> None:
        """A PositionEvent should be emitted after each fill."""
        captured: list[PositionEvent] = []

        async def handler(event: PositionEvent) -> None:
            captured.append(event)

        bus.subscribe(PositionEvent, handler)

        instrument = _make_instrument()
        request = _make_order_request(instrument=instrument, side=Side.BUY, quantity=Decimal("10"))
        order_id = await oms.submit_order(request)

        fill = _make_fill_event(
            order_id=order_id,
            instrument=instrument,
            side=Side.BUY,
            fill_price=Decimal("150"),
            fill_quantity=Decimal("10"),
            cumulative_quantity=Decimal("10"),
        )
        await bus.publish(fill)
        await asyncio.sleep(0.1)

        assert len(captured) == 1
        assert captured[0].instrument == instrument
        assert captured[0].quantity == Decimal("10")
        assert captured[0].avg_price == Decimal("150")

    @pytest.mark.asyncio
    async def test_add_to_long_position(self, oms: OrderManagementSystem, bus: EventBus) -> None:
        """Buying more of an existing long should increase position with VWAP."""
        instrument = _make_instrument()

        # Buy 10 @ 100.
        r1 = _make_order_request(instrument=instrument, side=Side.BUY, quantity=Decimal("10"))
        id1 = await oms.submit_order(r1)
        f1 = _make_fill_event(
            order_id=id1,
            instrument=instrument,
            side=Side.BUY,
            fill_price=Decimal("100"),
            fill_quantity=Decimal("10"),
            cumulative_quantity=Decimal("10"),
        )
        await bus.publish(f1)
        await asyncio.sleep(0.05)

        # Buy 10 more @ 120.
        r2 = _make_order_request(instrument=instrument, side=Side.BUY, quantity=Decimal("10"))
        id2 = await oms.submit_order(r2)
        f2 = _make_fill_event(
            order_id=id2,
            instrument=instrument,
            side=Side.BUY,
            fill_price=Decimal("120"),
            fill_quantity=Decimal("10"),
            cumulative_quantity=Decimal("10"),
        )
        await bus.publish(f2)
        await asyncio.sleep(0.05)

        position = oms.get_position(instrument)
        assert position is not None
        assert position.quantity == Decimal("20")
        # VWAP: (100*10 + 120*10) / 20 = 110
        assert position.avg_entry_price == Decimal("110")
        assert position.realized_pnl == Decimal("0")

    @pytest.mark.asyncio
    async def test_multiple_instruments_tracked_independently(
        self, oms: OrderManagementSystem, bus: EventBus
    ) -> None:
        """Positions for different instruments should be independent."""
        inst_a = _make_instrument(symbol="AAPL")
        inst_b = _make_instrument(symbol="MSFT")

        # Buy AAPL.
        ra = _make_order_request(instrument=inst_a, side=Side.BUY, quantity=Decimal("10"))
        ida = await oms.submit_order(ra)
        fa = _make_fill_event(
            order_id=ida,
            instrument=inst_a,
            side=Side.BUY,
            fill_price=Decimal("150"),
            fill_quantity=Decimal("10"),
            cumulative_quantity=Decimal("10"),
        )
        await bus.publish(fa)
        await asyncio.sleep(0.05)

        # Sell MSFT.
        rb = _make_order_request(instrument=inst_b, side=Side.SELL, quantity=Decimal("5"))
        idb = await oms.submit_order(rb)
        fb = _make_fill_event(
            order_id=idb,
            instrument=inst_b,
            side=Side.SELL,
            fill_price=Decimal("300"),
            fill_quantity=Decimal("5"),
            cumulative_quantity=Decimal("5"),
        )
        await bus.publish(fb)
        await asyncio.sleep(0.05)

        pos_a = oms.get_position(inst_a)
        pos_b = oms.get_position(inst_b)

        assert pos_a is not None
        assert pos_a.quantity == Decimal("10")
        assert pos_a.avg_entry_price == Decimal("150")

        assert pos_b is not None
        assert pos_b.quantity == Decimal("-5")
        assert pos_b.avg_entry_price == Decimal("300")


class TestOMSQueries:
    """Tests for OMS query methods."""

    @pytest.mark.asyncio
    async def test_get_order_returns_none_for_unknown(self, oms: OrderManagementSystem) -> None:
        """get_order should return None for unknown order IDs."""
        assert oms.get_order("nonexistent") is None

    @pytest.mark.asyncio
    async def test_get_position_returns_none_for_unknown(self, oms: OrderManagementSystem) -> None:
        """get_position should return None for instruments with no position."""
        instrument = _make_instrument(symbol="UNKNOWN")
        assert oms.get_position(instrument) is None

    @pytest.mark.asyncio
    async def test_get_all_orders(self, oms: OrderManagementSystem) -> None:
        """get_all_orders should return a copy of all tracked orders."""
        r1 = _make_order_request(quantity=Decimal("10"))
        r2 = _make_order_request(quantity=Decimal("20"))

        await oms.submit_order(r1)
        await oms.submit_order(r2)

        all_orders = oms.get_all_orders()
        assert len(all_orders) == 2
        # Verify it's a copy (modifying returned dict doesn't affect OMS).
        all_orders.clear()
        assert len(oms.get_all_orders()) == 2

    @pytest.mark.asyncio
    async def test_get_all_positions(self, oms: OrderManagementSystem, bus: EventBus) -> None:
        """get_all_positions should return a copy of all positions."""
        instrument = _make_instrument()
        request = _make_order_request(instrument=instrument, side=Side.BUY, quantity=Decimal("10"))
        order_id = await oms.submit_order(request)

        fill = _make_fill_event(
            order_id=order_id,
            instrument=instrument,
            side=Side.BUY,
            fill_price=Decimal("100"),
            fill_quantity=Decimal("10"),
            cumulative_quantity=Decimal("10"),
        )
        await bus.publish(fill)
        await asyncio.sleep(0.05)

        all_positions = oms.get_all_positions()
        assert len(all_positions) == 1
        assert instrument in all_positions
        # Verify it's a copy.
        all_positions.clear()
        assert len(oms.get_all_positions()) == 1

    @pytest.mark.asyncio
    async def test_fill_for_unknown_order_ignored(
        self, oms: OrderManagementSystem, bus: EventBus
    ) -> None:
        """A fill for an unknown order_id should be logged and ignored."""
        instrument = _make_instrument()
        fill = _make_fill_event(
            order_id="nonexistent-order",
            instrument=instrument,
            side=Side.BUY,
            fill_price=Decimal("100"),
            fill_quantity=Decimal("10"),
            cumulative_quantity=Decimal("10"),
        )
        await bus.publish(fill)
        await asyncio.sleep(0.05)

        # No positions should be created for unknown orders.
        assert len(oms.get_all_positions()) == 0

    @pytest.mark.asyncio
    async def test_partial_close_long(self, oms: OrderManagementSystem, bus: EventBus) -> None:
        """Partially closing a long should reduce position and compute PnL."""
        instrument = _make_instrument()

        # Open long: buy 10 @ 100.
        buy_request = _make_order_request(
            instrument=instrument, side=Side.BUY, quantity=Decimal("10")
        )
        buy_id = await oms.submit_order(buy_request)
        buy_fill = _make_fill_event(
            order_id=buy_id,
            instrument=instrument,
            side=Side.BUY,
            fill_price=Decimal("100"),
            fill_quantity=Decimal("10"),
            cumulative_quantity=Decimal("10"),
        )
        await bus.publish(buy_fill)
        await asyncio.sleep(0.05)

        # Partially close: sell 4 @ 110.
        sell_request = _make_order_request(
            instrument=instrument, side=Side.SELL, quantity=Decimal("4")
        )
        sell_id = await oms.submit_order(sell_request)
        sell_fill = _make_fill_event(
            order_id=sell_id,
            instrument=instrument,
            side=Side.SELL,
            fill_price=Decimal("110"),
            fill_quantity=Decimal("4"),
            cumulative_quantity=Decimal("4"),
        )
        await bus.publish(sell_fill)
        await asyncio.sleep(0.05)

        position = oms.get_position(instrument)
        assert position is not None
        assert position.quantity == Decimal("6")  # 10 - 4
        assert position.avg_entry_price == Decimal("100")  # Unchanged
        # Realized: (110 - 100) * 4 = 40
        assert position.realized_pnl == Decimal("40")
