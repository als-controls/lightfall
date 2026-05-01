# Tiled Data Browser Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all known bugs in the Tiled Data Browser panel and add double-click-to-visualize capability.

**Architecture:** The Data Browser uses a proper Qt MVC stack (TiledRecordModel + TiledRecordFilterProxy + QTableView) with background data fetching via QThreadFuture. Most bugs stem from incorrect Tiled query key paths, dates always being applied, and the callback from the background fetch not properly restoring UI state. The double-click feature requires replaying Bluesky documents from Tiled through the existing VisualizationPanel document pipeline.

**Tech Stack:** PySide6, tiled (Python client), Bluesky document model

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/lucid/ui/panels/tiled_browser_panel.py` | Modify | Fix callback, query key paths, add replay logic |
| `src/lucid/ui/models/tiled_model.py` | Modify | Reorder columns, add fetchMore lazy loading |
| `src/lucid/ui/widgets/tiled_filter_widget.py` | Modify | Fix date filter to allow "no date" |
| `src/lucid/ui/panels/visualization_panel.py` | Modify | Add public method to replay historical documents |
| `tests/ui/panels/test_tiled_browser_panel.py` | Create | Tests for browser panel fixes |
| `tests/ui/models/test_tiled_model.py` | Create | Tests for model changes |

---

## Task 0: Diagnostic Investigation

**Why:** Several bugs have likely root causes that need runtime confirmation before we can write the correct fix. This task gathers the data we need.

**Files:**
- Read: `src/lucid/ui/panels/tiled_browser_panel.py`
- Read: `src/lucid/services/tiled_service.py`

- [ ] **Step 1: Check Tiled entry metadata structure**

Add temporary debug logging to `_entry_to_record` to dump the actual metadata structure. This tells us (a) whether `stop` is in metadata and what it looks like, (b) the actual key paths for time/plan_name/exit_status, and (c) whether `.documents()` is available on entries.

```python
# In _entry_to_record, at line 462 after metadata = entry.metadata
logger.warning("DIAG: metadata keys = {}", list(metadata.keys()))
logger.warning("DIAG: stop_doc type = {}, value = {}", type(metadata.get("stop")), metadata.get("stop"))
logger.warning("DIAG: entry type = {}, dir = {}", type(entry), [x for x in dir(entry) if not x.startswith('_')])
```

Run the application and trigger a load. Check the log output.

- [ ] **Step 2: Check if FullText queries work**

Add logging to `_build_query` to see if FullText throws:

```python
# In _build_query, line 431
if filters.text_query:
    try:
        result = result.search(FullText(filters.text_query))
        logger.warning("DIAG: FullText search succeeded, result count = {}", len(result))
    except Exception as e:
        logger.warning("DIAG: FullText search FAILED: {}", e)
```

- [ ] **Step 3: Check if _on_records_loaded fires**

Add logging at the top of `_on_records_loaded`:

```python
logger.warning("DIAG: _on_records_loaded called, result type = {}, is None = {}", type(result), result is None)
```

And at the top of `_on_load_error`:

```python
logger.warning("DIAG: _on_load_error called, error = {}", error)
```

- [ ] **Step 4: Check Tiled Key path structure**

Test in a Python console connected to the same Tiled server:

```python
from tiled.client import from_uri
from tiled.queries import Key

client = from_uri("http://...")  # use same URL from config
# Try top-level key
try:
    r1 = client.search(Key("time") >= 0)
    print(f"Key('time') works: {len(r1)} results")
except Exception as e:
    print(f"Key('time') failed: {e}")

# Try nested key
try:
    r2 = client.search(Key("start.time") >= 0)
    print(f"Key('start.time') works: {len(r2)} results")
except Exception as e:
    print(f"Key('start.time') failed: {e}")
```

- [ ] **Step 5: Remove diagnostic logging and commit**

Remove all `DIAG:` log lines. Document findings for tasks below.

```bash
git add -A && git commit -m "chore: remove diagnostic logging from tiled browser"
```

---

## Task 1: Fix "Loading..." Persists and Refresh Button Disabled

**Why:** After data loads, the status label stays at "Loading..." and the refresh button stays disabled. This means `_on_records_loaded` either isn't being called, or it's throwing an exception that PySide6 swallows silently.

**Files:**
- Modify: `src/lucid/ui/panels/tiled_browser_panel.py:291-327,505-541`
- Test: `tests/ui/panels/test_tiled_browser_panel.py`

**Likely root cause:** Either (a) the QThreadFuture callback signature doesn't match, (b) an exception inside `_on_records_loaded` is swallowed by PySide6's signal dispatch, or (c) the `result is None` early return triggers incorrectly.

- [ ] **Step 1: Write test for loading state management**

```python
# tests/ui/panels/test_tiled_browser_panel.py
"""Tests for TiledBrowserPanel loading behavior."""

import pytest
from unittest.mock import MagicMock, patch

from lucid.ui.models.tiled_model import TiledRecord
from lucid.ui.panels.tiled_browser_panel import TiledBrowserPanel


@pytest.fixture
def make_record():
    """Factory for TiledRecord test instances."""
    from datetime import datetime

    def _make(uid="test-uid-1", scan_id=1, plan="count", status="success"):
        return TiledRecord(
            uid=uid,
            scan_id=scan_id,
            plan_name=plan,
            timestamp=datetime(2026, 4, 7, 12, 0, 0),
            exit_status=status,
            num_points=10,
            duration=5.0,
            sample_name="test_sample",
            metadata={},
            _client_key=uid,
        )

    return _make


