"""Tests for sysls.core.types module."""

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
    TimeInForce,
    Venue,
    generate_order_id,
)


class TestEnums:
    """Tests for core enum types."""

    def test_side_values(self) -> None:
        assert Side.BUY == "BUY"
        assert Side.SELL == "SELL"
        assert len(Side) == 2

    def test_order_type_values(self) -> None:
        assert OrderType.MARKET == "MARKET"
        assert OrderType.LIMIT == "LIMIT"
        assert OrderType.STOP == "STOP"
        assert OrderType.STOP_LIMIT == "STOP_LIMIT"
        assert len(OrderType) == 4

    def test_time_in_force_values(self) -> None:
        assert TimeInForce.GTC == "GTC"
        assert TimeInForce.IOC == "IOC"
        assert TimeInForce.FOK == "FOK"
        assert TimeInForce.DAY == "DAY"
        assert TimeInForce.GTD == "GTD"
        assert len(TimeInForce) == 5

    def test_asset_class_values(self) -> None:
        assert AssetClass.EQUITY == "EQUITY"
        assert AssetClass.CRYPTO_SPOT == "CRYPTO_SPOT"
        assert AssetClass.CRYPTO_PERP == "CRYPTO_PERP"
        assert AssetClass.EVENT == "EVENT"
        assert len(AssetClass) == 7

    def test_venue_values(self) -> None:
        assert Venue.TASTYTRADE == "TASTYTRADE"
        assert Venue.IBKR == "IBKR"
        assert Venue.CCXT == "CCXT"
        assert Venue.POLYMARKET == "POLYMARKET"
        assert Venue.PAPER == "PAPER"
        assert len(Venue) == 5

    def test_order_status_values(self) -> None:
        assert OrderStatus.PENDING == "PENDING"
        assert OrderStatus.FILLED == "FILLED"
        assert OrderStatus.CANCELLED == "CANCELLED"
        assert len(OrderStatus) == 8

    def test_enums_are_string_compatible(self) -> None:
        assert isinstance(Side.BUY, str)
        assert isinstance(OrderType.MARKET, str)
        assert isinstance(Venue.PAPER, str)


class TestInstrument:
    """Tests for the Instrument model."""

    def test_create_equity(self) -> None:
        inst = Instrument(
            symbol="NVDA",
            asset_class=AssetClass.EQUITY,
            venue=Venue.TASTYTRADE,
        )
        assert inst.symbol == "NVDA"
        assert inst.asset_class == AssetClass.EQUITY
        assert inst.venue == Venue.TASTYTRADE
        assert inst.currency == "USD"
        assert inst.multiplier == Decimal("1")
        assert inst.exchange is None

    def test_create_crypto_perp(self) -> None:
        inst = Instrument(
            symbol="BTC-USDT-PERP",
            asset_class=AssetClass.CRYPTO_PERP,
            venue=Venue.CCXT,
            exchange="binance",
            currency="USDT",
            multiplier=Decimal("10"),
            tick_size=Decimal("0.01"),
            lot_size=Decimal("0.001"),
        )
        assert inst.symbol == "BTC-USDT-PERP"
        assert inst.exchange == "binance"
        assert inst.currency == "USDT"
        assert inst.multiplier == Decimal("10")
        assert inst.tick_size == Decimal("0.01")
        assert inst.lot_size == Decimal("0.001")

    def test_instrument_is_frozen(self) -> None:
        inst = Instrument(
            symbol="NVDA",
            asset_class=AssetClass.EQUITY,
            venue=Venue.TASTYTRADE,
        )
        with pytest.raises(Exception):  # noqa: B017
            inst.symbol = "AAPL"  # type: ignore[misc]

    def test_instrument_equality(self) -> None:
        inst1 = Instrument(
            symbol="NVDA",
            asset_class=AssetClass.EQUITY,
            venue=Venue.TASTYTRADE,
        )
        inst2 = Instrument(
            symbol="NVDA",
            asset_class=AssetClass.EQUITY,
            venue=Venue.TASTYTRADE,
        )
        assert inst1 == inst2

    def test_instrument_inequality(self) -> None:
        inst1 = Instrument(
            symbol="NVDA",
            asset_class=AssetClass.EQUITY,
            venue=Venue.TASTYTRADE,
        )
        inst2 = Instrument(
            symbol="AAPL",
            asset_class=AssetClass.EQUITY,
            venue=Venue.TASTYTRADE,
        )
        assert inst1 != inst2

    def test_instrument_hashable(self) -> None:
        inst = Instrument(
            symbol="NVDA",
            asset_class=AssetClass.EQUITY,
            venue=Venue.TASTYTRADE,
        )
        s = {inst}
        assert len(s) == 1
        assert inst in s

    def test_instrument_str(self) -> None:
        inst = Instrument(
            symbol="NVDA",
            asset_class=AssetClass.EQUITY,
            venue=Venue.TASTYTRADE,
        )
        assert str(inst) == "NVDA:EQUITY:TASTYTRADE"

    def test_instrument_str_with_exchange(self) -> None:
        inst = Instrument(
            symbol="BTC-USDT",
            asset_class=AssetClass.CRYPTO_SPOT,
            venue=Venue.CCXT,
            exchange="binance",
        )
        assert str(inst) == "BTC-USDT:CRYPTO_SPOT:CCXT:binance"

    def test_instrument_json_roundtrip(self) -> None:
        inst = Instrument(
            symbol="NVDA",
            asset_class=AssetClass.EQUITY,
            venue=Venue.TASTYTRADE,
            tick_size=Decimal("0.01"),
        )
        json_str = inst.model_dump_json()
        restored = Instrument.model_validate_json(json_str)
        assert inst == restored


