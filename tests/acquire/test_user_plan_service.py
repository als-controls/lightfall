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


# --- synced scopes ---------------------------------------------------------------

from lightfall.acquire.plans.registry import PlanRegistry  # noqa: E402
from lightfall.content.clashes import Clash, ClashLog  # noqa: E402
from lightfall.content.sources import SourceItem  # noqa: E402


@pytest.fixture
def clean_registries():
    ClashLog.reset_instance()
    PlanRegistry.reset_instance()
    yield
    ClashLog.reset_instance()
    PlanRegistry.reset_instance()


def _synced_plan(root: Path, name: str, source: str, rev: int = 3, body: str | None = None):
    folder = root / f"{name}@{rev}"
    folder.mkdir(parents=True)
    path = folder / f"{name}.py"
    path.write_text(
        body
        if body is not None
        else "def plan(detectors):\n    yield from []\n",
        encoding="utf-8",
    )
    return SourceItem("plan", name, source, path, rev)


def _service(tracked_plan_dir, monkeypatch):
    service = UserPlanService.get_instance()
    monkeypatch.setattr(service, "_plans_dir", tracked_plan_dir)
    return service


def test_branch_plan_loads_with_source(
    tracked_plan_dir, monkeypatch, tmp_path, clean_registries
):
    service = _service(tracked_plan_dir, monkeypatch)
    item = _synced_plan(tmp_path / "synced", "xafs", "branch:7.0.1.1")

    service.load_all_plans(synced=[item])

    info = PlanRegistry.get_instance().get_plan("xafs")
    assert info is not None
    assert info.source == "branch:7.0.1.1"
    assert service._loaded_plans["xafs"] == (item.path, "branch:7.0.1.1")
    assert ClashLog.get_instance().all() == []


def test_local_beats_branch_and_reports(
    tracked_plan_dir, monkeypatch, tmp_path, clean_registries
):
    service = _service(tracked_plan_dir, monkeypatch)
    _write_user_plan(tracked_plan_dir, "xafs")
    item = _synced_plan(tmp_path / "synced", "xafs", "branch:7.0.1.1")

    service.load_all_plans(synced=[item])

    assert PlanRegistry.get_instance().get_plan("xafs").source == "local"
    assert ClashLog.get_instance().all() == [
        Clash("plan", "xafs", "local", "branch:7.0.1.1", False)
    ]


def test_synced_plan_refused_against_core_name(
    tracked_plan_dir, monkeypatch, tmp_path, clean_registries
):
    service = _service(tracked_plan_dir, monkeypatch)
    registry = PlanRegistry.get_instance()
    registry.register_ranked("count", lambda: (yield), "core", "core")
    item = _synced_plan(tmp_path / "synced", "count", "facility")

    service.load_all_plans(synced=[item])

    assert registry.get_plan("count").source == "core"
    assert "count" not in service._loaded_plans
    assert ClashLog.get_instance().all() == [
        Clash("plan", "count", "core", "facility", False)
    ]


def test_local_plan_named_like_core_is_refused(
    tracked_plan_dir, monkeypatch, clean_registries
):
    service = _service(tracked_plan_dir, monkeypatch)
    registry = PlanRegistry.get_instance()
    registry.register_ranked("count", lambda: (yield), "core", "core")
    _write_user_plan(tracked_plan_dir, "count")

    service.load_all_plans(synced=[])

    assert registry.get_plan("count").source == "core"
    assert ClashLog.get_instance().all() == [Clash("plan", "count", "core", "local", False)]


def test_synced_plan_with_error_reports_and_continues(
    tracked_plan_dir, monkeypatch, tmp_path, clean_registries
):
    service = _service(tracked_plan_dir, monkeypatch)
    root = tmp_path / "synced"
    bad = _synced_plan(root, "broken", "facility", body="raise RuntimeError('boom')\n")
    good = _synced_plan(root, "fine", "facility")
    errors: list[tuple[str, str]] = []
    service.plan_error.connect(lambda p, m: errors.append((p, m)))

    results = service.load_all_plans(synced=[bad, good])

    registry = PlanRegistry.get_instance()
    assert registry.get_plan("fine") is not None
    assert registry.get_plan("broken") is None
    assert errors == [(str(bad.path), "boom")]
    assert any(p == bad.path and isinstance(r, RuntimeError) for p, r in results)


def test_deleting_local_plan_falls_back_to_synced(
    tracked_plan_dir, monkeypatch, tmp_path, clean_registries
):
    service = _service(tracked_plan_dir, monkeypatch)
    local = _write_user_plan(tracked_plan_dir, "xafs")
    item = _synced_plan(tmp_path / "synced", "xafs", "branch:7.0.1.1")
    service.load_all_plans(synced=[item])
    assert PlanRegistry.get_instance().get_plan("xafs").source == "local"

    local.unlink()
    service._on_directory_changed(str(tracked_plan_dir))

    assert PlanRegistry.get_instance().get_plan("xafs").source == "branch:7.0.1.1"
    assert service._loaded_plans["xafs"] == (item.path, "branch:7.0.1.1")

    # and a new local file takes the name back
    _write_user_plan(tracked_plan_dir, "xafs")
    service._on_directory_changed(str(tracked_plan_dir))
    assert PlanRegistry.get_instance().get_plan("xafs").source == "local"


def test_deleting_local_plan_via_file_change_falls_back(
    tracked_plan_dir, monkeypatch, tmp_path, clean_registries
):
    service = _service(tracked_plan_dir, monkeypatch)
    local = _write_user_plan(tracked_plan_dir, "xafs")
    item = _synced_plan(tmp_path / "synced", "xafs", "facility")
    service.load_all_plans(synced=[item])

    local.unlink()
    service._on_file_changed(str(local))

    assert PlanRegistry.get_instance().get_plan("xafs").source == "facility"


def test_synced_plans_are_not_committed(
    tracked_plan_dir, monkeypatch, tmp_path, clean_registries
):
    service = _service(tracked_plan_dir, monkeypatch)
    item = _synced_plan(tmp_path / "synced", "xafs", "facility")

    service.load_all_plans(synced=[item])
    service._on_directory_changed(str(tracked_plan_dir))

    assert _git_log_subjects(tracked_plan_dir.parent) == []
    assert str(item.path) not in service._watcher.files()
