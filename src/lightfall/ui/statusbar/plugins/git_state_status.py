"""Git-state indicator: source checkouts off their default branch or with local changes.

Shows when any source-installed Lightfall repo is on a feature branch or a
detached HEAD, has uncommitted changes to tracked files, or has unpushed
commits. Hidden otherwise, and gated like the updates indicator (it reads the
same shared checker). Purely informational.
"""

from __future__ import annotations

from typing import Any, ClassVar

from lightfall.plugins.statusbar_plugin import StatusBarPlugin, StatusBarPluginMetadata
from lightfall.utils.logging import logger


class GitStateStatusPlugin(StatusBarPlugin):
    """Status bar indicator for noteworthy git state of Lightfall source checkouts."""

    metadata: ClassVar[StatusBarPluginMetadata] = StatusBarPluginMetadata(
        id="lightfall.statusbar.git_state",
        name="Git state",
        description="Shows Lightfall source checkouts on a feature branch or with local changes",
        priority=89,
        position="permanent",
        tooltip="Lightfall source checkouts",
    )

    def __init__(self, checker: Any | None = None) -> None:
        super().__init__()
        self._checker = checker
        self.set_visible(False)

    @property
    def name(self) -> str:
        return "git_state_status"

    @property
    def checker(self) -> Any:
        if self._checker is None:
            from lightfall.updates.checker import get_update_checker

            self._checker = get_update_checker()
        return self._checker

    def connect_signals(self) -> None:
        self.checker.result_changed.connect(self._on_changed)
        self.checker.refresh_gate()
        self.update()

    def disconnect_signals(self) -> None:
        try:
            self.checker.result_changed.disconnect(self._on_changed)
        except (RuntimeError, TypeError):
            pass

    def _on_changed(self, *args: Any) -> None:
        self.update()

    def update(self) -> None:
        from lightfall.ui.dialogs.git_state_dialog import describe_state

        repos = self.checker.result.noteworthy_repos
        if repos:
            n = len(repos)
            self.set_text(f"⎇ {n} repo{'s' if n != 1 else ''}")
            lines = []
            for repo in repos:
                state = describe_state(repo)
                lines.append(f"{repo.name}: {repo.branch or 'detached'}"
                             + (f", {state}" if state else ""))
            self.set_tooltip("\n".join(lines) + "\n\nClick for details.")
        self.set_visible(bool(repos))

    def on_clicked(self) -> None:
        try:
            from lightfall.core.application import LFApplication
            from lightfall.ui.dialogs.git_state_dialog import GitStateDialog

            app = LFApplication.get_instance()
            parent = app.main_window if app else None
            GitStateDialog(self.checker.result.noteworthy_repos, parent).exec()
        except Exception as e:  # noqa: BLE001 - a status bar click must never crash the app
            logger.debug("Could not open the git-state dialog: {}", e)
