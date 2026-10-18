"""Modern style tokens and QSS builders."""
from __future__ import annotations

import pytest

from lightfall.ui.style import modern


def test_dark_tokens_match_the_visual_language_notes():
    t = modern.tokens("dark")
    assert (t["bg"], t["pane"], t["rail"], t["line"], t["field"], t["txt"], t["dim"],
            t["acc"], t["ok"], t["warn"]) == (
        "#0e1117", "#181d27", "#141923", "#283042", "#10141c", "#e7eaf0", "#8b94a7",
        "#5aa9ff", "#3fb97c", "#e2a23b")


def test_light_tokens_follow_theme_colors():
    from lightfall.ui.theme.manager import LIGHT_COLORS

    t = modern.tokens("light", LIGHT_COLORS)
    assert t["acc"] == LIGHT_COLORS.primary
    assert t["txt"] == LIGHT_COLORS.text
    assert t["pane"] == LIGHT_COLORS.background
    assert t["err"] == LIGHT_COLORS.error


def test_unknown_theme_is_rejected():
    with pytest.raises(ValueError):
        modern.tokens("sepia")


def test_rgba():
    assert modern.rgba("#5aa9ff", 0.18) == "rgba(90, 169, 255, 46)"
    assert modern.rgba("#fff", 1.0) == "rgba(255, 255, 255, 255)"


def test_note_tints_are_10_and_35_percent():
    css = modern.note(modern.tokens("dark"), "warn")
    assert "rgba(226, 162, 59, 26)" in css and "rgba(226, 162, 59, 89)" in css


def test_every_rule_is_scoped_to_the_surface():
    css = modern.stylesheet(modern.tokens("dark"))
    rules = [line for line in css.splitlines() if "{" in line]
    assert rules and all(line.startswith(modern.SCOPE) for line in rules)


def test_stylesheet_parses_in_qt(qtbot, qtlog):
    from PySide6.QtWidgets import QFrame, QLabel, QLineEdit, QPushButton, QVBoxLayout

    surface = QFrame()
    surface.setProperty("modern", "surface")
    layout = QVBoxLayout(surface)
    for role, widget in (("primary", QPushButton("Go")), ("link", QPushButton("Back")),
                         ("title", QLabel("Title")), ("note", QFrame())):
        widget.setProperty("modern", role)
        layout.addWidget(widget)
    layout.addWidget(QLineEdit())
    qtbot.addWidget(surface)
    surface.setStyleSheet(modern.stylesheet(modern.tokens("dark")))
    surface.show()
    qtbot.waitExposed(surface)
    assert not [r for r in qtlog.records if "parse" in r.message.lower()]


def test_repolish_applies_a_changed_property(qtbot):
    from PySide6.QtWidgets import QFrame

    frame = QFrame()
    qtbot.addWidget(frame)
    frame.setProperty("selected", "true")
    modern.repolish(frame)
    assert frame.property("selected") == "true"
