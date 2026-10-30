"""Built-in backend types: defaults, summaries, validation, build, editors."""

from __future__ import annotations

import pytest

from lightfall.devices.backend_types.base import BuildContext
from lightfall.devices.backend_types.registry import BackendTypeRegistry
from lightfall.devices.backends.bcs import BCSBackend
from lightfall.devices.backends.happi import HappiBackend
from lightfall.devices.backends.mock import MockBackend

CTX = BuildContext("background", 3.0)


@pytest.fixture()
def registry():
    BackendTypeRegistry.reset()
    yield BackendTypeRegistry.get_instance()
    BackendTypeRegistry.reset()


def test_builtins_registered_in_order(registry):
    assert [t.type_id for t in registry.all()][:3] == ["mock", "bcs", "happi"]


def test_mock_build(registry):
    t = registry.get("mock")
    backend = t.build({"include_noisy": False}, "mock-2", CTX)
    assert isinstance(backend, MockBackend)
    assert backend.name == "mock-2"


def test_bcs_build_and_validate(registry):
    t = registry.get("bcs")
    cfg = t.default_config()
    assert cfg == {"host": "localhost", "port": 5577, "timeout_ms": 5000}
    backend = t.build({**cfg, "host": "bcs.lbl.gov"}, "bcs-a", CTX)
    assert isinstance(backend, BCSBackend)
    assert backend.name == "bcs-a"
    labelled = t.build(cfg, "bcs-b", BuildContext(beamline="7.0.1.1"))
    assert labelled._beamline == "7.0.1.1"  # this console's binding, not a row field
    assert t.summary({**cfg, "host": "bcs.lbl.gov"}) == "bcs.lbl.gov:5577"
    assert t.validate({**cfg, "host": "  "}) == ["Server host is required"]


def test_happi_build_passes_context(registry):
    t = registry.get("happi")
    backend = t.build({"path": "/tmp/x.json", "beamline": ""}, "happi-a", CTX)
    assert isinstance(backend, HappiBackend)
    assert backend.name == "happi-a"
    assert backend.path == "/tmp/x.json"
    assert backend._instantiate_mode == "background"
    assert backend._connection_timeout == 3.0
    assert t.summary({"path": "", "beamline": ""}) == "happi default config ($HAPPI_CFG)"


def test_two_happi_rows_get_distinct_names(registry):
    t = registry.get("happi")
    a = t.build({"path": "/a.json", "beamline": ""}, "happi", CTX)
    b = t.build({"path": "/b.json", "beamline": ""}, "happi-2", CTX)
    assert a.name != b.name


@pytest.mark.parametrize("type_id", ["mock", "bcs", "happi"])
def test_editor_round_trips_config(qtbot, registry, type_id):
    t = registry.get(type_id)
    cfg = t.default_config()
    editor = t.create_editor(cfg)
    qtbot.addWidget(editor)
    assert editor.get_config() == cfg


def test_bcs_editor_emits_changed(qtbot, registry):
    editor = registry.get("bcs").create_editor(registry.get("bcs").default_config())
    qtbot.addWidget(editor)
    with qtbot.waitSignal(editor.changed, timeout=1000):
        editor._host.setText("other")
    assert editor.get_config()["host"] == "other"