class TestLoadingStateManagement:
    """Test that loading state is properly managed."""

    def test_on_records_loaded_restores_state(self, qtbot, make_record):
        """After records load, Loading label and refresh button should reset."""
        with patch.object(TiledBrowserPanel, "__init__", lambda self, *a, **kw: None):
            panel = TiledBrowserPanel.__new__(TiledBrowserPanel)

        # Manually set up minimum required state
        panel._loading = True
        panel._model = MagicMock()
        panel._filter_widget = MagicMock()
        panel._status_label = MagicMock()
        panel._refresh_btn = MagicMock()
        panel._tiled_service = MagicMock()
        panel._tiled_service.state = MagicMock()
        panel._tiled_service.get_status_info.return_value = {"url": "http://test"}
        panel._total_records = 0
        panel._current_page = 0
        panel.PAGE_SIZE = 100
        panel._page_label = MagicMock()
        panel._prev_btn = MagicMock()
        panel._next_btn = MagicMock()
        panel._count_label = MagicMock()

        records = [make_record()]
        result = (records, 1, ["count"])

        panel._on_records_loaded(result)

        assert panel._loading is False
        panel._refresh_btn.setEnabled.assert_called_with(True)

    def test_on_records_loaded_none_still_restores(self, qtbot, make_record):
        """Even if result is None, loading state should be cleaned up."""
        with patch.object(TiledBrowserPanel, "__init__", lambda self, *a, **kw: None):
            panel = TiledBrowserPanel.__new__(TiledBrowserPanel)

        panel._loading = True
        panel._refresh_btn = MagicMock()
        panel._status_label = MagicMock()
        panel._tiled_service = MagicMock()

        panel._on_records_loaded(None)

        # Currently this returns early without cleanup - this test
        # documents the bug and will pass after the fix
        assert panel._loading is False
        panel._refresh_btn.setEnabled.assert_called_with(True)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /c/Users/rp/PycharmProjects/ncs/ncs
