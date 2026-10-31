"""ScopedBackend.targets for console-only keys."""
# ruff: noqa: F811, F401  (the make fixture is imported from test_scoped_backend)
from __future__ import annotations

import pytest

from lightfall.scopes import keys
from tests.ui.preferences.test_scoped_backend import make


@pytest.fixture(autouse=True)
def _isolated_registry():
    keys._reset_client_registry()
    yield
    keys._reset_client_registry()


def test_console_only_key_targets_local_only(make):
    backend, *_ = make()
    backend.activate()
    assert backend.targets("proxy_host") == ["local"]
    assert backend.targets("claude_api_key") == ["local"]
