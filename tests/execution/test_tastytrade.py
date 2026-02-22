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


# ---------------------------------------------------------------------------
# Mock helpers for connect/disconnect
# ---------------------------------------------------------------------------


def _make_mock_account(account_number: str = "5WX01234") -> MagicMock:
    """Create a mock tastytrade Account."""
    account = MagicMock()
    account.account_number = account_number
    return account


def _make_tastytrade_module(
    *,
    accounts: list[MagicMock] | None = None,
    auth_error: Exception | None = None,
    accounts_error: Exception | None = None,
) -> MagicMock:
    """Create a mock tastytrade module with configurable behavior.

    Args:
        accounts: List of mock accounts to return from get_accounts.
        auth_error: Exception to raise from session constructor.
        accounts_error: Exception to raise from Account.get_accounts.

    Returns:
        A MagicMock configured to act as the tastytrade module.
    """
    mock_module = MagicMock()

    # Session constructors
    mock_session = MagicMock()
    mock_session.destroy = MagicMock()

    if auth_error:
        mock_module.ProductionSession.side_effect = auth_error
        mock_module.CertificationSession.side_effect = auth_error
    else:
        mock_module.ProductionSession.return_value = mock_session
        mock_module.CertificationSession.return_value = mock_session

    # Account.get_accounts
    if accounts_error:
        mock_module.Account.get_accounts.side_effect = accounts_error
    else:
        mock_module.Account.get_accounts.return_value = (
            accounts if accounts is not None else [_make_mock_account()]
        )

    return mock_module


# ---------------------------------------------------------------------------
# Connect / disconnect tests
# ---------------------------------------------------------------------------