python -m pytest tests/ui/panels/test_tiled_browser_panel.py -v
```

Expected: `test_on_records_loaded_none_still_restores` FAILS (the None early return doesn't restore state).

- [ ] **Step 3: Fix _on_records_loaded to always restore UI state**

In `src/lucid/ui/panels/tiled_browser_panel.py`, replace the `_on_records_loaded` method:

```python
@Slot(object)
def _on_records_loaded(self, result: tuple | None = None) -> None:
    """Handle records loaded from background thread.

    Args:
        result: Tuple of (records, total_count, plan_names) from _do_fetch,
                or None if no result.
    """
    self._loading = False

    if result is None:
        self._update_status()
        self._refresh_btn.setEnabled(True)
        return

    try:
        records, total_count, plan_names = result
        self._total_records = total_count
        self._model.set_records(records)
        self._filter_widget.set_plan_names(plan_names or [])
        self._update_pagination()
    except Exception as e:
        logger.error("Error processing loaded records: {}", e)

    self._update_status()
    self._refresh_btn.setEnabled(True)

    logger.debug(
        "Loaded {} records (page {} of {})",
        self._model.rowCount(),
        self._current_page + 1,
        max(1, (self._total_records + self.PAGE_SIZE - 1) // self.PAGE_SIZE),
    )
```

Key changes:
1. `self._loading = False` moved to top (always runs)
2. `result is None` still returns early but restores UI first
3. Try/except around unpacking to catch any issue without losing UI state
4. `_update_status()` and `_refresh_btn.setEnabled(True)` always called

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /c/Users/rp/PycharmProjects/ncs/ncs
python -m pytest tests/ui/panels/test_tiled_browser_panel.py -v
```

Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
cd /c/Users/rp/PycharmProjects/ncs/ncs
git add tests/ui/panels/test_tiled_browser_panel.py src/lucid/ui/panels/tiled_browser_panel.py
git commit -m "fix(tiled-browser): always restore UI state after load completes

Previously, if _on_records_loaded received None or threw during
unpacking, the panel stayed in 'Loading...' state with refresh
disabled. Now loading flag, status, and refresh button are always
restored regardless of the result."
```

---

## Task 2: Fix Status Showing "Running" for Completed Runs

**Why:** Completed runs display "running" in the Status column instead of their actual exit status.

**Files:**
- Modify: `src/lucid/ui/panels/tiled_browser_panel.py:452-503`
- Test: `tests/ui/panels/test_tiled_browser_panel.py`

**Root cause:** In `_entry_to_record` (line 466):
```python
stop_doc = metadata.get("stop", {})
```
If `"stop"` key is absent, this returns `{}` (empty dict, **falsy** in Python). If `"stop"` is present but `None` (Tiled's representation of an in-progress run), `.get("stop", {})` returns `None` (also falsy). Either way, line 476 falls to `"running"`. The real issue depends on what Task 0 reveals about the actual metadata structure.

**Most likely fix (adjust after Task 0 findings):** The metadata structure from Tiled for Bluesky runs typically stores `start` and `stop` at the top level of `entry.metadata`, where `stop` is `None` for in-progress runs and a dict for completed runs. The empty-dict default masks the difference.

- [ ] **Step 1: Write test for status extraction**

```python
# Add to tests/ui/panels/test_tiled_browser_panel.py

class TestEntryToRecord:
    """Test _entry_to_record metadata extraction."""

    def _make_panel(self):
        """Create a minimal TiledBrowserPanel for testing."""
        with patch.object(TiledBrowserPanel, "__init__", lambda self, *a, **kw: None):
            return TiledBrowserPanel.__new__(TiledBrowserPanel)

    def test_completed_run_has_correct_status(self):
        """Completed run should show 'success', not 'running'."""
        panel = self._make_panel()
        entry = MagicMock()
        entry.metadata = {
            "start": {
                "uid": "abc-123",
                "scan_id": 42,
                "plan_name": "count",
                "time": 1712500000.0,
            },
            "stop": {
                "exit_status": "success",
                "time": 1712500060.0,
                "num_events": {"primary": 100},
            },
        }

        record = panel._entry_to_record("abc-123", entry)
        assert record.exit_status == "success"
        assert record.num_points == 100
        assert record.duration == pytest.approx(60.0)

    def test_running_run_with_none_stop(self):
        """In-progress run (stop=None) should show 'running'."""
        panel = self._make_panel()
        entry = MagicMock()
        entry.metadata = {
            "start": {
                "uid": "abc-456",
                "scan_id": 43,
                "plan_name": "scan",
                "time": 1712500000.0,
            },
            "stop": None,
        }

        record = panel._entry_to_record("abc-456", entry)
        assert record.exit_status == "running"

    def test_missing_stop_key_shows_unknown(self):
        """If stop key is entirely absent, show 'unknown'."""
        panel = self._make_panel()
        entry = MagicMock()
        entry.metadata = {
            "start": {
                "uid": "abc-789",
                "scan_id": 44,
                "plan_name": "count",
                "time": 1712500000.0,
            },
        }

        record = panel._entry_to_record("abc-789", entry)
        assert record.exit_status == "unknown"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/ui/panels/test_tiled_browser_panel.py::TestEntryToRecord -v
```

Expected: `test_running_run_with_none_stop` and `test_missing_stop_key_shows_unknown` FAIL.

- [ ] **Step 3: Fix _entry_to_record stop doc handling**

In `src/lucid/ui/panels/tiled_browser_panel.py`, replace lines 464-485:

```python
        # Get start document
        start_doc = metadata.get("start", {}) or {}

        # Get stop document - distinguish between None (running), absent (unknown), and present
        stop_doc_raw = metadata.get("stop", _SENTINEL)
        if stop_doc_raw is _SENTINEL:
            # Key not present at all
            stop_doc = None
            exit_status = "unknown"
        elif stop_doc_raw is None:
            # Explicitly None = run still in progress
            stop_doc = None
            exit_status = "running"
        else:
            stop_doc = stop_doc_raw
            exit_status = stop_doc.get("exit_status", "unknown")
```

And add the sentinel at module level (after the imports):

```python
_SENTINEL = object()
```

Also fix duration and num_points to use the new stop_doc:

```python
        # Calculate duration
        duration = None
        if stop_doc and "time" in stop_doc:
            stop_time = stop_doc["time"]
            duration = stop_time - time_val

        # Number of points from stop document
        num_points = 0
        if stop_doc:
            num_points = stop_doc.get("num_events", {}).get("primary", 0)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/ui/panels/test_tiled_browser_panel.py::TestEntryToRecord -v
```

Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lucid/ui/panels/tiled_browser_panel.py tests/ui/panels/test_tiled_browser_panel.py
git commit -m "fix(tiled-browser): correctly distinguish running vs completed runs

Use sentinel to distinguish 'stop key absent' from 'stop is None'
(running) from 'stop is a dict' (completed). Previously empty dict
default made both absent and None appear as running."
```

---

## Task 3: Fix Date Filter

**Why:** Date range filter doesn't work. Two bugs: (a) dates are always applied (never None), so every query is constrained to the last 30 days by default, and (b) the Key path for time queries is likely wrong.

**Files:**
- Modify: `src/lucid/ui/widgets/tiled_filter_widget.py:99-168,229-249`
- Modify: `src/lucid/ui/panels/tiled_browser_panel.py:398-450`
- Test: `tests/ui/models/test_tiled_model.py`

### Part A: Fix get_filters() to allow None dates

- [ ] **Step 1: Write test for filter with no date constraint**

```python
# tests/ui/widgets/test_tiled_filter_widget.py
"""Tests for TiledFilterWidget."""

import pytest
from datetime import datetime
from lucid.ui.widgets.tiled_filter_widget import TiledFilters


class TestTiledFilters:
    """Test TiledFilters dataclass."""

    def test_empty_filters(self):
        """Default filters should have no constraints."""
        f = TiledFilters()
        assert f.is_empty()
        assert f.start_date is None
        assert f.end_date is None

    def test_filters_with_dates(self):
        """Filters with explicit dates should not be empty."""
        f = TiledFilters(
            start_date=datetime(2026, 1, 1),
            end_date=datetime(2026, 4, 7),
        )
        assert not f.is_empty()
```

- [ ] **Step 2: Add "no date" option to filter widget**

In `src/lucid/ui/widgets/tiled_filter_widget.py`, replace the date setup in `_setup_ui` (lines 148-168):

```python
        # From date
        from_label = QLabel("From:")
        row2.addWidget(from_label)

        self._from_date = QDateEdit()
        self._from_date.setCalendarPopup(True)
        self._from_date.setDisplayFormat("yyyy-MM-dd")
        self._from_date.dateChanged.connect(self._on_filter_changed)
        row2.addWidget(self._from_date)

        self._from_enabled = QCheckBox()
        self._from_enabled.setChecked(False)
        self._from_enabled.toggled.connect(self._on_date_enabled_changed)
        row2.addWidget(self._from_enabled)

        # To date
        to_label = QLabel("To:")
        row2.addWidget(to_label)

        self._to_date = QDateEdit()
        self._to_date.setCalendarPopup(True)
        self._to_date.setDisplayFormat("yyyy-MM-dd")
        self._to_date.dateChanged.connect(self._on_filter_changed)
        row2.addWidget(self._to_date)

        self._to_enabled = QCheckBox()
        self._to_enabled.setChecked(False)
        self._to_enabled.toggled.connect(self._on_date_enabled_changed)
        row2.addWidget(self._to_enabled)
```

Add the `QCheckBox` import at the top:

```python
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    ...
)
```

Add new slot:

```python
    def _on_date_enabled_changed(self, checked: bool) -> None:
        """Handle date checkbox toggle."""
        self._from_date.setEnabled(self._from_enabled.isChecked())
        self._to_date.setEnabled(self._to_enabled.isChecked())
        self._emit_filters()
```

Initialize dates to sensible defaults but disabled:

```python
        self._from_date.setDate(datetime.now().date() - timedelta(days=30))
        self._from_date.setEnabled(False)
        self._to_date.setDate(datetime.now().date())
        self._to_date.setEnabled(False)
```

Update `get_filters()` to respect checkboxes:

```python
    def get_filters(self) -> TiledFilters:
        """Get the current filter settings."""
        start_dt = None
        end_dt = None

        if self._from_enabled.isChecked():
            from_date = self._from_date.date().toPython()
            start_dt = datetime.combine(from_date, datetime.min.time())

        if self._to_enabled.isChecked():
            to_date = self._to_date.date().toPython()
            end_dt = datetime.combine(to_date, datetime.max.time())

        return TiledFilters(
            start_date=start_dt,
            end_date=end_dt,
            text_query=self._search_input.text().strip(),
            plan_name=self._plan_combo.currentData(),
            exit_status=self._status_combo.currentData(),
        )
```

Update `_on_clear_clicked` to also uncheck the date checkboxes:

```python
        self._from_enabled.blockSignals(True)
        self._to_enabled.blockSignals(True)
        # ... existing clear logic ...
        self._from_enabled.setChecked(False)
        self._to_enabled.setChecked(False)
        self._from_date.setEnabled(False)
        self._to_date.setEnabled(False)
        self._from_enabled.blockSignals(False)
        self._to_enabled.blockSignals(False)
```

Update `set_enabled` to include the new checkboxes:

```python
        self._from_enabled.setEnabled(enabled)
        self._to_enabled.setEnabled(enabled)
```

### Part B: Fix Tiled Key paths

- [ ] **Step 3: Fix Key paths in _build_query (adjust based on Task 0 findings)**

In `src/lucid/ui/panels/tiled_browser_panel.py`, update `_build_query`. The correct key paths depend on Task 0 investigation but most likely need the nested form:

```python
    def _build_query(self, client: Any, filters: TiledFilters) -> Any:
        """Build Tiled query from filters."""
        try:
            from tiled.queries import Key
        except ImportError:
            logger.warning("tiled.queries not available, returning unfiltered results")
            return client

        result = client

        # Apply time filters (time is nested under start document)
        if filters.start_date:
            try:
                result = result.search(Key("time") >= filters.start_date.timestamp())
            except Exception as e:
                logger.warning("Failed to apply start_date filter: {}", e)

        if filters.end_date:
            try:
                result = result.search(Key("time") <= filters.end_date.timestamp())
            except Exception as e:
                logger.warning("Failed to apply end_date filter: {}", e)

        # Apply plan name filter (nested under start document)
        if filters.plan_name:
            try:
                result = result.search(Key("plan_name") == filters.plan_name)
            except Exception as e:
                logger.warning("Failed to apply plan_name filter: {}", e)

        # Apply exit status filter (nested under stop document)
        if filters.exit_status:
            try:
                result = result.search(Key("exit_status") == filters.exit_status)
            except Exception as e:
                logger.warning("Failed to apply exit_status filter: {}", e)

        return result
```

**NOTE:** The exact Key path syntax (`Key("time")` vs `Key("start.time")` vs `Key("start", "time")`) MUST be determined from Task 0, Step 4. The code above keeps `Key("time")` as a placeholder - update based on findings. The Tiled `CatalogOfBlueskyRuns` adapter may provide top-level convenience keys. Also, FullText search has been deliberately removed from server-side queries (see Task 4 for client-side text search).

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/ui/widgets/test_tiled_filter_widget.py tests/ui/panels/test_tiled_browser_panel.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/lucid/ui/widgets/tiled_filter_widget.py src/lucid/ui/panels/tiled_browser_panel.py tests/
git commit -m "fix(tiled-browser): fix date filter - dates now optional with checkboxes

Dates were always applied (defaulting to 30-day window), hiding older
data. Now dates are disabled by default with checkboxes to enable.
Also fixed Tiled Key paths for server-side filtering."
```

---

## Task 4: Fix Search Box

**Why:** Text search likely doesn't work because FullText queries may not be supported by the Tiled adapter, and failures are silently caught.

**Files:**
- Modify: `src/lucid/ui/panels/tiled_browser_panel.py:398-450`
- Modify: `src/lucid/ui/models/tiled_model.py:253-317`

**Strategy:** Remove server-side FullText search (unreliable across Tiled adapters). Instead, rely on the client-side proxy model filter which already exists and works. The search text from the filter widget should drive the proxy model's text filter, NOT the server-side query.

- [ ] **Step 1: Wire search text to proxy model filter**

In `src/lucid/ui/panels/tiled_browser_panel.py`, update `_on_filters_changed`:

```python
    @Slot(object)
    def _on_filters_changed(self, filters: TiledFilters) -> None:
        """Handle filter changes from filter widget."""
        # Apply text filter locally via proxy model (fast, no server round-trip)
        self._proxy_model.set_text_filter(filters.text_query)

        # Apply status filter locally via proxy model
        self._proxy_model.set_status_filter(filters.exit_status)

        # Only reload from server if server-side filters changed
        server_filters_changed = (
            filters.start_date != self._current_filters.start_date
            or filters.end_date != self._current_filters.end_date
            or filters.plan_name != self._current_filters.plan_name
        )

        self._current_filters = filters

        if server_filters_changed:
            self._current_page = 0
            self._load_data()
```

And remove text_query and exit_status from `_build_query` (they're now handled client-side):

```python
    def _build_query(self, client: Any, filters: TiledFilters) -> Any:
        """Build Tiled query from filters.

        Only applies server-side filters (date range, plan name).
        Text search and status filtering are handled client-side by
        the proxy model for reliability across Tiled adapters.
        """
        try:
            from tiled.queries import Key
        except ImportError:
            logger.warning("tiled.queries not available, returning unfiltered results")
            return client

        result = client

        if filters.start_date:
            try:
                result = result.search(Key("time") >= filters.start_date.timestamp())
            except Exception as e:
                logger.warning("Failed to apply start_date filter: {}", e)

        if filters.end_date:
            try:
                result = result.search(Key("time") <= filters.end_date.timestamp())
            except Exception as e:
                logger.warning("Failed to apply end_date filter: {}", e)

        if filters.plan_name:
            try:
                result = result.search(Key("plan_name") == filters.plan_name)
            except Exception as e:
                logger.warning("Failed to apply plan_name filter: {}", e)

        return result
```

- [ ] **Step 2: Run tests**

```bash
python -m pytest tests/ -v -k tiled
```

- [ ] **Step 3: Commit**

```bash
git add src/lucid/ui/panels/tiled_browser_panel.py
git commit -m "fix(tiled-browser): move text/status search to client-side proxy model

FullText queries are unreliable across Tiled adapters. Text search
and status filtering now use the local QSortFilterProxyModel, which
is fast and always works. Only date range and plan name filters
hit the server."
```

---

## Task 5: Reorder Columns

**Why:** Column order should be: Sample Name, Plan, Timestamp, Status, Scan ID. Points and Duration can be removed (they're available as tooltips or in a detail view later).

**Files:**
- Modify: `src/lucid/ui/models/tiled_model.py:53-251`
- Modify: `src/lucid/ui/panels/tiled_browser_panel.py:124-148` (header config)
- Test: `tests/ui/models/test_tiled_model.py`

- [ ] **Step 1: Write test for new column order**

```python
# tests/ui/models/test_tiled_model.py
"""Tests for TiledRecordModel."""

import pytest
from datetime import datetime
from lucid.ui.models.tiled_model import TiledRecord, TiledRecordModel
from PySide6.QtCore import Qt


@pytest.fixture
def model():
    return TiledRecordModel()


@pytest.fixture
def sample_record():
    return TiledRecord(
        uid="test-uid",
        scan_id=42,
        plan_name="count",
        timestamp=datetime(2026, 4, 7, 12, 0, 0),
        exit_status="success",
        num_points=100,
        duration=60.0,
        sample_name="my_sample",
        metadata={},
        _client_key="test-uid",
    )


class TestColumnOrder:
    """Test that columns are in the expected order."""

    def test_column_names(self, model):
        assert model.COLUMNS == ["Sample", "Plan", "Timestamp", "Status", "Scan ID"]

    def test_sample_is_first_column(self, model, sample_record):
        model.set_records([sample_record])
        idx = model.index(0, 0)
        assert model.data(idx, Qt.ItemDataRole.DisplayRole) == "my_sample"

    def test_plan_is_second_column(self, model, sample_record):
        model.set_records([sample_record])
        idx = model.index(0, 1)
        assert model.data(idx, Qt.ItemDataRole.DisplayRole) == "count"

    def test_scan_id_is_last_column(self, model, sample_record):
        model.set_records([sample_record])
        idx = model.index(0, 4)
        assert model.data(idx, Qt.ItemDataRole.DisplayRole) == "42"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/ui/models/test_tiled_model.py -v
```

- [ ] **Step 3: Update column definitions in TiledRecordModel**

In `src/lucid/ui/models/tiled_model.py`, replace the COLUMNS and display logic:

```python
    COLUMNS = ["Sample", "Plan", "Timestamp", "Status", "Scan ID"]
```

Replace `_get_display_data`:

```python
    def _get_display_data(self, record: TiledRecord, col: int) -> str:
        """Get display text for a column."""
        if col == 0:  # Sample
            return record.sample_name or "-"
        elif col == 1:  # Plan
            return record.plan_name or "-"
        elif col == 2:  # Timestamp
            return record.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        elif col == 3:  # Status
            return record.exit_status or "-"
        elif col == 4:  # Scan ID
            return str(record.scan_id) if record.scan_id is not None else "-"
        return ""
```

Replace `_get_tooltip_data`:

```python
    def _get_tooltip_data(self, record: TiledRecord, col: int) -> str | None:
        """Get tooltip text for a column."""
        if col == 4:  # Scan ID
            return f"UID: {record.uid}"
        elif col == 2:  # Timestamp
            parts = [record.timestamp.isoformat()]
            if record.duration is not None:
                parts.append(f"Duration: {record.duration:.1f}s")
            if record.num_points:
                parts.append(f"Points: {record.num_points}")
            return "\n".join(parts)
        return None
```

Replace `_get_foreground_data`:

```python
    def _get_foreground_data(self, record: TiledRecord, col: int) -> Any:
        """Get foreground color for status column."""
        if col == 3:  # Status column
            from PySide6.QtGui import QColor

            status = record.exit_status.lower() if record.exit_status else ""
            if status == "success":
                return QColor(0, 128, 0)
            elif status in ("fail", "error"):
                return QColor(192, 0, 0)
            elif status == "abort":
                return QColor(192, 128, 0)
            elif status == "running":
                return QColor(0, 100, 200)  # Blue for running
        return None
```

Replace `TextAlignmentRole` handling:

```python
        elif role == Qt.ItemDataRole.TextAlignmentRole:
            if col == 4:  # Scan ID - right align
                return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
```

Update `lessThan` in `TiledRecordFilterProxy`:

```python
    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        """Compare two items for sorting."""
        source_model = self.sourceModel()
        if source_model is None:
            return False

        left_record = source_model.get_record(left.row())
        right_record = source_model.get_record(right.row())

        if left_record is None or right_record is None:
            return False

        col = left.column()

        if col == 2:  # Timestamp
            return left_record.timestamp < right_record.timestamp
        elif col == 4:  # Scan ID
            left_val = left_record.scan_id or 0
            right_val = right_record.scan_id or 0
            return left_val < right_val

        # Default string comparison
        left_data = source_model.data(left, Qt.ItemDataRole.DisplayRole)
        right_data = source_model.data(right, Qt.ItemDataRole.DisplayRole)
        return str(left_data or "") < str(right_data or "")
```

- [ ] **Step 4: Update header configuration in tiled_browser_panel.py**

Replace lines 136-147:

```python
        # Configure header
        header = self._table_view.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)       # Sample - stretch
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)   # Plan
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)  # Timestamp
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)  # Status
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)  # Scan ID

        # Set default widths for interactive columns
        self._table_view.setColumnWidth(1, 120)  # Plan
