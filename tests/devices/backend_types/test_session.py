"""The session mode: one enabled exclusive row decides it."""

from __future__ import annotations

import pytest
from loguru import logger

from lightfall.devices.backend_types import session
from lightfall.devices.backend_types.registry import BackendTypeRegistry
from lightfall.devices.backend_types.rows import BackendRow
from lightfall.devices.backend_types.session import (
    SessionMode,
    crosses_mode_boundary,
    resolve_session_mode,
)
from tests.devices.backend_types.excl_type import ExclType, no_session_mode  # noqa: F401


@pytest.fixture()
def registry(no_session_mode):  # noqa: F811
    BackendTypeRegistry.reset()
    reg = BackendTypeRegistry.get_instance()
    reg.register(ExclType())
    yield reg
    BackendTypeRegistry.reset()


def _excl(name="excl", addr="127.0.0.1", enabled=True, **kw):
    return BackendRow("excl", name, enabled=enabled, config={"addr": addr}, **kw)


def test_no_exclusive_row_means_no_mode(registry):
    assert resolve_session_mode([BackendRow("mock", "mock")], registry) is None


def test_disabled_exclusive_row_ignored(registry):
    rows = [BackendRow("mock", "mock"), _excl(enabled=False)]
    assert resolve_session_mode(rows, registry) is None


def test_first_enabled_exclusive_row_wins_and_warns(registry):
    first, second = _excl("a"), _excl("b", addr="10.0.0.1")
    msgs: list[str] = []
    sink = logger.add(lambda m: msgs.append(str(m)), level="WARNING")
    try:
        mode = resolve_session_mode([first, second], registry)
    finally:
        logger.remove(sink)
    assert mode is not None and mode.row_id == first.id
    assert len(msgs) == 1 and "'b'" in msgs[0]


def test_mode_carries_label_and_env(registry):
    row = _excl()
    assert resolve_session_mode([row], registry) == SessionMode(
        row_id=row.id,
        type_id="excl",
        label="Excl: 127.0.0.1",
        env={"EPICS_CA_ADDR_LIST": "127.0.0.1"},
    )


def test_crosses_mode_boundary(registry):
    row = _excl()
    mock = BackendRow("mock", "mock")
    # normal session
    session.set_session_mode(None)
    assert crosses_mode_boundary([mock], registry) is False
    assert crosses_mode_boundary([mock, row], registry) is True
    # exclusive session on `row`
    session.set_session_mode(resolve_session_mode([row], registry))
    assert crosses_mode_boundary([mock], registry) is True
    edited = _excl(addr="10.0.0.2", id=row.id)
    assert crosses_mode_boundary([edited], registry) is False


def _get(rows):
    stored = {"device_backends": [r.to_dict() for r in rows] if rows is not None else None}
    return lambda key, default=None: stored.get(key, default)


def test_start_session_mode_applies_env(registry):
    from lightfall.devices.backend_types.session import current_session_mode, start_session_mode

    environ: dict[str, str] = {}
    mode = start_session_mode(_get([_excl()]), registry, environ)
    assert mode is not None and mode.label == "Excl: 127.0.0.1"
    assert environ == {"EPICS_CA_ADDR_LIST": "127.0.0.1"}
    assert current_session_mode() is mode


def test_start_session_mode_unknown_type(no_session_mode):  # noqa: F811
    from lightfall.devices.backend_types.session import current_session_mode, start_session_mode

    BackendTypeRegistry.reset()
    try:
        environ: dict[str, str] = {}
        assert start_session_mode(_get([_excl()]), BackendTypeRegistry.get_instance(), environ) is None
        assert environ == {} and current_session_mode() is None
    finally:
        BackendTypeRegistry.reset()


def test_start_session_mode_no_rows(registry):
    from lightfall.devices.backend_types.session import start_session_mode

    environ: dict[str, str] = {}
    assert start_session_mode(_get(None), registry, environ) is None
    assert environ == {}
