"""Tastytrade venue adapter via the tastytrade SDK.

Wraps the tastyware/tastytrade unofficial SDK to provide connectivity to
tastytrade through the VenueAdapter interface. The SDK is fully async,
so no asyncio.to_thread() wrapping is needed.
"""

from __future__ import annotations

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

    from sysls.core.bus import EventBus
    from sysls.core.types import OrderRequest

# Mapping from tastytrade OrderStatus strings to sysls OrderStatus.
_TT_STATUS_MAP: dict[str, OrderStatus] = {
    "Received": OrderStatus.SUBMITTED,
    "Routed": OrderStatus.ACCEPTED,
    "In Flight": OrderStatus.ACCEPTED,
    "Live": OrderStatus.ACCEPTED,
    "Filled": OrderStatus.FILLED,
    "Cancelled": OrderStatus.CANCELLED,
    "Cancel Requested": OrderStatus.ACCEPTED,
    "Rejected": OrderStatus.REJECTED,
    "Expired": OrderStatus.EXPIRED,
    "Contingent": OrderStatus.PENDING,
    "Replace Requested": OrderStatus.ACCEPTED,
    "Removed": OrderStatus.CANCELLED,
    "Partially Removed": OrderStatus.CANCELLED,
}

logger = structlog.get_logger(__name__)


