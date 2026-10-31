"""Built-in settings plugins for NCS.

This module contains the core settings plugins that are part of NCS,
including the AppearanceSettingsPlugin for theme and font settings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lightfall_settings import SettingSpec
from PySide6.QtWidgets import QApplication

from lightfall.plugins.settings_plugin import SettingsPlugin
from lightfall.ui.preferences.form.schema import (
    Choice,
    Integer,
    Section,
    SettingField,
    Toggle,
)
from lightfall.ui.preferences.manager import PreferencesManager
from lightfall.ui.theme import ThemeManager

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from PySide6.QtGui import QIcon

    from lightfall.ui.preferences.form.schema import FormValues

# Ron 2026-10-31: a beamline default theme is wanted (white is blinding, and
# is otherwise the default), so every appearance key may be set above the user.
_SCOPES = frozenset({"user", "branch", "beamline", "facility"})

_CONSOLE_STYLES = [
    # Popular dark styles
    "monokai", "dracula", "one-dark", "nord", "gruvbox-dark",
    "native", "vim", "github-dark", "solarized-dark",
    # Light styles
    "default", "friendly", "tango", "solarized-light",
]
# "" is a real value: the console follows the theme's colours.
_CONSOLE_STYLE_OPTIONS = [("", "Auto (follows theme)")] + [
    (style, style.title()) for style in _CONSOLE_STYLES
]


_PREVIEW_KEYS = ("theme", "font_size", "islands_mode")


def _theme_options() -> list[tuple[str, str]]:
    """Every theme the ThemeManager offers now (theme plugins register at startup)."""
    return [
        (info["name"], info["display_name"])
        for info in ThemeManager.get_instance().get_available_themes()
    ]


class AppearanceSettingsPlugin(SettingsPlugin):
    """Built-in settings for theme and font appearance.

    A form page with the theme (options from the ThemeRegistry), base font
    size, console syntax style and islands layout. Edits at the user's own
    scope are previewed live; Cancel restores what was in effect before.

    As a preload plugin (preload=True), it applies the saved theme
    immediately on load, before the main window is created.
    """

    def __init__(self) -> None:
        """Initialize the appearance settings plugin."""
        super().__init__()
        # (theme, islands, font size) in effect before the first preview; None
        # when nothing is being previewed.
        self._original: tuple[str, bool, int] | None = None

    @property
    def name(self) -> str:
        """Return unique identifier for this settings plugin."""
        return "appearance"

    @property
    def display_name(self) -> str:
        """Return human-readable name for preferences sidebar."""
        return "Appearance"

    @property
    def icon(self) -> QIcon | None:
        """Return optional icon for sidebar."""
        return None

    @property
    def category(self) -> str:
        """Return category for grouping."""
        return "general"

    @property
    def priority(self) -> int:
        """Return sort order (lower = higher in list)."""
        return 0  # First in list

    def on_loaded(self) -> None:
        """Apply saved theme immediately on load.

        Called for preload plugins before the main window is created.
        This ensures the correct theme is applied before any UI appears.
        """
        self._apply_saved_appearance()
        # Appearance keys resolve through the scope chain once a user logs in
        # (PreferencesManager.activate_scopes). At preload no scopes are
        # active, so values saved at user/branch scope (e.g. islands_mode)
        # read as defaults here. Re-apply whenever the scope chain changes.
        PreferencesManager.get_instance().scopes_changed.connect(
            lambda _info: self._apply_saved_appearance()
        )

    def _apply_saved_appearance(self) -> None:
        """Apply the currently-resolved islands, theme and font-size prefs."""
        prefs = PreferencesManager.get_instance()
        theme_mgr = ThemeManager.get_instance()
        # Apply the islands-layout preference before the theme so the first
        # stylesheet generation already reflects it.
        theme_mgr.set_islands_mode(bool(prefs.get("islands_mode", False)))
        # Use set_theme_by_name for string-based themes
        theme_mgr.set_theme_by_name(prefs.theme)
        # Apply the saved base font size so it takes effect before any UI
        # appears (mirrors the theme being applied here at preload).
        self._apply_font_size(prefs.font_size)

    def specs(self) -> Sequence[SettingSpec]:
        return [
            SettingSpec(key="theme", scopes=_SCOPES, label="Theme"),
            SettingSpec(key="font_size", scopes=_SCOPES, label="Font size"),
            SettingSpec(key="console_syntax_style", scopes=_SCOPES, label="Console style"),
            SettingSpec(key="islands_mode", scopes=_SCOPES, label="Islands layout"),
        ]

    def sections(self) -> Sequence[Section]:
        return [
            Section("Appearance", [
                SettingField("theme", Choice(_theme_options), label="Theme"),
                SettingField("font_size", Integer(8, 24), label="Font size", unit="pt"),
                SettingField("console_syntax_style", Choice(_CONSOLE_STYLE_OPTIONS),
                             label="Console style"),
                SettingField("islands_mode", Toggle(), label="Islands layout",
                             description="Rounded floating panels, on any theme"),
            ]),
        ]

    # Live preview -------------------------------------------------------------
    def preview(self, values: FormValues) -> None:
        """Show the edited theme, font size and islands layout in the running app.

        Only edits at the scope where the user's own values live (their user scope,
        or this console when not logged in) are previewed: a beamline or facility
        default may never reach this user, so it must not repaint their app.
        """
        prefs = PreferencesManager.get_instance()
        if values.scope != prefs.default_scope():
            return
        theme_mgr = ThemeManager.get_instance()
        if self._original is None:
            self._original = (
                theme_mgr.theme_name, theme_mgr.islands_mode, theme_mgr.base_font_size
            )
        theme_mgr.set_islands_mode(bool(values.effective("islands_mode", False)))
        theme_mgr.set_theme_by_name(str(values.effective("theme", "system")))
        self._apply_font_size(int(values.effective("font_size", 10)))
        is_dirty = getattr(values, "is_dirty", None)
        if is_dirty is not None and not any(is_dirty(key) for key in _PREVIEW_KEYS):
            # Every preview edit was undone: the app shows the saved values again,
            # so there is nothing left for Cancel to restore.
            self._original = None

    def revert_preview(self) -> None:
        """Restore the theme, islands layout and font size in effect before the preview."""
        if self._original is None:
            return  # nothing was previewed
        theme, islands, font_size = self._original
        self._original = None
        theme_mgr = ThemeManager.get_instance()
        theme_mgr.set_islands_mode(islands)
        theme_mgr.set_theme_by_name(theme)
        self._apply_font_size(font_size)

    def after_save(self, scope: str, changed: Mapping[str, Any]) -> None:
        """Apply the resolved values (user over beamline over facility) after a save.

        The saved values are now what is in effect, so a later Cancel keeps them.
        """
        self._original = None
        self._apply_saved_appearance()

    @staticmethod
    def _apply_font_size(size: int) -> None:
        """Set the application-wide base font point size.

        Two complementary steps:

        - ``QApplication.setFont()`` updates the inherited default font, which
          consumers that read it live (e.g. pyqtgraph plots) pick up.
        - ``ThemeManager.set_font_size()`` bakes the size into the global
          stylesheet and reapplies it, re-polishing every already-shown widget.
          This is required because ``setFont()`` alone does not restyle widgets
          created before the change while a global stylesheet is active — only
          plots and (on next show) menus reflected it otherwise.
        """
        app = QApplication.instance()
        if app is not None:
            font = app.font()
            font.setPointSize(int(size))
            app.setFont(font)
        ThemeManager.get_instance().set_font_size(int(size))
