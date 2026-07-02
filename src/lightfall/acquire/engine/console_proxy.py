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

from PySide6.QtCore import QEventLoop


class ConsoleREProxy:
    """Callable proxy over a Lightfall engine, delegating attrs to ``engine.RE``.

    Notes
    -----
    Completion correlation
        ``__call__`` submits via the engine (which returns the procedure id)
        and blocks only until *that* procedure's ``sigProcedureFinished`` fires
        — identified by id — so a concurrently queued GUI/console plan
        completing first does not prematurely release this call.

    RunEngine availability
        Attribute access and assignment delegate to ``engine.RE``, which must be
        present (i.e. the engine must have been adopted or started) before any
        attribute is accessed. Use :py:meth:`BlueskyEngine.adopt` or start the
        engine before binding this proxy into the console namespace.
    """

    def __init__(self, engine: Any) -> None:
        # Bypass our own __setattr__ (which delegates to the RE).
        object.__setattr__(self, "_engine", engine)

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        """Submit a plan and block (pumping Qt) until *that* plan terminates.

        Correlates on the procedure id returned by the engine's submit, so a
        different plan completing on the shared engine does not release this
        call. Re-raises the plan's exception if it ended in error.
        """
        engine = object.__getattribute__(self, "_engine")

        loop = QEventLoop()
        state: dict[str, Any] = {"id": None, "done": False, "error": None}

        def _on_finished(procedure_id: str, error: Any) -> None:
            # Ignore completions for other procedures on the shared engine.
            if state["id"] is not None and procedure_id == state["id"]:
                state["error"] = error
                state["done"] = True
                loop.quit()

        engine.sigProcedureFinished.connect(_on_finished)
        try:
            # submit is non-blocking and returns this procedure's id (instance
            # __call__ is respected for test doubles). None means a pre-submit
            # hook cancelled it — nothing will run, so don't block.
            state["id"] = engine.__call__(*args, **kwargs)
            if state["id"] is None:
                return
            if not state["done"]:
                loop.exec()  # pump GUI until this procedure's completion fires
        finally:
            engine.sigProcedureFinished.disconnect(_on_finished)

        error = state["error"]
        if error is not None:
            raise error

    def __getattr__(self, item: str) -> Any:
        # Only called when normal attribute lookup fails — delegate to the RE.
        engine = object.__getattribute__(self, "_engine")
        if engine.RE is None:
            raise RuntimeError(
                "ConsoleREProxy: engine has no RunEngine yet "
                "(call BlueskyEngine.adopt() or start the engine before using the console RE)"
            )
        return getattr(engine.RE, item)

    def __setattr__(self, key: str, value: Any) -> None:
        engine = object.__getattribute__(self, "_engine")
        if engine.RE is None:
            raise RuntimeError(
                "ConsoleREProxy: engine has no RunEngine yet "
                "(call BlueskyEngine.adopt() or start the engine before using the console RE)"
            )
        setattr(engine.RE, key, value)

    def __repr__(self) -> str:
        engine = object.__getattribute__(self, "_engine")
        return f"<ConsoleREProxy over {engine.RE!r}>"
