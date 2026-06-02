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
        PluginEntry(
            type_name="theme",
            name="catppuccin_mocha",
            import_path="lucid.ui.theme.builtin:CatppuccinMochaThemePlugin",
            preload=True,
        ),
        PluginEntry(
            type_name="theme",
            name="eldritch",
            import_path="lucid.ui.theme.builtin:EldritchThemePlugin",
            preload=True,
        ),
        PluginEntry(
            type_name="theme",
            name="evangelion",
            import_path="lucid.ui.theme.builtin:EvangelionThemePlugin",
            preload=True,
        ),
        PluginEntry(
            type_name="theme",
            name="ayaka",
            import_path="lucid.ui.theme.builtin:AyakaThemePlugin",
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
        # User Profile settings (avatar + identity preview)
        PluginEntry(
            type_name="settings",
            name="user_profile",
            import_path="lucid.ui.preferences.user_profile_settings:UserProfileSettingsPlugin",
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
        # Claude agent plugin settings (enable/disable AgentPlugin instances)
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
        # Agent plugins (skill prompts and/or MCP tool bags).
        # Each contributes via AgentRegistry; per-plugin MCP servers are
        # assembled at agent-construction time in lucid/claude/agent.py.
        PluginEntry(
            type_name="agent",
            name="alignment",
            import_path="lucid.plugins.agents.alignment:BeamlineAlignmentAgent",
        ),
        PluginEntry(
            type_name="agent",
            name="plan_design",
            import_path="lucid.plugins.agents.plan_design:PlanDesignAgent",
        ),
        PluginEntry(
            type_name="agent",
            name="scan_planning",
            import_path="lucid.plugins.agents.scan_planning:ScanPlanningAgent",
        ),
        PluginEntry(
            type_name="agent",
            name="autonomous_experiment",
            import_path=(
                "lucid.plugins.agents.autonomous_experiment:"
                "AutonomousExperimentAgent"
            ),
        ),
        PluginEntry(
            type_name="agent",
            name="panel_design",
            import_path="lucid.plugins.agents.panel_design:PanelDesignAgent",
        ),
        PluginEntry(
            type_name="agent",
            name="panel_builder",
            import_path="lucid.plugins.agents.panel_builder:PanelBuilderAgent",
        ),
        PluginEntry(
            type_name="agent",
            name="device_tools",
            import_path="lucid.plugins.agents.device_tools:DeviceToolsAgent",
        ),
        PluginEntry(
            type_name="agent",
            name="plan_tools",
            import_path="lucid.plugins.agents.plan_tools:PlanToolsAgent",
        ),
        PluginEntry(
            type_name="agent",
            name="engine_tools",
            import_path="lucid.plugins.agents.engine_tools:EngineToolsAgent",
        ),
        PluginEntry(
            type_name="agent",
            name="ipython_tools",
            import_path="lucid.plugins.agents.ipython_tools:IPythonToolsAgent",
        ),
        PluginEntry(
            type_name="agent",
            name="ncs_core_tools",
            import_path="lucid.claude.ncs_core_tools:NCSCoreToolPlugin",
        ),
        # Current-ESAF skill. Beamline read from tiled_beamline preference;
        # strictly now-only (no date parameter). See plugin docstring.
        PluginEntry(
            type_name="agent",
            name="current_esaf",
            import_path="lucid.plugins.agents.current_esaf:CurrentEsafAgent",
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
        PluginEntry(
            type_name="panel",
            name="pipeline_jobs",
            import_path="lucid.ui.panels.plugins.pipeline_jobs_plugin:PipelineJobsPanelPlugin",
            preload=True,
        ),
        PluginEntry(
            type_name="panel",
            name="pipeline_triggers",
            import_path="lucid.ui.panels.plugins.pipeline_triggers_plugin:PipelineTriggersPanelPlugin",
            preload=True,
        ),
    ],
)
