"""Agent picker for the tabbed Claude panel.

The "+" corner button offers the agents the user may open in a new tab. An
agent is offerable when it is enabled (registry preference), declares itself
``openable`` (``lightfall.openable`` in its definition file -- the observer,
for instance, is a background advisor and sets this false), and is not already
open: a tab is a singleton per agent.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QMenu, QWidget

if TYPE_CHECKING:  # pragma: no cover - typing only
    from lightfall.agents.spec import AgentSpec


def openable_specs(registry, open_names: set[str]) -> list[AgentSpec]:
    """Specs the picker may offer, core agents first then alphabetical.

    Args:
        registry: An ``AgentSpecRegistry`` (only ``enabled_specs()`` is used,
            so disabled agents never reach the menu).
        open_names: Names of agents that already have a tab.

    Returns:
        Offerable specs sorted by (scope != "core", name).
    """
    specs = [
        s
        for s in registry.enabled_specs()
        if getattr(s, "openable", True) and s.name not in open_names
    ]
    specs.sort(key=lambda s: (s.scope != "core", s.name))
    return specs


def build_picker_menu(
    registry,
    open_names: set[str],
    on_pick: Callable[[AgentSpec], None],
    parent: QWidget | None = None,
) -> QMenu:
    """Build the "+" button menu listing openable agents.

    Each entry shows the agent name (with a scope suffix for non-core agents)
    and its description as a tooltip. Disabled placeholder entry when nothing
    is offerable, so the button never opens an empty menu.
    """
    menu = QMenu(parent)
    specs = openable_specs(registry, open_names)
    if not specs:
        act = menu.addAction("No other agents available")
        act.setEnabled(False)
        return menu
    for spec in specs:
        label = spec.name if spec.scope == "core" else f"{spec.name}  ({spec.scope})"
        act = menu.addAction(label)
        act.setToolTip(spec.description)
        act.triggered.connect(lambda _checked=False, s=spec: on_pick(s))
    return menu
