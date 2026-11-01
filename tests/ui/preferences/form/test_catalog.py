"""Catalog editor: a table of discovered items with a Not set / On / Off combo each."""
from __future__ import annotations

import pytest
from qtpy.QtWidgets import QComboBox, QLineEdit, QTableWidget

from lightfall.ui.preferences import scope_api
from lightfall.ui.preferences.form.catalog import CatalogEditor
from lightfall.ui.preferences.form.model import FormModel
from lightfall.ui.preferences.form.row import SettingRow
from lightfall.ui.preferences.form.schema import Catalog, CatalogItem, Section, SettingField
from lightfall.ui.preferences.form.view import SettingsForm
from lightfall.ui.preferences.scope_api import ScopeChoice
from tests.ui.preferences.form.test_view import FakePlugin, clear_via_menu, menus  # noqa: F401

KEY = "plugins.enabled"
USER, LOCAL, BRANCH, BEAMLINE, FACILITY = "user:u1", "local", "branch:7.0.1.1", "beamline:7.0.1", "facility"

ITEMS = (
    CatalogItem("alpha", "Alpha", columns=("tool", "Analysis")),
    CatalogItem("beta", "Beta", default_enabled=False, columns=("monitor", "Status")),
    CatalogItem("core", "Core", protected=True, columns=("tool", "Built in")),
)


def make_editor(qtbot, items=ITEMS, columns=("Type", "Category")):
    spec = Catalog(lambda: items, columns=columns)
    editor = CatalogEditor(spec)
    qtbot.addWidget(editor)
    return editor


def table(editor) -> QTableWidget:
    return editor.findChild(QTableWidget)


def combo(editor, item_id) -> QComboBox:
    return editor.item_combo(item_id)


def pick(editor, item_id, text):
    box = combo(editor, item_id)
    box.setCurrentIndex(box.findText(text))  # emits like a user pick


def test_catalog_rows_and_columns(qtbot):
    editor = make_editor(qtbot)
    t = table(editor)
    headers = [t.horizontalHeaderItem(c).text() for c in range(t.columnCount())]
    assert headers == ["Name", "Type", "Category", "Here", "Inherited"]
    assert t.rowCount() == 3
    assert [t.item(r, 0).text() for r in range(3)] == ["Alpha", "Beta", "Core"]
    assert t.item(1, 1).text() == "monitor" and t.item(1, 2).text() == "Status"
    assert [combo(editor, "alpha").itemText(i) for i in range(3)] == ["Not set", "On", "Off"]
    assert editor.value() is None
    assert combo(editor, "alpha").currentText() == "Not set"
    assert editor.full_width


def test_choose_on_writes_only_that_item(qtbot):
    editor = make_editor(qtbot)
    editor.set_value({"beta": False})
    with qtbot.waitSignal(editor.changed):
        pick(editor, "alpha", "On")
    assert editor.value() == {"alpha": True, "beta": False}
    first = editor.value()
    first["x"] = 1
    assert "x" not in editor.value()  # a fresh dict each time


def test_set_value_keeps_items_the_catalog_does_not_list(qtbot):
    editor = make_editor(qtbot)
    editor.set_value({"gone": False})
    pick(editor, "alpha", "Off")
    assert editor.value() == {"gone": False, "alpha": False}


def test_not_set_removes_item_and_empty_is_none(qtbot):
    editor = make_editor(qtbot)
    editor.set_value({"alpha": True, "beta": True})
    pick(editor, "alpha", "Not set")
    assert editor.value() == {"beta": True}
    pick(editor, "beta", "Not set")
    assert editor.value() is None
    editor.set_value(None)
    assert all(combo(editor, i).currentText() == "Not set" for i in ("alpha", "beta"))


def test_inherited_column_shows_origin(qtbot):
    editor = make_editor(qtbot)
    editor.set_inherited({"alpha": (False, "Beamline 7.0.1")})
    t = table(editor)
    col = t.columnCount() - 1
    assert t.item(0, col).text() == "Off (Beamline 7.0.1)"
    assert t.item(1, col).text() == "Off (default)"
    assert t.item(2, col).text() == "On (always)"
    # The scope's own entry does not change what is inherited.
    editor.set_value({"alpha": True})
    assert t.item(0, col).text() == "Off (Beamline 7.0.1)"


def test_protected_item_cannot_be_off(qtbot):
    editor = make_editor(qtbot)
    box = combo(editor, "core")
    assert not box.isEnabled()
    assert box.currentText() == "On"
    editor.set_value({"core": False, "alpha": True})
    assert box.currentText() == "On"
    assert editor.value() == {"alpha": True}  # never written as Off


def test_filter_hides_rows(qtbot):
    editor = make_editor(qtbot)
    t = table(editor)
    line = editor.findChild(QLineEdit)
    assert line.placeholderText()
    line.setText("ALP")
    assert [t.isRowHidden(r) for r in range(3)] == [False, True, True]
    line.setText("")
    assert not any(t.isRowHidden(r) for r in range(3))


