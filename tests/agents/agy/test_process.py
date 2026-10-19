"""AgyProcess: the child's descendants die with it (Job object / process group)."""
from __future__ import annotations

import asyncio
import os
import signal
import sys
import time
from types import SimpleNamespace

import pytest

from lightfall.agents.backends.agy import process as agy_process
from lightfall.agents.backends.agy.home import pid_alive
from lightfall.agents.backends.agy.process import AgyProcess

# Stands in for agy: starts a sleeping grandchild, reports its pid, then waits.
_PARENT = """
import json, subprocess, sys, time
g = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
print(json.dumps({"gpid": g.pid}), flush=True)
time.sleep(120)
"""


def _wait_dead(pid: int, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.1)
    return False


def _force_kill(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass


@pytest.mark.skipif(sys.platform != "win32", reason="Job object is Windows only")
@pytest.mark.parametrize("how", ["kill", "interrupt"])
def test_windows_grandchild_dies_with_child(tmp_path, how):
    script = tmp_path / "parent.py"
    script.write_text(_PARENT, encoding="utf-8")
    proc = AgyProcess([sys.executable, str(script)], tmp_path, str(tmp_path), {},
                      model=None, conversation=None)

    async def go() -> int:
        await proc.start()
        ev = await asyncio.wait_for(proc.read_event(), 30)
        assert ev is not None
        if how == "interrupt":
            proc.interrupt()
            assert await proc.wait(5)
        await proc.kill()
        return ev["gpid"]

    gpid = asyncio.run(go())
    try:
        assert _wait_dead(gpid), "grandchild outlived the agy child"
    finally:
        _force_kill(gpid)


def test_job_setup_failure_is_a_logged_noop(monkeypatch):
    from lightfall.utils.logging import logger

    msgs: list[str] = []
    sink = logger.add(lambda m: msgs.append(str(m)), level="DEBUG")

    def boom(cls, pid):
        raise OSError("no jobs here")

    monkeypatch.setattr(agy_process.KillOnCloseJob, "_attach", classmethod(boom))
    try:
        assert agy_process.KillOnCloseJob.attach(1234) is None
    finally:
        logger.remove(sink)
    assert any("no kill-on-close job" in m and "DEBUG" in m for m in msgs)


class _FakeProc:
    def __init__(self, pid: int = 4321) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.stdin = None
        self.stderr = None
        self.calls: list[str] = []

    def terminate(self) -> None:
        self.calls.append("terminate")

    def kill(self) -> None:
        self.calls.append("kill")

    async def wait(self) -> int:
        while self.returncode is None:
            await asyncio.sleep(0.01)
        return self.returncode


def _posix(monkeypatch):
    """Run the POSIX branch on any OS: fake spawn, recorded killpg."""
    monkeypatch.setattr(agy_process.sys, "platform", "linux")
    monkeypatch.setattr(agy_process, "INTERRUPT_KILLS", False)
    seen: dict = {}
    fake = _FakeProc()
    killpg: list[tuple[int, int]] = []

    async def spawn(*args, **kwargs):
        seen.update(kwargs)
        return fake

    monkeypatch.setattr(agy_process.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(agy_process.os, "killpg",
                        lambda pgid, sig: killpg.append((pgid, sig)), raising=False)
    monkeypatch.setattr(agy_process.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(AgyProcess, "_drain_stderr", lambda self: asyncio.sleep(0))
    return SimpleNamespace(seen=seen, proc=fake, killpg=killpg)


def test_posix_child_gets_own_process_group_and_kill_signals_it(tmp_path, monkeypatch):
    env = _posix(monkeypatch)
    proc = AgyProcess("agy", tmp_path, str(tmp_path), {}, model=None, conversation=None)

    async def go():
        await proc.start()

        async def exit_soon():
            await asyncio.sleep(0.05)
            env.proc.returncode = -15

        t = asyncio.get_running_loop().create_task(exit_soon())
        await proc.kill()
        await t

    asyncio.run(go())
    assert env.seen["process_group"] == 0
    assert "creationflags" not in env.seen
    assert env.killpg == [(4321, signal.SIGTERM), (4321, 9)]  # TERM, then sweep stragglers
    assert env.proc.calls == []  # the group signal replaced the per-child one


def test_posix_kill_escalates_to_sigkill_on_the_group(tmp_path, monkeypatch):
    env = _posix(monkeypatch)
    monkeypatch.setattr(agy_process, "_TERM_WAIT_S", 0.05)
    proc = AgyProcess("agy", tmp_path, str(tmp_path), {}, model=None, conversation=None)

    def killpg(pgid, sig):
        env.killpg.append((pgid, sig))
        if sig == 9:
            env.proc.returncode = -9

    monkeypatch.setattr(agy_process.os, "killpg", killpg, raising=False)

    async def go():
        await proc.start()
        await proc.kill()

    asyncio.run(go())
    assert env.killpg[:2] == [(4321, signal.SIGTERM), (4321, 9)]


def test_posix_falls_back_to_child_when_group_is_gone(tmp_path, monkeypatch):
    env = _posix(monkeypatch)
    proc = AgyProcess("agy", tmp_path, str(tmp_path), {}, model=None, conversation=None)

    def killpg(pgid, sig):
        raise ProcessLookupError

    monkeypatch.setattr(agy_process.os, "killpg", killpg, raising=False)

    async def go():
        await proc.start()
        env.proc.returncode = None

        async def exit_soon():
            await asyncio.sleep(0.05)
            env.proc.returncode = -15

        t = asyncio.get_running_loop().create_task(exit_soon())
        await proc.kill()
        await t

    asyncio.run(go())
    assert env.proc.calls == ["terminate"]


def test_posix_interrupt_still_sends_sigint_to_the_child_only(tmp_path, monkeypatch):
    env = _posix(monkeypatch)
    sent: list[int] = []
    env.proc.send_signal = sent.append
    proc = AgyProcess("agy", tmp_path, str(tmp_path), {}, model=None, conversation=None)

    async def go():
        await proc.start()
        proc.interrupt()

    asyncio.run(go())
    assert sent == [signal.SIGINT]
    assert env.killpg == []
