"""Tests for the Position Reconciler."""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
import pytest_asyncio
from pydantic import ValidationError

from sysls.core.bus import EventBus
from sysls.core.events import FillEvent
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
from sysls.execution.oms import OrderManagementSystem
from sysls.execution.reconcile import (
    PositionDiscrepancy,
    PositionReconciler,
    ReconciliationReport,
)
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
    instrument: Instrument,
    side: Side = Side.BUY,
    quantity: Decimal = Decimal("10"),
) -> OrderRequest:
    """Create a test order request."""
    return OrderRequest(
        instrument=instrument,
        side=side,
        order_type=OrderType.MARKET,
        quantity=quantity,
        time_in_force=TimeInForce.GTC,
    )


async def _submit_and_fill(
    oms: OrderManagementSystem,
    bus: EventBus,
    instrument: Instrument,
    side: Side,
    quantity: Decimal,
    price: Decimal,
) -> None:
    """Helper to submit an order and immediately fill it through the bus."""
    request = _make_order_request(instrument=instrument, side=side, quantity=quantity)
    order_id = await oms.submit_order(request)

    fill = FillEvent(
        order_id=order_id,
        instrument=instrument,
        side=side,
        fill_price=price,
        fill_quantity=quantity,
        cumulative_quantity=quantity,
        order_status=OrderStatus.FILLED,
        source="test",
    )
    await bus.publish(fill)
    await asyncio.sleep(0.05)


# -- Fixtures --------------------------------------------------------------


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


@pytest.fixture
def reconciler() -> PositionReconciler:
    """Create a PositionReconciler."""
    return PositionReconciler()


# -- Model Tests -----------------------------------------------------------


class TestPositionDiscrepancy:
    """Tests for the PositionDiscrepancy model."""

    def test_frozen(self) -> None:
        """PositionDiscrepancy should be immutable."""
        instrument = _make_instrument()
        disc = PositionDiscrepancy(
            instrument=instrument,
            oms_quantity=Decimal("10"),
            venue_quantity=Decimal("12"),
            difference=Decimal("2"),
        )
        with pytest.raises(ValidationError):
            disc.oms_quantity = Decimal("20")  # type: ignore[misc]


class TestReconciliationReport:
    """Tests for the ReconciliationReport model."""

    def test_frozen(self) -> None:
        """ReconciliationReport should be immutable."""
        report = ReconciliationReport(
            venue_name="test",
            is_consistent=True,
        )
        with pytest.raises(ValidationError):
            report.is_consistent = False  # type: ignore[misc]

    def test_default_empty_lists(self) -> None:
        """ReconciliationReport should default to empty lists."""
        report = ReconciliationReport(
            venue_name="test",
            is_consistent=True,
        )
        assert report.discrepancies == []
        assert report.oms_only == []
        assert report.venue_only == []


# -- Reconciliation Tests --------------------------------------------------


