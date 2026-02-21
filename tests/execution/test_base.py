"""Tests for VenueAdapter ABC."""

from __future__ import annotations

from decimal import Decimal

import pytest

from sysls.core.types import (
    AssetClass,
    Instrument,
    OrderRequest,
    OrderStatus,
    OrderType,
    Side,
    Venue,
)
from sysls.execution.venues.base import VenueAdapter


class MockVenueAdapter(VenueAdapter):
    """Concrete mock implementation of VenueAdapter for testing.

    Tracks connect/disconnect calls and provides configurable behavior
    for order submission and position queries.
    """

    def __init__(self, name: str = "mock") -> None:
        self._name = name
        self._connected = False
        self.connect_count = 0
        self.disconnect_count = 0
        self.submitted_orders: list[OrderRequest] = []
        self.cancelled_orders: list[str] = []
        self._positions: dict[Instrument, Decimal] = {}
        self._balances: dict[str, Decimal] = {"USD": Decimal("100000")}
        self._next_venue_order_id = 1
        self._submit_error: Exception | None = None

    async def connect(self) -> None:
        """Establish connection to the mock venue."""
        self._connected = True
        self.connect_count += 1

    async def disconnect(self) -> None:
        """Disconnect from the mock venue."""
        self._connected = False
        self.disconnect_count += 1

    @property
    def name(self) -> str:
        """Human-readable venue name."""
        return self._name

    @property
    def is_connected(self) -> bool:
        """Whether the adapter has an active connection."""
        return self._connected

    @property
    def supported_order_types(self) -> list[OrderType]:
        """Order types supported by this mock venue."""
        return [OrderType.MARKET, OrderType.LIMIT]

    async def submit_order(self, order: OrderRequest) -> str:
        """Submit an order and return a mock venue order ID."""
        if self._submit_error is not None:
            raise self._submit_error
        self.submitted_orders.append(order)
        venue_id = f"MOCK-{self._next_venue_order_id}"
        self._next_venue_order_id += 1
        return venue_id

    async def cancel_order(self, venue_order_id: str, instrument: Instrument) -> None:
        """Cancel an order at the mock venue."""
        self.cancelled_orders.append(venue_order_id)

    async def get_order_status(self, venue_order_id: str, instrument: Instrument) -> OrderStatus:
        """Query order status at the mock venue."""
        return OrderStatus.ACCEPTED

    async def get_positions(self) -> dict[Instrument, Decimal]:
        """Get all mock positions."""
        return dict(self._positions)

    async def get_balances(self) -> dict[str, Decimal]:
        """Get mock account balances."""
        return dict(self._balances)

    def set_position(self, instrument: Instrument, quantity: Decimal) -> None:
        """Set a mock position for testing."""
        self._positions[instrument] = quantity

    def set_submit_error(self, error: Exception) -> None:
        """Configure the next submit_order call to raise an error."""
        self._submit_error = error

    def clear_submit_error(self) -> None:
        """Clear any configured submit error."""
        self._submit_error = None


class TestVenueAdapterABC:
    """Tests for VenueAdapter abstract base class."""

    def test_cannot_instantiate_directly(self) -> None:
        """VenueAdapter cannot be instantiated because it is abstract."""
        with pytest.raises(TypeError):
            VenueAdapter()  # type: ignore[abstract]

    @pytest.mark.asyncio
    async def test_aenter_calls_connect(self) -> None:
        """__aenter__ should call connect and return self."""
        adapter = MockVenueAdapter()
        assert adapter.connect_count == 0
        assert not adapter.is_connected

        result = await adapter.__aenter__()

        assert result is adapter
        assert adapter.connect_count == 1
        assert adapter.is_connected

    @pytest.mark.asyncio
    async def test_aexit_calls_disconnect(self) -> None:
        """__aexit__ should call disconnect."""
        adapter = MockVenueAdapter()
        await adapter.connect()
        assert adapter.is_connected

        await adapter.__aexit__(None, None, None)

        assert not adapter.is_connected
        assert adapter.disconnect_count == 1

    @pytest.mark.asyncio
    async def test_context_manager_protocol(self) -> None:
        """VenueAdapter should work as an async context manager."""
        adapter = MockVenueAdapter()

        async with adapter as ctx:
            assert ctx is adapter
            assert adapter.is_connected
            assert adapter.connect_count == 1

        assert not adapter.is_connected
        assert adapter.disconnect_count == 1

    @pytest.mark.asyncio
    async def test_context_manager_disconnect_on_exception(self) -> None:
        """VenueAdapter should disconnect even if an exception occurs."""
        adapter = MockVenueAdapter()

        with pytest.raises(RuntimeError, match="test error"):
            async with adapter:
                assert adapter.is_connected
                raise RuntimeError("test error")

        assert not adapter.is_connected
        assert adapter.disconnect_count == 1


class TestMockVenueAdapter:
    """Tests for the MockVenueAdapter itself, validating test infrastructure."""

    def test_name_property(self) -> None:
        """Mock venue should return the configured name."""
        adapter = MockVenueAdapter(name="test-venue")
        assert adapter.name == "test-venue"

    def test_supported_order_types(self) -> None:
        """Mock venue should report supported order types."""
        adapter = MockVenueAdapter()
        types = adapter.supported_order_types
        assert OrderType.MARKET in types
        assert OrderType.LIMIT in types

    @pytest.mark.asyncio
    async def test_submit_order(self) -> None:
        """Mock venue should track submitted orders and return IDs."""
        adapter = MockVenueAdapter()
        instrument = Instrument(
            symbol="TEST",
            asset_class=AssetClass.EQUITY,
            venue=Venue.PAPER,
        )
        order = OrderRequest(
            instrument=instrument,
            side=Side.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("10"),
        )

        venue_id = await adapter.submit_order(order)

        assert venue_id == "MOCK-1"
        assert len(adapter.submitted_orders) == 1
        assert adapter.submitted_orders[0] is order

    @pytest.mark.asyncio
    async def test_cancel_order(self) -> None:
        """Mock venue should track cancelled orders."""
        adapter = MockVenueAdapter()
        instrument = Instrument(
            symbol="TEST",
            asset_class=AssetClass.EQUITY,
            venue=Venue.PAPER,
        )

        await adapter.cancel_order("MOCK-1", instrument)

        assert "MOCK-1" in adapter.cancelled_orders

    @pytest.mark.asyncio
    async def test_get_positions_empty(self) -> None:
        """Mock venue should return empty positions by default."""
        adapter = MockVenueAdapter()
        positions = await adapter.get_positions()
        assert positions == {}

    @pytest.mark.asyncio
    async def test_get_positions_with_data(self) -> None:
        """Mock venue should return configured positions."""
        adapter = MockVenueAdapter()
        instrument = Instrument(
            symbol="TEST",
            asset_class=AssetClass.EQUITY,
            venue=Venue.PAPER,
        )
        adapter.set_position(instrument, Decimal("100"))

        positions = await adapter.get_positions()

        assert positions[instrument] == Decimal("100")

    @pytest.mark.asyncio
    async def test_get_balances(self) -> None:
        """Mock venue should return default balances."""
        adapter = MockVenueAdapter()
        balances = await adapter.get_balances()
        assert balances["USD"] == Decimal("100000")
