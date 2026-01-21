"""Base panel class for NCS panels.

Provides the foundation for all NCS panels with:
- Introspection API for Claude MCP tools
- Permission-based access control
- Theme awareness
- Standard lifecycle management
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ncs.auth.policy import Permission
from ncs.utils.logging import logger

if TYPE_CHECKING:
    from ncs.auth.session import User


@dataclass
class PanelMetadata:
    """Metadata describing a panel type.

    This metadata is used by:
    - PanelRegistry for discovery and instantiation
    - Claude MCP tools for introspection
    - UI for displaying panel information

    Attributes:
        id: Unique panel identifier (e.g., "ncs.panels.device").
        name: Human-readable panel name.
        description: Detailed description of the panel's purpose.
        icon: Icon name or path.
        category: Panel category for grouping (e.g., "Device", "Data", "Admin").
        required_permission: Permission needed to access this panel.
        singleton: Whether only one instance can exist.
        closable: Whether the panel can be closed by the user.
        keywords: Search keywords for finding this panel.
    """

    id: str
    name: str
    description: str = ""
    icon: str = ""
    category: str = "General"
    required_permission: Permission | None = None
    singleton: bool = True
    closable: bool = True
    keywords: list[str] = field(default_factory=list)

    def matches_search(self, query: str) -> bool:
        """Check if panel matches a search query.

        Args:
            query: Search string.

        Returns:
            True if panel matches.
        """
        query_lower = query.lower()
        searchable = [
            self.id.lower(),
            self.name.lower(),
            self.description.lower(),
            self.category.lower(),
        ] + [kw.lower() for kw in self.keywords]

        return any(query_lower in item for item in searchable)


class BasePanel(QWidget):
    """
    Base class for all NCS panels.

    BasePanel provides:
    - Introspection API for Claude MCP tools to understand panel structure
    - Permission checking for access control
    - Theme-aware styling hooks
    - Standard panel lifecycle (activate, deactivate, close)
    - Signal emission for state changes

    Subclasses should:
    1. Define class-level `panel_metadata` with PanelMetadata
    2. Override `_setup_ui()` to build the panel UI
    3. Override introspection methods to provide MCP tool support

    Class Attributes:
        panel_metadata: PanelMetadata describing this panel type.

    Signals:
        activated: Emitted when panel becomes active/focused.
        deactivated: Emitted when panel loses focus.
        state_changed: Emitted when panel state changes.
        closing: Emitted when panel is about to close.

    Example:
        >>> class MyPanel(BasePanel):
        ...     panel_metadata = PanelMetadata(
        ...         id="ncs.panels.my_panel",
        ...         name="My Panel",
        ...         description="Does something useful",
        ...     )
        ...
        ...     def _setup_ui(self):
        ...         label = QLabel("Hello!")
        ...         self.layout().addWidget(label)
    """

    # Class-level metadata - must be defined in subclasses
    panel_metadata: ClassVar[PanelMetadata] = PanelMetadata(
        id="ncs.panels.base",
        name="Base Panel",
        description="Abstract base panel",
    )

    # Signals
    activated = Signal()
    deactivated = Signal()
    state_changed = Signal(str, object)  # key, value
    closing = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the base panel.

        Args:
            parent: Parent widget.
        """
        super().__init__(parent)
        self._is_active = False
        self._panel_state: dict[str, Any] = {}

        # Set object name from metadata
        self.setObjectName(self.panel_metadata.id)

        # Setup layout
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

        # Allow subclasses to setup UI
        self._setup_ui()

        logger.debug("Created panel: {}", self.panel_metadata.id)

    def _setup_ui(self) -> None:
        """Setup the panel's user interface.

        Override in subclasses to build the panel UI.
        This is called during __init__ after the layout is created.
        """
        pass

    # Lifecycle methods

    def activate(self) -> None:
        """Called when the panel becomes the active/focused panel."""
        if not self._is_active:
            self._is_active = True
            self.activated.emit()
            self._on_activated()
            logger.debug("Panel activated: {}", self.panel_metadata.id)

    def deactivate(self) -> None:
        """Called when the panel loses focus."""
        if self._is_active:
            self._is_active = False
            self.deactivated.emit()
            self._on_deactivated()
            logger.debug("Panel deactivated: {}", self.panel_metadata.id)

    def _on_activated(self) -> None:
        """Hook for subclasses when panel is activated."""
        pass

    def _on_deactivated(self) -> None:
        """Hook for subclasses when panel is deactivated."""
        pass

    @property
    def is_active(self) -> bool:
        """Whether this panel is currently active."""
        return self._is_active

    def can_close(self) -> bool:
        """Check if the panel can be closed.

        Override to implement confirmation dialogs or prevent
        closing when work is in progress.

        Returns:
            True if panel can close.
        """
        return self.panel_metadata.closable

    def closeEvent(self, event) -> None:
        """Handle close event."""
        if self.can_close():
            self.closing.emit()
            self._on_closing()
            event.accept()
            logger.debug("Panel closing: {}", self.panel_metadata.id)
        else:
            event.ignore()

    def _on_closing(self) -> None:
        """Hook for subclasses when panel is closing."""
        pass

    # State management

    def get_state(self, key: str, default: Any = None) -> Any:
        """Get a panel state value.

        Args:
            key: State key.
            default: Default value if not set.

        Returns:
            The state value.
        """
        return self._panel_state.get(key, default)

    def set_state(self, key: str, value: Any) -> None:
        """Set a panel state value.

        Args:
            key: State key.
            value: State value.
        """
        old_value = self._panel_state.get(key)
        self._panel_state[key] = value
        if old_value != value:
            self.state_changed.emit(key, value)

    def get_all_state(self) -> dict[str, Any]:
        """Get all panel state as a dictionary."""
        return dict(self._panel_state)

    def restore_state(self, state: dict[str, Any]) -> None:
        """Restore panel state from a dictionary.

        Args:
            state: State dictionary to restore.
        """
        for key, value in state.items():
            self.set_state(key, value)

    # Permission checking

    @classmethod
    def check_access(cls, user: User) -> bool:
        """Check if a user can access this panel type.

        Args:
            user: The user to check.

        Returns:
            True if user has access.
        """
        if cls.panel_metadata.required_permission is None:
            return True

        from ncs.auth.session import SessionManager

        manager = SessionManager.get_instance()
        return manager.policy_engine.check_permission(
            user, cls.panel_metadata.required_permission
        )

    # Introspection API for Claude MCP tools

    def get_introspection_data(self) -> dict[str, Any]:
        """Get comprehensive introspection data for Claude MCP tools.

        This method provides all information Claude needs to understand
        and interact with this panel.

        Returns:
            Dictionary with panel information including:
            - metadata: Panel type metadata
            - state: Current panel state
            - widgets: Information about child widgets
            - actions: Available actions
        """
        return {
            # Panel metadata
            "metadata": {
                "id": self.panel_metadata.id,
                "name": self.panel_metadata.name,
                "description": self.panel_metadata.description,
                "category": self.panel_metadata.category,
                "singleton": self.panel_metadata.singleton,
            },
            # Current state
            "is_active": self._is_active,
            "is_visible": self.isVisible(),
            "is_enabled": self.isEnabled(),
            "state": self._panel_state,
            # Geometry
            "geometry": {
                "x": self.x(),
                "y": self.y(),
                "width": self.width(),
                "height": self.height(),
            },
            # Widget tree
            "widgets": self._introspect_widgets(),
            # Available actions
            "actions": self._get_available_actions(),
            # Panel-specific data from subclass
            **self._get_specific_introspection_data(),
        }

    def _introspect_widgets(self) -> list[dict[str, Any]]:
        """Introspect child widgets for MCP tools.

        Returns:
            List of widget information dictionaries.
        """
        widgets = []

        def collect_widgets(parent: QWidget, depth: int = 0) -> None:
            for child in parent.children():
                if isinstance(child, QWidget):
                    widget_info = {
                        "class": child.__class__.__name__,
                        "object_name": child.objectName(),
                        "visible": child.isVisible(),
                        "enabled": child.isEnabled(),
                        "depth": depth,
                    }

                    # Add text if available
                    if hasattr(child, "text") and callable(child.text):
                        try:
                            widget_info["text"] = child.text()
                        except Exception:
                            pass

                    # Add value if available
                    if hasattr(child, "value") and callable(child.value):
                        try:
                            widget_info["value"] = child.value()
                        except Exception:
                            pass

                    widgets.append(widget_info)

                    # Recursively collect from children (limit depth)
                    if depth < 3:
                        collect_widgets(child, depth + 1)

        collect_widgets(self)
        return widgets

    def _get_available_actions(self) -> list[dict[str, Any]]:
        """Get available actions for this panel.

        Override in subclasses to expose panel-specific actions.

        Returns:
            List of action descriptions.
        """
        return [
            {
                "name": "activate",
                "description": "Make this panel active",
                "method": "activate",
            },
            {
                "name": "close",
                "description": "Close this panel",
                "method": "close",
                "enabled": self.panel_metadata.closable,
            },
        ]

    def _get_specific_introspection_data(self) -> dict[str, Any]:
        """Get panel-specific introspection data.

        Override in subclasses to add additional data.

        Returns:
            Dictionary with panel-specific data.
        """
        return {}

    @classmethod
    def get_class_introspection_data(cls) -> dict[str, Any]:
        """Get class-level introspection data for panel discovery.

        Returns:
            Dictionary with panel class information.
        """
        return {
            "id": cls.panel_metadata.id,
            "name": cls.panel_metadata.name,
            "description": cls.panel_metadata.description,
            "category": cls.panel_metadata.category,
            "icon": cls.panel_metadata.icon,
            "singleton": cls.panel_metadata.singleton,
            "required_permission": (
                cls.panel_metadata.required_permission.name
                if cls.panel_metadata.required_permission
                else None
            ),
            "keywords": cls.panel_metadata.keywords,
            "class_name": cls.__name__,
            "module": cls.__module__,
        }

    # Actions that can be invoked by MCP tools

    def invoke_action(self, action_name: str, **kwargs: Any) -> Any:
        """Invoke an action by name.

        This allows Claude MCP tools to invoke panel actions by name.

        Args:
            action_name: Name of the action to invoke.
            **kwargs: Action arguments.

        Returns:
            Action result.

        Raises:
            ValueError: If action not found.
        """
        # Built-in actions
        if action_name == "activate":
            self.activate()
            return True
        elif action_name == "close":
            self.close()
            return True
        elif action_name == "set_state":
            key = kwargs.get("key")
            value = kwargs.get("value")
            if key:
                self.set_state(key, value)
                return True
            return False

        # Check for custom action handler
        handler = getattr(self, f"action_{action_name}", None)
        if handler and callable(handler):
            return handler(**kwargs)

        raise ValueError(f"Unknown action: {action_name}")
