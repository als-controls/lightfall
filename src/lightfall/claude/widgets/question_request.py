"""Inline widget for AskUserQuestion responses.

The CLI emits ``AskUserQuestion`` as a tool call with a structured input
containing one or more questions. This widget renders that input as
radio (single-select) or checkbox (multi-select) groups, and on submit
emits the user's choices as a {question_text: label_or_csv} dict.

Each question also has an "Other" free-text field, and the card can be
answered by text (e.g. from voice input) via ``answer_by_text``.
"""
from __future__ import annotations

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

_FILLER_WORDS = {"option", "the", "number"}


def _normalise(text: str) -> str:
    """Lowercase, strip punctuation, drop filler words, collapse whitespace."""
    text = re.sub(r"[^\w\s]", "", text.lower())
    return " ".join(w for w in text.split() if w not in _FILLER_WORDS)


def _match_one(part: str, labels: list[str]) -> str | None:
    norm = _normalise(part)
    if not norm:
        return None
    normed = [(lb, _normalise(lb)) for lb in labels]
    exact = [lb for lb, n in normed if n == norm]
    if exact:
        return exact[0]
    partial = [lb for lb, n in normed if norm in n]
    return partial[0] if len(partial) == 1 else None


def match_options(text: str, labels: list[str]) -> list[str]:
    """Labels matched by a single-select utterance (0 or 1 item).

    An exact normalised match wins; otherwise a unique label containing the
    normalised text; otherwise nothing (the text belongs in "Other").
    """
    m = _match_one(text, labels)
    return [m] if m is not None else []


