"""Tests for sysls.strategy.base module."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

import pytest

from sysls.core.bus import EventBus
from sysls.core.clock import LiveClock
from sysls.core.events import (
    BarEvent,
    FillEvent,
    MarketDataEvent,
    OrderSubmitted,
    PositionEvent,
    SignalDirection,
    SignalEvent,
)
from sysls.core.types import (
    AssetClass,
    Instrument,
    OrderStatus,
    OrderType,
    Side,
    Venue,
)
from sysls.strategy.base import Strategy, StrategyContext

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def instrument() -> Instrument:
    """Provide a standard test instrument."""
    return Instrument(
        symbol="NVDA",
        asset_class=AssetClass.EQUITY,
        venue=Venue.TASTYTRADE,
    )


@pytest.fixture()
def instrument_btc() -> Instrument:
    """Provide a second test instrument."""
    return Instrument(
        symbol="BTC-USDT-PERP",
        asset_class=AssetClass.CRYPTO_PERP,
        venue=Venue.CCXT,
        exchange="binance",
        currency="USDT",
    )


@pytest.fixture()
def bus() -> EventBus:
    """Provide a fresh EventBus instance."""
    return EventBus()


@pytest.fixture()
def clock() -> LiveClock:
    """Provide a LiveClock instance."""
    return LiveClock()


@pytest.fixture()
def context(bus: EventBus, clock: LiveClock) -> StrategyContext:
    """Provide a StrategyContext."""
    return StrategyContext(bus=bus, clock=clock)


# ---------------------------------------------------------------------------
# Concrete test strategy
# ---------------------------------------------------------------------------


class _TestStrategy(Strategy):
    """Concrete strategy subclass that records calls for testing."""

    def __init__(
        self,
        strategy_id: str,
        context: StrategyContext,
        instruments: list[Instrument],
        params: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(strategy_id, context, instruments, params)
        self.market_data_events: list[MarketDataEvent] = []
        self.fill_events: list[FillEvent] = []
        self.position_events: list[PositionEvent] = []
        self.started: bool = False
        self.stopped: bool = False

    async def on_market_data(self, event: MarketDataEvent) -> None:
        """Record market data events."""
        self.market_data_events.append(event)

    async def on_start(self) -> None:
        """Record start."""
        self.started = True

    async def on_stop(self) -> None:
        """Record stop."""
        self.stopped = True

    async def on_fill(self, event: FillEvent) -> None:
        """Record fills."""
        self.fill_events.append(event)

    async def on_position(self, event: PositionEvent) -> None:
        """Record position events."""
        self.position_events.append(event)


# ---------------------------------------------------------------------------
# StrategyContext tests
# ---------------------------------------------------------------------------


class TestStrategyContext:
    """Tests for StrategyContext."""

    def test_context_properties(self, bus: EventBus, clock: LiveClock) -> None:
        """Context exposes bus and clock via properties."""
        ctx = StrategyContext(bus=bus, clock=clock)
        assert ctx.bus is bus
        assert ctx.clock is clock

    @pytest.mark.asyncio
    async def test_context_emit_signal(
        self,
        bus: EventBus,
        clock: LiveClock,
        instrument: Instrument,
    ) -> None:
        """emit_signal publishes a SignalEvent on the bus."""
        ctx = StrategyContext(bus=bus, clock=clock)
        received: list[SignalEvent] = []

        async def handler(event: SignalEvent) -> None:
            received.append(event)

        bus.subscribe(SignalEvent, handler)
        await bus.start()
        try:
            await ctx.emit_signal(
                strategy_id="test-strat",
                instrument=instrument,
                direction=SignalDirection.LONG,
                strength=0.75,
                metadata={"reason": "breakout"},
            )
            # Allow dispatch
            await asyncio.sleep(0.05)
        finally:
            await bus.stop()

        assert len(received) == 1
        event = received[0]
        assert event.strategy_id == "test-strat"
        assert event.instrument == instrument
        assert event.direction == SignalDirection.LONG
        assert event.strength == 0.75
        assert event.metadata == {"reason": "breakout"}
        assert event.source == "strategy:test-strat"

    @pytest.mark.asyncio
    async def test_context_request_order(
        self,
        bus: EventBus,
        clock: LiveClock,
        instrument: Instrument,
    ) -> None:
        """request_order creates an OrderRequest and publishes OrderSubmitted."""
        ctx = StrategyContext(bus=bus, clock=clock)
        submitted: list[OrderSubmitted] = []

        async def handler(event: OrderSubmitted) -> None:
            submitted.append(event)

        bus.subscribe(OrderSubmitted, handler)
        await bus.start()
        try:
            request = await ctx.request_order(
                instrument=instrument,
                side=Side.BUY,
                quantity=Decimal("10"),
                order_type=OrderType.LIMIT,
                price=Decimal("150.00"),
            )
            await asyncio.sleep(0.05)
        finally:
            await bus.stop()

        # Verify OrderRequest returned
        assert request.instrument == instrument
        assert request.side == Side.BUY
        assert request.quantity == Decimal("10")
        assert request.order_type == OrderType.LIMIT
        assert request.price == Decimal("150.00")

        # Verify OrderSubmitted event published
        assert len(submitted) == 1
        event = submitted[0]
        assert event.order_id == request.order_id
        assert event.instrument == instrument
        assert event.side == Side.BUY
        assert event.quantity == Decimal("10")
        assert event.price == Decimal("150.00")


# ---------------------------------------------------------------------------
# Strategy ABC tests
# ---------------------------------------------------------------------------


class TestStrategyInit:
    """Tests for Strategy initialization."""

    def test_strategy_init_stores_attributes(
        self,
        context: StrategyContext,
        instrument: Instrument,
    ) -> None:
        """Strategy stores strategy_id, instruments, and params."""
        strat = _TestStrategy(
            strategy_id="my-strat",
            context=context,
            instruments=[instrument],
            params={"lookback": 20},
        )
        assert strat.strategy_id == "my-strat"
        assert strat.instruments == [instrument]
        assert strat.params == {"lookback": 20}

    def test_strategy_default_params_empty_dict(
        self,
        context: StrategyContext,
        instrument: Instrument,
    ) -> None:
        """When params is None, defaults to empty dict."""
        strat = _TestStrategy(
            strategy_id="s1",
            context=context,
            instruments=[instrument],
        )
        assert strat.params == {}

    def test_strategy_strategy_id_property(
        self,
        context: StrategyContext,
        instrument: Instrument,
    ) -> None:
        """strategy_id property returns the ID."""
        strat = _TestStrategy("abc-123", context, [instrument])
        assert strat.strategy_id == "abc-123"

    def test_strategy_instruments_property(
        self,
        context: StrategyContext,
        instrument: Instrument,
        instrument_btc: Instrument,
    ) -> None:
        """instruments property returns a copy of the instruments list."""
        instruments = [instrument, instrument_btc]
        strat = _TestStrategy("s1", context, instruments)
        result = strat.instruments
        assert result == instruments
        # Should be a copy, not the same list
        assert result is not strat._instruments


class TestStrategyLifecycle:
    """Tests for strategy lifecycle hooks."""

    @pytest.mark.asyncio
    async def test_strategy_lifecycle_on_start_default_noop(
        self,
        context: StrategyContext,
        instrument: Instrument,
    ) -> None:
        """Default on_start is a no-op (doesn't raise)."""

        class _MinimalStrategy(Strategy):
            async def on_market_data(self, event: MarketDataEvent) -> None:
                pass

        strat = _MinimalStrategy("s1", context, [instrument])
        # Should not raise
        await strat.on_start()

    @pytest.mark.asyncio
    async def test_strategy_lifecycle_on_stop_default_noop(
        self,
        context: StrategyContext,
        instrument: Instrument,
    ) -> None:
        """Default on_stop is a no-op (doesn't raise)."""

        class _MinimalStrategy(Strategy):
            async def on_market_data(self, event: MarketDataEvent) -> None:
                pass

        strat = _MinimalStrategy("s1", context, [instrument])
        await strat.on_stop()

    @pytest.mark.asyncio
    async def test_strategy_lifecycle_on_fill_default_noop(
        self,
        context: StrategyContext,
        instrument: Instrument,
    ) -> None:
        """Default on_fill is a no-op (doesn't raise)."""

        class _MinimalStrategy(Strategy):
            async def on_market_data(self, event: MarketDataEvent) -> None:
                pass

        strat = _MinimalStrategy("s1", context, [instrument])
        fill = FillEvent(
            order_id="ord-1",
            instrument=instrument,
            side=Side.BUY,
            fill_price=Decimal("100"),
            fill_quantity=Decimal("5"),
            cumulative_quantity=Decimal("5"),
            order_status=OrderStatus.FILLED,
        )
        await strat.on_fill(fill)

    @pytest.mark.asyncio
    async def test_strategy_lifecycle_on_position_default_noop(
        self,
        context: StrategyContext,
        instrument: Instrument,
    ) -> None:
        """Default on_position is a no-op (doesn't raise)."""

        class _MinimalStrategy(Strategy):
            async def on_market_data(self, event: MarketDataEvent) -> None:
                pass

        strat = _MinimalStrategy("s1", context, [instrument])
        pos = PositionEvent(
            instrument=instrument,
            quantity=Decimal("10"),
            avg_price=Decimal("100"),
        )
        await strat.on_position(pos)


class TestStrategyAbstract:
    """Tests for abstract method enforcement."""

    def test_strategy_on_market_data_abstract(self) -> None:
        """Cannot instantiate Strategy without implementing on_market_data."""
        with pytest.raises(TypeError, match="on_market_data"):
            Strategy(  # type: ignore[abstract]
                strategy_id="s1",
                context=None,  # type: ignore[arg-type]
                instruments=[],
            )


class TestStrategyConvenienceMethods:
    """Tests for Strategy convenience methods."""

    @pytest.mark.asyncio
    async def test_strategy_emit_signal_convenience(
        self,
        bus: EventBus,
        clock: LiveClock,
        instrument: Instrument,
    ) -> None:
        """Strategy.emit_signal delegates to context.emit_signal."""
        ctx = StrategyContext(bus=bus, clock=clock)
        strat = _TestStrategy("my-strat", ctx, [instrument])

        received: list[SignalEvent] = []

        async def handler(event: SignalEvent) -> None:
            received.append(event)

        bus.subscribe(SignalEvent, handler)
        await bus.start()
        try:
            await strat.emit_signal(
                instrument=instrument,
                direction=SignalDirection.SHORT,
                strength=0.5,
                metadata={"indicator": "rsi"},
            )
            await asyncio.sleep(0.05)
        finally:
            await bus.stop()

        assert len(received) == 1
        event = received[0]
        assert event.strategy_id == "my-strat"
        assert event.direction == SignalDirection.SHORT
        assert event.strength == 0.5
        assert event.metadata == {"indicator": "rsi"}

    @pytest.mark.asyncio
    async def test_strategy_request_order_convenience(
        self,
        bus: EventBus,
        clock: LiveClock,
        instrument: Instrument,
    ) -> None:
        """Strategy.request_order delegates to context.request_order."""
        ctx = StrategyContext(bus=bus, clock=clock)
        strat = _TestStrategy("my-strat", ctx, [instrument])

        submitted: list[OrderSubmitted] = []

        async def handler(event: OrderSubmitted) -> None:
            submitted.append(event)

        bus.subscribe(OrderSubmitted, handler)
        await bus.start()
        try:
            request = await strat.request_order(
                instrument=instrument,
                side=Side.SELL,
                quantity=Decimal("5"),
                order_type=OrderType.MARKET,
            )
            await asyncio.sleep(0.05)
        finally:
            await bus.stop()

        assert request.instrument == instrument
        assert request.side == Side.SELL
        assert request.quantity == Decimal("5")
        assert len(submitted) == 1


class TestConcreteStrategy:
    """Tests for the concrete _TestStrategy receiving events."""

    @pytest.mark.asyncio
    async def test_concrete_strategy_on_market_data(
        self,
        context: StrategyContext,
        instrument: Instrument,
    ) -> None:
        """Concrete strategy receives and records market data events."""
        strat = _TestStrategy("s1", context, [instrument])
        bar = BarEvent(
            instrument=instrument,
            open=Decimal("100"),
            high=Decimal("105"),
            low=Decimal("99"),
            close=Decimal("103"),
            volume=Decimal("1000"),
            bar_start_ns=0,
            bar_end_ns=60_000_000_000,
        )
        await strat.on_market_data(bar)
        assert len(strat.market_data_events) == 1
        assert strat.market_data_events[0] is bar

    @pytest.mark.asyncio
    async def test_concrete_strategy_on_start_on_stop(
        self,
        context: StrategyContext,
        instrument: Instrument,
    ) -> None:
        """Concrete strategy records start/stop lifecycle calls."""
        strat = _TestStrategy("s1", context, [instrument])
        assert not strat.started
        assert not strat.stopped
        await strat.on_start()
        assert strat.started
        await strat.on_stop()
        assert strat.stopped

    @pytest.mark.asyncio
    async def test_concrete_strategy_on_fill(
        self,
        context: StrategyContext,
        instrument: Instrument,
    ) -> None:
        """Concrete strategy records fill events."""
        strat = _TestStrategy("s1", context, [instrument])
        fill = FillEvent(
            order_id="ord-1",
            instrument=instrument,
            side=Side.BUY,
            fill_price=Decimal("150"),
            fill_quantity=Decimal("10"),
            cumulative_quantity=Decimal("10"),
            order_status=OrderStatus.FILLED,
        )
        await strat.on_fill(fill)
        assert len(strat.fill_events) == 1
        assert strat.fill_events[0] is fill

    @pytest.mark.asyncio
    async def test_concrete_strategy_on_position(
        self,
        context: StrategyContext,
        instrument: Instrument,
    ) -> None:
        """Concrete strategy records position events."""
        strat = _TestStrategy("s1", context, [instrument])
        pos = PositionEvent(
            instrument=instrument,
            quantity=Decimal("20"),
            avg_price=Decimal("100"),
            realized_pnl=Decimal("50"),
        )
        await strat.on_position(pos)
        assert len(strat.position_events) == 1
        assert strat.position_events[0] is pos
