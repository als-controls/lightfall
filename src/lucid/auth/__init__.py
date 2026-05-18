"""Authentication and authorization for NCS.

This package provides:
- Role-based access control (RBAC)
- Attribute-based access control (ABAC)
- Session management
- Authentication provider abstraction
"""

from lucid.auth.service_key import (
    MintedKey,
    mint_service_key,
    revoke_service_key,
)
from lucid.auth.policy import (
    Permission,
    PolicyEngine,
    Role,
)
from lucid.auth.session import (
    AuthState,
    Session,
    SessionManager,
    User,
)

__all__ = [
    "AuthState",
    "MintedKey",
    "Permission",
    "PolicyEngine",
    "Role",
    "Session",
    "SessionManager",
    "User",
    "mint_service_key",
    "revoke_service_key",
]
