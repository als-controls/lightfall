"""Layered configuration system for NCS.

Implements a priority-based configuration system where settings from
different sources are merged, with higher-priority layers overriding
lower-priority ones.

Priority order (lowest to highest):
    Defaults -> Global -> Beamline -> User -> Session
"""

from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any

import yaml

from lucid.utils.logging import logger


class ConfigPriority(IntEnum):
    """Configuration layer priorities."""

    DEFAULTS = 0
    GLOBAL = 10
    BEAMLINE = 20
    USER = 30
    SESSION = 40


@dataclass
class ConfigLayer:
    """
    A single configuration layer.

    Attributes:
        name: Human-readable layer name.
        priority: Layer priority (higher overrides lower).
        data: Configuration data dictionary.
        source: Optional path or identifier for the data source.
        mutable: Whether this layer can be modified at runtime.
    """

    name: str
    priority: ConfigPriority | int
    data: dict[str, Any] = field(default_factory=dict)
    source: Path | str | None = None
    mutable: bool = False

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value from this layer using dot notation.

        Args:
            key: Dot-separated key path (e.g., "ui.theme").
            default: Default value if key not found.

        Returns:
            The value or default.
        """
        parts = key.split(".")
        value = self.data
        for part in parts:
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                return default
        return value

    def set(self, key: str, value: Any) -> None:
        """Set a value in this layer using dot notation.

        Args:
            key: Dot-separated key path.
            value: Value to set.

        Raises:
            RuntimeError: If layer is not mutable.
        """
        if not self.mutable:
            raise RuntimeError(f"Cannot modify immutable layer '{self.name}'")

        parts = key.split(".")
        data = self.data
        for part in parts[:-1]:
            if part not in data:
                data[part] = {}
            data = data[part]
        data[parts[-1]] = value

    @classmethod
    def from_file(
        cls,
        path: Path | str,
        name: str | None = None,
        priority: ConfigPriority | int = ConfigPriority.USER,
        mutable: bool = False,
    ) -> ConfigLayer:
        """
        Create a config layer from a YAML file.

        Args:
            path: Path to YAML configuration file.
            name: Layer name (defaults to filename).
            priority: Layer priority.
            mutable: Whether layer is mutable.

        Returns:
            A new ConfigLayer instance.
        """
        path = Path(path)
        layer_name = name or path.stem

        if not path.exists():
            logger.debug("Config file not found: {}", path)
            return cls(name=layer_name, priority=priority, source=path, mutable=mutable)

        try:
            with path.open() as f:
                data = yaml.safe_load(f) or {}
            logger.debug("Loaded config layer '{}' from {}", layer_name, path)
            return cls(
                name=layer_name,
                priority=priority,
                data=data,
                source=path,
                mutable=mutable,
            )
        except Exception as e:
            logger.warning("Failed to load config from {}: {}", path, e)
            return cls(name=layer_name, priority=priority, source=path, mutable=mutable)

    def save(self) -> None:
        """Save this layer to its source file.

        Raises:
            RuntimeError: If no file source is set.
        """
        if not isinstance(self.source, Path):
            raise RuntimeError(f"Cannot save layer '{self.name}': no file source")

        self.source.parent.mkdir(parents=True, exist_ok=True)
        with self.source.open("w") as f:
            yaml.safe_dump(self.data, f, default_flow_style=False, sort_keys=False)
        logger.debug("Saved config layer '{}' to {}", self.name, self.source)


class LayeredConfig:
    """
    Manages multiple configuration layers with priority-based merging.

    Configuration values from higher-priority layers override those from
    lower-priority layers. Deep merging is performed for nested dictionaries.

    Example:
        >>> config = LayeredConfig()
        >>> config.add_layer(ConfigLayer("defaults", ConfigPriority.DEFAULTS, {"ui": {"theme": "light"}}))
        >>> config.add_layer(ConfigLayer("user", ConfigPriority.USER, {"ui": {"theme": "dark"}}))
        >>> config.get("ui.theme")  # Returns "dark"
    """

    def __init__(self) -> None:
        self._layers: list[ConfigLayer] = []
        self._merged: dict[str, Any] = {}
        self._dirty = True

    def add_layer(self, layer: ConfigLayer) -> None:
        """
        Add a configuration layer.

        Args:
            layer: The layer to add.
        """
        # Remove existing layer with same name
        self._layers = [lyr for lyr in self._layers if lyr.name != layer.name]
        self._layers.append(layer)
        self._layers.sort(key=lambda lyr: lyr.priority)
        self._dirty = True
        logger.debug(
            "Added config layer '{}' at priority {}",
            layer.name,
            layer.priority,
        )

    def remove_layer(self, name: str) -> bool:
        """
        Remove a layer by name.

        Args:
            name: Name of the layer to remove.

        Returns:
            True if layer was found and removed.
        """
        original_count = len(self._layers)
        self._layers = [lyr for lyr in self._layers if lyr.name != name]
        if len(self._layers) < original_count:
            self._dirty = True
            logger.debug("Removed config layer '{}'", name)
            return True
        return False

    def get_layer(self, name: str) -> ConfigLayer | None:
        """
        Get a layer by name.

        Args:
            name: Layer name.

        Returns:
            The layer or None if not found.
        """
        for layer in self._layers:
            if layer.name == name:
                return layer
        return None

    def layers(self) -> Iterator[ConfigLayer]:
        """Iterate over layers in priority order (lowest first)."""
        yield from self._layers

    def _merge(self) -> dict[str, Any]:
        """Merge all layers into a single configuration dict."""
        if not self._dirty:
            return self._merged

        result: dict[str, Any] = {}
        for layer in self._layers:
            result = self._deep_merge(result, layer.data)

        self._merged = result
        self._dirty = False
        return result

    @staticmethod
    def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        """Deep merge two dictionaries.

        Args:
            base: Base dictionary.
            override: Override dictionary (takes precedence).

        Returns:
            Merged dictionary.
        """
        result = deepcopy(base)
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = LayeredConfig._deep_merge(result[key], value)
            else:
                result[key] = deepcopy(value)
        return result

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a configuration value using dot notation.

        Args:
            key: Dot-separated key path (e.g., "ui.theme").
            default: Default value if key not found.

        Returns:
            The configuration value or default.
        """
        merged = self._merge()
        parts = key.split(".")
        value = merged
        for part in parts:
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                return default
        return value

    def set(self, key: str, value: Any, *, layer_name: str | None = None) -> None:
        """
        Set a configuration value.

        If no layer is specified, uses the highest-priority mutable layer.

        Args:
            key: Dot-separated key path.
            value: Value to set.
            layer_name: Target layer name (optional).

        Raises:
            RuntimeError: If no mutable layer is available.
        """
        target_layer: ConfigLayer | None = None

        if layer_name:
            target_layer = self.get_layer(layer_name)
            if target_layer is None:
                raise RuntimeError(f"Layer '{layer_name}' not found")
        else:
            # Find highest-priority mutable layer
            for layer in reversed(self._layers):
                if layer.mutable:
                    target_layer = layer
                    break

        if target_layer is None:
            raise RuntimeError("No mutable configuration layer available")

        target_layer.set(key, value)
        self._dirty = True

    def as_dict(self) -> dict[str, Any]:
        """Return the merged configuration as a dictionary."""
        return deepcopy(self._merge())

    def mark_dirty(self) -> None:
        """Mark the configuration as needing re-merge."""
        self._dirty = True
