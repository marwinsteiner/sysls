"""Tests for sysls.strategy.signal module."""

from __future__ import annotations

import time

import pytest

from sysls.core.events import SignalDirection, SignalEvent
from sysls.core.types import AssetClass, Instrument, Venue
from sysls.strategy.signal import (
    Signal,
    SignalBook,
    combine_signals_average,
    combine_signals_majority,
    combine_signals_weighted,
    signal_from_event,
    signal_to_event,
)

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def instrument() -> Instrument:
    """Provide a standard test instrument."""
    return Instrument(
        symbol="NVDA",
        asset_class=AssetClass.EQUITY,
        venue=Venue.TASTYTRADE,
    )


@pytest.fixture()
def instrument_btc() -> Instrument:
    """Provide a second test instrument."""
    return Instrument(
        symbol="BTC-USDT-PERP",
        asset_class=AssetClass.CRYPTO_PERP,
        venue=Venue.CCXT,
        exchange="binance",
        currency="USDT",
    )


# ---------------------------------------------------------------------------
# Signal model tests
# ---------------------------------------------------------------------------


class TestSignal:
    """Tests for the Signal model."""

    def test_signal_creation(self, instrument: Instrument) -> None:
        """Signal can be created with basic fields."""
        sig = Signal(
            instrument=instrument,
            direction=SignalDirection.LONG,
            strength=0.8,
            strategy_id="momentum",
        )
        assert sig.instrument == instrument
        assert sig.direction == SignalDirection.LONG
        assert sig.strength == 0.8
        assert sig.strategy_id == "momentum"
        assert sig.timestamp_ns > 0

    def test_signal_strength_clamped_above(self, instrument: Instrument) -> None:
        """Strength values above 1.0 are clamped to 1.0."""
        sig = Signal(
            instrument=instrument,
            direction=SignalDirection.LONG,
            strength=5.0,
        )
        assert sig.strength == 1.0

    def test_signal_strength_clamped_below(self, instrument: Instrument) -> None:
        """Strength values below -1.0 are clamped to -1.0."""
        sig = Signal(
            instrument=instrument,
            direction=SignalDirection.SHORT,
            strength=-3.0,
        )
        assert sig.strength == -1.0

    def test_signal_strength_within_range_unchanged(self, instrument: Instrument) -> None:
        """Strength values within [-1.0, 1.0] are not modified."""
        sig = Signal(
            instrument=instrument,
            direction=SignalDirection.LONG,
            strength=0.5,
        )
        assert sig.strength == 0.5

    def test_signal_frozen(self, instrument: Instrument) -> None:
        """Signal is immutable (frozen)."""
        sig = Signal(
            instrument=instrument,
            direction=SignalDirection.LONG,
            strength=0.5,
        )
        with pytest.raises(Exception):  # noqa: B017
            sig.strength = 0.9  # type: ignore[misc]

    def test_signal_default_metadata_empty(self, instrument: Instrument) -> None:
        """Default metadata is an empty dict."""
        sig = Signal(
            instrument=instrument,
            direction=SignalDirection.FLAT,
        )
        assert sig.metadata == {}

    def test_signal_default_strategy_id_empty(self, instrument: Instrument) -> None:
        """Default strategy_id is an empty string."""
        sig = Signal(
            instrument=instrument,
            direction=SignalDirection.FLAT,
        )
        assert sig.strategy_id == ""


# ---------------------------------------------------------------------------
# SignalBook tests
# ---------------------------------------------------------------------------


