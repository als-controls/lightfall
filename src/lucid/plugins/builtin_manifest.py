"""Built-in plugin manifest for NCS core plugins.

This manifest contains plugins that are part of the NCS core distribution.
It is loaded directly by the application, not via entry points.
"""

from __future__ import annotations

from lucid.plugins.manifest import PluginEntry, PluginManifest

builtin_manifest = PluginManifest(
    name="lucid.builtin",
    version="1.0.0",
    description="Built-in NCS plugins",
    plugins=[
        # Theme plugins - preload before appearance settings
        PluginEntry(
            type_name="theme",
            name="light",
            import_path="lucid.ui.theme.builtin:LightThemePlugin",
            preload=True,
        ),
        PluginEntry(
            type_name="theme",
            name="slate",
            import_path="lucid.ui.theme.builtin:SlateThemePlugin",
            preload=True,
        ),
        PluginEntry(
            type_name="theme",
            name="darkblue",
            import_path="lucid.ui.theme.builtin:DarkBlueThemePlugin",
            preload=True,
        ),
        PluginEntry(
            type_name="theme",
            name="islands",
            import_path="lucid.ui.theme.builtin:IslandsThemePlugin",
            preload=True,
        ),
        # Appearance settings - preload to apply theme before window
        PluginEntry(
            type_name="settings",
            name="appearance",
            import_path="lucid.ui.preferences.builtin:AppearanceSettingsPlugin",
            preload=True,
        ),
        # Network proxy settings - preload to configure WebEngine before any QWebEngineView
        PluginEntry(
            type_name="settings",
            name="proxy",
            import_path="lucid.ui.preferences.proxy_settings:ProxySettingsPlugin",
            preload=True,
        ),
        # Login & Session settings
        PluginEntry(
            type_name="settings",
            name="login",
            import_path="lucid.ui.preferences.login_settings:LoginSettingsPlugin",
        ),
        # External tools settings (for code navigation)
        PluginEntry(
            type_name="settings",
            name="external_tools",
            import_path="lucid.ui.preferences.editor_settings:ExternalToolsSettingsPlugin",
        ),
        # Device settings
        PluginEntry(
            type_name="settings",
            name="devices",
            import_path="lucid.ui.preferences.device_settings:DeviceSettingsPlugin",
        ),
        # Tiled settings
        PluginEntry(
            type_name="settings",
            name="tiled",
            import_path="lucid.ui.preferences.tiled_settings:TiledSettingsPlugin",
        ),
        # Logbook backend settings
        PluginEntry(
            type_name="settings",
            name="logbook",
            import_path="lucid.ui.preferences.logbook_settings:LogbookSettingsPlugin",
        ),
        # IPC settings
        PluginEntry(
            type_name="settings",
            name="ipc",
            import_path="lucid.ui.preferences.ipc_settings:IPCSettingsPlugin",
        ),
        # Claude settings
        PluginEntry(
            type_name="settings",
            name="claude",
            import_path="lucid.ui.preferences.claude_settings:ClaudeSettingsPlugin",
        ),
        # Claude tools settings (includes both tool plugins and skills)
        PluginEntry(
            type_name="settings",
            name="claude_tools",
            import_path="lucid.ui.preferences.tool_settings:ClaudeToolsSettingsPlugin",
        ),
        # Plugin management settings
        PluginEntry(
            type_name="settings",
            name="plugins",
            import_path="lucid.ui.preferences.plugin_settings:PluginSettingsPlugin",
        ),
        # Visualization settings
        PluginEntry(
            type_name="settings",
            name="visualization",
            import_path="lucid.ui.preferences.visualization_settings:VisualizationSettingsPlugin",
        ),
        # Engine plugins
        PluginEntry(
            type_name="engine",
            name="bluesky",
            import_path="lucid.acquire.engine.plugins.bluesky_plugin:BlueskyEnginePlugin",
        ),
        PluginEntry(
            type_name="engine",
            name="mock",
            import_path="lucid.acquire.engine.plugins.mock_plugin:MockEnginePlugin",
        ),
        # Status bar plugins - loaded dynamically via observer pattern
        # StatusBarManager subscribes to PluginLoader.plugin_loaded signal
        PluginEntry(
            type_name="statusbar",
            name="user_status",
            import_path="lucid.ui.statusbar.plugins.user_status:UserStatusPlugin",
        ),
        PluginEntry(
            type_name="statusbar",
            name="auth_status",
            import_path="lucid.ui.statusbar.plugins.auth_status:AuthStatusPlugin",
        ),
        PluginEntry(
            type_name="statusbar",
            name="connection_status",
            import_path="lucid.ui.statusbar.plugins.connection_status:ConnectionStatusPlugin",
        ),
        PluginEntry(
            type_name="statusbar",
            name="tiled_status",
            import_path="lucid.ui.statusbar.plugins.tiled_status:TiledStatusPlugin",
        ),
        PluginEntry(
            type_name="statusbar",
            name="als_beam_status",
            import_path="lucid.ui.statusbar.plugins.als_beam_status:ALSBeamStatusPlugin",
        ),
        PluginEntry(
            type_name="statusbar",
            name="thread_status",
            import_path="lucid.ui.statusbar.plugins.thread_status:ThreadStatusPlugin",
        ),
        # MCP Tool plugins - loaded during background loading
        # Claude panel collects tools when opened (also not preloaded)
        PluginEntry(
            type_name="mcp_tool",
            name="device_tools",
            import_path="lucid.plugins.tools.device_tools:DeviceToolPlugin",
        ),
        PluginEntry(
            type_name="mcp_tool",
            name="plan_tools",
            import_path="lucid.plugins.tools.plan_tools:PlanToolPlugin",
        ),
        PluginEntry(
            type_name="mcp_tool",
            name="engine_tools",
            import_path="lucid.plugins.tools.engine_tools:EngineToolPlugin",
        ),
        PluginEntry(
            type_name="mcp_tool",
            name="ipython_tools",
            import_path="lucid.plugins.tools.ipython_tools:IPythonToolPlugin",
        ),
        PluginEntry(
            type_name="mcp_tool",
            name="skill_docs",
            import_path="lucid.plugins.tools.skill_docs_tool:SkillDocsToolPlugin",
        ),
        # Skill plugins - loaded during background loading
        PluginEntry(
            type_name="skill",
            name="alignment",
            import_path="lucid.plugins.skills.alignment:BeamlineAlignmentSkill",
        ),
        PluginEntry(
            type_name="skill",
            name="plan_design",
            import_path="lucid.plugins.skills.plan_design:PlanDesignSkill",
        ),
        PluginEntry(
            type_name="skill",
            name="scan_planning",
            import_path="lucid.plugins.skills.scan_planning:ScanPlanningSkill",
        ),
        PluginEntry(
            type_name="skill",
            name="panel_design",
            import_path="lucid.plugins.skills.panel_design:PanelDesignSkill",
        ),
        PluginEntry(
            type_name="skill",
            name="panel_builder",
            import_path="lucid.plugins.skills.panel_builder:PanelBuilderSkill",
        ),
        # Panel plugins - preload to register with PanelRegistry before main window
        PluginEntry(
            type_name="panel",
            name="logbook",
            import_path="lucid.ui.panels.plugins.logbook_plugin:LogbookPanelPlugin",
            preload=True,
        ),
        PluginEntry(
            type_name="panel",
            name="logbook_entries",
            import_path="lucid.ui.panels.plugins.logbook_entries_plugin:LogbookEntriesPanelPlugin",
            preload=True,
        ),
        PluginEntry(
            type_name="panel",
            name="devices",
            import_path="lucid.ui.panels.plugins.device_plugin:DevicePanelPlugin",
            preload=True,
        ),
        PluginEntry(
            type_name="panel",
            name="bluesky",
            import_path="lucid.ui.panels.plugins.bluesky_plugin:BlueskyPanelPlugin",
            preload=True,
        ),
        PluginEntry(
            type_name="panel",
            name="queue",
            import_path="lucid.ui.panels.plugins.queue_plugin:QueuePanelPlugin",
            preload=True,
        ),
        PluginEntry(
            type_name="panel",
            name="documents",
            import_path="lucid.ui.panels.plugins.documents_plugin:DocumentsPanelPlugin",
            preload=True,
        ),
        PluginEntry(
            type_name="panel",
            name="claude",
            import_path="lucid.ui.panels.plugins.claude_plugin:ClaudePanelPlugin",
            preload=True,  # Preload for metadata; panel instantiation is deferred until clicked
        ),
        PluginEntry(
            type_name="panel",
            name="threads",
            import_path="lucid.ui.panels.plugins.threads_plugin:ThreadsPanelPlugin",
            preload=True,
        ),
        PluginEntry(
            type_name="panel",
            name="logging",
            import_path="lucid.ui.panels.plugins.logging_plugin:LoggingPanelPlugin",
            preload=True,
        ),
        PluginEntry(
            type_name="panel",
            name="tiled_browser",
            import_path="lucid.ui.panels.plugins.tiled_browser_plugin:TiledBrowserPanelPlugin",
            preload=True,
        ),
        PluginEntry(
            type_name="panel",
            name="ipython",
            import_path="lucid.ui.panels.plugins.ipython_plugin:IPythonPanelPlugin",
            preload=True,
        ),
        PluginEntry(
            type_name="panel",
            name="synoptic",
            import_path="lucid.ui.panels.plugins.synoptic_plugin:SynopticPanelPlugin",
            preload=True,
        ),
        PluginEntry(
            type_name="panel",
            name="visualization",
            import_path="lucid.ui.panels.plugins.visualization_plugin:VisualizationPanelPlugin",
            preload=True,
        ),
    ],
)
