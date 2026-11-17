"""Shortcut page: desktop shortcut (checked by default) and a Start Menu / app-menu entry."""
from __future__ import annotations

from PySide6.QtWidgets import QCheckBox

from lightfall.setup import shortcuts
from lightfall.setup.context import SetupContext
from lightfall.setup.page import StepPage
from lightfall.setup.shortcuts import Location


class ShortcutPage(StepPage):
    def __init__(self, ctx: SetupContext) -> None:
        super().__init__(ctx)
        self._outcome: dict[Location, bool] | None = None
        self.desktop_box = QCheckBox("Create desktop shortcut", self)
        self.desktop_box.setObjectName("setupShortcutDesktop")
        self.desktop_box.setChecked(True)
        self.menu_box = QCheckBox(f"Add to {shortcuts.menu_label()}", self)
        self.menu_box.setObjectName("setupShortcutMenu")
        self.menu_box.setChecked(True)
        for box in (self.desktop_box, self.menu_box):
            box.toggled.connect(lambda _checked: self.primary_changed.emit())
            self.body().addWidget(box)
        self.body().addStretch(1)

    def chosen_locations(self) -> list[Location]:
        chosen = []
        if self.desktop_box.isChecked():
            chosen.append(Location.DESKTOP)
        if self.menu_box.isChecked():
            chosen.append(Location.MENU)
        return chosen

    def set_outcome(self, outcome: dict[Location, bool]) -> None:
        self._outcome = outcome

    # --- StepPage ---------------------------------------------------------------

    def heading(self) -> tuple[str, str]:
        return ("Add a shortcut",
                "Start Lightfall from your desktop without opening a terminal.")

    def primary_label(self) -> str:
        return "Create shortcut" if self.chosen_locations() else "Continue"

    def summary(self) -> str:
        if not self._outcome:
            return "No shortcut"
        if not all(self._outcome.values()):
            return "Couldn't create shortcut"
        return "On desktop" if self._outcome.get(Location.DESKTOP) else (
            f"In {shortcuts.menu_label()}")

    def on_enter(self) -> None:
        existing = [label for location, label in (
            (Location.DESKTOP, "on your desktop"),
            (Location.MENU, f"in the {shortcuts.menu_label()}"),
        ) if shortcuts.shortcut_exists(location)]
        if existing:
            self.note.show_note("info", "Lightfall already has a shortcut "
                                + " and ".join(existing) + "; it will be replaced.")
        else:
            self.note.clear()
