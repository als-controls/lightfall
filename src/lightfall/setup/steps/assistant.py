"""Assistant step: Settings → Assistant's backend controls inside the wizard."""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from lightfall.setup.step import StepState, StepStatus

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

    from lightfall.setup.context import SetupContext
    from lightfall.setup.pages.assistant import AssistantPage
    from lightfall.ui.preferences.agent_backend_section import AgentBackendSection


class AssistantStep:
    id = "assistant"
    title = "Assistant"

    def __init__(self, info_factory: Callable[[], Any] | None = None,
                 section_factory: Callable[[], AgentBackendSection] | None = None) -> None:
        self._info_factory = info_factory
        self._section_factory = section_factory
        self._page: AssistantPage | None = None

    def detect(self, ctx: SetupContext) -> StepState:
        """Blocking (agy's check runs ``agy models``, up to 30 s): off the GUI thread."""
        if self._info_factory is not None:
            info = self._info_factory()
        else:
            from lightfall.agents.backends.info import backend_info

            info = backend_info()
        status = info.auth_status()
        if status.ok:
            return StepState(StepStatus.SATISFIED, f"{info.display_name} · signed in")
        return StepState(StepStatus.NEEDS_INPUT, f"{info.display_name} · not signed in")

    def widget(self, ctx: SetupContext) -> QWidget:
        from lightfall.setup.pages.assistant import AssistantPage

        section = self._section_factory() if self._section_factory is not None else None
        self._page = AssistantPage(ctx, section)
        return self._page

    def validate(self, ctx: SetupContext) -> str | None:
        return None

    def apply(self, ctx: SetupContext) -> None:
        if self._page is not None:
            self._page.section.save()
