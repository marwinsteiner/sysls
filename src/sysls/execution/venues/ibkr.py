"""Interactive Brokers venue adapter via ib_async.

Wraps the ib_async library (successor to ib_insync) to provide connectivity
to Interactive Brokers through the VenueAdapter interface. Most ib_async calls
are synchronous and are offloaded to a thread via asyncio.to_thread() to avoid
blocking the event loop.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog

from sysls.core.events import OrderAccepted, OrderCancelled
from sysls.core.exceptions import ConnectionError as SyslsConnectionError
from sysls.core.exceptions import OrderError, VenueError
from sysls.core.types import (
    AssetClass,
    Instrument,
    OrderStatus,
    OrderType,
    Side,
    Venue,
)
from sysls.execution.venues.base import VenueAdapter

if TYPE_CHECKING:
    from decimal import Decimal

    from ib_async import IB

    from sysls.core.bus import EventBus
    from sysls.core.types import OrderRequest

# Mapping from IB order status strings to sysls OrderStatus.
_IB_STATUS_MAP: dict[str, OrderStatus] = {
    "PendingSubmit": OrderStatus.SUBMITTED,
    "PendingCancel": OrderStatus.ACCEPTED,
    "PreSubmitted": OrderStatus.ACCEPTED,
    "Submitted": OrderStatus.ACCEPTED,
    "Filled": OrderStatus.FILLED,
    "Cancelled": OrderStatus.CANCELLED,
    "Inactive": OrderStatus.REJECTED,
    "ApiPending": OrderStatus.PENDING,
    "ApiCancelled": OrderStatus.CANCELLED,
}

logger = structlog.get_logger(__name__)


class IbkrAdapter(VenueAdapter):
    """Venue adapter for Interactive Brokers via the ib_async library.

    Provides a thin translation layer between sysls's normalized order types
    and the Interactive Brokers TWS/Gateway API. Supports equities, options,
    futures, and forex.

    All ib_async calls that interact with TWS/Gateway are offloaded to a
    thread via ``asyncio.to_thread()`` to avoid blocking the event loop.

    Args:
        bus: EventBus for emitting order lifecycle events.
        host: TWS/Gateway host address.
        port: TWS/Gateway port (7497 for paper, 7496 for live).
        client_id: Unique client ID for this connection.
        account: Optional account identifier for multi-account setups.
    """

    def __init__(
        self,
        bus: EventBus,
        host: str = "127.0.0.1",
        port: int = 7497,
        client_id: int = 1,
        account: str | None = None,
    ) -> None:
        self._bus = bus
        self._host = host
        self._port = port
        self._client_id = client_id
        self._account = account
        self._ib: IB | None = None
        self._logger = logger.bind(venue="ibkr")

    # -- Lifecycle ---------------------------------------------------------

    async def connect(self) -> None:
        """Connect to TWS/Gateway via ib_async.

        Raises:
            SyslsConnectionError: If ib_async is not installed or
                connection to TWS/Gateway fails.
        """
        try:
            from ib_async import IB
        except ImportError as exc:
            raise SyslsConnectionError(
                "ib_async is not installed. Install it with: pip install 'sysls[ibkr]'",
                venue=self.name,
            ) from exc

        ib = IB()
        try:
            await ib.connectAsync(
                self._host,
                self._port,
                clientId=self._client_id,
                account=self._account or "",
            )
        except Exception as exc:
            raise SyslsConnectionError(
                f"Failed to connect to TWS/Gateway at {self._host}:{self._port}: {exc}",
                venue=self.name,
            ) from exc

        self._ib = ib
        self._logger.info(
            "ibkr_connected",
            host=self._host,
            port=self._port,
            client_id=self._client_id,
        )

    async def disconnect(self) -> None:
        """Disconnect from TWS/Gateway.

        Safe to call multiple times.
        """
        if self._ib is not None:
            self._ib.disconnect()
            self._ib = None
            self._logger.info("ibkr_disconnected")

    # -- Properties --------------------------------------------------------

    @property
    def name(self) -> str:
        """Human-readable venue name."""
        return "ibkr"

    @property
    def is_connected(self) -> bool:
        """Whether the adapter has an active connection to TWS/Gateway."""
        if self._ib is None:
            return False
        return bool(self._ib.isConnected())

    @property
    def supported_order_types(self) -> list[OrderType]:
        """Order types supported by Interactive Brokers."""
        return [OrderType.MARKET, OrderType.LIMIT, OrderType.STOP, OrderType.STOP_LIMIT]

    # -- Order operations --------------------------------------------------

    async def submit_order(self, order: OrderRequest) -> str:
        """Submit an order to Interactive Brokers.

        Args:
            order: Normalized order request.

        Returns:
            Venue-assigned order ID.

        Raises:
            OrderError: If the order cannot be submitted.
            VenueError: If there is a connectivity issue.
        """
        ib = self._require_ib()
        contract = _to_ib_contract(order.instrument)
        ib_order = _to_ib_order(order)

        self._logger.info(
            "ibkr_order_submitting",
            symbol=order.instrument.symbol,
            side=order.side.value,
            order_type=order.order_type.value,
            quantity=str(order.quantity),
        )

        try:
            trade = await asyncio.to_thread(ib.placeOrder, contract, ib_order)
        except Exception as exc:
            self._wrap_ib_error(exc, context=f"submit_order for {order.instrument.symbol}")

        venue_order_id = str(trade.order.orderId)

        self._logger.info(
            "ibkr_order_submitted",
            order_id=order.order_id,
            venue_order_id=venue_order_id,
        )

        await self._bus.publish(
            OrderAccepted(
                order_id=order.order_id,
                instrument=order.instrument,
                venue_order_id=venue_order_id,
                source=self.name,
            )
        )

        return venue_order_id

    async def cancel_order(self, venue_order_id: str, instrument: Instrument) -> None:
        """Cancel an order on Interactive Brokers.

        Args:
            venue_order_id: The venue's order identifier.
            instrument: The instrument (needed for the cancel lookup).

        Raises:
            OrderError: If the order cannot be cancelled.
        """
        ib = self._require_ib()
        order_id = int(venue_order_id)

        # Find the trade by order ID
        trade = None
        for t in ib.openTrades():
            if t.order.orderId == order_id:
                trade = t
                break

        if trade is None:
            raise OrderError(
                f"Order {venue_order_id} not found in open trades",
                venue=self.name,
            )

        try:
            await asyncio.to_thread(ib.cancelOrder, trade.order)
        except Exception as exc:
            self._wrap_ib_error(exc, context=f"cancel_order {venue_order_id}")

        self._logger.info(
            "ibkr_order_cancelled",
            venue_order_id=venue_order_id,
        )

        await self._bus.publish(
            OrderCancelled(
                order_id=venue_order_id,
                instrument=instrument,
                reason="Cancelled via IBKR",
                source=self.name,
            )
        )

    async def get_order_status(self, venue_order_id: str, instrument: Instrument) -> OrderStatus:
        """Query current status of an order at Interactive Brokers.

        Args:
            venue_order_id: The venue's order identifier.
            instrument: The instrument.

        Returns:
            Current order status.
        """
        ib = self._require_ib()
        order_id = int(venue_order_id)

        for trade in ib.trades():
            if trade.order.orderId == order_id:
                return _map_ib_status(trade.orderStatus.status)

        return OrderStatus.PENDING

    # -- Position / balance queries ----------------------------------------

    async def get_positions(self) -> dict[Instrument, Decimal]:
        """Get all current positions from Interactive Brokers.

        Returns:
            Mapping from instrument to net quantity
            (positive=long, negative=short).

        Raises:
            VenueError: If positions cannot be fetched.
        """
        from decimal import Decimal as Dec

        ib = self._require_ib()

        try:
            raw_positions = await asyncio.to_thread(ib.positions)
        except Exception as exc:
            self._wrap_ib_error(exc, context="get_positions")

        positions: dict[Instrument, Dec] = {}
        for pos in raw_positions:
            quantity = Dec(str(pos.position))
            if quantity == Dec("0"):
                continue

            instrument = _build_instrument_from_contract(pos.contract)
            positions[instrument] = quantity

        return positions

    async def get_balances(self) -> dict[str, Decimal]:
        """Get account balances from Interactive Brokers.

        Returns:
            Mapping from currency code to available balance.

        Raises:
            VenueError: If balances cannot be fetched.
        """
        from decimal import Decimal as Dec

        ib = self._require_ib()

        try:
            account_values = await asyncio.to_thread(ib.accountValues)
        except Exception as exc:
            self._wrap_ib_error(exc, context="get_balances")

        balances: dict[str, Dec] = {}
        for av in account_values:
            if av.tag == "CashBalance" and av.currency and av.currency != "BASE":
                try:
                    amount = Dec(av.value)
                except Exception:
                    continue
                if amount != Dec("0"):
                    balances[av.currency] = amount

        return balances

    # -- Private helpers ---------------------------------------------------

    def _require_ib(self) -> IB:
        """Return the IB instance or raise if not connected.

        Returns:
            The ib_async IB instance.

        Raises:
            VenueError: If not connected.
        """
        if self._ib is None or not self._ib.isConnected():
            raise VenueError(
                "Not connected. Call connect() first.",
                venue=self.name,
            )
        return self._ib

    def _wrap_ib_error(self, exc: Exception, context: str) -> None:
        """Wrap an IB exception in a sysls exception and re-raise.

        Args:
            exc: The exception to wrap.
            context: Description of the operation that failed.

        Raises:
            OrderError: For order-related errors.
            SyslsConnectionError: For connection-related errors.
            VenueError: For other errors.
        """
        self._logger.error(
            "ibkr_error",
            context=context,
            error_type=type(exc).__name__,
            error=str(exc),
        )

        if isinstance(exc, (ConnectionError, OSError, TimeoutError)):
            raise SyslsConnectionError(
                f"{context}: {exc}",
                venue=self.name,
            ) from exc

        if isinstance(exc, ValueError):
            raise OrderError(
                f"{context}: {exc}",
                venue=self.name,
            ) from exc

        raise VenueError(
            f"{context}: {exc}",
            venue=self.name,
        ) from exc


def _to_ib_contract(instrument: Instrument) -> Any:
    """Map a sysls Instrument to an ib_async Contract.

    Args:
        instrument: The sysls instrument.

    Returns:
        An ib_async Contract subclass (Stock, Option, Future, Forex).

    Raises:
        OrderError: If the asset class is not supported.
    """
    from ib_async import Forex, Future, Option, Stock

    exchange = instrument.exchange or "SMART"
    currency = instrument.currency

    if instrument.asset_class == AssetClass.EQUITY:
        return Stock(instrument.symbol, exchange, currency)

    if instrument.asset_class == AssetClass.OPTION:
        # Symbol format expected: "AAPL 20240315 150 C" or similar
        # Parse option details from symbol metadata
        parts = instrument.symbol.split()
        if len(parts) >= 4:
            underlying = parts[0]
            expiry = parts[1]
            strike = float(parts[2])
            right = parts[3]  # "C" or "P"
            return Option(underlying, expiry, strike, right, exchange, currency)
        # Fallback: treat as plain symbol
        return Option(instrument.symbol, exchange=exchange, currency=currency)

    if instrument.asset_class == AssetClass.FUTURE:
        return Future(instrument.symbol, exchange=exchange, currency=currency)

    if instrument.asset_class == AssetClass.CRYPTO_SPOT:
        return Forex(
            symbol=instrument.symbol,
            currency=currency,
            exchange=exchange if exchange != "SMART" else "IDEALPRO",
        )

    raise OrderError(
        f"Unsupported asset class for IBKR: {instrument.asset_class}",
        venue="ibkr",
    )


def _to_ib_order(request: OrderRequest) -> Any:
    """Map a sysls OrderRequest to an ib_async Order.

    Args:
        request: The sysls order request.

    Returns:
        An ib_async Order subclass (MarketOrder, LimitOrder, etc.).

    Raises:
        OrderError: If the order type is not supported.
    """
    from ib_async import LimitOrder, MarketOrder, StopLimitOrder, StopOrder

    action = "BUY" if request.side == Side.BUY else "SELL"
    qty = float(request.quantity)

    if request.order_type == OrderType.MARKET:
        return MarketOrder(action, qty)

    if request.order_type == OrderType.LIMIT:
        if request.price is None:
            raise OrderError(
                "Limit order requires a price",
                venue="ibkr",
            )
        return LimitOrder(action, qty, float(request.price))

    if request.order_type == OrderType.STOP:
        if request.stop_price is None:
            raise OrderError(
                "Stop order requires a stop_price",
                venue="ibkr",
            )
        return StopOrder(action, qty, float(request.stop_price))

    if request.order_type == OrderType.STOP_LIMIT:
        if request.price is None or request.stop_price is None:
            raise OrderError(
                "Stop-limit order requires both price and stop_price",
                venue="ibkr",
            )
        return StopLimitOrder(action, qty, float(request.price), float(request.stop_price))

    raise OrderError(
        f"Unsupported order type for IBKR: {request.order_type}",
        venue="ibkr",
    )


def _map_ib_status(ib_status: str) -> OrderStatus:
    """Map an IB order status string to sysls OrderStatus.

    Args:
        ib_status: Status string from ib_async (e.g. ``"Submitted"``,
            ``"Filled"``).

    Returns:
        Corresponding sysls OrderStatus.
    """
    return _IB_STATUS_MAP.get(ib_status, OrderStatus.PENDING)


# Mapping from IB secType to sysls AssetClass.
_SEC_TYPE_MAP: dict[str, AssetClass] = {
    "STK": AssetClass.EQUITY,
    "OPT": AssetClass.OPTION,
    "FUT": AssetClass.FUTURE,
    "CASH": AssetClass.CRYPTO_SPOT,
}


def _build_instrument_from_contract(contract: Any) -> Instrument:
    """Build a sysls Instrument from an ib_async Contract.

    Args:
        contract: An ib_async Contract (from a Position object).

    Returns:
        A sysls Instrument.
    """
    from decimal import Decimal as Dec

    sec_type = getattr(contract, "secType", "STK")
    asset_class = _SEC_TYPE_MAP.get(sec_type, AssetClass.EQUITY)

    symbol = getattr(contract, "symbol", "")
    exchange = getattr(contract, "exchange", None) or "SMART"
    currency = getattr(contract, "currency", "USD")

    multiplier_str = getattr(contract, "multiplier", "")
    multiplier = Dec(multiplier_str) if multiplier_str else Dec("1")

    return Instrument(
        symbol=symbol,
        asset_class=asset_class,
        venue=Venue.IBKR,
        exchange=exchange,
        currency=currency,
        multiplier=multiplier,
    )
