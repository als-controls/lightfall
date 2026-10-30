"""Schema types and the SettingsPlugin form-page contract."""
from __future__ import annotations

import pytest
from lightfall_settings import SettingSpec

from lightfall.plugins.settings_plugin import SettingsPlugin
from lightfall.scopes.keys import _reset_client_registry, client_registry
from lightfall.ui.preferences.form.schema import (
    Section,
    SettingField,
    Text,
    Toggle,
    field_label,
    fields_at,
)


def test_field_label_falls_back_to_spec_then_key():
    spec = SettingSpec(key="a.b", label="Spec label")
    blank = SettingSpec(key="a.b")
    assert field_label(SettingField("a.b", Toggle(), label="Own"), spec) == "Own"
    assert field_label(SettingField("a.b", Toggle()), spec) == "Spec label"
    assert field_label(SettingField("a.b", Toggle()), blank) == "a.b"


def test_fields_at_filters_by_allowed():
    f1, f2, f3 = (SettingField(k, Text()) for k in ("k1", "k2", "k3"))
    sections = [Section("A", [f1, f2]), Section("B", [f3])]
    calls = []

    def allowed(key, scope):
        calls.append((key, scope))
        return key != "k2"

    assert fields_at(sections, "facility", allowed) == [f1, f3]
    assert ("k2", "facility") in calls


def test_form_page_needs_no_create_widget():
    class Page(SettingsPlugin):
        @property
        def name(self):
            return "p"

        def sections(self):
            return [Section("S", [SettingField("k", Toggle())])]

    page = Page()
    assert page.is_form_page()
    with pytest.raises(NotImplementedError):
        page.create_widget()


def test_specs_registered_on_construction():
    _reset_client_registry()

    class Page(SettingsPlugin):
        @property
        def name(self):
            return "p"

        def specs(self):
            return [SettingSpec(key="test_task3.key")]

    Page()
    assert client_registry().is_declared("test_task3.key")
    _reset_client_registry()
