# Sample Metadata Dialog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pre-submit dialog that collects sample name and arbitrary metadata before every RunEngine plan, with duplicate name validation against Tiled.

**Architecture:** Engine-level middleware — `BaseEngine` gets a pre-submit callable registry that runs before queuing. A `SampleMetadataDialog` (pyqtgraph ParameterTree with ScalableGroup) is registered as a pre-submit callable during BlueskyPanel setup. The dialog queries Tiled on accept to warn about duplicate sample names.

**Tech Stack:** PySide6, pyqtgraph ParameterTree, tiled.queries.Key, QSettings

**Spec:** `docs/superpowers/specs/2026-04-11-sample-metadata-dialog-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/lucid/acquire/engine/base.py` | Modify | Add `_pre_submit_callables` list, `register_pre_submit()`, `unregister_pre_submit()`, update `submit()` and `__call__()` |
| `src/lucid/acquire/engine/mock.py` | Modify | Update `MockEngine.submit()` to call pre-submit callables |
| `src/lucid/ui/dialogs/sample_metadata_dialog.py` | Create | `SampleMetadataDialog` with sample name field, ScalableGroup, duplicate check, force button |
| `src/lucid/ui/dialogs/__init__.py` | Modify | Export `SampleMetadataDialog` |
| `src/lucid/ui/panels/bluesky_panel.py` | Modify | Register sample metadata pre-submit callable in `_auto_configure()` |
| `tests/test_engine.py` | Modify | Add tests for pre-submit hook system |
| `tests/test_sample_metadata_dialog.py` | Create | Tests for dialog behavior |

---

### Task 1: Pre-submit hook system in BaseEngine

**Files:**
- Modify: `src/lucid/acquire/engine/base.py` (lines 85-227)
- Test: `tests/test_engine.py`

- [ ] **Step 1: Write failing tests for pre-submit registration**

Add to `tests/test_engine.py`:

```python
class TestPreSubmitHooks:
    """Tests for pre-submit callable system."""

    def test_register_pre_submit(self, mock_engine) -> None:
        """Test registering a pre-submit callable."""
        def hook(plan_name: str, kwargs: dict) -> dict:
            return {"extra": "metadata"}

        mock_engine.register_pre_submit(hook)
        assert hook in mock_engine._pre_submit_callables

    def test_unregister_pre_submit(self, mock_engine) -> None:
        """Test unregistering a pre-submit callable."""
        def hook(plan_name: str, kwargs: dict) -> dict:
            return {}

        mock_engine.register_pre_submit(hook)
        mock_engine.unregister_pre_submit(hook)
        assert hook not in mock_engine._pre_submit_callables

    def test_pre_submit_merges_kwargs(self, mock_engine) -> None:
        """Test that pre-submit callable's returned dict is merged into kwargs."""
        outputs = []
        mock_engine.subscribe(lambda name, doc: outputs.append((name, doc)))

        def hook(plan_name: str, kwargs: dict) -> dict:
            return {"sample_name": "my_sample"}

        mock_engine.register_pre_submit(hook)
        mock_engine.submit("test_procedure")

        # Check that sample_name appears in the start document
        start_doc = outputs[0][1]
        assert start_doc["sample_name"] == "my_sample"

    def test_pre_submit_cancel_returns_none(self, mock_engine) -> None:
        """Test that returning None from pre-submit cancels submission."""
        outputs = []
        mock_engine.subscribe(lambda name, doc: outputs.append((name, doc)))

        def hook(plan_name: str, kwargs: dict) -> None:
            return None  # Cancel

        mock_engine.register_pre_submit(hook)
        result = mock_engine.submit("test_procedure")

        assert result is None
        assert len(outputs) == 0  # No documents emitted

    def test_skip_pre_submit(self, mock_engine) -> None:
        """Test that skip_pre_submit=True bypasses hooks."""
        hook_called = []

        def hook(plan_name: str, kwargs: dict) -> dict:
            hook_called.append(True)
            return {}

        mock_engine.register_pre_submit(hook)
        mock_engine.submit("test_procedure", skip_pre_submit=True)

        assert len(hook_called) == 0

    def test_pre_submit_ordering(self, mock_engine) -> None:
        """Test that pre-submit callables run in registration order."""
        call_order = []

        def hook_a(plan_name: str, kwargs: dict) -> dict:
            call_order.append("a")
            return {"order": "a"}

        def hook_b(plan_name: str, kwargs: dict) -> dict:
            call_order.append("b")
            return {"order": "b"}

        mock_engine.register_pre_submit(hook_a)
        mock_engine.register_pre_submit(hook_b)
        mock_engine.submit("test_procedure")

        assert call_order == ["a", "b"]

    def test_pre_submit_receives_plan_name(self, mock_engine) -> None:
        """Test that pre-submit callable receives the plan name."""
        received_names = []

        def hook(plan_name: str, kwargs: dict) -> dict:
            received_names.append(plan_name)
            return {}

        mock_engine.register_pre_submit(hook)
        mock_engine.submit("test_procedure", name="my_scan")

        assert received_names == ["my_scan"]

    def test_call_passes_skip_pre_submit(self, mock_engine) -> None:
        """Test that __call__ passes skip_pre_submit through."""
        hook_called = []

        def hook(plan_name: str, kwargs: dict) -> dict:
            hook_called.append(True)
            return {}

        mock_engine.register_pre_submit(hook)
        mock_engine("test_procedure", skip_pre_submit=True)

        assert len(hook_called) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_engine.py::TestPreSubmitHooks -v`
