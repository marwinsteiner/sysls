"""Event type hierarchy for the sysls trading framework.

All inter-component communication flows through typed, immutable events
dispatched via the async event bus. This module defines the complete
event hierarchy as frozen Pydantic models.

Event (base)
├── MarketDataEvent
│   ├── QuoteEvent
│   ├── TradeEvent
│   ├── BarEvent
│   └── OrderBookEvent
├── OrderEvent
│   ├── OrderSubmitted
│   ├── OrderAccepted
│   ├── OrderRejected
│   ├── OrderCancelled
│   └── OrderAmended
├── FillEvent
├── PositionEvent
├── SignalEvent
├── RiskEvent
├── SystemEvent
│   ├── HeartbeatEvent
│   ├── ConnectionEvent
│   └── ErrorEvent
└── TimerEvent
"""

from __future__ import annotations

import time
import uuid
from decimal import Decimal
from enum import StrEnum, unique

from pydantic import BaseModel, Field

from sysls.core.types import Instrument, OrderStatus, Side  # noqa: TC001

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_event_id() -> str:
    """Generate a unique event identifier."""
    return str(uuid.uuid4())


def _now_ns() -> int:
    """Return current wall-clock time as nanoseconds since epoch."""
    return int(time.time() * 1_000_000_000)


# ---------------------------------------------------------------------------
# Base event
# ---------------------------------------------------------------------------


class Event(BaseModel, frozen=True):
    """Base class for all events in the system.

    Every event carries a unique ID, a nanosecond-precision timestamp,
    and an optional source identifier indicating which component emitted it.

    Attributes:
        event_id: Unique identifier for this event instance.
        timestamp_ns: Event creation time in nanoseconds since epoch.
        source: Identifier of the component that created the event.
    """

    event_id: str = Field(default_factory=_generate_event_id)
    timestamp_ns: int = Field(default_factory=_now_ns)
    source: str | None = None


# ---------------------------------------------------------------------------
# Market data events
# ---------------------------------------------------------------------------


class MarketDataEvent(Event, frozen=True):
    """Base class for all market data events.

    Attributes:
        instrument: The instrument this data relates to.
    """

    instrument: Instrument


class QuoteEvent(MarketDataEvent, frozen=True):
    """Bid/ask quote update.

    Attributes:
        bid_price: Current best bid price.
        bid_size: Size available at the bid.
        ask_price: Current best ask price.
        ask_size: Size available at the ask.
    """

    bid_price: Decimal
    bid_size: Decimal
    ask_price: Decimal
    ask_size: Decimal


class TradeEvent(MarketDataEvent, frozen=True):
    """Individual trade/tick event.

    Attributes:
        price: Trade execution price.
        size: Trade size.
        side: Aggressor side, if known.
    """

    price: Decimal
    size: Decimal
    side: Side | None = None


class BarEvent(MarketDataEvent, frozen=True):
    """OHLCV bar event.

    Attributes:
        open: Opening price.
        high: High price.
        low: Low price.
        close: Closing price.
        volume: Bar volume.
        bar_start_ns: Bar period start in nanoseconds since epoch.
        bar_end_ns: Bar period end in nanoseconds since epoch.
    """

    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    bar_start_ns: int
    bar_end_ns: int


class OrderBookEvent(MarketDataEvent, frozen=True):
    """L2/L3 order book snapshot or delta.

    Attributes:
        bids: List of (price, size) tuples on the bid side, best first.
        asks: List of (price, size) tuples on the ask side, best first.
        is_snapshot: True if this is a full snapshot, False for a delta update.
    """

    bids: tuple[tuple[Decimal, Decimal], ...] = ()
    asks: tuple[tuple[Decimal, Decimal], ...] = ()
    is_snapshot: bool = True


# ---------------------------------------------------------------------------
# Order events
# ---------------------------------------------------------------------------


class OrderEvent(Event, frozen=True):
    """Base class for order lifecycle events.

    Attributes:
        order_id: The order this event relates to.
        instrument: The instrument being traded.
    """

    order_id: str
    instrument: Instrument


class OrderSubmitted(OrderEvent, frozen=True):
    """Emitted when an order is submitted to a venue.

    Attributes:
        side: Order side.
        quantity: Order quantity.
        price: Limit price, if applicable.
    """

    side: Side
    quantity: Decimal
    price: Decimal | None = None


class OrderAccepted(OrderEvent, frozen=True):
    """Emitted when a venue acknowledges receipt of an order.

    Attributes:
        venue_order_id: The venue's own identifier for this order.
    """

    venue_order_id: str | None = None


class OrderRejected(OrderEvent, frozen=True):
    """Emitted when a venue rejects an order.

    Attributes:
        reason: Human-readable rejection reason from the venue.
    """

    reason: str


