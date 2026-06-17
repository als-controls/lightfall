from __future__ import annotations

from lightfall.auth.provider_registry import AuthProviderRegistry
from lightfall.plugins.auth_provider_plugin import AuthProviderPlugin
from lightfall.plugins.loader import PluginLoader
from lightfall.plugins.manifest import PluginEntry, PluginManifest


def test_auth_provider_plugin_lands_in_registry():
    AuthProviderRegistry.reset()
    loader = PluginLoader()
    loader.register_plugin_type("auth_provider", AuthProviderPlugin)

    manifest = PluginManifest(
        name="test-auth",
        version="0.0.0",
        description="test",
        plugins=[
            PluginEntry(
                type_name="auth_provider",
                name="fake_provider",
                import_path="tests.plugins.fake_auth_plugin:FakeAuthPlugin",
            ),
        ],
    )
    loader.load_manifest(manifest)
    ok, failed = loader.load_all_sync()

    assert (ok, failed) == (1, 0)
    assert AuthProviderRegistry.get_instance().has("fake_provider")
    AuthProviderRegistry.reset()
