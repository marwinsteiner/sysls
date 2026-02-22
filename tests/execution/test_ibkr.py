"""Tests for the IbkrAdapter venue adapter.

All tests use mocked ib_async -- no real TWS/Gateway connection is made.
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
from sysls.execution.venues.ibkr import (
    IbkrAdapter,
    _build_instrument_from_contract,
    _map_ib_status,
    _to_ib_contract,
    _to_ib_order,
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


# ---------------------------------------------------------------------------
# Submit order tests
# ---------------------------------------------------------------------------


def _make_mock_trade(order_id: int = 42, status: str = "Submitted") -> MagicMock:
    """Create a mock ib_async Trade object."""
    trade = MagicMock()
    trade.order.orderId = order_id
    trade.orderStatus.status = status
    return trade


@pytest.mark.asyncio
async def test_submit_market_order(event_bus: EventBus) -> None:
    """submit_order should call placeOrder and emit OrderAccepted."""
    import asyncio

    accepted_events: list[OrderAccepted] = []

    async def capture(event: OrderAccepted) -> None:
        accepted_events.append(event)

    event_bus.subscribe(OrderAccepted, capture)
    await event_bus.start()

    mock_ib = _make_mock_ib()
    mock_trade = _make_mock_trade(order_id=42)
    mock_ib.placeOrder.return_value = mock_trade
    mock_ib_class = MagicMock(return_value=mock_ib)

    with patch.dict("sys.modules", {"ib_async": MagicMock(IB=mock_ib_class)}):
        adapter = IbkrAdapter(bus=event_bus)
        await adapter.connect()

        order = _make_order(side=Side.BUY, order_type=OrderType.MARKET, quantity=Decimal("100"))
        venue_order_id = await adapter.submit_order(order)

    await asyncio.sleep(0.05)
    await event_bus.stop()

    assert venue_order_id == "42"
    mock_ib.placeOrder.assert_called_once()

    assert len(accepted_events) == 1
    assert accepted_events[0].venue_order_id == "42"
    assert accepted_events[0].order_id == order.order_id


@pytest.mark.asyncio
async def test_submit_order_error_wrapping(event_bus: EventBus) -> None:
    """submit_order should wrap IB errors as VenueError."""
    await event_bus.start()

    mock_ib = _make_mock_ib()
    mock_ib.placeOrder.side_effect = RuntimeError("API not available")
    mock_ib_class = MagicMock(return_value=mock_ib)

    with patch.dict("sys.modules", {"ib_async": MagicMock(IB=mock_ib_class)}):
        adapter = IbkrAdapter(bus=event_bus)
        await adapter.connect()

        with pytest.raises(VenueError, match="API not available"):
            await adapter.submit_order(_make_order())

    await event_bus.stop()


@pytest.mark.asyncio
async def test_submit_order_not_connected_raises(event_bus: EventBus) -> None:
    """submit_order should raise VenueError when not connected."""
    adapter = IbkrAdapter(bus=event_bus)
    with pytest.raises(VenueError, match="Not connected"):
        await adapter.submit_order(_make_order())


# ---------------------------------------------------------------------------
# Cancel order tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_order(event_bus: EventBus) -> None:
    """cancel_order should find the trade and cancel it."""
    import asyncio

    cancelled_events: list[OrderCancelled] = []

    async def capture(event: OrderCancelled) -> None:
        cancelled_events.append(event)

    event_bus.subscribe(OrderCancelled, capture)
    await event_bus.start()

    mock_ib = _make_mock_ib()
    mock_trade = _make_mock_trade(order_id=42)
    mock_ib.openTrades.return_value = [mock_trade]
    mock_ib_class = MagicMock(return_value=mock_ib)

    with patch.dict("sys.modules", {"ib_async": MagicMock(IB=mock_ib_class)}):
        adapter = IbkrAdapter(bus=event_bus)
        await adapter.connect()

        instrument = _make_equity_instrument()
        await adapter.cancel_order("42", instrument)

    await asyncio.sleep(0.05)
    await event_bus.stop()

    mock_ib.cancelOrder.assert_called_once_with(mock_trade.order)
    assert len(cancelled_events) == 1
    assert cancelled_events[0].reason == "Cancelled via IBKR"


@pytest.mark.asyncio
async def test_cancel_order_not_found_raises(event_bus: EventBus) -> None:
    """cancel_order should raise OrderError if order not in open trades."""
    await event_bus.start()

    mock_ib = _make_mock_ib()
    mock_ib.openTrades.return_value = []
    mock_ib_class = MagicMock(return_value=mock_ib)

    with patch.dict("sys.modules", {"ib_async": MagicMock(IB=mock_ib_class)}):
        adapter = IbkrAdapter(bus=event_bus)
        await adapter.connect()

        with pytest.raises(OrderError, match="not found in open trades"):
            await adapter.cancel_order("999", _make_equity_instrument())

    await event_bus.stop()


# ---------------------------------------------------------------------------
# Get order status tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_order_status_found(event_bus: EventBus) -> None:
    """get_order_status should find the trade and return mapped status."""
    mock_ib = _make_mock_ib()
    mock_trade = _make_mock_trade(order_id=42, status="Filled")
    mock_ib.trades.return_value = [mock_trade]
    mock_ib_class = MagicMock(return_value=mock_ib)

    with patch.dict("sys.modules", {"ib_async": MagicMock(IB=mock_ib_class)}):
        adapter = IbkrAdapter(bus=event_bus)
        await adapter.connect()

        status = await adapter.get_order_status("42", _make_equity_instrument())

    assert status == OrderStatus.FILLED


@pytest.mark.asyncio
async def test_get_order_status_not_found(event_bus: EventBus) -> None:
    """get_order_status should return PENDING if order not found."""
    mock_ib = _make_mock_ib()
    mock_ib.trades.return_value = []
    mock_ib_class = MagicMock(return_value=mock_ib)

    with patch.dict("sys.modules", {"ib_async": MagicMock(IB=mock_ib_class)}):
        adapter = IbkrAdapter(bus=event_bus)
        await adapter.connect()

        status = await adapter.get_order_status("999", _make_equity_instrument())

    assert status == OrderStatus.PENDING


# ---------------------------------------------------------------------------
# Build instrument from contract tests
# ---------------------------------------------------------------------------


def _make_mock_contract(
    sec_type: str = "STK",
    symbol: str = "AAPL",
    exchange: str = "SMART",
    currency: str = "USD",
    multiplier: str = "",
) -> MagicMock:
    """Create a mock ib_async Contract."""
    contract = MagicMock()
    contract.secType = sec_type
    contract.symbol = symbol
    contract.exchange = exchange
    contract.currency = currency
    contract.multiplier = multiplier
    return contract


def test_build_instrument_from_stock_contract() -> None:
    """Stock contract should produce an EQUITY instrument."""
    contract = _make_mock_contract(sec_type="STK", symbol="AAPL")
    instrument = _build_instrument_from_contract(contract)
    assert instrument.symbol == "AAPL"
    assert instrument.asset_class == AssetClass.EQUITY
    assert instrument.venue == Venue.IBKR
    assert instrument.exchange == "SMART"
    assert instrument.currency == "USD"
    assert instrument.multiplier == Decimal("1")


def test_build_instrument_from_future_contract() -> None:
    """Future contract should produce a FUTURE instrument with multiplier."""
    contract = _make_mock_contract(sec_type="FUT", symbol="ES", exchange="CME", multiplier="50")
    instrument = _build_instrument_from_contract(contract)
    assert instrument.symbol == "ES"
    assert instrument.asset_class == AssetClass.FUTURE
    assert instrument.exchange == "CME"
    assert instrument.multiplier == Decimal("50")


def test_build_instrument_from_option_contract() -> None:
    """Option contract should produce an OPTION instrument."""
    contract = _make_mock_contract(sec_type="OPT", symbol="AAPL", multiplier="100")
    instrument = _build_instrument_from_contract(contract)
    assert instrument.asset_class == AssetClass.OPTION
    assert instrument.multiplier == Decimal("100")


def test_build_instrument_from_forex_contract() -> None:
    """Forex contract should produce a CRYPTO_SPOT instrument."""
    contract = _make_mock_contract(
        sec_type="CASH", symbol="EUR", exchange="IDEALPRO", currency="USD"
    )
    instrument = _build_instrument_from_contract(contract)
    assert instrument.asset_class == AssetClass.CRYPTO_SPOT
    assert instrument.symbol == "EUR"


def test_build_instrument_unknown_sec_type_defaults_to_equity() -> None:
    """Unknown secType should default to EQUITY."""
    contract = _make_mock_contract(sec_type="UNKNOWN", symbol="XYZ")
    instrument = _build_instrument_from_contract(contract)
    assert instrument.asset_class == AssetClass.EQUITY


# ---------------------------------------------------------------------------
# Position tests
# ---------------------------------------------------------------------------


def _make_mock_position(
    symbol: str = "AAPL",
    sec_type: str = "STK",
    position: float = 100.0,
    avg_cost: float = 150.0,
) -> MagicMock:
    """Create a mock IB Position namedtuple."""
    pos = MagicMock()
    pos.contract = _make_mock_contract(sec_type=sec_type, symbol=symbol)
    pos.position = position
    pos.avgCost = avg_cost
    pos.account = "DU12345"
    return pos


@pytest.mark.asyncio
async def test_get_positions_empty(event_bus: EventBus) -> None:
    """get_positions should return empty dict when no positions."""
    mock_ib = _make_mock_ib()
    mock_ib.positions.return_value = []
    mock_ib_class = MagicMock(return_value=mock_ib)

    with patch.dict("sys.modules", {"ib_async": MagicMock(IB=mock_ib_class)}):
        adapter = IbkrAdapter(bus=event_bus)
        await adapter.connect()

        positions = await adapter.get_positions()

    assert positions == {}


@pytest.mark.asyncio
async def test_get_positions_long(event_bus: EventBus) -> None:
    """get_positions should return positive quantity for long positions."""
    mock_ib = _make_mock_ib()
    mock_ib.positions.return_value = [
        _make_mock_position(symbol="AAPL", position=100.0),
    ]
    mock_ib_class = MagicMock(return_value=mock_ib)

    with patch.dict("sys.modules", {"ib_async": MagicMock(IB=mock_ib_class)}):
        adapter = IbkrAdapter(bus=event_bus)
        await adapter.connect()

        positions = await adapter.get_positions()

    assert len(positions) == 1
    instrument = next(iter(positions.keys()))
    assert instrument.symbol == "AAPL"
    assert instrument.asset_class == AssetClass.EQUITY
    assert positions[instrument] == Decimal("100.0")


@pytest.mark.asyncio
async def test_get_positions_short(event_bus: EventBus) -> None:
    """get_positions should return negative quantity for short positions."""
    mock_ib = _make_mock_ib()
    mock_ib.positions.return_value = [
        _make_mock_position(symbol="TSLA", position=-50.0),
    ]
    mock_ib_class = MagicMock(return_value=mock_ib)

    with patch.dict("sys.modules", {"ib_async": MagicMock(IB=mock_ib_class)}):
        adapter = IbkrAdapter(bus=event_bus)
        await adapter.connect()

        positions = await adapter.get_positions()

    assert len(positions) == 1
    qty = next(iter(positions.values()))
    assert qty == Decimal("-50.0")


@pytest.mark.asyncio
async def test_get_positions_skips_zero(event_bus: EventBus) -> None:
    """get_positions should skip positions with zero quantity."""
    mock_ib = _make_mock_ib()
    mock_ib.positions.return_value = [
        _make_mock_position(symbol="AAPL", position=0.0),
        _make_mock_position(symbol="MSFT", position=200.0),
    ]
    mock_ib_class = MagicMock(return_value=mock_ib)

    with patch.dict("sys.modules", {"ib_async": MagicMock(IB=mock_ib_class)}):
        adapter = IbkrAdapter(bus=event_bus)
        await adapter.connect()

        positions = await adapter.get_positions()

    assert len(positions) == 1
    instrument = next(iter(positions.keys()))
    assert instrument.symbol == "MSFT"


# ---------------------------------------------------------------------------
# Balance tests
# ---------------------------------------------------------------------------


def _make_mock_account_value(
    tag: str, value: str, currency: str = "USD", account: str = "DU12345"
) -> MagicMock:
    """Create a mock IB AccountValue namedtuple."""
    av = MagicMock()
    av.tag = tag
    av.value = value
    av.currency = currency
    av.account = account
    av.modelCode = ""
    return av


@pytest.mark.asyncio
async def test_get_balances(event_bus: EventBus) -> None:
    """get_balances should return CashBalance values by currency."""
    mock_ib = _make_mock_ib()
    mock_ib.accountValues.return_value = [
        _make_mock_account_value("CashBalance", "50000.00", "USD"),
        _make_mock_account_value("CashBalance", "10000.00", "EUR"),
        _make_mock_account_value("NetLiquidation", "75000.00", "USD"),  # Should be ignored
        _make_mock_account_value("CashBalance", "0", "GBP"),  # Zero, should be excluded
        _make_mock_account_value("CashBalance", "5000.00", "BASE"),  # BASE, should be excluded
    ]
    mock_ib_class = MagicMock(return_value=mock_ib)

    with patch.dict("sys.modules", {"ib_async": MagicMock(IB=mock_ib_class)}):
        adapter = IbkrAdapter(bus=event_bus)
        await adapter.connect()

        balances = await adapter.get_balances()

    assert balances["USD"] == Decimal("50000.00")
    assert balances["EUR"] == Decimal("10000.00")
    assert "GBP" not in balances
    assert "BASE" not in balances
    assert len(balances) == 2


@pytest.mark.asyncio
async def test_get_balances_empty(event_bus: EventBus) -> None:
    """get_balances should return empty dict when no account values."""
    mock_ib = _make_mock_ib()
    mock_ib.accountValues.return_value = []
    mock_ib_class = MagicMock(return_value=mock_ib)

    with patch.dict("sys.modules", {"ib_async": MagicMock(IB=mock_ib_class)}):
        adapter = IbkrAdapter(bus=event_bus)
        await adapter.connect()

        balances = await adapter.get_balances()

    assert balances == {}
