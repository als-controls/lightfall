"""Tests for the check result model and for applying updates."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import lightfall.acquire.engine as engine_mod
from lightfall.updates import apply as apply_mod
from lightfall.updates.apply import UpdateBlocked, apply_updates, scan_running
from lightfall.updates.git import RepoState, read_repo_state
from lightfall.updates.model import CheckResult
from lightfall.updates.pypi import PypiState

from .conftest import commit, git, make_clone


class FakeRun:
    """Records pip and import-check invocations; returns success unless told otherwise."""

    def __init__(self, returncode: int = 0, verify_returncode: int = 0):
        self.calls: list[list[str]] = []
        self.returncode = returncode
        self.verify_returncode = verify_returncode

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        if cmd[1] == "-c":
            return subprocess.CompletedProcess(
                cmd, self.verify_returncode, stdout="",
                stderr="" if self.verify_returncode == 0 else "ModuleNotFoundError: lightfall")
        return subprocess.CompletedProcess(cmd, self.returncode, stdout="pip output", stderr="")

    @property
    def pip_calls(self) -> list[list[str]]:
        return [c for c in self.calls if c[1:3] == ["-m", "pip"]]

    @property
    def verify_calls(self) -> list[list[str]]:
        return [c for c in self.calls if c[1] == "-c"]


@pytest.fixture(autouse=True)
def idle(monkeypatch):
    monkeypatch.setattr(apply_mod, "scan_running", lambda: False)


def advance(git_remote: Path, tmp_path: Path, path: str, name: str = "other") -> None:
    other = make_clone(git_remote, tmp_path / name)
    commit(other, path, f"{path} changed\n")
    git(other, "push", "origin", "master")


def fetched(repo: Path) -> RepoState:
    return read_repo_state(repo, ("lightfall",), fetch=True)


def test_apply_pulls_and_skips_reinstall_without_pyproject_change(clone, git_remote, tmp_path):
    advance(git_remote, tmp_path, "src.py")
    run = FakeRun()
    [result] = apply_updates([fetched(clone)], [], run=run)
    assert result.ok and result.changed
    assert not any("pip" in c for call in run.calls for c in call)


def test_reinstall_deferred_to_exit_by_default(clone, git_remote, tmp_path):
    # pip must not run inside the live process (Windows holds its files open).
    advance(git_remote, tmp_path, "pyproject.toml")
    run = FakeRun()
    [result] = apply_updates([fetched(clone)], [], python="py", run=run)
    assert run.calls == []
    assert result.ok and result.changed and result.restart_safe
    assert result.pending_install is not None
    assert result.pending_install.root == clone
    assert result.pending_install.dists == ("lightfall",)
    assert str(clone) in result.manual_fix


def test_apply_reinstalls_now_and_verifies_import(clone, git_remote, tmp_path):
    advance(git_remote, tmp_path, "pyproject.toml")
    run = FakeRun()
    [result] = apply_updates([fetched(clone)], [], python="py", run=run, defer_install=False)
    assert result.ok and result.pending_install is None
    assert run.pip_calls == [["py", "-m", "pip", "install", "-e", str(clone)]]
    [verify] = run.verify_calls
    assert verify[0] == "py" and verify[3:] == ["lightfall", "lightfall"]


def test_failed_reinstall_is_reported(clone, git_remote, tmp_path):
    advance(git_remote, tmp_path, "pyproject.toml")
    [result] = apply_updates([fetched(clone)], [], run=FakeRun(returncode=1),
                             defer_install=False)
    assert not result.ok and result.changed
    assert result.install_failed and not result.restart_safe
    assert result.message == "reinstall failed"
    assert "pip output" in result.output
    assert "pip install -e" in result.manual_fix


def test_failed_import_check_after_reinstall_is_reported(clone, git_remote, tmp_path):
    # pip said OK but the venv cannot import the package (e.g. the .pth went missing).
    advance(git_remote, tmp_path, "pyproject.toml")
    [result] = apply_updates([fetched(clone)], [], run=FakeRun(verify_returncode=1),
                             defer_install=False)
    assert not result.ok and result.install_failed
    assert result.message == "reinstalled package does not import"
    assert "ModuleNotFoundError" in result.output


def test_failed_import_check_after_pypi_upgrade_is_reported():
    run = FakeRun(verify_returncode=1)
    [result] = apply_updates([], [PypiState("lightfall-a", "1.0", "1.1")], python="py", run=run)
    assert not result.ok and result.install_failed
    assert result.message == "upgraded package does not import"


def test_apply_rechecks_blockers(clone, git_remote, tmp_path):
    advance(git_remote, tmp_path, "src.py")
    state = fetched(clone)
    before = git(clone, "rev-parse", "HEAD")
    (clone / "README.md").write_text("edited after the check\n", encoding="utf-8")
    [result] = apply_updates([state], [], run=FakeRun())
    assert result.ok is False and result.changed is False
    assert result.message == "uncommitted changes"
    assert git(clone, "rev-parse", "HEAD") == before


def test_partial_failure_reported_per_item(git_remote, tmp_path):
    good = make_clone(git_remote, tmp_path / "good")
    bad = make_clone(git_remote, tmp_path / "bad")
    advance(git_remote, tmp_path, "src.py")
    states = [fetched(bad), fetched(good)]
    git(bad, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    results = apply_updates(states, [], run=FakeRun())
    assert [r.ok for r in results] == [False, True]
    assert results[0].name == "bad"


def test_pypi_single_upgrade_call():
    run = FakeRun()
    results = apply_updates(
        [], [PypiState("lightfall-a", "1.0", "1.1"), PypiState("lightfall-b", "2.0", "2.1")],
        python="py", run=run,
    )
    assert run.pip_calls == [
        ["py", "-m", "pip", "install", "--upgrade", "lightfall-a", "lightfall-b"]]
    assert [c[3] for c in run.verify_calls] == ["lightfall-a", "lightfall-b"]
    assert [(r.name, r.ok, r.changed) for r in results] == [
        ("lightfall-a", True, True), ("lightfall-b", True, True)]


def test_log_receives_commands(clone, git_remote, tmp_path):
    advance(git_remote, tmp_path, "pyproject.toml")
    lines: list[str] = []
    apply_updates([fetched(clone)], [], run=FakeRun(), log=lines.append, defer_install=False)
    assert any("pip install -e" in line for line in lines)
    assert any("pip output" in line for line in lines)


def test_log_lines_reach_app_logger(clone, git_remote, tmp_path):
    # Persisted so a failure can be diagnosed after a restart.
    from lightfall.utils.logging import logger

    advance(git_remote, tmp_path, "pyproject.toml")
    records: list[str] = []
    sink = logger.add(lambda m: records.append(m.record["message"]), level="INFO")
    try:
        apply_updates([fetched(clone)], [], run=FakeRun(returncode=1), defer_install=False)
    finally:
        logger.remove(sink)
    text = "\n".join(records)
    assert "[update] $ git -C" in text
    assert "pip install -e" in text
    assert "pip output" in text
    assert "reinstall failed" in text


def test_blocked_while_scan_running(monkeypatch):
    monkeypatch.setattr(apply_mod, "scan_running", lambda: True)
    with pytest.raises(UpdateBlocked):
        apply_updates([], [PypiState("lightfall", "1", "2")], run=FakeRun())


def test_scan_running_false_without_engine(monkeypatch):
    monkeypatch.setattr(engine_mod, "_engine", None)
    assert scan_running() is False


def test_scan_running_reads_engine(monkeypatch):
    monkeypatch.setattr(engine_mod, "_engine", SimpleNamespace(is_idle=True, queue_size=1))
    assert scan_running() is True
    monkeypatch.setattr(engine_mod, "_engine", SimpleNamespace(is_idle=False, queue_size=0))
    assert scan_running() is True
    monkeypatch.setattr(engine_mod, "_engine", SimpleNamespace(is_idle=True, queue_size=0))
    assert scan_running() is False


def repo(name: str, **kw) -> RepoState:
    base = {"root": Path(name), "dists": (name,), "branch": "master",
            "default_branch": "master", "remote": "origin", "dirty": False, "head": "a",
            "ahead": 0, "behind": 0, "remote_head": "a"}
    base.update(kw)
    return RepoState(**base)


def test_check_result_counts():
    result = CheckResult(
        repos=(repo("behind", behind=2), repo("clean"), repo("feat", branch="feat/x", behind=3)),
        pypi=(PypiState("p", "1.0", "1.1"), PypiState("q", "1.0", "1.0")),
    )
    assert [r.name for r in result.repo_updates] == ["behind"]
    assert [p.name for p in result.pypi_updates] == ["p"]
    assert result.update_count == 2
    assert [r.name for r in result.noteworthy_repos] == ["feat"]
    assert CheckResult.EMPTY.update_count == 0


def test_run_check_skips_repos_when_git_missing(monkeypatch):
    from lightfall.updates import model

    monkeypatch.setattr(model, "discover_installed", lambda: [])
    monkeypatch.setattr(model.git_mod, "GIT_MISSING", True)
    assert model.run_check(fetch=False) == CheckResult.EMPTY


def test_python_default_is_sys_executable():
    import inspect

    assert inspect.signature(apply_updates).parameters["python"].default == sys.executable


def test_reinstall_check_error_is_contained(git_remote, tmp_path, monkeypatch):
    from lightfall.updates.git import GitError

    one = make_clone(git_remote, tmp_path / "one")
    two = make_clone(git_remote, tmp_path / "two")
    advance(git_remote, tmp_path, "src.py")

    def broken(*a):
        raise GitError("diff timed out")

    monkeypatch.setattr(apply_mod, "pyproject_changed", broken)
    results = apply_updates([fetched(one), fetched(two)], [], run=FakeRun())
    assert [(r.ok, r.changed) for r in results] == [(False, True), (False, True)]
    assert results[0].message == "reinstall check failed"


def test_pip_runs_without_console_window():
    seen = {}

    def run(cmd, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    apply_updates([], [PypiState("lightfall", "1", "2")], run=run)
    assert seen.get("creationflags", 0) == getattr(subprocess, "CREATE_NO_WINDOW", 0)
