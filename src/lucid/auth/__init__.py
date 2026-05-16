"""Authentication and authorization for NCS.

This package provides:
- Role-based access control (RBAC)
- Attribute-based access control (ABAC)
- Session management
- Authentication provider abstraction
"""

from lucid.auth.job_key import (
    MintedJobKey,
    mint_job_key,
    revoke_job_key,
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
    "MintedJobKey",
    "Permission",
    "PolicyEngine",
    "Role",
    "Session",
    "SessionManager",
    "User",
    "mint_job_key",
    "revoke_job_key",
]