def test_read_only_disables_all_combos(qtbot):
    editor = make_editor(qtbot)
    editor.set_read_only(True)
    assert not any(combo(editor, i).isEnabled() for i in ("alpha", "beta", "core"))
    editor.set_read_only(False)
    assert combo(editor, "alpha").isEnabled() and combo(editor, "beta").isEnabled()
    assert not combo(editor, "core").isEnabled()  # protected stays fixed


# Form integration -------------------------------------------------------------
class CatalogPrefs:
    def __init__(self, layers=None, inherited=None):
        self.layers = dict(layers or {})
        self.inherited = dict(inherited or {})  # scope -> {id: (value, label)}
        self.calls: list[tuple[str, str]] = []

    def allowed_at(self, key, scope):
        return scope in (USER, BEAMLINE)

    def writable_at(self, key, scope):
        return scope == USER

    def lock_at(self, key, scope):
        return None

    def layer_value(self, key, scope):
        return self.layers.get((scope, key))

    def inherited_items(self, key, scope):
        self.calls.append((key, scope))
        return self.inherited.get(scope, {})

    def scope_choices(self):
        return [
            ScopeChoice(USER, "user", "User — u1", None, ""),
            ScopeChoice(BEAMLINE, "beamline", "Beamline 7.0.1", "viewer", "Read-only: you are a viewer here"),
        ]


def catalog_form(qtbot, prefs):
    sections = [Section("Plugins", [SettingField(KEY, Catalog(lambda: ITEMS, ("Type", "Category")))])]
    model = FormModel(prefs.layer_value)
    form = SettingsForm(FakePlugin(sections), model, prefs)
    qtbot.addWidget(form)
    form.show_scope(USER)
    (row,) = form.findChildren(SettingRow)
    return form, model, row


def inherited_text(editor, row):
    t = table(editor)
    return t.item(row, t.columnCount() - 1).text()


def test_form_passes_inherited_and_edits_go_to_model(qtbot, menus):  # noqa: F811
    prefs = CatalogPrefs(
        layers={(USER, KEY): {"beta": True}},
        inherited={USER: {"alpha": (False, "Beamline 7.0.1")}, BEAMLINE: {}},
    )
    form, model, row = catalog_form(qtbot, prefs)
    editor = row.editor
    assert inherited_text(editor, 0) == "Off (Beamline 7.0.1)"
    pick(editor, "alpha", "On")
    assert model.pending(USER, KEY) == {"beta": True, "alpha": True}
    assert model.is_dirty(USER, KEY)
    clear_via_menu(qtbot, row, menus, target=combo(editor, "alpha"))
    assert model.pending(USER, KEY) is None
    assert editor.value() is None
    form.show_scope(BEAMLINE)
    (row,) = form.findChildren(SettingRow)
    assert inherited_text(row.editor, 0) == "On (default)"
    assert not combo(row.editor, "alpha").isEnabled()  # read-only scope
    assert (KEY, BEAMLINE) in prefs.calls


def test_refresh_from_store_updates_inherited(qtbot):
    prefs = CatalogPrefs(inherited={USER: {}})
    form, _model, row = catalog_form(qtbot, prefs)
    assert inherited_text(row.editor, 0) == "On (default)"
    prefs.inherited[USER] = {"alpha": (False, "Facility — ALS")}
    form.refresh_from_store()
    assert inherited_text(row.editor, 0) == "Off (Facility — ALS)"


# PreferencesManager.inherited_items ------------------------------------------
class LayerPrefs:
    def __init__(self, layers, choices, allowed=None):
        self.layers = layers
        self.choices = choices
        self.allowed = allowed

    def scope_choices(self):
        return self.choices

    def allowed_at(self, key, scope):
        return self.allowed is None or scope in self.allowed

    def layer_value(self, key, scope):
        return self.layers.get(scope)


SERVER_CHOICES = [
    ScopeChoice(USER, "user", "User — u1", "owner", ""),
    ScopeChoice(LOCAL, "local", "This console", None, ""),
    ScopeChoice(BRANCH, "branch", "Branch 7.0.1.1", "operator", ""),
    ScopeChoice(BEAMLINE, "beamline", "Beamline 7.0.1", "operator", ""),
    ScopeChoice(FACILITY, "facility", "Facility — ALS", None, ""),
]


