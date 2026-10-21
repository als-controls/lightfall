"""QuestionRequestWidget.answer_by_text, match_options and the Other field."""
from __future__ import annotations

from lightfall.claude.widgets.question_request import (
    QuestionRequestWidget,
    match_options,
)


def _q(question, labels, multi=False):
    return {
        "question": question,
        "header": "H",
        "options": [{"label": lb, "description": ""} for lb in labels],
        "multiSelect": multi,
    }


def _make(qtbot, questions):
    w = QuestionRequestWidget("rid", questions)
    qtbot.addWidget(w)
    return w


def test_single_question_exact_match_submits(qtbot):
    w = _make(qtbot, [_q("Go?", ["Yes", "No"])])
    with qtbot.waitSignal(w.submitted) as blocker:
        assert w.answer_by_text("yes.") is True
    assert blocker.args == ["rid", {"Go?": "Yes"}]
    assert w.is_resolved


def test_unmatched_goes_to_other(qtbot):
    w = _make(qtbot, [_q("Go?", ["Yes", "No"])])
    with qtbot.waitSignal(w.submitted) as blocker:
        w.answer_by_text("maybe later")
    assert blocker.args[1] == {"Go?": "maybe later"}


def test_ambiguous_partial_goes_to_other(qtbot):
    # "alpha" is a substring of both labels and an exact match of neither.
    w = _make(qtbot, [_q("Which?", ["Alpha one", "Alpha two"])])
    with qtbot.waitSignal(w.submitted) as blocker:
        w.answer_by_text("alpha")
    assert blocker.args[1] == {"Which?": "alpha"}


def test_unique_partial_and_filler_words(qtbot):
    assert match_options("the second", ["First", "Second"]) == ["Second"]
    assert match_options("option a", ["Option A", "Option AB"]) == ["Option A"]
    assert match_options("a", ["Option A", "Option AB"]) == ["Option A"]
    assert match_options("sec", ["First", "Second"]) == ["Second"]


def test_multiselect_split_and_other(qtbot):
    w = _make(qtbot, [_q("Colors?", ["Red", "Green", "Blue"], multi=True)])
    with qtbot.waitSignal(w.submitted) as blocker:
        w.answer_by_text("red and blue, purple")
    assert blocker.args[1] == {"Colors?": "Red,Blue,purple"}


def test_multi_question_one_utterance_each(qtbot):
    w = _make(qtbot, [_q("A?", ["Yes", "No"]), _q("B?", ["Red", "Blue"])])
    assert w.current_index == 0
    assert w.answer_by_text("no") is False
    assert w.current_index == 1
    assert not w.is_resolved
    with qtbot.waitSignal(w.submitted) as blocker:
        assert w.answer_by_text("blue") is True
    assert blocker.args[1] == {"A?": "No", "B?": "Blue"}


def test_resolved_card_ignores_text(qtbot):
    w = _make(qtbot, [_q("Go?", ["Yes", "No"])])
    w.cancel_btn.click()
    assert w.is_resolved
    with qtbot.assertNotEmitted(w.submitted):
        assert w.answer_by_text("yes") is False


def test_typed_other_field_used_on_submit(qtbot):
    w = _make(qtbot, [_q("Go?", ["Yes", "No"])])
    assert not w.submit_btn.isEnabled()
    w.other_edits[0].setText("something else")
    assert w.submit_btn.isEnabled()
    with qtbot.waitSignal(w.submitted) as blocker:
        w.submit_btn.click()
    assert blocker.args[1] == {"Go?": "something else"}


def test_typed_other_appended_to_checked_multiselect(qtbot):
    w = _make(qtbot, [_q("C?", ["Red", "Blue"], multi=True)])
    w.findChildren(type(w.submit_btn))  # no-op; widget built
    from PySide6.QtWidgets import QCheckBox
    w.findChildren(QCheckBox)[0].setChecked(True)
    w.other_edits[0].setText("purple")
    with qtbot.waitSignal(w.submitted) as blocker:
        w.submit_btn.click()
    assert blocker.args[1] == {"C?": "Red,purple"}
