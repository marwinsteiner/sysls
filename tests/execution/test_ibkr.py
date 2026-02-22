"""Tests for the IbkrAdapter venue adapter.

All tests use mocked ib_async -- no real TWS/Gateway connection is made.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sysls.core.bus import EventBus
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
from sysls.execution.venues.ibkr import IbkrAdapter, _map_ib_status, _to_ib_contract, _to_ib_order

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


# ---------------------------------------------------------------------------
# Contract building tests
# ---------------------------------------------------------------------------


def test_to_ib_contract_equity() -> None:
    """Equity instrument should produce a Stock contract."""
    from ib_async import Stock

    instrument = _make_equity_instrument(symbol="AAPL")
    contract = _to_ib_contract(instrument)
    assert isinstance(contract, Stock)
    assert contract.symbol == "AAPL"
    assert contract.exchange == "SMART"
    assert contract.currency == "USD"


def test_to_ib_contract_equity_with_exchange() -> None:
    """Equity with explicit exchange should use that exchange."""
    from ib_async import Stock

    instrument = Instrument(
        symbol="AAPL",
        asset_class=AssetClass.EQUITY,
        venue=Venue.IBKR,
        exchange="NYSE",
        currency="USD",
    )
    contract = _to_ib_contract(instrument)
    assert isinstance(contract, Stock)
    assert contract.exchange == "NYSE"


def test_to_ib_contract_option_parsed() -> None:
    """Option with space-separated details should be parsed correctly."""
    from ib_async import Option

    instrument = Instrument(
        symbol="AAPL 20240315 150 C",
        asset_class=AssetClass.OPTION,
        venue=Venue.IBKR,
        currency="USD",
    )
    contract = _to_ib_contract(instrument)
    assert isinstance(contract, Option)
    assert contract.symbol == "AAPL"
    assert contract.lastTradeDateOrContractMonth == "20240315"
    assert contract.strike == 150.0
    assert contract.right == "C"


def test_to_ib_contract_option_put() -> None:
    """Put option should set right to 'P'."""
    from ib_async import Option

    instrument = Instrument(
        symbol="SPY 20240621 500 P",
        asset_class=AssetClass.OPTION,
        venue=Venue.IBKR,
        currency="USD",
    )
    contract = _to_ib_contract(instrument)
    assert isinstance(contract, Option)
    assert contract.right == "P"
    assert contract.strike == 500.0


def test_to_ib_contract_future() -> None:
    """Future instrument should produce a Future contract."""
    from ib_async import Future

    instrument = Instrument(
        symbol="ES",
        asset_class=AssetClass.FUTURE,
        venue=Venue.IBKR,
        exchange="CME",
        currency="USD",
    )
    contract = _to_ib_contract(instrument)
    assert isinstance(contract, Future)
    assert contract.symbol == "ES"
    assert contract.exchange == "CME"


def test_to_ib_contract_forex() -> None:
    """Crypto spot instrument should produce a Forex contract."""
    from ib_async import Forex

    instrument = Instrument(
        symbol="EUR",
        asset_class=AssetClass.CRYPTO_SPOT,
        venue=Venue.IBKR,
        currency="USD",
    )
    contract = _to_ib_contract(instrument)
    assert isinstance(contract, Forex)
    assert contract.symbol == "EUR"
    assert contract.currency == "USD"
    assert contract.exchange == "IDEALPRO"


def test_to_ib_contract_unsupported_raises() -> None:
    """Unsupported asset class should raise OrderError."""
    instrument = Instrument(
        symbol="SOME_EVENT",
        asset_class=AssetClass.EVENT,
        venue=Venue.IBKR,
        currency="USD",
    )
    with pytest.raises(OrderError, match="Unsupported asset class"):
        _to_ib_contract(instrument)


# ---------------------------------------------------------------------------
# Order building tests
# ---------------------------------------------------------------------------


def test_to_ib_order_market_buy() -> None:
    """Market buy should produce a MarketOrder with action BUY."""
    from ib_async import MarketOrder

    order = _make_order(side=Side.BUY, order_type=OrderType.MARKET, quantity=Decimal("100"))
    ib_order = _to_ib_order(order)
    assert isinstance(ib_order, MarketOrder)
    assert ib_order.action == "BUY"
    assert ib_order.totalQuantity == 100.0


def test_to_ib_order_market_sell() -> None:
    """Market sell should produce a MarketOrder with action SELL."""
    from ib_async import MarketOrder

    order = _make_order(side=Side.SELL, order_type=OrderType.MARKET, quantity=Decimal("50"))
    ib_order = _to_ib_order(order)
    assert isinstance(ib_order, MarketOrder)
    assert ib_order.action == "SELL"
    assert ib_order.totalQuantity == 50.0


def test_to_ib_order_limit() -> None:
    """Limit order should produce a LimitOrder with correct price."""
    from ib_async import LimitOrder

    order = _make_order(
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("200"),
        price=Decimal("150.50"),
    )
    ib_order = _to_ib_order(order)
    assert isinstance(ib_order, LimitOrder)
    assert ib_order.action == "BUY"
    assert ib_order.totalQuantity == 200.0
    assert ib_order.lmtPrice == 150.50


def test_to_ib_order_limit_no_price_raises() -> None:
    """Limit order without price should raise OrderError."""
    order = _make_order(side=Side.BUY, order_type=OrderType.LIMIT, price=None)
    with pytest.raises(OrderError, match="Limit order requires a price"):
        _to_ib_order(order)


def test_to_ib_order_stop() -> None:
    """Stop order should produce a StopOrder with correct stop price."""
    from ib_async import StopOrder

    order = _make_order(
        side=Side.SELL,
        order_type=OrderType.STOP,
        quantity=Decimal("75"),
        stop_price=Decimal("140.00"),
    )
    ib_order = _to_ib_order(order)
    assert isinstance(ib_order, StopOrder)
    assert ib_order.action == "SELL"
    assert ib_order.totalQuantity == 75.0
    assert ib_order.auxPrice == 140.00


def test_to_ib_order_stop_no_stop_price_raises() -> None:
    """Stop order without stop_price should raise OrderError."""
    order = _make_order(side=Side.SELL, order_type=OrderType.STOP, stop_price=None)
    with pytest.raises(OrderError, match="Stop order requires a stop_price"):
        _to_ib_order(order)


def test_to_ib_order_stop_limit() -> None:
    """Stop-limit order should produce a StopLimitOrder."""
    from ib_async import StopLimitOrder

    order = _make_order(
        side=Side.BUY,
        order_type=OrderType.STOP_LIMIT,
        quantity=Decimal("30"),
        price=Decimal("155.00"),
        stop_price=Decimal("153.00"),
    )
    ib_order = _to_ib_order(order)
    assert isinstance(ib_order, StopLimitOrder)
    assert ib_order.action == "BUY"
    assert ib_order.totalQuantity == 30.0
    assert ib_order.lmtPrice == 155.00
    assert ib_order.auxPrice == 153.00


def test_to_ib_order_stop_limit_missing_prices_raises() -> None:
    """Stop-limit order missing price or stop_price should raise OrderError."""
    order = _make_order(
        side=Side.BUY,
        order_type=OrderType.STOP_LIMIT,
        price=Decimal("155.00"),
        stop_price=None,
    )
    with pytest.raises(OrderError, match="Stop-limit order requires both"):
        _to_ib_order(order)
