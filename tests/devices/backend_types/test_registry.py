"""BackendTypeRegistry behaviour."""
from __future__ import annotations

import pytest

from lightfall.devices.backend_types.base import BackendType
from lightfall.devices.backend_types.registry import BackendTypeRegistry


class _Dummy(BackendType):
    type_id = "dummy"
    display_name = "Dummy"

    def default_config(self):
        return {}

    def summary(self, config):
        return ""

    def create_editor(self, config, parent=None):
        raise NotImplementedError

    def build(self, config, name, ctx):
        raise NotImplementedError


@pytest.fixture(autouse=True)
def _fresh_registry():
    BackendTypeRegistry.reset()
    yield
    BackendTypeRegistry.reset()


def test_register_get_all():
    reg = BackendTypeRegistry.get_instance()
    t = _Dummy()
    reg.register(t)
    assert reg.get("dummy") is t
    assert reg.all()[-1] is t
    assert reg.get("nope") is None


def test_duplicate_type_id_raises():
    reg = BackendTypeRegistry.get_instance()
    reg.register(_Dummy())
    with pytest.raises(ValueError, match="dummy"):
        reg.register(_Dummy())


def test_validate_defaults_to_no_errors():
    assert _Dummy().validate({}) == []
