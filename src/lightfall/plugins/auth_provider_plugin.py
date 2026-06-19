"""Auth-provider plugin type.

Lets any package (including core) contribute an authentication provider that
the login dialog renders as a login option. Mirrors EnginePlugin.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar

from lightfall.plugins.types import PluginType

if TYPE_CHECKING:
    from lightfall.auth.providers.base import AuthProvider


class AuthProviderPlugin(PluginType):
    """Abstract base for authentication-provider plugins."""

    type_name: ClassVar[str] = "auth_provider"
    is_singleton: ClassVar[bool] = True

    @property
    def description(self) -> str:
        return "Authentication provider plugin"

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique provider identifier."""
        ...

    @property
    def display_name(self) -> str:
        """Human-readable name for the login button."""
        return self.name.replace("_", " ").title()

    @property
    def button_label(self) -> str:
        """Full text shown on the provider's login button."""
        return f"Login with {self.display_name}"

    @property
    def accent_color(self) -> str:
        """Hex accent color for the provider's login button.

        Hover/pressed shades are derived from this automatically.
        """
        return "#4a5568"

    @property
    def requires_username(self) -> bool:
        """Whether the dialog should collect a username before authenticating."""
        return True

    @property
    def requires_password(self) -> bool:
        """Whether the dialog should collect a password before authenticating."""
        return False

    @property
    def priority(self) -> int:
        """Sort order in the login dialog (lower first)."""
        return 100

    @abstractmethod
    def create_provider(self) -> AuthProvider:
        """Create and return the AuthProvider instance to authenticate with."""
        ...

    def get_introspection_data(self) -> dict[str, Any]:
        return {
            "type": self.type_name,
            "name": self.name,
            "display_name": self.display_name,
            "button_label": self.button_label,
            "accent_color": self.accent_color,
            "requires_username": self.requires_username,
            "requires_password": self.requires_password,
            "priority": self.priority,
            "class": self.__class__.__name__,
            "module": self.__class__.__module__,
        }
