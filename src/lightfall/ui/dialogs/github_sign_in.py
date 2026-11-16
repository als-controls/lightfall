"""GitHub sign-in dialogs (device flow and fine-grained token) and account linking."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from lightfall.github.auth import (
    GitHubAuthError,
    client_id,
    credential_from_grant,
    credential_from_pat,
    poll_for_token,
    request_device_code,
)
from lightfall.github.session import GitHubCredential, GitHubSession, get_github_session, scrub
from lightfall.ui.dialogs.base import LFDialog
from lightfall.utils.crash_diagnostics import gui_thread_only
from lightfall.utils.threads import QThreadFuture

PAT_URL = "https://github.com/settings/personal-access-tokens/new"
PAT_PREFIX = "github_pat_"
PAT_PREFIX_ERROR = "Only fine-grained tokens (github_pat_…) are accepted."

_REASONS = {
    "expired": "The code expired. Close this dialog and try again.",
    "denied": "Authorization was denied on GitHub.",
    "cancelled": "Sign-in cancelled.",
    "invalid": "GitHub rejected the sign-in.",
    "network": "Could not reach GitHub.",
}


def _describe(exc: Exception) -> str:
    if isinstance(exc, GitHubAuthError):
        base = _REASONS.get(exc.reason, "Sign-in failed.")
        detail = scrub(str(exc))
        if detail and detail != exc.reason and exc.reason in ("invalid", "network"):
            return f"{base} ({detail})"
        return base
    return "Sign-in failed."


def _current_username() -> str:
    from lightfall.auth.session import SessionManager

    return SessionManager.get_instance().current_user.username


def confirm_login(cred: GitHubCredential, parent: QWidget | None) -> bool:
    """Check ``cred.login`` against the user's linked ``github_login``.

    Unset: offer to link it. Different: warn, and sign out if declined.
    Returns False only when the sign-in was dropped.
    """
    from lightfall.ui.preferences import PreferencesManager

    prefs = PreferencesManager.get_instance()
    linked = prefs.get("github_login")
    yes = QMessageBox.StandardButton.Yes
    if not linked:
        answer = QMessageBox.question(
            parent, "Link GitHub account",
            f"Link GitHub account {cred.login} to this Lightfall user?",
        )
        if answer == yes:
            prefs.set("github_login", cred.login)  # user scope: the spec allows only "user"
        return True
    if linked != cred.login:
        answer = QMessageBox.warning(
            parent, "Different GitHub account",
            f"You signed in as {cred.login}, but this Lightfall user is linked to "
            f"{linked}. Keep this sign-in?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != yes:
            get_github_session().sign_out()
            return False
    return True


class DeviceSignInDialog(LFDialog):
    """Shows the GitHub device code and polls until the user authorizes it."""

    code_received = Signal(object)

    def __init__(
        self,
        github_session: GitHubSession,
        lightfall_user: str,
        parent: QWidget | None = None,
        *,
        request: Callable[..., Any] = request_device_code,
        poll: Callable[..., Any] = poll_for_token,
        make_credential: Callable[..., GitHubCredential] = credential_from_grant,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sign in with GitHub")
        self._session = github_session
        self._user = lightfall_user
        self._cancel = threading.Event()
        self._finished = False
        self._verification_uri = "https://github.com/login/device"

        layout = QVBoxLayout(self)
        self.status_label = QLabel("Requesting a code from GitHub…")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        row = QHBoxLayout()
        self.code_edit = QLineEdit()
        self.code_edit.setReadOnly(True)
        self.code_edit.setPlaceholderText("Your code appears here")
        row.addWidget(self.code_edit)
        self.copy_button = QPushButton("Copy")
        self.copy_button.clicked.connect(self._copy_code)
        row.addWidget(self.copy_button)
        layout.addLayout(row)

        self.open_button = QPushButton("Open github.com/login/device")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self._open_page)
        layout.addWidget(self.open_button)

        buttons = QDialogButtonBox()
        self.cancel_button = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.cancel_button.clicked.connect(self._on_cancel)
        layout.addWidget(buttons)

        self.code_received.connect(self._on_code)

        def work() -> GitHubCredential:
            code = request(client_id())
            self.code_received.emit(code)
            grant = poll(client_id(), code, cancelled=self._cancel.is_set)
            return make_credential(grant, lightfall_user)

        self._future = QThreadFuture(
            work, callback_slot=self._on_result, except_slot=self._on_error,
            name="github_device_sign_in", log_exceptions=False,
        )
        self._future.start()

    @gui_thread_only
    def _on_code(self, code: Any) -> None:
        if self._cancel.is_set():
            return
        self.code_edit.setText(code.user_code)
        self._verification_uri = code.verification_uri or self._verification_uri
        self.open_button.setEnabled(True)
        self.status_label.setText(
            "Enter this code on GitHub, then authorize Lightfall. Waiting for approval…"
        )

    def _copy_code(self) -> None:
        QGuiApplication.clipboard().setText(self.code_edit.text())

    def _open_page(self) -> None:
        QDesktopServices.openUrl(QUrl(self._verification_uri))

    def _on_cancel(self) -> None:
        self._cancel.set()
        self.reject()

    @gui_thread_only
    def _on_result(self, cred: GitHubCredential) -> None:
        if self._cancel.is_set() or self._finished:
            return  # a late result after Cancel is dropped
        self._finished = True
        self._session.set_credential(cred)
        if confirm_login(cred, self):
            self.accept()
        else:
            self.reject()

    @gui_thread_only
    def _on_error(self, exc: Exception) -> None:
        if self._cancel.is_set():
            return
        self.status_label.setText(_describe(exc))
        self.open_button.setEnabled(False)
        self.cancel_button.setText("Close")


class PatSignInDialog(LFDialog):
    """Sign in with a fine-grained personal access token."""

    def __init__(
        self,
        github_session: GitHubSession,
        lightfall_user: str,
        parent: QWidget | None = None,
        *,
        make_credential: Callable[..., GitHubCredential] = credential_from_pat,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sign in with a GitHub token")
        self._session = github_session
        self._user = lightfall_user
        self._make = make_credential
        self._future: QThreadFuture | None = None

        layout = QVBoxLayout(self)
        link = QLabel(f'<a href="{PAT_URL}">Create a fine-grained token on github.com</a>')
        link.setOpenExternalLinks(False)
        link.linkActivated.connect(lambda url: QDesktopServices.openUrl(QUrl(url)))
        layout.addWidget(link)

        self.token_edit = QLineEdit()
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_edit.setPlaceholderText("github_pat_…")
        self.token_edit.returnPressed.connect(self._submit)
        layout.addWidget(self.token_edit)

        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox()
        self.sign_in_button = buttons.addButton("Sign in", QDialogButtonBox.ButtonRole.AcceptRole)
        self.sign_in_button.clicked.connect(self._submit)
        cancel = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        cancel.clicked.connect(self.reject)
        layout.addWidget(buttons)

    def _submit(self) -> None:
        token = self.token_edit.text().strip()
        if not token.startswith(PAT_PREFIX):
            self.error_label.setText(PAT_PREFIX_ERROR)
            return
        self.error_label.setText("Checking token…")
        self.sign_in_button.setEnabled(False)
        self.token_edit.clear()
        user, make = self._user, self._make

        def work() -> GitHubCredential:
            return make(token, user)

        self._future = QThreadFuture(
            work, callback_slot=self._on_result, except_slot=self._on_error,
            name="github_pat_sign_in", log_exceptions=False,
        )
        self._future.start()

    @gui_thread_only
    def _on_result(self, cred: GitHubCredential) -> None:
        self._session.set_credential(cred)
        if confirm_login(cred, self):
            self.accept()
        else:
            self.reject()

    @gui_thread_only
    def _on_error(self, exc: Exception) -> None:
        self.error_label.setText(_describe(exc))
        self.sign_in_button.setEnabled(True)


def start_sign_in(parent: QWidget | None) -> None:
    """Open the device-flow dialog (the token dialog if there is no client id)."""
    if not client_id():
        start_pat_sign_in(parent)
        return
    DeviceSignInDialog(get_github_session(), _current_username(), parent).exec()


def start_pat_sign_in(parent: QWidget | None) -> None:
    PatSignInDialog(get_github_session(), _current_username(), parent).exec()
