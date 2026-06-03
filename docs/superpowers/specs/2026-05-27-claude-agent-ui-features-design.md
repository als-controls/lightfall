# Claude Agent Panel — SDK Feature Integration

**Date:** 2026-05-27
**Scope:** Three independent additions to the embedded Claude Agent panel
(`lightfall.claude`) that close visible gaps against the `claude_agent_sdk` v0.2.82
feature surface.

## Background

The embedded agent panel currently reads `AssistantMessage` / `ResultMessage`
from `ClaudeSDKClient.receive_response()` and renders tool calls and
permission prompts. Three SDK features are emitted by the CLI but unhandled
by the host, producing user-visible degradation:

1. **`AskUserQuestion` (built-in tool)** — the model calls it to ask a
   structured multiple-choice question. With no host handler, the SDK
   denies the call and the model gives up on the turn. The user sees the
   agent "fall back to ending its turn."
2. **Partial streaming (`StreamEvent`)** — disabled by default, so each
   text block lands as a whole paragraph after the model finishes
   generating it. Perceived latency is the full block latency.
3. **Task tool subagent messages** — `TaskStartedMessage` /
   `TaskProgressMessage` / `TaskNotificationMessage` log as
   "unhandled msg type" in `worker.py`. Subagent activity is invisible
   to the user.

All three are addressed without changing the CLI subprocess model or the
panel's existing permission infrastructure.

## Non-goals

- Session listing, fork, resume (separate spec)
- MCP server status UI (separate spec)
- Model / permission-mode mid-session switching (separate spec)
- Rewind, rate-limit banner, cost footer (separate spec)
- Visual redesign of existing chat bubbles or permission widgets

## Feature 1: AskUserQuestion handler

### Routing

`AskUserQuestion` is a built-in Claude Code tool. It is dispatched through
the same `PreToolUse` hook + `can_use_tool` chain as every other tool.
Because hooks can only return `permissionDecision: "allow" | "deny" | "ask"`
and cannot rewrite the tool input, the answer-injection must happen in
`can_use_tool`, which returns `PermissionResultAllow(updated_input=...)`.

- **`create_pre_tool_use_hook`** (`permission_manager.py:342`): special-case
  `tool_name == "AskUserQuestion"` and return `{}` (no decision), letting
  the SDK fall through to `can_use_tool`.
- **`create_can_use_tool_callback`** (`permission_manager.py:398`):
  special-case the same name, route to a new
  `permission_manager.request_question(questions)` coroutine, and return
  `PermissionResultAllow(updated_input={"questions": questions,
  "answers": <user answers>})`.

### Input shape (from the CLI)

```python
{"questions": [
    {"question": str,
     "header": str,                # <=12 chars, chip label
     "options": [{"label": str, "description": str}, ...],
     "multiSelect": bool},
    ...]}
```

### Answer shape (returned to the CLI)

```python
{"questions": <same list passed through>,
 "answers": {"<question text>": "<label>",                    # single-select
             "<question text>": "<label1>,<label2>"}}         # multi-select
```

Multi-select answers are comma-separated strings of selected labels per
the SDK contract. "Other" is not synthesized client-side: we render
exactly the options the CLI sent. If a future CLI emits an `"Other"`
option as part of the list, it appears as just another labeled choice.

### PermissionManager additions

```python
question_requested = Signal(str, list)  # request_id, questions

async def request_question(
    self, questions: list[dict[str, Any]]
) -> tuple[bool, dict[str, str]]:
    """Emit question_requested, await response, return (answered, answers).
    answered=False means the user cancelled — caller should deny."""

def respond_to_question(
    self, request_id: str, answers: dict[str, str] | None
) -> None:
    """answers=None means the user cancelled the question."""
```

Internals mirror `request_permission` / `respond`: a `_pending_questions`
dict keyed by request_id, an `asyncio.Event` per request, the response
stored in `_question_responses`, and a 5-minute timeout. `cancel_all_pending`
also wakes pending questions, returning `(False, None)`.

