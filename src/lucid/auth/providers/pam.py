"""PAM authentication provider for Linux system accounts.

This provider authenticates users against the system's PAM stack,
which works with any account backend (local, LDAP, SSSD, FreeIPA, etc.).
Unix group membership is mapped to LUCID roles.

Intended for deployments like NSLS-II where facility-wide Linux accounts
are synced across machines and Keycloak is not available.
"""

from __future__ import annotations

import grp
import pwd
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from lucid.auth.policy import Role
from lucid.auth.providers.base import AuthProvider
from lucid.auth.session import Session, User
from lucid.utils.logging import logger

# Default mapping from Unix groups to LUCID roles.
# Deployments can override this via PamConfig.group_role_map.
DEFAULT_GROUP_ROLE_MAP: dict[str, Role] = {
    "ncs-developer": Role.DEVELOPER,
    "ncs-admin": Role.ADMIN,
    "ncs-staff": Role.STAFF,
    "ncs-operator": Role.OPERATOR,
    "ncs-user": Role.USER,
}


@dataclass
class PamConfig:
    """Configuration for the PAM auth provider.

    Attributes:
        service: PAM service name (maps to /etc/pam.d/<service>).
        group_role_map: Unix group name → LUCID Role mapping.
            Checked in order; all matching groups contribute roles.
        default_role: Role assigned when no group matches.
        session_duration: How long sessions remain valid.
    """

    service: str = "login"
    group_role_map: dict[str, Role] = field(
        default_factory=lambda: dict(DEFAULT_GROUP_ROLE_MAP)
    )
    default_role: Role = Role.USER
    session_duration: timedelta = timedelta(hours=8)


def _pam_authenticate(service: str, username: str, password: str) -> bool:
    """Authenticate via PAM. Returns True on success."""
    try:
        import pam as pam_module

        p = pam_module.pam()
        return p.authenticate(username, password, service=service)
    except ImportError:
        logger.error(
            "python-pam is not installed — install it with: pip install python-pam"
        )
        return False
    except Exception as e:
        logger.error("PAM authentication error: {}", e)
        return False


def _get_user_groups(username: str) -> set[str]:
    """Get all Unix groups a user belongs to."""
    groups: set[str] = set()
    try:
        # Primary group
        pw = pwd.getpwnam(username)
        primary = grp.getgrgid(pw.pw_gid)
        groups.add(primary.gr_name)
    except KeyError:
        pass

    # Supplementary groups
    try:
        all_groups = grp.getgrall()
        for g in all_groups:
            if username in g.gr_mem:
                groups.add(g.gr_name)
    except Exception as e:
        logger.warning("Failed to enumerate groups for {}: {}", username, e)

    return groups


def _get_gecos_name(username: str) -> str:
    """Extract display name from GECOS field."""
    try:
        pw = pwd.getpwnam(username)
        gecos = pw.pw_gecos
        # GECOS format: "Full Name,Room,Work Phone,Home Phone,Other"
        if gecos:
            return gecos.split(",")[0] or username
    except KeyError:
        pass
    return username


class PamAuthProvider(AuthProvider):
    """
    Authentication provider using Linux PAM.

    Authenticates against the system PAM stack and maps Unix group
    membership to LUCID roles. Works with any PAM backend (local
    accounts, LDAP, SSSD, FreeIPA, Kerberos, etc.).

    Requires the ``python-pam`` package and must run on a system
    where the target users have accounts.

    Example:
        >>> config = PamConfig(
        ...     service="login",
        ...     group_role_map={"beamline-staff": Role.STAFF},
        ... )
        >>> provider = PamAuthProvider(config)
        >>> session = await provider.authenticate(username="jdoe", password="secret")
    """

    def __init__(self, config: PamConfig | None = None) -> None:
        self._config = config or PamConfig()
        self._sessions: dict[str, Session] = {}

    @property
    def name(self) -> str:
        return "PAM System Auth"

    @property
    def supports_password_auth(self) -> bool:
        return True

    @property
    def supports_browser_auth(self) -> bool:
        return False

    def _resolve_roles(self, unix_groups: set[str]) -> set[Role]:
        """Map Unix groups to LUCID roles."""
        roles: set[Role] = set()
        for group_name, role in self._config.group_role_map.items():
            if group_name in unix_groups:
                roles.add(role)

        if not roles:
            roles.add(self._config.default_role)

        return roles

    async def authenticate(
        self,
        username: str | None = None,
        password: str | None = None,
        **kwargs: Any,
    ) -> Session | None:
        """Authenticate a user via PAM."""
        if not username or not password:
            logger.warning("PAM auth: missing username or password")
            return None

        if not _pam_authenticate(self._config.service, username, password):
            logger.warning("PAM auth failed for user: {}", username)
            return None

        # Gather system info
        unix_groups = _get_user_groups(username)
        roles = self._resolve_roles(unix_groups)
        display_name = _get_gecos_name(username)

        now = datetime.now(UTC)
        user = User(
            username=username,
            display_name=display_name,
            roles=roles,
            groups=unix_groups,
            authenticated_at=now,
            expires_at=now + self._config.session_duration,
        )

        token = secrets.token_urlsafe(32)
        refresh_token = secrets.token_urlsafe(32)

        session = Session(
            user=user,
            token=token,
            refresh_token=refresh_token,
        )

        self._sessions[token] = session
        logger.info(
            "User '{}' authenticated via PAM (groups={}, roles={})",
            username,
            unix_groups,
            {r.value for r in roles},
        )
        return session

    async def logout(self, session: Session) -> None:
        """End a session."""
        if session.token and session.token in self._sessions:
            del self._sessions[session.token]
            logger.debug("PAM session ended for user: {}", session.user.username)

    async def refresh(self, session: Session) -> Session | None:
        """Refresh a session's expiry time."""
        if not session.refresh_token or session.token not in self._sessions:
            return None

        now = datetime.now(UTC)
        new_token = secrets.token_urlsafe(32)
        new_refresh = secrets.token_urlsafe(32)

        user = User(
            username=session.user.username,
            display_name=session.user.display_name,
            email=session.user.email,
            roles=session.user.roles,
            groups=session.user.groups,
            attributes=session.user.attributes,
            authenticated_at=session.user.authenticated_at,
            expires_at=now + self._config.session_duration,
        )

        new_session = Session(
            user=user,
            token=new_token,
            refresh_token=new_refresh,
            created_at=session.created_at,
        )

        del self._sessions[session.token]
        self._sessions[new_token] = new_session
        return new_session

    async def check_connectivity(self) -> bool:
        """PAM is always available if we're on the right host."""
        try:
            import pam as pam_module  # noqa: F401

            return True
        except ImportError:
            return False

    async def get_user_info(self, session: Session) -> dict[str, Any] | None:
        """Get user info from system databases."""
        username = session.user.username
        try:
            pw = pwd.getpwnam(username)
            return {
                "username": username,
                "display_name": session.user.display_name,
                "uid": pw.pw_uid,
                "gid": pw.pw_gid,
                "home": pw.pw_dir,
                "shell": pw.pw_shell,
                "groups": sorted(session.user.groups),
                "roles": [r.value for r in session.user.roles],
            }
        except KeyError:
            return None

    async def validate_token(self, token: str) -> bool:
        """Check if a token is valid."""
        session = self._sessions.get(token)
        if not session:
            return False
        return session.is_valid
