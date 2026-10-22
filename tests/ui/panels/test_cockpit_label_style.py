"""The title-bar cockpit label (#ClaudeCockpitLabel) must use a muted but
legible text color drawn from the *application* palette.

Regression: it used ``color: palette(mid)``, and the label's own palette is the
unthemed Qt default, so the text resolved near-black and rendered dark-on-dark
in the dark theme.
"""

from __future__ import annotations

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QLabel

from lightfall.ui.panels.claude_panel import ClaudePanel


def test_cockpit_label_uses_app_palette_placeholder_color(qtbot):
    panel = ClaudePanel.__new__(ClaudePanel)  # skip heavy __init__
    label = QLabel("$0.00")
    qtbot.addWidget(label)
    panel._cost_label = label

    panel._apply_cockpit_label_style()

    expected = QApplication.instance().palette().color(
        QPalette.ColorRole.PlaceholderText
    ).name()
    sheet = label.styleSheet()
    assert f"color: {expected}" in sheet
    # The old broken role must not come back, and the color must be concrete.
    assert "palette(mid)" not in sheet
    assert "palette(" not in sheet


def test_apply_cockpit_label_style_noop_without_label(qtbot):
    panel = ClaudePanel.__new__(ClaudePanel)
    panel._cost_label = None
    panel._apply_cockpit_label_style()  # must not raise
