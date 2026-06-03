# Claude Agent Panel — SDK Feature Integration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three SDK feature integrations to the embedded Claude Agent panel: render `AskUserQuestion` interactively, stream assistant text token-by-token, and show live Task-tool subagent progress.

**Architecture:** All three additions plug into the existing `PersistentClaudeWorker` → `QtClaudeAgent` → `ClaudeAssistantWidget` pipeline. New worker signals are forwarded through `QtClaudeAgent` to the widget, which renders new inline UI components (`QuestionRequestWidget`, `TaskCard`) reusing the existing chat-bubble / permission-container visual idioms.

**Tech Stack:** PySide6, `claude_agent_sdk` 0.2.82, `pytest-qt`, `pytest-asyncio` 1.4.

**Spec:** `docs/superpowers/specs/2026-05-27-claude-agent-ui-features-design.md`

**Test runner (Linux):** `.venv/bin/python -m pytest <path>`

---

## File map

**Modified:**
- `src/lightfall/claude/permission_manager.py` — question request/response API, hook & callback special-cases.
- `src/lightfall/claude/agent.py` — re-expose 8 new signals from worker / permission manager; thin `respond_to_question` method; `include_partial_messages=True` option.
- `src/lightfall/claude/_internal/worker.py` — `StreamEvent` dispatch, `Task*Message` branches, 7 new signals.
- `src/lightfall/claude/widget.py` — slots and state for streaming bubbles, question widgets, task cards; suppress `_on_tool_called` for `Task`.

**Created:**
- `src/lightfall/claude/widgets/question_request.py` — `QuestionRequestWidget`.
- `src/lightfall/claude/widgets/task_card.py` — `TaskCard`.
- `tests/claude/test_question_request.py`
- `tests/claude/test_partial_streaming_worker.py`
- `tests/claude/test_task_progress_worker.py`
- `tests/ui/panels/claude/test_question_widget_render.py`
- `tests/ui/panels/claude/test_task_card_render.py`

---

# Phase 1 — AskUserQuestion

## Task 1.1: PermissionManager question API

Add a parallel request/response path for `AskUserQuestion` that mirrors the existing approval plumbing but returns a free-form answers dict.

**Files:**
- Modify: `src/lightfall/claude/permission_manager.py`
- Create: `tests/claude/test_question_request.py`

- [ ] **Step 1: Write failing tests**

Create `tests/claude/test_question_request.py`:

```python
"""PermissionManager.request_question / respond_to_question."""
from __future__ import annotations

import asyncio

import pytest

from lightfall.claude.permission_manager import PermissionManager


@pytest.mark.asyncio
async def test_request_question_emits_signal_and_returns_answers(qtbot):
    pm = PermissionManager()
    captured: list[tuple[str, list]] = []
    pm.question_requested.connect(
        lambda rid, qs: captured.append((rid, list(qs)))
    )

    task = asyncio.create_task(
        pm.request_question([{"question": "Which DB?", "options": []}])
    )
    # Let the signal propagate through the event loop.
    for _ in range(20):
        if captured:
            break
        await asyncio.sleep(0.01)
    assert captured, "question_requested signal was not emitted"
    request_id, questions = captured[0]
    assert questions == [{"question": "Which DB?", "options": []}]

    pm.respond_to_question(request_id, {"Which DB?": "PostgreSQL"})

    answered, answers = await asyncio.wait_for(task, timeout=1.0)
    assert answered is True
    assert answers == {"Which DB?": "PostgreSQL"}


@pytest.mark.asyncio
async def test_respond_with_none_means_cancelled(qtbot):
    pm = PermissionManager()
    captured: list[str] = []
    pm.question_requested.connect(lambda rid, _qs: captured.append(rid))

    task = asyncio.create_task(pm.request_question([{"question": "Q?"}]))
    for _ in range(20):
        if captured:
            break
        await asyncio.sleep(0.01)
    pm.respond_to_question(captured[0], None)

    answered, answers = await asyncio.wait_for(task, timeout=1.0)
    assert answered is False
    assert answers == {}


@pytest.mark.asyncio
async def test_cancel_all_pending_wakes_question(qtbot):
    pm = PermissionManager()
    captured: list[str] = []
    pm.question_requested.connect(lambda rid, _qs: captured.append(rid))

    task = asyncio.create_task(pm.request_question([{"question": "Q?"}]))
    for _ in range(20):
        if captured:
            break
        await asyncio.sleep(0.01)
    pm.cancel_all_pending()

    answered, answers = await asyncio.wait_for(task, timeout=1.0)
    assert answered is False
    assert answers == {}
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `.venv/bin/python -m pytest tests/claude/test_question_request.py -v`
Expected: 3 failures with `AttributeError: 'PermissionManager' object has no attribute 'question_requested'` (or similar).

- [ ] **Step 3: Add signal + state to `PermissionManager.__init__`**

In `permission_manager.py`, add the signal to the class-level signal definitions (around line 30):

```python
    # Signals
    permission_requested = Signal(str, str, dict)  # request_id, tool_name, tool_input
    question_requested = Signal(str, list)  # request_id, questions
    auto_approvals_changed = Signal(set)  # Current set of auto-approved tools
```

In `__init__` (just before `# Load saved preferences` at ~line 115), add:

```python
        # Pending AskUserQuestion requests — parallel plumbing to permissions,
        # but the response is a free-form answers dict, not a (bool, str) pair.
        self._pending_questions: dict[str, asyncio.Event] = {}
        self._pending_question_loops: dict[str, asyncio.AbstractEventLoop] = {}
        self._question_responses: dict[str, dict[str, str] | None] = {}
```

- [ ] **Step 4: Add `request_question` and `respond_to_question`**

Add these methods after `respond` (around line 300):

```python
    async def request_question(
        self, questions: list[dict[str, Any]]
    ) -> tuple[bool, dict[str, str]]:
        """Request the user to answer one or more multi-choice questions.

        Emits ``question_requested`` and waits for a response via
        ``respond_to_question``. Returns ``(answered, answers)`` where
        ``answered=False`` means the user cancelled.
        """
        request_id = str(uuid.uuid4())
        loop = asyncio.get_running_loop()
        event = asyncio.Event()

        self._pending_questions[request_id] = event
        self._pending_question_loops[request_id] = loop

        self.question_requested.emit(request_id, questions)

        try:
            await asyncio.wait_for(event.wait(), timeout=300)
        except TimeoutError:
            self._pending_questions.pop(request_id, None)
            self._pending_question_loops.pop(request_id, None)
            self._question_responses.pop(request_id, None)
            return (False, {})

        self._pending_questions.pop(request_id, None)
        self._pending_question_loops.pop(request_id, None)
        answers = self._question_responses.pop(request_id, None)
        if answers is None:
            return (False, {})
        return (True, answers)

    def respond_to_question(
        self, request_id: str, answers: dict[str, str] | None
    ) -> None:
        """Provide answers to a pending question request.

        Pass ``answers=None`` to indicate the user cancelled.
        """
        if request_id not in self._pending_questions:
            return
        self._question_responses[request_id] = answers
        loop = self._pending_question_loops.get(request_id)
        event = self._pending_questions.get(request_id)
        if loop and event:
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:
                self._pending_questions.pop(request_id, None)
                self._pending_question_loops.pop(request_id, None)
                self._question_responses.pop(request_id, None)
```

- [ ] **Step 5: Extend `cancel_all_pending` to wake pending questions**