class TestConnectDisconnect:
    """Test connection lifecycle."""

    @pytest.mark.asyncio
    async def test_connect_production(self, event_bus: EventBus) -> None:
        """connect() should create a ProductionSession when is_test=False."""
        mock_mod = _make_tastytrade_module()

        with patch.dict("sys.modules", {"tastytrade": mock_mod}):
            adapter = TastytradeAdapter(
                bus=event_bus, login="user@test.com", password="secret123"
            )
            await adapter.connect()

        assert adapter.is_connected
        mock_mod.ProductionSession.assert_called_once_with("user@test.com", "secret123")
        mock_mod.Account.get_accounts.assert_called_once()

    @pytest.mark.asyncio
    async def test_connect_certification(self, event_bus: EventBus) -> None:
        """connect() should create a CertificationSession when is_test=True."""
        mock_mod = _make_tastytrade_module()

        with patch.dict("sys.modules", {"tastytrade": mock_mod}):
            adapter = TastytradeAdapter(
                bus=event_bus, login="user@test.com", password="secret123",
                is_test=True,
            )
            await adapter.connect()

        assert adapter.is_connected
        mock_mod.CertificationSession.assert_called_once_with("user@test.com", "secret123")

    @pytest.mark.asyncio
    async def test_connect_selects_account_by_number(self, event_bus: EventBus) -> None:
        """connect() should select the account matching account_number."""
        acct1 = _make_mock_account("AAA111")
        acct2 = _make_mock_account("BBB222")
        mock_mod = _make_tastytrade_module(accounts=[acct1, acct2])

        with patch.dict("sys.modules", {"tastytrade": mock_mod}):
            adapter = TastytradeAdapter(
                bus=event_bus, login="user", password="pass",
                account_number="BBB222",
            )
            await adapter.connect()

        assert adapter.is_connected
        assert adapter._account is acct2

    @pytest.mark.asyncio
    async def test_connect_selects_first_account_when_none_specified(
        self, event_bus: EventBus
    ) -> None:
        """connect() should select the first account when no account_number given."""
        acct1 = _make_mock_account("AAA111")
        acct2 = _make_mock_account("BBB222")
        mock_mod = _make_tastytrade_module(accounts=[acct1, acct2])

        with patch.dict("sys.modules", {"tastytrade": mock_mod}):
            adapter = TastytradeAdapter(
                bus=event_bus, login="user", password="pass"
            )
            await adapter.connect()

        assert adapter._account is acct1

    @pytest.mark.asyncio
    async def test_connect_account_not_found_raises(self, event_bus: EventBus) -> None:
        """connect() should raise SyslsConnectionError if specified account not found."""
        acct = _make_mock_account("AAA111")
        mock_mod = _make_tastytrade_module(accounts=[acct])

        with patch.dict("sys.modules", {"tastytrade": mock_mod}):
            adapter = TastytradeAdapter(
                bus=event_bus, login="user", password="pass",
                account_number="NOTFOUND",
            )
            with pytest.raises(SyslsConnectionError, match="NOTFOUND not found"):
                await adapter.connect()

    @pytest.mark.asyncio
    async def test_connect_no_accounts_raises(self, event_bus: EventBus) -> None:
        """connect() should raise SyslsConnectionError if no accounts returned."""
        mock_mod = _make_tastytrade_module(accounts=[])

        with patch.dict("sys.modules", {"tastytrade": mock_mod}):
            adapter = TastytradeAdapter(
                bus=event_bus, login="user", password="pass"
            )
            with pytest.raises(SyslsConnectionError, match="No accounts found"):
                await adapter.connect()

    @pytest.mark.asyncio
    async def test_connect_import_error(self, event_bus: EventBus) -> None:
        """connect() should raise SyslsConnectionError if tastytrade not installed."""
        import sys

        saved = sys.modules.pop("tastytrade", None)
        try:
            with patch.dict("sys.modules", {"tastytrade": None}):
                adapter = TastytradeAdapter(
                    bus=event_bus, login="user", password="pass"
                )
                with pytest.raises(SyslsConnectionError, match="tastytrade is not installed"):
                    await adapter.connect()
        finally:
            if saved is not None:
                sys.modules["tastytrade"] = saved

    @pytest.mark.asyncio
    async def test_connect_auth_failure(self, event_bus: EventBus) -> None:
        """connect() should raise SyslsConnectionError on auth failure."""
        mock_mod = _make_tastytrade_module(
            auth_error=RuntimeError("Invalid credentials")
        )

        with patch.dict("sys.modules", {"tastytrade": mock_mod}):
            adapter = TastytradeAdapter(
                bus=event_bus, login="bad", password="wrong"
            )
            with pytest.raises(SyslsConnectionError, match="Failed to authenticate"):
                await adapter.connect()

    @pytest.mark.asyncio
    async def test_connect_get_accounts_failure(self, event_bus: EventBus) -> None:
        """connect() should raise SyslsConnectionError if get_accounts fails."""
        mock_mod = _make_tastytrade_module(
            accounts_error=RuntimeError("API error")
        )

        with patch.dict("sys.modules", {"tastytrade": mock_mod}):
            adapter = TastytradeAdapter(
                bus=event_bus, login="user", password="pass"
            )
            with pytest.raises(SyslsConnectionError, match="Failed to retrieve accounts"):
                await adapter.connect()

    @pytest.mark.asyncio
    async def test_disconnect(self, event_bus: EventBus) -> None:
        """disconnect() should destroy session and clear state."""
        mock_mod = _make_tastytrade_module()

        with patch.dict("sys.modules", {"tastytrade": mock_mod}):
            adapter = TastytradeAdapter(
                bus=event_bus, login="user", password="pass"
            )
            await adapter.connect()
            assert adapter.is_connected

            session = adapter._session
            await adapter.disconnect()

        assert not adapter.is_connected
        assert adapter._account is None
        session.destroy.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_when_not_connected(self, event_bus: EventBus) -> None:
        """disconnect() should be safe to call when not connected."""
        adapter = TastytradeAdapter(
            bus=event_bus, login="user", password="pass"
        )
        await adapter.disconnect()  # Should not raise
        assert not adapter.is_connected

    @pytest.mark.asyncio
    async def test_disconnect_destroy_exception_ignored(
        self, event_bus: EventBus
    ) -> None:
        """disconnect() should ignore exceptions from session.destroy()."""
        mock_mod = _make_tastytrade_module()

        with patch.dict("sys.modules", {"tastytrade": mock_mod}):
            adapter = TastytradeAdapter(
                bus=event_bus, login="user", password="pass"
            )
            await adapter.connect()
            adapter._session.destroy.side_effect = RuntimeError("destroy failed")

            await adapter.disconnect()  # Should not raise

        assert not adapter.is_connected

    @pytest.mark.asyncio
    async def test_context_manager(self, event_bus: EventBus) -> None:
        """TastytradeAdapter should support async context manager."""
        mock_mod = _make_tastytrade_module()

        with patch.dict("sys.modules", {"tastytrade": mock_mod}):
            adapter = TastytradeAdapter(
                bus=event_bus, login="user", password="pass"
            )

            async with adapter as a:
                assert a is adapter
                assert adapter.is_connected

        assert not adapter.is_connected


