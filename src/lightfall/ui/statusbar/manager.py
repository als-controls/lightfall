"""Status bar manager for NCS.

Manages the lifecycle of status bar indicator plugins, including
loading, positioning, and cleanup.

All plugin widgets render into a single manager-owned container that
spans the full width of the ``QStatusBar``. Inside the container, a
``QHBoxLayout`` holds the "left"-positioned plugins, then a stretch,
then the "right"/"permanent" plugins. Because the statusbar has only
one child widget there are no Qt per-item separator notches, and the
size grip is disabled so the far-right notch goes away too.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QSpacerItem, QStatusBar, QWidget

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
    - Rendering plugin widgets inside a single full-width container so
      Qt's per-item separator notches never appear, and disabling the
      size grip so there's no notch on the far right either
    - Connecting/disconnecting signal handlers
    - Reacting to per-plugin visibility changes
    - Cleanup on shutdown

    Plugins are positioned by priority (lower = further left). The
    ``position`` metadata field decides which side of the central
    stretch they sit on: "left" anchors to the left edge, "right" or
    "permanent" anchors to the right edge.

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

        # Quench the far-right size-grip notch.
        self._statusbar.setSizeGripEnabled(False)

        # A single container that spans the whole statusbar. With only one
        # child, the statusbar's per-item frame style never has a chance
        # to draw a notch between items, and there's no left/right boundary
        # frame either.
        self._container = QWidget(self._statusbar)
        self._layout = QHBoxLayout(self._container)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(ITEM_SPACING_PX)
        # Stretch=1 makes the container fill the full statusbar width so
        # the right-side plugins anchor to the actual right edge.
        self._statusbar.addWidget(self._container, 1)

        # Center stretch separating left and right plugin groups. Tracks
        # its layout index so insertions on either side stay sorted.
        self._stretch = QSpacerItem(
            0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )
        self._layout.addSpacerItem(self._stretch)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _stretch_index(self) -> int:
        """Current layout index of the center stretch spacer.

        We can't store a fixed index because inserts on either side shift
        it; ``indexOf`` is O(n) but n is tiny.
        """
        return self._layout.indexOf(self._stretch)

    @staticmethod
    def _side_for(plugin: StatusBarPlugin) -> str:
        """Map metadata.position to a side key."""
        return "left" if plugin.metadata.position == "left" else "right"

    def _insert_index(
        self, side: str, priority: int, exclude_id: str | None = None
    ) -> int:
        """Find the layout index to insert a new plugin so the side stays sorted.

        Lower priority sorts further left within each side. ``exclude_id``
        skips the plugin being inserted if it has already been recorded
        in :attr:`_plugins` / :attr:`_sides`.
        """
        stretch_idx = self._stretch_index()
        # Count peers on the same side with priority <= ours.
        peers_before = 0
        for pid, sd in self._sides.items():
            if sd != side or pid == exclude_id or pid not in self._plugins:
                continue
            if self._plugins[pid].metadata.priority <= priority:
                peers_before += 1
        if side == "left":
            # Left plugins live in [0, stretch_idx). Place after the peers.
            return peers_before
        # Right plugins live in (stretch_idx, end). Place after the peers.
        return stretch_idx + 1 + peers_before

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

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

        try:
            self._loader = services.get(PluginLoader)
            self._loader.plugin_loaded.connect(self._on_plugin_loaded)
            logger.debug("Subscribed to PluginLoader.plugin_loaded signal")
        except Exception:
            logger.warning("PluginLoader not available, dynamic loading disabled")

        try:
            registry = services.get(PluginRegistry)
        except Exception:
            logger.warning("PluginRegistry not available, no status bar plugins loaded")
            return 0

        loaded = 0

        plugin_infos = registry.get_by_type("statusbar")

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
        """Handle plugin_loaded signal from PluginLoader."""
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

    # ------------------------------------------------------------------
    # Add / remove
    # ------------------------------------------------------------------

    def add_plugin(self, plugin: StatusBarPlugin) -> bool:
        """Add a plugin to the status bar.

        Creates the widget, connects signals, and inserts it into the
        container at the position dictated by side + priority.

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

            # Track BEFORE create so _insert_index sees the new plugin's
            # bucket consistently if anything calls back during create.
            self._plugins[plugin_id] = plugin
            self._sides[plugin_id] = side

            widget = plugin.create_widget(self._container)
            # Backward-compat: custom create_widget overrides that don't
            # populate self._widget themselves still get tracked here.
            if plugin.widget is None:
                plugin._widget = widget

            widget.setVisible(plugin.is_visible)

            insert_idx = self._insert_index(
                side, plugin.metadata.priority, exclude_id=plugin_id
            )
            self._layout.insertWidget(insert_idx, widget)
            self._widgets[plugin_id] = widget

            try:
                plugin.visibility_changed.connect(self._on_visibility_changed)
            except (AttributeError, RuntimeError):
                pass

            plugin.connect_signals()
            plugin.update()

            logger.debug(
                "Added status bar plugin: {} (priority={}, side={})",
                plugin_id,
                plugin.metadata.priority,
                side,
            )
            return True

        except Exception as e:
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
        self._sides.pop(plugin_id, None)

        if plugin is None:
            return False

        try:
            try:
                plugin.visibility_changed.disconnect(self._on_visibility_changed)
            except (AttributeError, RuntimeError, TypeError):
                pass

            plugin.disconnect_signals()

            if widget is not None:
                self._layout.removeWidget(widget)
                widget.setParent(None)
                widget.deleteLater()

            logger.debug("Removed status bar plugin: {}", plugin_id)
            return True

        except Exception as e:
            logger.error("Error removing status bar plugin {}: {}", plugin_id, e)
            return False

    def _on_visibility_changed(self, _visible: bool) -> None:
        """Plugin visibility toggled.

        ``QHBoxLayout`` collapses hidden widgets automatically (size
        policy does not retain space when hidden), so the container
        reflows without any manual work. Hook kept for future extension.
        """

    # ------------------------------------------------------------------
    # Lifecycle / introspection
    # ------------------------------------------------------------------

    def cleanup(self) -> None:
        """Clean up all plugins and disconnect signals.

        Should be called when the main window is closing.
        """
        if self._loader is not None:
            try:
                self._loader.plugin_loaded.disconnect(self._on_plugin_loaded)
                logger.debug("Disconnected from PluginLoader.plugin_loaded signal")
            except (RuntimeError, TypeError):
                pass
            self._loader = None

        for plugin_id in list(self._plugins.keys()):
            self.remove_plugin(plugin_id)

        logger.debug("Status bar manager cleanup complete")

    def get_plugin(self, plugin_id: str) -> StatusBarPlugin | None:
        """Get a plugin by ID."""
        return self._plugins.get(plugin_id)

    def list_plugins(self) -> list[str]:
        """List all loaded plugin IDs."""
        return list(self._plugins.keys())

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools."""
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
