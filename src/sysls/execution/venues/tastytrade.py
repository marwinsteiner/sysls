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
            from tastytrade import Account, ProductionSession
            from tastytrade import CertificationSession
        except ImportError as exc:
            raise SyslsConnectionError(
                "tastytrade is not installed. "
                "Install it with: pip install 'sysls[tastytrade]'",
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
                a for a in accounts
                if getattr(a, "account_number", None) == self._account_number
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
            try:
                self._session.destroy()
            except Exception:
                pass  # Best-effort cleanup
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

        Args:
            order: Normalized order request.

        Returns:
            Venue-assigned order ID.

        Raises:
            OrderError: If the order cannot be submitted.
            VenueError: If there is a connectivity issue.
        """
        raise NotImplementedError

    async def cancel_order(self, venue_order_id: str, instrument: Instrument) -> None:
        """Cancel an order on tastytrade.

        Args:
            venue_order_id: The venue's order identifier.
            instrument: The instrument (used for event emission).

        Raises:
            OrderError: If the order cannot be cancelled.
        """
        raise NotImplementedError

    async def get_order_status(
        self, venue_order_id: str, instrument: Instrument
    ) -> OrderStatus:
        """Query current status of an order at tastytrade.

        Args:
            venue_order_id: The venue's order identifier.
            instrument: The instrument.

        Returns:
            Current order status mapped to sysls OrderStatus.
        """
        raise NotImplementedError

    # -- Position / balance queries ----------------------------------------

    async def get_positions(self) -> dict[Instrument, Decimal]:
        """Get all current positions from tastytrade.

        Returns:
            Mapping from instrument to net quantity
            (positive=long, negative=short).

        Raises:
            VenueError: If positions cannot be fetched.
        """
        raise NotImplementedError

    async def get_balances(self) -> dict[str, Decimal]:
        """Get account balances from tastytrade.

        Returns:
            Mapping from balance label to value (e.g. cash_balance,
            net_liquidating_value, equity_buying_power).

        Raises:
            VenueError: If balances cannot be fetched.
        """
        raise NotImplementedError

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

        Args:
            exc: The exception to wrap.
            context: Description of the operation that failed.

        Raises:
            OrderError: For order-related errors.
            SyslsConnectionError: For authentication/connection errors.
            VenueError: For other errors.
        """
        raise NotImplementedError


def _map_tt_status(tt_status: str) -> OrderStatus:
    """Map a tastytrade order status string to sysls OrderStatus.

    Args:
        tt_status: Status string from tastytrade (e.g. ``"Live"``,
            ``"Filled"``).

    Returns:
        Corresponding sysls OrderStatus.
    """
    return _TT_STATUS_MAP.get(tt_status, OrderStatus.PENDING)


def _build_instrument_from_position(position: Any) -> Instrument:
    """Build a sysls Instrument from a tastytrade CurrentPosition.

    Args:
        position: A tastytrade CurrentPosition object.

    Returns:
        A sysls Instrument.
    """
    raise NotImplementedError
