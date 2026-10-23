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


class _FakeEntryPoint:
    def __init__(self, name, load):
        self.name = name
        self._load = load

    def load(self):
        return self._load()


def _patch_entry_points(monkeypatch, eps):
    from lightfall.devices.backend_types import registry as registry_mod

    monkeypatch.setattr(
        registry_mod,
        "entry_points",
        lambda group=None: eps if group == registry_mod.ENTRY_POINT_GROUP else [],
    )


def _warnings():
    from loguru import logger

    msgs: list[str] = []
    sink = logger.add(lambda m: msgs.append(str(m)), level="WARNING")
    return msgs, lambda: logger.remove(sink)


def test_entry_point_types_registered_after_builtins(monkeypatch):
    _patch_entry_points(monkeypatch, [_FakeEntryPoint("dummy", lambda: _Dummy)])
    reg = BackendTypeRegistry.get_instance()
    assert isinstance(reg.get("dummy"), _Dummy)
    assert [t.type_id for t in reg.all()][:4] == ["mock", "bcs", "happi", "csm"]


def test_bad_entry_points_are_skipped(monkeypatch):
    from lightfall.devices.backend_types.csm import CSMBackendType

    class _DuplicateCsm(_Dummy):
        type_id = "csm"

    def _boom():
        raise ImportError("no such module")

    _patch_entry_points(
        monkeypatch,
        [
            _FakeEntryPoint("broken", _boom),
            _FakeEntryPoint("not-a-type", lambda: object),
            _FakeEntryPoint("dup-csm", lambda: _DuplicateCsm),
        ],
    )
    msgs, stop = _warnings()
    try:
        reg = BackendTypeRegistry.get_instance()
    finally:
        stop()
    assert isinstance(reg.get("csm"), CSMBackendType)
    for name in ("broken", "not-a-type", "dup-csm"):
        assert sum(name in m for m in msgs) == 1, (name, msgs)
