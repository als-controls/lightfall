"""Configuration manager for NCS.

Provides high-level configuration management with layered config support,
Pydantic validation, and automatic loading from standard locations.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from lightfall.config.layers import ConfigLayer, ConfigPriority, LayeredConfig
from lightfall.config.schema import NCSConfig
from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from collections.abc import Sequence


def _get_package_config_dir() -> Path:
    """Get the package's bundled config directory."""
    # Go up from src/ncs/config to find config/
    package_root = Path(__file__).parent.parent.parent.parent
    return package_root / "config"


def _get_user_config_dir() -> Path:
    """Get the user's NCS configuration directory."""
    # Use XDG_CONFIG_HOME on Linux, or platform-appropriate location
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "ncs"


def _get_global_config_dir() -> Path:
    """Get the system-wide NCS configuration directory."""
    if os.name == "nt":
        return Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")) / "ncs"
    return Path("/etc/ncs")


class ConfigManager:
    """
    High-level configuration manager for NCS.

    ConfigManager wraps LayeredConfig and provides:
    - Automatic loading from standard locations
    - Pydantic model validation
    - Convenient accessors for common settings
    - Configuration persistence

    Standard Configuration Locations (in priority order):
        1. Package defaults (bundled config/defaults/)
        2. Global config (/etc/ncs/ or %PROGRAMDATA%/ncs/)
        3. User config (~/.config/ncs/ or %APPDATA%/ncs/)
        4. Session overrides (runtime-only)

    Example:
        >>> config = ConfigManager()
        >>> config.get("ui.theme")  # Get with dot notation
        "dark"
        >>> config.model.ui.theme  # Get via Pydantic model
        "dark"
        >>> config.set("ui.theme", "light")  # Set in user layer
    """

    DEFAULT_CONFIG_FILENAME = "application.yaml"

    def __init__(
        self,
        *,
        extra_paths: Sequence[Path | str] | None = None,
        skip_standard_paths: bool = False,
    ) -> None:
        """
        Initialize the ConfigManager.

        Args:
            extra_paths: Additional configuration files to load.
            skip_standard_paths: Skip loading from standard locations
                (useful for testing).
        """
        self._layered = LayeredConfig()
        self._model: NCSConfig | None = None
        self._validation_errors: list[str] = []

        if not skip_standard_paths:
            self._load_standard_layers()

        if extra_paths:
            for i, path in enumerate(extra_paths):
                self._layered.add_layer(
                    ConfigLayer.from_file(
                        Path(path),
                        name=f"extra_{i}",
                        priority=ConfigPriority.USER + 1 + i,
                    )
                )

        # Add session layer (mutable, runtime-only)
        self._layered.add_layer(
            ConfigLayer(
                name="session",
                priority=ConfigPriority.SESSION,
                mutable=True,
            )
        )

        # Initialize model
        self._rebuild_model()

    def _load_standard_layers(self) -> None:
        """Load configuration from standard locations."""
        # 1. Package defaults
        defaults_path = _get_package_config_dir() / "defaults" / self.DEFAULT_CONFIG_FILENAME
        self._layered.add_layer(
            ConfigLayer.from_file(defaults_path, name="defaults", priority=ConfigPriority.DEFAULTS)
        )

        # 2. Global config
        global_path = _get_global_config_dir() / self.DEFAULT_CONFIG_FILENAME
        self._layered.add_layer(
            ConfigLayer.from_file(global_path, name="global", priority=ConfigPriority.GLOBAL)
        )

        # 3. User config
        user_path = _get_user_config_dir() / self.DEFAULT_CONFIG_FILENAME
        self._layered.add_layer(
            ConfigLayer.from_file(
                user_path, name="user", priority=ConfigPriority.USER, mutable=True
            )
        )

    def _rebuild_model(self) -> None:
        """Rebuild the Pydantic model from current configuration."""
        data = self._layered.as_dict()
        self._validation_errors.clear()

        try:
            self._model = NCSConfig.model_validate(data)
        except ValidationError as e:
            self._validation_errors = [str(err) for err in e.errors()]
            logger.warning("Configuration validation errors: {}", self._validation_errors)
            # Fall back to defaults
            self._model = NCSConfig()

    @property
    def model(self) -> NCSConfig:
        """
        Get the validated configuration model.

        Returns:
            The NCSConfig Pydantic model.
        """
        if self._model is None:
            self._rebuild_model()
        return self._model  # type: ignore[return-value]

    @property
    def validation_errors(self) -> list[str]:
        """Get any validation errors from the last model rebuild."""
        return list(self._validation_errors)

    @property
    def layers(self) -> LayeredConfig:
        """Access the underlying LayeredConfig."""
        return self._layered

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a configuration value using dot notation.

        Args:
            key: Dot-separated key path (e.g., "ui.theme").
            default: Default value if key not found.

        Returns:
            The configuration value.
        """
        return self._layered.get(key, default)

    def set(self, key: str, value: Any, *, persist: bool = False) -> None:
        """
        Set a configuration value.

        By default, sets in the session layer (runtime-only).
        Use persist=True to save to the user configuration file.

        Args:
            key: Dot-separated key path.
            value: Value to set.
            persist: If True, save to user config file.
        """
        if persist:
            self._layered.set(key, value, layer_name="user")
            user_layer = self._layered.get_layer("user")
            if user_layer:
                user_layer.save()
        else:
            self._layered.set(key, value, layer_name="session")

        self._model = None  # Invalidate cached model

    def reload(self) -> None:
        """Reload configuration from all sources."""
        # Re-load file-based layers
        for layer in self._layered.layers():
            if isinstance(layer.source, Path) and layer.source.exists():
                reloaded = ConfigLayer.from_file(
                    layer.source,
                    name=layer.name,
                    priority=layer.priority,
                    mutable=layer.mutable,
                )
                self._layered.add_layer(reloaded)

        self._rebuild_model()
        logger.info("Configuration reloaded")

    def save_user_config(self) -> None:
        """Save user layer to file."""
        user_layer = self._layered.get_layer("user")
        if user_layer:
            user_layer.save()
            logger.info("User configuration saved")

    def as_dict(self) -> dict[str, Any]:
        """Return the merged configuration as a dictionary."""
        return self._layered.as_dict()

    def get_user_config_path(self) -> Path:
        """Get the path to the user configuration file."""
        return _get_user_config_dir() / self.DEFAULT_CONFIG_FILENAME

    def ensure_user_config_dir(self) -> Path:
        """Ensure user config directory exists and return its path."""
        path = _get_user_config_dir()
        path.mkdir(parents=True, exist_ok=True)
        return path
