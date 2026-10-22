"""ClaudeAssistantWidget - High-level embeddable chat widget."""

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import qtawesome as qta
import shiboken6
from PySide6.QtCore import QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QKeyEvent, QPalette
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from lightfall.agents.backend import TranscriptEntry
from lightfall.claude.agent import QtClaudeAgent
from lightfall.claude.bus_endpoint import BusCardEntry, BusCardModel, ClaudeSessionEndpoint
from lightfall.claude.voice_queue import VoiceQueue, VoiceQueueStrip
from lightfall.claude.widgets.permission_request import PermissionRequestWidget
from lightfall.claude.widgets.question_request import QuestionRequestWidget
from lightfall.claude.widgets.task_card import TaskCard
from lightfall.ui.preferences.claude_settings import ClaudeSettingsProvider
from lightfall.ui.theme import scaled_pt
from lightfall.utils.crash_diagnostics import gui_thread_only
from lightfall.utils.logging import logger


def _resolve_backend_info(agent=None):
    """The descriptor for ``agent``'s backend (or the selected one, before the agent
    exists); Claude Code's if the lookup fails."""
    from lightfall.agents.backends import info
    from lightfall.agents.backends.select import AGY, CLAUDE_CODE

    try:
        if agent is not None:
            # Match the backend the agent actually built, not a fresh pref read.
            return info.backend_info(AGY if agent._agy else CLAUDE_CODE)
        return info.backend_info()
    except Exception as exc:  # noqa: BLE001 - naming/menus must never break the widget
        logger.warning("Assistant: backend lookup failed ({}); using Claude Code's", exc)
        return info.ClaudeCodeInfo()


@dataclass
class _StreamingBubble:
    """Tracks one in-progress streamed assistant block."""
    kind: str  # "text" or "thinking"
    frame: QWidget
    label: QLabel
    buffer: str = ""


class HeightForWidthWidget(QWidget):
    """Container widget that correctly reports minimumSizeHint for word-wrapped content.

    QScrollArea with widgetResizable=True uses layout.minimumSize() which
    calculates height at the widget's preferred width, not its actual width.
    This causes inflated height when word-wrapped QLabels are present.
    """

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        if self.layout():
            return self.layout().heightForWidth(width)
        return super().heightForWidth(width)

    def minimumSizeHint(self):
        base = super().minimumSizeHint()
        if self.layout() and self.width() > 0:
            h = self.layout().minimumHeightForWidth(self.width())
            return QSize(base.width(), h)
        return base


class _ChatInput(QPlainTextEdit):
    """Multi-line chat input.

    Enter submits the query (emits ``submit_requested``); Shift+Enter inserts a
    newline. The field starts at one line and auto-grows with content up to
    ``_MAX_LINES``, after which it scrolls.
    """

    submit_requested = Signal()

    _MAX_LINES = 6

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Scrollbar is toggled in _adjust_height: hidden while the field
        # auto-grows, shown only once content exceeds _MAX_LINES.
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setTabChangesFocus(True)
        self.document().documentLayout().documentSizeChanged.connect(
            self._adjust_height
        )
        self._adjust_height()

    def showEvent(self, event) -> None:
        # Recompute once the stylesheet/frame are applied: contentsMargins (and
        # thus the frame overhead) are zero until the widget is polished, so the
        # __init__ call alone would leave the field one chrome-height too short
        # and jump on the first keystroke.
        super().showEvent(event)
        self._adjust_height()

    def _line_height(self) -> int:
        return int(self.fontMetrics().lineSpacing())

    def _viewport_content_height(self, lines: int) -> int:
        # The text lines plus the document margin Qt reserves inside the viewport.
        return lines * self._line_height() + 2 * int(self.document().documentMargin())

    def _frame_overhead(self) -> int:
        # Space the frame/border/padding occupy *outside* the scroll viewport.
        # Measured directly so it works whether the border comes from the native
        # frame or from a Qt Style Sheet (which lands in contentsMargins).
        overhead = self.height() - self.viewport().height()
        if overhead > 0:
            return overhead
        margins = self.contentsMargins()
        return margins.top() + margins.bottom() + self.frameWidth() * 2

    def _adjust_height(self) -> None:
        # QPlainTextDocumentLayout reports document height in *lines* (qreal),
        # not pixels.
        doc_lines = self.document().size().height()
        lines = max(1, min(self._MAX_LINES, math.ceil(doc_lines)))
        self.setFixedHeight(
            self._viewport_content_height(lines) + self._frame_overhead()
        )
        # Only allow scrolling once content exceeds the cap.
        self.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
            if doc_lines > self._MAX_LINES
            else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                # Shift+Enter: insert a newline.
                super().keyPressEvent(event)
            else:
                self.submit_requested.emit()
            return
        super().keyPressEvent(event)


