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
    QMainWindow,
    QStatusBar,
    QToolBar,
    QWidget,
)

from lucid.auth.session import AuthState, SessionManager
from lucid.ui.docking import DockingManager
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
        self._docking_manager: DockingManager | None = None
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
        """Setup the central widget area with advanced docking."""
        # Initialize the docking manager (replaces QDockWidget system)
        self._docking_manager = DockingManager(self)
        self._docking_manager.initialize()

        # Connect docking signals
        self._docking_manager.panel_focused.connect(self._on_panel_focused)

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
        area: str | None = None,
        *,
        add_sidebar_button: bool = True,
    ) -> BasePanel | None:
        """Add a panel to the window.

        Panels are routed based on their area:
        - "left" → Left dock area with sidebar icon (exclusive)
        - "bottom" → Bottom dock area with sidebar icon (exclusive)
        - "center" → Center dock area, always visible

        Args:
            panel_id: Panel identifier.
            area: Dock area ("left", "bottom", "center").
                Defaults to panel's default_area metadata.
            add_sidebar_button: Whether to add sidebar button immediately.
                Set False to control sidebar icon order separately.

        Returns:
            The panel instance or None if failed.
        """
        if self._docking_manager is None:
            logger.error("Docking manager not initialized")
            return None

        # Check if already open
        existing = self._docking_manager.get_panel(panel_id)
        if existing is not None:
            self._docking_manager.show_panel(panel_id)
            return existing

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

        # Add via docking manager
        dock_widget = self._docking_manager.add_panel(
            panel_id, panel, area=area, add_sidebar_button=add_sidebar_button
        )

        if dock_widget is None:
            logger.warning("Failed to add panel to docking system: {}", panel_id)
            return None

        logger.debug("Added panel to window: {}", panel_id)
        return panel

    def setup_default_layout(self) -> None:
        """Setup the default panel layout with icon strip sidebar.

        Layout:
        - Sidebar top icons: Bluesky, Devices (dock to left)
        - Center (always visible): Logbook
        - Sidebar bottom icons: Claude, Documents, Logging, Synoptic (dock to bottom)

        Also clears saved state and prevents showEvent from restoring.
        """
        # Clear any saved state
        from PySide6.QtCore import QSettings
        settings = QSettings("ALS", "NCS")
        settings.remove("mainwindow/geometry")
        settings.remove("mainwindow/state")

        if self._docking_manager:
            self._docking_manager.clear_state(settings)

        # === Dock Layout Order (determines which area spans full width) ===
        # 1. Center panel first (establishes main dock area)
        self.add_panel("lucid.panels.logbook")

        # 2. Bottom panels SECOND (so bottom area spans full width)
        #    Add to dock WITHOUT sidebar buttons - we'll add buttons later
        bottom_panels = [
            "lucid.panels.claude",
            "lucid.panels.logging",
            "lucid.panels.synoptic",
        ]
        for panel_id in bottom_panels:
            self.add_panel(panel_id, add_sidebar_button=False)

        # 3. Left panels THIRD (left area is above bottom, not full height)
        #    Add to dock WITH sidebar buttons (these are the top icons)
        for panel_id in [
            "lucid.panels.bluesky",
            "lucid.panels.devices",
            "lucid.panels.documents",
        ]:
            self.add_panel(panel_id)

        # === Sidebar Icon Order ===
        # Add stretch to separate top and bottom icons
        if self._docking_manager:
            self._docking_manager.add_sidebar_stretch()

            # Now add sidebar buttons for bottom panels (bottom icons)
            for panel_id in bottom_panels:
                self._docking_manager.add_sidebar_button(panel_id)

        self._default_layout_applied = True
        logger.info("Applied default panel layout")

    def remove_panel(self, panel_id: str) -> bool:
        """Remove a panel from the window.

        Args:
            panel_id: Panel identifier.

        Returns:
            True if panel was removed.
        """
        if self._docking_manager is None:
            return False

        return self._docking_manager.remove_panel(panel_id)

    def get_panel(self, panel_id: str) -> BasePanel | None:
        """Get a panel by ID.

        Args:
            panel_id: Panel identifier.

        Returns:
            The panel instance or None.
        """
        if self._docking_manager is None:
            return None
        return self._docking_manager.get_panel(panel_id)

    def list_open_panels(self) -> list[str]:
        """Get list of open panel IDs.

        Returns:
            List of panel identifiers.
        """
        if self._docking_manager is None:
            return []
        return self._docking_manager.list_panels()

    def activate_panel(self, panel_id: str) -> bool:
        """Activate (focus) a panel.

        Args:
            panel_id: Panel identifier.

        Returns:
            True if panel was activated.
        """
        if self._docking_manager is None:
            return False

        return self._docking_manager.show_panel(panel_id)

    def toggle_panel(self, panel_id: str) -> bool:
        """Toggle panel visibility (PyCharm-like behavior).

        Click active panel icon → hide it
        Click inactive panel icon → show and focus it

        Args:
            panel_id: Panel identifier.

        Returns:
            True if toggle was successful.
        """
        if self._docking_manager is None:
            return False

        return self._docking_manager.toggle_panel(panel_id)

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

    def _set_theme(self, theme: Theme, *, save_preference: bool = True) -> None:
        """Set the application theme.

        Args:
            theme: Theme to apply.
            save_preference: If True, persist the theme to preferences.
                Set to False when called from preference_changed handler
                to avoid infinite recursion.
        """
        self._theme_manager.set_theme(theme)
        if save_preference:
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
                # Don't save preference - it was already saved, that's why we got this signal
                self._set_theme(Theme(value), save_preference=False)
            except ValueError:
                pass

    def _on_panel_focused(self, panel_id: str) -> None:
        """Handle panel focus change from docking manager."""
        self.panel_activated.emit(panel_id)

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
        from lucid.ui.dialogs import show_about_dialog

        show_about_dialog(self)

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
        from PySide6.QtCore import QSettings

        settings = QSettings("ALS", "NCS")
        settings.setValue("mainwindow/geometry", self.saveGeometry())

        # Save docking state
        if self._docking_manager:
            self._docking_manager.save_state(settings)

        self.statusBar().showMessage("Layout saved", 3000)

    def _restore_window_state(self) -> bool:
        """Restore window state from preferences.

        Returns:
            True if state was restored successfully.
        """
        from PySide6.QtCore import QSettings

        settings = QSettings("ALS", "NCS")

        # Restore geometry
        geometry = settings.value("mainwindow/geometry")
        if geometry:
            self.restoreGeometry(geometry)

        # Restore docking state
        docking_restored = False
        if self._docking_manager:
            docking_restored = self._docking_manager.restore_state(settings)

        if geometry or docking_restored:
            self.statusBar().showMessage("Layout restored", 3000)
            return True
        else:
            self.statusBar().showMessage("No saved layout found", 3000)
            return False

    # Lifecycle

    def showEvent(self, event) -> None:
        """Handle show event."""
        super().showEvent(event)

        # Only restore state on the first show - subsequent shows should not
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
            self._restore_window_state()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Handle close event."""
        self.about_to_close.emit()

        # Save window state if preference set
        if self._config_manager and self._config_manager.get("ui.remember_geometry", True):
            self._save_window_state()

        # Check if any panel has unsaved work (force=True to ignore closable flag)
        for panel_id in self.list_open_panels():
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

        # Add docking introspection
        if self._docking_manager:
            docking_data = self._docking_manager.get_introspection_data()
            data["open_panels"] = docking_data.get("panels", [])
            data["active_panel"] = docking_data.get("active_panel")
            data["sidebar_groups"] = docking_data.get("sidebar_groups", {})
        else:
            data["open_panels"] = []
            data["active_panel"] = None
            data["sidebar_groups"] = {}

        # Add status bar introspection
        if self._statusbar_manager:
            data["statusbar"] = self._statusbar_manager.get_introspection_data()

        return data
