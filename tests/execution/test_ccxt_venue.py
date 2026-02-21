"""Tests for the CcxtVenueAdapter.

All tests use mocked ccxt exchanges -- no real API calls are made.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import MagicMock, patch

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
from sysls.execution.venues.ccxt_venue import (
    CcxtVenueAdapter,
    _build_instrument,
    _map_order_status,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_instrument(
    symbol: str = "BTC/USDT",
    asset_class: AssetClass = AssetClass.CRYPTO_SPOT,
    exchange: str = "binance",
    currency: str = "USDT",
) -> Instrument:
    """Create a test instrument."""
    return Instrument(
        symbol=symbol,
        asset_class=asset_class,
        venue=Venue.CCXT,
        exchange=exchange,
        currency=currency,
    )


def _make_order(
    instrument: Instrument | None = None,
    side: Side = Side.BUY,
    order_type: OrderType = OrderType.MARKET,
    quantity: Decimal = Decimal("0.5"),
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


def _make_mock_exchange() -> MagicMock:
    """Create a mock ccxt exchange with standard behavior."""
    mock_exchange = MagicMock()
    mock_exchange.load_markets = MagicMock(return_value=None)
    mock_exchange.markets = {
        "BTC/USDT": {
            "id": "BTCUSDT",
            "symbol": "BTC/USDT",
            "base": "BTC",
            "quote": "USDT",
            "type": "spot",
            "precision": {"price": 0.01, "amount": 0.00001},
        },
    }
    mock_exchange.set_sandbox_mode = MagicMock()
    mock_exchange.create_order = MagicMock(
        return_value={"id": "123456", "status": "open", "symbol": "BTC/USDT"}
    )
    mock_exchange.cancel_order = MagicMock(return_value={"id": "123456", "status": "canceled"})
    mock_exchange.fetch_order = MagicMock(
        return_value={"id": "123456", "status": "open", "symbol": "BTC/USDT"}
    )
    mock_exchange.fetch_positions = MagicMock(return_value=[])
    mock_exchange.fetch_balance = MagicMock(
        return_value={
            "free": {"BTC": 1.5, "USDT": 10000.0, "ETH": 0.0},
            "used": {"BTC": 0.0, "USDT": 0.0},
            "total": {"BTC": 1.5, "USDT": 10000.0},
        }
    )
    return mock_exchange


@pytest.fixture
def event_bus() -> EventBus:
    """Create a fresh EventBus."""
    return EventBus()


@pytest.fixture
def mock_exchange() -> MagicMock:
    """Create a mock ccxt exchange."""
    return _make_mock_exchange()


# ---------------------------------------------------------------------------
# Connect / disconnect tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_creates_exchange_and_loads_markets(event_bus: EventBus) -> None:
    """connect() should create the exchange instance and load markets."""
    mock_ex = _make_mock_exchange()

    with patch("ccxt.binance", return_value=mock_ex, create=True):
        adapter = CcxtVenueAdapter(bus=event_bus, exchange_id="binance")
        await adapter.connect()

        assert adapter.is_connected
        mock_ex.load_markets.assert_called_once()


@pytest.mark.asyncio
async def test_connect_unknown_exchange_raises(event_bus: EventBus) -> None:
    """connect() with an unknown exchange should raise SyslsConnectionError."""
    adapter = CcxtVenueAdapter(bus=event_bus, exchange_id="nonexistent_exchange_xyz")

    with pytest.raises(SyslsConnectionError, match="Unknown ccxt exchange"):
        await adapter.connect()


@pytest.mark.asyncio
async def test_disconnect(event_bus: EventBus) -> None:
    """disconnect() should clear the exchange and set connected to False."""
    mock_ex = _make_mock_exchange()

    with patch("ccxt.binance", return_value=mock_ex, create=True):
        adapter = CcxtVenueAdapter(bus=event_bus, exchange_id="binance")
        await adapter.connect()
        assert adapter.is_connected

        await adapter.disconnect()
        assert not adapter.is_connected


@pytest.mark.asyncio
async def test_context_manager(event_bus: EventBus) -> None:
    """CcxtVenueAdapter should support async context manager."""
    mock_ex = _make_mock_exchange()

    with patch("ccxt.binance", return_value=mock_ex, create=True):
        adapter = CcxtVenueAdapter(bus=event_bus, exchange_id="binance")

        async with adapter as a:
            assert a is adapter
            assert adapter.is_connected

        assert not adapter.is_connected


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


def test_name_property(event_bus: EventBus) -> None:
    """name should return 'ccxt-{exchange_id}'."""
    adapter = CcxtVenueAdapter(bus=event_bus, exchange_id="binance")
    assert adapter.name == "ccxt-binance"


def test_name_property_different_exchange(event_bus: EventBus) -> None:
    """name should reflect the exchange_id."""
    adapter = CcxtVenueAdapter(bus=event_bus, exchange_id="bybit")
    assert adapter.name == "ccxt-bybit"


def test_supported_order_types(event_bus: EventBus) -> None:
    """supported_order_types should include MARKET, LIMIT, STOP_LIMIT."""
    adapter = CcxtVenueAdapter(bus=event_bus, exchange_id="binance")
    types = adapter.supported_order_types
    assert OrderType.MARKET in types
    assert OrderType.LIMIT in types
    assert OrderType.STOP_LIMIT in types


# ---------------------------------------------------------------------------
# Sandbox mode test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sandbox_mode(event_bus: EventBus) -> None:
    """sandbox=True should call set_sandbox_mode on the exchange."""
    mock_ex = _make_mock_exchange()

    with patch("ccxt.binance", return_value=mock_ex, create=True):
        adapter = CcxtVenueAdapter(bus=event_bus, exchange_id="binance", sandbox=True)
        await adapter.connect()

        mock_ex.set_sandbox_mode.assert_called_once_with(True)


@pytest.mark.asyncio
async def test_no_sandbox_by_default(event_bus: EventBus) -> None:
    """sandbox=False should not call set_sandbox_mode."""
    mock_ex = _make_mock_exchange()

    with patch("ccxt.binance", return_value=mock_ex, create=True):
        adapter = CcxtVenueAdapter(bus=event_bus, exchange_id="binance", sandbox=False)
        await adapter.connect()

        mock_ex.set_sandbox_mode.assert_not_called()


# ---------------------------------------------------------------------------
# Submit order tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_market_order(event_bus: EventBus) -> None:
    """submit_order with a market order should call create_order correctly."""
    accepted_events: list[OrderAccepted] = []

    async def capture(event: OrderAccepted) -> None:
        accepted_events.append(event)

    event_bus.subscribe(OrderAccepted, capture)
    await event_bus.start()

    mock_ex = _make_mock_exchange()
    mock_ex.create_order.return_value = {"id": "order-abc", "status": "open"}

    with patch("ccxt.binance", return_value=mock_ex, create=True):
        adapter = CcxtVenueAdapter(bus=event_bus, exchange_id="binance")
        await adapter.connect()

        order = _make_order(
            side=Side.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("0.5"),
        )
        venue_order_id = await adapter.submit_order(order)

    await asyncio.sleep(0.05)
    await event_bus.stop()

    assert venue_order_id == "order-abc"
    mock_ex.create_order.assert_called_once_with("BTC/USDT", "market", "buy", 0.5, None)

    assert len(accepted_events) == 1
    assert accepted_events[0].venue_order_id == "order-abc"


@pytest.mark.asyncio
async def test_submit_limit_order(event_bus: EventBus) -> None:
    """submit_order with a limit order should pass the price."""
    await event_bus.start()

    mock_ex = _make_mock_exchange()
    mock_ex.create_order.return_value = {"id": "order-def", "status": "open"}

    with patch("ccxt.binance", return_value=mock_ex, create=True):
        adapter = CcxtVenueAdapter(bus=event_bus, exchange_id="binance")
        await adapter.connect()

        order = _make_order(
            side=Side.SELL,
            order_type=OrderType.LIMIT,
            quantity=Decimal("1.0"),
            price=Decimal("50000"),
        )
        venue_order_id = await adapter.submit_order(order)

    await asyncio.sleep(0.05)
    await event_bus.stop()

    assert venue_order_id == "order-def"
    mock_ex.create_order.assert_called_once_with("BTC/USDT", "limit", "sell", 1.0, 50000.0)


# ---------------------------------------------------------------------------
# Cancel order tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_order(event_bus: EventBus) -> None:
    """cancel_order should call exchange.cancel_order and emit OrderCancelled."""
    cancelled_events: list[OrderCancelled] = []

    async def capture(event: OrderCancelled) -> None:
        cancelled_events.append(event)

    event_bus.subscribe(OrderCancelled, capture)
    await event_bus.start()

    mock_ex = _make_mock_exchange()

    with patch("ccxt.binance", return_value=mock_ex, create=True):
        adapter = CcxtVenueAdapter(bus=event_bus, exchange_id="binance")
        await adapter.connect()

        instrument = _make_instrument()
        await adapter.cancel_order("order-123", instrument)

    await asyncio.sleep(0.05)
    await event_bus.stop()

    mock_ex.cancel_order.assert_called_once_with("order-123", "BTC/USDT")
    assert len(cancelled_events) == 1
    assert cancelled_events[0].reason == "Cancelled via ccxt"


# ---------------------------------------------------------------------------
# Order status mapping tests
# ---------------------------------------------------------------------------


def test_map_order_status_open() -> None:
    """'open' should map to ACCEPTED."""
    assert _map_order_status("open") == OrderStatus.ACCEPTED


def test_map_order_status_closed() -> None:
    """'closed' should map to FILLED."""
    assert _map_order_status("closed") == OrderStatus.FILLED


def test_map_order_status_canceled() -> None:
    """'canceled' should map to CANCELLED."""
    assert _map_order_status("canceled") == OrderStatus.CANCELLED


def test_map_order_status_expired() -> None:
    """'expired' should map to EXPIRED."""
    assert _map_order_status("expired") == OrderStatus.EXPIRED


def test_map_order_status_rejected() -> None:
    """'rejected' should map to REJECTED."""
    assert _map_order_status("rejected") == OrderStatus.REJECTED


def test_map_order_status_unknown() -> None:
    """Unknown status should map to PENDING."""
    assert _map_order_status("unknown_status") == OrderStatus.PENDING


@pytest.mark.asyncio
async def test_get_order_status(event_bus: EventBus) -> None:
    """get_order_status should fetch and map the order status."""
    await event_bus.start()

    mock_ex = _make_mock_exchange()
    mock_ex.fetch_order.return_value = {
        "id": "order-789",
        "status": "closed",
        "symbol": "BTC/USDT",
    }

    with patch("ccxt.binance", return_value=mock_ex, create=True):
        adapter = CcxtVenueAdapter(bus=event_bus, exchange_id="binance")
        await adapter.connect()

        instrument = _make_instrument()
        status = await adapter.get_order_status("order-789", instrument)

    await asyncio.sleep(0.05)
    await event_bus.stop()

    assert status == OrderStatus.FILLED
    mock_ex.fetch_order.assert_called_once_with("order-789", "BTC/USDT")


# ---------------------------------------------------------------------------
# Position tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_positions_empty(event_bus: EventBus) -> None:
    """get_positions should return empty dict when no positions."""
    await event_bus.start()

    mock_ex = _make_mock_exchange()
    mock_ex.fetch_positions.return_value = []

    with patch("ccxt.binance", return_value=mock_ex, create=True):
        adapter = CcxtVenueAdapter(bus=event_bus, exchange_id="binance")
        await adapter.connect()

        positions = await adapter.get_positions()

    await asyncio.sleep(0.05)
    await event_bus.stop()

    assert positions == {}


@pytest.mark.asyncio
async def test_get_positions_with_derivatives(event_bus: EventBus) -> None:
    """get_positions should parse derivative positions correctly."""
    await event_bus.start()

    mock_ex = _make_mock_exchange()
    mock_ex.markets["BTC/USDT:USDT"] = {
        "id": "BTCUSDT",
        "symbol": "BTC/USDT:USDT",
        "base": "BTC",
        "quote": "USDT",
        "type": "swap",
        "precision": {"price": 0.1, "amount": 0.001},
    }
    mock_ex.fetch_positions.return_value = [
        {
            "symbol": "BTC/USDT:USDT",
            "contracts": 5,
            "side": "long",
            "info": {},
        },
    ]

    with patch("ccxt.binance", return_value=mock_ex, create=True):
        adapter = CcxtVenueAdapter(bus=event_bus, exchange_id="binance")
        await adapter.connect()

        positions = await adapter.get_positions()

    await asyncio.sleep(0.05)
    await event_bus.stop()

    assert len(positions) == 1
    instrument = next(iter(positions.keys()))
    assert instrument.symbol == "BTC/USDT:USDT"
    assert instrument.asset_class == AssetClass.CRYPTO_PERP
    assert positions[instrument] == Decimal("5")


@pytest.mark.asyncio
async def test_get_positions_short_is_negative(event_bus: EventBus) -> None:
    """Short positions should have negative quantity."""
    await event_bus.start()

    mock_ex = _make_mock_exchange()
    mock_ex.fetch_positions.return_value = [
        {
            "symbol": "BTC/USDT",
            "contracts": 3,
            "side": "short",
        },
    ]

    with patch("ccxt.binance", return_value=mock_ex, create=True):
        adapter = CcxtVenueAdapter(bus=event_bus, exchange_id="binance")
        await adapter.connect()

        positions = await adapter.get_positions()

    await asyncio.sleep(0.05)
    await event_bus.stop()

    assert len(positions) == 1
    qty = next(iter(positions.values()))
    assert qty == Decimal("-3")


# ---------------------------------------------------------------------------
# Balance tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_balances(event_bus: EventBus) -> None:
    """get_balances should return free balances, excluding zeros."""
    await event_bus.start()

    mock_ex = _make_mock_exchange()

    with patch("ccxt.binance", return_value=mock_ex, create=True):
        adapter = CcxtVenueAdapter(bus=event_bus, exchange_id="binance")
        await adapter.connect()

        balances = await adapter.get_balances()

    await asyncio.sleep(0.05)
    await event_bus.stop()

    assert balances["BTC"] == Decimal("1.5")
    assert balances["USDT"] == Decimal("10000.0")
    # ETH has 0.0 balance, should be excluded.
    assert "ETH" not in balances


# ---------------------------------------------------------------------------
# Error wrapping tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_error_wrapping_invalid_order(event_bus: EventBus) -> None:
    """ccxt InvalidOrder should be wrapped as OrderError."""
    import ccxt

    await event_bus.start()

    mock_ex = _make_mock_exchange()
    mock_ex.create_order.side_effect = ccxt.InvalidOrder("Invalid amount")

    with patch("ccxt.binance", return_value=mock_ex, create=True):
        adapter = CcxtVenueAdapter(bus=event_bus, exchange_id="binance")
        await adapter.connect()

        with pytest.raises(OrderError, match="Invalid amount"):
            await adapter.submit_order(_make_order())

    await asyncio.sleep(0.05)
    await event_bus.stop()


@pytest.mark.asyncio
async def test_error_wrapping_network_error(event_bus: EventBus) -> None:
    """ccxt NetworkError should be wrapped as SyslsConnectionError."""
    import ccxt

    await event_bus.start()

    mock_ex = _make_mock_exchange()
    mock_ex.create_order.side_effect = ccxt.NetworkError("Connection timeout")

    with patch("ccxt.binance", return_value=mock_ex, create=True):
        adapter = CcxtVenueAdapter(bus=event_bus, exchange_id="binance")
        await adapter.connect()

        with pytest.raises(SyslsConnectionError, match="Connection timeout"):
            await adapter.submit_order(_make_order())

    await asyncio.sleep(0.05)
    await event_bus.stop()


@pytest.mark.asyncio
async def test_error_wrapping_exchange_error(event_bus: EventBus) -> None:
    """ccxt ExchangeError should be wrapped as VenueError."""
    import ccxt

    await event_bus.start()

    mock_ex = _make_mock_exchange()
    mock_ex.create_order.side_effect = ccxt.ExchangeError("Rate limited")

    with patch("ccxt.binance", return_value=mock_ex, create=True):
        adapter = CcxtVenueAdapter(bus=event_bus, exchange_id="binance")
        await adapter.connect()

        with pytest.raises(VenueError, match="Rate limited"):
            await adapter.submit_order(_make_order())

    await asyncio.sleep(0.05)
    await event_bus.stop()


@pytest.mark.asyncio
async def test_error_wrapping_order_not_found(event_bus: EventBus) -> None:
    """ccxt OrderNotFound should be wrapped as OrderError."""
    import ccxt

    await event_bus.start()

    mock_ex = _make_mock_exchange()
    mock_ex.cancel_order.side_effect = ccxt.OrderNotFound("Order not found")

    with patch("ccxt.binance", return_value=mock_ex, create=True):
        adapter = CcxtVenueAdapter(bus=event_bus, exchange_id="binance")
        await adapter.connect()

        with pytest.raises(OrderError, match="Order not found"):
            await adapter.cancel_order("bad-id", _make_instrument())

    await asyncio.sleep(0.05)
    await event_bus.stop()


@pytest.mark.asyncio
async def test_not_connected_raises_venue_error(event_bus: EventBus) -> None:
    """Calling methods without connecting should raise VenueError."""
    adapter = CcxtVenueAdapter(bus=event_bus, exchange_id="binance")

    with pytest.raises(VenueError, match="Not connected"):
        await adapter.submit_order(_make_order())


# ---------------------------------------------------------------------------
# Symbol conversion tests
# ---------------------------------------------------------------------------


def test_symbol_conversion_spot(event_bus: EventBus) -> None:
    """Spot instrument with / in symbol should pass through."""
    adapter = CcxtVenueAdapter(bus=event_bus, exchange_id="binance")
    instrument = _make_instrument(symbol="BTC/USDT")
    assert adapter._to_ccxt_symbol(instrument) == "BTC/USDT"


def test_symbol_conversion_dash_format(event_bus: EventBus) -> None:
    """Instrument with dash format should be converted to slash format."""
    adapter = CcxtVenueAdapter(bus=event_bus, exchange_id="binance")
    instrument = _make_instrument(symbol="BTC-USDT")
    assert adapter._to_ccxt_symbol(instrument) == "BTC/USDT"


def test_symbol_conversion_perp(event_bus: EventBus) -> None:
    """Perpetual instrument should get :QUOTE appended."""
    adapter = CcxtVenueAdapter(bus=event_bus, exchange_id="binance")
    instrument = _make_instrument(
        symbol="BTC-USDT",
        asset_class=AssetClass.CRYPTO_PERP,
    )
    assert adapter._to_ccxt_symbol(instrument) == "BTC/USDT:USDT"


def test_symbol_conversion_future(event_bus: EventBus) -> None:
    """Future instrument should get :QUOTE appended."""
    adapter = CcxtVenueAdapter(bus=event_bus, exchange_id="binance")
    instrument = _make_instrument(
        symbol="ETH-USDT",
        asset_class=AssetClass.CRYPTO_FUTURE,
    )
    assert adapter._to_ccxt_symbol(instrument) == "ETH/USDT:USDT"


def test_symbol_conversion_base_only(event_bus: EventBus) -> None:
    """Single symbol name should use instrument currency as quote."""
    adapter = CcxtVenueAdapter(bus=event_bus, exchange_id="binance")
    instrument = _make_instrument(symbol="BTC", currency="USDT")
    assert adapter._to_ccxt_symbol(instrument) == "BTC/USDT"


# ---------------------------------------------------------------------------
# Build instrument tests
# ---------------------------------------------------------------------------


def test_build_instrument_spot() -> None:
    """_build_instrument should create a CRYPTO_SPOT for spot markets."""
    market = {
        "type": "spot",
        "quote": "USDT",
        "precision": {"price": 0.01, "amount": 0.001},
    }
    instrument = _build_instrument("BTC/USDT", market, "binance")
    assert instrument.asset_class == AssetClass.CRYPTO_SPOT
    assert instrument.symbol == "BTC/USDT"
    assert instrument.venue == Venue.CCXT
    assert instrument.exchange == "binance"
    assert instrument.currency == "USDT"
    assert instrument.tick_size == Decimal("0.01")
    assert instrument.lot_size == Decimal("0.001")


def test_build_instrument_swap() -> None:
    """_build_instrument should create CRYPTO_PERP for swap markets."""
    market = {
        "type": "swap",
        "quote": "USDT",
        "precision": {},
    }
    instrument = _build_instrument("BTC/USDT:USDT", market, "bybit")
    assert instrument.asset_class == AssetClass.CRYPTO_PERP


def test_build_instrument_future() -> None:
    """_build_instrument should create CRYPTO_FUTURE for future markets."""
    market = {
        "type": "future",
        "quote": "USD",
        "precision": {},
    }
    instrument = _build_instrument("BTC/USD:BTC-240329", market, "binance")
    assert instrument.asset_class == AssetClass.CRYPTO_FUTURE


# ---------------------------------------------------------------------------
# API credentials tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_credentials_passed_to_exchange(event_bus: EventBus) -> None:
    """API key and secret should be passed to the exchange constructor."""
    mock_constructor = MagicMock(return_value=_make_mock_exchange())

    with patch("ccxt.binance", mock_constructor, create=True):
        adapter = CcxtVenueAdapter(
            bus=event_bus,
            exchange_id="binance",
            api_key="test-key",
            api_secret="test-secret",
        )
        await adapter.connect()

        config = mock_constructor.call_args[0][0]
        assert config["apiKey"] == "test-key"
        assert config["secret"] == "test-secret"


@pytest.mark.asyncio
async def test_extra_config_merged(event_bus: EventBus) -> None:
    """Extra config should be merged into the exchange constructor config."""
    mock_constructor = MagicMock(return_value=_make_mock_exchange())

    with patch("ccxt.binance", mock_constructor, create=True):
        adapter = CcxtVenueAdapter(
            bus=event_bus,
            exchange_id="binance",
            extra_config={"timeout": 30000, "rateLimit": 1200},
        )
        await adapter.connect()

        config = mock_constructor.call_args[0][0]
        assert config["timeout"] == 30000
        assert config["rateLimit"] == 1200


# ---------------------------------------------------------------------------
# Load markets failure test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_load_markets_failure(event_bus: EventBus) -> None:
    """connect() should raise SyslsConnectionError if load_markets fails."""
    import ccxt

    mock_ex = _make_mock_exchange()
    mock_ex.load_markets.side_effect = ccxt.ExchangeError("Service unavailable")

    with patch("ccxt.binance", return_value=mock_ex, create=True):
        adapter = CcxtVenueAdapter(bus=event_bus, exchange_id="binance")

        with pytest.raises(SyslsConnectionError, match="Failed to load markets"):
            await adapter.connect()

        assert not adapter.is_connected