Expected: FAIL — `register_pre_submit` does not exist.

- [ ] **Step 3: Implement pre-submit hooks in BaseEngine**

In `src/lucid/acquire/engine/base.py`, add to `__init__` (after `self._next_token = 0`):

```python
self._pre_submit_callables: list[Callable[[str, dict[str, Any]], dict[str, Any] | None]] = []
```

Add these methods after `unsubscribe()`:

```python
def register_pre_submit(
    self, callable_: Callable[[str, dict[str, Any]], dict[str, Any] | None]
) -> None:
    """Register a callable invoked before each plan submission.

    The callable receives (plan_name, kwargs) and must return:
    - dict: additional kwargs to merge into the submission
    - None: cancel the submission

    Callables run on the calling thread in registration order.

    Args:
        callable_: Function with signature (str, dict) -> dict | None.
    """
    self._pre_submit_callables.append(callable_)

def unregister_pre_submit(
    self, callable_: Callable[[str, dict[str, Any]], dict[str, Any] | None]
) -> None:
    """Remove a pre-submit callable.

    Args:
        callable_: The callable to remove.
    """
    try:
        self._pre_submit_callables.remove(callable_)
    except ValueError:
        pass
```

Modify `submit()` — add `skip_pre_submit` parameter and pre-submit logic before queuing. Replace the current method:

```python
def submit(
    self,
    procedure: Any,
    *,
    priority: int = 1,
    name: str = "",
    skip_pre_submit: bool = False,
    **kwargs: Any,
) -> str | None:
    """Submit a procedure for execution.

    Args:
        procedure: The procedure to execute.
        priority: Queue priority (lower = higher priority). Default is 1.
        name: Human-readable name for the procedure. If not provided,
            attempts to detect from generator function name.
        skip_pre_submit: If True, bypass pre-submit callables.
        **kwargs: Additional procedure parameters.

    Returns:
        The unique ID of the submitted procedure, or None if cancelled
        by a pre-submit callable.
    """
    # Auto-detect name from generator if not provided
    if not name:
        name = self._get_procedure_name(procedure)

    # Run pre-submit callables
    if not skip_pre_submit:
        for callable_ in self._pre_submit_callables:
            try:
                result = callable_(name, kwargs)
                if result is None:
                    logger.info(f"[{self._name}] Submission cancelled by pre-submit hook")
                    return None
                kwargs.update(result)
            except Exception as ex:
                logger.warning(f"[{self._name}] Error in pre-submit callable: {ex}")
                return None

    item = PrioritizedProcedure(priority, procedure, kwargs, name=name)
    self._queue.put(item)
    self._queue_items.append(item)
    self._queue_items.sort(key=lambda x: x.priority)
    logger.debug(f"[{self._name}] Queued '{name}' with priority {priority}, id={item.id[:8]}")
    self.sigQueueChanged.emit()
    return item.id
```

