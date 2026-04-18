"""Status bar manager for NCS.

Manages the lifecycle of status bar indicator plugins, including
loading, positioning, and cleanup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import QStatusBar, QWidget

from lucid.utils.logging import logger

if TYPE_CHECKING:
    from lucid.plugins.info import PluginInfo
    from lucid.plugins.loader import PluginLoader
    from lucid.plugins.statusbar_plugin import StatusBarPlugin


class StatusBarManager:
    """Manages status bar indicator plugins.

    The StatusBarManager handles:
    - Loading plugins from the PluginRegistry
    - Subscribing to PluginLoader signals for dynamic plugin loading
    - Creating and positioning indicator widgets
    - Connecting/disconnecting signal handlers
    - Cleanup on shutdown

    Plugins are positioned by priority (lower = further left) and
    can be added to left, right, or permanent areas of the status bar.

    The manager uses an Observer pattern to receive notifications when
    statusbar plugins are loaded, allowing plugins to load in the background
    without requiring preload=True.

    Example:
        >>> statusbar = QStatusBar()
        >>> manager = StatusBarManager(statusbar, parent)
        >>> manager.load_plugins()
        >>> # Later, on cleanup:
        >>> manager.cleanup()
    """

    def __init__(
        self,
        statusbar: QStatusBar,
        parent: QWidget | None = None,
    ) -> None:
        """Initialize the status bar manager.

        Args:
            statusbar: The QStatusBar to manage.
            parent: Parent widget (typically the main window).
        """
        self._statusbar = statusbar
        self._parent = parent
        self._plugins: dict[str, StatusBarPlugin] = {}
        self._widgets: dict[str, QWidget] = {}
        self._loader: PluginLoader | None = None

    def load_plugins(self) -> int:
        """Load status bar plugins from the registry and subscribe to future loads.

        Discovers all registered statusbar plugins that are already loaded,
        creates their widgets, and adds them to the status bar. Also subscribes
        to the PluginLoader's plugin_loaded signal to handle plugins that load
        later in the background.

        Returns:
            Number of plugins successfully loaded (already-loaded plugins only).
        """
        from lucid.core import NCSApplication
        from lucid.plugins.loader import PluginLoader
        from lucid.plugins.registry import PluginRegistry

        app = NCSApplication.get_instance()
        services = app.services

        # Subscribe to PluginLoader signals for dynamic plugin loading
        try:
            self._loader = services.get(PluginLoader)
            self._loader.plugin_loaded.connect(self._on_plugin_loaded)
            logger.debug("Subscribed to PluginLoader.plugin_loaded signal")
        except Exception:
            logger.warning("PluginLoader not available, dynamic loading disabled")

        # Get registry and load any already-loaded statusbar plugins
        try:
            registry = services.get(PluginRegistry)
        except Exception:
            logger.warning("PluginRegistry not available, no status bar plugins loaded")
            return 0

        loaded = 0

        # Get all statusbar plugins
        plugin_infos = registry.get_by_type("statusbar")

        # Sort by priority (from metadata)
        sorted_infos = sorted(
            plugin_infos,
            key=lambda info: getattr(
                getattr(info.instance, "metadata", None),
                "priority",
                100,
            )
            if info.instance
            else 100,
        )

        for info in sorted_infos:
            if info.instance is None:
                logger.debug(
                    "Statusbar plugin {} not yet instantiated, will load via observer",
                    info.unique_id,
                )
                continue

            plugin = info.instance
            if self.add_plugin(plugin):
                loaded += 1

        logger.info("Loaded {} status bar plugins (more may load dynamically)", loaded)
        return loaded

    def _on_plugin_loaded(self, plugin_info: PluginInfo) -> None:
        """Handle plugin_loaded signal from PluginLoader.

        Filters for statusbar type plugins and adds them to the status bar.

        Args:
            plugin_info: Information about the loaded plugin.
        """
        if plugin_info.type_name != "statusbar":
            return

        if plugin_info.instance is None:
            logger.warning(
                "Statusbar plugin {} has no instance, skipping",
                plugin_info.unique_id,
            )
            return

        plugin_id = plugin_info.instance.metadata.id
        if plugin_id in self._plugins:
            logger.debug("Statusbar plugin {} already added", plugin_id)
            return

        logger.debug("Adding dynamically loaded statusbar plugin: {}", plugin_id)
        if self.add_plugin(plugin_info.instance):
            logger.info("Dynamically loaded status bar plugin: {}", plugin_id)

    def add_plugin(self, plugin: StatusBarPlugin) -> bool:
        """Add a plugin to the status bar.

        Creates the widget, connects signals, and adds to the status bar.

        Args:
            plugin: The StatusBarPlugin instance.

        Returns:
            True if successfully added.
        """
        plugin_id = plugin.metadata.id

        if plugin_id in self._plugins:
            logger.warning("Plugin {} already added to status bar", plugin_id)
            return False

        try:
            # Create widget
            widget = plugin.create_widget(self._statusbar)
            plugin._widget = widget

            # Connect signals
            plugin.connect_signals()

            # Initial update
            plugin.update()

            # Add to status bar based on position
            position = plugin.metadata.position
            if position == "left":
                self._statusbar.addWidget(widget)
            elif position == "right":
                self._statusbar.addWidget(widget)
                # Note: Qt doesn't have a true "right" for addWidget,
                # permanent widgets go to the right
            else:  # permanent (default)
                self._statusbar.addPermanentWidget(widget)

            # Track
            self._plugins[plugin_id] = plugin
            self._widgets[plugin_id] = widget

            logger.debug(
                "Added status bar plugin: {} (priority={})",
                plugin_id,
                plugin.metadata.priority,
            )
            return True

        except Exception as e:
            logger.error("Failed to add status bar plugin {}: {}", plugin_id, e)
            return False

    def remove_plugin(self, plugin_id: str) -> bool:
        """Remove a plugin from the status bar.

        Disconnects signals and removes the widget.

        Args:
            plugin_id: The plugin identifier.

        Returns:
            True if successfully removed.
        """
        plugin = self._plugins.pop(plugin_id, None)
        widget = self._widgets.pop(plugin_id, None)

        if plugin is None:
            return False

        try:
            # Disconnect signals
            plugin.disconnect_signals()

            # Remove widget from status bar
            if widget:
                self._statusbar.removeWidget(widget)
                widget.deleteLater()

            logger.debug("Removed status bar plugin: {}", plugin_id)
            return True

        except Exception as e:
            logger.error("Error removing status bar plugin {}: {}", plugin_id, e)
            return False

    def cleanup(self) -> None:
        """Clean up all plugins and disconnect signals.

        Should be called when the main window is closing.
        """
        # Disconnect from PluginLoader signal
        if self._loader is not None:
            try:
                self._loader.plugin_loaded.disconnect(self._on_plugin_loaded)
                logger.debug("Disconnected from PluginLoader.plugin_loaded signal")
            except (RuntimeError, TypeError):
                # Signal already disconnected or object deleted
                pass
            self._loader = None

        # Remove all plugins
        for plugin_id in list(self._plugins.keys()):
            self.remove_plugin(plugin_id)

        logger.debug("Status bar manager cleanup complete")

    def _rebuild_statusbar(self) -> None:
        """Rebuild the status bar with plugins in priority order.

        Called after adding/removing plugins to ensure correct ordering.
        """
        # Remove all widgets
        for widget in self._widgets.values():
            self._statusbar.removeWidget(widget)

        # Re-add in priority order
        sorted_plugins = sorted(
            self._plugins.values(),
            key=lambda p: p.metadata.priority,
        )

        for plugin in sorted_plugins:
            widget = self._widgets.get(plugin.metadata.id)
            if widget:
                position = plugin.metadata.position
                if position == "left":
                    self._statusbar.addWidget(widget)
                elif position == "right":
                    self._statusbar.addWidget(widget)
                else:
                    self._statusbar.addPermanentWidget(widget)

    def get_plugin(self, plugin_id: str) -> StatusBarPlugin | None:
        """Get a plugin by ID.

        Args:
            plugin_id: The plugin identifier.

        Returns:
            The plugin instance or None.
        """
        return self._plugins.get(plugin_id)

    def list_plugins(self) -> list[str]:
        """List all loaded plugin IDs.

        Returns:
            List of plugin identifiers.
        """
        return list(self._plugins.keys())

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools.

        Returns:
            Dictionary with status bar state and plugin information.
        """
        return {
            "plugin_count": len(self._plugins),
            "plugins": [
                plugin.get_introspection_data()
                for plugin in sorted(
                    self._plugins.values(),
                    key=lambda p: p.metadata.priority,
                )
            ],
        }
