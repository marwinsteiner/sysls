"""Tests for sysls.core.config."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
import yaml
from pydantic import ValidationError

from sysls.core.config import (
    DataConfig,
    LoggingConfig,
    RiskConfig,
    SyslsConfig,
    VenueConfig,
    load_config,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestSubModels:
    """Tests for nested configuration sub-models."""

    def test_logging_config_defaults(self) -> None:
        cfg = LoggingConfig()
        assert cfg.level == "INFO"
        assert cfg.json_output is True

    def test_logging_config_custom(self) -> None:
        cfg = LoggingConfig(level="DEBUG", json_output=False)
        assert cfg.level == "DEBUG"
        assert cfg.json_output is False

    def test_logging_config_rejects_invalid_level(self) -> None:
        with pytest.raises(ValidationError):
            LoggingConfig(level="TRACE")  # type: ignore[arg-type]

    def test_venue_config_required_name(self) -> None:
        with pytest.raises(ValidationError):
            VenueConfig()  # type: ignore[call-arg]

    def test_venue_config_defaults(self) -> None:
        cfg = VenueConfig(name="binance")
        assert cfg.name == "binance"
        assert cfg.enabled is True
        assert cfg.paper is False
        assert cfg.api_key == ""
        assert cfg.api_secret == ""
        assert cfg.extra == {}

    def test_venue_config_full(self) -> None:
        cfg = VenueConfig(
            name="ibkr",
            enabled=False,
            paper=True,
            api_key="key123",
            api_secret="secret456",
            extra={"gateway": "localhost:4001"},
        )
        assert cfg.name == "ibkr"
        assert cfg.enabled is False
        assert cfg.paper is True
        assert cfg.extra["gateway"] == "localhost:4001"

    def test_data_config_defaults(self) -> None:
        cfg = DataConfig()
        assert cfg.default_provider == "polygon"
        assert cfg.arctic_uri == "lmdb://data/arctic"

    def test_risk_config_defaults(self) -> None:
        cfg = RiskConfig()
        assert cfg.max_position_notional == 100_000.0
        assert cfg.max_total_notional == 500_000.0
        assert cfg.max_drawdown_pct == 5.0

    def test_risk_config_custom(self) -> None:
        cfg = RiskConfig(
            max_position_notional=50_000.0,
            max_total_notional=200_000.0,
            max_drawdown_pct=2.5,
        )
        assert cfg.max_position_notional == 50_000.0
        assert cfg.max_drawdown_pct == 2.5


class TestSyslsConfig:
    """Tests for the root SyslsConfig settings model."""

    def test_defaults(self) -> None:
        cfg = SyslsConfig()
        assert cfg.mode == "paper"
        assert cfg.log_level == "INFO"
        assert isinstance(cfg.logging, LoggingConfig)
        assert isinstance(cfg.data, DataConfig)
        assert isinstance(cfg.risk, RiskConfig)
        assert cfg.venues == []

    def test_mode_validation(self) -> None:
        cfg = SyslsConfig(mode="live")
        assert cfg.mode == "live"

        cfg = SyslsConfig(mode="backtest")
        assert cfg.mode == "backtest"

    def test_invalid_mode_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SyslsConfig(mode="invalid")  # type: ignore[arg-type]

    def test_invalid_log_level_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SyslsConfig(log_level="VERBOSE")  # type: ignore[arg-type]

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SYSLS_MODE", "live")
        cfg = SyslsConfig()
        assert cfg.mode == "live"

    def test_env_nested_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SYSLS_DATA__DEFAULT_PROVIDER", "databento")
        cfg = SyslsConfig()
        assert cfg.data.default_provider == "databento"

    def test_init_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SYSLS_MODE", "live")
        cfg = SyslsConfig(mode="backtest")
        assert cfg.mode == "backtest"

    def test_extra_fields_ignored(self) -> None:
        cfg = SyslsConfig(nonexistent_field="should_be_ignored")  # type: ignore[call-arg]
        assert not hasattr(cfg, "nonexistent_field")

    def test_venues_list(self) -> None:
        venues = [
            VenueConfig(name="binance"),
            VenueConfig(name="ibkr", paper=True),
        ]
        cfg = SyslsConfig(venues=venues)
        assert len(cfg.venues) == 2
        assert cfg.venues[0].name == "binance"
        assert cfg.venues[1].paper is True


class TestLoadConfig:
    """Tests for the load_config helper function."""

    def test_load_defaults(self) -> None:
        cfg = load_config()
        assert isinstance(cfg, SyslsConfig)
        assert cfg.mode == "paper"

    def test_load_with_overrides(self) -> None:
        cfg = load_config(mode="backtest", log_level="DEBUG")
        assert cfg.mode == "backtest"
        assert cfg.log_level == "DEBUG"

    def test_load_from_yaml(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "test_config.yaml"
        yaml_file.write_text(
            yaml.dump(
                {
                    "mode": "live",
                    "log_level": "WARNING",
                    "data": {"default_provider": "databento"},
                    "risk": {"max_drawdown_pct": 3.0},
                }
            )
        )
        cfg = load_config(yaml_path=yaml_file)
        assert cfg.mode == "live"
        assert cfg.log_level == "WARNING"
        assert cfg.data.default_provider == "databento"
        assert cfg.risk.max_drawdown_pct == 3.0

    def test_load_from_yaml_with_venues(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "venues.yaml"
        yaml_file.write_text(
            yaml.dump(
                {
                    "venues": [
                        {"name": "binance", "paper": True},
                        {"name": "ibkr", "enabled": False},
                    ],
                }
            )
        )
        cfg = load_config(yaml_path=yaml_file)
        assert len(cfg.venues) == 2
        assert cfg.venues[0].name == "binance"
        assert cfg.venues[0].paper is True
        assert cfg.venues[1].enabled is False

    def test_load_yaml_with_overrides(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "base.yaml"
        yaml_file.write_text(yaml.dump({"mode": "live"}))
        cfg = load_config(yaml_path=yaml_file, mode="backtest")
        # init overrides should win over YAML
        assert cfg.mode == "backtest"

    def test_load_yaml_with_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        yaml_file = tmp_path / "env_test.yaml"
        yaml_file.write_text(yaml.dump({"mode": "live"}))
        monkeypatch.setenv("SYSLS_LOG_LEVEL", "ERROR")
        cfg = load_config(yaml_path=yaml_file)
        assert cfg.mode == "live"
        assert cfg.log_level == "ERROR"

    def test_load_nonexistent_yaml_graceful(self, tmp_path: Path) -> None:
        # Pointing at a nonexistent file should still work — YAML source
        # simply finds nothing and defaults apply.
        cfg = load_config(yaml_path=tmp_path / "does_not_exist.yaml")
        assert cfg.mode == "paper"

    def test_load_config_str_path(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "str_path.yaml"
        yaml_file.write_text(yaml.dump({"mode": "backtest"}))
        cfg = load_config(yaml_path=str(yaml_file))
        assert cfg.mode == "backtest"

    def test_env_vars_cleaned_between_tests(self) -> None:
        # Ensure SYSLS_ env vars from other tests don't leak
        assert os.environ.get("SYSLS_MODE") is None
