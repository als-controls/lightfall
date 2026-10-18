"""Facility step: pick the facility whose server configures this install."""
from __future__ import annotations

from typing import TYPE_CHECKING

from lightfall.setup.bootstrap import has_bootstrap
from lightfall.setup.facilities import Facility, load_facilities, normalize_server_url
from lightfall.setup.step import StepState, StepStatus

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

    from lightfall.setup.context import SetupContext


class FacilityStep:
    id = "facility"
    title = "Facility"

    def __init__(self, facilities: list[Facility] | None = None) -> None:
        self._facilities = facilities

    def facilities(self) -> list[Facility]:
        if self._facilities is None:
            self._facilities = load_facilities()
        return self._facilities

    def detect(self, ctx: SetupContext) -> StepState:
        if not has_bootstrap(ctx.config):
            return StepState(StepStatus.NEEDS_INPUT, "Choose where you work")
        return StepState(StepStatus.SATISFIED, ctx.facility_name() or "Facility settings loaded")

    def widget(self, ctx: SetupContext) -> QWidget:
        from lightfall.setup.pages.facility import FacilityPage

        return FacilityPage(ctx, self.facilities())

    def validate(self, ctx: SetupContext) -> str | None:
        if ctx.facility is None:
            return "Choose your facility, or enter its server URL."
        try:
            normalize_server_url(ctx.facility.server_url)
        except ValueError as exc:
            return str(exc)
        return None

    def apply(self, ctx: SetupContext) -> None:
        # The choice is already in ctx.facility. A bootstrap fetched for another
        # facility no longer applies.
        if (ctx.bootstrap is not None and ctx.facility is not None
                and ctx.bootstrap.server_url != ctx.facility.server_url):
            ctx.bootstrap = None
