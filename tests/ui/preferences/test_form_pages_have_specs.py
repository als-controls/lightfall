"""Guard: every field a form page shows has a declared scope rule."""
from __future__ import annotations

import pytest

from lightfall.plugins.builtin_manifest import builtin_manifest
from lightfall.scopes.keys import client_registry, console_only
from lightfall.ui.preferences.form.schema import SettingField, section_fields


def _settings_plugins():
    from lightfall.plugins.settings_plugin import SettingsPlugin

    for entry in builtin_manifest.plugins:
        if entry.type_name != "settings":
            continue
        module, _, attr = entry.import_path.partition(":")
        mod = pytest.importorskip(module)
        cls = getattr(mod, attr)
        assert issubclass(cls, SettingsPlugin)
        yield entry.name, cls


def test_form_pages_declare_specs_for_every_field(qapp):
    missing: list[str] = []
    checked = 0
    for name, cls in _settings_plugins():
        plugin = cls()
        if not plugin.is_form_page():
            continue
        checked += 1
        plugin.register_specs()
        registry = client_registry()
        for section in plugin.sections():
            for item in section_fields(section):
                if isinstance(item, SettingField):
                    key = item.key
                    if not (console_only(key) or registry.is_declared(key)):
                        missing.append(f"{name}: {key}")
    assert checked >= 2, "expected at least the Visualization and Login form pages"
    assert not missing, "form-page fields without a declared scope rule:\n" + "\n".join(missing)