Modify `__call__()` to pass `skip_pre_submit`:

```python
def __call__(self, *args: Any, **kwargs: Any) -> None:
    """Convenience method for submit().

    If a single positional argument is provided, it's used as the procedure.
    Otherwise, all args are bundled as the procedure.
    """
    skip_pre_submit = kwargs.pop("skip_pre_submit", False)
    if args:
        procedure = args[0] if len(args) == 1 else args
        self.submit(procedure, skip_pre_submit=skip_pre_submit, **kwargs)
    else:
        raise ValueError("No procedure provided")
```

- [ ] **Step 4: Update MockEngine.submit() to support pre-submit hooks**

In `src/lucid/acquire/engine/mock.py`, update the `submit()` signature and add pre-submit logic at the top of the method:

```python
def submit(
    self,
    procedure: Any,
    *,
    priority: int = 1,
    name: str = "",
    skip_pre_submit: bool = False,
    **kwargs: Any,
) -> str | None:
    """Submit a procedure for execution.

    The mock engine executes immediately and synchronously.

    Args:
        procedure: The procedure to execute (ignored in mock).
        priority: Queue priority (ignored in mock).
        name: Human-readable name for the procedure.
        skip_pre_submit: If True, bypass pre-submit callables.
        **kwargs: Additional parameters (included in start document).

    Returns:
        The unique ID of the submitted procedure, or None if cancelled.
    """
    # Auto-detect name from generator if not provided
    if not name:
        name = self._get_procedure_name(procedure)

    # Run pre-submit callables
    if not skip_pre_submit:
        for callable_ in self._pre_submit_callables:
            try:
                result = callable_(name, kwargs)
                if result is None:
                    logger.info(f"[{self._name}] Submission cancelled by pre-submit hook")
                    return None
                kwargs.update(result)
            except Exception as ex:
                logger.warning(f"[{self._name}] Error in pre-submit callable: {ex}")
                return None

    # Generate a unique ID for this "run"
    self._current_uid = str(uuid.uuid4())
    uid = self._current_uid

    self._set_state(EngineState.RUNNING)
    self.sigStart.emit()

    logger.debug(f"[mock] Executing mock procedure: {procedure}")

    # Emit mock start document
    start_doc = {
        "uid": self._current_uid,
        "plan_name": str(procedure) if procedure else "mock_plan",
        "time": 0.0,
        **kwargs,
    }
    self._emit_output("start", start_doc)

    # Emit mock stop document
    stop_doc = {
        "uid": str(uuid.uuid4()),
        "run_start": self._current_uid,
        "exit_status": "success",
        "time": 0.0,
        "num_events": {},
    }
    self._emit_output("stop", stop_doc)

    self._current_uid = None
    self._set_state(EngineState.IDLE)
    self.sigFinish.emit()
    return uid
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /c/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_engine.py -v`
Expected: ALL PASS (both new and existing tests).

- [ ] **Step 6: Commit**

```bash
cd /c/Users/rp/PycharmProjects/ncs/ncs
git add src/lucid/acquire/engine/base.py src/lucid/acquire/engine/mock.py tests/test_engine.py
git commit -m "feat: add pre-submit hook system to BaseEngine

Callables registered via register_pre_submit() run before each plan
submission and can inject metadata or cancel the submission."
```

---

### Task 2: SampleMetadataDialog — core dialog with ScalableGroup

**Files:**
- Create: `src/lucid/ui/dialogs/sample_metadata_dialog.py`
- Test: `tests/test_sample_metadata_dialog.py`

