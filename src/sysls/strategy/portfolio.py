"""Portfolio construction: target weights to order instructions.

Converts target portfolio weights into concrete OrderRequests by
computing the difference between desired positions and current
holdings. Supports both long-only and long-short portfolios, and
optionally integrates with the RiskEngine for pre-trade validation.

Example usage::

    constructor = PortfolioConstructor(risk_engine=engine)
    orders = constructor.compute_rebalance_orders(
        targets=[TargetWeight(instrument=nvda, weight=0.10)],
        current_positions={nvda: Decimal("0")},
        portfolio_value=Decimal("100000"),
        prices={nvda: Decimal("150")},
    )
"""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal
from typing import TYPE_CHECKING

import structlog
from pydantic import BaseModel

from sysls.core.types import Instrument, OrderType, Side, generate_order_id

if TYPE_CHECKING:
    from sysls.core.types import OrderRequest
    from sysls.strategy.risk import RiskEngine


class TargetWeight(BaseModel, frozen=True):
    """Target portfolio weight for an instrument.

    Attributes:
        instrument: The target instrument.
        weight: Target weight as a fraction of portfolio (e.g. 0.10 = 10%).
            Positive = long, negative = short, 0 = flat.
    """

    instrument: Instrument
    weight: float


class PortfolioConstructor:
    """Converts target portfolio weights into order instructions.

    Given target weights, current positions, and portfolio value,
    computes the trades needed to rebalance. Supports both long-only
    and long-short portfolios.

    Args:
        risk_engine: Optional risk engine for pre-trade checks.
    """

    def __init__(self, risk_engine: RiskEngine | None = None) -> None:
        self._risk_engine = risk_engine
        self._logger = structlog.get_logger(__name__)

    def compute_rebalance_orders(
        self,
        targets: list[TargetWeight],
        current_positions: dict[Instrument, Decimal],
        portfolio_value: Decimal,
        prices: dict[Instrument, Decimal],
        order_type: OrderType = OrderType.MARKET,
    ) -> list[OrderRequest]:
        """Compute orders needed to rebalance to target weights.

        For each target:
        1. Compute target quantity = (weight * portfolio_value) / price
        2. Compute delta = target_quantity - current_position
        3. If delta != 0, create an OrderRequest

        Orders that close existing positions are generated before
        orders that open new ones (sells before buys for risk reduction).

        If a risk_engine is provided, each generated order is checked
        against risk limits. Orders that violate limits are excluded
        and a warning is logged.

        Args:
            targets: List of target weights.
            current_positions: Current position quantities by instrument.
            portfolio_value: Total portfolio value in base currency.
            prices: Current prices for each instrument.
            order_type: Order type for generated orders (default: MARKET).

        Returns:
            List of OrderRequests to execute the rebalance.
            Sells come before buys.
        """
        target_quantities = self.compute_target_quantities(targets, portfolio_value, prices)
        deltas = self.compute_deltas(target_quantities, current_positions)
        orders = self.deltas_to_orders(deltas, prices, order_type)

        if self._risk_engine is not None:
            checked_orders: list[OrderRequest] = []
            for order in orders:
                price = prices.get(order.instrument)
                violations = self._risk_engine.check_order(order, current_price=price)
                if violations:
                    self._logger.warning(
                        "order_excluded_by_risk",
                        order_id=order.order_id,
                        instrument=str(order.instrument),
                        violations=[v.rule_name for v in violations],
                    )
                else:
                    checked_orders.append(order)
            return checked_orders

        return orders

    def compute_target_quantities(
        self,
        targets: list[TargetWeight],
        portfolio_value: Decimal,
        prices: dict[Instrument, Decimal],
    ) -> dict[Instrument, Decimal]:
        """Compute target quantities from weights without generating orders.

        Useful for inspection/display before executing.

        Args:
            targets: Target weights.
            portfolio_value: Total portfolio value.
            prices: Current instrument prices.

        Returns:
            Mapping from instrument to target quantity.
        """
        result: dict[Instrument, Decimal] = {}

        for target in targets:
            price = prices.get(target.instrument)
            if price is None or price == Decimal("0"):
                self._logger.warning(
                    "target_skipped_no_price",
                    instrument=str(target.instrument),
                )
                continue

            weight_decimal = Decimal(str(target.weight))
            notional = weight_decimal * portfolio_value
            quantity = (notional / price).quantize(Decimal("1"), rounding=ROUND_DOWN)
            result[target.instrument] = quantity

        return result

    @staticmethod
    def compute_deltas(
        target_quantities: dict[Instrument, Decimal],
        current_positions: dict[Instrument, Decimal],
    ) -> dict[Instrument, Decimal]:
        """Compute position deltas (target - current) per instrument.

        Also includes instruments in current_positions but not in
        targets (delta = -current_position, i.e. close the position).

        Args:
            target_quantities: Target quantities per instrument.
            current_positions: Current quantities per instrument.

        Returns:
            Delta per instrument (positive = need to buy, negative = need to sell).
        """
        deltas: dict[Instrument, Decimal] = {}

        # Compute delta for all target instruments
        all_instruments = set(target_quantities.keys()) | set(current_positions.keys())
        for instrument in all_instruments:
            target = target_quantities.get(instrument, Decimal("0"))
            current = current_positions.get(instrument, Decimal("0"))
            delta = target - current
            if delta != Decimal("0"):
                deltas[instrument] = delta

        return deltas

    @staticmethod
    def deltas_to_orders(
        deltas: dict[Instrument, Decimal],
        prices: dict[Instrument, Decimal],
        order_type: OrderType = OrderType.MARKET,
    ) -> list[OrderRequest]:
        """Convert position deltas to OrderRequests.

        Skips zero deltas. Sells are ordered before buys.

        Args:
            deltas: Position delta per instrument.
            prices: Current prices (used for LIMIT orders).
            order_type: Type for generated orders.

        Returns:
            List of OrderRequests (sells first, then buys).
        """
        from sysls.core.types import OrderRequest

        sells: list[OrderRequest] = []
        buys: list[OrderRequest] = []

        for instrument, delta in deltas.items():
            if delta == Decimal("0"):
                continue

            side = Side.BUY if delta > Decimal("0") else Side.SELL
            quantity = abs(delta)
            price = prices.get(instrument) if order_type == OrderType.LIMIT else None

            order = OrderRequest(
                order_id=generate_order_id(),
                instrument=instrument,
                side=side,
                order_type=order_type,
                quantity=quantity,
                price=price,
            )

            if side == Side.SELL:
                sells.append(order)
            else:
                buys.append(order)

        return sells + buys
