"""Tests for the portfolio construction module."""

from __future__ import annotations

from decimal import Decimal

import pytest

from sysls.core.bus import EventBus
from sysls.core.types import (
    AssetClass,
    Instrument,
    OrderType,
    Side,
    Venue,
)
from sysls.strategy.portfolio import PortfolioConstructor, TargetWeight
from sysls.strategy.risk import (
    MaxPositionLimit,
    RiskEngine,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


@pytest.fixture
def tsla() -> Instrument:
    """TSLA equity instrument."""
    return Instrument(
        symbol="TSLA",
        asset_class=AssetClass.EQUITY,
        venue=Venue.PAPER,
        currency="USD",
    )


@pytest.fixture
def constructor() -> PortfolioConstructor:
    """Create a PortfolioConstructor without risk engine."""
    return PortfolioConstructor()


# ---------------------------------------------------------------------------
# compute_target_quantities
# ---------------------------------------------------------------------------


def test_compute_target_quantities_basic(
    constructor: PortfolioConstructor, nvda: Instrument
) -> None:
    """Basic target quantity computation: 10% of $100k at $150/share = 66 shares."""
    targets = [TargetWeight(instrument=nvda, weight=0.10)]
    prices = {nvda: Decimal("150")}
    portfolio_value = Decimal("100000")

    result = constructor.compute_target_quantities(targets, portfolio_value, prices)

    # 0.10 * 100000 / 150 = 66.666... -> truncated to 66
    assert result[nvda] == Decimal("66")


def test_compute_target_quantities_short(
    constructor: PortfolioConstructor, nvda: Instrument
) -> None:
    """Negative weight produces negative target quantity."""
    targets = [TargetWeight(instrument=nvda, weight=-0.10)]
    prices = {nvda: Decimal("150")}
    portfolio_value = Decimal("100000")

    result = constructor.compute_target_quantities(targets, portfolio_value, prices)

    # -0.10 * 100000 / 150 = -66.666... -> truncated toward zero = -66
    assert result[nvda] == Decimal("-66")


def test_compute_target_quantities_zero_weight(
    constructor: PortfolioConstructor, nvda: Instrument
) -> None:
    """Zero weight produces zero target quantity."""
    targets = [TargetWeight(instrument=nvda, weight=0.0)]
    prices = {nvda: Decimal("150")}
    portfolio_value = Decimal("100000")

    result = constructor.compute_target_quantities(targets, portfolio_value, prices)

    assert result[nvda] == Decimal("0")


def test_compute_target_quantities_no_price_skips(
    constructor: PortfolioConstructor, nvda: Instrument, aapl: Instrument
) -> None:
    """Instruments without a price are skipped."""
    targets = [
        TargetWeight(instrument=nvda, weight=0.10),
        TargetWeight(instrument=aapl, weight=0.10),
    ]
    # Only NVDA has a price
    prices = {nvda: Decimal("150")}
    portfolio_value = Decimal("100000")

    result = constructor.compute_target_quantities(targets, portfolio_value, prices)

    assert nvda in result
    assert aapl not in result


# ---------------------------------------------------------------------------
# compute_deltas
# ---------------------------------------------------------------------------


def test_compute_deltas_new_positions(nvda: Instrument, aapl: Instrument) -> None:
    """Deltas for new positions equal target quantities."""
    target_quantities = {nvda: Decimal("100"), aapl: Decimal("50")}
    current_positions: dict[Instrument, Decimal] = {}

    deltas = PortfolioConstructor.compute_deltas(target_quantities, current_positions)

    assert deltas[nvda] == Decimal("100")
    assert deltas[aapl] == Decimal("50")


def test_compute_deltas_close_positions(
    nvda: Instrument,
) -> None:
    """Deltas to close a position when target is zero."""
    target_quantities: dict[Instrument, Decimal] = {nvda: Decimal("0")}
    current_positions = {nvda: Decimal("100")}

    deltas = PortfolioConstructor.compute_deltas(target_quantities, current_positions)

    assert deltas[nvda] == Decimal("-100")


def test_compute_deltas_rebalance(
    nvda: Instrument,
) -> None:
    """Deltas for rebalancing an existing position."""
    target_quantities = {nvda: Decimal("150")}
    current_positions = {nvda: Decimal("100")}

    deltas = PortfolioConstructor.compute_deltas(target_quantities, current_positions)

    assert deltas[nvda] == Decimal("50")


def test_compute_deltas_includes_untracked_positions(nvda: Instrument, aapl: Instrument) -> None:
    """Positions not in targets get a close delta."""
    target_quantities = {nvda: Decimal("100")}
    current_positions = {nvda: Decimal("100"), aapl: Decimal("50")}

    deltas = PortfolioConstructor.compute_deltas(target_quantities, current_positions)

    # NVDA: 100 - 100 = 0, so not in deltas
    assert nvda not in deltas
    # AAPL: 0 - 50 = -50
    assert deltas[aapl] == Decimal("-50")


# ---------------------------------------------------------------------------
# deltas_to_orders
# ---------------------------------------------------------------------------


def test_deltas_to_orders_skips_zero(
    nvda: Instrument,
) -> None:
    """Zero deltas produce no orders."""
    deltas = {nvda: Decimal("0")}
    prices = {nvda: Decimal("150")}

    orders = PortfolioConstructor.deltas_to_orders(deltas, prices)

    assert orders == []


def test_deltas_to_orders_sells_before_buys(nvda: Instrument, aapl: Instrument) -> None:
    """Sell orders come before buy orders."""
    deltas = {nvda: Decimal("50"), aapl: Decimal("-30")}
    prices = {nvda: Decimal("150"), aapl: Decimal("200")}

    orders = PortfolioConstructor.deltas_to_orders(deltas, prices)

    assert len(orders) == 2
    # First order should be a sell (AAPL)
    assert orders[0].side == Side.SELL
    assert orders[0].instrument == aapl
    assert orders[0].quantity == Decimal("30")
    # Second order should be a buy (NVDA)
    assert orders[1].side == Side.BUY
    assert orders[1].instrument == nvda
    assert orders[1].quantity == Decimal("50")


def test_deltas_to_orders_with_limit_type(
    nvda: Instrument,
) -> None:
    """LIMIT orders include the current price."""
    deltas = {nvda: Decimal("10")}
    prices = {nvda: Decimal("150")}

    orders = PortfolioConstructor.deltas_to_orders(deltas, prices, order_type=OrderType.LIMIT)

    assert len(orders) == 1
    assert orders[0].order_type == OrderType.LIMIT
    assert orders[0].price == Decimal("150")


def test_deltas_to_orders_market_no_price(
    nvda: Instrument,
) -> None:
    """MARKET orders do not include a price."""
    deltas = {nvda: Decimal("10")}
    prices = {nvda: Decimal("150")}

    orders = PortfolioConstructor.deltas_to_orders(deltas, prices, order_type=OrderType.MARKET)

    assert len(orders) == 1
    assert orders[0].order_type == OrderType.MARKET
    assert orders[0].price is None


# ---------------------------------------------------------------------------
# compute_rebalance_orders (full flow)
# ---------------------------------------------------------------------------


def test_compute_rebalance_orders_full_flow(
    constructor: PortfolioConstructor, nvda: Instrument, aapl: Instrument
) -> None:
    """Full rebalance: buy NVDA, sell AAPL."""
    targets = [
        TargetWeight(instrument=nvda, weight=0.20),
        TargetWeight(instrument=aapl, weight=0.0),
    ]
    current_positions = {aapl: Decimal("50")}
    portfolio_value = Decimal("100000")
    prices = {nvda: Decimal("100"), aapl: Decimal("200")}

    orders = constructor.compute_rebalance_orders(
        targets, current_positions, portfolio_value, prices
    )

    # Should have a sell for AAPL and a buy for NVDA
    sells = [o for o in orders if o.side == Side.SELL]
    buys = [o for o in orders if o.side == Side.BUY]

    assert len(sells) == 1
    assert sells[0].instrument == aapl
    assert sells[0].quantity == Decimal("50")

    assert len(buys) == 1
    assert buys[0].instrument == nvda
    # 0.20 * 100000 / 100 = 200 shares
    assert buys[0].quantity == Decimal("200")

    # Sells come before buys
    sell_idx = orders.index(sells[0])
    buy_idx = orders.index(buys[0])
    assert sell_idx < buy_idx


def test_compute_rebalance_orders_with_risk_engine_blocks(
    nvda: Instrument,
) -> None:
    """Orders violating risk limits are excluded."""
    bus = EventBus()
    risk_engine = RiskEngine(
        bus=bus,
        limits=[
            MaxPositionLimit(
                name="max_pos",
                max_quantity=Decimal("10"),
            )
        ],
    )

    constructor = PortfolioConstructor(risk_engine=risk_engine)

    targets = [TargetWeight(instrument=nvda, weight=0.50)]
    current_positions: dict[Instrument, Decimal] = {}
    portfolio_value = Decimal("100000")
    prices = {nvda: Decimal("100")}

    orders = constructor.compute_rebalance_orders(
        targets, current_positions, portfolio_value, prices
    )

    # 0.50 * 100000 / 100 = 500 shares, but max position is 10
    # Order should be excluded by risk engine
    assert orders == []


def test_compute_rebalance_orders_flat_portfolio(
    constructor: PortfolioConstructor, nvda: Instrument, aapl: Instrument
) -> None:
    """All weights = 0 closes all existing positions."""
    targets = [
        TargetWeight(instrument=nvda, weight=0.0),
        TargetWeight(instrument=aapl, weight=0.0),
    ]
    current_positions = {nvda: Decimal("100"), aapl: Decimal("50")}
    portfolio_value = Decimal("100000")
    prices = {nvda: Decimal("150"), aapl: Decimal("200")}

    orders = constructor.compute_rebalance_orders(
        targets, current_positions, portfolio_value, prices
    )

    # Both should be sells
    assert len(orders) == 2
    assert all(o.side == Side.SELL for o in orders)

    quantities = {o.instrument: o.quantity for o in orders}
    assert quantities[nvda] == Decimal("100")
    assert quantities[aapl] == Decimal("50")


def test_compute_rebalance_orders_no_price_skips(
    constructor: PortfolioConstructor, nvda: Instrument, aapl: Instrument
) -> None:
    """Instruments without prices are skipped in target computation."""
    targets = [
        TargetWeight(instrument=nvda, weight=0.10),
        TargetWeight(instrument=aapl, weight=0.10),
    ]
    current_positions: dict[Instrument, Decimal] = {}
    portfolio_value = Decimal("100000")
    # Only NVDA has a price
    prices = {nvda: Decimal("150")}

    orders = constructor.compute_rebalance_orders(
        targets, current_positions, portfolio_value, prices
    )

    # Only NVDA order should be generated
    assert len(orders) == 1
    assert orders[0].instrument == nvda