def test_inherited_items_merges_broader_layers():
    prefs = LayerPrefs(
        {
            FACILITY: {"a": False, "b": False, "c": True},
            BEAMLINE: {"b": True, "c": None},  # None removes c
            BRANCH: {"d": False},
            LOCAL: {"e": True},
            USER: {"a": True},  # the shown scope's own layer never counts
        },
        SERVER_CHOICES,
    )
    assert scope_api.inherited_items(prefs, KEY, USER) == {
        "a": (False, "Facility — ALS"),
        "b": (True, "Beamline 7.0.1"),
        "d": (False, "Branch 7.0.1.1"),
        "e": (True, "This console"),
    }
    assert scope_api.inherited_items(prefs, KEY, BEAMLINE) == {
        "a": (False, "Facility — ALS"),
        "b": (False, "Facility — ALS"),
        "c": (True, "Facility — ALS"),
    }
    assert scope_api.inherited_items(prefs, KEY, FACILITY) == {}
    assert scope_api.inherited_items(prefs, KEY, LOCAL)["d"] == (False, "Branch 7.0.1.1")
    assert "e" not in scope_api.inherited_items(prefs, KEY, LOCAL)


def test_inherited_items_skips_layers_the_key_cannot_live_at():
    prefs = LayerPrefs({LOCAL: {"e": True}, BEAMLINE: {"b": True}}, SERVER_CHOICES,
                       allowed={USER, BRANCH, BEAMLINE, FACILITY})
    assert scope_api.inherited_items(prefs, KEY, USER) == {"b": (True, "Beamline 7.0.1")}


@pytest.fixture
def files_pm(qapp, tmp_path, monkeypatch):
    from lightfall.ui.preferences.manager import PreferencesManager
    from tests.ui.preferences.test_manager_identity import FakeCM

    monkeypatch.delenv("LIGHTFALL_SCOPES_DISABLED", raising=False)
    (tmp_path / "facility.yaml").write_text(
        "settings:\n  plugins.enabled:\n    a: false\n    b: false\n", encoding="utf-8")
    (tmp_path / "beamline-7.0.1.yaml").write_text(
        "settings:\n  plugins.enabled:\n    b: true\n", encoding="utf-8")
    PreferencesManager.reset()
    manager = PreferencesManager.get_instance()
    manager.set_config_manager(FakeCM(BRANCH))
    assert manager.load_local_scopes(tmp_path) is True
    yield manager
    PreferencesManager.reset()


def test_manager_inherited_items_from_scope_files(files_pm):
    assert files_pm.inherited_items(KEY, LOCAL) == {
        "a": (False, "Facility — facility"),
        "b": (True, "Beamline 7.0.1"),
    }
    assert files_pm.inherited_items(KEY, BEAMLINE) == {
        "a": (False, "Facility — facility"),
        "b": (False, "Facility — facility"),
    }


# Fix round 1 ------------------------------------------------------------------
def test_narrow_table_shows_every_row_in_full(qtbot):
    editor = make_editor(qtbot)
    editor.resize(160, 400)  # far narrower than the columns' contents
    editor.show()
    qtbot.waitExposed(editor)
    t = table(editor)
    rows_height = sum(t.rowHeight(r) for r in range(t.rowCount()))
    assert t.viewport().height() >= rows_height


def test_protected_item_inherits_always_on(qtbot):
    editor = make_editor(qtbot)
    editor.set_inherited({"core": (False, "Beamline 7.0.1")})
    assert inherited_text(editor, 2) == "On (always)"


def test_filter_menu_has_no_clear(qtbot, menus):  # noqa: F811
    from qtpy.QtCore import QPoint
    from qtpy.QtGui import QContextMenuEvent
    from qtpy.QtWidgets import QApplication

    prefs = CatalogPrefs(inherited={USER: {}})
    form, _model, row = catalog_form(qtbot, prefs)
    form.show()
    line = row.editor.filter_edit
    # The line edit's own menu is built by createStandardContextMenu and shown with a
    # non-blocking popup; the row would instead build one with its Clear action and run
    # it through SettingRow._popup (the `menus` fixture records that). Nothing here waits.
    own = line.createStandardContextMenu()
    assert own.actions()
    assert all("Clear (not set)" not in a.text() for a in own.actions())
    own.deleteLater()
    pos = QPoint(2, 2)
    QApplication.sendEvent(line, QContextMenuEvent(QContextMenuEvent.Reason.Mouse, pos, line.mapToGlobal(pos)))
    assert not menus  # the event reached the line edit, not the row's Clear menu
    from qtpy.QtWidgets import QMenu
    for popup in QApplication.topLevelWidgets():  # tidy: close the line edit's own popup
        if isinstance(popup, QMenu):
            popup.close()


def test_console_none_item_drops_file_items_everywhere(files_pm):
    # A None item in the console YAML drops the file layers' item, in get, origins and
    # inherited_items alike (the resolver's semantics).
    files_pm._local.store_quiet(KEY, {"a": None, "c": True})
    assert files_pm.get(KEY) == {"b": True, "c": True}
    assert files_pm.item_origins(KEY) == {"b": "beamline:7.0.1", "c": "local"}
