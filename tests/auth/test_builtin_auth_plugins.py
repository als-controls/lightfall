from __future__ import annotations

from lightfall.auth.provider_registry import AuthProviderRegistry
from lightfall.auth.providers.builtin_plugins import (
    KeycloakAuthPlugin,
    LocalAuthPlugin,
    PamAuthPlugin,
    register_builtin_auth_plugins,
)
from lightfall.auth.providers.local import LocalAuthProvider


def test_local_plugin_creates_local_provider():
    plugin = LocalAuthPlugin()
    assert plugin.name == "local"
    assert plugin.requires_username is True
    assert plugin.requires_password is True
    assert isinstance(plugin.create_provider(), LocalAuthProvider)


def test_keycloak_and_pam_need_no_form():
    assert KeycloakAuthPlugin().requires_username is False
    assert KeycloakAuthPlugin().requires_password is False
    assert PamAuthPlugin().requires_username is False
    assert PamAuthPlugin().requires_password is False


def test_register_builtins_respects_pam_flag():
    AuthProviderRegistry.reset()
    reg = AuthProviderRegistry.get_instance()
    register_builtin_auth_plugins(reg, config=None, include_pam=False)
    names = set(reg.get_names())
    assert "local" in names and "keycloak" in names
    assert "pam" not in names

    AuthProviderRegistry.reset()
    reg = AuthProviderRegistry.get_instance()
    register_builtin_auth_plugins(reg, config=None, include_pam=True)
    assert "pam" in set(reg.get_names())
    AuthProviderRegistry.reset()


def test_register_builtins_respects_disabled_preference(monkeypatch):
    import lightfall.auth.providers.builtin_plugins as bp

    monkeypatch.setattr(
        bp, "_disabled_plugin_ids", lambda: {"auth_provider:keycloak"}
    )
    AuthProviderRegistry.reset()
    reg = AuthProviderRegistry.get_instance()
    register_builtin_auth_plugins(reg, config=None, include_pam=False)
    names = set(reg.get_names())
    assert "keycloak" not in names  # disabled
    assert "local" in names  # always the fallback
    AuthProviderRegistry.reset()


def test_local_respects_disabled_preference(monkeypatch):
    import lightfall.auth.providers.builtin_plugins as bp

    monkeypatch.setattr(
        bp, "_disabled_plugin_ids", lambda: {"auth_provider:local"}
    )
    AuthProviderRegistry.reset()
    reg = AuthProviderRegistry.get_instance()
    register_builtin_auth_plugins(reg, config=None, include_pam=False)
    names = set(reg.get_names())
    assert "local" not in names  # disabled, so not offered at login
    assert "keycloak" in names
    AuthProviderRegistry.reset()


def test_local_is_disabled_by_default():
    from lightfall.auth.providers.builtin_plugins import DEFAULT_DISABLED_PLUGIN_IDS

    assert "auth_provider:local" in DEFAULT_DISABLED_PLUGIN_IDS


def test_seed_default_disabled_plugins(monkeypatch):
    """Seeding adds the defaults to disabled_plugins once, then leaves the
    user's explicit choices alone."""
    import lightfall.auth.providers.builtin_plugins as bp

    store: dict = {}

    class _FakePrefs:
        def get(self, key, default=None):
            return store.get(key, default)

        def set(self, key, value):
            store[key] = value

    class _FakePrefsManager:
        @staticmethod
        def get_instance():
            return _FakePrefs()

    monkeypatch.setattr(
        "lightfall.ui.preferences.manager.PreferencesManager", _FakePrefsManager
    )

    bp.seed_default_disabled_plugins()
    assert "auth_provider:local" in store["disabled_plugins"]
    assert store[bp._DEFAULTS_SEEDED_PREF] is True

    # User re-enables local; seeding must not undo it on a later run.
    store["disabled_plugins"] = []
    bp.seed_default_disabled_plugins()
    assert store["disabled_plugins"] == []


def test_builtin_manifest_declares_auth_providers():
    """The Plugins settings list is built from manifests; built-in auth
    providers must be declared there to appear (and be toggleable)."""
    from lightfall.plugins.builtin_manifest import builtin_manifest

    auth_names = {e.name for e in builtin_manifest.get_plugins_by_type("auth_provider")}
    assert {"keycloak", "local"} <= auth_names
    # All declared as preload so they're registered before the login dialog.
    assert all(
        e.preload for e in builtin_manifest.get_plugins_by_type("auth_provider")
    )