### Widget

New file: `lightfall/claude/widgets/question_request.py`

```python
class QuestionRequestWidget(QFrame):
    submitted = Signal(str, dict)   # request_id, answers
    cancelled = Signal(str)         # request_id
```

Layout:
- Header row: "Claude is asking…" (gray, small) + question count if > 1
- One `QGroupBox` per question:
  - Title row: the `header` chip (small accent badge) + the question text
  - For each option: a `QRadioButton` (single-select) or `QCheckBox`
    (multi-select), with the option label as the button text and the
    description as a small secondary line beneath
- Footer: "Submit" (default) and "Cancel" buttons
- "Submit" is disabled until every question has at least one selection

`ClaudeAssistantWidget` additions (`claude/widget.py`):
- New slot `_on_question_requested(request_id, questions)` that creates a
  `QuestionRequestWidget`, stores it in
  `self._pending_question_widgets: dict[str, QuestionRequestWidget]`, and
  adds it to `self._permission_layout` (reusing the existing container).
- Submit handler builds the answers dict and calls
  `agent.respond_to_question(request_id, answers)`.
- Cancel handler calls `agent.respond_to_question(request_id, None)`.
- `_on_reset_conversation` also clears `_pending_question_widgets`.

`QtClaudeAgent` additions (`claude/agent.py`):
- `question_requested = Signal(str, list)` re-exposing
  `PermissionManager.question_requested`.
- `respond_to_question(request_id, answers)` thin wrapper.

## Feature 2: Partial streaming

### Option flip

`ClaudeAgentOptions(include_partial_messages=True)` in `agent.py:298`.

### Stream-event shape

`StreamEvent.event` is the raw Anthropic API SSE event dict. The types
we care about:

- `message_start` — new assistant message; ignored
- `content_block_start` — `{"index": int, "content_block": {"type":
  "text" | "thinking" | "tool_use" | ..., ...}}`
- `content_block_delta` — `{"index": int, "delta": {"type":
  "text_delta" | "thinking_delta" | "input_json_delta", "text" | "thinking" |
  "partial_json": str}}`
- `content_block_stop` — `{"index": int}`
- `message_delta`, `message_stop` — ignored

Block identity for the widget is the StreamEvent's parent `uuid` + the
event's `index` (concatenated as `f"{uuid}:{index}"`).

### Worker changes

In `_run_query`, between `AssistantMessage` and `ResultMessage` branches,
add:

```python
elif isinstance(msg, StreamEvent):
    self._dispatch_stream_event(msg)
```

`_dispatch_stream_event` parses `msg.event` and emits, depending on type:

- `partial_block_started(block_id: str, block_kind: str)`
- `partial_text(block_id: str, delta: str)`
- `partial_thinking(block_id: str, delta: str)`
- `partial_block_finished(block_id: str)`

Tool-use input deltas (`input_json_delta`) and message-level events are
not currently rendered and are dropped.

### Widget changes

New state on `ClaudeAssistantWidget`:

```python
self._streaming_bubbles: dict[str, _StreamingBubble] = {}
```

`_StreamingBubble` is a tiny dataclass-ish struct: `{kind: "text" |
"thinking", widget: QFrame, label: QLabel, buffer: str, finished: bool}`.

Slot behavior:
- `partial_block_started` → if `block_kind` is `"text"` or `"thinking"`,
  create a chat bubble of that kind (reusing `_make_assistant_frame` /
  `_make_thinking_frame`) and store it.
- `partial_text` / `partial_thinking` → append delta to the buffer,
  `setText(buffer)` on the label (plain text — no markdown re-parse per
  delta).
- `partial_block_finished` → mark `finished=True`, re-render the bubble's
  contents using the existing markdown path (`_render_markdown`).

`_on_message` (full `AssistantMessage` block) check: if a streaming bubble
exists for the same block, skip the append. If no streaming bubble
exists (e.g. options changed mid-session), fall back to the current
append-on-block-arrival behavior.

