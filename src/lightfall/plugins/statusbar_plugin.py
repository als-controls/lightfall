"""Status bar plugin type for status bar indicators.

StatusBarPlugin is the plugin type for status bar indicators. Plugins
implementing this interface provide widgets that appear in the main
window's status bar.

The base class provides a default flat-button widget and one display call,
:meth:`StatusBarPlugin.set_state`, which takes text, a qtawesome icon name
and a theme color *role*. The base class builds the tinted icon, applies the
color, and re-renders on theme changes. Subclasses never touch
``ThemeManager`` or hex colors, and typically only implement ``update()``,
``connect_signals()``, ``disconnect_signals()`` and optionally ``on_clicked()``.

Style rules. At runtime a violation only logs a warning. For in-repo plugins,
``tests/ui/statusbar/test_statusbar_style.py`` fails on them:

- Icons come from qtawesome via ``set_state(icon=...)``, never from unicode
  symbols or emoji in the label text.
- Colors are theme roles (:data:`COLOR_ROLES`), never hex/CSS literals.
"""

from __future__ import annotations

import unicodedata
from abc import abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QToolButton

from lightfall.plugins.types import PluginType
from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

#: Theme color roles accepted by :meth:`StatusBarPlugin.set_state` (attribute
#: names on the theme's ``ThemeColors``). ``None`` means the default text color.
COLOR_ROLES: frozenset[str] = frozenset(
    {"info", "success", "warning", "error", "text", "text_secondary", "primary", "secondary"}
)

#: Unicode categories of symbols used as icons (arrows, check marks, emoji,
#: hourglasses, ...). Non-ASCII letters (micro sign) and punctuation
#: (ellipsis, dashes, middle dot) are fine.
_SYMBOL_CATEGORIES = frozenset({"So", "Sm", "Sk"})


def icon_like_chars(text: str) -> list[str]:
    """Non-ASCII symbol/emoji characters in ``text`` (which belong in an icon instead)."""
    return [c for c in text if ord(c) > 127 and unicodedata.category(c) in _SYMBOL_CATEGORIES]


@dataclass
class StatusBarPluginMetadata:
    """Metadata for a status bar plugin.

    Attributes:
        id: Unique identifier (e.g., "lightfall.statusbar.user").
        name: Human-readable display name.
        description: Description of what the indicator shows.
        priority: Sort order (lower = further left in status bar).
        position: Where to add the widget ("left", "right", "permanent").
        tooltip: Default tooltip text.
    """

    id: str
    name: str
    description: str = ""
    priority: int = 100
    position: str = "permanent"  # left, right, permanent
    tooltip: str = ""


class _StatusBarSignals(QObject):
    """QObject host for plugin signals (StatusBarPlugin is not a QObject)."""

    visibility_changed = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self.theme_callback: Any = None

    @Slot()
    def relay_theme_changed(self) -> None:
        if self.theme_callback is not None:
            self.theme_callback()


