"""Normalized market data schemas and conversion utilities.

Defines the canonical DataFrame column schemas that all
:class:`~sysls.data.connector.DataConnector` implementations must
produce, and provides conversion functions between DataFrames and the
typed events in :mod:`sysls.core.events`.

DataFrame conventions:

* **Index**: ``DatetimeIndex`` named ``"timestamp"`` in UTC.
* **Prices / quantities**: ``float64`` for vectorized performance.
  Use the event conversion helpers when ``Decimal`` precision is
  required on the execution path.
* **Side column**: string values ``"BUY"``, ``"SELL"``, or ``""``
  (empty string when unknown).
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from sysls.core.events import BarEvent, QuoteEvent, TradeEvent
from sysls.core.types import Side

if TYPE_CHECKING:
    from sysls.core.types import Instrument

# ---------------------------------------------------------------------------
# Canonical column schemas
# ---------------------------------------------------------------------------

BAR_DTYPES: dict[str, np.dtype] = {
    "open": np.dtype("float64"),
    "high": np.dtype("float64"),
    "low": np.dtype("float64"),
    "close": np.dtype("float64"),
    "volume": np.dtype("float64"),
    "vwap": np.dtype("float64"),
    "trade_count": np.dtype("int64"),
}
"""Column name → dtype mapping for normalized bar DataFrames."""

BAR_COLUMNS: list[str] = list(BAR_DTYPES.keys())
"""Ordered column names for bar DataFrames."""

TRADE_DTYPES: dict[str, np.dtype] = {
    "price": np.dtype("float64"),
    "size": np.dtype("float64"),
    "side": np.dtype("object"),  # "BUY", "SELL", or ""
}
"""Column name → dtype mapping for normalized trade DataFrames."""

TRADE_COLUMNS: list[str] = list(TRADE_DTYPES.keys())
"""Ordered column names for trade DataFrames."""

QUOTE_DTYPES: dict[str, np.dtype] = {
    "bid_price": np.dtype("float64"),
    "bid_size": np.dtype("float64"),
    "ask_price": np.dtype("float64"),
    "ask_size": np.dtype("float64"),
}
"""Column name → dtype mapping for normalized quote DataFrames."""

QUOTE_COLUMNS: list[str] = list(QUOTE_DTYPES.keys())
"""Ordered column names for quote DataFrames."""


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def validate_bar_dataframe(df: pd.DataFrame) -> None:
    """Validate that *df* conforms to the canonical bar schema.

    Checks that all required columns are present and the index is a
    ``DatetimeIndex``.

    Args:
        df: DataFrame to validate.

    Raises:
        ValueError: If the schema is invalid.
    """
    _validate_schema(df, BAR_COLUMNS, "bar")


def validate_trade_dataframe(df: pd.DataFrame) -> None:
    """Validate that *df* conforms to the canonical trade schema.

    Args:
        df: DataFrame to validate.

    Raises:
        ValueError: If the schema is invalid.
    """
    _validate_schema(df, TRADE_COLUMNS, "trade")


def validate_quote_dataframe(df: pd.DataFrame) -> None:
    """Validate that *df* conforms to the canonical quote schema.

    Args:
        df: DataFrame to validate.

    Raises:
        ValueError: If the schema is invalid.
    """
    _validate_schema(df, QUOTE_COLUMNS, "quote")


def _validate_schema(
    df: pd.DataFrame,
    required_columns: list[str],
    label: str,
) -> None:
    """Shared validation logic for all data schemas."""
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError(
            f"{label} DataFrame must have a DatetimeIndex, got {type(df.index).__name__}"
        )
    missing = set(required_columns) - set(df.columns)
    if missing:
        raise ValueError(f"{label} DataFrame missing required columns: {sorted(missing)}")


# ---------------------------------------------------------------------------
# DataFrame → Event conversion
# ---------------------------------------------------------------------------


def bars_to_events(
    df: pd.DataFrame,
    instrument: Instrument,
    *,
    source: str | None = None,
) -> list[BarEvent]:
    """Convert a normalized bar DataFrame to a list of BarEvents.

    Args:
        df: DataFrame conforming to the bar schema.
        instrument: Instrument to attach to each event.
        source: Optional source identifier for the events.

    Returns:
        One :class:`BarEvent` per row, ordered by timestamp.

    Raises:
        ValueError: If the DataFrame does not match the bar schema.
    """
    validate_bar_dataframe(df)
    events: list[BarEvent] = []
    for ts, row in df.iterrows():
        ts_ns = int(pd.Timestamp(ts).value)  # nanoseconds since epoch
        events.append(
            BarEvent(
                instrument=instrument,
                open=Decimal(str(row["open"])),
                high=Decimal(str(row["high"])),
                low=Decimal(str(row["low"])),
                close=Decimal(str(row["close"])),
                volume=Decimal(str(row["volume"])),
                bar_start_ns=ts_ns,
                bar_end_ns=ts_ns,  # single-point; caller may adjust
                timestamp_ns=ts_ns,
                source=source,
            )
        )
    return events


def trades_to_events(
    df: pd.DataFrame,
    instrument: Instrument,
    *,
    source: str | None = None,
) -> list[TradeEvent]:
    """Convert a normalized trade DataFrame to a list of TradeEvents.

    Args:
        df: DataFrame conforming to the trade schema.
        instrument: Instrument to attach to each event.
        source: Optional source identifier for the events.

    Returns:
        One :class:`TradeEvent` per row, ordered by timestamp.

    Raises:
        ValueError: If the DataFrame does not match the trade schema.
    """
    validate_trade_dataframe(df)
    events: list[TradeEvent] = []
    for ts, row in df.iterrows():
        ts_ns = int(pd.Timestamp(ts).value)
        side_str = row["side"]
        side: Side | None = None
        if side_str == "BUY":
            side = Side.BUY
        elif side_str == "SELL":
            side = Side.SELL

        events.append(
            TradeEvent(
                instrument=instrument,
                price=Decimal(str(row["price"])),
                size=Decimal(str(row["size"])),
                side=side,
                timestamp_ns=ts_ns,
                source=source,
            )
        )
    return events


def quotes_to_events(
    df: pd.DataFrame,
    instrument: Instrument,
    *,
    source: str | None = None,
) -> list[QuoteEvent]:
    """Convert a normalized quote DataFrame to a list of QuoteEvents.

    Args:
        df: DataFrame conforming to the quote schema.
        instrument: Instrument to attach to each event.
        source: Optional source identifier for the events.

    Returns:
        One :class:`QuoteEvent` per row, ordered by timestamp.

    Raises:
        ValueError: If the DataFrame does not match the quote schema.
    """
    validate_quote_dataframe(df)
    events: list[QuoteEvent] = []
    for ts, row in df.iterrows():
        ts_ns = int(pd.Timestamp(ts).value)
        events.append(
            QuoteEvent(
                instrument=instrument,
                bid_price=Decimal(str(row["bid_price"])),
                bid_size=Decimal(str(row["bid_size"])),
                ask_price=Decimal(str(row["ask_price"])),
                ask_size=Decimal(str(row["ask_size"])),
                timestamp_ns=ts_ns,
                source=source,
            )
        )
    return events
