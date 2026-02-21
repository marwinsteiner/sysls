"""Signal types and combinators for the sysls strategy framework.

Signals represent trading intentions -- the output of strategy analysis.
They express a directional opinion with a strength/conviction level.
This module provides:

- ``Signal``: Frozen Pydantic model representing a trading signal.
- ``SignalBook``: Mutable container tracking the latest signal per instrument.
- Combinator functions for combining multiple signals (average, majority, weighted).
- Conversion utilities between ``Signal`` models and ``SignalEvent`` bus events.

Example usage::

    signal = Signal(
        instrument=nvda,
        direction=SignalDirection.LONG,
        strength=0.8,
        strategy_id="momentum",
    )

    book = SignalBook(max_age_seconds=300)
    book.update(signal)

    combined = combine_signals_average([sig1, sig2, sig3], instrument=nvda)
"""

from __future__ import annotations

import time

from pydantic import BaseModel, Field, model_validator

from sysls.core.events import SignalDirection, SignalEvent
from sysls.core.types import Instrument  # noqa: TC001


class Signal(BaseModel, frozen=True):
    """A trading signal with instrument, direction, and strength.

    Signals are the output of strategy analysis. They express a directional
    opinion with a strength/conviction level. Multiple signals can be
    combined using the combinator functions.

    Attributes:
        instrument: Target instrument.
        direction: Signal direction (LONG, SHORT, FLAT).
        strength: Signal strength in [-1.0, 1.0]. Positive = long conviction,
            negative = short conviction, 0 = flat/neutral. Values outside this
            range are clamped.
        strategy_id: ID of the strategy that generated this signal.
        timestamp_ns: When the signal was generated (ns since epoch).
        metadata: Optional key-value metadata.
    """

    instrument: Instrument
    direction: SignalDirection
    strength: float = 1.0
    strategy_id: str = ""
    timestamp_ns: int = Field(default_factory=lambda: int(time.time() * 1_000_000_000))
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _clamp_strength(self) -> Signal:
        """Clamp strength to [-1.0, 1.0]."""
        clamped = max(-1.0, min(1.0, self.strength))
        if clamped != self.strength:
            # Use object.__setattr__ because the model is frozen
            object.__setattr__(self, "strength", clamped)
        return self


class SignalBook:
    """Tracks the latest signal per instrument.

    The SignalBook is a mutable container that stores the most recent
    signal for each instrument. It supports iteration, lookups, and
    bulk operations.

    Args:
        max_age_seconds: Optional maximum age in seconds. Signals older
            than this are considered stale and filtered from active signals.
    """

    def __init__(self, max_age_seconds: float | None = None) -> None:
        self._signals: dict[Instrument, Signal] = {}
        self._max_age_seconds = max_age_seconds

    def update(self, signal: Signal) -> None:
        """Update the signal for an instrument (replaces previous).

        Args:
            signal: The new signal to store.
        """
        self._signals[signal.instrument] = signal

    def get(self, instrument: Instrument) -> Signal | None:
        """Get the latest signal for an instrument.

        Args:
            instrument: The instrument to look up.

        Returns:
            The latest signal, or None if no signal exists for this instrument.
        """
        signal = self._signals.get(instrument)
        if signal is not None and self._is_stale(signal):
            return None
        return signal

    def remove(self, instrument: Instrument) -> None:
        """Remove the signal for an instrument.

        Args:
            instrument: The instrument whose signal to remove.
        """
        self._signals.pop(instrument, None)

    def clear(self) -> None:
        """Remove all signals."""
        self._signals.clear()

    @property
    def active_signals(self) -> dict[Instrument, Signal]:
        """All current signals (excluding stale ones if max_age set)."""
        if self._max_age_seconds is None:
            return dict(self._signals)
        return {inst: sig for inst, sig in self._signals.items() if not self._is_stale(sig)}

    @property
    def instruments(self) -> list[Instrument]:
        """Instruments with active signals."""
        return list(self.active_signals.keys())

    def __len__(self) -> int:
        """Number of active signals."""
        return len(self.active_signals)

    def __contains__(self, instrument: Instrument) -> bool:
        """Check if an instrument has an active signal."""
        return self.get(instrument) is not None

    def _is_stale(self, signal: Signal) -> bool:
        """Check if a signal is older than max_age_seconds.

        Args:
            signal: The signal to check.

        Returns:
            True if the signal is stale, False otherwise.
        """
        if self._max_age_seconds is None:
            return False
        now_ns = int(time.time() * 1_000_000_000)
        age_seconds = (now_ns - signal.timestamp_ns) / 1_000_000_000
        return age_seconds > self._max_age_seconds


# ---------------------------------------------------------------------------
# Combinator functions
# ---------------------------------------------------------------------------


