"""Cancelling a login must interrupt the auth provider's blocking wait.

The login runs on a QThreadFuture that blocks in the provider's browser flow.
Cancellation only unblocks it if the worker is given an interrupt_callable
that calls provider.cancel(); otherwise cancel() has nothing to interrupt and
the worker used to be force-terminated (interpreter corruption -> 0xC0000005).
"""
from __future__ import annotations

from types import SimpleNamespace

from lightfall.ui.dialogs.login_dialog import LoginDialog


def test_interrupt_login_cancels_current_provider() -> None:
    cancel_calls: list[bool] = []
    fake_provider = SimpleNamespace(cancel=lambda: cancel_calls.append(True))
    fake_self = SimpleNamespace(_current_provider=fake_provider)

    # Real production hook against a lightweight stand-in self.
    LoginDialog._interrupt_login(fake_self)  # type: ignore[arg-type]

    assert cancel_calls == [True]


def test_interrupt_login_noop_without_provider() -> None:
    fake_self = SimpleNamespace(_current_provider=None)
    # Must not raise when there is no provider yet.
    LoginDialog._interrupt_login(fake_self)  # type: ignore[arg-type]
