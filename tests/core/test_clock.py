"""Tests for sysls.core.clock."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone
from typing import TYPE_CHECKING

import pytest

from sysls.core.clock import Clock, LiveClock, SimulatedClock

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)


def _make_counter_callback() -> tuple[list[str], Callable[[], Awaitable[None]]]:
    """Return a (log, callback) pair. Callback appends to log when called."""
    log: list[str] = []

    async def cb() -> None:
        log.append("fired")

    return log, cb  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Clock Protocol
# ---------------------------------------------------------------------------


class TestClockProtocol:
    """Verify that both implementations satisfy the Clock protocol."""

    def test_live_clock_satisfies_protocol(self) -> None:
        assert isinstance(LiveClock(), Clock)

    def test_simulated_clock_satisfies_protocol(self) -> None:
        assert isinstance(SimulatedClock(T0), Clock)


# ---------------------------------------------------------------------------
# LiveClock
# ---------------------------------------------------------------------------


class TestLiveClock:
    """Tests for LiveClock."""

    def test_now_returns_utc(self) -> None:
        clock = LiveClock()
        now = clock.now()
        assert now.tzinfo is not None
        assert now.tzinfo == UTC

    def test_now_is_recent(self) -> None:
        clock = LiveClock()
        now = clock.now()
        diff = abs((datetime.now(UTC) - now).total_seconds())
        assert diff < 1.0

    @pytest.mark.asyncio
    async def test_schedule_fires_callback(self) -> None:
        clock = LiveClock()
        log: list[str] = []

        async def cb() -> None:
            log.append("fired")

        await clock.schedule(timedelta(milliseconds=50), cb)
        await asyncio.sleep(0.15)
        assert log == ["fired"]

    @pytest.mark.asyncio
    async def test_cancel_all_prevents_callback(self) -> None:
        clock = LiveClock()
        log: list[str] = []

        async def cb() -> None:
            log.append("fired")

        await clock.schedule(timedelta(milliseconds=200), cb)
        await clock.cancel_all()
        await asyncio.sleep(0.3)
        assert log == []

    @pytest.mark.asyncio
    async def test_multiple_schedules(self) -> None:
        clock = LiveClock()
        order: list[int] = []

        async def cb1() -> None:
            order.append(1)

        async def cb2() -> None:
            order.append(2)

        await clock.schedule(timedelta(milliseconds=50), cb1)
        await clock.schedule(timedelta(milliseconds=100), cb2)
        await asyncio.sleep(0.2)
        assert order == [1, 2]

    @pytest.mark.asyncio
    async def test_cancel_all_clears_task_list(self) -> None:
        clock = LiveClock()

        async def noop() -> None:
            pass

        await clock.schedule(timedelta(seconds=10), noop)
        assert len(clock._tasks) == 1
        await clock.cancel_all()
        assert len(clock._tasks) == 0


# ---------------------------------------------------------------------------
# SimulatedClock
# ---------------------------------------------------------------------------


class TestSimulatedClock:
    """Tests for SimulatedClock."""

    def test_now_returns_start_time(self) -> None:
        clock = SimulatedClock(T0)
        assert clock.now() == T0

    def test_requires_timezone_aware_start(self) -> None:
        naive = datetime(2024, 1, 1, 12, 0, 0)
        with pytest.raises(ValueError, match="timezone-aware"):
            SimulatedClock(naive)

    def test_accepts_non_utc_timezone(self) -> None:
        eastern = timezone(timedelta(hours=-5))
        start = datetime(2024, 1, 1, 12, 0, 0, tzinfo=eastern)
        clock = SimulatedClock(start)
        assert clock.now() == start

    @pytest.mark.asyncio
    async def test_advance_to_updates_time(self) -> None:
        clock = SimulatedClock(T0)
        target = T0 + timedelta(hours=1)
        await clock.advance_to(target)
        assert clock.now() == target

    @pytest.mark.asyncio
    async def test_advance_by_updates_time(self) -> None:
        clock = SimulatedClock(T0)
        await clock.advance_by(timedelta(minutes=30))
        assert clock.now() == T0 + timedelta(minutes=30)

    @pytest.mark.asyncio
    async def test_advance_to_backwards_raises(self) -> None:
        clock = SimulatedClock(T0)
        past = T0 - timedelta(seconds=1)
        with pytest.raises(ValueError, match="Cannot move clock backwards"):
            await clock.advance_to(past)

    @pytest.mark.asyncio
    async def test_advance_by_negative_raises(self) -> None:
        clock = SimulatedClock(T0)
        with pytest.raises(ValueError, match="Cannot advance by negative"):
            await clock.advance_by(timedelta(seconds=-1))

    @pytest.mark.asyncio
    async def test_advance_to_same_time_is_noop(self) -> None:
        clock = SimulatedClock(T0)
        await clock.advance_to(T0)
        assert clock.now() == T0

    @pytest.mark.asyncio
    async def test_advance_by_zero_is_noop(self) -> None:
        clock = SimulatedClock(T0)
        await clock.advance_by(timedelta(0))
        assert clock.now() == T0

    @pytest.mark.asyncio
    async def test_schedule_fires_on_advance(self) -> None:
        clock = SimulatedClock(T0)
        log, cb = _make_counter_callback()

        await clock.schedule(timedelta(minutes=5), cb)
        assert log == []

        await clock.advance_by(timedelta(minutes=5))
        assert log == ["fired"]

    @pytest.mark.asyncio
    async def test_schedule_fires_in_order(self) -> None:
        clock = SimulatedClock(T0)
        order: list[int] = []

        async def cb1() -> None:
            order.append(1)

        async def cb2() -> None:
            order.append(2)

        async def cb3() -> None:
            order.append(3)

        await clock.schedule(timedelta(minutes=10), cb2)
        await clock.schedule(timedelta(minutes=5), cb1)
        await clock.schedule(timedelta(minutes=15), cb3)

        await clock.advance_by(timedelta(minutes=20))
        assert order == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_schedule_only_fires_due_callbacks(self) -> None:
        clock = SimulatedClock(T0)
        log: list[str] = []

        async def early() -> None:
            log.append("early")

        async def late() -> None:
            log.append("late")

        await clock.schedule(timedelta(minutes=5), early)
        await clock.schedule(timedelta(minutes=30), late)

        await clock.advance_by(timedelta(minutes=10))
        assert log == ["early"]

        await clock.advance_by(timedelta(minutes=25))
        assert log == ["early", "late"]

    @pytest.mark.asyncio
    async def test_cancel_all_clears_pending(self) -> None:
        clock = SimulatedClock(T0)
        log, cb = _make_counter_callback()

        await clock.schedule(timedelta(minutes=5), cb)
        await clock.cancel_all()
        await clock.advance_by(timedelta(minutes=10))
        assert log == []

    @pytest.mark.asyncio
    async def test_callback_error_does_not_stop_others(self) -> None:
        clock = SimulatedClock(T0)
        log: list[str] = []

        async def failing() -> None:
            raise RuntimeError("boom")

        async def succeeding() -> None:
            log.append("ok")

        await clock.schedule(timedelta(minutes=5), failing)
        await clock.schedule(timedelta(minutes=10), succeeding)

        # Should not raise — error is logged, not propagated
        await clock.advance_by(timedelta(minutes=15))
        assert log == ["ok"]

    @pytest.mark.asyncio
    async def test_time_set_to_trigger_during_callback(self) -> None:
        """During callback execution, clock.now() should return the trigger time."""
        clock = SimulatedClock(T0)
        captured: list[datetime] = []

        async def capture_time() -> None:
            captured.append(clock.now())

        trigger_delay = timedelta(minutes=7)
        await clock.schedule(trigger_delay, capture_time)
        await clock.advance_to(T0 + timedelta(minutes=20))

        assert len(captured) == 1
        assert captured[0] == T0 + trigger_delay

    @pytest.mark.asyncio
    async def test_multiple_advances(self) -> None:
        clock = SimulatedClock(T0)
        log: list[str] = []

        async def cb() -> None:
            log.append("fired")

        await clock.schedule(timedelta(hours=1), cb)

        await clock.advance_by(timedelta(minutes=30))
        assert log == []
        assert clock.now() == T0 + timedelta(minutes=30)

        await clock.advance_by(timedelta(minutes=30))
        assert log == ["fired"]
        assert clock.now() == T0 + timedelta(hours=1)

    @pytest.mark.asyncio
    async def test_pending_count(self) -> None:
        clock = SimulatedClock(T0)

        async def noop() -> None:
            pass

        await clock.schedule(timedelta(minutes=1), noop)
        await clock.schedule(timedelta(minutes=2), noop)
        assert len(clock._pending) == 2

        await clock.advance_by(timedelta(minutes=1, seconds=30))
        assert len(clock._pending) == 1

        await clock.advance_by(timedelta(minutes=1))
        assert len(clock._pending) == 0
