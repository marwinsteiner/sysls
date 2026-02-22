"""Tests for the TastytradeAdapter venue adapter.

All tests use mocked tastytrade SDK -- no real API calls are made.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sysls.core.bus import EventBus
from sysls.core.events import OrderAccepted, OrderCancelled
from sysls.core.exceptions import ConnectionError as SyslsConnectionError
from sysls.core.exceptions import OrderError, VenueError
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
from sysls.execution.venues.tastytrade import (
    TastytradeAdapter,
    _map_tt_status,
)

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_equity_instrument(
    symbol: str = "AAPL",
    currency: str = "USD",
) -> Instrument:
    """Create a test equity instrument."""
    return Instrument(
        symbol=symbol,
        asset_class=AssetClass.EQUITY,
        venue=Venue.TASTYTRADE,
        currency=currency,
    )


def _make_order(
    instrument: Instrument | None = None,
    side: Side = Side.BUY,
    order_type: OrderType = OrderType.MARKET,
    quantity: Decimal = Decimal("100"),
    price: Decimal | None = None,
    stop_price: Decimal | None = None,
) -> OrderRequest:
    """Create a test order request."""
    return OrderRequest(
        instrument=instrument or _make_equity_instrument(),
        side=side,
        order_type=order_type,
        quantity=quantity,
        price=price,
        stop_price=stop_price,
        time_in_force=TimeInForce.GTC,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def event_bus() -> EventBus:
    """Create a fresh EventBus."""
    return EventBus()


# ---------------------------------------------------------------------------
# Properties and basic instantiation
# ---------------------------------------------------------------------------


class TestProperties:
    """Test basic adapter properties."""

    def test_name_property(self, event_bus: EventBus) -> None:
        """name should return 'tastytrade'."""
        adapter = TastytradeAdapter(
            bus=event_bus, login="user", password="pass"
        )
        assert adapter.name == "tastytrade"

    def test_is_connected_false_when_not_connected(self, event_bus: EventBus) -> None:
        """is_connected should be False before connect()."""
        adapter = TastytradeAdapter(
            bus=event_bus, login="user", password="pass"
        )
        assert not adapter.is_connected

    def test_supported_order_types(self, event_bus: EventBus) -> None:
        """supported_order_types should include MARKET, LIMIT, STOP, STOP_LIMIT."""
        adapter = TastytradeAdapter(
            bus=event_bus, login="user", password="pass"
        )
        types = adapter.supported_order_types
        assert OrderType.MARKET in types
        assert OrderType.LIMIT in types
        assert OrderType.STOP in types
        assert OrderType.STOP_LIMIT in types

    def test_require_session_raises_when_not_connected(
        self, event_bus: EventBus
    ) -> None:
        """_require_session should raise VenueError when not connected."""
        adapter = TastytradeAdapter(
            bus=event_bus, login="user", password="pass"
        )
        with pytest.raises(VenueError, match="Not connected"):
            adapter._require_session()


# ---------------------------------------------------------------------------
# Status mapping
# ---------------------------------------------------------------------------


class TestStatusMapping:
    """Test tastytrade status string to sysls OrderStatus mapping."""

    def test_received(self) -> None:
        """'Received' should map to SUBMITTED."""
        assert _map_tt_status("Received") == OrderStatus.SUBMITTED

    def test_routed(self) -> None:
        """'Routed' should map to ACCEPTED."""
        assert _map_tt_status("Routed") == OrderStatus.ACCEPTED

    def test_in_flight(self) -> None:
        """'In Flight' should map to ACCEPTED."""
        assert _map_tt_status("In Flight") == OrderStatus.ACCEPTED

    def test_live(self) -> None:
        """'Live' should map to ACCEPTED."""
        assert _map_tt_status("Live") == OrderStatus.ACCEPTED

    def test_filled(self) -> None:
        """'Filled' should map to FILLED."""
        assert _map_tt_status("Filled") == OrderStatus.FILLED

    def test_cancelled(self) -> None:
        """'Cancelled' should map to CANCELLED."""
        assert _map_tt_status("Cancelled") == OrderStatus.CANCELLED

    def test_cancel_requested(self) -> None:
        """'Cancel Requested' should map to ACCEPTED."""
        assert _map_tt_status("Cancel Requested") == OrderStatus.ACCEPTED

    def test_rejected(self) -> None:
        """'Rejected' should map to REJECTED."""
        assert _map_tt_status("Rejected") == OrderStatus.REJECTED

    def test_expired(self) -> None:
        """'Expired' should map to EXPIRED."""
        assert _map_tt_status("Expired") == OrderStatus.EXPIRED

    def test_contingent(self) -> None:
        """'Contingent' should map to PENDING."""
        assert _map_tt_status("Contingent") == OrderStatus.PENDING

    def test_replace_requested(self) -> None:
        """'Replace Requested' should map to ACCEPTED."""
        assert _map_tt_status("Replace Requested") == OrderStatus.ACCEPTED

    def test_removed(self) -> None:
        """'Removed' should map to CANCELLED."""
        assert _map_tt_status("Removed") == OrderStatus.CANCELLED

    def test_partially_removed(self) -> None:
        """'Partially Removed' should map to CANCELLED."""
        assert _map_tt_status("Partially Removed") == OrderStatus.CANCELLED

    def test_unknown_status(self) -> None:
        """Unknown status should map to PENDING."""
        assert _map_tt_status("SomeUnknownStatus") == OrderStatus.PENDING
