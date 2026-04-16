"""Plan UI framework — plans can embed runtime UI widgets.

Plans opt in by decorating with @plan_with_ui(UIClass). When LUCID's
Plans panel submits such a plan, it creates a UI widget and shows it as
a tab in the panel. The plan and UI share state via a module-level
PlanState instance that the plan module defines.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

_PlanFunc = TypeVar("_PlanFunc", bound=Callable[..., Any])


class PlanState(QObject):
    """Shared state between a running plan and its UI.

    Plan subclasses add their own signals and attributes. The plan module
    keeps a module-level instance that both the plan function and the UI
    widget reference directly.

    Thread safety:
      - Qt signals are thread-safe across threads.
      - Simple attribute reads/writes on primitives are GIL-safe.
      - Plans reset their state explicitly at the start of each run.
    """

    status_changed = Signal(str)

    stop_requested: bool = False
    pause_requested: bool = False

    def __init__(self) -> None:
        super().__init__()


class PlanUI(QWidget):
    """Base class for plan UI widgets.

    Subclasses build their UI in __init__ and reference the module-level
    PlanState instance directly (no framework injection needed).
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)


def plan_with_ui(ui_class: type[PlanUI]) -> Callable[[_PlanFunc], _PlanFunc]:
    """Decorator: attach a UI class to a plan function.

    The Plans panel checks for the _plan_ui_class attribute when a plan is
    submitted. If present, the panel instantiates the UI and shows it as
    a tab while the plan runs.

    Example:
        >>> class MyPanel(PlanUI):
        ...     pass
        >>> @plan_with_ui(MyPanel)
        ... def my_plan(detectors):
        ...     yield from bps.count(detectors)
    """

    def decorator(plan_func: _PlanFunc) -> _PlanFunc:
        plan_func._plan_ui_class = ui_class  # type: ignore[attr-defined]
        return plan_func

    return decorator


def get_plan_ui_class(plan_func: Callable[..., Any]) -> type[PlanUI] | None:
    """Return the UI class attached to a plan, or None if it has none."""
    return getattr(plan_func, "_plan_ui_class", None)
