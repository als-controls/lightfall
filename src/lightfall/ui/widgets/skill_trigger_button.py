"""Reusable button that triggers a Lightfall skill via the Claude Assistant.

``SkillTriggerButton`` is a drop-in widget that encapsulates the
panel→Claude dispatch bridge: get-or-open the singleton Claude Assistant
panel, guard against a busy agent, and hand it a prompt that asks Claude to
run a skill. It is the recommended way to give a panel a "run this skill"
button — see ``triggering-the-claude-assistant`` in
``panel_design/references/panel_design.md``.

The manual form of this pattern lives in
``LogbookPanel._get_claude_panel`` / ``_send_to_claude``; this widget exists
so panels don't have to re-implement it.
"""

from __future__ import annotations

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from lightfall.utils.logging import logger

# The singleton Claude Assistant panel id. PanelRegistry.create() returns the
# existing instance or opens a new one.
_CLAUDE_PANEL_ID = "lightfall.panels.claude"


class SkillTriggerButton(QWidget):
    """A button + status label that dispatches a prompt to the Claude Assistant.

    Layout: a :class:`QPushButton` with a status :class:`QLabel` below it, in a
    margin-free :class:`QVBoxLayout` — the host panel owns the outer margins.

    On click the widget (optionally) shows a Yes/Cancel confirmation, then
    ensures the Claude panel is open, guards on ``agent.is_busy()``, and calls
    ``panel.action_send_message(prompt)``. The status label and a toast report
    the outcome.

    Args:
        skill_name: Human-readable skill name, used in logs and toast text.
        prompt: The prompt sent to Claude. Include the target skill's name
            verbatim and a phrase from its "Use this skill when…" description
            so the agent reliably matches it.
        label: Button text. Defaults to ``"▶ Run"``.
        confirm_text: If set, a Yes/Cancel dialog with this body is shown
            before dispatching.
        confirm_title: Title for the confirmation dialog (defaults to
            ``"Confirm"``). Ignored when ``confirm_text`` is ``None``.
        parent: Parent widget.

    Signals:
        dispatched: Emitted with the prompt string after a *successful*
            dispatch, so host panels can chain follow-up behavior.
    """

    dispatched = Signal(str)

    def __init__(
        self,
        skill_name: str,
        prompt: str,
        label: str = "▶ Run",
        confirm_text: str | None = None,
        confirm_title: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._skill_name = skill_name
        self._prompt = prompt
        self._confirm_text = confirm_text
        self._confirm_title = confirm_title or "Confirm"
        self._setup_ui(label)

    def _setup_ui(self, label: str) -> None:
        # No margins — the host panel sets them.
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._button = QPushButton(label)
        self._button.setToolTip(f"Run the '{self._skill_name}' skill via Claude")
        self._button.clicked.connect(self._on_clicked)
        layout.addWidget(self._button)

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        # Neutral by default. Palette roles (not hex) so the label tracks
        # light/dark theme switches without an explicit re-style.
        self._set_status("", error=False)
        layout.addWidget(self._status_label)

    # ── Status helpers ────────────────────────────────────────────

    def _set_status(self, text: str, *, error: bool) -> None:
        # palette(highlight) for errors, palette(mid) for neutral — both track
        # the active theme, unlike hard-coded hex colors.
        role = "highlight" if error else "mid"
        self._status_label.setStyleSheet(f"color: palette({role});")
        self._status_label.setText(text)

    # ── Click / dispatch ──────────────────────────────────────────

    @Slot()
    def _on_clicked(self) -> None:
        if self._confirm_text is not None:
            reply = QMessageBox.question(
                self,
                self._confirm_title,
                self._confirm_text,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Yes:
                logger.debug(
                    "SkillTriggerButton: '{}' dispatch cancelled at confirm",
                    self._skill_name,
                )
                return
        self._dispatch()

    def _dispatch(self) -> None:
        """Open Claude, guard on busy, and send the prompt."""
        try:
            panel, agent = self._get_claude_panel()
            if panel is None or agent is None:
                self._fail("Claude Assistant is unavailable")
                return
            if agent.is_busy():
                self._fail("Claude is busy — try again in a moment")
                return
            panel.action_send_message(self._prompt)
        except Exception:
            logger.exception(
                "SkillTriggerButton: failed to trigger skill '{}'", self._skill_name
            )
            self._fail("Failed to start skill")
            return

        logger.info("SkillTriggerButton: dispatched skill '{}' to Claude", self._skill_name)
        self._set_status(f"Sent to Claude: {self._skill_name}", error=False)
        self._toast_success()
        self.dispatched.emit(self._prompt)

    def _get_claude_panel(self):
        """Get-or-open the singleton Claude panel.

        Returns ``(panel, agent)`` or ``(None, None)`` when the panel/agent
        isn't available yet.
        """
        from lightfall.ui.panels.registry import PanelRegistry

        registry = PanelRegistry.get_instance()
        panel = registry.create(_CLAUDE_PANEL_ID)
        widget = getattr(panel, "_claude_widget", None)
        if panel is not None and widget is not None and hasattr(widget, "agent"):
            return panel, widget.agent
        return None, None

    def _fail(self, message: str) -> None:
        logger.warning("SkillTriggerButton ('{}'): {}", self._skill_name, message)
        self._set_status(message, error=True)
        self._toast_error(message)

    # ── Toasts (best-effort) ──────────────────────────────────────

    def _toast_success(self) -> None:
        try:
            from lightfall.ui.toast import ToastManager

            ToastManager.get_instance().success("Skill started", self._skill_name)
        except Exception:
            logger.debug("SkillTriggerButton: ToastManager unavailable (success)")

    def _toast_error(self, message: str) -> None:
        try:
            from lightfall.ui.toast import ToastManager

            ToastManager.get_instance().error("Skill failed", message)
        except Exception:
            logger.debug("SkillTriggerButton: ToastManager unavailable (error)")
