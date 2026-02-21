"""ccxt-based crypto exchange venue adapter.

Wraps the ccxt unified API to provide connectivity to crypto exchanges
(Binance, Bybit, OKX, etc.) through the VenueAdapter interface. All ccxt
calls are synchronous and are offloaded to a thread via asyncio.to_thread()
to avoid blocking the event loop.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
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
    Venue,
)
from sysls.execution.venues.base import VenueAdapter

if TYPE_CHECKING:
    import ccxt as ccxt_lib

    from sysls.core.bus import EventBus
    from sysls.core.types import OrderRequest

# Mapping from ccxt unified order status strings to sysls OrderStatus.
_CCXT_STATUS_MAP: dict[str, OrderStatus] = {
    "open": OrderStatus.ACCEPTED,
    "closed": OrderStatus.FILLED,
    "canceled": OrderStatus.CANCELLED,
    "expired": OrderStatus.EXPIRED,
    "rejected": OrderStatus.REJECTED,
}


class CcxtVenueAdapter(VenueAdapter):
    """Venue adapter for crypto exchanges via the ccxt library.

    Provides a thin translation layer between sysls's normalized order types
    and the ccxt unified exchange API. Supports any ccxt-compatible exchange
    (Binance, Bybit, OKX, Coinbase, etc.).

    All ccxt calls are synchronous and are offloaded to a thread via
    ``asyncio.to_thread()`` to avoid blocking the event loop.

    Args:
        bus: EventBus for emitting order lifecycle events.
        exchange_id: ccxt exchange identifier (e.g. ``"binance"``, ``"bybit"``).
        api_key: API key for authenticated endpoints. None for public-only.
        api_secret: API secret for authenticated endpoints.
        sandbox: Whether to enable the exchange's sandbox/testnet mode.
        extra_config: Additional ccxt configuration dict merged into the
            exchange constructor config.
    """

    def __init__(
        self,
        bus: EventBus,
        exchange_id: str,
        api_key: str | None = None,
        api_secret: str | None = None,
        sandbox: bool = False,
        extra_config: dict[str, Any] | None = None,
    ) -> None:
        self._bus = bus
        self._exchange_id = exchange_id
        self._api_key = api_key
        self._api_secret = api_secret
        self._sandbox = sandbox
        self._extra_config = extra_config or {}
        self._exchange: ccxt_lib.Exchange | None = None
        self._connected = False
        self._logger = structlog.get_logger(__name__)

    # -- Lifecycle ---------------------------------------------------------

    async def connect(self) -> None:
        """Create the ccxt exchange instance and load markets.

        Raises:
            SyslsConnectionError: If ccxt is not installed or market loading fails.
        """
        try:
            import ccxt
        except ImportError as exc:
            raise SyslsConnectionError(
                "ccxt is not installed. Install it with: pip install 'sysls[ccxt]'",
                venue=f"ccxt-{self._exchange_id}",
            ) from exc

        exchange_class = getattr(ccxt, self._exchange_id, None)
        if exchange_class is None:
            raise SyslsConnectionError(
                f"Unknown ccxt exchange: {self._exchange_id}",
                venue=f"ccxt-{self._exchange_id}",
            )

        config: dict[str, Any] = {**self._extra_config}
        if self._api_key is not None:
            config["apiKey"] = self._api_key
        if self._api_secret is not None:
            config["secret"] = self._api_secret

        self._exchange = exchange_class(config)

        if self._sandbox:
            self._exchange.set_sandbox_mode(True)

        try:
            await asyncio.to_thread(self._exchange.load_markets)
        except ccxt.BaseError as exc:
            self._exchange = None
            raise SyslsConnectionError(
                f"Failed to load markets: {exc}",
                venue=f"ccxt-{self._exchange_id}",
            ) from exc

        self._connected = True
        self._logger.info(
            "ccxt_venue_connected",
            exchange=self._exchange_id,
            sandbox=self._sandbox,
            num_markets=len(self._exchange.markets) if self._exchange.markets else 0,
        )

    async def disconnect(self) -> None:
        """Release the ccxt exchange instance."""
        self._connected = False
        self._exchange = None
        self._logger.info("ccxt_venue_disconnected", exchange=self._exchange_id)

    # -- Properties --------------------------------------------------------

    @property
    def name(self) -> str:
        """Human-readable venue name."""
        return f"ccxt-{self._exchange_id}"

    @property
    def is_connected(self) -> bool:
        """Whether the adapter has an active connection."""
        return self._connected

    @property
    def supported_order_types(self) -> list[OrderType]:
        """Order types commonly supported by crypto exchanges."""
        return [OrderType.MARKET, OrderType.LIMIT, OrderType.STOP_LIMIT]

    # -- Order operations --------------------------------------------------

    async def submit_order(self, order: OrderRequest) -> str:
        """Submit an order to the exchange via ccxt.

        Args:
            order: Normalized order request.

        Returns:
            Venue-assigned order ID.

        Raises:
            OrderError: If the order cannot be submitted.
            VenueError: If there is a connectivity issue.
        """
        exchange = self._require_exchange()
        symbol = self._to_ccxt_symbol(order.instrument)
        side = order.side.value.lower()
        order_type = order.order_type.value.lower()
        amount = float(order.quantity)
        price = float(order.price) if order.price is not None else None

        self._logger.info(
            "ccxt_order_submitting",
            symbol=symbol,
            side=side,
            type=order_type,
            amount=amount,
            price=price,
        )

        try:
            result = await asyncio.to_thread(
                exchange.create_order,
                symbol,
                order_type,
                side,
                amount,
                price,
            )
        except Exception as exc:
            self._wrap_ccxt_error(exc, context=f"submit_order for {symbol}")

        venue_order_id = str(result.get("id", ""))
        if not venue_order_id:
            raise OrderError(
                "Exchange returned no order ID",
                venue=self.name,
            )

        self._logger.info(
            "ccxt_order_submitted",
            order_id=order.order_id,
            venue_order_id=venue_order_id,
            symbol=symbol,
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
        """Cancel an order on the exchange.

        Args:
            venue_order_id: The exchange's order identifier.
            instrument: The instrument (needed by ccxt for cancel).

        Raises:
            OrderError: If the order cannot be cancelled.
        """
        exchange = self._require_exchange()
        symbol = self._to_ccxt_symbol(instrument)

        try:
            await asyncio.to_thread(
                exchange.cancel_order,
                venue_order_id,
                symbol,
            )
        except Exception as exc:
            self._wrap_ccxt_error(exc, context=f"cancel_order {venue_order_id}")

        self._logger.info(
            "ccxt_order_cancelled",
            venue_order_id=venue_order_id,
            symbol=symbol,
        )

        await self._bus.publish(
            OrderCancelled(
                order_id=venue_order_id,
                instrument=instrument,
                reason="Cancelled via ccxt",
                source=self.name,
            )
        )

    async def get_order_status(self, venue_order_id: str, instrument: Instrument) -> OrderStatus:
        """Query current status of an order on the exchange.

        Args:
            venue_order_id: The exchange's order identifier.
            instrument: The instrument.

        Returns:
            Current order status mapped to sysls OrderStatus.

        Raises:
            OrderError: If the order status cannot be fetched.
        """
        exchange = self._require_exchange()
        symbol = self._to_ccxt_symbol(instrument)

        try:
            result = await asyncio.to_thread(
                exchange.fetch_order,
                venue_order_id,
                symbol,
            )
        except Exception as exc:
            self._wrap_ccxt_error(exc, context=f"get_order_status {venue_order_id}")

        ccxt_status = result.get("status", "")
        return _map_order_status(ccxt_status)

    # -- Position / balance queries ----------------------------------------

    async def get_positions(self) -> dict[Instrument, Decimal]:
        """Get all current positions from the exchange.

        Attempts to use ``fetch_positions`` for derivatives exchanges.
        Falls back to deriving positions from balance for spot-only exchanges.

        Returns:
            Mapping from instrument to net quantity.

        Raises:
            VenueError: If positions cannot be fetched.
        """
        exchange = self._require_exchange()

        try:
            raw_positions = await asyncio.to_thread(exchange.fetch_positions)
        except AttributeError:
            # Spot-only exchange without fetch_positions.
            return {}
        except Exception as exc:
            self._wrap_ccxt_error(exc, context="get_positions")

        positions: dict[Instrument, Decimal] = {}
        for pos in raw_positions:
            contracts = pos.get("contracts", 0) or 0
            side = pos.get("side", "")
            symbol = pos.get("symbol", "")

            if contracts == 0 and not symbol:
                continue

            quantity = Decimal(str(contracts))
            if side == "short":
                quantity = -quantity

            if quantity == Decimal("0"):
                continue

            market = exchange.markets.get(symbol, {}) if exchange.markets else {}
            instrument = _build_instrument(
                symbol=symbol,
                market=market,
                exchange_id=self._exchange_id,
            )
            positions[instrument] = quantity

        return positions

    async def get_balances(self) -> dict[str, Decimal]:
        """Get account balances from the exchange.

        Returns:
            Mapping from currency code to available (free) balance.

        Raises:
            VenueError: If balances cannot be fetched.
        """
        exchange = self._require_exchange()

        try:
            balance = await asyncio.to_thread(exchange.fetch_balance)
        except Exception as exc:
            self._wrap_ccxt_error(exc, context="get_balances")

        free: dict[str, Any] = balance.get("free", {})
        return {
            currency: Decimal(str(amount))
            for currency, amount in free.items()
            if amount is not None and float(amount) != 0.0
        }

    # -- Private helpers ---------------------------------------------------

    def _require_exchange(self) -> ccxt_lib.Exchange:
        """Return the exchange instance or raise if not connected.

        Returns:
            The ccxt exchange instance.

        Raises:
            VenueError: If not connected.
        """
        if self._exchange is None or not self._connected:
            raise VenueError(
                "Not connected. Call connect() first.",
                venue=self.name,
            )
        return self._exchange

    @staticmethod
    def _to_ccxt_symbol(instrument: Instrument) -> str:
        """Convert an Instrument to a ccxt-compatible symbol string.

        Handles spot (``BTC/USDT``), perpetuals (``BTC/USDT:USDT``),
        and futures based on the instrument's asset class.

        Args:
            instrument: The sysls instrument.

        Returns:
            ccxt-formatted symbol string.
        """
        symbol = instrument.symbol

        # If the symbol already contains '/', assume it's in ccxt format.
        if "/" in symbol:
            return symbol

        # Try to parse common patterns like "BTC-USDT", "BTCUSDT", etc.
        if "-" in symbol:
            parts = symbol.split("-")
            base = parts[0]
            quote = parts[1] if len(parts) > 1 else "USDT"
        else:
            # Assume the symbol is already a base currency.
            base = symbol
            quote = instrument.currency if instrument.currency != "USD" else "USDT"

        ccxt_symbol = f"{base}/{quote}"

        # Append settle currency for derivatives.
        if instrument.asset_class in (
            AssetClass.CRYPTO_PERP,
            AssetClass.CRYPTO_FUTURE,
        ):
            ccxt_symbol = f"{ccxt_symbol}:{quote}"

        return ccxt_symbol

    def _wrap_ccxt_error(self, exc: Exception, context: str) -> None:
        """Wrap a ccxt exception in a sysls exception and re-raise.

        Maps ccxt error types to appropriate sysls exception types:
        - ``InvalidOrder``, ``OrderNotFound`` -> ``OrderError``
        - ``NetworkError`` -> ``SyslsConnectionError``
        - Other ``BaseError`` -> ``VenueError``
        - Non-ccxt exceptions -> ``VenueError``

        Args:
            exc: The exception to wrap.
            context: Description of the operation that failed.

        Raises:
            OrderError: For order-related ccxt errors.
            SyslsConnectionError: For network-related ccxt errors.
            VenueError: For other ccxt errors.
        """
        import ccxt

        self._logger.error(
            "ccxt_error",
            exchange=self._exchange_id,
            context=context,
            error_type=type(exc).__name__,
            error=str(exc),
        )

        if isinstance(exc, (ccxt.InvalidOrder, ccxt.OrderNotFound)):
            raise OrderError(
                f"{context}: {exc}",
                venue=self.name,
            ) from exc
        if isinstance(exc, ccxt.NetworkError):
            raise SyslsConnectionError(
                f"{context}: {exc}",
                venue=self.name,
            ) from exc
        if isinstance(exc, ccxt.BaseError):
            raise VenueError(
                f"{context}: {exc}",
                venue=self.name,
            ) from exc

        raise VenueError(
            f"{context}: {exc}",
            venue=self.name,
        ) from exc


def _map_order_status(ccxt_status: str) -> OrderStatus:
    """Map a ccxt unified order status string to sysls OrderStatus.

    Args:
        ccxt_status: Status string from ccxt (e.g. ``"open"``, ``"closed"``).

    Returns:
        Corresponding sysls OrderStatus.
    """
    return _CCXT_STATUS_MAP.get(ccxt_status, OrderStatus.PENDING)


def _build_instrument(
    symbol: str,
    market: dict[str, Any],
    exchange_id: str,
) -> Instrument:
    """Build an Instrument from a ccxt market dict.

    Args:
        symbol: The ccxt symbol string.
        market: The ccxt market dict for this symbol.
        exchange_id: The ccxt exchange identifier.

    Returns:
        A sysls Instrument.
    """
    market_type = market.get("type", "spot")
    if market_type == "swap":
        asset_class = AssetClass.CRYPTO_PERP
    elif market_type == "future":
        asset_class = AssetClass.CRYPTO_FUTURE
    elif market_type == "option":
        asset_class = AssetClass.OPTION
    else:
        asset_class = AssetClass.CRYPTO_SPOT

    quote = market.get("quote", "USDT")

    tick_size = None
    lot_size = None
    precision = market.get("precision", {})
    if precision:
        price_precision = precision.get("price")
        if price_precision is not None:
            tick_size = Decimal(str(price_precision))
        amount_precision = precision.get("amount")
        if amount_precision is not None:
            lot_size = Decimal(str(amount_precision))

    return Instrument(
        symbol=symbol,
        asset_class=asset_class,
        venue=Venue.CCXT,
        exchange=exchange_id,
        currency=quote,
        tick_size=tick_size,
        lot_size=lot_size,
    )
