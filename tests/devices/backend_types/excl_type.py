"""An exclusive backend type for tests of the session mode (no real plugin needed)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from lightfall.devices.backend_types import session
from lightfall.devices.backend_types.base import BackendType


class ExclType(BackendType):
    type_id = "excl"
    display_name = "Excl"
    exclusive = True

    def default_config(self) -> dict[str, Any]:
        return {"addr": "127.0.0.1"}

    def summary(self, config: dict[str, Any]) -> str:
        return config.get("addr", "")

    def create_editor(self, config, parent=None):
        from lightfall.devices.backend_types.base import BackendEditor

        class _Editor(BackendEditor):
            def get_config(self):
                return dict(config)

        return _Editor(parent)

    def build(self, config, name, ctx):
        return SimpleNamespace(name=name)

    def session_env(self, config: dict[str, Any]) -> dict[str, str]:
        return {"EPICS_CA_ADDR_LIST": config["addr"]}

    def session_label(self, config: dict[str, Any]) -> str:
        return f"Excl: {config['addr']}"


@pytest.fixture()
def no_session_mode():
    session.set_session_mode(None)
    yield
    session.set_session_mode(None)