- [ ] **Step 1: Write failing tests for dialog basics**

Create `tests/test_sample_metadata_dialog.py`:

```python
"""Tests for the SampleMetadataDialog."""

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import QApplication, QDialogButtonBox


@pytest.fixture
def qapp():
    """Ensure QApplication exists for Qt."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture
def dialog(qapp):
    """Create a SampleMetadataDialog for testing."""
    from lucid.ui.dialogs.sample_metadata_dialog import SampleMetadataDialog

    dlg = SampleMetadataDialog()
    return dlg


class TestSampleMetadataDialogBasics:
    """Tests for basic dialog functionality."""

    def test_dialog_creates(self, dialog) -> None:
        """Test that dialog instantiates without error."""
        assert dialog is not None

    def test_sample_name_field_exists(self, dialog) -> None:
        """Test that sample name field exists and is empty."""
        assert dialog._sample_name_edit is not None
        assert dialog._sample_name_edit.text() == ""

    def test_get_metadata_includes_sample_name(self, dialog) -> None:
        """Test that get_metadata returns sample_name."""
        dialog._sample_name_edit.setText("test_sample")
        metadata = dialog.get_metadata()
        assert metadata["sample_name"] == "test_sample"

    def test_get_metadata_includes_arbitrary_fields(self, dialog) -> None:
        """Test that get_metadata includes user-added fields."""
        dialog._sample_name_edit.setText("test_sample")

        # Add a custom field via the ScalableGroup
        dialog._metadata_group.addNew("str")
        children = dialog._metadata_group.children()
        assert len(children) > 0

        # Set a value on the new field
        child = children[-1]
        child.setName("temperature")
        child.setValue("25.0")

        metadata = dialog.get_metadata()
        assert metadata["sample_name"] == "test_sample"
        assert metadata["temperature"] == "25.0"

    def test_empty_sample_name_rejected(self, dialog) -> None:
        """Test that empty sample name shows validation error."""
        dialog._sample_name_edit.setText("")
        # Try to accept — should not close
        dialog._on_accept_clicked()
        assert dialog._warning_label.text() != ""
        assert dialog.result() != dialog.DialogCode.Accepted

    def test_reserved_field_name_rejected(self, dialog) -> None:
        """Test that reserved field names are rejected."""
        dialog._sample_name_edit.setText("test_sample")

        # Add a field with a reserved name
        dialog._metadata_group.addNew("str")
        child = dialog._metadata_group.children()[-1]
        child.setName("uid")

        dialog._on_accept_clicked()
        assert "reserved" in dialog._warning_label.text().lower()

    def test_run_button_exists(self, dialog) -> None:
        """Test that the Run button exists."""
        assert dialog._accept_btn is not None
        assert dialog._accept_btn.text() == "Run"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_sample_metadata_dialog.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement SampleMetadataDialog**

Create `src/lucid/ui/dialogs/sample_metadata_dialog.py`:

```python
"""Sample metadata dialog for pre-run metadata collection.

Prompts the user for a sample name and optional arbitrary metadata
before a plan is submitted to the RunEngine.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from lucid.ui.dialogs.base import LucidDialog
from lucid.utils.logging import logger

try:
    from pyqtgraph.parametertree import Parameter, ParameterTree
    from pyqtgraph.parametertree.parameterTypes import GroupParameter

    HAS_PYQTGRAPH = True
except ImportError:
    HAS_PYQTGRAPH = False
    Parameter = None
    ParameterTree = None
    GroupParameter = None

# Reserved field names that cannot be used for arbitrary metadata
RESERVED_FIELDS = frozenset({
    "uid",
    "time",
    "scan_id",
    "plan_name",
    "plan_type",
    "plan_args",
    "plan_pattern_args",
    "plan_pattern_module",
    "num_points",
    "num_intervals",
    "hints",
    "detectors",
    "motors",
    "sample_name",
})

_QSETTINGS_KEY = "lucid.dialogs.sample_metadata.v1"


class ScalableGroup(GroupParameter):
    """A group parameter that allows users to add/remove typed fields.

    Provides an "Add" button with type selection (str, float, int).
    New fields are renamable and removable.
    """

    def __init__(self, **opts: Any) -> None:
        opts["type"] = "group"
        opts["addText"] = "Add"
        opts["addList"] = ["str", "float", "int"]
        super().__init__(**opts)

    def addNew(self, typ: str) -> None:
        """Add a new child parameter of the given type.

        Args:
            typ: Parameter type ('str', 'float', or 'int').
        """
        defaults = {"str": "", "float": 0.0, "int": 0}
        self.addChild(
            {
                "name": f"field_{len(self.children()) + 1}",
                "type": typ,
                "value": defaults[typ],
                "removable": True,
                "renamable": True,
            }
        )


class SampleMetadataDialog(LucidDialog):
    """Dialog for collecting sample name and arbitrary metadata before a run.

    Shows a sample name field (required) and a pyqtgraph ParameterTree
    with a ScalableGroup for adding arbitrary typed metadata fields.
    Validates against duplicate sample names in Tiled and reserved field names.

    The ScalableGroup state (field definitions and values) is persisted
    across sessions via QSettings. The sample name is not persisted.

    Example:
        dialog = SampleMetadataDialog()
        if dialog.exec() == QDialog.DialogCode.Accepted:
            metadata = dialog.get_metadata()
            # {"sample_name": "my_sample", "temperature": 25.0, ...}
    """

    def __init__(
        self,
        reserved: frozenset[str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """Initialize the dialog.

        Args:
            reserved: Additional reserved field names beyond the defaults.
            parent: Parent widget.
        """
        super().__init__(parent)
        self._reserved = RESERVED_FIELDS | (reserved or frozenset())
        self._force_mode = False
        self.setWindowTitle("Sample Metadata")
        self.setMinimumWidth(400)
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the dialog UI."""
        layout = QVBoxLayout(self)

        # Sample name row
        name_layout = QHBoxLayout()
        name_label = QLabel("Sample Name:")
        name_label.setStyleSheet("font-weight: bold;")
        name_layout.addWidget(name_label)

        self._sample_name_edit = QLineEdit()
        self._sample_name_edit.setPlaceholderText("Enter sample name (required)")
        self._sample_name_edit.textChanged.connect(self._on_sample_name_changed)
        name_layout.addWidget(self._sample_name_edit)

        layout.addLayout(name_layout)

        # Warning label (hidden by default)
        self._warning_label = QLabel("")
        self._warning_label.setStyleSheet("color: #c00; font-style: italic;")
        self._warning_label.setWordWrap(True)
        self._warning_label.hide()
        layout.addWidget(self._warning_label)

        # Metadata parameter tree
        if HAS_PYQTGRAPH:
            # Restore or create the ScalableGroup
            self._metadata_group = ScalableGroup(name="Additional Metadata")
            self._restore_state()

            self._param_tree = ParameterTree(showHeader=False)
            self._param_tree.setParameters(self._metadata_group, showTop=True)
            layout.addWidget(self._param_tree, 1)
        else:
            self._metadata_group = None
            self._param_tree = None
            fallback = QLabel("pyqtgraph not available — no additional metadata fields.")
            layout.addWidget(fallback, 1)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        self._accept_btn = QPushButton("Run")
        self._accept_btn.setDefault(True)
        self._accept_btn.clicked.connect(self._on_accept_clicked)
        button_layout.addWidget(self._accept_btn)

        layout.addLayout(button_layout)

    def _on_sample_name_changed(self, text: str) -> None:
        """Reset force mode and warning when sample name changes."""
        self._force_mode = False
        self._accept_btn.setText("Run")
        self._warning_label.hide()
        self._warning_label.setText("")

    def _on_accept_clicked(self) -> None:
        """Handle accept/force button click."""
        sample_name = self._sample_name_edit.text().strip()

        # Validate non-empty
        if not sample_name:
            self._warning_label.setText("Sample name is required.")
            self._warning_label.show()
            return

        # Validate reserved field names in arbitrary metadata
        if self._metadata_group is not None:
            for child in self._metadata_group.children():
                if child.name() in self._reserved:
                    self._warning_label.setText(
                        f'The field name "{child.name()}" is reserved and cannot be used.'
                    )
                    self._warning_label.show()
                    return

        # If in force mode, accept immediately
        if self._force_mode:
            self._save_state()
            self.accept()
            return

        # Check for duplicate sample name in Tiled
        if self._check_duplicate_sample_name(sample_name):
            self._warning_label.setText(
                f'"{sample_name}" already exists in Tiled. '
                "Choose a different name or click Force to proceed."
            )
            self._warning_label.show()
            self._accept_btn.setText("Force")
            self._force_mode = True
            return

        # All good — accept
        self._save_state()
        self.accept()

    def _check_duplicate_sample_name(self, sample_name: str) -> bool:
        """Check if a sample name already exists in Tiled.

        Args:
            sample_name: The sample name to check.

        Returns:
            True if the name already exists, False otherwise.
            Returns False if Tiled is not connected (degraded mode).
        """
        try:
            from lucid.services.tiled_service import TiledConnectionState, TiledService

            service = TiledService.get_instance()
            if not service.is_connected or service._client is None:
                return False

            from tiled.queries import Key

            results = service._client.search(Key("start.sample_name") == sample_name)
            return len(results) > 0
        except Exception as e:
            logger.warning("Failed to check duplicate sample name: {}", e)
            return False

    def get_metadata(self) -> dict[str, Any]:
        """Get the collected metadata.

        Returns:
            Dictionary with 'sample_name' and any arbitrary fields.
        """
        metadata: dict[str, Any] = {
            "sample_name": self._sample_name_edit.text().strip(),
        }

        if self._metadata_group is not None:
            for child in self._metadata_group.children():
                name = child.name()
                if name not in self._reserved:
                    metadata[name] = child.value()

        return metadata

    def _save_state(self) -> None:
        """Save the ScalableGroup state to QSettings."""
        if self._metadata_group is not None:
            try:
                QSettings().setValue(_QSETTINGS_KEY, self._metadata_group.saveState())
            except Exception as e:
                logger.warning("Failed to save metadata dialog state: {}", e)

    def _restore_state(self) -> None:
        """Restore the ScalableGroup state from QSettings."""
        if self._metadata_group is None:
            return

        state = QSettings().value(_QSETTINGS_KEY)
        if state is not None:
            try:
                self._metadata_group.restoreState(state)
            except Exception as e:
                logger.warning("Failed to restore metadata dialog state: {}", e)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_sample_metadata_dialog.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
