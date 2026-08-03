from pathlib import Path

import pytest

from lightfall.agents import drafts as drafts_mod
from lightfall.agents import skills_store
from lightfall.agents.registry import AgentSpecRegistry
from lightfall.agents.spec import parse_agent_file
from lightfall.ui.panels.agent_editor import models


def _agent(dir: Path, name: str, body: str = "prompt body") -> Path:
    dir.mkdir(parents=True, exist_ok=True)
    p = dir / f"{name}.md"
    p.write_text(f"---\nname: {name}\ndescription: d-{name}\n---\n{body}", encoding="utf-8")
    return p


def _skill(root: Path, name: str, body: str = "b") -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\n{body}", encoding="utf-8")
    return d


@pytest.fixture()
def registry():
    AgentSpecRegistry.reset_instance()
    yield AgentSpecRegistry.get_instance()
    AgentSpecRegistry.reset_instance()


class TestAgentRows:
    def test_user_scope_is_editable_core_is_not(self, registry, tmp_path):
        _agent(tmp_path / "core", "observer")
        _agent(tmp_path / "user", "custom")
        registry.register_scope_dir("core", tmp_path / "core")
        registry.register_scope_dir("user", tmp_path / "user")

        rows = {r.name: r for r in models.agent_rows(registry)}

        assert rows["observer"].scope == "core"
        assert rows["observer"].editable is False
        assert rows["custom"].scope == "user"
        assert rows["custom"].editable is True

    def test_shadow_detection(self, registry, tmp_path):
        _agent(tmp_path / "core", "lightfall", body="core prompt")
        _agent(tmp_path / "user", "lightfall", body="user prompt")
        registry.register_scope_dir("core", tmp_path / "core")
        registry.register_scope_dir("user", tmp_path / "user")

        rows = {r.name: r for r in models.agent_rows(registry)}

        assert rows["lightfall"].scope == "user"
        assert rows["lightfall"].shadowed_scopes == ("core",)

    def test_no_shadow_when_unique_name(self, registry, tmp_path):
        _agent(tmp_path / "core", "observer")
        registry.register_scope_dir("core", tmp_path / "core")

        rows = {r.name: r for r in models.agent_rows(registry)}

        assert rows["observer"].shadowed_scopes == ()

    def test_error_rows_included_and_greyed(self, registry, tmp_path):
        _agent(tmp_path / "core", "good")
        bad = tmp_path / "core" / "bad.md"
        bad.write_text("no frontmatter", encoding="utf-8")
        registry.register_scope_dir("core", tmp_path / "core")

        rows = models.agent_rows(registry)
        error_rows = [r for r in rows if r.error is not None]

        assert len(error_rows) == 1
        assert error_rows[0].name == "bad"
        assert error_rows[0].scope == "core"
        assert error_rows[0].editable is False
        assert error_rows[0].source_path == bad
        assert "frontmatter" in error_rows[0].error


class TestSkillRows:
    def test_scope_attribution(self, tmp_path, monkeypatch):
        builtin, bl, user = tmp_path / "builtin", tmp_path / "bl", tmp_path / "user"
        _skill(builtin, "core-skill")
        _skill(bl, "bl-skill")
        _skill(user, "user-skill")
        monkeypatch.setattr(skills_store, "builtin_skills_dir", lambda: builtin)
        monkeypatch.setattr(skills_store, "user_skills_dir", lambda: user)
        monkeypatch.setattr(
            skills_store, "resolve_skills", lambda extra_dirs=None: _real_resolve(builtin, bl, user)
        )

        rows = {r.name: r for r in models.skill_rows()}

        assert rows["core-skill"].scope == "core"
        assert rows["core-skill"].editable is False
        assert rows["bl-skill"].scope == "beamline"
        assert rows["bl-skill"].editable is False
        assert rows["user-skill"].scope == "user"
        assert rows["user-skill"].editable is True


def _real_resolve(builtin: Path, bl: Path, user: Path) -> dict[str, Path]:
    resolved: dict[str, Path] = {}
    for root in (builtin, bl, user):
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if (child / "SKILL.md").is_file():
                resolved[child.name] = child
    return resolved


class TestDraftRows:
    def test_diff_target_set_for_revision(self, tmp_path, monkeypatch):
        user = tmp_path / "user"
        active = _skill(user, "spec-a", body="active body")
        monkeypatch.setattr(skills_store, "builtin_skills_dir", lambda: tmp_path / "none")
        monkeypatch.setattr(skills_store, "user_skills_dir", lambda: user)
        monkeypatch.setattr(drafts_mod, "user_skills_dir", lambda: user)

        drafts_mod.save_draft("spec-a", "d", "revised body", author="ron")

        rows = {r.name: r for r in models.draft_rows()}

        assert rows["spec-a"].is_revision is True
        assert rows["spec-a"].diff_target == active / "SKILL.md"

    def test_diff_target_none_for_new_draft(self, tmp_path, monkeypatch):
        user = tmp_path / "user"
        user.mkdir(parents=True)
        monkeypatch.setattr(skills_store, "builtin_skills_dir", lambda: tmp_path / "none")
        monkeypatch.setattr(skills_store, "user_skills_dir", lambda: user)
        monkeypatch.setattr(drafts_mod, "user_skills_dir", lambda: user)

        drafts_mod.save_draft("brand-new", "d", "body", author="ron")

        rows = {r.name: r for r in models.draft_rows()}

        assert rows["brand-new"].is_revision is False
        assert rows["brand-new"].diff_target is None


