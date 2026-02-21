"""Tests for the DataConnector ABC and BarTimeframe enum."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd
import pytest

from sysls.core.types import AssetClass, Instrument, Venue
from sysls.data.connector import BarTimeframe, DataConnector

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sysls.core.events import BarEvent, QuoteEvent, TradeEvent

# ---------------------------------------------------------------------------
# Concrete stub for testing the ABC
# ---------------------------------------------------------------------------


class StubConnector(DataConnector):
    """Minimal concrete implementation for testing ABC behaviour."""

    def __init__(self) -> None:
        self._connected = False

    @property
    def name(self) -> str:
        return "stub"

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def get_historical_bars(
        self,
        instrument: Instrument,
        start: datetime,
        end: datetime,
        timeframe: BarTimeframe = BarTimeframe.DAY_1,
    ) -> pd.DataFrame:
        return pd.DataFrame()

    async def get_historical_trades(
        self,
        instrument: Instrument,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        return pd.DataFrame()

    async def get_historical_quotes(
        self,
        instrument: Instrument,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        return pd.DataFrame()

    async def stream_quotes(
        self,
        instruments: list[Instrument],
    ) -> AsyncIterator[QuoteEvent]:
        return  # empty generator
        yield  # make it an async generator  # pragma: no cover

    async def stream_trades(
        self,
        instruments: list[Instrument],
    ) -> AsyncIterator[TradeEvent]:
        return
        yield  # pragma: no cover

    async def stream_bars(
        self,
        instruments: list[Instrument],
        timeframe: BarTimeframe = BarTimeframe.MINUTE_1,
    ) -> AsyncIterator[BarEvent]:
        return
        yield  # pragma: no cover


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def instrument() -> Instrument:
    return Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY, venue=Venue.PAPER)


# ---------------------------------------------------------------------------
# BarTimeframe tests
# ---------------------------------------------------------------------------


class TestBarTimeframe:
    """Tests for the BarTimeframe enum."""

    def test_all_values_are_strings(self) -> None:
        for tf in BarTimeframe:
            assert isinstance(tf, str)
            assert isinstance(tf.value, str)

    def test_common_timeframes_exist(self) -> None:
        assert BarTimeframe.MINUTE_1 == "1min"
        assert BarTimeframe.HOUR_1 == "1h"
        assert BarTimeframe.DAY_1 == "1d"

    def test_from_string(self) -> None:
        assert BarTimeframe("1d") is BarTimeframe.DAY_1
        assert BarTimeframe("1min") is BarTimeframe.MINUTE_1


# ---------------------------------------------------------------------------
# DataConnector ABC tests
# ---------------------------------------------------------------------------


class TestDataConnectorABC:
    """Tests for the DataConnector abstract base class."""

    def test_cannot_instantiate_abc_directly(self) -> None:
        with pytest.raises(TypeError):
            DataConnector()  # type: ignore[abstract]

    def test_stub_instantiation(self) -> None:
        connector = StubConnector()
        assert connector.name == "stub"
        assert not connector.is_connected

    @pytest.mark.asyncio
    async def test_connect_disconnect(self) -> None:
        connector = StubConnector()
        assert not connector.is_connected

        await connector.connect()
        assert connector.is_connected

        await connector.disconnect()
        assert not connector.is_connected

    @pytest.mark.asyncio
    async def test_async_context_manager(self) -> None:
        connector = StubConnector()
        assert not connector.is_connected

        async with connector:
            assert connector.is_connected

        assert not connector.is_connected

    @pytest.mark.asyncio
    async def test_historical_bars_returns_dataframe(self, instrument: Instrument) -> None:
        async with StubConnector() as conn:
            result = await conn.get_historical_bars(
                instrument,
                start=datetime(2024, 1, 1),
                end=datetime(2024, 12, 31),
            )
            assert isinstance(result, pd.DataFrame)

    @pytest.mark.asyncio
    async def test_stream_quotes_is_async_iterable(self, instrument: Instrument) -> None:
        async with StubConnector() as conn:
            events = []
            async for event in conn.stream_quotes([instrument]):
                events.append(event)
            assert events == []
