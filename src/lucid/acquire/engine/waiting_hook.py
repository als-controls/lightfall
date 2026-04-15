"""WaitingHookBridge — translates Bluesky waiting_hook callbacks to Qt signals.

Bluesky's RunEngine calls ``waiting_hook(status_objs)`` with a set of
StatusBase objects when it enters a wait, and ``waiting_hook(None)`` when all
waits resolve. This bridge converts those calls into throttled Qt signals
suitable for driving progress UI on the main thread.
"""

from __future__ import annotations

import threading
from typing import Any

from PySide6.QtCore import QMetaObject, QObject, Qt, QTimer, Signal

from lucid.utils.logging import logger

__all__ = ["WaitingHookBridge"]

# Flush interval in milliseconds (~10 Hz)
_FLUSH_INTERVAL_MS = 100


class WaitingHookBridge(QObject):
    """Callable bridge between Bluesky waiting_hook and Qt signals.

    Assigned to ``RE.waiting_hook``, this object is called from the
    RunEngine thread. It buffers progress updates and flushes them to
    Qt signals on the main thread via a QTimer.

    All mutable state shared between threads is protected by ``_lock``.
    The ``_done_buffer`` and ``_group_cleared`` flag allow the flush timer
    (running on the main/Qt thread) to emit ``sigDeviceFinished`` and
    ``sigWaitGroupCleared`` safely, rather than emitting from the RE or
    device threads directly.

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

        # Lock protects _buffer, _done_buffer, _group_cleared, _active_statuses
        self._lock = threading.Lock()

        # Buffer: device_name -> (current, initial, target, fraction)
        self._buffer: dict[str, tuple[float, float, float, float]] = {}

        # Buffer for finished device names (drained on main thread by _flush)
        self._done_buffer: list[str] = []

        # Flag: set True when _on_all_resolved is called, cleared by _flush
        self._group_cleared: bool = False

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

        new_statuses: list[Any] = []
        with self._lock:
            for st in status_objs:
                # Skip if we're already tracking this status
                if st in self._active_statuses:
                    continue
                self._active_statuses.add(st)
                new_statuses.append(st)

        # Subscribe outside the lock — watch()/add_callback() may call back
        # into our buffer methods which also acquire _lock.
        for st in new_statuses:
            self._subscribe_status(st)

        # Ensure the flush timer is running (must be started from main thread)
        QMetaObject.invokeMethod(
            self._timer, "start", Qt.ConnectionType.QueuedConnection
        )

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
                logger.warning(
                    f"[waiting_hook] watch() failed for {name}, treating as indeterminate"
                )
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
            with self._lock:
                self._active_statuses.discard(st)
                self._done_buffer.append(name)
            logger.debug(f"[waiting_hook] {name} finished")
            # Ensure the timer is running so the done event gets flushed
            QMetaObject.invokeMethod(
                self._timer, "start", Qt.ConnectionType.QueuedConnection
            )

        return _on_done

    def _buffer_update(
        self, name: str, current: float, initial: float, target: float, fraction: float
    ) -> None:
        """Thread-safe buffer write (called from RE/device thread)."""
        with self._lock:
            self._buffer[name] = (current, initial, target, fraction)

    def _flush(self) -> None:
        """Timer slot — emit buffered signals on the main thread."""
        with self._lock:
            progress_snapshot = dict(self._buffer)
            self._buffer.clear()
            done_snapshot = list(self._done_buffer)
            self._done_buffer.clear()
            group_cleared = self._group_cleared
            self._group_cleared = False

        for name, (current, initial, target, fraction) in progress_snapshot.items():
            self.sigDeviceProgress.emit(name, current, initial, target, fraction)

        for name in done_snapshot:
            self.sigDeviceFinished.emit(name)

        if group_cleared:
            self.sigWaitGroupCleared.emit()
            self._timer.stop()

    def _on_all_resolved(self) -> None:
        """Handle ``waiting_hook(None)`` — all waits cleared.

        Called from the RE thread.  Buffers the cleared flag so that
        ``_flush`` (main thread) emits ``sigWaitGroupCleared``.
        """
        with self._lock:
            self._group_cleared = True
            self._active_statuses.clear()
        logger.debug("[waiting_hook] all waits resolved")
        # Ensure the timer is running so _flush can emit the cleared signal
        QMetaObject.invokeMethod(
            self._timer, "start", Qt.ConnectionType.QueuedConnection
        )

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
