"""Tests for the IbkrAdapter venue adapter.

All tests use mocked ib_async -- no real TWS/Gateway connection is made.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sysls.core.bus import EventBus
from sysls.core.exceptions import ConnectionError as SyslsConnectionError
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


# ---------------------------------------------------------------------------
# Connect / disconnect tests
# ---------------------------------------------------------------------------


def _make_mock_ib(connected: bool = True) -> MagicMock:
    """Create a mock IB instance with standard behavior."""
    mock_ib = MagicMock()
    mock_ib.isConnected.return_value = connected
    mock_ib.connectAsync = AsyncMock()
    mock_ib.disconnect = MagicMock()
    mock_ib.positions.return_value = []
    mock_ib.accountValues.return_value = []
    mock_ib.trades.return_value = []
    mock_ib.openTrades.return_value = []
    return mock_ib


@pytest.mark.asyncio
async def test_connect_success(event_bus: EventBus) -> None:
    """connect() should create an IB instance and call connectAsync."""
    mock_ib = _make_mock_ib()
    mock_ib_class = MagicMock(return_value=mock_ib)
    with patch.dict("sys.modules", {"ib_async": MagicMock(IB=mock_ib_class)}):
        adapter = IbkrAdapter(bus=event_bus, host="127.0.0.1", port=7497, client_id=1)
        await adapter.connect()

        assert adapter.is_connected
        mock_ib.connectAsync.assert_awaited_once_with("127.0.0.1", 7497, clientId=1, account="")


@pytest.mark.asyncio
async def test_connect_with_account(event_bus: EventBus) -> None:
    """connect() should pass account to connectAsync."""
    mock_ib = _make_mock_ib()
    mock_ib_class = MagicMock(return_value=mock_ib)

    with patch.dict("sys.modules", {"ib_async": MagicMock(IB=mock_ib_class)}):
        adapter = IbkrAdapter(
            bus=event_bus, host="10.0.0.1", port=7496, client_id=5, account="DU12345"
        )
        await adapter.connect()

        mock_ib.connectAsync.assert_awaited_once_with(
            "10.0.0.1", 7496, clientId=5, account="DU12345"
        )


@pytest.mark.asyncio
async def test_connect_import_error(event_bus: EventBus) -> None:
    """connect() should raise SyslsConnectionError if ib_async is not installed."""
    import sys

    # Temporarily remove ib_async from sys.modules to force ImportError
    saved = sys.modules.pop("ib_async", None)
    try:
        with patch.dict("sys.modules", {"ib_async": None}):
            adapter = IbkrAdapter(bus=event_bus)
            with pytest.raises(SyslsConnectionError, match="ib_async is not installed"):
                await adapter.connect()
    finally:
        if saved is not None:
            sys.modules["ib_async"] = saved


@pytest.mark.asyncio
async def test_connect_connection_failure(event_bus: EventBus) -> None:
    """connect() should raise SyslsConnectionError if connectAsync fails."""
    mock_ib = _make_mock_ib(connected=False)
    mock_ib.connectAsync = AsyncMock(side_effect=ConnectionRefusedError("Connection refused"))
    mock_ib_class = MagicMock(return_value=mock_ib)

    with patch.dict("sys.modules", {"ib_async": MagicMock(IB=mock_ib_class)}):
        adapter = IbkrAdapter(bus=event_bus)
        with pytest.raises(SyslsConnectionError, match="Failed to connect"):
            await adapter.connect()


@pytest.mark.asyncio
async def test_disconnect(event_bus: EventBus) -> None:
    """disconnect() should call ib.disconnect() and clear state."""
    mock_ib = _make_mock_ib()
    mock_ib_class = MagicMock(return_value=mock_ib)

    with patch.dict("sys.modules", {"ib_async": MagicMock(IB=mock_ib_class)}):
        adapter = IbkrAdapter(bus=event_bus)
        await adapter.connect()
        assert adapter.is_connected

        await adapter.disconnect()
        mock_ib.disconnect.assert_called_once()
        assert not adapter.is_connected


@pytest.mark.asyncio
async def test_disconnect_when_not_connected(event_bus: EventBus) -> None:
    """disconnect() should be safe to call when not connected."""
    adapter = IbkrAdapter(bus=event_bus)
    await adapter.disconnect()  # Should not raise
    assert not adapter.is_connected


@pytest.mark.asyncio
async def test_context_manager(event_bus: EventBus) -> None:
    """IbkrAdapter should support async context manager."""
    mock_ib = _make_mock_ib()
    mock_ib_class = MagicMock(return_value=mock_ib)

    with patch.dict("sys.modules", {"ib_async": MagicMock(IB=mock_ib_class)}):
        adapter = IbkrAdapter(bus=event_bus)

        async with adapter as a:
            assert a is adapter
            assert adapter.is_connected

        mock_ib.disconnect.assert_called_once()
        assert not adapter.is_connected
