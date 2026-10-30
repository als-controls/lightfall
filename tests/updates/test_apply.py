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
    """Records pip invocations; returns success unless told otherwise."""

    def __init__(self, returncode: int = 0):
        self.calls: list[list[str]] = []
        self.returncode = returncode

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, self.returncode, stdout="pip output", stderr="")


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


def test_apply_reinstalls_on_pyproject_change(clone, git_remote, tmp_path):
    advance(git_remote, tmp_path, "pyproject.toml")
    run = FakeRun()
    [result] = apply_updates([fetched(clone)], [], python="py", run=run)
    assert result.ok
    assert run.calls == [["py", "-m", "pip", "install", "-e", str(clone)]]


def test_failed_reinstall_is_reported(clone, git_remote, tmp_path):
    advance(git_remote, tmp_path, "pyproject.toml")
    [result] = apply_updates([fetched(clone)], [], run=FakeRun(returncode=1))
    assert not result.ok and result.changed
    assert "pip output" in result.output


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
    assert run.calls == [["py", "-m", "pip", "install", "--upgrade", "lightfall-a", "lightfall-b"]]
    assert [(r.name, r.ok, r.changed) for r in results] == [
        ("lightfall-a", True, True), ("lightfall-b", True, True)]


def test_log_receives_commands(clone, git_remote, tmp_path):
    advance(git_remote, tmp_path, "pyproject.toml")
    lines: list[str] = []
    apply_updates([fetched(clone)], [], run=FakeRun(), log=lines.append)
    assert any("pip install -e" in line for line in lines)


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
