"""Status bar manager for NCS.

Manages the lifecycle of status bar indicator plugins, including
loading, positioning, and cleanup.

Plugins are rendered into a manager-owned container with a
``QHBoxLayout`` (one for the left side, one for the right). The
layout supplies a small fixed gap between adjacent indicators, so
there are no Qt-style separator notches and hidden plugins close up
naturally.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import QHBoxLayout, QStatusBar, QWidget

from lucid.utils.logging import logger

if TYPE_CHECKING:
    from lucid.plugins.info import PluginInfo
    from lucid.plugins.loader import PluginLoader
    from lucid.plugins.statusbar_plugin import StatusBarPlugin


# Spacing between adjacent status bar entries (pixels). Small enough to
# read as related items, large enough to distinguish them without a glyph.
ITEM_SPACING_PX = 4


class StatusBarManager:
    """Manages status bar indicator plugins.

    The StatusBarManager handles:
    - Loading plugins from the PluginRegistry
    - Subscribing to PluginLoader signals for dynamic plugin loading
    - Creating and positioning indicator widgets inside left/right
      container layouts (no Qt separator notches between items)
    - Connecting/disconnecting signal handlers
    - Reacting to per-plugin visibility changes
    - Cleanup on shutdown

    Plugins are positioned by priority (lower = further left) and
    can be added to the left or right (permanent) area of the status bar.

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
        self._sides: dict[str, str] = {}  # plugin_id -> "left" | "right"
        self._loader: PluginLoader | None = None

        # Build the two container widgets we own. All plugin widgets
        # live inside one of these — never directly on the QStatusBar —
        # so we get full control over inter-item spacing and the absence
        # of Qt's per-item separator notches.
        self._left_container, self._left_layout = self._make_side_container()
        self._right_container, self._right_layout = self._make_side_container()
        self._statusbar.addWidget(self._left_container)
        self._statusbar.addPermanentWidget(self._right_container)

    @staticmethod
    def _make_side_container() -> tuple[QWidget, QHBoxLayout]:
        """Create one side container with a tight horizontal layout."""
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(ITEM_SPACING_PX)
        return container, layout

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

    @staticmethod
    def _side_for(plugin: StatusBarPlugin) -> str:
        """Map metadata.position to a side container key."""
        position = plugin.metadata.position
        return "left" if position == "left" else "right"

    def _layout_for(self, side: str) -> QHBoxLayout:
        return self._left_layout if side == "left" else self._right_layout

    def _insert_index(self, side: str, priority: int, exclude_id: str | None = None) -> int:
        """Find the layout index to insert a new plugin so the side stays sorted.

        Lower priority sorts further left within each side. ``exclude_id``
        skips the plugin being inserted if it has already been recorded
        in :attr:`_plugins` / :attr:`_sides`.
        """
        index = 0
        for pid, sd in self._sides.items():
            if sd != side or pid == exclude_id or pid not in self._plugins:
                continue
            if self._plugins[pid].metadata.priority <= priority:
                index += 1
        return index

    def add_plugin(self, plugin: StatusBarPlugin) -> bool:
        """Add a plugin to the status bar.

        Creates the widget, connects signals, and inserts the widget into
        the appropriate side container at the position dictated by its
        priority.

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
            side = self._side_for(plugin)
            container = self._left_container if side == "left" else self._right_container
            layout = self._layout_for(side)

            # Track BEFORE creating widget so _insert_index sees the new plugin's
            # priority bucket consistently if anything calls back during create.
            self._plugins[plugin_id] = plugin
            self._sides[plugin_id] = side

            # Create widget parented to the side container.
            widget = plugin.create_widget(container)
            # Backward-compat: some custom create_widget overrides don't set
            # self._widget themselves; make sure it points at what they returned.
            if plugin.widget is None:
                plugin._widget = widget

            # Honour any pre-set visibility (default True).
            widget.setVisible(plugin.is_visible)

            # Insert at the correct priority slot.
            insert_idx = self._insert_index(
                side, plugin.metadata.priority, exclude_id=plugin_id
            )
            layout.insertWidget(insert_idx, widget)

            # Track widget
            self._widgets[plugin_id] = widget

            # React to per-plugin visibility toggles.
            try:
                plugin.visibility_changed.connect(self._on_visibility_changed)
            except (AttributeError, RuntimeError):
                # Older plugins without the signal — they just call setVisible
                # on their widget directly; layout still collapses fine.
                pass

            # Connect signals
            plugin.connect_signals()

            # Initial update
            plugin.update()

            logger.debug(
                "Added status bar plugin: {} (priority={}, side={})",
                plugin_id,
                plugin.metadata.priority,
                side,
            )
            return True

        except Exception as e:
            # Roll back tracking on failure
            self._plugins.pop(plugin_id, None)
            self._sides.pop(plugin_id, None)
            self._widgets.pop(plugin_id, None)
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
        side = self._sides.pop(plugin_id, None)

        if plugin is None:
            return False

        try:
            try:
                plugin.visibility_changed.disconnect(self._on_visibility_changed)
            except (AttributeError, RuntimeError, TypeError):
                pass

            plugin.disconnect_signals()

            if widget is not None and side is not None:
                self._layout_for(side).removeWidget(widget)
                widget.setParent(None)
                widget.deleteLater()

            logger.debug("Removed status bar plugin: {}", plugin_id)
            return True

        except Exception as e:
            logger.error("Error removing status bar plugin {}: {}", plugin_id, e)
            return False

    def _on_visibility_changed(self, _visible: bool) -> None:
        """Handle a plugin's visibility toggle.

        QHBoxLayout collapses hidden widgets automatically (size policy
        does not retain space when hidden), so the side container reflows
        without any manual layout work — this slot exists mainly as a
        hook for future logic (e.g. logging, emitting an aggregated
        signal).
        """
        # No layout work needed — kept for future extension.

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
