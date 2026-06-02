"""Dark frame management for background correction.

DarkFrameManager acts as a Bluesky document callback. Subscribe it to the
acquisition engine to automatically capture dark frames from the "dark"
stream.

Two data paths are supported:
1. **Embedded data** (SimDetector): Dark frame arrays are in the event
   document's `data` dict. Captured inline and cached immediately.
2. **File-written data** (PIMTE3, Andor): Event `data` contains datum
   references (strings). Read from Tiled immediately on the event
   (data is persisted before event is emitted).

`load_dark_from_tiled()` searches recent Tiled runs for the most recent
dark stream to populate the cache at initialization time.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from PySide6.QtCore import QObject, Signal

from lightfall.utils.logging import logger


class DarkFrameManager(QObject):
    """Manages dark frame capture, caching, and subtraction.

    Signals:
        dark_updated: Emitted when a new dark frame is cached.
        dark_cleared: Emitted when the cached dark frame is cleared.
    """

    dark_updated = Signal()
    dark_cleared = Signal()

    def __init__(self, device_name: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._device_name = device_name
        self._cached_dark: np.ndarray | None = None

        # Per-run tracking
        self._run_uid: str | None = None
        self._dark_descriptor_uids: set[str] = set()
        self._dark_frames: list[np.ndarray] = []
        self._image_field: str | None = None
        self._has_dark_stream: bool = False

    def __call__(self, name: str, doc: dict[str, Any]) -> None:
        if name == "start":
            self._on_start(doc)
        elif name == "descriptor":
            self._on_descriptor(doc)
        elif name == "event":
            self._on_event(doc)
        elif name == "stop":
            self._on_stop(doc)

    def _on_start(self, doc: dict[str, Any]) -> None:
        self._run_uid = doc.get("uid")
        self._dark_descriptor_uids.clear()
        self._dark_frames.clear()
        self._image_field = None
        self._has_dark_stream = False

    def _on_descriptor(self, doc: dict[str, Any]) -> None:
        stream_name = doc.get("name", "")
        if stream_name != "dark":
            return
        self._has_dark_stream = True
        self._dark_descriptor_uids.add(doc.get("uid", ""))
        data_keys = doc.get("data_keys", {})
        for key in data_keys:
            if self._device_name in key and "image" in key:
                self._image_field = key
                break
        if self._image_field is None:
            for key, info in data_keys.items():
                shape = info.get("shape", [])
                if len(shape) >= 2:
                    self._image_field = key
                    break

    def _on_event(self, doc: dict[str, Any]) -> None:
        """Capture dark frame immediately on each dark event.

        For embedded data: accumulate frames and update cache immediately.
        For file-written data (datum refs): read from Tiled right away.
        """
        descriptor_uid = doc.get("descriptor", "")
        if descriptor_uid not in self._dark_descriptor_uids:
            return
        if not self._image_field:
            return

        data = doc.get("data", {})
        filled = doc.get("filled", {})
        value = data.get(self._image_field)
        if value is None:
            return

        is_filled = filled.get(self._image_field, False)
        if is_filled and isinstance(value, np.ndarray):
            self._dark_frames.append(value.astype(np.float64))
            self._update_cached_dark()
        elif is_filled and hasattr(value, "__array__"):
            self._dark_frames.append(np.asarray(value, dtype=np.float64))
            self._update_cached_dark()
        elif self._run_uid and self._image_field:
            # Datum reference — read from Tiled immediately
            self._read_dark_from_tiled(self._run_uid, self._image_field)

    def _update_cached_dark(self) -> None:
        if self._dark_frames:
            self._cached_dark = np.mean(self._dark_frames, axis=0)
            logger.info(
                f"Cached dark frame for {self._device_name} "
                f"(inline, {len(self._dark_frames)} frame(s) averaged)"
            )
            self.dark_updated.emit()

    def _on_stop(self, doc: dict[str, Any]) -> None:
        self._dark_frames.clear()
        self._dark_descriptor_uids.clear()

    def _read_dark_from_tiled(self, run_uid: str, image_field: str) -> None:
        try:
            from lightfall.services.tiled_service import TiledService
            service = TiledService.get_instance()
            if not service.is_connected:
                logger.debug("Tiled not connected — cannot read dark frame")
                return
            client = service._client
            run = client[run_uid]
            dark_data = run["dark"]["data"][image_field]
            arr = np.asarray(dark_data.values, dtype=np.float64)
            if arr.ndim == 3:
                arr = np.mean(arr, axis=0)
            elif arr.ndim > 3:
                arr = np.squeeze(arr)
                if arr.ndim == 3:
                    arr = np.mean(arr, axis=0)
            self._cached_dark = arr
            logger.info(
                f"Cached dark frame for {self._device_name} "
                f"(from Tiled run {run_uid[:8]})"
            )
            self.dark_updated.emit()
        except Exception as e:
            logger.warning(f"Failed to read dark from Tiled: {e}")

    def load_dark_from_tiled(
        self, image_field: str | None = None, search_last_n: int = 10
    ) -> None:
        if image_field is None:
            image_field = f"{self._device_name}_image"
        try:
            from lightfall.services.tiled_service import TiledService
            service = TiledService.get_instance()
            if not service.is_connected:
                logger.debug("Tiled not connected — cannot search for historical darks")
                return
            client = service._client
            recent_runs = client.values_indexer[-search_last_n:]
            for run in reversed(list(recent_runs)):
                try:
                    if "dark" in run.keys():
                        dark_data = run["dark"]["data"][image_field]
                        arr = np.asarray(dark_data.values, dtype=np.float64)
                        if arr.ndim == 3:
                            arr = np.mean(arr, axis=0)
                        elif arr.ndim > 3:
                            arr = np.squeeze(arr)
                            if arr.ndim == 3:
                                arr = np.mean(arr, axis=0)
                        self._cached_dark = arr
                        logger.info(
                            f"Loaded historical dark frame for {self._device_name} from Tiled"
                        )
                        self.dark_updated.emit()
                        return
                except Exception:
                    continue
            logger.debug(f"No historical dark frame found in last {search_last_n} runs")
        except Exception as e:
            logger.warning(f"Failed to search Tiled for historical darks: {e}")

    @property
    def has_dark(self) -> bool:
        return self._cached_dark is not None

    @property
    def dark_frame(self) -> np.ndarray | None:
        return self._cached_dark

    def subtract(self, image: np.ndarray) -> np.ndarray:
        if self._cached_dark is None:
            return image
        if self._cached_dark.shape != image.shape:
            logger.warning(
                f"Dark frame shape {self._cached_dark.shape} doesn't match "
                f"image shape {image.shape} — skipping subtraction"
            )
            return image
        result = image.astype(np.float64) - self._cached_dark
        np.clip(result, 0, None, out=result)
        return result.astype(image.dtype)

    def clear(self) -> None:
        self._cached_dark = None
        self.dark_cleared.emit()
