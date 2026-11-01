"""Mapping editor: a catalog-style table whose "Here" cell is a value editor."""
from __future__ import annotations

from qtpy.QtWidgets import QLabel, QLineEdit, QTableWidget

from lightfall.ui.preferences.form.catalog import CatalogEditor, MappingEditor
from lightfall.ui.preferences.form.editors import create_editor
from lightfall.ui.preferences.form.schema import (
    Catalog,
    CatalogItem,
    Choice,
    Integer,
    Mapping,
    Number,
    Secret,
    SettingField,
    Text,
    Url,
)

ITEMS = (
    CatalogItem("feed.a", "Feed A", default=30, columns=("Beam",)),
    CatalogItem("feed.b", "Feed B", default=60, columns=("Ring",)),
    CatalogItem("feed.c", "Feed C", columns=("Ring",)),
)
SEVERITIES = (("info", "Info"), ("warn", "Warn"), ("error", "Error"))


def make_editor(qtbot, value=None, items=ITEMS, unit="", columns=("Source",)):
    value = value or Integer(1, None)
    field = SettingField("monitor.intervals", Mapping(lambda: items, value, columns=columns), unit=unit)
    editor = create_editor(field.editor, field)
    qtbot.addWidget(editor)
    return editor


def table(editor) -> QTableWidget:
    return editor.findChild(QTableWidget)


def edit_of(editor, item_id) -> QLineEdit:
    return editor.item_editor(item_id).findChild(QLineEdit)


def type_into(editor, item_id, text):
    box = edit_of(editor, item_id)
    box.setText(text)
    box.textEdited.emit(text)


def test_mapping_int_values(qtbot):
    editor = make_editor(qtbot, unit="s")
    assert isinstance(editor, MappingEditor) and editor.full_width
    t = table(editor)
    headers = [t.horizontalHeaderItem(c).text() for c in range(t.columnCount())]
    assert headers == ["Name", "Source", "Here (s)", "Inherited"]  # the unit is in the header
    assert not [lbl for lbl in editor.item_editor("feed.a").findChildren(QLabel) if lbl.text() == "s"]
    assert editor.value() is None
    with qtbot.waitSignal(editor.changed):
        type_into(editor, "feed.a", "15")
    assert editor.value() == {"feed.a": 15}
    editor.set_value({"feed.b": 90, "gone": 7})
    assert edit_of(editor, "feed.b").text() == "90"
    assert edit_of(editor, "feed.a").text() == ""
    type_into(editor, "feed.a", "5")
    assert editor.value() == {"feed.b": 90, "gone": 7, "feed.a": 5}  # unlisted ids kept
    assert editor.is_valid()
    # Compact rows: the same height as a catalog row.
    catalog = CatalogEditor(Catalog(lambda: ITEMS))
    qtbot.addWidget(catalog)
    assert table(editor).rowHeight(0) <= table(catalog).rowHeight(0)


def test_mapping_invalid_row_is_not_pushed(qtbot):
    editor = make_editor(qtbot)
    editor.set_value({"feed.a": 20, "feed.b": 40})
    type_into(editor, "feed.a", "0")  # below the minimum
    assert not editor.is_valid()
    assert editor.value() == {"feed.b": 40}  # no stale 20 for the invalid row
    type_into(editor, "feed.a", "12")
    assert editor.is_valid()
    assert editor.value() == {"feed.a": 12, "feed.b": 40}


def test_mapping_choice_values(qtbot):
    editor = make_editor(qtbot, value=Choice(SEVERITIES))
    box = editor.item_combo("feed.b")
    assert [box.itemText(i) for i in range(box.count())] == ["Not set", "Info", "Warn", "Error"]
    with qtbot.waitSignal(editor.changed):
        box.setCurrentIndex(box.findText("Warn"))
    assert editor.value() == {"feed.b": "warn"}
    editor.set_value({"feed.a": "error"})
    assert editor.item_combo("feed.a").currentText() == "Error"
    assert editor.value() == {"feed.a": "error"}


