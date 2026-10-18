"""Sign-in page: an SSO card; the primary button opens the existing LoginDialog."""
from __future__ import annotations

from urllib.parse import urlsplit

from PySide6.QtCore import Qt

from lightfall.setup.context import SetupContext
from lightfall.setup.page import StepPage, Tile
from lightfall.setup.steps.signin import provider_label, signed_in_label
from lightfall.ui.dialogs.login_dialog import LoginDialog, LoginResult


class SignInPage(StepPage):
    def __init__(self, ctx: SetupContext) -> None:
        super().__init__(ctx)
        self._card = Tile("", "", avatar="🔑", parent=self)
        self._card.setObjectName("setupSsoCard")
        self._card.setCursor(Qt.CursorShape.ArrowCursor)
        self.body().addWidget(self._card)
        self.body().addStretch(1)
        self._refresh()

    def heading(self) -> tuple[str, str]:
        return ("Sign in",
                "One sign-in also connects Tiled and the logbook, and loads your facility, "
                "beamline and personal settings.")

    def primary_label(self) -> str:
        if self.ctx.session.is_authenticated:
            return "Continue"
        return f"Sign in with {self.ctx.facility_label()}"

    def skip_label(self) -> str:
        return "Continue as guest"

    def summary(self) -> str:
        return signed_in_label(self.ctx) if self.ctx.session.is_authenticated else ""

    def on_enter(self) -> None:
        self._refresh()  # the Network step may have changed the sign-on config
        self.primary_changed.emit()

    def _refresh(self) -> None:
        provider = self.ctx.config.model.auth.provider
        label = provider_label(self.ctx.config)
        if self.ctx.session.is_authenticated:
            user = self.ctx.session.current_user.username
            self._card.set_text(f"Signed in as {user}", label)
        elif provider.type == "keycloak":
            host = urlsplit(provider.server_url).hostname or provider.server_url
            self._card.set_text(f"{self.ctx.facility_label()} single sign-on",
                                f"{label} · {host} · opens your browser")
        else:
            self._card.set_text(label, "Sign in with your account")
        if self.ctx.bootstrap is not None:
            loaded = ", ".join(self.ctx.bootstrap.summary())
            if loaded:
                name = self.ctx.facility_name() or self.ctx.facility_label()
                self.note.show_note("ok", f"Loaded from {name}: {loaded}.")

    def on_primary(self) -> None:
        if self.ctx.session.is_authenticated:
            self.completed.emit()
            return
        dialog = LoginDialog(parent=self.window(), title="Sign in", allow_guest=True,
                             show_on_expiry=False)
        dialog.exec()
        result = dialog.login_result
        if result == LoginResult.AUTHENTICATED:
            self._refresh()
            self.primary_changed.emit()
            self.completed.emit()
        elif result == LoginResult.GUEST:
            self.skip_requested.emit()
        else:
            self.note.show_note("warn", "Sign-in was cancelled. Try again, or continue as guest.")
            self.primary_changed.emit()