class TestSignalBook:
    """Tests for the SignalBook container."""

    def test_signal_book_update_and_get(self, instrument: Instrument) -> None:
        """Can update and retrieve a signal."""
        book = SignalBook()
        sig = Signal(
            instrument=instrument,
            direction=SignalDirection.LONG,
            strength=0.7,
        )
        book.update(sig)
        result = book.get(instrument)
        assert result is sig

    def test_signal_book_update_replaces(self, instrument: Instrument) -> None:
        """Updating a signal for the same instrument replaces the previous one."""
        book = SignalBook()
        sig1 = Signal(
            instrument=instrument,
            direction=SignalDirection.LONG,
            strength=0.5,
        )
        sig2 = Signal(
            instrument=instrument,
            direction=SignalDirection.SHORT,
            strength=-0.8,
        )
        book.update(sig1)
        book.update(sig2)
        result = book.get(instrument)
        assert result is sig2

    def test_signal_book_get_missing_returns_none(self, instrument: Instrument) -> None:
        """Getting a signal for an unknown instrument returns None."""
        book = SignalBook()
        assert book.get(instrument) is None

    def test_signal_book_remove(self, instrument: Instrument) -> None:
        """Can remove a signal for an instrument."""
        book = SignalBook()
        sig = Signal(
            instrument=instrument,
            direction=SignalDirection.LONG,
            strength=0.5,
        )
        book.update(sig)
        book.remove(instrument)
        assert book.get(instrument) is None

    def test_signal_book_remove_missing_no_error(self, instrument: Instrument) -> None:
        """Removing a non-existent signal does not raise."""
        book = SignalBook()
        book.remove(instrument)  # Should not raise

    def test_signal_book_clear(self, instrument: Instrument, instrument_btc: Instrument) -> None:
        """Clear removes all signals."""
        book = SignalBook()
        book.update(Signal(instrument=instrument, direction=SignalDirection.LONG))
        book.update(Signal(instrument=instrument_btc, direction=SignalDirection.SHORT))
        assert len(book) == 2
        book.clear()
        assert len(book) == 0

    def test_signal_book_contains(self, instrument: Instrument) -> None:
        """in-operator checks for active signal presence."""
        book = SignalBook()
        assert instrument not in book
        book.update(Signal(instrument=instrument, direction=SignalDirection.LONG))
        assert instrument in book

    def test_signal_book_len(self, instrument: Instrument, instrument_btc: Instrument) -> None:
        """len returns the number of active signals."""
        book = SignalBook()
        assert len(book) == 0
        book.update(Signal(instrument=instrument, direction=SignalDirection.LONG))
        assert len(book) == 1
        book.update(Signal(instrument=instrument_btc, direction=SignalDirection.SHORT))
        assert len(book) == 2

    def test_signal_book_active_signals_filters_stale(
        self, instrument: Instrument, instrument_btc: Instrument
    ) -> None:
        """Stale signals are filtered from active_signals when max_age is set."""
        book = SignalBook(max_age_seconds=1.0)

        # Create a stale signal (timestamp far in the past)
        stale_ts = int((time.time() - 10) * 1_000_000_000)
        stale_sig = Signal(
            instrument=instrument,
            direction=SignalDirection.LONG,
            strength=0.5,
            timestamp_ns=stale_ts,
        )
        # Create a fresh signal
        fresh_sig = Signal(
            instrument=instrument_btc,
            direction=SignalDirection.SHORT,
            strength=-0.3,
        )

        book.update(stale_sig)
        book.update(fresh_sig)

        active = book.active_signals
        assert instrument not in active
        assert instrument_btc in active
        assert len(book) == 1

    def test_signal_book_instruments(
        self, instrument: Instrument, instrument_btc: Instrument
    ) -> None:
        """instruments property returns list of instruments with active signals."""
        book = SignalBook()
        book.update(Signal(instrument=instrument, direction=SignalDirection.LONG))
        book.update(Signal(instrument=instrument_btc, direction=SignalDirection.SHORT))
        instruments = book.instruments
        assert set(instruments) == {instrument, instrument_btc}

    def test_signal_book_no_max_age_returns_all(self, instrument: Instrument) -> None:
        """Without max_age, all signals are considered active regardless of timestamp."""
        book = SignalBook()
        old_ts = int((time.time() - 100_000) * 1_000_000_000)
        sig = Signal(
            instrument=instrument,
            direction=SignalDirection.LONG,
            strength=0.5,
            timestamp_ns=old_ts,
        )
        book.update(sig)
        assert instrument in book
        assert len(book) == 1


