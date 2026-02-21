"""Tests for sysls.core.config."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml

if TYPE_CHECKING:
    from pathlib import Path

from sysls.core.config import (
    DataConfig,
    LoggingConfig,
    RiskConfig,
    SyslsConfig,
    VenueConfig,
    load_config,
)
from sysls.core.exceptions import ConfigError


class TestDefaults:
    """Verify defaults are sensible without any config file or env vars."""

    def test_default_mode(self) -> None:
        cfg = load_config()
        assert cfg.mode == "paper"

    def test_default_log_level(self) -> None:
        cfg = load_config()
        assert cfg.log_level == "INFO"

    def test_default_logging(self) -> None:
        cfg = load_config()
        assert cfg.logging.level == "INFO"
        assert cfg.logging.json_output is True

    def test_default_data(self) -> None:
        cfg = load_config()
        assert cfg.data.default_provider == "polygon"

    def test_default_risk(self) -> None:
        cfg = load_config()
        assert cfg.risk.max_position_notional == 100_000.0

    def test_default_venues_empty(self) -> None:
        cfg = load_config()
        assert cfg.venues == []


class TestYamlLoading:
    """Verify YAML config files are loaded and parsed."""

    def test_load_from_yaml(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(yaml.dump({"mode": "live", "log_level": "DEBUG"}))
        cfg = load_config(yaml_path=yaml_file)
        assert cfg.mode == "live"
        assert cfg.log_level == "DEBUG"

    def test_nested_yaml(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(
            yaml.dump(
                {
                    "logging": {"level": "WARNING", "json_output": False},
                    "data": {"default_provider": "databento"},
                }
            )
        )
        cfg = load_config(yaml_path=yaml_file)
        assert cfg.logging.level == "WARNING"
        assert cfg.logging.json_output is False
        assert cfg.data.default_provider == "databento"

    def test_venues_from_yaml(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "config.yaml"
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
        assert cfg.venues[1].name == "ibkr"
        assert cfg.venues[1].enabled is False

    def test_missing_yaml_uses_defaults(self) -> None:
        cfg = load_config(yaml_path="/tmp/nonexistent_sysls_cfg.yaml")
        assert cfg.mode == "paper"

    def test_risk_from_yaml(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(
            yaml.dump(
                {
                    "risk": {"max_drawdown_pct": 10.0, "max_position_notional": 50_000.0},
                }
            )
        )
        cfg = load_config(yaml_path=yaml_file)
        assert cfg.risk.max_drawdown_pct == 10.0
        assert cfg.risk.max_position_notional == 50_000.0


class TestEnvOverride:
    """Verify environment variables override YAML and defaults."""

    def test_env_overrides_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SYSLS_MODE", "live")
        cfg = load_config()
        assert cfg.mode == "live"

    def test_env_overrides_yaml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(yaml.dump({"mode": "backtest"}))
        monkeypatch.setenv("SYSLS_MODE", "live")
        cfg = load_config(yaml_path=yaml_file)
        assert cfg.mode == "live"

    def test_nested_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SYSLS_LOGGING__LEVEL", "ERROR")
        cfg = load_config()
        assert cfg.logging.level == "ERROR"


class TestInitOverride:
    """Verify constructor kwargs override env and YAML."""

    def test_init_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SYSLS_MODE", "backtest")
        cfg = load_config(mode="live")
        assert cfg.mode == "live"

    def test_init_overrides_yaml(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(yaml.dump({"mode": "backtest"}))
        cfg = load_config(yaml_path=yaml_file, mode="paper")
        assert cfg.mode == "paper"


class TestLoadConfig:
    """Verify the load_config convenience function."""

    def test_load_defaults(self) -> None:
        cfg = load_config()
        assert isinstance(cfg, SyslsConfig)

    def test_load_with_yaml(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(yaml.dump({"mode": "live"}))
        cfg = load_config(yaml_path=yaml_file)
        assert cfg.mode == "live"

    def test_load_with_overrides(self) -> None:
        cfg = load_config(mode="backtest")
        assert cfg.mode == "backtest"

    def test_load_wraps_errors_in_config_error(self) -> None:
        with pytest.raises(ConfigError):
            load_config(mode="invalid_mode")  # type: ignore[arg-type]


class TestSubModels:
    """Verify sub-model validation."""

    def test_logging_config_rejects_bad_level(self) -> None:
        with pytest.raises(ValueError):
            LoggingConfig(level="INVALID")  # type: ignore[arg-type]

    def test_venue_config_requires_name(self) -> None:
        with pytest.raises(ValueError):
            VenueConfig()  # type: ignore[call-arg]

    def test_venue_config_defaults(self) -> None:
        v = VenueConfig(name="test")
        assert v.enabled is True
        assert v.paper is False
        assert v.api_key == ""
        assert v.extra == {}

    def test_risk_config_defaults(self) -> None:
        r = RiskConfig()
        assert r.max_drawdown_pct == 5.0

    def test_data_config_defaults(self) -> None:
        d = DataConfig()
        assert d.arctic_uri == "lmdb://data/arctic"
