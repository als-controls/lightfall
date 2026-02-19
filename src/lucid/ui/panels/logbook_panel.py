"""Fragment-based logbook panel for LUCID.

Replaces the old single-document LogbookWidget with a split-pane layout:
entry list (sidebar) + EntryWidget (fragment view), backed by the
offline-first ``LogbookClient``.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, ClassVar

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import QSplitter, QWidget

from lucid.logbook.entry_widget import EntryData, EntryListWidget, EntryWidget
from lucid.logbook.fragment_widgets import FragmentData, FragmentType
from lucid.ui.panels.base import BasePanel, PanelMetadata
from lucid.utils.logging import logger

if TYPE_CHECKING:
    from lucid.logbook.client import LogbookClient


class LogbookPanel(BasePanel):
    """Panel displaying the fragment-based logbook.

    Layout::

        ┌──────────┬──────────────────────┐
        │ Entry    │                      │
        │ List     │   EntryWidget        │
        │ (sidebar)│   (fragment view)    │
        │          │                      │
        └──────────┴──────────────────────┘

    Signals:
        note_added: Emitted when user adds a note.
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

        # Deferred async init
        QTimer.singleShot(0, self._deferred_init)

    # ── UI setup ──────────────────────────────────────────────────

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
        self._entry_list.new_entry_requested.connect(self._on_new_entry_requested)
        self._entry_widget.fragment_added.connect(self._on_fragment_added)
        self._entry_widget.fragment_changed.connect(self._on_fragment_changed)

    # ── Deferred init ─────────────────────────────────────────────

    def _deferred_init(self) -> None:
        """Run async initialisation from the Qt event loop."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self._async_init())
            else:
                loop.run_until_complete(self._async_init())
        except RuntimeError:
            logger.warning("No event loop for logbook init")

    async def _async_init(self) -> None:
        from lucid.logbook.client import LogbookClient

        self._client = LogbookClient.get_instance()
        await self._client.init()

        # Get or create a default logbook
        import getpass
        user = getpass.getuser()
        self._logbook_id = await self._client.get_or_create_logbook(user)

        await self._load_entries()

        # Start event listener
        self._start_event_listener()

        # Schedule first sync
        self._client.schedule_sync(delay_ms=10_000)

        logger.info("LogbookPanel initialised (logbook={})", self._logbook_id)

    async def _load_entries(self) -> None:
        if not self._client or not self._logbook_id:
            return

        rows = await self._client.list_entries(self._logbook_id)
        entry_datas: list[EntryData] = []
        for row in rows:
            ed = self._row_to_entry_data(row)
            self._entries[ed.id] = ed
            entry_datas.append(ed)

        self._entry_list.set_entries(entry_datas)

        # Select first entry if available
        if entry_datas:
            first = entry_datas[0]
            self._entry_list.select_entry(first.id)
            await self._select_entry(first.id)

    def _row_to_entry_data(self, row: dict[str, Any]) -> EntryData:
        from datetime import datetime
        tags = row.get("tags", "[]")
        if isinstance(tags, str):
            tags = json.loads(tags)
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

    # ── Event listener ────────────────────────────────────────────

    def _start_event_listener(self) -> None:
        from lucid.logbook.event_listener import EventListener
        listener = EventListener.get_instance()
        listener.current_entry_id = self._current_entry_id
        listener.start()

    # ── Slots ─────────────────────────────────────────────────────

    @Slot(str)
    def _on_entry_selected(self, entry_id: str) -> None:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self._select_entry(entry_id))
            else:
                loop.run_until_complete(self._select_entry(entry_id))
        except RuntimeError:
            pass

    async def _select_entry(self, entry_id: str) -> None:
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
        frag_rows = await self._client.list_fragments(entry_id)
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
                created_at=self._entries.get(entry_id, EntryData()).created_at,
            ))

        entry_data = self._entries.get(entry_id, EntryData(id=entry_id))
        entry_data.fragments = fragments
        self._entry_widget.set_entry(entry_data)

    @Slot()
    def _on_new_entry_requested(self) -> None:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self._create_entry())
            else:
                loop.run_until_complete(self._create_entry())
        except RuntimeError:
            pass

    async def _create_entry(self) -> None:
        if not self._client or not self._logbook_id:
            return

        entry_id = await self._client.create_entry(self._logbook_id, title="New Entry")
        ed = EntryData(id=entry_id, title="New Entry")
        self._entries[entry_id] = ed
        self._entry_list.add_entry(ed)
        self._entry_list.select_entry(entry_id)
        await self._select_entry(entry_id)
        logger.info("Created new entry {}", entry_id)

    @Slot(str, str)
    def _on_fragment_added(self, entry_id: str, fragment_id: str) -> None:
        if not self._client:
            return
        self._run_async(
            self._client.add_fragment(entry_id, kind="text", fragment_id=fragment_id)
        )

    @Slot(str, str, str)
    def _on_fragment_changed(self, entry_id: str, fragment_id: str, content: str) -> None:
        if not self._client:
            return
        self._run_async(
            self._client.update_fragment(fragment_id, content=content)
        )

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

    # ── Helpers ────────────────────────────────────────────────────

    @staticmethod
    def _run_async(coro: Any) -> None:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(coro)
            else:
                loop.run_until_complete(coro)
        except RuntimeError:
            logger.debug("No event loop for async operation")

    def _on_closing(self) -> None:
        if self._client:
            self._run_async(self._client.close())
