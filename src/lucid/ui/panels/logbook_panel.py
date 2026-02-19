"""Fragment-based logbook panel for LUCID.

Split-pane layout: entry list (sidebar) + EntryWidget (fragment view),
backed by the offline-first ``LogbookClient`` (synchronous SQLite).
"""

from __future__ import annotations

import getpass
import json
from datetime import datetime
from typing import Any, ClassVar

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import QSplitter, QWidget

from lucid.logbook.client import LogbookClient
from lucid.logbook.entry_widget import EntryData, EntryListWidget, EntryWidget
from lucid.logbook.fragment_widgets import FragmentData, FragmentType
from lucid.ui.panels.base import BasePanel, PanelMetadata
from lucid.utils.logging import logger


class LogbookPanel(BasePanel):
    """Panel displaying the fragment-based logbook.

    Layout::

        ┌──────────┬──────────────────────┐
        │ Entry    │                      │
        │ List     │   EntryWidget        │
        │ (sidebar)│   (fragment view)    │
        │          │                      │
        └──────────┴──────────────────────┘
    """

    panel_metadata: ClassVar[PanelMetadata] = PanelMetadata(
        id="lucid.panels.logbook",
        name="Logbook",
        description="Experiment logbook for recording notes and viewing system events",
        icon="book-open",
        category="Core",
        required_permission=None,
        singleton=True,
        closable=False,
        keywords=["log", "notes", "experiment", "journal", "record"],
        default_area="center",
        sidebar_group="top",
        auto_hide=False,
        sidebar_order=0,
    )

    note_added = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        self._client: LogbookClient | None = None
        self._logbook_id: str | None = None
        self._current_entry_id: str | None = None
        self._entries: dict[str, EntryData] = {}
        super().__init__(parent)

        # Deferred init (after widget is shown)
        QTimer.singleShot(0, self._deferred_init)

    def _setup_ui(self) -> None:
        self._splitter = QSplitter(Qt.Orientation.Horizontal, self)

        self._entry_list = EntryListWidget()
        self._entry_widget = EntryWidget(EntryData())

        self._splitter.addWidget(self._entry_list)
        self._splitter.addWidget(self._entry_widget)
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 3)
        self._splitter.setSizes([250, 750])

        self._layout.addWidget(self._splitter)

        # Signals
        self._entry_list.entry_selected.connect(self._on_entry_selected)
        self._entry_list.entry_delete_requested.connect(self._on_entry_deleted)
        self._entry_list.new_entry_requested.connect(self._on_new_entry_requested)
        self._entry_widget.fragment_added.connect(self._on_fragment_added)
        self._entry_widget.fragment_changed.connect(self._on_fragment_changed)
        self._entry_widget.fragment_deleted.connect(self._on_fragment_deleted)
        self._entry_widget.claude_requested.connect(self._on_claude_requested)
        self._entry_widget.title_changed.connect(self._on_title_changed)

    # ── Init ──────────────────────────────────────────────────────

    def _deferred_init(self) -> None:
        try:
            self._client = LogbookClient.get_instance()
            self._client.init()

            user = getpass.getuser()
            self._logbook_id = self._client.get_or_create_logbook(user)

            self._load_entries()
            self._start_event_listener()

            # Background sync after 10s
            QTimer.singleShot(10_000, self._try_sync)

            logger.info("LogbookPanel initialised (logbook={})", self._logbook_id)
        except Exception as e:
            logger.error("LogbookPanel init failed: {}", e)

    def _load_entries(self) -> None:
        if not self._client or not self._logbook_id:
            return

        rows = self._client.list_entries(self._logbook_id)
        entry_datas: list[EntryData] = []
        for row in rows:
            ed = self._row_to_entry_data(row)
            self._entries[ed.id] = ed
            entry_datas.append(ed)

        self._entry_list.set_entries(entry_datas)

        if entry_datas:
            first = entry_datas[0]
            self._entry_list.select_entry(first.id)
            self._select_entry(first.id)

    def _row_to_entry_data(self, row: dict[str, Any]) -> EntryData:
        tags = row.get("tags", "[]")
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except (json.JSONDecodeError, TypeError):
                tags = []
        created = row.get("created_at", "")
        if isinstance(created, str) and created:
            try:
                created_dt = datetime.fromisoformat(created)
            except ValueError:
                created_dt = datetime.now()
        else:
            created_dt = datetime.now()
        return EntryData(
            id=row["id"],
            title=row.get("title") or "",
            tags=tags,
            created_at=created_dt,
            updated_at=created_dt,
        )

    def _start_event_listener(self) -> None:
        try:
            from lucid.logbook.event_listener import EventListener
            listener = EventListener.get_instance()
            listener.current_entry_id = self._current_entry_id
            listener.fragment_injected.connect(self._on_fragment_injected)
            listener.start()
        except Exception as e:
            logger.debug("Could not start event listener: {}", e)

    # ── Entry selection ───────────────────────────────────────────

    def _select_entry(self, entry_id: str) -> None:
        if not self._client:
            return

        self._current_entry_id = entry_id

        # Update event listener
        try:
            from lucid.logbook.event_listener import EventListener
            EventListener.get_instance().current_entry_id = entry_id
        except Exception:
            pass

        # Load fragments
        frag_rows = self._client.list_fragments(entry_id)
        fragments: list[FragmentData] = []
        for fr in frag_rows:
            data_raw = fr.get("data")
            if isinstance(data_raw, str):
                try:
                    data_raw = json.loads(data_raw)
                except (json.JSONDecodeError, TypeError):
                    data_raw = {}
            ftype = FragmentType.READONLY if fr.get("kind") == "readonly" else FragmentType.TEXT
            fragments.append(FragmentData(
                id=fr["id"],
                fragment_type=ftype,
                content=fr.get("content", ""),
                subtype=fr.get("subtype", ""),
                metadata=data_raw or {},
            ))

        entry_data = self._entries.get(entry_id, EntryData(id=entry_id))
        entry_data.fragments = fragments
        self._entry_widget.set_entry(entry_data)

    # ── Slots ─────────────────────────────────────────────────────

    @Slot(str)
    def _on_entry_selected(self, entry_id: str) -> None:
        self._select_entry(entry_id)

    @Slot()
    def _on_new_entry_requested(self) -> None:
        if not self._client or not self._logbook_id:
            return
        try:
            entry_id = self._client.create_entry(self._logbook_id, title="")
            ed = EntryData(id=entry_id, title="")
            self._entries[entry_id] = ed
            self._entry_list.add_entry(ed)
            self._entry_list.select_entry(entry_id)
            self._select_entry(entry_id)
            logger.info("Created new entry {}", entry_id)
        except Exception as e:
            logger.error("Failed to create entry: {}", e)

    @Slot(str, str)
    def _on_fragment_added(self, entry_id: str, fragment_id: str) -> None:
        if not self._client:
            return
        try:
            self._client.add_fragment(entry_id, kind="text", fragment_id=fragment_id)
        except Exception as e:
            logger.error("Failed to persist fragment: {}", e)

    @Slot(str, str, str)
    def _on_fragment_changed(self, entry_id: str, fragment_id: str, content: str) -> None:
        if not self._client:
            return
        try:
            self._client.update_fragment(fragment_id, content=content)
        except Exception as e:
            logger.error("Failed to update fragment: {}", e)

    @Slot(str)
    def _on_fragment_injected(self, entry_id: str) -> None:
        """Refresh the entry view when the EventListener injects a fragment."""
        if entry_id == self._current_entry_id:
            self._select_entry(entry_id)

    @Slot(str, str)
    def _on_title_changed(self, entry_id: str, new_title: str) -> None:
        if not self._client:
            return
        if entry_id in self._entries:
            self._entries[entry_id].title = new_title
        try:
            self._client.update_entry(entry_id, title=new_title)
        except Exception as e:
            logger.error("Failed to update title: {}", e)
        self._entry_list.set_entries(list(self._entries.values()))

    @Slot(str)
    def _on_entry_deleted(self, entry_id: str) -> None:
        if not self._client:
            return
        try:
            self._client.delete_entry(entry_id)
            self._entries.pop(entry_id, None)
            logger.info("Deleted entry {}", entry_id)
            # If we deleted the current entry, select another
            if self._current_entry_id == entry_id:
                if self._entries:
                    next_id = next(iter(self._entries))
                    self._select_entry(next_id)
                    self._entry_list.select_entry(next_id)
                else:
                    self._current_entry_id = None
                    self._entry_widget.set_entry(EntryData())
        except Exception as e:
            logger.error("Failed to delete entry: {}", e)

    @Slot(str, str)
    def _on_fragment_deleted(self, entry_id: str, fragment_id: str) -> None:
        if not self._client:
            return
        try:
            self._client.delete_fragment(fragment_id)
            logger.info("Deleted fragment {} from entry {}", fragment_id, entry_id)
        except Exception as e:
            logger.error("Failed to delete fragment: {}", e)

    @Slot(str, str)
    def _on_claude_requested(self, entry_id: str, fragment_id: str) -> None:
        """Send a fragment's content to Claude and append the response."""
        # Find the fragment content
        entry = self._entries.get(entry_id)
        if not entry:
            return
        frag = next((f for f in entry.fragments if f.id == fragment_id), None)
        if not frag:
            return

        # Build the prompt from fragment content
        content = frag.content or json.dumps(frag.metadata, indent=2, default=str)
        prompt = (
            f"The user is asking about this logbook fragment:\n\n{content}\n\n"
            "Please provide a helpful, concise response."
        )

        # Try to send to the Claude panel
        try:
            self._send_to_claude(entry_id, prompt)
        except Exception as e:
            logger.error("Failed to send to Claude: {}", e)

    def _get_claude_panel(self):
        """Find or open the Claude panel. Returns (panel, agent) or (None, None)."""
        try:
            from lucid.ui.panels.registry import PanelRegistry

            registry = PanelRegistry.get_instance()
            panel = registry.create("lucid.panels.claude")

            if panel and getattr(panel, "_claude_widget", None) and hasattr(panel._claude_widget, "agent"):
                return panel, panel._claude_widget.agent
        except Exception as e:
            logger.error("Could not get Claude panel: {}", e)
        return None, None

    def _send_to_claude(self, entry_id: str, prompt: str) -> None:
        """Send prompt to Claude and collect the response into a fragment."""
        panel, agent = self._get_claude_panel()
        if not agent:
            logger.warning("Claude panel/agent not available")
            return

        if agent.is_busy():
            logger.warning("Claude is busy, cannot send logbook request")
            return

        # Track pending response
        self._pending_claude_entry = entry_id
        self._pending_claude_messages: list[str] = []

        # Connect to agent signals
        agent.message_received.connect(self._on_claude_message)
        agent.query_completed.connect(self._on_claude_complete)

        # Send via the widget (shows in chat UI too)
        panel.action_send_message(prompt)
        logger.info("Sent logbook fragment to Claude for entry {}", entry_id)

    @Slot(str)
    def _on_claude_message(self, message: str) -> None:
        """Collect Claude message chunks."""
        if hasattr(self, "_pending_claude_messages"):
            self._pending_claude_messages.append(message)

    @Slot()
    def _on_claude_complete(self) -> None:
        """Claude finished responding — inject the response as a fragment."""
        entry_id = getattr(self, "_pending_claude_entry", None)
        messages = getattr(self, "_pending_claude_messages", [])

        # Disconnect signals
        try:
            _, agent = self._get_claude_panel()
            if agent:
                agent.message_received.disconnect(self._on_claude_message)
                agent.query_completed.disconnect(self._on_claude_complete)
        except (RuntimeError, TypeError):
            pass

        if not entry_id or not messages or not self._client:
            self._pending_claude_entry = None
            self._pending_claude_messages = []
            return

        response_text = "\n\n".join(messages)

        try:
            frag_id = self._client.add_fragment(
                entry_id,
                kind="readonly",
                subtype="claude_response",
                content=response_text,
            )
            logger.info("Injected Claude response fragment {} into entry {}", frag_id, entry_id)
            # Refresh the view
            if entry_id == self._current_entry_id:
                self._select_entry(entry_id)
        except Exception as e:
            logger.error("Failed to inject Claude response: {}", e)
        finally:
            self._pending_claude_entry = None
            self._pending_claude_messages = []

    def _try_sync(self) -> None:
        if self._client:
            self._client.schedule_sync()

    # ── Introspection ─────────────────────────────────────────────

    def _get_specific_introspection_data(self) -> dict[str, Any]:
        return {
            "logbook_id": self._logbook_id,
            "current_entry_id": self._current_entry_id,
            "entry_count": len(self._entries),
        }

    def _get_available_actions(self) -> list[dict[str, Any]]:
        actions = super()._get_available_actions()
        actions.append({
            "name": "new_entry",
            "description": "Create a new logbook entry",
            "method": "action_new_entry",
        })
        return actions

    def action_new_entry(self) -> bool:
        self._on_new_entry_requested()
        return True
