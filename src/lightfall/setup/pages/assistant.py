"""Assistant page: AgentBackendSection's groups with a segmented backend control."""
from __future__ import annotations

from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QPushButton

from lightfall.agents.backends.select import AGY
from lightfall.setup.context import SetupContext
from lightfall.setup.page import StepPage
from lightfall.ui.preferences.agent_backend_section import BACKEND_KINDS, AgentBackendSection


class AssistantPage(StepPage):
    def __init__(self, ctx: SetupContext, section: AgentBackendSection | None = None) -> None:
        super().__init__(ctx)
        self.section = section or AgentBackendSection()
        self._buttons: dict[str, QPushButton] = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        row = QHBoxLayout()
        row.setSpacing(0)
        last = len(BACKEND_KINDS) - 1
        for index, (kind, label) in enumerate(BACKEND_KINDS):
            button = QPushButton(label, self)
            button.setObjectName(f"setupBackend-{kind}")
            button.setCheckable(True)
            button.setProperty("modern", "segment")
            button.setProperty("segPos", "first" if index == 0 else
                               "last" if index == last else "mid")
            button.clicked.connect(lambda _checked=False, k=kind: self.section.set_kind(k))
            group.addButton(button)
            row.addWidget(button)
            self._buttons[kind] = button
        row.addStretch(1)
        self.body().addLayout(row)
        self.body().addWidget(self.section.create_backend_group(show_selector=False))
        self.body().addWidget(self.section.create_gemini_group())
        self.body().addStretch(1)
        self.section.on_kind_changed = self._on_kind_changed
        self.section.load()  # default backend: agent_backend (the bootstrap's agent.backend)

    def _on_kind_changed(self, kind: str) -> None:
        button = self._buttons.get(kind)
        if button is not None:
            button.setChecked(True)

    def heading(self) -> tuple[str, str]:
        return ("Your assistant",
                "Lightfall's assistant can drive panels, devices and plans. Choose a backend "
                "and sign in.")

    def summary(self) -> str:
        name = "Gemini" if self.section.selected_kind() == AGY else "Claude"
        status = self.section.last_status
        signed_in = status is not None and status.ok
        return f"{name} · {'signed in' if signed_in else 'not signed in'}"

    def on_leave(self) -> None:
        self.section.stop()  # drop any in-flight check and stop sign-in polling

    def close_page(self) -> None:
        self.section.stop()