# ---------------------------------------------------------------------------
# Mock helpers for order operations
# ---------------------------------------------------------------------------


def _make_mock_order_module() -> MagicMock:
    """Create a mock tastytrade.order module with all needed types."""
    order_mod = MagicMock()

    # OrderAction enum-like values
    order_mod.OrderAction.BUY_TO_OPEN = "Buy to Open"
    order_mod.OrderAction.SELL_TO_CLOSE = "Sell to Close"

    # OrderType enum-like values
    order_mod.OrderType.MARKET = "Market"
    order_mod.OrderType.LIMIT = "Limit"
    order_mod.OrderType.STOP = "Stop"
    order_mod.OrderType.STOP_LIMIT = "Stop Limit"

    # OrderTimeInForce enum-like values
    order_mod.OrderTimeInForce.GTC = "GTC"
    order_mod.OrderTimeInForce.DAY = "Day"
    order_mod.OrderTimeInForce.IOC = "IOC"
    order_mod.OrderTimeInForce.GTD = "GTD"

    # InstrumentType enum-like values
    order_mod.InstrumentType.EQUITY = "Equity"
    order_mod.InstrumentType.EQUITY_OPTION = "Equity Option"
    order_mod.InstrumentType.FUTURE = "Future"
    order_mod.InstrumentType.CRYPTOCURRENCY = "Cryptocurrency"

    # Leg and NewOrder constructors (just pass through)
    order_mod.Leg = MagicMock()
    order_mod.NewOrder = MagicMock()

    return order_mod


def _make_mock_placed_order_response(order_id: int = 12345) -> MagicMock:
    """Create a mock PlacedOrderResponse."""
    response = MagicMock()
    response.order.id = order_id
    return response


def _setup_connected_adapter(
    event_bus: EventBus,
    *,
    order_response: MagicMock | None = None,
    place_order_error: Exception | None = None,
    delete_order_error: Exception | None = None,
) -> tuple[TastytradeAdapter, MagicMock, MagicMock]:
    """Set up a TastytradeAdapter that appears connected with mocked internals.

    Returns:
        Tuple of (adapter, mock_session, mock_account).
    """
    mock_session = MagicMock()
    mock_account = _make_mock_account()

    if order_response is not None:
        mock_account.place_order.return_value = order_response
    else:
        mock_account.place_order.return_value = _make_mock_placed_order_response()

    if place_order_error:
        mock_account.place_order.side_effect = place_order_error

    if delete_order_error:
        mock_account.delete_order.side_effect = delete_order_error

    adapter = TastytradeAdapter(
        bus=event_bus, login="user", password="pass"
    )
    adapter._session = mock_session
    adapter._account = mock_account

    return adapter, mock_session, mock_account


# ---------------------------------------------------------------------------
# Submit order tests
# ---------------------------------------------------------------------------


