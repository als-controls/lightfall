"""Base panel class for NCS panels.

Provides the foundation for all NCS panels with:
- Introspection API for Claude MCP tools
- Permission-based access control
- Theme awareness
- Standard lifecycle management
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget

from lightfall.auth.policy import Permission
from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from lightfall.auth.session import User


class PanelStatus(Enum):
    """Lifecycle/health status of a panel, shown as a sidebar icon tint.

    UNINITIALIZED renders in the theme's text color (the pre-existing
    default); the other values use the matching theme color role.
    """

    UNINITIALIZED = "uninitialized"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    INFO = "info"


@dataclass
class PanelMetadata:
    """Metadata describing a panel type.

    This metadata is used by:

    - PanelRegistry for discovery and instantiation
    - Claude MCP tools for introspection
    - UI for displaying panel information

    Attributes:
        id: Unique panel identifier (e.g., "lightfall.panels.device").
        name: Human-readable panel name.
        description: Detailed description of the panel's purpose.
        icon: Icon name or path.
        category: Panel category for grouping (e.g., "Device", "Data", "Admin").
        required_permission: Permission needed to access this panel.
        singleton: Whether only one instance can exist.
        closable: Whether the panel can be closed by the user.
        keywords: Search keywords for finding this panel.
        default_area: Default dock area ("left", "right", "bottom", "center").
        sidebar_group: Sidebar group within the area ("top", "bottom").
        auto_hide: Whether panel starts in auto-hide sidebar mode.
        sidebar_order: Order within sidebar group (lower = higher).
        proactive_init: Whether to eagerly instantiate this panel during
            the post-startup proactive init sweep. Set False for heavy
            panels that should stay fully lazy.
        warmup_import: Optional module name imported in a background
            thread when the proactive init sweep starts, so a heavy
            import chain (e.g. "lightfall.claude") is already in
            sys.modules by the time this panel initializes.
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

    # Docking preferences
    default_area: str = "left"
    sidebar_group: str = "top"
    auto_hide: bool = True
    sidebar_order: int = 0
    proactive_init: bool = True
    warmup_import: str = ""

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
        status_changed: Emitted when the panel's status changes.
        title_bar_actions_changed: Emitted when title bar actions change.

    Content is placed inside a built-in vertical QScrollArea, so when a
    panel's widgets don't fit the available area the user can scroll
    instead of having them clipped. Subclasses add widgets to
    ``self._layout`` (the inner container's layout) exactly as before --
    the scroll area is transparent to the subclass API.

    Example:
        >>> class MyPanel(BasePanel):
        ...     panel_metadata = PanelMetadata(
        ...         id="lightfall.panels.my_panel",
        ...         name="My Panel",
        ...         description="Does something useful",
        ...     )
        ...
        ...     def _setup_ui(self):
        ...         label = QLabel("Hello!")
        ...         self._layout.addWidget(label)
    """

    # Class-level metadata - must be defined in subclasses
    panel_metadata: ClassVar[PanelMetadata] = PanelMetadata(
        id="lightfall.panels.base",
        name="Base Panel",
        description="Abstract base panel",
    )

    # Signals
    activated = Signal()
    deactivated = Signal()
    state_changed = Signal(str, object)  # key, value
    closing = Signal()
    icon_changed = Signal(str, str)  # icon_name, color (empty = theme default)
    status_changed = Signal(object)  # PanelStatus
    title_bar_actions_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the base panel.

        Args:
            parent: Parent widget.
        """
        super().__init__(parent)
        self._is_active = False
        self._panel_state: dict[str, Any] = {}
        self._status = PanelStatus.UNINITIALIZED
        # Title bar actions must exist before _setup_ui so subclasses can
        # register actions during UI construction.
        self._title_bar_actions: list[QAction] = []
        # Arbitrary panel-contributed title-bar widgets (e.g. a status
        # spinner used as a toggle). Rendered alongside the action buttons.
        self._title_bar_widgets: list[QWidget] = []
        # Keeps title-bar dropdown menus alive (they are owned by the button
        # popup, not parented to the panel — see add_title_bar_button).
        self._title_bar_menus: list[Any] = []

        # Set object name from metadata
        self.setObjectName(self.panel_metadata.id)

        # Force QSS background painting — QWidget doesn't paint its own
        # background by default. WA_StyledBackground makes Qt paint the
        # QSS-defined background (including border-radius) in paintEvent.
        # Unlike setAutoFillBackground (which paints a flat palette rect
        # ignoring border-radius), this respects the full stylesheet.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        # The panel itself carries the QSS background + PanelTitleBar top
        # margin. Inside, a vertical-only QScrollArea wraps the actual
        # content so that if a panel's contents exceed its area the user
        # can scroll instead of having widgets clipped. The scroll bar
        # auto-hides when not needed (AsNeeded) and horizontal scrolling
        # is disabled -- panels should size their content to fit width.
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 4, 0, 0)
        outer_layout.setSpacing(0)

        self._scroll_area = QScrollArea(self)
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._scroll_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        outer_layout.addWidget(self._scroll_area)

        # Inner container holds subclass content. `self._layout` points at
        # this container's layout so existing subclasses that call
        # `self._layout.addWidget/insertWidget/removeWidget` keep working
        # unchanged -- they transparently add to the scrollable content.
        self._content = QWidget()
        self._scroll_area.setWidget(self._content)
        self._layout = QVBoxLayout(self._content)
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

    def can_close(self, force: bool = False) -> bool:
        """Check if the panel can be closed.

        Override to implement confirmation dialogs or prevent
        closing when work is in progress.

        Args:
            force: If True, ignore the closable metadata flag (used for
                   application shutdown). Subclasses may still return False
                   if they have unsaved work.

        Returns:
            True if panel can close.
        """
        if force:
            return True
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

    # Sidebar icon updates

    def set_sidebar_icon(self, icon_name: str = "", color: str = "") -> None:
        """Update the sidebar icon and/or color at runtime.

        Emits icon_changed which the docking manager connects to the sidebar.

        Args:
            icon_name: New qtawesome icon name (e.g. "mdi6.alert-circle").
                       Empty string keeps the current icon.
            color: Icon color as hex string (e.g. "#e74c3c").
                   Empty string resets to theme default.
        """
        self.icon_changed.emit(icon_name, color)

    @property
    def status(self) -> PanelStatus:
        """Current panel status (drives the sidebar icon tint)."""
        return self._status

    def set_status(self, status: PanelStatus) -> None:
        """Set the panel status.

        Emits status_changed, which the docking manager maps to a
        theme-colored sidebar icon tint.

        Args:
            status: New PanelStatus.
        """
        if status is self._status:
            return
        self._status = status
        self.status_changed.emit(status)

    # Title bar actions

    def add_title_bar_action(self, action: QAction) -> None:
        """Add an action rendered as an icon-only button in the panel
        title bar.

        Give the action an icon -- title bar buttons are icon-only; the
        action's text/tooltip becomes the button tooltip.

        Args:
            action: The QAction to add.
        """
        self._title_bar_actions.append(action)
        self.title_bar_actions_changed.emit()

    def add_title_bar_button(
        self,
        icon_name: str,
        tooltip: str,
        on_triggered=None,
        *,
        checkable: bool = False,
        checked: bool = False,
        menu: Any = None,
    ) -> QAction:
        """Create a themed QAction and add it as a title bar button.

        Convenience over ``add_title_bar_action`` that builds the QAction
        with a qtawesome icon tinted to match the title bar's other buttons.

        Args:
            icon_name: qtawesome icon name (e.g. "mdi6.plus").
            tooltip: Tooltip / accessible text for the button.
            on_triggered: Optional slot connected to ``triggered``. For a
                checkable action it receives the new checked state.
            checkable: Whether the action toggles.
            checked: Initial checked state (only if ``checkable``).
            menu: Optional QMenu; when set the button opens it as a popup
                (used for sort / filter / target-style pickers).

        Returns:
            The created QAction (already added to the title bar).
        """
        import qtawesome as qta
        from PySide6.QtGui import QIcon

        try:
            from lightfall.ui.theme import ThemeManager

            color = ThemeManager.get_instance().colors.text_secondary
        except Exception:
            color = "#808080"

        try:
            icon = qta.icon(icon_name, color=color)
        except Exception:
            icon = QIcon()

        action = QAction(icon, tooltip, self)
        action.setToolTip(tooltip)
        if checkable:
            action.setCheckable(True)
            action.setChecked(checked)
        if menu is not None:
            # Do NOT setParent(self): reparenting a QMenu to a normal widget
            # clears its Qt.Popup window flag, so it renders inline (filling
            # the panel) instead of popping from the button. Keep a Python
            # reference instead so it survives until the title bar's button
            # adopts it as its popup menu.
            self._title_bar_menus.append(menu)
            action.setMenu(menu)
        if on_triggered is not None:
            action.triggered.connect(on_triggered)
        self.add_title_bar_action(action)
        return action

    def add_title_bar_widget(self, widget: QWidget) -> None:
        """Add an arbitrary widget to the panel title bar.

        Unlike ``add_title_bar_action`` (which renders a QAction as an icon
        button), this places a caller-owned widget — e.g. a status spinner
        doubling as a toggle — directly into the title bar. The panel retains
        ownership; the title bar never deletes it on rebuild.

        Args:
            widget: The widget to show in the title bar.
        """
        self._title_bar_widgets.append(widget)
        self.title_bar_actions_changed.emit()

    @property
    def title_bar_actions(self) -> list[QAction]:
        """Actions shown as title bar buttons (copy of internal list)."""
        return list(self._title_bar_actions)

    @property
    def title_bar_widgets(self) -> list[QWidget]:
        """Widgets shown in the title bar (copy of internal list)."""
        return list(self._title_bar_widgets)

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

        from lightfall.auth.session import SessionManager

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
