"""Backend rows: persistence, legacy migration, naming, build context."""

from __future__ import annotations

from lightfall.devices.backend_types.base import BuildContext
from lightfall.devices.backend_types.rows import (
    DEVICE_BACKENDS_KEY,
    BackendRow,
    build_context,
    load_or_migrate,
    load_rows,
    migrate_legacy,
    restore_ids_from_value,
    restore_value,
    rows_from_value,
    rows_to_value,
    save_rows,
    unique_name,
)


class FakePrefs:
    def __init__(self, data: dict | None = None) -> None:
        self.data = dict(data or {})
        self.scopes: dict = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value, scope=None):
        self.data[key] = value
        self.scopes[key] = scope


def _by_type(rows):
    return {r.type: r for r in rows}


def test_row_round_trip_keeps_id():
    prefs = FakePrefs()
    row = BackendRow(type="happi", name="happi-a", enabled=False, config={"path": "/x.json"})
    save_rows(prefs, [row])
    assert prefs.data[DEVICE_BACKENDS_KEY] == {
        row.id: {
            "id": row.id,
            "type": "happi",
            "name": "happi-a",
            "enabled": False,
            "config": {"path": "/x.json"},
        }
    }
    (loaded,) = load_rows(prefs.get)
    assert loaded == row


def test_load_rows_absent_is_none():
    assert load_rows(FakePrefs().get) is None


def test_load_rows_skips_malformed_entries():
    prefs = FakePrefs({DEVICE_BACKENDS_KEY: [{"name": "no-type"}, {"type": "mock", "name": "m"}]})
    rows = load_rows(prefs.get)
    assert [r.name for r in rows] == ["m"]


def test_unknown_type_is_preserved():
    prefs = FakePrefs(
        {
            DEVICE_BACKENDS_KEY: [
                {"id": "a", "type": "plugin-x", "name": "x", "enabled": True, "config": {"k": 1}}
            ]
        }
    )
    rows = load_rows(prefs.get)
    save_rows(prefs, rows)
    assert prefs.data[DEVICE_BACKENDS_KEY]["a"]["type"] == "plugin-x"
    assert prefs.data[DEVICE_BACKENDS_KEY]["a"]["config"] == {"k": 1}


def test_union_shape_loads_in_order():
    prefs = FakePrefs({DEVICE_BACKENDS_KEY: {
        "b": {"id": "b", "type": "mock", "name": "first"},
        "a": {"id": "a", "type": "happi", "name": "second"},
    }})
    assert [r.name for r in load_rows(prefs.get)] == ["first", "second"]


def test_union_item_id_comes_from_its_key():
    (row,) = rows_from_value({"k": {"type": "mock", "name": "m"}})
    assert row.id == "k"


def test_rows_to_value_is_keyed_by_id():
    rows = [BackendRow(type="mock", name="m", id="x")]
    assert rows_to_value(rows) == {"x": rows[0].to_dict()}
    assert rows_from_value(rows_to_value(rows)) == rows


def test_restore_ids_accept_both_shapes():
    assert restore_ids_from_value(["a", "b"]) == ["a", "b"]
    assert restore_ids_from_value({"a": True, "b": True}) == ["a", "b"]
    assert restore_ids_from_value(None) == []
    assert restore_value(["a", "b"]) == {"a": True, "b": True}


def test_migrate_fresh_config_is_enabled_mock_only():
    rows = migrate_legacy(FakePrefs().get)
    assert [(r.type, r.name, r.enabled) for r in rows] == [("mock", "mock", True)]
    assert rows[0].config == {"include_noisy": True}


def test_migrate_explicit_all_off():
    prefs = FakePrefs(
        {"device_mock_enabled": False, "device_bcs_enabled": False, "device_happi_enabled": False}
    )
    rows = migrate_legacy(prefs.get)
    assert all(not r.enabled for r in rows)


def test_migrate_bcs_only_with_settings():
    prefs = FakePrefs(
        {
            "device_mock_enabled": False,
            "device_bcs_enabled": True,
            "device_bcs_host": "bcs.lbl.gov",
            "device_bcs_port": 6000,
            "device_bcs_beamline": "7.0.1.1",
            "device_bcs_timeout_ms": 2000,
        }
    )
    rows = _by_type(migrate_legacy(prefs.get))
    assert rows["mock"].enabled is False
    assert rows["bcs"].enabled is True
    assert rows["bcs"].config == {
        "host": "bcs.lbl.gov",
        "port": 6000,
        "beamline": "7.0.1.1",
        "timeout_ms": 2000,
    }
    assert "happi" not in rows


def test_migrate_legacy_device_backend_bcs():
    rows = _by_type(migrate_legacy(FakePrefs({"device_backend": "bcs"}).get))
    assert rows["bcs"].enabled is True
    assert rows["mock"].enabled is False


def test_migrate_disabled_happi_with_path_keeps_config():
    prefs = FakePrefs(
        {
            "device_happi_enabled": False,
            "device_happi_path": "/db.json",
            "device_happi_beamline": "bl1",
            "device_mock_enabled": True,
        }
    )
    rows = _by_type(migrate_legacy(prefs.get))
    assert rows["happi"].enabled is False
    assert rows["happi"].config == {"path": "/db.json", "beamline": "bl1"}


def test_load_or_migrate_saves_once():
    prefs = FakePrefs({"device_backend": "mock"})
    rows = load_or_migrate(prefs)
    assert DEVICE_BACKENDS_KEY in prefs.data
    assert prefs.scopes[DEVICE_BACKENDS_KEY] == "local"  # console config, never user scope
    assert load_or_migrate(prefs) == rows


def test_empty_list_is_not_remigrated():
    prefs = FakePrefs({DEVICE_BACKENDS_KEY: [], "device_mock_enabled": True})
    assert load_or_migrate(prefs) == []
    prefs = FakePrefs({DEVICE_BACKENDS_KEY: {}, "device_mock_enabled": True})
    assert load_or_migrate(prefs) == []


def test_unique_name():
    assert unique_name("happi", []) == "happi"
    assert unique_name("happi", ["happi"]) == "happi-2"
    assert unique_name("happi", ["HAPPI", "happi-2"]) == "happi-3"


def test_build_context_defaults_and_legacy_fallback():
    assert build_context(FakePrefs().get) == BuildContext("none", 5.0)
    assert (
        build_context(FakePrefs({"device_happi_instantiate": True}).get).instantiate_mode
        == "blocking"
    )
    ctx = build_context(
        FakePrefs({"device_instantiate_mode": "background", "device_connection_timeout": 2.5}).get
    )
    assert ctx == BuildContext("background", 2.5)
