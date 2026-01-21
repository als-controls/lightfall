"""User preferences management for NCS.

This package provides:
- PreferencesManager for storing user preferences
- Integration with QSettings for Qt-specific state
- Integration with ConfigManager for typed preferences
"""

from ncs.ui.preferences.manager import PreferencesManager

__all__ = [
    "PreferencesManager",
]
