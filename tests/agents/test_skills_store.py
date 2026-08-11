from pathlib import Path

from lightfall.agents import skills_store


def _skill(root: Path, name: str, body: str = "b") -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\n{body}", encoding="utf-8")
    return d


def test_resolution_precedence(tmp_path, monkeypatch):
    builtin, bl, user = tmp_path / "builtin", tmp_path / "bl", tmp_path / "user"
    _skill(builtin, "shared")
    _skill(bl, "shared")
    _skill(user, "shared")
    _skill(builtin, "core-only")
    monkeypatch.setattr(skills_store, "builtin_skills_dir", lambda: builtin)
    monkeypatch.setattr(skills_store, "user_skills_dir", lambda: user)
    resolved = skills_store.resolve_skills(extra_dirs=[bl])
    assert resolved["shared"] == user / "shared"
    assert resolved["core-only"] == builtin / "core-only"


def test_drafts_dir_excluded(tmp_path, monkeypatch):
    user = tmp_path / "user"
    _skill(user / "_drafts", "sneaky")
    monkeypatch.setattr(skills_store, "builtin_skills_dir", lambda: tmp_path / "none")
    monkeypatch.setattr(skills_store, "user_skills_dir", lambda: user)
    assert "sneaky" not in skills_store.resolve_skills()


def test_materialize_copies_skill_and_references(tmp_path, monkeypatch):
    builtin = tmp_path / "builtin"
    d = _skill(builtin, "s1")
    (d / "references").mkdir()
    (d / "references" / "r.md").write_text("ref", encoding="utf-8")
    monkeypatch.setattr(skills_store, "builtin_skills_dir", lambda: builtin)
    monkeypatch.setattr(skills_store, "user_skills_dir", lambda: tmp_path / "nouser")
    out = tmp_path / "session"
    (out / "skills").mkdir(parents=True)
    done = skills_store.materialize_skills(("s1", "missing"), out)
    assert done == ["s1"]
    assert (out / "skills" / "s1" / "SKILL.md").exists()
    assert (out / "skills" / "s1" / "references" / "r.md").read_text(encoding="utf-8") == "ref"


def test_builtin_skills_ship_with_package():
    resolved = skills_store.resolve_skills()
    assert "scan_planning" in resolved  # extracted in Step 1


def test_materialize_resolves_template_variable(tmp_path, monkeypatch):
    builtin = tmp_path / "builtin"
    _skill(builtin, "esaf", body="Beamline is {{beamline}}.")
    monkeypatch.setattr(skills_store, "builtin_skills_dir", lambda: builtin)
    monkeypatch.setattr(skills_store, "user_skills_dir", lambda: tmp_path / "nouser")
    out = tmp_path / "session"
    (out / "skills").mkdir(parents=True)

    done = skills_store.materialize_skills(("esaf",), out, variables={"beamline": "7.0.1"})

    assert done == ["esaf"]
    content = (out / "skills" / "esaf" / "SKILL.md").read_text(encoding="utf-8")
    assert "Beamline is 7.0.1." in content
    assert "{{beamline}}" not in content


def test_materialize_unknown_template_variable_keeps_raw_body(tmp_path, monkeypatch, caplog):
    builtin = tmp_path / "builtin"
    _skill(builtin, "bad", body="Value: {{unknown}}.")
    monkeypatch.setattr(skills_store, "builtin_skills_dir", lambda: builtin)
    monkeypatch.setattr(skills_store, "user_skills_dir", lambda: tmp_path / "nouser")
    out = tmp_path / "session"
    (out / "skills").mkdir(parents=True)

    done = skills_store.materialize_skills(("bad",), out, variables={})

    assert done == ["bad"]
    content = (out / "skills" / "bad" / "SKILL.md").read_text(encoding="utf-8")
    assert "{{unknown}}" in content


def test_template_variables_returns_beamline_user_endstation():
    variables = skills_store.template_variables()
    assert set(variables) == {"beamline", "user", "endstation"}
    assert isinstance(variables["beamline"], str)
    assert isinstance(variables["user"], str)
    assert variables["endstation"] == ""


class _FakeToolPlugin:
    category = "general"
    priority = 100

    def __init__(self, name: str, skill_dir: Path | None):
        self.name = name
        self._skill_dir = skill_dir

    @property
    def description(self) -> str:
        return "d"

    def get_skill_dir(self):
        return self._skill_dir


def _register_fake_plugin(name: str, skill_dir: Path | None):
    from lightfall.ui.panels.claude.tool_registry import ToolRegistry

    ToolRegistry.get_instance().register(_FakeToolPlugin(name, skill_dir))


def test_plugin_skill_dir_resolves_by_plugin_name(tmp_path, monkeypatch):
    from lightfall.ui.panels.claude.tool_registry import ToolRegistry

    ToolRegistry.reset_instance()
    try:
        pkg = tmp_path / "pkg_skill"
        pkg.mkdir()
        (pkg / "SKILL.md").write_text("---\nname: x\ndescription: d\n---\nbody", encoding="utf-8")
        _register_fake_plugin("saxs_analysis", pkg)
        monkeypatch.setattr(skills_store, "builtin_skills_dir", lambda: tmp_path / "nob")
        monkeypatch.setattr(skills_store, "user_skills_dir", lambda: tmp_path / "nou")
        resolved = skills_store.resolve_skills()
        assert resolved["saxs_analysis"] == pkg
    finally:
        ToolRegistry.reset_instance()


def test_plugin_skill_precedence_between_builtin_and_user(tmp_path, monkeypatch):
    from lightfall.ui.panels.claude.tool_registry import ToolRegistry

    ToolRegistry.reset_instance()
    try:
        builtin, user = tmp_path / "builtin", tmp_path / "user"
        _skill(builtin, "shared")
        pkg = tmp_path / "plugin_shared"
        pkg.mkdir()
        (pkg / "SKILL.md").write_text("---\nname: shared\ndescription: d\n---\np", encoding="utf-8")
        _register_fake_plugin("shared", pkg)

        monkeypatch.setattr(skills_store, "builtin_skills_dir", lambda: builtin)
        monkeypatch.setattr(skills_store, "user_skills_dir", lambda: user)
        assert skills_store.resolve_skills()["shared"] == pkg  # plugin beats builtin

        _skill(user, "shared")
        assert skills_store.resolve_skills()["shared"] == user / "shared"  # user beats plugin
    finally:
        ToolRegistry.reset_instance()


def test_plugin_skill_dir_without_skill_md_ignored(tmp_path, monkeypatch):
    from lightfall.ui.panels.claude.tool_registry import ToolRegistry

    ToolRegistry.reset_instance()
    try:
        empty = tmp_path / "empty"
        empty.mkdir()
        _register_fake_plugin("broken", empty)
        _register_fake_plugin("none_dir", None)
        monkeypatch.setattr(skills_store, "builtin_skills_dir", lambda: tmp_path / "nob")
        monkeypatch.setattr(skills_store, "user_skills_dir", lambda: tmp_path / "nou")
        resolved = skills_store.resolve_skills()
        assert "broken" not in resolved
        assert "none_dir" not in resolved
    finally:
        ToolRegistry.reset_instance()


def test_tool_plugin_get_skill_dir_default_is_none():
    from lightfall.plugins.tool_plugin import ToolPlugin

    class P(ToolPlugin):
        @property
        def name(self) -> str:
            return "p"

        @property
        def description(self) -> str:
            return "d"

    assert P().get_skill_dir() is None
