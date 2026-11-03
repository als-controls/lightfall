"""Saved Keycloak sign-in cookies behind the login dialog's "Continue as <name>".

The embedded login window runs on an in-memory browser profile, so Keycloak's
single-sign-on cookies vanish when Lightfall exits and every launch asks for a
password again. After a successful embedded login the provider saves those
cookies here, together with the name of the person they belong to. The next
login dialog then offers "Continue as <name>": choosing it puts the cookies
back into the login window, and Keycloak finishes the login without a password
for as long as its own SSO session is still alive (the realm's session
timeouts decide that, not Lightfall).

Resuming is always an explicit click; "Sign in as someone else" never loads
these cookies. Logout deletes the file. Beamline machines usually share one OS
account, so the cookies are only as private as that account's app-data folder;
the facility can switch the feature off with the ``keycloak_resume_session``
preference.

No tokens are stored: the file holds the browser cookies only, and Lightfall
still goes through the normal authorization-code flow on every launch.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from loguru import logger

STASH_FILENAME = "keycloak-sso.json"
RESUME_SETTING_KEY = "keycloak_resume_session"
DEFAULT_RESUME_ENABLED = True


@dataclass
class SsoStash:
    """Keycloak SSO cookies saved for one person on one realm.

    Attributes:
        issuer: ``<server_url>/realms/<realm>``; a stash from another realm or
            server is ignored.
        username: Keycloak ``preferred_username`` of the person who signed in.
        display_name: Name shown on the "Continue as" button.
        cookies: Raw ``Set-Cookie`` strings
            (``QNetworkCookie.toRawForm(Full)``) for the Keycloak host.
        saved_at: Unix time the stash was written.
    """

    issuer: str
    username: str
    display_name: str
    cookies: list[str]
    saved_at: float = field(default_factory=time.time)

    @property
    def label_name(self) -> str:
        """The name to show the user: display name, else username."""
        return self.display_name or self.username


def stash_path() -> Path:
    """Where the stash lives: the per-account Lightfall config directory."""
    from lightfall_utils.config.manager import _user_config_dir

    return _user_config_dir("ncs") / STASH_FILENAME


def resume_enabled() -> bool:
    """Whether "Continue as <name>" is switched on (preference, default on)."""
    try:
        from lightfall.ui.preferences.manager import PreferencesManager

        value = PreferencesManager.get_instance().get(
            RESUME_SETTING_KEY, DEFAULT_RESUME_ENABLED
        )
    except Exception as e:  # preferences unavailable (headless tools, tests)
        logger.debug("resume preference unavailable, using default: {}", e)
        return DEFAULT_RESUME_ENABLED
    return bool(value) if value is not None else DEFAULT_RESUME_ENABLED


def load_stash(issuer: str, path: Path | None = None) -> SsoStash | None:
    """Return the saved stash for ``issuer``, or None if absent or unusable."""
    path = path or stash_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        logger.warning("Ignoring unreadable Keycloak sign-in stash {}: {}", path, e)
        return None
    try:
        stash = SsoStash(
            issuer=str(raw["issuer"]),
            username=str(raw["username"]),
            display_name=str(raw.get("display_name", "")),
            cookies=[str(c) for c in raw["cookies"]],
            saved_at=float(raw.get("saved_at", 0.0)),
        )
    except (KeyError, TypeError, ValueError) as e:
        logger.warning("Ignoring malformed Keycloak sign-in stash {}: {}", path, e)
        return None
    if stash.issuer != issuer or not stash.cookies:
        return None
    return stash


def save_stash(stash: SsoStash, path: Path | None = None) -> None:
    """Write ``stash`` atomically, readable only by this account where possible."""
    path = path or stash_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
        # Owner-only from creation (POSIX); Windows ignores the mode bits.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(asdict(stash), f)
        os.replace(tmp, path)
        logger.info("Saved Keycloak sign-in for '{}' (Continue as)", stash.username)
    except OSError as e:
        logger.warning("Could not save Keycloak sign-in stash {}: {}", path, e)
        with contextlib.suppress(OSError):
            tmp.unlink()


def clear_stash(path: Path | None = None) -> None:
    """Delete the stash, if any (logout, or the feature switched off)."""
    path = path or stash_path()
    try:
        path.unlink()
        logger.info("Cleared saved Keycloak sign-in")
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning("Could not delete Keycloak sign-in stash {}: {}", path, e)
