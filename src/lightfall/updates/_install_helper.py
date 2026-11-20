"""Finish a Lightfall update after Lightfall has exited, then start it again.

Run as ``python -I _install_helper.py SPEC.json`` from a copy outside the
package. It uses only the standard library so it imports nothing from the venv
it is reinstalling. Running ``pip install -e`` from inside the live process
fails on Windows, which holds the running Scripts launchers and loaded files
open. That failure left the venv without lightfall's ``.pth`` on 2026-11-20.

The spec is a JSON object::

    {"pid": 1234,                 # wait for this process to exit first
     "python": "...python.exe",   # the venv interpreter
     "installs": [{"root": "...", "dists": ["lightfall"],
                   "modules": {"lightfall": ["lightfall"]}}],
     "relaunch": ["..."] | null,  # argv to start once the installs verify
     "cwd": "...",
     "log": "...update.log",
     "grace": 1.0}                # seconds to wait after the process exits

Every command and its output go to ``log``. If a reinstalled package does not
import cleanly afterwards, Lightfall is not relaunched. On Windows a message box then
shows the manual ``pip install -e`` command.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Callable

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

#: ``python -c`` body run as ``VERIFY_CODE DIST [MODULE ...]``: the dist's metadata
#: must exist and each module must import.
VERIFY_CODE = (
    "import importlib, importlib.metadata, sys; "
    "importlib.metadata.distribution(sys.argv[1]); "
    "[importlib.import_module(m) for m in sys.argv[2:]]"
)

Runner = Callable[..., subprocess.CompletedProcess]


def verify_command(python: str, dist: str, modules: list[str] | tuple[str, ...]) -> list[str]:
    return [python, "-c", VERIFY_CODE, dist, *modules]


def pip_install_command(python: str, root: str) -> list[str]:
    return [python, "-m", "pip", "install", "-e", str(root)]


def wait_for_exit(pid: int, timeout: float = 120.0) -> bool:
    """Wait until process ``pid`` has exited; False if it is still running at ``timeout``."""
    if sys.platform == "win32":
        import ctypes

        synchronize = 0x00100000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            return True  # already gone
        try:
            return kernel32.WaitForSingleObject(handle, int(timeout * 1000)) == 0
        finally:
            kernel32.CloseHandle(handle)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            pass
        time.sleep(0.2)
    return False


def notify(text: str) -> None:
    """Tell the user something went wrong (a message box on Windows; stderr elsewhere)."""
    if sys.platform == "win32":
        import ctypes

        mb_iconerror, mb_topmost = 0x10, 0x40000
        ctypes.windll.user32.MessageBoxW(None, text, "Lightfall update", mb_iconerror | mb_topmost)
    else:
        print(text, file=sys.stderr)


def launch(argv: list[str], cwd: str | None) -> None:
    kwargs: dict = {"close_fds": True, "cwd": cwd or None, "stdin": subprocess.DEVNULL,
                    "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(argv, **kwargs)


class _Log:
    def __init__(self, path: str | None):
        self._fh = open(path, "a", encoding="utf-8") if path else None

    def __call__(self, line: str) -> None:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        if self._fh is not None:
            self._fh.write(f"{stamp} {line}\n")
            self._fh.flush()

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()


def _run(cmd: list[str], run: Runner, log: Callable[[str], None]) -> bool:
    log("$ " + subprocess.list2cmdline(cmd))
    try:
        proc = run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                   creationflags=NO_WINDOW, timeout=1800)
    except (OSError, subprocess.TimeoutExpired) as e:
        log(f"failed to run: {e}")
        return False
    output = ((proc.stdout or "") + (proc.stderr or "")).rstrip()
    if output:
        log(output)
    log(f"exit code {proc.returncode}")
    return proc.returncode == 0


def finish(
    spec: dict,
    run: Runner = subprocess.run,
    wait: Callable[[int], bool] = wait_for_exit,
    notify: Callable[[str], None] = notify,
    launch: Callable[[list[str], str | None], None] = launch,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Install, verify, relaunch. 0 = all good, 1 = an install failed, 2 = Lightfall never exited."""
    log = _Log(spec.get("log"))
    try:
        python = spec["python"]
        log(f"update helper started (waiting for pid {spec['pid']})")
        if not wait(int(spec["pid"])):
            log("Lightfall did not exit; nothing was installed")
            notify("Lightfall did not exit, so its update was not installed.\n\n"
                   f"Log: {spec.get('log')}")
            return 2
        sleep(float(spec.get("grace", 1.0)))
        broken: list[str] = []
        pip_failed: list[str] = []
        for item in spec.get("installs", []):
            root = item["root"]
            ok = _run(pip_install_command(python, root), run, log)
            if not ok:
                # A launcher that has not quite exited can still hold a file; retry once.
                sleep(3.0)
                ok = _run(pip_install_command(python, root), run, log)
            if not ok:
                pip_failed.append(root)
            for dist in item.get("dists", []):
                modules = (item.get("modules") or {}).get(dist, [])
                if not _run(verify_command(python, dist, modules), run, log):
                    log(f"verify failed: {dist} is not importable after reinstall")
                    broken.append(root)
                    break
        manual = "\n".join(
            subprocess.list2cmdline(pip_install_command(python, root))
            for root in dict.fromkeys(broken + pip_failed))
        if broken:
            log("not relaunching: the environment is broken")
            notify("Lightfall's update left the Python environment unable to import "
                   "Lightfall, so it was not restarted.\n\nRun this, then start "
                   f"Lightfall again:\n\n{manual}\n\nLog: {spec.get('log')}")
            return 1
        relaunch = spec.get("relaunch")
        if relaunch:
            log("$ " + subprocess.list2cmdline(relaunch))
            launch(list(relaunch), spec.get("cwd"))
        if pip_failed:
            notify("pip reported an error while finishing Lightfall's update, though "
                   f"Lightfall still imports. To retry:\n\n{manual}\n\nLog: {spec.get('log')}")
            return 1
        log("update helper finished")
        return 0
    except Exception as e:  # never die silently: the user would just see Lightfall vanish
        log(f"update helper crashed: {e!r}")
        notify(f"Lightfall's update helper failed: {e!r}\n\nLog: {spec.get('log')}")
        return 1
    finally:
        log.close()


def main(argv: list[str]) -> int:
    with open(argv[1], encoding="utf-8") as fh:
        spec = json.load(fh)
    return finish(spec)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
