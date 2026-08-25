"""Built-in theme plugins: lightfall-utils palettes + Lightfall's plugin framework.

The class names and this module path are load-bearing: plugins/builtin_manifest.py
references "lightfall.ui.theme.builtin:XThemePlugin" import strings, and the
plugin loader validates issubclass(cls, ThemePlugin).
"""

from __future__ import annotations

from lightfall.plugins.theme_plugin import ThemePlugin
from lightfall_utils.theming import builtin as _builtin
from lightfall_utils.theming.builtin import generate_islands_stylesheet  # noqa: F401


class LightThemePlugin(_builtin.LightThemePlugin, ThemePlugin):
    pass


class SlateThemePlugin(_builtin.SlateThemePlugin, ThemePlugin):
    pass


class DarkBlueThemePlugin(_builtin.DarkBlueThemePlugin, ThemePlugin):
    pass


class IslandsThemePlugin(_builtin.IslandsThemePlugin, ThemePlugin):
    pass


class CatppuccinMochaThemePlugin(_builtin.CatppuccinMochaThemePlugin, ThemePlugin):
    pass


class EldritchThemePlugin(_builtin.EldritchThemePlugin, ThemePlugin):
    pass


class EvangelionThemePlugin(_builtin.EvangelionThemePlugin, ThemePlugin):
    pass


class AyakaThemePlugin(_builtin.AyakaThemePlugin, ThemePlugin):
    pass
