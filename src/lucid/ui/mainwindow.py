"""Main window for the LUCID application.

Provides the primary application window with:
- Dock-based panel system
- Menu and toolbar
- Status bar with auth and connection status
- Theme management
- Window state persistence
"""

from __future__ import annotations

import sys
from datetime import UTC
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QMainWindow,
    QStatusBar,
    QToolBar,
    QWidget,
)

from lucid.auth.session import AuthState, SessionManager
from lucid.ui.panels.base import BasePanel
from lucid.ui.panels.registry import PanelRegistry
from lucid.ui.preferences import PreferencesDialog, PreferencesManager
from lucid.ui.statusbar import StatusBarManager
from lucid.ui.theme import Theme, ThemeManager
from lucid.ui.widgets.runengine_control import RunEngineControlWidget
from lucid.utils.logging import logger

if TYPE_CHECKING:
    from lucid.config import ConfigManager


class NCSMainWindow(QMainWindow):
    """
    Main application window for LUCID.

    NCSMainWindow provides:
    - Menu bar with standard and custom menus
    - Toolbar with common actions
    - Dock widget system for panels
    - Status bar with user, auth state, and connection info
    - Theme switching support
    - Window state persistence

    Signals:
        panel_activated: Emitted when a panel becomes active.
        about_to_close: Emitted when window is about to close.

    Example:
        >>> window = NCSMainWindow()
        >>> window.show()
    """

    panel_activated = Signal(str)  # panel_id
    about_to_close = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the main window.

        Args:
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._config_manager: ConfigManager | None = None
        self._panel_docks: dict[str, QDockWidget] = {}
        self._active_panel_id: str | None = None
        self._default_layout_applied: bool = False
        self._initial_show_done: bool = False
        self._statusbar_manager: StatusBarManager | None = None

        # Get manager instances
        self._session_manager = SessionManager.get_instance()
        self._theme_manager = ThemeManager.get_instance()
        self._prefs_manager = PreferencesManager.get_instance()
        self._panel_registry = PanelRegistry.get_instance()

        # Setup UI
        self._setup_window()
        self._setup_menus()
        self._setup_toolbar()
        self._setup_statusbar()
        self._setup_central_widget()

        # Connect signals
        self._connect_signals()

        # Apply initial theme
        self._apply_theme()

        logger.info("Main window initialized")

    def _setup_window(self) -> None:
        """Configure window properties."""
        self.setWindowTitle("LUCID - Lightsource Unified Control Interface Dashboard")
        self.setMinimumSize(1024, 768)

        # Enable dock nesting and tabbing
        self.setDockNestingEnabled(True)

        # Set object name for state saving
        self.setObjectName("NCSMainWindow")

    def _setup_menus(self) -> None:
        """Create the menu bar."""
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("&File")

        # Open action
        open_action = QAction("&Open...", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self._on_open)
        file_menu.addAction(open_action)

        # Recent files submenu
        self._recent_menu = file_menu.addMenu("Recent Files")
        self._update_recent_menu()

        file_menu.addSeparator()

        # Save layout action
        save_layout_action = QAction("Save Layout", self)
        save_layout_action.triggered.connect(self._save_window_state)
        file_menu.addAction(save_layout_action)

        # Restore layout action
        restore_layout_action = QAction("Restore Layout", self)
        restore_layout_action.triggered.connect(self._restore_window_state)
        file_menu.addAction(restore_layout_action)

        file_menu.addSeparator()

        # Exit action
        exit_action = QAction("E&xit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # View menu
        view_menu = menubar.addMenu("&View")

        # Panels submenu
        self._panels_menu = view_menu.addMenu("Panels")
        self._update_panels_menu()

        view_menu.addSeparator()

        # Theme submenu
        theme_menu = view_menu.addMenu("Theme")

        light_action = QAction("Light", self)
        light_action.setCheckable(True)
        light_action.triggered.connect(lambda: self._set_theme(Theme.LIGHT))
        theme_menu.addAction(light_action)

        dark_action = QAction("Dark", self)
        dark_action.setCheckable(True)
        dark_action.triggered.connect(lambda: self._set_theme(Theme.DARK))
        theme_menu.addAction(dark_action)

        system_action = QAction("System", self)
        system_action.setCheckable(True)
        system_action.setChecked(True)
        system_action.triggered.connect(lambda: self._set_theme(Theme.SYSTEM))
        theme_menu.addAction(system_action)

        self._theme_actions = {
            Theme.LIGHT: light_action,
            Theme.DARK: dark_action,
            Theme.SYSTEM: system_action,
        }

        view_menu.addSeparator()

        # Show/hide toolbar
        self._toolbar_action = QAction("Show Toolbar", self)
        self._toolbar_action.setCheckable(True)
        self._toolbar_action.setChecked(self._prefs_manager.show_toolbar)
        self._toolbar_action.triggered.connect(self._toggle_toolbar)
        view_menu.addAction(self._toolbar_action)

        # Show/hide statusbar
        self._statusbar_action = QAction("Show Status Bar", self)
        self._statusbar_action.setCheckable(True)
        self._statusbar_action.setChecked(self._prefs_manager.show_statusbar)
        self._statusbar_action.triggered.connect(self._toggle_statusbar)
        view_menu.addAction(self._statusbar_action)

        # Tools menu
        tools_menu = menubar.addMenu("&Tools")

        # Preferences action
        prefs_action = QAction("&Preferences...", self)
        prefs_action.setShortcut("Ctrl+,")
        prefs_action.triggered.connect(self._on_preferences)
        tools_menu.addAction(prefs_action)

        # User menu
        user_menu = menubar.addMenu("&User")

        # Login action
        self._login_action = QAction("&Login...", self)
        self._login_action.triggered.connect(self._on_login)
        user_menu.addAction(self._login_action)

        # Logout action
        self._logout_action = QAction("Log&out", self)
        self._logout_action.triggered.connect(self._on_logout)
        self._logout_action.setEnabled(False)  # Disabled until logged in
        user_menu.addAction(self._logout_action)

        user_menu.addSeparator()

        # User info (disabled, just shows current user)
        self._user_info_action = QAction("Guest", self)
        self._user_info_action.setEnabled(False)
        user_menu.addAction(self._user_info_action)

        # Help menu
        help_menu = menubar.addMenu("&Help")

        # About action
        about_action = QAction("&About LUCID", self)
        about_action.triggered.connect(self._on_about)
        help_menu.addAction(about_action)

        # Add RunEngine control widget to menubar corner
        self._re_control = RunEngineControlWidget()
        menubar.setCornerWidget(self._re_control, Qt.Corner.TopRightCorner)

    def _setup_toolbar(self) -> None:
        """Create the main toolbar."""
        self._toolbar = QToolBar("Main Toolbar")
        self._toolbar.setObjectName("MainToolbar")
        self.addToolBar(self._toolbar)

        # Set visibility from preferences
        self._toolbar.setVisible(self._prefs_manager.show_toolbar)

    def set_engine(self, engine) -> None:
        """Connect the Engine to the menubar control widget.

        Args:
            engine: The Engine instance.
        """
        self._re_control.set_engine(engine)
        logger.info("Connected Engine to toolbar control")

    def set_run_engine(self, re) -> None:
        """Connect the Engine to the menubar control widget.

        Deprecated: Use set_engine() instead.

        Args:
            re: The Engine instance.
        """
        self.set_engine(re)

    def _setup_statusbar(self) -> None:
        """Create the status bar with plugin-based indicators."""
        statusbar = QStatusBar()
        self.setStatusBar(statusbar)

        # Create and initialize status bar manager
        self._statusbar_manager = StatusBarManager(statusbar, self)
        self._statusbar_manager.load_plugins()

        # Set visibility from preferences
        statusbar.setVisible(self._prefs_manager.show_statusbar)

    def _setup_central_widget(self) -> None:
        """Setup the central widget area."""
        # Use a placeholder central widget
        # Panels will be docked around this
        central = QWidget()
        central.setMaximumSize(0, 0)  # Zero size so docks fill the window
        self.setCentralWidget(central)

    def _connect_signals(self) -> None:
        """Connect to manager signals."""
        # Session signals (for panels menu updates)
        self._session_manager.state_changed.connect(self._on_auth_state_changed)
        self._session_manager.user_changed.connect(self._on_user_changed)

        # Theme signals
        self._theme_manager.theme_changed.connect(self._on_theme_changed)

        # Preferences signals
        self._prefs_manager.preference_changed.connect(self._on_preference_changed)

    def set_config_manager(self, config_manager: ConfigManager) -> None:
        """Set the config manager for the window.

        Args:
            config_manager: ConfigManager instance.
        """
        self._config_manager = config_manager
        self._prefs_manager.set_config_manager(config_manager)

        # Apply theme from config
        theme_str = config_manager.get("ui.theme", "system")
        try:
            theme = Theme(theme_str)
            self._theme_manager.set_theme(theme)
        except ValueError:
            pass

    # Panel management

    def add_panel(
        self,
        panel_id: str,
        area: Qt.DockWidgetArea = Qt.DockWidgetArea.LeftDockWidgetArea,
    ) -> BasePanel | None:
        """Add a panel to the window.

        Args:
            panel_id: Panel identifier.
            area: Dock area to add panel to.

        Returns:
            The panel instance or None if failed.
        """
        # Check if already open
        if panel_id in self._panel_docks:
            dock = self._panel_docks[panel_id]
            dock.show()
            dock.raise_()
            return dock.widget()

        # Create panel
        panel = self._panel_registry.create(panel_id)
        if panel is None:
            logger.warning("Failed to create panel: {}", panel_id)
            return None

        # Check permission
        if not panel.check_access(self._session_manager.current_user):
            logger.warning(
                "User lacks permission for panel: {}", panel_id
            )
            return None

        # Create dock widget
        dock = QDockWidget(panel.panel_metadata.name, self)
        dock.setObjectName(f"dock_{panel_id}")
        dock.setWidget(panel)

        # Configure dock
        if panel.panel_metadata.closable:
            dock.setFeatures(
                QDockWidget.DockWidgetFeature.DockWidgetClosable
                | QDockWidget.DockWidgetFeature.DockWidgetMovable
                | QDockWidget.DockWidgetFeature.DockWidgetFloatable
            )
        else:
            dock.setFeatures(
                QDockWidget.DockWidgetFeature.DockWidgetMovable
                | QDockWidget.DockWidgetFeature.DockWidgetFloatable
            )

        # Connect dock signals
        dock.visibilityChanged.connect(
            lambda visible, pid=panel_id: self._on_dock_visibility_changed(pid, visible)
        )

        # Add to window
        self.addDockWidget(area, dock)
        self._panel_docks[panel_id] = dock

        logger.debug("Added panel to window: {}", panel_id)
        return panel

    def setup_default_layout(self) -> None:
        """Setup the default panel layout.

        Opens Bluesky+Devices tabbed on left, Logbook+Documents tabbed on right.
        Also clears saved state and prevents showEvent from restoring.
        """
        # Clear any saved state
        from PySide6.QtCore import QSettings
        settings = QSettings("ALS", "NCS")
        settings.remove("mainwindow/geometry")
        settings.remove("mainwindow/state")

        # Add Bluesky panel on the left
        self.add_panel(
            "lucid.panels.bluesky",
            area=Qt.DockWidgetArea.LeftDockWidgetArea,
        )

        # Add Devices panel on the left
        self.add_panel(
            "lucid.panels.devices",
            area=Qt.DockWidgetArea.LeftDockWidgetArea,
        )

        # Tabify Bluesky and Devices
        bluesky_dock = self._panel_docks.get("lucid.panels.bluesky")
        devices_dock = self._panel_docks.get("lucid.panels.devices")
        if bluesky_dock and devices_dock:
            self.tabifyDockWidget(bluesky_dock, devices_dock)
            bluesky_dock.raise_()

        # Add Logbook panel on the right
        self.add_panel(
            "lucid.panels.logbook",
            area=Qt.DockWidgetArea.RightDockWidgetArea,
        )

        # Add Documents panel on the right
        self.add_panel(
            "lucid.panels.documents",
            area=Qt.DockWidgetArea.RightDockWidgetArea,
        )

        # Tabify Logbook and Documents
        logbook_dock = self._panel_docks.get("lucid.panels.logbook")
        documents_dock = self._panel_docks.get("lucid.panels.documents")
        if logbook_dock and documents_dock:
            self.tabifyDockWidget(logbook_dock, documents_dock)
            logbook_dock.raise_()

        # Pre-create Synoptic panel (hidden) to avoid OpenGL flicker on Windows.
        # OpenGL context creation causes brief window flicker, so we do it here
        # before the main window is visible to the user.
        try:
            synoptic_panel = self.add_panel(
                "lucid.panels.synoptic",
                area=Qt.DockWidgetArea.RightDockWidgetArea,
            )
            synoptic_dock = self._panel_docks.get("lucid.panels.synoptic")
            if synoptic_dock and synoptic_panel:
                # Force the deferred OpenGL initialization now (before window is shown)
                if hasattr(synoptic_panel, '_complete_initialization') and not synoptic_panel._deferred_init_done:
                    synoptic_panel._deferred_init_done = True
                    synoptic_panel._complete_initialization()
                synoptic_dock.hide()
        except Exception as e:
            logger.debug("Could not pre-create Synoptic panel: {}", e)

        self._default_layout_applied = True
        logger.info("Applied default panel layout")

    def remove_panel(self, panel_id: str) -> bool:
        """Remove a panel from the window.

        Args:
            panel_id: Panel identifier.

        Returns:
            True if panel was removed.
        """
        dock = self._panel_docks.pop(panel_id, None)
        if dock is None:
            return False

        self.removeDockWidget(dock)
        dock.deleteLater()

        logger.debug("Removed panel from window: {}", panel_id)
        return True

    def get_panel(self, panel_id: str) -> BasePanel | None:
        """Get a panel by ID.

        Args:
            panel_id: Panel identifier.

        Returns:
            The panel instance or None.
        """
        dock = self._panel_docks.get(panel_id)
        if dock:
            return dock.widget()
        return None

    def list_open_panels(self) -> list[str]:
        """Get list of open panel IDs.

        Returns:
            List of panel identifiers.
        """
        return list(self._panel_docks.keys())

    def activate_panel(self, panel_id: str) -> bool:
        """Activate (focus) a panel.

        Args:
            panel_id: Panel identifier.

        Returns:
            True if panel was activated.
        """
        dock = self._panel_docks.get(panel_id)
        if dock is None:
            return False

        dock.show()
        dock.raise_()
        dock.widget().activate()

        if self._active_panel_id != panel_id:
            # Deactivate previous
            if self._active_panel_id:
                prev_dock = self._panel_docks.get(self._active_panel_id)
                if prev_dock:
                    prev_dock.widget().deactivate()

            self._active_panel_id = panel_id
            self.panel_activated.emit(panel_id)

        return True

    # Menu updates

    def _update_panels_menu(self) -> None:
        """Update the panels submenu."""
        self._panels_menu.clear()

        # Group by category
        by_category = self._panel_registry.list_by_category(
            self._session_manager.current_user
        )

        for category in sorted(by_category.keys()):
            if len(by_category) > 1:
                cat_menu = self._panels_menu.addMenu(category)
            else:
                cat_menu = self._panels_menu

            for meta in by_category[category]:
                action = QAction(meta.name, self)
                action.setToolTip(meta.description)
                action.triggered.connect(
                    lambda checked, pid=meta.id: self.add_panel(pid)
                )
                cat_menu.addAction(action)

    def _update_recent_menu(self) -> None:
        """Update the recent files menu."""
        self._recent_menu.clear()

        recent = self._prefs_manager.get_recent_files()
        if not recent:
            action = QAction("(No recent files)", self)
            action.setEnabled(False)
            self._recent_menu.addAction(action)
            return

        for path in recent:
            action = QAction(path, self)
            action.triggered.connect(lambda checked, p=path: self._open_file(p))
            self._recent_menu.addAction(action)

        self._recent_menu.addSeparator()
        clear_action = QAction("Clear Recent Files", self)
        clear_action.triggered.connect(self._prefs_manager.clear_recent_files)
        self._recent_menu.addAction(clear_action)

    # Theme handling

    def _apply_theme(self) -> None:
        """Apply current theme to the window."""
        self._theme_manager.apply_to_application()

        # Update theme action checkmarks
        current = self._theme_manager.theme
        for theme, action in self._theme_actions.items():
            action.setChecked(theme == current)

    def _set_theme(self, theme: Theme) -> None:
        """Set the application theme.

        Args:
            theme: Theme to apply.
        """
        self._theme_manager.set_theme(theme)
        self._prefs_manager.theme = theme.value

    # Signal handlers

    def _force_window_to_front(self) -> None:
        """Force the window to the foreground, even on Windows.

        On Windows, apps cannot normally steal focus from another application
        (like a browser after OAuth flow). This method uses multiple techniques
        to reliably bring the window to the front:
        1. Ensure window is not minimized
        2. Temporarily use WindowStaysOnTopHint to force visibility
        3. Remove the hint after a brief delay so window behaves normally
        4. Call all standard activation methods
        """
        # Ensure not minimized and visible
        self.showNormal()

        # On Windows, temporarily set stay-on-top to force visibility
        if sys.platform == "win32":
            # Get current flags and add stay-on-top
            flags = self.windowFlags()
            self.setWindowFlags(flags | Qt.WindowStaysOnTopHint)
            self.show()  # Required after setWindowFlags

            # Remove the hint after a short delay
            def remove_stay_on_top() -> None:
                self.setWindowFlags(flags)
                self.show()  # Required after setWindowFlags
                self.activateWindow()

            QTimer.singleShot(100, remove_stay_on_top)
        else:
            self.show()

        # Standard activation
        self.raise_()
        self.activateWindow()
        QApplication.setActiveWindow(self)

    @Slot(AuthState, AuthState)
    def _on_auth_state_changed(
        self, new_state: AuthState, old_state: AuthState
    ) -> None:
        """Handle auth state change."""
        self._update_panels_menu()  # Permissions may have changed

        # Raise the window when login completes (user returns from browser)
        if new_state == AuthState.AUTHENTICATED:
            self._force_window_to_front()

    @Slot(object)
    def _on_user_changed(self, user: Any) -> None:
        """Handle user change."""
        self._update_panels_menu()  # Available panels may change
        self._update_user_menu(user)

        # Show login toast if user logged in (not anonymous)
        if hasattr(user, "username") and user.username != "anonymous":
            self._show_login_notification(user)

    def _show_login_notification(self, user: Any) -> None:
        """Show toast with session expiry info.

        Args:
            user: The logged-in User object.
        """
        from datetime import datetime

        from lucid.ui.toast import ToastManager

        # Check if user has expiry info
        if not hasattr(user, "expires_at") or user.expires_at is None:
            return

        # Calculate remaining time
        now = datetime.now(UTC)
        remaining_seconds = (user.expires_at - now).total_seconds()
        logger.debug(
            "Session info: expires_at={}, now={}, remaining={}s",
            user.expires_at,
            now,
            remaining_seconds,
        )

        # Format expiry time
        expires_text = self._format_expiry(user.expires_at)
        duration_text = self._format_time_remaining(user.expires_at)

        toast_manager = ToastManager.get_instance()
        toast_manager.info(
            "Logged In",
            f"Session expires at {expires_text} ({duration_text})",
            duration=8000,
        )

    def _format_expiry(self, expires_at: Any) -> str:
        """Format expiry time for display.

        Args:
            expires_at: Expiry datetime.

        Returns:
            Formatted time string like "3:00 PM".
        """
        local_time = expires_at.astimezone()
        return local_time.strftime("%I:%M %p").lstrip("0")

    def _format_time_remaining(self, expires_at: Any) -> str:
        """Format time remaining until expiry.

        Args:
            expires_at: Expiry datetime.

        Returns:
            Formatted string like "in 2 hours".
        """
        from datetime import datetime

        now = datetime.now(UTC)
        remaining = (expires_at - now).total_seconds()

        if remaining < 3600:  # Less than 1 hour
            minutes = max(1, round(remaining / 60))
            return f"in {minutes} minute{'s' if minutes != 1 else ''}"
        hours = round(remaining / 3600)
        return f"in {hours} hour{'s' if hours != 1 else ''}"

    @Slot(Theme)
    def _on_theme_changed(self, theme: Theme) -> None:
        """Handle theme change."""
        self._apply_theme()

    @Slot(str, object)
    def _on_preference_changed(self, key: str, value: Any) -> None:
        """Handle preference change."""
        if key == "theme":
            try:
                self._set_theme(Theme(value))
            except ValueError:
                pass

    def _on_dock_visibility_changed(self, panel_id: str, visible: bool) -> None:
        """Handle dock visibility change."""
        panel = self.get_panel(panel_id)
        if panel:
            if visible:
                panel.activate()
            else:
                panel.deactivate()

    # Action handlers

    def _on_open(self) -> None:
        """Handle open action."""
        # TODO: Implement file open dialog
        pass

    def _open_file(self, path: str) -> None:
        """Open a file by path."""
        # TODO: Implement file opening
        self._prefs_manager.add_recent_file(path)
        self._update_recent_menu()

    def _on_preferences(self) -> None:
        """Open preferences dialog."""
        dialog = PreferencesDialog(self)
        dialog.exec()

    def _on_about(self) -> None:
        """Show about dialog."""
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.about(
            self,
            "About LUCID",
            "LUCID - LBNL Unified Control Interface for Data acquisition\n\n"
            "A modern control system for the ALS facility.\n\n"
            "Version: Development",
        )

    def _on_login(self) -> None:
        """Show login dialog."""
        from lucid.ui.dialogs import LoginDialog

        dialog = LoginDialog(self)
        dialog.exec()

    def _on_logout(self) -> None:
        """Logout current user."""
        import asyncio

        from lucid.utils.threads import QThreadFuture

        def do_logout() -> None:
            asyncio.run(self._session_manager.logout())

        thread = QThreadFuture(do_logout, name="logout")
        thread.start()

    def _update_user_menu(self, user: Any) -> None:
        """Update user menu state based on current user.

        Args:
            user: The current User object.
        """
        from lucid.auth.session import ANONYMOUS_USER

        is_authenticated = user.username != ANONYMOUS_USER.username

        self._login_action.setEnabled(not is_authenticated)
        self._logout_action.setEnabled(is_authenticated)

        if is_authenticated:
            self._user_info_action.setText(f"Logged in as: {user.display_name}")
        else:
            self._user_info_action.setText("Guest")

    def _toggle_toolbar(self, checked: bool) -> None:
        """Toggle toolbar visibility."""
        self._toolbar.setVisible(checked)
        self._prefs_manager.show_toolbar = checked

    def _toggle_statusbar(self, checked: bool) -> None:
        """Toggle statusbar visibility."""
        self.statusBar().setVisible(checked)
        self._prefs_manager.show_statusbar = checked

    # Window state

    def _save_window_state(self) -> None:
        """Save window state to preferences."""
        self._prefs_manager.save_window_state(self)
        self.statusBar().showMessage("Layout saved", 3000)

    def _restore_window_state(self) -> None:
        """Restore window state from preferences."""
        if self._prefs_manager.restore_window_state(self):
            self.statusBar().showMessage("Layout restored", 3000)
        else:
            self.statusBar().showMessage("No saved layout found", 3000)

    # Lifecycle

    def showEvent(self, event) -> None:
        """Handle show event."""
        super().showEvent(event)

        # Only restore state on the first show - subsequent shows (e.g., after
        # being briefly hidden during OpenGL context creation) should not
        # re-trigger the full state restoration
        if self._initial_show_done:
            return
        self._initial_show_done = True

        # Skip restoration if we just applied a fresh default layout
        if self._default_layout_applied:
            self._default_layout_applied = False
            return

        # Restore window state if preference set
        if self._config_manager and self._config_manager.get("ui.remember_geometry", True):
            self._prefs_manager.restore_window_state(self)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Handle close event."""
        self.about_to_close.emit()

        # Save window state if preference set
        if self._config_manager and self._config_manager.get("ui.remember_geometry", True):
            self._prefs_manager.save_window_state(self)

        # Check if any panel has unsaved work (force=True to ignore closable flag)
        for panel_id in list(self._panel_docks.keys()):
            panel = self.get_panel(panel_id)
            if panel and not panel.can_close(force=True):
                # Panel has unsaved work or other reason to prevent shutdown
                event.ignore()
                return

        # Cleanup status bar manager
        if self._statusbar_manager:
            self._statusbar_manager.cleanup()

        event.accept()
        logger.info("Main window closed")

    # Introspection for MCP tools

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for Claude MCP tools.

        Returns:
            Dictionary with window state and panel information.
        """
        data = {
            "window_title": self.windowTitle(),
            "geometry": {
                "x": self.x(),
                "y": self.y(),
                "width": self.width(),
                "height": self.height(),
            },
            "is_visible": self.isVisible(),
            "is_maximized": self.isMaximized(),
            "is_fullscreen": self.isFullScreen(),
            "open_panels": [
                {
                    "id": panel_id,
                    "title": dock.windowTitle(),
                    "visible": dock.isVisible(),
                    "floating": dock.isFloating(),
                }
                for panel_id, dock in self._panel_docks.items()
            ],
            "active_panel": self._active_panel_id,
            "available_panels": [
                meta.id
                for meta in self._panel_registry.list_available(
                    self._session_manager.current_user
                )
            ],
            "theme": self._theme_manager.theme.value,
            "user": self._session_manager.current_user.username,
            "auth_state": self._session_manager.state.name,
        }

        # Add status bar introspection
        if self._statusbar_manager:
            data["statusbar"] = self._statusbar_manager.get_introspection_data()

        return data
