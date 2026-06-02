"""User preferences management for NCS.

This package provides:
- PreferencesManager for storing user preferences
- PreferencesDialog for editing preferences (plugin-driven)
- AppearanceSettingsPlugin for theme and font settings
- DeviceSettingsPlugin for device backend configuration
- Integration with QSettings for Qt-specific state
- Integration with ConfigManager for typed preferences
"""

from lightfall.ui.preferences.builtin import AppearanceSettingsPlugin
from lightfall.ui.preferences.device_settings import DeviceSettingsPlugin
from lightfall.ui.preferences.dialog import PreferencesDialog
from lightfall.ui.preferences.manager import PreferencesManager
from lightfall.ui.preferences.user_profile_settings import UserProfileSettingsPlugin

__all__ = [
    "AppearanceSettingsPlugin",
    "DeviceSettingsPlugin",
    "PreferencesDialog",
    "PreferencesManager",
    "UserProfileSettingsPlugin",
]
