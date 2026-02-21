"""Tests for sysls.core.exceptions."""

from __future__ import annotations

import pytest

from sysls.core.exceptions import (
    ConfigError,
    ConnectionError,  # noqa: A004
    DataError,
    DataNotFoundError,
    EventBusError,
    OrderError,
    RiskLimitError,
    StrategyError,
    SyslsError,
    VenueError,
)


class TestExceptionHierarchy:
    """Verify the inheritance tree is correct."""

    def test_sysls_error_is_exception(self) -> None:
        assert issubclass(SyslsError, Exception)

    @pytest.mark.parametrize(
        "exc_cls",
        [
            ConfigError,
            VenueError,
            DataError,
            StrategyError,
            RiskLimitError,
            EventBusError,
        ],
    )
    def test_direct_children_of_sysls_error(self, exc_cls: type) -> None:
        assert issubclass(exc_cls, SyslsError)

    def test_order_error_is_venue_error(self) -> None:
        assert issubclass(OrderError, VenueError)

    def test_connection_error_is_venue_error(self) -> None:
        assert issubclass(ConnectionError, VenueError)

    def test_data_not_found_is_data_error(self) -> None:
        assert issubclass(DataNotFoundError, DataError)


class TestVenueError:
    """Verify VenueError stores venue name and formats message."""

    def test_venue_attribute(self) -> None:
        err = VenueError("timeout", venue="binance")
        assert err.venue == "binance"

    def test_message_format(self) -> None:
        err = VenueError("timeout", venue="binance")
        assert str(err) == "[binance] timeout"

    def test_caught_as_sysls_error(self) -> None:
        with pytest.raises(SyslsError):
            raise VenueError("fail", venue="ibkr")


class TestOrderError:
    """Verify OrderError inherits VenueError behaviour."""

    def test_venue_attribute(self) -> None:
        err = OrderError("rejected", venue="tastytrade")
        assert err.venue == "tastytrade"

    def test_caught_as_venue_error(self) -> None:
        with pytest.raises(VenueError):
            raise OrderError("rejected", venue="tastytrade")


class TestRiskLimitError:
    """Verify RiskLimitError stores limit_name and formats message."""

    def test_limit_name_attribute(self) -> None:
        err = RiskLimitError("exceeded", limit_name="max_notional")
        assert err.limit_name == "max_notional"

    def test_message_format(self) -> None:
        err = RiskLimitError("exceeded", limit_name="max_notional")
        assert str(err) == "[max_notional] exceeded"

    def test_caught_as_sysls_error(self) -> None:
        with pytest.raises(SyslsError):
            raise RiskLimitError("exceeded", limit_name="drawdown")
