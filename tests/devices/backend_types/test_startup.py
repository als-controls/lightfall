"""install_backend_rows: build, skip, and defer-until-login."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QObject, Signal

from lightfall.auth.session import AuthState
from lightfall.devices.backend_types import startup
from lightfall.devices.backend_types.base import BuildContext
from lightfall.devices.backend_types.registry import BackendTypeRegistry
from lightfall.devices.backend_types.rows import BackendRow
from lightfall.devices.backend_types.startup import install_backend_rows


class FakeCatalog:
    def __init__(self):
        self.added = []
        self.added_and_connected = []

    def add_backend(self, backend):
        self.added.append(backend)

    def add_and_connect_backend(self, backend):
        self.added_and_connected.append(backend)


class FakeSession(QObject):
    state_changed = Signal(object, object)

    def __init__(self, authenticated: bool):
        super().__init__()
        self.is_authenticated = authenticated


CSM_CFG = {
    "url": "https://csm.test",
    "beamline_id": 12,
    "beamline_name": "7.0.1.1",
    "ioc_id": None,
    "ioc_name": None,
    "name_filter": "",
    "regex": False,
    "include_inactive": False,
}


@pytest.fixture()
def registry():
    BackendTypeRegistry.reset()
    yield BackendTypeRegistry.get_instance()
    BackendTypeRegistry.reset()
    startup._INSTALLER = None  # don't let later tests' saves apply to this FakeCatalog


def _install(rows, registry, session, catalog=None):
    catalog = catalog or FakeCatalog()
    deferred = install_backend_rows(
        rows, registry=registry, catalog=catalog, ctx=BuildContext(), session=session
    )
    return catalog, deferred


def test_enabled_rows_added_disabled_and_unknown_skipped(registry, qtbot):
    rows = [
        BackendRow("mock", "mock"),
        BackendRow("mock", "mock-2", enabled=False),
        BackendRow("plugin-x", "x"),
        BackendRow("happi", "happi", config={"path": "", "beamline": ""}),
        BackendRow("happi", "happi-2", config={"path": "/b.json", "beamline": ""}),
    ]
    catalog, deferred = _install(rows, registry, FakeSession(True))
    assert [b.name for b in catalog.added] == ["mock", "happi", "happi-2"]
    assert deferred == []


def test_csm_added_immediately_when_logged_in(registry, qtbot):
    catalog, deferred = _install(
        [BackendRow("csm", "csm-7011", config=CSM_CFG)], registry, FakeSession(True)
    )
    assert [b.name for b in catalog.added] == ["csm-7011"]
    assert deferred == []


def test_csm_deferred_until_login_then_added_once(registry, qtbot):
    session = FakeSession(False)
    catalog, deferred = _install([BackendRow("csm", "csm-7011", config=CSM_CFG)], registry, session)
    assert catalog.added == []
    assert [r.name for r in deferred] == ["csm-7011"]

    session.state_changed.emit(AuthState.AUTHENTICATING, AuthState.UNAUTHENTICATED)
    assert catalog.added_and_connected == []

    session.state_changed.emit(AuthState.AUTHENTICATED, AuthState.AUTHENTICATING)
    assert [b.name for b in catalog.added_and_connected] == ["csm-7011"]

    session.state_changed.emit(AuthState.UNAUTHENTICATED, AuthState.AUTHENTICATED)
    session.state_changed.emit(AuthState.AUTHENTICATED, AuthState.UNAUTHENTICATED)
    assert len(catalog.added_and_connected) == 1


def test_build_failure_is_logged_not_raised(registry, qtbot):
    bad = BackendRow("csm", "csm-bad", config={**CSM_CFG, "beamline_id": None})
    catalog, deferred = _install([bad, BackendRow("mock", "mock")], registry, FakeSession(True))
    assert [b.name for b in catalog.added] == ["mock"]


def test_exclusive_row_suppresses_others(registry, qtbot):
    from tests.devices.backend_types.excl_type import ExclType

    registry.register(ExclType())
    rows = [BackendRow("mock", "mock"), BackendRow("excl", "sim", config={"addr": "127.0.0.1"})]
    catalog, _ = _install(rows, registry, FakeSession(True))
    assert [b.name for b in catalog.added] == ["sim"]


def test_rows_pointing_at_same_happi_file_load_once(registry, qtbot, tmp_path):
    """Same file spelled differently (slashes, and case on Windows) is one source."""
    import os

    db = tmp_path / "bcs.json"
    spelled_differently = str(db).replace("\\", "/")
    if os.name == "nt":
        spelled_differently = spelled_differently.upper()
    rows = [
        BackendRow("happi", "bcs-12021-chipps", config={"path": str(db)}),
        BackendRow("happi", "happi", config={"path": spelled_differently}),
        BackendRow("happi", "other", config={"path": str(tmp_path / "other.json")}),
    ]
    catalog, _ = _install(rows, registry, FakeSession(authenticated=True))
    assert [b.name for b in catalog.added] == ["bcs-12021-chipps", "other"]


def test_happi_rows_without_path_are_not_deduplicated():
    from lightfall.devices.backend_types.happi import HappiBackendType

    assert HappiBackendType().source_key({"path": ""}) is None
