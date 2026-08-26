"""Lightfall's ConfigManager: lightfall-utils machinery bound to the LFConfig schema."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lightfall.config.schema import LFConfig
from lightfall_utils.config.manager import ConfigManager as _BaseConfigManager


def _lightfall_defaults_path() -> Path | None:
    """Repo-root config/defaults — present in source checkouts, absent in wheels."""
    candidate = Path(__file__).parent.parent.parent.parent / "config" / "defaults"
    return candidate if candidate.is_dir() else None


class ConfigManager(_BaseConfigManager):
    """ConfigManager preconfigured for Lightfall.

    app_name stays "ncs" so existing user configs (~/.config/ncs,
    %APPDATA%\\ncs, /etc/ncs) keep working.
    """

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("model_class", LFConfig)
        kwargs.setdefault("app_name", "ncs")
        kwargs.setdefault("defaults_path", _lightfall_defaults_path())
        super().__init__(**kwargs)

    @property
    def model(self) -> LFConfig:
        """The validated configuration model, typed as LFConfig."""
        return super().model  # type: ignore[return-value]