class TestGenerateOrderId:
    """Tests for generate_order_id."""

    def test_returns_string(self) -> None:
        oid = generate_order_id()
        assert isinstance(oid, str)

    def test_uniqueness(self) -> None:
        ids = {generate_order_id() for _ in range(100)}
        assert len(ids) == 100


class TestOrderRequest:
    """Tests for the OrderRequest model."""

    @pytest.fixture()
    def instrument(self) -> Instrument:
        return Instrument(
            symbol="NVDA",
            asset_class=AssetClass.EQUITY,
            venue=Venue.TASTYTRADE,
        )

    def test_market_order(self, instrument: Instrument) -> None:
        order = OrderRequest(
            instrument=instrument,
            side=Side.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("100"),
        )
        assert order.side == Side.BUY
        assert order.order_type == OrderType.MARKET
        assert order.quantity == Decimal("100")
        assert order.price is None
        assert order.time_in_force == TimeInForce.GTC
        assert order.order_id  # auto-generated

    def test_limit_order(self, instrument: Instrument) -> None:
        order = OrderRequest(
            instrument=instrument,
            side=Side.SELL,
            order_type=OrderType.LIMIT,
            quantity=Decimal("50"),
            price=Decimal("150.25"),
            time_in_force=TimeInForce.DAY,
        )
        assert order.price == Decimal("150.25")
        assert order.time_in_force == TimeInForce.DAY

    def test_order_request_is_frozen(self, instrument: Instrument) -> None:
        order = OrderRequest(
            instrument=instrument,
            side=Side.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("100"),
        )
        with pytest.raises(Exception):  # noqa: B017
            order.quantity = Decimal("200")  # type: ignore[misc]

    def test_order_request_auto_generates_id(self, instrument: Instrument) -> None:
        order1 = OrderRequest(
            instrument=instrument,
            side=Side.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("100"),
        )
        order2 = OrderRequest(
            instrument=instrument,
            side=Side.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("100"),
        )
        assert order1.order_id != order2.order_id

    def test_order_request_json_roundtrip(self, instrument: Instrument) -> None:
        order = OrderRequest(
            instrument=instrument,
            side=Side.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("100"),
            price=Decimal("150.00"),
            client_order_id="my-order-1",
        )
        json_str = order.model_dump_json()
        restored = OrderRequest.model_validate_json(json_str)
        assert order.instrument == restored.instrument
        assert order.side == restored.side
        assert order.quantity == restored.quantity
        assert order.price == restored.price
