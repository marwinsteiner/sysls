"""Unified clock abstraction for sysls.

Strategies and other components use the ``Clock`` protocol to get the current
time and schedule callbacks.  In live mode the clock returns wall-clock time;
in backtest mode it returns simulated time that advances with the data feed.

Usage::

    clock: Clock = LiveClock()          # production
    clock: Clock = SimulatedClock(...)   # backtesting

    now = clock.now()
    clock.schedule(timedelta(seconds=5), my_callback)
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

import structlog

logger = structlog.get_logger(__name__)

# Type alias for timer callbacks
TimerCallback = Callable[[], Awaitable[None]]


@runtime_checkable
class Clock(Protocol):
    """Protocol that all clock implementations must satisfy.

    Using a ``Protocol`` rather than an ABC allows duck-typing: any object
    with the right methods works, which simplifies testing.
    """

    def now(self) -> datetime:
        """Return the current timestamp (always UTC)."""
        ...

    async def schedule(
        self,
        delay: timedelta,
        callback: TimerCallback,
    ) -> None:
        """Schedule *callback* to be called after *delay*."""
        ...

    async def cancel_all(self) -> None:
        """Cancel all pending scheduled callbacks."""
        ...


# ---------------------------------------------------------------------------
# Live clock — real wall-clock time
# ---------------------------------------------------------------------------


class LiveClock:
    """Clock backed by the system wall clock (UTC).

    Scheduled callbacks are dispatched via ``asyncio`` timers on the
    running event loop.
    """

    def __init__(self) -> None:
        self._tasks: list[asyncio.Task[None]] = []

    def now(self) -> datetime:
        """Return the current UTC wall-clock time."""
        return datetime.now(UTC)

    async def schedule(
        self,
        delay: timedelta,
        callback: TimerCallback,
    ) -> None:
        """Schedule *callback* after *delay* using an asyncio task."""

        async def _run() -> None:
            await asyncio.sleep(delay.total_seconds())
            await callback()

        loop = asyncio.get_running_loop()
        task = loop.create_task(_run())
        self._tasks.append(task)

    async def cancel_all(self) -> None:
        """Cancel all pending scheduled callbacks."""
        for task in self._tasks:
            if not task.done():
                task.cancel()
        self._tasks.clear()


# ---------------------------------------------------------------------------
# Simulated clock — for backtesting
# ---------------------------------------------------------------------------


class SimulatedClock:
    """Clock with manually controlled time for backtesting.

    Time starts at *start* and advances only when :meth:`advance_to` or
    :meth:`advance_by` is called.  Scheduled callbacks fire when simulated
    time passes their trigger point.
    """

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("SimulatedClock requires a timezone-aware start datetime")
        self._current: datetime = start
        self._pending: list[tuple[datetime, TimerCallback]] = []

    def now(self) -> datetime:
        """Return the current simulated time."""
        return self._current

    async def schedule(
        self,
        delay: timedelta,
        callback: TimerCallback,
    ) -> None:
        """Schedule *callback* to fire when simulated time passes *now + delay*."""
        trigger_at = self._current + delay
        self._pending.append((trigger_at, callback))
        # Keep sorted by trigger time for efficient processing
        self._pending.sort(key=lambda item: item[0])

    async def cancel_all(self) -> None:
        """Discard all pending scheduled callbacks."""
        self._pending.clear()

    async def advance_to(self, target: datetime) -> None:
        """Advance the clock to *target*, firing any due callbacks in order.

        Args:
            target: The new simulated time. Must be >= current time.

        Raises:
            ValueError: If *target* is before the current simulated time.
        """
        if target < self._current:
            raise ValueError(f"Cannot move clock backwards: {target} < {self._current}")

        # Fire callbacks whose trigger time <= target, in chronological order
        while self._pending and self._pending[0][0] <= target:
            trigger_at, callback = self._pending.pop(0)
            self._current = trigger_at
            try:
                await callback()
            except Exception:
                logger.exception(
                    "timer_callback_error",
                    trigger_at=trigger_at.isoformat(),
                )

        self._current = target

    async def advance_by(self, delta: timedelta) -> None:
        """Advance the clock by *delta*, firing any due callbacks.

        Args:
            delta: Duration to advance. Must be non-negative.
        """
        if delta < timedelta(0):
            raise ValueError(f"Cannot advance by negative delta: {delta}")
        await self.advance_to(self._current + delta)
