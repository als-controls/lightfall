"""One ``agy`` child process in persistent stream-json mode.

agy reads one NDJSON ``user`` line per turn on stdin and writes NDJSON events on
stdout.

Interrupt: on POSIX ``SIGINT`` makes agy report ``status=ERROR error=interrupted``
and exit.  On Windows the GUI (pythonw, no console) cannot deliver
``CTRL_BREAK_EVENT`` -- ``os.kill`` fails and falls back to TerminateProcess -- so
the child is spawned with ``CREATE_NO_WINDOW`` and interrupt terminates it outright
(verified live: a hard kill mid-turn then ``--conversation <id>`` resumes with
context).  Either way the caller respawns with ``--conversation``.

Process tree: the child's descendants (hook commands and anything they start) die
with it.  Windows puts the child in a Job object with KILL_ON_JOB_CLOSE (which
also reaps the tree if Lightfall itself dies); POSIX gives it its own process
group and signals the group.  If that setup fails only the direct child is
killed, as before.

This module must not import ``claude_agent_sdk``.
"""
from __future__ import annotations

import asyncio
import collections
import contextlib
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

from lightfall.utils.logging import logger

_STREAM_LIMIT = 16 * 1024 * 1024
_STDERR_LINES = 50
_TERM_WAIT_S = 3.0
_KILL_WAIT_S = 2.0

#: True where interrupt() kills the child instead of signalling it.
INTERRUPT_KILLS = sys.platform == "win32"