class TastytradeAdapter(VenueAdapter):
    """Venue adapter for tastytrade via the tastyware SDK.

    Provides a thin translation layer between sysls's normalized order types
    and the tastytrade API. Supports equities, options, and futures.

    The tastytrade SDK is fully async, so no thread offloading is needed.

    Args:
        bus: EventBus for emitting order lifecycle events.
        login: Tastytrade login email or username.
        password: Tastytrade account password.
        is_test: Whether to use the certification/sandbox environment.
        account_number: Optional account number. If not provided, the
            first account returned by the API will be used.
    """

    def __init__(
        self,
        bus: EventBus,
        login: str,
        password: str,
        *,
        is_test: bool = False,
        account_number: str | None = None,
    ) -> None:
        self._bus = bus
        self._login = login
        self._password = password
        self._is_test = is_test
        self._account_number = account_number
        self._session: Any | None = None
        self._account: Any | None = None
        self._logger = logger.bind(venue="tastytrade")

    # -- Lifecycle ---------------------------------------------------------

    async def connect(self) -> None:
        """Connect to tastytrade API.

        Creates a session using the tastytrade SDK and retrieves the
        specified account (or first available). Uses ProductionSession
        for live trading or CertificationSession for sandbox/test.

        Raises:
            SyslsConnectionError: If the tastytrade SDK is not installed,
                authentication fails, or no accounts are found.
        """
        try:
            from tastytrade import Account, CertificationSession, ProductionSession
        except ImportError as exc:
            raise SyslsConnectionError(
                "tastytrade is not installed. Install it with: pip install 'sysls[tastytrade]'",
                venue=self.name,
            ) from exc

        try:
            if self._is_test:
                session = CertificationSession(self._login, self._password)
            else:
                session = ProductionSession(self._login, self._password)
        except Exception as exc:
            raise SyslsConnectionError(
                f"Failed to authenticate with tastytrade: {exc}",
                venue=self.name,
            ) from exc

        try:
            accounts = Account.get_accounts(session)
        except Exception as exc:
            raise SyslsConnectionError(
                f"Failed to retrieve accounts: {exc}",
                venue=self.name,
            ) from exc

        if not accounts:
            raise SyslsConnectionError(
                "No accounts found for this login.",
                venue=self.name,
            )

        if self._account_number:
            matched = [
                a for a in accounts if getattr(a, "account_number", None) == self._account_number
            ]
            if not matched:
                raise SyslsConnectionError(
                    f"Account {self._account_number} not found. "
                    f"Available: {[getattr(a, 'account_number', '?') for a in accounts]}",
                    venue=self.name,
                )
            account = matched[0]
        else:
            account = accounts[0]

        self._session = session
        self._account = account
        self._logger.info(
            "tastytrade_connected",
            is_test=self._is_test,
            account=getattr(account, "account_number", "unknown"),
        )

    async def disconnect(self) -> None:
        """Disconnect from tastytrade API.

        Destroys the session and clears internal state. Safe to call
        multiple times.
        """
        if self._session is not None:
            import contextlib

            with contextlib.suppress(Exception):
                self._session.destroy()
            self._session = None
            self._account = None
            self._logger.info("tastytrade_disconnected")

    # -- Properties --------------------------------------------------------

    @property
    def name(self) -> str:
        """Human-readable venue name."""
        return "tastytrade"

    @property
    def is_connected(self) -> bool:
        """Whether the adapter has an active session."""
        return self._session is not None

    @property
    def supported_order_types(self) -> list[OrderType]:
        """Order types supported by tastytrade."""
        return [OrderType.MARKET, OrderType.LIMIT, OrderType.STOP, OrderType.STOP_LIMIT]

    # -- Order operations --------------------------------------------------

    async def submit_order(self, order: OrderRequest) -> str:
        """Submit an order to tastytrade.

        Builds a tastytrade NewOrder with a Leg from the order request
        and submits it via the account API.

        Args:
            order: Normalized order request.

        Returns:
            Venue-assigned order ID.

        Raises:
            OrderError: If the order cannot be submitted.
            VenueError: If there is a connectivity issue.
        """
        from decimal import Decimal as Dec

        session = self._require_session()

        try:
            from tastytrade.order import Leg, NewOrder, OrderAction
        except ImportError as exc:
            raise VenueError(
                f"Failed to import tastytrade order types: {exc}",
                venue=self.name,
            ) from exc

        # Map sysls Side to tastytrade OrderAction
        action = OrderAction.BUY_TO_OPEN if order.side == Side.BUY else OrderAction.SELL_TO_CLOSE

        # Map sysls OrderType to tastytrade OrderType
        tt_order_type = _map_sysls_order_type(order.order_type)

        # Map instrument asset class to tastytrade InstrumentType
        tt_instrument_type = _map_asset_class_to_instrument_type(order.instrument.asset_class)

        leg = Leg(
            instrument_type=tt_instrument_type,
            symbol=order.instrument.symbol,
            action=action,
            quantity=int(order.quantity),
        )

        # Build the NewOrder
        tt_tif = _map_time_in_force(order.time_in_force)

        new_order = NewOrder(
            time_in_force=tt_tif,
            order_type=tt_order_type,
            legs=[leg],
            price=Dec(str(order.price)) if order.price is not None else None,
            stop_trigger=Dec(str(order.stop_price)) if order.stop_price is not None else None,
        )

        self._logger.info(
            "tastytrade_order_submitting",
            symbol=order.instrument.symbol,
            side=order.side.value,
            order_type=order.order_type.value,
            quantity=str(order.quantity),
        )

        try:
            response = self._account.place_order(session, new_order, dry_run=False)
        except Exception as exc:
            self._wrap_tt_error(exc, context=f"submit_order for {order.instrument.symbol}")

        # Extract venue order ID from response
        venue_order_id = str(response.order.id)

        self._logger.info(
            "tastytrade_order_submitted",
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
        """Cancel an order on tastytrade.

        Args:
            venue_order_id: The venue's order identifier.
            instrument: The instrument (used for event emission).

        Raises:
            OrderError: If the order cannot be cancelled.
        """
        session = self._require_session()

        self._logger.info(
            "tastytrade_order_cancelling",
            venue_order_id=venue_order_id,
        )

        try:
            self._account.delete_order(session, int(venue_order_id))
        except Exception as exc:
            self._wrap_tt_error(exc, context=f"cancel_order {venue_order_id}")

        self._logger.info(
            "tastytrade_order_cancelled",
            venue_order_id=venue_order_id,
        )

        await self._bus.publish(
            OrderCancelled(
                order_id=venue_order_id,
                instrument=instrument,
                reason="Cancelled via tastytrade",
                source=self.name,
            )
        )

    async def get_order_status(self, venue_order_id: str, instrument: Instrument) -> OrderStatus:
        """Query current status of an order at tastytrade.

        Args:
            venue_order_id: The venue's order identifier.
            instrument: The instrument.

        Returns:
            Current order status mapped to sysls OrderStatus.

        Raises:
            OrderError: If the order cannot be fetched.
            VenueError: If there is a connectivity issue.
        """
        session = self._require_session()

        try:
            placed_order = self._account.get_order(session, int(venue_order_id))
        except Exception as exc:
            self._wrap_tt_error(exc, context=f"get_order_status {venue_order_id}")

        status_str = getattr(placed_order, "status", None)
        if status_str is None:
            return OrderStatus.PENDING

        # The status may be an enum with a .value or a plain string
        status_value = getattr(status_str, "value", str(status_str))
        return _map_tt_status(status_value)

    # -- Position / balance queries ----------------------------------------

    async def get_positions(self) -> dict[Instrument, Decimal]:
        """Get all current positions from tastytrade.

        Returns:
            Mapping from instrument to net quantity
            (positive=long, negative=short).

        Raises:
            VenueError: If positions cannot be fetched.
        """
        from decimal import Decimal as Dec

        session = self._require_session()

        try:
            raw_positions = self._account.get_positions(session)
        except Exception as exc:
            self._wrap_tt_error(exc, context="get_positions")

        positions: dict[Instrument, Dec] = {}
        for pos in raw_positions:
            quantity = Dec(str(getattr(pos, "quantity", 0)))
            if quantity == Dec("0"):
                continue

            instrument = _build_instrument_from_position(pos)
            positions[instrument] = quantity

        return positions

    async def get_balances(self) -> dict[str, Decimal]:
        """Get account balances from tastytrade.

        Returns:
            Mapping from balance label to value (e.g. cash_balance,
            net_liquidating_value, equity_buying_power).

        Raises:
            VenueError: If balances cannot be fetched.
        """
        from decimal import Decimal as Dec

        session = self._require_session()

        try:
            balance = self._account.get_balances(session)
        except Exception as exc:
            self._wrap_tt_error(exc, context="get_balances")

        result: dict[str, Dec] = {}

        # Extract standard balance fields
        for field_name in (
            "cash_balance",
            "net_liquidating_value",
            "equity_buying_power",
            "derivative_buying_power",
            "day_trading_buying_power",
            "maintenance_excess",
        ):
            value = getattr(balance, field_name, None)
            if value is not None:
                try:
                    dec_value = Dec(str(value))
                    if dec_value != Dec("0"):
                        result[field_name] = dec_value
                except Exception:
                    continue

        return result

    # -- Private helpers ---------------------------------------------------

    def _require_session(self) -> Any:
        """Return the session or raise if not connected.

        Returns:
            The tastytrade Session instance.

        Raises:
            VenueError: If not connected.
        """
        if self._session is None:
            raise VenueError(
                "Not connected. Call connect() first.",
                venue=self.name,
            )
        return self._session

    def _wrap_tt_error(self, exc: Exception, context: str) -> None:
        """Wrap a tastytrade exception in a sysls exception and re-raise.

        Maps tastytrade SDK exceptions to appropriate sysls exception types:
        - ``TastytradeError`` with auth-related messages -> ``SyslsConnectionError``
        - ``TastytradeError`` with order-related messages -> ``OrderError``
        - ``ConnectionError`` / ``TimeoutError`` -> ``SyslsConnectionError``
        - Other exceptions -> ``VenueError``

        Args:
            exc: The exception to wrap.
            context: Description of the operation that failed.

        Raises:
            OrderError: For order-related errors.
            SyslsConnectionError: For authentication/connection errors.
            VenueError: For other errors.
        """
        self._logger.error(
            "tastytrade_error",
            context=context,
            error_type=type(exc).__name__,
            error=str(exc),
        )

        # Check for TastytradeError by class name to avoid import at module level
        exc_type_name = type(exc).__name__

        if isinstance(exc, (ConnectionError, OSError, TimeoutError)):
            raise SyslsConnectionError(
                f"{context}: {exc}",
                venue=self.name,
            ) from exc

        if exc_type_name == "TastytradeError":
            msg = str(exc).lower()
            if any(kw in msg for kw in ("auth", "login", "session", "token", "credential")):
                raise SyslsConnectionError(
                    f"{context}: {exc}",
                    venue=self.name,
                ) from exc
            if any(kw in msg for kw in ("order", "quantity", "price", "leg", "symbol")):
                raise OrderError(
                    f"{context}: {exc}",
                    venue=self.name,
                ) from exc
            raise VenueError(
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


def _map_sysls_order_type(order_type: OrderType) -> Any:
    """Map a sysls OrderType to a tastytrade OrderType enum value.

    Args:
        order_type: The sysls order type.

    Returns:
        The corresponding tastytrade OrderType.

    Raises:
        OrderError: If the order type is not supported.
    """
    from tastytrade.order import OrderType as TtOrderType

    mapping = {
        OrderType.MARKET: TtOrderType.MARKET,
        OrderType.LIMIT: TtOrderType.LIMIT,
        OrderType.STOP: TtOrderType.STOP,
        OrderType.STOP_LIMIT: TtOrderType.STOP_LIMIT,
    }
    tt_type = mapping.get(order_type)
    if tt_type is None:
        raise OrderError(
            f"Unsupported order type for tastytrade: {order_type}",
            venue="tastytrade",
        )
    return tt_type


def _map_time_in_force(tif: Any) -> Any:
    """Map a sysls TimeInForce to a tastytrade OrderTimeInForce enum value.

    Args:
        tif: The sysls TimeInForce value.

    Returns:
        The corresponding tastytrade OrderTimeInForce.
    """
    from tastytrade.order import OrderTimeInForce

    from sysls.core.types import TimeInForce

    mapping = {
        TimeInForce.GTC: OrderTimeInForce.GTC,
        TimeInForce.DAY: OrderTimeInForce.DAY,
        TimeInForce.IOC: OrderTimeInForce.IOC,
        TimeInForce.GTD: OrderTimeInForce.GTD,
    }
    return mapping.get(tif, OrderTimeInForce.DAY)


def _map_asset_class_to_instrument_type(asset_class: AssetClass) -> Any:
    """Map a sysls AssetClass to a tastytrade InstrumentType.

    Args:
        asset_class: The sysls asset class.

    Returns:
        The corresponding tastytrade InstrumentType.
    """
    from tastytrade.order import InstrumentType

    mapping = {
        AssetClass.EQUITY: InstrumentType.EQUITY,
        AssetClass.OPTION: InstrumentType.EQUITY_OPTION,
        AssetClass.FUTURE: InstrumentType.FUTURE,
        AssetClass.CRYPTO_SPOT: InstrumentType.CRYPTOCURRENCY,
    }
    return mapping.get(asset_class, InstrumentType.EQUITY)


def _map_tt_status(tt_status: str) -> OrderStatus:
    """Map a tastytrade order status string to sysls OrderStatus.

    Args:
        tt_status: Status string from tastytrade (e.g. ``"Live"``,
            ``"Filled"``).

    Returns:
        Corresponding sysls OrderStatus.
    """
    return _TT_STATUS_MAP.get(tt_status, OrderStatus.PENDING)


# Mapping from tastytrade InstrumentType strings to sysls AssetClass.
_TT_INSTRUMENT_TYPE_MAP: dict[str, AssetClass] = {
    "Equity": AssetClass.EQUITY,
    "Equity Option": AssetClass.OPTION,
    "Future": AssetClass.FUTURE,
    "Future Option": AssetClass.OPTION,
    "Cryptocurrency": AssetClass.CRYPTO_SPOT,
}


def _build_instrument_from_position(position: Any) -> Instrument:
    """Build a sysls Instrument from a tastytrade CurrentPosition.

    Args:
        position: A tastytrade CurrentPosition object with symbol,
            instrument_type, and underlying_symbol attributes.

    Returns:
        A sysls Instrument with appropriate asset class and venue.
    """
    symbol = getattr(position, "symbol", "")
    instrument_type_raw = getattr(position, "instrument_type", None)

    # Extract the string value from the enum
    instrument_type_str = getattr(instrument_type_raw, "value", str(instrument_type_raw or ""))
    asset_class = _TT_INSTRUMENT_TYPE_MAP.get(instrument_type_str, AssetClass.EQUITY)

    return Instrument(
        symbol=symbol,
        asset_class=asset_class,
        venue=Venue.TASTYTRADE,
        currency="USD",
    )
