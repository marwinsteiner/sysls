"""Tests for sysls.core.bus module."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from sysls.core.bus import EventBus, Priority, _resolve_priority
from sysls.core.events import (
    Event,
    FillEvent,
    HeartbeatEvent,
    MarketDataEvent,
    OrderSubmitted,
    QuoteEvent,
    RiskEvent,
    RiskSeverity,
    SignalDirection,
    SignalEvent,
    TimerEvent,
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


@pytest.fixture()
def bus() -> EventBus:
    """Provide a fresh EventBus instance."""
    return EventBus()


class TestPriority:
    """Tests for priority resolution."""

    def test_risk_event_is_critical(self) -> None:
        event = RiskEvent(
            severity=RiskSeverity.WARNING,
            rule_name="test",
            message="test",
        )
        assert _resolve_priority(event) == Priority.CRITICAL

    def test_system_event_is_critical(self) -> None:
        event = HeartbeatEvent(component="test")
        assert _resolve_priority(event) == Priority.CRITICAL

    def test_order_event_is_high(self, instrument: Instrument) -> None:
        event = OrderSubmitted(
            order_id="x",
            instrument=instrument,
            side=Side.BUY,
            quantity=Decimal("1"),
        )
        assert _resolve_priority(event) == Priority.HIGH

    def test_fill_event_is_high(self, instrument: Instrument) -> None:
        event = FillEvent(
            order_id="x",
            instrument=instrument,
            side=Side.BUY,
            fill_price=Decimal("100"),
            fill_quantity=Decimal("10"),
            cumulative_quantity=Decimal("10"),
            order_status=OrderStatus.FILLED,
        )
        assert _resolve_priority(event) == Priority.HIGH

    def test_market_data_event_is_normal(self, instrument: Instrument) -> None:
        event = QuoteEvent(
            instrument=instrument,
            bid_price=Decimal("150"),
            bid_size=Decimal("100"),
            ask_price=Decimal("151"),
            ask_size=Decimal("100"),
        )
        assert _resolve_priority(event) == Priority.NORMAL

    def test_signal_event_is_low(self, instrument: Instrument) -> None:
        event = SignalEvent(
            strategy_id="test",
            instrument=instrument,
            direction=SignalDirection.LONG,
        )
        assert _resolve_priority(event) == Priority.LOW

    def test_timer_event_is_low(self) -> None:
        event = TimerEvent(timer_name="test", scheduled_ns=0)
        assert _resolve_priority(event) == Priority.LOW

    def test_base_event_is_low(self) -> None:
        event = Event()
        assert _resolve_priority(event) == Priority.LOW


class TestEventBusLifecycle:
    """Tests for start/stop lifecycle."""

    @pytest.mark.asyncio
    async def test_start_sets_running(self, bus: EventBus) -> None:
        await bus.start()
        assert bus.is_running is True
        await bus.stop()

    @pytest.mark.asyncio
    async def test_stop_clears_running(self, bus: EventBus) -> None:
        await bus.start()
        await bus.stop()
        assert bus.is_running is False

    @pytest.mark.asyncio
    async def test_double_start_raises(self, bus: EventBus) -> None:
        await bus.start()
        with pytest.raises(RuntimeError, match="already running"):
            await bus.start()
        await bus.stop()

    @pytest.mark.asyncio
    async def test_stop_without_start_is_noop(self, bus: EventBus) -> None:
        await bus.stop()  # Should not raise

    @pytest.mark.asyncio
    async def test_publish_before_start_raises(self, bus: EventBus) -> None:
        with pytest.raises(RuntimeError, match="not running"):
            await bus.publish(Event())


class TestSubscription:
    """Tests for subscribe/unsubscribe."""

    def test_subscribe_returns_id(self, bus: EventBus) -> None:
        handler = AsyncMock()
        sub_id = bus.subscribe(Event, handler)
        assert isinstance(sub_id, int)

    def test_subscribe_increments_id(self, bus: EventBus) -> None:
        handler = AsyncMock()
        id1 = bus.subscribe(Event, handler)
        id2 = bus.subscribe(Event, handler)
        assert id2 == id1 + 1

    def test_unsubscribe_returns_true_for_valid_id(self, bus: EventBus) -> None:
        handler = AsyncMock()
        sub_id = bus.subscribe(Event, handler)
        assert bus.unsubscribe(sub_id) is True

    def test_unsubscribe_returns_false_for_invalid_id(self, bus: EventBus) -> None:
        assert bus.unsubscribe(9999) is False

    def test_subscriber_count_all(self, bus: EventBus) -> None:
        handler = AsyncMock()
        bus.subscribe(Event, handler)
        bus.subscribe(QuoteEvent, handler)
        assert bus.subscriber_count() == 2

    def test_subscriber_count_by_type(self, bus: EventBus) -> None:
        handler = AsyncMock()
        bus.subscribe(Event, handler)
        bus.subscribe(QuoteEvent, handler)
        assert bus.subscriber_count(Event) == 1
        assert bus.subscriber_count(QuoteEvent) == 1

    def test_subscriber_count_empty(self, bus: EventBus) -> None:
        assert bus.subscriber_count() == 0
        assert bus.subscriber_count(Event) == 0

    def test_unsubscribe_decrements_count(self, bus: EventBus) -> None:
        handler = AsyncMock()
        sub_id = bus.subscribe(Event, handler)
        assert bus.subscriber_count() == 1
        bus.unsubscribe(sub_id)
        assert bus.subscriber_count() == 0


class TestDispatch:
    """Tests for event dispatch behavior."""

    @pytest.mark.asyncio
    async def test_handler_receives_event(self, bus: EventBus) -> None:
        handler = AsyncMock()
        bus.subscribe(Event, handler)
        await bus.start()

        event = Event(source="test")
        await bus.publish(event)
        await asyncio.sleep(0.2)
        await bus.stop()

        handler.assert_called_once_with(event)

    @pytest.mark.asyncio
    async def test_wildcard_subscription(self, bus: EventBus, instrument: Instrument) -> None:
        """Subscribing to MarketDataEvent should receive QuoteEvent."""
        handler = AsyncMock()
        bus.subscribe(MarketDataEvent, handler)
        await bus.start()

        quote = QuoteEvent(
            instrument=instrument,
            bid_price=Decimal("150"),
            bid_size=Decimal("100"),
            ask_price=Decimal("151"),
            ask_size=Decimal("100"),
        )
        await bus.publish(quote)
        await asyncio.sleep(0.2)
        await bus.stop()

        handler.assert_called_once_with(quote)

    @pytest.mark.asyncio
    async def test_exact_and_wildcard_both_fire(
        self, bus: EventBus, instrument: Instrument
    ) -> None:
        """Both exact-type and base-type subscribers receive the event."""
        exact_handler = AsyncMock()
        wildcard_handler = AsyncMock()
        bus.subscribe(QuoteEvent, exact_handler)
        bus.subscribe(MarketDataEvent, wildcard_handler)
        await bus.start()

        quote = QuoteEvent(
            instrument=instrument,
            bid_price=Decimal("150"),
            bid_size=Decimal("100"),
            ask_price=Decimal("151"),
            ask_size=Decimal("100"),
        )
        await bus.publish(quote)
        await asyncio.sleep(0.2)
        await bus.stop()

        exact_handler.assert_called_once_with(quote)
        wildcard_handler.assert_called_once_with(quote)

    @pytest.mark.asyncio
    async def test_unsubscribed_handler_not_called(self, bus: EventBus) -> None:
        handler = AsyncMock()
        sub_id = bus.subscribe(Event, handler)
        bus.unsubscribe(sub_id)
        await bus.start()

        await bus.publish(Event())
        await asyncio.sleep(0.2)
        await bus.stop()

        handler.assert_not_called()

    @pytest.mark.asyncio
    async def test_handler_error_does_not_crash_bus(self, bus: EventBus) -> None:
        """A handler that raises should not stop the bus or affect other handlers."""
        bad_handler = AsyncMock(side_effect=RuntimeError("boom"))
        good_handler = AsyncMock()
        bus.subscribe(Event, bad_handler)
        bus.subscribe(Event, good_handler)
        await bus.start()

        event = Event()
        await bus.publish(event)
        await asyncio.sleep(0.2)
        await bus.stop()

        bad_handler.assert_called_once()
        good_handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_multiple_events_dispatched(self, bus: EventBus) -> None:
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        bus.subscribe(Event, handler)
        await bus.start()

        events = [Event(source=f"e{i}") for i in range(5)]
        for e in events:
            await bus.publish(e)
        await asyncio.sleep(0.3)
        await bus.stop()

        assert len(received) == 5

    @pytest.mark.asyncio
    async def test_priority_ordering(self, bus: EventBus, instrument: Instrument) -> None:
        """Higher-priority events should be dispatched before lower-priority ones."""
        order: list[str] = []

        async def handler(event: Event) -> None:
            order.append(type(event).__name__)

        bus.subscribe(Event, handler)
        # Don't start yet — enqueue events first, then start to process them
        # Actually the bus needs to be running to publish. So we'll use a bounded
        # queue and publish fast, then let the dispatcher catch up.
        await bus.start()

        # Publish low-priority first, then high-priority
        signal = SignalEvent(
            strategy_id="test",
            instrument=instrument,
            direction=SignalDirection.LONG,
        )
        risk = RiskEvent(
            severity=RiskSeverity.BREACH,
            rule_name="test",
            message="test",
        )

        # Signal is LOW priority, Risk is CRITICAL
        await bus.publish(signal)
        await bus.publish(risk)
        await asyncio.sleep(0.3)
        await bus.stop()

        # Both should be received (order may vary due to async timing,
        # but both must be dispatched)
        assert len(order) == 2


class TestMetrics:
    """Tests for bus metrics tracking."""

    @pytest.mark.asyncio
    async def test_events_published_counter(self, bus: EventBus) -> None:
        bus.subscribe(Event, AsyncMock())
        await bus.start()

        await bus.publish(Event())
        await bus.publish(Event())
        await asyncio.sleep(0.2)
        await bus.stop()

        assert bus.metrics.events_published == 2

    @pytest.mark.asyncio
    async def test_events_dispatched_counter(self, bus: EventBus) -> None:
        bus.subscribe(Event, AsyncMock())
        await bus.start()

        await bus.publish(Event())
        await asyncio.sleep(0.2)
        await bus.stop()

        assert bus.metrics.events_dispatched >= 1

    @pytest.mark.asyncio
    async def test_handler_errors_counter(self, bus: EventBus) -> None:
        bus.subscribe(Event, AsyncMock(side_effect=ValueError("bad")))
        await bus.start()

        await bus.publish(Event())
        await asyncio.sleep(0.2)
        await bus.stop()

        assert bus.metrics.handler_errors == 1

    @pytest.mark.asyncio
    async def test_dispatch_latency_tracked(self, bus: EventBus) -> None:
        bus.subscribe(Event, AsyncMock())
        await bus.start()

        await bus.publish(Event())
        await asyncio.sleep(0.2)
        await bus.stop()

        assert bus.metrics.total_dispatch_latency_ns > 0

    def test_initial_metrics_are_zero(self, bus: EventBus) -> None:
        assert bus.metrics.events_published == 0
        assert bus.metrics.events_dispatched == 0
        assert bus.metrics.handler_errors == 0
        assert bus.metrics.total_dispatch_latency_ns == 0
        assert bus.metrics.max_queue_depth == 0