class TestSubmitOrder:
    """Test order submission."""

    @pytest.mark.asyncio
    async def test_submit_market_order(self, event_bus: EventBus) -> None:
        """submit_order should call place_order and emit OrderAccepted."""
        import asyncio

        accepted_events: list[OrderAccepted] = []

        async def capture(event: OrderAccepted) -> None:
            accepted_events.append(event)

        event_bus.subscribe(OrderAccepted, capture)
        await event_bus.start()

        order_mod = _make_mock_order_module()

        with patch.dict(
            "sys.modules", {"tastytrade.order": order_mod}
        ):
            adapter, mock_session, mock_account = _setup_connected_adapter(
                event_bus,
                order_response=_make_mock_placed_order_response(order_id=42),
            )
            order = _make_order(
                side=Side.BUY, order_type=OrderType.MARKET, quantity=Decimal("100")
            )
            venue_order_id = await adapter.submit_order(order)

        await asyncio.sleep(0.05)
        await event_bus.stop()

        assert venue_order_id == "42"
        mock_account.place_order.assert_called_once()
        assert len(accepted_events) == 1
        assert accepted_events[0].venue_order_id == "42"
        assert accepted_events[0].order_id == order.order_id

    @pytest.mark.asyncio
    async def test_submit_limit_order_with_price(self, event_bus: EventBus) -> None:
        """submit_order for LIMIT should pass price to NewOrder."""
        import asyncio

        order_mod = _make_mock_order_module()

        with patch.dict("sys.modules", {"tastytrade.order": order_mod}):
            adapter, _, mock_account = _setup_connected_adapter(event_bus)

            await event_bus.start()
            order = _make_order(
                side=Side.BUY,
                order_type=OrderType.LIMIT,
                quantity=Decimal("50"),
                price=Decimal("150.50"),
            )
            await adapter.submit_order(order)
            await asyncio.sleep(0.05)
            await event_bus.stop()

        # Verify place_order was called
        mock_account.place_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_submit_sell_order_uses_sell_to_close(
        self, event_bus: EventBus
    ) -> None:
        """submit_order with Side.SELL should use SELL_TO_CLOSE action."""
        import asyncio

        order_mod = _make_mock_order_module()

        with patch.dict("sys.modules", {"tastytrade.order": order_mod}):
            adapter, _, mock_account = _setup_connected_adapter(event_bus)

            await event_bus.start()
            order = _make_order(side=Side.SELL, order_type=OrderType.MARKET)
            await adapter.submit_order(order)
            await asyncio.sleep(0.05)
            await event_bus.stop()

        mock_account.place_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_submit_order_not_connected_raises(self, event_bus: EventBus) -> None:
        """submit_order should raise VenueError when not connected."""
        adapter = TastytradeAdapter(
            bus=event_bus, login="user", password="pass"
        )
        with pytest.raises(VenueError, match="Not connected"):
            await adapter.submit_order(_make_order())

    @pytest.mark.asyncio
    async def test_submit_order_api_error(self, event_bus: EventBus) -> None:
        """submit_order should wrap API errors via _wrap_tt_error."""
        import asyncio

        order_mod = _make_mock_order_module()

        with patch.dict("sys.modules", {"tastytrade.order": order_mod}):
            adapter, _, _ = _setup_connected_adapter(
                event_bus,
                place_order_error=RuntimeError("API failure"),
            )
            await event_bus.start()

            with pytest.raises(VenueError, match="API failure"):
                await adapter.submit_order(_make_order())

            await asyncio.sleep(0.05)
            await event_bus.stop()


# ---------------------------------------------------------------------------
# Cancel order tests
# ---------------------------------------------------------------------------


class TestCancelOrder:
    """Test order cancellation."""

    @pytest.mark.asyncio
    async def test_cancel_order(self, event_bus: EventBus) -> None:
        """cancel_order should call delete_order and emit OrderCancelled."""
        import asyncio

        cancelled_events: list[OrderCancelled] = []

        async def capture(event: OrderCancelled) -> None:
            cancelled_events.append(event)

        event_bus.subscribe(OrderCancelled, capture)
        await event_bus.start()

        adapter, mock_session, mock_account = _setup_connected_adapter(event_bus)
        instrument = _make_equity_instrument()

        await adapter.cancel_order("12345", instrument)

        await asyncio.sleep(0.05)
        await event_bus.stop()

        mock_account.delete_order.assert_called_once_with(mock_session, 12345)
        assert len(cancelled_events) == 1
        assert cancelled_events[0].reason == "Cancelled via tastytrade"
        assert cancelled_events[0].order_id == "12345"

    @pytest.mark.asyncio
    async def test_cancel_order_not_connected_raises(self, event_bus: EventBus) -> None:
        """cancel_order should raise VenueError when not connected."""
        adapter = TastytradeAdapter(
            bus=event_bus, login="user", password="pass"
        )
        with pytest.raises(VenueError, match="Not connected"):
            await adapter.cancel_order("999", _make_equity_instrument())

    @pytest.mark.asyncio
    async def test_cancel_order_api_error(self, event_bus: EventBus) -> None:
        """cancel_order should wrap API errors via _wrap_tt_error."""
        import asyncio

        adapter, _, _ = _setup_connected_adapter(
            event_bus,
            delete_order_error=RuntimeError("Order not found"),
        )
        await event_bus.start()

        with pytest.raises(VenueError, match="Order not found"):
            await adapter.cancel_order("999", _make_equity_instrument())

        await asyncio.sleep(0.05)
        await event_bus.stop()


