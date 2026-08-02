---
name: panel-design
description: Expertise in designing Lightfall panel plugins with self-registration
---

## Lightfall Panel Design

Expert at designing Qt panel plugins for Lightfall with self-registration.

**See `references/panel_design.md` (loaded automatically by the SDK Skill tool)** for full API reference covering:
- `PanelMetadata` - id, name, category, docking preferences, proactive_init
- `BasePanel` lifecycle - _setup_ui(), signals, state management
- Status indicator - `PanelStatus` + `set_status()` tint the sidebar icon (health at a glance)
- Title bar toolbar - `add_title_bar_button()` for high-level panel actions
- MCP introspection - _get_specific_introspection_data(), actions
- User-plugin auto-registration via `PluginType.__init_subclass__`
- Qt widgets and layout patterns

Key imports: `from lightfall.ui.panels.base import BasePanel, PanelMetadata`