Modify `cancel_all_pending` (around line 189) — append after the existing loop body:

```python
    def cancel_all_pending(self) -> None:
        """Cancel all pending permission requests.

        Denies all waiting requests, waking their coroutines.
        Called when the user cancels a query so pending approval
        dialogs don't block the cancellation.
        """
        for request_id in list(self._pending_requests.keys()):
            self._responses[request_id] = (False, "Query cancelled by user")
            loop = self._pending_loops.get(request_id)
            event = self._pending_requests.get(request_id)
            if loop and event:
                try:
                    loop.call_soon_threadsafe(event.set)
                except RuntimeError:
                    pass
        # Also wake pending question requests with a cancel response.
        for request_id in list(self._pending_questions.keys()):
            self._question_responses[request_id] = None
            loop = self._pending_question_loops.get(request_id)
            event = self._pending_questions.get(request_id)
            if loop and event:
                try:
                    loop.call_soon_threadsafe(event.set)
                except RuntimeError:
                    pass
```

- [ ] **Step 6: Run tests to confirm they pass**

Run: `.venv/bin/python -m pytest tests/claude/test_question_request.py -v`
Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add src/lightfall/claude/permission_manager.py tests/claude/test_question_request.py
git commit -m "PermissionManager: add request_question / respond_to_question

Parallel to request_permission, but returns a free-form answers dict
instead of (bool, str). Used by the AskUserQuestion handler in the
can_use_tool callback to inject user answers as updated_input.

cancel_all_pending now wakes pending questions (responds with None,
meaning cancelled) so a query cancel doesn't leave the worker blocked.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 1.2: Special-case AskUserQuestion in `can_use_tool` and PreToolUse hook

The can_use_tool callback must return `PermissionResultAllow(updated_input=...)` to feed answers back. The PreToolUse hook must pass through (return `{}`) so it doesn't intercept first.

**Files:**
- Modify: `src/lightfall/claude/permission_manager.py:342-448`
- Test: `tests/claude/test_question_request.py` (append)

- [ ] **Step 1: Add failing test for can_use_tool integration**

Append to `tests/claude/test_question_request.py`:

```python
@pytest.mark.asyncio
async def test_can_use_tool_routes_AskUserQuestion_to_request_question(qtbot):
    from claude_agent_sdk import (
        PermissionResultAllow,
        PermissionResultDeny,
        ToolPermissionContext,
    )
    from lightfall.claude.permission_manager import (
        PermissionManager,
        create_can_use_tool_callback,
    )

    pm = PermissionManager()
    callback = create_can_use_tool_callback(pm)
    questions = [{"question": "Which?", "options": [{"label": "A"}], "multiSelect": False}]

    # Respond before awaiting; we'll connect a signal handler that calls
    # respond_to_question as soon as the request is emitted.
    def _auto_answer(request_id: str, _qs: list) -> None:
        pm.respond_to_question(request_id, {"Which?": "A"})

    pm.question_requested.connect(_auto_answer)

    result = await callback(
        "AskUserQuestion",
        {"questions": questions},
        ToolPermissionContext(),
    )

    assert isinstance(result, PermissionResultAllow)
    assert result.updated_input == {
        "questions": questions,
        "answers": {"Which?": "A"},
    }


@pytest.mark.asyncio
async def test_can_use_tool_AskUserQuestion_cancel_denies(qtbot):
    from claude_agent_sdk import PermissionResultDeny, ToolPermissionContext
    from lightfall.claude.permission_manager import (
        PermissionManager,
        create_can_use_tool_callback,
    )

    pm = PermissionManager()
    callback = create_can_use_tool_callback(pm)

    def _auto_cancel(request_id: str, _qs: list) -> None:
        pm.respond_to_question(request_id, None)

    pm.question_requested.connect(_auto_cancel)

    result = await callback(
        "AskUserQuestion",
        {"questions": [{"question": "Q?"}]},
        ToolPermissionContext(),
    )

    assert isinstance(result, PermissionResultDeny)


@pytest.mark.asyncio
async def test_pre_tool_use_hook_passes_through_AskUserQuestion(qtbot):
    from lightfall.claude.permission_manager import (
        PermissionManager,
        create_pre_tool_use_hook,
    )

    pm = PermissionManager()
    hook = create_pre_tool_use_hook(pm)

    result = await hook(
        {"tool_name": "AskUserQuestion", "tool_input": {"questions": []}},
        "tool_use_id_xyz",
        None,
    )

    # Empty dict means "no decision" — SDK falls through to can_use_tool.
    assert result == {}
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `.venv/bin/python -m pytest tests/claude/test_question_request.py -v -k "AskUserQuestion or pre_tool_use"`
Expected: 3 failures.

- [ ] **Step 3: Special-case AskUserQuestion in `create_can_use_tool_callback`**

Edit `permission_manager.py` `create_can_use_tool_callback` (line ~398). Replace the `can_use_tool` function body with:

```python
    async def can_use_tool(
        tool_name: str,
        tool_input: dict[str, Any],
        context: ToolPermissionContext
    ) -> PermissionResult:
        """
        Permission callback that waits for UI approval.

        Special case: ``AskUserQuestion`` is the CLI's built-in clarifying
        question tool. We render it as a question UI and inject the user's
        answers via ``updated_input``; ``PermissionResultAllow`` can carry
        that, hooks cannot, so this is the only place it can be handled.

        Args:
            tool_name: Name of the tool
            tool_input: Tool input parameters
            context: Permission context (unused currently)

        Returns:
            PermissionResult allowing or denying the tool use
        """
        if tool_name == "AskUserQuestion":
            questions = (
                tool_input.get("questions", [])
                if isinstance(tool_input, dict) else []
            )
            if not questions:
                return PermissionResultDeny(
                    message="AskUserQuestion called with no questions"
                )
            try:
                answered, answers = await permission_manager.request_question(
                    questions
                )
            except Exception as e:
                return PermissionResultDeny(
                    message=f"Question system error: {e}"
                )
            if not answered:
                return PermissionResultDeny(message="User declined to answer")
            return PermissionResultAllow(
                updated_input={"questions": questions, "answers": answers}
            )

        try:
            allowed, message = await permission_manager.request_permission(
                tool_name, tool_input
            )
        except Exception as e:
            return PermissionResultDeny(
                message=f"Permission system error: {e}"
            )

        if allowed:
            return PermissionResultAllow()
        else:
            return PermissionResultDeny(message=message or "User denied permission")
```

- [ ] **Step 4: Special-case AskUserQuestion in `create_pre_tool_use_hook`**

Edit `permission_manager.py` `create_pre_tool_use_hook` (line ~342). After the `tool_name = hook_input.get("tool_name", "")` line:

```python
    async def pre_tool_use_hook(
        hook_input,
        tool_use_id,
        context,
    ):
        tool_name = hook_input.get("tool_name", "")
        tool_input = hook_input.get("tool_input", {})

        # Pass through AskUserQuestion — handled in can_use_tool with
        # updated_input, which a hook cannot return. An empty dict tells
        # the SDK "no decision", so it falls through to can_use_tool.
        if tool_name == "AskUserQuestion":
            return {}

        # Check auto-approval first
        if permission_manager.is_auto_approved(tool_name):
            return {}  # Empty dict = allow (no override)

        # ... rest unchanged ...
