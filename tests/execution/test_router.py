"""Tests for the Smart Order Router."""

from __future__ import annotations

from decimal import Decimal

import pytest

from sysls.core.exceptions import OrderError
from sysls.core.types import (
    AssetClass,
    Instrument,
    OrderRequest,
    OrderType,
    Side,
    TimeInForce,
    Venue,
)
from sysls.execution.router import SmartOrderRouter
from tests.execution.test_base import MockVenueAdapter

# -- Helpers ---------------------------------------------------------------


def _make_instrument(
    symbol: str = "AAPL",
    venue: Venue = Venue.PAPER,
) -> Instrument:
    """Create a test instrument."""
    return Instrument(
        symbol=symbol,
        asset_class=AssetClass.EQUITY,
        venue=venue,
    )


def _make_order_request(
    instrument: Instrument | None = None,
    side: Side = Side.BUY,
    quantity: Decimal = Decimal("10"),
) -> OrderRequest:
    """Create a test order request."""
    if instrument is None:
        instrument = _make_instrument()
    return OrderRequest(
        instrument=instrument,
        side=side,
        order_type=OrderType.MARKET,
        quantity=quantity,
        time_in_force=TimeInForce.GTC,
    )


# -- Tests -----------------------------------------------------------------


class TestSmartOrderRouterRegistration:
    """Tests for venue registration and lookup."""

    def test_register_venue(self) -> None:
        """register_venue should store the adapter by name."""
        router = SmartOrderRouter()
        adapter = MockVenueAdapter(name="PAPER")

        router.register_venue("PAPER", adapter)

        assert router.get_venue("PAPER") is adapter

    def test_unregister_venue(self) -> None:
        """unregister_venue should remove the adapter."""
        router = SmartOrderRouter()
        adapter = MockVenueAdapter(name="PAPER")
        router.register_venue("PAPER", adapter)

        router.unregister_venue("PAPER")

        assert router.get_venue("PAPER") is None

    def test_unregister_unknown_venue_raises(self) -> None:
        """unregister_venue for unknown name should raise OrderError."""
        router = SmartOrderRouter()

        with pytest.raises(OrderError, match="not registered"):
            router.unregister_venue("NONEXISTENT")

    def test_get_venue_returns_none_for_unknown(self) -> None:
        """get_venue should return None for unregistered names."""
        router = SmartOrderRouter()
        assert router.get_venue("PAPER") is None

    def test_registered_venues_property(self) -> None:
        """registered_venues should return sorted list of venue names."""
        router = SmartOrderRouter()
        adapter_b = MockVenueAdapter(name="CCXT")
        adapter_a = MockVenueAdapter(name="PAPER")

        router.register_venue("PAPER", adapter_a)
        router.register_venue("CCXT", adapter_b)

        assert router.registered_venues == ["CCXT", "PAPER"]

    def test_registered_venues_empty(self) -> None:
        """registered_venues should return empty list when no venues registered."""
        router = SmartOrderRouter()
        assert router.registered_venues == []

    def test_constructor_with_initial_venues(self) -> None:
        """SmartOrderRouter should accept initial venues in constructor."""
        adapter = MockVenueAdapter(name="PAPER")
        router = SmartOrderRouter(venues={"PAPER": adapter})

        assert router.get_venue("PAPER") is adapter
        assert router.registered_venues == ["PAPER"]


class TestSmartOrderRouterRouting:
    """Tests for order routing logic."""

    def test_resolve_venue_finds_correct_adapter(self) -> None:
        """resolve_venue should match instrument.venue to registered adapter."""
        router = SmartOrderRouter()
        paper_adapter = MockVenueAdapter(name="PAPER")
        router.register_venue("PAPER", paper_adapter)

        instrument = _make_instrument(venue=Venue.PAPER)
        request = _make_order_request(instrument=instrument)

        adapter = router.resolve_venue(request)
        assert adapter is paper_adapter

    def test_resolve_venue_raises_for_unknown_venue(self) -> None:
        """resolve_venue should raise OrderError for unregistered venues."""
        router = SmartOrderRouter()
        instrument = _make_instrument(venue=Venue.CCXT)
        request = _make_order_request(instrument=instrument)

        with pytest.raises(OrderError, match="No venue adapter registered"):
            router.resolve_venue(request)

    @pytest.mark.asyncio
    async def test_route_order_submits_to_correct_venue(self) -> None:
        """route_order should submit through the resolved venue adapter."""
        router = SmartOrderRouter()
        paper_adapter = MockVenueAdapter(name="PAPER")
        router.register_venue("PAPER", paper_adapter)

        instrument = _make_instrument(venue=Venue.PAPER)
        request = _make_order_request(instrument=instrument)

        venue_order_id = await router.route_order(request)

        assert venue_order_id == "MOCK-1"
        assert len(paper_adapter.submitted_orders) == 1
        assert paper_adapter.submitted_orders[0] is request

    @pytest.mark.asyncio
    async def test_route_order_to_multiple_venues(self) -> None:
        """route_order should route to different venues based on instrument."""
        router = SmartOrderRouter()
        paper_adapter = MockVenueAdapter(name="PAPER")
        ccxt_adapter = MockVenueAdapter(name="CCXT")
        router.register_venue("PAPER", paper_adapter)
        router.register_venue("CCXT", ccxt_adapter)

        paper_instrument = _make_instrument(symbol="AAPL", venue=Venue.PAPER)
        ccxt_instrument = Instrument(
            symbol="BTC-USDT",
            asset_class=AssetClass.CRYPTO_SPOT,
            venue=Venue.CCXT,
        )

        paper_request = _make_order_request(instrument=paper_instrument)
        ccxt_request = _make_order_request(instrument=ccxt_instrument)

        await router.route_order(paper_request)
        await router.route_order(ccxt_request)

        assert len(paper_adapter.submitted_orders) == 1
        assert len(ccxt_adapter.submitted_orders) == 1

    @pytest.mark.asyncio
    async def test_route_order_raises_for_unknown_venue(self) -> None:
        """route_order should raise OrderError for unregistered venues."""
        router = SmartOrderRouter()
        instrument = _make_instrument(venue=Venue.IBKR)
        request = _make_order_request(instrument=instrument)

        with pytest.raises(OrderError, match="No venue adapter registered"):
            await router.route_order(request)
