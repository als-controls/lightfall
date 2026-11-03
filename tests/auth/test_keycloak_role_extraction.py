"""Keycloak role-claim extraction, incl. ALS beamline-scoped roles.

ALS Keycloak issues staff roles with a "<role>:<beamline>" convention
(e.g. "staff:7.0.1.1"). The mapper must resolve these to the base NCS role
so beamline scientists get DEVICE_CONFIGURE, while unrelated scoped claims
(e.g. "tiled:admin") must NOT grant any NCS role.
"""
from __future__ import annotations

import pytest

from lightfall.auth.policy import Permission, PolicyEngine, Role
from lightfall.auth.providers.keycloak import KeycloakAuthProvider, KeycloakConfig
from lightfall.auth.session import User


@pytest.fixture
def provider() -> KeycloakAuthProvider:
    config = KeycloakConfig(
        server_url="http://keycloak.example/auth",
        realm="test",
        client_id="test-client",
    )
    return KeycloakAuthProvider(config, callback_timeout=60)


def _realm(roles: list[str]) -> dict:
    return {"realm_access": {"roles": roles}}


def test_beamline_scoped_staff_maps_to_staff(provider: KeycloakAuthProvider) -> None:
    roles = provider._extract_roles(_realm(["staff:7.0.1.1"]))
    assert Role.STAFF in roles


def test_scoped_staff_grants_device_configure(provider: KeycloakAuthProvider) -> None:
    roles = provider._extract_roles(_realm(["staff:7.0.1.1"]))
    user = User(username="ronpandolfi", roles=roles)
    assert PolicyEngine().check_permission(user, Permission.DEVICE_CONFIGURE)


def test_real_world_token_claims(provider: KeycloakAuthProvider) -> None:
    # The actual claim set observed for a beamline scientist.
    roles = provider._extract_roles(
        _realm([
            "offline_access",
            "staff:7.0.1.1",
            "uma_authorization",
            "default-roles-alsncd",
            "tiled:admin",
        ])
    )
    assert Role.STAFF in roles
    # "tiled:admin" must not have granted the NCS ADMIN role.
    assert Role.ADMIN not in roles


def test_unrelated_scoped_claim_is_ignored(provider: KeycloakAuthProvider) -> None:
    # No recognizable base role -> default USER, nothing else.
    roles = provider._extract_roles(_realm(["tiled:admin"]))
    assert roles == {Role.USER}


def test_org_prefixed_scoped_role(provider: KeycloakAuthProvider) -> None:
    roles = provider._extract_roles(_realm(["als-operator:7.0.1.1"]))
    assert Role.OPERATOR in roles


def test_no_roles_defaults_to_user(provider: KeycloakAuthProvider) -> None:
    roles = provider._extract_roles(_realm(["offline_access", "uma_authorization"]))
    assert roles == {Role.USER}


# --- Keycloak group-path claims -------------------------------------------

def _groups(groups: list[str]) -> dict:
    return {"groups": groups}


def test_all_beamlines_admin_group_maps_to_admin(provider: KeycloakAuthProvider) -> None:
    roles = provider._extract_roles(_groups(["/beamlines/_all/admin"]))
    assert Role.ADMIN in roles


@pytest.mark.parametrize(
    "path",
    ["beamlines/_all/admin", "/beamlines/_all/admin/", "/Beamlines//_ALL/Admin"],
)
def test_group_path_normalisation(provider: KeycloakAuthProvider, path: str) -> None:
    assert Role.ADMIN in provider._extract_roles(_groups([path]))


def test_admin_group_grants_admin_system(provider: KeycloakAuthProvider) -> None:
    roles = provider._extract_roles(_groups(["/beamlines/_all/admin"]))
    user = User(username="ronpandolfi", roles=roles)
    assert PolicyEngine().check_permission(user, Permission.ADMIN_SYSTEM)


@pytest.mark.parametrize(
    "path",
    [
        "/beamlines/7.0.1/maintainer",
        "/beamlines/7.0.1/admin",  # beamline-scoped admin is not global admin
        "/csm/scope/admin",
        "/admin",
        "/foo/beamlines/_all/admin",  # suffix match must not count
        "/beamlines/_all/admin/extra",
        "/BCG_staff",
        "/csm/scope/global",
        "/ncs-admin",  # path claims skip the name-based prefix rule
        "/x/staff:7.0.1.1",  # ...and the "<role>:<beamline>" rule
    ],
)
def test_other_group_paths_grant_nothing(provider: KeycloakAuthProvider, path: str) -> None:
    assert provider._extract_roles(_groups([path])) == {Role.USER}


def test_real_world_claims_with_groups(provider: KeycloakAuthProvider) -> None:
    claims = {
        "realm_access": {
            "roles": [
                "offline_access",
                "staff:7.0.1.1",
                "uma_authorization",
                "default-roles-alsncd",
                "tiled:admin",
            ]
        },
        "groups": [
            "/beamlines/_all/admin",
            "/beamlines/7.0.1/maintainer",
            "/BCG_staff",
            "/csm/scope/global",
        ],
    }
    assert provider._extract_roles(claims) == {Role.STAFF, Role.ADMIN}
    # Without the _all/admin group, the same token stays STAFF only.
    claims["groups"].remove("/beamlines/_all/admin")
    assert provider._extract_roles(claims) == {Role.STAFF}