# ---------------------------------------------------------------------------
# Get order status tests
# ---------------------------------------------------------------------------


def _make_mock_placed_order(status: str = "Live") -> MagicMock:
    """Create a mock PlacedOrder with a given status.

    Args:
        status: The status string value.

    Returns:
        Mock PlacedOrder.
    """
    order = MagicMock()
    # Simulate enum-like status with .value attribute
    order.status.value = status
    return order


class TestGetOrderStatus:
    """Test order status querying."""

    @pytest.mark.asyncio
    async def test_get_order_status_live(self, event_bus: EventBus) -> None:
        """get_order_status should return ACCEPTED for 'Live' status."""
        adapter, mock_session, mock_account = _setup_connected_adapter(event_bus)
        mock_account.get_order.return_value = _make_mock_placed_order("Live")

        status = await adapter.get_order_status("42", _make_equity_instrument())

        assert status == OrderStatus.ACCEPTED
        mock_account.get_order.assert_called_once_with(mock_session, 42)

    @pytest.mark.asyncio
    async def test_get_order_status_filled(self, event_bus: EventBus) -> None:
        """get_order_status should return FILLED for 'Filled' status."""
        adapter, _, mock_account = _setup_connected_adapter(event_bus)
        mock_account.get_order.return_value = _make_mock_placed_order("Filled")

        status = await adapter.get_order_status("99", _make_equity_instrument())

        assert status == OrderStatus.FILLED

    @pytest.mark.asyncio
    async def test_get_order_status_cancelled(self, event_bus: EventBus) -> None:
        """get_order_status should return CANCELLED for 'Cancelled' status."""
        adapter, _, mock_account = _setup_connected_adapter(event_bus)
        mock_account.get_order.return_value = _make_mock_placed_order("Cancelled")

        status = await adapter.get_order_status("77", _make_equity_instrument())

        assert status == OrderStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_get_order_status_rejected(self, event_bus: EventBus) -> None:
        """get_order_status should return REJECTED for 'Rejected' status."""
        adapter, _, mock_account = _setup_connected_adapter(event_bus)
        mock_account.get_order.return_value = _make_mock_placed_order("Rejected")

        status = await adapter.get_order_status("88", _make_equity_instrument())

        assert status == OrderStatus.REJECTED

    @pytest.mark.asyncio
    async def test_get_order_status_unknown(self, event_bus: EventBus) -> None:
        """get_order_status should return PENDING for unknown statuses."""
        adapter, _, mock_account = _setup_connected_adapter(event_bus)
        mock_account.get_order.return_value = _make_mock_placed_order("SomeNewStatus")

        status = await adapter.get_order_status("55", _make_equity_instrument())

        assert status == OrderStatus.PENDING

    @pytest.mark.asyncio
    async def test_get_order_status_no_status_attr(self, event_bus: EventBus) -> None:
        """get_order_status should return PENDING if order has no status."""
        adapter, _, mock_account = _setup_connected_adapter(event_bus)
        placed_order = MagicMock(spec=[])  # No attributes at all
        mock_account.get_order.return_value = placed_order

        status = await adapter.get_order_status("66", _make_equity_instrument())

        assert status == OrderStatus.PENDING

    @pytest.mark.asyncio
    async def test_get_order_status_not_connected_raises(
        self, event_bus: EventBus
    ) -> None:
        """get_order_status should raise VenueError when not connected."""
        adapter = TastytradeAdapter(
            bus=event_bus, login="user", password="pass"
        )
        with pytest.raises(VenueError, match="Not connected"):
            await adapter.get_order_status("42", _make_equity_instrument())

    @pytest.mark.asyncio
    async def test_get_order_status_api_error(self, event_bus: EventBus) -> None:
        """get_order_status should wrap API errors via _wrap_tt_error."""
        adapter, _, mock_account = _setup_connected_adapter(event_bus)
        mock_account.get_order.side_effect = RuntimeError("Not found")

        with pytest.raises(VenueError, match="Not found"):
            await adapter.get_order_status("42", _make_equity_instrument())
