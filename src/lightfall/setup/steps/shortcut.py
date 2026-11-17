"""Shortcut step: offer a desktop shortcut (and a Start Menu / app-menu entry).

Satisfied when a Lightfall shortcut is already on the desktop, or when the user chose
none last time. A shortcut that can't be written logs a warning; the wizard moves on.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from lightfall.setup import shortcuts
from lightfall.setup.shortcuts import Location, ShortcutError
from lightfall.setup.step import StepState, StepStatus
from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

    from lightfall.setup.context import SetupContext
    from lightfall.setup.pages.shortcut import ShortcutPage

DECLINED_KEY = "setup.shortcut.declined"


class ShortcutStep:
    id = "shortcut"
    title = "Shortcut"

    def __init__(self) -> None:
        self._page: ShortcutPage | None = None

    def detect(self, ctx: SetupContext) -> StepState:
        if shortcuts.launcher() is None:
            return StepState(StepStatus.SATISFIED, "Not available")
        if shortcuts.shortcut_exists(Location.DESKTOP):
            return StepState(StepStatus.SATISFIED, "On desktop")
        if ctx.config.get(DECLINED_KEY) is True:
            return StepState(StepStatus.SATISFIED, "No shortcut")
        return StepState(StepStatus.NEEDS_INPUT, "Not on desktop")

    def widget(self, ctx: SetupContext) -> QWidget:
        from lightfall.setup.pages.shortcut import ShortcutPage

        self._page = ShortcutPage(ctx)
        return self._page

    def validate(self, ctx: SetupContext) -> str | None:
        return None

    def apply(self, ctx: SetupContext) -> None:
        if self._page is None:
            return
        chosen = self._page.chosen_locations()
        self._page.set_outcome(create_shortcuts(chosen))
        ctx.config.set(DECLINED_KEY, not chosen, persist=True)


def create_shortcuts(locations: list[Location]) -> dict[Location, bool]:
    """Create each shortcut; a failure is logged, never raised. Returns success per location."""
    outcome: dict[Location, bool] = {}
    if not locations:
        return outcome
    launch = shortcuts.launcher()
    for location in locations:
        if launch is None:
            logger.warning("Couldn't create the {} shortcut: lightfall-gui not found",
                           location.value)
            outcome[location] = False
            continue
        try:
            path = shortcuts.create_shortcut(location, launch)
        except ShortcutError as exc:
            logger.warning("Couldn't create the {} shortcut: {}", location.value, exc)
            outcome[location] = False
        else:
            logger.info("Created {} shortcut at {}", location.value, path)
            outcome[location] = True
    return outcome