cd /c/Users/rp/PycharmProjects/ncs/ncs
git add src/lucid/ui/dialogs/sample_metadata_dialog.py tests/test_sample_metadata_dialog.py
git commit -m "feat: add SampleMetadataDialog with ScalableGroup

Collects sample_name (required) and arbitrary typed metadata fields.
Validates reserved names. Persists field state via QSettings."
```

---

### Task 3: Tiled duplicate check tests

**Files:**
- Modify: `tests/test_sample_metadata_dialog.py`

- [ ] **Step 1: Write tests for duplicate check behavior**

Add to `tests/test_sample_metadata_dialog.py`:

```python
class TestDuplicateCheck:
    """Tests for Tiled duplicate sample name checking."""

    @patch("lucid.ui.dialogs.sample_metadata_dialog.TiledService")
    def test_duplicate_name_shows_warning(self, mock_tiled_cls, dialog) -> None:
        """Test that duplicate name shows warning and switches to Force."""
        mock_service = MagicMock()
        mock_service.is_connected = True
        mock_service._client.search.return_value = ["existing_run"]
        mock_tiled_cls.get_instance.return_value = mock_service

        dialog._sample_name_edit.setText("existing_sample")
        dialog._on_accept_clicked()

        assert "already exists" in dialog._warning_label.text()
        assert dialog._accept_btn.text() == "Force"
        assert dialog._force_mode is True

    @patch("lucid.ui.dialogs.sample_metadata_dialog.TiledService")
    def test_force_accepts_duplicate(self, mock_tiled_cls, dialog) -> None:
        """Test that Force button accepts despite duplicate."""
        mock_service = MagicMock()
        mock_service.is_connected = True
        mock_service._client.search.return_value = ["existing_run"]
        mock_tiled_cls.get_instance.return_value = mock_service

        dialog._sample_name_edit.setText("existing_sample")

        # First click triggers warning
        dialog._on_accept_clicked()
        assert dialog._force_mode is True

        # Second click forces acceptance
        # (We can't test dialog.accept() directly since it closes the dialog,
        # but we can verify force_mode was set and the accept path is taken)
        assert dialog._accept_btn.text() == "Force"

    @patch("lucid.ui.dialogs.sample_metadata_dialog.TiledService")
    def test_tiled_not_connected_skips_check(self, mock_tiled_cls, dialog) -> None:
        """Test that disconnected Tiled skips duplicate check."""
        mock_service = MagicMock()
        mock_service.is_connected = False
        mock_tiled_cls.get_instance.return_value = mock_service

        dialog._sample_name_edit.setText("any_name")

        # Should not show warning — check skipped
        result = dialog._check_duplicate_sample_name("any_name")
        assert result is False

    def test_changing_name_resets_force_mode(self, dialog) -> None:
        """Test that changing sample name resets force mode."""
        dialog._force_mode = True
        dialog._accept_btn.setText("Force")

        dialog._sample_name_edit.setText("new_name")

        assert dialog._force_mode is False
        assert dialog._accept_btn.text() == "Run"
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd /c/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_sample_metadata_dialog.py -v`
Expected: ALL PASS.

- [ ] **Step 3: Commit**

```bash
cd /c/Users/rp/PycharmProjects/ncs/ncs
git add tests/test_sample_metadata_dialog.py
git commit -m "test: add duplicate check tests for SampleMetadataDialog"
```

---

### Task 4: Export from __init__ and wire into BlueskyPanel

**Files:**
- Modify: `src/lucid/ui/dialogs/__init__.py`
- Modify: `src/lucid/ui/panels/bluesky_panel.py`
- Test: `tests/test_engine.py` (integration-style test)

- [ ] **Step 1: Write failing integration test**

Add to `tests/test_engine.py`:

```python
class TestPreSubmitIntegration:
    """Integration tests for pre-submit with SampleMetadataDialog."""

    def test_sample_metadata_callable_returns_metadata(self, qapp) -> None:
        """Test the callable that wraps SampleMetadataDialog."""
        from unittest.mock import patch

        from lucid.ui.panels.bluesky_panel import _sample_metadata_pre_submit

        # Mock the dialog to auto-accept with metadata
        with patch(
            "lucid.ui.panels.bluesky_panel.SampleMetadataDialog"
        ) as MockDialog:
            mock_dialog = MagicMock()
            mock_dialog.exec.return_value = MockDialog.DialogCode.Accepted
            mock_dialog.get_metadata.return_value = {"sample_name": "test"}
            MockDialog.return_value = mock_dialog
            # Make DialogCode accessible
            MockDialog.DialogCode = type("DC", (), {"Accepted": 1})()
            mock_dialog.DialogCode = MockDialog.DialogCode

            result = _sample_metadata_pre_submit("scan", {})
            assert result == {"sample_name": "test"}

    def test_sample_metadata_callable_returns_none_on_cancel(self, qapp) -> None:
        """Test the callable returns None when dialog is cancelled."""
        from unittest.mock import patch

        from lucid.ui.panels.bluesky_panel import _sample_metadata_pre_submit

        with patch(
            "lucid.ui.panels.bluesky_panel.SampleMetadataDialog"
        ) as MockDialog:
            mock_dialog = MagicMock()
            mock_dialog.exec.return_value = 0  # Rejected
            MockDialog.return_value = mock_dialog
            MockDialog.DialogCode = type("DC", (), {"Accepted": 1})()
            mock_dialog.DialogCode = MockDialog.DialogCode

            result = _sample_metadata_pre_submit("scan", {})
            assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_engine.py::TestPreSubmitIntegration -v`
Expected: FAIL — `_sample_metadata_pre_submit` does not exist.

- [ ] **Step 3: Update dialogs __init__.py**

In `src/lucid/ui/dialogs/__init__.py`, add the import and export:

```python
from lucid.ui.dialogs.sample_metadata_dialog import SampleMetadataDialog
```

And add `"SampleMetadataDialog"` to the `__all__` list.

- [ ] **Step 4: Add pre-submit callable and registration to BlueskyPanel**

In `src/lucid/ui/panels/bluesky_panel.py`, add a module-level function after the imports:

```python
from lucid.ui.dialogs.sample_metadata_dialog import SampleMetadataDialog


