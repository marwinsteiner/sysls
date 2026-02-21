"""Tests for sysls.core.clock."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from sysls.core.clock import Clock, LiveClock, SimulatedClock

# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestClockProtocol:
    """Verify both clock implementations satisfy the Clock protocol."""

    def test_live_clock_is_clock(self) -> None:
        assert isinstance(LiveClock(), Clock)

    def test_simulated_clock_is_clock(self) -> None:
        start = datetime(2024, 1, 1, tzinfo=UTC)
        assert isinstance(SimulatedClock(start), Clock)


# ---------------------------------------------------------------------------
# LiveClock
# ---------------------------------------------------------------------------


class TestLiveClock:
    """Tests for the wall-clock implementation."""

    def test_now_returns_utc(self) -> None:
        clock = LiveClock()
        now = clock.now()
        assert now.tzinfo == UTC

    def test_now_is_recent(self) -> None:
        clock = LiveClock()
        now = clock.now()
        delta = datetime.now(UTC) - now
        assert delta < timedelta(seconds=1)

    @pytest.mark.asyncio
    async def test_schedule_fires_callback(self) -> None:
        clock = LiveClock()
        called = False

        async def cb() -> None:
            nonlocal called
            called = True

        await clock.schedule(timedelta(milliseconds=50), cb)
        await asyncio.sleep(0.15)
        assert called

    @pytest.mark.asyncio
    async def test_cancel_all(self) -> None:
        clock = LiveClock()
        called = False

        async def cb() -> None:
            nonlocal called
            called = True

        await clock.schedule(timedelta(milliseconds=100), cb)
        await clock.cancel_all()
        await asyncio.sleep(0.2)
        assert not called


# ---------------------------------------------------------------------------
# SimulatedClock
# ---------------------------------------------------------------------------


class TestSimulatedClock:
    """Tests for the backtest clock implementation."""

    def _utc(self, year: int, month: int, day: int, hour: int = 0) -> datetime:
        return datetime(year, month, day, hour, tzinfo=UTC)

    def test_requires_tz_aware_start(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            SimulatedClock(datetime(2024, 1, 1))

    def test_now_returns_start(self) -> None:
        start = self._utc(2024, 1, 1)
        clock = SimulatedClock(start)
        assert clock.now() == start

    @pytest.mark.asyncio
    async def test_advance_to(self) -> None:
        start = self._utc(2024, 1, 1)
        clock = SimulatedClock(start)
        target = self._utc(2024, 1, 2)
        await clock.advance_to(target)
        assert clock.now() == target

    @pytest.mark.asyncio
    async def test_advance_by(self) -> None:
        start = self._utc(2024, 1, 1)
        clock = SimulatedClock(start)
        await clock.advance_by(timedelta(hours=6))
        assert clock.now() == self._utc(2024, 1, 1, 6)

    @pytest.mark.asyncio
    async def test_advance_backwards_raises(self) -> None:
        start = self._utc(2024, 6, 1)
        clock = SimulatedClock(start)
        with pytest.raises(ValueError, match="backwards"):
            await clock.advance_to(self._utc(2024, 5, 1))

    @pytest.mark.asyncio
    async def test_advance_by_negative_raises(self) -> None:
        start = self._utc(2024, 1, 1)
        clock = SimulatedClock(start)
        with pytest.raises(ValueError, match="negative"):
            await clock.advance_by(timedelta(hours=-1))

    @pytest.mark.asyncio
    async def test_scheduled_callback_fires_on_advance(self) -> None:
        start = self._utc(2024, 1, 1)
        clock = SimulatedClock(start)
        fired_at: datetime | None = None

        async def cb() -> None:
            nonlocal fired_at
            fired_at = clock.now()

        await clock.schedule(timedelta(hours=3), cb)
        await clock.advance_to(self._utc(2024, 1, 1, 6))
        assert fired_at == self._utc(2024, 1, 1, 3)

    @pytest.mark.asyncio
    async def test_multiple_callbacks_fire_in_order(self) -> None:
        start = self._utc(2024, 1, 1)
        clock = SimulatedClock(start)
        order: list[int] = []

        async def make_cb(n: int) -> None:
            order.append(n)

        await clock.schedule(timedelta(hours=3), lambda: make_cb(3))
        await clock.schedule(timedelta(hours=1), lambda: make_cb(1))
        await clock.schedule(timedelta(hours=2), lambda: make_cb(2))

        await clock.advance_to(self._utc(2024, 1, 1, 6))
        assert order == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_callback_not_fired_if_not_due(self) -> None:
        start = self._utc(2024, 1, 1)
        clock = SimulatedClock(start)
        called = False

        async def cb() -> None:
            nonlocal called
            called = True

        await clock.schedule(timedelta(hours=10), cb)
        await clock.advance_to(self._utc(2024, 1, 1, 5))
        assert not called
        # Still pending
        assert len(clock._pending) == 1

    @pytest.mark.asyncio
    async def test_cancel_all_clears_pending(self) -> None:
        start = self._utc(2024, 1, 1)
        clock = SimulatedClock(start)

        async def cb() -> None:
            pass

        await clock.schedule(timedelta(hours=1), cb)
        await clock.schedule(timedelta(hours=2), cb)
        assert len(clock._pending) == 2
        await clock.cancel_all()
        assert len(clock._pending) == 0

    @pytest.mark.asyncio
    async def test_callback_error_is_logged_not_raised(self) -> None:
        start = self._utc(2024, 1, 1)
        clock = SimulatedClock(start)

        async def bad_cb() -> None:
            raise RuntimeError("boom")

        await clock.schedule(timedelta(hours=1), bad_cb)
        # Should not raise — error is logged
        await clock.advance_to(self._utc(2024, 1, 1, 2))
        assert clock.now() == self._utc(2024, 1, 1, 2)

    @pytest.mark.asyncio
    async def test_advance_to_same_time_is_noop(self) -> None:
        start = self._utc(2024, 1, 1)
        clock = SimulatedClock(start)
        await clock.advance_to(start)
        assert clock.now() == start

    @pytest.mark.asyncio
    async def test_advance_by_zero(self) -> None:
        start = self._utc(2024, 1, 1)
        clock = SimulatedClock(start)
        await clock.advance_by(timedelta(0))
        assert clock.now() == start
