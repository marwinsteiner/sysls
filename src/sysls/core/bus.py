"""Async event bus with priority dispatch for the sysls trading framework.

The event bus is the backbone of all inter-component communication.
Components subscribe to event types and receive events asynchronously
via priority-aware dispatch. Supports exact type subscriptions,
wildcard (base class) subscriptions, and provides metrics on queue
depth and dispatch latency.

Example usage::

    bus = EventBus()

    async def on_quote(event: QuoteEvent) -> None:
        print(f"Quote: {event.instrument}")

    bus.subscribe(QuoteEvent, on_quote)
    await bus.start()
    await bus.publish(some_quote_event)
    # on_quote is called asynchronously
    await bus.stop()
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import defaultdict
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from enum import IntEnum, unique
from typing import Any

import structlog

from sysls.core.events import (
    Event,
    FillEvent,
    MarketDataEvent,
    OrderEvent,
    RiskEvent,
    SystemEvent,
)

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

# Type alias for async event handler callbacks.
EventHandler = Callable[[Any], Coroutine[Any, Any, None]]


@unique
class Priority(IntEnum):
    """Dispatch priority levels. Lower numeric value = higher priority.

    Risk events are dispatched before order events, which are dispatched
    before market data, etc. This ensures risk checks can halt execution
    before new orders are processed.
    """

    CRITICAL = 0  # System events, risk breaches
    HIGH = 10  # Order and fill events
    NORMAL = 20  # Market data
    LOW = 30  # Signals, analytics, timers


# Default priority mapping for built-in event types.
_DEFAULT_PRIORITIES: dict[type[Event], Priority] = {
    RiskEvent: Priority.CRITICAL,
    SystemEvent: Priority.CRITICAL,
    OrderEvent: Priority.HIGH,
    FillEvent: Priority.HIGH,
    MarketDataEvent: Priority.NORMAL,
}


def _resolve_priority(event: Event) -> Priority:
    """Determine dispatch priority for an event based on its type hierarchy.

    Walks the MRO to find the most specific match in the priority table.

    Args:
        event: The event to classify.

    Returns:
        The priority level for this event.
    """
    for cls in type(event).__mro__:
        if cls in _DEFAULT_PRIORITIES:
            return _DEFAULT_PRIORITIES[cls]
    return Priority.LOW


@dataclass
class BusMetrics:
    """Tracks event bus operational metrics.

    Attributes:
        events_published: Total number of events published.
        events_dispatched: Total number of handler invocations.
        handler_errors: Total number of handler exceptions caught.
        total_dispatch_latency_ns: Cumulative handler execution time in ns.
        max_queue_depth: Peak queue depth observed.
    """

    events_published: int = 0
    events_dispatched: int = 0
    handler_errors: int = 0
    total_dispatch_latency_ns: int = 0
    max_queue_depth: int = 0


@dataclass
class _Subscription:
    """Internal representation of an event subscription.

    Attributes:
        event_type: The event type subscribed to.
        handler: The async callback.
        subscription_id: Unique ID for unsubscribe.
    """

    event_type: type[Event]
    handler: EventHandler
    subscription_id: int = field(default=0)


class EventBus:
    """Async event bus with priority dispatch.

    The bus maintains an asyncio.PriorityQueue of pending events. A background
    dispatcher task pulls events in priority order and fans them out to all
    matching subscribers. Subscriptions match on exact type and all base classes,
    enabling wildcard-style subscriptions (e.g. subscribing to ``MarketDataEvent``
    receives ``QuoteEvent``, ``TradeEvent``, etc.).

    Args:
        max_queue_size: Maximum number of events that can be buffered. 0 = unbounded.
    """

    def __init__(self, max_queue_size: int = 0) -> None:
        self._queue: asyncio.PriorityQueue[tuple[int, int, Event]] = asyncio.PriorityQueue(
            maxsize=max_queue_size
        )
        # Maps event type -> list of subscriptions (includes base-class subs).
        self._subscribers: dict[type[Event], list[_Subscription]] = defaultdict(list)
        self._next_sub_id: int = 0
        self._sequence: int = 0  # Tie-breaker for same-priority events (FIFO).
        self._dispatcher_task: asyncio.Task[None] | None = None
        self._running: bool = False
        self._metrics: BusMetrics = BusMetrics()

    @property
    def metrics(self) -> BusMetrics:
        """Return current bus metrics."""
        return self._metrics

    @property
    def is_running(self) -> bool:
        """Return whether the bus dispatcher is active."""
        return self._running

    def subscribe(self, event_type: type[Event], handler: EventHandler) -> int:
        """Register a handler for an event type.

        The handler will be called for events of the exact type AND any subclass.
        For example, subscribing to ``MarketDataEvent`` will also receive
        ``QuoteEvent``, ``TradeEvent``, ``BarEvent``, and ``OrderBookEvent``.

        Args:
            event_type: The event type to subscribe to.
            handler: An async callable that accepts one argument (the event).

        Returns:
            A subscription ID that can be used to unsubscribe.
        """
        sub_id = self._next_sub_id
        self._next_sub_id += 1
        sub = _Subscription(
            event_type=event_type,
            handler=handler,
            subscription_id=sub_id,
        )
        self._subscribers[event_type].append(sub)
        logger.debug(
            "subscription_added",
            event_type=event_type.__name__,
            subscription_id=sub_id,
        )
        return sub_id

    def unsubscribe(self, subscription_id: int) -> bool:
        """Remove a subscription by its ID.

        Args:
            subscription_id: The ID returned by ``subscribe()``.

        Returns:
            True if the subscription was found and removed, False otherwise.
        """
        for event_type, subs in self._subscribers.items():
            for i, sub in enumerate(subs):
                if sub.subscription_id == subscription_id:
                    subs.pop(i)
                    logger.debug(
                        "subscription_removed",
                        event_type=event_type.__name__,
                        subscription_id=subscription_id,
                    )
                    return True
        return False

    async def publish(self, event: Event) -> None:
        """Publish an event to the bus.

        The event is enqueued with its priority and will be dispatched
        by the background dispatcher task.

        Args:
            event: The event to publish.

        Raises:
            RuntimeError: If the bus has not been started.
        """
        if not self._running:
            raise RuntimeError("EventBus is not running. Call start() first.")

        priority = _resolve_priority(event)
        self._sequence += 1
        await self._queue.put((priority, self._sequence, event))
        self._metrics.events_published += 1

        current_depth = self._queue.qsize()
        if current_depth > self._metrics.max_queue_depth:
            self._metrics.max_queue_depth = current_depth

    async def start(self) -> None:
        """Start the background dispatcher task.

        Raises:
            RuntimeError: If the bus is already running.
        """
        if self._running:
            raise RuntimeError("EventBus is already running.")
        self._running = True
        self._dispatcher_task = asyncio.create_task(self._dispatch_loop())
        logger.info("event_bus_started")

    async def stop(self) -> None:
        """Stop the dispatcher and drain remaining events.

        Waits for all currently queued events to be processed before stopping.
        """
        if not self._running:
            return
        self._running = False

        # Drain remaining events.
        await self._queue.join()

        if self._dispatcher_task is not None:
            self._dispatcher_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._dispatcher_task
            self._dispatcher_task = None

        logger.info(
            "event_bus_stopped",
            metrics={
                "events_published": self._metrics.events_published,
                "events_dispatched": self._metrics.events_dispatched,
                "handler_errors": self._metrics.handler_errors,
            },
        )

    async def _dispatch_loop(self) -> None:
        """Background loop that pulls events from the queue and dispatches them."""
        while self._running:
            try:
                _priority, _seq, event = await asyncio.wait_for(self._queue.get(), timeout=0.1)
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            try:
                await self._dispatch_event(event)
            finally:
                self._queue.task_done()

    async def _dispatch_event(self, event: Event) -> None:
        """Fan out an event to all matching subscribers.

        A subscriber matches if its subscribed type is in the event's MRO.
        This enables wildcard subscriptions: subscribing to Event receives
        everything, subscribing to MarketDataEvent receives all market data, etc.

        Args:
            event: The event to dispatch.
        """
        event_type = type(event)
        handlers: list[EventHandler] = []

        # Collect handlers: walk the MRO of the event type and find all
        # subscriptions that match.
        for cls in event_type.__mro__:
            if cls in self._subscribers:
                for sub in self._subscribers[cls]:
                    handlers.append(sub.handler)

        for handler in handlers:
            start_ns = time.perf_counter_ns()
            try:
                await handler(event)
                self._metrics.events_dispatched += 1
            except Exception:
                self._metrics.handler_errors += 1
                logger.exception(
                    "handler_error",
                    event_type=type(event).__name__,
                    handler=getattr(handler, "__name__", str(handler)),
                )
            finally:
                elapsed_ns = time.perf_counter_ns() - start_ns
                self._metrics.total_dispatch_latency_ns += elapsed_ns

    def subscriber_count(self, event_type: type[Event] | None = None) -> int:
        """Return the number of active subscriptions.

        Args:
            event_type: If provided, count only subscriptions for this type.
                If None, count all subscriptions across all types.

        Returns:
            Number of active subscriptions.
        """
        if event_type is not None:
            return len(self._subscribers.get(event_type, []))
        return sum(len(subs) for subs in self._subscribers.values())