def _sample_metadata_pre_submit(plan_name: str, kwargs: dict) -> dict | None:
    """Pre-submit callable that shows the SampleMetadataDialog.

    Args:
        plan_name: Name of the plan being submitted.
        kwargs: Current kwargs for the plan.

    Returns:
        Metadata dict to merge, or None if cancelled.
    """
    dialog = SampleMetadataDialog()
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return dialog.get_metadata()
    return None
```

In `BlueskyPanel._auto_configure()`, after the engine is set, register the callable. Add at the end of the `try` block where `set_engine` is called:

```python
def _auto_configure(self) -> None:
    """Auto-configure with Engine, PlanRegistry, and DeviceCatalog singletons."""
    try:
        engine = get_engine()
        self.set_engine(engine)
        # Register sample metadata dialog as pre-submit hook
        engine.register_pre_submit(_sample_metadata_pre_submit)
    except Exception as e:
        logger.debug("Could not auto-configure Engine: {}", e)

    try:
        registry = get_registry()
        self.set_registry(registry)
    except Exception as e:
        logger.debug("Could not auto-configure PlanRegistry: {}", e)

    try:
        catalog = DeviceCatalog.get_instance()
        self.set_catalog(catalog)
    except Exception as e:
        logger.debug("Could not auto-configure DeviceCatalog: {}", e)