class QuestionRequestWidget(QFrame):
    """Inline widget rendering AskUserQuestion in the permission area.

    Signals:
        submitted(str, dict): request_id, {question_text: selected_label(s)}
        cancelled(str): request_id
    """

    submitted = Signal(str, dict)
    cancelled = Signal(str)

    def __init__(
        self,
        request_id: str,
        questions: list[dict],
        parent: QWidget | None = None,
        speaker: str = "Assistant",
    ) -> None:
        super().__init__(parent)
        self.request_id = request_id
        self._speaker = speaker  # who is asking, e.g. "Gemini"
        self.questions = list(questions)
        self._is_resolved = False
        # Per-question widget tracking: (question_dict, QButtonGroup-or-list[QCheckBox])
        self._question_widgets: list[
            tuple[dict, QButtonGroup | list[QCheckBox]]
        ] = []
        self._boxes: list[QGroupBox] = []
        self.other_edits: list[QLineEdit] = []

        self._setup_ui()
        self._apply_theme_style()
        self._update_submit_state()

    @property
    def is_resolved(self) -> bool:
        return self._is_resolved

    @property
    def current_index(self) -> int:
        """Index of the first unanswered question (len(questions) if none)."""
        for i in range(len(self._question_widgets)):
            if not self._is_answered(i):
                return i
        return len(self._question_widgets)

    def _setup_ui(self) -> None:
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)

        name = self._speaker
        if len(self.questions) == 1:
            header_text = f"❓ <b>{name} is asking…</b>"
        else:
            header_text = (
                f"❓ <b>{name} is asking {len(self.questions)} questions…</b>"
            )
        header = QLabel(header_text)
        header.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(header)

        for q in self.questions:
            layout.addWidget(self._build_question_box(q))

        button_row = QHBoxLayout()
        button_row.addStretch()

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.clicked.connect(self._on_cancel)
        button_row.addWidget(self.cancel_btn)

        self.submit_btn = QPushButton("✓ Submit")
        self.submit_btn.setDefault(True)
        self.submit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.submit_btn.setEnabled(False)
        self.submit_btn.clicked.connect(self._on_submit)
        button_row.addWidget(self.submit_btn)

        layout.addLayout(button_row)

    def _build_question_box(self, question: dict) -> QGroupBox:
        header_text = question.get("header") or ""
        text = question.get("question") or ""
        options = question.get("options") or []
        multi = bool(question.get("multiSelect", False))

        box = QGroupBox()
        box.setObjectName("questionBox")
        self._boxes.append(box)
        v = QVBoxLayout(box)
        v.setContentsMargins(8, 6, 8, 6)
        v.setSpacing(2)

        if header_text:
            chip = QLabel(f"<b>{self._escape(header_text)}</b>")
            chip.setTextFormat(Qt.TextFormat.RichText)
            v.addWidget(chip)

        q_label = QLabel(text)
        q_label.setTextFormat(Qt.TextFormat.PlainText)
        q_label.setWordWrap(True)
        v.addWidget(q_label)

        index = len(self._question_widgets)
        if multi:
            checkboxes: list[QCheckBox] = []
            for opt in options:
                label = opt.get("label", "")
                desc = opt.get("description", "")
                cb = QCheckBox(label)
                if desc:
                    cb.setToolTip(desc)
                cb.stateChanged.connect(self._update_submit_state)
                v.addWidget(cb)
                checkboxes.append(cb)
            self._question_widgets.append((question, checkboxes))
        else:
            group = QButtonGroup(self)
            group.setExclusive(True)
            for opt in options:
                label = opt.get("label", "")
                desc = opt.get("description", "")
                rb = QRadioButton(label)
                if desc:
                    rb.setToolTip(desc)
                rb.toggled.connect(
                    lambda checked, i=index: self._on_radio_toggled(i, checked)
                )
                group.addButton(rb)
                v.addWidget(rb)
            self._question_widgets.append((question, group))

        other = QLineEdit()
        other.setPlaceholderText("Other…")
        other.setClearButtonEnabled(True)
        other.textChanged.connect(self._update_submit_state)
        v.addWidget(other)
        self.other_edits.append(other)

        return box

    def _on_radio_toggled(self, index: int, checked: bool) -> None:
        # Picking an option replaces any typed "Other" answer.
        if checked and index < len(self.other_edits):
            self.other_edits[index].clear()
        self._update_submit_state()

    def _is_answered(self, i: int) -> bool:
        _q, widgets = self._question_widgets[i]
        if self.other_edits[i].text().strip():
            return True
        if isinstance(widgets, QButtonGroup):
            return widgets.checkedButton() is not None
        return any(cb.isChecked() for cb in widgets)

    def _highlight_current(self) -> None:
        cur = self.current_index
        for i, box in enumerate(self._boxes):
            if i == cur and not self._is_resolved:
                box.setStyleSheet(
                    "QGroupBox#questionBox { border: 2px solid #9b82d4; "
                    "border-radius: 4px; }"
                )
            else:
                box.setStyleSheet("")

    def answer_by_text(self, text: str) -> bool:
        """Answer the current question by text.

        Returns True when this completed the card and it was submitted.
        """
        if self._is_resolved:
            return False
        i = self.current_index
        if i >= len(self._question_widgets):
            return False
        q, widgets = self._question_widgets[i]
        other = self.other_edits[i]
        labels = [o.get("label", "") for o in (q.get("options") or [])]
        if isinstance(widgets, QButtonGroup):
            matched = match_options(text, labels)
            if matched:
                for btn in widgets.buttons():
                    if btn.text() == matched[0]:
                        btn.setChecked(True)
                other.clear()
            elif text.strip():
                other.setText(text.strip())
        else:
            checked: list[str] = []
            unmatched: list[str] = []
            for part in re.split(r",|\band\b", text):
                if not part.strip():
                    continue
                m = match_options(part, labels)
                if m:
                    if m[0] not in checked:
                        checked.append(m[0])
                else:
                    unmatched.append(part.strip())
            for cb in widgets:
                cb.setChecked(cb.text() in checked)
            other.setText(",".join(unmatched))
        self._update_submit_state()
        if self.current_index >= len(self._question_widgets):
            self._on_submit()
            return True
        return False

    def _update_submit_state(self, *_args) -> None:
        self.submit_btn.setEnabled(
            all(self._is_answered(i) for i in range(len(self._question_widgets)))
        )
        self._highlight_current()

    def _collect_answers(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for i, (q, widgets) in enumerate(self._question_widgets):
            text = q.get("question", "")
            other = self.other_edits[i].text().strip()
            if isinstance(widgets, QButtonGroup):
                btn = widgets.checkedButton()
                if other:
                    out[text] = other
                elif btn is not None:
                    out[text] = btn.text()
            else:
                selected = [cb.text() for cb in widgets if cb.isChecked()]
                if other:
                    selected.append(other)
                # SDK multi-select contract: comma-separated labels.
                out[text] = ",".join(selected)
        return out

    def _on_submit(self) -> None:
        if self._is_resolved:
            return
        self._is_resolved = True
        self.submitted.emit(self.request_id, self._collect_answers())
        self._show_resolved()

    def _on_cancel(self) -> None:
        if self._is_resolved:
            return
        self._is_resolved = True
        self.cancelled.emit(self.request_id)
        self._show_resolved()

    def _show_resolved(self) -> None:
        self.submit_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        for edit in self.other_edits:
            edit.setEnabled(False)
        self._highlight_current()
        for _q, widgets in self._question_widgets:
            if isinstance(widgets, QButtonGroup):
                for btn in widgets.buttons():
                    btn.setEnabled(False)
            else:
                for cb in widgets:
                    cb.setEnabled(False)

    def _apply_theme_style(self) -> None:
        palette = self.palette()
        is_dark = palette.color(QPalette.ColorRole.Base).lightness() < 128
        if is_dark:
            bg = "rgba(80, 70, 110, 0.35)"
            border = "rgba(150, 130, 200, 0.5)"
        else:
            bg = "rgba(220, 215, 240, 0.55)"
            border = "rgba(150, 130, 200, 0.6)"
        self.setStyleSheet(
            f"""
            QuestionRequestWidget {{
                background-color: {bg};
                border: 1px solid {border};
                border-radius: 6px;
            }}
            QPushButton {{ padding: 2px 8px; border-radius: 4px; }}
            """
        )

    @staticmethod
    def _escape(text: str) -> str:
        return (
            text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )

    def keyPressEvent(self, event) -> None:
        if self._is_resolved:
            super().keyPressEvent(event)
            return
        if event.key() == Qt.Key.Key_Escape:
            self._on_cancel()
        else:
            super().keyPressEvent(event)
