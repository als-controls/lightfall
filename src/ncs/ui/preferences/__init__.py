"""User preferences management for NCS.

This package provides:
- PreferencesManager for storing user preferences
- PreferencesDialog for editing preferences (plugin-driven)
- AppearanceSettingsPlugin for theme and font settings
- DeviceSettingsPlugin for device backend configuration
- Integration with QSettings for Qt-specific state
- Integration with ConfigManager for typed preferences
"""

from ncs.ui.preferences.builtin import AppearanceSettingsPlugin
from ncs.ui.preferences.device_settings import DeviceSettingsPlugin
from ncs.ui.preferences.dialog import PreferencesDialog
from ncs.ui.preferences.manager import PreferencesManager

__all__ = [
    "AppearanceSettingsPlugin",
    "DeviceSettingsPlugin",
    "PreferencesDialog",
    "PreferencesManager",
]
