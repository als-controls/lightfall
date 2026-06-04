"""Tests for UserPlanService git tracking integration."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lightfall.acquire.plans.user_plans import UserPlanService
from lightfall.utils.git_tracker import GitTracker


@pytest.fixture(autouse=True)
def reset_singletons():
    UserPlanService.reset_instance()
    GitTracker.reset_instance()
    yield
    UserPlanService.reset_instance()
    GitTracker.reset_instance()


@pytest.fixture
def tracked_plan_dir(tmp_path, monkeypatch):
    GitTracker.reset_instance()
    repo_root = tmp_path / "lightfall"
    repo_root.mkdir()
    plans_dir = repo_root / "plans"
    plans_dir.mkdir()

    tracker = GitTracker(repo_root=repo_root)
    monkeypatch.setattr(GitTracker, "_instance", tracker)
    yield plans_dir
    # monkeypatch handles cleanup


def _write_user_plan(dir_: Path, name: str) -> Path:
    path = dir_ / f"{name}.py"
    path.write_text(
        '''"""user plan."""
from __future__ import annotations
import bluesky.plans as bp
def plan(detectors, motor, start=-1.0, stop=1.0, num=3):
    yield from bp.scan(detectors, motor, start, stop, num)
''',
        encoding="utf-8",
    )
    return path


def _git_log_subjects(repo_root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "log", "--format=%s"],
        cwd=repo_root, capture_output=True, text=True,
    )
    return [line for line in out.stdout.splitlines() if line] if out.returncode == 0 else []


def test_load_plan_commits_with_supplied_message(tracked_plan_dir, monkeypatch):
    service = UserPlanService.get_instance()
    monkeypatch.setattr(service, "_plans_dir", tracked_plan_dir)

    path = _write_user_plan(tracked_plan_dir, "my_scan")
    service.load_plan_from_file(path, commit_msg="agent: create my_scan")

    assert _git_log_subjects(tracked_plan_dir.parent) == ["agent: create my_scan"]


def test_create_new_plan_commits_template(tracked_plan_dir, monkeypatch):
    service = UserPlanService.get_instance()
    monkeypatch.setattr(service, "_plans_dir", tracked_plan_dir)
    monkeypatch.setattr(service, "_ensure_directory", lambda: True)

    service.create_new_plan("seeded", "Seeded plan", commit_msg="agent: seed plan")

    assert _git_log_subjects(tracked_plan_dir.parent) == ["agent: seed plan"]


def test_external_plan_delete_commits_removal(tracked_plan_dir, monkeypatch):
    service = UserPlanService.get_instance()
    monkeypatch.setattr(service, "_plans_dir", tracked_plan_dir)

    path = _write_user_plan(tracked_plan_dir, "doomed")
    service.load_plan_from_file(path, commit_msg="agent: add doomed")

    path.unlink()
    service._on_file_changed(str(path))

    subjects = _git_log_subjects(tracked_plan_dir.parent)
    assert subjects[0].startswith("external delete:")
    assert "doomed.py" in subjects[0]
    assert len(subjects) == 2  # initial load + deletion


def test_load_plan_with_syntax_error_still_commits(tracked_plan_dir, monkeypatch):
    """Failed loads stay in history (forensic evidence)."""
    service = UserPlanService.get_instance()
    monkeypatch.setattr(service, "_plans_dir", tracked_plan_dir)

    bad = tracked_plan_dir / "broken.py"
    bad.write_text("def : syntax error", encoding="utf-8")

    service.load_plan_from_file(bad, commit_msg="agent: try broken")

    assert _git_log_subjects(tracked_plan_dir.parent) == ["agent: try broken"]
