"""Layered configuration for sysls.

Configuration priority (highest wins):
    1. Constructor kwargs (CLI flags)
    2. Environment variables (``SYSLS_`` prefix)
    3. YAML config file
    4. Model defaults

Usage::

    # Load from defaults + env + default config.yaml (if it exists)
    cfg = load_config()

    # Load with explicit YAML path
    cfg = load_config(yaml_path="custom.yaml")

    # Override individual values (simulates CLI flags)
    cfg = load_config(mode="backtest")
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from sysls.core.exceptions import ConfigError

# ---------------------------------------------------------------------------
# Sub-models (nested config sections)
# ---------------------------------------------------------------------------


class LoggingConfig(BaseModel):
    """Logging configuration."""

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    json_output: bool = True


class VenueConfig(BaseModel):
    """Configuration for a single venue connection."""

    name: str
    enabled: bool = True
    paper: bool = False
    api_key: str = ""
    api_secret: str = ""
    extra: dict[str, str] = Field(default_factory=dict)


class DataConfig(BaseModel):
    """Data layer configuration."""

    default_provider: str = "polygon"
    arctic_uri: str = "lmdb://data/arctic"


class RiskConfig(BaseModel):
    """Global risk limits."""

    max_position_notional: float = 100_000.0
    max_total_notional: float = 500_000.0
    max_drawdown_pct: float = 5.0


# ---------------------------------------------------------------------------
# Root config
# ---------------------------------------------------------------------------


class SyslsConfig(BaseSettings):
    """Root configuration for the sysls framework.

    Supports layered configuration: defaults, YAML file, environment
    variables (prefixed ``SYSLS_``), and constructor kwargs.

    Use :func:`load_config` to instantiate with a custom YAML path.
    Direct instantiation reads from ``config.yaml`` in the working directory
    (if it exists).
    """

    model_config = SettingsConfigDict(
        env_prefix="SYSLS_",
        env_nested_delimiter="__",
        yaml_file="config.yaml",
        extra="ignore",
    )

    # -- Top-level settings -------------------------------------------------
    mode: Literal["live", "paper", "backtest"] = "paper"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # -- Nested sections ----------------------------------------------------
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    venues: list[VenueConfig] = Field(default_factory=list)

    # -----------------------------------------------------------------------
    # Source priority
    # -----------------------------------------------------------------------

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Set source priority: init > env > yaml > defaults."""
        return (
            init_settings,
            env_settings,
            YamlConfigSettingsSource(settings_cls),
        )


def load_config(
    yaml_path: Path | str | None = None,
    **overrides: object,
) -> SyslsConfig:
    """Load configuration with an optional explicit YAML path.

    Creates a temporary ``SyslsConfig`` subclass pointing at *yaml_path*
    so the YAML source reads the correct file.

    Args:
        yaml_path: Path to a YAML config file. When ``None`` the default
            ``config.yaml`` in the working directory is used (if it exists).
        **overrides: Key-value overrides applied with highest priority
            (equivalent to CLI flags).

    Returns:
        A fully resolved ``SyslsConfig`` instance.

    Raises:
        ConfigError: If the configuration cannot be loaded.
    """
    try:
        if yaml_path is not None:
            # Build a one-off subclass with the right yaml_file path.
            yaml_str = str(yaml_path)

            class _WithYaml(SyslsConfig):
                model_config = SettingsConfigDict(
                    env_prefix="SYSLS_",
                    env_nested_delimiter="__",
                    yaml_file=yaml_str,
                    extra="ignore",
                )

            return _WithYaml(**overrides)  # type: ignore[arg-type]
        return SyslsConfig(**overrides)  # type: ignore[arg-type]
    except Exception as exc:
        raise ConfigError(f"Failed to load configuration: {exc}") from exc
