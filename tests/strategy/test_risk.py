"""Tests for the risk engine module."""

from __future__ import annotations

from decimal import Decimal

import pytest

from sysls.core.bus import EventBus
from sysls.core.events import PositionEvent, RiskSeverity
from sysls.core.types import (
    AssetClass,
    Instrument,
    OrderRequest,
    OrderType,
    Side,
    TimeInForce,
    Venue,
)
from sysls.strategy.risk import (
    MaxDrawdownLimit,
    MaxNotionalLimit,
    MaxOpenOrdersLimit,
    MaxOrderSizeLimit,
    MaxPositionLimit,
    RiskEngine,
    RiskLimit,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def bus() -> EventBus:
    """Create a fresh EventBus for each test."""
    return EventBus()


@pytest.fixture
def nvda() -> Instrument:
    """NVDA equity instrument."""
    return Instrument(
        symbol="NVDA",
        asset_class=AssetClass.EQUITY,
        venue=Venue.PAPER,
        currency="USD",
    )


@pytest.fixture
def aapl() -> Instrument:
    """AAPL equity instrument."""
    return Instrument(
        symbol="AAPL",
        asset_class=AssetClass.EQUITY,
        venue=Venue.PAPER,
        currency="USD",
    )


def _make_order(
    instrument: Instrument,
    side: Side = Side.BUY,
    quantity: Decimal = Decimal("10"),
    price: Decimal | None = None,
    order_type: OrderType = OrderType.MARKET,
) -> OrderRequest:
    """Helper to create an OrderRequest for testing."""
    return OrderRequest(
        instrument=instrument,
        side=side,
        order_type=order_type,
        quantity=quantity,
        price=price,
        time_in_force=TimeInForce.GTC,
    )


# ---------------------------------------------------------------------------
# Init and limit management
# ---------------------------------------------------------------------------


def test_risk_engine_init_empty_limits(bus: EventBus) -> None:
    """RiskEngine initializes with no limits by default."""
    engine = RiskEngine(bus=bus)
    assert engine.get_limits() == []


def test_risk_engine_add_remove_limit(bus: EventBus) -> None:
    """Limits can be added and removed by name."""
    engine = RiskEngine(bus=bus)
    limit = RiskLimit(name="test_limit")
    engine.add_limit(limit)
    assert len(engine.get_limits()) == 1
    assert engine.get_limits()[0].name == "test_limit"

    removed = engine.remove_limit("test_limit")
    assert removed is True
    assert engine.get_limits() == []


def test_risk_engine_remove_nonexistent_limit(bus: EventBus) -> None:
    """Removing a nonexistent limit returns False."""
    engine = RiskEngine(bus=bus)
    assert engine.remove_limit("nonexistent") is False


def test_risk_engine_get_limits(bus: EventBus) -> None:
    """get_limits returns a copy of the limits list."""
    limit1 = RiskLimit(name="limit_1")
    limit2 = RiskLimit(name="limit_2")
    engine = RiskEngine(bus=bus, limits=[limit1, limit2])

    limits = engine.get_limits()
    assert len(limits) == 2
    # Modifying the returned list should not affect the engine
    limits.clear()
    assert len(engine.get_limits()) == 2


# ---------------------------------------------------------------------------
# MaxPositionLimit
# ---------------------------------------------------------------------------


def test_max_position_limit_blocks_order(bus: EventBus, nvda: Instrument) -> None:
    """Order that would exceed max position is blocked."""
    engine = RiskEngine(
        bus=bus,
        limits=[
            MaxPositionLimit(
                name="max_pos_NVDA",
                instrument=nvda,
                max_quantity=Decimal("50"),
            )
        ],
    )

    order = _make_order(nvda, Side.BUY, Decimal("51"))
    violations = engine.check_order(order)
    assert len(violations) == 1
    assert violations[0].severity == RiskSeverity.BREACH
    assert violations[0].rule_name == "max_pos_NVDA"


def test_max_position_limit_allows_order(bus: EventBus, nvda: Instrument) -> None:
    """Order within max position is allowed."""
    engine = RiskEngine(
        bus=bus,
        limits=[
            MaxPositionLimit(
                name="max_pos_NVDA",
                instrument=nvda,
                max_quantity=Decimal("50"),
            )
        ],
    )

    order = _make_order(nvda, Side.BUY, Decimal("50"))
    violations = engine.check_order(order)
    assert violations == []


def test_max_position_limit_allows_reducing_order(bus: EventBus, nvda: Instrument) -> None:
    """Sell order that reduces position below limit is allowed."""
    engine = RiskEngine(
        bus=bus,
        limits=[
            MaxPositionLimit(
                name="max_pos_NVDA",
                instrument=nvda,
                max_quantity=Decimal("50"),
            )
        ],
    )
    # Simulate existing long position of 40
    engine._positions[nvda] = Decimal("40")

    # Selling 10 reduces position to 30 -- should pass
    order = _make_order(nvda, Side.SELL, Decimal("10"))
    violations = engine.check_order(order)
    assert violations == []


def test_max_position_limit_per_instrument_vs_global(
    bus: EventBus, nvda: Instrument, aapl: Instrument
) -> None:
    """Instrument-specific limit only applies to that instrument."""
    engine = RiskEngine(
        bus=bus,
        limits=[
            MaxPositionLimit(
                name="max_pos_NVDA",
                instrument=nvda,
                max_quantity=Decimal("10"),
            )
        ],
    )

    # AAPL order should not be checked against NVDA limit
    order = _make_order(aapl, Side.BUY, Decimal("100"))
    violations = engine.check_order(order)
    assert violations == []

    # Global limit (instrument=None) applies to all
    engine_global = RiskEngine(
        bus=bus,
        limits=[
            MaxPositionLimit(
                name="max_pos_global",
                max_quantity=Decimal("10"),
            )
        ],
    )
    violations = engine_global.check_order(order)
    assert len(violations) == 1


# ---------------------------------------------------------------------------
# MaxNotionalLimit
# ---------------------------------------------------------------------------


def test_max_notional_limit_blocks_order(bus: EventBus, nvda: Instrument) -> None:
    """Order with notional exceeding limit is blocked."""
    engine = RiskEngine(
        bus=bus,
        limits=[
            MaxNotionalLimit(
                name="max_notional",
                max_notional=Decimal("10000"),
            )
        ],
    )

    # 100 shares at $150 = $15,000 notional
    order = _make_order(nvda, Side.BUY, Decimal("100"), price=Decimal("150"))
    violations = engine.check_order(order)
    assert len(violations) == 1
    assert violations[0].rule_name == "max_notional"


def test_max_notional_limit_allows_order(bus: EventBus, nvda: Instrument) -> None:
    """Order with notional below limit is allowed."""
    engine = RiskEngine(
        bus=bus,
        limits=[
            MaxNotionalLimit(
                name="max_notional",
                max_notional=Decimal("20000"),
            )
        ],
    )

    # 100 shares at $150 = $15,000 notional
    order = _make_order(nvda, Side.BUY, Decimal("100"), price=Decimal("150"))
    violations = engine.check_order(order)
    assert violations == []


def test_max_notional_limit_skipped_without_price(bus: EventBus, nvda: Instrument) -> None:
    """Notional check is skipped when no price is available."""
    engine = RiskEngine(
        bus=bus,
        limits=[
            MaxNotionalLimit(
                name="max_notional",
                max_notional=Decimal("100"),
            )
        ],
    )

    # Market order with no price and no current_price -- skip check
    order = _make_order(nvda, Side.BUY, Decimal("1000"))
    violations = engine.check_order(order)
    assert violations == []


def test_max_notional_limit_uses_current_price(bus: EventBus, nvda: Instrument) -> None:
    """Notional check uses current_price when order has no price."""
    engine = RiskEngine(
        bus=bus,
        limits=[
            MaxNotionalLimit(
                name="max_notional",
                max_notional=Decimal("10000"),
            )
        ],
    )

    order = _make_order(nvda, Side.BUY, Decimal("100"))
    violations = engine.check_order(order, current_price=Decimal("150"))
    assert len(violations) == 1


# ---------------------------------------------------------------------------
# MaxOrderSizeLimit
# ---------------------------------------------------------------------------


def test_max_order_size_limit_blocks_order(bus: EventBus, nvda: Instrument) -> None:
    """Order exceeding max size is blocked."""
    engine = RiskEngine(
        bus=bus,
        limits=[
            MaxOrderSizeLimit(
                name="max_order_size",
                max_quantity=Decimal("50"),
            )
        ],
    )

    order = _make_order(nvda, Side.BUY, Decimal("51"))
    violations = engine.check_order(order)
    assert len(violations) == 1
    assert violations[0].rule_name == "max_order_size"


def test_max_order_size_limit_allows_order(bus: EventBus, nvda: Instrument) -> None:
    """Order within max size is allowed."""
    engine = RiskEngine(
        bus=bus,
        limits=[
            MaxOrderSizeLimit(
                name="max_order_size",
                max_quantity=Decimal("50"),
            )
        ],
    )

    order = _make_order(nvda, Side.BUY, Decimal("50"))
    violations = engine.check_order(order)
    assert violations == []


# ---------------------------------------------------------------------------
# MaxDrawdownLimit
# ---------------------------------------------------------------------------


def test_max_drawdown_limit_blocks_order(bus: EventBus, nvda: Instrument) -> None:
    """Order is blocked when drawdown exceeds limit."""
    engine = RiskEngine(
        bus=bus,
        limits=[
            MaxDrawdownLimit(
                name="max_drawdown",
                max_drawdown_pct=0.05,
            )
        ],
    )

    # Set peak and current to create a 10% drawdown
    engine.update_portfolio_value(Decimal("100000"))
    engine._current_value = Decimal("89000")  # 11% drawdown

    order = _make_order(nvda, Side.BUY, Decimal("1"))
    violations = engine.check_order(order)
    assert len(violations) == 1
    assert violations[0].rule_name == "max_drawdown"


def test_max_drawdown_limit_allows_order(bus: EventBus, nvda: Instrument) -> None:
    """Order is allowed when drawdown is within limit."""
    engine = RiskEngine(
        bus=bus,
        limits=[
            MaxDrawdownLimit(
                name="max_drawdown",
                max_drawdown_pct=0.10,
            )
        ],
    )

    # Set peak and current to create a 2% drawdown
    engine.update_portfolio_value(Decimal("100000"))
    engine._current_value = Decimal("98000")

    order = _make_order(nvda, Side.BUY, Decimal("1"))
    violations = engine.check_order(order)
    assert violations == []


# ---------------------------------------------------------------------------
# MaxOpenOrdersLimit
# ---------------------------------------------------------------------------


def test_max_open_orders_limit_blocks(bus: EventBus, nvda: Instrument) -> None:
    """Order is blocked when open order count is at limit."""
    engine = RiskEngine(
        bus=bus,
        limits=[
            MaxOpenOrdersLimit(
                name="max_open_orders",
                max_orders=5,
            )
        ],
    )
    engine._open_order_count = 5

    order = _make_order(nvda, Side.BUY, Decimal("1"))
    violations = engine.check_order(order)
    assert len(violations) == 1
    assert violations[0].rule_name == "max_open_orders"


def test_max_open_orders_limit_allows(bus: EventBus, nvda: Instrument) -> None:
    """Order is allowed when open order count is below limit."""
    engine = RiskEngine(
        bus=bus,
        limits=[
            MaxOpenOrdersLimit(
                name="max_open_orders",
                max_orders=5,
            )
        ],
    )
    engine._open_order_count = 4

    order = _make_order(nvda, Side.BUY, Decimal("1"))
    violations = engine.check_order(order)
    assert violations == []


# ---------------------------------------------------------------------------
# Multiple limits
# ---------------------------------------------------------------------------


def test_multiple_limits_all_checked(bus: EventBus, nvda: Instrument) -> None:
    """All enabled limits are checked; multiple violations returned."""
    engine = RiskEngine(
        bus=bus,
        limits=[
            MaxPositionLimit(
                name="max_pos",
                max_quantity=Decimal("10"),
            ),
            MaxOrderSizeLimit(
                name="max_order_size",
                max_quantity=Decimal("5"),
            ),
        ],
    )

    # Order of 20 violates both limits
    order = _make_order(nvda, Side.BUY, Decimal("20"))
    violations = engine.check_order(order)
    assert len(violations) == 2
    rule_names = {v.rule_name for v in violations}
    assert rule_names == {"max_pos", "max_order_size"}


def test_disabled_limit_skipped(bus: EventBus, nvda: Instrument) -> None:
    """Disabled limits are not checked."""
    engine = RiskEngine(
        bus=bus,
        limits=[
            MaxPositionLimit(
                name="max_pos",
                max_quantity=Decimal("10"),
                enabled=False,
            ),
        ],
    )

    order = _make_order(nvda, Side.BUY, Decimal("100"))
    violations = engine.check_order(order)
    assert violations == []


def test_check_order_returns_empty_when_passes(bus: EventBus, nvda: Instrument) -> None:
    """check_order returns an empty list when all limits pass."""
    engine = RiskEngine(
        bus=bus,
        limits=[
            MaxPositionLimit(
                name="max_pos",
                max_quantity=Decimal("100"),
            ),
            MaxOrderSizeLimit(
                name="max_size",
                max_quantity=Decimal("50"),
            ),
        ],
    )

    order = _make_order(nvda, Side.BUY, Decimal("10"))
    violations = engine.check_order(order)
    assert violations == []


# ---------------------------------------------------------------------------
# Position event handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_position_event_updates_internal_state(bus: EventBus, nvda: Instrument) -> None:
    """Position events update internal position tracking."""
    engine = RiskEngine(bus=bus)
    await engine.start()
    await bus.start()

    event = PositionEvent(
        instrument=nvda,
        quantity=Decimal("50"),
        avg_price=Decimal("150"),
        realized_pnl=Decimal("100"),
    )
    await bus.publish(event)

    # Give the dispatcher time to process
    import asyncio

    await asyncio.sleep(0.2)
    await bus.stop()

    assert engine._positions[nvda] == Decimal("50")
    assert engine._realized_pnl == Decimal("100")


# ---------------------------------------------------------------------------
# Portfolio value and drawdown tracking
# ---------------------------------------------------------------------------


def test_update_portfolio_value_tracks_peak(bus: EventBus) -> None:
    """update_portfolio_value tracks the peak value correctly."""
    engine = RiskEngine(bus=bus)

    engine.update_portfolio_value(Decimal("100000"))
    assert engine._peak_value == Decimal("100000")
    assert engine._current_value == Decimal("100000")

    engine.update_portfolio_value(Decimal("110000"))
    assert engine._peak_value == Decimal("110000")

    # Value drops -- peak should not change
    engine.update_portfolio_value(Decimal("105000"))
    assert engine._peak_value == Decimal("110000")
    assert engine._current_value == Decimal("105000")


def test_current_drawdown_pct(bus: EventBus) -> None:
    """current_drawdown_pct is calculated correctly."""
    engine = RiskEngine(bus=bus)

    # No peak -- drawdown is 0
    assert engine.current_drawdown_pct == 0.0

    engine.update_portfolio_value(Decimal("100000"))
    assert engine.current_drawdown_pct == 0.0

    engine._current_value = Decimal("95000")
    assert engine.current_drawdown_pct == pytest.approx(0.05)

    engine._current_value = Decimal("90000")
    assert engine.current_drawdown_pct == pytest.approx(0.10)


# ---------------------------------------------------------------------------
# Order submitted/completed tracking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_order_submitted_increments_count(bus: EventBus) -> None:
    """on_order_submitted increments the open order count."""
    engine = RiskEngine(bus=bus)
    assert engine._open_order_count == 0
    await engine.on_order_submitted()
    assert engine._open_order_count == 1
    await engine.on_order_submitted()
    assert engine._open_order_count == 2


@pytest.mark.asyncio
async def test_on_order_completed_decrements_count(bus: EventBus) -> None:
    """on_order_completed decrements the open order count (min 0)."""
    engine = RiskEngine(bus=bus)
    engine._open_order_count = 2
    await engine.on_order_completed()
    assert engine._open_order_count == 1
    await engine.on_order_completed()
    assert engine._open_order_count == 0
    # Should not go below 0
    await engine.on_order_completed()
    assert engine._open_order_count == 0
