"""Signal configuration for data acquisition.

Provides a UI model for configuring which signals to acquire and display
during scans. This complements ophyd's signal `kind` system with user-facing
configuration and preset management.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from loguru import logger
from PySide6.QtCore import QObject, Signal as QtSignal

if TYPE_CHECKING:
    from ophyd import Device


class SignalKind(str, Enum):
    """Signal kind for acquisition configuration.

    Mirrors ophyd's Kind enum but as a string enum for easier serialization.
    """

    HINTED = "hinted"  # Primary data, shown in plots
    NORMAL = "normal"  # Saved but not displayed prominently
    CONFIG = "config"  # Configuration values, saved once per run
    OMITTED = "omitted"  # Not acquired


@dataclass
class SignalDefinition:
    """Definition of a signal for acquisition.

    Represents a single signal that can be acquired during a scan,
    with metadata for display and configuration.

    Attributes:
        name: Unique identifier for this signal definition.
        device_name: Name of the device in the catalog.
        signal_path: Path to the signal on the device (e.g., "motor.readback").
        kind: How this signal should be treated during acquisition.
        label: Display label for UI.
        unit: Unit string for display.
        description: Optional description.
        dtype: Data type hint (e.g., "number", "array", "string").
        shape: Expected data shape (empty for scalar).
    """

    name: str
    device_name: str
    signal_path: str = ""
    kind: SignalKind = SignalKind.NORMAL
    label: str = ""
    unit: str = ""
    description: str = ""
    dtype: str = "number"
    shape: list[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Set default label if not provided."""
        if not self.label:
            self.label = self.name

    @property
    def full_path(self) -> str:
        """Get the full path to the signal.

        Returns:
            Full path as device.signal_path or just device_name.
        """
        if self.signal_path:
            return f"{self.device_name}.{self.signal_path}"
        return self.device_name

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization.

        Returns:
            Dictionary representation.
        """
        return {
            "name": self.name,
            "device_name": self.device_name,
            "signal_path": self.signal_path,
            "kind": self.kind.value,
            "label": self.label,
            "unit": self.unit,
            "description": self.description,
            "dtype": self.dtype,
            "shape": self.shape,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SignalDefinition:
        """Create from dictionary.

        Args:
            data: Dictionary with signal definition data.

        Returns:
            SignalDefinition instance.
        """
        kind = data.get("kind", "normal")
        if isinstance(kind, str):
            kind = SignalKind(kind)
        return cls(
            name=data["name"],
            device_name=data["device_name"],
            signal_path=data.get("signal_path", ""),
            kind=kind,
            label=data.get("label", ""),
            unit=data.get("unit", ""),
            description=data.get("description", ""),
            dtype=data.get("dtype", "number"),
            shape=data.get("shape", []),
        )


@dataclass
class SignalPreset:
    """A named preset of signal configurations.

    Presets allow users to save and restore signal selections
    for different acquisition scenarios.

    Attributes:
        name: Preset name.
        description: Optional description.
        signal_names: List of signal names included in this preset.
    """

    name: str
    description: str = ""
    signal_names: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "name": self.name,
            "description": self.description,
            "signal_names": self.signal_names,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SignalPreset:
        """Create from dictionary."""
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            signal_names=data.get("signal_names", []),
        )


class SignalConfiguration(QObject):
    """User-configurable signal selection for scans.

    Manages a collection of signal definitions and presets,
    allowing users to configure which signals to acquire and display.

    Signals:
        signals_changed: Emitted when signals are added/removed.
        preset_applied: Emitted when a preset is loaded.
        kind_changed: Emitted when a signal's kind changes.

    Example:
        >>> config = SignalConfiguration()
        >>> config.add_signal(SignalDefinition("motor_x", "motor", "readback"))
        >>> config.set_kind("motor_x", SignalKind.HINTED)
        >>> detectors = config.get_hinted_signals()
    """

    signals_changed = QtSignal()
    preset_applied = QtSignal(str)  # preset name
    kind_changed = QtSignal(str, object)  # signal name, new kind

    def __init__(self, parent: QObject | None = None) -> None:
        """Initialize signal configuration.

        Args:
            parent: Optional Qt parent.
        """
        super().__init__(parent)
        self._signals: dict[str, SignalDefinition] = {}
        self._presets: dict[str, SignalPreset] = {}

    # === Signal Management ===

    def add_signal(self, signal: SignalDefinition) -> None:
        """Add a signal to the configuration.

        Args:
            signal: Signal definition to add.
        """
        self._signals[signal.name] = signal
        self.signals_changed.emit()
        logger.debug(f"Added signal: {signal.name}")

    def remove_signal(self, name: str) -> bool:
        """Remove a signal from the configuration.

        Args:
            name: Signal name to remove.

        Returns:
            True if signal was removed.
        """
        if name in self._signals:
            del self._signals[name]
            self.signals_changed.emit()
            logger.debug(f"Removed signal: {name}")
            return True
        return False

    def get_signal(self, name: str) -> SignalDefinition | None:
        """Get a signal by name.

        Args:
            name: Signal name.

        Returns:
            SignalDefinition or None.
        """
        return self._signals.get(name)

    def get_all_signals(self) -> list[SignalDefinition]:
        """Get all signals.

        Returns:
            List of all signal definitions.
        """
        return list(self._signals.values())

    def set_kind(self, name: str, kind: SignalKind) -> bool:
        """Set the kind for a signal.

        Args:
            name: Signal name.
            kind: New signal kind.

        Returns:
            True if signal was found and updated.
        """
        signal = self._signals.get(name)
        if signal:
            signal.kind = kind
            self.kind_changed.emit(name, kind)
            return True
        return False

    def clear(self) -> None:
        """Remove all signals."""
        self._signals.clear()
        self.signals_changed.emit()

    # === Filtering ===

    def get_signals_by_kind(self, kind: SignalKind) -> list[SignalDefinition]:
        """Get signals of a specific kind.

        Args:
            kind: Signal kind to filter by.

        Returns:
            List of matching signals.
        """
        return [s for s in self._signals.values() if s.kind == kind]

    def get_hinted_signals(self) -> list[SignalDefinition]:
        """Get signals that should be shown in plots.

        Returns:
            List of hinted signals.
        """
        return self.get_signals_by_kind(SignalKind.HINTED)

    def get_normal_signals(self) -> list[SignalDefinition]:
        """Get signals that should be saved but not prominently displayed.

        Returns:
            List of normal signals.
        """
        return self.get_signals_by_kind(SignalKind.NORMAL)

    def get_config_signals(self) -> list[SignalDefinition]:
        """Get configuration signals.

        Returns:
            List of config signals.
        """
        return self.get_signals_by_kind(SignalKind.CONFIG)

    def get_active_signals(self) -> list[SignalDefinition]:
        """Get all signals that should be acquired (not omitted).

        Returns:
            List of active signals.
        """
        return [s for s in self._signals.values() if s.kind != SignalKind.OMITTED]

    # === Preset Management ===

    def add_preset(self, preset: SignalPreset) -> None:
        """Add a preset.

        Args:
            preset: Preset to add.
        """
        self._presets[preset.name] = preset
        logger.debug(f"Added preset: {preset.name}")

    def remove_preset(self, name: str) -> bool:
        """Remove a preset.

        Args:
            name: Preset name.

        Returns:
            True if preset was removed.
        """
        if name in self._presets:
            del self._presets[name]
            return True
        return False

    def get_preset(self, name: str) -> SignalPreset | None:
        """Get a preset by name.

        Args:
            name: Preset name.

        Returns:
            SignalPreset or None.
        """
        return self._presets.get(name)

    def get_all_presets(self) -> list[SignalPreset]:
        """Get all presets.

        Returns:
            List of all presets.
        """
        return list(self._presets.values())

    def apply_preset(self, name: str) -> bool:
        """Apply a preset, setting signals to HINTED or OMITTED.

        Signals in the preset are set to HINTED, others to OMITTED.

        Args:
            name: Preset name.

        Returns:
            True if preset was found and applied.
        """
        preset = self._presets.get(name)
        if not preset:
            return False

        for signal in self._signals.values():
            if signal.name in preset.signal_names:
                signal.kind = SignalKind.HINTED
            else:
                signal.kind = SignalKind.OMITTED

        self.preset_applied.emit(name)
        logger.info(f"Applied preset: {name}")
        return True

    def save_current_as_preset(self, name: str, description: str = "") -> SignalPreset:
        """Save current hinted signals as a preset.

        Args:
            name: Preset name.
            description: Optional description.

        Returns:
            The created preset.
        """
        hinted_names = [s.name for s in self.get_hinted_signals()]
        preset = SignalPreset(name=name, description=description, signal_names=hinted_names)
        self._presets[name] = preset
        return preset

    # === Device Integration ===

    def get_device_names(self) -> set[str]:
        """Get unique device names from all signals.

        Returns:
            Set of device names.
        """
        return {s.device_name for s in self._signals.values()}

    def get_signals_for_device(self, device_name: str) -> list[SignalDefinition]:
        """Get signals for a specific device.

        Args:
            device_name: Device name to filter by.

        Returns:
            List of signals for the device.
        """
        return [s for s in self._signals.values() if s.device_name == device_name]

    # === Bluesky Integration ===

    def get_hints(self) -> dict[str, dict[str, list[str]]]:
        """Get hints dictionary for Bluesky.

        Returns:
            Dictionary in Bluesky hints format.
        """
        hints: dict[str, dict[str, list[str]]] = {}
        for signal in self.get_hinted_signals():
            if signal.device_name not in hints:
                hints[signal.device_name] = {"fields": []}
            hints[signal.device_name]["fields"].append(signal.full_path)
        return hints

    # === Serialization ===

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization.

        Returns:
            Dictionary representation.
        """
        return {
            "signals": [s.to_dict() for s in self._signals.values()],
            "presets": [p.to_dict() for p in self._presets.values()],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SignalConfiguration:
        """Create from dictionary.

        Args:
            data: Dictionary with configuration data.

        Returns:
            SignalConfiguration instance.
        """
        config = cls()
        for signal_data in data.get("signals", []):
            config.add_signal(SignalDefinition.from_dict(signal_data))
        for preset_data in data.get("presets", []):
            config.add_preset(SignalPreset.from_dict(preset_data))
        return config
