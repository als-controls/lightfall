"""Plugin manifest definitions.

A manifest defines a collection of plugins from a single source (package).
Entry points in pyproject.toml point to manifest modules, allowing plugin
modifications without package reinstall.

Example manifest module::

    # my_beamline/manifest.py
    from lucid.plugins import PluginManifest, PluginEntry

    manifest = PluginManifest(
        name="my-beamline-plans",
        version="1.0.0",
        plugins=[
            PluginEntry("plan", "my_scan", "my_beamline.plans:MyScanPlan"),
            PluginEntry("plan", "my_align", "my_beamline.plans:MyAlignPlan"),
        ]
    )

Entry point in pyproject.toml::

    [project.entry-points."lucid.plugins"]
    my_beamline = "my_beamline.manifest:manifest"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PluginEntry:
    """Definition of a single plugin within a manifest.

    Attributes:
        type_name: The plugin type (e.g., "plan", "panel", "device").
        name: Unique plugin name within this type.
        import_path: Python import path in format "module.path:ClassName".
        metadata: Additional plugin-specific metadata (optional).
        preload: If True, load synchronously before main window creation.

    Example::

        PluginEntry(
            type_name="plan",
            name="my_scan",
            import_path="my_beamline.plans:MyScanPlan",
            metadata={"priority": 10},
        )

    Preload plugins are loaded before the main window is created,
    allowing them to apply settings (like theme) immediately::

        PluginEntry(
            type_name="settings",
            name="appearance",
            import_path="lucid.ui.preferences.builtin:AppearanceSettingsPlugin",
            preload=True,
        )
    """

    type_name: str
    name: str
    import_path: str
    metadata: dict[str, Any] = field(default_factory=dict)
    preload: bool = False

    @property
    def unique_id(self) -> str:
        """Unique identifier combining type and name."""
        return f"{self.type_name}:{self.name}"

    def __post_init__(self) -> None:
        """Validate the entry after initialization."""
        if not self.type_name:
            raise ValueError("type_name cannot be empty")
        if not self.name:
            raise ValueError("name cannot be empty")
        if not self.import_path:
            raise ValueError("import_path cannot be empty")
        if ":" not in self.import_path:
            raise ValueError(
                f"import_path must be in format 'module.path:ClassName', "
                f"got '{self.import_path}'"
            )


@dataclass
class PluginManifest:
    """A manifest defining a collection of plugins from one source.

    Entry points in pyproject.toml point to manifest modules. This allows
    plugins to be added, removed, or modified without reinstalling the
    package - simply edit the manifest module and restart the application.

    Attributes:
        name: Manifest name (e.g., "als-beamline-7.0.1.1").
        version: Manifest version string.
        description: Human-readable description of this plugin collection.
        plugins: List of plugin entries.
        metadata: Additional manifest metadata.

    Example::

        manifest = PluginManifest(
            name="beamline-7.0.1.1-plans",
            version="1.0.0",
            description="Custom plans for beamline 7.0.1.1",
            plugins=[
                PluginEntry("plan", "my_scan", "my_beamline.plans:MyScanPlan"),
                PluginEntry("plan", "my_align", "my_beamline.plans:MyAlignPlan"),
            ],
        )
    """

    name: str
    version: str = "0.0.0"
    description: str = ""
    plugins: list[PluginEntry] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate the manifest after initialization."""
        if not self.name:
            raise ValueError("Manifest name cannot be empty")

    def get_plugins_by_type(self, type_name: str) -> list[PluginEntry]:
        """Get all plugins of a specific type.

        Args:
            type_name: The plugin type to filter by.

        Returns:
            List of PluginEntry for that type.
        """
        return [p for p in self.plugins if p.type_name == type_name]

    def get_plugin_types(self) -> set[str]:
        """Get set of all plugin types in this manifest.

        Returns:
            Set of type names.
        """
        return {p.type_name for p in self.plugins}

    def __len__(self) -> int:
        """Return number of plugins in manifest."""
        return len(self.plugins)