```

- [ ] **Step 5: Run tests to confirm they pass**

Run: `.venv/bin/python -m pytest tests/claude/test_question_request.py -v`
Expected: 6 passed (3 original + 3 new).

- [ ] **Step 6: Commit**

```bash
git add src/lightfall/claude/permission_manager.py tests/claude/test_question_request.py
git commit -m "Route AskUserQuestion through can_use_tool with updated_input

The built-in AskUserQuestion tool needs the user's selections fed back
as the tool's input (\"answers\" field). PermissionResultAllow carries
updated_input; hooks cannot. So the PreToolUse hook passes through and
the can_use_tool callback handles it: prompts via PermissionManager.
request_question, then returns Allow with the questions + answers map.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 1.3: `QuestionRequestWidget`

Inline widget rendering one or more questions with radios / checkboxes.

**Files:**
- Create: `src/lightfall/claude/widgets/question_request.py`
- Test: `tests/ui/panels/claude/test_question_widget_render.py`

- [ ] **Step 1: Write failing test**

Create `tests/ui/panels/claude/test_question_widget_render.py`:

```python
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
```

- [ ] **Step 2: Run test to confirm it fails**

Run: `.venv/bin/python -m pytest tests/ui/panels/claude/test_question_widget_render.py -v`
Expected: `ImportError: No module named 'lightfall.claude.widgets.question_request'`.

- [ ] **Step 3: Implement the widget**

Create `src/lightfall/claude/widgets/question_request.py`:

```python
"""Inline widget for AskUserQuestion responses.

The CLI emits ``AskUserQuestion`` as a tool call with a structured input
containing one or more questions. This widget renders that input as
radio (single-select) or checkbox (multi-select) groups, and on submit
emits the user's choices as a {question_text: label_or_csv} dict.
"""
from __future__ import annotations

from typing import cast

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
```

- [ ] **Step 4: Run test to confirm it passes**

Run: `.venv/bin/python -m pytest tests/ui/panels/claude/test_question_widget_render.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/claude/widgets/question_request.py tests/ui/panels/claude/test_question_widget_render.py
git commit -m "Add QuestionRequestWidget for AskUserQuestion rendering

One QGroupBox per question, radios for single-select / checkboxes for
multi-select. Submit is gated on every question having at least one
selection. Multi-select answers are joined with commas per the SDK
contract.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 1.4: Wire signal through `QtClaudeAgent` and into `ClaudeAssistantWidget`

Forward the permission manager's `question_requested` signal up to the agent, then connect it in the widget.

**Files:**
- Modify: `src/lightfall/claude/agent.py`
- Modify: `src/lightfall/claude/widget.py`

- [ ] **Step 1: Add signal and method on `QtClaudeAgent`**

In `agent.py`, add the signal definition near line 185 (with `permission_requested`):

```python
    permission_requested = Signal(str, str, dict)  # request_id, tool_name, tool_input
    question_requested = Signal(str, list)  # request_id, questions
```

In `__init__`, after the `permission_requested` forwarding (around line 254), add:

```python
            # Forward question requests to our signal
            self._permission_manager.question_requested.connect(
                self.question_requested.emit
            )
```

Add a public method near `respond_to_permission` (around line 502):

```python
    def respond_to_question(
        self,
        request_id: str,
        answers: dict[str, str] | None,
    ) -> None:
        """Provide answers to a pending AskUserQuestion request.

        Pass ``answers=None`` to indicate the user cancelled the question;
        the agent will reply with a Deny so the model knows.
        """
        if self._permission_manager:
            self._permission_manager.respond_to_question(request_id, answers)
```

- [ ] **Step 2: Connect in widget and add state**

In `widget.py`, after `self._pending_permission_widgets` (around line 121), add:

```python
        # request_id -> QuestionRequestWidget
        self._pending_question_widgets: dict[str, "QuestionRequestWidget"] = {}
```

In `_connect_signals` (line 224), after the `permission_requested` connect (line 235), add:

```python
            # AskUserQuestion routes through the same permission manager
            # but uses its own widget and response API.
            self.agent.question_requested.connect(self._on_question_requested)
```

Add the import near the top with the other widget imports:

```python
from lightfall.claude.widgets.permission_request import PermissionRequestWidget
from lightfall.claude.widgets.question_request import QuestionRequestWidget
```

Add three slots after `_cleanup_permission_widget` (around line 462):

```python
    @Slot(str, list)
    def _on_question_requested(
        self, request_id: str, questions: list
    ) -> None:
        """Render an AskUserQuestion in the permission container."""
        widget = QuestionRequestWidget(request_id, questions)
        widget.submitted.connect(self._on_question_submitted)
        widget.cancelled.connect(self._on_question_cancelled)
        self._pending_question_widgets[request_id] = widget
        self._permission_layout.addWidget(widget)
        self._permission_container.show()
        widget.setFocus()

    @Slot(str, dict)
    def _on_question_submitted(
        self, request_id: str, answers: dict
    ) -> None:
        self.agent.respond_to_question(request_id, dict(answers))
        self._cleanup_question_widget(request_id)

    @Slot(str)
    def _on_question_cancelled(self, request_id: str) -> None:
        self.agent.respond_to_question(request_id, None)
        self._cleanup_question_widget(request_id)

    def _cleanup_question_widget(self, request_id: str) -> None:
        widget = self._pending_question_widgets.pop(request_id, None)
        if widget is not None:
            self._permission_layout.removeWidget(widget)
            widget.deleteLater()
        # Hide the container only if nothing else is using it.
        if (
            not self._pending_permission_widgets
            and not self._pending_question_widgets
        ):
            self._permission_container.hide()
```

Also update `_cleanup_permission_widget` (around line 448) to match the new shared hide condition. Replace its final hide block:

```python
        # Hide container if no more pending requests
        if not self._pending_permission_widgets:
            self._permission_container.hide()
```

with:

```python
        # Hide container if no more pending approvals or questions
        if (
            not self._pending_permission_widgets
            and not self._pending_question_widgets
        ):
            self._permission_container.hide()
```

Also update `_on_reset_conversation` (around line 280) — in the cleanup block alongside `_pending_permission_widgets`:

```python
        # Clear any pending permission widgets
        for widget in self._pending_permission_widgets.values():
            widget.deleteLater()
        self._pending_permission_widgets.clear()
        self._pending_tool_names.clear()
        # Clear any pending question widgets
        for widget in self._pending_question_widgets.values():
            widget.deleteLater()
        self._pending_question_widgets.clear()
        self._permission_container.hide()
```

- [ ] **Step 3: Smoke-test that existing claude tests still pass**

Run: `.venv/bin/python -m pytest tests/claude/ tests/ui/panels/claude/ -v`
Expected: all pass (no regressions; new test files included).

- [ ] **Step 4: Commit**

```bash
git add src/lightfall/claude/agent.py src/lightfall/claude/widget.py
git commit -m "Wire AskUserQuestion signal through agent into ClaudeAssistantWidget

QtClaudeAgent now re-exposes PermissionManager.question_requested, with
respond_to_question as the response API. ClaudeAssistantWidget renders
the QuestionRequestWidget in the existing _permission_container,
shared with the approval widgets — same show/hide lifecycle.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

# Phase 2 — Partial streaming

## Task 2.1: Worker `StreamEvent` dispatch and signals

Parse `StreamEvent` from the SDK and emit per-block partial signals.

**Files:**
- Modify: `src/lightfall/claude/_internal/worker.py`
- Create: `tests/claude/test_partial_streaming_worker.py`

- [ ] **Step 1: Write failing test**

Create `tests/claude/test_partial_streaming_worker.py`:

```python
"""PersistentClaudeWorker dispatches StreamEvent into partial_* signals."""
from __future__ import annotations

import asyncio

import pytest

from claude_agent_sdk.types import ResultMessage, StreamEvent
from lightfall.claude._internal.worker import PersistentClaudeWorker


class _StreamStubClient:
    """Stub client that yields StreamEvents then ResultMessage."""

    def __init__(self) -> None:
        self._events: list = []

    def script(self, events: list) -> None:
        self._events = list(events)

    async def connect(self, prompt: str | None = None) -> None:
        pass

    async def query(self, prompt: str) -> None:
        pass

    async def receive_response(self):
        for e in self._events:
            yield e

    async def interrupt(self) -> None:
        pass


def _make_stream_event(event_dict: dict, *, uuid: str = "msg-1") -> StreamEvent:
    return StreamEvent(
        uuid=uuid, session_id="sess", event=event_dict, parent_tool_use_id=None
    )


def _result() -> ResultMessage:
    return ResultMessage(
        subtype="success", duration_ms=0, duration_api_ms=0,
        is_error=False, num_turns=1, session_id="sess",
    )


def test_text_streaming_emits_started_text_finished(qtbot):
    client = _StreamStubClient()
    client.script([
        _make_stream_event({
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "text", "text": ""},
        }),
        _make_stream_event({
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": "Hello "},
        }),
        _make_stream_event({
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": "world"},
        }),
        _make_stream_event({"type": "content_block_stop", "index": 0}),
        _result(),
    ])

    worker = PersistentClaudeWorker(client)
    started: list[tuple[str, str]] = []
    texts: list[tuple[str, str]] = []
    finished: list[str] = []
    worker.partial_block_started.connect(
        lambda bid, kind: started.append((bid, kind))
    )
    worker.partial_text.connect(lambda bid, d: texts.append((bid, d)))
    worker.partial_block_finished.connect(finished.append)

    worker.start()
    try:
        qtbot.waitUntil(lambda: worker._is_connected, timeout=3000)
        with qtbot.waitSignal(worker.query_completed, timeout=3000):
            worker.send_query("hi")
        assert started == [("msg-1:0", "text")]
        assert texts == [("msg-1:0", "Hello "), ("msg-1:0", "world")]
        assert finished == ["msg-1:0"]
    finally:
        worker.stop()
        assert worker.wait(3000)


def test_thinking_streaming_emits_partial_thinking(qtbot):
    client = _StreamStubClient()
    client.script([
        _make_stream_event({
            "type": "content_block_start", "index": 1,
            "content_block": {"type": "thinking"},
        }),
        _make_stream_event({
            "type": "content_block_delta", "index": 1,
            "delta": {"type": "thinking_delta", "thinking": "Let me think…"},
        }),
        _make_stream_event({"type": "content_block_stop", "index": 1}),
        _result(),
    ])

    worker = PersistentClaudeWorker(client)
    thinks: list[tuple[str, str]] = []
    worker.partial_thinking.connect(lambda bid, d: thinks.append((bid, d)))

    worker.start()
    try:
        qtbot.waitUntil(lambda: worker._is_connected, timeout=3000)
        with qtbot.waitSignal(worker.query_completed, timeout=3000):
            worker.send_query("hi")
        assert thinks == [("msg-1:1", "Let me think…")]
    finally:
        worker.stop()
        assert worker.wait(3000)
```

- [ ] **Step 2: Run test to confirm it fails**

Run: `.venv/bin/python -m pytest tests/claude/test_partial_streaming_worker.py -v`
Expected: `AttributeError: 'PersistentClaudeWorker' object has no attribute 'partial_block_started'`.

- [ ] **Step 3: Add signals to `PersistentClaudeWorker`**

In `worker.py`, with the other signal definitions on `PersistentClaudeWorker` (around line 257):

```python
    # Signals
    message_received = Signal(str)
    thinking_received = Signal(str)
    tool_called = Signal(str, dict)
    tool_result = Signal(str, dict)
    error_occurred = Signal(str)
    query_completed = Signal()
    query_cancelled = Signal()
    result_received = Signal(dict)
    connected = Signal()
    # Partial streaming (content_block_* events from StreamEvent)
    partial_block_started = Signal(str, str)  # block_id, kind
    partial_text = Signal(str, str)           # block_id, delta
    partial_thinking = Signal(str, str)       # block_id, delta
    partial_block_finished = Signal(str)      # block_id
```

- [ ] **Step 4: Import `StreamEvent` and add dispatch branch in `_run_query`**

In `worker.py`, expand the deferred import at line ~372 to include `StreamEvent`:

```python
            from claude_agent_sdk.types import (
                AssistantMessage,
                ResultMessage,
                StreamEvent,
                TextBlock,
                ThinkingBlock,
                ToolResultBlock,
                ToolUseBlock,
            )
```

Then, between the `AssistantMessage` branch and the `ResultMessage` branch (around line 452), insert:

```python
                elif isinstance(msg, StreamEvent):
                    self._dispatch_stream_event(msg)
```

Add the dispatcher as a method on `PersistentClaudeWorker` (place it just above `_drain_response_stream`):

```python
    def _dispatch_stream_event(self, msg: Any) -> None:
        """Parse a StreamEvent and emit per-block partial_* signals.

        Block identity is ``{message_uuid}:{event.index}`` so a single
        assistant message's multiple content blocks (text + tool_use, say)
        get distinct ids. Non-text/thinking events (tool input JSON deltas,
        message-level events) are dropped here — the widget renders the
        canonical ``ToolUseBlock`` etc. from the assembled ``AssistantMessage``.
        """
        event = getattr(msg, "event", None) or {}
        event_type = event.get("type", "")
        index = event.get("index")
        if index is None:
            return
        block_id = f"{getattr(msg, 'uuid', '')}:{index}"

        if event_type == "content_block_start":
            block = event.get("content_block", {}) or {}
            kind = block.get("type", "")
            if kind in ("text", "thinking"):
                self.partial_block_started.emit(block_id, kind)
        elif event_type == "content_block_delta":
            delta = event.get("delta", {}) or {}
            dtype = delta.get("type", "")
            if dtype == "text_delta":
                self.partial_text.emit(block_id, delta.get("text", ""))
            elif dtype == "thinking_delta":
                self.partial_thinking.emit(block_id, delta.get("thinking", ""))
        elif event_type == "content_block_stop":
            self.partial_block_finished.emit(block_id)
```

- [ ] **Step 5: Run test to confirm it passes**

Run: `.venv/bin/python -m pytest tests/claude/test_partial_streaming_worker.py -v`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add src/lightfall/claude/_internal/worker.py tests/claude/test_partial_streaming_worker.py
git commit -m "Worker: dispatch StreamEvent into partial_block_* signals

With include_partial_messages=True, the SDK streams per-token deltas
as StreamEvent objects carrying raw Anthropic API events. Parse the
content_block_start / content_block_delta / content_block_stop events
into partial_block_started / partial_text / partial_thinking /
partial_block_finished, keyed by f\"{msg.uuid}:{index}\".

Non-text/thinking events (tool input json_delta, message-level) are
dropped — the widget renders the canonical assembled message for those.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2.2: Suppress duplicate emit of TextBlock / ThinkingBlock in AssistantMessage

When `include_partial_messages=True`, text and thinking arrive twice — once via stream events, again in the assembled `AssistantMessage`. Skip the second emit to avoid double rendering.

