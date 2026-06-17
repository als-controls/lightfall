from __future__ import annotations

from lightfall.auth.providers.base import AuthProvider
from lightfall.plugins.auth_provider_plugin import AuthProviderPlugin


class _StubProvider(AuthProvider):
    @property
    def name(self): return "stub"
    @property
    def supports_password_auth(self): return False
    @property
    def supports_browser_auth(self): return False
    async def authenticate(self, username=None, password=None, **kwargs): return None
    async def logout(self, session): return None
    async def refresh(self, session): return None
    async def check_connectivity(self): return True


class _MyAuthPlugin(AuthProviderPlugin):
    @property
    def name(self) -> str:
        return "nsls2_tiled"

    def create_provider(self) -> AuthProvider:
        return _StubProvider()


def test_auth_provider_plugin_contract():
    plugin = _MyAuthPlugin()
    assert plugin.type_name == "auth_provider"
    assert plugin.is_singleton is True
    assert plugin.name == "nsls2_tiled"
    assert plugin.display_name == "Nsls2 Tiled"
    assert plugin.requires_username is True
    assert plugin.requires_password is False
    assert plugin.priority == 100
    assert isinstance(plugin.create_provider(), AuthProvider)
    assert AuthProviderPlugin.validate_class(_MyAuthPlugin) is True
