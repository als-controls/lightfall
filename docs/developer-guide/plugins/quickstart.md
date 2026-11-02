# Plugin Quickstart

Create your first Lightfall plugin in 5 minutes. This guide walks through creating a minimal settings plugin that adds a preferences page.

## Prerequisites

- Lightfall installed in development mode
- Basic Python and Qt knowledge

## Step 1: Create the Plugin Class

Create a new file `my_settings_plugin.py`. A settings page is a *form page*:
you declare its settings and the Preferences dialog builds the controls,
shows them per scope and saves only what changed.

```python
"""My first Lightfall plugin."""

from lightfall_settings import SettingSpec

from lightfall.plugins.settings_plugin import SettingsPlugin
from lightfall.ui.preferences.form.schema import Action, Section, SettingField, Text, Toggle


class MySettingsPlugin(SettingsPlugin):
    """A minimal form page: two settings and a button."""

    @property
    def name(self) -> str:
        return "my_settings"

    @property
    def display_name(self) -> str:
        return "My Settings"

    def specs(self):
        # Keys this page introduces, and which scopes may set them.
        return (
            SettingSpec(key="my.greeting", type=str, scopes={"user"}),
            SettingSpec(key="my.enabled", type=bool, default=True, scopes={"beamline", "user"}),
        )

    def sections(self):
        return [
            Section("My Settings", [
                SettingField("my.greeting", Text(blank_is_unset=True), label="Greeting",
                             placeholder="Hello"),
                SettingField("my.enabled", Toggle(), label="Enabled"),
                Action("Say it", run=self._say),
            ]),
        ]

    def _say(self, values) -> str:
        # Runs on a worker thread; the returned text is shown next to the button.
        return values.effective("my.greeting") or "Hello"
```

## Step 2: Register in the Built-in Manifest

For development, the easiest approach is to add your plugin to the built-in manifest. Edit `src/lightfall/plugins/builtin_manifest.py`:

```python
# Add to the plugins list:
PluginEntry(
    type_name="settings",
    name="my_settings",
    import_path="path.to.my_settings_plugin:MySettingsPlugin",
),
```

Replace `path.to.my_settings_plugin` with the actual import path to your file.

## Step 3: Test Your Plugin

1. Run Lightfall:
   ```bash
   lightfall
   ```

2. Open Preferences (Ctrl+, or File > Preferences)

3. Your "My Settings" page should appear in the sidebar

## What's Happening

1. On startup, `PluginLoader` processes the built-in manifest
2. It finds your `PluginEntry` and queues it for loading
3. Your plugin class is imported and instantiated
4. Its specs are registered, so the scope chain knows the new keys
5. When you open Preferences, the page is built from `sections()`; OK/Apply
   writes the changed fields at the scope chosen in the page header

Read the values anywhere with
`PreferencesManager.get_instance().get("my.greeting")`.

## Next Steps

Now that you have a working plugin:

1. **Add real functionality**: See the [SettingsPlugin reference](plugin-types/settings.md) for editors, validation, previews, `after_save()` and console-only keys
2. **Create a package**: See [External Packages](external-packages.md) to distribute your plugins
3. **Try other plugin types**: Browse the [Plugin Type Reference](plugin-types/index.md)
