"""Authorization policy engine with hybrid RBAC/ABAC support.

This module implements a flexible authorization system that combines:
- Role-Based Access Control (RBAC): Users have roles with predefined permissions
- Attribute-Based Access Control (ABAC): Permissions can have conditions based on
  attributes like beam time, resource ownership, location, etc.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any

from lucid.utils.logging import logger

if TYPE_CHECKING:
    from collections.abc import Callable

    from lucid.auth.session import User


class Permission(Enum):
    """Standard NCS permissions.

    Permissions are organized by subsystem:
    - DEVICE_*: Device management operations
    - SCAN_*: Data acquisition/scan operations
    - DATA_*: Data access and management
    - CONFIG_*: Configuration changes
    - ADMIN_*: Administrative operations
    - PANEL_*: UI panel access
    """

    # Device permissions
    DEVICE_VIEW = auto()
    DEVICE_CONTROL = auto()
    DEVICE_CONFIGURE = auto()
    DEVICE_ADMIN = auto()

    # Scan/acquisition permissions
    SCAN_VIEW = auto()
    SCAN_RUN = auto()
    SCAN_ABORT = auto()
    SCAN_CONFIGURE = auto()

    # Data permissions
    DATA_VIEW = auto()
    DATA_EXPORT = auto()
    DATA_DELETE = auto()
    DATA_ADMIN = auto()

    # Configuration permissions
    CONFIG_VIEW = auto()
    CONFIG_USER = auto()  # Modify user-level config
    CONFIG_BEAMLINE = auto()  # Modify beamline config
    CONFIG_GLOBAL = auto()  # Modify global config

    # Administrative permissions
    ADMIN_USERS = auto()
    ADMIN_SYSTEM = auto()
    ADMIN_AUDIT = auto()

    # Panel permissions
    PANEL_VIEW_BASIC = auto()
    PANEL_VIEW_EXPERT = auto()
    PANEL_VIEW_ADMIN = auto()

    # Script/automation permissions
    SCRIPT_RUN = auto()
    SCRIPT_ADMIN = auto()

    # Logbook permissions
    LOGBOOK_VIEW = auto()
    LOGBOOK_EDIT = auto()
    LOGBOOK_ADMIN = auto()


class Role(Enum):
    """Standard NCS roles with hierarchical permissions.

    Roles are ordered from least to most privileged.
    Higher roles inherit permissions from lower roles.
    """

    GUEST = "guest"
    USER = "user"
    OPERATOR = "operator"
    BEAMLINE_SCIENTIST = "beamline_scientist"
    STAFF = "staff"
    ADMIN = "admin"
    DEVELOPER = "developer"


# Role hierarchy: each role inherits from roles listed
ROLE_HIERARCHY: dict[Role, list[Role]] = {
    Role.GUEST: [],
    Role.USER: [Role.GUEST],
    Role.OPERATOR: [Role.USER],
    Role.BEAMLINE_SCIENTIST: [Role.OPERATOR],
    Role.STAFF: [Role.BEAMLINE_SCIENTIST],
    Role.ADMIN: [Role.STAFF],
    Role.DEVELOPER: [Role.ADMIN],
}

# Default permissions for each role (before inheritance)
DEFAULT_ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.GUEST: {
        Permission.DEVICE_VIEW,
        Permission.SCAN_VIEW,
        Permission.DATA_VIEW,
        Permission.CONFIG_VIEW,
        Permission.PANEL_VIEW_BASIC,
        Permission.LOGBOOK_VIEW,
    },
    Role.USER: {
        Permission.DEVICE_CONTROL,
        Permission.SCAN_RUN,
        Permission.SCAN_ABORT,
        Permission.DATA_EXPORT,
        Permission.CONFIG_USER,
        Permission.SCRIPT_RUN,
        Permission.LOGBOOK_EDIT,
    },
    Role.OPERATOR: {
        Permission.SCAN_CONFIGURE,
        Permission.PANEL_VIEW_EXPERT,
    },
    Role.BEAMLINE_SCIENTIST: {
        Permission.DEVICE_CONFIGURE,
        Permission.CONFIG_BEAMLINE,
        Permission.DATA_DELETE,
        Permission.SCRIPT_ADMIN,
        Permission.LOGBOOK_ADMIN,
    },
    Role.STAFF: {
        Permission.PANEL_VIEW_ADMIN,
        Permission.ADMIN_AUDIT,
    },
    Role.ADMIN: {
        Permission.DEVICE_ADMIN,
        Permission.DATA_ADMIN,
        Permission.CONFIG_GLOBAL,
        Permission.ADMIN_USERS,
        Permission.ADMIN_SYSTEM,
    },
    Role.DEVELOPER: set(),  # Inherits all from ADMIN
}


@dataclass
class PolicyCondition:
    """A condition that must be met for a permission to be granted.

    Conditions enable ABAC by checking runtime attributes.

    Attributes:
        name: Human-readable condition name.
        description: Description of what this condition checks.
        check: Callable that returns True if condition is met.
            Receives (user, context) and returns bool.
    """

    name: str
    description: str
    check: Callable[[User, dict[str, Any]], bool]


@dataclass
class PolicyRule:
    """A policy rule that grants or denies a permission.

    Attributes:
        permission: The permission this rule applies to.
        effect: "allow" or "deny".
        roles: Roles this rule applies to (empty = all roles).
        conditions: Conditions that must all be met.
        priority: Higher priority rules are evaluated first.
    """

    permission: Permission
    effect: str = "allow"
    roles: set[Role] = field(default_factory=set)
    conditions: list[PolicyCondition] = field(default_factory=list)
    priority: int = 0

    def matches_role(self, role: Role) -> bool:
        """Check if this rule applies to a role."""
        if not self.roles:
            return True
        return role in self.roles

    def evaluate_conditions(self, user: User, context: dict[str, Any]) -> bool:
        """Evaluate all conditions for this rule."""
        return all(cond.check(user, context) for cond in self.conditions)


class PolicyEngine:
    """
    Authorization engine implementing hybrid RBAC/ABAC.

    The PolicyEngine evaluates permission requests against:
    1. Role-based permissions (RBAC)
    2. Custom policy rules with conditions (ABAC)
    3. Role hierarchy inheritance

    Example:
        >>> engine = PolicyEngine()
        >>> user = User(username="jdoe", roles={Role.OPERATOR})
        >>> engine.check_permission(user, Permission.DEVICE_CONTROL)
        True
        >>> engine.check_permission(user, Permission.ADMIN_SYSTEM)
        False
    """

    def __init__(self) -> None:
        """Initialize the policy engine with default role permissions."""
        self._role_permissions: dict[Role, set[Permission]] = {}
        self._custom_rules: list[PolicyRule] = []
        self._computed_permissions: dict[Role, set[Permission]] = {}

        # Initialize with default permissions
        self._initialize_default_permissions()

    def _initialize_default_permissions(self) -> None:
        """Set up default role-based permissions with inheritance."""
        # Copy default permissions
        for role, perms in DEFAULT_ROLE_PERMISSIONS.items():
            self._role_permissions[role] = perms.copy()

        # Compute inherited permissions
        self._recompute_inherited_permissions()

    def _recompute_inherited_permissions(self) -> None:
        """Recompute role permissions including inheritance."""
        self._computed_permissions.clear()

        for role in Role:
            all_perms: set[Permission] = set()
            self._collect_inherited_permissions(role, all_perms)
            self._computed_permissions[role] = all_perms

    def _collect_inherited_permissions(
        self, role: Role, collected: set[Permission]
    ) -> None:
        """Recursively collect permissions from role and its parents."""
        # Add direct permissions
        if role in self._role_permissions:
            collected.update(self._role_permissions[role])

        # Add inherited permissions
        for parent_role in ROLE_HIERARCHY.get(role, []):
            self._collect_inherited_permissions(parent_role, collected)

    def get_role_permissions(self, role: Role) -> set[Permission]:
        """Get all permissions for a role including inherited ones.

        Args:
            role: The role to get permissions for.

        Returns:
            Set of all permissions for the role.
        """
        return self._computed_permissions.get(role, set()).copy()

    def get_user_permissions(self, user: User) -> set[Permission]:
        """Get all permissions for a user based on their roles.

        Args:
            user: The user to get permissions for.

        Returns:
            Set of all permissions the user has via their roles.
        """
        all_perms: set[Permission] = set()
        for role in user.roles:
            all_perms.update(self.get_role_permissions(role))
        return all_perms

    def add_rule(self, rule: PolicyRule) -> None:
        """Add a custom policy rule.

        Args:
            rule: The rule to add.
        """
        self._custom_rules.append(rule)
        # Sort by priority (higher first)
        self._custom_rules.sort(key=lambda r: r.priority, reverse=True)
        logger.debug("Added policy rule for {} (effect={})", rule.permission, rule.effect)

    def remove_rule(self, permission: Permission, effect: str = "allow") -> bool:
        """Remove a custom rule by permission and effect.

        Args:
            permission: The permission the rule applies to.
            effect: The rule effect ("allow" or "deny").

        Returns:
            True if a rule was removed.
        """
        original_count = len(self._custom_rules)
        self._custom_rules = [
            r for r in self._custom_rules
            if not (r.permission == permission and r.effect == effect)
        ]
        return len(self._custom_rules) < original_count

    def grant_role_permission(self, role: Role, permission: Permission) -> None:
        """Grant an additional permission to a role.

        Args:
            role: The role to grant permission to.
            permission: The permission to grant.
        """
        if role not in self._role_permissions:
            self._role_permissions[role] = set()
        self._role_permissions[role].add(permission)
        self._recompute_inherited_permissions()
        logger.debug("Granted {} to role {}", permission, role)

    def revoke_role_permission(self, role: Role, permission: Permission) -> None:
        """Revoke a permission from a role.

        Args:
            role: The role to revoke permission from.
            permission: The permission to revoke.
        """
        if role in self._role_permissions:
            self._role_permissions[role].discard(permission)
            self._recompute_inherited_permissions()
            logger.debug("Revoked {} from role {}", permission, role)

    def check_permission(
        self,
        user: User,
        permission: Permission,
        context: dict[str, Any] | None = None,
    ) -> bool:
        """Check if a user has a specific permission.

        This evaluates:
        1. Custom deny rules (ABAC)
        2. Custom allow rules (ABAC)
        3. Role-based permissions (RBAC)

        Args:
            user: The user requesting permission.
            permission: The permission being requested.
            context: Optional context for ABAC conditions.

        Returns:
            True if the user has the permission.
        """
        context = context or {}

        # Check custom rules first (higher priority)
        for rule in self._custom_rules:
            if rule.permission != permission:
                continue

            # Check if rule applies to any of user's roles
            applies_to_user = any(rule.matches_role(role) for role in user.roles)
            if not applies_to_user and rule.roles:
                continue

            # Evaluate conditions
            if not rule.evaluate_conditions(user, context):
                continue

            # Rule matches - apply effect
            if rule.effect == "deny":
                logger.debug(
                    "Permission {} denied for {} by rule",
                    permission,
                    user.username,
                )
                return False
            elif rule.effect == "allow":
                logger.debug(
                    "Permission {} allowed for {} by rule",
                    permission,
                    user.username,
                )
                return True

        # Fall back to role-based permissions
        user_perms = self.get_user_permissions(user)
        has_perm = permission in user_perms

        if has_perm:
            logger.debug(
                "Permission {} allowed for {} by role",
                permission,
                user.username,
            )
        else:
            logger.debug(
                "Permission {} denied for {} (not in roles)",
                permission,
                user.username,
            )

        return has_perm

    def check_any_permission(
        self,
        user: User,
        permissions: set[Permission],
        context: dict[str, Any] | None = None,
    ) -> bool:
        """Check if user has any of the specified permissions.

        Args:
            user: The user requesting permission.
            permissions: Set of permissions to check.
            context: Optional context for ABAC conditions.

        Returns:
            True if user has at least one permission.
        """
        return any(
            self.check_permission(user, perm, context)
            for perm in permissions
        )

    def check_all_permissions(
        self,
        user: User,
        permissions: set[Permission],
        context: dict[str, Any] | None = None,
    ) -> bool:
        """Check if user has all of the specified permissions.

        Args:
            user: The user requesting permission.
            permissions: Set of permissions to check.
            context: Optional context for ABAC conditions.

        Returns:
            True if user has all permissions.
        """
        return all(
            self.check_permission(user, perm, context)
            for perm in permissions
        )

    def require_permission(
        self,
        user: User,
        permission: Permission,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Require a permission, raising an exception if not granted.

        Args:
            user: The user requesting permission.
            permission: The permission required.
            context: Optional context for ABAC conditions.

        Raises:
            PermissionError: If the user lacks the permission.
        """
        if not self.check_permission(user, permission, context):
            raise PermissionError(
                f"User '{user.username}' lacks permission: {permission.name}"
            )