def test_mapping_not_set_removes(qtbot):
    editor = make_editor(qtbot, value=Choice(SEVERITIES))
    editor.set_value({"feed.a": "warn", "feed.b": "info"})
    box = editor.item_combo("feed.a")
    box.setCurrentIndex(box.findText("Not set"))
    assert editor.value() == {"feed.b": "info"}
    editor.item_combo("feed.b").setCurrentIndex(0)
    assert editor.value() is None
    number = make_editor(qtbot)
    number.set_value({"feed.a": 3})
    type_into(number, "feed.a", "")
    assert number.value() is None


def test_mapping_inherited_column(qtbot):
    editor = make_editor(qtbot, unit="s")
    t = table(editor)
    col = t.columnCount() - 1
    editor.set_inherited({"feed.a": (60, "Beamline 7.0.1")})
    assert t.item(0, col).text() == "60 s (Beamline 7.0.1)"
    assert t.item(1, col).text() == "60 s (default)"  # the item's code default
    assert t.item(2, col).text() == "Not set (default)"  # no default at all
    editor.set_value({"feed.a": 5})
    assert t.item(0, col).text() == "60 s (Beamline 7.0.1)"  # own entry changes nothing


def test_mapping_inherited_choice_uses_label(qtbot):
    items = (CatalogItem("m", "M", default="warn"), CatalogItem("n", "N"))
    editor = make_editor(qtbot, value=Choice(SEVERITIES), items=items, columns=())
    t = table(editor)
    col = t.columnCount() - 1
    editor.set_inherited({"n": ("error", "Facility")})
    assert t.item(0, col).text() == "Warn (default)"
    assert t.item(1, col).text() == "Error (Facility)"


def test_mapping_read_only_and_float(qtbot):
    editor = make_editor(qtbot, value=Number(0.5, None), unit="s")
    editor.set_value({"feed.a": 1.5})
    assert edit_of(editor, "feed.a").text() == "1.5"
    editor.set_read_only(True)
    assert edit_of(editor, "feed.a").isReadOnly()
    editor.set_read_only(False)
    assert not edit_of(editor, "feed.a").isReadOnly()


def test_mapping_filter(qtbot):
    editor = make_editor(qtbot)
    editor.filter_edit.setText("feed c")
    t = table(editor)
    assert [t.isRowHidden(r) for r in range(3)] == [True, True, False]


def test_mapping_stored_out_of_range_value_round_trips_untouched(qtbot):
    editor = make_editor(qtbot, unit="s")
    editor.set_value({"feed.a": 0, "feed.b": 40})  # 0 is below the minimum of 1
    assert editor.is_valid()  # not flagged on load (only an edit is validated)
    assert editor.value() == {"feed.a": 0, "feed.b": 40}


def test_mapping_line_edit_kinds_get_a_fixed_here_width(qtbot):
    from lightfall.ui.preferences.form.catalog import VALUE_COLUMN_WIDTH

    for spec in (Integer(1, None), Text(), Url(), Secret()):
        editor = make_editor(qtbot, value=spec)
        t = table(editor)
        here = t.columnCount() - 2
        assert t.horizontalHeader().sectionResizeMode(here) == t.horizontalHeader().ResizeMode.Fixed, spec.kind
        assert t.columnWidth(here) == VALUE_COLUMN_WIDTH, spec.kind


def test_mapping_inherited_text_is_not_clipped(qtbot):
    items = (CatalogItem("a", "A long feed name here", default=30, columns=("Some monitor",)),)
    editor = make_editor(qtbot, items=items, unit="s")
    editor.set_inherited({"a": (86400, "Branch 7.0.1.1 (the long name)")})
    editor.resize(760, 200)
    editor.show()
    qtbot.waitExposed(editor)
    t = table(editor)
    last = t.columnCount() - 1
    cell = t.item(0, last)
    # room for the text plus the theme's item padding (4 px a side) and the cell margins
    assert t.columnWidth(last) >= t.fontMetrics().horizontalAdvance(cell.text()) + 16
    assert t.horizontalHeader().length() <= t.viewport().width()  # nothing pushed off the right edge
    wide = t.columnWidth(last)
    editor.set_inherited({})  # shorter text: the column follows it
    assert t.item(0, last).text() == "30 s (default)"
    assert t.columnWidth(last) < wide