# ---------------------------------------------------------------------------
# Combinator tests
# ---------------------------------------------------------------------------


class TestCombineSignalsAverage:
    """Tests for combine_signals_average."""

    def test_combine_signals_average(self, instrument: Instrument) -> None:
        """Average of same-direction signals gives correct result."""
        signals = [
            Signal(instrument=instrument, direction=SignalDirection.LONG, strength=0.8),
            Signal(instrument=instrument, direction=SignalDirection.LONG, strength=0.6),
            Signal(instrument=instrument, direction=SignalDirection.LONG, strength=0.4),
        ]
        result = combine_signals_average(signals, instrument)
        assert result.direction == SignalDirection.LONG
        assert abs(result.strength - 0.6) < 1e-10

    def test_combine_signals_average_mixed_directions(self, instrument: Instrument) -> None:
        """Average of mixed-direction signals can produce any direction."""
        signals = [
            Signal(instrument=instrument, direction=SignalDirection.LONG, strength=0.5),
            Signal(instrument=instrument, direction=SignalDirection.SHORT, strength=-0.8),
            Signal(instrument=instrument, direction=SignalDirection.LONG, strength=0.1),
        ]
        result = combine_signals_average(signals, instrument)
        # (0.5 + -0.8 + 0.1) / 3 = -0.2 / 3 = -0.0667
        assert result.direction == SignalDirection.SHORT
        assert result.strength < 0

    def test_combine_signals_average_empty_raises(self, instrument: Instrument) -> None:
        """Combining an empty list raises ValueError."""
        with pytest.raises(ValueError, match="empty"):
            combine_signals_average([], instrument)


class TestCombineSignalsMajority:
    """Tests for combine_signals_majority."""

    def test_combine_signals_majority(self, instrument: Instrument) -> None:
        """Majority vote selects the most common direction."""
        signals = [
            Signal(instrument=instrument, direction=SignalDirection.LONG, strength=0.3),
            Signal(instrument=instrument, direction=SignalDirection.LONG, strength=0.5),
            Signal(instrument=instrument, direction=SignalDirection.SHORT, strength=-0.9),
        ]
        result = combine_signals_majority(signals, instrument)
        assert result.direction == SignalDirection.LONG
        # 2 out of 3 voted LONG
        assert abs(result.strength - 2 / 3) < 1e-10

    def test_combine_signals_majority_tie(self, instrument: Instrument) -> None:
        """In a tie, FLAT is preferred as the conservative choice."""
        signals = [
            Signal(instrument=instrument, direction=SignalDirection.LONG, strength=0.5),
            Signal(instrument=instrument, direction=SignalDirection.SHORT, strength=-0.5),
            Signal(instrument=instrument, direction=SignalDirection.FLAT, strength=0.0),
        ]
        result = combine_signals_majority(signals, instrument)
        # All three have count=1, tie -> FLAT preferred
        assert result.direction == SignalDirection.FLAT
        assert result.strength == 0.0

    def test_combine_signals_majority_empty_raises(self, instrument: Instrument) -> None:
        """Combining an empty list raises ValueError."""
        with pytest.raises(ValueError, match="empty"):
            combine_signals_majority([], instrument)

    def test_combine_signals_majority_short_wins(self, instrument: Instrument) -> None:
        """SHORT majority produces negative strength."""
        signals = [
            Signal(instrument=instrument, direction=SignalDirection.SHORT, strength=-0.5),
            Signal(instrument=instrument, direction=SignalDirection.SHORT, strength=-0.8),
            Signal(instrument=instrument, direction=SignalDirection.LONG, strength=0.3),
        ]
        result = combine_signals_majority(signals, instrument)
        assert result.direction == SignalDirection.SHORT
        assert result.strength < 0


