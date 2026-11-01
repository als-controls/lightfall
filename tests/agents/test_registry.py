from pathlib import Path

import pytest

from lightfall.agents.registry import AgentSpecRegistry
from lightfall.content.clashes import Clash, ClashLog
from lightfall.content.sources import SourceItem


def _agent(dir: Path, name: str, body: str = "prompt body") -> Path:
    dir.mkdir(parents=True, exist_ok=True)
    p = dir / f"{name}.md"
    p.write_text(f"---\nname: {name}\ndescription: d-{name}\n---\n{body}", encoding="utf-8")
    return p


@pytest.fixture()
def registry():
    AgentSpecRegistry.reset_instance()
    yield AgentSpecRegistry.get_instance()
    AgentSpecRegistry.reset_instance()


def test_scope_shadowing_user_over_beamline_over_core(registry, tmp_path):
    _agent(tmp_path / "core", "lightfall", body="core prompt")
    _agent(tmp_path / "bl", "lightfall", body="beamline prompt")
    _agent(tmp_path / "user", "lightfall", body="user prompt")
    _agent(tmp_path / "core", "observer")
    registry.register_scope_dir("core", tmp_path / "core")
    registry.register_scope_dir("beamline", tmp_path / "bl")
    registry.register_scope_dir("user", tmp_path / "user")
    specs = {s.name: s for s in registry.specs()}
    assert specs["lightfall"].prompt == "user prompt"
    assert specs["lightfall"].scope == "user"
    assert specs["observer"].scope == "core"


def test_bad_file_is_skipped_and_reported(registry, tmp_path):
    _agent(tmp_path / "core", "good")
    bad = tmp_path / "core" / "bad.md"
    bad.write_text("no frontmatter", encoding="utf-8")
    registry.register_scope_dir("core", tmp_path / "core")
    assert [s.name for s in registry.specs()] == ["good"]
    assert len(registry.errors()) == 1
    assert registry.errors()[0][0] == bad


def test_bad_template_var_is_skipped_and_reported(registry, tmp_path):
    _agent(tmp_path / "user", "good")
    bad = tmp_path / "user" / "typo.md"
    bad.write_text("---\nname: typo\ndescription: d\n---\nHello {{typo}}", encoding="utf-8")
    registry.register_scope_dir("user", tmp_path / "user")
    assert [s.name for s in registry.specs()] == ["good"]
    assert len(registry.errors()) == 1
    assert registry.errors()[0][0] == bad


def test_enabled_specs_pref_pair(registry, tmp_path, monkeypatch):
    _agent(tmp_path / "core", "a")
    _agent(tmp_path / "core", "b")
    registry.register_scope_dir("core", tmp_path / "core")
    prefs = {"disabled_agents": ["b"]}
    monkeypatch.setattr(registry, "_read_list_pref", lambda key: prefs.get(key))
    assert [s.name for s in registry.enabled_specs()] == ["a"]


def test_changed_signal_emitted_on_reload(registry, tmp_path, qtbot):
    _agent(tmp_path / "core", "a")
    registry.register_scope_dir("core", tmp_path / "core")
    with qtbot.waitSignal(registry.changed, timeout=1000):
        registry.reload()


def _item(path: Path, source: str) -> SourceItem:
    return SourceItem("agent", path.stem, source, path)


@pytest.fixture()
def clashes():
    ClashLog.reset_instance()
    yield ClashLog.get_instance()
    ClashLog.reset_instance()


def test_branch_shadows_beamline_shadows_facility_and_user_wins(registry, tmp_path):
    for sub in ("fac", "bl", "br"):
        _agent(tmp_path / sub, "lightfall", body=f"{sub} prompt")
    items = [
        _item(tmp_path / "fac" / "lightfall.md", "facility"),
        _item(tmp_path / "bl" / "lightfall.md", "beamline:7.0.1"),
        _item(tmp_path / "br" / "lightfall.md", "branch:7.0.1.1"),
    ]
    registry.set_synced(items)
    spec = registry.get("lightfall")
    assert spec.prompt == "br prompt"
    assert spec.scope == "branch:7.0.1.1"

    registry.set_synced(items[:2])
    assert registry.get("lightfall").scope == "beamline:7.0.1"

    _agent(tmp_path / "user", "lightfall", body="user prompt")
    registry.register_scope_dir("user", tmp_path / "user")
    registry.set_synced(items)
    assert registry.get("lightfall").scope == "user"


def test_package_shadowed_by_facility(registry, tmp_path):
    _agent(tmp_path / "core", "lightfall", body="core prompt")
    _agent(tmp_path / "pkg", "lightfall", body="package prompt")
    _agent(tmp_path / "fac", "lightfall", body="facility prompt")
    registry.register_scope_dir("core", tmp_path / "core")
    registry.register_scope_dir("package", tmp_path / "pkg")
    assert registry.get("lightfall").scope == "package"
    registry.set_synced([_item(tmp_path / "fac" / "lightfall.md", "facility")])
    assert registry.get("lightfall").prompt == "facility prompt"


def test_beamline_alias_registers_as_package(registry, tmp_path):
    _agent(tmp_path / "bl", "endstation")
    registry.register_scope_dir("beamline", tmp_path / "bl")
    assert registry.get("endstation").scope == "package"


def test_set_synced_replaces_previous_set(registry, tmp_path):
    a = _agent(tmp_path / "s", "alpha")
    b = _agent(tmp_path / "s", "beta")
    registry.set_synced([_item(a, "facility")])
    assert {s.name for s in registry.specs()} == {"alpha"}
    registry.set_synced([_item(b, "branch:7.0.1.1")])
    assert {s.name for s in registry.specs()} == {"beta"}
    registry.set_synced([])
    assert registry.specs() == []


def test_shadows_reported_to_clash_log(registry, clashes, tmp_path):
    _agent(tmp_path / "core", "lightfall")
    _agent(tmp_path / "fac", "lightfall")
    _agent(tmp_path / "br", "lightfall")
    registry.register_scope_dir("core", tmp_path / "core")
    registry.set_synced([
        _item(tmp_path / "fac" / "lightfall.md", "facility"),
        _item(tmp_path / "br" / "lightfall.md", "branch:7.0.1.1"),
    ])
    registry.reload()  # deduped across reloads
    assert clashes.all() == [
        Clash("agent", "lightfall", "branch:7.0.1.1", "core", False),
        Clash("agent", "lightfall", "branch:7.0.1.1", "facility", False),
    ]
