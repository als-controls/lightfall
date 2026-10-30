"""Lightfall's ConfigManager: lightfall-utils machinery bound to the LFConfig schema."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from lightfall.config.schema import LFConfig
from lightfall.utils.logging import logger
from lightfall_utils.config.layers import ConfigLayer
from lightfall_utils.config.manager import ConfigManager as _BaseConfigManager

# Each pass drops at least one bad value, so this bounds work, not correctness.
_MAX_REPAIR_PASSES = 20


def _drop_path(data: dict[str, Any], loc: tuple[Any, ...]) -> bool:
    """Delete the deepest dict key along pydantic error ``loc`` that exists in ``data``.

    ``loc`` can run past what the data holds (e.g. into a list item or a union
    branch tag); the value removed is then the nearest enclosing dict entry, so its
    field default applies. Returns False if nothing could be removed.
    """
    parent: Any = None
    key: Any = None
    node: Any = data
    for part in loc:
        if isinstance(node, dict) and part in node:
            parent, key, node = node, part, node[part]
        else:
            break
    if parent is None:
        return False
    del parent[key]
    return True


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

    def unset(self, key: str, *, persist: bool = True) -> None:
        """Remove ``key`` from the user layer (``persist``) or the session layer.

        Unlike ``set(key, None)``, nothing is written: lower layers and the schema
        default apply again. Writing ``None`` leaves ``key: null`` in the YAML, which
        fails validation for any non-optional field.
        """
        layer = self._layered.get_layer("user" if persist else "session")
        if layer is None:
            return
        parts = key.split(".")
        node: Any = layer.data
        for part in parts[:-1]:
            if not isinstance(node, dict) or part not in node:
                return
            node = node[part]
        if not isinstance(node, dict) or parts[-1] not in node:
            return
        del node[parts[-1]]
        self._layered.mark_dirty()
        if persist:
            layer.save()
        self._model = None

    def _rebuild_model(self) -> None:
        """Validate the merged config, dropping only the values that fail.

        The base class falls back to an all-defaults model on any validation error,
        so one bad preference silently discarded everything else, including
        ``site.bind``. Here each failing value is removed (its field default applies)
        and the rest is validated again.
        """
        data = self._layered.as_dict()
        self._validation_errors = []
        for _ in range(_MAX_REPAIR_PASSES):
            try:
                self._model = self._model_class.model_validate(data)
                return
            except ValidationError as e:
                errors = e.errors()
            self._validation_errors += [str(err) for err in errors]
            dropped = [err["loc"] for err in errors if _drop_path(data, tuple(err["loc"]))]
            if not dropped:
                break
            logger.warning("Configuration values ignored (invalid; defaults used): {}",
                           [".".join(map(str, loc)) for loc in dropped])
        logger.warning("Configuration validation errors: {}", self._validation_errors)
        self._model = self._model_class()

    def add_layer(self, layer: ConfigLayer) -> None:
        """Add or replace a config layer (by name) and invalidate the cached model."""
        self._layered.add_layer(layer)
        self._model = None

    def reload(self) -> None:
        """Reload every file layer, then re-install the bootstrap layer sanitized.

        The base reload re-reads bootstrap.yaml raw; a hand-edited bad file would come
        back unsanitized, so the bootstrap loader runs again over it.
        """
        super().reload()
        from lightfall.setup.bootstrap import BOOTSTRAP_LAYER, load_bootstrap_layer

        if self._layered.get_layer(BOOTSTRAP_LAYER) is not None:
            load_bootstrap_layer(self)
            self._rebuild_model()

    @property
    def model(self) -> LFConfig:
        """The validated configuration model, typed as LFConfig."""
        return super().model  # type: ignore[return-value]