class TestCopyAndReset:
    def test_copy_to_user_creates_editable_shadow(self, registry, tmp_path, monkeypatch):
        core_dir = tmp_path / "core"
        user_dir = tmp_path / "user"
        _agent(core_dir, "observer", body="core body")
        registry.register_scope_dir("core", core_dir)
        monkeypatch.setattr(
            "lightfall.agents.registry.user_agents_dir", lambda: user_dir
        )
        user_dir.mkdir(parents=True, exist_ok=True)
        registry.register_scope_dir("user", user_dir)

        dest = models.copy_to_user("observer", registry)

        assert dest == user_dir / "observer.md"
        assert dest.is_file()
        assert "core body" in dest.read_text(encoding="utf-8")
        # Registry reloaded and now resolves the user-scope shadow.
        assert registry.get("observer").scope == "user"

    def test_copy_to_user_unknown_name_raises(self, registry):
        with pytest.raises(models.EditorError):
            models.copy_to_user("nope", registry)

    def test_copy_skill_to_user(self, tmp_path, monkeypatch):
        builtin = tmp_path / "builtin"
        user = tmp_path / "user"
        _skill(builtin, "s1", body="skill body")
        monkeypatch.setattr(skills_store, "builtin_skills_dir", lambda: builtin)
        monkeypatch.setattr(skills_store, "user_skills_dir", lambda: user)
        user.mkdir(parents=True, exist_ok=True)

        dest = models.copy_skill_to_user("s1")

        assert dest == user / "s1"
        assert (dest / "SKILL.md").is_file()

    def test_copy_skill_to_user_unknown_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(skills_store, "builtin_skills_dir", lambda: tmp_path / "none")
        monkeypatch.setattr(skills_store, "user_skills_dir", lambda: tmp_path / "user")

        with pytest.raises(models.EditorError):
            models.copy_skill_to_user("nope")

    def test_reset_to_default_removes_shadow(self, tmp_path, monkeypatch):
        user_dir = tmp_path / "user"
        user_dir.mkdir(parents=True)
        shadow = _agent(user_dir, "observer", body="user body")
        monkeypatch.setattr(
            "lightfall.agents.registry.user_agents_dir", lambda: user_dir
        )

        models.reset_to_default("observer")

        assert not shadow.exists()

    def test_reset_to_default_rejects_non_shadow(self, tmp_path, monkeypatch):
        user_dir = tmp_path / "user"
        user_dir.mkdir(parents=True)
        monkeypatch.setattr(
            "lightfall.agents.registry.user_agents_dir", lambda: user_dir
        )

        with pytest.raises(models.EditorError):
            models.reset_to_default("no-such-agent")


class TestSerializeAgentFile:
    def test_round_trips_through_parser(self, tmp_path):
        content = models.serialize_agent_file(
            name="custom",
            description="does a thing",
            prompt="Be helpful with {{beamline}}.",
            model="opus",
            effort="high",
            tools=["device_tools"],
            skills=["s1", "s2"],
            memory=False,
            subagent=False,
            openable=True,
            on_message="auto",
        )
        path = tmp_path / "custom.md"
        path.write_text(content, encoding="utf-8")

        spec = parse_agent_file(path, "user")

        assert spec.name == "custom"
        assert spec.description == "does a thing"
        assert spec.prompt == "Be helpful with {{beamline}}."
        assert spec.model == "opus"
        assert spec.effort == "high"
        assert spec.tools == ("device_tools",)
        assert spec.skills == ("s1", "s2")
        assert spec.memory is False
        assert spec.subagent is False
        assert spec.openable is True
        assert spec.on_message == "auto"

    def test_blank_optionals_are_omitted(self):
        content = models.serialize_agent_file(
            name="a", description="d", prompt="p", model="", effort=None
        )
        assert "model:" not in content
        assert "effort:" not in content
        assert "tools:" not in content

    def test_special_characters_are_yaml_quoted(self, tmp_path):
        content = models.serialize_agent_file(
            name="a", description="colon: and #hash", prompt="p"
        )
        path = tmp_path / "a.md"
        path.write_text(content, encoding="utf-8")

        assert parse_agent_file(path, "user").description == "colon: and #hash"


class TestSaveAgentFile:
    def test_writes_valid_content(self, tmp_path):
        path = tmp_path / "a.md"
        content = models.serialize_agent_file(name="a", description="d", prompt="p")

        models.save_agent_file(path, content)

        assert parse_agent_file(path, "user").prompt == "p"
        assert not list(tmp_path.glob(".*editor-tmp"))

    def test_invalid_content_raises_and_leaves_original(self, tmp_path):
        path = tmp_path / "a.md"
        good = models.serialize_agent_file(name="a", description="d", prompt="good")
        models.save_agent_file(path, good)

        bad = models.serialize_agent_file(
            name="a", description="d", prompt="uses {{bogus}}"
        )
        with pytest.raises(models.EditorError):
            models.save_agent_file(path, bad)

        assert parse_agent_file(path, "user").prompt == "good"
        assert not list(tmp_path.glob(".*editor-tmp"))


class TestNewAgentFile:
    def test_creates_parseable_template(self, tmp_path, monkeypatch):
        user_dir = tmp_path / "user"
        user_dir.mkdir(parents=True)
        monkeypatch.setattr(
            "lightfall.agents.registry.user_agents_dir", lambda: user_dir
        )

        dest = models.new_agent_file("my-agent")

        assert dest == user_dir / "my-agent.md"
        assert parse_agent_file(dest, "user").name == "my-agent"

    def test_rejects_bad_name(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "lightfall.agents.registry.user_agents_dir", lambda: tmp_path
        )
        with pytest.raises(models.EditorError):
            models.new_agent_file("bad name/../x")

    def test_rejects_existing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "lightfall.agents.registry.user_agents_dir", lambda: tmp_path
        )
        models.new_agent_file("dup")
        with pytest.raises(models.EditorError):
            models.new_agent_file("dup")