**Files:**
- Modify: `src/lightfall/claude/_internal/worker.py:405-450`
- Test: `tests/claude/test_partial_streaming_worker.py` (append)

- [ ] **Step 1: Add failing test**

Append to `tests/claude/test_partial_streaming_worker.py`:

```python
def test_assistant_message_does_not_re_emit_text_when_streamed(qtbot):
    """The full AssistantMessage still arrives after streaming completes.
    With our suppression, message_received fires zero times for the
    streamed TextBlock — partial_* already covered it."""
    from claude_agent_sdk.types import AssistantMessage, TextBlock

    client = _StreamStubClient()
    client.script([
        _make_stream_event({
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "text"},
        }),
        _make_stream_event({
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": "Hello"},
        }),
        _make_stream_event({"type": "content_block_stop", "index": 0}),
        AssistantMessage(content=[TextBlock(text="Hello")], model="claude-sonnet-4-6", parent_tool_use_id=None),
        _result(),
    ])

    worker = PersistentClaudeWorker(client)
    msg_received: list[str] = []
    worker.message_received.connect(msg_received.append)

    worker.start()
    try:
        qtbot.waitUntil(lambda: worker._is_connected, timeout=3000)
        with qtbot.waitSignal(worker.query_completed, timeout=3000):
            worker.send_query("hi")
        # The streamed text was rendered via partial_text; the full
        # AssistantMessage echo must not re-emit it.
        assert msg_received == []
    finally:
        worker.stop()
        assert worker.wait(3000)
```

- [ ] **Step 2: Run test to confirm it fails**

Run: `.venv/bin/python -m pytest tests/claude/test_partial_streaming_worker.py::test_assistant_message_does_not_re_emit_text_when_streamed -v`
Expected: assert fails — `msg_received == ['Hello']`.

- [ ] **Step 3: Suppress text/thinking re-emit**

In `worker.py` `_run_query`, modify the `if isinstance(msg, AssistantMessage):` branch's per-block loop. Replace the `TextBlock` and `ThinkingBlock` arms:

```python
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        # Check cancel between blocks too
                        if self._cancel_requested:
                            logger.info("[sdk-stream] cancel mid-block — draining")
                            await self._drain_response_stream()
                            self.query_cancelled.emit()
                            return

                        if isinstance(block, TextBlock):
                            # With include_partial_messages=True (Lucid's
                            # default), text already arrived as StreamEvent
                            # content_block_delta and rendered live. The full
                            # AssistantMessage echoes it; skip to avoid
                            # double-rendering.
                            logger.info(
                                "[sdk-stream] TextBlock len={} (streamed; skip)",
                                len(block.text or ""),
                            )
                        elif isinstance(block, ThinkingBlock):
                            logger.info(
                                "[sdk-stream] ThinkingBlock len={} (streamed; skip)",
                                len(getattr(block, "thinking", "") or ""),
                            )
                        elif isinstance(block, ToolUseBlock):
                            # ... unchanged ...
```

(Keep `ToolUseBlock` and `ToolResultBlock` branches intact — they still emit `tool_called` / `tool_result`.)

- [ ] **Step 4: Run test to confirm it passes**

Run: `.venv/bin/python -m pytest tests/claude/test_partial_streaming_worker.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/claude/_internal/worker.py tests/claude/test_partial_streaming_worker.py
git commit -m "Worker: skip TextBlock / ThinkingBlock re-emit in AssistantMessage

With include_partial_messages=True (now the panel's default), text and
thinking already arrived as StreamEvents and were rendered live by the
widget. The CLI still emits the assembled AssistantMessage at end of
turn; re-emitting message_received / thinking_received would double-
render the same content.

ToolUseBlock and ToolResultBlock are NOT streamed in the same way and
remain emitted from AssistantMessage as before.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2.3: Enable `include_partial_messages` and forward signals to widget

**Files:**
- Modify: `src/lightfall/claude/agent.py`
- Modify: `src/lightfall/claude/widget.py`

- [ ] **Step 1: Flip the option in `agent.py`**

In `agent.py`, in the `options_dict` definition (around line 298), add:

```python
        options_dict = {
            "plugins": [{"type": "local", "path": str(self._session_plugin_dir)}],
            "mcp_servers": mcp_servers,
            "allowed_tools": allowed_tools,
            "system_prompt": system_prompt,
            "permission_mode": permission_mode,
            "max_turns": max_turns,
            # Opus 4.7's CLI default is --thinking-display omitted, which makes
            # ThinkingBlock.thinking arrive empty. Opt in to summarized text so
            # the agent panel's thinking boxes have content.
            "thinking": {"type": "adaptive", "display": "summarized"},
            # Stream per-token deltas so the chat bubble grows live instead of
            # waiting for the whole block. The worker translates these into
            # partial_* signals and the widget appends as they arrive.
            "include_partial_messages": True,
        }
```

- [ ] **Step 2: Add signals and signal-forwarding on `QtClaudeAgent`**

In `agent.py`, add to the class-level signals (around line 184):

```python
    permission_requested = Signal(str, str, dict)
    question_requested = Signal(str, list)
    # Partial streaming
    partial_block_started = Signal(str, str)
    partial_text = Signal(str, str)
    partial_thinking = Signal(str, str)
    partial_block_finished = Signal(str)
```

In `_ensure_connected`, after the existing `self._worker.<sig>.connect(self.<sig>)` lines (around line 378), add:

```python
        # Partial streaming forwards
        self._worker.partial_block_started.connect(self.partial_block_started)
        self._worker.partial_text.connect(self.partial_text)
        self._worker.partial_thinking.connect(self.partial_thinking)
        self._worker.partial_block_finished.connect(self.partial_block_finished)
```

- [ ] **Step 3: Add streaming bubble state and slots to `ClaudeAssistantWidget`**

In `widget.py`, add a small dataclass at module level (after imports):

```python
from dataclasses import dataclass


@dataclass
class _StreamingBubble:
    """Tracks one in-progress streamed assistant block."""
    kind: str  # "text" or "thinking"
    frame: QWidget
    label: QLabel
    buffer: str = ""
```

(Adjust the `from PySide6.QtWidgets import` line to include `QWidget` if not already present.)

In `ClaudeAssistantWidget.__init__`, after `self._pending_question_widgets` (added in Task 1.4), add:

```python
        # block_id -> _StreamingBubble for in-progress streamed text/thinking.
        self._streaming_bubbles: dict[str, _StreamingBubble] = {}
```

In `_connect_signals`, add:

```python
            # Partial streaming
            self.agent.partial_block_started.connect(self._on_partial_block_started)
            self.agent.partial_text.connect(self._on_partial_text)
            self.agent.partial_thinking.connect(self._on_partial_thinking)
            self.agent.partial_block_finished.connect(self._on_partial_block_finished)