class StatusBarPlugin(PluginType):
    """Abstract base for status bar indicator plugins.

    Default behaviour: each plugin renders as a flat ``QToolButton`` so
    every entry looks consistent and signals clickability. Subclasses
    drive their display by overriding ``update()`` and calling the
    :meth:`set_state` (text + qtawesome icon + theme color role) and
    :meth:`set_tooltip`. Theme changes re-render the last state
    automatically (see :meth:`on_theme_changed`). Override
    ``on_clicked()`` to react to button clicks.

    Subclasses that need a more complex widget (e.g. anchoring an
    overlay) can override ``create_widget()``. They must then also
    assign ``self._widget`` themselves, and can read :attr:`theme_colors`
    and override :meth:`on_theme_changed` to stay themed.

    Class Attributes:
        type_name: "statusbar" - identifies this as a statusbar plugin.
        is_singleton: True - status bar plugins are singletons.
        metadata: Class-level metadata about this indicator.

    Lifecycle:
        1. Plugin is instantiated on load
        2. StatusBarManager calls create_widget() to get the widget
        3. connect_signals() is called to wire up state change handlers
        4. update() is called to set initial state
        5. During runtime, signals trigger update() calls
        6. On cleanup, disconnect_signals() is called

    Example::

        class MyStatusPlugin(StatusBarPlugin):
            metadata = StatusBarPluginMetadata(
                id="lightfall.statusbar.my_status",
                name="My Status",
                priority=50,
            )

            @property
            def name(self) -> str:
                return "my_status"

            def update(self) -> None:
                if self._service.ok:
                    self.set_state("OK", icon="mdi6.check-circle", role="success",
                                   tooltip="Everything is fine")
                else:
                    self.set_state("Down", icon="mdi6.alert-circle", role="error",
                                   tooltip=self._service.error)

            def connect_signals(self) -> None:
                self._service.changed.connect(self.update)

            def disconnect_signals(self) -> None:
                self._service.changed.disconnect(self.update)

            def on_clicked(self) -> None:
                open_my_status_dialog()
    """

    type_name: ClassVar[str] = "statusbar"
    is_singleton: ClassVar[bool] = True

    # Subclasses must define this
    metadata: ClassVar[StatusBarPluginMetadata]

    def __init__(self) -> None:
        """Initialize the status bar plugin."""
        self._widget: QWidget | None = None
        self._button: QToolButton | None = None
        self._signals = _StatusBarSignals()
        self._signals.theme_callback = self.on_theme_changed
        self._visible: bool = True
        self._theme_manager: Any = None
        self._theme_attached = False
        self._state: tuple[str, str | None, str | None] | None = None
        self._style_warnings: set[str] = set()

    @property
    def description(self) -> str:
        """Human-readable description of this status bar plugin."""
        return "Status bar indicator plugin"

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this status bar plugin.

        This should be unique within the statusbar type and is used to
        identify the plugin in the registry.
        """
        ...

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        """Create the status bar indicator widget.

        Default implementation returns a flat ``QToolButton`` whose
        ``clicked`` signal is wired to :meth:`on_clicked`. Subclasses
        that want a different widget should override this and assign
        ``self._widget`` themselves; they are responsible for any click
        wiring in that case.

        Args:
            parent: Parent widget (the status bar container).

        Returns:
            A QWidget to display in the status bar.
        """
        button = QToolButton(parent)
        button.setAutoRaise(True)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        if self.metadata.tooltip:
            button.setToolTip(self.metadata.tooltip)
        button.clicked.connect(self.on_clicked)
        self._button = button
        self._widget = button
        self._attach_theme()
        return button

    @abstractmethod
    def update(self) -> None:
        """Update the widget display based on current state.

        Called after create_widget() and whenever the underlying
        state changes. Should read current state and update the
        widget accordingly.
        """
        ...

    @abstractmethod
    def connect_signals(self) -> None:
        """Connect to service signals for state change notifications.

        Called after create_widget(). Should connect to relevant
        service signals that will trigger update() calls.
        """
        ...

    @abstractmethod
    def disconnect_signals(self) -> None:
        """Disconnect from service signals.

        Called during cleanup. Should disconnect all signals that
        were connected in connect_signals().
        """
        ...

    def on_clicked(self) -> None:
        """Handle a button click. Default is a no-op."""

    @property
    def widget(self) -> QWidget | None:
        """Get the created widget, if any.

        Returns:
            The widget created by create_widget(), or None.
        """
        return self._widget

    # ------------------------------------------------------------------
    # Visibility
    # ------------------------------------------------------------------

    @property
    def visibility_changed(self) -> Signal:
        """Signal emitted with the new visibility (bool) when toggled."""
        return self._signals.visibility_changed

    @property
    def is_visible(self) -> bool:
        """Whether the plugin's widget is currently shown."""
        return self._visible

    def set_visible(self, visible: bool) -> None:
        """Show or hide this plugin's widget in the status bar.

        Hidden widgets are removed from the layout flow (no reserved
        slot, no extra separator), so neighbouring plugins close up.
        """
        if self._visible == visible:
            return
        self._visible = visible
        if self._widget is not None:
            self._widget.setVisible(visible)
        self._signals.visibility_changed.emit(visible)

    # ------------------------------------------------------------------
    # Theming
    # ------------------------------------------------------------------

    def _theme(self) -> Any:
        if self._theme_manager is None:
            try:
                from lightfall.ui.theme import ThemeManager

                self._theme_manager = ThemeManager.get_instance()
            except Exception as e:  # noqa: BLE001 - no theme yet: fall back to defaults
                logger.debug("Status bar plugin has no ThemeManager: {}", e)
        return self._theme_manager

    @property
    def theme_colors(self) -> Any:
        """The current theme's ``ThemeColors`` (defaults if no theme is available)."""
        colors = getattr(self._theme(), "colors", None)
        if colors is None:
            from lightfall_utils.theming.manager import ThemeColors

            colors = ThemeColors()
        return colors

    def theme_color(self, role: str | None) -> str | None:
        """The current color for a theme ``role`` (see :data:`COLOR_ROLES`), or None."""
        if role is None:
            return None
        if role not in COLOR_ROLES:
            self._warn_style(f"role:{role}", "unknown color role {!r}; use one of {}",
                             role, sorted(COLOR_ROLES))
            return None
        return getattr(self.theme_colors, role, None) or None

    def _attach_theme(self) -> None:
        """Re-render on theme changes (idempotent; the manager also calls this)."""
        if self._theme_attached:
            return
        signal = getattr(self._theme(), "colors_changed", None)
        if signal is None:
            return
        try:
            signal.connect(self._signals.relay_theme_changed)
            self._theme_attached = True
        except (RuntimeError, TypeError) as e:
            logger.debug("Could not follow theme changes: {}", e)

    def _detach_theme(self) -> None:
        if not self._theme_attached:
            return
        self._theme_attached = False
        try:
            self._theme_manager.colors_changed.disconnect(self._signals.relay_theme_changed)
        except (RuntimeError, TypeError, AttributeError):
            pass

    def on_theme_changed(self) -> None:
        """Called when the theme's colors change.

        The default re-renders the last :meth:`set_state`. Plugins with
        custom widgets override this to restyle them.
        """
        if self._state is not None:
            self._render_state()

    def _warn_style(self, key: str, message: str, *args: Any) -> None:
        if key in self._style_warnings:
            return
        self._style_warnings.add(key)
        logger.warning("Status bar plugin {}: " + message, self.metadata.id, *args)

    # ------------------------------------------------------------------
    # Display helpers for subclasses (default-widget path)
    # ------------------------------------------------------------------

    def set_state(
        self,
        text: str = "",
        *,
        icon: str | None = None,
        role: str | None = None,
        tooltip: str | None = None,
    ) -> None:
        """Show ``text`` with a qtawesome ``icon``, both in the theme color ``role``.

        Args:
            text: Label, which may be empty for an icon-only indicator. Use
                no unicode symbols or emoji here; pass ``icon`` instead.
            icon: qtawesome icon name, e.g. ``"mdi6.database"``.
            role: Theme color role from :data:`COLOR_ROLES` (``"success"``,
                ``"warning"``, ``"error"``, ``"info"``, ``"text_secondary"``,
                ...), or None for the default text color.
            tooltip: Optional tooltip to set at the same time.

        The state is re-rendered with the new colors whenever the theme changes.
        """
        self._state = (text, icon, role)
        self._render_state()
        if tooltip is not None:
            self.set_tooltip(tooltip)

    def _render_state(self) -> None:
        text, icon_name, role = self._state
        color = self.theme_color(role)
        self.set_text(text)
        icon = None
        if icon_name:
            try:
                import qtawesome as qta

                icon = qta.icon(icon_name, color=color or self.theme_colors.text)
            except Exception as e:  # noqa: BLE001 - a bad icon name must not break the bar
                self._warn_style(f"icon:{icon_name}", "could not load icon {!r}: {}",
                                 icon_name, e)
        self.set_icon(icon)
        self._apply_color(color)

    def set_text(self, text: str) -> None:
        """Set the displayed text on the default button widget.

        Prefer :meth:`set_state`. Unicode symbols or emoji in ``text`` log a
        warning; use a qtawesome icon instead.
        """
        symbols = icon_like_chars(text)
        if symbols:
            self._warn_style("glyph:" + "".join(sorted(set(symbols))),
                             "label {!r} uses symbol characters {}; use a qtawesome "
                             "icon via set_state(icon=...) instead", text, symbols)
        if self._button is not None:
            self._button.setText(text)
        self._update_toolbutton_style()

    def set_icon(self, icon: QIcon | None) -> None:
        """Set a leading icon on the default button widget.

        Switches the button to show the icon beside any text. Pass ``None``
        to clear the icon and revert to text-only display.
        """
        if self._button is None:
            return
        if icon is None:
            self._button.setIcon(QIcon())
        else:
            self._button.setIcon(icon)
        self._update_toolbutton_style()

    def _update_toolbutton_style(self) -> None:
        """Pick the button style from the current icon/text combination.

        Keeps the default button compact: icon-only when there is no text,
        text-only when there is no icon, and icon-beside-text otherwise.
        """
        if self._button is None:
            return
        has_icon = not self._button.icon().isNull()
        has_text = bool(self._button.text())
        if not has_icon:
            style = Qt.ToolButtonStyle.ToolButtonTextOnly
        elif has_text:
            style = Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        else:
            style = Qt.ToolButtonStyle.ToolButtonIconOnly
        self._button.setToolButtonStyle(style)

    def set_tooltip(self, tooltip: str) -> None:
        """Set the tooltip on the default button widget."""
        if self._button is not None:
            self._button.setToolTip(tooltip)
        elif self._widget is not None:
            self._widget.setToolTip(tooltip)

    def set_color(self, color: str | None) -> None:
        """Apply a raw CSS foreground color to the default button widget.

        Deprecated: use ``set_state(role=...)``, which follows theme changes.
        A raw color stays fixed when the theme switches. Logs a warning
        once per plugin.
        """
        if color:
            self._warn_style("set_color", "set_color({!r}) is deprecated; use "
                             "set_state(role=...) so the color follows the theme", color)
        self._apply_color(color)

    def _apply_color(self, color: str | None) -> None:
        if self._button is None:
            return
        if color:
            self._button.setStyleSheet(f"QToolButton {{ color: {color}; }}")
        else:
            self._button.setStyleSheet("")

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools.

        Returns:
            Dictionary with status bar plugin information.
        """
        meta = self.metadata
        data: dict[str, Any] = {
            "type": self.type_name,
            "name": self.name,
            "id": meta.id,
            "display_name": meta.name,
            "description": meta.description,
            "priority": meta.priority,
            "position": meta.position,
            "is_visible": self._visible,
            "class": self.__class__.__name__,
            "module": self.__class__.__module__,
        }

        if self._state is not None:
            data["icon"] = self._state[1]
            data["color_role"] = self._state[2]
        if self._button is not None:
            data["current_text"] = self._button.text()
            data["tooltip"] = self._button.toolTip()
        elif self._widget is not None:
            from PySide6.QtWidgets import QLabel

            if isinstance(self._widget, QLabel):
                data["current_text"] = self._widget.text()
                data["tooltip"] = self._widget.toolTip()

        return data
