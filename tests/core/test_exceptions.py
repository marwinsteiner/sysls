"""Tests for sysls.core.exceptions."""

from __future__ import annotations

import pytest

from sysls.core import exceptions


class TestSyslsErrorHierarchy:
    """Verify that all exceptions form a proper hierarchy under SyslsError."""

    @pytest.mark.parametrize(
        "exc_cls",
        [
            exceptions.ConfigError,
            exceptions.VenueError,
            exceptions.OrderError,
            exceptions.ConnectionError,
            exceptions.DataError,
            exceptions.DataNotFoundError,
            exceptions.StrategyError,
            exceptions.RiskLimitError,
            exceptions.EventBusError,
        ],
    )
    def test_subclass_of_sysls_error(self, exc_cls: type[exceptions.SyslsError]) -> None:
        assert issubclass(exc_cls, exceptions.SyslsError)

    @pytest.mark.parametrize(
        "exc_cls",
        [
            exceptions.ConfigError,
            exceptions.VenueError,
            exceptions.DataError,
            exceptions.StrategyError,
            exceptions.RiskLimitError,
            exceptions.EventBusError,
        ],
    )
    def test_direct_subclass_of_sysls_error(self, exc_cls: type[exceptions.SyslsError]) -> None:
        assert exceptions.SyslsError in exc_cls.__mro__

    def test_order_error_is_venue_error(self) -> None:
        assert issubclass(exceptions.OrderError, exceptions.VenueError)

    def test_connection_error_is_venue_error(self) -> None:
        assert issubclass(exceptions.ConnectionError, exceptions.VenueError)

    def test_data_not_found_is_data_error(self) -> None:
        assert issubclass(exceptions.DataNotFoundError, exceptions.DataError)


class TestSyslsError:
    """Tests for the base SyslsError."""

    def test_basic_message(self) -> None:
        exc = exceptions.SyslsError("something broke")
        assert str(exc) == "something broke"

    def test_catchable_as_sysls_error(self) -> None:
        with pytest.raises(exceptions.SyslsError):
            raise exceptions.SyslsError("test")

    def test_is_subclass_of_exception(self) -> None:
        assert issubclass(exceptions.SyslsError, Exception)


class TestVenueError:
    """Tests for VenueError and its subclasses."""

    def test_venue_error_message_includes_venue(self) -> None:
        exc = exceptions.VenueError("timeout", venue="binance")
        assert str(exc) == "[binance] timeout"
        assert exc.venue == "binance"

    def test_venue_error_requires_venue_kwarg(self) -> None:
        with pytest.raises(TypeError):
            exceptions.VenueError("msg")  # type: ignore[call-arg]

    def test_order_error_inherits_venue(self) -> None:
        exc = exceptions.OrderError("rejected", venue="ibkr")
        assert exc.venue == "ibkr"
        assert "[ibkr]" in str(exc)

    def test_connection_error_inherits_venue(self) -> None:
        exc = exceptions.ConnectionError("disconnected", venue="tastytrade")
        assert exc.venue == "tastytrade"
        assert "[tastytrade]" in str(exc)

    def test_catch_venue_error_catches_order_error(self) -> None:
        with pytest.raises(exceptions.VenueError):
            raise exceptions.OrderError("fill failed", venue="ccxt")

    def test_catch_venue_error_catches_connection_error(self) -> None:
        with pytest.raises(exceptions.VenueError):
            raise exceptions.ConnectionError("lost", venue="ccxt")

    def test_catch_sysls_error_catches_venue_error(self) -> None:
        with pytest.raises(exceptions.SyslsError):
            raise exceptions.VenueError("err", venue="test")


class TestRiskLimitError:
    """Tests for RiskLimitError."""

    def test_message_includes_limit_name(self) -> None:
        exc = exceptions.RiskLimitError("exceeded", limit_name="max_notional")
        assert str(exc) == "[max_notional] exceeded"
        assert exc.limit_name == "max_notional"

    def test_requires_limit_name_kwarg(self) -> None:
        with pytest.raises(TypeError):
            exceptions.RiskLimitError("msg")  # type: ignore[call-arg]

    def test_catchable_as_sysls_error(self) -> None:
        with pytest.raises(exceptions.SyslsError):
            raise exceptions.RiskLimitError("breach", limit_name="drawdown")


class TestDataErrors:
    """Tests for DataError and DataNotFoundError."""

    def test_data_error_basic(self) -> None:
        exc = exceptions.DataError("connection failed")
        assert str(exc) == "connection failed"

    def test_data_not_found_basic(self) -> None:
        exc = exceptions.DataNotFoundError("AAPL bars not available")
        assert str(exc) == "AAPL bars not available"

    def test_catch_data_error_catches_not_found(self) -> None:
        with pytest.raises(exceptions.DataError):
            raise exceptions.DataNotFoundError("missing")


class TestConfigError:
    """Tests for ConfigError."""

    def test_basic_message(self) -> None:
        exc = exceptions.ConfigError("invalid yaml")
        assert str(exc) == "invalid yaml"


class TestStrategyError:
    """Tests for StrategyError."""

    def test_basic_message(self) -> None:
        exc = exceptions.StrategyError("division by zero in signal")
        assert str(exc) == "division by zero in signal"


class TestEventBusError:
    """Tests for EventBusError."""

    def test_basic_message(self) -> None:
        exc = exceptions.EventBusError("queue overflow")
        assert str(exc) == "queue overflow"
