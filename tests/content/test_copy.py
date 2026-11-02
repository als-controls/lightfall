"""Copy to mine: a synced item's entry point into ~/lightfall, never overwriting unasked."""
from __future__ import annotations

from pathlib import Path

import pytest

from lightfall.acquire.plans.user_plans import UserPlanService
from lightfall.agents.registry import AgentSpecRegistry
from lightfall.content.actions import ContentRow
from lightfall.content.copy import copy_to_mine
from lightfall.plugins.user_plugins import UserPluginService

BRANCH = "branch:7.0.1.1"


@pytest.fixture(autouse=True)
def no_services():
    for cls in (UserPlanService, UserPluginService, AgentSpecRegistry):
        cls.reset_instance()
    yield
    for cls in (UserPlanService, UserPluginService, AgentSpecRegistry):
        cls.reset_instance()


def _row(type_: str, name: str, path: Path | None, source: str = BRANCH) -> ContentRow:
    return ContentRow(type=type_, name=name, source=source, revision=3, path=path,
                      shadows=(), error=None)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_copy_each_type(tmp_path):
    synced, root = tmp_path / "synced", tmp_path / "mine"
    plan = _write(synced / "xafs@3" / "xafs.py", "def plan():\n    yield from []\n")
    single = _write(synced / "tool@1" / "tool.py", "T = 1\n")
    pkg = synced / "tools@7"
    _write(pkg / "__init__.py", "X = 1\n")
    _write(pkg / "sub" / "helper.py", "Y = 2\n")
    _write(pkg / "__pycache__" / "x.cpython-314.pyc", "junk")
    _write(pkg / "sub" / "__pycache__" / "h.pyc", "junk")
    agent = _write(synced / "helper@2" / "helper.md", "---\nname: helper\n---\nhi\n")
    skill = synced / "fitting@9"
    _write(skill / "SKILL.md", "---\nname: fitting\n---\nfit\n")
    _write(skill / "refs" / "notes.md", "n\n")

    assert copy_to_mine(_row("plan", "xafs", plan), root=root) == root / "plans" / "xafs.py"
    assert (root / "plans" / "xafs.py").read_text(encoding="utf-8") == plan.read_text(
        encoding="utf-8")

    assert copy_to_mine(_row("plugin", "tool", single), root=root) == (
        root / "plugins" / "tool.py")
    assert (root / "plugins" / "tool.py").read_text(encoding="utf-8") == "T = 1\n"

    dest = copy_to_mine(_row("plugin", "tools", pkg), root=root)
    assert dest == root / "plugins" / "tools"
    assert sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()) == [
        "__init__.py", "sub/helper.py"]

    assert copy_to_mine(_row("agent", "helper", agent), root=root) == (
        root / "agents" / "helper.md")
    assert (root / "agents" / "helper.md").read_text(encoding="utf-8").startswith("---")

    dest = copy_to_mine(_row("skill", "fitting", skill), root=root)
    assert dest == root / "skills" / "fitting"
    assert (dest / "SKILL.md").is_file() and (dest / "refs" / "notes.md").is_file()


def test_copy_refuses_to_overwrite_without_consent(tmp_path):
    synced, root = tmp_path / "synced", tmp_path / "mine"
    plan = _write(synced / "xafs@3" / "xafs.py", "NEW = 1\n")
    mine = _write(root / "plans" / "xafs.py", "MINE = 1\n")
    with pytest.raises(FileExistsError):
        copy_to_mine(_row("plan", "xafs", plan), root=root)
    assert mine.read_text(encoding="utf-8") == "MINE = 1\n"  # Cancel: untouched

    skill = synced / "fitting@9"
    _write(skill / "SKILL.md", "new\n")
    own = _write(root / "skills" / "fitting" / "SKILL.md", "mine\n")
    _write(root / "skills" / "fitting" / "extra.md", "keep?\n")
    with pytest.raises(FileExistsError):
        copy_to_mine(_row("skill", "fitting", skill), root=root)
    assert own.read_text(encoding="utf-8") == "mine\n"

    # A plugin of the same name in the other form (file vs folder) is the same item.
    pkg = synced / "tools@7"
    _write(pkg / "__init__.py", "X = 1\n")
    single = _write(root / "plugins" / "tools.py", "MINE = 1\n")
    with pytest.raises(FileExistsError):
        copy_to_mine(_row("plugin", "tools", pkg), root=root)
    assert single.read_text(encoding="utf-8") == "MINE = 1\n"
    assert not (root / "plugins" / "tools").exists()

    # With consent the copy replaces the local item whole.
    copy_to_mine(_row("plan", "xafs", plan), root=root, overwrite=True)
    assert mine.read_text(encoding="utf-8") == "NEW = 1\n"
    copy_to_mine(_row("skill", "fitting", skill), root=root, overwrite=True)
    assert own.read_text(encoding="utf-8") == "new\n"
    assert not (root / "skills" / "fitting" / "extra.md").exists()
    copy_to_mine(_row("plugin", "tools", pkg), root=root, overwrite=True)
    assert not single.exists() and (root / "plugins" / "tools" / "__init__.py").is_file()


def test_copy_rejects_rows_without_a_file_or_already_mine(tmp_path):
    with pytest.raises(ValueError):
        copy_to_mine(_row("plan", "count", None, source="core"), root=tmp_path)
    mine = _write(tmp_path / "plans" / "x.py", "X = 1\n")
    with pytest.raises(ValueError):
        copy_to_mine(_row("plan", "x", mine, source="local"), root=tmp_path)
    with pytest.raises(ValueError):
        copy_to_mine(_row("plan", "../evil", mine), root=tmp_path)


def test_copy_loads_plans_and_plugins_through_running_services(tmp_path, monkeypatch):
    calls = []

    class Fake:  # what reset_instance() calls on the way out
        def deleteLater(self):
            pass

        def _cleanup_temp_dir(self):
            pass

    class FakePlans(Fake):
        def load_plan_from_file(self, path, commit_msg=None):
            calls.append(("plan", path, commit_msg))

    class FakePlugins(Fake):
        def _displace_synced(self, name):
            calls.append(("displace", name))

        def load_plugin_from_file(self, path, commit_msg=None):
            calls.append(("plugin", path, commit_msg))

    monkeypatch.setattr(UserPlanService, "_instance", FakePlans())
    monkeypatch.setattr(UserPluginService, "_instance", FakePlugins())
    synced, root = tmp_path / "synced", tmp_path / "mine"
    plan = _write(synced / "xafs@3" / "xafs.py", "P = 1\n")
    tool = _write(synced / "tool@1" / "tool.py", "T = 1\n")

    copy_to_mine(_row("plan", "xafs", plan), root=root)
    copy_to_mine(_row("plugin", "tool", tool), root=root)
    assert calls == [
        ("plan", root / "plans" / "xafs.py", f"copied from {BRANCH}"),
        ("displace", "tool"),
        ("plugin", root / "plugins" / "tool.py", f"copied from {BRANCH}"),
    ]
