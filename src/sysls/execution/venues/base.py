"""Venue adapter abstract base class.

Defines the interface that all venue adapters (paper, ccxt, IBKR, tastytrade,
Polymarket, etc.) must implement. Venue adapters are thin translation layers
between sysls's normalized types and venue-specific APIs.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from decimal import Decimal

    from sysls.core.types import Instrument, OrderRequest, OrderStatus, OrderType


class VenueAdapter(ABC):
    """Abstract base class for venue connectivity.

    Venue adapters are thin translation layers between sysls's normalized
    types and venue-specific APIs. Business logic (retry, rate limiting,
    batching) lives in the OMS, not in adapters.

    Adapters support the async context manager protocol for clean
    resource lifecycle management.
    """

    # -- Lifecycle ---------------------------------------------------------

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the venue."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Cleanly release all resources. Safe to call multiple times."""

    async def __aenter__(self) -> Self:
        """Enter async context manager, establishing connection."""
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        """Exit async context manager, releasing resources."""
        await self.disconnect()

    # -- Properties --------------------------------------------------------

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable venue name (e.g. 'paper', 'ccxt-binance')."""

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Whether the adapter has an active connection."""

    @property
    @abstractmethod
    def supported_order_types(self) -> list[OrderType]:
        """Order types supported by this venue."""

    # -- Order operations --------------------------------------------------

    @abstractmethod
    async def submit_order(self, order: OrderRequest) -> str:
        """Submit an order to the venue.

        Args:
            order: Normalized order request.

        Returns:
            Venue-assigned order ID.

        Raises:
            OrderError: If the order cannot be submitted.
            VenueError: If there is a connectivity issue.
        """

    @abstractmethod
    async def cancel_order(self, venue_order_id: str, instrument: Instrument) -> None:
        """Cancel an existing order.

        Args:
            venue_order_id: Venue's order identifier.
            instrument: The instrument (some venues need this for cancel).

        Raises:
            OrderError: If the order cannot be cancelled.
        """

    @abstractmethod
    async def get_order_status(self, venue_order_id: str, instrument: Instrument) -> OrderStatus:
        """Query current status of an order at the venue.

        Args:
            venue_order_id: Venue's order identifier.
            instrument: The instrument.

        Returns:
            Current order status.
        """

    # -- Position / balance queries ----------------------------------------

    @abstractmethod
    async def get_positions(self) -> dict[Instrument, Decimal]:
        """Get all current positions at the venue.

        Returns:
            Mapping from instrument to net quantity
            (positive=long, negative=short).
        """

    @abstractmethod
    async def get_balances(self) -> dict[str, Decimal]:
        """Get account balances by currency.

        Returns:
            Mapping from currency code to available balance.
        """
