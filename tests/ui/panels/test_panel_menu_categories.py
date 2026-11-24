"""View > Panels grouping: category order, case-folding, and in-category sort."""

from __future__ import annotations

import pytest

from lightfall.ui.panels.base import PANEL_CATEGORIES, BasePanel, PanelMetadata
from lightfall.ui.panels.registry import PanelRegistry


@pytest.fixture
def registry():
    PanelRegistry.reset()
    reg = PanelRegistry.get_instance()
    yield reg
    PanelRegistry.reset()


def _panel(pid: str, name: str, category: str, menu_order: int = 100) -> type[BasePanel]:
    return type(
        f"P_{pid}",
        (BasePanel,),
        {"panel_metadata": PanelMetadata(id=pid, name=name, category=category, menu_order=menu_order)},
    )


def test_categories_follow_canonical_order_then_alphabetical(registry):
    for pid, cat in [("a", "Zeta"), ("b", "Developer"), ("c", "Acquisition"), ("d", "Alpha"), ("e", "Data")]:
        registry.register(_panel(pid, pid, cat))

    assert list(registry.list_by_category()) == ["Acquisition", "Data", "Developer", "Alpha", "Zeta"]


def test_category_case_is_folded_onto_canonical_spelling(registry):
    registry.register(_panel("a", "SAXS", "analysis"))
    registry.register(_panel("b", "XPCS", "Analysis"))
    registry.register(_panel("c", "X", "user"))
    registry.register(_panel("d", "Y", "User"))

    groups = registry.list_by_category()
    assert [m.name for m in groups["Analysis"]] == ["SAXS", "XPCS"]
    assert "analysis" not in groups
    # Non-canonical categories merge too, keeping the first-seen spelling.
    assert len(groups) == 2


def test_panels_sorted_by_menu_order_then_name(registry):
    registry.register(_panel("a", "zeta", "Data", menu_order=100))
    registry.register(_panel("b", "Beta", "Data", menu_order=100))
    registry.register(_panel("c", "Omega", "Data", menu_order=10))

    assert [m.name for m in registry.list_by_category()["Data"]] == ["Omega", "Beta", "zeta"]


def test_builtin_panels_use_canonical_categories():
    from lightfall.ui.panels import (
        bluesky_panel,
        checkpoints_panel,
        claude_panel,
        content_panel,
        device_panel,
        documents_panel,
        ipython_panel,
        logbook_entries_panel,
        logbook_panel,
        logging_panel,
        monitor_panel,
        pipeline_jobs_panel,
        pipeline_triggers_panel,
        queue,
        shussebora_panel,
        threads_panel,
        tiled_browser_panel,
        visualization_panel,
    )
    from lightfall.ui.panels.agent_editor import panel as agent_editor
    from lightfall.ui.panels.synoptic import panel as synoptic

    modules = [
        bluesky_panel, checkpoints_panel, claude_panel, content_panel, device_panel,
        documents_panel, ipython_panel, logbook_entries_panel, logbook_panel,
        logging_panel, monitor_panel, pipeline_jobs_panel, pipeline_triggers_panel,
        queue, shussebora_panel, threads_panel, tiled_browser_panel, visualization_panel,
        agent_editor, synoptic,
    ]
    found = [
        obj.panel_metadata
        for mod in modules
        for obj in vars(mod).values()
        if isinstance(obj, type) and issubclass(obj, BasePanel) and obj is not BasePanel
        and obj.__module__ == mod.__name__ and "panel_metadata" in vars(obj)
    ]
    assert len(found) >= len(modules)
    bad = {m.name: m.category for m in found if m.category not in PANEL_CATEGORIES}
    assert not bad
