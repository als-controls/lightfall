"""Sign-in step: the usual sign-on through the existing LoginDialog."""
from __future__ import annotations

from typing import TYPE_CHECKING

from lightfall.setup.step import StepState, StepStatus

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

    from lightfall.config.manager import ConfigManager
    from lightfall.setup.context import SetupContext

PROVIDER_LABELS = {"keycloak": "Keycloak", "pam": "Linux user", "local": "Local account"}


def provider_label(config: ConfigManager) -> str:
    ptype = config.model.auth.provider.type
    return PROVIDER_LABELS.get(ptype, ptype)


def signed_in_label(ctx: SetupContext) -> str:
    return f"{ctx.session.current_user.username} · {provider_label(ctx.config)}"


def _session_matches_provider(ctx: SetupContext) -> bool:
    """False when the signed-in session came from a different provider than the one now
    configured (reconfigure_services keeps a live session across a provider change)."""
    provider = getattr(ctx.session, "_provider", None)
    if provider is None:
        return True
    ptype = ctx.config.model.auth.provider.type
    if ptype not in PROVIDER_LABELS:
        return True  # a plugin provider: nothing to compare
    return ptype in type(provider).__name__.lower()


def _signed_in(ctx: SetupContext) -> bool:
    return ctx.session.is_authenticated and _session_matches_provider(ctx)


class SignInStep:
    id = "signin"
    title = "Sign in"

    def detect(self, ctx: SetupContext) -> StepState:
        if _signed_in(ctx):
            return StepState(StepStatus.SATISFIED, signed_in_label(ctx))
        return StepState(StepStatus.NEEDS_INPUT, "Not signed in")

    def widget(self, ctx: SetupContext) -> QWidget:
        from lightfall.setup.pages.signin import SignInPage

        return SignInPage(ctx)

    def validate(self, ctx: SetupContext) -> str | None:
        return None if ctx.session.is_authenticated else "You're not signed in yet."

    def apply(self, ctx: SetupContext) -> None:
        return None  # LoginDialog already attached the session
