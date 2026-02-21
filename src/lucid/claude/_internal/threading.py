"""Thread-safe utilities for Qt widget access from background threads."""

import threading
from typing import Any, Callable
from PySide6.QtCore import QObject, Signal, Slot, QThread
from PySide6.QtWidgets import QApplication


class MainThreadInvoker(QObject):
    """
    Helper object that lives on the main thread and executes functions there.

    This is necessary because Qt widgets can only be accessed from the main thread.
    """

    # Signal to request function execution on main thread
    _invoke_signal = Signal(object, object, object)  # func, args, result_holder

    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        super().__init__()
        # Connect signal to slot - since this object is on main thread,
        # the slot will execute on main thread
        self._invoke_signal.connect(self._execute, type=Qt.ConnectionType.QueuedConnection)

    @Slot(object, object, object)
    def _execute(self, func, args, result_holder):
        """Execute the function on the main thread."""
        try:
            result_holder["value"] = func(*args)
        except Exception as e:
            import traceback
            result_holder["error"] = f"{str(e)}\n{traceback.format_exc()}"
        finally:
            result_holder["done"].set()

    @classmethod
    def instance(cls) -> "MainThreadInvoker":
        """Get or create the singleton instance on the main thread."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    # Create on main thread
                    app = QApplication.instance()
                    if app:
                        cls._instance = cls()
                        # Move to main thread if not already there
                        cls._instance.moveToThread(app.thread())
        return cls._instance


# Import Qt here to avoid issues
from PySide6.QtCore import Qt


def run_on_main_thread(func: Callable, *args) -> Any:
    """
    Run a function on the Qt main thread and return its result.

    This blocks until the function completes on the main thread.

    Args:
        func: The function to run
        *args: Arguments to pass to the function

    Returns:
        The return value of the function

    Raises:
        RuntimeError: If the function raises an exception
    """
    # Check if we're already on the main thread
    app = QApplication.instance()
    if not app:
        # No Qt app, just run directly
        return func(*args)

    if QThread.currentThread() == app.thread():
        # Already on main thread, just call directly
        return func(*args)

    # Need to marshal to main thread
    result_holder = {
        "value": None,
        "error": None,
        "done": threading.Event()
    }

    invoker = MainThreadInvoker.instance()
    if invoker is None:
        # Fallback - just try to run directly (may fail for widget access)
        return func(*args)

    # Emit signal to invoke on main thread
    invoker._invoke_signal.emit(func, args, result_holder)

    # Wait for completion with timeout
    if not result_holder["done"].wait(timeout=30.0):
        raise RuntimeError("Timeout waiting for main thread execution")

    if result_holder["error"]:
        raise RuntimeError(result_holder["error"])

    return result_holder["value"]
