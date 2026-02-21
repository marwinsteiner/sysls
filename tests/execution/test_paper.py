"""Tests for the PaperVenue adapter.

Validates the paper trading adapter's order lifecycle, fill simulation,
position/balance tracking, and event bus integration.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import patch

import pytest

from sysls.core.bus import EventBus
from sysls.core.events import FillEvent, OrderAccepted, OrderCancelled
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
from sysls.execution.paper import PaperVenue

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_instrument(symbol: str = "BTC-USDT", currency: str = "USD") -> Instrument:
    """Create a test instrument."""
    return Instrument(
        symbol=symbol,
        asset_class=AssetClass.CRYPTO_SPOT,
        venue=Venue.PAPER,
        currency=currency,
    )


def _make_order(
    instrument: Instrument | None = None,
    side: Side = Side.BUY,
    order_type: OrderType = OrderType.MARKET,
    quantity: Decimal = Decimal("10"),
    price: Decimal | None = None,
) -> OrderRequest:
    """Create a test order request."""
    return OrderRequest(
        instrument=instrument or _make_instrument(),
        side=side,
        order_type=order_type,
        quantity=quantity,
        price=price,
        time_in_force=TimeInForce.GTC,
    )


@pytest.fixture
def event_bus() -> EventBus:
    """Create a fresh EventBus for testing."""
    return EventBus()


@pytest.fixture
def paper_venue(event_bus: EventBus) -> PaperVenue:
    """Create a PaperVenue with default settings."""
    return PaperVenue(bus=event_bus)


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_disconnect_lifecycle(paper_venue: PaperVenue) -> None:
    """PaperVenue should toggle connected state on connect/disconnect."""
    assert not paper_venue.is_connected

    await paper_venue.connect()
    assert paper_venue.is_connected

    await paper_venue.disconnect()
    assert not paper_venue.is_connected


@pytest.mark.asyncio
async def test_context_manager(event_bus: EventBus) -> None:
    """PaperVenue should support async context manager protocol."""
    venue = PaperVenue(bus=event_bus)

    assert not venue.is_connected

    async with venue as v:
        assert v is venue
        assert venue.is_connected

    assert not venue.is_connected


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


def test_name_property(paper_venue: PaperVenue) -> None:
    """PaperVenue.name should return 'paper'."""
    assert paper_venue.name == "paper"


def test_supported_order_types(paper_venue: PaperVenue) -> None:
    """PaperVenue should support all four order types."""
    types = paper_venue.supported_order_types
    assert OrderType.MARKET in types
    assert OrderType.LIMIT in types
    assert OrderType.STOP in types
    assert OrderType.STOP_LIMIT in types
    assert len(types) == 4


def test_initial_balances_default(event_bus: EventBus) -> None:
    """Default initial balance should be 100000 USD."""
    venue = PaperVenue(bus=event_bus)
    # Access internal state for testing.
    assert venue._balances == {"USD": Decimal("100000")}


def test_initial_balances_custom(event_bus: EventBus) -> None:
    """Custom initial balances should be set correctly."""
    balances = {"USD": Decimal("50000"), "BTC": Decimal("1.5")}
    venue = PaperVenue(bus=event_bus, initial_balances=balances)
    assert venue._balances == balances


# ---------------------------------------------------------------------------
# Market order fill tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_market_order_fills_immediately(event_bus: EventBus) -> None:
    """Market orders should be filled immediately with events emitted."""
    received_events: list[object] = []

    async def capture_accepted(event: OrderAccepted) -> None:
        received_events.append(event)

    async def capture_fill(event: FillEvent) -> None:
        received_events.append(event)

    event_bus.subscribe(OrderAccepted, capture_accepted)
    event_bus.subscribe(FillEvent, capture_fill)
    await event_bus.start()

    venue = PaperVenue(bus=event_bus)
    await venue.connect()

    order = _make_order(price=Decimal("50000"))
    venue_order_id = await venue.submit_order(order)

    # Allow the bus to dispatch.
    await asyncio.sleep(0.05)
    await event_bus.stop()

    assert venue_order_id.startswith("PAPER-")
    assert len(venue_order_id) == 18  # "PAPER-" + 12 hex chars

    # Should have received OrderAccepted + FillEvent.
    accepted_events = [e for e in received_events if isinstance(e, OrderAccepted)]
    fill_events = [e for e in received_events if isinstance(e, FillEvent)]

    assert len(accepted_events) == 1
    assert accepted_events[0].order_id == order.order_id
    assert accepted_events[0].venue_order_id == venue_order_id

    assert len(fill_events) == 1
    fill = fill_events[0]
    assert fill.order_id == order.order_id
    assert fill.fill_price == Decimal("50000")
    assert fill.fill_quantity == Decimal("10")
    assert fill.cumulative_quantity == Decimal("10")
    assert fill.order_status == OrderStatus.FILLED
    assert fill.side == Side.BUY


@pytest.mark.asyncio
async def test_market_order_default_fill_price(event_bus: EventBus) -> None:
    """Market order without price should use default fill price of 100."""
    fills: list[FillEvent] = []

    async def capture_fill(event: FillEvent) -> None:
        fills.append(event)

    event_bus.subscribe(FillEvent, capture_fill)
    await event_bus.start()

    venue = PaperVenue(bus=event_bus)
    await venue.connect()

    order = _make_order(price=None)  # No price for market order
    await venue.submit_order(order)

    await asyncio.sleep(0.05)
    await event_bus.stop()

    assert len(fills) == 1
    assert fills[0].fill_price == Decimal("100")


# ---------------------------------------------------------------------------
# Limit order tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_limit_order_accepted_no_fill(event_bus: EventBus) -> None:
    """Limit orders should be accepted but not filled (no matching in Phase 2)."""
    fills: list[FillEvent] = []
    accepted: list[OrderAccepted] = []

    async def capture_fill(event: FillEvent) -> None:
        fills.append(event)

    async def capture_accepted(event: OrderAccepted) -> None:
        accepted.append(event)

    event_bus.subscribe(FillEvent, capture_fill)
    event_bus.subscribe(OrderAccepted, capture_accepted)
    await event_bus.start()

    venue = PaperVenue(bus=event_bus)
    await venue.connect()

    order = _make_order(
        order_type=OrderType.LIMIT,
        price=Decimal("49000"),
    )
    venue_order_id = await venue.submit_order(order)

    await asyncio.sleep(0.05)
    await event_bus.stop()

    # Accepted but no fill.
    assert len(accepted) == 1
    assert accepted[0].venue_order_id == venue_order_id
    assert len(fills) == 0


# ---------------------------------------------------------------------------
# Cancel order tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_order(event_bus: EventBus) -> None:
    """Cancelling an order should emit OrderCancelled and update status."""
    cancelled_events: list[OrderCancelled] = []

    async def capture_cancelled(event: OrderCancelled) -> None:
        cancelled_events.append(event)

    event_bus.subscribe(OrderCancelled, capture_cancelled)
    await event_bus.start()

    venue = PaperVenue(bus=event_bus)
    await venue.connect()

    # Submit a limit order (won't be filled).
    order = _make_order(order_type=OrderType.LIMIT, price=Decimal("49000"))
    venue_order_id = await venue.submit_order(order)

    # Cancel it.
    await venue.cancel_order(venue_order_id, order.instrument)

    await asyncio.sleep(0.05)
    await event_bus.stop()

    assert len(cancelled_events) == 1
    assert cancelled_events[0].order_id == order.order_id
    assert cancelled_events[0].reason == "Cancelled by user"

    # Order status should be CANCELLED.
    status = await venue.get_order_status(venue_order_id, order.instrument)
    assert status == OrderStatus.CANCELLED


@pytest.mark.asyncio
async def test_cancel_unknown_order_raises(event_bus: EventBus) -> None:
    """Cancelling a non-existent order should raise OrderError."""
    await event_bus.start()

    venue = PaperVenue(bus=event_bus)
    await venue.connect()

    with pytest.raises(OrderError, match="not found"):
        await venue.cancel_order("PAPER-nonexistent", _make_instrument())

    await asyncio.sleep(0.05)
    await event_bus.stop()


# ---------------------------------------------------------------------------
# Order status tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_order_status(event_bus: EventBus) -> None:
    """get_order_status should return the current status of a tracked order."""
    await event_bus.start()

    venue = PaperVenue(bus=event_bus)
    await venue.connect()

    # Submit a limit order (stays ACCEPTED).
    order = _make_order(order_type=OrderType.LIMIT, price=Decimal("49000"))
    venue_order_id = await venue.submit_order(order)

    status = await venue.get_order_status(venue_order_id, order.instrument)
    assert status == OrderStatus.ACCEPTED

    await asyncio.sleep(0.05)
    await event_bus.stop()


@pytest.mark.asyncio
async def test_get_order_status_unknown_raises(event_bus: EventBus) -> None:
    """Querying status of a non-existent order should raise OrderError."""
    await event_bus.start()

    venue = PaperVenue(bus=event_bus)
    await venue.connect()

    with pytest.raises(OrderError, match="not found"):
        await venue.get_order_status("PAPER-unknown", _make_instrument())

    await asyncio.sleep(0.05)
    await event_bus.stop()


@pytest.mark.asyncio
async def test_market_order_status_is_filled(event_bus: EventBus) -> None:
    """Market orders should have FILLED status after execution."""
    await event_bus.start()

    venue = PaperVenue(bus=event_bus)
    await venue.connect()

    order = _make_order(price=Decimal("100"))
    venue_order_id = await venue.submit_order(order)

    await asyncio.sleep(0.05)

    status = await venue.get_order_status(venue_order_id, order.instrument)
    assert status == OrderStatus.FILLED

    await event_bus.stop()


# ---------------------------------------------------------------------------
# Position and balance tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_positions_after_buy_fill(event_bus: EventBus) -> None:
    """Positions should reflect bought quantity after a market order fill."""
    await event_bus.start()

    venue = PaperVenue(bus=event_bus)
    await venue.connect()

    instrument = _make_instrument()
    order = _make_order(
        instrument=instrument, side=Side.BUY, quantity=Decimal("5"), price=Decimal("100")
    )
    await venue.submit_order(order)

    await asyncio.sleep(0.05)

    positions = await venue.get_positions()
    assert positions[instrument] == Decimal("5")

    await event_bus.stop()


@pytest.mark.asyncio
async def test_get_positions_after_sell_fill(event_bus: EventBus) -> None:
    """Positions should reflect negative quantity after a sell order fill."""
    await event_bus.start()

    venue = PaperVenue(bus=event_bus)
    await venue.connect()

    instrument = _make_instrument()
    order = _make_order(
        instrument=instrument, side=Side.SELL, quantity=Decimal("3"), price=Decimal("100")
    )
    await venue.submit_order(order)

    await asyncio.sleep(0.05)

    positions = await venue.get_positions()
    assert positions[instrument] == Decimal("-3")

    await event_bus.stop()


@pytest.mark.asyncio
async def test_get_balances_after_buy_fill(event_bus: EventBus) -> None:
    """Balances should decrease by notional after a buy fill."""
    await event_bus.start()

    venue = PaperVenue(
        bus=event_bus,
        initial_balances={"USD": Decimal("100000")},
    )
    await venue.connect()

    order = _make_order(side=Side.BUY, quantity=Decimal("10"), price=Decimal("50"))
    await venue.submit_order(order)

    await asyncio.sleep(0.05)

    balances = await venue.get_balances()
    # 100000 - (10 * 50) = 99500
    assert balances["USD"] == Decimal("99500")

    await event_bus.stop()


@pytest.mark.asyncio
async def test_get_balances_after_sell_fill(event_bus: EventBus) -> None:
    """Balances should increase by notional after a sell fill."""
    await event_bus.start()

    venue = PaperVenue(
        bus=event_bus,
        initial_balances={"USD": Decimal("100000")},
    )
    await venue.connect()

    order = _make_order(side=Side.SELL, quantity=Decimal("5"), price=Decimal("200"))
    await venue.submit_order(order)

    await asyncio.sleep(0.05)

    balances = await venue.get_balances()
    # 100000 + (5 * 200) = 101000
    assert balances["USD"] == Decimal("101000")

    await event_bus.stop()


# ---------------------------------------------------------------------------
# Partial fill tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partial_fill_probability(event_bus: EventBus) -> None:
    """With partial_fill_probability=1.0, orders should always get two fills."""
    fills: list[FillEvent] = []

    async def capture_fill(event: FillEvent) -> None:
        fills.append(event)

    event_bus.subscribe(FillEvent, capture_fill)
    await event_bus.start()

    venue = PaperVenue(bus=event_bus, partial_fill_probability=1.0)
    await venue.connect()

    order = _make_order(quantity=Decimal("10"), price=Decimal("100"))
    await venue.submit_order(order)

    await asyncio.sleep(0.05)
    await event_bus.stop()

    assert len(fills) == 2

    # First fill: partial (50% = 5)
    assert fills[0].fill_quantity == Decimal("5")
    assert fills[0].cumulative_quantity == Decimal("5")
    assert fills[0].order_status == OrderStatus.PARTIALLY_FILLED

    # Second fill: remaining (5)
    assert fills[1].fill_quantity == Decimal("5")
    assert fills[1].cumulative_quantity == Decimal("10")
    assert fills[1].order_status == OrderStatus.FILLED


@pytest.mark.asyncio
async def test_no_partial_fill_when_probability_zero(event_bus: EventBus) -> None:
    """With partial_fill_probability=0.0, orders should always get one fill."""
    fills: list[FillEvent] = []

    async def capture_fill(event: FillEvent) -> None:
        fills.append(event)

    event_bus.subscribe(FillEvent, capture_fill)
    await event_bus.start()

    venue = PaperVenue(bus=event_bus, partial_fill_probability=0.0)
    await venue.connect()

    order = _make_order(quantity=Decimal("10"), price=Decimal("100"))
    await venue.submit_order(order)

    await asyncio.sleep(0.05)
    await event_bus.stop()

    assert len(fills) == 1
    assert fills[0].fill_quantity == Decimal("10")
    assert fills[0].order_status == OrderStatus.FILLED


@pytest.mark.asyncio
async def test_partial_fill_with_mock_random(event_bus: EventBus) -> None:
    """Partial fill should trigger when random() < partial_fill_probability."""
    fills: list[FillEvent] = []

    async def capture_fill(event: FillEvent) -> None:
        fills.append(event)

    event_bus.subscribe(FillEvent, capture_fill)
    await event_bus.start()

    venue = PaperVenue(bus=event_bus, partial_fill_probability=0.5)
    await venue.connect()

    # Mock random.random() to return 0.3 (below 0.5 threshold => partial fill)
    with patch("sysls.execution.paper.random.random", return_value=0.3):
        order = _make_order(quantity=Decimal("20"), price=Decimal("100"))
        await venue.submit_order(order)

    await asyncio.sleep(0.05)
    await event_bus.stop()

    assert len(fills) == 2
    assert fills[0].fill_quantity == Decimal("10")
    assert fills[1].fill_quantity == Decimal("10")


# ---------------------------------------------------------------------------
# Fill latency test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fill_latency(event_bus: EventBus) -> None:
    """Fill latency should delay fill execution."""
    fills: list[FillEvent] = []

    async def capture_fill(event: FillEvent) -> None:
        fills.append(event)

    event_bus.subscribe(FillEvent, capture_fill)
    await event_bus.start()

    # Use 100ms latency.
    venue = PaperVenue(bus=event_bus, fill_latency_ms=100)
    await venue.connect()

    order = _make_order(price=Decimal("100"))
    await venue.submit_order(order)

    # Give time for the latency + bus dispatch.
    await asyncio.sleep(0.2)
    await event_bus.stop()

    # Fill should still happen, just with a delay.
    assert len(fills) == 1
    assert fills[0].fill_quantity == Decimal("10")


# ---------------------------------------------------------------------------
# Multiple orders test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multiple_orders_accumulate_positions(event_bus: EventBus) -> None:
    """Multiple orders for the same instrument should accumulate positions."""
    await event_bus.start()

    venue = PaperVenue(bus=event_bus)
    await venue.connect()

    instrument = _make_instrument()

    # Buy 5
    order1 = _make_order(
        instrument=instrument, side=Side.BUY, quantity=Decimal("5"), price=Decimal("100")
    )
    await venue.submit_order(order1)

    # Buy 3 more
    order2 = _make_order(
        instrument=instrument, side=Side.BUY, quantity=Decimal("3"), price=Decimal("100")
    )
    await venue.submit_order(order2)

    await asyncio.sleep(0.05)

    positions = await venue.get_positions()
    assert positions[instrument] == Decimal("8")

    await event_bus.stop()


@pytest.mark.asyncio
async def test_empty_positions_and_balances_initially(event_bus: EventBus) -> None:
    """Positions should be empty initially; balances should have defaults."""
    venue = PaperVenue(bus=event_bus)

    positions = await venue.get_positions()
    assert positions == {}

    balances = await venue.get_balances()
    assert balances == {"USD": Decimal("100000")}


@pytest.mark.asyncio
async def test_positions_and_balances_return_copies(event_bus: EventBus) -> None:
    """get_positions and get_balances should return copies, not references."""
    venue = PaperVenue(bus=event_bus)

    positions = await venue.get_positions()
    positions["fake"] = Decimal("999")  # type: ignore[index]
    assert await venue.get_positions() == {}

    balances = await venue.get_balances()
    balances["FAKE"] = Decimal("999")
    assert "FAKE" not in await venue.get_balances()
