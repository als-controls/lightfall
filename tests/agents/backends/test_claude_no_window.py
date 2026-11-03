"""The SDK must spawn the ``claude`` CLI with CREATE_NO_WINDOW on Windows.

Under ``lightfall-gui.exe`` Lightfall has no console, so a console child
spawned without ``CREATE_NO_WINDOW`` pops up a visible console window for
every agent session.
"""

from __future__ import annotations

import subprocess
import sys

import anyio
import pytest

from lightfall.agents.backends.claude_code import no_window

win_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only behaviour")


@win_only
def test_importing_the_backend_patches_the_sdk_transport():
    from claude_agent_sdk._internal.transport import subprocess_cli

    import lightfall.agents.backends.claude_code  # noqa: F401

    assert getattr(subprocess_cli.anyio, "_lightfall_no_window", False)
    assert no_window.patch_sdk_no_console_window() is True  # idempotent
    # Everything other than open_process goes to the real anyio.
    assert subprocess_cli.anyio.fail_after is anyio.fail_after


@win_only
def test_open_process_adds_create_no_window(monkeypatch):
    seen: dict = {}

    async def fake_open_process(*args, **kwargs):
        seen.update(kwargs)
        return "proc"

    monkeypatch.setattr(anyio, "open_process", fake_open_process)
    proxy = no_window._NoWindowAnyio()

    assert anyio.run(lambda: proxy.open_process(["claude"], stdin=subprocess.PIPE)) == "proc"
    assert seen["creationflags"] & subprocess.CREATE_NO_WINDOW
    assert seen["stdin"] == subprocess.PIPE

    seen.clear()
    anyio.run(lambda: proxy.open_process(["claude"], creationflags=subprocess.CREATE_NEW_PROCESS_GROUP))
    assert seen["creationflags"] == subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW


def test_patch_is_a_noop_off_windows(monkeypatch):
    monkeypatch.setattr(no_window.sys, "platform", "linux")
    assert no_window.patch_sdk_no_console_window() is False
