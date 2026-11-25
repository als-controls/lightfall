"""ui_wait: block (on the agent's thread) until something is true on screen.

A timeout is an answer (``met: False``), not an error: the agent decides what to do.
"""

from __future__ import annotations

import time
from typing import Any

from lightfall.ui.surface.locate import LocateError, locate
from lightfall.ui.surface.tree import SurfaceError
from lightfall.utils.crash_diagnostics import gui_thread_only

CONDITIONS = ("idle", "element", "toast", "run_finished")
POLL_S = 0.1
IDLE_STABLE_S = 0.3


class WaitError(SurfaceError):
    pass


def _engine():
    from lightfall.acquire.engine import peek_engine

    return peek_engine()


def _engine_idle() -> bool:
    engine = _engine()
    if engine is None:
        return True
    try:
        return bool(engine.is_idle) and not getattr(engine, "queue_size", 0)
    except Exception:  # noqa: BLE001 - an engine mid-teardown counts as idle
        return True


@gui_thread_only
def _element_present(target: dict) -> bool:
    try:
        locate(target)
        return True
    except LocateError as e:
        return "matches for" in str(e)  # several matches: it is present


@gui_thread_only
def _toasts(since: float, text: str | None, kind: str | None) -> list[dict]:
    from lightfall.ui.toast import ToastManager

    out = []
    for t in ToastManager.get_instance().recent(since):
        if kind and t.get("kind") != kind:
            continue
        hay = f'{t.get("title", "")} {t.get("text", "")}'.casefold()
        if text and text.casefold() not in hay:
            continue
        out.append(t)
    return out


def _last_run_status() -> dict:
    """Exit status of the most recent run, if the engine records one."""
    engine = _engine()
    for attr in ("last_result", "last_run", "last_exit_status"):
        value = getattr(engine, attr, None) if engine is not None else None
        if value:
            return {"last": str(value)[:200]}
    return {}


def wait(condition: str, timeout_s: float = 30.0, **kw: Any) -> dict:
    """Wait for *condition*. Call from a worker thread."""
    from lightfall.claude._internal.threading import run_on_main_thread
    from lightfall.utils.threads import is_main_thread

    if condition not in CONDITIONS:
        raise WaitError(f"unknown condition '{condition}'; conditions: {', '.join(CONDITIONS)}")
    if is_main_thread():
        raise RuntimeError("wait() must run off the GUI thread")
    t0 = time.monotonic()
    deadline = t0 + max(0.0, float(timeout_s))

    def done(met: bool, **detail) -> dict:
        out: dict[str, Any] = {"met": met, "waited_s": round(time.monotonic() - t0, 2)}
        if detail:
            out["detail"] = detail
        return out

    if condition == "element":
        target = {k: kw[k] for k in ("role", "name", "within") if kw.get(k)}
        if not target:
            raise WaitError("'element' needs role and/or name (and optionally within)")
        gone = bool(kw.get("gone"))
        while True:
            present = run_on_main_thread(_element_present, target)
            if present != gone:
                return done(True)
            if time.monotonic() >= deadline:
                return done(False, still_present=present)
            time.sleep(POLL_S)

    if condition == "toast":
        text, kind = kw.get("text"), kw.get("kind")
        while True:
            hits = run_on_main_thread(_toasts, t0, text, kind)
            if hits:
                t = hits[-1]
                return done(True, toast=f'{t.get("kind")} "{t.get("title")}"'
                            + (f': {t["text"]}' if t.get("text") else ""))
            if time.monotonic() >= deadline:
                return done(False)
            time.sleep(POLL_S)

    if condition == "idle":
        stable_since = None
        while True:
            idle = run_on_main_thread(_engine_idle)
            now = time.monotonic()
            if idle:
                stable_since = stable_since or now
                if now - stable_since >= IDLE_STABLE_S:
                    return done(True)
            else:
                stable_since = None
            if now >= deadline:
                return done(False, engine="busy")
            time.sleep(POLL_S)

    # run_finished: the engine was busy at or after the call, and is idle again.
    seen_busy = False
    while True:
        idle = run_on_main_thread(_engine_idle)
        if not idle:
            seen_busy = True
        elif seen_busy:
            return done(True, **run_on_main_thread(_last_run_status))
        if time.monotonic() >= deadline:
            return done(False, engine="busy" if not idle else "never started")
        time.sleep(POLL_S)
