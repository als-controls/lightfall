# tests/auth/test_provider_registry.py
from __future__ import annotations

from lightfall.auth.provider_registry import AuthProviderRegistry
from lightfall.plugins.auth_provider_plugin import AuthProviderPlugin


class _Plugin(AuthProviderPlugin):
    def __init__(self, name, priority=100):
        self._n = name
        self._p = priority
    @property
    def name(self):
        return self._n
    @property
    def priority(self):
        return self._p
    def create_provider(self):
        raise NotImplementedError


def test_register_lookup_and_priority_sort():
    AuthProviderRegistry.reset()
    reg = AuthProviderRegistry.get_instance()
    assert reg.get_all() == []

    low = _Plugin("local", priority=10)
    high = _Plugin("nsls2_tiled", priority=5)
    reg.register(low)
    reg.register(high)

    assert reg.has("local") and reg.get("local") is low
    # Sorted by priority ascending: nsls2_tiled (5) before local (10)
    assert [p.name for p in reg.get_all()] == ["nsls2_tiled", "local"]
    assert set(reg.get_names()) == {"local", "nsls2_tiled"}
    AuthProviderRegistry.reset()