```

Add four new slots (place after `_on_thinking`):

```python
    @Slot(str, str)
    def _on_partial_block_started(self, block_id: str, kind: str) -> None:
        """Begin a streamed text or thinking bubble."""
        if kind == "text":
            colors = self._get_theme_colors()
            frame = self._create_card(
                "",  # filled in as deltas arrive
                accent="#9c27b0",
                label="Claude",
                label_color="#9c27b0",
            )
        elif kind == "thinking":
            frame = self._create_card(
                "", label="Thinking", italic=True, small=True,
            )
        else:
            return
        # _create_card returns the outer QFrame; the body QLabel is the
        # last widget added to its layout by _create_card.
        label = self._find_body_label(frame)
        if label is None:
            return
        self._streaming_bubbles[block_id] = _StreamingBubble(
            kind=kind, frame=frame, label=label, buffer=""
        )
        self._add_widget(frame)

    @Slot(str, str)
    def _on_partial_text(self, block_id: str, delta: str) -> None:
        bubble = self._streaming_bubbles.get(block_id)
        if bubble is None or bubble.kind != "text":
            return
        bubble.buffer += delta
        # Plain text during streaming — markdown render once on finish.
        bubble.label.setText(self._escape_html(bubble.buffer))
        self._scroll_to_bottom_if_needed()

    @Slot(str, str)
    def _on_partial_thinking(self, block_id: str, delta: str) -> None:
        bubble = self._streaming_bubbles.get(block_id)
        if bubble is None or bubble.kind != "thinking":
            return
        bubble.buffer += delta
        bubble.label.setText(self._escape_html(bubble.buffer))
        self._scroll_to_bottom_if_needed()

    @Slot(str)
    def _on_partial_block_finished(self, block_id: str) -> None:
        bubble = self._streaming_bubbles.pop(block_id, None)
        if bubble is None:
            return
        if bubble.kind == "text" and bubble.buffer:
            # One markdown render at end — see spec for the perf rationale.
            from lightfall.claude.markdown import render_markdown
            bubble.label.setText(render_markdown(bubble.buffer))
        # thinking stays plaintext (existing widget style).
```

Add a helper that locates the body label inside a card built by `_create_card`. Place it just after `_create_card` (line ~566):

```python
    @staticmethod
    def _find_body_label(card: QFrame) -> QLabel | None:
        """Return the last QLabel child of a card built by _create_card —
        that's the body label _create_card adds last."""
        labels = card.findChildren(QLabel)
        return labels[-1] if labels else None

    def _scroll_to_bottom_if_needed(self) -> None:
        """Defer a scroll-to-bottom; no-op if user has scrolled up."""
        from PySide6.QtCore import QTimer
        if self._at_bottom:
            QTimer.singleShot(0, self._scroll_to_bottom)
```

Also extend `_on_reset_conversation` (line ~280) — after clearing pending widgets:

```python
        # Clear any in-progress streaming bubbles
        self._streaming_bubbles.clear()
```

(The frame widgets are children of the chat layout and were already deleted in the earlier chat-clear loop.)

- [ ] **Step 4: Smoke test**

Run: `.venv/bin/python -m pytest tests/claude/ tests/ui/panels/claude/ -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/claude/agent.py src/lightfall/claude/widget.py
git commit -m "Enable partial streaming and wire bubbles into the chat panel

Set include_partial_messages=True; QtClaudeAgent re-exposes the four
partial_* signals from the worker. ClaudeAssistantWidget tracks an
in-progress _StreamingBubble per block_id: appends raw text on each
delta (cheap), then re-renders once with full markdown at content_
block_stop (one parse instead of one-per-token).

Thinking blocks stream the same way but stay plaintext at finish, to
match the existing thinking-bubble style.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

# Phase 4 — Task tool progress

## Task 4.1: Worker `Task*Message` branches and signals

Parse the three `SystemMessage` subclasses for Task tool progress.

**Files:**
- Modify: `src/lightfall/claude/_internal/worker.py`
- Create: `tests/claude/test_task_progress_worker.py`

- [ ] **Step 1: Write failing test**

Create `tests/claude/test_task_progress_worker.py`:

```python
"""PersistentClaudeWorker parses Task* messages from the stream."""
from __future__ import annotations

import pytest

from claude_agent_sdk.types import (
    ResultMessage,
    TaskNotificationMessage,
    TaskProgressMessage,
    TaskStartedMessage,
)
from lightfall.claude._internal.worker import PersistentClaudeWorker


class _TaskStubClient:
    def __init__(self) -> None:
        self._events: list = []

    def script(self, events: list) -> None:
        self._events = list(events)

    async def connect(self, prompt: str | None = None) -> None:
        pass

    async def query(self, prompt: str) -> None:
        pass

    async def receive_response(self):
        for e in self._events:
            yield e

    async def interrupt(self) -> None:
        pass


def _result() -> ResultMessage:
    return ResultMessage(
        subtype="success", duration_ms=0, duration_api_ms=0,
        is_error=False, num_turns=1, session_id="sess",
    )


def test_task_lifecycle_signals(qtbot):
    client = _TaskStubClient()
    started_msg = TaskStartedMessage(
        subtype="task_started", data={},
        task_id="t1", description="investigate", uuid="u1",
        session_id="sess", tool_use_id="tool-use-42",
    )
    progress_msg = TaskProgressMessage(
        subtype="task_progress", data={},
        task_id="t1", description="investigate",
        usage={"total_tokens": 1500, "tool_uses": 3, "duration_ms": 100},
        uuid="u2", session_id="sess",
        last_tool_name="Read",
    )
    finished_msg = TaskNotificationMessage(
        subtype="task_notification", data={},
        task_id="t1", status="completed",
        output_file="/tmp/x.jsonl",
        summary="Found three widgets",
        uuid="u3", session_id="sess",
        usage={"total_tokens": 2000, "tool_uses": 5, "duration_ms": 250},
    )
    client.script([started_msg, progress_msg, finished_msg, _result()])

    worker = PersistentClaudeWorker(client)
    started: list[tuple[str, str, str]] = []
    progress: list[tuple[str, str, dict, str]] = []
    finished: list[tuple[str, str, str, str, dict]] = []
    worker.task_started.connect(
        lambda a, b, c: started.append((a, b, c))
    )
    worker.task_progress.connect(
        lambda a, b, c, d: progress.append((a, b, dict(c), d))
    )
    worker.task_finished.connect(
        lambda a, b, c, d, e: finished.append((a, b, c, d, dict(e)))
    )

    worker.start()
    try:
        qtbot.waitUntil(lambda: worker._is_connected, timeout=3000)
        with qtbot.waitSignal(worker.query_completed, timeout=3000):
            worker.send_query("go")

        assert started == [("t1", "investigate", "tool-use-42")]
        assert progress == [(
            "t1", "investigate",
            {"total_tokens": 1500, "tool_uses": 3, "duration_ms": 100},
            "Read",
        )]
        assert finished == [(
            "t1", "completed", "Found three widgets", "/tmp/x.jsonl",
            {"total_tokens": 2000, "tool_uses": 5, "duration_ms": 250},
        )]
    finally:
        worker.stop()
        assert worker.wait(3000)
```

- [ ] **Step 2: Run test to confirm it fails**

Run: `.venv/bin/python -m pytest tests/claude/test_task_progress_worker.py -v`
Expected: `AttributeError: 'PersistentClaudeWorker' object has no attribute 'task_started'`.

- [ ] **Step 3: Add signals**

In `worker.py`, add to `PersistentClaudeWorker` signals:

```python
    # Task tool subagent lifecycle (Task*Message)
    task_started = Signal(str, str, str)            # task_id, description, tool_use_id
    task_progress = Signal(str, str, dict, str)     # task_id, description, usage, last_tool
    task_finished = Signal(str, str, str, str, dict)  # task_id, status, summary, output_file, usage
```

- [ ] **Step 4: Import and dispatch in `_run_query`**

In the deferred import block in `_run_query`, add:

