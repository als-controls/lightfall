"""GitHub Preferences page: account link, sign-in status, and the GitHub App client id."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lightfall_settings import SettingSpec
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from lightfall.github import DEFAULT_CLIENT_ID
from lightfall.github.session import get_github_session
from lightfall.plugins.settings_plugin import SettingsPlugin
from lightfall.ui.dialogs.github_sign_in import start_pat_sign_in, start_sign_in
from lightfall.ui.preferences.form.schema import Embed, Section, SettingField, Text

if TYPE_CHECKING:
    from collections.abc import Sequence

    from PySide6.QtGui import QIcon

GATED_TEXT = "Pushing requires the system-admin permission."
_KINDS = {"app": "GitHub App", "pat": "fine-grained token"}


class GitHubStatusWidget(QWidget):
    """Current GitHub sign-in with Sign in / Use a token / Sign out buttons."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        github_session: Any | None = None,
        checker: Any | None = None,
    ) -> None:
        super().__init__(parent)
        self._session = github_session if github_session is not None else get_github_session()
        if checker is None:
            from lightfall.updates.checker import get_update_checker

            checker = get_update_checker()
        self._checker = checker

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        row = QHBoxLayout()
        self.sign_in_button = QPushButton("Sign in with GitHub")
        self.token_button = QPushButton("Use a token instead…")
        self.sign_out_button = QPushButton("Sign out")
        self.sign_in_button.clicked.connect(lambda: start_sign_in(self))
        self.token_button.clicked.connect(lambda: start_pat_sign_in(self))
        self.sign_out_button.clicked.connect(self._session.sign_out)
        for button in (self.sign_in_button, self.token_button, self.sign_out_button):
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)

        self._session.changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        cred = self._session.credential()
        if cred is None:
            text = "Not signed in"
        else:
            text = f"GitHub: {cred.masked} ({_KINDS.get(cred.kind, cred.kind)})"
        allowed = bool(self._checker.is_allowed())
        if not allowed:
            text += f"\n{GATED_TEXT}"
        self.status_label.setText(text)
        self.sign_in_button.setEnabled(allowed)
        self.token_button.setEnabled(allowed)
        self.sign_out_button.setEnabled(cred is not None)


class GitHubSettingsPlugin(SettingsPlugin):
    """Settings page for GitHub sign-in (per user) and the GitHub App (site-wide)."""

    @property
    def name(self) -> str:
        return "github"

    @property
    def display_name(self) -> str:
        return "GitHub"

    @property
    def icon(self) -> QIcon | None:
        return None

    @property
    def category(self) -> str:
        return "general"

    @property
    def priority(self) -> int:
        return 6  # just after Login & Session (5)

    def specs(self) -> Sequence[SettingSpec]:
        return [
            SettingSpec(key="github_login", type=str, label="GitHub account", page="github"),
            SettingSpec(
                key="github_app_client_id", type=str,
                scopes=frozenset({"facility", "beamline", "branch"}),
                label="GitHub App client id", page="github",
            ),
        ]

    def sections(self) -> Sequence[Section]:
        return [
            Section("GitHub account", [
                SettingField(
                    "github_login", Text(blank_is_unset=True), label="GitHub username",
                    description=(
                        "The GitHub account linked to your Lightfall user. Sign-in is kept "
                        "in memory only and ends when you log out."
                    ),
                ),
                Embed(factory=GitHubStatusWidget, scopes=frozenset({"user"})),
            ]),
            Section("GitHub App", [
                SettingField(
                    "github_app_client_id", Text(blank_is_unset=True),
                    label="Client id", placeholder=DEFAULT_CLIENT_ID,
                    description="Leave unset to use Lightfall's built-in GitHub App.",
                ),
            ]),
        ]
