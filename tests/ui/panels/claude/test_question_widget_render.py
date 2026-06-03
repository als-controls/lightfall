"""QuestionRequestWidget renders questions and emits answers on submit."""
from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QRadioButton

from lightfall.claude.widgets.question_request import QuestionRequestWidget


def test_single_select_question(qtbot):
    questions = [{
        "question": "Which DB?",
        "header": "DB",
        "options": [
            {"label": "PostgreSQL", "description": "relational"},
            {"label": "MongoDB", "description": "document"},
        ],
        "multiSelect": False,
    }]
    widget = QuestionRequestWidget("rid-1", questions)
    qtbot.addWidget(widget)

    radios = widget.findChildren(QRadioButton)
    assert [r.text() for r in radios] == ["PostgreSQL", "MongoDB"]

    # Submit disabled until a choice is made.
    assert not widget.submit_btn.isEnabled()

    radios[0].setChecked(True)
    assert widget.submit_btn.isEnabled()

    submitted: list[tuple[str, dict]] = []
    widget.submitted.connect(lambda rid, ans: submitted.append((rid, dict(ans))))
    widget.submit_btn.click()
    assert submitted == [("rid-1", {"Which DB?": "PostgreSQL"})]


def test_multi_select_question(qtbot):
    questions = [{
        "question": "Features?",
        "options": [
            {"label": "Auth"},
            {"label": "Caching"},
            {"label": "Logging"},
        ],
        "multiSelect": True,
    }]
    widget = QuestionRequestWidget("rid-2", questions)
    qtbot.addWidget(widget)

    checks = widget.findChildren(QCheckBox)
    assert [c.text() for c in checks] == ["Auth", "Caching", "Logging"]
    checks[0].setChecked(True)
    checks[2].setChecked(True)

    submitted: list[tuple[str, dict]] = []
    widget.submitted.connect(lambda rid, ans: submitted.append((rid, dict(ans))))
    widget.submit_btn.click()

    # Multi-select answers are comma-separated per SDK contract.
    assert submitted == [("rid-2", {"Features?": "Auth,Logging"})]


def test_cancel_emits_cancelled(qtbot):
    widget = QuestionRequestWidget(
        "rid-3", [{"question": "Q?", "options": [{"label": "X"}]}]
    )
    qtbot.addWidget(widget)
    cancelled: list[str] = []
    widget.cancelled.connect(cancelled.append)
    widget.cancel_btn.click()
    assert cancelled == ["rid-3"]


def test_question_text_renders_as_plaintext(qtbot):
    """Model-provided question text must never render as HTML."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel

    widget = QuestionRequestWidget(
        "rid-4",
        [{"question": "<b>NOT BOLD</b>", "options": [{"label": "X"}]}],
    )
    qtbot.addWidget(widget)

    labels = widget.findChildren(QLabel)
    # Find the question label by its text content.
    question_labels = [
        lb for lb in labels if lb.text() == "<b>NOT BOLD</b>"
    ]
    assert question_labels, "question label not found by text"
    assert question_labels[0].textFormat() == Qt.TextFormat.PlainText


def test_escape_key_cancels(qtbot):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import QEvent

    widget = QuestionRequestWidget(
        "rid-5", [{"question": "Q?", "options": [{"label": "X"}]}]
    )
    qtbot.addWidget(widget)
    cancelled: list[str] = []
    widget.cancelled.connect(cancelled.append)

    qtbot.keyClick(widget, Qt.Key.Key_Escape)
    assert cancelled == ["rid-5"]


def test_two_question_widget_pluralizes_header(qtbot):
    from PySide6.QtWidgets import QLabel

    widget = QuestionRequestWidget(
        "rid-6",
        [
            {"question": "Q1?", "options": [{"label": "A"}]},
            {"question": "Q2?", "options": [{"label": "B"}]},
        ],
    )
    qtbot.addWidget(widget)
    labels = widget.findChildren(QLabel)
    assert any("2 questions" in lb.text() for lb in labels), (
        "expected pluralized header for multi-question widget"
    )