```

Update the default sort column:

```python
        self._table_view.sortByColumn(2, Qt.SortOrder.DescendingOrder)  # Sort by Timestamp desc
```

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/ui/models/test_tiled_model.py tests/ui/panels/test_tiled_browser_panel.py -v
```

- [ ] **Step 6: Commit**

```bash
git add src/lucid/ui/models/tiled_model.py src/lucid/ui/panels/tiled_browser_panel.py tests/
git commit -m "refactor(tiled-browser): reorder columns to Sample, Plan, Timestamp, Status, Scan ID

Points and Duration removed from columns (available in timestamp tooltip).
Sample name is now the first column for easier scanning."
```

---

## Task 6: Replace Pagination with Lazy Loading

**Why:** Qt views natively support lazy loading via `canFetchMore`/`fetchMore`. This is better UX than manual Prev/Next pagination buttons.

**Files:**
- Modify: `src/lucid/ui/models/tiled_model.py` (add fetchMore to source model)
- Modify: `src/lucid/ui/panels/tiled_browser_panel.py` (remove pagination UI, refactor loading)

**Architecture:** The TiledRecordModel gets a reference to the fetch function. When the view scrolls near the bottom, Qt calls `canFetchMore()` which returns True if there are more records on the server. Then `fetchMore()` triggers a background fetch for the next batch, and `append_records()` adds them to the model.

