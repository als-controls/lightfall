"""Thread-safe utilities for Qt widget access from background threads.

Thin wrapper around lucid.utils.threads for backward compatibility.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from PySide6.QtWidgets import QApplication

from lucid.utils.threads import invoke_in_main_thread, is_main_thread


def run_on_main_thread(func: Callable[..., Any], *args: Any) -> Any:
    """Run a function on the Qt main thread and return its result.

    If already on the main thread, calls directly. Otherwise marshals
    via Qt event and blocks until complete.

    Args:
        func: The function to run.
        *args: Arguments to pass to the function.

    Returns:
        The return value of the function.

    Raises:
        RuntimeError: If the function raises an exception or times out.
    """
    app = QApplication.instance()
    if not app or is_main_thread():
        return func(*args)

    # Need to marshal to main thread and block for result
    result_holder: dict[str, Any] = {
        "value": None,
        "error": None,
        "done": threading.Event(),
    }

    def _wrapper() -> None:
        try:
            result_holder["value"] = func(*args)
        except Exception as exc:
            import traceback
            result_holder["error"] = f"{exc}\n{traceback.format_exc()}"
        finally:
            result_holder["done"].set()

    invoke_in_main_thread(_wrapper, force_event=True)

    if not result_holder["done"].wait(timeout=30.0):
        raise RuntimeError("Timeout waiting for main thread execution")

    if result_holder["error"]:
        raise RuntimeError(result_holder["error"])

    return result_holder["value"]
