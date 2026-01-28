"""Authentication providers for NCS.

This package contains authentication provider implementations:
- LocalAuthProvider: Development/testing provider with local user database
- KeycloakAuthProvider: Production OIDC provider using Keycloak
"""

from lucid.auth.providers.base import AuthProvider
from lucid.auth.providers.local import LocalAuthProvider

__all__ = [
    "AuthProvider",
    "LocalAuthProvider",
]

# Keycloak provider available if dependencies are installed
try:
    from lucid.auth.providers.keycloak import KeycloakAuthProvider

    __all__.append("KeycloakAuthProvider")
except ImportError:
    pass
