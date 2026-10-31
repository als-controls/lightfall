"""PreferencesManager fills identity prefs from site.bind when nothing sets them."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from lightfall.ui.preferences.manager import PreferencesManager


class FakeCM:
    def __init__(self, bind, **prefs):
        self.model = SimpleNamespace(site=SimpleNamespace(facility="als", bind=bind))
        self.data = {f"preferences.{k}": v for k, v in prefs.items()}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def unset(self, key, *, persist=True):
        self.data.pop(key, None)

    def set(self, key, value, *, persist=False):
        self.data[key] = value


@pytest.fixture
def pm(qapp):
    PreferencesManager.reset()
    yield PreferencesManager.get_instance()
    PreferencesManager.reset()


def test_bound_console_derives_identity(pm):
    pm.set_config_manager(FakeCM("branch:7.0.1.1"))
    assert pm.get("tiled_beamline") == "7.0.1.1"
    assert pm.get("ipc_topic_prefix", "als.7011") == "als.7011"
    assert pm.get("current_beamline", "default") == "default"  # not derived (layout key)


def test_bound_console_ignores_its_own_value_and_blank_counts_as_unset(pm):
    # Bound: the console's YAML no longer overrides the binding (Ron, 2026-10-31).
    pm.set_config_manager(FakeCM("branch:7.0.2.1", ipc_topic_prefix="dev.me", tiled_beamline=""))
    assert pm.get("ipc_topic_prefix", "als.7011") == "als.7021"
    assert pm.get("tiled_beamline", "") == "7.0.2.1"


def test_unbound_keeps_caller_default(pm):
    pm.set_config_manager(FakeCM(None))
    assert pm.get("ipc_topic_prefix", "als.7011") == "als.7011"
    assert pm.get("tiled_beamline", None) is None


# Ron 2026-10-31: identity keys follow the console's binding when bound; editable at
# "This console" when unbound.

def _files(tmp_path, **branch_settings):
    lines = "".join(f"  {k}: {v}\n" for k, v in branch_settings.items()) or "  theme: dark\n"
    (tmp_path / "facility.yaml").write_text("settings:\n  tiled_url: https://f\n",
                                            encoding="utf-8")
    (tmp_path / "branch-7.0.1.1.yaml").write_text("settings:\n" + lines, encoding="utf-8")
    return tmp_path


def test_bound_console_uses_derived_over_console_value(pm):
    cm = FakeCM("branch:7.0.1.1", ipc_topic_prefix="dev.me", tiled_beamline="12.0.2")
    pm.set_config_manager(cm)
    assert pm.console_bound()
    assert pm.get("ipc_topic_prefix", "x") == "als.7011"
    assert pm.get("tiled_beamline") == "7.0.1.1"
    # The console's own value is still its layer value (just not in effect).
    assert pm.layer_value("ipc_topic_prefix", "local") == "dev.me"


def test_bound_console_uses_derived_with_scope_files_loaded(pm, tmp_path, monkeypatch):
    monkeypatch.delenv("LIGHTFALL_SCOPES_DISABLED", raising=False)
    pm.set_config_manager(FakeCM("branch:7.0.1.1", ipc_topic_prefix="dev.me"))
    assert pm.load_local_scopes(_files(tmp_path))
    assert pm.get("ipc_topic_prefix", "x") == "als.7011"
    assert pm.layer_value("ipc_topic_prefix", "local") == "dev.me"


def test_bound_console_prefers_scope_value_over_derived(pm, tmp_path, monkeypatch):
    monkeypatch.delenv("LIGHTFALL_SCOPES_DISABLED", raising=False)
    pm.set_config_manager(FakeCM("branch:7.0.1.1", ipc_topic_prefix="dev.me"))
    assert pm.load_local_scopes(_files(tmp_path, ipc_topic_prefix="als.custom"))
    assert pm.get("ipc_topic_prefix", "x") == "als.custom"
    assert pm.get("tiled_beamline") == "7.0.1.1"  # no scope value: derived


@pytest.mark.parametrize("bind", ["facility", None])
def test_unbound_console_uses_console_value(pm, bind):
    pm.set_config_manager(FakeCM(bind, ipc_topic_prefix="dev.me"))
    assert not pm.console_bound()
    assert pm.derived_value("ipc_topic_prefix") is None
    assert pm.get("ipc_topic_prefix", "x") == "dev.me"
    assert pm.get("tiled_beamline", "dflt") == "dflt"
    pm.set("tiled_beamline", "12.0.2", scope="local")
    assert pm.get("tiled_beamline") == "12.0.2"
    assert pm.layer_value("tiled_beamline", "local") == "12.0.2"


def test_unbound_console_value_with_scope_files_loaded(pm, tmp_path, monkeypatch):
    monkeypatch.delenv("LIGHTFALL_SCOPES_DISABLED", raising=False)
    cm = FakeCM("facility")
    pm.set_config_manager(cm)
    (tmp_path / "facility.yaml").write_text("settings:\n  tiled_url: https://f\n",
                                            encoding="utf-8")
    assert pm.load_local_scopes(tmp_path)
    assert pm.allowed_at("tiled_beamline", "local")
    assert pm.writable_at("tiled_beamline", "local")
    pm.set("tiled_beamline", "12.0.2", scope="local")
    assert cm.data["preferences.tiled_beamline"] == "12.0.2"
    assert pm.get("tiled_beamline") == "12.0.2"


@pytest.mark.parametrize("bind", ["branch:7.0.1.1", "beamline:7.0.1"])
def test_bound_console_refuses_identity_writes_here(pm, bind):
    cm = FakeCM(bind)
    pm.set_config_manager(cm)
    assert pm.allowed_at("ipc_topic_prefix", "local")
    assert not pm.writable_at("ipc_topic_prefix", "local")
    with pytest.raises(ValueError, match="binding"):
        pm.set("ipc_topic_prefix", "dev.me", scope="local")
    with pytest.raises(ValueError, match="binding"):
        pm.remove("ipc_topic_prefix", scope="local")
    assert "preferences.ipc_topic_prefix" not in cm.data
