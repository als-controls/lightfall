"""Layered device rows: what a scope shows and how its own layer is edited."""

from __future__ import annotations

import pytest

from lightfall.devices.backend_types.registry import BackendTypeRegistry
from lightfall.devices.backend_types.rows import (
    BackendRow,
    enter_exclusive,
    leave_exclusive,
    normalized,
    set_enabled,
    shown_rows,
    with_row,
    without_row,
)
from tests.devices.backend_types.excl_type import ExclType

ORIGIN = "Beamline 7.0.1"


@pytest.fixture()
def registry():
    BackendTypeRegistry.reset()
    reg = BackendTypeRegistry.get_instance()
    reg.register(ExclType())
    yield reg
    BackendTypeRegistry.reset()


def _mock(rid, enabled=True, name=None):
    return BackendRow("mock", name or rid, enabled=enabled, config={}, id=rid).to_dict()


def _inh(*items):
    return {item["id"]: (item, ORIGIN) for item in items}


def test_shown_rows_orders_inherited_then_own():
    a, b = _mock("a"), _mock("b")
    own = {"z": _mock("z"), "b": {**b, "enabled": False}}
    shown = shown_rows(own, _inh(a, b))
    assert [s.row.id for s in shown] == ["a", "b", "z"]
    assert [(s.own, s.origin) for s in shown] == [(False, ORIGIN), (True, ORIGIN), (True, None)]
    assert shown[1].row.enabled is False


def test_shown_rows_skips_malformed():
    assert shown_rows({"x": {"name": "no type"}, "y": None}, {}) == []


def test_switch_off_then_on_is_a_noop():
    a = _mock("a")
    inh = _inh(a)
    off = set_enabled(None, inh, "a", False)
    assert off == {"a": {**a, "enabled": False}}
    assert set_enabled(off, inh, "a", True) is None


def test_without_row_on_override_uses_inherited():
    a = _mock("a")
    own = {"a": {**a, "enabled": False}, "n": _mock("n")}
    assert without_row(own, "a", _inh(a)) == {"n": _mock("n")}
    assert without_row({"a": {**a, "enabled": False}}, "a", _inh(a)) is None


def test_with_row_keeps_position():
    own = {"a": _mock("a"), "b": _mock("b")}
    renamed = BackendRow.from_dict({**_mock("a"), "name": "renamed"})
    out = with_row(own, renamed)
    assert list(out) == ["a", "b"]
    assert out["a"]["name"] == "renamed"
    assert own["a"]["name"] == "a"  # input untouched
    out2 = with_row(own, BackendRow.from_dict(_mock("c")))
    assert list(out2) == ["a", "b", "c"]
    assert with_row(None, BackendRow.from_dict(_mock("c"))) == {"c": _mock("c")}


def test_enter_and_leave_exclusive_over_inherited_rows(registry):
    a, b = _mock("a"), _mock("b")
    p = BackendRow("excl", "p", enabled=False, config={"addr": "x"}, id="p").to_dict()
    inh = _inh(a, b)
    own = {"p": p}
    entered, off_ids = enter_exclusive(own, inh, "p", registry)
    assert entered == {
        "a": {**a, "enabled": False},
        "b": {**b, "enabled": False},
        "p": {**p, "enabled": True},
    }
    assert off_ids == ["a", "b"]
    assert own == {"p": p}
    assert leave_exclusive(entered, inh, "p", off_ids) == {"p": {**p, "enabled": False}}


def test_leave_exclusive_ignores_unshown_restore_ids(registry):
    p = BackendRow("excl", "p", config={"addr": "x"}, id="p").to_dict()
    assert leave_exclusive({"p": p}, {}, "p", ["gone"]) == {"p": {**p, "enabled": False}}


def test_normalized_empty_is_none():
    a = _mock("a")
    assert normalized({"a": dict(a)}, _inh(a)) is None
    assert normalized({}, {}) is None
    assert normalized(None, _inh(a)) is None
    assert normalized({"a": {**a, "enabled": False}}, _inh(a)) == {"a": {**a, "enabled": False}}
