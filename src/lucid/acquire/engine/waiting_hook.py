"""WaitingHookBridge — translates Bluesky waiting_hook callbacks to Qt signals.

Bluesky's RunEngine calls ``waiting_hook(status_objs)`` with a set of
StatusBase objects when it enters a wait, and ``waiting_hook(None)`` when all
waits resolve. This bridge converts those calls into throttled Qt signals
suitable for driving progress UI on the main thread.
"""

from __future__ import annotations

import threading
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from lucid.utils.logging import logger

__all__ = ["WaitingHookBridge"]

# Flush interval in milliseconds (~10 Hz)
_FLUSH_INTERVAL_MS = 100


class WaitingHookBridge(QObject):
    """Callable bridge between Bluesky waiting_hook and Qt signals.

    Assigned to ``RE.waiting_hook``, this object is called from the
    RunEngine thread. It buffers progress updates and flushes them to
    Qt signals on the main thread via a QTimer.

    Signals:
        sigDeviceProgress(str, float, float, float, float):
            ``(device_name, current, initial, target, fraction)``
            Emitted (on timer tick) when a status object's ``.watch()``
            reports progress.  ``fraction = -1`` signals indeterminate.
        sigDeviceFinished(str):
            ``(device_name,)`` — emitted when an individual status completes.
        sigWaitGroupCleared:
            Emitted when called with ``None`` (all waits resolved).
    """

    sigDeviceProgress = Signal(str, float, float, float, float)
    sigDeviceFinished = Signal(str)
    sigWaitGroupCleared = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

        # Buffer: device_name -> (current, initial, target, fraction)
        self._buffer: dict[str, tuple[float, float, float, float]] = {}
        self._lock = threading.Lock()

        # Active status objects we're tracking (prevent GC issues)
        self._active_statuses: set[Any] = set()

        # Flush timer — runs on the thread that owns this QObject (main thread)
        self._timer = QTimer(self)
        self._timer.setInterval(_FLUSH_INTERVAL_MS)
        self._timer.timeout.connect(self._flush)

    # ------------------------------------------------------------------
    # Public callable interface (called from RE thread)
    # ------------------------------------------------------------------

    def __call__(self, status_objs: set[Any] | None) -> None:
        """Bluesky waiting_hook entry point.

        Args:
            status_objs: A set of ophyd StatusBase objects, or ``None``
                when all pending waits have resolved.
        """
        if status_objs is None:
            self._on_all_resolved()
            return

        for st in status_objs:
            # Skip if we're already tracking this status
            if st in self._active_statuses:
                continue
            self._active_statuses.add(st)
            self._subscribe_status(st)

        # Ensure the flush timer is running
        if not self._timer.isActive():
            self._timer.start()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _subscribe_status(self, st: Any) -> None:
        """Subscribe to watch and completion callbacks on a single status."""
        name = self._status_name(st)

        if hasattr(st, "watch"):
            try:
                st.watch(self._make_watch_callback(name))
            except Exception:
                logger.debug(f"[waiting_hook] watch() failed for {name}, treating as indeterminate")
                self._buffer_update(name, 0.0, 0.0, 0.0, -1.0)
        else:
            # Non-watchable: indeterminate progress
            self._buffer_update(name, 0.0, 0.0, 0.0, -1.0)

        # Completion callback
        st.add_callback(self._make_done_callback(name, st))

    def _make_watch_callback(self, name: str):
        """Return a closure suitable for ``st.watch(callback)``."""

        def _on_watch(**kwargs: Any) -> None:
            current = float(kwargs.get("current", 0))
            initial = float(kwargs.get("initial", 0))
            target = float(kwargs.get("target", 0))
            fraction = float(kwargs.get("fraction", -1))
            self._buffer_update(name, current, initial, target, fraction)

        return _on_watch

    def _make_done_callback(self, name: str, st: Any):
        """Return a closure for ``st.add_callback(cb)``."""

        def _on_done(status: Any = None) -> None:
            self._active_statuses.discard(st)
            # Emit finished signal (will be cross-thread → queued connection)
            self.sigDeviceFinished.emit(name)
            logger.debug(f"[waiting_hook] {name} finished")

        return _on_done

    def _buffer_update(
        self, name: str, current: float, initial: float, target: float, fraction: float
    ) -> None:
        """Thread-safe buffer write (called from RE thread)."""
        with self._lock:
            self._buffer[name] = (current, initial, target, fraction)

    def _flush(self) -> None:
        """Timer slot — emit buffered progress updates on the main thread."""
        with self._lock:
            snapshot = dict(self._buffer)
            self._buffer.clear()

        for name, (current, initial, target, fraction) in snapshot.items():
            self.sigDeviceProgress.emit(name, current, initial, target, fraction)

    def _on_all_resolved(self) -> None:
        """Handle ``waiting_hook(None)`` — all waits cleared."""
        self._timer.stop()
        # Flush any remaining buffered updates
        self._flush()
        self._active_statuses.clear()
        self.sigWaitGroupCleared.emit()
        logger.debug("[waiting_hook] all waits resolved")

    @staticmethod
    def _status_name(st: Any) -> str:
        """Extract a human-readable name from a status object."""
        # ophyd statuses often expose .obj.name
        if hasattr(st, "obj") and hasattr(st.obj, "name"):
            return str(st.obj.name)
        # Fall back to the name kwarg if stored
        if hasattr(st, "name"):
            return str(st.name)
        return type(st).__name__
