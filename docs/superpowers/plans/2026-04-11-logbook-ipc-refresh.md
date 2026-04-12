# Logbook IPC Refresh Fix

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the logbook UI panel refresh when entries are created via IPC.

**Architecture:** Add an `_on_entry_created_callback` to `LogbookClient` (following the existing `_on_pull_callback` pattern), call it from `create_entry()`, and register a handler in `LogbookPanel` that inserts the new entry into the sidebar.

**Tech Stack:** Python, PySide6, sqlite3

**Bug:** When an external IPC client creates a logbook entry via `commands.logbook.add`, the entry is persisted to SQLite but the LogbookPanel never learns about it. The panel only refreshes on manual user actions or remote sync pulls.

---

### Task 1: Add entry-created callback to LogbookClient

**Files:**
- Modify: `src/lucid/logbook/client.py`
- Modify: `src/lucid/ui/panels/logbook_panel.py`
- Test: `tests/ipc/test_integration.py`

- [ ] **Step 1: Write test for the callback**

In `tests/ipc/test_integration.py`, add to the logbook integration test class:

```python
class TestLogbookIPCRefresh:
    """Verify LogbookClient fires entry-created callback."""

    def test_create_entry_fires_callback(self):
        client = LogbookClient.__new__(LogbookClient)
        client._db = None
        client._sync_timer = None
        client._on_pull_callback = None
        client._on_sync_error_callback = None
        client._on_sync_restored_callback = None
        client._on_entry_created_callback = None
        client._sync_failed = False
        client._offline_only = True
        client._server_url = None

        # Set up in-memory DB
        import sqlite3
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.executescript("""
            CREATE TABLE IF NOT EXISTS logbook (id TEXT PRIMARY KEY, user_id TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS entry (id TEXT PRIMARY KEY, logbook_id TEXT, title TEXT, tags TEXT, created_at TEXT, updated_at TEXT, sync_status TEXT);
            CREATE TABLE IF NOT EXISTS fragment (id TEXT PRIMARY KEY, entry_id TEXT, kind TEXT, subtype TEXT, content TEXT, data TEXT, position INTEGER, created_at TEXT, updated_at TEXT, sync_status TEXT);
        """)
        client._db = db
        client._initialized = True

        captured = []
        client.set_on_entry_created_callback(lambda eid, lid: captured.append((eid, lid)))

        logbook_id = "test-logbook"
        db.execute("INSERT INTO logbook (id, user_id, created_at) VALUES (?, ?, ?)", (logbook_id, "testuser", "2026-01-01"))
        db.commit()

        entry_id = client.create_entry(logbook_id, title="IPC Test")

        assert len(captured) == 1
        assert captured[0] == (entry_id, logbook_id)

    def test_no_callback_no_error(self):
        """create_entry doesn't crash when no callback is registered."""
        client = LogbookClient.__new__(LogbookClient)
        client._db = None
        client._sync_timer = None
        client._on_pull_callback = None
        client._on_sync_error_callback = None
        client._on_sync_restored_callback = None
        client._on_entry_created_callback = None
        client._sync_failed = False
        client._offline_only = True
        client._server_url = None

        import sqlite3
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.executescript("""
            CREATE TABLE IF NOT EXISTS logbook (id TEXT PRIMARY KEY, user_id TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS entry (id TEXT PRIMARY KEY, logbook_id TEXT, title TEXT, tags TEXT, created_at TEXT, updated_at TEXT, sync_status TEXT);
            CREATE TABLE IF NOT EXISTS fragment (id TEXT PRIMARY KEY, entry_id TEXT, kind TEXT, subtype TEXT, content TEXT, data TEXT, position INTEGER, created_at TEXT, updated_at TEXT, sync_status TEXT);
        """)
        client._db = db
        client._initialized = True

        db.execute("INSERT INTO logbook (id, user_id, created_at) VALUES (?, ?, ?)", ("lb", "testuser", "2026-01-01"))
        db.commit()

        # Should not raise
        entry_id = client.create_entry("lb", title="No callback")
        assert entry_id  # Got a valid ID back
```

Add the necessary import at the top of the file if not present:

```python
from lucid.logbook.client import LogbookClient
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/PycharmProjects/ncs/ncs && .venv/Scripts/python.exe -m pytest tests/ipc/test_integration.py::TestLogbookIPCRefresh -v`
Expected: FAIL — `_on_entry_created_callback` attribute doesn't exist.

- [ ] **Step 3: Add callback to LogbookClient**

In `src/lucid/logbook/client.py`:

In `__init__` (line 366), after `self._on_sync_restored_callback`, add:

```python
self._on_entry_created_callback: callable | None = None
```

After the existing `set_on_pull_callback` method (line 725), add:

```python
def set_on_entry_created_callback(self, callback: callable) -> None:
    """Register a callback invoked when an entry is created locally.

    The callback receives ``(entry_id, logbook_id)``.
    """
    self._on_entry_created_callback = callback
```

In `create_entry` (after `db.commit()` on line 478, before `self.schedule_sync()` on line 479), add:

```python
if self._on_entry_created_callback:
    self._on_entry_created_callback(eid, logbook_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/PycharmProjects/ncs/ncs && .venv/Scripts/python.exe -m pytest tests/ipc/test_integration.py::TestLogbookIPCRefresh -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Wire the callback in LogbookPanel**

In `src/lucid/ui/panels/logbook_panel.py`, in `_deferred_init` (after line 141 where `_on_sync_restored_callback` is set), add:

```python
self._client.set_on_entry_created_callback(self._on_ipc_entry_created)
```

Add the handler method to the `# ── Slots ──` section (after `_on_new_entry_requested`):

```python
def _on_ipc_entry_created(self, entry_id: str, logbook_id: str) -> None:
    """Handle an entry created outside the panel (e.g. via IPC)."""
    if logbook_id != self._logbook_id:
        return
    if entry_id in self._entries:
        return  # Already known (manual creation path)
    row = self._client.get_entry(entry_id) if self._client else None
    if not row:
        return
    ed = self._row_to_entry_data(row)
    self._entries[entry_id] = ed
    if self._entries_panel:
        self._entries_panel.add_entry(ed)
```

**IMPORTANT:** Check whether `LogbookClient` has a `get_entry(entry_id)` method. If not, use `list_entries(logbook_id)` and find the matching entry, or add a simple `get_entry` method:

```python
def get_entry(self, entry_id: str) -> dict[str, Any] | None:
    db = self._ensure_db()
    row = db.execute("SELECT * FROM entry WHERE id = ?", (entry_id,)).fetchone()
    return dict(row) if row else None
```

- [ ] **Step 6: Run full test suite**

Run: `cd ~/PycharmProjects/ncs/ncs && .venv/Scripts/python.exe -m pytest tests/ipc/ -v`
Expected: All pass.

- [ ] **Step 7: Commit**

```bash
cd ~/PycharmProjects/ncs/ncs
git add src/lucid/logbook/client.py src/lucid/ui/panels/logbook_panel.py tests/ipc/test_integration.py
git commit -m "fix(logbook): refresh panel when entries are created via IPC"
```
