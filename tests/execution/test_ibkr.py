"""Tests for the IbkrAdapter venue adapter.

All tests use mocked ib_async -- no real TWS/Gateway connection is made.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from sysls.core.bus import EventBus
from sysls.core.exceptions import VenueError
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
from sysls.execution.venues.ibkr import IbkrAdapter, _map_ib_status

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
        venue=Venue.IBKR,
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


def test_name_property(event_bus: EventBus) -> None:
    """name should return 'ibkr'."""
    adapter = IbkrAdapter(bus=event_bus)
    assert adapter.name == "ibkr"


def test_is_connected_false_when_not_connected(event_bus: EventBus) -> None:
    """is_connected should be False before connect()."""
    adapter = IbkrAdapter(bus=event_bus)
    assert not adapter.is_connected


def test_supported_order_types(event_bus: EventBus) -> None:
    """supported_order_types should include MARKET, LIMIT, STOP, STOP_LIMIT."""
    adapter = IbkrAdapter(bus=event_bus)
    types = adapter.supported_order_types
    assert OrderType.MARKET in types
    assert OrderType.LIMIT in types
    assert OrderType.STOP in types
    assert OrderType.STOP_LIMIT in types


def test_require_ib_raises_when_not_connected(event_bus: EventBus) -> None:
    """_require_ib should raise VenueError when not connected."""
    adapter = IbkrAdapter(bus=event_bus)
    with pytest.raises(VenueError, match="Not connected"):
        adapter._require_ib()


# ---------------------------------------------------------------------------
# Status mapping
# ---------------------------------------------------------------------------


def test_map_ib_status_submitted() -> None:
    """'Submitted' should map to ACCEPTED."""
    assert _map_ib_status("Submitted") == OrderStatus.ACCEPTED


def test_map_ib_status_filled() -> None:
    """'Filled' should map to FILLED."""
    assert _map_ib_status("Filled") == OrderStatus.FILLED


def test_map_ib_status_cancelled() -> None:
    """'Cancelled' should map to CANCELLED."""
    assert _map_ib_status("Cancelled") == OrderStatus.CANCELLED


def test_map_ib_status_inactive() -> None:
    """'Inactive' should map to REJECTED."""
    assert _map_ib_status("Inactive") == OrderStatus.REJECTED


def test_map_ib_status_api_pending() -> None:
    """'ApiPending' should map to PENDING."""
    assert _map_ib_status("ApiPending") == OrderStatus.PENDING


def test_map_ib_status_api_cancelled() -> None:
    """'ApiCancelled' should map to CANCELLED."""
    assert _map_ib_status("ApiCancelled") == OrderStatus.CANCELLED


def test_map_ib_status_pending_submit() -> None:
    """'PendingSubmit' should map to SUBMITTED."""
    assert _map_ib_status("PendingSubmit") == OrderStatus.SUBMITTED


def test_map_ib_status_pending_cancel() -> None:
    """'PendingCancel' should map to ACCEPTED."""
    assert _map_ib_status("PendingCancel") == OrderStatus.ACCEPTED


def test_map_ib_status_pre_submitted() -> None:
    """'PreSubmitted' should map to ACCEPTED."""
    assert _map_ib_status("PreSubmitted") == OrderStatus.ACCEPTED


def test_map_ib_status_unknown() -> None:
    """Unknown status should map to PENDING."""
    assert _map_ib_status("SomeUnknownStatus") == OrderStatus.PENDING