- [ ] **Step 1: Add fetchMore support to TiledRecordModel**

In `src/lucid/ui/models/tiled_model.py`, add to `TiledRecordModel`:

```python
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._records: list[TiledRecord] = []
        self._total_available: int = 0
        self._fetch_callback: Callable[[], None] | None = None
        self._fetching: bool = False

    def set_total_available(self, total: int) -> None:
        """Set the total number of records available on the server."""
        self._total_available = total

    def set_fetch_callback(self, callback: Callable[[], None] | None) -> None:
        """Set the callback to invoke when more data is needed."""
        self._fetch_callback = callback

    def canFetchMore(self, parent: QModelIndex | None = None) -> bool:
        """Return True if more records are available on the server."""
        if parent is None:
            parent = QModelIndex()
        if parent.isValid():
            return False
        return len(self._records) < self._total_available and not self._fetching

    def fetchMore(self, parent: QModelIndex | None = None) -> None:
        """Fetch the next batch of records."""
        if parent is None:
            parent = QModelIndex()
        if parent.isValid():
            return
        if self._fetching or not self._fetch_callback:
            return
        self._fetching = True
        self._fetch_callback()

    def set_fetching(self, fetching: bool) -> None:
        """Update the fetching state (called when background fetch completes)."""
        self._fetching = fetching
```

