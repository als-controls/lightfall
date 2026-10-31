"""Fields for keys derived from the console's binding (``CONSOLE_WHEN_UNBOUND_KEYS``).

On a bound console such a key comes from the binding, so at "This console" the field
hides and an Info line shows the bound value instead; on an unbound console the field
is edited at "This console". At beamline/branch scope it is a plain field.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from lightfall.ui.preferences.form.schema import FormValues, Info, SettingField

CONSOLE = frozenset({"local"})


def _prefs():
    from lightfall.ui.preferences.manager import PreferencesManager

    return PreferencesManager.get_instance()


def shown_unless_bound(values: FormValues) -> bool:
    """A binding-derived field shows except at "This console" on a bound console.

    The binding is read live; a binding change shows up when the form is rebuilt
    (scope change or ``scopes_changed``).
    """
    return values.scope != "local" or not _prefs().console_bound()


def binding_text(key: str, default: str = "") -> Callable[[FormValues], str]:
    """Info text "From this console's binding: <value>", or "" (the line hides) when unbound."""

    def text(_values: FormValues) -> str:
        prefs = _prefs()
        if not prefs.console_bound():
            return ""
        return f"From this console's binding: {prefs.get(key, default) or ''}"

    return text


def binding_derived(field: SettingField, default: str = "") -> tuple[SettingField, Info]:
    """``field`` shown unless bound, plus its "This console" binding Info line."""
    return replace(field, visible_when=shown_unless_bound), Info(binding_text(field.key, default), scopes=CONSOLE)
