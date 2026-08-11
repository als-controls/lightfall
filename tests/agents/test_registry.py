from pathlib import Path

import pytest

from lightfall.agents.registry import AgentSpecRegistry


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
