"""Shutdown mutes loggers whose errors at exit are expected noise.

GlitchTip LIGHTFALL-5: at interpreter exit the Claude Agent SDK's own atexit
hook terminates every Claude CLI it spawned (TerminateProcess, exit code 1 on
Windows). Lightfall never stops its Claude sessions on app exit, so each live
session's SDK message reader sees the kill and logs "Fatal error in message
reader: Command failed with exit code 1" at ERROR, which Sentry reports.
"""

from __future__ import annotations

import logging

import pytest

from lightfall.main import _SHUTDOWN_MUTED_LOGGERS, _mute_shutdown_loggers

SDK_READER_LOGGER = "claude_agent_sdk._internal.query"


@pytest.fixture
def restore_levels():
    names = (*_SHUTDOWN_MUTED_LOGGERS, SDK_READER_LOGGER)
    saved = {name: logging.getLogger(name).level for name in names}
    yield
    for name, level in saved.items():
        logging.getLogger(name).setLevel(level)


def test_shutdown_mutes_claude_sdk_reader_exit_error(restore_levels, caplog):
    caplog.set_level(logging.DEBUG)

    _mute_shutdown_loggers()

    logging.getLogger(SDK_READER_LOGGER).error(
        "Fatal error in message reader: Command failed with exit code 1 "
        "(exit code: 1)\nError output: Check stderr output for details"
    )
    assert [r for r in caplog.records if r.name == SDK_READER_LOGGER] == []


def test_shutdown_still_mutes_caproto(restore_levels):
    _mute_shutdown_loggers()

    assert not logging.getLogger("caproto.circ").isEnabledFor(logging.ERROR)
