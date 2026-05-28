"""Tests for SkillTriggerButton.

The widget encapsulates the panel→Claude dispatch bridge documented in
``panel_design.md`` (triggering-the-claude-assistant). These tests mock the
PanelRegistry / Claude panel so nothing touches a real agent, and patch the
ToastManager so headless runs don't try to paint toasts.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QMessageBox

from lucid.ui.widgets.skill_trigger_button import SkillTriggerButton


@pytest.fixture
def fake_agent():
    agent = MagicMock()
    agent.is_busy.return_value = False
    return agent


@pytest.fixture
def fake_panel(fake_agent):
    panel = MagicMock()
    # The agent lives at panel._claude_widget.agent (see logbook_panel).
    panel._claude_widget.agent = fake_agent
    return panel


@pytest.fixture
def patched_registry(fake_panel):
    """Patch PanelRegistry so create() returns our fake Claude panel."""
    with patch("lucid.ui.panels.registry.PanelRegistry") as registry_cls:
        registry_cls.get_instance.return_value.create.return_value = fake_panel
        yield fake_panel


@pytest.fixture(autouse=True)
def silence_toasts():
    """Stop the lazy ToastManager import from constructing a real manager."""
    with patch("lucid.ui.toast.ToastManager") as toast_cls:
        yield toast_cls


class TestSkillTriggerButton:
    def test_button_wired_to_handler(self, qtbot):
        # (a) Clicking the button drives the dispatch path.
        widget = SkillTriggerButton("my_skill", "do the thing")
        qtbot.addWidget(widget)
        with patch.object(widget, "_dispatch") as dispatch:
            widget._button.click()
        dispatch.assert_called_once()

    def test_confirm_dialog_shown_when_confirm_text_set(self, qtbot, patched_registry):
        # (b) A confirm_text triggers a Yes/Cancel dialog before dispatch.
        widget = SkillTriggerButton(
            "my_skill", "prompt", confirm_text="Really run it?"
        )
        qtbot.addWidget(widget)
        with patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
        ) as question:
            widget._button.click()
        question.assert_called_once()
        patched_registry.action_send_message.assert_called_once_with("prompt")

    def test_no_confirm_dialog_without_confirm_text(self, qtbot, patched_registry):
        widget = SkillTriggerButton("my_skill", "prompt")
        qtbot.addWidget(widget)
        with patch.object(QMessageBox, "question") as question:
            widget._button.click()
        question.assert_not_called()

    def test_confirm_cancel_prevents_dispatch(self, qtbot, patched_registry):
        widget = SkillTriggerButton(
            "my_skill", "prompt", confirm_text="Really run it?"
        )
        qtbot.addWidget(widget)
        with patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel
        ):
            widget._button.click()
        patched_registry.action_send_message.assert_not_called()

    def test_sends_exact_prompt_on_success(self, qtbot, patched_registry):
        # (c) The exact prompt string reaches action_send_message.
        widget = SkillTriggerButton("my_skill", "EXACT PROMPT TEXT")
        qtbot.addWidget(widget)
        widget._button.click()
        patched_registry.action_send_message.assert_called_once_with(
            "EXACT PROMPT TEXT"
        )

    def test_busy_guard_prevents_dispatch(self, qtbot, patched_registry):
        # (d) When the agent is busy, nothing is sent.
        patched_registry._claude_widget.agent.is_busy.return_value = True
        widget = SkillTriggerButton("my_skill", "prompt")
        qtbot.addWidget(widget)
        widget._button.click()
        patched_registry.action_send_message.assert_not_called()

    def test_dispatched_signal_fires_only_on_success(self, qtbot, patched_registry):
        # (e) The dispatched signal carries the prompt, and only on success.
        widget = SkillTriggerButton("my_skill", "prompt")
        qtbot.addWidget(widget)
        received: list[str] = []
        widget.dispatched.connect(received.append)

        widget._button.click()
        assert received == ["prompt"]

        # Busy → no dispatch → no signal.
        received.clear()
        patched_registry._claude_widget.agent.is_busy.return_value = True
        widget._button.click()
        assert received == []
