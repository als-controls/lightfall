"""Inline widget for AskUserQuestion responses.

The CLI emits ``AskUserQuestion`` as a tool call with a structured input
containing one or more questions. This widget renders that input as
radio (single-select) or checkbox (multi-select) groups, and on submit
emits the user's choices as a {question_text: label_or_csv} dict.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


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
    ) -> None:
        super().__init__(parent)
        self.request_id = request_id
        self.questions = list(questions)
        self._is_resolved = False
        # Per-question widget tracking: (question_dict, QButtonGroup-or-list[QCheckBox])
        self._question_widgets: list[
            tuple[dict, QButtonGroup | list[QCheckBox]]
        ] = []

        self._setup_ui()
        self._apply_theme_style()

    def _setup_ui(self) -> None:
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)

        if len(self.questions) == 1:
            header_text = "❓ <b>Claude is asking…</b>"
        else:
            header_text = (
                f"❓ <b>Claude is asking {len(self.questions)} questions…</b>"
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
                rb.toggled.connect(self._update_submit_state)
                group.addButton(rb)
                v.addWidget(rb)
            self._question_widgets.append((question, group))

        return box

    def _update_submit_state(self, *_args) -> None:
        for _q, widgets in self._question_widgets:
            if isinstance(widgets, QButtonGroup):
                if widgets.checkedButton() is None:
                    self.submit_btn.setEnabled(False)
                    return
            else:
                if not any(cb.isChecked() for cb in widgets):
                    self.submit_btn.setEnabled(False)
                    return
        self.submit_btn.setEnabled(True)

    def _collect_answers(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for q, widgets in self._question_widgets:
            text = q.get("question", "")
            if isinstance(widgets, QButtonGroup):
                btn = widgets.checkedButton()
                if btn is not None:
                    out[text] = btn.text()
            else:
                selected = [cb.text() for cb in widgets if cb.isChecked()]
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
