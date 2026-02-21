"""Tests for sysls.core.events module."""

from __future__ import annotations

from decimal import Decimal

import pytest

from sysls.core.events import (
    BarEvent,
    ConnectionEvent,
    ConnectionStatus,
    ErrorEvent,
    Event,
    FillEvent,
    HeartbeatEvent,
    MarketDataEvent,
    OrderAccepted,
    OrderAmended,
    OrderBookEvent,
    OrderCancelled,
    OrderEvent,
    OrderRejected,
    OrderSubmitted,
    PositionEvent,
    QuoteEvent,
    RiskEvent,
    RiskSeverity,
    SignalDirection,
    SignalEvent,
    SystemEvent,
    TimerEvent,
    TradeEvent,
)
from sysls.core.types import AssetClass, Instrument, OrderStatus, Side, Venue


@pytest.fixture()
def instrument() -> Instrument:
    """Provide a standard test instrument."""
    return Instrument(
        symbol="NVDA",
        asset_class=AssetClass.EQUITY,
        venue=Venue.TASTYTRADE,
    )


class TestBaseEvent:
    """Tests for the base Event class."""

    def test_event_has_auto_id(self) -> None:
        event = Event()
        assert event.event_id
        assert isinstance(event.event_id, str)

    def test_event_has_timestamp(self) -> None:
        event = Event()
        assert event.timestamp_ns > 0

    def test_event_ids_are_unique(self) -> None:
        events = [Event() for _ in range(100)]
        ids = {e.event_id for e in events}
        assert len(ids) == 100

    def test_event_is_frozen(self) -> None:
        event = Event()
        with pytest.raises(Exception):  # noqa: B017
            event.source = "test"  # type: ignore[misc]

    def test_event_with_source(self) -> None:
        event = Event(source="test-component")
        assert event.source == "test-component"

    def test_event_default_source_is_none(self) -> None:
        event = Event()
        assert event.source is None


class TestMarketDataEvents:
    """Tests for market data event types."""

    def test_quote_event(self, instrument: Instrument) -> None:
        event = QuoteEvent(
            instrument=instrument,
            bid_price=Decimal("150.00"),
            bid_size=Decimal("100"),
            ask_price=Decimal("150.05"),
            ask_size=Decimal("200"),
        )
        assert event.bid_price == Decimal("150.00")
        assert event.ask_price == Decimal("150.05")
        assert isinstance(event, MarketDataEvent)
        assert isinstance(event, Event)

    def test_trade_event(self, instrument: Instrument) -> None:
        event = TradeEvent(
            instrument=instrument,
            price=Decimal("150.03"),
            size=Decimal("50"),
            side=Side.BUY,
        )
        assert event.price == Decimal("150.03")
        assert event.side == Side.BUY

    def test_trade_event_side_optional(self, instrument: Instrument) -> None:
        event = TradeEvent(
            instrument=instrument,
            price=Decimal("150.03"),
            size=Decimal("50"),
        )
        assert event.side is None

    def test_bar_event(self, instrument: Instrument) -> None:
        event = BarEvent(
            instrument=instrument,
            open=Decimal("149.00"),
            high=Decimal("151.00"),
            low=Decimal("148.50"),
            close=Decimal("150.00"),
            volume=Decimal("1000000"),
            bar_start_ns=1_000_000_000,
            bar_end_ns=1_060_000_000_000,
        )
        assert event.open == Decimal("149.00")
        assert event.close == Decimal("150.00")
        assert event.volume == Decimal("1000000")

    def test_orderbook_event(self, instrument: Instrument) -> None:
        bids = ((Decimal("150.00"), Decimal("100")), (Decimal("149.99"), Decimal("200")))
        asks = ((Decimal("150.05"), Decimal("50")), (Decimal("150.10"), Decimal("150")))
        event = OrderBookEvent(
            instrument=instrument,
            bids=bids,
            asks=asks,
            is_snapshot=True,
        )
        assert len(event.bids) == 2
        assert len(event.asks) == 2
        assert event.is_snapshot is True

    def test_orderbook_event_defaults(self, instrument: Instrument) -> None:
        event = OrderBookEvent(instrument=instrument)
        assert event.bids == ()
        assert event.asks == ()
        assert event.is_snapshot is True


