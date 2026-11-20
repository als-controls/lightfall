"""Deferred reinstalls: the exit-time helper, its spec, and the restart hook.

No test here runs a real pip; the helper is driven with fake run/wait/launch.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from lightfall.updates import _install_helper as helper
from lightfall.updates import deferred
from lightfall.updates.deferred import PendingInstall
from lightfall.utils import restart


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    deferred.clear()
    monkeypatch.setattr(restart, "_EXIT_COMMAND", None)
    monkeypatch.setattr(restart, "_RESTART_REQUESTED", False)
    monkeypatch.setattr(restart, "_RELAUNCHED", False)
    yield
    deferred.clear()


class Runner:
    def __init__(self, pip_codes=(0,), verify_code=0):
        self.calls: list[list[str]] = []
        self.pip_codes = list(pip_codes)
        self.verify_code = verify_code

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        if cmd[1] == "-c":
            return subprocess.CompletedProcess(cmd, self.verify_code, "", "verify says no")
        code = self.pip_codes.pop(0) if self.pip_codes else 0
        return subprocess.CompletedProcess(cmd, code, "pip says hi", "")


def spec(tmp_path: Path, relaunch=("py", "-m", "lightfall")) -> dict:
    return {"pid": 4242, "python": "py",
            "installs": [{"root": "/src/lightfall", "dists": ["lightfall"],
                          "modules": {"lightfall": ["lightfall"]}}],
            "relaunch": list(relaunch) if relaunch else None, "cwd": str(tmp_path),
            "log": str(tmp_path / "update.log"), "grace": 0}


def finish(s, run, **kw):
    launched, notes = [], []
    kw.setdefault("wait", lambda pid: True)
    code = helper.finish(s, run=run, notify=notes.append,
                         launch=lambda argv, cwd: launched.append(argv),
                         sleep=lambda t: None, **kw)
    return code, launched, notes


def test_helper_installs_verifies_and_relaunches(tmp_path):
    run = Runner()
    code, launched, notes = finish(spec(tmp_path), run)
    assert code == 0 and notes == []
    assert run.calls[0] == ["py", "-m", "pip", "install", "-e", "/src/lightfall"]
    assert run.calls[1][:2] == ["py", "-c"] and run.calls[1][3:] == ["lightfall", "lightfall"]
    assert launched == [["py", "-m", "lightfall"]]
    log = (tmp_path / "update.log").read_text(encoding="utf-8")
    assert "pip install -e /src/lightfall" in log and "pip says hi" in log


def test_helper_does_not_relaunch_into_broken_env(tmp_path):
    code, launched, notes = finish(spec(tmp_path), Runner(pip_codes=(1, 1), verify_code=1))
    assert code == 1
    assert launched == []
    assert notes and "pip install -e /src/lightfall" in notes[0]
    assert "verify failed" in (tmp_path / "update.log").read_text(encoding="utf-8")


def test_helper_retries_pip_once(tmp_path):
    run = Runner(pip_codes=(1, 0))
    code, launched, notes = finish(spec(tmp_path), run)
    assert code == 0 and launched and notes == []
    assert sum(c[1:3] == ["-m", "pip"] for c in run.calls) == 2


def test_helper_pip_error_but_importable_relaunches_and_warns(tmp_path):
    code, launched, notes = finish(spec(tmp_path), Runner(pip_codes=(1, 1)))
    assert code == 1 and launched and notes


def test_helper_waits_for_lightfall_to_exit(tmp_path):
    run = Runner()
    code, launched, notes = finish(spec(tmp_path), run, wait=lambda pid: False)
    assert code == 2 and run.calls == [] and launched == [] and notes


def test_helper_without_relaunch_only_installs(tmp_path):
    code, launched, _ = finish(spec(tmp_path, relaunch=None), Runner())
    assert code == 0 and launched == []


def test_helper_is_stdlib_only():
    # It runs while the venv is half-reinstalled, so it must not import lightfall or deps.
    import ast

    tree = ast.parse(Path(helper.__file__).read_text(encoding="utf-8"))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imports.add((node.module or "").split(".")[0])
    assert imports <= set(sys.stdlib_module_names) | {"__future__"}


def test_exit_command_writes_spec_and_copies_helper(tmp_path):
    deferred.schedule([PendingInstall(Path("/src/lightfall"), ("lightfall",))])
    cmd = deferred.exit_command(["py", "-m", "lightfall"], python="py", pid=7,
                                workdir=tmp_path, logs=tmp_path / "logs")
    assert cmd[:2] == ["py", "-I"]
    script, spec_path = Path(cmd[2]), Path(cmd[3])
    assert script.parent == tmp_path and script.read_text(encoding="utf-8") == Path(
        helper.__file__).read_text(encoding="utf-8")
    data = json.loads(spec_path.read_text(encoding="utf-8"))
    assert data["pid"] == 7 and data["relaunch"] == ["py", "-m", "lightfall"]
    assert data["installs"][0]["root"] == str(Path("/src/lightfall"))
    assert data["installs"][0]["modules"] == {"lightfall": ["lightfall"]}
    assert Path(data["log"]).parent == tmp_path / "logs"
    assert deferred.pending() == []  # consumed
    assert deferred.exit_command(None, workdir=tmp_path) is None


def test_helper_spec_runs_end_to_end(tmp_path):
    # The copied script parses a real spec file; nothing to install, no relaunch.
    done = subprocess.Popen([sys.executable, "-c", "pass"])  # an already-exited pid
    done.wait()
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps({"pid": done.pid, "python": sys.executable, "installs": [],
                                     "relaunch": None, "log": str(tmp_path / "x.log"),
                                     "grace": 0}), encoding="utf-8")
    proc = subprocess.run([sys.executable, "-I", helper.__file__, str(spec_path)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert "update helper finished" in (tmp_path / "x.log").read_text(encoding="utf-8")


def test_schedule_registers_exit_command():
    deferred.schedule([PendingInstall(Path("/src/x"), ("x",))])
    assert restart._EXIT_COMMAND is deferred.exit_command


def test_restart_runs_exit_command_with_relaunch_argv(monkeypatch):
    seen, spawned = [], []
    monkeypatch.setattr(sys, "orig_argv", ["py", "-m", "lightfall"])
    monkeypatch.setattr(restart, "relaunch", lambda args=None: spawned.append(args) or True)
    restart.set_exit_command(lambda argv: seen.append(argv) or ["helper"])
    restart._RESTART_REQUESTED = True
    assert restart.relaunch_if_requested() is True
    assert seen == [["py", "-m", "lightfall"]] and spawned == [["helper"]]
    assert restart.relaunch_if_requested() is False  # once only
    assert spawned == [["helper"]]


def test_plain_quit_still_runs_exit_command(monkeypatch):
    seen, spawned = [], []
    monkeypatch.setattr(restart, "relaunch", lambda args=None: spawned.append(args) or True)
    restart.set_exit_command(lambda argv: seen.append(argv) or ["helper"])
    assert restart.relaunch_if_requested() is False
    assert seen == [None] and spawned == [["helper"]]


def test_exit_command_error_falls_back_to_plain_relaunch(monkeypatch):
    spawned = []
    monkeypatch.setattr(restart, "relaunch", lambda args=None: spawned.append(args) or True)

    def boom(argv):
        raise OSError("disk full")

    restart.set_exit_command(boom)
    restart._RESTART_REQUESTED = True
    assert restart.relaunch_if_requested() is True
    assert spawned == [None]
