"""console_only and client-registered specs."""
from __future__ import annotations

import pytest
from lightfall_settings import SettingSpec

from lightfall.scopes import keys
from lightfall.scopes.keys import client_registry, console_only, register_client_specs


@pytest.fixture(autouse=True)
def _isolated_registry():
    keys._reset_client_registry()
    yield
    keys._reset_client_registry()


def test_console_only_matches_local_only():
    assert console_only("proxy_host") and console_only("my_api_key") and not console_only("theme")


def test_registered_client_spec_reaches_registry():
    register_client_specs([SettingSpec(key="visualization.colormap",
                                       scopes=frozenset({"user", "beamline"}))])
    assert client_registry().get("visualization.colormap").allows("beamline")


def test_shared_spec_wins_over_client_spec():
    register_client_specs([SettingSpec(key="tiled_url", scopes=frozenset({"user"}))])
    assert client_registry().get("tiled_url").allows("facility")


def test_registry_is_cached_and_updated_in_place():
    reg = client_registry()
    register_client_specs([SettingSpec(key="x.late", scopes=frozenset({"beamline"}))])
    assert client_registry() is reg
    assert reg.get("x.late").allows("beamline")


def test_later_registration_replaces_earlier():
    register_client_specs([SettingSpec(key="x.k", scopes=frozenset({"user"}))])
    register_client_specs([SettingSpec(key="x.k", scopes=frozenset({"beamline"}))])
    spec = client_registry().get("x.k")
    assert spec.allows("beamline") and not spec.allows("user")
