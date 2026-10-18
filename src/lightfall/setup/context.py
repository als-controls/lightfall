"""What the setup steps share: the chosen facility, the fetched bootstrap, service handles."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lightfall.auth.session import SessionManager
    from lightfall.config.manager import ConfigManager
    from lightfall.setup.bootstrap import Bootstrap
    from lightfall.setup.facilities import Facility
    from lightfall.ui.preferences.manager import PreferencesManager


@dataclass
class SetupContext:
    config: ConfigManager
    session: SessionManager
    prefs: PreferencesManager
    facility: Facility | None = None
    bootstrap: Bootstrap | None = None
    via_proxy: bool = False
    # The Session this wizard run's sign-in created (SignInPage, LoginResult.AUTHENTICATED).
    signed_in_session: Any = None

    @classmethod
    def from_services(cls) -> SetupContext:
        from lightfall.auth.session import SessionManager
        from lightfall.config import ConfigManager
        from lightfall.core import LFApplication
        from lightfall.ui.preferences.manager import PreferencesManager

        return cls(
            config=LFApplication.get_instance().services.get(ConfigManager),
            session=SessionManager.get_instance(),
            prefs=PreferencesManager.get_instance(),
        )

    def _stored(self, key: str) -> str:
        value: Any = self.config.get(f"setup.facility.{key}")
        return value if isinstance(value, str) else ""

    def server_url(self) -> str | None:
        """The facility server: the current choice, else the one bootstrap.yaml came from."""
        if self.facility is not None:
            return self.facility.server_url
        if self.bootstrap is not None:
            return self.bootstrap.server_url
        return self._stored("server_url") or None

    def facility_name(self) -> str:
        if self.facility is not None:
            return self.facility.name
        if self.bootstrap is not None and self.bootstrap.facility_name:
            return self.bootstrap.facility_name
        return self._stored("name")

    def facility_label(self) -> str:
        """Short name for buttons and headings ("Sign in with ALS")."""
        if self.facility is not None:
            return self.facility.label
        return self.facility_name() or "your facility"
