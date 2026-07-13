"""QtClaudeAgent.stop() must not force-terminate an unresponsive worker.

``QThread.terminate()`` on a thread executing Python corrupts the interpreter
heap and crashes the process (0xC0000005). A worker that ignores ``stop()``
must be abandoned, not killed.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from loguru import logger

from lightfall.claude.agent import QtClaudeAgent


def test_stop_abandons_unresponsive_worker_without_terminate() -> None:
    terminate_calls: list[bool] = []
    fake_worker = SimpleNamespace(
        isRunning=lambda: True,
        stop=lambda: None,
        wait=lambda ms=0: False,  # never stops within the timeout
        terminate=lambda: terminate_calls.append(True),
    )
    fake_self = SimpleNamespace(
        _worker=fake_worker,
        _is_connected=True,
        _session_plugin_dir=Path("does-not-exist-xyz"),
    )

    messages: list[str] = []
    sink_id = logger.add(lambda m: messages.append(str(m)), level="WARNING")
    try:
        # Call the real production method against a lightweight stand-in self;
        # stop() only touches the three attributes above.
        QtClaudeAgent.stop(fake_self)  # type: ignore[arg-type]
    finally:
        logger.remove(sink_id)

    assert terminate_calls == []
    assert fake_self._is_connected is False
    assert any("abandon" in m.lower() for m in messages)