### Why plaintext during streaming, markdown at finish

`QLabel`/`QTextEdit` rich-text reparse on every `setText` is the dominant
per-delta cost in benchmarks. Plaintext deltas render in microseconds;
a single markdown render at block end produces the same final visual.

## Feature 4: Task tool progress

### Worker changes

Add three branches to the `elif isinstance(msg, ...)` chain in `_run_query`,
using `isinstance` against the concrete subclasses (which are also
`SystemMessage`, so order matters — these branches must precede the
generic `SystemMessage` fallback):

```python
elif isinstance(msg, TaskStartedMessage):
    self.task_started.emit(msg.task_id, msg.description, msg.tool_use_id or "")
elif isinstance(msg, TaskProgressMessage):
    self.task_progress.emit(
        msg.task_id, msg.description,
        dict(msg.usage), msg.last_tool_name or "")
elif isinstance(msg, TaskNotificationMessage):
    self.task_finished.emit(
        msg.task_id, msg.status, msg.summary,
        msg.output_file, dict(msg.usage) if msg.usage else {})
```

### Widget

New file: `lightfall/claude/widgets/task_card.py`

```python
class TaskCard(QFrame):
    """One subagent task; updated in place across progress messages."""
    def __init__(self, task_id: str, description: str, parent=None): ...
    def update_progress(self, description: str, usage: dict, last_tool: str): ...
    def mark_finished(self, status: str, summary: str, output_file: str,
                      usage: dict): ...
```

Layout:
- Collapsed view: status icon + description (truncated to one line) +
  small counter "tokens · tools" + chevron-toggle
- Expanded view: full description, last tool name, summary text,
  "Open output…" link if `output_file` is provided
- Status icon: spinning `qta.icon("mdi.loading", color=...)` for running,
  "mdi.check-circle" for completed, "mdi.alert-circle" for failed,
  "mdi.stop-circle" for stopped

### Widget state on ClaudeAssistantWidget

```python
self._task_cards: dict[str, TaskCard] = {}
self._task_tool_use_ids: dict[str, str] = {}  # tool_use_id -> task_id
```

Slot behavior:
- `task_started` → create `TaskCard`, add to `_chat_layout` at current
  end, store by `task_id`, record `tool_use_id` → `task_id`.
- `task_progress` → look up by `task_id`, call `update_progress`.
- `task_finished` → look up, `mark_finished`. Cards are kept visible.

### Tool-result suppression

When `_on_tool_result(tool_use_id, ...)` fires and that `tool_use_id` is
in `_task_tool_use_ids`, skip the usual "Tool returned…" system-message
append — the card already represents both the work and the outcome.

Similarly, `_on_tool_called` for the Task tool can be suppressed in favor
of the card (the card replaces both the "Using tool: Task" notice and
the eventual result echo).

## Cross-cutting

### Worker signals

Five new `Signal` definitions on `PersistentClaudeWorker`:
- `partial_block_started(str, str)`, `partial_text(str, str)`,
  `partial_thinking(str, str)`, `partial_block_finished(str)`
- `task_started(str, str, str)`, `task_progress(str, str, dict, str)`,
  `task_finished(str, str, str, str, dict)`

…and one on `PermissionManager`:
- `question_requested(str, list)`

All re-exposed on `QtClaudeAgent` via `worker.<sig>.connect(self.<sig>)`
in `_ensure_connected`, matching the existing pattern.

### Cancel-path behavior

`cancel_all_pending` denies pending questions (same as it denies pending
approvals). Task cards stay visible across cancel — their state badges
already encode "stopped" via the cancel-triggered `TaskNotificationMessage`
the CLI emits when interrupted.

### Theming

All new widgets follow the existing chat-bubble idiom: themed `QFrame`
+ `palette()`-driven colors + `qta` icons. No new theme keys.

## Testing

