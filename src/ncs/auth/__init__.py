"""Authentication and authorization for NCS.

This package provides:
- Role-based access control (RBAC)
- Attribute-based access control (ABAC)
- Session management
- Authentication provider abstraction
"""

from ncs.auth.policy import (
    Permission,
    PolicyEngine,
    Role,
)
from ncs.auth.session import (
    AuthState,
    Session,
    SessionManager,
    User,
)

__all__ = [
    "AuthState",
    "Permission",
    "PolicyEngine",
    "Role",
    "Session",
    "SessionManager",
    "User",
]
