"""Smart Order Router for venue selection.

In Phase 2, routing is simple: match the instrument's venue to a registered
venue adapter. Future phases will add cross-venue routing, cost optimization,
and liquidity-based order splitting.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from sysls.core.exceptions import OrderError

if TYPE_CHECKING:
    from sysls.core.types import OrderRequest
    from sysls.execution.venues.base import VenueAdapter


class SmartOrderRouter:
    """Routes orders to the appropriate venue adapter.

    In Phase 2, routing is straightforward: match instrument.venue to a
    registered venue adapter by name. Future phases will add cross-venue
    routing, cost optimization, and liquidity-based splitting.

    Args:
        venues: Initial mapping of venue name to adapter. Can be empty.
    """

    def __init__(self, venues: dict[str, VenueAdapter] | None = None) -> None:
        self._venues: dict[str, VenueAdapter] = dict(venues or {})
        self._logger = structlog.get_logger(__name__)

    def register_venue(self, name: str, adapter: VenueAdapter) -> None:
        """Register a venue adapter.

        Args:
            name: Name to register under (should match Venue enum value).
            adapter: The venue adapter instance.
        """
        self._venues[name] = adapter
        self._logger.info("venue_registered", venue=name)

    def unregister_venue(self, name: str) -> None:
        """Remove a venue adapter.

        Args:
            name: The venue name to remove.

        Raises:
            OrderError: If the venue is not registered.
        """
        if name not in self._venues:
            raise OrderError(
                f"Venue '{name}' is not registered",
                venue=name,
            )
        del self._venues[name]
        self._logger.info("venue_unregistered", venue=name)

    def get_venue(self, name: str) -> VenueAdapter | None:
        """Look up a venue adapter by name.

        Args:
            name: The venue name.

        Returns:
            The adapter if found, None otherwise.
        """
        return self._venues.get(name)

    def resolve_venue(self, request: OrderRequest) -> VenueAdapter:
        """Find the appropriate venue for an order request.

        Uses the instrument's venue field to look up the registered adapter.

        Args:
            request: The order request containing the instrument.

        Returns:
            The matching venue adapter.

        Raises:
            OrderError: If no suitable venue is registered.
        """
        venue_name = request.instrument.venue.value
        adapter = self._venues.get(venue_name)
        if adapter is None:
            raise OrderError(
                f"No venue adapter registered for '{venue_name}'",
                venue=venue_name,
            )
        return adapter

    async def route_order(self, request: OrderRequest) -> str:
        """Route and submit an order to the appropriate venue.

        Resolves the venue for the instrument and delegates submission to it.

        Args:
            request: The order request to route.

        Returns:
            Venue-assigned order ID.

        Raises:
            OrderError: If routing or submission fails.
        """
        adapter = self.resolve_venue(request)
        self._logger.info(
            "order_routing",
            order_id=request.order_id,
            venue=adapter.name,
            instrument=str(request.instrument),
        )
        venue_order_id = await adapter.submit_order(request)
        self._logger.info(
            "order_routed",
            order_id=request.order_id,
            venue=adapter.name,
            venue_order_id=venue_order_id,
        )
        return venue_order_id

    @property
    def registered_venues(self) -> list[str]:
        """List of registered venue names.

        Returns:
            Sorted list of venue name strings.
        """
        return sorted(self._venues.keys())
