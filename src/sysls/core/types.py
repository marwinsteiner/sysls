"""Core domain types for the sysls trading framework.

Defines fundamental types used across all layers: instruments, order parameters,
sides, time-in-force policies, and venue identifiers. These types form the
shared vocabulary that all components communicate through.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from enum import StrEnum, unique

from pydantic import BaseModel, Field


@unique
class Side(StrEnum):
    """Order or position side."""

    BUY = "BUY"
    SELL = "SELL"


@unique
class OrderType(StrEnum):
    """Supported order types."""

    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


@unique
class TimeInForce(StrEnum):
    """Time-in-force policies for orders."""

    GTC = "GTC"  # Good 'til cancelled
    IOC = "IOC"  # Immediate or cancel
    FOK = "FOK"  # Fill or kill
    DAY = "DAY"  # Day order
    GTD = "GTD"  # Good 'til date


@unique
class AssetClass(StrEnum):
    """Broad asset class categories."""

    EQUITY = "EQUITY"
    CRYPTO_SPOT = "CRYPTO_SPOT"
    CRYPTO_PERP = "CRYPTO_PERP"
    CRYPTO_FUTURE = "CRYPTO_FUTURE"
    OPTION = "OPTION"
    FUTURE = "FUTURE"
    EVENT = "EVENT"  # Polymarket-style event contracts


@unique
class Venue(StrEnum):
    """Supported trading venues."""

    TASTYTRADE = "TASTYTRADE"
    IBKR = "IBKR"
    CCXT = "CCXT"  # Generic ccxt-based crypto venue
    POLYMARKET = "POLYMARKET"
    PAPER = "PAPER"  # Paper trading / simulated


@unique
class OrderStatus(StrEnum):
    """Lifecycle states of an order."""

    PENDING = "PENDING"  # Created, not yet submitted
    SUBMITTED = "SUBMITTED"  # Sent to venue
    ACCEPTED = "ACCEPTED"  # Acknowledged by venue
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class Instrument(BaseModel, frozen=True):
    """Uniquely identifies a tradeable instrument across venues.

    Instruments are immutable value objects. Two instruments with the same
    symbol, asset_class, and venue are considered equal.

    Attributes:
        symbol: Canonical symbol (e.g. "NVDA", "BTC-USDT-PERP", "WILL_BTC_HIT_100K").
        asset_class: The broad asset class this instrument belongs to.
        venue: The venue this instrument is traded on.
        exchange: Optional exchange identifier within a venue (e.g. "binance" under CCXT).
        currency: Quote currency (e.g. "USD", "USDT").
        multiplier: Contract multiplier. Defaults to 1 for spot instruments.
        tick_size: Minimum price increment. None if unknown.
        lot_size: Minimum quantity increment. None if unknown.
    """

    symbol: str
    asset_class: AssetClass
    venue: Venue
    exchange: str | None = None
    currency: str = "USD"
    multiplier: Decimal = Decimal("1")
    tick_size: Decimal | None = None
    lot_size: Decimal | None = None

    def __str__(self) -> str:
        """Return a human-readable representation."""
        parts = [self.symbol, self.asset_class.value, self.venue.value]
        if self.exchange:
            parts.append(self.exchange)
        return ":".join(parts)


def generate_order_id() -> str:
    """Generate a unique order identifier.

    Returns:
        A UUID4 string suitable for use as an order or correlation ID.
    """
    return str(uuid.uuid4())


class OrderRequest(BaseModel, frozen=True):
    """A request to submit an order.

    This is the normalized order representation that flows through the system.
    Venue adapters translate this into venue-specific API calls.

    Attributes:
        order_id: Unique identifier for this order.
        instrument: The instrument to trade.
        side: Buy or sell.
        order_type: Market, limit, stop, etc.
        quantity: Order quantity (always positive).
        price: Limit price. Required for LIMIT and STOP_LIMIT orders.
        stop_price: Stop trigger price. Required for STOP and STOP_LIMIT orders.
        time_in_force: How long the order remains active.
        client_order_id: Optional client-specified correlation ID.
    """

    order_id: str = Field(default_factory=generate_order_id)
    instrument: Instrument
    side: Side
    order_type: OrderType
    quantity: Decimal
    price: Decimal | None = None
    stop_price: Decimal | None = None
    time_in_force: TimeInForce = TimeInForce.GTC
    client_order_id: str | None = None
