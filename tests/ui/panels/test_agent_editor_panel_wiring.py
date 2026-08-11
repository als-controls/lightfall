"""Construction-level wiring tests for the Agents & Skills editor panel.

Deliberately shallow: metadata, plugin/manifest registration, and that the
panel builds under qtbot against monkeypatched agent/skill/draft stores.
Business logic lives in ``agent_editor.models`` and ``agents.drafts`` and is
tested there.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from lightfall.agents import drafts as drafts_mod
from lightfall.agents import skills_store
from lightfall.agents.registry import AgentSpecRegistry


def _agent(dir: Path, name: str, body: str = "prompt body") -> Path:
    dir.mkdir(parents=True, exist_ok=True)
    p = dir / f"{name}.md"
    p.write_text(f"---\nname: {name}\ndescription: d-{name}\n---\n{body}", encoding="utf-8")
    return p


def _skill(root: Path, name: str, body: str = "b") -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: d\n---\n{body}", encoding="utf-8"
    )
    return d


@pytest.fixture()
def stores(tmp_path, monkeypatch):
    """Point agents/skills/drafts at tmp dirs and return a fresh registry."""
    core_dir = tmp_path / "agents_core"
    user_dir = tmp_path / "agents_user"
    builtin_skills = tmp_path / "skills_builtin"
    user_skills = tmp_path / "skills_user"
    for d in (core_dir, user_dir, builtin_skills, user_skills):
        d.mkdir(parents=True, exist_ok=True)

    _agent(core_dir, "observer", body="core prompt")
    _agent(user_dir, "custom", body="user prompt")
    _skill(builtin_skills, "core-skill")
    _skill(user_skills, "user-skill")

    monkeypatch.setattr(skills_store, "builtin_skills_dir", lambda: builtin_skills)
    monkeypatch.setattr(skills_store, "user_skills_dir", lambda: user_skills)
    monkeypatch.setattr(drafts_mod, "user_skills_dir", lambda: user_skills)
    monkeypatch.setattr("lightfall.agents.registry.user_agents_dir", lambda: user_dir)

    AgentSpecRegistry.reset_instance()
    registry = AgentSpecRegistry.get_instance()
    registry.register_scope_dir("core", core_dir)
    registry.register_scope_dir("user", user_dir)
    yield registry
    AgentSpecRegistry.reset_instance()


class TestMetadata:
    def test_metadata_fields(self):
        from lightfall.ui.panels.agent_editor.panel import AgentEditorPanel

        md = AgentEditorPanel.panel_metadata
        assert md.id == "lightfall.panels.agent_editor"
        assert md.name == "Agents & Skills"
        assert md.category == "development"
        assert md.singleton is True

    def test_metadata_sidebar_visible(self):
        """Gotchas doc (commit 507177d): center + empty icon = invisible panel."""
        from lightfall.ui.panels.agent_editor.panel import AgentEditorPanel

        md = AgentEditorPanel.panel_metadata
        assert md.default_area in {"left", "bottom"}
        assert md.icon


class TestPlugin:
    def test_plugin_returns_panel_class(self):
        from lightfall.ui.panels.agent_editor.panel import AgentEditorPanel
        from lightfall.ui.panels.agent_editor.plugin import AgentEditorPanelPlugin

        plugin = AgentEditorPanelPlugin()
        assert plugin.name == "agent_editor"
        assert plugin.get_panel_class() is AgentEditorPanel

    def test_manifest_entry_present_and_resolves(self):
        from lightfall.plugins.builtin_manifest import builtin_manifest
        from lightfall.plugins.panel_plugin import PanelPlugin

        matching = [
            e
            for e in builtin_manifest.plugins
            if e.type_name == "panel" and e.name == "agent_editor"
        ]
        assert len(matching) == 1
        entry = matching[0]
        assert entry.preload is True
        assert entry.import_path == (
            "lightfall.ui.panels.agent_editor.plugin:AgentEditorPanelPlugin"
        )
        module_path, class_name = entry.import_path.split(":")
        cls = getattr(importlib.import_module(module_path), class_name)
        assert issubclass(cls, PanelPlugin)


class TestConstruction:
    def test_builds_with_tabs(self, qtbot, stores):
        from lightfall.ui.panels.agent_editor.panel import AgentEditorPanel

        panel = AgentEditorPanel(registry=stores)
        qtbot.addWidget(panel)

        assert panel.tab_labels() == ["Agents", "Skills"]
        assert "observer" in panel.agent_names()
        assert "custom" in panel.agent_names()
        assert "user-skill" in panel.skill_names()

    def test_drafts_badge_reflects_seeded_draft(self, qtbot, stores):
        from lightfall.ui.panels.agent_editor.panel import AgentEditorPanel

        drafts_mod.save_draft("brand-new", "a draft", "body", author="ron")

        panel = AgentEditorPanel(registry=stores)
        qtbot.addWidget(panel)

        assert panel.tab_labels() == ["Agents", "Skills (1)"]
        assert panel.draft_names() == ["brand-new"]

    def test_refresh_picks_up_new_draft(self, qtbot, stores):
        from lightfall.ui.panels.agent_editor.panel import AgentEditorPanel

        panel = AgentEditorPanel(registry=stores)
        qtbot.addWidget(panel)
        assert panel.tab_labels()[1] == "Skills"

        drafts_mod.save_draft("later", "a draft", "body", author="ron")
        panel.refresh_skills()

        assert panel.tab_labels()[1] == "Skills (1)"

    def test_selecting_rows_does_not_raise(self, qtbot, stores):
        from lightfall.ui.panels.agent_editor.panel import AgentEditorPanel

        drafts_mod.save_draft("brand-new", "a draft", "body", author="ron")
        panel = AgentEditorPanel(registry=stores)
        qtbot.addWidget(panel)

        for i in range(panel._agent_list.count()):
            panel._agent_list.setCurrentRow(i)
        for i in range(panel._skill_list.count()):
            panel._skill_list.setCurrentRow(i)


class TestSerialization:
    def test_save_round_trips_edits(self, qtbot, stores):
        from lightfall.agents.spec import parse_agent_file
        from lightfall.ui.panels.agent_editor.panel import AgentEditorPanel

        panel = AgentEditorPanel(registry=stores)
        qtbot.addWidget(panel)

        panel.select_agent("custom")
        panel._desc_edit.setText("edited description")
        panel._prompt_edit.setPlainText("edited prompt")
        assert panel.save_current_agent() is True
        assert panel._agent_error.text() == ""

        spec = parse_agent_file(stores.get("custom").source_path, "user")
        assert spec.description == "edited description"
        assert spec.prompt == "edited prompt"

    def test_save_shows_inline_error_and_leaves_file_intact(self, qtbot, stores):
        from lightfall.ui.panels.agent_editor.panel import AgentEditorPanel

        panel = AgentEditorPanel(registry=stores)
        qtbot.addWidget(panel)

        path = stores.get("custom").source_path
        before = path.read_text(encoding="utf-8")

        panel.select_agent("custom")
        panel._prompt_edit.setPlainText("uses {{nonsense}} variable")
        assert panel.save_current_agent() is False
        assert panel._agent_error.text() != ""
        assert path.read_text(encoding="utf-8") == before
