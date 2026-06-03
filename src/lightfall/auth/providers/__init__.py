"""Authentication providers for NCS.

This package contains authentication provider implementations:
- LocalAuthProvider: Development/testing provider with local user database
- KeycloakAuthProvider: Production OIDC provider using Keycloak
"""

from lightfall.auth.providers.base import AuthProvider
from lightfall.auth.providers.local import LocalAuthProvider

__all__ = [
    "AuthProvider",
    "LocalAuthProvider",
]

# Keycloak provider available if dependencies are installed
try:
    from lightfall.auth.providers.keycloak import KeycloakAuthProvider  # noqa: F401

    __all__.append("KeycloakAuthProvider")
except ImportError:
    pass

# PAM provider available on Linux with python-pam installed
try:
    from lightfall.auth.providers.pam import PamAuthProvider  # noqa: F401

    __all__.append("PamAuthProvider")
except ImportError:
    pass
