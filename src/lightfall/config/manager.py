"""Lightfall's ConfigManager: lightfall-utils machinery bound to the LFConfig schema."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lightfall.config.schema import LFConfig
from lightfall_utils.config.layers import ConfigLayer
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

    def user_preferences(self) -> dict[str, Any]:
        """Preferences the user saved: the user config layer only, without defaults."""
        layer = self._layered.get_layer("user")
        prefs = layer.get("preferences", {}) if layer is not None else {}
        return dict(prefs) if isinstance(prefs, dict) else {}

    def add_layer(self, layer: ConfigLayer) -> None:
        """Add or replace a config layer (by name) and invalidate the cached model."""
        self._layered.add_layer(layer)
        self._model = None

    @property
    def model(self) -> LFConfig:
        """The validated configuration model, typed as LFConfig."""
        return super().model  # type: ignore[return-value]