class ClaudeAssistantWidget(QWidget):
    """
    A widget that provides a chat interface with Claude.

    This widget embeds a complete chat UI where users can interact with Claude
    about the target Qt window. Claude can see, understand, and interact with
    the target window's widgets.

    Example:
        ```python
        from PySide6.QtWidgets import QApplication, QMainWindow
        from lightfall.claude import ClaudeAssistantWidget

        app = QApplication([])
        window = QMainWindow()

        # Create Claude assistant for the window
        claude = ClaudeAssistantWidget(target_window=window)
        claude.show()

        window.show()
        app.exec()
        ```

    Signals:
        approval_needed(str, str, dict): Emitted when a tool needs user approval.
            Arguments: (request_id, tool_name, tool_input)
        approval_resolved(str, bool): Emitted when user resolves an approval request.
            Arguments: (request_id, was_allowed)
    """

    # Signals
    query_started = Signal()                     # Emitted when a query begins processing
    approval_needed = Signal(str, str, dict)   # request_id, tool_name, tool_input
    approval_resolved = Signal(str, bool)       # request_id, was_allowed
    model_change_requested = Signal(str)   # combo preset the user picked
    effort_change_requested = Signal(str)  # effort level the user picked
    bus_pending_changed = Signal(int)      # pending agent-message card count

    # Snapshotted in __init__ (class defaults cover widgets built without it).
    _backend_info = None
    _speaker: str | None = None
    _sign_in_hint: QFrame | None = None
    _auth_future = None
    _auth_generation = 0
    # Built in __init__; None only for widgets assembled without it (tests).
    voice_queue: VoiceQueue | None = None
    _turn_errored = False

    @property
    def backend_info(self):
        """The ``BackendInfo`` this widget's agent was built with.

        Fixed for the widget's lifetime: a backend switch saved while a reply
        runs only takes effect when the panel rebuilds the widget, so until then
        the tune menu, toasts and names keep describing the live agent.
        """
        if self._backend_info is None:
            self._backend_info = _resolve_backend_info()
        return self._backend_info

    @property
    def speaker_name(self) -> str:
        """The backend's display name ("Claude", "Gemini")."""
        if self._speaker is None:
            self._speaker = self.backend_info.display_name
        return self._speaker

    def __init__(
        self,
        target_window: QWidget,
        api_key: str | None = None,
        api_url: str | None = None,
        cli_path: str | None = None,
        additional_system_prompt: str | None = None,
        permission_mode: str = "default",
        require_approval: bool = True,
        model: str | None = None,
        effort: str | None = None,
        resume: str | None = None,
        disable_betas: bool = False,
        spec=None,
        launched_by: str | None = None,
        parent: QWidget | None = None
    ):
        """
        Initialize the Claude assistant widget.

        Args:
            target_window: The Qt window for Claude to interact with
            api_key: Anthropic API key. Optional if authenticated via `claude login`.
                    Can also be set via ANTHROPIC_API_KEY environment variable.
            api_url: Not used - set ANTHROPIC_BASE_URL environment variable instead
            cli_path: Path to Claude Code CLI executable (auto-detected if not provided)
            additional_system_prompt: Optional additional text to append to the system prompt.
            permission_mode: SDK permission mode ('default', 'acceptEdits', 'bypassPermissions').
            require_approval: If True, show UI approval for tool calls (default True).
                              Set False alongside permission_mode='bypassPermissions' to
                              fully silence prompts.
            launched_by: Bus name of the agent that launched this session via
                         ``mcp__bus__launch_agent``, if any. Seeds the new
                         session's bus endpoint with an auto-accept whitelist
                         for this sender.
            parent: Parent widget

        Note:
            Authentication can be provided in two ways:
            1. API Key: Pass api_key or set ANTHROPIC_API_KEY environment variable
            2. OAuth (subscription): Run `claude login` in terminal to authenticate with your
               Claude Pro/Max subscription. No API key needed after login.
        """
        super().__init__(parent)

        self.target_window = target_window
        self._require_approval = require_approval
        # Read the backend once, with the agent below: see ``backend_info``.
        self._backend_info = _resolve_backend_info()

        # Track pending permission widgets by request_id
        self._pending_permission_widgets: dict[str, PermissionRequestWidget] = {}
        # request_id -> QuestionRequestWidget
        self._pending_question_widgets: dict[str, QuestionRequestWidget] = {}
        # block_id -> _StreamingBubble for in-progress streamed text/thinking.
        self._streaming_bubbles: dict[str, _StreamingBubble] = {}
        # task_id -> TaskCard
        self._task_cards: dict[str, TaskCard] = {}
        # tool_use_id -> task_id (so the Task tool's tool_called / tool_result
        # can be suppressed in favor of the card)
        self._task_tool_use_ids: dict[str, str] = {}
        # Track tool names for "Always Allow" functionality
        self._pending_tool_names: dict[str, str] = {}
        # Thinking / tool-usage fragments, hidden by default; the panel's
        # title-bar toggle flips visibility via set_verbose_visible().
        self._verbose_visible = False
        self._verbose_widgets: list[QWidget] = []

        # Create the agent
        try:
            # ``spec`` selects which agent definition this session runs. The
            # tabbed panel passes one per tab; callers that don't (legacy /
            # tests) get the main "lightfall" agent as before.
            if spec is None:
                from lightfall.agents.registry import AgentSpecRegistry

                spec = AgentSpecRegistry.get_instance().get("lightfall")

            self.agent = QtClaudeAgent(
                target_window,
                api_key,
                api_url,
                cli_path,
                permission_mode=permission_mode,
                max_turns=ClaudeSettingsProvider.get_max_turns(),
                additional_system_prompt=additional_system_prompt,
                require_approval=require_approval,
                model=model,
                effort=effort,
                resume=resume,
                disable_betas=disable_betas,
                spec=spec,
                parent=self,
            )
        except ValueError as e:
            # API key not provided
            self._setup_error_ui(str(e))
            return
        self._backend_info = _resolve_backend_info(self.agent)

        # Bus-message cards: pure-logic bookkeeping (see BusCardModel), plus
        # a card-entry -> live QFrame map so signal handlers can update or
        # remove the right widget.
        self._bus_card_model = BusCardModel()
        self._bus_card_widgets: dict[int, QFrame] = {}

        # Bus endpoint: routes messages from other agents into this session
        # per the spec's delivery policy (default "queue" if no spec).
        self.bus_endpoint = ClaudeSessionEndpoint(
            name=spec.name if spec else "lightfall",
            description=spec.description if spec else "Lightfall assistant",
            submit=self._submit_bus_prompt,
            is_busy=lambda: self.agent.is_busy() or self._is_busy,
            on_queued=lambda sender, message: None,
            policy=spec.on_message if spec else "queue",
            auto_accept_senders={launched_by} if launched_by else None,
            parent=self,
        )
        self.bus_endpoint.message_queued.connect(self._on_bus_message_queued)
        self.bus_endpoint.message_auto_accepted.connect(
            self._on_bus_message_auto_accepted
        )

        # Prompts submitted (by voice) while the agent was busy; see
        # submit_user_prompt. Drains before AgentBus pending messages.
        self.voice_queue = VoiceQueue(self)
        # True once error_occurred fires during the current turn.
        self._turn_errored = False

        # Setup UI
        self._setup_ui()
        self._connect_signals()
        self.agent.query_completed.connect(self._flush_bus_on_completion)
        # A finished turn proves the backend is signed in.
        self.agent.query_completed.connect(self._on_turn_completed_sign_in)
        self._check_sign_in()

    def _setup_ui(self) -> None:
        """Setup the user interface."""
        self.setWindowTitle("Assistant")
        self.setMinimumSize(400, 100)

        # Main layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # Permission request container (appears above conversation)
        self._permission_container = QWidget()
        self._permission_layout = QVBoxLayout(self._permission_container)
        self._permission_layout.setContentsMargins(0, 0, 0, 0)
        self._permission_layout.setSpacing(8)
        self._permission_container.hide()  # Hidden when no pending requests
        layout.addWidget(self._permission_container)

        # Chat display — scroll area with vertical widget layout
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self._chat_container = HeightForWidthWidget()
        self._chat_layout = QVBoxLayout(self._chat_container)
        self._chat_layout.setContentsMargins(0, 0, 0, 0)
        self._chat_layout.setSpacing(4)
        self._chat_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._scroll_area.setWidget(self._chat_container)
        layout.addWidget(self._scroll_area)

        # Autoscroll: track whether user is at bottom before content changes
        self._at_bottom = True
        sb = self._scroll_area.verticalScrollBar()
        sb.valueChanged.connect(self._on_scroll_value_changed)
        sb.rangeChanged.connect(self._on_scroll_range_changed)

        # Voice queue strip: prompts waiting for the agent, just above the input row.
        self._voice_queue_strip = VoiceQueueStrip(self.voice_queue)
        self._voice_queue_strip.send_queued_requested.connect(self._on_send_queued)
        layout.addWidget(self._voice_queue_strip)

        # Input area
        input_layout = QHBoxLayout()
        self._input_layout = input_layout
        input_layout.setAlignment(Qt.AlignmentFlag.AlignBottom)

        self.input_field = _ChatInput()
        self.input_field.setPlaceholderText(
            f"Hi {self.speaker_name}, Tell me about Lightfall...  (Shift+Enter for a new line)"
        )
        self.input_field.submit_requested.connect(self._send_query)
        input_layout.addWidget(self.input_field)

        self.send_button = QPushButton(qta.icon("mdi6.send"), "Send")
        self.send_button.clicked.connect(self._on_send_button_clicked)
        input_layout.addWidget(self.send_button)

        from PySide6.QtWidgets import QMenu, QToolButton
        self.tune_button = QToolButton()
        self.tune_button.setIcon(qta.icon("mdi6.tune-variant"))
        self.tune_button.setFixedWidth(32)
        self.tune_button.setToolTip("Model / reasoning effort")
        self.tune_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.tune_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._tune_menu = QMenu(self.tune_button)
        self._tune_menu.aboutToShow.connect(self._build_tune_menu)
        self.tune_button.setMenu(self._tune_menu)
        input_layout.addWidget(self.tune_button)

        self.reset_button = QPushButton(qta.icon("mdi6.broom"), "")
        self.reset_button.setFixedWidth(32)
        self.reset_button.setToolTip("Reset conversation")
        self.reset_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reset_button.clicked.connect(self._on_reset_conversation)
        input_layout.addWidget(self.reset_button)

        layout.addLayout(input_layout)

        # Track busy state for button toggling
        self._is_busy = False
        # Store default placeholder for restoration
        self._default_placeholder = (
            f"Hi {self.speaker_name}, Tell me about Lightfall...  (Shift+Enter for a new line)"
        )
        # placeholder_flash bookkeeping: (flashed text, placeholder to restore).
        self._flash: tuple[str, str] | None = None
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._end_placeholder_flash)

    def add_input_accessory(self, widget: QWidget) -> None:
        """Insert ``widget`` into the input row, immediately after the Send button."""
        index = self._input_layout.indexOf(self.send_button)
        self._input_layout.insertWidget(index + 1, widget)

    def set_voice_queue(self, queue: VoiceQueue) -> None:
        """Adopt ``queue`` (e.g. handed across a widget rebuild) and show it in the strip."""
        old = self.voice_queue
        if old is queue:
            return
        # Take Qt ownership: the queue was parented to the widget that built it,
        # and that widget's deletion would otherwise delete the queue with it.
        queue.setParent(self)
        self.voice_queue = queue
        self._voice_queue_strip.set_queue(queue)  # also disconnects the old queue
        if old is not None and shiboken6.isValid(old) and old.parent() is self:
            old.deleteLater()

    def placeholder_flash(self, text: str, ms: int = 1500) -> None:
        """Show ``text`` as the input placeholder for ``ms`` milliseconds.

        On expiry the previous placeholder comes back, unless something else
        (e.g. a busy-state change) replaced the flashed text meanwhile.
        """
        current = self.input_field.placeholderText()
        # Chain onto a running flash only while it is still showing; if something
        # (e.g. _set_busy_state) replaced it, the current placeholder is the one
        # to come back to.
        if self._flash is not None and current == self._flash[0]:
            restore = self._flash[1]
        else:
            restore = current
        self._flash = (text, restore)
        self.input_field.setPlaceholderText(text)
        self._flash_timer.start(max(0, int(ms)))

    def _end_placeholder_flash(self) -> None:
        if self._flash is None:
            return
        text, restore = self._flash
        self._flash = None
        if self.input_field.placeholderText() == text:
            self.input_field.setPlaceholderText(restore)

    def _setup_error_ui(self, error_message: str) -> None:
        """Setup error UI when initialization fails."""
        layout = QVBoxLayout(self)
        error_label = QLabel(f"Error: {error_message}")
        error_label.setWordWrap(True)
        error_label.setStyleSheet("color: red; padding: 20px;")
        layout.addWidget(error_label)

    def _connect_signals(self) -> None:
        """Connect agent signals to UI updates."""
        self.agent.message_received.connect(self._on_message)
        self.agent.thinking_received.connect(self._on_thinking)
        self.agent.tool_called.connect(self._on_tool_called)
        self.agent.error_occurred.connect(self._on_error)
        self.agent.query_completed.connect(self._on_query_completed)
        self.agent.query_cancelled.connect(self._on_query_cancelled)

        # AskUserQuestion is interactive, not a permission gate — always
        # connect, regardless of approval mode. In bypassPermissions the
        # user still wants to see clarifying questions; the agent.py
        # mirror of this asymmetry must be kept in sync with this.
        self.agent.question_requested.connect(self._on_question_requested)

        # Standard permission approval signals only when approvals required.
        if self._require_approval:
            self.agent.permission_requested.connect(self._on_permission_requested)
            self.agent.permission_expired.connect(self._on_permission_expired)

        # Partial streaming
        self.agent.partial_block_started.connect(self._on_partial_block_started)
        self.agent.partial_text.connect(self._on_partial_text)
        self.agent.partial_thinking.connect(self._on_partial_thinking)
        self.agent.partial_block_finished.connect(self._on_partial_block_finished)

        # Task tool subagent progress
        self.agent.task_started.connect(self._on_task_started)
        self.agent.task_progress.connect(self._on_task_progress)
        self.agent.task_finished.connect(self._on_task_finished)

    @Slot()
    def _on_send_button_clicked(self) -> None:
        """Handle send/cancel button click."""
        if self._is_busy:
            # Cancel the current query
            self._cancel_query()
        else:
            # Send a new query
            self._send_query()

    @Slot()
    def _send_query(self) -> None:
        """Send the text typed in the input field."""
        prompt = self.input_field.toPlainText().strip()

        if not prompt:
            return

        if self.agent.is_busy():
            self._append_system_message("Please wait for the current query to complete...")
            return

        # Clear input
        self.input_field.clear()
        self._send_prompt(prompt)

    def _send_prompt(self, prompt: str) -> None:
        """Show ``prompt`` as a user message and start a turn with it."""
        # Show user message
        self._append_user_message(prompt)
        self._turn_errored = False

        # Set busy state - disable input and change button to Cancel
        self._set_busy_state(True, f"{self.speaker_name} is thinking...")
        self.query_started.emit()

        # Send to Claude (non-blocking)
        self.agent.query_sync(prompt)

    def _cancel_query(self) -> None:
        """Cancel the current query."""
        if self.agent.cancel():
            self._set_busy_state(True, "Cancelling...")
            self.send_button.setEnabled(False)  # Disable while cancelling
        else:
            self._append_system_message("No query to cancel")

    def _on_reset_conversation(self) -> None:
        """Reset the conversation — clear chat and start fresh."""
        # Stop any in-progress query
        self.agent.reset_conversation()

        # Clear all chat messages
        while self._chat_layout.count():
            item = self._chat_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Clear any pending permission widgets
        for widget in self._pending_permission_widgets.values():
            widget.deleteLater()
        self._pending_permission_widgets.clear()
        self._pending_tool_names.clear()
        # Clear any pending question widgets
        for widget in self._pending_question_widgets.values():
            widget.deleteLater()
        self._pending_question_widgets.clear()
        # Clear any in-progress streaming bubbles
        self._streaming_bubbles.clear()
        # Verbose fragments were children of the chat layout (deleted above)
        self._verbose_widgets.clear()
        # Clear any task card tracking (the widgets themselves are children
        # of the chat layout and were already deleted above).
        self._task_cards.clear()
        self._task_tool_use_ids.clear()
        self._permission_container.hide()
        # Bus-message cards are children of the chat layout too (already
        # deleted above) -- drop the tracking, then re-render a fresh card
        # for every message still queued in the endpoint. Those messages
        # were never delivered, so they must stay actionable even though
        # the old transcript (and any auto/dismissed historical cards) is
        # gone. Rebuild the model from the endpoint's current pending list
        # so indices line up for later accept(index)/dismiss(index) calls.
        self._bus_card_widgets.clear()
        pending_entries = self._bus_card_model.rebuild_from_pending(
            self.bus_endpoint.pending()
        )
        for entry in pending_entries:
            frame = self._append_bus_message_card(
                entry.sender, entry.message, auto=False, entry=entry
            )
            self._bus_card_widgets[id(entry)] = frame
        self.bus_pending_changed.emit(self._bus_card_model.pending_count())

        # Reset busy state
        self._set_busy_state(False)

        # Show confirmation
        self._append_system_message("Conversation reset")

    def load_transcript(self, messages: list) -> None:
        """Repaint a restored session's conversation into the chat.

        Resume restores the model's context; the chat itself keeps no history
        model, so we re-render user prompts + assistant text + tool-call chips
        from the SDK SessionMessage list.
        """
        from lightfall.claude.transcript import extract_message_text

        for sm in messages:
            if isinstance(sm, TranscriptEntry):
                role, text, tools = sm.role, sm.text, sm.tool_names
            else:
                role, text, tools = extract_message_text(sm)
            if role == "user":
                if text:
                    self._append_user_message(text)
            elif role == "assistant":
                if text:
                    self._append_assistant_message(text)
                for tool in tools:
                    self._register_verbose_widget(
                        self._append_system_message(
                            f"⚙ {self._format_tool_name(tool)}"
                        )
                    )
        self._append_system_message("— restored session —")

    def _build_tune_menu(self) -> None:
        """Populate the input-row model/effort popup from current settings."""
        from lightfall.agents.backends.info import ModelOption
        menu = self._tune_menu
        menu.clear()
        info = self.backend_info
        current_model = info.current_model()
        current_effort = info.current_effort()

        menu.addSection("Model (live)" if info.caps.live_set_model else "Model (next turn)")
        models = list(info.cached_models())
        if len(models) <= 1:
            self._warm_models(info)  # cold cache: fill it for the next open
        if current_model and all(opt.id != current_model for opt in models):
            # A custom id, or one not (yet) in the cache: show it as chosen.
            models.append(ModelOption(current_model, current_model))
        for opt in models:
            act = menu.addAction(opt.label)
            act.setCheckable(True)
            act.setChecked(opt.id == current_model)
            act.triggered.connect(
                lambda _c=False, v=opt.id: self.model_change_requested.emit(v)
            )
        refresh_act = menu.addAction("↻ Refresh models")
        refresh_act.triggered.connect(self._refresh_models)

        menu.addSection("Effort (next turn)" if info.caps.live_set_effort
                        else "Effort (restarts conversation)")
        # The backend knows which levels the model offers (Claude: xhigh/max
        # are Opus-only; agy: the levels `agy models` lists for the model).
        for level in info.effort_options(current_model):
            act = menu.addAction(level or "Default")
            act.setCheckable(True)
            act.setChecked(level == current_effort)
            act.triggered.connect(
                lambda _c=False, lv=level: self.effort_change_requested.emit(lv)
            )

        menu.addSection("Agent bus")
        auto_respond_act = menu.addAction("Auto-accept agent messages")
        auto_respond_act.setCheckable(True)
        auto_respond_act.setChecked(self.bus_endpoint.policy == "auto")
        auto_respond_act.triggered.connect(self._on_toggle_auto_respond)

    def _on_toggle_auto_respond(self, checked: bool) -> None:
        """Settings-menu toggle: switch the bus endpoint's delivery policy."""
        self.bus_endpoint.set_policy("auto" if checked else "queue")

    def submit_user_prompt(
        self, text: str, *, source: str = "voice"
    ) -> Literal["answered", "sent", "queued", "ignored"]:
        """Submit ``text`` as a user prompt without touching the input field.

        - ``"ignored"``: ``text`` is blank, or a question card is showing but
          ``text`` gave it nothing usable (e.g. just "and"). An unresolved card
          always consumes the text: it is never also sent or queued, since a
          prompt sent mid-question would reach the agent out of turn.
        - ``"answered"``: a question card is showing; ``text`` answered the current
          question of the oldest one (whether or not that completed the card).
        - ``"sent"``: the session was idle (not cancelling, no permission card,
          nothing queued), so a turn started with ``text``.
        - ``"queued"``: otherwise; it waits in ``voice_queue``.
        """
        text = text.strip()
        if not text:
            return "ignored"
        queue = self.voice_queue
        for card in list(self._pending_question_widgets.values()):
            if card.is_resolved:
                continue
            result = card.answer_text(text)
            return "ignored" if result == "rejected" else "answered"
        if self._can_send_now() and (queue is None or len(queue) == 0):
            self._send_prompt(text)
            return "sent"
        if queue is None:
            return "ignored"
        queue.push(text)
        logger.debug("Assistant: queued {} prompt ({} waiting)", source, len(queue))
        return "queued"

    def _can_send_now(self) -> bool:
        """Idle: no turn running or cancelling, and no permission card waiting."""
        return not self._is_busy and not self._pending_permission_widgets

    def _drain_voice_queue(self) -> None:
        """Send the next queued prompt, if not paused or held and the session is idle."""
        queue = self.voice_queue
        if queue is None or queue.paused or queue.held or not self._can_send_now():
            return
        item = queue.pop_next()
        if item is not None:
            self._send_prompt(item.text)

    def drain_voice_queue(self) -> None:
        """Send the next queued voice prompt if the session is idle (e.g. right
        after a queue was handed over to this widget)."""
        self._drain_voice_queue()

    def show_system_notice(self, text: str) -> None:
        """Append ``text`` to the chat as a system notice."""
        self._append_system_message(text)

    @Slot()
    def _on_send_queued(self) -> None:
        """The strip's "Send queued" button: lift the pause (and any hold) and
        send one."""
        if self.voice_queue is not None:
            self.voice_queue.resume()
            self.voice_queue.release()
        self._drain_voice_queue()

    def _submit_bus_prompt(self, text: str) -> bool:
        """Inject a bus-delivered prompt into the session, as if the user typed it.

        Returns False (without side effects) if a query is already in flight,
        or if the user has an in-progress draft in the input field (the
        endpoint re-queues the message and the banner reflects the residue).
        """
        text = text.strip()
        if not text:
            return False
        if self.agent.is_busy() or self._is_busy:
            return False
        if self.input_field.toPlainText().strip():
            return False
        self._send_prompt(text)
        return True

    def _flush_bus_on_completion(self) -> None:
        """When a query finishes, flush bus messages queued while busy.

        "auto" policy: flush everything queued (existing behavior). "queue"
        policy: only flush messages from senders whitelisted via "Always
        accept" -- everything else stays as an interactive card awaiting
        the user's Accept/Dismiss. Either way, the badge count is
        re-emitted afterward so it stays truthful (the per-message
        ``message_auto_accepted`` handler already emits it per drained
        message, but this final emission guarantees the settled count).

        The voice queue goes first: while it holds unpaused prompts, bus
        messages wait for a later completion.
        """
        queue = self.voice_queue
        voice_first = (
            queue is not None and len(queue) and not queue.paused and not queue.held
        )
        if not voice_first:
            if self.bus_endpoint.policy == "auto":
                self.bus_endpoint.flush_pending()
            else:
                self.bus_endpoint.flush_whitelisted()
        self.bus_pending_changed.emit(self._bus_card_model.pending_count())

    def _on_bus_message_queued(self, sender: str, message: str) -> None:
        """A message is pending (either "queue" policy, or "auto" policy
        with the session busy) -- render an interactive Accept/Dismiss card."""
        entry = self._bus_card_model.add_pending(sender, message)
        frame = self._append_bus_message_card(sender, message, auto=False, entry=entry)
        self._bus_card_widgets[id(entry)] = frame
        self.bus_pending_changed.emit(self._bus_card_model.pending_count())

    def _on_bus_message_auto_accepted(self, sender: str, message: str) -> None:
        """A message was auto-submitted -- render a non-interactive card.

        If this message already had a pending card (queued while busy under
        "auto" policy, now flushed on completion), replace that stale card
        instead of leaving two cards for the same message.
        """
        stale_entry = self._bus_card_model.take_pending(sender, message)
        if stale_entry is not None:
            stale_frame = self._bus_card_widgets.pop(id(stale_entry), None)
            if stale_frame is not None:
                stale_frame.deleteLater()

        entry = self._bus_card_model.add_auto(sender, message)
        frame = self._append_bus_message_card(sender, message, auto=True, entry=entry)
        self._bus_card_widgets[id(entry)] = frame
        self.bus_pending_changed.emit(self._bus_card_model.pending_count())

    def _append_bus_message_card(
        self, sender: str, message: str, *, auto: bool, entry: BusCardEntry
    ) -> QFrame:
        """Render one inline agent-message card into the chat transcript.

        The transcript is already a QVBoxLayout stack of live widgets (see
        _create_card / _add_widget, and TaskCard/PermissionRequestWidget for
        precedent) rather than a single HTML QTextEdit, so an interactive
        card with real buttons drops straight into that same mechanism --
        no separate widget region is needed.
        """
        palette = self.palette()
        base = palette.color(QPalette.ColorRole.Base)
        is_dark = base.lightness() < 128
        bg = "#2a2a2a" if is_dark else "#f5f5f5"

        card = QFrame()
        card.setObjectName("busMessageCard")
        card.setFrameShape(QFrame.Shape.NoFrame)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        card.setStyleSheet(
            f"QFrame#busMessageCard {{ background: {bg}; border-left: 4px solid #f9a825; "
            f"border-radius: 4px; padding: 8px 12px; }}"
        )

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(4)

        timestamp = datetime.now().strftime("%H:%M")
        header = QLabel(f"⚡ from {sender} · {timestamp}")
        header.setStyleSheet(
            f"font-weight: bold; font-size: {scaled_pt(8)}pt; color: #f9a825; "
            f"letter-spacing: 1px;"
        )
        card_layout.addWidget(header)

        body = QLabel(self._escape_html(message))
        body.setWordWrap(True)
        body.setTextFormat(Qt.TextFormat.RichText)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        card_layout.addWidget(body)

        if auto:
            note = QLabel("auto-accepted")
            note.setStyleSheet(
                f"color: #888; font-style: italic; font-size: {scaled_pt(8)}pt;"
            )
            card_layout.addWidget(note)
        else:
            btn_row = QHBoxLayout()
            btn_row.setContentsMargins(0, 4, 0, 0)
            btn_row.setSpacing(8)
            accept_btn = QPushButton("Accept")
            always_accept_btn = QPushButton("Always accept")
            dismiss_btn = QPushButton("Dismiss")
            btn_row.addWidget(accept_btn)
            btn_row.addWidget(always_accept_btn)
            btn_row.addWidget(dismiss_btn)
            btn_row.addStretch(1)
            card_layout.addLayout(btn_row)

            busy_note = QLabel("")
            busy_note.setStyleSheet(
                f"color: #ff6666; font-size: {scaled_pt(8)}pt;"
            )
            busy_note.hide()
            card_layout.addWidget(busy_note)

            accept_btn.clicked.connect(
                lambda: self._on_bus_card_accept(entry, card, busy_note)
            )
            always_accept_btn.clicked.connect(
                lambda: self._on_bus_card_always_accept(entry, sender)
            )
            dismiss_btn.clicked.connect(
                lambda: self._on_bus_card_dismiss(entry, card)
            )

        self._add_widget(card)
        return card

    def _on_bus_card_accept(
        self, entry: BusCardEntry, card: QFrame, busy_note: QLabel
    ) -> None:
        """Accept button: submit this card's message via the endpoint.

        On refusal (session went busy between render and click) the card
        stays and shows a transient note; on success the card is removed.
        """
        index = self._bus_card_model.pending_index_of(entry)
        if index is None:
            return
        if self.bus_endpoint.accept(index):
            self._bus_card_model.remove(entry)
            self._bus_card_widgets.pop(id(entry), None)
            card.deleteLater()
        else:
            busy_note.setText("agent busy — try again")
            busy_note.show()
        self.bus_pending_changed.emit(self._bus_card_model.pending_count())

    def _on_bus_card_always_accept(self, entry: BusCardEntry, sender: str) -> None:
        """Always accept button: whitelist ``sender`` and drain their
        already-pending messages.

        ``endpoint.always_accept`` emits ``message_auto_accepted`` per
        drained message *synchronously*, and that signal is already wired
        (in ``__init__``) to ``_on_bus_message_auto_accepted``, which does
        the FIFO ``take_pending`` + ``add_auto`` swap -- removing each
        drained message's stale interactive card (including this one, if
        its message drains) and replacing it with a non-interactive "auto"
        card. So by the time ``always_accept`` returns here, the model is
        already reconciled for the common case.

        ``prune_pending_not_in`` runs afterward purely as a safety net for
        an edge case ``take_pending``'s FIFO match can't disambiguate:
        duplicate (sender, message) pairs among this sender's pending cards.
        It removes any leftover stale pending entries not in the endpoint's
        post-drain ``pending()`` list and deletes their frames. This must
        run after the drain (not before/instead of it) so it reconciles
        against the *post-drain* pending list.
        """
        self.bus_endpoint.always_accept(sender)
        stale = self._bus_card_model.prune_pending_not_in(self.bus_endpoint.pending())
        for stale_entry in stale:
            stale_frame = self._bus_card_widgets.pop(id(stale_entry), None)
            if stale_frame is not None:
                stale_frame.deleteLater()
        self.bus_pending_changed.emit(self._bus_card_model.pending_count())

    def _on_bus_card_dismiss(self, entry: BusCardEntry, card: QFrame) -> None:
        """Dismiss button: drop the message without submitting; grey the card."""
        index = self._bus_card_model.pending_index_of(entry)
        if index is not None:
            self.bus_endpoint.dismiss(index)
        self._bus_card_model.mark_dismissed(entry)
        card.setEnabled(False)
        effect = QGraphicsOpacityEffect(card)
        effect.setOpacity(0.4)
        card.setGraphicsEffect(effect)
        self.bus_pending_changed.emit(self._bus_card_model.pending_count())

    def _warm_models(self, info) -> None:
        """Fill a cold model cache once, off the GUI thread (no-op while one runs)."""
        from lightfall.utils.threads import QThreadFuture

        running = getattr(self, "_models_future", None)
        if running is not None and running.isRunning():
            return
        self._models_future = QThreadFuture(
            lambda: info.list_models(),
            except_slot=lambda exc: logger.info("Model list warm-up failed: {}", exc),
            name="assistant-warm-models", log_exceptions=False,
        )
        self._models_future.start()

    def _refresh_models(self) -> None:
        """Re-query the backend's model list off the GUI thread.

        The next menu open shows the refreshed cache.
        """
        from lightfall.utils.threads import QThreadFuture

        info = self.backend_info
        self._models_future = QThreadFuture(
            lambda: info.list_models(refresh=True),
            except_slot=lambda exc: logger.info("Model refresh failed: {}", exc),
            name="assistant-refresh-models", log_exceptions=False,
        )
        self._models_future.start()

    # --- sign-in hint (agy) ------------------------------------------------------

    def _check_sign_in(self, refresh: bool = False) -> None:
        """agy only: check sign-in off the GUI thread; hint if signed out.

        Claude's credentials are checked before the widget is built. agy's check
        runs ``agy models`` (up to 30 s), so without this a signed-out user only
        learns it from the first turn's error.
        """
        from lightfall.agents.backends.select import AGY
        from lightfall.utils.threads import QThreadFuture

        info = self.backend_info
        if info.kind != AGY:
            return
        self._auth_generation += 1
        gen = self._auth_generation
        self._auth_future = QThreadFuture(
            lambda: info.auth_status(refresh=refresh),
            callback_slot=lambda status, g=gen: self._on_auth_status(status, g),
            except_slot=lambda exc: logger.info("Assistant sign-in check failed: {}", exc),
            name="assistant-auth-check", log_exceptions=False,
        )
        self._auth_future.start()

    @gui_thread_only
    def _on_auth_status(self, status, generation: int) -> None:
        """Result of ``_check_sign_in``; dropped if superseded or the widget is gone."""
        if not shiboken6.isValid(self) or generation != self._auth_generation:
            return
        if status.ok:
            self._clear_sign_in_hint()
        else:
            self._show_sign_in_hint(status.detail)

    def _on_turn_completed_sign_in(self) -> None:
        """A turn worked, so agy is signed in: drop the hint and any check still out."""
        self._auth_generation += 1
        self._clear_sign_in_hint()

    def _show_sign_in_hint(self, detail: str) -> None:
        hint = self._sign_in_hint
        if hint is not None and shiboken6.isValid(hint):
            return
        name = self.backend_info.display_name
        card = QFrame()
        card.setObjectName("assistantSignInHint")
        card.setStyleSheet(
            "QFrame#assistantSignInHint { border-left: 4px solid #f9a825; "
            "border-radius: 4px; padding: 8px 12px; }"
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        # AgyInfo.auth_status reports a real sign-in failure as "Not signed in: ...";
        # other failures (agy missing, a timed-out check) must not blame sign-in.
        action = (f"Sign in to {name}" if detail.startswith("Not signed in")
                  else f"Check {name}")
        text = QLabel(f"{detail}\n{action} in Settings → Assistant.")
        text.setWordWrap(True)
        layout.addWidget(text)
        row = QHBoxLayout()
        button = QPushButton("Open Settings…")
        button.clicked.connect(self._open_assistant_settings)
        row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        self._sign_in_hint = card
        self._add_widget(card)

    def _clear_sign_in_hint(self) -> None:
        hint, self._sign_in_hint = self._sign_in_hint, None
        if hint is not None and shiboken6.isValid(hint):
            hint.hide()
            hint.setParent(None)
            hint.deleteLater()

    def _open_assistant_settings(self) -> None:
        """Open Settings → Assistant, then check sign-in again (bypassing the cache)."""
        from lightfall.ui.preferences import dialog as prefs_dialog

        prefs_dialog.PreferencesDialog(self.window(), initial_page="claude").exec()
        self._check_sign_in(refresh=True)

    def _set_busy_state(self, busy: bool, status_text: str = "") -> None:
        """Set the busy state of the widget.

        Args:
            busy: True if a query is in progress.
            status_text: Text to show in the input field placeholder.
        """
        self._is_busy = busy

        if busy:
            # Disable input, show status as placeholder
            self.input_field.setEnabled(False)
            self.input_field.setPlaceholderText(status_text or "Processing...")
            self.send_button.setIcon(qta.icon("mdi6.stop"))
            self.send_button.setText("Cancel")
            self.send_button.setEnabled(True)
            self.reset_button.setEnabled(False)
            self.tune_button.setEnabled(False)
        else:
            # Enable input, restore placeholder, change button back
            self.input_field.setEnabled(True)
            self.input_field.setPlaceholderText(self._default_placeholder)
            self.send_button.setIcon(qta.icon("mdi6.send"))
            self.send_button.setText("Send")
            self.send_button.setEnabled(True)
            self.reset_button.setEnabled(True)
            self.tune_button.setEnabled(True)
            self.input_field.setFocus()

    @Slot(str)
    def _on_message(self, message: str) -> None:
        """Handle message from Claude."""
        self._append_assistant_message(message)

    @Slot(str)
    def _on_thinking(self, thinking: str) -> None:
        """Handle thinking block from Claude."""
        self._append_thinking_message(thinking)

    @Slot(str, str)
    def _on_partial_block_started(self, block_id: str, kind: str) -> None:
        """Begin a streamed text or thinking bubble."""
        if kind == "text":
            frame = self._create_card(
                "",  # filled in as deltas arrive
                accent="#9c27b0",
                label=self.speaker_name,
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
        if kind == "thinking":
            self._register_verbose_widget(frame)
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
        if not bubble.buffer:
            # Empty bubble — no content ever arrived. Remove the ghost
            # card so the AssistantMessage path's card stands alone.
            if bubble.frame in self._verbose_widgets:
                self._verbose_widgets.remove(bubble.frame)
            bubble.frame.deleteLater()
            return
        if bubble.kind == "text":
            # One markdown render at end — see spec for the perf rationale.
            from lightfall.claude.markdown import render_markdown
            bubble.label.setText(render_markdown(bubble.buffer))
        elif bubble.kind == "thinking":
            # Thinking stays plaintext, but re-render once without the trailing
            # newline(s) the model appends — otherwise _escape_html's "\n"->"<br>"
            # leaves a stray blank line at the end of the fragment.
            bubble.label.setText(self._escape_html(bubble.buffer.rstrip()))

    @Slot(str, dict)
    def _on_tool_called(self, tool_name: str, tool_input: dict) -> None:
        """Handle tool call."""
        # The Task tool is represented by its own inline card (TaskCard);
        # the generic "Using tool" notice would duplicate that.
        if tool_name == "Task":
            return
        # Simplify tool name for display
        display_name = tool_name.replace("mcp__qt__", "")
        self._register_verbose_widget(
            self._append_system_message(f"Using tool: {display_name}")
        )

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

    @Slot(str)
    def _on_error(self, error: str) -> None:
        """Handle error."""
        self._turn_errored = True
        # Don't fire queued prompts into back-to-back failures (e.g. auth).
        if self.voice_queue is not None:
            self.voice_queue.pause()
        self._append_error_message(error)
        self._set_busy_state(False)

    def _finalize_open_fragments(self, cancelled: bool) -> None:
        """Close what a turn left open: render streaming bubbles; on cancel, stop task cards.

        Normal completion leaves task cards alone: a background subagent can
        still report after the turn ends.
        """
        for block_id in list(self._streaming_bubbles):
            self._on_partial_block_finished(block_id)
        if cancelled:
            for card in self._task_cards.values():
                if card.is_running:
                    card.mark_finished("stopped", "Cancelled", "", {})

    @Slot()
    def _on_query_completed(self) -> None:
        """Handle query completion."""
        self._finalize_open_fragments(cancelled=False)
        self._withdraw_stale_cards()
        self._set_busy_state(False)
        # A turn that finished without error lifts a sticky error pause.
        if not self._turn_errored and self.voice_queue is not None:
            self.voice_queue.resume()
        # Runs before _flush_bus_on_completion (connected later), so the
        # voice queue drains ahead of AgentBus messages.
        self._drain_voice_queue()

    @Slot()
    def _on_query_cancelled(self) -> None:
        """Handle query cancellation."""
        self._finalize_open_fragments(cancelled=True)
        self._append_system_message("Query cancelled")
        self._withdraw_stale_cards()
        self._set_busy_state(False)
        # Stop keeps the queue; the next prompt goes once the stop settles.
        self._drain_voice_queue()

    # --- Permission handling ---

    @Slot(str, str, dict)
    def _on_permission_requested(
        self,
        request_id: str,
        tool_name: str,
        tool_input: dict
    ) -> None:
        """
        Handle permission request from the agent.

        Creates an inline permission widget and shows it.
        """
        # Store tool name for "Always Allow" functionality
        self._pending_tool_names[request_id] = tool_name

        # Create permission widget
        widget = PermissionRequestWidget(request_id, tool_name, tool_input)
        widget.allowed.connect(self._on_permission_allowed)
        widget.denied.connect(self._on_permission_denied)

        # Track the widget
        self._pending_permission_widgets[request_id] = widget

        # Add to container and show
        self._permission_layout.addWidget(widget)
        self._permission_container.show()

        # Update status in input field placeholder
        display_name = self._format_tool_name(tool_name)
        self.input_field.setPlaceholderText(f"Awaiting approval: {display_name}")

        # Add message to chat about the pending request
        self._append_system_message(f"Tool '{display_name}' is requesting permission...")

        # Emit signal for external listeners
        self.approval_needed.emit(request_id, tool_name, tool_input)

        # Focus the widget for keyboard shortcuts
        widget.setFocus()

    @Slot(str, bool)
    def _on_permission_allowed(self, request_id: str, always_allow: bool) -> None:
        """Handle user allowing a permission request."""
        tool_name = self._pending_tool_names.get(request_id, "")

        # If "Always Allow" was selected, add to the list
        if always_allow and tool_name:
            self.agent.add_always_allowed_tool(tool_name)

        # Respond to the agent
        self.agent.respond_to_permission(request_id, allowed=True, always=always_allow)

        # Update chat
        display_name = self._format_tool_name(tool_name)
        status = "Always Allowed" if always_allow else "Allowed"
        self._append_system_message(f"\u2713 {status}: {display_name}")

        # Emit signal for external listeners
        self.approval_resolved.emit(request_id, True)

        # Clean up
        self._cleanup_permission_widget(request_id)

    @Slot(str, str)
    def _on_permission_denied(self, request_id: str, reason: str) -> None:
        """Handle user denying a permission request."""
        tool_name = self._pending_tool_names.get(request_id, "")

        # Respond to the agent
        self.agent.respond_to_permission(
            request_id, allowed=False, message=reason
        )

        # Update chat
        display_name = self._format_tool_name(tool_name)
        self._append_system_message(f"\u2717 Denied: {display_name}")

        # Emit signal for external listeners
        self.approval_resolved.emit(request_id, False)

        # Clean up
        self._cleanup_permission_widget(request_id)

    @Slot(str)
    def _on_permission_expired(self, request_id: str) -> None:
        """A request ended unanswered (timed out or cancelled): withdraw its card."""
        tool_name = self._pending_tool_names.get(request_id)
        if request_id not in self._pending_permission_widgets:
            return
        display_name = self._format_tool_name(tool_name) if tool_name else "tool"
        self._append_system_message(f"✗ Approval expired: {display_name}")
        self.approval_resolved.emit(request_id, False)
        self._cleanup_permission_widget(request_id)

    def _cleanup_permission_widget(self, request_id: str) -> None:
        """Remove a permission widget after it's been resolved."""
        # Remove from tracking
        self._pending_tool_names.pop(request_id, None)
        widget = self._pending_permission_widgets.pop(request_id, None)

        if widget:
            # Remove from layout (will be deleted)
            self._permission_layout.removeWidget(widget)
            widget.deleteLater()

        # Hide container if no more pending approvals or questions
        if (
            not self._pending_permission_widgets
            and not self._pending_question_widgets
        ):
            self._permission_container.hide()
            # Update placeholder to show working state
            self.input_field.setPlaceholderText(f"{self.speaker_name} is working...")
        # A card resolved while idle may have been the only thing holding the queue.
        self._drain_voice_queue()

    def _withdraw_stale_cards(self) -> None:
        """Drop permission/question cards left over from a turn that has ended.

        A Stop releases pending permission waits without emitting
        ``permission_expired``, so without this the card would linger and keep
        ``_can_send_now`` False forever.
        """
        for request_id in list(getattr(self, "_pending_permission_widgets", {})):
            self.approval_resolved.emit(request_id, False)
            self._cleanup_permission_widget(request_id)
        for request_id in list(getattr(self, "_pending_question_widgets", {})):
            self._cleanup_question_widget(request_id)

    @Slot(str, list)
    def _on_question_requested(
        self, request_id: str, questions: list
    ) -> None:
        """Render an AskUserQuestion in the permission container."""
        widget = QuestionRequestWidget(request_id, questions, speaker=self.speaker_name)
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

    def _format_tool_name(self, name: str) -> str:
        """Format tool name for display (strip MCP prefixes)."""
        if name.startswith("mcp__"):
            parts = name.split("__")
            if len(parts) >= 3:
                return parts[-1]
        return name

    def _get_theme_colors(self) -> dict:
        """Get theme-aware colors from the system palette."""
        palette = self.palette()

        # Get base colors
        text_color = palette.color(QPalette.ColorRole.Text).name()
        link_color = palette.color(QPalette.ColorRole.Link).name()

        # Create muted version for system messages (mix with background)
        text_rgb = palette.color(QPalette.ColorRole.Text)
        bg_rgb = palette.color(QPalette.ColorRole.Base)

        # Blend text with background for muted color
        muted_r = int(text_rgb.red() * 0.6 + bg_rgb.red() * 0.4)
        muted_g = int(text_rgb.green() * 0.6 + bg_rgb.green() * 0.4)
        muted_b = int(text_rgb.blue() * 0.6 + bg_rgb.blue() * 0.4)
        muted_color = f"rgb({muted_r}, {muted_g}, {muted_b})"

        return {
            "user": link_color,  # Use link color for user messages (blue in light, brighter in dark)
            "assistant": text_color,  # Normal text color
            "system": muted_color,  # Muted text
            "error": "#ff4444" if palette.color(QPalette.ColorRole.Base).lightness() > 128 else "#ff6666"  # Red, adjusted for theme
        }

    # --- Card / message widget builders ---

    def _create_card(
        self,
        body_html: str,
        *,
        accent: str = "",
        label: str = "",
        label_color: str = "",
        italic: bool = False,
        small: bool = False,
    ) -> QFrame:
        """Create a styled card widget (fragment-style box with accent bar).

        Returns a QFrame with a colored left border, label, and rich-text body.
        """
        palette = self.palette()
        base = palette.color(QPalette.ColorRole.Base)
        is_dark = base.lightness() < 128
        bg = "#2a2a2a" if is_dark else "#f5f5f5"

        card = QFrame()
        card.setObjectName("chatCard")
        card.setFrameShape(QFrame.Shape.NoFrame)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        border_left = f"border-left: 4px solid {accent}; " if accent else ""
        card.setStyleSheet(
            f"QFrame#chatCard {{ background: {bg}; {border_left}"
            f"border-radius: 4px; padding: 8px 12px; }}"
        )

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(2)

        if label:
            lbl = QLabel(label.upper())
            lc = label_color or "#888"
            lbl.setStyleSheet(
                f"font-weight: bold; font-size: {scaled_pt(8)}pt; color: {lc}; "
                f"letter-spacing: 1px;"
            )
            card_layout.addWidget(lbl)

        body_label = QLabel()
        body_label.setWordWrap(True)
        body_label.setTextFormat(Qt.TextFormat.RichText)
        body_label.setOpenExternalLinks(True)
        body_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        body_label.setCursor(Qt.CursorShape.IBeamCursor)
        body_label.setText(body_html)
        body_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )

        style_parts = []
        if italic:
            style_parts.append("font-style: italic;")
        if small:
            style_parts.append(f"font-size: {scaled_pt(9)}pt;")
        if style_parts:
            body_label.setStyleSheet(" ".join(style_parts))

        card_layout.addWidget(body_label)
        return card

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

    def _add_widget(self, widget: QWidget) -> None:
        """Add a widget to the chat layout and scroll to bottom."""
        self._chat_layout.addWidget(widget)
        # Defer scroll so layout has time to update
        QTimer.singleShot(0, self._scroll_to_bottom)

    # --- Verbose fragments (thinking + tool usage) ---

    def set_verbose_visible(self, visible: bool) -> None:
        """Show or hide thinking and tool-usage fragments in the chat."""
        self._verbose_visible = visible
        for widget in self._verbose_widgets:
            widget.setVisible(visible)
        self._scroll_to_bottom_if_needed()

    def _register_verbose_widget(self, widget: QWidget) -> None:
        """Track a thinking / tool-usage fragment; hide it if verbose is off."""
        self._verbose_widgets.append(widget)
        if not self._verbose_visible:
            widget.hide()

    def _append_user_message(self, message: str) -> None:
        """Append user message to chat display."""
        colors = self._get_theme_colors()
        card = self._create_card(
            self._escape_html(message),
            accent=colors["user"],
            label="You",
            label_color=colors["user"],
        )
        self._add_widget(card)

    def _append_assistant_message(self, message: str) -> None:
        """Append Claude's message to chat display with markdown rendering."""
        from lightfall.claude.markdown import render_markdown

        card = self._create_card(
            render_markdown(message),
            accent="#9c27b0",
            label=self.speaker_name,
            label_color="#9c27b0",
        )
        self._add_widget(card)

    def _append_thinking_message(self, thinking: str) -> None:
        """Append thinking block to chat display."""
        # Thinking blocks usually arrive with a trailing newline (often two);
        # _escape_html turns each "\n" into "<br>", so a bare rstrip() here
        # avoids a stray blank line at the end of the fragment.
        card = self._create_card(
            self._escape_html(thinking.rstrip()),
            label="Thinking", italic=True, small=True,
        )
        self._register_verbose_widget(card)
        self._add_widget(card)

    def _append_system_message(self, message: str) -> QLabel:
        """Append system message as simple italic text."""
        colors = self._get_theme_colors()
        lbl = QLabel()
        lbl.setWordWrap(True)
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setText(self._escape_html(message))
        lbl.setStyleSheet(
            f"QLabel {{ color: {colors['system']}; font-style: italic; "
            f"font-size: {scaled_pt(9)}pt; padding: 2px 4px; }}"
        )
        self._add_widget(lbl)
        return lbl

    def _append_error_message(self, message: str) -> None:
        """Append error message to chat display."""
        colors = self._get_theme_colors()
        card = self._create_card(
            f"<b>Error:</b> {self._escape_html(message)}",
            accent=colors["error"],
            label="Error",
            label_color=colors["error"],
        )
        self._add_widget(card)

    def _on_scroll_value_changed(self, value: int) -> None:
        """Track whether user is at the bottom of the scroll area."""
        sb = self._scroll_area.verticalScrollBar()
        self._at_bottom = value >= sb.maximum()

    def _on_scroll_range_changed(self, _min: int, max_val: int) -> None:
        """Auto-scroll to bottom when content grows, if user was at bottom."""
        if self._at_bottom:
            self._scroll_area.verticalScrollBar().setValue(max_val)

    def _scroll_to_bottom(self) -> None:
        """Scroll chat area to bottom."""
        sb = self._scroll_area.verticalScrollBar()
        sb.setValue(sb.maximum())

    @staticmethod
    def _escape_html(text: str) -> str:
        """Escape HTML special characters."""
        return (text
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
                .replace("'", "&#x27;")
                .replace("\n", "<br>"))
