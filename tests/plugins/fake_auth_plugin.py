from __future__ import annotations

from lightfall.plugins.auth_provider_plugin import AuthProviderPlugin


class FakeAuthPlugin(AuthProviderPlugin):
    @property
    def name(self) -> str:
        return "fake_provider"

    def create_provider(self):
        raise NotImplementedError
