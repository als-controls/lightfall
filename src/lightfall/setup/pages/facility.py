"""Facility page: a tile per packaged facility plus "Other facility" (URL field)."""
from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLineEdit

from lightfall.setup.context import SetupContext
from lightfall.setup.facilities import Facility, other_facility
from lightfall.setup.page import StepPage, Tile

OTHER = "other"


class FacilityPage(StepPage):
    def __init__(self, ctx: SetupContext, facilities: list[Facility]) -> None:
        super().__init__(ctx)
        self._facilities = {facility.id: facility for facility in facilities}
        self._tiles: dict[str, Tile] = {}
        self._selected: str | None = None
        row = QHBoxLayout()
        row.setSpacing(12)
        for facility in facilities:
            tile = Tile(facility.name, facility.subtitle, parent=self)
            tile.setObjectName(f"facilityTile-{facility.id}")
            tile.clicked.connect(lambda fid=facility.id: self._select(fid))
            row.addWidget(tile, 1)
            self._tiles[facility.id] = tile
        other = Tile("Other facility", "Enter your facility's Lightfall server", parent=self)
        other.setObjectName("facilityTile-other")
        other.clicked.connect(lambda: self._select(OTHER))
        row.addWidget(other, 1)
        self._tiles[OTHER] = other
        self.body().addLayout(row)
        self._url = QLineEdit(self)
        self._url.setObjectName("facilityUrl")
        self._url.setPlaceholderText("https://lightfall.example.org")
        self._url.returnPressed.connect(self.on_primary)
        self._url.hide()
        self.body().addWidget(self._url)
        self.body().addStretch(1)
        self._preselect()

    def _preselect(self) -> None:
        current = self.ctx.facility
        stored = self.ctx.server_url()
        if current is not None and current.id in self._facilities:
            self._select(current.id)
        elif stored:
            match = next((f.id for f in self._facilities.values() if f.server_url == stored),
                         None)
            if match is not None:
                self._select(match)
            else:
                self._url.setText(stored)
                self._select(OTHER)
        elif len(self._facilities) == 1:
            self._select(next(iter(self._facilities)))

    def _select(self, fid: str) -> None:
        self._selected = fid
        for key, tile in self._tiles.items():
            tile.set_selected(key == fid)
        self.note.clear()
        if fid == OTHER:
            self.ctx.facility = None
            self._url.show()
            self._url.setFocus()
        else:
            self.ctx.facility = self._facilities[fid]
            self._url.hide()
        self.primary_changed.emit()

    def heading(self) -> tuple[str, str]:
        return ("Where are you working?",
                "Lightfall loads its sign-on, services and assistant settings from your "
                "facility's server.")

    def summary(self) -> str:
        return self.ctx.facility.name if self.ctx.facility is not None else ""

    def on_primary(self) -> None:
        if self._selected == OTHER:
            try:
                self.ctx.facility = other_facility(self._url.text())
            except ValueError as exc:
                self.ctx.facility = None
                self.note.show_note("error", str(exc))
                return
        self.completed.emit()
