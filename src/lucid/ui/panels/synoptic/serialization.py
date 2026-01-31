"""Serialization utilities for synoptic view data.

This module handles:
- Saving/loading DeviceSynopticData to/from DeviceInfo.metadata
- Saving/loading SynopticViewState to/from local preferences
- Beam path configuration storage
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lucid.ui.panels.synoptic.models import (
    BeamPathSegment,
    DeviceSynopticData,
    SynopticViewState,
)
from lucid.utils.logging import logger

if TYPE_CHECKING:
    from lucid.devices.model import DeviceInfo


SYNOPTIC_METADATA_KEY = "synoptic"
VIEW_STATE_PREF_PREFIX = "synoptic.view_state"
BEAM_PATH_PREF_PREFIX = "synoptic.beam_path"


def get_device_synoptic_data(device_info: DeviceInfo) -> DeviceSynopticData | None:
    """Get synoptic data from a device's metadata.

    Args:
        device_info: The device info to read from.

    Returns:
        DeviceSynopticData if present, None otherwise.
    """
    metadata = device_info.metadata
    synoptic_dict = metadata.get(SYNOPTIC_METADATA_KEY)

    if synoptic_dict is None:
        return None

    try:
        return DeviceSynopticData.from_dict(synoptic_dict)
    except (KeyError, ValueError, TypeError) as e:
        logger.warning(
            "Invalid synoptic data for device {}: {}",
            device_info.name,
            e,
        )
        return None


def set_device_synoptic_data(
    device_info: DeviceInfo,
    synoptic_data: DeviceSynopticData,
) -> None:
    """Set synoptic data in a device's metadata.

    Note: This modifies the DeviceInfo in memory. The caller is
    responsible for persisting the change to the backend.

    Args:
        device_info: The device info to update.
        synoptic_data: The synoptic data to store.
    """
    device_info.metadata[SYNOPTIC_METADATA_KEY] = synoptic_data.to_dict()
    logger.debug("Updated synoptic data for device: {}", device_info.name)


def remove_device_synoptic_data(device_info: DeviceInfo) -> bool:
    """Remove synoptic data from a device's metadata.

    Args:
        device_info: The device info to update.

    Returns:
        True if data was removed, False if not present.
    """
    if SYNOPTIC_METADATA_KEY in device_info.metadata:
        del device_info.metadata[SYNOPTIC_METADATA_KEY]
        logger.debug("Removed synoptic data for device: {}", device_info.name)
        return True
    return False


def get_or_create_device_synoptic_data(
    device_info: DeviceInfo,
) -> DeviceSynopticData:
    """Get existing synoptic data or create default for device.

    Args:
        device_info: The device info to read/create from.

    Returns:
        Existing or new DeviceSynopticData with category defaults.
    """
    existing = get_device_synoptic_data(device_info)
    if existing is not None:
        return existing

    # Create default based on category
    category = device_info.category.value if device_info.category else "other"
    return DeviceSynopticData.default_for_category(category)


class SynopticPersistence:
    """Handles persistence of synoptic view state and beam paths.

    Uses PreferencesManager for local storage of:
    - View state (camera position, projection mode, etc.)
    - Beam path configuration
    """

    def __init__(self, beamline_id: str | None = None) -> None:
        """Initialize persistence handler.

        Args:
            beamline_id: Optional beamline ID for scoped storage.
        """
        self._beamline_id = beamline_id or "default"

    @property
    def _view_state_key(self) -> str:
        """Get preferences key for view state."""
        return f"{VIEW_STATE_PREF_PREFIX}.{self._beamline_id}"

    @property
    def _beam_path_key(self) -> str:
        """Get preferences key for beam path."""
        return f"{BEAM_PATH_PREF_PREFIX}.{self._beamline_id}"

    def load_view_state(self) -> SynopticViewState | None:
        """Load view state from preferences.

        Returns:
            Saved view state or None if not found.
        """
        try:
            from lucid.ui.preferences.manager import PreferencesManager

            prefs = PreferencesManager.get_instance()
            state_dict = prefs.get(self._view_state_key)

            if state_dict is None:
                return None

            return SynopticViewState.from_dict(state_dict)
        except Exception as e:
            logger.warning("Failed to load synoptic view state: {}", e)
            return None

    def save_view_state(self, state: SynopticViewState) -> bool:
        """Save view state to preferences.

        Args:
            state: View state to save.

        Returns:
            True if saved successfully.
        """
        try:
            from lucid.ui.preferences.manager import PreferencesManager

            prefs = PreferencesManager.get_instance()
            prefs.set(self._view_state_key, state.to_dict())
            logger.debug("Saved synoptic view state for: {}", self._beamline_id)
            return True
        except Exception as e:
            logger.error("Failed to save synoptic view state: {}", e)
            return False

    def load_beam_path(self) -> list[BeamPathSegment]:
        """Load beam path segments from preferences.

        Returns:
            List of beam path segments.
        """
        try:
            from lucid.ui.preferences.manager import PreferencesManager

            prefs = PreferencesManager.get_instance()
            segments_data = prefs.get(self._beam_path_key, [])

            return [BeamPathSegment.from_dict(d) for d in segments_data]
        except Exception as e:
            logger.warning("Failed to load beam path: {}", e)
            return []

    def save_beam_path(self, segments: list[BeamPathSegment]) -> bool:
        """Save beam path segments to preferences.

        Args:
            segments: Segments to save.

        Returns:
            True if saved successfully.
        """
        try:
            from lucid.ui.preferences.manager import PreferencesManager

            prefs = PreferencesManager.get_instance()
            segments_data = [seg.to_dict() for seg in segments]
            prefs.set(self._beam_path_key, segments_data)
            logger.debug("Saved beam path for: {}", self._beamline_id)
            return True
        except Exception as e:
            logger.error("Failed to save beam path: {}", e)
            return False


class DeviceSynopticSaver:
    """Handles debounced saving of device synoptic data to backend.

    Collects changes and batches them for efficient saving.
    """

    def __init__(self, debounce_ms: int = 500) -> None:
        """Initialize the saver.

        Args:
            debounce_ms: Debounce time in milliseconds.
        """
        from PySide6.QtCore import QTimer

        self._debounce_ms = debounce_ms
        self._pending_saves: dict[str, tuple[DeviceInfo, DeviceSynopticData]] = {}
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._flush_saves)

    def schedule_save(
        self,
        device_info: DeviceInfo,
        synoptic_data: DeviceSynopticData,
    ) -> None:
        """Schedule a device synoptic data save.

        Args:
            device_info: Device to save.
            synoptic_data: Data to save.
        """
        device_id = str(device_info.id)
        self._pending_saves[device_id] = (device_info, synoptic_data)

        # Reset timer
        self._timer.stop()
        self._timer.start(self._debounce_ms)

    def _flush_saves(self) -> None:
        """Flush all pending saves."""
        if not self._pending_saves:
            return

        saves = list(self._pending_saves.values())
        self._pending_saves.clear()

        for device_info, synoptic_data in saves:
            try:
                set_device_synoptic_data(device_info, synoptic_data)
                self._persist_device(device_info)
            except Exception as e:
                logger.error(
                    "Failed to save synoptic data for {}: {}",
                    device_info.name,
                    e,
                )

    def _persist_device(self, device_info: DeviceInfo) -> None:
        """Persist device info to backend.

        Args:
            device_info: Device to persist.
        """
        try:
            from lucid.devices import DeviceCatalog

            catalog = DeviceCatalog.get_instance()
            catalog.update_device(device_info)
            logger.debug("Persisted device: {}", device_info.name)
        except Exception as e:
            logger.error("Failed to persist device {}: {}", device_info.name, e)

    def flush(self) -> None:
        """Immediately flush all pending saves."""
        self._timer.stop()
        self._flush_saves()

    def cancel_pending(self, device_id: str) -> None:
        """Cancel pending save for a device.

        Args:
            device_id: Device ID to cancel.
        """
        self._pending_saves.pop(device_id, None)

    def has_pending(self) -> bool:
        """Check if there are pending saves."""
        return len(self._pending_saves) > 0