### `tests/claude/test_ask_user_question.py`

- Build `PermissionManager`, attach a Qt signal spy on `question_requested`.
- Construct a stub `can_use_tool` callback via `create_can_use_tool_callback`,
  invoke it with `tool_name="AskUserQuestion"`, `tool_input={"questions": [...]}`
  inside a fresh `asyncio` loop.
- Verify `question_requested` fired with the questions list.
- Call `respond_to_question(request_id, {"q": "A"})` from the main thread.
- Assert the callback returned `PermissionResultAllow` with
  `updated_input={"questions": [...], "answers": {"q": "A"}}`.
- Second test for cancellation: `respond_to_question(request_id, None)`
  → callback returns `PermissionResultDeny`.

### `tests/claude/test_partial_streaming.py`

- Run `PersistentClaudeWorker` with a stub client whose `receive_response`
  yields synthetic `StreamEvent` objects: block_start(text),
  three block_delta(text_delta), block_stop, then `ResultMessage`.
- Assert the emitted signal sequence: `partial_block_started("…:0",
  "text")`, three `partial_text("…:0", "<delta>")`, one
  `partial_block_finished("…:0")`, then `query_completed`.

### `tests/claude/test_task_progress.py`

- Stub `receive_response` yields `TaskStartedMessage` →
  `TaskProgressMessage` → `TaskNotificationMessage` → `ResultMessage`.
- Assert `task_started` / `task_progress` / `task_finished` fire with
  the expected field values.

### Widget thin tests (pytest-qt)

- `test_question_widget_appears.py` — emit `question_requested` on the
  agent stub, assert a `QuestionRequestWidget` exists inside
  `_permission_layout` and the submit button is initially disabled.
- `test_task_card_appears.py` — emit `task_started`, assert a `TaskCard`
  is inside `_chat_layout`; emit `task_finished`, assert the card's
  status text updates.

No pixel-level or screenshot assertions.

## Risks and unknowns

- **`AskUserQuestion` input shape stability.** The schema is documented
  for SDK ≥ 0.2 but is not part of `claude_agent_sdk.types` — it's the
  CLI's tool. If a future CLI version changes the shape, the host
  parser must adapt. Mitigation: parse defensively (treat missing keys
  as no-op) and log unrecognized shapes.
- **`StreamEvent` ordering.** The CLI emits stream events interleaved
  with the final `AssistantMessage`. The widget logic assumes the
  block_id from streaming matches the block from the assembled message
  — if it doesn't, the user sees the text appear twice. The
  `_on_message` skip-check using `_streaming_bubbles` handles this.
- **Markdown re-parse on finish.** A pathological CLI that finishes a
  block but emits one more delta would race. We treat post-finish
  deltas as a no-op (drop with a warning log).
- **Task tool result content.** The CLI may also emit a `ToolResultBlock`
  for the Task tool. If both the card and the standard tool-result path
  fire, the card wins (suppression by `tool_use_id` lookup).

## File-touch summary

Modified:
- `src/lightfall/claude/agent.py` — options flip, three new signal re-exports,
  `respond_to_question`.
- `src/lightfall/claude/permission_manager.py` — `question_requested`,
  `request_question`, `respond_to_question`, `AskUserQuestion`
  special-cases in hook + can_use_tool factories.
- `src/lightfall/claude/_internal/worker.py` — `StreamEvent` dispatch,
  `Task*Message` branches, new Signals.
- `src/lightfall/claude/widget.py` — slots and state for streaming bubbles,
  question widgets, task cards.

New:
- `src/lightfall/claude/widgets/question_request.py`
- `src/lightfall/claude/widgets/task_card.py`
- `tests/claude/test_ask_user_question.py`
- `tests/claude/test_partial_streaming.py`
- `tests/claude/test_task_progress.py`
- `tests/ui/panels/claude/test_question_widget_appears.py`
- `tests/ui/panels/claude/test_task_card_appears.py`
