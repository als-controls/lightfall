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