class OrderCancelled(OrderEvent, frozen=True):
    """Emitted when an order is cancelled.

    Attributes:
        reason: Optional cancellation reason.
    """

    reason: str | None = None


class OrderAmended(OrderEvent, frozen=True):
    """Emitted when an order is amended (price or quantity change).

    Attributes:
        new_quantity: Updated quantity, if changed.
        new_price: Updated price, if changed.
    """

    new_quantity: Decimal | None = None
    new_price: Decimal | None = None


# ---------------------------------------------------------------------------
# Fill event
# ---------------------------------------------------------------------------


class FillEvent(Event, frozen=True):
    """Partial or full order fill.

    Attributes:
        order_id: The order that was filled.
        instrument: The instrument traded.
        side: Fill side.
        fill_price: Execution price.
        fill_quantity: Quantity filled in this event.
        cumulative_quantity: Total quantity filled so far for this order.
        order_status: Updated order status after this fill.
        venue_fill_id: Venue's identifier for this fill.
        commission: Commission charged, if known.
    """

    order_id: str
    instrument: Instrument
    side: Side
    fill_price: Decimal
    fill_quantity: Decimal
    cumulative_quantity: Decimal
    order_status: OrderStatus
    venue_fill_id: str | None = None
    commission: Decimal | None = None


# ---------------------------------------------------------------------------
# Position event
# ---------------------------------------------------------------------------


class PositionEvent(Event, frozen=True):
    """Emitted when a position changes.

    Attributes:
        instrument: The instrument whose position changed.
        quantity: Net position quantity (positive = long, negative = short).
        avg_price: Average entry price of the current position.
        realized_pnl: Realized PnL from the position change that triggered this event.
    """

    instrument: Instrument
    quantity: Decimal
    avg_price: Decimal
    realized_pnl: Decimal = Decimal("0")


# ---------------------------------------------------------------------------
# Signal event
# ---------------------------------------------------------------------------


@unique
class SignalDirection(StrEnum):
    """Direction of a trading signal."""

    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


class SignalEvent(Event, frozen=True):
    """Strategy signal emission.

    Attributes:
        strategy_id: Identifier of the strategy that generated the signal.
        instrument: Target instrument.
        direction: Signal direction.
        strength: Signal strength/conviction, typically in [-1.0, 1.0].
        metadata: Optional key-value metadata from the strategy.
    """

    strategy_id: str
    instrument: Instrument
    direction: SignalDirection
    strength: float = 1.0
    metadata: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Risk event
# ---------------------------------------------------------------------------


@unique
class RiskSeverity(StrEnum):
    """Severity level for risk events."""

    INFO = "INFO"
    WARNING = "WARNING"
    BREACH = "BREACH"  # Hard limit breached


class RiskEvent(Event, frozen=True):
    """Risk limit breach or warning.

    Attributes:
        severity: How severe this risk event is.
        rule_name: Name of the risk rule that triggered.
        message: Human-readable description of the risk event.
        instrument: The instrument involved, if applicable.
        current_value: Current value of the metric that triggered the event.
        limit_value: The limit that was approached or breached.
    """

    severity: RiskSeverity
    rule_name: str
    message: str
    instrument: Instrument | None = None
    current_value: float | None = None
    limit_value: float | None = None


# ---------------------------------------------------------------------------
# System events
# ---------------------------------------------------------------------------


class SystemEvent(Event, frozen=True):
    """Base class for system-level events."""


class HeartbeatEvent(SystemEvent, frozen=True):
    """Periodic heartbeat for liveness monitoring.

    Attributes:
        component: Name of the component emitting the heartbeat.
    """

    component: str


@unique
class ConnectionStatus(StrEnum):
    """Connection state for venue connections."""

    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    RECONNECTING = "RECONNECTING"


class ConnectionEvent(SystemEvent, frozen=True):
    """Venue connect/disconnect notification.

    Attributes:
        venue: The venue whose connection state changed.
        status: New connection status.
        reason: Optional reason for the state change.
    """

    venue: str
    status: ConnectionStatus
    reason: str | None = None


class ErrorEvent(SystemEvent, frozen=True):
    """System error notification.

    Attributes:
        error_type: Class name or category of the error.
        message: Human-readable error description.
        component: Component where the error occurred.
        recoverable: Whether the system can continue operating.
    """

    error_type: str
    message: str
    component: str | None = None
    recoverable: bool = True


# ---------------------------------------------------------------------------
# Timer event
# ---------------------------------------------------------------------------


class TimerEvent(Event, frozen=True):
    """Scheduled callback event.

    Attributes:
        timer_name: Identifier for this timer.
        scheduled_ns: When the timer was supposed to fire (ns since epoch).
    """

    timer_name: str
    scheduled_ns: int
