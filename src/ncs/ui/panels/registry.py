"""Panel registry for managing NCS panels.

The registry provides:
- Central catalog of available panel types
- Entry point discovery for plugin panels
- Panel instantiation with dependency injection
- Permission filtering based on user roles
- Introspection API for Claude MCP tools
"""

from __future__ import annotations

import threading
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any

from ncs.ui.panels.base import BasePanel, PanelMetadata
from ncs.utils.logging import logger

if TYPE_CHECKING:
    from ncs.auth.session import User


class PanelRegistry:
    """
    Central registry for NCS panels.

    PanelRegistry provides:
    - Registration of panel types (built-in and plugins)
    - Discovery via Python entry points (ncs.panels)
    - Panel instantiation with dependency injection
    - Permission filtering (panels filtered by user access)
    - Singleton management for singleton panels
    - Search and introspection for Claude MCP tools

    Entry Point Registration:
        Plugin panels can be registered via pyproject.toml:
        ```toml
        [project.entry-points."ncs.panels"]
        my_panel = "my_plugin.panels:MyPanel"
        ```

    Example:
        >>> registry = PanelRegistry.get_instance()
        >>> registry.register(MyPanel)
        >>> panel = registry.create("ncs.panels.my_panel")
        >>> available = registry.list_available(user)
    """

    _instance: PanelRegistry | None = None
    _lock = threading.RLock()

    def __init__(self) -> None:
        """Initialize the panel registry."""
        self._panel_types: dict[str, type[BasePanel]] = {}
        self._singleton_instances: dict[str, BasePanel] = {}
        self._discovered = False

    @classmethod
    def get_instance(cls) -> PanelRegistry:
        """Get the singleton PanelRegistry instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton instance (for testing)."""
        with cls._lock:
            cls._instance = None

    def register(
        self,
        panel_class: type[BasePanel],
        *,
        replace: bool = False,
    ) -> None:
        """Register a panel type.

        Args:
            panel_class: The panel class to register.
            replace: If True, replace existing registration.

        Raises:
            ValueError: If panel is already registered and replace is False.
        """
        panel_id = panel_class.panel_metadata.id

        if panel_id in self._panel_types and not replace:
            raise ValueError(f"Panel '{panel_id}' is already registered")

        self._panel_types[panel_id] = panel_class
        logger.debug(
            "Registered panel: {} ({})",
            panel_id,
            panel_class.__name__,
        )

    def unregister(self, panel_id: str) -> bool:
        """Unregister a panel type.

        Args:
            panel_id: The panel ID to unregister.

        Returns:
            True if the panel was registered.
        """
        if panel_id in self._panel_types:
            del self._panel_types[panel_id]
            # Also remove any singleton instance
            self._singleton_instances.pop(panel_id, None)
            logger.debug("Unregistered panel: {}", panel_id)
            return True
        return False

    def register_builtin_panels(self) -> int:
        """Register built-in NCS panels.

        Returns:
            Number of panels registered.
        """
        count = 0

        # Import built-in panels
        from ncs.ui.panels.bluesky_panel import BlueskyPanel
        from ncs.ui.panels.device_panel import DevicePanel
        from ncs.ui.panels.documents_panel import DocumentsPanel
        from ncs.ui.panels.logbook_panel import LogbookPanel

        builtin_panels = [
            LogbookPanel,
            DevicePanel,
            BlueskyPanel,
            DocumentsPanel,
        ]

        # Try to import Claude panel (requires pyside-claude)
        try:
            from ncs.ui.panels.claude_panel import ClaudePanel
            builtin_panels.append(ClaudePanel)
        except ImportError:
            logger.debug("Claude panel not available (pyside-claude not installed)")

        for panel_class in builtin_panels:
            try:
                self.register(panel_class, replace=True)
                count += 1
            except Exception as e:
                logger.error(
                    "Failed to register built-in panel {}: {}",
                    panel_class.__name__,
                    e,
                )

        logger.info("Registered {} built-in panels", count)
        return count

    def discover_plugins(self) -> int:
        """Discover and register panel plugins via entry points.

        Returns:
            Number of panels discovered.
        """
        if self._discovered:
            return 0

        # First register built-in panels
        self.register_builtin_panels()

        count = 0

        try:
            # Python 3.10+ style
            eps = entry_points(group="ncs.panels")
        except TypeError:
            # Fallback for older Python
            all_eps = entry_points()
            eps = all_eps.get("ncs.panels", [])

        for ep in eps:
            try:
                panel_class = ep.load()
                if issubclass(panel_class, BasePanel):
                    self.register(panel_class, replace=True)
                    count += 1
                    logger.info("Discovered plugin panel: {} ({})", ep.name, ep.value)
                else:
                    logger.warning(
                        "Entry point {} does not point to a BasePanel subclass",
                        ep.name,
                    )
            except Exception as e:
                logger.error("Failed to load panel plugin {}: {}", ep.name, e)

        self._discovered = True
        return count

    def get(self, panel_id: str) -> type[BasePanel] | None:
        """Get a panel class by ID.

        Args:
            panel_id: The panel identifier.

        Returns:
            The panel class or None if not found.
        """
        return self._panel_types.get(panel_id)

    def get_metadata(self, panel_id: str) -> PanelMetadata | None:
        """Get metadata for a panel type.

        Args:
            panel_id: The panel identifier.

        Returns:
            The panel metadata or None if not found.
        """
        panel_class = self._panel_types.get(panel_id)
        if panel_class:
            return panel_class.panel_metadata
        return None

    def create(
        self,
        panel_id: str,
        *,
        parent: Any = None,
        **kwargs: Any,
    ) -> BasePanel | None:
        """Create a panel instance.

        For singleton panels, returns the existing instance if one exists.

        Args:
            panel_id: The panel identifier.
            parent: Parent widget.
            **kwargs: Additional arguments passed to panel constructor.

        Returns:
            The panel instance or None if panel type not found.
        """
        panel_class = self._panel_types.get(panel_id)
        if panel_class is None:
            logger.warning("Unknown panel type: {}", panel_id)
            return None

        # Check for singleton
        if panel_class.panel_metadata.singleton:
            if panel_id in self._singleton_instances:
                existing = self._singleton_instances[panel_id]
                # Re-parent if needed
                if parent and existing.parent() != parent:
                    existing.setParent(parent)
                return existing

        # Create new instance
        try:
            panel = panel_class(parent=parent, **kwargs)

            # Store singleton
            if panel_class.panel_metadata.singleton:
                self._singleton_instances[panel_id] = panel

            logger.debug("Created panel instance: {}", panel_id)
            return panel

        except Exception as e:
            logger.error("Failed to create panel {}: {}", panel_id, e)
            return None

    def get_singleton(self, panel_id: str) -> BasePanel | None:
        """Get existing singleton instance without creating.

        Args:
            panel_id: The panel identifier.

        Returns:
            The singleton instance or None.
        """
        return self._singleton_instances.get(panel_id)

    def destroy_singleton(self, panel_id: str) -> bool:
        """Destroy a singleton panel instance.

        Args:
            panel_id: The panel identifier.

        Returns:
            True if a singleton was destroyed.
        """
        panel = self._singleton_instances.pop(panel_id, None)
        if panel:
            panel.close()
            panel.deleteLater()
            return True
        return False

    def list_all(self) -> list[PanelMetadata]:
        """List all registered panel metadata.

        Returns:
            List of panel metadata.
        """
        return [cls.panel_metadata for cls in self._panel_types.values()]

    def list_available(self, user: User) -> list[PanelMetadata]:
        """List panels available to a specific user.

        Filters panels based on required permissions.

        Args:
            user: The user to check permissions for.

        Returns:
            List of accessible panel metadata.
        """
        available = []
        for panel_class in self._panel_types.values():
            if panel_class.check_access(user):
                available.append(panel_class.panel_metadata)
        return available

    def list_by_category(
        self,
        user: User | None = None,
    ) -> dict[str, list[PanelMetadata]]:
        """List panels grouped by category.

        Args:
            user: Optional user for permission filtering.

        Returns:
            Dictionary mapping category to list of panels.
        """
        if user:
            panels = self.list_available(user)
        else:
            panels = self.list_all()

        by_category: dict[str, list[PanelMetadata]] = {}
        for meta in panels:
            if meta.category not in by_category:
                by_category[meta.category] = []
            by_category[meta.category].append(meta)

        return by_category

    def search(
        self,
        query: str,
        user: User | None = None,
    ) -> list[PanelMetadata]:
        """Search for panels matching a query.

        Args:
            query: Search string.
            user: Optional user for permission filtering.

        Returns:
            List of matching panel metadata.
        """
        if user:
            panels = self.list_available(user)
        else:
            panels = self.list_all()

        return [meta for meta in panels if meta.matches_search(query)]

    # Introspection API for Claude MCP tools

    def get_introspection_data(self, user: User | None = None) -> dict[str, Any]:
        """Get comprehensive introspection data for Claude MCP tools.

        Args:
            user: Optional user for permission filtering.

        Returns:
            Dictionary with registry information.
        """
        if user:
            available_panels = self.list_available(user)
        else:
            available_panels = self.list_all()

        return {
            "total_registered": len(self._panel_types),
            "available_to_user": len(available_panels),
            "categories": list(self.list_by_category(user).keys()),
            "panels": [
                {
                    "id": meta.id,
                    "name": meta.name,
                    "description": meta.description,
                    "category": meta.category,
                    "singleton": meta.singleton,
                    "has_instance": meta.id in self._singleton_instances,
                }
                for meta in available_panels
            ],
            "active_singletons": list(self._singleton_instances.keys()),
        }

    def get_panel_introspection(self, panel_id: str) -> dict[str, Any] | None:
        """Get introspection data for a specific panel.

        Args:
            panel_id: The panel identifier.

        Returns:
            Panel introspection data or None if not found.
        """
        panel_class = self._panel_types.get(panel_id)
        if panel_class is None:
            return None

        data = panel_class.get_class_introspection_data()

        # Add instance data if singleton exists
        instance = self._singleton_instances.get(panel_id)
        if instance:
            data["instance"] = instance.get_introspection_data()

        return data

    def list_panel_ids(self) -> list[str]:
        """Get list of all registered panel IDs.

        Returns:
            List of panel identifiers.
        """
        return list(self._panel_types.keys())