class TestCombineSignalsWeighted:
    """Tests for combine_signals_weighted."""

    def test_combine_signals_weighted(self, instrument: Instrument) -> None:
        """Weighted combination uses normalized weights."""
        signals = [
            Signal(instrument=instrument, direction=SignalDirection.LONG, strength=1.0),
            Signal(instrument=instrument, direction=SignalDirection.SHORT, strength=-1.0),
        ]
        # Weight the LONG signal 3x more than SHORT
        result = combine_signals_weighted(signals, [3.0, 1.0], instrument)
        # (1.0 * 0.75) + (-1.0 * 0.25) = 0.5
        assert result.direction == SignalDirection.LONG
        assert abs(result.strength - 0.5) < 1e-10

    def test_combine_signals_weighted_mismatched_lengths_raises(
        self, instrument: Instrument
    ) -> None:
        """Mismatched signals and weights lengths raises ValueError."""
        signals = [
            Signal(instrument=instrument, direction=SignalDirection.LONG, strength=0.5),
        ]
        with pytest.raises(ValueError, match="same length"):
            combine_signals_weighted(signals, [1.0, 2.0], instrument)

    def test_combine_signals_weighted_empty_raises(self, instrument: Instrument) -> None:
        """Combining an empty list raises ValueError."""
        with pytest.raises(ValueError, match="empty"):
            combine_signals_weighted([], [], instrument)

    def test_combine_signals_weighted_zero_weights(self, instrument: Instrument) -> None:
        """All-zero weights produce a FLAT signal."""
        signals = [
            Signal(instrument=instrument, direction=SignalDirection.LONG, strength=1.0),
        ]
        result = combine_signals_weighted(signals, [0.0], instrument)
        assert result.direction == SignalDirection.FLAT
        assert result.strength == 0.0


# ---------------------------------------------------------------------------
# Conversion tests
# ---------------------------------------------------------------------------


class TestSignalConversion:
    """Tests for signal_from_event and signal_to_event."""

    def test_signal_from_event(self, instrument: Instrument) -> None:
        """Can convert a SignalEvent to a Signal model."""
        event = SignalEvent(
            strategy_id="my-strat",
            instrument=instrument,
            direction=SignalDirection.LONG,
            strength=0.75,
            metadata={"key": "value"},
        )
        sig = signal_from_event(event)
        assert sig.instrument == instrument
        assert sig.direction == SignalDirection.LONG
        assert sig.strength == 0.75
        assert sig.strategy_id == "my-strat"
        assert sig.timestamp_ns == event.timestamp_ns
        assert sig.metadata == {"key": "value"}

    def test_signal_to_event(self, instrument: Instrument) -> None:
        """Can convert a Signal model to a SignalEvent."""
        sig = Signal(
            instrument=instrument,
            direction=SignalDirection.SHORT,
            strength=-0.5,
            strategy_id="mean-rev",
            metadata={"indicator": "bollinger"},
        )
        event = signal_to_event(sig, source="test")
        assert isinstance(event, SignalEvent)
        assert event.instrument == instrument
        assert event.direction == SignalDirection.SHORT
        assert event.strength == -0.5
        assert event.strategy_id == "mean-rev"
        assert event.metadata == {"indicator": "bollinger"}
        assert event.source == "test"

    def test_signal_roundtrip(self, instrument: Instrument) -> None:
        """Signal -> SignalEvent -> Signal preserves key fields."""
        original = Signal(
            instrument=instrument,
            direction=SignalDirection.LONG,
            strength=0.9,
            strategy_id="trend",
            metadata={"tf": "1h"},
        )
        event = signal_to_event(original)
        recovered = signal_from_event(event)
        assert recovered.instrument == original.instrument
        assert recovered.direction == original.direction
        assert recovered.strength == original.strength
        assert recovered.strategy_id == original.strategy_id
        assert recovered.metadata == original.metadata
