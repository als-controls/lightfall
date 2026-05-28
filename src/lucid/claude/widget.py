"""ClaudeAssistantWidget - High-level embeddable chat widget."""

from dataclasses import dataclass

from PySide6.QtCore import QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from lucid.claude.agent import QtClaudeAgent
from lucid.claude.widgets.permission_request import PermissionRequestWidget
from lucid.claude.widgets.question_request import QuestionRequestWidget
from lucid.claude.widgets.task_card import TaskCard
from lucid.ui.preferences.claude_settings import ClaudeSettingsProvider
from lucid.utils.logging import logger


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


class ClaudeAssistantWidget(QWidget):
    """
    A widget that provides a chat interface with Claude.

    This widget embeds a complete chat UI where users can interact with Claude
    about the target Qt window. Claude can see, understand, and interact with
    the target window's widgets.

    Example:
        ```python
        from PySide6.QtWidgets import QApplication, QMainWindow
        from lucid.claude import ClaudeAssistantWidget

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

    def __init__(
        self,
        target_window: QWidget,
        api_key: str | None = None,
        api_url: str | None = None,
        cli_path: str | None = None,
        additional_system_prompt: str | None = None,
        permission_mode: str = "default",
        require_approval: bool = True,
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

        # Track pending permission widgets by request_id
        self._pending_permission_widgets: dict[str, PermissionRequestWidget] = {}
        # request_id -> QuestionRequestWidget
        self._pending_question_widgets: dict[str, "QuestionRequestWidget"] = {}
        # block_id -> _StreamingBubble for in-progress streamed text/thinking.
        self._streaming_bubbles: dict[str, _StreamingBubble] = {}
        # task_id -> TaskCard
        self._task_cards: dict[str, TaskCard] = {}
        # tool_use_id -> task_id (so the Task tool's tool_called / tool_result
        # can be suppressed in favor of the card)
        self._task_tool_use_ids: dict[str, str] = {}
        # Track tool names for "Always Allow" functionality
        self._pending_tool_names: dict[str, str] = {}

        # Create the agent
        try:
            self.agent = QtClaudeAgent(
                target_window,
                api_key,
                api_url,
                cli_path,
                permission_mode=permission_mode,
                max_turns=ClaudeSettingsProvider.get_max_turns(),
                additional_system_prompt=additional_system_prompt,
                require_approval=require_approval,
                parent=self,
            )
        except ValueError as e:
            # API key not provided
            self._setup_error_ui(str(e))
            return

        # Setup UI
        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self) -> None:
        """Setup the user interface."""
        self.setWindowTitle("Claude Assistant")
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

        # Input area
        input_layout = QHBoxLayout()

        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("Hi Claude, Tell me about Lucid...")
        self.input_field.returnPressed.connect(self._send_query)
        input_layout.addWidget(self.input_field)

        self.send_button = QPushButton("Send")
        self.send_button.clicked.connect(self._on_send_button_clicked)
        input_layout.addWidget(self.send_button)

        import qtawesome as qta

        self.reset_button = QPushButton(qta.icon("mdi.broom"), "")
        self.reset_button.setFixedWidth(32)
        self.reset_button.setToolTip("Reset conversation")
        self.reset_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reset_button.clicked.connect(self._on_reset_conversation)
        input_layout.addWidget(self.reset_button)

        layout.addLayout(input_layout)

        # Track busy state for button toggling
        self._is_busy = False
        # Store default placeholder for restoration
        self._default_placeholder = "Hi Claude, Tell me about Lucid..."

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
        """Send the user's query to Claude."""
        prompt = self.input_field.text().strip()

        if not prompt:
            return

        if self.agent.is_busy():
            self._append_system_message("Please wait for the current query to complete...")
            return

        # Clear input
        self.input_field.clear()

        # Show user message
        self._append_user_message(prompt)

        # Set busy state - disable input and change button to Cancel
        self._set_busy_state(True, "Claude is thinking...")
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
        # Clear any task card tracking (the widgets themselves are children
        # of the chat layout and were already deleted above).
        self._task_cards.clear()
        self._task_tool_use_ids.clear()
        self._permission_container.hide()

        # Reset busy state
        self._set_busy_state(False)

        # Show confirmation
        self._append_system_message("Conversation reset")

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
            self.send_button.setText("Cancel")
            self.send_button.setEnabled(True)
            self.reset_button.setEnabled(False)
        else:
            # Enable input, restore placeholder, change button back
            self.input_field.setEnabled(True)
            self.input_field.setPlaceholderText(self._default_placeholder)
            self.send_button.setText("Send")
            self.send_button.setEnabled(True)
            self.reset_button.setEnabled(True)
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
        logger.info(
            "[widget-stream] partial_block_started block={} kind={}",
            block_id, kind,
        )
        if kind == "text":
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
            logger.warning(
                "[widget-stream] _find_body_label returned None for block={}",
                block_id,
            )
            return
        self._streaming_bubbles[block_id] = _StreamingBubble(
            kind=kind, frame=frame, label=label, buffer=""
        )
        self._add_widget(frame)

    @Slot(str, str)
    def _on_partial_text(self, block_id: str, delta: str) -> None:
        logger.info(
            "[widget-stream] partial_text block={} delta_len={}",
            block_id, len(delta),
        )
        bubble = self._streaming_bubbles.get(block_id)
        if bubble is None:
            logger.warning(
                "[widget-stream] partial_text for unknown block_id={}",
                block_id,
            )
            return
        if bubble.kind != "text":
            return
        bubble.buffer += delta
        # Plain text during streaming — markdown render once on finish.
        bubble.label.setText(self._escape_html(bubble.buffer))
        self._scroll_to_bottom_if_needed()

    @Slot(str, str)
    def _on_partial_thinking(self, block_id: str, delta: str) -> None:
        logger.info(
            "[widget-stream] partial_thinking block={} delta_len={}",
            block_id, len(delta),
        )
        bubble = self._streaming_bubbles.get(block_id)
        if bubble is None:
            logger.warning(
                "[widget-stream] partial_thinking for unknown block_id={}",
                block_id,
            )
            return
        if bubble.kind != "thinking":
            return
        bubble.buffer += delta
        bubble.label.setText(self._escape_html(bubble.buffer))
        self._scroll_to_bottom_if_needed()

    @Slot(str)
    def _on_partial_block_finished(self, block_id: str) -> None:
        bubble = self._streaming_bubbles.pop(block_id, None)
        if bubble is None:
            logger.info(
                "[widget-stream] partial_block_finished for unknown block_id={}",
                block_id,
            )
            return
        logger.info(
            "[widget-stream] partial_block_finished block={} kind={} buffer_len={}",
            block_id, bubble.kind, len(bubble.buffer),
        )
        if not bubble.buffer:
            # Empty bubble — no content ever arrived. Remove the ghost
            # card so the AssistantMessage path's card stands alone.
            bubble.frame.deleteLater()
            return
        if bubble.kind == "text":
            # One markdown render at end — see spec for the perf rationale.
            from lucid.claude.markdown import render_markdown
            bubble.label.setText(render_markdown(bubble.buffer))
        # thinking stays plaintext (existing widget style).

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
        self._append_error_message(error)
        self._set_busy_state(False)

    @Slot()
    def _on_query_completed(self) -> None:
        """Handle query completion."""
        self._set_busy_state(False)

    @Slot()
    def _on_query_cancelled(self) -> None:
        """Handle query cancellation."""
        self._append_system_message("Query cancelled")
        self._set_busy_state(False)

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
            self.input_field.setPlaceholderText("Claude is working...")

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
                f"font-weight: bold; font-size: 8pt; color: {lc}; "
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
            style_parts.append("font-size: 9pt;")
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
        from lucid.claude.markdown import render_markdown

        card = self._create_card(
            render_markdown(message),
            accent="#9c27b0",
            label="Claude",
            label_color="#9c27b0",
        )
        self._add_widget(card)

    def _append_thinking_message(self, thinking: str) -> None:
        """Append thinking block to chat display."""
        card = self._create_card(
            self._escape_html(thinking),
            label="Thinking", italic=True, small=True,
        )
        self._add_widget(card)

    def _append_system_message(self, message: str) -> None:
        """Append system message as simple italic text."""
        colors = self._get_theme_colors()
        lbl = QLabel()
        lbl.setWordWrap(True)
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setText(self._escape_html(message))
        lbl.setStyleSheet(
            f"QLabel {{ color: {colors['system']}; font-style: italic; "
            f"font-size: 9pt; padding: 2px 4px; }}"
        )
        self._add_widget(lbl)

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
