"""Console-facing proxy that routes ``RE(plan)`` calls through the engine.

When Lightfall hosts a Bluesky session in its embedded in-process IPython
console, the namespace name ``RE`` is rebound to one of these proxies. A
console-typed ``RE(plan)`` then submits to the GUI engine (which runs the plan
on its own worker thread) and blocks the caller only until the plan finishes —
while a nested Qt event loop keeps the GUI responsive so Abort still works.

Every other attribute access (``RE.md``, ``RE.md = ...``, ``RE.subscribe``,
``RE.preprocessors``, ``RE.state``, ``RE.abort`` ...) is delegated to the
underlying RunEngine, so existing profile code that references the global
``RE`` keeps working unchanged.
"""

from __future__ import annotations

from typing import Any

from loguru import logger
from PySide6.QtCore import QEventLoop


class ConsoleREProxy:
    """Callable proxy over a Lightfall engine, delegating attrs to ``engine.RE``."""

    def __init__(self, engine: Any) -> None:
        # Bypass our own __setattr__ (which delegates to the RE).
        object.__setattr__(self, "_engine", engine)

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        """Submit a plan and block (pumping Qt) until it terminates."""
        engine = object.__getattribute__(self, "_engine")

        loop = QEventLoop()
        captured: dict[str, BaseException] = {}

        def _on_finish() -> None:
            loop.quit()

        def _on_abort() -> None:
            loop.quit()

        def _on_exception(exc: BaseException) -> None:
            captured["error"] = exc
            loop.quit()

        engine.sigFinish.connect(_on_finish)
        engine.sigAbort.connect(_on_abort)
        engine.sigException.connect(_on_exception)
        try:
            engine.__call__(*args, **kwargs)  # non-blocking submit (instance __call__ respected)
            loop.exec()                       # pump GUI until a terminal signal
        finally:
            engine.sigFinish.disconnect(_on_finish)
            engine.sigAbort.disconnect(_on_abort)
            engine.sigException.disconnect(_on_exception)

        if "error" in captured:
            raise captured["error"]

    def __getattr__(self, item: str) -> Any:
        # Only called when normal attribute lookup fails — delegate to the RE.
        engine = object.__getattribute__(self, "_engine")
        return getattr(engine.RE, item)

    def __setattr__(self, key: str, value: Any) -> None:
        engine = object.__getattribute__(self, "_engine")
        setattr(engine.RE, key, value)

    def __repr__(self) -> str:
        engine = object.__getattribute__(self, "_engine")
        return f"<ConsoleREProxy over {engine.RE!r}>"
