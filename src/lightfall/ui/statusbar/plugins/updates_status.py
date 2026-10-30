"""Updates indicator: shows when installed Lightfall packages have updates.

Hidden when nothing is out of date, and whenever the session is offline,
anonymous, or lacks ``ADMIN_SYSTEM`` (the shared checker reports nothing then).
Clicking opens the update dialog, which can apply the updates and restart.
"""

from __future__ import annotations

from typing import Any, ClassVar

from lightfall.plugins.statusbar_plugin import StatusBarPlugin, StatusBarPluginMetadata
from lightfall.utils.logging import logger


class UpdatesStatusPlugin(StatusBarPlugin):
    """Status bar indicator for available Lightfall package updates."""

    metadata: ClassVar[StatusBarPluginMetadata] = StatusBarPluginMetadata(
        id="lightfall.statusbar.updates",
        name="Updates",
        description="Shows when installed Lightfall packages have updates available",
        priority=90,
        position="permanent",
        tooltip="Lightfall updates",
    )

    def __init__(self, checker: Any | None = None) -> None:
        super().__init__()
        self._checker = checker
        self.set_visible(False)

    @property
    def name(self) -> str:
        return "updates_status"

    @property
    def checker(self) -> Any:
        if self._checker is None:
            from lightfall.updates.checker import get_update_checker

            self._checker = get_update_checker()
        return self._checker

    def connect_signals(self) -> None:
        self.checker.result_changed.connect(self._on_changed)
        self.checker.restart_pending_changed.connect(self._on_changed)
        self.checker.refresh_gate()
        self.update()

    def disconnect_signals(self) -> None:
        for signal in (self.checker.result_changed, self.checker.restart_pending_changed):
            try:
                signal.disconnect(self._on_changed)
            except (RuntimeError, TypeError):
                pass

    def _on_changed(self, *args: Any) -> None:
        self.update()

    def update(self) -> None:
        result = self.checker.result
        count = result.update_count
        names = [r.name for r in result.repo_updates] + [p.name for p in result.pypi_updates]
        if count:
            self.set_text(f"⬆ {count} update{'s' if count != 1 else ''}")
            tip = "Updates available:\n" + "\n".join(names)
            if self.checker.restart_pending:
                tip += "\n\nRestart pending for updates already applied."
            self.set_tooltip(tip + "\n\nClick to review and update.")
        elif self.checker.restart_pending:
            self.set_text("⟳ Restart pending")
            self.set_tooltip("Updates were applied. Click to restart Lightfall.")
        self.set_visible(self.checker.active and (bool(count) or self.checker.restart_pending))

    def on_clicked(self) -> None:
        if not self.checker.is_allowed():
            return
        try:
            from lightfall.core.application import LFApplication
            from lightfall.ui.dialogs.update_dialog import UpdateDialog, ask_restart

            app = LFApplication.get_instance()
            parent = app.main_window if app else None
            if self.checker.result.update_count == 0 and self.checker.restart_pending:
                from lightfall.updates.apply import scan_running
                from lightfall.utils.restart import request_restart

                if ask_restart(parent) and not scan_running():
                    request_restart()
                return
            UpdateDialog(self.checker, parent).exec()
        except Exception as e:  # noqa: BLE001 - a status bar click must never crash the app
            logger.debug("Could not open the update dialog: {}", e)