Add the `Callable` import:

```python
from collections.abc import Callable
```

- [ ] **Step 2: Refactor TiledBrowserPanel to use lazy loading**

In `src/lucid/ui/panels/tiled_browser_panel.py`:

Remove the pagination UI from `_setup_ui` (lines 155-176 - the entire pagination_layout section). Remove `_prev_btn`, `_next_btn`, `_page_label`, `_count_label`, `_current_page`, `_update_pagination`, `_on_prev_page`, `_on_next_page`.

Add a simple record count label in the status bar instead:

```python
        self._count_label = QLabel("")
        status_layout.addWidget(self._count_label)
```

Update `__init__` - remove `_current_page`, add fetch callback wiring:

```python
        self._total_records = 0
        self._loading = False
        self._fetch_thread: QThreadFuture | None = None

        self._model = TiledRecordModel()
        self._model.set_fetch_callback(self._fetch_more)
        self._proxy_model = TiledRecordFilterProxy()
        self._proxy_model.setSourceModel(self._model)
```

Replace `_load_data` to always start fresh (page 0):

```python
    def _load_data(self) -> None:
        """Load initial batch of data from Tiled server with current filters."""
        if not self._tiled_service.is_connected:
            return
        if self._loading:
            return

        client = self._tiled_service._client
        if client is None:
            return

        self._loading = True
        self._model.clear()
        self._refresh_btn.setEnabled(False)
        self._status_label.setText("Loading...")

        filters = self._current_filters
        self._fetch_thread = QThreadFuture(
            self._do_fetch,
            client,
            filters,
            0,  # start from beginning
            self.PAGE_SIZE,
            callback_slot=self._on_initial_load,
            except_slot=self._on_load_error,
            name="tiled_fetch",
        )
        self._fetch_thread.start()
```