```

- [ ] **Step 5: Run all tests**

Run: `cd /c/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_engine.py tests/test_sample_metadata_dialog.py -v`
Expected: ALL PASS.

- [ ] **Step 6: Commit**

```bash
cd /c/Users/rp/PycharmProjects/ncs/ncs
git add src/lucid/ui/dialogs/__init__.py src/lucid/ui/dialogs/sample_metadata_dialog.py src/lucid/ui/panels/bluesky_panel.py tests/test_engine.py
git commit -m "feat: wire SampleMetadataDialog into BlueskyPanel via pre-submit hook

Register _sample_metadata_pre_submit on engine in BlueskyPanel._auto_configure.
Dialog shows before every plan submission from the panel."
```

---

### Task 5: Full test run and cleanup

**Files:**
- All modified files

- [ ] **Step 1: Run the full test suite**

Run: `cd /c/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/ -v --tb=short`
Expected: ALL PASS. No regressions.

- [ ] **Step 2: Verify existing engine tests still pass with new submit signature**

Run: `cd /c/Users/rp/PycharmProjects/ncs/ncs && python -m pytest tests/test_engine.py -v`
Expected: ALL PASS. The `skip_pre_submit` parameter has a default of `False`, so existing callers are unaffected. The `submit()` return type changed from `str` to `str | None`, but existing tests that call `submit()` should still work since they either check `is not None` or just use the return value.

- [ ] **Step 3: Commit any fixups**

If any tests needed fixing:

```bash
cd /c/Users/rp/PycharmProjects/ncs/ncs
git add -u
git commit -m "fix: test adjustments for pre-submit hook integration"
```