class TestOrderEvents:
    """Tests for order lifecycle event types."""

    def test_order_submitted(self, instrument: Instrument) -> None:
        event = OrderSubmitted(
            order_id="order-123",
            instrument=instrument,
            side=Side.BUY,
            quantity=Decimal("100"),
            price=Decimal("150.00"),
        )
        assert event.order_id == "order-123"
        assert event.side == Side.BUY
        assert isinstance(event, OrderEvent)

    def test_order_accepted(self, instrument: Instrument) -> None:
        event = OrderAccepted(
            order_id="order-123",
            instrument=instrument,
            venue_order_id="venue-456",
        )
        assert event.venue_order_id == "venue-456"

    def test_order_rejected(self, instrument: Instrument) -> None:
        event = OrderRejected(
            order_id="order-123",
            instrument=instrument,
            reason="Insufficient buying power",
        )
        assert event.reason == "Insufficient buying power"

    def test_order_cancelled(self, instrument: Instrument) -> None:
        event = OrderCancelled(
            order_id="order-123",
            instrument=instrument,
            reason="User requested",
        )
        assert event.reason == "User requested"

    def test_order_cancelled_no_reason(self, instrument: Instrument) -> None:
        event = OrderCancelled(
            order_id="order-123",
            instrument=instrument,
        )
        assert event.reason is None

    def test_order_amended(self, instrument: Instrument) -> None:
        event = OrderAmended(
            order_id="order-123",
            instrument=instrument,
            new_quantity=Decimal("200"),
            new_price=Decimal("151.00"),
        )
        assert event.new_quantity == Decimal("200")
        assert event.new_price == Decimal("151.00")


class TestFillEvent:
    """Tests for FillEvent."""

    def test_fill_event(self, instrument: Instrument) -> None:
        event = FillEvent(
            order_id="order-123",
            instrument=instrument,
            side=Side.BUY,
            fill_price=Decimal("150.03"),
            fill_quantity=Decimal("50"),
            cumulative_quantity=Decimal("50"),
            order_status=OrderStatus.PARTIALLY_FILLED,
        )
        assert event.fill_price == Decimal("150.03")
        assert event.order_status == OrderStatus.PARTIALLY_FILLED
        assert event.commission is None

    def test_fill_event_with_commission(self, instrument: Instrument) -> None:
        event = FillEvent(
            order_id="order-123",
            instrument=instrument,
            side=Side.BUY,
            fill_price=Decimal("150.03"),
            fill_quantity=Decimal("100"),
            cumulative_quantity=Decimal("100"),
            order_status=OrderStatus.FILLED,
            venue_fill_id="fill-789",
            commission=Decimal("1.50"),
        )
        assert event.commission == Decimal("1.50")
        assert event.venue_fill_id == "fill-789"


class TestPositionEvent:
    """Tests for PositionEvent."""

    def test_position_event(self, instrument: Instrument) -> None:
        event = PositionEvent(
            instrument=instrument,
            quantity=Decimal("100"),
            avg_price=Decimal("150.00"),
            realized_pnl=Decimal("25.50"),
        )
        assert event.quantity == Decimal("100")
        assert event.realized_pnl == Decimal("25.50")

    def test_position_event_default_pnl(self, instrument: Instrument) -> None:
        event = PositionEvent(
            instrument=instrument,
            quantity=Decimal("100"),
            avg_price=Decimal("150.00"),
        )
        assert event.realized_pnl == Decimal("0")


class TestSignalEvent:
    """Tests for SignalEvent."""

    def test_signal_direction_values(self) -> None:
        assert SignalDirection.LONG == "LONG"
        assert SignalDirection.SHORT == "SHORT"
        assert SignalDirection.FLAT == "FLAT"

    def test_signal_event(self, instrument: Instrument) -> None:
        event = SignalEvent(
            strategy_id="my-strategy",
            instrument=instrument,
            direction=SignalDirection.LONG,
            strength=0.85,
            metadata={"reason": "vol breakout"},
        )
        assert event.strategy_id == "my-strategy"
        assert event.direction == SignalDirection.LONG
        assert event.strength == 0.85
        assert event.metadata["reason"] == "vol breakout"

    def test_signal_event_defaults(self, instrument: Instrument) -> None:
        event = SignalEvent(
            strategy_id="my-strategy",
            instrument=instrument,
            direction=SignalDirection.FLAT,
        )
        assert event.strength == 1.0
        assert event.metadata == {}