class KillOnCloseJob:
    """Windows Job object that kills every process in it when terminated or closed.

    :meth:`attach` returns ``None`` (logged at DEBUG) when the job cannot be set
    up; the caller then kills only the direct child.
    """

    _KILL_ON_JOB_CLOSE = 0x2000
    _EXTENDED_LIMIT_INFORMATION = 9
    _PROCESS_SET_QUOTA = 0x0100
    _PROCESS_TERMINATE = 0x0001

    def __init__(self, handle: int, k32: Any) -> None:
        self._handle: int | None = handle
        self._k32 = k32

    @classmethod
    def attach(cls, pid: int) -> KillOnCloseJob | None:
        try:
            return cls._attach(pid)
        except Exception as e:  # noqa: BLE001 - best effort, never fatal
            logger.debug("agy: no kill-on-close job for pid={}: {}", pid, e)
            return None

    @classmethod
    def _attach(cls, pid: int) -> KillOnCloseJob:
        import ctypes
        from ctypes import wintypes

        class _Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                        ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class _Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", _Basic),
                        ("IoInfo", ctypes.c_uint64 * 6),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                ctypes.c_void_p, wintypes.DWORD]
        k32.SetInformationJobObject.restype = wintypes.BOOL
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.AssignProcessToJobObject.restype = wintypes.BOOL
        k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k32.TerminateJobObject.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.restype = wintypes.BOOL

        job = k32.CreateJobObjectW(None, None)
        if not job:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            info = _Extended()
            info.BasicLimitInformation.LimitFlags = cls._KILL_ON_JOB_CLOSE
            if not k32.SetInformationJobObject(job, cls._EXTENDED_LIMIT_INFORMATION,
                                               ctypes.byref(info), ctypes.sizeof(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            proc = k32.OpenProcess(cls._PROCESS_SET_QUOTA | cls._PROCESS_TERMINATE, False, pid)
            if not proc:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                if not k32.AssignProcessToJobObject(job, proc):
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                k32.CloseHandle(proc)
        except BaseException:
            k32.CloseHandle(job)
            raise
        return cls(job, k32)

    def terminate(self) -> None:
        """Kill every process in the job now (the job stays open)."""
        if self._handle is not None and not self._k32.TerminateJobObject(self._handle, 1):
            logger.debug("agy: TerminateJobObject failed")

    def close(self) -> None:
        """Close the job; KILL_ON_JOB_CLOSE kills whatever is still in it. Idempotent."""
        handle, self._handle = self._handle, None
        if handle is not None:
            self._k32.CloseHandle(handle)


class AgyProcess:
    """Spawn and talk to one agy child."""

    def __init__(self, agy: str | list[str], home: Path, cwd: str, env_extra: dict[str, str],
                 model: str | None, conversation: str | None,
                 effort: str | None = None) -> None:
        self._argv0 = [agy] if isinstance(agy, str) else list(agy)
        self._home = Path(home)
        self._cwd = cwd
        self._env_extra = dict(env_extra)
        self._model = model
        self._effort = effort  # already validated for the model (backend.effort_flag)
        self._conversation = conversation
        self._proc: asyncio.subprocess.Process | None = None
        self._stderr: collections.deque[str] = collections.deque(maxlen=_STDERR_LINES)
        self._stderr_task: asyncio.Task[None] | None = None
        self._interrupted = False
        self._job: KillOnCloseJob | None = None  # Windows
        self._pgid: int | None = None  # POSIX

    @property
    def argv(self) -> list[str]:
        return [
            *self._argv0,
            "--agent", "operator",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--print=",
            *(["--model", self._model] if self._model else []),
            *(["--effort", self._effort] if self._effort else []),
            *(["--conversation", self._conversation] if self._conversation else []),
        ]

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc is not None else None

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    @property
    def stderr_tail(self) -> str:
        return "\n".join(self._stderr)

    async def start(self) -> None:
        env = {**os.environ, "USERPROFILE": str(self._home), "HOME": str(self._home),
               **self._env_extra}
        kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = (subprocess.CREATE_NEW_PROCESS_GROUP
                                       | subprocess.CREATE_NO_WINDOW)
        else:
            kwargs["process_group"] = 0  # own group, so kill() reaches grandchildren
        self._proc = await asyncio.create_subprocess_exec(
            *self.argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._cwd,
            env=env,
            limit=_STREAM_LIMIT,
            **kwargs,
        )
        if sys.platform == "win32":
            self._job = KillOnCloseJob.attach(self._proc.pid)
        else:
            self._pgid = self._proc.pid
        self._stderr_task = asyncio.get_running_loop().create_task(
            self._drain_stderr(), name=f"agy-stderr:{self._proc.pid}")
        logger.info("agy started pid={} model={} effort={} conversation={}",
                    self._proc.pid, self._model, self._effort, self._conversation)

    async def _drain_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        stream = self._proc.stderr
        try:
            while True:
                line = await stream.readline()
                if not line:
                    return
                text = line.decode("utf-8", errors="replace").rstrip("\r\n")
                if text:
                    self._stderr.append(text)
                    logger.debug("agy stderr: {}", text)
        except (asyncio.CancelledError, ValueError, OSError):
            return

    async def wait_stderr(self, timeout: float) -> None:
        """Give the stderr drain a moment to catch the child's last words."""
        task = self._stderr_task
        if task is not None and not task.done():
            await asyncio.wait({task}, timeout=timeout)

    async def send(self, text: str) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("agy process not started")
        line = json.dumps({"event": "user", "message": {"role": "user", "content": text}})
        self._proc.stdin.write((line + "\n").encode("utf-8"))
        await self._proc.stdin.drain()

    async def read_event(self) -> dict | None:
        """Next JSON object from stdout; ``None`` at EOF. Non-JSON lines are skipped."""
        if self._proc is None or self._proc.stdout is None:
            return None
        stream = self._proc.stdout
        while True:
            try:
                line = await stream.readline()
            except ValueError as e:  # a line longer than the stream limit
                logger.warning("agy stdout line skipped: {}", e)
                continue
            if not line:
                return None
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                obj = json.loads(text)
            except ValueError:
                logger.debug("agy non-JSON stdout: {}", text[:300])
                continue
            if isinstance(obj, dict):
                return obj
            logger.debug("agy non-object stdout: {}", text[:300])

    def interrupt(self) -> None:
        """Stop the current turn (idempotent). Windows: terminate; POSIX: SIGINT."""
        if not self.alive or self._interrupted:
            return
        assert self._proc is not None
        self._interrupted = True
        try:
            if INTERRUPT_KILLS:
                self._signal_tree(kill=False)
            else:
                self._proc.send_signal(signal.SIGINT)
        except (OSError, ProcessLookupError) as e:
            logger.warning("agy interrupt failed (pid={}): {}", self._proc.pid, e)

    async def wait(self, timeout: float) -> bool:
        """Wait up to ``timeout`` s for the child to exit; True if it has."""
        if self._proc is None:
            return True
        try:
            await asyncio.wait_for(self._proc.wait(), timeout)
        except TimeoutError:
            return False
        return True

    def _signal_tree(self, *, kill: bool) -> None:
        """Terminate (or kill) the child and its descendants; only the child when no
        job/group was set up. Raises OSError/ProcessLookupError like Popen does."""
        assert self._proc is not None
        if self._job is not None:
            self._job.terminate()
        elif self._pgid is not None:
            try:
                os.killpg(self._pgid, signal.SIGKILL if kill else signal.SIGTERM)
                return
            except (ProcessLookupError, PermissionError):
                pass  # group gone / not ours: fall back to the child itself
        if kill:
            self._proc.kill()
        else:
            self._proc.terminate()

    async def kill(self) -> None:
        """Terminate, wait up to 3 s, then kill (the whole tree). Bounded and idempotent."""
        proc = self._proc
        if proc is None:
            return
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError, OSError):
                self._signal_tree(kill=False)
            if not await self.wait(_TERM_WAIT_S):
                with contextlib.suppress(ProcessLookupError, OSError):
                    self._signal_tree(kill=True)
                if not await self.wait(_KILL_WAIT_S):
                    logger.warning("agy pid={} did not exit after kill", proc.pid)
            if self._pgid is not None:
                # Descendants that ignored SIGTERM (the leader may have exited first).
                with contextlib.suppress(OSError):
                    os.killpg(self._pgid, signal.SIGKILL)
        self._pgid = None
        job, self._job = self._job, None
        if job is not None:
            job.close()  # KILL_ON_JOB_CLOSE: reaps anything left in the tree
        if proc.stdin is not None:
            with contextlib.suppress(Exception):
                proc.stdin.close()
        task = self._stderr_task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.wait({task}, timeout=0.5)