```python
            from claude_agent_sdk.types import (
                AssistantMessage,
                ResultMessage,
                StreamEvent,
                TaskNotificationMessage,
                TaskProgressMessage,
                TaskStartedMessage,
                TextBlock,
                ThinkingBlock,
                ToolResultBlock,
                ToolUseBlock,
            )
```

Add three branches BEFORE the existing `elif isinstance(msg, ResultMessage):` and AFTER the `StreamEvent` branch:

```python
                elif isinstance(msg, TaskStartedMessage):
                    self.task_started.emit(
                        msg.task_id, msg.description, msg.tool_use_id or "",
                    )
                elif isinstance(msg, TaskProgressMessage):
                    self.task_progress.emit(
                        msg.task_id, msg.description,
                        dict(msg.usage) if msg.usage else {},
                        msg.last_tool_name or "",
                    )
                elif isinstance(msg, TaskNotificationMessage):
                    self.task_finished.emit(
                        msg.task_id, msg.status, msg.summary or "",
                        msg.output_file or "",
                        dict(msg.usage) if msg.usage else {},
                    )
```

(Place these before the generic `else: logger.info("unhandled msg type=...")` block.)

- [ ] **Step 5: Run test to confirm it passes**

Run: `.venv/bin/python -m pytest tests/claude/test_task_progress_worker.py -v`
Expected: 1 passed.

- [ ] **Step 6: Commit**

```bash
git add src/lightfall/claude/_internal/worker.py tests/claude/test_task_progress_worker.py
git commit -m "Worker: parse Task*Message into task_started/progress/finished signals

The Task tool dispatches subagents; the CLI surfaces them as
TaskStartedMessage / TaskProgressMessage / TaskNotificationMessage
SystemMessage subclasses. Previously these landed in the 'unhandled
msg type' log branch. Now each emits a typed signal carrying the
fields needed for an inline progress card.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4.2: `TaskCard` widget

**Files:**
- Create: `src/lightfall/claude/widgets/task_card.py`
- Create: `tests/ui/panels/claude/test_task_card_render.py`

- [ ] **Step 1: Write failing test**

Create `tests/ui/panels/claude/test_task_card_render.py`:

```python
"""TaskCard renders status, description, and counters."""
from __future__ import annotations

from lightfall.claude.widgets.task_card import TaskCard


def test_initial_state_is_running(qtbot):
    card = TaskCard("t1", "investigating widget tree")
    qtbot.addWidget(card)
    assert card.task_id == "t1"
    assert card._status == "running"
    assert "investigating widget tree" in card.title_label.text()


def test_update_progress_refreshes_counters(qtbot):
    card = TaskCard("t1", "investigating")
    qtbot.addWidget(card)
    card.update_progress(
        "investigating",
        {"total_tokens": 12345, "tool_uses": 7, "duration_ms": 100},
        "Read",
    )
    # 12,345 should be formatted with a thousands separator.
    assert "12,345" in card.counter_label.text()
    assert "7 tools" in card.counter_label.text()


def test_mark_finished_sets_status_and_summary(qtbot):
    card = TaskCard("t1", "investigating")
    qtbot.addWidget(card)
    card.mark_finished(
        status="completed",
        summary="Found 3 widgets",
        output_file="/tmp/x.jsonl",
        usage={"total_tokens": 2000, "tool_uses": 5, "duration_ms": 200},
    )
    assert card._status == "completed"
    assert "Found 3 widgets" in card.detail_summary.text()
    assert "/tmp/x.jsonl" in card.output_link.text()


def test_unknown_status_falls_back_to_completed(qtbot):
    card = TaskCard("t1", "x")
    qtbot.addWidget(card)
    card.mark_finished("bogus", "", "", {})
    assert card._status == "completed"