# Common ABAC condition factories


def during_beam_time(user: User, context: dict[str, Any]) -> bool:
    """Check if user is operating during their allocated beam time.

    Expects context to contain:
    - beam_time_active: bool indicating if beam time is active
    - beam_time_users: list of usernames with access
    """
    if not context.get("beam_time_active", False):
        return False
    allowed_users = context.get("beam_time_users", [])
    return user.username in allowed_users


def owns_resource(user: User, context: dict[str, Any]) -> bool:
    """Check if user owns the resource being accessed.

    Expects context to contain:
    - resource_owner: username of the resource owner
    """
    return context.get("resource_owner") == user.username


def is_local_network(user: User, context: dict[str, Any]) -> bool:
    """Check if access is from the local facility network.

    Expects context to contain:
    - is_local: bool indicating local network access
    """
    return context.get("is_local", False)


# Pre-built condition objects
CONDITION_BEAM_TIME = PolicyCondition(
    name="during_beam_time",
    description="Access only during allocated beam time",
    check=during_beam_time,
)

CONDITION_OWNS_RESOURCE = PolicyCondition(
    name="owns_resource",
    description="User must own the resource",
    check=owns_resource,
)

CONDITION_LOCAL_NETWORK = PolicyCondition(
    name="is_local_network",
    description="Access from local facility network only",
    check=is_local_network,
)