Add `_fetch_more` for lazy loading:

```python
    def _fetch_more(self) -> None:
        """Fetch next batch of records (called by model's fetchMore)."""
        if not self._tiled_service.is_connected or self._loading:
            self._model.set_fetching(False)
            return

        client = self._tiled_service._client
        if client is None:
            self._model.set_fetching(False)
            return

        self._loading = True
        offset = self._model.rowCount()

        self._fetch_thread = QThreadFuture(
            self._do_fetch,
            client,
            self._current_filters,
            offset // self.PAGE_SIZE,  # page number
            self.PAGE_SIZE,
            callback_slot=self._on_more_loaded,
            except_slot=self._on_load_error,
            name="tiled_fetch_more",
        )
        self._fetch_thread.start()
```

Split the callback into initial vs incremental:

```python
    @Slot(object)
    def _on_initial_load(self, result: tuple | None = None) -> None:
        """Handle initial data load."""
        self._loading = False

        if result is None:
            self._update_status()
            self._refresh_btn.setEnabled(True)
            return

        try:
            records, total_count, plan_names = result
            self._total_records = total_count
            self._model.set_total_available(total_count)
            self._model.set_records(records)
            self._filter_widget.set_plan_names(plan_names or [])
            self._count_label.setText(f"{self._model.rowCount()} of {total_count}")
        except Exception as e:
            logger.error("Error processing loaded records: {}", e)

        self._update_status()
        self._refresh_btn.setEnabled(True)

    @Slot(object)
    def _on_more_loaded(self, result: tuple | None = None) -> None:
        """Handle incremental data load (lazy loading)."""
        self._loading = False
        self._model.set_fetching(False)

        if result is None:
            return

        try:
            records, total_count, plan_names = result
            self._total_records = total_count
            self._model.set_total_available(total_count)
            self._model.append_records(records)
            self._count_label.setText(f"{self._model.rowCount()} of {total_count}")
        except Exception as e:
            logger.error("Error appending records: {}", e)
```

- [ ] **Step 3: Remove pagination-related actions from introspection**

Remove `action_next_page`, `action_prev_page` methods and their entries in `_get_available_actions`.

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/ -v -k tiled
```

- [ ] **Step 5: Commit**

```bash
git add src/lucid/ui/models/tiled_model.py src/lucid/ui/panels/tiled_browser_panel.py
git commit -m "refactor(tiled-browser): replace pagination with lazy loading via fetchMore

Qt's canFetchMore/fetchMore mechanism loads records on demand as
the user scrolls, replacing manual Prev/Next buttons. Simpler UX
and no need to manage page state."
```

---

## Task 7: Double-Click to Open in Visualization Panel

**Why:** Double-clicking a run in the Data Browser should replay its documents through the Visualization panel so users can view historical data.

**Files:**
- Modify: `src/lucid/ui/panels/visualization_panel.py` (add replay_from_tiled method)
- Modify: `src/lucid/ui/panels/tiled_browser_panel.py` (connect signal, add replay logic)

**Architecture:** The VisualizationPanel already accepts documents via `_on_document(name, doc)` and auto-resets on a new "start" document. We add a public method `replay_documents(docs)` that feeds a sequence of (name, doc) pairs through the pipeline. The TiledBrowserPanel fetches the full document stream from Tiled in a background thread and sends it to the VisualizationPanel.

**Key question from Task 0:** How to get full documents from a Tiled entry. Options:
- If `entry.documents()` exists, use it directly
- Otherwise, reconstruct from `entry.metadata` (start/stop) + `entry['primary'].read()` (data)

- [ ] **Step 1: Add replay_documents to VisualizationPanel**

In `src/lucid/ui/panels/visualization_panel.py`, add a public method:

```python
    def replay_documents(self, documents: list[tuple[str, dict]]) -> None:
        """Replay a sequence of Bluesky documents through the visualization pipeline.

        Used to visualize historical runs from the Data Browser.

        Args:
            documents: List of (name, doc) tuples in chronological order.
                       Expected order: start, descriptor(s), event(s), stop.
        """
        for name, doc in documents:
            self._on_document(name, doc)
            # Also feed to buffer for data access by visualization widgets
            if self._buffer:
                self._buffer(name, doc)
