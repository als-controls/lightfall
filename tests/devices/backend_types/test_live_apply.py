"""apply_backend_rows: Settings -> Devices edits reach the running catalog."""

from __future__ import annotations

import pytest

from lightfall.auth.session import AuthState
from lightfall.devices.backend_types import startup
from lightfall.devices.backend_types.base import BuildContext
from lightfall.devices.backend_types.registry import BackendTypeRegistry
from lightfall.devices.backend_types.rows import BackendRow
from lightfall.devices.backend_types.startup import apply_backend_rows, install_backend_rows
from tests.devices.backend_types.test_startup import CSM_CFG, FakeCatalog, FakeSession


class LiveCatalog(FakeCatalog):
    def __init__(self):
        super().__init__()
        self.removed = []

    def remove_backend(self, name):
        self.removed.append(name)


@pytest.fixture()
def registry():
    BackendTypeRegistry.reset()
    yield BackendTypeRegistry.get_instance()
    BackendTypeRegistry.reset()
    startup._INSTALLER = None


def _start(rows, registry, session):
    catalog = LiveCatalog()
    install_backend_rows(
        rows, registry=registry, catalog=catalog, ctx=BuildContext(), session=session
    )
    return catalog


def test_apply_before_devices_start_is_a_no_op(registry):
    startup._INSTALLER = None
    assert apply_backend_rows([BackendRow("mock", "mock")]) is False


def test_unchanged_rows_keep_their_backend(registry, qtbot):
    rows = [BackendRow("mock", "mock")]
    catalog = _start(rows, registry, FakeSession(True))

    assert apply_backend_rows(rows) is True

    assert catalog.removed == [] and catalog.added_and_connected == []


def test_removed_disabled_and_changed_rows_are_rebuilt(registry, qtbot):
    keep, drop, off = BackendRow("mock", "a"), BackendRow("mock", "b"), BackendRow("mock", "c")
    catalog = _start([keep, drop, off], registry, FakeSession(True))
    off.enabled = False
    keep_renamed = BackendRow("mock", "a-renamed", id=keep.id)

    apply_backend_rows([keep_renamed, off])

    assert sorted(catalog.removed) == ["a", "b", "c"]
    assert [b.name for b in catalog.added_and_connected] == ["a-renamed"]


def test_new_row_is_added_and_connected(registry, qtbot):
    first = BackendRow("mock", "mock")
    catalog = _start([first], registry, FakeSession(True))

    apply_backend_rows([first, BackendRow("mock", "mock-2")])

    assert [b.name for b in catalog.added_and_connected] == ["mock-2"]
    assert catalog.removed == []


def test_new_csm_row_while_logged_out_waits_for_login(registry, qtbot):
    session = FakeSession(False)
    catalog = _start([], registry, session)

    apply_backend_rows([BackendRow("csm", "csm-7011", config=CSM_CFG)])
    assert catalog.added_and_connected == []

    session.state_changed.emit(AuthState.AUTHENTICATED, AuthState.UNAUTHENTICATED)
    assert [b.name for b in catalog.added_and_connected] == ["csm-7011"]


def test_csm_row_removed_before_login_is_never_built(registry, qtbot):
    session = FakeSession(False)
    catalog = _start([BackendRow("csm", "csm-7011", config=CSM_CFG)], registry, session)

    apply_backend_rows([])
    session.state_changed.emit(AuthState.AUTHENTICATED, AuthState.UNAUTHENTICATED)

    assert catalog.added == [] and catalog.added_and_connected == []