class TestRiskEvent:
    """Tests for RiskEvent."""

    def test_risk_severity_values(self) -> None:
        assert RiskSeverity.INFO == "INFO"
        assert RiskSeverity.WARNING == "WARNING"
        assert RiskSeverity.BREACH == "BREACH"

    def test_risk_event(self, instrument: Instrument) -> None:
        event = RiskEvent(
            severity=RiskSeverity.BREACH,
            rule_name="max_position_size",
            message="Position size exceeds limit",
            instrument=instrument,
            current_value=1500.0,
            limit_value=1000.0,
        )
        assert event.severity == RiskSeverity.BREACH
        assert event.rule_name == "max_position_size"
        assert event.current_value == 1500.0
        assert event.limit_value == 1000.0

    def test_risk_event_no_instrument(self) -> None:
        event = RiskEvent(
            severity=RiskSeverity.WARNING,
            rule_name="portfolio_drawdown",
            message="Drawdown approaching limit",
        )
        assert event.instrument is None
        assert event.current_value is None


class TestSystemEvents:
    """Tests for system event types."""

    def test_heartbeat_event(self) -> None:
        event = HeartbeatEvent(component="oms")
        assert event.component == "oms"
        assert isinstance(event, SystemEvent)

    def test_connection_event(self) -> None:
        event = ConnectionEvent(
            venue="TASTYTRADE",
            status=ConnectionStatus.CONNECTED,
        )
        assert event.venue == "TASTYTRADE"
        assert event.status == ConnectionStatus.CONNECTED
        assert event.reason is None

    def test_connection_event_with_reason(self) -> None:
        event = ConnectionEvent(
            venue="IBKR",
            status=ConnectionStatus.DISCONNECTED,
            reason="Server maintenance",
        )
        assert event.reason == "Server maintenance"

    def test_connection_status_values(self) -> None:
        assert ConnectionStatus.CONNECTED == "CONNECTED"
        assert ConnectionStatus.DISCONNECTED == "DISCONNECTED"
        assert ConnectionStatus.RECONNECTING == "RECONNECTING"

    def test_error_event(self) -> None:
        event = ErrorEvent(
            error_type="VenueError",
            message="Connection timeout",
            component="tastytrade-adapter",
            recoverable=True,
        )
        assert event.error_type == "VenueError"
        assert event.recoverable is True

    def test_error_event_defaults(self) -> None:
        event = ErrorEvent(
            error_type="RuntimeError",
            message="Something went wrong",
        )
        assert event.component is None
        assert event.recoverable is True


class TestTimerEvent:
    """Tests for TimerEvent."""

    def test_timer_event(self) -> None:
        event = TimerEvent(
            timer_name="risk_check",
            scheduled_ns=1_000_000_000_000,
        )
        assert event.timer_name == "risk_check"
        assert event.scheduled_ns == 1_000_000_000_000
        assert isinstance(event, Event)


class TestEventHierarchy:
    """Tests for event type hierarchy relationships."""

    def test_market_data_events_inherit_from_event(self, instrument: Instrument) -> None:
        quote = QuoteEvent(
            instrument=instrument,
            bid_price=Decimal("1"),
            bid_size=Decimal("1"),
            ask_price=Decimal("2"),
            ask_size=Decimal("1"),
        )
        assert isinstance(quote, MarketDataEvent)
        assert isinstance(quote, Event)

    def test_order_events_inherit_from_event(self, instrument: Instrument) -> None:
        submitted = OrderSubmitted(
            order_id="x",
            instrument=instrument,
            side=Side.BUY,
            quantity=Decimal("1"),
        )
        assert isinstance(submitted, OrderEvent)
        assert isinstance(submitted, Event)

    def test_system_events_inherit_from_event(self) -> None:
        heartbeat = HeartbeatEvent(component="test")
        assert isinstance(heartbeat, SystemEvent)
        assert isinstance(heartbeat, Event)

    def test_event_json_roundtrip(self, instrument: Instrument) -> None:
        event = QuoteEvent(
            instrument=instrument,
            bid_price=Decimal("150.00"),
            bid_size=Decimal("100"),
            ask_price=Decimal("150.05"),
            ask_size=Decimal("200"),
            source="test",
        )
        json_str = event.model_dump_json()
        restored = QuoteEvent.model_validate_json(json_str)
        assert event.bid_price == restored.bid_price
        assert event.instrument == restored.instrument
        assert event.source == restored.source
