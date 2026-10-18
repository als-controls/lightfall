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


# Built-in provider types and the class that implements each (matched through the MRO,
# so subclasses count; nothing is imported to check).
PROVIDER_CLASSES = {
    "keycloak": "lightfall.auth.providers.keycloak.KeycloakAuthProvider",
    "pam": "lightfall.auth.providers.pam.PamAuthProvider",
    "local": "lightfall.auth.providers.local.LocalAuthProvider",
}


def _provider_type(provider: object) -> str | None:
    """The built-in type ("keycloak", "pam", "local") of a provider object, else None."""
    names = {f"{c.__module__}.{c.__qualname__}" for c in type(provider).__mro__}
    for ptype, cls in PROVIDER_CLASSES.items():
        if cls in names:
            return ptype
    return None


def _session_matches_provider(ctx: SetupContext) -> bool:
    """False when the signed-in session came from a different provider than the one now
    configured (reconfigure_services keeps a live session across a provider change)."""
    provider = ctx.session.provider
    if provider is None:
        return True
    ptype = ctx.config.model.auth.provider.type
    if ptype not in PROVIDER_CLASSES:
        return True  # a plugin provider: nothing to compare
    return _provider_type(provider) == ptype


def session_provider_label(ctx: SetupContext) -> str:
    """Label of the provider the live session came from ("" if unknown)."""
    provider = ctx.session.provider
    if provider is None:
        return ""
    ptype = _provider_type(provider)
    return PROVIDER_LABELS[ptype] if ptype is not None else type(provider).__name__


def signed_in_label(ctx: SetupContext) -> str:
    label = session_provider_label(ctx) or provider_label(ctx.config)
    return f"{ctx.session.current_user.username} · {label}"


def _signed_in_this_run(ctx: SetupContext) -> bool:
    """The live session is the one this wizard run's sign-in created."""
    made = ctx.signed_in_session
    return made is not None and ctx.session.session is made


def is_signed_in(ctx: SetupContext) -> bool:
    """Signed in this run (any offered provider), or with the provider configured now."""
    if not ctx.session.is_authenticated:
        return False
    return _signed_in_this_run(ctx) or _session_matches_provider(ctx)


class SignInStep:
    id = "signin"
    title = "Sign in"

    def detect(self, ctx: SetupContext) -> StepState:
        if is_signed_in(ctx):
            return StepState(StepStatus.SATISFIED, signed_in_label(ctx))
        return StepState(StepStatus.NEEDS_INPUT, "Not signed in")

    def widget(self, ctx: SetupContext) -> QWidget:
        from lightfall.setup.pages.signin import SignInPage

        return SignInPage(ctx)

    def validate(self, ctx: SetupContext) -> str | None:
        return None if is_signed_in(ctx) else "You're not signed in yet."

    def apply(self, ctx: SetupContext) -> None:
        return None  # LoginDialog already attached the session
