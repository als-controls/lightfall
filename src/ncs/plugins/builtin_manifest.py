"""Built-in plugin manifest for NCS core plugins.

This manifest contains plugins that are part of the NCS core distribution.
It is loaded directly by the application, not via entry points.
"""

from __future__ import annotations

from ncs.plugins.manifest import PluginEntry, PluginManifest

builtin_manifest = PluginManifest(
    name="ncs.builtin",
    version="1.0.0",
    description="Built-in NCS plugins",
    plugins=[
        # Appearance settings - preload to apply theme before window
        PluginEntry(
            type_name="settings",
            name="appearance",
            import_path="ncs.ui.preferences.builtin:AppearanceSettingsPlugin",
            preload=True,
        ),
    ],
)