```

**Note:** The exact implementation depends on how `_on_document` and `_buffer` interact. If `set_engine()` already connects both, we may need to disconnect the engine first and manually route. Verify this works at runtime.

- [ ] **Step 2: Add document fetching to TiledBrowserPanel**

In `src/lucid/ui/panels/tiled_browser_panel.py`, add a method to fetch documents from a Tiled entry:

```python
    def _fetch_run_documents(self, client_key: str) -> list[tuple[str, dict]]:
        """Fetch all documents for a run from Tiled (background thread).

        Args:
            client_key: Key for the run in the Tiled catalog.

        Returns:
            List of (name, doc) pairs in chronological order.
        """
        client = self._tiled_service._client
        if client is None:
            return []

        entry = client[client_key]

        # Try the documents() API first (available in some Tiled adapters)
        if hasattr(entry, "documents"):
            return list(entry.documents())

        # Fallback: reconstruct from metadata and data arrays
        documents = []
        metadata = entry.metadata
        start_doc = metadata.get("start", {})
        if start_doc:
            documents.append(("start", dict(start_doc)))

        # Get descriptors and events from primary stream
        if "primary" in entry:
            primary = entry["primary"]
            if hasattr(primary, "metadata") and "descriptors" in primary.metadata:
                for desc in primary.metadata["descriptors"]:
                    documents.append(("descriptor", dict(desc)))

            # Read data as events
            dataset = primary.read()
            if dataset is not None:
                for i in range(len(dataset.get("time", []))):
                    event_doc = {
                        "data": {k: dataset[k].values[i] for k in dataset if k != "time"},
                        "timestamps": {k: dataset["time"].values[i] for k in dataset if k != "time"},
                        "time": float(dataset["time"].values[i]),
                        "seq_num": i + 1,
                        "uid": f"replayed-{i}",
                        "descriptor": documents[1][1]["uid"] if len(documents) > 1 else "",
                    }
                    documents.append(("event", event_doc))

        stop_doc = metadata.get("stop")
        if stop_doc:
            documents.append(("stop", dict(stop_doc)))

        return documents
```

- [ ] **Step 3: Connect double-click signal to replay**

In `src/lucid/ui/panels/tiled_browser_panel.py`, update `_on_table_double_clicked`:

```python
    @Slot()
    def _on_table_double_clicked(self) -> None:
        """Handle table row double-click - open run in Visualization panel."""
        selection = self._table_view.selectionModel().selectedRows()
        if not selection:
            return

        proxy_index = selection[0]
        source_index = self._proxy_model.mapToSource(proxy_index)
        record = self._model.get_record(source_index.row())
        if not record:
            return

        self.record_double_clicked.emit(record)
        logger.info("Opening run {} in visualization", record.uid[:8])

        # Fetch documents in background and replay in visualization
        self._fetch_thread = QThreadFuture(
            self._fetch_run_documents,
            record._client_key,
            callback_slot=self._on_run_documents_fetched,
            except_slot=self._on_replay_error,
            name="tiled_replay",
        )
        self._fetch_thread.start()

    @Slot(object)
    def _on_run_documents_fetched(self, documents: list | None = None) -> None:
        """Handle fetched documents - replay in visualization panel."""
        if not documents:
            logger.warning("No documents to replay")
            return

        # Find the visualization panel
        from lucid.ui.panels.visualization_panel import VisualizationPanel
        viz_panel = self._find_panel(VisualizationPanel)
        if viz_panel is None:
            logger.warning("Visualization panel not found")
            return

        viz_panel.replay_documents(documents)

    @Slot(Exception)
    def _on_replay_error(self, error: Exception) -> None:
        """Handle error fetching run documents."""
        logger.error("Failed to fetch run documents: {}", error)

    def _find_panel(self, panel_class: type) -> Any:
        """Find an active panel instance by class.

        Walks the widget tree to find a panel of the given class.
        """
        from PySide6.QtWidgets import QApplication
        for widget in QApplication.allWidgets():
            if isinstance(widget, panel_class):
                return widget
        return None
```

- [ ] **Step 4: Run tests and verify manually**

```bash
python -m pytest tests/ -v -k tiled
```

Manual verification: launch the app, open Data Browser, double-click a completed run. The Visualization panel should show the data.

- [ ] **Step 5: Commit**

```bash
git add src/lucid/ui/panels/tiled_browser_panel.py src/lucid/ui/panels/visualization_panel.py
git commit -m "feat(tiled-browser): double-click run to replay in Visualization panel

Fetches Bluesky documents from Tiled entry in background thread,
then replays them through the VisualizationPanel's document pipeline.
Supports both entry.documents() API and manual reconstruction."
```

---

## Summary of Changes

| Bug/Feature | Root Cause | Fix |
|-------------|-----------|-----|
| "Loading..." persists | `_on_records_loaded` doesn't restore state on None/error | Always restore UI state (Task 1) |
| Status shows "running" | Empty dict default masks None stop doc | Sentinel-based stop doc detection (Task 2) |
| Date filter broken | Dates always applied + wrong Key paths | Optional date checkboxes + correct Key paths (Task 3) |
| Search broken | FullText unreliable, silent failures | Client-side proxy model filtering (Task 4) |
| Column order wrong | Hard-coded column list | Reorder to Sample, Plan, Timestamp, Status, Scan ID (Task 5) |
| Pagination buttons | Manual page management | Qt fetchMore lazy loading (Task 6) |
| Double-click does nothing | Signal not connected | Fetch docs + replay in Visualization (Task 7) |

## Dependencies

```
Task 0 (investigation) ──> Task 2 (status fix, needs metadata structure)
                       ──> Task 3 Part B (Key paths)
                       ──> Task 7 (document fetching approach)

Task 1 (loading fix) ──> independent
Task 3 Part A (date UI) ──> independent
Task 4 (search fix) ──> independent
Task 5 (columns) ──> independent
Task 6 (lazy loading) ──> after Task 1
Task 7 (double-click) ──> after Task 0
```

Tasks 1, 3A, 4, 5 can run in parallel. Task 0 should run first. Task 6 depends on Task 1. Task 7 depends on Task 0.