def combine_signals_average(signals: list[Signal], instrument: Instrument) -> Signal:
    """Combine signals by averaging their strengths.

    All signals should relate to the same instrument. The resulting direction
    is determined by the sign of the average strength.

    Args:
        signals: List of signals to combine.
        instrument: The target instrument.

    Returns:
        A new Signal with averaged strength.

    Raises:
        ValueError: If signals is empty.
    """
    if not signals:
        raise ValueError("Cannot combine empty list of signals.")

    avg_strength = sum(s.strength for s in signals) / len(signals)
    direction = _direction_from_strength(avg_strength)
    return Signal(
        instrument=instrument,
        direction=direction,
        strength=avg_strength,
    )


def combine_signals_majority(signals: list[Signal], instrument: Instrument) -> Signal:
    """Combine signals by majority vote on direction.

    Counts LONG vs SHORT vs FLAT. The majority direction wins.
    Strength is the proportion of votes for the winning direction.

    Args:
        signals: List of signals to combine.
        instrument: The target instrument.

    Returns:
        A new Signal with majority-voted direction.

    Raises:
        ValueError: If signals is empty.
    """
    if not signals:
        raise ValueError("Cannot combine empty list of signals.")

    counts: dict[SignalDirection, int] = {
        SignalDirection.LONG: 0,
        SignalDirection.SHORT: 0,
        SignalDirection.FLAT: 0,
    }
    for s in signals:
        counts[s.direction] += 1

    # Find the direction(s) with the maximum count
    max_count = max(counts.values())
    # In case of tie, prefer FLAT as the conservative choice
    if counts[SignalDirection.FLAT] == max_count:
        winner = SignalDirection.FLAT
    elif counts[SignalDirection.LONG] == max_count:
        winner = SignalDirection.LONG
    else:
        winner = SignalDirection.SHORT

    strength = max_count / len(signals)
    # Map strength to the appropriate sign
    if winner == SignalDirection.SHORT:
        strength = -strength
    elif winner == SignalDirection.FLAT:
        strength = 0.0

    return Signal(
        instrument=instrument,
        direction=winner,
        strength=strength,
    )


def combine_signals_weighted(
    signals: list[Signal],
    weights: list[float],
    instrument: Instrument,
) -> Signal:
    """Combine signals with explicit weights.

    Computes a weighted average of signal strengths. Weights are
    normalized to sum to 1.

    Args:
        signals: List of signals to combine.
        weights: Weight for each signal (must be same length as signals).
        instrument: The target instrument.

    Returns:
        A new Signal with weighted average strength.

    Raises:
        ValueError: If signals is empty or lengths don't match.
    """
    if not signals:
        raise ValueError("Cannot combine empty list of signals.")
    if len(signals) != len(weights):
        raise ValueError(
            f"signals and weights must have same length: {len(signals)} != {len(weights)}"
        )

    total_weight = sum(weights)
    if total_weight == 0:
        return Signal(
            instrument=instrument,
            direction=SignalDirection.FLAT,
            strength=0.0,
        )

    normalized = [w / total_weight for w in weights]
    weighted_strength = sum(s.strength * w for s, w in zip(signals, normalized, strict=True))
    direction = _direction_from_strength(weighted_strength)

    return Signal(
        instrument=instrument,
        direction=direction,
        strength=weighted_strength,
    )


# ---------------------------------------------------------------------------
# Conversion utilities
# ---------------------------------------------------------------------------


def signal_from_event(event: SignalEvent) -> Signal:
    """Convert a SignalEvent to a Signal model.

    Args:
        event: The SignalEvent to convert.

    Returns:
        A Signal model with the same data.
    """
    return Signal(
        instrument=event.instrument,
        direction=event.direction,
        strength=event.strength,
        strategy_id=event.strategy_id,
        timestamp_ns=event.timestamp_ns,
        metadata=dict(event.metadata),
    )


def signal_to_event(signal: Signal, source: str | None = None) -> SignalEvent:
    """Convert a Signal model to a SignalEvent for bus publishing.

    Args:
        signal: The Signal model to convert.
        source: Optional source identifier for the event.

    Returns:
        A SignalEvent suitable for publishing on the event bus.
    """
    return SignalEvent(
        strategy_id=signal.strategy_id,
        instrument=signal.instrument,
        direction=signal.direction,
        strength=signal.strength,
        metadata=dict(signal.metadata),
        source=source,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _direction_from_strength(strength: float) -> SignalDirection:
    """Determine direction from a numeric strength value.

    Args:
        strength: The strength value.

    Returns:
        LONG if positive, SHORT if negative, FLAT if zero.
    """
    if strength > 0:
        return SignalDirection.LONG
    if strength < 0:
        return SignalDirection.SHORT
    return SignalDirection.FLAT
