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
        # Device settings
        PluginEntry(
            type_name="settings",
            name="devices",
            import_path="ncs.ui.preferences.device_settings:DeviceSettingsPlugin",
        ),
        # Tiled settings
        PluginEntry(
            type_name="settings",
            name="tiled",
            import_path="ncs.ui.preferences.tiled_settings:TiledSettingsPlugin",
        ),
        # Claude settings
        PluginEntry(
            type_name="settings",
            name="claude",
            import_path="ncs.ui.preferences.claude_settings:ClaudeSettingsPlugin",
        ),
        # Engine plugins
        PluginEntry(
            type_name="engine",
            name="bluesky",
            import_path="ncs.acquire.engine.plugins.bluesky_plugin:BlueskyEnginePlugin",
        ),
        PluginEntry(
            type_name="engine",
            name="mock",
            import_path="ncs.acquire.engine.plugins.mock_plugin:MockEnginePlugin",
        ),
        # Status bar plugins
        PluginEntry(
            type_name="statusbar",
            name="user_status",
            import_path="ncs.ui.statusbar.plugins.user_status:UserStatusPlugin",
        ),
        PluginEntry(
            type_name="statusbar",
            name="auth_status",
            import_path="ncs.ui.statusbar.plugins.auth_status:AuthStatusPlugin",
        ),
        PluginEntry(
            type_name="statusbar",
            name="connection_status",
            import_path="ncs.ui.statusbar.plugins.connection_status:ConnectionStatusPlugin",
        ),
        PluginEntry(
            type_name="statusbar",
            name="tiled_status",
            import_path="ncs.ui.statusbar.plugins.tiled_status:TiledStatusPlugin",
        ),
    ],
)