```

- [ ] **Step 2: Run test to confirm it fails**

Run: `.venv/bin/python -m pytest tests/ui/panels/claude/test_task_card_render.py -v`
Expected: `ImportError: No module named 'lightfall.claude.widgets.task_card'`.

- [ ] **Step 3: Implement `TaskCard`**

Create `src/lightfall/claude/widgets/task_card.py`:

```python
"""Inline card for one Task tool subagent run.

Updated in place across TaskStartedMessage / TaskProgressMessage /
TaskNotificationMessage. The card lives in the chat flow at the spot
where the subagent was dispatched, so the chronological reading order
matches what actually happened.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

try:
    import qtawesome as qta
except ImportError:  # pragma: no cover - qtawesome is a hard dep elsewhere
    qta = None  # type: ignore[assignment]


class TaskCard(QFrame):
    """Live status card for a single Task subagent run."""

    STATUS_ICONS: dict[str, tuple[str, str]] = {
        "running": ("mdi.loading", "#5fa8d3"),
        "completed": ("mdi.check-circle", "#4caf50"),
        "failed": ("mdi.alert-circle", "#f44336"),
        "stopped": ("mdi.stop-circle", "#ff9800"),
    }

    def __init__(
        self,
        task_id: str,
        description: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.task_id = task_id
        self._expanded = False
        self._description = description
        self._summary = ""
        self._output_file = ""
        self._last_tool = ""
        self._usage: dict = {}
        self._status = "running"

        self._setup_ui()
        self._apply_theme_style()
        self._refresh()

    def _setup_ui(self) -> None:
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        # --- Header row -----------------------------------------------------
        header_row = QHBoxLayout()
        header_row.setSpacing(6)

        self.toggle_btn = QPushButton("▶")
        self.toggle_btn.setFixedSize(20, 20)
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_btn.setStyleSheet("QPushButton { border: none; padding: 0; }")
        self.toggle_btn.clicked.connect(self._toggle)
        header_row.addWidget(self.toggle_btn)

        self.status_label = QLabel()
        self.status_label.setFixedSize(16, 16)
        header_row.addWidget(self.status_label)

        self.title_label = QLabel()
        self.title_label.setTextFormat(Qt.TextFormat.RichText)
        self.title_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        header_row.addWidget(self.title_label, 1)

        self.counter_label = QLabel()
        self.counter_label.setStyleSheet("color: gray; font-size: 9pt;")
        header_row.addWidget(self.counter_label)

        layout.addLayout(header_row)

        # --- Expanded details ----------------------------------------------
        self.details_widget = QWidget()
        self.details_widget.setVisible(False)
        d = QVBoxLayout(self.details_widget)
        d.setContentsMargins(28, 4, 0, 0)
        d.setSpacing(2)

        self.detail_description = QLabel()
        self.detail_description.setWordWrap(True)
        d.addWidget(self.detail_description)

        self.detail_last_tool = QLabel()
        self.detail_last_tool.setStyleSheet("color: gray; font-size: 9pt;")
        d.addWidget(self.detail_last_tool)

        self.detail_summary = QLabel()
        self.detail_summary.setWordWrap(True)
        d.addWidget(self.detail_summary)

        self.output_link = QLabel()
        self.output_link.setOpenExternalLinks(True)
        d.addWidget(self.output_link)

        layout.addWidget(self.details_widget)

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self.details_widget.setVisible(self._expanded)
        self.toggle_btn.setText("▼" if self._expanded else "▶")

    # --- Public update API --------------------------------------------------

    def update_progress(
        self,
        description: str,
        usage: dict,
        last_tool: str,
    ) -> None:
        self._description = description
        if usage:
            self._usage = dict(usage)
        self._last_tool = last_tool
        self._refresh()

    def mark_finished(
        self,
        status: str,
        summary: str,
        output_file: str,
        usage: dict,
    ) -> None:
        self._status = status if status in self.STATUS_ICONS else "completed"
        self._summary = summary
        self._output_file = output_file
        if usage:
            self._usage = dict(usage)
        self._refresh()

    # --- Rendering ----------------------------------------------------------

    def _refresh(self) -> None:
        if qta is not None:
            icon_name, color = self.STATUS_ICONS.get(
                self._status, ("mdi.help", "gray")
            )
            if self._status == "running":
                spin_icon = qta.icon(
                    icon_name, color=color, animation=qta.Spin(self.status_label)
                )
                self.status_label.setPixmap(spin_icon.pixmap(16, 16))
            else:
                self.status_label.setPixmap(
                    qta.icon(icon_name, color=color).pixmap(16, 16)
                )

        truncated = self._truncate(self._description, 60)
        self.title_label.setText(f"<b>Task:</b> {self._escape(truncated)}")

        tokens = self._usage.get("total_tokens", 0)
        tools = self._usage.get("tool_uses", 0)
        if tokens or tools:
            self.counter_label.setText(f"{tokens:,} tokens · {tools} tools")
        else:
            self.counter_label.setText("")

        self.detail_description.setText(self._description)
        self.detail_last_tool.setText(
            f"Last tool: {self._escape(self._last_tool)}" if self._last_tool else ""
        )
        self.detail_summary.setText(self._summary)
        self.output_link.setText(
            f'<a href="file://{self._output_file}">📄 Open transcript</a>'
            if self._output_file else ""
        )

    def _apply_theme_style(self) -> None:
        palette = self.palette()
        is_dark = palette.color(QPalette.ColorRole.Base).lightness() < 128
        if is_dark:
            bg = "rgba(40, 60, 80, 0.4)"
            border = "rgba(80, 120, 160, 0.5)"
        else:
            bg = "rgba(220, 230, 245, 0.55)"
            border = "rgba(120, 160, 200, 0.5)"
        self.setStyleSheet(
            f"""
            TaskCard {{
                background-color: {bg};
                border: 1px solid {border};
                border-radius: 6px;
            }}
            """
        )

    @staticmethod
    def _escape(text: str) -> str:
        return (
            text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )

    @staticmethod
    def _truncate(text: str, n: int) -> str:
        return text if len(text) <= n else text[: n - 1] + "…"
```

- [ ] **Step 4: Run test to confirm it passes**

Run: `.venv/bin/python -m pytest tests/ui/panels/claude/test_task_card_render.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/claude/widgets/task_card.py tests/ui/panels/claude/test_task_card_render.py
git commit -m "Add TaskCard widget for Task tool subagent progress

One card per task_id, updated in place across start/progress/finished.
Collapsed by default: status icon + truncated description + token /
tool counter. Expanded: full description, last tool name, summary,
'Open transcript' link to the output_file.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4.3: Wire `TaskCard` into `ClaudeAssistantWidget`, suppress Task tool_called

**Files:**
- Modify: `src/lightfall/claude/agent.py`
- Modify: `src/lightfall/claude/widget.py`

- [ ] **Step 1: Add signals and forwarding on `QtClaudeAgent`**

In `agent.py`, add to class-level signals:

```python
    task_started = Signal(str, str, str)
    task_progress = Signal(str, str, dict, str)
    task_finished = Signal(str, str, str, str, dict)
```

In `_ensure_connected`, add to the worker signal connects:

```python
        # Task tool subagent forwards
        self._worker.task_started.connect(self.task_started)
        self._worker.task_progress.connect(self.task_progress)
        self._worker.task_finished.connect(self.task_finished)
```

- [ ] **Step 2: Add task-card state and slots in widget**

In `widget.py`, add imports:

```python
from lightfall.claude.widgets.task_card import TaskCard
```

In `ClaudeAssistantWidget.__init__`, after `self._streaming_bubbles` (added in Task 2.3):

```python
        # task_id -> TaskCard
        self._task_cards: dict[str, TaskCard] = {}
        # tool_use_id -> task_id (so the Task tool's tool_called / tool_result
        # can be suppressed in favor of the card)
        self._task_tool_use_ids: dict[str, str] = {}
```

In `_connect_signals`, add:

```python
            # Task tool subagent progress
            self.agent.task_started.connect(self._on_task_started)
            self.agent.task_progress.connect(self._on_task_progress)
            self.agent.task_finished.connect(self._on_task_finished)
```

Add three slots (place near `_on_tool_called`):

```python
    @Slot(str, str, str)
    def _on_task_started(
        self, task_id: str, description: str, tool_use_id: str
    ) -> None:
        card = TaskCard(task_id, description)
        self._task_cards[task_id] = card
        if tool_use_id:
            self._task_tool_use_ids[tool_use_id] = task_id
        self._add_widget(card)

    @Slot(str, str, dict, str)
    def _on_task_progress(
        self, task_id: str, description: str, usage: dict, last_tool: str
    ) -> None:
        card = self._task_cards.get(task_id)
        if card is not None:
            card.update_progress(description, dict(usage), last_tool)

    @Slot(str, str, str, str, dict)
    def _on_task_finished(
        self,
        task_id: str,
        status: str,
        summary: str,
        output_file: str,
        usage: dict,
    ) -> None:
        card = self._task_cards.get(task_id)
        if card is not None:
            card.mark_finished(status, summary, output_file, dict(usage))
```

- [ ] **Step 3: Suppress `_on_tool_called` for the Task tool**

In `widget.py` `_on_tool_called` (line ~340), gate the system-message append:

```python
    @Slot(str, dict)
    def _on_tool_called(self, tool_name: str, tool_input: dict) -> None:
        """Handle tool call."""
        # The Task tool is represented by its own inline card (TaskCard);
        # the generic "Using tool" notice would duplicate that.
        if tool_name == "Task":
            return
        # Simplify tool name for display
        display_name = tool_name.replace("mcp__qt__", "")
        self._append_system_message(f"Using tool: {display_name}")
```

- [ ] **Step 4: Reset state on conversation reset**

In `_on_reset_conversation`, after `self._streaming_bubbles.clear()`:

```python
        # Clear any task card tracking (the widgets themselves are children
        # of the chat layout and were already deleted above).
        self._task_cards.clear()
        self._task_tool_use_ids.clear()
```

- [ ] **Step 5: Smoke test the whole test suite**

Run: `.venv/bin/python -m pytest tests/claude/ tests/ui/panels/claude/ -v`
Expected: all pass — Task 1, 2, and 4 tests plus the original 18.

- [ ] **Step 6: Commit**

```bash
git add src/lightfall/claude/agent.py src/lightfall/claude/widget.py
git commit -m "Render Task tool subagents inline as live TaskCards

QtClaudeAgent re-exposes task_started / task_progress / task_finished;
ClaudeAssistantWidget creates one TaskCard per task_id, updates it on
progress, finalizes it on the notification. _on_tool_called drops the
generic 'Using tool: Task' notice because the card represents both
the work and its outcome.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Final verification

- [ ] **Step 1: Full claude/ test suite**

```bash
.venv/bin/python -m pytest tests/claude/ tests/ui/panels/claude/ -v
```

Expected counts (additive on top of the pre-plan 29):
- `test_question_request.py`: 6 tests
- `test_partial_streaming_worker.py`: 3 tests
- `test_task_progress_worker.py`: 1 test
- `test_question_widget_render.py`: 3 tests
- `test_task_card_render.py`: 4 tests

Total: 46 passed.

- [ ] **Step 2: Push**

```bash
git push origin master
```