class TestPositionReconciler:
    """Tests for the reconciliation process."""

    @pytest.mark.asyncio
    async def test_both_empty_is_consistent(
        self,
        oms: OrderManagementSystem,
        venue: MockVenueAdapter,
        reconciler: PositionReconciler,
    ) -> None:
        """When both OMS and venue have no positions, report is consistent."""
        report = await reconciler.reconcile(oms, venue)

        assert report.is_consistent is True
        assert report.venue_name == "PAPER"
        assert report.discrepancies == []
        assert report.oms_only == []
        assert report.venue_only == []

    @pytest.mark.asyncio
    async def test_matching_positions_is_consistent(
        self,
        oms: OrderManagementSystem,
        bus: EventBus,
        venue: MockVenueAdapter,
        reconciler: PositionReconciler,
    ) -> None:
        """When OMS and venue positions match exactly, report is consistent."""
        instrument = _make_instrument()

        # Create OMS position via fill.
        await _submit_and_fill(oms, bus, instrument, Side.BUY, Decimal("10"), Decimal("100"))

        # Set matching venue position.
        venue.set_position(instrument, Decimal("10"))

        report = await reconciler.reconcile(oms, venue)

        assert report.is_consistent is True
        assert report.discrepancies == []

    @pytest.mark.asyncio
    async def test_quantity_mismatch(
        self,
        oms: OrderManagementSystem,
        bus: EventBus,
        venue: MockVenueAdapter,
        reconciler: PositionReconciler,
    ) -> None:
        """Quantity mismatch between OMS and venue should produce discrepancy."""
        instrument = _make_instrument()

        # OMS: long 10.
        await _submit_and_fill(oms, bus, instrument, Side.BUY, Decimal("10"), Decimal("100"))

        # Venue: long 12 (venue got 2 extra somehow).
        venue.set_position(instrument, Decimal("12"))

        report = await reconciler.reconcile(oms, venue)

        assert report.is_consistent is False
        assert len(report.discrepancies) == 1
        disc = report.discrepancies[0]
        assert disc.instrument == instrument
        assert disc.oms_quantity == Decimal("10")
        assert disc.venue_quantity == Decimal("12")
        assert disc.difference == Decimal("2")

    @pytest.mark.asyncio
    async def test_oms_only_position(
        self,
        oms: OrderManagementSystem,
        bus: EventBus,
        venue: MockVenueAdapter,
        reconciler: PositionReconciler,
    ) -> None:
        """Position in OMS but not at venue should be reported as oms_only."""
        instrument = _make_instrument()

        # OMS has a position.
        await _submit_and_fill(oms, bus, instrument, Side.BUY, Decimal("10"), Decimal("100"))

        # Venue has no positions (default).

        report = await reconciler.reconcile(oms, venue)

        assert report.is_consistent is False
        assert len(report.oms_only) == 1
        assert report.oms_only[0] == instrument
        assert report.venue_only == []

    @pytest.mark.asyncio
    async def test_venue_only_position(
        self,
        oms: OrderManagementSystem,
        venue: MockVenueAdapter,
        reconciler: PositionReconciler,
    ) -> None:
        """Position at venue but not in OMS should be reported as venue_only."""
        instrument = _make_instrument()

        # Venue has a position.
        venue.set_position(instrument, Decimal("5"))

        # OMS has no positions (default).

        report = await reconciler.reconcile(oms, venue)

        assert report.is_consistent is False
        assert report.oms_only == []
        assert len(report.venue_only) == 1
        assert report.venue_only[0] == instrument

    @pytest.mark.asyncio
    async def test_multiple_discrepancies(
        self,
        oms: OrderManagementSystem,
        bus: EventBus,
        venue: MockVenueAdapter,
        reconciler: PositionReconciler,
    ) -> None:
        """Multiple mismatches should all be reported."""
        inst_a = _make_instrument(symbol="AAPL")
        inst_b = _make_instrument(symbol="MSFT")
        inst_c = _make_instrument(symbol="GOOG")

        # OMS: AAPL=10, MSFT=-5.
        await _submit_and_fill(oms, bus, inst_a, Side.BUY, Decimal("10"), Decimal("150"))
        await _submit_and_fill(oms, bus, inst_b, Side.SELL, Decimal("5"), Decimal("300"))

        # Venue: AAPL=8 (mismatch), MSFT=-5 (match), GOOG=20 (venue-only).
        venue.set_position(inst_a, Decimal("8"))
        venue.set_position(inst_b, Decimal("-5"))
        venue.set_position(inst_c, Decimal("20"))

        report = await reconciler.reconcile(oms, venue)

        assert report.is_consistent is False

        # One discrepancy for AAPL.
        assert len(report.discrepancies) == 1
        assert report.discrepancies[0].instrument == inst_a
        assert report.discrepancies[0].difference == Decimal("-2")

        # No oms_only (both AAPL and MSFT are also at venue).
        assert report.oms_only == []

        # GOOG is venue-only.
        assert len(report.venue_only) == 1
        assert report.venue_only[0] == inst_c

    @pytest.mark.asyncio
    async def test_zero_quantity_positions_ignored(
        self,
        oms: OrderManagementSystem,
        bus: EventBus,
        venue: MockVenueAdapter,
        reconciler: PositionReconciler,
    ) -> None:
        """Zero-quantity positions should be filtered out before comparison."""
        instrument = _make_instrument()

        # Create and then close an OMS position (leaves zero quantity).
        await _submit_and_fill(oms, bus, instrument, Side.BUY, Decimal("10"), Decimal("100"))
        await _submit_and_fill(oms, bus, instrument, Side.SELL, Decimal("10"), Decimal("110"))

        # Set a zero-quantity venue position too.
        venue.set_position(instrument, Decimal("0"))

        report = await reconciler.reconcile(oms, venue)

        # Both sides have zero — should be consistent.
        assert report.is_consistent is True
        assert report.discrepancies == []
        assert report.oms_only == []
        assert report.venue_only == []
