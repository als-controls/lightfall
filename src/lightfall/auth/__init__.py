"""Authentication and authorization for NCS.

This package provides:
- Role-based access control (RBAC)
- Attribute-based access control (ABAC)
- Session management
- Authentication provider abstraction
"""

from lightfall.auth.service_key import (
    MintedKey,
    mint_service_key,
    revoke_service_key,
)
from lightfall.auth.policy import (
    Permission,
    PolicyEngine,
    Role,
)
from lightfall.auth.session import (
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
