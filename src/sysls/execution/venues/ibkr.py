"""Interactive Brokers venue adapter via ib_async.

Wraps the ib_async library (successor to ib_insync) to provide connectivity
to Interactive Brokers through the VenueAdapter interface. Most ib_async calls
are synchronous and are offloaded to a thread via asyncio.to_thread() to avoid
blocking the event loop.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from sysls.core.exceptions import ConnectionError as SyslsConnectionError
from sysls.core.exceptions import VenueError
from sysls.core.types import (
    Instrument,
    OrderStatus,
    OrderType,
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
        raise NotImplementedError

    async def cancel_order(self, venue_order_id: str, instrument: Instrument) -> None:
        """Cancel an order on Interactive Brokers.

        Args:
            venue_order_id: The venue's order identifier.
            instrument: The instrument (needed for the cancel lookup).

        Raises:
            OrderError: If the order cannot be cancelled.
        """
        raise NotImplementedError

    async def get_order_status(self, venue_order_id: str, instrument: Instrument) -> OrderStatus:
        """Query current status of an order at Interactive Brokers.

        Args:
            venue_order_id: The venue's order identifier.
            instrument: The instrument.

        Returns:
            Current order status.
        """
        raise NotImplementedError

    # -- Position / balance queries ----------------------------------------

    async def get_positions(self) -> dict[Instrument, Decimal]:
        """Get all current positions from Interactive Brokers.

        Returns:
            Mapping from instrument to net quantity
            (positive=long, negative=short).

        Raises:
            VenueError: If positions cannot be fetched.
        """
        raise NotImplementedError

    async def get_balances(self) -> dict[str, Decimal]:
        """Get account balances from Interactive Brokers.

        Returns:
            Mapping from currency code to available balance.

        Raises:
            VenueError: If balances cannot be fetched.
        """
        raise NotImplementedError

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


def _to_ib_contract(instrument: Instrument) -> Any:
    """Map a sysls Instrument to an ib_async Contract.

    Args:
        instrument: The sysls instrument.

    Returns:
        An ib_async Contract subclass (Stock, Option, Future, Forex).

    Raises:
        OrderError: If the asset class is not supported.
    """
    raise NotImplementedError


def _to_ib_order(request: OrderRequest) -> Any:
    """Map a sysls OrderRequest to an ib_async Order.

    Args:
        request: The sysls order request.

    Returns:
        An ib_async Order subclass (MarketOrder, LimitOrder, etc.).

    Raises:
        OrderError: If the order type is not supported.
    """
    raise NotImplementedError


def _map_ib_status(ib_status: str) -> OrderStatus:
    """Map an IB order status string to sysls OrderStatus.

    Args:
        ib_status: Status string from ib_async (e.g. ``"Submitted"``,
            ``"Filled"``).

    Returns:
        Corresponding sysls OrderStatus.
    """
    return _IB_STATUS_MAP.get(ib_status, OrderStatus.PENDING)
